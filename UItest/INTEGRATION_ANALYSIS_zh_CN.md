# 基于原核心开发新 UI 的集成分析

> **目标功能范围**：沙箱（Box）管理、沙箱内文件管控、沙箱内网络管控。
>
> **代码基准**：`sandboxie-plus/Sandboxie` 的 `master` 快照（提交 `cdba709`，2026-10）。函数名与行为可能随版本演进，行号尤其易变。
>
> **相关原型**：本仓库 `UItest/`（Python + tkinter + ctypes，已跑通基础链路）。本文档在其实测结论之上，给出完整清单与代码风险分析。

## 1. 总体结论

核心可以原样保留，新 UI 既不需要编译进核心工程，也不需要修改内核代码。分层如下：

| 层 | 组件 | 处置 |
|---|---|---|
| 内核层 | `SbieDrv.sys` | 保留（官方签名版优先） |
| 管控层 | `SbieSvc.exe` + `Sandboxie.ini` + `Templates.ini` | 保留 |
| 注入层 | `SbieDll.dll`（`core/low` 内嵌进服务） | 保留 |
| 接口层 | `SbieApi_*` / `SbieDll_*` 导出 + 服务消息协议 | 由新 UI 调用 |
| 界面层 | `SbieCtrl.exe` / `SandMan.exe` / Shell 扩展 | 全部替换 |

一句话：**运行期只需加载 4 个二进制（`SbieDrv.sys`、`SbieSvc.exe`、`SbieDll.dll`、`SbieMsg.dll`）和 2 个配置文件（`Sandboxie.ini`、`Templates.ini`）；三大功能通过 `SbieDll.dll` 导出的 `SbieApi_*` / `SbieDll_*` 即可完成；除非要做"接管消息通道"或"自签名驱动"，一个核心源文件都不用改。**

## 2. 需要加载的文件（不含界面）

### 2.1 运行期文件

| 文件 | 由谁加载 | 作用 | 必需性 |
|---|---|---|---|
| `SbieDrv.sys` | 内核服务（SCM 注册，安装/维护工具写入） | 隔离引擎：文件、注册表、网络、进程的过滤与重定向 | 必须 |
| `SbieSvc.exe` | Windows 服务；也可被 `SbieDll_StartSbieSvc` 按需拉起 | IPC 服务器集合（进程、文件、队列、SbieIni、MountManager 等） | 必须 |
| `SbieDll.dll` | 由驱动注入沙箱进程；UI 进程链接 `SbieDll.lib` 后也会把它加载为 API 客户端 | 用户态 Hook + `SbieApi_*` / `SbieDll_*` 导出实现 | 必须 |
| `SbieMsg.dll` | UI 通过 `LoadLibrary` 加载（常量 `SBIEMSG_DLL`） | 状态码到消息文案的映射 | 建议（否则界面只能显示数字状态码） |
| `Sandboxie.ini` | 驱动与服务共同读写（系统模式在 `\SystemRoot\Sandboxie.ini`，便携模式在安装目录） | 全部配置：沙箱定义、文件规则、网络规则 | 必须 |
| `Templates.ini` | 由**驱动**启动时从安装目录读取 | 模板展开与默认值来源 | 必须（官方驱动依赖，缺失会导致默认值不完整） |
| `Start.exe` | 由 UI、快捷方式或脚本调用 | 命令行入沙启动 | 可选（可用服务消息替代） |
| `SbieIni.exe` | 由 UI 调用 | 配置查询/修改命令行工具 | 可选 |
| `KmdUtil.exe` / `UpdUtil.exe` | 由安装器或 UI 调用 | 驱动与服务的安装、启停、更新 | 产品化必需，开发期可省 |

补充：

- 安装目录即 `SbieApi_GetHomePath` 返回的 Home Path，用于定位 ini、日志与工具。
- `LowLevel.dll` 不单独分发：它被内嵌进 `SbieSvc.exe` 作为注入资源。
- 只有文件副本而没有已安装并运行的驱动与服务时，任何 UI 都无法工作（`UItest/README.md` 中对此有明确说明）。

### 2.2 编译期接口文件

