# 基于原核心开发新 UI 的集成分析

> **目标功能范围**：沙箱（Box）管理、沙箱内文件管控、沙箱内网络管控。
>
> **代码基准**：`sandboxie-plus/Sandboxie` 的 `master` 快照（提交 `cdba709`，2026-10）。函数名与行为可能随版本演进，行号尤其易变。
>
> **相关原型**：本仓库 `UItest/`（Python + tkinter + ctypes，已跑通基础链路）。本文档在其实测结论之上，给出完整清单与代码风险分析。

## 0. 项目硬约束（本文档所有结论的前提）

本项目固定运行在以下条件，文档中不再考虑任何其他情形：

| 约束 | 内容 | 直接影响 |
|---|---|---|
| C1 | 永远没有官方签发的证书 | 所有由证书门控的功能永久不可用 |
| C2 | 永远不开启"不要求驱动签名"的模式（不启用测试签名等） | 驱动的信任校验始终生效 |
| C3 | 永远使用官方签名的核心二进制，不重新编译、不替换、不重新签名核心组件 | 驱动、服务、注入 DLL、工具全部保持原样 |

由 C2 与 C3 可以推出一个覆盖全篇的结论：**新 UI 永远无法通过驱动的调用方签名校验**，因此凡是要求"调用方已签名"的能力，一律视为永久不可用；凡是要求重新编译核心组件的做法，一律不在本项目范围内。后文对每个接口都会标注这一属性。

## 1. 总体结论

核心原样保留，新 UI 只能建立在外围。分层如下：

| 层 | 组件 | 处置 |
|---|---|---|
| 内核层 | `SbieDrv.sys` | 保留官方签名版，不修改 |
| 管控层 | `SbieSvc.exe` + `Sandboxie.ini` + `Templates.ini` | 保留官方签名版，不修改 |
| 注入层 | `SbieDll.dll`（`LowLevel.dll` 内嵌进服务） | 保留官方签名版，不修改 |
| 接口层 | `SbieApi_*` / `SbieDll_*` 导出 + 服务消息协议 | 由新 UI 调用，其中受签名保护的部分不可用 |
| 界面层 | 原有界面组件 | 全部由自研 UI 替换 |

一句话：**运行期依赖 4 个二进制（`SbieDrv.sys`、`SbieSvc.exe`、`SbieDll.dll`、`SbieMsg.dll`）与 2 个配置文件（`Sandboxie.ini`、`Templates.ini`），全部使用官方原版；三大功能中"沙箱管理、文件管控、网络管控"的主体可用，但消息弹窗、活动监控、强制入沙开关等受签名保护的能力永久不可用。新 UI 不需要、也不允许修改任何核心源文件。**

### 1.1 永久不可用能力速览

| 能力 | 依赖的检查 | 结论 |
|---|---|---|
| 注册为会话界面（session leader） | 驱动 `MyIsCallerSigned`（`core/drv/session.c`） | 永久不可用（`STATUS_INVALID_SIGNATURE`） |
| 读取驱动消息日志 `SbieApi_GetMessage` | 必须是本会话的 leader（`Api_GetMessage` 内校验） | 永久不可用；改用服务日志文件（见 3.5） |
| 沙箱活动监控 `API_MONITOR_CONTROL` / `API_MONITOR_GET2` | 依赖 leader 创建的会话块 | 永久不可用（除非官方界面正在运行并为该会话创建了块，不能作为设计依赖） |
| 交互队列（文件迁移、网络封锁放行应答） | 队列由 leader 创建并独占 | 永久不可用 |
| 结构化的 2199 自动恢复 / 2198 迁移进度 / 1399 进程启动通知 | 由驱动消息通道投递 | 永久不可用（服务日志文件也会主动跳过这三类） |
| 禁用强制入沙的开关（`SbieApi_DisableForceProcess` 的设置方向） | 驱动 `MyIsCallerSigned` | 永久不可用（查询方向可用） |
| 进程豁免控制 `SbieApi_ProcessExemptionControl` | 驱动 `MyIsCallerSigned` | 永久不可用 |
| 安全参数写入 `API_SET_SECURE_PARAM` | 驱动 `MyIsCallerSigned` | 永久不可用 |
| 证书门控功能：隐私模式、安全增强、加密沙箱、`NetworkDnsFilter`、`NetworkUseProxy` 等 | 驱动 `Verify_CertInfo` 与设置的 `Requirements=c*` | 永久不可用（写入也会被忽略或导致进程被处置） |
| 重新编译并替换核心组件 | 各组件的 `.sig` 校验（如服务启动时的 `API_SET_SERVICE_PORT`） | 禁止：自编译产物签名不匹配，会导致服务初始化失败等连锁问题 |

