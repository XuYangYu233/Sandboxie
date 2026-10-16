"""Minimal ctypes bridge to Sandboxie's SbieDll.dll (UItest Manager).

This module loads the SbieDll.dll that ships with an installed Sandboxie /
Sandboxie-Plus build and wraps the stable exported API subset used by this
test UI.  No compilation and no part of the Sandboxie build tree is needed:
the tool talks to the already-installed driver/service through the shipped
DLL.

The DLL location is resolved in this order:
  1. explicit path passed to SbieAPI.load(sbie_home=...)
  2. registry: HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc -> ImagePath
  3. common default installation directories

Signatures are kept in sync with Sandboxie/core/dll/sbieapi.h and
Sandboxie/core/dll/sbiedll.h (x64 build, stable API subset).
"""

import ctypes
import os

BOXNAME_COUNT = 40          # WCHAR[BOXNAME_COUNT], see Sandboxie/common/defines.h
IMAGE_NAME_LEN = 96         # WCHAR[96], see sbieapi.h
SID_LEN = 96                # WCHAR[96], see sbieapi.h
MAX_PIDS = 512              # API_MAX_PIDS, see Sandboxie/core/drv/api_defs.h
CURRENT_SESSION = 0xFFFFFFFF

STATUS_SUCCESS = 0

# flags for the SbieApi_QueryConf setting_index (see core/drv/api_flags.h)
CONF_GET_NO_EXPAND = 0x20000000
CONF_GET_NO_GLOBAL = 0x40000000
CONF_GET_NO_TEMPLS = 0x10000000

# extra NTSTATUS codes used by the configuration APIs / service
STATUS_RESOURCE_NAME_NOT_FOUND = 0xC0000223
STATUS_WRONG_PASSWORD = 0xC000006A


