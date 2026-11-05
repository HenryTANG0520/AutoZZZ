# 本机 MCP 服务

2026-11-05：源码已接入受控 MCP 服务、固定上游只读/指南兼容层和配置/画面编辑，尚待新完整包发布及真实 MCP 客户端、游戏联动验收。目录保留上游28个工具名称和3个prompts；其中7个操作工具仍明确返回尚缺授权适配，不能把“目录完整”当成“功能全部完成”。另有2个AutoZZZ扩展工具。

## 使用入口

在应用运行窗口点击 **MCP服务…**。服务复用这个运行窗口的 SDK 工作进程、已准备应用和 `RuntimeSession`，不另起游戏执行器。默认端点为 `http://127.0.0.1:23001/mcp`，可在启动前修改端口。

点击启动后，界面显示连接地址及本次 Bearer 令牌；令牌默认遮蔽，由用户点击复制到 MCP 客户端的认证配置。令牌只在本次启动有效，不写进日志或运行报告。关闭管理窗口会停止服务及其启动的任务。运行窗口关闭时也会关闭服务并等待原 SDK 工作进程退出。

执行应用前，先在原运行窗口完成初始化和准备。MCP管理窗口显示当前准备应用及资源操作清单；用户核对后单次授权。游戏须已经进入可运行状态，远端工具不会自动启动或重新登录游戏。原正常启动/登录入口继续保留。

刷本授权只能沿用已经存在的持久预算编号，并核对原总电量、场次和单场上限。此入口不会新建账本、提高或续满额度。存在待核对出战时拒绝重新授权。补电等资源行为仍需本地界面明确确认。

## 执行范围

| MCP工具 | 行为 |
| --- | --- |
| `get_run_status()` | 返回原上游RunSlot状态字段，额外`autozzz`字段包含受控执行状态；不返回本地资源确认令牌 |
| `run_prepared_application()` | 执行本地已准备并单次授权的固定应用，无远端应用或预算参数 |
| `pause_run(paused)` | 暂停或继续本服务启动的原任务 |
| `stop_run()` | 请求原任务停止，工作线程实际结束前仍保留运行锁 |

授权绑定准备任务编号、应用、配置哈希、预算及资源确认；默认15分钟有效。配置或准备任务变化后，旧授权失效；调用启动前即消耗授权，失败或结果未知不会自动重发。重复的相同 MCP 请求编号返回原响应，不另起任务。撤销未用授权不会重启或改变正在运行的任务。

实际启动、暂停和停止都委托已有 `OneDragonService` 与 `OneDragonExecution`，沿用全局任务互斥、焦点等待、观察代次、资源确认及持久预算。MCP不能暂停或停止其他界面/会话启动的任务。

## 编辑会话

在本地MCP管理窗口的“编辑会话”分组中选择**配置**和/或**画面区域**，点击**启用本次编辑**。这是连续编辑的本地开关，无须每次弹确认；远端工具不能启用、扩展或恢复此授权。配置编辑限当前账号，group_id须为合法单个名称；画面资料限当前profile。

会话记录固定源码签名、当前账号，以及配置/画面YAML的修订基线。每次写入先检查运行空闲、原stdio/HTTP共享调度锁和跨进程profile锁，再执行上游真实校验。成功后更新本会话基线；外部修改或切号使会话失效，不自动采纳新版本。开始运行或关闭服务也会关闭编辑。每次修改撤销未使用的运行grant，后续运行须重新准备、核对资源及授权，不修改持久电量预算。