## 2. 需要加载的文件（不含界面）

### 2.1 运行期文件

| 文件 | 由谁加载 | 作用 | 必需性 |
|---|---|---|---|
| `SbieDrv.sys` | 内核服务（SCM 注册，安装工具写入） | 隔离引擎：文件、注册表、网络、进程过滤与重定向 | 必须（官方签名版） |
| `SbieSvc.exe` | Windows 服务；也可被 `SbieDll_StartSbieSvc` 按需拉起 | IPC 服务器集合（进程、文件、队列、SbieIni、MountManager 等） | 必须（官方签名版） |
| `SbieDll.dll` | 由驱动注入沙箱进程；UI 进程链接 `SbieDll.lib` 后也会把它加载为 API 客户端 | 用户态 Hook + `SbieApi_*` / `SbieDll_*` 导出实现 | 必须（官方签名版） |
| `SbieMsg.dll` | UI 通过 `LoadLibrary` 加载（常量 `SBIEMSG_DLL`） | 状态码到消息文案的映射 | 建议（否则界面只能显示数字状态码） |
| `Sandboxie.ini` | 驱动与服务共同读写（系统模式在 `\SystemRoot\Sandboxie.ini`，便携模式在安装目录） | 全部配置：沙箱定义、文件规则、网络规则 | 必须 |
| `Templates.ini` | 由**驱动**启动时从安装目录读取 | 模板展开与默认值来源 | 必须（官方驱动依赖，缺失会导致默认值不完整） |
| `Start.exe` | 由 UI、快捷方式或脚本调用 | 命令行入沙启动、`run_sbie_ctrl` 拉起界面 | 可选（官方签名版） |
| `SbieIni.exe` | 由 UI 调用 | 配置查询修改命令行工具 | 可选（官方签名版） |

补充：

- 安装目录即 `SbieApi_GetHomePath` 返回的 Home Path，用于定位 ini、日志与工具。
- `LowLevel.dll` 不单独分发：它被内嵌进 `SbieSvc.exe` 作为注入资源。
- 只有文件副本而没有已安装并运行的驱动与服务时，任何 UI 都无法工作。
- 官方安装会为若干组件附带 `.sig` 旁文件（例如 `SbieSvc.exe.sig`、`Start.exe.sig`），这些文件必须随组件原样保留；替换组件而不替换签名会导致信任校验失败。

### 2.2 编译期接口文件

| 文件 | 内容 | 何时需要 |
|---|---|---|
| `Sandboxie/core/dll/sbieapi.h` | `SbieApi_*` 全部导出声明 | 始终（主接口） |
| `Sandboxie/core/dll/sbiedll.h` | `SbieDll_*` 导出（含 `SbieDll_FormatMessage*`、`SbieDll_RunSandboxed`、`SbieDll_KillOne`） | 消息格式化、启动与结束进程时 |
| `Sandboxie/core/dll/SboxDll.def`、`SboxDll64.def` | 导出与序号定义 | 链接 `SbieDll.lib` 时由链接器使用 |
| `Sandboxie/core/drv/api_defs.h` | API 函数码枚举与参数结构 | 走 `SbieApi_Ioctl` / `SbieApi_Call`，或自实现客户端时（只读参考） |
| `Sandboxie/core/drv/api_flags.h` | `CONF_GET_*` 查询标志与掩码 | 精细控制配置读取时（只读参考） |
| `Sandboxie/common/defines.h` | `BOXNAME_COUNT`、`CONF_*` 操作码、长度上限 | 写配置与分配缓冲区 |
| `Sandboxie/common/my_version.h` | 文件名常量（SbieSvc.exe、Start.exe、SbieMsg.dll、SbieIni.exe） | 路径解析 |
| `Sandboxie/common/win32_ntddk.h` | NT 结构定义 | 32/64 位混用或自实现协议时 |
| `Sandboxie/core/svc/msgids.h` 及 `*Wire.h` | 服务消息 ID 与结构 | 只在与 SbieSvc 直接通信时需要（只读参考） |
| `SandboxiePlus/QSbieAPI/`（LGPL） | 现成的纯用户态客户端实现：自建 IOCTL + ALPC，不依赖 SbieDll | 参考或移植（尤其非 C/C++ 语言） |

