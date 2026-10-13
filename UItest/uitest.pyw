"""UItest - minimal tkinter UI for an installed Sandboxie.

This is a test harness for the UItest branch; see README.md for limitations.

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
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from sbie_api import SbieAPI, SbieError

REFRESH_MS = 3000
MESSAGE_POLL_MS = 1000
IGNORED_MESSAGE_IDS = {1399, 2198, 1321}    # process start / file migration / forced

SBIESVC_KEY = r"SYSTEM\CurrentControlSet\Services\SbieSvc"
DEFAULT_LOG_PATH = r"C:\Windows\System32\LogFiles\Sandboxie.log"


class UItestApp:
    """Two lists: sandboxes on the left, their processes on the right."""

    def __init__(self, root, api):
        self.root = root
        self.api = api

        # Try to become the session UI so that driver messages (for example
        # "sandboxed program has crashed", SBIE2224) are shown by this app
        # instead of the official SandMan.  The driver only allows this for
        # trusted (signed) processes, or any process while Windows
        # test-signing is enabled; if it fails, SandMan keeps showing them.
        self.is_leader = False
        self._messages_primed = False
        try:
            self.is_leader = api.take_over()
        except SbieError:
            pass

        # Without leadership the driver refuses to hand us the message log,
        # so fall back to the plain-text log file the service can write when
        # HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc\LogFile is set.
        self._log_path = None
        self._log_pos = 0
        self._log_buffer = ""
        if not self.is_leader:
            self._log_path = api.log_file_path()
            if self._log_path:
                try:
                    self._log_pos = os.path.getsize(self._log_path)
                except OSError:
                    self._log_pos = 0

        self._build_ui()
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)
        self.root.after(MESSAGE_POLL_MS, self._poll_messages)

    # -- UI construction -------------------------------------------------

    def _build_ui(self):
        self.root.title("UItest - Sandboxie minimal UI")
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

    # -- refresh logic ---------------------------------------------------

    def refresh(self):
        """Reload sandboxes and the process list of the selected sandbox."""
        try:
            version = self.api.version()
            boxes = self.api.enum_boxes()
        except SbieError as e:
            self.lbl_status.config(text=f"Not connected: {e}")
            return

        if self.is_leader:
            source = "messages: UItest (api)"
        elif self._log_path:
            source = "messages: UItest (log file)"
        else:
            source = "messages: other UI"
        self.lbl_status.config(text=f"Driver {version}   Connected   [{source}]")
        self._fill_boxes(boxes)
        self._fill_processes()
        self._update_buttons()
        self.lbl_footer.config(
            text=f"{len(boxes)} box(es) | last refresh {time.strftime('%H:%M:%S')}")

    def _auto_refresh(self):
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)

    def _poll_messages(self):
        """Show new driver messages with our own tkinter dialogs.

        Two sources are supported: the driver message log when this process
        is the session leader (needs a trusted/signed build), otherwise the
        service log file when the LogFile registry value is configured.
        """
        if self.is_leader:
            self._poll_messages_api()
        else:
            if not self._log_path:      # late setup (e.g. UAC accepted late)
                self._log_path = self.api.log_file_path()
                if self._log_path:
                    try:
                        self._log_pos = os.path.getsize(self._log_path)
                    except OSError:
                        self._log_pos = 0
            if self._log_path:
                self._poll_messages_file()

        self.root.after(MESSAGE_POLL_MS, self._poll_messages)

    def _poll_messages_api(self):
        try:
            messages = self.api.get_messages()
        except SbieError:
            self.is_leader = False      # lost (or never had) leadership
            messages = []

        if not self._messages_primed:
            self._messages_primed = True    # drop the backlog on startup
            return

        for message in messages:
            if message["id"] in IGNORED_MESSAGE_IDS:
                continue
            text = self.api.format_message(message["code"], message["strings"])
            if message["pid"]:
                text += f"\n\n(process id {message['pid']})"
            messagebox.showwarning("Sandboxie message", text, parent=self.root)

    def _poll_messages_file(self):
        """Show new lines from the service log file (UTF-16LE, CRLF)."""
        try:
            size = os.path.getsize(self._log_path)
            if size < self._log_pos:            # file was recreated/truncated
                self._log_pos = 0
                self._log_buffer = ""
            if size == self._log_pos:
                return
            with open(self._log_path, "rb") as handle:
                handle.seek(self._log_pos)
                data = handle.read()
                self._log_pos = handle.tell()
        except OSError:
            return

        if self._log_pos % 2:                   # keep UTF-16 alignment
            self._log_pos -= 1

        self._log_buffer += data.decode("utf-16-le", errors="replace")
        while "\r\n" in self._log_buffer:
            line, self._log_buffer = self._log_buffer.split("\r\n", 1)
            if line.strip():
                messagebox.showwarning("Sandboxie message", line.strip(),
                                       parent=self.root)

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


# ---------------------------------------------------------------------------
# one-time setup: let UItest show messages itself


def _find_plus_ini(home):
    """Return the Sandboxie-Plus.ini path used by SandMan, or None."""
    for candidate in (os.path.join(home, "PlusData", "Sandboxie-Plus.ini"),
                      os.path.join(home, "Sandboxie-Plus.ini")):
        if os.path.isfile(candidate):
            return candidate
    return None


def _disable_sandman_messages(ini_path):
    """Set [UIConfig] DisableMessages=true in Sandboxie-Plus.ini.

    Stops SandMan from showing its own popup when UItest displays the
    messages from the service log file.  Formatting and encoding of the
    file are preserved; returns True when it is (now) configured.
    """
    try:
        with open(ini_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return False

    text = None
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return False

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)

    section = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section is not None:
                break                       # end of the [UIConfig] section
            if stripped[1:-1].strip().lower() == "uiconfig":
                section = index
            continue
        if section is not None:
            key, _, value = line.partition("=")
            if key.strip().lower() == "disablemessages":
                if value.strip().lower() == "true":
                    return True             # already configured
                lines[index] = "DisableMessages=true"
                break
    else:
        if section is None:
            lines += ["", "[UIConfig]", "DisableMessages=true"]
        else:
            lines.insert(section + 1, "DisableMessages=true")

    try:
        with open(ini_path, "wb") as handle:
            handle.write(newline.join(lines).encode(
                encoding if encoding != "utf-8-sig" else "utf-8"))
    except OSError:
        return False
    return True


def _ensure_service_log_file(api, log_path):
    """Make the service write its messages to `log_path`.

    Reads the LogFile registry value first; only when it is missing does this
    ask for elevation (one UAC prompt) to add it.  Returns the configured
    path, or None when the value could not be set.
    """
    if api.log_file_path():
        return api.log_file_path()

    params = f'add "{SBIESVC_KEY}" /t REG_SZ /v LogFile /d "2;{log_path}" /f'
    reg_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "reg.exe")
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_int]
    result = shell32.ShellExecuteW(None, "runas", reg_exe, params, None, 0) or 0
    if result <= 32:
        return None                     # user cancelled the UAC prompt

    for _ in range(30):                 # wait for the elevated reg.exe
        time.sleep(0.5)
        if api.log_file_path():
            break
    return api.log_file_path()


def setup_message_channel(api, log_path=None):
    """Best-effort setup so messages appear in our own style.

    * ensure the service writes messages to a log file (may raise one UAC
      prompt, only when the registry value is not set yet)
    * stop SandMan from showing its own message popups (SandMan must be
      restarted for that to take effect)
    """
    log_path = _ensure_service_log_file(api, log_path or DEFAULT_LOG_PATH)
    ini_path = _find_plus_ini(api.home)
    if ini_path:
        _disable_sandman_messages(ini_path)
    return log_path


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
    parser = argparse.ArgumentParser(description="UItest - Sandboxie minimal UI")
    parser.add_argument("--sbie-home",
                        help="Sandboxie installation directory (auto-detected if omitted)")
    parser.add_argument("--selftest", type=int, metavar="SECONDS",
                        help="open the window, refresh once, then close (smoke test)")
    parser.add_argument("--log-path", metavar="PATH",
                        help=f"service message log file (default: {DEFAULT_LOG_PATH})")
    parser.add_argument("--no-setup", action="store_true",
                        help="skip the automatic message-channel setup (no UAC prompt)")
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

    if not args.no_setup:
        try:
            setup_message_channel(api, args.log_path)
        except Exception:               # setup must never block the UI
            pass

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