- `set_config`复用ConfigurationStore标量类型/枚举校验和上游字段/只读校验；`add_config_item`、`delete_config_item`使用原配置路由和领域方法。新增项不能指定只读身份字段。上游中途save被atomic_apply抑制，先在当前账号的临时YAML校验并序列化，再以已知旧/新字节通过MapTransaction归档原子发布；不直接裸写原配置文件。
- `upsert_screen_area`、`delete_screen_area`使用固定上游区域、矩形、模板存在性等校验。复用NativeResourceGuard的受限画面路径清单和ResourceArchives事务，不创建截图、游戏输入或第二个RunSlot，也不安装全局TemplateInfo补丁。
- 本地**归档**选择和**恢复所选归档**按当前new_hash核对后恢复，已有其他修改时拒绝覆盖；恢复本身也归档。配置归档位于`archives/mcp-config`，画面归档沿用`archives/devtools`。
- 中断的配置发布会阻止新编辑，本地归档列表提供**恢复中断配置保存**，绑定当时journal哈希，仅恢复已知旧/新版本，外部未知字节拒绝。中断的画面发布沿用开发资源工具的**恢复中断修改…**审阅/恢复入口；关闭当前运行窗口后可从开发工具进入，不自动恢复现场。

原编辑工具的名称和参数schema保留；修订和权限保存在本地会话，不增加可由远端伪造的授权参数。保存失败会关闭本次编辑，保留错误原因和归档证据。

## 报告与关闭

远端启动产生的新 `job_id` 必须明确指向原准备任务和当前应用，原 `RuntimeSession` 才会接续。无关编号仍按串任务错误拒绝。正常接续会生成标准 `report.json` 与 `run.log`；报告 `request.source` 为 `mcp`，保留预算、应用结果、失败、停止和进程退出事实。瞬间完成的任务同样生成终态报告，不要求恰好轮询到运行中状态。

本地MCP界面请求使用原连接的带标签RPC。失败返回对应 `rpc_error`，解除原界面等待；关闭运行窗口会解除未完成界面等待，不重发其操作。退出顺序是先停止MCP监听，再停止并等待原应用，最后关闭SDK。网络处理和stdio服务调用共享调度锁；关闭监听不持有该外层调度锁等待线程。

服务日志为有界的200条生命周期/操作记录，可在窗口刷新查看；正常游戏日志和运行结果仍写原报告。它没有把上游全部服务日志界面逐项等价迁入。

## 传输