## 3. 需要调用的函数与过程

### 3.1 连接与生命周期

| 调用 | 作用 | 本项目可用性 |
|---|---|---|
| `SbieApi_GetVersion` / `SbieApi_GetVersionEx` | 探测驱动是否存在及版本 | 可用；启动第一步 |
| `SbieApi_GetHomePath` | 取得安装目录 | 可用 |
| `SbieApi_SessionLeader(session_id, &pid)` | 查询当前会话的 leader | 可用（查询方向） |
| `SbieApi_SessionLeader(0, NULL)` | 注册为会话界面 | **永久不可用**，必然返回 `STATUS_INVALID_SIGNATURE`；不要调用或不要依赖其结果 |
| `SbieApi_GetMessage` | 读取驱动消息日志 | **永久不可用**（只有 leader 或服务可读）；改用服务日志文件 |
| `SbieDll_StartSbieSvc` | 按需启动服务 | 通常不需要（服务随系统启动） |

### 3.2 沙箱管理

| 功能 | 调用 | 说明与注意 |
|---|---|---|
| 枚举沙箱 | `SbieApi_EnumBoxesEx(index, buf, ignore_hidden)` | index 从 -1 开始，循环直到返回失败 |
| 沙箱是否启用 | `SbieApi_IsBoxEnabled(box)` | 对应 `Enabled=y` 及 `Enabled=y,user1,...` 形式 |
| 查询沙箱路径 | `SbieApi_QueryBoxPath(box, file, key, ipc, len...)` | 分别返回文件根、注册表根、IPC 根，是文件管控与恢复的基础 |
| 读取配置 | `SbieApi_QueryConf` / `QueryConfBool` / `QueryConfNumber` / `QueryConfNumber64` | 默认展开模板；`QueryConfAsIs` 不展开 |
| 创建沙箱 | `SbieApi_UpdateConf(CONF_UPDATE_VALUE, box, L"Enabled", L"y")` 后 `SbieApi_ReloadConf` | 官方 Plus 的 `CreateBox` 等价于写 `Enabled=y` |
| 删除沙箱 | `SbieApi_UpdateConf(CONF_REMOVE_SECTION, box, NULL, L"")` | 第 4 个参数必须非 NULL，实现里直接对其调用 `wcslen` |
| 修改、追加、删除设置 | `SbieApi_UpdateConf(CONF_UPDATE_VALUE / CONF_APPEND_VALUE / CONF_REMOVE_VALUE, ...)` | 逐项写入；写完后按需 `SbieApi_ReloadConf` |
| 复制或重命名沙箱 | 逐项读取配置后写入新 section，再用 `CONF_REMOVE_SECTION` 删除旧 section | QSbieAPI 的 `RenameSection` 是现成算法 |
| 启动程序入沙 | `SbieDll_RunSandboxed(box, cmd, dir, flags, si, pi)` | 可用；等价于向服务发 `MSGID_PROCESS_RUN_SANDBOXED`；`SbieDll_RunStartExe` 在宿主进程中不可用 |
| 进程枚举 | `SbieApi_EnumProcessEx(box, all_sessions, session, pids, count)` | count 是入参容量加出参数量，必须先填好缓冲区大小 |
| 进程详情 | `SbieApi_QueryProcessEx2` / `SbieApi_QueryProcessInfoEx` | 映像路径、PID、会话、所属沙箱 |
| 终止进程 | `SbieDll_KillOne(pid)` / `SbieDll_KillAll(session, box)` | 可用；也可通过服务消息 `MSGID_PROCESS_KILL_ALL` |
| 禁用强制入沙开关 | `SbieApi_DisableForceProcess` | 查询方向可用；**设置方向永久不可用**（需要签名） |
| 进程豁免控制 | `SbieApi_ProcessExemptionControl` | **永久不可用**（整个接口需要签名校验） |

### 3.3 文件管控

生效链路：UI 写配置，驱动在进程创建或配置重载时读取规则并构建匹配表（`Sandboxie/core/drv/file.c` 顶部读取 `OpenFilePath`、`ClosedFilePath`、`ReadFilePath`、`WriteFilePath`），随后在文件打开时按规则放行、阻断或重定向。

