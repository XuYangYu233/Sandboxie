# UItest Manager - a management shell over Sandboxie Plus

A deliberately small tkinter front-end for an **already installed** Sandboxie
Plus.  It is an **outer management shell**: it drives the official core
(driver `SbieDrv` + service `SbieSvc`) through the shipped `SbieDll.dll` and
coexists with the official **SandMan** UI, which keeps the signed
session-leader role (message popups, monitoring, interactive queue).  This
tool never replaces, modifies or competes with SandMan, and nothing is
compiled.

## Division of labour

| Responsibility | Owner |
|---|---|
| Isolation engine (driver), service, injection | official Sandboxie Plus core, untouched |
| Message popups, activity monitoring, interactive queue (signed session-leader features) | official SandMan UI, kept running |
| Basic management: list boxes, run in box, terminate processes | this shell (via `SbieDll.dll`) |
| Advanced management (options, snapshots, recovery, templates) | official SandMan UI - use the "Open SandMan" button |

## How it works

The UI loads the shipped `SbieDll.dll` with `ctypes` and calls a small subset
of the stable exported API:

| Call | Used for |
|---|---|
| `SbieApi_GetVersion` | connection state / driver version |
| `SbieApi_EnumBoxesEx`, `SbieApi_IsBoxEnabled` | sandbox list (hidden entries filtered) |
| `SbieApi_EnumProcessEx`, `SbieApi_QueryProcess` | process list of the selected sandbox |
| `SbieApi_SessionLeader` (query only) | show which official UI owns the session-leader role |
| `SbieDll_RunSandboxed` | start a program inside a sandbox (service request) |
| `SbieDll_KillOne` | terminate a sandboxed process |

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

- Windows with a working Sandboxie Plus installation (driver `SbieDrv` and
  service `SbieSvc` running; signed or development build)
- The official SandMan UI installed in the same folder (used for
  session-leader features and advanced management; the shell works without
  it for listing/running/terminating, it only warns in the status bar)
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
```

The window shows the sandbox list on the left and the process list of the
selected sandbox on the right.  It refreshes every 3 seconds; "Run in Box..."
starts a picked program inside the selected sandbox and "Terminate Process"
kills the selected process (with a confirmation prompt).  "Open SandMan"
starts (or activates) the official UI for anything advanced.

## Relationship with SandMan

- The shell never tries to become the session leader (`SbieApi_SessionLeader`
  set direction), so it never fights SandMan for the session UI role.
- Driver messages ("sandboxed program has crashed", SBIE2224, ...) are shown
  by SandMan, exactly as in a stock installation.
- The shell does not touch `Sandboxie-Plus.ini`, does not ask for elevation
  and does not change any service registry value.

## Known limitations

- Minimal feature set on purpose: no box creation/deletion, no settings
  editor, no file recovery, no tray icon, no notifications - the official
  UI remains the tool for those.
- `SbieDll_RunStartExe` cannot be used from a plain host process (it depends
  on `Dll_HomeDosPath`, which SbieDll only initializes when injected into a
  sandboxed process); `SbieDll_RunSandboxed` is used instead.
- `SbieApi_EnumProcessEx` treats the count parameter as an in/out buffer
  capacity: it must be initialized to the buffer size (512 here) on entry.
- Driver and user-mode components must be compatible builds; mixing versions
  (for example an older driver with newer user-mode DLLs) can silently break
  individual APIs.