| 文件 | 内容 | 何时需要 |
|---|---|---|
| `Sandboxie/core/dll/sbieapi.h` | `SbieApi_*` 全部导出声明 | 始终（主接口） |
| `Sandboxie/core/dll/sbiedll.h` | `SbieDll_*` 导出（含 `SbieDll_FormatMessage*`、`SbieDll_CallServer`、`SbieDll_RunSandboxed`、`SbieDll_KillOne`） | 消息格式化、启动/结束进程时 |
| `Sandboxie/core/dll/SboxDll.def`、`SboxDll64.def` | 导出与序号定义 | 链接 `SbieDll.lib` 时由链接器使用 |
| `Sandboxie/core/drv/api_defs.h` | API 函数码枚举与参数结构（`API_*_ARGS`） | 走 `SbieApi_Ioctl` / `SbieApi_Call`，或自实现客户端时 |
| `Sandboxie/core/drv/api_flags.h` | `CONF_GET_*` 查询标志与掩码 | 精细控制配置读取时 |
| `Sandboxie/common/defines.h` | `BOXNAME_COUNT`、`CONF_*` 操作码、各类长度上限 | 写配置与分配缓冲区 |
| `Sandboxie/common/my_version.h` | 文件名常量（SbieSvc.exe、Start.exe、SbieMsg.dll、SbieIni.exe、SbieCtrl.exe） | 路径解析 |
| `Sandboxie/common/win32_ntddk.h` | NT 结构定义 | 32/64 位混用或自实现协议时 |
| `Sandboxie/core/svc/msgids.h` 及 `*Wire.h` | 服务消息 ID 与结构（Process、File、Queue、Interactive、SbieIni、Gui、MountManager） | 只在与 SbieSvc 直接通信时需要 |
| `SandboxiePlus/QSbieAPI/`（LGPL） | 现成的纯用户态客户端实现：自建 IOCTL + ALPC，不依赖 SbieDll | 参考或移植（尤其非 C/C++ 语言） |

### 2.3 明确不需要的文件

- `Sandboxie/apps/control`（SbieCtrl.exe）、`SandboxiePlus/SandMan`（Qt 界面）、`SbieShell`、`MiscHelpers`、`QtSingleApp`、`UGlobalHotkey`：运行期完全不需要。
- `Sandboxie/apps/common`：MFC 公共界面库，不用 MFC 即可排除。
- `SandboxieTools/MiniDump`：崩溃转储工具，非必需。
- `SandboxieTools/ImBox`、`ImDisk`：只与加密沙箱相关；但注意 `SbieSvc` 的 MountManager 在编译时已包含 ImBox，删源码目录会导致服务编译失败。
- 已安装的 SandMan/SbieCtrl 可以不卸载，但要注意"双 UI 争夺 session leader"，见 5.6 节。

## 3. 需要调用的函数与过程

### 3.1 连接与生命周期

| 调用 | 作用 | 备注 |
|---|---|---|
| `SbieApi_GetVersion` / `SbieApi_GetVersionEx` | 探测驱动是否存在及版本 | 启动第一步；失败即"驱动未安装或版本不兼容" |
| `SbieApi_GetHomePath` | 取得安装目录 | 定位 ini、日志与配套工具 |
| `SbieApi_SessionLeader(0, NULL)` | 注册为"本会话的界面"（session leader） | 需要驱动信任该可执行文件；失败只影响消息通道，不影响基础功能 |
| `SbieApi_SessionLeader(session_id, &pid)` | 查询当前会话的 leader | 判断"是否已有其它界面在运行" |
| `SbieDll_StartSbieSvc` | 按需启动服务 | 服务通常随系统启动，一般无需调用 |
| `SbieApi_GetMessage` | 读取驱动消息日志 | 仅服务或 session leader 可读；其它进程得到 `STATUS_ACCESS_DENIED` |

### 3.2 沙箱管理