| 配置键 | 语义 | 用途与风险 |
|---|---|---|
| `FileRootPath` | 沙箱文件根目录 | 定位箱内文件；文件恢复与清理都依赖它 |
| `OpenFilePath` | 允许直接访问宿主文件或目录，不重定向 | 白名单放行，等于在沙箱上开洞，最需要审慎 |
| `ClosedFilePath` | 阻断对指定路径的访问 | 黑名单 |
| `ReadFilePath` | 只读放行，写操作仍被沙箱化 | 只读共享宿主文件 |
| `WriteFilePath` | 隐藏宿主同名文件，新建内容落在沙箱 | 特殊场景 |
| `OpenKeyPath` / `ClosedKeyPath` | 注册表侧的对应行为 | 与文件规则同构 |

| 功能 | 调用或做法 | 说明 |
|---|---|---|
| 读取某沙箱的文件规则 | `SbieApi_QueryConf(box, L"OpenFilePath", index, buf, len)` 逐条枚举 | UI 应优先使用的正规途径 |
| 箱内文件枚举 | 直接遍历 `QueryBoxPath` 得到的 FileRootPath | 目录按盘符组织，官方界面也是这么做的 |
| 文件恢复 | UI 侧文件复制（建目录、复制、冲突询问） | 恢复本质是普通文件复制；恢复前可选执行用户配置的安全检查器 |
| 取得箱内文件的真实宿主路径 | `SbieApi_OpenFile` 加 `SbieApi_GetFileName` | 用这对接口取真实路径，而不是字符串拼接 |
| 自动恢复与文件迁移的实时通知 | 消息 2199、队列请求 `MAN_FILE_MIGRATION` | **永久不可用**（前者被服务日志跳过，后者属于 leader 独占的交互队列）；文件恢复只能由用户手动触发 |
| 驱动内部路径表 | `SbieApi_QueryPathList`（参数为 `fo`、`fc` 等两字符代码） | 面向沙箱进程或服务的低层接口，UI 一般不需要 |

### 3.4 网络管控

| 配置键或接口 | 语义 | 源码依据 |
|---|---|---|
| `AllowNetworkAccess=[process,]y or n` | 指定进程是否允许联网，默认允许 | `wfp.c` 中按 `AllowNetworkAccess` 计算 `BlockInternet` |
| `NetworkAccess=[process,]Allow 或 Block;Port=...;Address=...;Protocol=Any 或 TCP 或 UDP` | 逐条出站规则，含端口、地址、协议 | `wfp.c` 的 `WFP_LoadRules` 读取，`common/netfw.c` 解析 |
| `BlockLocalLoop` | 是否阻断本地回环 | `wfp.c` 中与 `NetworkAccess` 同处读取 |
| `NetFwTrace` | 流量跟踪，用于调试 | 通过 `Process_GetTraceFlag` 读取 |
| `SbieApi_QueryDrvInfo` | 读取 `SBIE_FEATURE_FLAG_WFP`，判断驱动是否带 WFP | QSbieAPI 的 `GetFeatureStr` 示例 |
| `SbieApi_CheckInternetAccess` | 检查 Internet 设备访问是否被阻断 | 经典界面用它做"网络已阻断"提示 |
| `NetworkDnsFilter` / `NetworkUseProxy` | DNS 过滤与强制代理 | **永久不可用**：设置带 `Requirements=ce*`，由驱动端证书检查门控，本项目没有证书，写入也不会生效 |

行为特征（重要）：

- 规则在进程创建或配置重载时构建；规则解析失败时驱动选择 fail-closed（宁可断网也不放行，见 `WFP_UpdateProcess`）。
- 必须用 `SbieApi_QueryConf` 回读校验：键名写错时驱动按默认值处理，而默认是**允许联网**，会出现"界面显示已阻断、实际可以上网"的假安全。

### 3.5 消息与通知（本项目只有唯一可用通道）

| 通道 | 依赖 | 本项目结论 |
|---|---|---|
| 驱动消息日志 `SbieApi_GetMessage` | 调用方必须是 session leader | 永久不可用 |
| 交互队列 `MSGID_QUEUE_CREATE` / `GETREQ` / `PUTRPL` | 队列由 leader 创建并独占 | 永久不可用 |
| 沙箱活动监控 `API_MONITOR_CONTROL` / `API_MONITOR_GET2` | 需要 leader 创建的会话块 | 永久不可用 |
| **服务日志文件（LogFile）** | 服务注册表值 `LogFile` + 读取该文本文件 | **唯一可用通道**，但有限制，见下 |

服务日志文件（`DriverAssistLog.cpp`）的性质与限制：

