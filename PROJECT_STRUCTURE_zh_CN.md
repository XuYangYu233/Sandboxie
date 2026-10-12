# Sandboxie 项目结构解析（模块划分与作用）

> 分析对象：[sandboxie-plus/Sandboxie](https://github.com/sandboxie-plus/Sandboxie) 的 `master` 分支（2026-10 快照，提交 `cdba709`）。
>
> Sandboxie 是一款面向 Windows NT 内核平台的沙箱隔离软件：它让应用程序在隔离环境中运行或安装，程序对本地磁盘、映射驱动器与注册表的修改不会永久落到真实系统中，适合受控地测试不可信程序与上网冲浪。
>
> 项目同时提供 Classic（MFC 旧界面）与 Plus（Qt 新界面）两个版本，两者共享同一套核心组件（安全性、兼容性一致），区别主要在用户界面及其附加功能。

## 目录总览

| 顶层目录 | 内容 |
|---|---|
| `Sandboxie/` | 核心引擎 + Classic 界面（驱动、服务、注入 DLL、配置工具、安装脚本） |
| `SandboxiePlus/` | Qt 版新界面（Sandboxie Plus 管理器及其配套库） |
| `SandboxieTools/` | 配套工具（磁盘镜像、崩溃转储、更新工具等） |
| `Installer/` | Sandboxie Plus 的 Inno Setup 打包脚本 |
| `.github/` | CI 工作流、CodeQL、Issue 模板等 |

---

## 一、`Sandboxie/` —— 核心引擎（两款界面共用）

核心三件套：**内核驱动 `SbieDrv.sys`、服务 `SbieSvc.exe`、注入 DLL `SbieDll.dll`**。研究源码建议按此顺序入手。

### 1. `core/drv` —— 内核驱动（约 4.0 万行）→ `SbieDrv.sys`

整个软件最核心的部分。工作在 Windows 内核态：

- 系统调用挂钩与过滤（`syscall*.c`、`hook*.c`），拦截进程/线程/文件/注册表/对象管理器等操作
- 沙箱重定向：把沙箱进程的文件、注册表访问映射到沙箱目录（`box.c`、`file_xlat.c`、`key_flt.c`、`obj_flt.c`）
- 网络过滤（`wfp.c`，基于 Windows Filtering Platform）
- 令牌与权限控制（`token.c`、`session.c`），进程强制入沙（`process_force.c`）
- 与用户态通信的管理接口（`api.c`、`ipc*.c`）

### 2. `core/dll` —— 注入 DLL（约 8.8 万行，代码量最大）→ `SbieDll.dll`

注入到每个沙箱进程的用户态 DLL，Hook 大量 Win32/NT API，并与驱动/服务配合完成隔离：

- 文件类：`file*.c`（含删除、复制、回收、快照）
- 注册表类：`key*.c`
- 图形界面类：`gui*.c`、`taskbar.c`（窗口标题加 `[#]` 标识、消息过滤等）
- 网络类：`net.c`、`iphlp.c`、`wsa_defs.h`
- COM/RPC/LSA/服务/对象等：`com.c`、`rpcrt.c`、`lsa.c`、`scm*.c`、`obj.c`
- 注入与加载：`ldr*.c`、`hook_inst.c`、`lowlevel_inject.c`

### 3. `core/svc` —— 系统服务 → `SbieSvc.exe`

运行在系统服务会话中的代理层，提供各类 IPC 服务器，承接沙箱进程转发来的请求：

- 文件服务器 `fileserver.cpp`、命名管道 `namedpipeserver.cpp`
- COM/RPC 相关：`comserver*.cpp`、`EpMapperServer.cpp`
- GUI 服务器 `GuiServer.cpp`（窗口枚举、消息转发）
- 进程 / 网络 / 终端 / 队列服务器：`ProcessServer.cpp`、`iphlpserver.cpp`、`terminalserver.cpp`、`queueserver.cpp`
- 配置与辅助：`sbieiniserver.cpp`、`DriverAssist*.cpp`（驱动安装辅助）、`MountManager.cpp`

### 4. `core/low` —— 注入用最小 DLL → `LowLevel.dll`

代码注入阶段使用的小型 DLL（会作为资源内嵌进 `SbieSvc.exe`），保证在沙箱进程早期就能建立注入环境。

### 5. `apps/control` —— Classic 图形界面 → `SbieCtrl.exe`

Sandboxie Classic 的 MFC 界面，实时显示沙箱内程序活动：沙箱管理、进程列表、文件恢复、快速恢复、设置对话框、更新提示、界面警示（`BorderGuard`）等。

### 6. `apps/start` —— 命令行启动器 → `start.exe`

把程序投入沙箱运行的入口程序，也是右键菜单/命令行调用的主要方式（`start.cpp`、`rundlg.cpp`、`boxdlg.cpp`）。

### 7. `apps/com` —— COM/系统组件包装器

面向特定系统组件的辅助进程，保证这些组件在沙箱内也能正常工作：

| 子目录 | 产物 | 作用 |
|---|---|---|
| `BITS` | `SandboxieBITS.exe` | 后台智能传输服务（BITS）包装 |
| `Crypto` | `SandboxieCrypto.exe` | 加密服务包装 |
| `DcomLaunch` | `SandboxieDcomLaunch.exe` | DCOM 启动器包装 |
| `RpcSs` | `SandboxieRpcSs.exe` | RPC 子系统包装 |
| `WUAU` | `SandboxieWUAU.exe` | Windows 自动更新服务包装 |

### 8. `apps/ini` —— 配置工具 → `SbieIni.exe`

查询/修改 `Sandboxie.ini` 的命令行工具（`query.c`、`update.c`、`cmd.c`）。

### 9. `apps/common` —— 界面公共库 → `common.lib`

供 Control 与 Start 共用的 GUI 公共代码：消息框、布局、字体、命令行解析、WebView 等。

### 10. `common` —— 底层通用库（约 2.5 万行）

被核心各模块共用的基础设施：Detours（API Hook 框架）、JSON、NtCRT、INI 解析（`ini.cpp`）、红黑树、哈希表、Base64/RC4/大数、字符串处理、网络防火墙辅助等。

### 11. `msgs` —— 消息与多语言资源 → `SboxMsg.dll`

消息文本定义与 30+ 语言的翻译文件（`Text-*.txt`），通过 `parse.c` 生成资源。

### 12. `SboxHostDll` —— 宿主注入 DLL

注入到**宿主进程**（非沙箱进程）的 DLL，把本应留在宿主中的进程"拉入"沙箱。目前主要用于 MS Office（注入 `OfficeClickToRun.exe`）。

### 13. `install` —— 安装脚本与工具

- `kmdutil/` → `KmdUtil.exe`：安装期启动/停止/管理 Sbie 驱动
- NSIS 安装脚本（`SandboxieVS.nsi`、`*.nsh`）、模板与许可文本
- `release/` → 合并 x64/x86 安装包的包装工程
- `build.bat`、`ParseVersion.bat` 等构建脚本

---

## 二、`SandboxiePlus/` —— Qt 版新界面（约 9.5 万行）

### 1. `SandMan/` —— 主程序 → `SandMan.exe`

Sandboxie Plus 管理器，功能最丰富的模块，内部按职责分层：

- `Engine/`、`Models/`：数据模型与业务逻辑
- `Forms/`、`Views/`、`Windows/`、`Wizards/`：界面、视图、窗口与向导
- `Helpers/`、`Resources/`、`Troubleshooting/`：辅助、资源与故障排查
- 顶层 `SandMan.cpp`：应用主体；`BoxMonitor.cpp`、`BoxJob.cpp`、`BoxTransfer.cpp`：沙箱监控、任务与导入导出
- `OnlineUpdater.cpp`、`AddonManager.cpp`：在线更新与插件管理
- `SandManRecovery.cpp`、`SandManTray.cpp`：文件恢复与托盘图标

Plus 特有功能大多在这里实现：快照管理、触发器、隐私沙箱、加密沙箱、网络防火墙配置、全局热键、Vintage 视图等。

### 2. `QSbieAPI/` —— Qt 封装的 Sandboxie API 库

以便捷的 Qt/C++ 接口访问驱动与服务的库：

- `SbieAPI.cpp`：调用驱动/服务的主接口
- `Sandboxie/`：`SandBox.cpp`（沙箱）、`BoxedProcess.cpp`（沙箱进程）、`SbieIni.cpp`（配置）、`SbieTemplates.cpp`（模板）、`BoxBorder.cpp`（边框）

### 3. `SbieShell/` —— Shell 集成

- `SbieShellExt/`：资源管理器右键菜单扩展（"在沙箱中运行"等）
- `SbieShellPkg/`：Shell 包（桌面/开始菜单集成）
- `SbieShell/`：配套工程

### 4. `MiscHelpers/` —— 通用助手库

通用工具函数与 `Archive/`（基于 7z 的压缩/解压封装），用于沙箱的导入导出与备份。

### 5. `QtSingleApp/` —— 第三方单实例框架

保证 SandMan 只运行一个实例（第三方组件）。

### 6. `UGlobalHotkey/` —— 第三方全局热键库

支持全局热键（如挂起/终止所有沙箱进程）。

---

## 三、`SandboxieTools/` —— 配套工具（约 3.6 万行）

| 子目录 | 作用 |
|---|---|
| `ImBox/` | 磁盘镜像/内存读写工具：支持 ImDisk 虚拟盘、物理内存、虚拟内存、加密（Crypto）与镜像文件的 IO 抽象 |
| `ImDisk/` | 虚拟磁盘驱动相关头文件 |
| `MiniDump/` | 进程外崩溃转储工具（收集崩溃现场用于调试） |
| `UpdUtil/` | 驱动/服务安装与更新工具（联网下载、校验） |
| `Common/` | 共享代码：HTTP 请求、JSON、Base64、签名校验等 |

---

## 四、打包与工程化

- `Installer/`：Inno Setup 打包脚本，生成 Sandboxie Plus 安装包；含 Qt、OpenSSL、7-Zip 等依赖获取脚本（`get_*.cmd`）与多语言安装界面
- `.github/workflows/`：CI 构建（`main.yml`）、CodeQL 代码扫描、翻译更新、winget 发布等

---

## 五、整体架构关系

```
用户界面层   SbieCtrl.exe (Classic MFC) / SandMan.exe (Plus Qt) / start.exe / Shell 扩展
                    │  配置与命令（Sandboxie.ini / SbieIni / API）
管控层       SbieSvc.exe（服务，各类 IPC 服务器）+ KmdUtil / UpdUtil（安装维护）
                    │  驱动 API / IOCTL
内核层       SbieDrv.sys（系统调用挂钩、文件/注册表/网络/进程过滤与重定向）
                    │
沙箱进程     SbieDll.dll（注入的 API Hook）+ LowLevel.dll + 沙箱内的程序
宿主进程     SboxHostDll.dll（注入宿主进程，如 Office）
```

运行时大致流程：用户通过界面或 `start.exe` 发起入沙 → 服务与驱动协作创建沙箱环境并注入 `SbieDll.dll` → 沙箱进程的所有文件/注册表/网络等操作经 Hook 重定向到沙箱存储或转发给服务/驱动处理 → 界面实时展示活动并支持文件恢复。

## 六、构建要点

- 需 Visual Studio 2022 + Windows SDK + **WDK**（编译驱动必需）、MFC 组件
- 经典构建顺序：先 Win32 的 service/dll，再 x64 主工程与驱动（见 `Sandboxie/ReadMe.md`）
- Plus 界面额外需要 Qt 6 与 7-Zip；`SandboxiePlus/install_qt.cmd`、`qmake_plus.cmd` 等脚本可自动化
- 本地自编译驱动未签名，需 `bcdedit /set testsigning on` 并重启后才能加载测试

## 附：主要模块代码规模（粗略统计，不含第三方库）

| 模块 | 文件数 | 行数 |
|---|---:|---:|
| `Sandboxie/core/dll` | 111 | ≈88,400 |
| `Sandboxie/apps` | 165 | ≈45,700 |
| `Sandboxie/core/drv` | 84 | ≈39,800 |
| `Sandboxie/common` | 59 | ≈24,800 |
| `Sandboxie/core/svc` | 72 | ≈25,900 |
| `Sandboxie/core/low` | 5 | ≈2,400 |
| `Sandboxie/SboxHostDll` | 8 | ≈400 |
| `SandboxiePlus` | 331 | ≈95,400 |
| `SandboxieTools` | 100 | ≈36,500 |