| 功能 | 调用 | 说明与注意 |
|---|---|---|
| 枚举沙箱 | `SbieApi_EnumBoxesEx(index, buf, ignore_hidden)` | index 从 -1 开始，循环直到返回失败 |
| 沙箱是否启用 | `SbieApi_IsBoxEnabled(box)` | 对应 `Enabled=y` 及 `Enabled=y,user1,...` 形式 |
| 查询沙箱路径 | `SbieApi_QueryBoxPath(box, file, key, ipc, len...)` | 分别返回文件根、注册表根、IPC 根，是文件管控与恢复的基础 |
| 读取配置 | `SbieApi_QueryConf` / `QueryConfBool` / `QueryConfNumber` / `QueryConfNumber64` | 默认展开模板；`QueryConfAsIs` 不展开 |
| 创建沙箱 | `SbieApi_UpdateConf(CONF_UPDATE_VALUE, box, L"Enabled", L"y")` 后 `SbieApi_ReloadConf` | 官方 Plus 的 `CreateBox` 等价于写 `Enabled=y`（见 QSbieAPI 的 `CSbieAPI::CreateBox`） |
| 删除沙箱 | `SbieApi_UpdateConf(CONF_REMOVE_SECTION, box, NULL, L"")` | 第 4 个参数必须非 NULL，实现里直接对其调用 `wcslen` |
| 修改/追加/删除设置 | `SbieApi_UpdateConf(CONF_UPDATE_VALUE / CONF_APPEND_VALUE / CONF_REMOVE_VALUE, ...)` | 逐项写入；写完后按需 `SbieApi_ReloadConf` |
| 复制或重命名沙箱 | 逐项读取配置后写入新 section，再用 `CONF_REMOVE_SECTION` 删除旧 section | QSbieAPI 的 `RenameSection` 是现成算法 |
| 启动程序入沙 | `SbieDll_RunSandboxed(box, cmd, dir, flags, si, pi)` | 宿主进程可用，等价于向服务发 `MSGID_PROCESS_RUN_SANDBOXED`；`SbieDll_RunStartExe` 在宿主进程中不可用（依赖只有沙箱进程内才初始化的状态） |
| 进程枚举 | `SbieApi_EnumProcessEx(box, all_sessions, session, pids, count)` | count 是入参容量加出参数量，必须先填好缓冲区大小 |
| 进程详情 | `SbieApi_QueryProcessEx2` / `SbieApi_QueryProcessInfoEx` | 映像路径、PID、会话、所属沙箱 |
| 终止进程 | `SbieDll_KillOne(pid)` / `SbieDll_KillAll(session, box)` | 也可通过服务消息 `MSGID_PROCESS_KILL_ALL` |
| 进程豁免控制 | `SbieApi_ProcessExemptionControl` | 需要驱动签名校验通过（见 5.1 节） |

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
| 文件恢复 | UI 侧文件复制（建目录、复制、冲突询问） | 官方 SandMan 的 `RecoverFiles` 就是普通文件复制；恢复前可选执行用户配置的安全检查器 |
| 取得箱内文件的真实宿主路径 | `SbieApi_OpenFile` 加 `SbieApi_GetFileName` | 经典界面的 `QuickRecover.cpp` 用这对接口取真实路径，而不是字符串拼接 |
| 自动恢复通知 | 驱动消息 2199 | 沙箱程序请求"另存到宿主"时触发，QSbieAPI 中对应 `FileToRecover` |
| 交互式文件迁移 | 服务队列请求 `MAN_FILE_MIGRATION` | 必须应答 `MSGID_QUEUE_PUTRPL`，否则发起方等待 |
| 驱动内部路径表 | `SbieApi_QueryPathList`（参数为 `fo`、`fc` 等两字符代码） | 面向沙箱进程或服务的低层接口，UI 一般不需要 |

### 3.4 网络管控