- 注册表值位于 `HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc`，格式为 `版本;文件路径`（如 `2;C:\Windows\System32\LogFiles\Sandboxie.log`），版本 2 附带时间戳、版本 3 附带用户名；需要管理员一次性配置。
- 文件为 UTF-16，内容为格式化后的消息文本（含 `SBIExxxx` 编号），UI 直接读取即可展示，不需要任何签名。
- **服务会主动跳过三类通知**：2199 自动恢复、2198 文件迁移进度、1399 进程启动。因此这三类结构化通知在本项目中永久缺失，文件恢复只能手工触发，也不会有"程序已入沙"的实时事件。
- 其余用户可见消息（崩溃、启动被阻止等）都会进入该文件。

服务自动拉起界面的约定（`sbieiniserver.cpp` 的 `RunSbieCtrl`）仍然可用：用户设置 `SbieCtrl_AutoStartAgent` 指定程序与参数（未设置时由服务使用内置默认值），由服务从安装目录启动；`SbieCtrl_EnableAutoStart=n` 可禁止。**注意：被拉起不等于获得消息权限，本项目的 UI 仍只能靠日志文件显示消息。**

## 4. 需要修改的文件

在硬约束下，修改面被压缩到"核心之外"。原则：**凡是要重新编译核心组件的改动，一律不做；凡是会让官方二进制失去 `.sig` 的替换，一律不做。**

### 4.1 可以修改（不触碰签名）

| 文件 | 用途 | 说明 |
|---|---|---|
| 新增的 UI 工程与源码 | 界面主体 | 链接 `SbieDll.lib` 或自实现客户端；核心源文件不动 |
| 自带命令行工具（如探测程序） | 自检与排障 | 同上 |
| 打包脚本与安装清单 | 发行 | 必须**原样携带**官方签名二进制及其 `.sig` 文件 |
| `Sandboxie.ini`（运行时数据） | 用户设置、沙箱配置 | 通过 `SbieApi_UpdateConf` 写入，不直接编辑文件 |
| `Templates.ini`（安装目录数据文件） | 自定义默认模板 | 驱动只读取，不做签名校验；但写错会影响默认值甚至隔离强度，需谨慎测试 |
| 服务注册表值（如 `LogFile`、`MultiLog`） | 消息日志与分流 | 管理员一次性写入 |
| 官方界面的配置文件（如 `Sandboxie-Plus.ini`） | 关闭官方弹窗等行为 | 例如 `[UIConfig] DisableMessages=true`，避免与自研 UI 重复弹窗 |

### 4.2 禁止修改（会破坏信任链或超出项目范围）

| 对象 | 为什么禁止 |
|---|---|
| `Sandboxie/core/drv` 全部（含 `verify.c`、`util.c`） | 重编译的驱动没有官方签名，在本项目（不启用测试签名）条件下无法加载；改动隔离逻辑还会引入 BSOD 与逃逸风险 |
| `Sandboxie/core/svc`（含 `sbieiniserver.cpp`、`ProcessServer.cpp` 等） | 自编译 `SbieSvc.exe` 无法匹配 `SbieSvc.exe.sig`，服务启动时的 `API_SET_SERVICE_PORT` 校验会失败，导致服务的核心通信通道注册不上 |
| `Sandboxie/core/dll`、`core/low` | 注入链核心；自编译版本与官方驱动的版本与协议匹配无保证 |
| `Sandboxie/apps/start`、`apps/ini` | 官方签名组件，替换后相关接口的签名校验可能失败 |
| `Sandboxie/msgs` | 官方签名资源组件，改动无收益 |
| `Sandboxie/core/drv/api_defs.h`、`core/svc/msgids.h`、`*Wire.h` | 只可作为只读协议参考；扩展协议需要两端同时实现，而两端都是官方二进制，扩展不可实现 |

### 4.3 想要"服务默认拉起自研 UI"的正确做法

不需要改代码：把用户设置 `SbieCtrl_AutoStartAgent` 指向自研程序（可带参数）即可由服务拉起。但它不会获得任何签名能力，消息仍需从服务日志文件读取。

## 5. 代码层面的风险

### 5.1 签名信任链带来的永久能力缺失（本项目的核心约束）

