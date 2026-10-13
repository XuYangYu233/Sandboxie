"""Minimal ctypes bridge to Sandboxie's SbieDll.dll (UItest test harness).

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
MESSAGE_BUFFER_LEN = 4096   # characters, for SbieApi_GetMessage

STATUS_SUCCESS = 0
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_DEVICE_ALREADY_ATTACHED = 0xC0000038


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

    def __init__(self, func, status=None):
        self.func = func
        self.status = status
        if status is None:
            message = f"{func} failed"
        else:
            message = f"{func} failed (0x{status & 0xFFFFFFFF:08X})"
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


class SbieAPI:
    """Thin wrapper around the SbieDll exports used by UItest."""

    def __init__(self, dll, home):
        self._dll = dll
        self.home = home
        self.session_id = _current_session_id()
        self.last_message_num = 0
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
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p

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

        d.SbieApi_GetMessage.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
                                         ctypes.POINTER(ctypes.c_ulong),
                                         ctypes.POINTER(ctypes.c_ulong),
                                         ctypes.c_wchar_p, ctypes.c_ulong]
        d.SbieApi_GetMessage.restype = ctypes.c_ulong

        d.SbieDll_FormatMessage.argtypes = [ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_wchar_p)]
        d.SbieDll_FormatMessage.restype = ctypes.c_void_p

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

    # -- session leader and driver messages ------------------------------

    def session_leader_pid(self):
        """Return the pid of the session UI, or 0 if none is registered."""
        pid = ctypes.c_void_p(0)
        rc = self._dll.SbieApi_SessionLeader(self.session_id, ctypes.byref(pid))
        if rc != STATUS_SUCCESS:
            raise SbieError("SbieApi_SessionLeader", rc)
        return pid.value or 0

    def take_over(self):
        """Try to become the session UI (session leader).

        The driver only accepts this from a process it trusts (one with a
        matching .sig file, or any process while Windows test-signing is
        enabled), so in practice it succeeds only on a development machine.
        Returns True on success, False if another agent already manages the
        session; raises SbieError otherwise (e.g. STATUS_INVALID_SIGNATURE).
        """
        rc = self._dll.SbieApi_SessionLeader(0, None)
        if rc == STATUS_SUCCESS:
            return True
        if (rc & 0xFFFFFFFF) == STATUS_DEVICE_ALREADY_ATTACHED:
            return False
        raise SbieError("SbieApi_SessionLeader", rc)

    def log_file_path(self):
        """Return the service message log path, or None.

        The service writes every SBIE message to this file when the admin
        sets HKLM\...\Services\SbieSvc -> LogFile to "<level>;<path>" (a
        documented Sandboxie setting, see "Messages From Sandboxie" in the
        official documentation).  Reading a plain file needs no signature,
        so this is the way for an unsigned UI to see driver messages.
        """
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services\SbieSvc") as key:
                value, _ = winreg.QueryValueEx(key, "LogFile")
        except OSError:
            return None
        if not isinstance(value, str) or ";" not in value:
            return None
        path = value.split(";", 1)[1].strip()
        return path or None

    def get_messages(self):
        """Return new SBIE driver messages for this session.

        The driver allows this only for the session leader (or the service);
        other callers get STATUS_ACCESS_DENIED.
        """
        messages = []
        num = ctypes.c_ulong(self.last_message_num)
        for _ in range(256):
            msgid = ctypes.c_ulong(0)
            pid = ctypes.c_ulong(0)
            buf = ctypes.create_unicode_buffer(MESSAGE_BUFFER_LEN)
            rc = self._dll.SbieApi_GetMessage(ctypes.byref(num), self.session_id,
                                              ctypes.byref(msgid), ctypes.byref(pid),
                                              buf, MESSAGE_BUFFER_LEN * 2)
            if (rc & 0xFFFFFFFF) == STATUS_ACCESS_DENIED:
                raise SbieError("SbieApi_GetMessage", rc)   # not the session leader
            if rc != STATUS_SUCCESS:    # 0 = entry read, anything else = no more entries
                break
            self.last_message_num = num.value

            strings, chunk = [], []
            for ch in buf:              # buffer holds NUL-separated strings
                if ch == "\0":
                    if not chunk:
                        break
                    strings.append("".join(chunk))
                    chunk = []
                else:
                    chunk.append(ch)
            # modern builds key the message table by the full code (severity
            # and facility bits included, e.g. 0x810208B0 for SBIE2224), so
            # keep both the full code and the plain message number
            messages.append({"code": msgid.value, "id": msgid.value & 0xFFFF,
                             "pid": pid.value, "strings": strings})
        return messages

    def _format_once(self, code, ins):
        ptr = self._dll.SbieDll_FormatMessage(code, ins)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            ctypes.windll.kernel32.LocalFree(ptr)

    def format_message(self, code, strings):
        """Format an SBIE message code with its substitution strings.

        `code` is the full code as stored in the driver log (severity and
        facility bits included); if that is not found, retry with the plain
        message number, which older drivers used.
        """
        ins = (ctypes.c_wchar_p * (len(strings) + 2))()
        for i, text in enumerate(strings, start=1):     # ins[1] holds %2's value
            ins[i] = text

        message = self._format_once(code, ins)
        if not message or message.startswith("err="):
            fallback = self._format_once(code & 0xFFFF, ins)
            if fallback and not fallback.startswith("err="):
                return fallback
        return message or f"SBIE{code & 0xFFFF}"

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