| 配置键或接口 | 语义 | 源码依据 |
|---|---|---|
| `AllowNetworkAccess=[process,]y or n` | 指定进程是否允许联网，默认允许 | `wfp.c` 中按 `AllowNetworkAccess` 计算 `BlockInternet` |
| `NetworkAccess=[process,]Allow 或 Block;Port=...;Address=...;Protocol=Any 或 TCP 或 UDP` | 逐条出站规则，含端口、地址、协议 | `wfp.c` 的 `WFP_LoadRules` 读取，`common/netfw.c` 解析 |
| `BlockLocalLoop` | 是否阻断本地回环 | `wfp.c` 中与 `NetworkAccess` 同处读取 |
| `NetFwTrace` | 流量跟踪，用于调试 | 通过 `Process_GetTraceFlag` 读取 |
| `NetworkDnsFilter` | DNS 阻断或重定向 | `SbieSettings.ini` 标注 `Requirements=ce`，无证书时驱动层不生效 |
| `NetworkUseProxy` | 强制代理 | `Requirements=ce3` |
| `SbieApi_QueryDrvInfo` | 读取 `SBIE_FEATURE_FLAG_WFP`，判断驱动是否带 WFP | QSbieAPI 的 `GetFeatureStr` 示例 |
| `SbieApi_CheckInternetAccess` | 检查 Internet 设备访问是否被阻断 | 经典界面用它做"网络已阻断"提示 |

行为特征（重要）：

- 规则在进程创建或配置重载时构建；规则解析失败时驱动选择 fail-closed（宁可断网也不放行，见 `WFP_UpdateProcess`）。
- 必须用 `SbieApi_QueryConf` 回读校验：键名写错时驱动按默认值处理，而默认是**允许联网**，会出现"界面显示已阻断、实际可以上网"的假安全。

### 3.5 事件与消息（后台循环，新 UI 必做）

官方 Plus 的做法是一个后台线程（`QSbieAPI::run`）循环读取三类来源，新 UI 可以直接照抄这个结构：

| 来源 | 调用 | 内容 | 是否可省 |
|---|---|---|---|
| 驱动消息日志 | `SbieApi_GetMessage` | SBIE 消息，例如崩溃、自动恢复、进程启动通知 | 可用服务日志文件兜底（见下） |
| 监控事件 | `SbieApi_MonitorControl` 打开，`SbieApi_MonitorGetEx` 读取 | 文件、注册表、网络活动，用于"沙箱活动"视图 | 可省，但界面会缺少活动视图 |
| 交互队列 | `MSGID_QUEUE_CREATE` 建队列，`MSGID_QUEUE_GETREQ` 取请求，`MSGID_QUEUE_PUTRPL` 回应答 | 沙箱进程发起的文件迁移、网络封锁放行等需要 UI 决策的请求 | 若支持这两类交互则不可省 |
| 消息文案 | `SbieDll_FormatMessage`（内部加载 SbieMsg.dll） | 状态码转文本 | 建议保留 |

无法成为 session leader 时的替代方案（`UItest` 已验证）：给服务设置注册表值 `LogFile`（位于 `HKLM\SYSTEM\CurrentControlSet\Services\SbieSvc`），服务会把 SBIE 消息写成文本文件，UI 直接读取该文件即可展示消息，**不需要任何签名**，代价是以管理员身份配置一次。

### 3.6 服务自动拉起界面的约定

`sbieiniserver.cpp` 中的 `RunSbieCtrl` 表明：服务需要提示用户时，会用 `SbieDll_RunFromHome` 从安装目录启动"agent"：

- 用户设置 `SbieCtrl_AutoStartAgent` 指定程序与参数，缺省回退到 `SBIECTRL_EXE`（`my_version.h` 中为 `SbieCtrl.exe`）。
- `SbieCtrl_EnableAutoStart=n` 可以禁止自动启动。
- 已有 leader 时直接返回"已加载"，不会重复启动。

## 4. 需要修改的文件