- 驱动内嵌 ECDSA-P256 公钥，用可执行文件旁边的 `.sig` 校验调用进程（`core/drv/verify.c` 的 `KphVerifyCurrentProcess` 与 `core/drv/util.c` 的 `MyIsCallerSigned`）。
- 受校验的操作包括：`API_SESSION_LEADER`（设置方向）、`API_SET_SERVICE_PORT`、`API_PROCESS_EXEMPTION_CONTROL`、`API_SET_SECURE_PARAM`、`API_FORCE_CHILDREN`、`API_DISABLE_FORCE_PROCESS`（设置方向）。
- 新 UI 没有 `.sig`，上述操作必然失败（`STATUS_INVALID_SIGNATURE`，即 `0xC000A000`）。因此会话领导权、驱动消息日志、活动监控、交互队列、强制入沙开关等能力在本项目中不存在替代实现路径，只能改用第 3.5 节的服务日志文件，或干脆不做这些功能。

### 5.2 协议与 ABI 漂移

- UI 与核心必须同版本，或严格限定使用的函数子集（QSbieAPI 注释明确说明只使用部分函数以兼容多个驱动版本）。
- 结构约定陷阱：`UNICODE_STRING64`、`API_NUM_ARGS` 为 8 个 64 位参数槽、指针宽度与结构 packing；32 位 UI 与 64 位驱动之间的参数布局不同（`common/win32_ntddk.h` 即为此存在）。
- 具体案例：`SbieApi_UpdateConf` 内部先对 value 调用 `wcslen`，传 NULL 会在 UI 侧直接崩溃，删除值应传空字符串；`SbieApi_EnumProcessEx` 的 count 必须先填缓冲区容量，填 0 会拿不到任何进程。

### 5.3 配置写入路径

- 必须通过 `SbieApi_UpdateConf` 写入（驱动转交服务端 `SbieIniServer`），不要直接改 `Sandboxie.ini`：服务需要对写操作串行化、加锁、刷新内存缓存并通知驱动重载；绕过它会与缓存和锁竞争，出现"改了不生效或部分生效"。
- 写入后按需调用 `SbieApi_ReloadConf`，并用 `SbieApi_QueryConf` 回读确认。
- 便携模式与系统模式路径不同，应使用 `SbieApi_GetHomePath`，不要硬编码 `C:\Windows\Sandboxie.ini`。

### 5.4 沙箱边界被界面削弱的可能

- UI 只负责写配置，但 `OpenFilePath`、`AllowNetworkAccess` 这类键直接决定隔离强度；一个写错通配符（如 `*`、设备路径、UNC、带重解析点的路径）就可能把宿主目录开放给沙箱程序。
- 配置值会经驱动展开环境变量与模板（`conf_expand.c`）；UI 应做输入校验与回读校验，不信任用户输入。
- 恢复或删除文件时，箱内内容属于不可信数据：路径可能包含 junction 或符号链接（重解析点），恶意程序可以借此把"恢复到宿主"变成"覆盖任意宿主文件"。官方做法是先用 `SbieApi_OpenFile` 加 `SbieApi_GetFileName` 确认真实路径，并可选执行安全检查器，最后才复制；新 UI 若用字符串拼接路径，风险显著上升。

### 5.5 网络管控的假安全

- 驱动在规则解析失败时 fail-closed，但**键名写错**属于"没有规则"而非"解析失败"，此时默认策略是放行（`AllowNetworkAccess` 默认 TRUE），界面显示已阻断而实际可联网。
- 规则在进程创建时构建，配置重载时会刷新；改完配置立即测试同一进程可能仍按旧规则运行，容易误判。
- `NetworkDnsFilter`、`NetworkUseProxy` 等由证书门控的设置在无证书环境下**永久不生效**，UI 不应提供这些开关，也不要向用户承诺可用。

### 5.6 消息与通知的功能缺口

- 由于拿不到会话领导权，消息弹窗、活动监控、交互队列全部不可用；唯一替代是服务日志文件，而该文件**不包含** 2199 自动恢复、2198 迁移进度、1399 进程启动三类通知。
- 直接后果：文件恢复只能手工触发；无法实现"程序入沙时提示/记录"的实时事件；无法响应沙箱程序发起的文件迁移与联网放行请求（这些请求既然无人应答，行为由核心默认处置决定）。
- 如果官方界面仍在运行，它会继续作为会话界面处理这些请求与弹窗；自研 UI 与它并存时要注意职责边界（自研 UI 只管管理与配置，不要承诺接管消息）。

### 5.7 进程与权限

