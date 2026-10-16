# UItest 管理器 - 基于 Sandboxie Plus 的管理壳

一个刻意保持小巧的 tkinter 前端，面向**已安装**的 Sandboxie Plus。它是**外层管理壳**：
通过随安装提供的 `SbieDll.dll` 驱动官方核心（驱动 `SbieDrv` + 服务 `SbieSvc`），
并与官方 **SandMan** 界面共存——SandMan 继续持有签名要求的会话主持人（session leader）
职责（消息弹窗、活动监控、交互队列）。本工具绝不替换、修改或竞争 SandMan，
也不需要编译任何东西。

## 分工

| 职责 | 归属 |
|---|---|
| 隔离引擎（驱动）、服务、注入 | 官方 Sandboxie Plus 核心，原样保留 |
| 消息弹窗、活动监控、交互队列（需要签名的会话级功能） | 官方 SandMan 界面，保持运行 |
| 基础管理：列出沙箱、运行/终止进程、新建/编辑/删除沙箱配置 | 本工具（通过 `SbieDll.dll`） |
| 高级管理（选项、快照、恢复、模板） | 官方 SandMan 界面——使用“打开 SandMan”按钮 |

## 工作原理

界面用 `ctypes` 加载随安装提供的 `SbieDll.dll`，调用一小部分稳定的导出接口：

| 调用 | 用途 |
|---|---|
| `SbieApi_GetVersion` | 连接状态 / 驱动版本 |
| `SbieApi_EnumBoxesEx`、`SbieApi_IsBoxEnabled` | 沙箱列表（过滤隐藏项） |
| `SbieApi_EnumProcessEx`、`SbieApi_QueryProcess` | 所选沙箱的进程列表 |
| `SbieApi_SessionLeader`（仅查询） | 显示当前会话主持人由哪个官方界面担任 |
| `SbieDll_RunSandboxed` | 在沙箱内启动程序（服务请求） |
| `SbieDll_KillOne` | 终止沙箱进程 |
| `SbieApi_QueryConf`、`SbieApi_ReloadConf` | 列出/读取沙箱设置；重载配置 |
| `SbieDll_UpdateConf` | 通过 SbieSvc 服务写一条设置（新建沙箱、修改策略、删除沙箱） |
| `SbieApi_QueryBoxPath` | 解析沙箱内容目录（用于删除内容） |

不需要 Sandboxie 构建树中的任何源码，也不需要编译。DLL 位置的查找顺序：

1. 命令行参数 `--sbie-home PATH`
2. 环境变量 `SBIE_HOME`
3. 注册表 `HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc` -> `ImagePath`
4. `C:\Program Files\Sandboxie-Plus` / `C:\Program Files\Sandboxie`

自动检测失败时会弹出文件对话框让你手动选择目录。注意：只有驱动和服务已安装并
运行时这些文件才有用——单独拷一份文件是无法工作的。

## 运行要求

- 已安装并可用的 Sandboxie Plus（驱动 `SbieDrv` 与服务 `SbieSvc` 正在运行；
  签名版或开发版均可）
- 同目录下安装官方 SandMan 界面（用于会话级功能与高级管理；本工具在没有它时
  仍可列出/运行/终止/管理配置，只会在状态栏提示）
- 64 位 Python 3.8+ 且带 tkinter（在 Anaconda Python 3.11 上测试）

## 使用方法

```bat
:: 不带界面的自检（版本、沙箱、进程）
python UItest\probe.py

:: 运行界面（pythonw 避免弹出控制台窗口）
pythonw UItest\uitest.pyw

:: 可选：指定安装目录，或运行冒烟测试
python UItest\uitest.pyw --sbie-home "C:\path\to\Sandboxie"
python UItest\uitest.pyw --selftest 3
```

窗口左侧是沙箱列表，右侧是所选沙箱的进程列表，每 3 秒刷新一次：
“在沙箱中运行...”选择一个程序并在所选沙箱内启动，“终止进程”结束所选进程
（有确认提示）。“新建沙箱...”创建沙箱，“编辑配置...”打开所选沙箱的设置
编辑器（双击单元格可直接编辑），“删除沙箱”移除它（可选择是否同时删除内容
目录）。“打开 SandMan”启动（或激活）官方界面，用于一切高级操作。

## 与 SandMan 的关系

- 本工具从不尝试成为会话主持人（`SbieApi_SessionLeader` 设置方向），因此不会
  与 SandMan 争夺会话界面角色。
- 驱动消息（例如“沙箱程序已崩溃”SBIE2224）由 SandMan 显示，与标准安装一致。
- 本工具不修改 `Sandboxie-Plus.ini`、不请求提升权限、不改动任何服务注册表值
  （配置写入统一走 SbieSvc 服务的 `SbieDll_UpdateConf` 通道）。

## 已知限制

- 有意不做：沙箱重命名、快照、文件恢复界面、模板向导、ImDisk 挂载、
  挂起/恢复进程、编辑受密码保护的配置——这些继续由官方 SandMan 界面承担。
- 普通宿主进程无法使用 `SbieDll_RunStartExe`（它依赖 `Dll_HomeDosPath`，
  该值只在 SbieDll 被注入沙箱进程时才初始化），因此使用 `SbieDll_RunSandboxed`。
- `SbieApi_EnumProcessEx` 把 count 参数当作输入/输出缓冲区容量：调用前必须
  初始化为缓冲区大小（这里是 512）。
- 驱动与用户态组件必须是兼容的构建；混用版本（例如旧驱动配新用户态 DLL）
  可能让个别接口静默失效。
