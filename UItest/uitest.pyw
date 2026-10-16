"""UItest Manager - a thin management shell over an installed Sandboxie Plus.

This tool drives the official core (driver SbieDrv + service SbieSvc) through
the shipped SbieDll.dll and coexists with the official SandMan UI, which
keeps the signed session-leader role (message popups, monitoring, interactive
queue).  It never replaces, modifies or competes with SandMan.

Run without a console window:
    pythonw UItest\\uitest.pyw

Options:
    --sbie-home PATH    Sandboxie installation directory (auto-detected if omitted)
    --selftest SECONDS  open the window, refresh once, then close (smoke test)
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from sbie_api import SbieAPI, SbieError

REFRESH_MS = 3000


class UItestApp:
    """Two lists: sandboxes on the left, their processes on the right."""

    def __init__(self, root, api):
        self.root = root
        self.api = api

        # We are an outer management shell: the official SandMan UI keeps
        # the signed session-leader role (message popups, monitoring,
        # interactive queue), so this tool never tries to take it over.
        self.sandman_pid = None

        self._build_ui()
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)

    # -- UI construction -------------------------------------------------

    def _build_ui(self):
        self.root.title("UItest Manager - Sandboxie Plus management shell")
        self.root.geometry("900x520")
        self.root.minsize(700, 400)

        toolbar = ttk.Frame(self.root, padding=(6, 4))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.btn_refresh = ttk.Button(toolbar, text="Refresh", command=self.refresh)
        self.btn_refresh.pack(side=tk.LEFT)

        self.btn_run = ttk.Button(toolbar, text="Run in Box...",
                                  command=self.on_run, state=tk.DISABLED)
        self.btn_run.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_kill = ttk.Button(toolbar, text="Terminate Process",
                                   command=self.on_kill, state=tk.DISABLED)
        self.btn_kill.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_new = ttk.Button(toolbar, text="New Box...", command=self.on_new_box)
        self.btn_new.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_edit = ttk.Button(toolbar, text="Edit Config...",
                                   command=self.on_edit_config, state=tk.DISABLED)
        self.btn_edit.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_delete = ttk.Button(toolbar, text="Delete Box",
                                     command=self.on_delete_box, state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_sandman = ttk.Button(toolbar, text="Open SandMan",
                                      command=self.on_open_sandman)
        self.btn_sandman.pack(side=tk.LEFT, padx=(6, 0))

        self.lbl_status = ttk.Label(toolbar, text="Connecting...")
        self.lbl_status.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=4)

        # left: sandbox list
        left = ttk.Frame(paned, padding=(0, 0, 4, 0))
        paned.add(left, weight=1)
        ttk.Label(left, text="Sandboxes").pack(anchor=tk.W)
        self.tree_boxes = ttk.Treeview(left, columns=("box",), show="headings",
                                       selectmode="browse")
        self.tree_boxes.heading("box", text="Box")
        self.tree_boxes.column("box", width=200, anchor=tk.W)
        self.tree_boxes.pack(fill=tk.BOTH, expand=True)
        self.tree_boxes.bind("<<TreeviewSelect>>", self._on_box_selected)

        # right: process list of the selected sandbox
        right = ttk.Frame(paned, padding=(4, 0, 0, 0))
        paned.add(right, weight=3)
        ttk.Label(right, text="Processes (selected sandbox)").pack(anchor=tk.W)
        self.tree_procs = ttk.Treeview(right, columns=("pid", "image", "session"),
                                       show="headings", selectmode="browse")
        for col, title, width, anchor in (("pid", "PID", 80, tk.E),
                                          ("image", "Image", 340, tk.W),
                                          ("session", "Session", 80, tk.E)):
            self.tree_procs.heading(col, text=title)
            self.tree_procs.column(col, width=width, anchor=anchor)
        self.tree_procs.pack(fill=tk.BOTH, expand=True)
        self.tree_procs.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())

        self.lbl_footer = ttk.Label(self.root, anchor=tk.W, padding=(8, 2))
        self.lbl_footer.pack(side=tk.BOTTOM, fill=tk.X)

    # -- helpers ---------------------------------------------------------

    def selected_box(self):
        selection = self.tree_boxes.selection()
        if not selection:
            return None
        return self.tree_boxes.item(selection[0], "values")[0]

    def selected_pid(self):
        selection = self.tree_procs.selection()
        if not selection:
            return None
        return int(self.tree_procs.item(selection[0], "values")[0])

    def _on_box_selected(self, _event=None):
        self._fill_processes()
        self._update_buttons()

    def _update_buttons(self):
        self.btn_run.config(state=tk.NORMAL if self.selected_box() else tk.DISABLED)
        self.btn_kill.config(state=tk.NORMAL if self.selected_pid() is not None
                             else tk.DISABLED)
        has_box = self.selected_box() is not None
        self.btn_edit.config(state=tk.NORMAL if has_box else tk.DISABLED)
        self.btn_delete.config(state=tk.NORMAL if has_box else tk.DISABLED)

    # -- SandMan integration ------------------------------------------------

    def sandman_exe(self):
        """Path to the official SandMan UI next to the core files."""
        return os.path.join(self.api.home, "SandMan.exe")

    def sandman_running(self):
        """Return the SandMan.exe pid if it is running, else None."""
        tasklist = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32", "tasklist.exe")
        try:
            out = subprocess.run(
                [tasklist, "/FI", "IMAGENAME eq SandMan.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.stdout.splitlines():
            parts = line.strip('"').split('","')
            if parts and parts[0].lower() == "sandman.exe":
                try:
                    return int(parts[1])
                except (IndexError, ValueError):
                    return 0
        return None

    def on_open_sandman(self):
        """Start or activate the official SandMan UI.

        SandMan is a single-instance Qt application: launching SandMan.exe
        while it is already running hands the request to the existing
        instance (ShowWnd) and exits, so this button both starts and
        activates the official UI.  SandMan keeps the signed session-leader
        role (message popups, monitoring, interactive queue) that this
        management shell cannot take over.
        """
        sandman = self.sandman_exe()
        if not os.path.isfile(sandman):
            messagebox.showwarning(
                "Open SandMan",
                f"SandMan.exe was not found next to the core:\n{sandman}\n\n"
                "Keep/install the official Sandboxie Plus UI for "
                "session-leader features (message popups, monitoring).")
            return
        try:
            subprocess.Popen([sandman])
        except OSError as e:
            messagebox.showerror("Open SandMan", str(e))

    # -- refresh logic ---------------------------------------------------

    def refresh(self):
        """Reload sandboxes and the process list of the selected sandbox."""
        try:
            version = self.api.version()
            boxes = self.api.enum_boxes()
        except SbieError as e:
            self.lbl_status.config(text=f"Not connected: {e}")
            return

        sandman_pid = self.sandman_running()
        self.sandman_pid = sandman_pid
        leader = None
        try:
            leader = self.api.session_leader()
        except SbieError:
            pass
        sandman_txt = (f"running (pid {sandman_pid})" if sandman_pid is not None
                       else "NOT running (start it for messages/monitoring)")
        leader_txt = str(leader) if leader else "-"
        self.lbl_status.config(
            text=(f"Driver {version} Connected | SandMan: {sandman_txt} "
                  f"| session UI: {leader_txt}"))
        self._fill_boxes(boxes)
        self._fill_processes()
        self._update_buttons()
        self.lbl_footer.config(
            text=(f"{len(boxes)} box(es) | Sandboxie Plus: {self.api.home} | "
                  f"last refresh {time.strftime('%H:%M:%S')}"))

    def _auto_refresh(self):
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)

    def _fill_boxes(self, boxes):
        current = self.selected_box()
        self.tree_boxes.delete(*self.tree_boxes.get_children())
        for box in boxes:
            self.tree_boxes.insert("", tk.END, values=(box,))

        target = current if current in boxes else (boxes[0] if boxes else None)
        if target:
            for item in self.tree_boxes.get_children():
                if self.tree_boxes.item(item, "values")[0] == target:
                    self.tree_boxes.selection_set(item)
                    break

    def _fill_processes(self):
        current = self.selected_pid()
        self.tree_procs.delete(*self.tree_procs.get_children())

        box = self.selected_box()
        if not box:
            return
        try:
            pids = self.api.box_processes(box)
        except SbieError as e:
            self.lbl_status.config(text=f"Not connected: {e}")
            return

        for pid in pids:
            try:
                info = self.api.query_process(pid)
            except SbieError:
                continue    # process exited between the two calls
            self.tree_procs.insert("", tk.END,
                                   values=(pid, info["image"], info["session"]))

        if current is not None:
            for item in self.tree_procs.get_children():
                if int(self.tree_procs.item(item, "values")[0]) == current:
                    self.tree_procs.selection_set(item)
                    break

    # -- actions ---------------------------------------------------------

    def on_run(self):
        """Ask for a program and start it inside the selected sandbox."""
        box = self.selected_box()
        if not box:
            messagebox.showwarning("Run in Box", "Select a sandbox first.")
            return
        path = filedialog.askopenfilename(
            title=f"Run in sandbox '{box}'",
            filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.api.run_in_box(subprocess.list2cmdline([path]), box)
        except SbieError as e:
            messagebox.showerror("Run in Box", str(e))
        self.root.after(800, self.refresh)

    def on_kill(self):
        """Terminate the selected sandboxed process."""
        pid = self.selected_pid()
        if pid is None:
            return
        if not messagebox.askyesno("Terminate Process", f"Terminate process {pid}?"):
            return
        try:
            self.api.kill_process(pid)
        except SbieError as e:
            messagebox.showerror("Terminate Process", str(e))
        self.root.after(300, self.refresh)

    # -- box management ---------------------------------------------------

    def on_new_box(self):
        """Create a new sandbox."""
        name = simpledialog.askstring(
            "New Box", "Sandbox name (letters, digits, '_'):", parent=self.root)
        if name is None:
            return
        try:
            self.api.create_box(name.strip())
        except (SbieError, ValueError) as e:
            messagebox.showerror("New Box", str(e))
            return
        self.refresh()

    def on_delete_box(self):
        """Remove the selected sandbox config (optionally the content too)."""
        box = self.selected_box()
        if not box:
            return
        delete_content = messagebox.askyesno(
            "Delete Box",
            f"Delete the sandbox '{box}'?\n\n"
            "The configuration section will be removed from Sandboxie.ini.\n\n"
            "Also delete the box content folder? (No keeps the files)")
        if delete_content is None:
            return
        try:
            removed_content = self.api.delete_box_content(box) if delete_content else False
            self.api.remove_box(box)
        except (SbieError, OSError) as e:
            messagebox.showerror("Delete Box", str(e))
            return
        self.refresh()
        messagebox.showinfo(
            "Delete Box",
            f"Sandbox '{box}' removed"
            + (" (content deleted)" if removed_content else " (content kept)"))

    def on_edit_config(self):
        """Open the config editor for the selected sandbox."""
        box = self.selected_box()
        if box:
            self._open_config_editor(box)

    def _open_config_editor(self, box):
        try:
            section = self.api.get_section(box)
        except SbieError as e:
            messagebox.showerror("Edit Config", str(e))
            return

        win = tk.Toplevel(self.root)
        win.title(f"Edit config: {box}")
        win.geometry("680x500")
        win.transient(self.root)

        frame = ttk.Frame(win, padding=6)
        frame.pack(fill=tk.BOTH, expand=True)

        tree = ttk.Treeview(frame, columns=("setting", "value"), show="headings",
                            selectmode="extended")
        tree.heading("setting", text="Setting")
        tree.column("setting", width=200, anchor=tk.W)
        tree.heading("value", text="Value")
        tree.column("value", width=420, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True)

        def load_rows():
            tree.delete(*tree.get_children())
            for setting, values in section.items():
                for value in values:
                    tree.insert("", tk.END, values=(setting, value))

        def add_row():
            tree.insert("", tk.END, values=("", ""))

        def delete_rows():
            for item in tree.selection():
                tree.delete(item)

        def save():
            nonlocal section
            entries = [tree.item(item, "values") for item in tree.get_children()]
            entries = [entry for entry in entries if (entry[0] or "").strip()]
            try:
                self.api.update_section(box, entries)
                self.api.reload_conf()
            except (SbieError, ValueError) as e:
                messagebox.showerror("Save Config", str(e))
                try:
                    section = self.api.get_section(box)
                except SbieError:
                    section = {}
                load_rows()
                return
            win.destroy()
            self.refresh()

        load_rows()

        bar = ttk.Frame(frame)
        bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(bar, text="Add Row", command=add_row).pack(side=tk.LEFT)
        ttk.Button(bar, text="Delete Row(s)", command=delete_rows).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="Save", command=save).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(
            side=tk.RIGHT, padx=(6, 0))


def load_api(root, sbie_home):
    """Load the bridge; if auto-detection fails, ask the user for the folder."""
    try:
        return SbieAPI.load(sbie_home)
    except (OSError, FileNotFoundError) as e:
        messagebox.showinfo(
            "UItest",
            f"Sandboxie was not found automatically.\n\n{e}\n\n"
            "Select the Sandboxie installation folder (the one that contains "
            "SbieDll.dll), or cancel to exit.")
        folder = filedialog.askdirectory(
            title="Select the Sandboxie installation folder")
        if not folder:
            return None
        try:
            return SbieAPI.load(folder)
        except (OSError, FileNotFoundError) as e2:
            messagebox.showerror("UItest", f"Cannot load SbieDll.dll:\n{e2}")
            return None


def main():
    parser = argparse.ArgumentParser(description="UItest Manager - Sandboxie Plus management shell")
    parser.add_argument("--sbie-home",
                        help="Sandboxie installation directory (auto-detected if omitted)")
    parser.add_argument("--selftest", type=int, metavar="SECONDS",
                        help="open the window, refresh once, then close (smoke test)")
    args = parser.parse_args()

    try:
        # crisper text on high-DPI displays (best effort)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()

    api = load_api(root, args.sbie_home)
    if api is None:
        return 1

    try:
        api.version()
    except SbieError as e:
        messagebox.showerror(
            "UItest",
            f"The Sandboxie core is not reachable:\n{e}\n\n"
            "The driver (SbieDrv) and service (SbieSvc) must be installed and "
            "running. Use SandMan's Maintenance menu to install/start them, "
            "or reinstall Sandboxie.")
        return 1

    UItestApp(root, api)
    root.deiconify()
    if args.selftest:
        root.after(args.selftest * 1000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
