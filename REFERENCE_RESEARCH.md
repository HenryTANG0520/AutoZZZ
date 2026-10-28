# AutoZZZ 与绝区零一条龙研究记录

<a id="comparison-20261028"></a>

## 2026-10-28：重启开发与项目差异研究报告

### 结论与研究范围

**AutoZZZ 当前仍是一套独立实现；绝区零一条龙已经提供更广的自动化框架。重启后最值得投入的方向，是复用一条龙的执行能力，完善面向用户的任务配置、策略管理与运行结果表达。** 本报告建议先验证上游现有接口能否满足需求，再决定哪些功能做成插件，哪些确实需要修改上游源码。

研究日期为 2026-10-28，时区 Asia/Shanghai。比较基准：

- AutoZZZ：已提交版本 [`cac67da`](https://github.com/HenryTANG0520/AutoZZZ/tree/cac67da9a6b9e7f307639ef468add74e25168e50)，包含此前每日、战斗、快捷手册风格界面及自动更新等待功能。比较时不把本机未提交的录像店祝语补充分支计入公开版本。
- 绝区零一条龙：研究时拉取的官方 `main`，固定为 [`f80b68456306d2afb97be72436dcda27f2ed6b59`](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/f80b68456306d2afb97be72436dcda27f2ed6b59)，该提交日期为 2026-10-27。下列上游源码链接均指向这个快照，避免 `main` 更新后结论失去依据。
- 方法：阅读双方源码、配置、许可证和上游官方文档。本次没有安装或启动一条龙，没有操作游戏，没有执行对照跑分或成功率测试。AutoZZZ 过去的实机记录见 [VALIDATION.md](VALIDATION.md)，不视为当前游戏版本重新验收。

“一条龙能力更广”有代码依据；“AutoZZZ 的交互一定更好”没有本次用户实验支持。交互改善部分均为待验证的设计建议。

### 一、开发关系与历史边界

项目所有者决定：2026-10-28 前的最初设计仅受 BetterGI 的 I/O 思路启发；发现绝区零一条龙后，曾认为独立重复实现不再有价值，于 2026-10-26 停止开发；实际试用一条龙后，希望改善交互，于 2026-10-28 恢复开发，将未来方向定位为基于一条龙的二次开发。这是开发动机与路线声明。

源码来源另需按事实记录：AutoZZZ 的底座来自 AutoHSR；此前研究已参考一条龙的操作流程，5 张图标模板直接来自其固定版本，并已记录来源及哈希。因此不能将此前成果表述为“从未参考一条龙”或“没有使用任何一条龙素材”。本报告下半部分保留了 2026-10-11 起的历史研究，既有署名见 [LICENSE_NOTICE.md](LICENSE_NOTICE.md)。目前也不能称 AutoZZZ 已经完成一条龙框架集成：现有执行入口、识别器、流程和配置仍是本项目自身实现。

### 二、核心差异

| 维度 | AutoZZZ 当前实现 | 一条龙固定版本 | 对后续开发的意义 |
| --- | --- | --- | --- |
| 产品范围 | 共享签到、咖啡、录像店、每日领奖、周纪、城募；独立的试验战斗 | 除每日外，还包含体力计划、恶名狩猎、空洞、式舆防卫战、巡回等应用目录 [U1] | 自动化范围已有大量重叠；扩展玩法优先复用上游 |
| 应用架构 | `Runner` 调用每日流程与独立战斗流程，专用模块组织周纪和城募 [A1] | `ZContext`、应用工厂、应用组、可发现插件、Operation 节点及流转 [U2] | 上游更适合作为多个玩法共享的执行底座 |
| 文字与页面识别 | MSS 截屏、RapidOCR、模板/颜色、代码中的归一化区域；难认按钮局部再识别 [A2] | 独立 OCR、识别缓存、页面 YAML、页面路由、模板与模型能力 [U2][U3] | 继续维护第二套页面数据和识别逻辑会产生重复适配成本 |
| 战斗决策 | JSON 加载有界按键序列，按顺序执行；监视 HUD 与结算，不识别角色技能时机 [A3] | 条件策略、优先级、状态与动作模板；识别角色、连携、支援等，闪避可结合视觉与声音 [U4] | 这是最大的能力差距，换文件格式不能消除差距 |
| 策略可编辑性 | `A/E/Q/Space` 与 `h` 长按语法简单，易写短序列；策略带版本和 SHA-256 [A3] | YAML 的场景、触发器、条件表达式与可复用动作模板，表达力更强 [U4] | 可做简易编辑入口，但不能宣称现有 JSON 与上游 YAML 等价 |
| 副本与配队 | 名称查找、有限翻页、精确队卡 SELECT/SELECTED、可选角色顺序核对；实测主要为异常演练 [A3] | 体力计划按分类、任务、编队、战斗配置组织，并有预编队识别与管理 [U5] | 用户可从简单名称输入升级为名称匹配、候选及队伍预览 |
| 账号与区服 | 国服/国际服路径切换，共用一份主配置；不保管账号密码 [A4] | 独立账号实例和应用组记录，支持国服/B服/国际服的客户端与登录编排 [U2] | 不能把切换路径等同于上游多账号管理 |
| 窗口与输入 | 前台 Win32 输入；失焦释放并等待，恢复后重新识别 [A1][A2] | 多种截图实现；另有依赖虚拟手柄的后台模式，官方仍标注测试版 [U6] | 上游多一种运行方式；后台模式并非所有环境都无条件可用 |
| 桌面交互 | Tkinter 自绘游戏快捷手册风格，顶部目标/日常/训练/记录/设置，左侧日志覆盖层 [A5] | PySide6、Fluent 组件、仪表盘、账户与资源管理、任务配置、Overlay 和运行前检查 [U7] | 不能把上游描述成“没有界面或日志”；差异应落到具体任务的操作成本 |
| 完成判定 | 核实满400且四档奖励已领取，达到终点后不再做每日动作；收尾奖励另记 [A1] | 活跃度奖励应用有确认/预览分支，再检查第4档奖励区域 [U8] | 可保留“目标已经达成”与“各步骤如何结束”的分开展示，但不能由实现差异推断谁必然误判 |
| 电量控制 | 每批最多15场/300电量，出战前预留，结算登记；未结算项阻止重开，不补电 [A3] | 体力计划有目标次数、卡数和玩法消耗等配置 [U5] | 上游体力计划和本项目预算语义不同，迁移时需逐条映射、核实 |
| 集成方式 | CLI、Python 内部类和 GUI，共同调用本项目 Runner [A1] | 插件之外已有 HTTP/MCP 后端，可列应用、读编队、启动任务、查询和停止 [U9] | 可先做执行适配层，不必第一步复制整套源码 |
| 安装与维护 | 少量顶层依赖、PyInstaller 文件夹构建；更新程序主要靠重新构建/分发 [A6] | uv、Qt、识别/音频依赖、模型/代码更新与多种启动包 [U10] | 上游安装与版本管理需要纳入交互设计；不能仅按依赖数量推断运行快慢 |
| 测试与证据 | 同仓库脱敏截图、单元回放、假后端 GUI、历史本机实测报告 [A7] | 独立测试仓和流程截图测试方法；该快照的 `test-check.yml` 明确跳过执行测试 [U11] | 不能因主仓没有 tests 就说上游没测试，也不能把 CI 工作流成功直接当成测试全部通过 |

### 三、战斗能力不是同一层级

AutoZZZ 的 `Ah, Eh, Space - Ehh, Space - E, Ahhh |` 表达“按什么、按多久、按什么顺序”。运行中能在失焦、停止、未知 HUD 和结算时释放按键，但不会根据红黄光、当前角色能量或连携状态重新决定下一招。此前国服删去维琳娜动作后伤害不足，需要人工调整为更长的爱芮序列，正说明固定序列对队伍与练度有依赖。[A3][A7]

一条龙的 `AutoBattleOperator` 基于条件操作器，策略可以根据状态触发不同优先级的动作；其角色上下文、闪避上下文和样例策略包含状态识别与打断逻辑。因此，今后若目标是通用战斗，应优先接入它的策略体系。AutoZZZ 的简写可以保留为初学者编辑方式或转换工具，但第一版只应转换静态动作子集；条件、优先级、角色状态和模板引用必须明确标注为无法无损转换的内容。[U4]

### 四、交互改进应解决什么

一条龙已经有仪表盘、运行前检查、预编队页面、设置卡片和悬浮层，不能把“增加日志”“增加开始按钮”当作新的差异。以下建议来自本项目用户历次操作反馈与两边的配置结构，尚需用真实任务验证：

| 用户要做的事 | 值得验证的设计 | 验收方式 |
| --- | --- | --- |
| 每天只领完日常 | 首页提供“每日零电量”目标，展开后才显示签到/饮品/营业/收尾细项 | 新用户能找到入口；实际计划不包含体力应用；400与领奖证据明确 |
| 指定一支队 | 输入名称后展示精确匹配、候选角色、顺序和所用战斗策略 | 同名或未找到不能静默换队；出战前可见生效队伍 |
| 修改一个策略 | 常用参数可视化，保留原始文件编辑、来源、版本和校验结果 | 用户能知道修改了什么、何时生效；不误覆盖高级 YAML |
| 知道本次会消耗什么 | 开始前显示区服、客户端、任务、队伍、现有电量上限与是否补电 | 预览与执行使用同一份冻结方案；每场开始前仍由执行侧检查 |
| 出错后继续 | 显示已完成目标、当前步骤、失败原因和可做的恢复动作 | 不因附加领奖失败抹去已确认每日完成；未结算战斗不能变成新出战 |
| 理解日志 | 默认用任务阶段与结果说明，技术细节和截图可展开 | 能回答“卡在哪、为何等待、是否扣电、接下来怎么办” |

这不是认定上游缺少所有这些能力，而是下一轮应测的任务清单。建议让同一批用户分别完成“首次配置每日”“选择队伍刷一场”“导入策略”“失败后恢复”，记录完成率、用时、误操作和求助次数，再决定具体改版。

### 五、建议的二次开发方式

**建议先验证“AutoZZZ 交互界面 + 一条龙执行服务”，确有执行语义差异时再增加上游插件或少量源码修改。** 这是工程建议，本轮尚未实现连接。

一条龙当前已有本地后端，默认地址为 `127.0.0.1:23001`。已核实的接口包括 `/health`、`/game/applications`、`/game/predefined-teams`、`/game/run/standalone`、`/game/run/one-dragon`、`/game/status` 和 `/game/stop`。状态对象包含运行状态、当前节点、重试次数、结束原因和失败节点。这些能支撑配置向导和运行反馈的原型，但并非已经证明足以满足全部配置写入、暂停恢复、日志订阅和预算需求。[U9]

| 路线 | 适合的内容 | 主要代价 | 本报告建议 |
| --- | --- | --- | --- |
| 独立界面调用本地 HTTP/MCP | 任务入口、读取账号/编队结果、启动应用、展示状态 | 需要版本协商、配置映射、部署与异常处理；现有接口不等于全部需求已覆盖 | 先验证，只接一个简单每日应用 |
| 上游应用插件 | 自定义每日目标、执行侧预算检查、特殊报告 | 依赖上游 Application/Operation 机制及版本 | 在需要精确执行约束时采用 |
| 在上游 Qt 界面增加简易模式 | 复用账户、资源、设置卡片和 Overlay，减少两套桌面框架 | 需将 Tkinter 交互逻辑改写为 Qt；自绘控件不能直接搬用 | 若长期统一分发，值得评估并向上游贡献 |
| 大范围复制并长期分叉 | 大量执行逻辑都要改变 | 持续合并上游页面、角色、模型和框架更新 | 当前需求不足以证明有必要 |

关键迁移约束：

1. **预算必须由执行侧落实。** 在界面显示“最多20电量”或超时调用 stop，不能保证上游已开始的体力计划不超支。需要核对体力计划参数、补电开关和每次出战前后的登记；必要时做应用插件。不能把 AutoZZZ 的本地账本直接当成对另一个执行器的约束。
2. **JSON 策略与 YAML 条件策略分别处理。** 自动识别格式、显示原始来源；不兼容内容应报错或交给高级编辑，不静默降级。
3. **同一时刻只有一个执行器发送游戏输入。** 旧 Runner 与上游后端的互斥不能只靠各自内部锁；迁移期需明确执行来源与运行句柄。
4. **先保留原程序和配置。** 数据迁移另存新配置并能回退，用户已有账户、队名和策略不应被首次连接覆盖。
5. **固定并显示上游版本。** 适配器先检测功能与字段，不能假设未来上游 `main` 始终兼容。测试通过后再更新所支持的快照。

### 六、应保留、复用和逐步替换的内容

| 处理方式 | 内容 |
| --- | --- |
| 保留为产品要求与回归样例 | 每日完成的证据、区服和队伍误选防护、运行前计划预览、预算与未结算语义、失败原因和恢复提示、已有脱敏截图 |
| 优先复用上游 | 页面与传送数据、角色/技能状态、自动战斗、复杂玩法、账号实例、模型资源和应用框架 |
| 通过测试后逐步替换 | AutoZZZ 重复的截图/输入层、日常导航、怪物卡配置、固定序列执行器；不一次性删除可回退实现 |
| 需要重新设计而非直接复制 | Tkinter 自绘界面与 Qt 组件的衔接、JSON 到 YAML 的编辑体验、上游结果到 AutoZZZ 目标完成状态的映射 |

### 七、复用与署名

AutoZZZ 现有许可为 `GPL-3.0-only`；研究快照的一条龙仓库包含 GPLv3 许可证文本。复用时按实际引入的文件记录上游版本、原作者、许可和修改，分发修改版时保留相应许可与对应源码。仓库名称、标志、模型、字体和游戏画面素材需分别核对权利说明，不能从代码许可证推定所有素材均归项目所有。既有 5 张模板的署名继续保留，不因本次重启而删除。[LICENSE_NOTICE.md](LICENSE_NOTICE.md)、[上游 LICENSE](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/LICENSE)

README 的“基于一条龙二次开发”描述今后的定位；只有实际完成引入、适配和验证之后，才能逐项说明哪些执行能力已经来自上游。

### 八、下一阶段最小验证范围

1. 建立只读连接：显示后端是否就绪、上游版本、当前实例、应用和编队；连接失败不修改配置。
2. 做一个完整的“零电量每日”方案预览与单应用执行，核对计划、实际任务、停止及报告是否一致。
3. 将“每日目标已完成”和“任务组结束”分开映射，再接入周纪/城募收尾与异常恢复。
4. 加入编队与策略预览，最后迁移战斗。先验证执行侧预算和禁止补电，再做消耗电量的实机试验。

本次交付为 README 路线更新及源码对比报告；没有改动自动化执行代码，没有启动游戏，也没有把两项目集成状态标成已完成。

### 可复核的源码与文档

AutoZZZ 当前源码：

- **[A1] 执行与完成判定**：[app.py](autozzz/app.py)、[workflows.py](autozzz/workflows.py)、[models.py](autozzz/models.py)。
- **[A2] 识别与输入**：[vision.py](autozzz/vision.py)、[windows.py](autozzz/windows.py)、[policy.py](autozzz/policy.py)。
- **[A3] 战斗与策略**：[battle.py](autozzz/battle.py)、[battle_engine.py](autozzz/battle_engine.py)、[battle_strategy.py](autozzz/battle_strategy.py)、[battle_budget.py](autozzz/battle_budget.py)、[BATTLE.md](BATTLE.md)。
- **[A4] 区服与配置**：[config.py](autozzz/config.py)。
- **[A5] 桌面与反馈**：[desktop.py](autozzz/desktop.py)、[battle_ui.py](autozzz/battle_ui.py)、[ux.py](autozzz/ux.py)、[overlay.py](autozzz/overlay.py)。
- **[A6] 环境与构建**：[requirements.txt](requirements.txt)、[build.ps1](tools/build.ps1)。
- **[A7] 验收与测试**：[VALIDATION.md](VALIDATION.md)、[tests](tests)、[battle_gui_smoke.py](tools/battle_gui_smoke.py)。

一条龙固定快照：

- **[U1] 玩法模块**：[application 目录](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application)。
- **[U2] 框架、应用组与账号**：[架构说明](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/one_dragon/one_dragon_architecture.md)、[插件开发](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/guides/application_plugin_guide.md)。
- **[U3] 页面与区域**：[screen_info 目录](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/f80b68456306d2afb97be72436dcda27f2ed6b59/assets/game_data/screen_info)。
- **[U4] 条件战斗**：[条件操作机制](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/one_dragon/modules/conditional_operation.md)、[AutoBattleOperator](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/auto_battle/auto_battle_operator.py)、[闪避上下文](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/auto_battle/auto_battle_dodge_context.py)。
- **[U5] 副本计划**：[ChargePlanConfig](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application/charge_plan/charge_plan_config.py)、[预编队页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/gui/view/one_dragon/predefined_team_interface.py)。
- **[U6] 窗口与后台模式**：[PcControllerBase](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/one_dragon/base/controller/pc_controller_base.py)、[官方游戏设置说明](https://one-dragon.com/zzz/zh/setting/setting_game.html)。
- **[U7] 桌面与反馈**：[GUI 入口](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/gui/app.py)、[运行前检查](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/gui/dialog/pre_flight_check_dialog.py)、[DebugTraceBus](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/one_dragon/modules/debug_trace_bus.md)。
- **[U8] 每日奖励**：[EngagementRewardApp](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application/engagement_reward/engagement_reward_app.py)。
- **[U9] 现有集成接口**：[HTTP 应用接口](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/backend/http/service_routes.py)、[游戏接口](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/backend/http/routes.py)、[服务入口](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/backend/entry/server.py)、[状态结构](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/backend/schemas.py)。
- **[U10] 环境与安装**：[pyproject.toml](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/pyproject.toml)、[官方快速开始](https://one-dragon.com/zzz/zh/quickstart.html)、[必要设置](https://one-dragon.com/zzz/zh/config.html)。
- **[U11] 测试边界**：[测试方法](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/testing/README.md)、[该快照 CI 配置](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/.github/workflows/test-check.yml)。

[A1]: autozzz/app.py
[A2]: autozzz/vision.py
[A3]: BATTLE.md
[A4]: autozzz/config.py
[A5]: autozzz/desktop.py
[A6]: requirements.txt
[A7]: VALIDATION.md
[U1]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application
[U2]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/one_dragon/one_dragon_architecture.md
[U3]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/f80b68456306d2afb97be72436dcda27f2ed6b59/assets/game_data/screen_info
[U4]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/one_dragon/modules/conditional_operation.md
[U5]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application/charge_plan/charge_plan_config.py
[U6]: https://one-dragon.com/zzz/zh/setting/setting_game.html
[U7]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/gui/app.py
[U8]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/application/engagement_reward/engagement_reward_app.py
[U9]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/src/zzz_od/backend/http/service_routes.py
[U10]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/pyproject.toml
[U11]: https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/f80b68456306d2afb97be72436dcda27f2ed6b59/docs/develop/testing/README.md

---

## 历史记录：绝区零非战斗每日参考研究

本文历史实机证据链接指向原校准机器的 `runs/`；这些截图 / 日志不随 Git 仓库分发。可共享的脱敏回放素材位于 `tests/fixtures/`。

研究日期：2026-10-11（Asia/Shanghai）。参考 OneDragon 官方仓库 `main` 的源码与页面配置。本文件提供上游可核实的操作路径；尚未实机校准的快捷键、镜头与坐标不应直接作为执行成功依据。

范围：登录、录像店营业、喝咖啡、共享每日签到、快捷手册日常领奖。`scratch` 字段对应共享每日签到，可由刮刮乐、占卜或饼铺每日零食盲盒任一完成；不能将该字段理解为只能刮卡。所有战斗、消耗电量、体力计划均不在本阶段范围内。喝咖啡是恢复电量，上游咖啡程序可接体力计划，AutoZZZ 不应接入这一分支。

## 地图与快捷手册：先使用可见入口

上游为地图、快捷手册配置的是视觉点击入口和手柄动作，没有直接用键盘 `M` / `F2`。2026-10-11 国服实机已确认 HUD 标注 `M` 地图与 `F2` 手册，并验证按键有效；Unity 轮询要求按住约 0.08 秒，瞬时按下再释放可能无效。其他已验证动作是 `F` 交互与 `W` 前进。所有按键之后仍须识别后置页面，不能凭按键次数推进。

以下坐标为游戏客户区标准 **1920×1080** 的 `(x1,y1,x2,y2)`。须按截图尺寸缩放，再加客户区桌面偏移；不能包含标题栏。`normal_world.yml` 设 `pc_alt: true`，表示键鼠点击这类 HUD 入口需要处理鼠标显形/Alt 状态，最终仍以实机行为为准。

| 入口/识别项 | ROI | 上游判据 |
| --- | --- | --- |
| 大世界地图入口 | `(1696,319,1858,415)` | 模板 `normal_world/map`，阈值 0.5 |
| 大世界快捷手册入口 | `(1440,28,1689,118)` | 模板 `normal_world/compendium`，阈值 0.7 |
| 地图主区域名称 | `(0,90,1920,930)` | OCR 查找目标主区域；排除底部点名 |
| 地图底部传送点名称 | `(70,1000,1848,1058)` | OCR 查找该区域目标点名 |
| 地图传送确认 | `(1024,600,1232,708)` | OCR `确认` |
| 地图左上返回 | `(82,13,150,90)` | 模板 `menu/back` |
| 手册顶部标签 | `(708,140,1636,220)` | OCR `日常`，当前标签额外黄色范围过滤 |

具体源接口是 `Transport(ctx, area_name, tp_name, wait_at_last=True)`，其中 `open_map()` 调用 `round_by_find_and_click_area(last_screenshot, '大世界', '地图')`；`do_transport()` 调用 `MapTransport(ctx, area_name, tp_name)`。地图已打开时直接走 MapTransport。

MapTransport 的顺序是 **选择主区域 → 选择底部传送点 → 点击确认 → 等待大世界**。区名未显示时，在屏幕中心左右拖动约 500 px；点名未显示时，在底部条左右拖动约 750–800 px。上游在主区域列表连续三轮不变化后仅反向一次；选点失败最多回到主区域重新选择两次，避免无限拖动。AutoZZZ 可以先实现较简单的有限 OCR 查找，但必须区分主区域文字与底部点名。

目标地图路径：

- 刮刮卡：`六分街 → 报刊亭`。
- 喝咖啡：`六分街 → 咖啡店`。
- 录像店营业：`录像店 → 柜台`，不是 `六分街 → 音像店`。

大世界确认不应只看界面有文字。上游地图页面确认组合为多个区名、至少一个点名、左上返回图标；传送后可能触发代理人好感度对话，应先识别，再采取确定的返回或退出动作。不能把通用的 `确认` 按钮视为任何状态下均可点击。

来源：[Transport](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/operation/transport.py)、[MapTransport](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/operation/map_transport.py)、[大世界页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/normal_world.yml)、[地图页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/map.yml)、[地图名称表](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/map_area.yml)、[键位配置](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/config/game_config.py)。

**地图实机差异：** 国服 `M` 默认打开布亚斯特城区的 3D 城市地图，显示 `区域信息`、`区域切换`，不是上述旧版横向传送页面。右上 `快捷导航` 的归一化文字框约 `(0.7953,0.0324,0.8516,0.0630)`，尚未验证其后置页面。当前优先用 `F2 → 日常 → 对应任务卡的唯一前往`，避免依赖未校准的 3D 地图转场。

## 刮刮卡

上游动作：传送 `六分街/报刊亭`；如果没有已经处于报刊亭界面的证据，按 `W` 1 秒，停 1 秒，按 `F` 0.2 秒，再等待 3 秒。随后识别并点击 `刮刮卡`。

| 内容 | ROI |
| --- | --- |
| 刮刮卡入口 | `(94,956,172,1050)` |
| 每日可刮取一次 | `(704,656,1132,692)` |
| 对话选项 | `(1438,560,1862,776)` |
| 嗷呜标题 | `(890,792,1042,828)` |
| 嗷呜被你叫醒了 | `(780,518,1138,586)` |
| 同类玩法确认 | `(882,602,1082,654)` |

夜间睡觉时，对话候选是 `叫醒他`、`叫醒嗷呜`、`只是来看刮刮卡和报纸`；叫醒提示出现后需继续。没有选项时可在确认处于嗷呜对话后点击标题推进。

刮取页确认文字是 `每日可刮取一次`。这个文字**不是已完成证据**。刮取动作分别为：

1. `(780,522) → (1134,556)`，1.5 秒。
2. `(782,556) → (1138,588)`，1.5 秒。
3. `(788,590) → (1134,632)`，1.5 秒。

每次拖动后等待 1 秒。AutoZZZ 应额外识别结果或领奖页，再记录完成。上游完成拖动后直接返回大世界，其成功状态不完全等同于奖励已核实。

当前上游注明：刮刮卡与 **卦象集录、吼吼饼铺** 共享每日一次，三选一。出现同类玩法确认应读弹窗正文，确认今日同类已完成后再作为幂等跳过；不能把中央任意 `确认` 判为刮取成功。

来源：[刮卡程序](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/scratch_card/scratch_card_app.py)、[报刊亭页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/news_stand.yml)。

## 国服实机补充：饼铺共享每日签到

2026-10-11 国服实机客户区为 3840×2160，所有以下位置仍转换为 1920×1080 标准或直接使用归一化坐标。Windows 显示缩放与工具预览大小不应再次缩放已经归一化的客户区坐标。

真实日常手册中，共享签到卡标题为 `进行一次刮刮乐、占卜或抽取饼铺盲盒`。按该卡唯一 `前往` 后，实机自动传送到 **布亚斯特城区的吼吼饼铺**，并不总是六分街报刊亭。NPC 为 `吼吼先生`，OCR 位置归一化约 `(0.4818,0.0620,0.5292,0.0870)`。卡前往成功后正对柜台，官方饼铺程序明确落点足够近、无需前移，等待约 1 秒后按 `F` 0.2 秒交互即可。

确认进入饼铺集卡机页后，OCR 实际读出 `集卡兑换` 与 `每日可领取一次零食盲盒」`。页面下方的 **集卡兑换不是每日领取按钮**。本阶段应仅领取上方每日零食盲盒。

| 状态 | 可核实文字与动作 |
| --- | --- |
| 今日领取前的集卡机 | 必须同时识别 `每日可领取一次` 与 `零食盲盒`；点击上排左盲盒，标准 ROI `(1100,430,1260,570)`，中心标准 `(1180,500)`，归一化 `(0.6146,0.4630)` |
| 盲盒居中 | 官方程序判据 `查看今天`，点击屏幕中心揭示；应确认处于刚选出的每日零食盲盒流程 |
| 卡片揭示 | 实机根代理已校准 `Roarin` 卡片页的 `确定` 与 `分享`；仅点击 `确定`，不点击分享 |
| 收益弹窗 | 实机文字为 **`获得`**，非 `获得奖励`；同时有数量、物品名 `丁尼` 与 `确认`。本次截图显示 `5888` 丁尼。确认中心归一化约 `(0.5099,0.6769)`，标准约 `(979,731)` |
| 当日已领取 | 官方程序优先识别 `同类型奖励`；可能是右下 `今日已领取同类型奖励`，也可能是弹窗 `今日已经完成过一次相同类型的奖励领取` |
| 完成核验 | 优先用饼铺明确 `今日已领取同类型奖励`，或可见原任务卡的**同列** `1/1` / 明确完成；收益弹窗须在已执行每日签到动作的上下文中才可作为辅助证据 |

领取状态机必须先检查今日同类型已领取，再处理 `确定/确认`。否则已完成弹窗的确认会被误当领取确认，关闭后又点击盲盒，导致循环。仅出现 `每日可领取一次` 是可领取页证据，不能作为已完成证据。

国服过程截图：

- [日常前往落地到吼吼先生](runs/live-session/20261011_200904_432022/200926_082201_after.png)。
- [每日零食盲盒集卡机](runs/live-session/20261011_201331_253618/201354_895889_after.png)。
- [获得 5888 丁尼弹窗](runs/live-session/20261011_201539_146809/201545_586318_after.png)。

这些过程截图证明日常前往实际目的地与领取界面，不单独证明日常已满或奖励全部领取。本阶段未操作 `集卡兑换`，也未使用调频抽卡入口。

**实机核验限制：** `scratch-7` 领取后回到日常，共享签到卡已经从当前三列视口中消失。剩余 `品尝1次咖啡` 保持第 1 列，`开启今日录像店经营` 移到第 2 列，第 3 列补入运算中枢获取邦布资源任务，各自仍为 `0/1`。完成任务可能自动离开当前视口，因此“原卡未找到”不能单独判断失败，也不能单独认定成功。若使用任务卡变化佐证，须保存动作前明确目标卡、动作后同一日常页、明确收益/当日已领证据；待进一步校准后也可加入绿色活跃档位提升 100 的前后图像证据。不能从 `今日最大活跃度400` 推断当前活跃度。

对应截图：[领取后日常卡排列变化](runs/live-session/20261011_201723_733105/201738_997841_after.png)。

官方来源：[吼吼饼铺程序](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/hou_hou_bakery/hou_hou_bakery_app.py)、[吼吼饼铺页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/hou_hou_bakery.yml)。二者同属上述 GPL-3.0 仓库。饼铺签到与刮卡共享每日一次，不应重复尝试领取第二次同类奖励。

## 咖啡

上游默认传送 `六分街/咖啡店`，识别小地图视角并转向正西（180°，误差允许 2°），按 `W` 1 秒，停 1 秒，按 `F` 0.2 秒。镜头转角按小地图实时识别和灵敏度补偿，不能照搬固定鼠标位移。其他解锁区域可以直接传送到咖啡入口，不过本阶段以六分街为准。

| 内容 | ROI |
| --- | --- |
| 咖啡列表 | `(132,682,1122,1054)` |
| 点单 | `(1702,880,1914,1020)` |
| 点单后跳过 | `(1684,64,1850,200)` |
| 电量确认 | `(875,640,1200,754)` |
| 不可贪杯确认 | `(882,606,1084,690)` |
| 额外点单弹窗确认 | `(1040,600,1228,688)` |

默认目标咖啡为 `汀曼特调`。上游还有 `沙罗特调` 等额外、不计上限咖啡；MVP 可先只做每日正常一杯。上游 OCR 咖啡名称后，点击名称中心上移 50 px，以选中对应卡片。只有 `点单` 文字已经出现，面板仍可能在滑入动画，需额外等 1 秒再选咖啡。

点单后识别 `跳过`。上游指出直接点击有时无效，需要先从跳过 ROI 左上拖到中心（0.2 秒）让鼠标显形，再点击。然后等 `电量确认`。已经喝过的分支是 `不可贪杯` 弹窗，需要同时读取正文与按钮，而非只认中央确认。完成应由电量恢复结果、已喝过正文或手册喝咖啡任务完成状态佐证。

**不要点击电量不足或其他弹窗的 `前往`，也不要接上游 `结束后运行体力计划` 分支。**

来源：[咖啡程序](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_app.py)、[咖啡页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/coffee_shop.yml)、[咖啡配置](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_config.py)、[咖啡数据](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/coffee_data.yml)、[朝向识别](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/operation/turning/turn_to_angle.py)。

### 国服实机补充：片刻闲的对话免费点单

2026-10-11 实机从日常 `品尝1次咖啡` 卡 `前往` 后，出现布亚斯特城区茶饮店的 `汀曼大师` 对话，不出现六分街的咖啡列表或 `点单` 面板。画面右侧实际选项是 `来杯「汀曼特调」！`、`来杯「鲜萃红茶」！`、`不喝了`；OCR 可能把引号读成感叹号。这与上游 `CoffeeTransportPoint.POINT_3 = 布亚斯特城区/片刻闲` 分支一致，不能仅因传统点单面板未出现就认定入口失败。

上游 `CoffeeApp` 类说明当天可免费点一杯。`dialog_choose_coffee()` 在确认 `汀曼大师` 标题后，从右侧选项 OCR 查找候选咖啡名；常规候选包含 `汀曼特调`。在 `POINT_3` 成功选中后，流程直接进入 `点单后跳过`，随后确认电量结果，不要求中途出现传统 `点单` 或 `免费` 标签。`鲜萃红茶` 不在当前上游咖啡数据名单，不能根据第一项免费把第二项也纳入自动领取。

| 内容 | 标准 1920×1080 ROI | 归一化 ROI |
| --- | --- | --- |
| 右侧对话选项 | `(1438,536,1914,830)` | `(0.7490,0.4963,0.9969,0.7685)` |
| 汀曼大师标题 | `(880,800,1040,840)` | `(0.4583,0.7407,0.5417,0.7778)` |

仅在已知咖啡日常前往上下文、明确 NPC 标题和唯一 `汀曼特调` 选项同时成立时，才可把它作为有上游来源的每日免费白名单入口。上游源码本身没有读取价格、丁尼或支付弹窗，免费依据来自程序说明和所实现的固定每日流程；它不能替代实机费用检查。AutoZZZ 仍须先拦截 `确认支付`、`消耗菲林`、`购买电量` 等状态，再执行白名单选项。上游仅用对话标题消失推定已喝过，这一判据过弱，AutoZZZ 应另外取得电量恢复结果、当天已喝正文或日常活跃度提升证据。

现场截图：[汀曼大师三项对话](runs/live-session/20261011_202053_332187/202105_465275_after.png)。对应源行为：[免费每日说明](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_app.py#L36)、[对话点单分支](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_app.py#L129-L152)、[点单跳过与电量确认](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_app.py#L245-L282)、[地点配置](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/coffee/coffee_config.py)、[咖啡名单](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/coffee_data.yml)。

后续实机 `coffee-4` 执行该第一选项后，已经显示奖励页 `获得`、数量 `80`、物品 `电量`、`确认`。OCR 将 `获得` 与 `电量` 分成两个 token，不能只匹配连续字符串 `获得电量`。应在刚执行咖啡点单的上下文中同时确认收益标题、明确物品名、数字数量和唯一确认按钮，保存证据后确认，再核实日常卡或活跃度变化。这个电量奖励是恢复电量，不是消耗电量。截图：[获得 80 电量](runs/live-session/20261011_202423_322064/202440_083978_after.png)。

`coffee-5` 随后确认了 80 电量收益，返回大世界。下一次手册截图已显示前三档绿色、当前 300 活跃，咖啡卡从列表消失，录像店任务进入第 1 列；此时上方 `320/240` 是电量库存，不能混入活跃度读数。截图：[咖啡后当前 300 活跃](runs/live-session/20261011_202923_436226/202935_964159_video_daily_jump.png)。

## 录像店营业

传送 `录像店/柜台`，上游转向正东（0°），按 `W` 1 秒，停 1 秒，按 `F` 0.2 秒。柜台交互后直接进经营界面。

新版上游也提供 `澄辉坪/录像店营业点` 与 `布亚斯特城区/录像店营业点`。澄辉坪落点可直接交互；布亚斯特落点与旧柜台同样按小地图转向正东后前移再交互。新城区交互先出现 NPC 对话，应在右侧选项标准 ROI `(1438,536,1914,830)` 查找并点击 `查看经营状况`。布亚斯特经常需要先推进一次对话，选项才显示；上游点击标题标准 ROI `(880,800,1040,840)` 推进，但 AutoZZZ 应额外确认是刚进入的录像店经营 NPC 对话，不能在未知对话上点击固定位置。旧柜台则无需该对话分支。

| 内容 | ROI |
| --- | --- |
| 昨日账本 | `(528,224,679,273)` |
| 昨日账本关闭图标范围 | `(1250,100,1500,350)`，模板 `menu/btn_close` |
| 经营状况标签 | `(1272,14,1552,84)` |
| 宣传员入口 | `(800,620,1000,892)` |
| 录像带入口 | `(1106,618,1270,896)` |
| 选择宣传员标题 | `(104,1006,420,1050)` |
| 宣传员第 1 / 第 2 卡片 | `(532,144,720,422)` / `(744,144,924,420)` |
| 选择宣传员确认 | `(1590,1004,1816,1054)` |
| 推荐上架 | `(1320,1002,1516,1052)` |
| 上架筛选 | `(1588,28,1816,78)` |
| 上架 / 下架 / 换下 | `(1628,1004,1816,1052)` |
| 开始营业 / 正在营业 | `(1536,950,1792,998)` |
| 开始营业确认 | `(1032,600,1232,656)` |
| 营业后确认 | `(800,600,1050,656)` |
| 左上返回 | `(54,12,162,84)`，模板 `menu/back` |

每日首次可能先显示 `昨日账本`，点关闭后重新截图确认已消失；不要立即当作经营界面。进入经营界面后，点 `经营状况`，避免默认被跳到顾客咨询页。

如果出现 `正在营业`，直接退出并标记今日已经营业。如果是 `开始营业`，先进入宣传员：已经选择过时右下会显示 `换下`，保持即可；否则可选前两个可用卡片之一并确认。随后进入录像带，优先用 `推荐上架`；上游没有该按钮时会根据顶部三个题材 OCR 筛选并上架。`上架` 与 `下架` 共用按钮位置，必须严格识别完整文字，避免把已上架录像带下架。最后返回经营状况，点击 `开始营业`、弹窗 `确认`、营业后 `确认`，再验证 `正在营业`。

宣传员与录像带设置对达成日常和收益的影响不应凭空推定；如旧账号已配置好，优先复用。不要处理咨询、推荐录像带给顾客等额外分支，除非后续独立验证必要。

来源：[录像店程序](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/random_play/random_play_app.py)、[录像店页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/random_play.yml)、[传送配置](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/random_play/random_play_config.py)。

### 宣传员选择策略与本次空槽实机

上游 `choose_promoter()` 没有计算最佳收益宣传员。它先检查 `换下`：出现则意味着已经选择，直接返回复用。若配置任一候选为 `随机`，按日期最后一位奇偶在前两个候选卡片轮换；若配置了名字，先在列表 OCR 指定名字的白色文字，失败再匹配头像，按两个人选依次尝试，有限滚动重试后兜底第 1 槽。

初版讨论曾建议复用已有宣传员。2026-10-11 用户新增了指定名字的配置要求，因此当前策略是：已营业时不改动；尚未营业时按用户指定名字选择，留空或账号中找不到指定名字则选择第一位。不能把上游日期轮换称为最佳策略，也不按角色稀有度推定经营收益。`换下` 表示当前卡已经选中，不能将它当成确认按钮无条件点击。

| 内容 | 标准 ROI / 中心 | 归一化中心 |
| --- | --- | --- |
| 经营面板宣传员槽入口 | `(800,620,1000,892)` / `(900,756)` | `(0.4688,0.7000)` |
| 宣传员候选第 1 卡 | `(532,144,720,422)` / `(626,283)` | `(0.3260,0.2620)` |
| 宣传员候选第 2 卡 | `(744,144,924,420)` / `(834,282)` | `(0.4344,0.2611)` |
| 宣传员确认 | `(1590,1004,1816,1054)` / `(1703,1029)` | `(0.8870,0.9528)` |
| 名字或头像查找列表 | `(530,140,1770,972)` | 必须局限选择页列表 |

本次 `video-4` / `video-5` 经营面板为 1 个空宣传员槽与 3 个空录像带槽，显示 `选择宣传员`、`EMPTY`、`开始营业`。根代理用本次实机自采的 `video_slot_plus` 模板在录像带槽内定位加号，进入第 1 录像带槽；不能全屏点击加号，因为宣传员加号外观相同。根代理随后使用 `推荐上架`，实图已显示 3 张录像带封面，剧情祝语弹窗 `诚信经营，丁尼为本！` 与 `确认`。宣传员槽在背景仍空，故本次开店路径可能无需选宣传员即可达成日常，先验证营业结果后再决定是否添加设置。

祝语确认是上游 `营业后确认` 节点，标准 ROI `(800,600,1050,656)`；本次按钮中心归一化约 `(0.510,0.581)`，标准约 `(979,627)`。仅在已经请求开始营业的上下文中，凭完整固定祝语与唯一确认按钮继续，不能要求祝语含 `营业` 两字，也不能把该祝语直接当作已验证正在营业。确认后重新核实 `正在营业` 或日常活跃到 400。

现场截图：[空宣传员与录像带槽](runs/live-session/20261011_203336_807094/203338_827058_video_empty_slots.png)、[营业后固定祝语](runs/live-session/20261011_203336_807094/203349_219619_error.png)。上游源码：[宣传员复用与选择](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/random_play/random_play_app.py#L169-L216)、[推荐上架](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/random_play/random_play_app.py#L304-L312)、[营业后确认](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/random_play/random_play_app.py#L418-L422)。

随后 `video-6` 点击固定祝语确认后，实机直接退出经营面板，回到 `阿玖 / 影像档案架` 旁的大世界。后置不能强制只等经营面板，否则会在成功退出后超时；应接受已识别大世界，再打开日常验证活跃度或对应卡变化。截图：[营业确认后返回大世界](runs/live-session/20261011_203521_731990/203611_044683_error.png)。

## 每日活跃度领奖与最终完成判据

上游通过页面跳转 `round_by_goto_screen(screen_name='快捷手册-日常')` 打开日常；实现时先用大世界手册入口，再点 `日常` 标签最容易验证。不要从 `训练` / `作战` 页点击副本 `前往`。

| 内容 | ROI / 判据 |
| --- | --- |
| 日常标签 | `(708,140,1636,220)`，OCR `日常`；黄色过滤 `(200,160,0)`–`(255,210,60)` |
| 今日最大活跃度 | `(417,250,646,320)`，OCR `今日最大活跃度` |
| 活跃度奖励确认 | `(882,704,1086,754)`，OCR `确认` |
| 奖励预览 | `(429,195,655,303)`，OCR `奖励预览` |
| 最后一档已完成 | `(1532,250,1602,350)`，模板 `compendium/completed`，阈值 0.7 |
| 手册退出 | `(1721,229,1799,307)` |

上游点击 `今日最大活跃度`，领取后点击 `确认`。若出现 `奖励预览`，含义为“已领取或活跃度未满”，不能单独宣告每日成功。上游最终重新匹配最后一档 `completed` 模板，以判断活跃度已满。AutoZZZ 当前采用更完整的最终判据：**四个分离奖励档位均匹配已领取勾选模板或各档明确已领取文字**，此时可证明 400 活跃且全部奖励已领，不强制要求页面没有显示的 `400/400` 数值。单个末档模板不代表四档全部领取。

实际手册奖励火苗中心归一化 x 约为 `0.3905、0.5330、0.6751、0.8184`，y 约 `0.2765`。未领取但已达成的火苗显示绿色；领取后绿色勾选圆标与上游 `completed` 模板一致。可在火苗附近小范围统计 HSV 绿色像素来读当前 100/200/300/400，但只能接受从第一档连续亮起的状态。`今日最大活跃度400` 表示上限，不是当前值；绿色火苗也不能被当作已领奖。最终模板匹配应按四个不同 x 档位单独检查，不能重复使用同一图标的全局匹配结果。

登录、营业、咖啡、刮卡这条路线无需消耗体力；但当日是否已经满活跃度应由当前版本实机手册最终核实，不能仅依据固定步骤全部执行过即宣告完成。

来源：[活跃度领奖程序](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/src/zzz_od/application/engagement_reward/engagement_reward_app.py)、[手册页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/compendium.yml)、[手册日常页面](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/assets/game_data/screen_info/compendium_errands.yml)。

### 本次国服完整结果证据

2026-10-11 国服分阶段实机校准执行已经完成饼铺共享签到、汀曼特调每日饮品、推荐上架并开启录像店、日常奖励领取。`daily-1` 最终报告 `engagement: 400`、`daily_verified: true`、`stamina_spent: 0`；这里的电量支出字段是程序记录，同时本次执行步骤均为上述非战斗路径。

领奖弹窗实际显示 `获得`：600 绳网信用、60 菲林、3000 丁尼、3 正式调查员记录、3 变频音擎电源、3 邦布算法模块；仅点击该奖励确认。随后手册最终截图中 **100、200、300、400 四个位置都变成绿色勾选圆标**，任务卡下显示 `活跃度已满`。录像店卡已从当前视口消失，说明本次营业将活跃从 300 增至 400。其他尚未做的电量消耗、资源获取、邀约任务仍显示未完成，不影响已满活跃与每日奖励领取。

- [当日日常奖励收益](runs/live-session/20261011_203727_863101/203740_212755_daily_reward_received.png)。
- [四档已领取最终截图](runs/live-session/20261011_203727_863101/203743_528809_daily_verified.png)。
- [最终报告](runs/live-session/20261011_203727_863101/report.json)。

以上证明本次国服分阶段路径达成目标。国际服后来完成了从当天 100 活跃起的一次连续运行，见下节；国服本次则仍属于分阶段校准记录。

### 国际服一次连续运行验收

2026-10-11 国际服报告 [report.json](runs/20261011_205413_955527/report.json) 指定客户端 `E:\HoYoPlay\games\ZenlessZoneZero Game\ZenlessZoneZero.exe`，`server: global`，四项任务全部 `ok`、最终 `engagement: 400`、`daily_verified: true`。本次从已登录大世界开始，20:54:13.956 到 20:56:22.847，共约 **2 分 09 秒**。[连续执行日志](runs/20261011_205413_955527/run.log) 按签到、免费饮品、录像店、领奖依次完成，没有中间人工调整、暂停或失败记录。

| 阶段 | 独立核对的截图事实 |
| --- | --- |
| 执行前 | [首次日常](runs/20261011_205413_955527/205426_409065_scratch_daily_jump.png) 仅 100 档为绿色火苗，其余档灰色；咖啡、共享签到、录像店 3 卡均为 `0/1`。电量 `240/240`。 |
| 共享签到 | [饼铺收益](runs/20261011_205413_955527/205451_450693_bakery_reward.png) 为 2888 丁尼；随后 [签到核验](runs/20261011_205413_955527/205506_200424_scratch_verified.png) 的 100、200 档绿色，签到卡移出，另两项仍 `0/1`。 |
| 免费饮品 | [对话选项](runs/20261011_205413_955527/205519_863981_coffee_daily_dialogue.png) 为同一已校准汀曼特调白名单；[收益](runs/20261011_205413_955527/205526_459330_coffee_reward.png) 明确获得 80 电量。[下次日常](runs/20261011_205413_955527/205539_391284_video_daily_jump.png) 前三档绿色，咖啡卡移出，电量 `320/240`。 |
| 录像店 | 日志包含 `阿玖 → 查看经营状况 → 推荐上架 → 开始营业 → 确认`；[营业结果](runs/20261011_205413_955527/205607_041011_video_opened.png) 显示固定祝语 `诚信经营，丁尼为本！` 与唯一确认，背景 3 录像带已上架。 |
| 领奖 | [奖励收益](runs/20261011_205413_955527/205619_940342_daily_reward_received.png) 显示 600 绳网信用、60 菲林、3000 丁尼与 3 种材料各 3；确认后 [最终截图](runs/20261011_205413_955527/205622_700506_daily_verified.png) 的 100、200、300、400 四个独立位置均为绿色勾选，任务卡底部显示 `活跃度已满`。 |

零电量消耗不仅依赖报告的 `stamina_spent: 0`（该字段为程序记录）。本次实图电量从 `240/240` 增至 `320/240`，恰为免费饮品 +80，最终消耗电量任务仍是 `0/60`；日志也没有战斗、电池补充或电量购买步骤。国际服 UI 这次使用简体中文，客户区仍为 3840×2160，区域已解锁，成功路径为新城区饼铺、片刻闲、阿玖经营点。

**验收范围仍有的缺口：** 本目录起点已经登录大世界；后续最终 EXE 的关闭客户端后启动、重复进入按钮及已完成日复验已经通过，见下节。保存账号登录窗未独立实测；报告 `reset_day: unverified-global-reset`，国际服跨每日重置保护尚未实机验收。不同语言、其他区域解锁状态、旧报刊亭刮卡、旧咖啡面板与宣传员可选分支不能从这次新城区路径推定已经实测。以上缺口不改变本次国际服未完成日常一次连续完成并领取四档奖励的结论。

### 登录入口与打包 EXE 记录应分别解释

更早的国际服 [20:46 运行日志](runs/20261011_204633_814060/run.log) 确实在 20:46:37、20:47:25 两次点击 `点击进入游戏`，随后曾进入大世界。但 [该次报告](runs/20261011_204633_814060/report.json) 为 **failed**；[超时截图](runs/20261011_204633_814060/204830_617191_timeout.png) 是丽都城募页面，后续快捷手册等待失败。这个记录可证明重复进入按钮曾被实机触发，不能称为完整启动并完成每日成功。上述日志未命中 `cached_account_login` 或 `点击：进入游戏`，保存账号登录分支未独立实测。

前版打包 EXE 的国际服 [21:01 报告](dist/AutoZZZ/runs/20261011_210150_382200/report.json) 为 **success**，四项 `ok`、400 活跃、已领取。其 [日志](dist/AutoZZZ/runs/20261011_210150_382200/run.log) 显示签到、咖啡、录像店均因当天已满且已领而跳过；[最终四档截图](dist/AutoZZZ/runs/20261011_210150_382200/210221_749825_daily_verified.png) 也已独立核对。因此该记录证明打包资源、OCR、已完成日重复运行和最终完成识别，不等于 EXE 再次从 100 活跃执行未完成日常。关闭客户端后的冷启动后来另行取得以下独立成功记录。

### 最终 EXE 国际服冷启动复验

正常退出国际服后，最终 EXE 在 21:12:18 发出启动请求，[冷启动日志](dist/AutoZZZ/runs/20261011_211218_159560/run.log) 随后等待游戏窗口；在 21:12:45.746 与 21:12:57.425 两次识别并点击 `点击进入游戏`，21:13:25 进入日常核验。 [该次报告](dist/AutoZZZ/runs/20261011_211218_159560/report.json) 从 21:12:18.159 到 21:13:42.743，约 **1 分 25 秒**，为 **success**，四项 `ok`、400 活跃、`daily_verified: true`、记录 0 电量支出。

该次 [初始保存图](dist/AutoZZZ/runs/20261011_211218_159560/211227_209812_start.png) 捕获的是桌面，说明游戏呈现尚未就绪；它不能当作黑屏截图。程序在启动未就绪期间只读等待，日志没有此阶段点击，之后才点击明确标题入口。早先冷启动黑屏观察属于其他失败运行，不混作本次图片证据。

[最终日常截图](dist/AutoZZZ/runs/20261011_211218_159560/211342_595569_daily_verified.png) 已独立目视核对：四个档位均为绿色勾选，电量 `320/240`，消耗电量任务仍为 `0/60`。这次是**关闭客户端 → 启动 → 等待 → 两次进入按钮 → 已完成日核验**成功。由于当日日常此前已经完成，签到、咖啡与录像店按已满且已领跳过；它与 20:54 的未完成日从 100 起连续执行共同验证启动与日常功能，但不能说本次冷启动又重新领取了签到、免费饮品和每日奖励。

本次会话记忆使保存账号登录窗未出现，因此 `cached_account_login` / `进入游戏` 账号窗分支依旧没有独立实机验收。国际服跨每日重置仍未验收。

## 可借鉴资源与实机校准要求

可直接参考页面 YAML 的文字、客户区 ROI、图标模板 ID，以及模块的有限重试状态机。模板真实文件路径需要从仓库资源树再次确认：本研究确认了配置中的模板引用，没有成功核验每个 PNG 文件路径。上游 LICENSE 是 **GPL-3.0**；如果复制上游源码、YAML 或模板，应保留来源、许可证与修改说明。仓库 LOGO 在 README 另有版权标注，不应作为 AutoZZZ 的图标使用。[仓库与许可](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon)、[LICENSE](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/main/LICENSE)。

上游基础设置建议是 16:9 客户区，首选 1920×1080 窗口，画面保持完整、稳定，避免 HDR/滤镜改变识别颜色；镜头转向要求灵敏度校准。只做非战斗每日时无需借鉴预备编队等战斗要求。上述研究尚未改变本机游戏设置。[官方必要设置](https://one-dragon.com/zzz/zh/config.html)、[官方游戏设置](https://one-dragon.com/zzz/zh/setting/setting_game.html)。

后续仍可扩展校准：

1. 其他已解锁区域的日常前往落点，以及旧地图传送入口。
2. 六分街报刊亭、六分街咖啡店、旧录像店柜台路径。
3. 新日期国服的一次连续运行及国际服跨每日重置状态。
4. 其他界面语言、窗口尺寸和不同镜头/起点。
5. 不同账号宣传员设置与缺少推荐上架时的有限配置分支。

## 2026-10-16：完成后正常退出参考

核实 BetterGI 官方仓库 babalae/better-genshin-impact，固定提交 c9db8b8291fe4ac25a600aee1582298005919eb2。只阅读源码，未安装或运行 BetterGI。

- [OneDragonFlowViewModel.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/ViewModel/Pages/OneDragonFlowViewModel.cs)：任务列表快照、取消时提前返回、最终 CheckRewardsTask 后执行 CompletionAction，配置 JSON 保存。
- [OneDragonFlowConfig.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/Core/Config/OneDragonFlowConfig.cs) 与 [OneDragonFlowPage.xaml](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/View/Pages/OneDragonFlowPage.xaml)：结束动作独立配置，默认空动作，UI 双向绑定。
- [SystemControl.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/GameTask/SystemControl.cs)：绑定当前游戏运行环境与会话，先 CloseMainWindow、等待退出；上游随后有 Kill 回退及独立关机能力。AutoZZZ 按用户要求不实现这些分支，只正常关闭并等待本轮对应进程。
- [CancellationContext.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/Core/Script/CancellationContext.cs) 与 [TaskRunner.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/GameTask/TaskRunner.cs)：手动取消标记、取消令牌、任务锁和 finally 清理。
- [App.xaml.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/App.xaml.cs)：启动入口调用 UseElevated；官方 README 说明管理员游戏需要对应权限才能输入。AutoZZZ 保留现有管理员启动器和 UAC 提示，不新增权限设置或持久提权。
- [ConfigService.cs](https://github.com/babalae/better-genshin-impact/blob/c9db8b8291fe4ac25a600aee1582298005919eb2/BetterGenshinImpact/Service/ConfigService.cs)：配置快照、临时文件、同卷原子替换和写入失败反馈。AutoZZZ 沿用既有临时文件替换保存；本功能不引入后台自动保存。

本次采用：单独且默认关闭的 close_game_on_success 布尔设置；结束动作放在本轮已选免费收尾之后；400 活跃及四档全领与每项已选结果均需为 ok；此前 partial、异常和停止不触发退出；正常 WM_CLOSE 只发给本轮已核实的同一路径、同一进程句柄；普通退出确认需明确退出提示及唯一确认；等待最多 15 秒，未退出则记录 partial，不强杀。只启动游戏的 enter_game 操作不触发该开关。用户原配置保持，仅点击“保存设置”时写入。

## 2026-10-22 桌面 UI 实机对照

本次以网上公开的实际游戏 UI 为布局基准，替换先前只借用黑灰 / 黄绿和磁带元素的方案。研究期间未打开绝区零。

| 对照来源 | 实际采用的结构 |
| --- | --- |
| [Gfinity 的快捷手册实机图](https://www.gfinityesports.com/article/zenless-zone-zero-dailies) | 包边手册、缝线、橙色书脊、凸起标签、活跃度槽、竖向任务卡、底部胶囊按钮 |
| [米游社中文日常界面](https://www.miyoushe.com/zzz/article/54960667) | 中文标题层级、日常标签、完成绿与黄色选中态、左侧挂件 |
| [游戏设置实机图](https://note.com/sandriver/n/n10297bc10f44) | 黑色嵌套面板、左侧分类、灰色胶囊行、深色输入槽和黄色焦点环 |
| [官网应用脚本](https://zzz.mihoyo.com/_nuxt/d6e69e2.js) | 确定实际使用的印品鸿蒙体、Mont Heavy 与 ZZZ 标志资源 URL |

官网资源通过公开 URL 下载，字体只转换容器格式。来源、SHA-256 和变更记录见 [assets/ui/sources.json](assets/ui/sources.json)，权益说明见 [assets/ui/README.md](assets/ui/README.md)。完成图标复用本仓库已注明来源的 `daily_completed.png`；未复制新的上游程序代码。

AutoZZZ 将这些控件接入自身的每日、独立战斗和日志：目标 / 日常 / 训练 / 记录 / 设置；六项卡片使用真实任务结果，活跃度槽读取当前报告变量，未验证时不点亮。按钮文案和页面内容对应助手现有操作，布局随窗口尺寸缩放。因此这是游戏 UI 的功能化重建，不是完整游戏截图或逐像素不变的贴图。原参考截图仅保存在忽略目录 `runs/ui-reference/`，不发布玩家账号和水印。
