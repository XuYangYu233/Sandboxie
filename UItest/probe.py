"""Connectivity self-check for the UItest bridge.

Usage:
    python UItest\probe.py [--sbie-home PATH]

Prints the driver version, the Sandboxie home path, all usable sandboxes
and the processes running inside them.  Exits with code 0 on success.
"""

import argparse
import os
import sys

from sbie_api import SbieAPI, SbieError


def main():
    parser = argparse.ArgumentParser(description="Sandboxie UItest connectivity probe")
    parser.add_argument("--sbie-home",
                        help="Sandboxie installation directory (auto-detected if omitted)")
    args = parser.parse_args()

    try:
        api = SbieAPI.load(args.sbie_home)
    except (OSError, FileNotFoundError) as e:
        print(f"FATAL: cannot load SbieDll.dll: {e}")
        return 1

    try:
        print(f"SbieDll      : {api.home}")
        print(f"Driver       : {api.version()}")
        print(f"Home path    : {api.home_path()}")
        sandman = os.path.join(api.home, "SandMan.exe")
        print(f"SandMan.exe  : {'found' if os.path.isfile(sandman) else 'MISSING'} ({sandman})")
        boxes = api.enum_boxes()
        print(f"Sandboxes    : {len(boxes)} -> {boxes}")
        for box in boxes:
            pids = api.box_processes(box)
            print(f"  [{box}] {len(pids)} process(es)")
            for pid in pids:
                info = api.query_process(pid)
                print(f"    pid={pid:<6} image={info['image']:<40} session={info['session']}")
    except SbieError as e:
        print(f"FATAL: {e}")
        print("Hint: the driver (SbieDrv) and service (SbieSvc) must be "
              "installed and running.")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