- UI 应作为普通用户进程运行：它要处理来自沙箱（不可信）的请求；常驻管理员权限会把 UI 变成高价值攻击面。唯一需要提权的是一次性的注册表配置（如 `LogFile`）。
- 提权失败要可降级：拿不到 UAC 只应影响消息日志通道，不应让主界面不可用。

### 5.8 版本与平台矩阵

- 核心支持 Win32、x64、ARM64、ARM64EC；自研 C++ UI 需要明确与核心匹配的位数组合，Python 加 ctypes 则跟随解释器位数。
- 内核代码没有自动化测试，任何涉及驱动的改动都只能人工回归（本项目禁止改驱动，因此该风险只需通过"不修改"来规避）。

### 5.9 风险等级汇总

| 风险 | 等级 | 触发条件 | 主要后果 |
|---|---|---|---|
| 依赖签名能力（leader、监控、队列） | 高 | 设计时未确认接口是否受保护 | 功能永久不可用，返工 |
| 手改 Sandboxie.ini | 高 | 绕过 `SbieApi_UpdateConf` | 配置不生效、缓存不一致 |
| 恢复文件跟随重解析点 | 高 | 直接按字符串路径复制 | 可能覆盖任意宿主文件 |
| 网络规则键名写错 | 中 | 未回读校验 | 假安全：显示阻断但可联网 |
| 误用证书门控设置 | 中 | 实现了 `NetworkDnsFilter` 等不可用功能 | 功能不生效，用户困惑 |
| ABI 与结构不匹配 | 中 | 手写结构或跨版本混用 | 调用失败甚至数据损坏 |
| 双 UI 并存 | 中 | 未界定职责 | 消息显示混乱或缺失 |
| 通配符配置过宽 | 中 | `OpenFilePath` 等写入过宽规则 | 隔离强度下降 |
| 常驻管理员权限 | 中 | UI 以管理员运行 | 攻击面扩大 |

## 6. 推荐实现与验证清单

```text
+--------------------------- 新 UI（普通用户进程） ---------------------------+
| 沙箱列表  进程列表  文件视图  网络策略编辑  设置  日志文件消息窗            |
|                     SbieBridge（薄封装层）                                  |
+-----------------------------------+-----------------------------------------+
        SbieApi_* / SbieDll_* 导出（推荐可用集合）   只读参考 IOCTL/ALPC 协议
+-----------------------------------v-----------------------------------------+
| SbieSvc.exe（官方签名版，必须运行）<-ALPC->  SbieDrv.sys（官方签名版）      |
|  进程/文件/SbieIni 服务器             隔离引擎、重定向、WFP 网络过滤        |
+-----------------------------------+-----------------------------------------+
                          注入 SbieDll.dll（沙箱进程内）
```

实施顺序与验证方式：

| 阶段 | 功能 | 验证方式 |
|---|---|---|
| 1 | 版本探测与沙箱枚举 | `SbieApi_GetVersion` 返回正常；`EnumBoxesEx` 结果与官方界面一致 |
| 2 | 进程列表、启动与终止 | 启动记事本入沙后能在列表看到；终止后列表消失 |
| 3 | 配置读改写 | 写入后 `QueryConf` 回读一致；重启 UI 后仍然生效 |
| 4 | 文件管控 | 写 `OpenFilePath` 后宿主文件可直接读写；`ClosedFilePath` 后访问被拒 |
| 5 | 文件恢复 | 沙箱内生成文件，手动恢复到宿主成功；含重解析点的用例被拒绝或妥善处理 |
| 6 | 网络管控 | `AllowNetworkAccess=n` 后目标进程不能联网；`QueryConf` 回读与实测一致 |
| 7 | 消息通道 | 配置 `LogFile` 注册表值；触发崩溃消息，确认文本文件出现新行且 UI 弹窗显示 |

阶段 7 是消息能力的全部：不要实现依赖 session leader 的弹窗、监控或自动恢复交互。

## 7. 官方服务器请求排查（仅限本文档列出的组件）

结论：本文档第 2 节列出的组件在运行中都不会自动向官方服务器发送请求。仅有的官方网址接触点分两类，都出现在 `Start.exe` 与 `SbieMsg.dll` 中：一类是"用户点击后由浏览器打开"，另一类是"纯文本"。

