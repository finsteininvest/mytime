import sqlite3
import tkinter as tk
import tkinter.font
from tkinter import ttk, messagebox, simpledialog, filedialog, colorchooser
from datetime import datetime, timedelta, date, time, timezone, timedelta
import calendar
import os
import sys
import csv

DB_PATH = "planner.db"
HOUR_START = 0
HOUR_END = 24
MINUTE_STEP = 15
ROW_HEIGHT = 28
LEFT_WIDTH = 440
RIGHT_MIN_WIDTH = 980  # a bit wider for week columns
EVENT_MIN_HEIGHT = 12

DEFAULT_DAILY_SPAN_DAYS = 30
DEFAULT_WEEKLY_SPAN_WEEKS = 8

# --------------------- Persistence ---------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # tasks
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 30,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            recurrence TEXT DEFAULT NULL,         -- NULL | 'DAILY' | 'WEEKLY'
            is_template INTEGER NOT NULL DEFAULT 0,
            project_id INTEGER DEFAULT NULL,
            priority TEXT DEFAULT 'Normal'        -- 'Low' | 'Normal' | 'High'
        )
        """
    )
    # Add is_done column to tasks if it doesn't exist
    cur.execute("PRAGMA table_info(tasks)")
    columns = [row[1] for row in cur.fetchall()]
    if 'is_done' not in columns:
        cur.execute("ALTER TABLE tasks ADD COLUMN is_done INTEGER NOT NULL DEFAULT 0")
    if 'parent_id' not in columns:
        cur.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE")

    # projects
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            color TEXT NOT NULL DEFAULT '#cde7ff',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # events
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            task_id INTEGER,
            start_dt TEXT NOT NULL,
            end_dt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            project_id INTEGER,
            priority TEXT DEFAULT 'Normal',
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE SET NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    # Add is_done column to events if it doesn't exist
    cur.execute("PRAGMA table_info(events)")
    columns = [row[1] for row in cur.fetchall()]
    if 'is_done' not in columns:
        cur.execute("ALTER TABLE events ADD COLUMN is_done INTEGER NOT NULL DEFAULT 0")
    if 'notes' not in columns:
        cur.execute("ALTER TABLE events ADD COLUMN notes TEXT DEFAULT ''")
    conn.commit(); conn.close()

# --------------------- Helpers ---------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def clamp(val, lo, hi):
    return max(lo, min(hi, val))

def y_to_time(y):
    total_steps = y // ROW_HEIGHT
    minutes = int(total_steps * MINUTE_STEP)
    base = time(hour=HOUR_START, minute=0)
    dt = datetime.combine(date.today(), base) + timedelta(minutes=minutes)
    return dt.time()

def time_to_y(t: time):
    minutes_since_start = (t.hour - HOUR_START) * 60 + t.minute
    steps = minutes_since_start // MINUTE_STEP
    return int(steps * ROW_HEIGHT)

def round_dt(dt: datetime):
    minutes = dt.minute
    rounded = (minutes // MINUTE_STEP) * MINUTE_STEP
    return dt.replace(minute=rounded, second=0, microsecond=0)

# ICS helpers (minimal)

def dt_from_ics(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        base = datetime.strptime(s, "%Y%m%dT%H%M%SZ")
        return base
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        d = datetime.strptime(s, "%Y%m%d")
        return datetime.combine(d.date(), time(0,0))
    except ValueError:
        raise ValueError(f"Unsupported ICS datetime: {s}")

# --------------------- UI: Project Editor ---------------------

class ProjectEditor(tk.Toplevel):
    def __init__(self, master, on_change):
        super().__init__(master)
        self.title("Projects")
        self.resizable(False, False)
        self.on_change = on_change

        self.tree = ttk.Treeview(self, columns=("name","color"), show="headings", height=8, selectmode="browse")
        self.tree.heading("name", text="Project")
        self.tree.heading("color", text="Color")
        self.tree.column("name", width=200)
        self.tree.column("color", width=120)
        self.tree.grid(row=0, column=0, columnspan=3, padx=8, pady=8)

        ttk.Button(self, text="＋ Add", command=self.add).grid(row=1, column=0, padx=8, pady=(0,8), sticky="w")
        ttk.Button(self, text="✎ Rename", command=self.rename).grid(row=1, column=1, padx=4, pady=(0,8))
        ttk.Button(self, text="🎨 Color", command=self.recolor).grid(row=1, column=2, padx=4, pady=(0,8))

        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        conn = get_conn()
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        conn.close()
        for r in rows:
            self.tree.insert("", "end", iid=str(r["id"]), values=(r["name"], r["color"]))

    def add(self):
        name = simpledialog.askstring("New project", "Name:", parent=self)
        if not name:
            return
        initial = "#58a6ff"
        picked = colorchooser.askcolor(title="Pick project color", initialcolor=initial)
        color = picked[1] if picked and picked[1] else initial
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO projects(name, color, created_at, updated_at) VALUES (?,?,?,?)",
                (name, color, now_iso(), now_iso())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            messagebox.showerror("Exists", "Project with this name already exists.")
        finally:
            conn.close()
        self.refresh(); self.on_change()

    def rename(self):
        sel = self.tree.selection()
        if not sel: return
        pid = int(sel[0])
        new = simpledialog.askstring("Rename", "New name:", parent=self)
        if not new: return
        conn = get_conn(); conn.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (new, now_iso(), pid)); conn.commit(); conn.close()
        self.refresh(); self.on_change()

    def recolor(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        conn = get_conn(); row = conn.execute("SELECT color FROM projects WHERE id=?", (pid,)).fetchone(); conn.close()
        initial = row["color"] if row and row["color"] else "#58a6ff"
        picked = colorchooser.askcolor(title="Pick project color", initialcolor=initial)
        color = picked[1] if picked and picked[1] else None
        if not color:
            return
        conn = get_conn(); conn.execute("UPDATE projects SET color=?, updated_at=? WHERE id=?", (color, now_iso(), pid)); conn.commit(); conn.close()
        self.refresh(); self.on_change()

# --------------------- UI: Task Editor ---------------------

class EventEditor(tk.Toplevel):
    def __init__(self, master, event, on_save=None):
        super().__init__(master)
        self.title("Event")
        self.resizable(False, False)
        self.event = event
        self.on_save = on_save

        # --- Title
        ttk.Label(self, text="Title").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        self.title_var = tk.StringVar(value=event["title"] if event else "")
        ttk.Entry(self, textvariable=self.title_var, width=44).grid(row=1, column=0, columnspan=4, padx=8, sticky="ew")

        # --- Date & Time
        start_dt = datetime.fromisoformat(event["start_dt"]) if event and event.get("start_dt") else datetime.now()
        end_dt = datetime.fromisoformat(event["end_dt"]) if event and event.get("end_dt") else start_dt + timedelta(minutes=60)
        duration_minutes = (end_dt - start_dt).total_seconds() / 60

        ttk.Label(self, text="Date (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", padx=8, pady=(8,2))
        ttk.Label(self, text="Time (HH:MM)").grid(row=2, column=1, sticky="w", padx=8, pady=(8,2))
        ttk.Label(self, text="Duration (min)").grid(row=2, column=2, sticky="w", padx=8, pady=(8,2))

        self.date_var = tk.StringVar(value=start_dt.strftime("%Y-%m-%d"))
        self.time_var = tk.StringVar(value=start_dt.strftime("%H:%M"))
        self.duration_var = tk.IntVar(value=int(duration_minutes))

        ttk.Entry(self, textvariable=self.date_var, width=14).grid(row=3, column=0, padx=8, sticky="w")
        ttk.Entry(self, textvariable=self.time_var, width=10).grid(row=3, column=1, padx=8, sticky="w")
        ttk.Spinbox(self, from_=5, to=480, increment=5, textvariable=self.duration_var, width=10).grid(row=3, column=2, padx=8, sticky="w")

        # --- Notes
        ttk.Label(self, text="Notes").grid(row=5, column=0, sticky="w", padx=8, pady=(8,2))
        self.notes = tk.Text(self, width=60, height=8)
        self.notes.grid(row=6, column=0, columnspan=4, padx=8, pady=(0,8))
        if event and event.get("notes"):
            self.notes.insert("1.0", event["notes"])

        # --- Buttons
        btns = ttk.Frame(self)
        btns.grid(row=7, column=0, columnspan=4, sticky="e", padx=8, pady=(0,8))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=(6,0))
        ttk.Button(btns, text="Save", command=self.save).pack(side=tk.RIGHT)

        # Simple keyboard UX
        self.bind("<Escape>", lambda e: self.destroy())

    def _parse_start(self):
        d = self.date_var.get().strip()
        t = self.time_var.get().strip()
        # require HH:MM; show a friendly error if invalid
        try:
            return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Invalid date/time", "Please use YYYY-MM-DD and HH:MM (24h).")
            return None

    def save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("Missing title", "Please enter a title")
            return

        new_start = self._parse_start()
        if new_start is None:
            return

        duration = timedelta(minutes=self.duration_var.get())
        new_end = new_start + duration
        notes = self.notes.get("1.0", "end").strip()

        if self.on_save:
            self.on_save({
                "title": title,
                "notes": notes,
                "start_dt": new_start,
                "end_dt": new_end,
            })
        self.destroy()


class TaskEditor(tk.Toplevel):
    def __init__(self, master, task=None, on_save=None, parent_id=None):
        super().__init__(master)
        self.title("Task")
        self.resizable(False, False)
        self.task = task
        self.on_save = on_save
        self.parent_id = parent_id
        if task and 'parent_id' in task.keys():
            self.parent_id = task['parent_id']

        ttk.Label(self, text="Title").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        self.title_var = tk.StringVar(value=task["title"] if task else "")
        title_entry = ttk.Entry(self, textvariable=self.title_var, width=44)
        title_entry.grid(row=1, column=0, columnspan=4, padx=8, sticky="ew")
        title_entry.focus_set()

        ttk.Label(self, text="Duration (min)").grid(row=2, column=0, sticky="w", padx=8, pady=(8,2))
        self.duration_var = tk.IntVar(value=task["duration_minutes"] if task else 30)
        ttk.Spinbox(self, from_=5, to=480, increment=5, textvariable=self.duration_var, width=10).grid(row=3, column=0, padx=8, sticky="w")

        ttk.Label(self, text="Recurrence").grid(row=2, column=1, sticky="w", padx=8, pady=(8,2))
        self.recur_var = tk.StringVar(value=task["recurrence"] if task and task["recurrence"] else "None")
        ttk.Combobox(self, textvariable=self.recur_var, values=["None","DAILY","WEEKLY"], state="readonly", width=10).grid(row=3, column=1, padx=8)

        ttk.Label(self, text="Priority").grid(row=2, column=2, sticky="w", padx=8, pady=(8,2))
        self.prio_var = tk.StringVar(value=task["priority"] if task else "Normal")
        ttk.Combobox(self, textvariable=self.prio_var, values=["Low","Normal","High"], state="readonly", width=10).grid(row=3, column=2, padx=8)

        ttk.Label(self, text="Project").grid(row=2, column=3, sticky="w", padx=8, pady=(8,2))
        self.project_var = tk.StringVar(value="")
        self.project_map = self._load_projects()
        proj_names = ["(none)"] + [p[1] for p in self.project_map]
        default_name = "(none)"
        if task and task["project_id"]:
            for pid, name in self.project_map:
                if pid == task["project_id"]:
                    default_name = name
        self.project_combo = ttk.Combobox(self, textvariable=self.project_var, values=proj_names, state="readonly", width=14)
        self.project_combo.grid(row=3, column=3, padx=8)
        self.project_combo.set(default_name)

        self.is_tmpl = tk.BooleanVar(value=bool(task["is_template"]) if task else False)
        ttk.Checkbutton(self, text="Save as template", variable=self.is_tmpl).grid(row=4, column=0, padx=8, pady=(8,2), sticky="w")

        ttk.Label(self, text="Notes").grid(row=5, column=0, sticky="w", padx=8, pady=(8,2))
        self.notes = tk.Text(self, width=60, height=8)
        self.notes.grid(row=6, column=0, columnspan=4, padx=8, pady=(0,8))
        if task:
            self.notes.insert("1.0", task["notes"] or "")

        btns = ttk.Frame(self)
        btns.grid(row=7, column=0, columnspan=4, pady=8)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Save", command=self.save).pack(side=tk.RIGHT)

    def _load_projects(self):
        conn = get_conn(); rows = conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall(); conn.close()
        return [(r["id"], r["name"]) for r in rows]

    def save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("Missing title", "Please enter a title")
            return
        dur = max(5, int(self.duration_var.get()))
        notes = self.notes.get("1.0", "end").strip()
        rec = self.recur_var.get()
        rec = None if rec == "None" else rec
        prio = self.prio_var.get()
        proj_name = self.project_var.get()
        pid = None
        if proj_name and proj_name != "(none)":
            for ppid, name in self.project_map:
                if name == proj_name:
                    pid = ppid
                    break
        if self.on_save:
            self.on_save({
                "title": title,
                "duration_minutes": dur,
                "notes": notes,
                "recurrence": rec,
                "is_template": 1 if self.is_tmpl.get() else 0,
                "project_id": pid,
                "priority": prio,
                "parent_id": self.parent_id
            })
        self.destroy()

# --------------------- UI: Tasks Panel ---------------------

class TasksPanel(ttk.Frame):
    def __init__(self, master, on_task_selected, on_new_from_template):
        self.filter_str = ""
        self.hidden_flags = set()
        super().__init__(master)
        self.on_task_selected = on_task_selected
        self.on_new_from_template = on_new_from_template
        self.filter_str = ""

        search_frame = ttk.Frame(self)
        search_frame.pack(fill=tk.X, padx=8, pady=(8,0))
        ttk.Label(search_frame, text="Filter:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        search_entry.bind("<KeyRelease>", self._on_search)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("prio","dur","recur","proj", "scheduled"), show="headings", selectmode="browse", height=18)
        self.tree.heading("prio", text="Priority")
        self.tree.heading("dur", text="Duration")
        self.tree.heading("recur", text="Recurs")
        self.tree.heading("proj", text="Project")
        self.tree.heading("scheduled", text="Scheduled")
        self.tree.column("prio", width=60, anchor="center")
        self.tree.column("dur", width=70, anchor="center")
        self.tree.column("recur", width=60, anchor="center")
        self.tree.column("proj", width=90, anchor="w")
        self.tree.column("scheduled", width=120, anchor="w")

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Double-1>", self._edit)
        self.tree.bind("u", lambda e: self.unschedule())  # keyboard shortcut
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Button-4>", self._on_mousewheel)
        self.tree.bind("<Button-5>", self._on_mousewheel)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Control-Button-1>", self._show_context_menu)

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Export visible to CSV", command=self._export_visible_to_csv)
        self.context_menu.add_command(label="Copy visible to Clipboard", command=self._copy_visible_to_clipboard)

        # Configure font and tag for done tasks
        style = ttk.Style()
        font_spec = style.lookup("Treeview", "font")
        done_font = tk.font.Font(font=font_spec)
        done_font.configure(overstrike=True)
        self.tree.tag_configure("done", font=done_font, foreground="#777")

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=8, pady=(0,8))
        ttk.Button(btns, text="(＋) Add", command=self.add).pack(side=tk.LEFT)
        ttk.Button(btns, text="Add Subtask", command=self.add_subtask).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="✎ (E)dit", command=self.edit).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="✔ (D)one", command=self.mark_as_done).pack(side=tk.LEFT)
        ttk.Button(btns, text="(U)nschedule", command=self.unschedule).pack(side=tk.LEFT)
        ttk.Button(btns, text="🗑 Delete", command=self.delete).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="📄 From Template", command=self.new_from_template).pack(side=tk.RIGHT)

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            self.tree.yview_scroll(-1 * event.delta, "units")
        else:
            if hasattr(event, 'delta'):
                self.tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                if event.num == 4:
                    self.tree.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.tree.yview_scroll(1, "units")

    def _on_search(self, event=None):
        self.refresh(self.search_var.get())

    def unschedule(self):
        tid = self.selected_task_id()
        if not tid:
            return
        conn = get_conn()
        count = conn.execute("SELECT COUNT(*) FROM events WHERE task_id=?", (tid,)).fetchone()[0]
        if count == 0:
            conn.close()
            messagebox.showinfo("Unschedule", "This task has no scheduled events.")
            return
        if not messagebox.askyesno("Unschedule", f"Remove {count} scheduled event(s) for this task?"):
            conn.close()
            return
        conn.execute("DELETE FROM events WHERE task_id=?", (tid,))
        conn.commit(); conn.close()
        # refresh everywhere
        self.on_new_from_template()  # calls App.refresh_all

    def refresh(self, filter_str=None):
        # --- 1) Parse commands like "/hide Scheduled" or "/unhide Scheduled"
        if filter_str is not None:
            cmd = filter_str.strip()
            if cmd.startswith("/hide "):
                flag = cmd[len("/hide "):].strip()
                if flag.lower() == "all":
                    self.hidden_flags.update({"Scheduled", "Done"})
                elif flag:
                    self.hidden_flags.add(flag)
                self.filter_str = ""
            elif cmd.startswith("/unhide"):
                rest = cmd[len("/unhide "):].strip()
                if rest.lower() == "all":
                    self.hidden_flags.clear()
                else:
                    self.hidden_flags.discard(rest)
                self.filter_str = ""
            else:
                self.filter_str = cmd

        # --- 2) Clear current rows
        for i in self.tree.get_children():
            self.tree.delete(i)

        # --- 3) Query ALL tasks and events
        conn = get_conn()
        all_tasks = conn.execute("""
            SELECT t.*, p.name AS proj
            FROM tasks t
            LEFT JOIN projects p ON t.project_id = p.id
            ORDER BY t.created_at ASC
        """,).fetchall()

        events_by_task = {}
        all_events = conn.execute("SELECT task_id, start_dt FROM events ORDER BY start_dt").fetchall()
        conn.close()

        for ev in all_events:
            tid = ev["task_id"]
            if tid:
                events_by_task.setdefault(tid, []).append(datetime.fromisoformat(ev["start_dt"]))

        # --- 4) Filter tasks in memory to preserve hierarchy
        tasks_to_display = []
        if self.filter_str:
            tasks_by_id = {r['id']: dict(r) for r in all_tasks}

            # Find direct matches
            filter_term = self.filter_str.lower()
            matching_ids = {
                r['id'] for r in all_tasks
                if filter_term in r['title'].lower()
                or (r['notes'] and filter_term in r['notes'].lower())
                or (r['proj'] and filter_term in r['proj'].lower())
            }

            # Include ancestors and descendants of matches
            ids_to_show = set()

            def get_all_descendants(task_id):
                descendants = set()
                children = [t for t in all_tasks if t['parent_id'] == task_id]
                for child in children:
                    descendants.add(child['id'])
                    descendants.update(get_all_descendants(child['id']))
                return descendants

            def get_all_ancestors(task_id):
                ancestors = set()
                parent_id = tasks_by_id.get(task_id, {}).get('parent_id')
                while parent_id:
                    ancestors.add(parent_id)
                    parent_id = tasks_by_id.get(parent_id, {}).get('parent_id')
                return ancestors

            for tid in matching_ids:
                ids_to_show.add(tid)
                ids_to_show.update(get_all_ancestors(tid))
                ids_to_show.update(get_all_descendants(tid))

            tasks_to_display = [r for r in all_tasks if r['id'] in ids_to_show]
        else:
            tasks_to_display = all_tasks

        # --- 5) Build tree from the filtered list
        now = datetime.now()
        for r in tasks_to_display:
            task_events = events_by_task.get(r["id"], [])
            is_scheduled = bool(task_events)
            is_done = bool(r["is_done"])

            if ("Scheduled" in self.hidden_flags and is_scheduled) or \
               ("Done" in self.hidden_flags and is_done):
                continue

            scheduled_date_str = ""
            if task_events:
                future_events = [dt for dt in task_events if dt > now]
                shown = future_events[0] if future_events else task_events[-1] if r["recurrence"] else task_events[0]
                scheduled_date_str = shown.strftime("%Y-%m-%d %H:%M")

            tags = ("done",) if is_done else ()
            parent_iid = str(r['parent_id']) if r['parent_id'] else ""

            if parent_iid and not self.tree.exists(parent_iid):
                continue # Parent is hidden, so hide child

            self.tree.insert(
                parent_iid, "end", iid=str(r["id"]), text=r["title"],
                values=(
                    r["priority"], r["duration_minutes"], r["recurrence"] or "",
                    r["proj"] or "—", scheduled_date_str
                ),
                tags=tags
            )

        self.tree.configure(show=("tree", "headings"))
        self.tree.heading("#0", text="Task")

    '''
    /hide Scheduled → hides any task that has at least one event linked in events.
    /unhide Scheduled → shows them again.
    /hide Done and /unhide Done work too since we wired that in.
    /hide all / /unhide all are optional helpers already included.
    '''

    def selected_task_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add(self, parent_id=None):
        def on_save(data):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tasks(title, duration_minutes, notes, created_at, updated_at, recurrence, is_template, project_id, priority, parent_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (data["title"], data["duration_minutes"], data["notes"], now_iso(), now_iso(), data["recurrence"], data["is_template"], data["project_id"], data["priority"], data["parent_id"])
            )
            new_task_id = cur.lastrowid
            conn.commit()
            conn.close()
            self.refresh()

            if new_task_id:
                iid = str(new_task_id)
                if self.tree.exists(iid):
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self.tree.see(iid)
                    self.on_task_selected(new_task_id)
        TaskEditor(self, on_save=on_save, parent_id=parent_id)

    def add_subtask(self):
        parent_id = self.selected_task_id()
        if not parent_id:
            messagebox.showinfo("No task selected", "Please select a parent task first.")
            return
        self.add(parent_id=parent_id)

    def _edit(self, *_):
        self.edit()

    def edit(self):
        tid = self.selected_task_id()
        if not tid: return
        conn = get_conn(); task = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone(); conn.close()
        def on_save(data):
            conn = get_conn()
            conn.execute(
                "UPDATE tasks SET title=?, duration_minutes=?, notes=?, updated_at=?, recurrence=?, is_template=?, project_id=?, priority=?, parent_id=? WHERE id=?",
                (data["title"], data["duration_minutes"], data["notes"], now_iso(), data["recurrence"], data["is_template"], data["project_id"], data["priority"], data["parent_id"], tid)
            )
            conn.execute(
                "UPDATE events SET title=?, project_id=? WHERE task_id=?",
                (data["title"], data["project_id"], tid)
            )
            conn.commit(); conn.close()
            self.on_new_from_template() # This calls App.refresh_all
        TaskEditor(self, task=task, on_save=on_save)

    def mark_as_done(self):
        tid = self.selected_task_id()
        if not tid: return
        conn = get_conn()
        task = conn.execute("SELECT is_done FROM tasks WHERE id=?", (tid,)).fetchone()
        if not task: return
        new_status = 1 if not task["is_done"] else 0
        conn.execute("UPDATE tasks SET is_done=?, updated_at=? WHERE id=?", (new_status, now_iso(), tid))
        conn.commit()
        conn.close()
        self.on_new_from_template() # This calls App.refresh_all

    def delete(self):
        tid = self.selected_task_id()
        if not tid: return
        if not messagebox.askyesno("Delete task", "Delete this task? (Scheduled events remain)"):
            return
        conn = get_conn(); conn.execute("DELETE FROM tasks WHERE id=?", (tid,)); conn.commit(); conn.close(); self.refresh()

    def _select(self, *_):
        tid = self.selected_task_id()
        if tid: self.on_task_selected(tid)

    def new_from_template(self):
        conn = get_conn(); rows = conn.execute("SELECT * FROM tasks WHERE is_template=1 ORDER BY title").fetchall(); conn.close()
        if not rows:
            messagebox.showinfo("Templates", "No templates yet. Mark a task as 'Save as template'.")
            return
        choices = [f"• {r['title']} ({r['duration_minutes']}m)" for r in rows]
        prompt = "Enter number:" + "".join(f"{i+1}. {choices[i]}" for i in range(len(choices)))
        idx = simpledialog.askinteger("Choose template", prompt, minvalue=1, maxvalue=len(choices), parent=self)
        if not idx: return
        r = rows[idx-1]
        conn = get_conn();
        conn.execute(
            "INSERT INTO tasks(title, duration_minutes, notes, created_at, updated_at, recurrence, is_template, project_id, priority) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["title"], r["duration_minutes"], r["notes"], now_iso(), now_iso(), r["recurrence"], 0, r["project_id"], r["priority"])
        ); conn.commit(); conn.close(); self.refresh()
        messagebox.showinfo("Template created", f"Created new task from template: {r['title']}")
        self.on_new_from_template()

    def _show_context_menu(self, event):
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _get_visible_tasks(self):
        tasks = []
        for iid in self.tree.get_children(""):
            item = self.tree.item(iid)
            values = item['values']
            tasks.append([item['text']] + values)
            # Also get children
            for child_iid in self.tree.get_children(iid):
                child_item = self.tree.item(child_iid)
                child_values = child_item['values']
                tasks.append(["  " + child_item['text']] + child_values)
        return tasks

    def _export_visible_to_csv(self):
        tasks = self._get_visible_tasks()
        if not tasks:
            messagebox.showinfo("No tasks", "No visible tasks to export.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export visible tasks"
        )
        if not filepath:
            return

        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Title", "Priority", "Duration", "Recurs", "Project", "Scheduled"])
                writer.writerows(tasks)
            messagebox.showinfo("Export successful", f"Exported {len(tasks)} tasks to {filepath}")
        except Exception as e:
            messagebox.showerror("Export failed", f"An error occurred: {e}")

    def _copy_visible_to_clipboard(self):
        tasks = self._get_visible_tasks()
        if not tasks:
            return

        # Using StringIO to build the CSV string in memory
        from io import StringIO
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["Title", "Priority", "Duration", "Recurs", "Project", "Scheduled"])
        writer.writerows(tasks)

        csv_string = output.getvalue()
        self.clipboard_clear()
        self.clipboard_append(csv_string)
        messagebox.showinfo("Copied", f"Copied {len(tasks)} tasks to clipboard.")

# --------------------- UI: Events Panel ---------------------

class EventsPanel(ttk.Frame):
    def __init__(self, master, on_event_selected):
        super().__init__(master)
        self.on_event_selected = on_event_selected
        self.filter_str = ""

        search_frame = ttk.Frame(self)
        search_frame.pack(fill=tk.X, padx=8, pady=(8,0))
        ttk.Label(search_frame, text="Filter:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4,0))
        search_entry.bind("<KeyRelease>", self._on_search)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("date", "time", "project", "done"), show="headings", selectmode="browse")
        self.tree.heading("date", text="Date")
        self.tree.heading("time", text="Time")
        self.tree.heading("project", text="Project")
        self.tree.heading("done", text="Done")

        self.tree.column("date", width=100, anchor="w")
        self.tree.column("time", width=80, anchor="center")
        self.tree.column("project", width=120, anchor="w")
        self.tree.column("done", width=50, anchor="center")

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=8, padx=(0, 8))

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Button-4>", self._on_mousewheel)
        self.tree.bind("<Button-5>", self._on_mousewheel)

        style = ttk.Style()
        font_spec = style.lookup("Treeview", "font")
        done_font = tk.font.Font(font=font_spec)
        done_font.configure(overstrike=True)
        self.tree.tag_configure("done", font=done_font, foreground="#777")

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            self.tree.yview_scroll(-1 * event.delta, "units")
        else:
            if hasattr(event, 'delta'):
                self.tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                if event.num == 4:
                    self.tree.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.tree.yview_scroll(1, "units")

    def _on_search(self, event=None):
        self.refresh(self.search_var.get())

    def refresh(self, filter_str=None):
        if filter_str is not None:
            self.filter_str = filter_str

        for i in self.tree.get_children():
            self.tree.delete(i)

        conn = get_conn()
        query = "SELECT e.*, p.name as proj_name FROM events e LEFT JOIN projects p ON e.project_id = p.id"
        params = []
        if self.filter_str:
            query += " WHERE (e.title LIKE ? OR e.notes LIKE ?)"
            params.extend([f"%{self.filter_str}%", f"%{self.filter_str}%"])
        query += " ORDER BY e.start_dt"

        events = conn.execute(query, params).fetchall()
        conn.close()

        for event in events:
            start_dt = datetime.fromisoformat(event["start_dt"])
            date_str = start_dt.strftime("%Y-%m-%d")
            time_str = start_dt.strftime("%H:%M")
            proj_name = event["proj_name"] or "—"
            done_str = "✔" if event["is_done"] else ""

            tags = ()
            if event["is_done"]:
                tags = ("done",)

            self.tree.insert(
                "", "end", iid=str(event["id"]),
                values=(date_str, time_str, proj_name, done_str),
                text=event["title"],
                tags=tags
            )

        self.tree.configure(show=("tree", "headings"))
        self.tree.heading("#0", text="Event Title")

    def _on_select(self, _):
        sel = self.tree.selection()
        if not sel:
            return
        event_id = int(sel[0])
        self.on_event_selected(event_id)

# --------------------- Views: Day & Week ---------------------

class BaseCalendarView(ttk.Frame):
    def __init__(self, master, on_change_callback=None):
        super().__init__(master)
        self.selected_task_id = None
        self._event_items = {}
        self._reverse_map = {}
        self.on_change_callback = on_change_callback

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            self.canvas.yview_scroll(-1 * event.delta, "units")
        else:
            if hasattr(event, 'delta'):
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                if event.num == 4:
                    self.canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.canvas.yview_scroll(1, "units")

    def _notify_change(self):
        if self.on_change_callback:
            self.on_change_callback()
        else:
            self.refresh()

    def set_selected_task(self, tid):
        self.selected_task_id = tid

    def _load_project_color(self, project_id):
        if not project_id: return "#cde7ff", "#58a6ff"
        conn = get_conn(); r = conn.execute("SELECT color FROM projects WHERE id=?", (project_id,)).fetchone(); conn.close()
        color = r["color"] if r else "#cde7ff"
        outline = "#4a6dac"
        return color, outline

    def _create_event(self, title, task_id, start_dt, end_dt, project_id, priority, notes):
        conn = get_conn();
        conn.execute(
            "INSERT INTO events(title, task_id, start_dt, end_dt, created_at, updated_at, project_id, priority, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, task_id, start_dt.isoformat(), end_dt.isoformat(), now_iso(), now_iso(), project_id, priority, notes)
        ); conn.commit(); conn.close(); self._notify_change()

    def _create_series(self, task, first_start_dt: datetime):
        rec = task["recurrence"]
        dur = timedelta(minutes=int(task["duration_minutes"]))
        if rec == "DAILY":
            count = DEFAULT_DAILY_SPAN_DAYS; delta = timedelta(days=1)
        else:
            count = DEFAULT_WEEKLY_SPAN_WEEKS; delta = timedelta(weeks=1)
        resp = messagebox.askyesno("Create series", f"Create {rec.lower()} series of {count} occurrences starting {first_start_dt.strftime('%Y-%m-%d %H:%M')}?")
        if not resp: return
        conn = get_conn(); cur = conn.cursor(); start = first_start_dt
        for _ in range(count):
            cur.execute(
                "INSERT INTO events(title, task_id, start_dt, end_dt, created_at, updated_at, project_id, priority, notes) VALUES (?,?,?,?,?,?,?,?,?)",
                (task["title"], task["id"], start.isoformat(), (start+dur).isoformat(), now_iso(), now_iso(), task["project_id"], task["priority"], task["notes"]))
            start += delta
        conn.commit(); conn.close(); self._notify_change()

    def _open_task_editor_for_event(self, event_data):
        r = dict(event_data)  # shallow, cheap
        task_id = r.get("task_id") or r.get("event_task_id")
        #task_id = event_data.get("task_id") or event_data.get("event_task_id")
        if not task_id:
            messagebox.showinfo("No Task", "This event is not linked to a task.")
            return

        conn = get_conn()
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        if not task:
            messagebox.showerror("Error", f"Task with ID {task_id} not found.")
            return

        def on_save(data):
            conn = get_conn()
            conn.execute(
                "UPDATE tasks SET title=?, duration_minutes=?, notes=?, updated_at=?, recurrence=?, is_template=?, project_id=?, priority=? WHERE id=?",
                (data["title"], data["duration_minutes"], data["notes"], now_iso(), data["recurrence"], data["is_template"], data["project_id"], data["priority"], task_id)
            )
            conn.execute(
                "UPDATE events SET project_id=? WHERE task_id=?",
                (data["project_id"], task_id)
            )
            conn.commit()
            conn.close()
            self._notify_change()

        TaskEditor(self, task=task, on_save=on_save)

    def _toggle_done(self, task_id):
        conn = get_conn()
        task = conn.execute("SELECT is_done FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            conn.close()
            return
        new_status = 1 if not task["is_done"] else 0
        conn.execute("UPDATE tasks SET is_done=?, updated_at=? WHERE id=?", (new_status, now_iso(), task_id))
        conn.commit()
        conn.close()
        self._notify_change()

    def _toggle_event_done(self, event_id):
        conn = get_conn()
        event = conn.execute("SELECT is_done FROM events WHERE id=?", (event_id,)).fetchone()
        if not event:
            conn.close()
            return
        new_status = 1 if not event["is_done"] else 0
        conn.execute("UPDATE events SET is_done=?, updated_at=? WHERE id=?", (new_status, now_iso(), event_id))
        conn.commit()
        conn.close()
        self._notify_change()

    def _delete_event(self, event):
        event_id = event['id']
        if not messagebox.askyesno("Delete Event", "Are you sure you want to delete this event?"):
            return
        conn = get_conn()
        conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
        conn.close()
        self._notify_change()

    def _open_event_editor(self, event_data):
        event_id = event_data.get("id")
        if not event_id:
            return

        conn = get_conn()
        event = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        conn.close()

        if event:  # <-- add this line
            event = dict(event)

        def on_save(data):
            conn = get_conn()
            conn.execute(
                "UPDATE events SET title=?, notes=?, start_dt=?, end_dt=?, updated_at=? WHERE id=?",
                (
                    data["title"],
                    data["notes"],
                    data["start_dt"].isoformat(timespec="minutes"),
                    data["end_dt"].isoformat(timespec="minutes"),
                    now_iso(),
                    event_id,
                )
            )
            conn.commit()
            conn.close()
            self._notify_change()

        EventEditor(self, event=event, on_save=on_save)


class DayView(BaseCalendarView):
    def __init__(self, master, notify_callback, on_change_callback=None):
        super().__init__(master, on_change_callback=on_change_callback)
        self.notify_callback = notify_callback
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=(8,0))
        self.date_var = tk.StringVar()
        ttk.Button(top, text="◀", width=3, command=self.prev_day).pack(side=tk.LEFT)
        ttk.Button(top, text="Today", command=self.today).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="▶", width=3, command=self.next_day).pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.date_var, font=("TkDefaultFont", 12, "bold")).pack(side=tk.RIGHT)

        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        self.canvas = tk.Canvas(canvas_frame, bg="#fafafa", highlightthickness=0)

        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Control-Button-1>", self.on_right_click)  # macOS ctrl-click
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)


        self.current_date = date.today()
        self.dragging_event_id = None
        self.drag_mode = None
        self.drag_offset_y = 0
        self.context_menu = tk.Menu(self, tearoff=0)
        self.canvas_height = 0
        self.canvas.bind("<Configure>", lambda e: self.refresh())

        self._init_now_dot_hooks()
        self.refresh(); self.after(60000, self._tick_notifications)

    def on_right_click(self, event):
        """
        Right-click on an event opens a context menu.
        """
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        overlaps = self.canvas.find_overlapping(canvas_x, canvas_y, canvas_x, canvas_y)
        item = overlaps[0] if overlaps else None
        if not item:
            return

        if item in self._event_items:
            val = self._event_items[item]
            event_data_row = val[0] if isinstance(val, tuple) else val
            event_data = dict(event_data_row)
            event_id = event_data.get("id")

            self.context_menu.delete(0, "end")

            if event_id:
                is_done = event_data.get("is_done", 0)
                label = "Mark as Not Done" if is_done else "Mark as Done"
                self.context_menu.add_command(label=label, command=lambda: self._toggle_event_done(event_id))
                self.context_menu.add_separator()

            #self.context_menu.add_command(label="Edit Task...", command=lambda: self._open_task_editor_for_event(event_data))
            self.context_menu.add_command(label="Edit Event...", command=lambda: self._open_event_editor(event_data))
            self.context_menu.add_command(label="Delete Event", command=lambda: self._delete_event(event_data))

            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    def today(self): self.current_date = date.today(); self.refresh()
    def prev_day(self): self.current_date -= timedelta(days=1); self.refresh()
    def next_day(self): self.current_date += timedelta(days=1); self.refresh()

    def refresh(self):
        self.canvas.delete("all")
        self.date_var.set(self.current_date.strftime("%A, %d %b %Y"))
        self._draw_grid(); self._draw_events(); self._draw_now_dot()

    def _draw_grid(self):
        width = max(self.winfo_width() or RIGHT_MIN_WIDTH, RIGHT_MIN_WIDTH)
        total_minutes = (HOUR_END - HOUR_START) * 60
        total_steps = total_minutes // MINUTE_STEP
        self.canvas_height = int(total_steps * ROW_HEIGHT)
        self.canvas.config(scrollregion=(0, 0, width, self.canvas_height))
        for h in range(HOUR_START, HOUR_END):
            y = time_to_y(time(hour=h, minute=0))
            self.canvas.create_line(60, y, width-12, y, fill="#ddd")
            self.canvas.create_text(42, y+2, text=f"{h:02d}:00", anchor="e", fill="#555")

        y_end = self.canvas_height
        self.canvas.create_line(60, y_end, width-12, y_end, fill="#ddd")
        self.canvas.create_text(42, y_end+2, text=f"{HOUR_END:02d}:00", anchor="e", fill="#555")

        for h in range(HOUR_START, HOUR_END):
            for m in (15, 30, 45):
                y = time_to_y(time(hour=h, minute=m))
                self.canvas.create_line(60, y, width-12, y, fill="#eee")

    def _draw_events(self):
        self._event_items.clear(); self._reverse_map.clear()
        conn = get_conn(); start_day = datetime.combine(self.current_date, time(0,0)); end_day = start_day + timedelta(days=1)
        rows = conn.execute("SELECT * FROM events WHERE start_dt>=? AND start_dt<? ORDER BY start_dt", (start_day.isoformat(), end_day.isoformat())).fetchall(); conn.close()
        for r in rows: self._render_event(r)

    def _render_event(self, r):
        start = datetime.fromisoformat(r["start_dt"]).time(); end = datetime.fromisoformat(r["end_dt"]).time()
        y1 = time_to_y(start); y2 = max(y1 + EVENT_MIN_HEIGHT, time_to_y(end))
        x1, x2 = 80, max(self.winfo_width()-40, 400)
        fill, outline = self._load_project_color(r["project_id"]) if "project_id" in r.keys() else ("#cde7ff","#58a6ff")
        rect = self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline)
        label = ("‼ " if r["priority"]=="High" else ("· " if r["priority"]=="Low" else "")) + r["title"]
        text_color = "#333" # Default text color
        if r["is_done"]:
            text_color = "#999" # Light grey for done tasks
        text = self.canvas.create_text(x1+8, y1+8, text=label, anchor="nw", fill=text_color)
        if r["notes"]:
            self.canvas.create_text(x2-8, y1+8, text="📝", anchor="ne", fill="#555")
        handle = self.canvas.create_rectangle(x2-12, y2-6, x2-4, y2-2, fill=outline, outline="")
        self._event_items[rect] = r; self._event_items[text] = r; self._event_items[handle] = (r, "handle")
        self._reverse_map.setdefault(r["id"], []).extend([rect, text, handle])

    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        overlaps = self.canvas.find_overlapping(canvas_x, canvas_y, canvas_x, canvas_y)

        # Prioritize finding a handle among overlapping items, checking topmost first
        for item_id in reversed(overlaps):
            if item_id in self._event_items:
                val = self._event_items[item_id]
                if isinstance(val, tuple) and val[1] == "handle":
                    r = val[0]
                    self.dragging_event_id = r["id"]
                    self.drag_mode = "resize"
                    return

        # If no handle was clicked, check for a regular event item
        for item_id in reversed(overlaps):
             if item_id in self._event_items:
                val = self._event_items[item_id]
                if not isinstance(val, tuple):
                    r = val
                    self.dragging_event_id = r["id"]
                    self.drag_mode = "move"
                    rect_id = self._reverse_map[self.dragging_event_id][0]
                    x1, y1, x2, y2 = self.canvas.coords(rect_id)
                    self.drag_offset_y = self.canvas.canvasy(event.y) - y1
                    return

        # If nothing was clicked, it's a click on an empty space
        if not self.selected_task_id:
            messagebox.showinfo("Select a task", "Select a task from the list first.")
            return
        conn = get_conn(); task = conn.execute("SELECT * FROM tasks WHERE id=?", (self.selected_task_id,)).fetchone(); conn.close()
        if not task: return

        t = y_to_time(self.canvas.canvasy(event.y)); start_dt = round_dt(datetime.combine(self.current_date, t)); end_dt = start_dt + timedelta(minutes=int(task["duration_minutes"]))
        if task["recurrence"] in ("DAILY","WEEKLY"): self._create_series(task, start_dt)
        else: self._create_event(task["title"], task["id"], start_dt, end_dt, task["project_id"], task["priority"], task["notes"])

    def on_drag(self, event):
        if not self.dragging_event_id: return

        # Auto-scroll
        scroll_margin = 30
        if event.y < scroll_margin:
            self.canvas.yview_scroll(-1, "units")
        elif event.y > self.canvas.winfo_height() - scroll_margin:
            self.canvas.yview_scroll(1, "units")

        rect_id, text_id, handle_id = self._reverse_map[self.dragging_event_id]
        x1, y1, x2, y2 = self.canvas.coords(rect_id)

        canvas_y = self.canvas.canvasy(event.y)

        if self.drag_mode == "move":
            height = y2 - y1
            new_y1 = clamp(canvas_y - self.drag_offset_y, 0, self.canvas_height - height)
            new_y2 = new_y1 + height

            self.canvas.coords(rect_id, x1, new_y1, x2, new_y2)
            self.canvas.coords(text_id, x1 + 8, new_y1 + 8)
            self.canvas.coords(handle_id, x2 - 12, new_y2 - 6, x2 - 4, new_y2 - 2)

        elif self.drag_mode == "resize":
            new_y2 = clamp(canvas_y, y1 + EVENT_MIN_HEIGHT, self.canvas_height)
            self.canvas.coords(rect_id, x1, y1, x2, new_y2)
            self.canvas.coords(handle_id, x2 - 12, new_y2 - 6, x2 - 4, new_y2 - 2)

    def on_release(self, event):
        if not self.dragging_event_id: return
        rect_id, text_id, handle_id = self._reverse_map[self.dragging_event_id]
        x1, y1, x2, y2 = self.canvas.coords(rect_id)
        start_time = y_to_time(y1); end_time = y_to_time(y2)
        start_dt = round_dt(datetime.combine(self.current_date, start_time)); end_dt = round_dt(datetime.combine(self.current_date, end_time))
        if end_dt <= start_dt: end_dt = start_dt + timedelta(minutes=MINUTE_STEP)
        conn = get_conn(); conn.execute("UPDATE events SET start_dt=?, end_dt=?, updated_at=? WHERE id=?", (start_dt.isoformat(), end_dt.isoformat(), now_iso(), self.dragging_event_id)); conn.commit(); conn.close()
        self.dragging_event_id = None; self.drag_mode = None; self._notify_change()

    def _init_now_dot_hooks(self):
        if not hasattr(self, "canvas"):
            return
        self._now_dot_job = None
        self._start_now_dot_timer()

    def _start_now_dot_timer(self):
        if getattr(self, "_now_dot_job", None):
            try:
                self.after_cancel(self._now_dot_job)
            except Exception:
                pass
        self._draw_now_dot()
        now = datetime.now()
        ms = (60 - now.second) * 1000
        self._now_dot_job = self.after(ms, self._start_now_dot_timer)

    def _draw_now_dot(self):
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return
        c = self.canvas
        c.delete("now_dot")
        now = datetime.now()
        if self.current_date != now.date():
            return
        y = time_to_y(now.time())
        x1 = 60
        r = 4
        width = max(self.winfo_width() or RIGHT_MIN_WIDTH, RIGHT_MIN_WIDTH)
        c.create_line(x1, y, width - 12, y, fill="red", tags="now_dot")
        c.create_oval(x1 - r, y - r, x1 + r, y + r, fill="red", outline="red", tags="now_dot")

    def _tick_notifications(self):
        now = datetime.now()
        start_day = datetime.combine(date.today(), time(0, 0))
        end_day = start_day + timedelta(days=1)
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, title, start_dt FROM events WHERE start_dt>=? AND start_dt<? ORDER BY start_dt",
            (start_day.isoformat(), end_day.isoformat())
        ).fetchall()
        conn.close()

        for r in rows:
            event_start_dt = datetime.fromisoformat(r["start_dt"])
            delta_seconds = (event_start_dt - now).total_seconds()
            event_time_str = event_start_dt.strftime('%H:%M')

            # Notify 5 minutes before
            if 240 < delta_seconds <= 300:
                self.master.event_generate("<<Notify>>", when="tail")
                self.notify_callback(f"Upcoming in 5 mins: {r['title']} at {event_time_str}")

            # Notify at start time
            elif 0 <= delta_seconds < 60:
                self.master.event_generate("<<Notify>>", when="tail")
                self.notify_callback(f"Starting now: {r['title']} at {event_time_str}")

        self.after(60000, self._tick_notifications)

class WeekView(BaseCalendarView):
    def __init__(self, master, on_change_callback=None):
        super().__init__(master, on_change_callback=on_change_callback)
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=8, pady=(8,0))
        self.week_label = tk.StringVar()
        self._now_dot_job = None
        self.now_dot_radius = 4

        ttk.Button(header, text="◀", width=3, command=self.prev_week).pack(side=tk.LEFT)
        ttk.Button(header, text="This week", command=self.this_week).pack(side=tk.LEFT, padx=6)
        ttk.Button(header, text="▶", width=3, command=self.next_week).pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.week_label, font=("TkDefaultFont", 12, "bold")).pack(side=tk.RIGHT)

        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        self.canvas = tk.Canvas(canvas_frame, bg="#fafafa", highlightthickness=0)

        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Control-Button-1>", self.on_right_click)  # macOS ctrl-click
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        self.monday = self._monday_of(date.today())
        self.dragging_event_id = None
        self.drag_offset = (0,0)  # (dx, dy)
        self.drag_rect_orig = None  # (x1,y1,x2,y2)
        self.context_menu = tk.Menu(self, tearoff=0)
        self.canvas_height = 0
        self._init_now_dot_hooks()
        self.canvas.bind("<Configure>", lambda e: self.refresh())
        self._start_now_dot_timer()
        self.refresh()

    def _init_now_dot_hooks(self):
        """Call this AFTER the week canvas has been created."""
        # If your canvas has another name (e.g. self.week_canvas), alias it:
        if not hasattr(self, "canvas"):
            # ↓↓↓ CHANGE THIS if your canvas is named differently
            if hasattr(self, "week_canvas"):
                self.canvas = self.week_canvas
            elif hasattr(self, "grid_canvas"):
                self.canvas = self.grid_canvas
            # else: leave as is; we'll guard below

        # Guard if we still don't have a canvas
        if not hasattr(self, "canvas"):
            return

        # one-time state
        self._now_dot_job = None
        self.now_dot_radius = 4

        # keep the dot positioned on resize
        # start minute-by-minute updates
        self._start_now_dot_timer()

    def _start_now_dot_timer(self):
        # cancel any previous timer
        if getattr(self, "_now_dot_job", None):
            try:
                self.after_cancel(self._now_dot_job)
            except Exception:
                pass
        # draw now and schedule next update at the next minute boundary
        self._draw_now_dot()
        now = datetime.now()
        ms = (60 - now.second) * 1000
        self._now_dot_job = self.after(ms, self._start_now_dot_timer)

    def _draw_now_dot(self):
        # bail out if canvas isn’t ready
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return

        c = self.canvas
        c.delete("now_dot")

        # only draw if 'today' is within the displayed week
        week_start = self.monday
        now = datetime.now()
        today = now.date()
        if not (week_start <= today <= week_start + timedelta(days=6)):
            return

        # column for today
        day_idx = (today - week_start).days
        x1, x2 = self._day_x_bounds(day_idx)

        # y using your existing grid mapping (starts at 00:00)
        y = time_to_y(now.time())

        # small red dot near the right edge of today’s column
        x = x2 - 8
        r = 4
        c.create_oval(x - r, y - r, x + r, y + r, fill="red", outline="red", tags=("now_dot",))

    def on_right_click(self, event):
        """
        Right-click on an event opens a context menu.
        """
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        overlaps = self.canvas.find_overlapping(canvas_x, canvas_y, canvas_x, canvas_y)
        item = overlaps[0] if overlaps else None
        if not item:
            return

        if item in self._event_items:
            val = self._event_items[item]
            event_data_row = val[0] if isinstance(val, tuple) else val
            event_data = dict(event_data_row)
            event_id = event_data.get("id")

            self.context_menu.delete(0, "end")

            if event_id:
                is_done = event_data.get("is_done", 0)
                label = "Mark as Not Done" if is_done else "Mark as Done"
                self.context_menu.add_command(label=label, command=lambda: self._toggle_event_done(event_id))
                self.context_menu.add_separator()

            #self.context_menu.add_command(label="Edit Task...", command=lambda: self._open_task_editor_for_event(event_data))
            self.context_menu.add_command(label="Edit Event...", command=lambda: self._open_event_editor(event_data))
            self.context_menu.add_command(label="Delete Event", command=lambda: self._delete_event(event_data))

            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    # Week navigation
    def _monday_of(self, d: date) -> date:
        return d - timedelta(days=(d.weekday()))  # Monday=0
    def this_week(self): self.monday = self._monday_of(date.today()); self.refresh()
    def prev_week(self): self.monday -= timedelta(days=7); self.refresh()
    def next_week(self): self.monday += timedelta(days=7); self.refresh()

    def refresh(self):
        self.canvas.delete("all")
        sunday = self.monday + timedelta(days=6)
        self.week_label.set(f"Week of {self.monday.strftime('%d %b %Y')} – {sunday.strftime('%d %b %Y')}")
        self._draw_grid(); self._draw_events(); self._draw_now_dot()

    # Layout calculations
    def _geom(self):
        total_minutes = (HOUR_END - HOUR_START) * 60
        total_steps = total_minutes // MINUTE_STEP
        height = int(total_steps * ROW_HEIGHT)
        width = max(self.winfo_width() or RIGHT_MIN_WIDTH, RIGHT_MIN_WIDTH)
        left_margin = 60
        right_margin = 12
        col_gap = 2
        col_width = (width - left_margin - right_margin - col_gap*6) // 7
        return width, height, left_margin, right_margin, col_width, col_gap

    def _day_x_bounds(self, day_idx):
        width, height, left, right, col_w, gap = self._geom()
        x1 = left + day_idx * (col_w + gap)
        x2 = x1 + col_w
        return x1, x2

    def _draw_grid(self):
        width, height, left, right, col_w, gap = self._geom()
        self.canvas_height = height
        self.canvas.config(scrollregion=(0, 0, width, self.canvas_height))
        # hours
        for h in range(HOUR_START, HOUR_END):
            y = time_to_y(time(hour=h, minute=0))
            self.canvas.create_line(left, y, width-right, y, fill="#ddd")
            self.canvas.create_text(left-8, y+2, text=f"{h:02d}:00", anchor="e", fill="#555")

        y_end = height
        self.canvas.create_line(left, y_end, width-right, y_end, fill="#ddd")
        self.canvas.create_text(left-8, y_end+2, text=f"{HOUR_END:02d}:00", anchor="e", fill="#555")

        for h in range(HOUR_START, HOUR_END):
            for m in (15, 30, 45):
                y = time_to_y(time(hour=h, minute=m))
                self.canvas.create_line(left, y, width-right, y, fill="#eee")
        # day columns + headers
        for d in range(7):
            x1, x2 = self._day_x_bounds(d)
            self.canvas.create_rectangle(x1, 0, x2, self.canvas_height, outline="#e5e5e5", width=1)
            label_date = (self.monday + timedelta(days=d)).strftime("%a %d %b")
            self.canvas.create_text((x1+x2)//2, 12, text=label_date, anchor="n", font=("TkDefaultFont", 10, "bold"))

    def _draw_events(self):
        self._event_items.clear(); self._reverse_map.clear()
        start_week = datetime.combine(self.monday, time(0,0))
        end_week = start_week + timedelta(days=7)
        conn = get_conn(); rows = conn.execute("SELECT * FROM events WHERE start_dt>=? AND start_dt<? ORDER BY start_dt", (start_week.isoformat(), end_week.isoformat())).fetchall(); conn.close()
        for r in rows: self._render_event(r)

    def _render_event(self, r):
        start = datetime.fromisoformat(r["start_dt"]) ; end = datetime.fromisoformat(r["end_dt"])
        day_idx = (start.date() - self.monday).days
        if not (0 <= day_idx < 7):
            return
        y1 = time_to_y(start.time()); y2 = max(y1 + EVENT_MIN_HEIGHT, time_to_y(end.time()))
        x1, x2 = self._day_x_bounds(day_idx)
        # add small horizontal insets
        x1i, x2i = x1+4, x2-4
        fill, outline = self._load_project_color(r["project_id"]) if "project_id" in r.keys() else ("#cde7ff","#58a6ff")
        rect = self.canvas.create_rectangle(x1i, y1, x2i, y2, fill=fill, outline=outline)
        label = ("‼ " if r["priority"]=="High" else ("· " if r["priority"]=="Low" else "")) + r["title"]
        text_color = "#333"
        if r["is_done"]:
            text_color = "#999"
        text = self.canvas.create_text(x1i+6, y1+6, text=label, anchor="nw", fill=text_color)
        if r["notes"]:
            self.canvas.create_text(x2i-6, y1+6, text="📝", anchor="ne", fill="#555")
        self._event_items[rect] = r; self._event_items[text] = r
        self._reverse_map.setdefault(r["id"], []).extend([rect, text])

    # Interaction
    def on_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        overlaps = self.canvas.find_overlapping(canvas_x, canvas_y, canvas_x, canvas_y)
        item = overlaps[0] if overlaps else None
        if item and item in self._event_items:
            r = self._event_items[item]
            self.dragging_event_id = r["id"]
            rect_id, text_id = self._reverse_map[self.dragging_event_id]
            x1, y1, x2, y2 = self.canvas.coords(rect_id)
            self.drag_offset = (self.canvas.canvasx(event.x) - x1, self.canvas.canvasy(event.y) - y1)
            self.drag_rect_orig = (x1, y1, x2, y2)
            return
        # create new from selected task at clicked day/time
        if not self.selected_task_id:
            return
        conn = get_conn(); task = conn.execute("SELECT * FROM tasks WHERE id=?", (self.selected_task_id,)).fetchone(); conn.close()
        if not task: return
        '''
        if not task["recurrence"]:
            conn = get_conn()
            existing_event = conn.execute("SELECT id FROM events WHERE task_id=?", (self.selected_task_id,)).fetchone()
            conn.close()
            if existing_event:
                messagebox.showerror("Already scheduled", "This non-recurring task is already scheduled.")
                return
        '''
        day_idx = self._day_idx_from_x(event.x)
        if day_idx is None: return
        slot_date = self.monday + timedelta(days=day_idx)
        t = y_to_time(self.canvas.canvasy(event.y))
        start_dt = round_dt(datetime.combine(slot_date, t))
        end_dt = start_dt + timedelta(minutes=int(task["duration_minutes"]))
        if task["recurrence"] in ("DAILY","WEEKLY"): self._create_series(task, start_dt)
        else: self._create_event(task["title"], task["id"], start_dt, end_dt, task["project_id"], task["priority"], task["notes"])

    def _day_idx_from_x(self, x):
        for d in range(7):
            x1, x2 = self._day_x_bounds(d)
            if x1 <= x <= x2:
                return d
        return None

    def on_drag(self, event):
        if not self.dragging_event_id: return

        # Auto-scroll
        scroll_margin = 30
        if event.y < scroll_margin:
            self.canvas.yview_scroll(-1, "units")
        elif event.y > self.canvas.winfo_height() - scroll_margin:
            self.canvas.yview_scroll(1, "units")

        rect_id, text_id = self._reverse_map[self.dragging_event_id]
        ox1, oy1, ox2, oy2 = self.drag_rect_orig

        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)

        dx = canvas_x - (ox1 + self.drag_offset[0])
        dy = canvas_y - (oy1 + self.drag_offset[1])

        height = oy2 - oy1
        width = ox2 - ox1

        new_x1 = ox1 + dx
        new_y1 = clamp(oy1 + dy, 0, self.canvas_height - height)
        new_x2 = new_x1 + width
        new_y2 = new_y1 + height

        self.canvas.coords(rect_id, new_x1, new_y1, new_x2, new_y2)
        self.canvas.coords(text_id, new_x1 + 6, new_y1 + 6)

    def on_release(self, event):
        if not self.dragging_event_id: return
        rect_id, text_id = self._reverse_map[self.dragging_event_id]
        x1, y1, x2, y2 = self.canvas.coords(rect_id)
        # snap horizontally to nearest day column by center
        center_x = (x1 + x2) / 2
        target_day = self._day_idx_from_x(center_x)
        if target_day is None:
            # If dropped outside, snap back to original day
            start_dt_orig = datetime.fromisoformat(self._event_items[rect_id]["start_dt"])
            target_day = (start_dt_orig.date() - self.monday).days

        # compute new start/end datetimes
        start_time = y_to_time(y1); end_time = y_to_time(y2)
        new_date = self.monday + timedelta(days=target_day)
        new_start_dt = round_dt(datetime.combine(new_date, start_time))
        new_end_dt = round_dt(datetime.combine(new_date, end_time))
        if new_end_dt <= new_start_dt:
            new_end_dt = new_start_dt + timedelta(minutes=MINUTE_STEP)
        # save
        conn = get_conn(); conn.execute("UPDATE events SET start_dt=?, end_dt=?, updated_at=? WHERE id=?", (new_start_dt.isoformat(), new_end_dt.isoformat(), now_iso(), self.dragging_event_id)); conn.commit(); conn.close()
        self.dragging_event_id = None; self.drag_rect_orig = None; self._notify_change()

# --------------------- ICS Export/Import ---------------------

def export_ics(filepath: str, events_rows):
    def fmt(dt: datetime): return dt.strftime("%Y%m%dT%H%M%S")
    lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//MyTime-Python//EN"]
    for r in events_rows:
        dtstart = datetime.fromisoformat(r["start_dt"]) ; dtend = datetime.fromisoformat(r["end_dt"])
        lines += ["BEGIN:VEVENT", f"SUMMARY:{r['title']}", f"DTSTART:{fmt(dtstart)}", f"DTEND:{fmt(dtend)}", f"UID:{r['id']}@mytime-python"]
        if r["notes"]:
            lines.append(f"DESCRIPTION:{r['notes']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(filepath, "w", encoding="utf-8") as f: f.write("".join(map(lambda l: l + '\r\n', lines)))



def import_ics(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f: data = f.read().splitlines()
    events = []; cur = {}
    for line in data:
        line = line.strip()
        if line == "BEGIN:VEVENT": cur = {}
        elif line == "END:VEVENT":
            if {"SUMMARY","DTSTART","DTEND"}.issubset(cur.keys()): events.append(cur)
            cur = {}
        else:
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.split(";",1)[0]
                cur[k] = v
    conn = get_conn(); cur = conn.cursor()
    for e in events:
        title = e.get("SUMMARY", "(No title)")
        notes = e.get("DESCRIPTION", "")
        try:
            s = dt_from_ics(e["DTSTART"]); en = dt_from_ics(e["DTEND"])
        except Exception as ex:
            print("Skip bad VEVENT:", ex, file=sys.stderr); continue
        cur.execute("INSERT INTO events(title, task_id, start_dt, end_dt, created_at, updated_at, project_id, priority, notes) VALUES (?,?,?,?,?,?,?,?,?)", (title, None, s.isoformat(), en.isoformat(), now_iso(), now_iso(), None, 'Normal', notes))
    conn.commit(); conn.close()

# --------------------- App Shell ---------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MyTime Planner V1.9.5")
        try:
            # Set application icon
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
            photo = tk.PhotoImage(file=icon_path)
            self.iconphoto(True, photo)
        except Exception as e:
            print(f"Could not load application icon: {e}", file=sys.stderr)
        self.geometry(f"{LEFT_WIDTH+RIGHT_MIN_WIDTH}x860")
        self.minsize(LEFT_WIDTH+RIGHT_MIN_WIDTH, 680)
        self.style = ttk.Style(self)
        if os.name == "nt": self.style.theme_use("vista")
        else: self.style.theme_use("clam")

        self.daily_span_days = DEFAULT_DAILY_SPAN_DAYS
        self.weekly_span_weeks = DEFAULT_WEEKLY_SPAN_WEEKS

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(paned, width=LEFT_WIDTH)
        right = ttk.Frame(paned)
        paned.add(left, weight=0); paned.add(right, weight=1)

        header = ttk.Frame(left); header.pack(fill=tk.X, padx=8, pady=(8,0))
        ttk.Label(header, text="Inbox", font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Projects", command=self._open_projects).pack(side=tk.RIGHT)

        self.tasks_panel = TasksPanel(left, on_task_selected=self.on_task_selected, on_new_from_template=self.refresh_all)
        self.tasks_panel.pack(fill=tk.BOTH, expand=True)

        # Right: Notebook with Day and Week views
        ttk.Label(right, text="Planner", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=8, pady=(8,0))
        self.tabs = ttk.Notebook(right)
        self.day_view = DayView(self.tabs, notify_callback=self._notify, on_change_callback=self.refresh_all)
        self.week_view = WeekView(self.tabs, on_change_callback=self.refresh_all)
        self.events_panel = EventsPanel(self.tabs, on_event_selected=self.on_event_selected)
        self.tabs.add(self.day_view, text="Day")
        self.tabs.add(self.week_view, text="Week")
        self.tabs.add(self.events_panel, text="All Events")
        self.tabs.pack(fill=tk.BOTH, expand=True)

        self._create_menu()
        self.refresh_all()
        self.bind("<plus>", self.on_plus_key)
        self.bind("<e>", self.on_e_key)
        self.bind("<d>", self.on_d_key)

    def _create_menu(self):
        menubar = tk.Menu(self)
        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="New Task", command=self.tasks_panel.add)
        filem.add_command(label="Export .ics (all)", command=self._export_all)
        filem.add_command(label="Export .ics (visible day)", command=self._export_day)
        filem.add_command(label="Import .ics", command=self._import_ics)
        filem.add_separator()
        filem.add_command(label="Quit", command=self.destroy)
        menubar.add_cascade(label="File", menu=filem)

        settings = tk.Menu(menubar, tearoff=0)
        settings.add_command(label="Recurrence Span…", command=self._set_recur_span)
        menubar.add_cascade(label="Settings", menu=settings)

        viewm = tk.Menu(menubar, tearoff=0)
        viewm.add_command(label="Day View", command=lambda: self.tabs.select(self.day_view))
        viewm.add_command(label="Week View", command=lambda: self.tabs.select(self.week_view))
        menubar.add_cascade(label="View", menu=viewm)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="About", command=lambda: messagebox.showinfo("About", "Structured-style Planner Tkinter + SQLite Day/Week views, ICS, recurrence, templates, colors, notifications"))
        menubar.add_cascade(label="Help", menu=helpm)
        self.config(menu=menubar)

    def on_plus_key(self, event=None):
        focused_widget = self.focus_get()
        if isinstance(focused_widget, (ttk.Entry, ttk.Spinbox, tk.Text)):
            return
        self.tasks_panel.add()

    def on_e_key(self, event=None):
        focused_widget = self.focus_get()
        if isinstance(focused_widget, (ttk.Entry, ttk.Spinbox, tk.Text)):
            return
        self.tasks_panel.edit()

    def on_d_key(self, event=None):
        focused_widget = self.focus_get()
        if isinstance(focused_widget, (ttk.Entry, ttk.Spinbox, tk.Text)):
            return
        self.tasks_panel.mark_as_done()

    def _open_projects(self):
        ProjectEditor(self, on_change=self.refresh_all)

    def _set_recur_span(self):
        d = simpledialog.askinteger("Daily span", "Number of days for DAILY series:", initialvalue=self.daily_span_days, minvalue=1, maxvalue=365, parent=self)
        if d: self.daily_span_days = d
        w = simpledialog.askinteger("Weekly span", "Number of weeks for WEEKLY series:", initialvalue=self.weekly_span_weeks, minvalue=1, maxvalue=104, parent=self)
        if w: self.weekly_span_weeks = w
        global DEFAULT_DAILY_SPAN_DAYS, DEFAULT_WEEKLY_SPAN_WEEKS
        DEFAULT_DAILY_SPAN_DAYS = self.daily_span_days
        DEFAULT_WEEKLY_SPAN_WEEKS = self.weekly_span_weeks

    def _export_all(self):
        path = filedialog.asksaveasfilename(defaultextension=".ics", filetypes=[("iCal files","*.ics")], title="Export all events")
        if not path: return
        conn = get_conn(); rows = conn.execute("SELECT * FROM events ORDER BY start_dt").fetchall(); conn.close()
        export_ics(path, rows); messagebox.showinfo("Export", f"Exported {len(rows)} events to {path}")

    def _export_day(self):
        path = filedialog.asksaveasfilename(defaultextension=".ics", filetypes=[("iCal files","*.ics")], title="Export visible day")
        if not path: return
        start_day = datetime.combine(self.day_view.current_date, time(0,0)); end_day = start_day + timedelta(days=1)
        conn = get_conn(); rows = conn.execute("SELECT * FROM events WHERE start_dt>=? AND start_dt<? ORDER BY start_dt", (start_day.isoformat(), end_day.isoformat())).fetchall(); conn.close()
        export_ics(path, rows); messagebox.showinfo("Export", f"Exported {len(rows)} events to {path}")
    def _import_ics(self):
        path = filedialog.askopenfilename(filetypes=[("iCal files","*.ics"),("All","*.*")], title=".ics")
        if not path: return
        try:
            import_ics(path)
        except Exception as ex:
            messagebox.showerror("Import failed", str(ex)); return
        self.refresh_all(); messagebox.showinfo("Import", "Import completed.")

    def _notify(self, text: str):
        self.bell()
        messagebox.showwarning("Reminder", text)

    def on_task_selected(self, tid):
        self.day_view.set_selected_task(tid)
        self.week_view.set_selected_task(tid)

        conn = get_conn()
        event = conn.execute(
            "SELECT start_dt FROM events WHERE task_id=? AND start_dt >= ? ORDER BY start_dt ASC LIMIT 1",
            (tid, datetime.now().isoformat())
        ).fetchone()

        if not event:
            event = conn.execute(
                "SELECT start_dt FROM events WHERE task_id=? ORDER BY start_dt DESC LIMIT 1",
                (tid,)
            ).fetchone()

        conn.close()

        if event:
            event_date = datetime.fromisoformat(event['start_dt']).date()

            self.day_view.current_date = event_date
            self.day_view.refresh()

            self.week_view.monday = self.week_view._monday_of(event_date)
            self.week_view.refresh()

            self.tabs.select(self.day_view)

    def on_event_selected(self, event_id):
        conn = get_conn()
        event = conn.execute("SELECT start_dt FROM events WHERE id=?", (event_id,)).fetchone()
        conn.close()
        if not event:
            return

        event_date = datetime.fromisoformat(event['start_dt']).date()

        self.day_view.current_date = event_date
        self.day_view.refresh()

        self.week_view.monday = self.week_view._monday_of(event_date)
        self.week_view.refresh()

        self.tabs.select(self.day_view)

    def refresh_all(self):
        self.tasks_panel.refresh(); self.day_view.refresh(); self.week_view.refresh(); self.events_panel.refresh()


if __name__ == "__main__":
    init_db()
    App().mainloop()
