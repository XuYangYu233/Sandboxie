# UItest - minimal Sandboxie UI (experimental)

A deliberately small tkinter front-end for an **already installed** Sandboxie.
It is a test harness for the `UItest` branch, not a product: the goal is to
prove that a third-party UI can drive the Sandboxie core with a few dozen
lines of `ctypes` and no compiler at all.

## How it works

The UI loads the shipped `SbieDll.dll` with `ctypes` and calls the stable
exported API subset:

| Call | Used for |
|---|---|
| `SbieApi_GetVersion` | connection state / driver version |
| `SbieApi_EnumBoxesEx`, `SbieApi_IsBoxEnabled` | sandbox list (hidden entries filtered) |
| `SbieApi_EnumProcessEx`, `SbieApi_QueryProcess` | process list of the selected sandbox |
| `SbieDll_RunSandboxed` | start a program inside a sandbox (service request) |
| `SbieDll_KillOne` | terminate a sandboxed process |
| `SbieApi_SessionLeader` | try to become the session UI (session leader) |
| `SbieApi_GetMessage`, `SbieDll_FormatMessage` | read and format driver messages |

No part of the Sandboxie build tree is required, and nothing is compiled.
The DLL location is resolved in this order:

1. `--sbie-home PATH` command line option
2. `SBIE_HOME` environment variable
3. registry: `HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc` -> `ImagePath`
4. `C:\Program Files\Sandboxie-Plus` / `C:\Program Files\Sandboxie`

If auto-detection fails, the UI asks for the folder with a file dialog.
Note that the folder is only useful while the driver and service are
installed: a leftover copy of the files alone cannot work.

## Requirements

- Windows with a working Sandboxie installation (driver `SbieDrv` and service
  `SbieSvc` running; Sandboxie Plus or Classic, signed or development build)
- 64-bit Python 3.8+ with tkinter (tested with Anaconda Python 3.11)

## Usage

```bat
:: self check without the GUI (version, sandboxes, processes)
python UItest\probe.py

:: run the UI (pythonw avoids the console window)
pythonw UItest\uitest.pyw

:: optional: point at a specific installation, or run a smoke test
python UItest\uitest.pyw --sbie-home "C:\path\to\Sandboxie" 
python UItest\uitest.pyw --selftest 3

:: message-channel options
python UItest\uitest.pyw --no-setup                  :: skip the setup below (no UAC)
python UItest\uitest.pyw --log-path "D:\sbie.log"    :: use another log file location
```

The window shows the sandbox list on the left and the process list of the
selected sandbox on the right. It refreshes every 3 seconds; "Run in Box..."
starts a picked program inside the selected sandbox and "Terminate Process"
kills the selected process (with a confirmation prompt).

## Driver messages (popups)

Sandboxie logs messages such as `SBIE2224 Sandboxed program has crashed` into a
kernel message log.  The driver allows reading that log only for the service
and for the **session leader** (the UI process that called
`SbieApi_SessionLeader`); any other process gets `STATUS_ACCESS_DENIED`.

At startup the UI calls `SbieApi_SessionLeader(0, NULL)`:

- **Success** - UItest becomes the session UI, polls the log through
  `SbieApi_GetMessage` and shows new messages in its own tkinter dialogs
  (the status bar reads `messages: UItest`).  SandMan will then refuse to take
  over while UItest is running ("another agent is already managing this
  session").
- **Failure** - if a service log file is configured (see below), UItest tails
  it instead and still shows the messages in its own dialogs (the status bar
  reads `messages: UItest (log file)`); otherwise the official UI (SandMan)
  keeps showing them and the status bar reads `messages: other UI`.

### Reading messages without a trusted signature (log file)

The service can write every SBIE message to a plain text file.  As an
administrator, set the documented registry value once:

```bat
reg.exe add "HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc" /t REG_SZ /v LogFile /d "2;C:\Windows\System32\LogFiles\Sandboxie.log" /f
```

Level `2` adds timestamps, level `3` also appends the user name; an optional
`MultiLog=1308,1307` splits selected messages into per-box files.  See
"Messages From Sandboxie" in the official documentation for details.

The file is UTF-16LE with CRLF line endings (written by
`DriverAssist::LogMessage_Write`).  UItest starts reading at the end of the
file and shows each new line in its own tkinter dialog, which needs no
signature at all.  If the value is not set, the status bar shows
`messages: other UI` and UItest does not display messages.

### Automatic setup on startup

Unless `--no-setup` is given, UItest prepares both channels at startup:

1. **Service log file** - if the `LogFile` registry value is missing, UItest
   asks for elevation **once** (a normal UAC prompt) and adds it.  When the
   value already exists it is used as-is and no prompt is shown.
2. **SandMan popups** - `<home>\PlusData\Sandboxie-Plus.ini` or
   `<home>\Sandboxie-Plus.ini` is updated so that `[UIConfig]` contains
   `DisableMessages=true`, keeping SandMan from showing a second copy of the
   same message.  SandMan reads this setting at startup, so restart it (or
   toggle Options > Disable Message Popup) for the change to take effect.

The setup is best-effort: cancelling the UAC prompt or an unreadable ini file
never blocks the UI, it only means the status bar will show
`messages: other UI`.

Taking over requires the driver to trust the executable.  The driver accepts
either a `.sig` file next to the exe matching the key compiled into the
driver, or **any** process while Windows test-signing is enabled.  For
development that means running once, as an administrator:

```bat
bcdedit /set testsigning on
:: then reboot
```

This is the same approach the project's own build instructions use for
unsigned development builds.  Note the trade-off: test-signing relaxes kernel
driver signature enforcement for the whole machine and shows a desktop
watermark.  Without it, the official signed UI remains responsible for
message popups.

## Known limitations

- Minimal feature set on purpose: no box creation/deletion, no settings
  editor, no file recovery, no tray icon, no notifications.
- The UI tries to register as the session UI (`SbieApi_SessionLeader`) to show
  driver messages itself; see "Driver messages" below.  Whether that works
  depends on the driver trusting this executable.
- `SbieDll_RunStartExe` cannot be used from a plain host process (it depends
  on `Dll_HomeDosPath`, which SbieDll only initializes when injected into a
  sandboxed process); `SbieDll_RunSandboxed` is used instead.
- `SbieApi_EnumProcessEx` treats the count parameter as an in/out buffer
  capacity: it must be initialized to the buffer size (512 here) on entry.
- Driver and user-mode components must be compatible builds; mixing versions
  (for example an older driver with newer user-mode DLLs) can silently break
  individual APIs.