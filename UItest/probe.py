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
    parser = argparse.ArgumentParser(description="Sandboxie UItest 连通性检测")
    parser.add_argument("--sbie-home",
                        help="Sandboxie 安装目录（省略时自动检测）")
    args = parser.parse_args()

    try:
        api = SbieAPI.load(args.sbie_home)
    except (OSError, FileNotFoundError) as e:
        print(f"错误：无法加载 SbieDll.dll：{e}")
        return 1

    try:
        print(f"SbieDll.dll : {api.home}")
        print(f"驱动版本    : {api.version()}")
        print(f"安装目录    : {api.home_path()}")
        sandman = os.path.join(api.home, "SandMan.exe")
        print(f"SandMan.exe  : {'存在' if os.path.isfile(sandman) else '缺失'} ({sandman})")
        box_info = api.list_boxes()
        print(f"沙箱数量    : {len(box_info)} -> {[b for b, _ in box_info]}")
        for box, enabled in box_info:
            if not enabled:
                print(f"  [{box}] 已禁用")
                continue
            pids = api.box_processes(box)
            print(f"  [{box}] {len(pids)} 个进程")
            for pid in pids:
                info = api.query_process(pid)
                print(f"    pid={pid:<6} 映像={info['image']:<40} 会话={info['session']}")
    except SbieError as e:
        print(f"错误：{e}")
        print("提示：请确认驱动（SbieDrv）和服务（SbieSvc）已安装并运行。")
        return 1

    print("检测通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