| 组件 | 自动请求 | 说明与依据 |
|---|---|---|
| `SbieDrv.sys` | 否 | 对 `\Device\Afd` 等路径的处理只是过滤沙箱内程序的网络设备访问（`core/drv/file.c`）；证书校验完全在本地完成：读取安装目录下的 `Certificate.dat`，用内嵌公钥验证哈希与签名（`core/drv/verify.c`），没有网络调用 |
| `SbieSvc.exe` | 否 | 服务代码中不存在 HTTP/WinHTTP 客户端，职责限于 IPC 与进程管理 |
| `SbieDll.dll` | 否 | `net.c`、`proxy.c` 中的 SOCKS5 逻辑是替沙箱内程序连接**用户配置的代理地址**（该功能受证书门控，本项目不可用）；`dns_filter.c` 只改写沙箱内程序的 DNS 解析。DLL 自身不连接任何官方地址 |
| `SbieMsg.dll` | 否 | 纯消息文本资源；部分文案包含官方网址（如升级提示），只是文字，不产生请求 |
| `Sandboxie.ini`、`Templates.ini` | 否 | 数据文件；`Templates.ini` 中的 `Tmpl.Url=` 是模板元数据（指向第三方厂商主页），仅供界面展示 |
| `Start.exe` | 否（自身没有 HTTP 代码） | 提醒对话框的按钮通过 `ShellExecute` 打开官方网址（`apps/start/aboutdlg.cpp`），只在用户点击时发生；以 `http://` 或 `https://` 开头的命令行参数会被当作"在沙箱中打开该网址"（`apps/start/start.cpp`） |
| `SbieIni.exe` | 否 | 纯配置读写工具 |

对自研 UI 的两条约束：

- 不要把消息文本中的网址自动打开，也不要渲染为可点击链接；
- 启动程序统一使用 `SbieDll_RunSandboxed`（见 3.2 节），不经过 `Start.exe`，即可避免其提醒对话框出现。

以上为源码层结论，未进行网络抓包验证。

## 8. 参考源码索引

| 文件 | 用途 |
|---|---|
| `Sandboxie/core/dll/sbieapi.h` | `SbieApi_*` 接口清单与参数说明 |
| `Sandboxie/core/dll/sbiedll.h` | `SbieDll_*` 接口与 PATH 标志 |
| `Sandboxie/core/dll/sbieapi.c` | 各接口的实现，可确认参数校验细节 |
| `Sandboxie/core/drv/api_defs.h` | API 函数码与参数结构 |
| `Sandboxie/core/drv/api.c` | 各 API 的驱动侧处理与权限校验（`Api_GetMessage` 的 leader 校验在这里） |
| `Sandboxie/core/drv/session.c` | session leader、监控与相关签名校验 |
| `Sandboxie/core/drv/verify.c` | 签名校验实现与内嵌公钥 |
| `Sandboxie/core/drv/util.c` | `MyIsCallerSigned`：测试签名模式与 `.sig` 校验逻辑 |
| `Sandboxie/core/drv/conf_user.c` | 沙箱配置与 `Enabled` 语义 |
| `Sandboxie/core/drv/file.c` | 文件规则（Open、Closed、Read、Write）的实现 |
| `Sandboxie/core/drv/wfp.c` | 网络规则加载与 WFP 行为 |
| `Sandboxie/common/netfw.c` | `NetworkAccess` 规则解析 |
| `Sandboxie/common/defines.h` | CONF 操作码与长度常量 |
| `Sandboxie/core/svc/msgids.h` | 服务消息 ID |
| `Sandboxie/core/svc/InteractiveWire.h` | 交互队列请求结构（本项目不可用，只作了解） |
| `Sandboxie/core/svc/sbieiniserver.cpp` | 配置写入与自动拉起界面逻辑 |
| `Sandboxie/core/svc/DriverAssistLog.cpp` | 服务日志文件格式与"跳过三类通知"的行为 |
| `Sandboxie/core/dll/net.c` | 沙箱内程序的网络 Hook 与 SOCKS5 代理转发（不产生官方请求） |
| `Sandboxie/apps/start/aboutdlg.cpp` | 提醒对话框及打开官方网址的按钮 |
| `Sandboxie/apps/start/start.cpp` | 命令行参数处理（含以 http/https 参数在沙箱中打开网址） |
| `SandboxiePlus/QSbieAPI/SbieAPI.cpp` | 官方参考客户端：连接、消息循环、配置读写 |
| `SandboxiePlus/QSbieAPI/Sandboxie/SandBox.cpp` | 官方参考：沙箱管理与启动流程 |
| `UItest/README.md` | 本仓库最小原型已验证的结论与限制 |