| 文件 | 何时必须改 | 说明与连带影响 |
|---|---|---|
| 新增的 UI 工程与源码 | 总是 | 链接 `SbieDll.lib` 或自实现客户端，核心源码不动 |
| `Sandboxie/common/my_version.h` | 想让服务默认拉起你的可执行文件，且不想依赖 ini 配置 | 改 `SBIECTRL_EXE`；安装器需同步改名，否则回退路径失效 |
| `Sandboxie/core/svc/sbieiniserver.cpp` | 想改变自动拉起或接管逻辑 | 服务端改动影响所有会话，改后需重建并签名 `SbieSvc.exe` |
| `Sandboxie/core/drv/verify.c`（`KphpTrustedPublicKey`） | UI 必须成为 session leader 且拿不到官方签名 | 必须同时重建并签名驱动；所有被校验组件（含 `SbieSvc.exe`，因 `Api_SetServicePort` 也校验）都要用你的私钥重签，否则服务注册失败会导致沙箱整体不可用 |
| `Sandboxie/core/drv/util.c`（`MyIsCallerSigned`） | 想在自编译驱动上放宽校验 | 会削弱防篡改信任链，强烈不建议 |
| `Sandboxie/core/drv/api_defs.h` | 仅当要新增 API 函数 | `API_*` 是顺序枚举（隐式加一），在中间插入或删除会改变后续所有函数码并破坏 ABI；只能在 `API_LAST` 前追加，且全部组件需整体升级 |
| `Sandboxie/core/svc/msgids.h` 与各 `*Wire.h` | 仅当要扩展服务协议 | 同上，会破坏与官方组件的互通 |
| `Sandboxie/install/Templates.ini`、`SbieSettings.ini` | 需要新的默认模板或设置元数据 | 只影响默认值与编辑器提示 |
| `Sandboxie/install/*.nsi`、`Installer/*.iss` | 打包发行 | 不属于运行期加载，但决定最终文件清单 |
| （可选）`Sandboxie/apps/start`、`apps/ini` | 若要改造这两个命令行工具 | 一般保持原样 |

不建议修改：`core/drv` 的隔离逻辑（`file.c`、`key.c`、`wfp.c`、`process*.c` 等）、`core/dll` 的 Hook 清单、既有 API 码顺序。理由见第 5 节。

## 5. 代码层面的风险

### 5.1 签名与信任链（最高优先级）

- 驱动内嵌 ECDSA-P256 公钥（`core/drv/verify.c`），通过可执行文件旁的 `.sig` 文件校验调用进程（`KphVerifyFile` 与 `MyIsCallerSigned`）。
- 受校验的操作包括 `API_SESSION_LEADER`、`API_SET_SERVICE_PORT`、`API_PROCESS_EXEMPTION_CONTROL`、`API_SET_SECURE_PARAM`、`API_GET_SECURE_PARAM`、`API_FORCE_CHILDREN` 等。
- 后果：未签名的自研 UI 调用 `SbieApi_SessionLeader(0, NULL)` 会得到 `STATUS_INVALID_SIGNATURE`（0xC000A000），无法接管消息通道；基础列举、启动、结束进程与配置功能不受影响（UItest 已实测）。
- 缓解手段：开发期执行 `bcdedit / set testsigning on` 后重启（驱动跳过校验，但整机生效）；或者替换公钥并对全部组件重签（工程量大，且需自行解决驱动签名）。

### 5.2 协议与 ABI 漂移

- UI 与核心应保持同版本，或严格限定使用的函数子集（QSbieAPI 注释明确说明只使用部分函数以兼容多个驱动版本）。
- 结构约定陷阱：`UNICODE_STRING64`、`API_NUM_ARGS` 为 8 个 64 位参数槽、指针宽度与结构 packing；32 位 UI 与 64 位驱动之间的参数布局不同（`common/win32_ntddk.h` 即为此存在）。
- 具体案例：`SbieApi_UpdateConf` 内部先对 value 调用 `wcslen`，传 NULL 会在 UI 侧直接崩溃，删除值应传空字符串；`SbieApi_EnumProcessEx` 的 count 必须先填缓冲区容量，填 0 会拿不到任何进程。

### 5.3 配置写入路径

- 必须通过 `SbieApi_UpdateConf` 写入（驱动转交服务端 `SbieIniServer`），不要直接改 `Sandboxie.ini`：服务需要对写操作串行化、加锁、更新内存缓存并通知驱动重载；绕过它会与缓存和锁竞争，出现"改了不生效或部分生效"。
- 写入后按需调用 `SbieApi_ReloadConf`，并用 `SbieApi_QueryConf` 回读确认。
- 便携模式与系统模式路径不同，应使用 `SbieApi_GetHomePath`，不要硬编码 `C:\Windows\Sandboxie.ini`。

### 5.4 沙箱边界被界面削弱的可能