实现遵循 [MCP 2025-03-26 Streamable HTTP 规范](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)，使用标准库HTTP服务器和JSON响应，无额外 `mcp` / `uvicorn` 安装依赖；没有启动固定SDK原 `zzz_od.backend.entry.server`。[prompts/list与prompts/get](https://modelcontextprotocol.io/specification/2025-03-26/server/prompts)返回固定上游原提示词正文。

- 仅绑定 `127.0.0.1`；检查Host、Origin、Bearer。
- 支持初始化版本协商、`Mcp-Session-Id`、`MCP-Protocol-Version`校验、`tools/list`、`tools/call`、`prompts/list`、`prompts/get`、`ping`及通知202响应。
- GET返回405，明确不提供SSE；DELETE注销会话。
- 请求上限64 KiB，批次最多16条，会话最多16个，重放缓存有界。
- 不支持旧版HTTP+SSE、主动服务端通知、进度SSE、断线流重放或持久远端会话。

## 与固定上游的差异

| 上游能力 | 当前状态 |
| --- | --- |
| `get_run_status`、`stop_run` | 状态复用原RunSlot；停止仅限本服务启动的任务 |
| `run_one_dragon`、`run_standalone_app` | 保留原名和参数schema；`block=false`执行须匹配本地准备/单次授权，沿用同一任务、报告和预算。`block=true`明确拒绝，不伪造等待完成 |
| `open_game`、`close_game` | 原名称/schema可发现；因尚缺专用本地授权适配而明确拒绝，不执行游戏输入 |
| `check_game_window`、`capture_game_screen`、`analyze_screen` | 已直接调用固定上游工具正文与感知方法，复用原ctx；截图仍按上游打码UID；支持指定截图离线分析 |
| `click_game`、`key_tap`、`drag`、`input_text` | 保留原名称/schema，尚缺与准备任务绑定的输入授权适配，明确拒绝 |
| `upsert_screen_area`、`delete_screen_area` | 已接本地编辑开关、profile修订、原区域校验和受限归档事务 |
| `list_applications`、`get_predefined_teams`、`list_operations`、`describe_operation` | 已调用固定上游只读工具正文；operation扫描定位固定源码而非可写配置目录，描述仅允许扫描清单中的op_id，不导入任意模块 |
| `run_operation` | 保留原名/schema，尚缺与本地准备操作、参数哈希和资源授权绑定的专用入口，明确拒绝 |
| `list_app_configs`、`describe_config`、`get_config` | 已调用固定上游路由和配置查询正文，不将AutoZZZ自有配置结构伪装成上游结果 |
| `set_config`、`add_config_item`、`delete_config_item` | 已接本地编辑开关、当前账号、原领域校验、atomic_apply暂存和归档原子发布 |
| prompts及usage guides | 固定源码实际有3个prompts、4项指南目录、2个指南工具，已保留原名、参数、正文；初始化同时说明AutoZZZ授权边界 |

兼容层不复制工具逻辑：从固定SDK的`app.py`、`service_app.py`、`config_app.py`和`prompts.py`读取原工厂/装饰器注册，执行原函数。没有构造第二个`ZzzBackendContext`运行槽，也没有替换进程全局`mcp`模块。工具目录的`_meta["autozzz/availability"]`明确标明只读、单次授权执行、修订绑定编辑会话、仅本会话任务控制或尚缺适配。

固定SDK缺少`pydantic`和`mcp`。这里仅用本地注册捕获器替换传输装饰器及`Field`注解元数据，按原签名生成JSON Schema（类型、必填项、默认值、描述及Literal枚举），运行时严格校验JSON类型；不实现Pydantic隐式类型转换、错误格式、输出schema或原MCP SDK的全部行为。未知参数拒绝。该差异不影响原只读工具正文及提示词正文，但不宣称框架逐字节等价。

`autozzz/onedragon_mcp_catalog.json`记录上述4个固定源码文件SHA256；源码变化时拒绝使用过期签名。可用固定SDK解释器执行`tools/mcp_readonly_sdk_smoke.py --refresh-catalog`重新生成目录，并完成验证后发布。首次读取配置可能触发上游已有的默认配置缓存/文件初始化行为，不会调用写入工具。

## 离线证据

- 16项MCP单元/真实回环HTTP检查：协议协商、认证、边界、单次授权、请求重放、同调度锁、关闭与端口释放。
- 8项上游兼容检查：28工具目录/参数、3个prompts、原指南正文、严格参数、受控运行别名、未授权操作拒绝、固定源码哈希变化拒绝与无额外运行槽。
- 8项编辑检查：本地开关/范围、修订前进、撤销旧grant、归档恢复、外部变更/切号/运行忙拒绝、冲突及跨账号恢复拒绝、原子失败不改原件、中断配置发布阻止编辑及哈希绑定恢复。
- `tools/mcp_readonly_sdk_smoke.py`：真实固定SDK、真实本机HTTP，13个只读工具及3个原prompts通过；只替换设备观察/OCR夹具，没有游戏输入，没有创建执行器或第二个RunSlot。发现并修复分离源码/配置目录导致operation列表为空的问题。
- 同一工具增加`--edits`：另外5个编辑工具经HTTP实际保存、配置/画面恢复、恢复冲突、外部变更及非法字段/矩形拒绝通过；所有资料只写入独立夹具profile。
- 4项会话检查：未知任务拒绝、瞬间完成任务的标准报告、带标签失败解等待、关闭解除原请求。
- `tools/mcp_execution_smoke.py`：实际固定SDK、真实 `OneDragonService` / `OneDragonExecution` / `RuntimeSession`，替换设备与应用节点为无输入夹具；通过实际本机HTTP启动一次原SDK任务并完成报告。
- `tools/mcp_gui_smoke.py`：真实Tk运行窗口和MCP管理窗口，同一假会话、带标签RPC，编辑开关与归档恢复、三种DPI的默认/最小布局、错误回传与关闭通过；用户配置未变化。

上述检查没有启动游戏、没有游戏输入、没有创建实际电量预算；不代替真实MCP客户端与游戏联动验收。