class STARTUPINFO(ctypes.Structure):
    """Subset of the Win32 STARTUPINFO structure (full layout)."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_ulong),
        ("dwY", ctypes.c_ulong),
        ("dwXSize", ctypes.c_ulong),
        ("dwYSize", ctypes.c_ulong),
        ("dwXCountChars", ctypes.c_ulong),
        ("dwYCountChars", ctypes.c_ulong),
        ("dwFillAttribute", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("wShowWindow", ctypes.c_ushort),
        ("cbReserved2", ctypes.c_ushort),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_ulong),
        ("dwThreadId", ctypes.c_ulong),
    ]


class SbieError(RuntimeError):
    """Raised when an API call does not succeed."""

    def __init__(self, func, status=None, hint=None):
        self.func = func
        self.status = status
        self.hint = hint
        if hint is not None:
            message = f"{func}：{hint}"
        elif status is None:
            message = f"{func} 调用失败"
        else:
            message = f"{func} 调用失败 (0x{status & 0xFFFFFFFF:08X})"
        super().__init__(message)


def _current_session_id():
    """Return the Windows session id of this process."""
    sid = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.ProcessIdToSessionId(
        ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    return sid.value if ok else 0


def _find_home(sbie_home=None):
    """Return the Sandboxie home directory (the one containing SbieDll.dll)."""
    candidates = []
    if sbie_home:
        candidates.append(os.path.abspath(sbie_home))

    env_home = os.environ.get("SBIE_HOME")
    if env_home:
        candidates.append(os.path.abspath(env_home))

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Services\SbieSvc") as key:
            image_path, _ = winreg.QueryValueEx(key, "ImagePath")
        image_path = os.path.expandvars(image_path.strip().strip('"'))
        candidates.append(os.path.dirname(image_path))
    except OSError:
        pass

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(os.path.join(program_files, "Sandboxie-Plus"))
    candidates.append(os.path.join(program_files, "Sandboxie"))

    for path in candidates:
        if path and os.path.isfile(os.path.join(path, "SbieDll.dll")):
            return path

    raise FileNotFoundError(
        "Sandboxie was not found; searched: " + "; ".join(candidates) +
        ". If Sandboxie is installed in a custom folder, pass --sbie-home PATH "
        "or set the SBIE_HOME environment variable.")


def _nt_to_dos(path):
    """Convert an NT namespace path to a Win32 DOS path (best effort).

    Handles the two common shapes returned by SbieApi_QueryBoxPath:
    \\??\\C:\... and \\Device\\HarddiskVolumeN\... (mapped via
    QueryDosDeviceW); anything else is returned unchanged.
    """
    if path.startswith("\\??\\"):
        return path[4:]
    if path.startswith("\\Device\\"):
        prefix = "\\Device\\"
        target = None
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = letter + ":"
            buf = ctypes.create_unicode_buffer(256)
            n = ctypes.windll.kernel32.QueryDosDeviceW(drive, buf, 256)
            if not n:
                continue
            name = buf.value
            if path == name or path.startswith(name + "\\"):
                target = (letter + ":", name)
                break
        if target:
            return target[0] + path[len(target[1]):]
    return path


class SbieAPI:
    """Thin wrapper around the SbieDll exports used by UItest."""

    def __init__(self, dll, home):
        self._dll = dll
        self.home = home
        self.session_id = _current_session_id()
        self._bind()

    @classmethod
    def load(cls, sbie_home=None):
        home = _find_home(sbie_home)
        dll = ctypes.WinDLL(os.path.join(home, "SbieDll.dll"))
        return cls(dll, home)

    def _bind(self):
        d = self._dll

        # Win32 helpers used by this module; explicit argtypes keep 64-bit
        # pointers from being truncated to int.
        k32 = ctypes.windll.kernel32
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        d.SbieApi_GetVersion.argtypes = [ctypes.c_wchar_p]
        d.SbieApi_GetVersion.restype = ctypes.c_long

        d.SbieApi_GetHomePath.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong,
                                          ctypes.c_wchar_p, ctypes.c_ulong]
        d.SbieApi_GetHomePath.restype = ctypes.c_long

        d.SbieApi_EnumBoxesEx.argtypes = [ctypes.c_long, ctypes.c_wchar_p, ctypes.c_ubyte]
        d.SbieApi_EnumBoxesEx.restype = ctypes.c_long

        d.SbieApi_IsBoxEnabled.argtypes = [ctypes.c_wchar_p]
        d.SbieApi_IsBoxEnabled.restype = ctypes.c_long

        d.SbieApi_EnumProcessEx.argtypes = [ctypes.c_wchar_p, ctypes.c_ubyte, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong),
                                            ctypes.POINTER(ctypes.c_ulong)]
        d.SbieApi_EnumProcessEx.restype = ctypes.c_long

        d.SbieApi_QueryProcess.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                           ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           ctypes.POINTER(ctypes.c_ulong)]
        d.SbieApi_QueryProcess.restype = ctypes.c_long

        d.SbieDll_RunSandboxed.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           ctypes.c_wchar_p, ctypes.c_ulong,
                                           ctypes.POINTER(STARTUPINFO),
                                           ctypes.POINTER(PROCESS_INFORMATION)]
        d.SbieDll_RunSandboxed.restype = ctypes.c_int        # BOOL

        d.SbieDll_KillOne.argtypes = [ctypes.c_ulong]
        d.SbieDll_KillOne.restype = ctypes.c_ubyte          # BOOLEAN

        d.SbieApi_SessionLeader.argtypes = [ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_void_p)]
        d.SbieApi_SessionLeader.restype = ctypes.c_long
        d.SbieApi_QueryConf.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                        ctypes.c_ulong, ctypes.c_wchar_p,
                                        ctypes.c_ulong]
        d.SbieApi_QueryConf.restype = ctypes.c_long

        d.SbieApi_ReloadConf.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        d.SbieApi_ReloadConf.restype = ctypes.c_long

        d.SbieApi_QueryBoxPath.argtypes = [ctypes.c_wchar_p,
                                           ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           ctypes.c_wchar_p,
                                           ctypes.POINTER(ctypes.c_ulong),
                                           ctypes.POINTER(ctypes.c_ulong),
                                           ctypes.POINTER(ctypes.c_ulong)]
        d.SbieApi_QueryBoxPath.restype = ctypes.c_long

        d.SbieDll_UpdateConf.argtypes = [ctypes.c_wchar, ctypes.c_wchar_p,
                                         ctypes.c_wchar_p, ctypes.c_wchar_p,
                                         ctypes.c_wchar_p]
        d.SbieDll_UpdateConf.restype = ctypes.c_ulong


    # -- queries ---------------------------------------------------------

    def version(self):
        """Return the driver version string, e.g. '5.73.2'."""
        buf = ctypes.create_unicode_buffer(16)
        rc = self._dll.SbieApi_GetVersion(buf)
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_GetVersion", rc)
        return buf.value

    def home_path(self):
        """Return the Sandboxie home path as reported by the driver."""
        buf = ctypes.create_unicode_buffer(1024)
        rc = self._dll.SbieApi_GetHomePath(None, 0, buf, len(buf))
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_GetHomePath", rc)
        return buf.value

    def enum_boxes(self):
        """Return usable sandbox names (hidden/user-settings entries filtered)."""
        name = ctypes.create_unicode_buffer(BOXNAME_COUNT)
        index, boxes = -1, []
        while True:
            index = self._dll.SbieApi_EnumBoxesEx(index, name, True)
            if index == -1:
                break
            box = name.value
            if not box:
                continue
            if self._dll.SbieApi_IsBoxEnabled(box) == STATUS_SUCCESS:
                boxes.append(box)
        return boxes

    def box_processes(self, box):
        """Return the process ids currently running inside `box`."""
        # the driver treats `count` as an in/out buffer capacity, so it must
        # be initialized to the buffer size on entry (0 would return nothing)
        pids = (ctypes.c_ulong * MAX_PIDS)()
        count = ctypes.c_ulong(MAX_PIDS)
        rc = self._dll.SbieApi_EnumProcessEx(box, False, CURRENT_SESSION,
                                             pids, ctypes.byref(count))
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_EnumProcessEx", rc)
        return [pid for pid in list(pids)[:count.value] if pid]

    def query_process(self, pid):
        """Return {pid, box, image, session} for a sandboxed process."""
        box = ctypes.create_unicode_buffer(BOXNAME_COUNT)
        image = ctypes.create_unicode_buffer(IMAGE_NAME_LEN)
        sid = ctypes.create_unicode_buffer(SID_LEN)
        session = ctypes.c_ulong(0)
        rc = self._dll.SbieApi_QueryProcess(ctypes.c_void_p(pid), box, image, sid,
                                            ctypes.byref(session))
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_QueryProcess", rc)
        return {"pid": pid, "box": box.value, "image": image.value,
                "session": session.value}

    # -- session information ----------------------------------------------

    def session_leader(self):
        """Return the pid of the current session UI, or None.

        Query direction only (no signature check): reports which official
        UI owns the session-leader role, normally SandMan.
        """
        pid = ctypes.c_void_p(0)
        rc = self._dll.SbieApi_SessionLeader(self.session_id, ctypes.byref(pid))
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_SessionLeader", rc)
        return pid.value or None
    # -- configuration (management) ---------------------------------------

    def query_conf(self, section, setting, index=0, max_len=8192):
        """Read one config value from the driver (local, unexpanded)."""
        buf = ctypes.create_unicode_buffer(max_len)
        idx = index | CONF_GET_NO_TEMPLS | CONF_GET_NO_GLOBAL | CONF_GET_NO_EXPAND
        rc = self._dll.SbieApi_QueryConf(section, setting, idx, buf, max_len)
        if rc == STATUS_RESOURCE_NAME_NOT_FOUND:
            raise SbieError("SbieApi_QueryConf", rc, hint="未找到该设置")
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_QueryConf", rc)
        return buf.value

    def list_settings(self, section):
        """Return the local setting names of `section` (templates excluded)."""
        names = []
        index = 0
        while True:
            name = ctypes.create_unicode_buffer(66)
            idx = index | CONF_GET_NO_TEMPLS | CONF_GET_NO_GLOBAL | CONF_GET_NO_EXPAND
            rc = self._dll.SbieApi_QueryConf(section, None, idx, name, 66)
            if rc != STATUS_SUCCESS:
                break
            names.append(name.value)
            index += 1
        return names

    def get_section(self, section):
        """Return {setting: [value, ...]} for a config section (local values)."""
        result = {}
        for setting in self.list_settings(section):
            values = []
            index = 0
            while index < 512:
                try:
                    values.append(self.query_conf(section, setting, index))
                except SbieError:
                    break
                index += 1
            result[setting] = values
        return result

    def set_setting(self, section, setting, value="", mode="set", password=None):
        """Write one config value through the SbieSvc service (official path).

        mode: 'set' replaces a setting (setting '*' with an empty value
        removes the whole section; an empty value removes the whole setting),
        'append' and 'insert' add a value line, 'delete' removes the given
        value line (or the whole setting when the value is empty).
        """
        op = {"set": "s", "append": "a", "insert": "i", "delete": "d"}.get(mode)
        if op is None:
            raise ValueError(f"无效的模式：{mode!r}")
        if not section:
            raise ValueError("段名不能为空")
        status = self._dll.SbieDll_UpdateConf(op, password or "", section,
                                              setting, value or "")
        if status == STATUS_SUCCESS:
            return
        if status == STATUS_WRONG_PASSWORD:
            raise SbieError("SbieDll_UpdateConf", status,
                            hint="配置受密码保护，请使用 SandMan 解锁")
        raise SbieError("SbieDll_UpdateConf", status)

    def reload_conf(self, flags=0):
        """Ask the driver to reload its configuration."""
        rc = self._dll.SbieApi_ReloadConf(0xFFFFFFFF, flags)
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_ReloadConf", rc)

    def validate_box_name(self, name):
        """Raise ValueError unless `name` is a valid new sandbox name."""
        name = (name or "").strip()
        if not name or len(name) > BOXNAME_COUNT - 2:
            raise ValueError("沙箱名为空或超过 38 个字符")
        def _ok(ch):
            return (ch.isascii() and ch.isalnum()) or ch == "_"
        if not all(_ok(ch) for ch in name):
            raise ValueError("沙箱名只能包含字母、数字和下划线")
        reserved = {"aux", "clock$", "con", "nul", "prn"}
        reserved |= {f"com{i}" for i in range(10)}
        reserved |= {f"lpt{i}" for i in range(10)}
        if (name.lower() in reserved or name.lower() == "globalsettings"
                or name.lower().startswith("usersettings_")):
            raise ValueError("该名称被系统保留")

    def create_box(self, name):
        """Create a new sandbox (Enabled=y) and reload the configuration."""
        self.validate_box_name(name)
        self.set_setting(name, "Enabled", "y")
        self.reload_conf()

    def remove_box(self, name):
        """Remove the sandbox config section; the content folder is kept."""
        self.set_setting(name, "*", "", mode="set")
        self.reload_conf()

    def box_file_root(self, name):
        """Return the DOS path of the box content folder, or None."""
        file_path = ctypes.create_unicode_buffer(1024)
        key_path = ctypes.create_unicode_buffer(1024)
        ipc_path = ctypes.create_unicode_buffer(1024)
        file_len = ctypes.c_ulong(1024)
        key_len = ctypes.c_ulong(1024)
        ipc_len = ctypes.c_ulong(1024)
        rc = self._dll.SbieApi_QueryBoxPath(
            name, file_path, key_path, ipc_path,
            ctypes.byref(file_len), ctypes.byref(key_len), ctypes.byref(ipc_len))
        if rc != STATUS_SUCCESS or not file_path.value:
            return None
        path = _nt_to_dos(file_path.value)
        return path.rstrip("\\") or None

    def delete_box_content(self, name):
        """Delete the sandbox content folder; False when it does not exist."""
        import shutil
        root = self.box_file_root(name)
        if not root:
            return False
        if os.path.split(root)[1].lower() != name.lower():
            raise SbieError("delete_box_content",
                            hint=f"意外的沙箱目录：{root}")
        if not os.path.isdir(root):
            return False
        shutil.rmtree(root)
        return True

    def update_section(self, section, entries):
        """Apply `entries` (list of (setting, value)) to `section` as a diff.

        Only touched settings are written.  A row with an empty value removes
        the setting; settings named '' or '*' are rejected so the editor can
        not remove the whole section by accident.
        """
        new = {}
        removed = set()
        for setting, value in entries:
            setting = (setting or "").strip()
            if not setting or setting == "*":
                raise ValueError(f"无效的设置名：{setting!r}")
            if not value:
                removed.add(setting)
            else:
                new.setdefault(setting, []).append(value)
        old = self.get_section(section)
        for setting, old_values in old.items():
            if setting in removed or setting not in new:
                if old_values:
                    self.set_setting(section, setting, "", mode="set")
            elif new[setting] != old_values:
                self._replace_setting_values(section, setting, new[setting])
        for setting, values in new.items():
            if setting not in old and values:
                self._replace_setting_values(section, setting, values)

    def _replace_setting_values(self, section, setting, values):
        self.set_setting(section, setting, values[0], mode="set")
        for value in values[1:]:
            self.set_setting(section, setting, value, mode="append")


    # -- actions ---------------------------------------------------------

    def run_in_box(self, command, box, workdir=None):
        """Start `command` inside `box` through the SbieSvc service.

        Returns the new process id.  This is the same request path used by
        Start.exe and SandMan (MSGID_PROCESS_RUN_SANDBOXED); SbieDll_RunStartExe
        is not usable here because it depends on Dll_HomeDosPath, which SbieDll
        only initializes when injected into a sandboxed process.
        """
        si = STARTUPINFO()
        si.cb = ctypes.sizeof(STARTUPINFO)
        pi = PROCESS_INFORMATION()
        ok = self._dll.SbieDll_RunSandboxed(box, command,
                                            workdir or os.path.expanduser("~"),
                                            0, ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            raise SbieError("SbieDll_RunSandboxed")
        for handle in (pi.hProcess, pi.hThread):
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
        return pi.dwProcessId

    def kill_process(self, pid):
        """Terminate a single sandboxed process."""
        if not self._dll.SbieDll_KillOne(pid):
            raise SbieError("SbieDll_KillOne")