- UI 只负责写配置，但 `OpenFilePath`、`AllowNetworkAccess` 这类键直接决定隔离强度；一个写错通配符（如 `*`、设备路径、UNC、带重解析点的路径）就可能把宿主目录开放给沙箱程序。
- 配置值会经驱动展开环境变量与模板（`conf_expand.c`）；UI 应做输入校验与回读校验，不信任用户输入。
- 恢复或删除文件时，箱内内容属于不可信数据：路径可能包含 junction 或符号链接（重解析点），恶意程序可以借此把"恢复到宿主"变成"覆盖任意宿主文件"。官方做法是先用 `SbieApi_OpenFile` 加 `SbieApi_GetFileName` 确认真实路径，并可选执行安全检查器，最后才复制；新 UI 若用字符串拼接路径，风险显著上升。

### 5.5 网络管控的假安全

- 驱动在规则解析失败时 fail-closed，但**键名写错**属于"没有规则"而非"解析失败"，此时默认策略是放行（`AllowNetworkAccess` 默认 TRUE），界面显示已阻断而实际可联网。
- 规则在进程创建时构建，配置重载时会刷新；改完配置立即测试同一进程可能仍按旧规则运行，容易误判。
- `NetworkDnsFilter`、`NetworkUseProxy` 等带 `Requirements=ce*` 的设置，无证书时在驱动层不生效，UI 不应把它们当作基础能力。

### 5.6 队列与消息处理

- 队列请求必须应答：`MAN_FILE_MIGRATION`、`MAN_INET_BLOCKADE` 若不调用 `MSGID_QUEUE_PUTRPL`，发起方会一直等待（表现为沙箱内程序卡住）。
- 队列创建时把事件句柄交给服务（跨进程句柄语义），实现不当会造成句柄泄漏或事件失效。
- 消息读取有身份门槛（服务或 session leader），队列名按会话区分（`*MANPROXY_` 加会话号）；多会话机器需要按会话建立实例。
- 双 UI 并存：第二个调用 `SbieApi_SessionLeader` 的进程会得到 `STATUS_DEVICE_ALREADY_ATTACHED`（0xC0000038）。官方 SandMan 有明确的提示与接管流程，新 UI 应实现等价逻辑，否则消息显示会混乱。

### 5.7 进程与权限

- UI 应作为普通用户进程运行：它要处理来自沙箱（不可信）的请求；若为了写配置或装服务而常驻管理员权限，会把 UI 变成高价值攻击面。安装与提权动作应交给一次性辅助进程（官方 KmdUtil 与 UpdUtil 模式）。
- 提权失败要可降级：拿不到 UAC 只应影响消息通道或服务安装，不应让主界面不可用（UItest 的 best-effort 设计即为此）。

### 5.8 版本与平台矩阵

- 核心支持 Win32、x64、ARM64、ARM64EC；自研 C++ UI 需要明确与核心匹配的位数组合，Python 加 ctypes 则天然跟随解释器位数。
- 内核代码没有自动化测试，驱动侧改动的回归只能人工验证（启动与停止沙箱、进程创建、文件恢复），代价与风险都高。

### 5.9 风险等级汇总

| 风险 | 等级 | 触发条件 | 主要后果 |
|---|---|---|---|
| 签名校验失败 | 高 | 自研 UI 未签名且未开启测试签名 | 无法接管消息通道，弹窗缺失 |
| 替换驱动公钥 | 高 | 改 `verify.c` 并重建 | 全部组件需重签，服务注册失败即整体不可用 |
| 手改 Sandboxie.ini | 高 | 绕过 `SbieApi_UpdateConf` | 配置不生效、缓存不一致 |
| 恢复文件跟随重解析点 | 高 | 直接按字符串路径复制 | 可能覆盖任意宿主文件 |
| 网络规则键名写错 | 中 | 未回读校验 | 假安全：显示阻断但可联网 |
| 队列请求未应答 | 中 | 未实现 `MSGID_QUEUE_PUTRPL` | 沙箱内程序卡住 |
| ABI 与结构不匹配 | 中 | 手写结构或跨版本混用 | 调用失败甚至数据损坏 |
| 双 UI 并存 | 中 | 未处理 leader 冲突 | 消息分裂显示，用户体验混乱 |
| 通配符配置过宽 | 中 | `OpenFilePath` 等写入过宽规则 | 隔离强度下降 |
| 常驻管理员权限 | 中 | UI 以管理员运行 | 攻击面扩大 |

