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

# 配置编辑器：勾选式布尔选项（设置名 -> 中文标签），分类排列。
# 设置名与 SandMan 的 Options* 页面一致（见 SandboxiePlus/SandMan/Windows/）。
CFG_OPTION_CATEGORIES = [
    ("常规", [
        ("Enabled", "启用此沙箱（取消勾选将禁用沙箱）"),
        ("AutoDelete", "自动清空沙箱内容"),
        ("AutoRemove", "沙箱内容清空后自动移除沙箱"),
        ("NeverDelete", "禁止删除沙箱内容"),
        ("NeverRemove", "禁止移除沙箱"),
        ("SeparateUserFolders", "隔离用户文件夹"),
    ]),
    ("安全与权限", [
        ("DropAdminRights", "降低管理员权限"),
        ("FakeAdminRights", "伪管理员权限"),
        ("UseSecurityMode", "安全增强模式"),
        ("SysCallLockDown", "系统调用封锁"),
        ("RestrictDevices", "限制设备访问"),
        ("MsiInstallerExemptions", "MSI 安装器豁免"),
        ("OpenCredentials", "允许访问凭据"),
        ("OpenClipboard", "允许访问剪贴板"),
        ("ClosePrintSpooler", "关闭打印后台服务"),
        ("AllowSpoolerPrintToFile", "允许打印到文件"),
        ("BlockInterferePower", "阻止干扰电源管理"),
        ("NotifyProcessAccessDenied", "进程访问被拒时通知"),
        ("UseOriginalACLs", "使用原始 ACL"),
    ]),
    ("网络", [
        ("BlockNetworkFiles", "阻止网络文件/共享"),
        ("BlockNetParam", "阻止网络参数"),
        ("PromptForInternetAccess", "程序联网时提示"),
        ("NotifyInternetAccessDenied", "联网被拒时通知"),
    ]),
    ("恢复与清理", [
        ("AutoRecover", "自动恢复文件"),
        ("UseAutoRecoverIgnoreForQuick", "快速恢复时应用忽略规则"),
    ]),
    ("高级", [
        ("UsePrivacyMode", "隐私模式"),
        ("NoSecurityIsolation", "关闭安全隔离"),
        ("NoSecurityFiltering", "关闭安全过滤"),
        ("DisableForceRules", "禁用强制入沙规则"),
        ("BlockScreenCapture", "阻止屏幕捕获"),
        ("HideDiskSerialNumber", "隐藏磁盘序列号"),
        ("HideNetworkAdapterMAC", "隐藏网卡 MAC"),
        ("HideFirmwareInfo", "隐藏固件信息"),
        ("HideNonSystemProcesses", "隐藏非系统进程"),
        ("StripSystemPrivileges", "剥离系统特权"),
        ("UseSandboxDesktop", "使用沙箱桌面"),
        ("UseFileImage", "使用镜像文件沙箱"),
    ]),
]


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
        self.root.title("UItest 管理器 - 基于 Sandboxie Plus 的管理壳")
        self.root.geometry("900x520")
        self.root.minsize(700, 400)

        toolbar = ttk.Frame(self.root, padding=(6, 4))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.btn_refresh = ttk.Button(toolbar, text="刷新", command=self.refresh)
        self.btn_refresh.pack(side=tk.LEFT)

        self.btn_run = ttk.Button(toolbar, text="在沙箱中运行...",
                                  command=self.on_run, state=tk.DISABLED)
        self.btn_run.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_kill = ttk.Button(toolbar, text="终止进程",
                                   command=self.on_kill, state=tk.DISABLED)
        self.btn_kill.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_new = ttk.Button(toolbar, text="新建沙箱...", command=self.on_new_box)
        self.btn_new.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_edit = ttk.Button(toolbar, text="编辑配置...",
                                   command=self.on_edit_config, state=tk.DISABLED)
        self.btn_edit.pack(side=tk.LEFT, padx=(6, 0))

        self.btn_delete = ttk.Button(toolbar, text="删除沙箱",
                                     command=self.on_delete_box, state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=(6, 0))
        self.btn_sandman = ttk.Button(toolbar, text="打开 SandMan",
                                      command=self.on_open_sandman)
        self.btn_sandman.pack(side=tk.LEFT, padx=(6, 0))

        self.lbl_status = ttk.Label(toolbar, text="连接中...")
        self.lbl_status.pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=4)

        # left: sandbox list
        left = ttk.Frame(paned, padding=(0, 0, 4, 0))
        paned.add(left, weight=1)
        ttk.Label(left, text="沙箱").pack(anchor=tk.W)
        self.tree_boxes = ttk.Treeview(left, columns=("box", "state"), show="headings",
                                       selectmode="browse")
        self.tree_boxes.heading("box", text="名称")
        self.tree_boxes.column("box", width=200, anchor=tk.W)
        self.tree_boxes.heading("state", text="状态")
        self.tree_boxes.column("state", width=90, anchor=tk.W)
        self.tree_boxes.pack(fill=tk.BOTH, expand=True)
        self.tree_boxes.bind("<<TreeviewSelect>>", self._on_box_selected)

        # right: process list of the selected sandbox
        right = ttk.Frame(paned, padding=(4, 0, 0, 0))
        paned.add(right, weight=3)
        ttk.Label(right, text="进程（所选沙箱）").pack(anchor=tk.W)
        self.tree_procs = ttk.Treeview(right, columns=("pid", "image", "session"),
                                       show="headings", selectmode="browse")
        for col, title, width, anchor in (("pid", "PID", 80, tk.E),
                                          ("image", "映像", 340, tk.W),
                                          ("session", "会话", 80, tk.E)):
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
                "打开 SandMan",
                f"未在核心目录找到 SandMan.exe：\n{sandman}\n\n"
                "请保留/安装官方 Sandboxie Plus 界面，以获得会话级功能"
                "（消息弹窗、监控）。")
            return
        try:
            subprocess.Popen([sandman])
        except OSError as e:
            messagebox.showerror("打开 SandMan", str(e))

    # -- refresh logic ---------------------------------------------------

    def refresh(self):
        """Reload sandboxes and the process list of the selected sandbox."""
        try:
            version = self.api.version()
            boxes = self.api.list_boxes()
        except SbieError as e:
            self.lbl_status.config(text=f"连接失败：{e}")
            return

        sandman_pid = self.sandman_running()
        self.sandman_pid = sandman_pid
        leader = None
        try:
            leader = self.api.session_leader()
        except SbieError:
            pass
        sandman_txt = (f"运行中（PID {sandman_pid}）" if sandman_pid is not None
                       else "未运行（建议启动以获得消息/监控）")
        leader_txt = str(leader) if leader else "-"
        self.lbl_status.config(
            text=(f"驱动 {version} 已连接 | SandMan：{sandman_txt} "
                  f"| 会话界面：{leader_txt}"))
        self._fill_boxes(boxes)
        self._fill_processes()
        self._update_buttons()
        self.lbl_footer.config(
            text=(f"{len(boxes)} 个沙箱 | Sandboxie Plus：{self.api.home} | "
                  f"刷新于 {time.strftime('%H:%M:%S')}"))

    def _auto_refresh(self):
        self.refresh()
        self.root.after(REFRESH_MS, self._auto_refresh)

    def _fill_boxes(self, boxes):
        current = self.selected_box()
        self.tree_boxes.delete(*self.tree_boxes.get_children())
        for name, enabled in boxes:
            self.tree_boxes.insert("", tk.END,
                               values=(name, "" if enabled else "已禁用"))
        target = current if current in [b for b, _ in boxes] else (boxes[0][0] if boxes else None)
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
            self.lbl_status.config(text=f"连接失败：{e}")
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
            messagebox.showwarning("在沙箱中运行", "请先选择一个沙箱。")
            return
        path = filedialog.askopenfilename(
            title=f"在沙箱 '{box}' 中运行",
            filetypes=[("程序", "*.exe"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.api.run_in_box(subprocess.list2cmdline([path]), box)
        except SbieError as e:
            messagebox.showerror("在沙箱中运行", str(e))
        self.root.after(800, self.refresh)

    def on_kill(self):
        """Terminate the selected sandboxed process."""
        pid = self.selected_pid()
        if pid is None:
            return
        if not messagebox.askyesno("终止进程", f"确定终止进程 {pid} 吗？"):
            return
        try:
            self.api.kill_process(pid)
        except SbieError as e:
            messagebox.showerror("终止进程", str(e))
        self.root.after(300, self.refresh)

    # -- box management ---------------------------------------------------

    def on_new_box(self):
        """Create a new sandbox."""
        name = simpledialog.askstring(
            "新建沙箱", "沙箱名称（仅限字母、数字、下划线）：", parent=self.root)
        if name is None:
            return
        try:
            self.api.create_box(name.strip())
        except (SbieError, ValueError) as e:
            messagebox.showerror("新建沙箱", str(e))
            return
        self.refresh()

    def on_delete_box(self):
        """Remove the selected sandbox config (optionally the content too)."""
        box = self.selected_box()
        if not box:
            return
        delete_content = messagebox.askyesno(
            "删除沙箱",
            f"确定删除沙箱 '{box}'？\n\n"
            "配置段将从 Sandboxie.ini 中移除。\n\n"
            "是否同时删除沙箱内容目录？（选择“否”保留文件）")
        if delete_content is None:
            return
        try:
            removed_content = self.api.delete_box_content(box) if delete_content else False
            self.api.remove_box(box)
        except (SbieError, OSError) as e:
            messagebox.showerror("删除沙箱", str(e))
            return
        self.refresh()
        messagebox.showinfo(
            "删除沙箱",
            f"沙箱 '{box}' 已删除"
            + ("（内容目录已删除）" if removed_content else "（内容目录已保留）"))

    def on_edit_config(self):
        """Open the config editor for the selected sandbox."""
        box = self.selected_box()
        if box:
            self._open_config_editor(box)

    def _open_config_editor(self, box):
        try:
            section = self.api.get_section(box)
        except SbieError as e:
            messagebox.showerror("编辑配置", str(e))
            return

        win = tk.Toplevel(self.root)
        win.title(f"编辑配置：{box}")
        win.geometry("700x620")
        win.transient(self.root)

        outer = ttk.Frame(win, padding=6)
        outer.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ---------------------------------------------------------------
        # 选项页：常用布尔开关，三态勾选框
        #  1 = 本地启用(y)   0 = 本地禁用(n)   2 = 未设置(继承模板/全局)
        # ---------------------------------------------------------------

        check_items = []    # 每项 [IntVar, 设置名, 初始状态]

        def bool_state(name):
            values = section.get(name) or []
            if name == "Enabled" and not values:
                return 1        # 无本地 Enabled = 默认启用，始终显示为勾选
            if not values:
                return 2
            val = values[0].strip().lower()
            if val == "y":
                return 1
            if val == "n":
                return 0
            return 1        # 其他取值视为启用，可在“手动编辑”页查看

        for cat_title, options in CFG_OPTION_CATEGORIES:
            page = ttk.Frame(notebook, padding=8)
            notebook.add(page, text=cat_title)
            ttk.Label(page, text="勾选=启用(y)　取消勾选=禁用(n)　×=恢复继承（Enabled 除外，防止沙箱消失）",
                      foreground="#888888").pack(anchor=tk.W, pady=(0, 6))
            for setting, label in options:
                var = tk.IntVar(value=bool_state(setting))
                row = ttk.Frame(page)
                row.pack(fill=tk.X, pady=(1, 1))
                cb = tk.Checkbutton(row, text=label, variable=var,
                                    onvalue=1, offvalue=0, tristatevalue=2,
                                    anchor="w")
                cb.pack(side=tk.LEFT)
                cb._sbie_setting = setting
                cb._sbie_var = var
                if setting != "Enabled":
                    clear = ttk.Button(row, text="×", width=2,
                                       command=lambda v=var: v.set(2))
                    clear.pack(side=tk.LEFT, padx=(4, 0))
                    clear._sbie_setting = setting
                check_items.append([var, setting, bool_state(setting)])

        # ---------------------------------------------------------------
        # 手动编辑页：设置名/值 表格（可双击编辑、添加/删除行）
        # ---------------------------------------------------------------

        manual = ttk.Frame(notebook, padding=8)
        notebook.add(manual, text="手动编辑")

        tree = ttk.Treeview(manual, columns=("setting", "value"), show="headings",
                            selectmode="extended")
        tree.heading("setting", text="设置名")
        tree.column("setting", width=200, anchor=tk.W)
        tree.heading("value", text="值")
        tree.column("value", width=420, anchor=tk.W)
        tree.pack(fill=tk.BOTH, expand=True)

        def load_rows():
            tree.delete(*tree.get_children())
            for setting, values in section.items():
                for value in values:
                    tree.insert("", tk.END, values=(setting, value))

        def edit_item(item, col):
            """在该单元格上弹出输入框进行行内编辑。"""
            values = list(tree.item(item, "values"))
            if not item or not values or col >= len(values):
                return
            tree.see(item)
            tree.update_idletasks()
            box = tree.bbox(item, "#%d" % (col + 1))
            if box is None:
                return
            entry = tk.Entry(tree)
            entry.place(x=box[0], y=box[1], width=box[2], height=box[3])
            entry.insert(0, values[col])
            entry.focus_force()
            entry.selection_range(0, tk.END)
            state = {"done": False}

            def commit(_event=None):
                if state["done"]:
                    return
                state["done"] = True
                values[col] = entry.get()
                tree.item(item, values=values)
                entry.destroy()

            def cancel(_event=None):
                if state["done"]:
                    return
                state["done"] = True
                entry.destroy()

            entry.bind("<Return>", commit)
            entry.bind("<FocusOut>", commit)
            entry.bind("<Escape>", cancel)

        def edit_cell(event):
            item = tree.identify_row(event.y)
            column = tree.identify_column(event.x)
            if item and column in ("#1", "#2"):
                edit_item(item, 0 if column == "#1" else 1)

        tree.bind("<Double-1>", edit_cell)

        def add_row():
            """新增一行并立即进入“设置名”单元格编辑。"""
            item = tree.insert("", tk.END, values=("", ""))
            tree.selection_set(item)
            tree.see(item)
            edit_item(item, 0)

        def delete_rows():
            for item in tree.selection():
                tree.delete(item)

        def refresh_all():
            """保存失败后按实际状态刷新选项与表格。"""
            nonlocal section
            try:
                section = self.api.get_section(box)
            except SbieError:
                section = {}
            load_rows()
            for item in check_items:
                state = bool_state(item[1])
                item[0].set(state)
                item[2] = state

        def save():
            try:
                # 1) 先应用手动表格的增删改
                entries = [tree.item(item, "values") for item in tree.get_children()]
                entries = [entry for entry in entries if (entry[0] or "").strip()]
                self.api.update_section(box, entries)
                # 2) 再应用选项页差异（同一设置两处都改时，选项页优先）
                for item in check_items:
                    var, setting, initial = item
                    cur = var.get()
                    if setting == "Enabled" and cur == 2:
                        cur = 1     # Enabled 不能恢复继承（会删除本地值）
                    if cur == initial:
                        continue
                    if cur == 1:
                        self.api.set_setting(box, setting, "y")
                    elif cur == 0:
                        self.api.set_setting(box, setting, "n")
                    else:
                        self.api.set_setting(box, setting, "")
                # 3) 兜底：沙箱不能丢失 Enabled，否则会从系统中消失
                after = self.api.get_section(box)
                if "Enabled" not in after:
                    self.api.set_setting(box, "Enabled", "y")
                self.api.reload_conf()
            except (SbieError, ValueError) as e:
                messagebox.showerror("保存配置", str(e))
                refresh_all()
                return
            win.destroy()
            self.refresh()

        load_rows()

        mbar = ttk.Frame(manual)
        mbar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(mbar, text="添加行", command=add_row).pack(side=tk.LEFT)
        ttk.Button(mbar, text="删除选中行", command=delete_rows).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Label(mbar, text="提示：双击单元格可直接编辑。",
                  foreground="#888888").pack(side=tk.RIGHT)

        bar = ttk.Frame(outer)
        bar.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(bar, text="保存", command=save).pack(side=tk.RIGHT)
        ttk.Button(bar, text="取消", command=win.destroy).pack(
            side=tk.RIGHT, padx=(6, 0))


def load_api(root, sbie_home):
    """Load the bridge; if auto-detection fails, ask the user for the folder."""
    try:
        return SbieAPI.load(sbie_home)
    except (OSError, FileNotFoundError) as e:
        messagebox.showinfo(
            "UItest",
            f"未自动找到 Sandboxie。\n\n{e}\n\n"
            "请选择 Sandboxie 安装目录（包含 SbieDll.dll 的文件夹），"
            "或取消退出。")
        folder = filedialog.askdirectory(
            title="选择 Sandboxie 安装目录")
        if not folder:
            return None
        try:
            return SbieAPI.load(folder)
        except (OSError, FileNotFoundError) as e2:
            messagebox.showerror("UItest", f"无法加载 SbieDll.dll：\n{e2}")
            return None


def main():
    parser = argparse.ArgumentParser(description="UItest 管理器 - 基于 Sandboxie Plus 的管理壳")
    parser.add_argument("--sbie-home",
                        help="Sandboxie 安装目录（省略时自动检测）")
    parser.add_argument("--selftest", type=int, metavar="SECONDS",
                        help="打开窗口、刷新一次后自动关闭（冒烟测试）")
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
            f"无法连接 Sandboxie 核心：\n{e}\n\n"
            "请确认驱动（SbieDrv）和服务（SbieSvc）已安装并运行。"
            "可在 SandMan 的“维护”菜单中安装/启动它们，或重新安装 Sandboxie。")
        return 1

    UItestApp(root, api)
    root.deiconify()
    if args.selftest:
        root.after(args.selftest * 1000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