## 6. 推荐实现与验证清单

```text
+--------------------------- 新 UI（普通用户进程） ---------------------------+
| 沙箱列表  进程列表  文件视图  网络策略编辑  消息弹窗  设置                  |
|                     SbieBridge（薄封装层）                                  |
+-----------------------------------+-----------------------------------------+
        SbieApi_* / SbieDll_* 导出（推荐）        或  自实现 IOCTL + ALPC 客户端
+-----------------------------------v-----------------------------------------+
| SbieSvc.exe（必须运行）  <-ALPC->  SbieDrv.sys（必须加载）                   |
|  进程/文件/队列/SbieIni 服务器       隔离引擎、重定向、WFP 网络过滤          |
+-----------------------------------+-----------------------------------------+
                          注入 SbieDll.dll（沙箱进程内）
```

实施顺序与验证方式：

| 阶段 | 功能 | 验证方式 |
|---|---|---|
| 1 | 版本探测与沙箱枚举 | `SbieApi_GetVersion` 返回正常；`EnumBoxesEx` 结果与官方界面一致 |
| 2 | 进程列表与启动/终止 | 启动记事本入沙后能在列表看到；终止后列表消失 |
| 3 | 配置读改写 | 写入后 `QueryConf` 回读一致；重启 UI 后仍然生效 |
| 4 | 文件管控 | 写 `OpenFilePath` 后宿主文件可直接读写；`ClosedFilePath` 后访问被拒 |
| 5 | 文件恢复 | 沙箱内生成文件，恢复到宿主成功；含重解析点的用例被拒绝或妥善处理 |
| 6 | 网络管控 | `AllowNetworkAccess=n` 后目标进程不能联网；`QueryConf` 回读与实测一致 |
| 7 | 消息通道 | 触发崩溃消息，UI 弹窗显示；若无法成为 leader，验证日志文件兜底路径 |

## 7. 参考源码索引

| 文件 | 用途 |
|---|---|
| `Sandboxie/core/dll/sbieapi.h` | `SbieApi_*` 接口清单与参数说明 |
| `Sandboxie/core/dll/sbiedll.h` | `SbieDll_*` 接口与 PATH 标志 |
| `Sandboxie/core/dll/sbieapi.c` | 各接口的具体实现，可确认参数校验细节 |
| `Sandboxie/core/drv/api_defs.h` | API 函数码与参数结构 |
| `Sandboxie/core/drv/api.c` | 各 API 的驱动侧处理与权限校验 |
| `Sandboxie/core/drv/session.c` | session leader 与相关校验 |
| `Sandboxie/core/drv/verify.c` | 签名校验实现与内嵌公钥 |
| `Sandboxie/core/drv/conf_user.c` | 沙箱配置与 `Enabled` 语义 |
| `Sandboxie/core/drv/file.c` | 文件规则（Open、Closed、Read、Write）的实现 |
| `Sandboxie/core/drv/wfp.c` | 网络规则加载与 WFP 行为 |
| `Sandboxie/common/netfw.c` | `NetworkAccess` 规则解析 |
| `Sandboxie/common/defines.h` | CONF 操作码与长度常量 |
| `Sandboxie/core/svc/msgids.h` | 服务消息 ID |
| `Sandboxie/core/svc/InteractiveWire.h` | 交互队列请求结构 |
| `Sandboxie/core/svc/sbieiniserver.cpp` | 配置写入与自动拉起界面逻辑 |
| `SandboxiePlus/QSbieAPI/SbieAPI.cpp` | 官方参考客户端：连接、消息循环、配置读写 |
| `SandboxiePlus/QSbieAPI/Sandboxie/SandBox.cpp` | 官方参考：沙箱管理与交互队列使用 |
| `SandboxiePlus/SandMan/SandManRecovery.cpp` | 官方参考：文件恢复实现 |
| `UItest/README.md` | 本仓库最小原型已验证的结论与限制 |