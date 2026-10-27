<!-- 当前 I/O 契约于 2026-10-20 更新；文末 2026-10-19 验证记录描述历史实现。 -->

# Saves 领域对象与集合 API

完整建模实现与验证状态见[存档完整建模迁移](finished/saves-modeling.md)。五种格式均按声明字段建模，未知字段默认 error，显式 warn 后警告并丢弃，不提供 ignore；XML 不再保留原始树或序列化片段。以下示例省略的字段不表示未建模，完整新增字段与类型见该说明及对应领域类型。

容器接口详见 [存档容器与成员关系](saves-containers.md)：房间统计使用普通 dict，受控成员容器维护活动区及回收区的独立索引，冲突在加载时处理。非法值修复回调尚未设计或实现。

## 首要设计原则

以下原则优先于本文的具体接口方案和历史验证记录；发现既有实现与原则不符时，应查证后修正，不能以现有代码或测试反推游戏行为。

- 严格模式下支持且仅支持游戏正常输出的文件。游戏能反序列化某种手工输入，不代表严格模式应接受它；判断依据是正常保存路径，而非是否可以构造这种 XML／YAML。
- 宽松模式下支持游戏可以读取且不会立即出错的文件，允许用户读取后检查和修复。遵循游戏真实的默认值、忽略和覆盖行为，不因看起来矛盾而额外拒绝，也不把“读取成功”承诺为后续所有玩法都安全。
- 如果某些不一致会导致游戏读取后出现错误行为，必须禁止写出这种不一致。读取可用于修复不代表允许原样写回；由用户显式修复，再执行独立的写出校验，不静默截断、重排或猜测数据。
- 所有不确定行为都必须先回到游戏源码查证，再实现。追踪原版／Everest／相关 Mod 的实际读写及使用路径；涉及底层序列化器时继续核对对应实现，必要时用等价样本验证机制。记录来源、运行时和验证范围，不把推测、其他运行时样本或 mock 当作已完成的游戏端到端验证。

`LastSave` 是上述有效性检查的明确例外：本库只通过只读 `MainSave.last_save_text: str | None` 透传元素文本，不解释日期、时区或小数精度，严格模式同样不校验其 DateTime 语法。因此读取或写出成功不证明该字段可被游戏解析。XML 结构与未知字段检查仍适用；缺失保存为 None 并省略输出，空元素保存为空字符串；空构造输出游戏默认文本 `0001-01-01T00:00:00`。不提供公开赋值、日期转换或自动更新时间能力，也不保留原始 XML 节点。

`MainSave.assist_mode` 与 `variant_mode` 在原版界面中互斥：`OuiAssistMode` 开启辅助模式时关闭异变模式，`OuiFileSelectSlot.OnVariantSelected()` 开启异变模式时关闭辅助模式。因此严格读取拒绝两者同时为 true，由 Pydantic 的 model_post_init 校验并抛出 `ValidationError`（属于 ValueError）。但 `SaveData.AssistModeChecks()` 不纠正同时为 true 的输入，此时辅助参数按异变模式分支处理；宽松读取允许保留两者，普通赋值不联动，写出也不强制互斥。读取约束描述游戏正常生成的状态，不作为编辑后的写出限制；本库不替用户选择要关闭哪个模式。

严格读取时，主存档 `Time`、Map 的 `TimePlayed/BestTime/BestFullClearTime`、Session 的 `Time`（包括 OldStats 内的 Map）必须为 17 ms 的整数倍，XML 字段通过 XmlTimerDuration 注解校验，不满足时抛出带字段路径的 `ValidationError`（属于 ValueError）；非 Pydantic 的领域检查仍可抛出 BadProgressSave。主文件及备份的活动／回收分区沿用同一检查；独立进度解析由其 strict 参数控制。依据是原版 `Level.UpdateTime()` 使用 `Engine.RawDeltaTime`，经 `TimeSpan.FromSeconds()` 整毫秒舍入后累加，正常 60 Hz 步长为 17 ms；Everest 的 `NETCoreifier/Patches/TimeSpan.cs` 保留该舍入行为。宽松读取、普通赋值和写出均保留完整 ticks，不自动对齐，也不把规则加入通用 `Duration` 或 `SaveDuration`。

`EndersBlenderSave(strict=True)` 默认检查 first_clear 和 last_saved 中的 `room_times`，可通过 strict=False 读取非对齐数据。Blender 的 `AddTimer()` 默认使用相同游戏计时步长；`room_rta_times` 则来自 `DateTime.Now.Ticks` 的真实时间差，不检查 17 ms 整数倍。strict 仅影响读取，写出不据此限制已编辑数据。尚无 strict 参数的其他 Mod 文档不因此新增隐式计时检查。

状态：本文确认范围已于 2026-10-19 实现并完成逐项回查，包含文件层、槽位协调层、领域对象及业务操作。下文保留设计决策和验证证据；不表示覆盖所有游戏／Mod 存档字段，也不表示经过游戏运行加载验证。旧设计与格式调研见 [Saves 三层接口设计](archive/saves-api-design.md)。代码块仍是接口说明，不是待导入的实现模块。

### 已实现范围

- `saves.progress` 提供 `PersonalBests`、`MapProgress`、`LevelProgress`、`CampaignProgress`、`Session`，members 模块提供受控成员与分区索引。领域值不依赖文件对象、UI 或 Pist，普通进度不自动联动存档累计；对象级 XML/YAML 转换由对应类型的方法封装。
- 已覆盖集合引用和替换顺序、身份只读、SID 归属、父结构缺失时不创建、领域复制隔离及内部别名保留、进度重置与成员移除的区别、跨根共享子对象，以及视图跟随根或成员替换。身份查询直接按 SID 推导归属并查表，不重建全量索引；批量遍历单次消费。
- Ender's Blender 的六类初见／上次保存统计、名称和颜色通过 `RoomStats` 及普通字典访问。加载时转换全部已建模统计，结构或数值无效在加载时报错。可转换但缺少关联字段的记录可读取修复，写出检查必要关联；已知别名解码为独立领域值，未知字段按 error／warn 策略处理。
- 文件通过一次性 `read()`／`write()` 加载和保存。`SaveSlot` 是位置句柄，`SavesDir.slots` 提供含空位的有限只读序列、可选空位过滤及调试槽位入口；Mod 文件由 `ModSave.mod_name` 定位。主文件／备份默认严格检查，宽松加载不豁免后续写出校验。
- Collab 的 `sessions` 集合、速度莓 PB、大厅门／彩虹莓／传送台集合及已学技巧通过普通领域对象访问。保留会话首次访问 `.session` 时解析，原始 XML 作为不可变输入，已解析会话可以直接编辑。另提供速度莓提示状态、大厅访问位置及地图显示开关；未知字段默认报错，显式 warn 则警告并丢弃。
- 主文件和 Everest 备份支持完整已知字段、原版数字 ID 和草莓累计差额。LevelProgress.maps 表达 A/B/C 位置，main.vanilla 与 main.mods.campaigns 分别提供原版及 Mod 进度，活动／回收区各有索引。未知 XML 遵循 error／warn 策略，不再承诺 QName、注释、PI 或 CDATA 保真。
- 已实现当前会话双分支协调、整个 Mod 快照的显式恢复，以及 `correct_map_progress()` 的累计差额预检和更新。跨存档 `move_map_progress/move_level_progress/move_campaign_progress` 已提供显式覆盖／叠加、诗句选择、累计联动、参与对象重叠检查和可选 Blender 联动；测试覆盖正常转移、拒绝前两端不变和持有引用的行为。
- 独立 merge／overwrite、判空与清空操作，以及不清空源的 `copy_*_progress` 已实现。移动与复制共享转移预检，移动另组合独立清空准备；单 Map 排他模式只检查选中 side，A-side 才包含磁带。
- 2026-10-22 补充活动／保留会话随进度转移：主存档按活动槽位、Collab 按 SID 槽位判断冲突，显式 discard 才丢弃冲突会话，Collab 的文本／binary Mod 载荷成组处理；具体范围与限制见第 10 节。

### 历史验证记录

以下记录对应 2026-10-19 的领域实现验证，不是每次后续修改的验收结果。专项及调用方当时共 499 项通过，Ruff／Pyright 通过；临时真实副本覆盖复制提交与重读，并验证源成员、进度及累计保持不变。
- 已在存档 6 的临时 Blender 副本上验证全部统计、名称顺序与颜色的编辑和重载，并在临时主文件副本上验证默认严格加载、死亡数修改、提交和重载，没有修改游戏原件或数据库。
- 已在存档 6 的 Collab 临时副本上验证 29 条保留会话的写回与重载；主文件整体生命周期在同一临时副本的 352 个实际进度分区、4416 条 Map 上验证无修改写回的规范化 XML 一致，以及校正后的路径提交、回读和备份快照重载。此前独立编解码统计的 353 项包括无进度的 Celeste 占位项，整体领域统计不再将其重复计入。原件及源副本均未修改。这是功能验证，不替代后续全部业务操作的集成回查。

## 目标与范围

`saves.progress` 按领域职责组织为子包：`map.py` 保存 `MapProgress` 与 `PersonalBests`，`level.py` 保存具有 A/B/C 位置及异常输入观察能力的 `LevelProgress`，`campaign.py` 保存 `CampaignProgress`，`members.py` 管理成员归属与分区索引，`session.py` 保存立即求值的 `Session`，会话原文和惰性领域值由 `saves.session_input.SessionInput` 协调。通用身份集合位于 `berries.containers`。存档包入口只重导出公共类型；实现内部直接导入所属模块。对象级转换随类型封装，跨对象业务协调和文件写出由 operations 及 files 子包负责。

`saves.files` 组织文件文档，构造器直接建立领域默认值，不先解析空模板；一次性路径／流适配位于 TextDocMixin。codecs.values 集中 count、ticks、Duration 和布尔值转换，codecs.single 用 gmpy2 的 IEEE32 上下文处理 float32。XML 的 tree、schema、fields、mods、session、summit_gems、vanilla、editing 分别提供临时树工具、模式校验、协议辅助与输出预检；已删除 progress／extensions 的片段保真实现。YAML 的 nodes、tags、enders_blender 处理临时节点、稀疏标签元数据与并行统计适配。

Blender 转移协调位于 `operations/blender.py`；空进度判定位于 `operations/edit.py`。进度对象通过自身方法检查原始扩展和历史草莓计数偏移，通用 XML 检查由 codec 提供；输出准备组合对象级转换，Blender 统计的校验和独立字段准备收在转移模块的私有步骤中，不作为 RoomStats 的公共 API。codec 不依赖文件文档或业务操作，operations 不直接解释 XML／YAML；依赖方向由架构测试约束。

通用的 `berries.containers.KeyedDict[K, T]` 继承 `Mapping`，由构造参数 key 函数派生身份；`add()`、`update()` 按 dict 语义覆盖，`adddefault()` 保留已有值并返回最终值，`replace()` 要求 key 已存在。存档成员通过组合 KeyedDict 的 CampaignMembers／LevelMembers 维护归属约束与索引同步；宽松读取可以保留唯一错放输入，新成员安装和写出分别按其契约校验。通用映射语义见 [KeyedDict](keyed-dict.md)，领域协调见 [存档容器](saves-containers.md)。

### 对象级 XML/YAML 转换

主文件和 Everest 备份将非空会话立即解码为 Session；Collab 的外层 XML 字符串通过 SessionInput 保留，首次访问 .session 时解码并缓存。Session 仅保留领域值和业务变化基准。显式存在但 in_level=false 的安全会话阻止回退旧 CurrentSession。无法确定身份的活动会话拒绝读取；未知 Level ID 通过 unresolved_levels 暴露。主文件只将安全会话的实际修改同步到同身份旧会话，缺少安全会话时不凭空创建它。

PersonalBests、MapProgress、LevelProgress、CampaignProgress、Session、ModSaveData 的 from_xml() 建立完整已知值，to_xml() 建立新临时元素，文本编码由对应适配入口负责。ModSaveData 是主文件与备份共用的 Mod 数据模型，不提供文件管理能力。RoomStats.from_yaml()／to_yaml() 处理房间字段；对象转换不发现、打开或提交文件。

Map、Level、Campaign、Session 和 ModSaveData 的 from_xml() 接收已解析的 Element。解析不消费或改写输入树，也不长期保留它；已知字段在解析时全部转换，非法值立即报错。只有 Collab 的外层会话字符串保持按需解析。Map／Level／Campaign 的转移准备直接校验领域值，不通过构造 XML 再解码来准备副本。

`saves.operations` 同样是职责子包：`transfer.py` 集中转移预检及 copy／move／merge／overwrite 入口，不为几个包装函数单独拆模块；`edit.py` 提供累计校正、清空、判空、显式恢复和进行中查询；`poem.py` 管理诗句选择和默认 ID 规则；`preparation.py` 共享输出校验后的领域对象准备，不承担 XML 编解码。包入口只重导出已有公共函数及类型，原有导入方式不变。所有操作只组合显式传入的内存对象，不隐式读取、提交文件或建立备份。

MapProgress.validate_output() 和 LevelProgress.validate_output() 检查原生写出所需的数值范围、无损 ticks、文本／实体键及三项非空 Modes，不构造 XML、不修改当前值，也不套用严格读取才要求的 17 ms 精度。编码器复用同一组字段约束和结构检查。Campaign 的元数据通过 CampaignFields 校验，准备时仍检查整个候选 Campaign 的成员归属和写出有效性，不能因只移动一个 Map 就放过未修改成员中的非法值。准备结果保留未修改 Level／side 的引用，并按来源计数偏移重建实际参与修改的 Map；仍须在全部预检成功后才安装进度与累计。

先从用户如何浏览、查询、复制、校正和转移存档数据出发确定 API，再实现文件编解码。不得以当前 XML/YAML 节点组织或 Pist 已有调用方式限制领域模型。本文覆盖槽位定位、文件访问、主存档与 Everest 备份、地图集／LevelProgress／Map 进度、当前与保留会话、Ender's Blender 统计，以及校正和跨存档移动；不宣称已建模 Celeste 或所有 Mod 的全部字段。

领域对象保存普通 Python 数据，不作为节点代理。未知字段默认拒绝，显式 warn 后不进入领域对象或输出。有游戏语义的默认值差异、历史累计偏移和变化基准单独建模，不以原始编码代替。引用、复制和集合契约不因完整建模而改变。

写出主存档时，直接编辑 Mod 集合、进度或会话也须更新 `HasModdedSaveData`，避免下次恢复流程用旧快照覆盖已明确写出的内容。此前未标记的文件保留独立基准，在写出阶段比较有序 Campaign 值、安全地图引用、会话和占位项，不通过编码或摘要判断变化，也不为比较构造进度对象或索引；新建主存档使用空进度基准。只有输入已标记 `HasModdedSaveData=true` 时才用 None 表示无需比较，而不是用 None 表示空进度。不让普通集合操作联动父字段，未修改 Mod 字段时不因只读浏览、会话物化或仅修改原版字段而改写恢复语义。

`ModSaveData` 根据 FieldModel 的字段元数据比较完整已建模数据，包括成员顺序、活动／回收分区、活动 Celeste 占位项、最后 Map 引用及会话，排除派生索引和内部编辑基准。MapProgress、LevelProgress、CampaignProgress 和 Session 同样通过 `==`／`!=` 比较领域值，未解析 Level 使用 dataclass 的字段比较；这些可变对象均不可哈希。索引关联及会话编辑基准不参与比较，判断实例身份使用 `is`。比较无需先修复或编码；使用普通值比较，不保证包含 NaN 的异常数据具有稳定的等价关系。共享的 `MapRef` 位于 `progress/map_ref.py`，供文件和会话共同使用，不属于会话专属状态。

已知 YAML 字段也可能携带未解释的 tag。编解码只保存这些 tag 的稀疏路径、节点类型和 tag 文本，不保留第二份可编辑值；重建已知映射、序列或标量时复用对应信息。序列标量按值及同值出现次序匹配，避免集合排序后将 tag 转移给其他元素；成员明确移除时不复活旧值。恢复 tag 仅复制发生变化的节点路径，不修改惰性记录的只读输入或共享的未知别名。

## 1. 统一命名与对象关系

| 概念 | 类型或成员 | 含义 |
| --- | --- | --- |
| 存档目录 | `SavesDir` | 通过 slots 访问有限只读序列，普通迭代包含空位 |
| 槽位 | `SaveSlot` | 编号与文件位置，不是已加载文件组 |
| 单文件 | `Save` | 编解码、内存对象和显式文件访问的生命周期 |
| 某个 Mod 的文件 | `ModSave` | 子类声明对应的 `mod_name` |
| 主存档 | `MainSave` | 原版、Mod 进度、全局累计及主文件字段 |
| Everest 备份 | `ModBackupSave` | 平台配套备份，不是某个 Mod 的 `ModSave` |
| Mod 进度投影 | `ModsProgressView`，只读绑定的成员 `mods` | 编辑根模型实际持有的活动／回收地图集和会话 |
| 地图集进度 | `CampaignProgress` | 保存累计、诗句及显式 `levels` 成员 |
| 同地图集 LevelProgress 集合 | `LevelMembers`，成员 `levels` | 按唯一 SID 访问，新写入校验归属并同步分区索引 |
| SID 对应的进度及 Map 集合 | `LevelProgress` | 通过 maps 列表保存该 SID 的各 side |
| 具体 side 进度 | `MapProgress` | 对应现有 Berries 的 `Map` 概念 |
| 分区 LevelProgress 索引 | `LevelIndex`，property `flat_levels`／`recycled_flat_levels` | 两区独立，引用实际 Level，并协调成员修改 |
| 上次选择的地图身份 | `last_map` | SID 与 side，不沿用协议名称 `LastArea` |

```text
MainSave
├── vanilla: VanillaProgressView
│   └── levels: LevelMembers
├── mods: ModsProgressView
│   ├── campaigns
│   ├── recycled_campaigns
│   ├── last_map
│   └── session
├── flat_levels             原版 + 活动 Mod 的 SID 索引
├── recycled_flat_levels    回收区 SID 索引
├── current_map      当前会话身份的廉价查询
└── session          已立即解码的完整当前会话
```

原版进度通过 `main.vanilla` 访问，活动 Mod 地图集通过 `main.mods.campaigns` 访问。根字段 `main.campaigns` 同样只保存 Mod 成员，并非原版与 Mod 的统一集合。`flat_levels` 与 `recycled_flat_levels` 分别覆盖活动区、回收区，加载时构造并在结构修改时同步，不在查询时生成字典或消歧。进度成员不依靠唯一父节点或唯一父存档决定自身语义。

XML LevelSets 中名为 Celeste 的持久化条目通过 ModSaveData.vanilla_entry 建模，并由 mods.vanilla_entry 投影，不进入活动 Mod Campaign 集合。该对象只保存文件中实际存在的数据，不执行转发；VanillaProgressView 始终绑定根部原版字段，与条目是否存在无关。缺失条目保持 None，构造、访问视图和写出均不自动补建。已有条目写出时必须满足名称 Celeste、空 Areas／未解析 Level／Poem 的约束；主文件在临时副本中用原版根字段生成 UnlockedAreas／TotalStrawberries，内存条目不变。独立 Mod 备份没有原版根数据，保留条目自身的合法累计，不臆造对应值。回收区中同名项仍作为普通 recycled_campaigns['Celeste'] 保留，不设专用字段，也不联动原版进度。未知字段同样遵循 error／warn。

### 分区索引是活的成员入口

`main.flat_levels` 持续绑定该 MainSave 的原版和活动 Mod，`main.recycled_flat_levels` 绑定当前回收区。两个索引按 key 迭代，values／items 是活视图，返回实际进度引用；修改在发生时同步索引，不要求重新查询或显式 reindex。通过 campaign.levels 或扁平索引均可增删和替换；父 Campaign 必须存在。接口及数据关系详见 [存档容器与成员关系](saves-containers.md)。

显式安装进度或替换 Campaign／levels 后，已持有的绑定视图和分区索引看到新结构；被移除对象及旧成员容器仍可独立使用，不再被旧根订阅。集合复制不复制索引订阅或整个根文档；普通引用共享时所有已登记根都同步预检和更新。查询只读当前索引，不触发回调。普通 Map 字段和 level.maps 仍可编辑，不自动联动累计。

```python
levels = target.flat_levels
target.install_campaigns((target, deepcopy(source.vanilla.snapshot())))
for level in target.vanilla.levels.values():
    assert levels[level.sid] is level
```

## 2. 对象、复制与修改契约

Everest 在运行时为原版与 Mod 提供统一的 LevelSetStats 接口。初始化绑定 SaveData 后，Celeste 条目的 UnlockedAreas／TotalStrawberries 读写转向根部原版累计，AreasIncludingCeleste 指向根部 Areas，派生统计和解锁查询据此工作；并非所有成员都转发，自身 Areas／Poem 在正常流程中为空。缺失项会由 Everest 补建，而 Berries 通过独立视图满足运行时访问，不据此修改输入文件结构。实现证据见 [SaveData.cs](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L659-L700)。

严格读取与写出共用活动 Celeste 条目的空列表约束，允许合法的非零累计；宽松读取保留非空列表供检查和显式修复，不自动清除。写出前必须清空或迁出非空 Areas 与 Poem。Areas 还具有明确的错位风险：Everest 的 computeAreasSafe 会把它追加到全局进度列表，导致后续 Mod 地图按数字 ID 访问时错位；Poem 则按正常输出和严格读回的一致性要求拒绝。回收区 Celeste 按普通回收历史处理，使用通用成员及冲突规则，不套用这组空列表校验，也不从其数字 ID 推导原版 SID。

Campaign 的 XML 输入区分两个入口：`CampaignProgress.from_xml(root, ...)` 读取完整 LevelSetStats，名称直接来自 XML Name，不再由调用方重复提供；Name 缺失时沿用原有 Mod 集合加载的空字符串处理，不额外引入新的缺失字段策略。`CampaignProgress.from_main_xml(root, ...)` 从主存档根直接读取 Areas、Poem、UnlockedAreas 和 TotalStrawberries，固定使用 Celeste 与原版数字 ID 规则，其他主存档字段由 MainSave 解码器校验。两个入口复用字段解析、严格模式和冲突处理，不构造临时包装元素，不复制或移动输入树的节点。

地图身份统一使用 `berries.game.levels.MapKey`（`type MapKey = tuple[str, LevelSide]`），也从 Berries 包入口导出；元组首项是 Level 的 SID，side 进一步确定具体 Map，不增加运行时包装或改变比较语义。Level／LevelProgress 的自身身份仍名为 sid。存档持久化引用使用 `MapRef(level_id, side, level_sid)`，位于 `saves.progress.map_ref`，从 saves 和 saves.progress 导出；它保留 XML 中可缺失的 SID 与数字 ID，`resolve_key(vanilla=False)` 返回 MapKey 或 None。显式 SID 优先，仅在 vanilla=True 时允许按内置原版数字 ID 解析；解析不会补写 SID。`last_map_ref` 保存引用，`last_map` 提供解析后的身份，`current_map` 提供活跃会话的身份；主存档的 last_map 按原版规则只读解析，ModsProgressView 的 last_map 保留既有可写投影语义。

`MainSaveData` 是独立于文件入口的可变领域 XmlModel，位于 saves.main_save_data，也从 saves 包入口导出。它拥有主字段及原版 levels／unresolved_levels／poem／unlocked_levels／total_strawberries／vanilla_session；公共基类 ModSaveData 拥有 campaigns／recycled_campaigns／safe_last_map_ref／safe_session／vanilla_entry。需要内部替换的 levels／campaigns／recycled_campaigns 使用私有属性和只读 property，其余实际数据为普通模型字段，两者均参与值比较和标准复制；不保存另一份完整进度快照。vanilla／mods 是只读绑定的视图，可编辑投影字段和受控成员，不能整体替换。派生视图和索引排除出比较及复制，副本通过 model_post_copy 重新绑定到新根。成员替换预检、索引联动和批量安装均属数据层。集合输入通过 PrivateAttr 的 alias 声明，FieldModel 据此生成运行时校验和静态构造提示；字段校验及成员安装完成后才建立视图、索引和编辑基准。from_xml 复用相同校验路径，不通过 context 传集合，不先创建空对象再重载。对象可变且不可哈希，不提供文件 read／write；to_xml 输出完整数据。内部直接更新私有集合属性，不绕过 frozen；LastSave 仍为真正冻结的模型字段。详细边界见 [存档 XML 字段绑定](finished/save-xml-binding.md)。

Mod 编辑基准 `_mods_baseline` 由 MainSave 文件协调层持有，用于写出时判断是否更新 HasModdedSaveData，不进入 MainSaveData。它声明为 `PrivateAttr(default=None, compare=False)`，在构造方法完成领域成员初始化后按实际 Mod 数据建立；已标记输入保留 None，未标记输入和空构造使用实际状态的独立副本，不先创建待替换的空基准。它只排除值比较，保留默认复制行为；浅拷贝共享该基准，深拷贝隔离它，二者均保留原有变化检测。

`MainSave(MainSaveData, Save)` 只增加文本／文件入口及 Mod 编辑基准协调，不重复定义数据或索引。双会话输出及 Celeste 占位累计同步由完整编码过程负责，不修改原对象。ModBackupSave 继承 ModSaveData，直接拥有其字段，mods 视图绑定这些字段。独立的 Mod 数据统一使用 ModSaveData，独立 Campaign 使用 CampaignProgress。ModsProgressView 不提供 snapshot()；需要递归隔离时显式 deepcopy 数据对象，复制视图本身仍指向原根对象。VanillaProgressView.snapshot() 保留其根字段转为 CampaignProgress 的职责。strict／unknown／冲突回调仅沿本次解析传递，不是构造参数或实例状态；写出使用固定领域约束，不沿用读取策略。

此前 ModsProgress 同时持有数据、建立索引并承担 XML 中转，与 ModSaveData 重复；其 snapshot() 只分离成员外壳，仍共享子进度和会话。现已删除这个模型及对应入口。Campaign 分区先解码、消解冲突，直接作为根模型构造输入；安全会话和最后地图引用通过 Xml 注解绑定，根模型在输入安装后统一建立索引。整体安装接受 ModSaveData 或绑定视图，预检后沿用输入成员引用；不会隐式复制。需要独立编辑备份数据时可使用以下方式：

```python
prepared = deepcopy(backup)
progress = prepared.mods.campaigns['Pack'].levels['Pack/Map'].maps[LevelSide.A]
if progress is not None:
    progress.deaths += 1
main.install_mod_progress(prepared.mods)
# 安装共享 prepared 的成员；如需保留独立的后续工作副本，调用方另行复制。
```

- 普通成员赋值和集合 `add()`、`replace()` 保留引用，不隐式复制，也不从其他容器搬走对象。
- 只有集合语义的类型主动提供 `.copy()`，非集合领域对象使用标准 `copy.copy()`，不额外包装同名便捷方法。集合的 `.copy()` 与 `copy.copy()` 都复制本层存储并保留成员引用；普通领域对象的 `copy.copy()` 保留字段值及嵌套对象引用。`copy.deepcopy()` 才递归隔离可写状态，并保留副本内部的共享关系。这个契约同样适用于 `Save` 及其具体格式子类。
- 新结果优先直接构造，例如 PersonalBests、收集集合和检查点。复制已有状态时保留全部建模值、历史累计偏移和缺损结构，不用只填当前操作所需字段的构造器冒充完整副本。Campaign／Level 可以浅复制外层并独立准备参与修改的成员；Blender 保留必要字段存在性。
- `CampaignProgress` 是普通领域对象，浅复制保留 `levels` 容器和 `poem` 列表引用；需要独立增删 Level 成员时显式执行 `branch.levels = original.levels.copy()`，副本仍引用原 Level。`LevelProgress` 不定制复制协议，普通浅复制保留 maps 列表引用；需要独立调整位置时显式执行 `branch.maps = original.maps.copy()`，成员仍共享。本层累计字段及磁带状态可独立赋值，直接修改共享的集合、子进度或诗句列表仍会影响两端。`MapProgress` 的收集集合与最佳成绩、`ModSaveData` 的地图集和会话、`RoomStats` 的房间列表与字典同样保留引用，需要递归隔离时显式使用 `deepcopy()`。
- `Save` 是支持编解码的内存存档领域对象，不提供 `.copy()` 便捷方法，但支持标准复制协议。浅拷贝共享领域成员；`MainSave`／`ModBackupSave` 重建各自的派生索引，使替换副本的 vanilla／mods 不会重新绑定原对象的索引，而共享成员的修改仍对两端可见。深拷贝递归隔离可写数据并重建索引，保持副本内部成员与索引的引用一致。复制不执行文件 I/O。
- 查询返回集合中实际持有的对象，不是必须交回 setter 的临时快照。
- 删除成员（映射的 `del`／`pop()`，或其他容器的 `remove()`）后，外部引用仍可使用；移除对象不自动清空其进度。
- `PersonalBests`、`MapProgress`、`LevelProgress`、`CampaignProgress`、`RoomStats` 的 `reset()` 清除其受支持的进度，保留身份与 LevelProgress／Map 结构，不联动父累计。成员容器和普通字典的 `clear()` 移除各自范围内的成员；`campaign.levels.clear()` 不改动 Campaign 自身的累计或诗句。重置单 Map 用 progress.reset()，重置整个 Level 用 level.reset()；没有 FlatMaps.reset 入口。不提供旧的归零 `clear()` 别名。
- 普通字段或集合修改只改变自身数据，不隐式调整父存档累计、会话、备份或其他 Mod 数据。
- 身份字段 `CampaignProgress.name`、`LevelProgress.sid`、`Session.sid/side` 只读。`MapProgress` 不保存 side，其身份由 Level 中的位置或扁平视图的 `(SID, side)` 键表达；同一个进度对象可以被多个位置引用。重命名和身份转换不在本版范围内，不能通过任意赋值破坏索引。
- 成员容器的 `add()` 覆盖已有身份，`adddefault()` 保留已有项；Blender 字典遵循普通 dict 写入语义。各集合的 `replace()` 要求原身份存在，不自动合并。合并属于显式业务操作。

```python
from copy import deepcopy

campaign = source.mods.campaigns['Cdog/ExtendedCSides']
target.mods.campaigns.add(campaign)
assert target.mods.campaigns[campaign.name] is campaign

del target.mods.campaigns[campaign.name]
target.mods.campaigns.add(deepcopy(campaign))
assert target.mods.campaigns[campaign.name] is not campaign
```

共享引用符合普通 Python 语义；跨存档业务转移则应生成需要的独立目标状态，不能清空源对象时连带清空目标。不得为了联动全局累计而让共享进度对象隐式记住某个父存档。

## 3. 目录、槽位与一次性文件 I/O

`Save` 是支持编解码的内存存档领域对象，不是持续打开的文件对象。构造器创建空白文档；`read(file, ...)` 一次性从路径或流读取，`fromstring(data, ...)` 接收 `str`／`bytes` 内容，返回后均不持有文件或流。`tostring()` 返回当前文档文本，不进行 I/O；`write(file)` 先完整编码，再打开目标路径或写入外部流。文档没有模式、`close()`、`closed` 或上下文协议，不自动保存；允许重复编辑并写往不同目标，不提供额外的 `.copy()` 或备份 API。

`SaveFile = str | PathLike[str] | BinaryIO | TextIO` 是公开类型别名，从 `berries.game.saves.files` 导入。字符串传给 `read()` 时始终表示路径，传给 `fromstring()` 时始终表示内容。路径读写自行打开并关闭文件；外部流从当前游标读取或写入，不自动 seek、truncate、flush 或 close，不要求可 seek。路径及二进制流使用 UTF-8 输出；文本流接收字符串，编码和换行由流自身决定。XML 文本输出不携带编码声明，避免与调用方选择的编码矛盾；字节输入仍按文件声明解码，已解码字符串不受旧编码声明限制。

通用适配位于 `berries.text_doc.TextDocMixin`：扩展点为格式类的 `fromstring(data, /, ...)` 与 `tostring()`，不依赖 XML、YAML 或存档领域。`read(file, /, *args, **kwargs)` 一次读取内容后，将额外参数转发给 fromstring，而不是构造器；通过 ParamSpec 和结构化解析协议保留具体解析参数及返回类型的静态检查。五种存档的构造器只建立空白领域值，解析选项只在各自解析入口出现。读取入口在解析 ValueError 上通过 `add_note()` 附上文件／流信息，保留原异常类型与对象；fromstring 不虚构来源标签。解析参数错误也可能发生在内容已读取之后，不承诺输入游标未变化。

路径写出采用普通覆盖语义；编码失败发生在打开目标之前，已有文件不变。底层写入失败可能留下部分输出，不提供原子替换、来源摘要检查、备份或自动重试；这些文件管理策略由调用方显式安排。多次写入之间没有跨文件事务，调用方自行处理部分成功。

```python
save = MainSave.read(path)
before = save.tostring()
save.deaths += 10
after = save.tostring()
show_diff(before, after)
save.write(path)

new_save = MainSave()
new_save.write(other_path)

buffer = BytesIO()
save.write(buffer)
encoded = buffer.getvalue()

# 已有文本／字节无需包装成文件流。
restored = MainSave.fromstring(encoded)

# 也可以直接写入 StringIO。
text_buffer = StringIO()
save.write(text_buffer)
assert text_buffer.getvalue() == after
```

无 SID 的原版记录以 VANILLA_SIDS 的固定 0–10 Area ID 索引。超出范围的 Level 保留为 CampaignProgress.unresolved_levels 的结构化值，显式 resolve(sid)、加入正确集合并移除未解析项后才能写出，不生成虚构 SID。数字身份规则仅用于原版分区与旧 CurrentSession，不套用到 Mod 分区或安全会话；活动会话无法确定身份时拒绝读取。

`MainSave()` 初始化完整的 11 个原版空章节，每章含三个空 side。主文件写出时，`vanilla.levels` 必须恰好按 `VANILLA_SIDS` 顺序包含全部原版章节；缺失、重排、额外 SID 或保留的未知原版 AreaStats 均拒绝写出，需调用方显式修复。不会在写出时自动补项、重排或丢弃未知记录。数字 ID 是原版编码边界根据确定的 SID／位置派生的协议字段：包括新建或替换的 Level，都输出正确的 `ID`；无 SID 的原版章节不因此补写 SID。此约束不改变通用 CampaignProgress 或 Mod 地图集的增删语义。

`SaveSlot(saves_dir, number)` 只保存编号和 Saves 目录位置；`main(*, strict=True, on_campaign_conflict=None, on_level_conflict=None)`、`backup(*, strict=True, on_campaign_conflict=None, on_level_conflict=None)`、`mod(SaveType, /, *args, **kwargs)` 一次性加载具体格式，不持有文件会话。ModSave 子类声明 mod_name，不用给槽位增加字段；mod() 定位文件后将额外参数转发给该类型的 read()，最终由 fromstring 接收解析选项，不传给构造器。main／backup 以及具体格式的 read 保留显式解析参数提示；动态 mod 选择入口目前只保留返回类型，不承诺检查任意第三方格式的全部参数。读取不存在的文件抛出 FileNotFoundError，其他 I/O 异常保持原生分类；内容错误保留来源信息，消歧回调异常原样传播。

`SavesDir` 是目录对象，普通构造器直接接受 Saves 目录，`from_game_dir()` 按游戏路径和 `EVEREST_SAVEPATH` 定位；只读 `path` 返回目录路径。容器协议属于只读 `slots: SaveSlots` 视图，而不是目录自身：长度为 `max(3, 最高普通编号 + 1)`，整数下标是编号而非压缩后的列表位置，拒绝负数和越界；`slots[...]` 访问不参与普通长度和迭代的 debug 编号 -1。切片按标准规则归一化边界，返回位置元组；`iter(filter_empty=False, reverse=False)` 可独立选择过滤空位和反向遍历，`__iter__()` 与 `__reversed__()` 共用该实现，每次遍历确定一次缓存范围。范围长度在首次使用时扫描并缓存，随后长度与整数下标查询不枚举目录；调用 `slots.invalidate_cache()` 仅清除缓存，不立即扫描，可重复调用；下次访问范围时重新发现，扫描失败直接抛错且不缓存失败结果。已持有视图在重新发现后观察新范围；文件存在性及内容不缓存，过滤空位仍查询当前文件，但只遍历缓存范围。不存在的目录仍显示三个空位置，权限或无效目录错误不隐藏。视图不支持赋值、插入、删除或文件写出。

`SaveSlot` 是普通位置对象，只读 `number` 与 `saves_dir: Path` 分别表示编号与所在保存目录，不表示主文件路径；相等与哈希使用同类型、编号和宿主 Path 比较，不解析为文件系统实际身份。槽位存在性只检查主文件，不解析内容；损坏文件和符号链接仍是已有槽位，权限错误不能当作空位，主文件位置是目录等错误类型时明确报错。孤立伴随文件只能通过选定编号显式加载。

`SavesDir.find_empty_slot(start: int = 0)` 接受非负整数起点，默认从 0 开始，从该编号开始向后寻找首个缺少主文件的槽位，不限制在当前 `slots` 范围内；伴随文件独存不占用编号。不填补起点之前的空位，不创建目录或文件，也不预留位置，目录未变化时重复调用可返回相同编号。取得返回的 `SaveSlot` 后，由调用方创建文档并通过已有路径函数显式写出。

```python
saves = SavesDir.from_game_dir(game_dir)
saves.slots.invalidate_cache()  # 清除范围缓存；下次访问时重新发现
for slot in saves.slots.iter(filter_empty=True):
    save = slot.main()
    print(slot.number, save.deaths)

slot = saves.find_empty_slot()
MainSave().write(save_slot_path(slot.saves_dir, slot.number))
```

下文写出示例使用 `berries.game.saves.paths` 的 `save_slot_path`、`mod_save_data_path` 和 `mod_save_path` 显式确定目标。业务层仍只组合内存对象，不隐式加载或提交。异常 `ProgressConflictError` 表达业务冲突，`UnsupportedProgressError` 表达无法安全处理的扩展；两者与结构错误 `BadProgressSave` 均继承 `ValueError`，不替代原生 I/O 异常。

### 独立 Mod 会话文件组

Everest 默认将各 Mod 的当前会话写到 `<编号>-modsession-<Mod名>.celeste`，debug 使用 `debug-modsession-<Mod名>.celeste`；它们属于整个活动会话，不按 SID 再分组。文件可能是文本或 binary，只需作为原始文件处理，不为 CommunalHelper 等 Mod 另建 Session 模型。Mod 覆写 Everest 读写方法并使用自定义位置的文件不在默认约定范围内。

```python
class SaveSlot:
    def mod_session_paths(self) -> dict[str, Path]: ...


def mod_session_path(saves_dir: Path, number: int, mod_name: str) -> Path: ...
def copy_mod_sessions(source: SaveSlot, target: SaveSlot) -> None: ...
def move_mod_sessions(source: SaveSlot, target: SaveSlot) -> None: ...
def clear_mod_sessions(slot: SaveSlot) -> None: ...
```

上述函数可从 `berries.game.saves` 导入，具体文件协调实现位于 `saves.mod_sessions`，不混入纯内存的 `saves.operations`。`mod_session_paths()` 每次扫描当前目录，按文件名顺序返回新字典，key 是原始 Mod 名称，value 是实际文件 Path；不读取内容，不要求主文件存在，不递归扫描，也不收集其他编号、临时文件或 `.bak` 等备份后缀。同名目录不当作文件，符号链接按文件条目保留，具体复制／移动行为沿用原生 API；缺失目录、权限等错误直接传播，不伪装为空组。文件名匹配采用宿主 Path 的大小写规则，Mod 名称不做 casefold。`mod_session_path()` 只定位默认文件，不读取或创建文件，Mod 名称须为单个宿主路径组件。

copy 使用 `shutil.copyfile`，只复制内容、不承诺保留元数据；move 使用 `shutil.move`，包括其跨文件系统的回退行为。两者按来源文件顺序逐项处理，随后删除目标独有的旧组文件，避免来源没有的 Mod 被错误恢复成目标旧会话；来源存在但组为空表示清空目标组，来源目录不存在则报错。需要时创建目标目录；同一物理目录的同编号转移拒绝，不能以目录别名对自身转移。clear 只逐项 unlink 所选组，不清空整个 Saves 目录，不递归删除，不改主文件、持久 modsave、Everest 备份或 Collab 文件。

这些是立即生效的普通文件操作，不自动判断本次迁移是否选中了活动会话；调用方必须在修改内存进度前记下会话身份，只有对应活动会话被复制／移动或丢弃时才调用。来源不存在的某个 Mod 文件保持缺失，让 Everest 按该 Mod 的默认行为初始化；Collab 保留会话中的附带载荷由进度操作协调，不能用这组当前会话文件代替。任何一步失败直接抛出原生异常，已完成的复制／移动／删除不回滚、不重试、不自动备份；原组和目标可能处于部分完成状态，也不保证多次文件操作与主文档写出的一致性。

扩展保障由使用方组合基础接口：先用 `mod_session_paths()` 获得精确文件清单，用 `mod_session_path()` 定位目标，再自行通过 shutil 等安排备份、自定义转移或恢复。默认入口不提供事务、锁、执行计划对象或自动保存主文档，不能把内存进度的预检保证理解为磁盘文件组原子性。

```python
from berries.game.saves import clear_mod_sessions, move_mod_sessions, save_slot_path
from berries.game.saves.operations import move_map_progress

source = source_slot.main()
target = target_slot.main()
# 在内存操作改变会话身份前记下两端状态。
source_current, target_current = source.current_map, target.current_map
move_map_progress(
    source,
    target,
    sid,
    side,
    mode='merge',
    poem_id=None,
    goldenberry_ids=goldenberry_ids,
    discard_current_session=True,
)
if source_current == (sid, side):
    move_mod_sessions(source_slot, target_slot)
elif target_current == (sid, side):
    # 没有来源活动会话，但本次清除了目标关联活动会话。
    clear_mod_sessions(target_slot)
# 两端均是无关活动会话时，不碰任何独立 modsession。
source.write(save_slot_path(source_slot.saves_dir, source_slot.number))
target.write(save_slot_path(target_slot.saves_dir, target_slot.number))
```

上述示例用于单 map 移动；level 按 SID、campaign 按最终 Campaign 名称判断所选活动会话，不能无条件搬运整个源槽位。copy 使用同样的身份选择并改调 `copy_mod_sessions`，不删除来源组；单存档 clear 仅在本次确实丢弃关联活动会话时调用 `clear_mod_sessions`。这是显式组合示例，不是磁盘事务：文件转移与随后任一次 write 都可能留下部分结果，使用方自行决定是否在执行前增加保障。涉及 Collab 时，另按第 10 节传入对应内存文档，并显式写出。

## 4. 主进度领域对象

领域名称统一采用 Side、Level、Campaign，对应游戏协议中的 Mode、Area、LevelSet；XML 字段名不变。`MapRef` 保存所属 Level 的数字 level_id、可选 level_sid 和 side，直接声明 XML 绑定；SideName 表达协议的 Normal／BSide／CSide 文本，与领域枚举 LevelSide 区分。LevelProgress 与 UnresolvedLevelProgress 的数字编号命名为 id，Session 中引用地图编号的字段保留 level_id，避免与会话自身身份混淆。`last_map_ref` 对应 LastArea，根字段 safe_last_map_ref 对应 LastArea_Safe，mods.last_map_ref 提供同一字段的投影；last_map 是 SID 与 side 投影。vanilla_session 对应供原版读取的 CurrentSession，safe_session 对应安全会话；公开 session 优先选择安全会话再回退原版，两份持久值均在模型中参与比较和复制。

```python
class MainSave(MainSaveData, Save):
    # 构造器接受模型字段；读取策略只传给 from_xml／fromstring／read。

    name: str
    time_played: Duration
    deaths: int
    total_goldenberries: int
    summit_gems: list[bool] | None
    assist_mode: bool
    variant_mode: bool
    version: str | None
    cheat_mode: bool
    assists: Assists
    theo_sister_name: str | None
    total_jumps: int
    total_wall_jumps: int
    total_dashes: int
    flags: set[str]
    revealed_chapter9: bool
    last_map_ref: MapRef

    @property
    def vanilla(self) -> VanillaProgressView: ...
    @property
    def mods(self) -> ModsProgressView: ...

    @staticmethod
    def install_campaigns(*changes: tuple[MainSave, CampaignProgress]) -> None: ...

    @property
    def last_save_text(self) -> str | None: ...

    @property
    def flat_levels(self) -> LevelIndex: ...
    @property
    def recycled_flat_levels(self) -> LevelIndex: ...
    @property
    def current_map(self) -> MapKey | None: ...
    @property
    def session(self) -> Session | None: ...
    @session.setter
    def session(self, value: Session | None) -> None: ...
    @property
    def needs_mod_recovery(self) -> bool: ...


class ModBackupSave(ModSaveData, Save):
    @property
    def mods(self) -> ModsProgressView: ...


class ModSaveData(XmlModel, FieldModel):
    # campaigns／recycled_campaigns 是私有 alias 构造输入的只读 property。
    campaigns: CampaignMembers
    recycled_campaigns: CampaignMembers
    safe_last_map_ref: MapRef | None
    safe_session: Session | None
    vanilla_entry: CampaignProgress | None

    @property
    def mods(self) -> ModsProgressView: ...

    def install_mod_progress(self, value: ModSaveData | ModsProgressView) -> None: ...


class CampaignProgress:
    @property
    def name(self) -> str: ...

    unlocked_levels: int
    total_strawberries: int
    poem: list[str]
    levels: LevelMembers
    unresolved_levels: list[UnresolvedLevelProgress]

    def reset(self) -> None: ...


class LevelProgress:
    def __init__(
        self,
        sid: str,
        maps: Iterable[MapProgress | None] | None = None,
        *,
        cassette: bool = False,
        id: int = 0,
    ) -> None: ...

    @property
    def sid(self) -> str: ...

    id: int
    cassette: bool
    maps: list[MapProgress | None]

    def reset(self) -> None: ...


class MapProgress:
    time_played: Duration
    deaths: int
    completed: bool
    single_run_completed: bool
    full_clear: bool
    heart: bool
    strawberries: set[MapEntityID]
    checkpoints: set[str]
    bests: PersonalBests
    total_strawberries: int  # 保留相对实体集合的历史偏移

    def reset(self) -> None: ...


class PersonalBests:
    time: Duration
    full_clear_time: Duration
    deaths: int
    dashes: int

    def reset(self) -> None: ...
```

下列字段表省略部分已建模字段，完整字段见相应类型与建模说明。`CampaignProgress.total_strawberries` 表示已保存的地图集累计数量，不是实体集合；`MapProgress.strawberries` 是具体 Map 已收集实例的集合，包含什么收集品须结合实体规则解释。磁带存放在 LevelProgress，不在各 Map 重复保存；移动业务按 A-side 进度处理。`unlocked_levels` 表示游戏保存的 Level 解锁进度值，XML 协议名仍为 `UnlockedAreas`，不改变其数值或联动规则。

Session 中表示房间的字段统一使用 room：`first_room`、`dashes_at_room_start`、`furthest_seen_room`、`room_flags` 分别对应原协议 `FirstLevel`、`DashesAtLevelStart`、`FurthestSeenLevel`、`LevelFlags`，其中 room_flags 保存已访问房间名。`level_id` 仍表示地图编号，不是房间。MapReadFields 的 `total_strawberries` 对应 `TotalStrawberries` 累计数量，与 MapProgress 的草莓实体集合 strawberries 区分；这些 Python 命名调整不改变 XML 协议。

main.session 优先安全会话，必要时回退旧会话。两份会话各自保留全部已建模值，只有安全会话相对于基准的修改同步到同身份旧会话。None 清除两份；替换身份时，确切原版 SID 写入两份，其他 SID 只写安全会话并清除旧会话。主文件会话加载时已完整解码，current_map 查询不解析 XML。

协调依据是原版的 `LevelProgress.GiveUp`／`LevelExit`、`OuiChapterPanel.Reset` 和 `SaveData.StartSession`，以及 Everest 的 `SaveData.AfterInitialize/BeforeSave` 和 `UserIO._SerializeModSave`。返回地图选择先把旧会话 `InArea` 置为 false 并保存，再在章节面板清除当前会话；进入另一张地图替换当前会话。Everest 将正常代码的当前会话重定向到安全字段，加载后清空旧字段，序列化前仅为原版地图复制到旧字段，序列化后再清空内存中的旧字段。因此两份字段并不是两张地图的历史会话槽位。此结论来自 `.pist/references/Everest` 的 `790e3a54112c5a18f12e51b338ddeac2c6bfcd8e` 及本地原版反编译参考，不包含第三方 Mod 自行修改退出流程的行为。

PersonalBests.time／full_clear_time 为 Duration，deaths／dashes 为 int，保存原始值且默认零。正时间表示已建立的最佳时间，最佳死亡／冲刺由 single_run_completed 标识，合法零成绩不是缺失。标志与存储值分开：未成立成绩不抹成 None，非正原始时间也不丢弃；写出检查范围，不暗改标志或原值。

时长统一为 Duration，保留有符号精度，不自动截断、舍入或按计时帧对齐，协议 ticks 范围与精度在写出边界检查。宽松读取采用已核实的构造默认值，非法已知文本不套默认；输出建立规范化已知字段，不保留原始数值拼写或编码片段。

### 严格加载与归属不匹配

`strict` 是 `MainSave`、`ModBackupSave` 的关键字参数，默认 `True`，槽位的 `main()`、`backup()` 原样传递。它属于进度文件的领域校验，不放到通用 `Save`、`ModSave` 或 `slot.mod()` 上，也不因此给所有格式附加同一套开关。

正常存档中 `LevelProgress.maps` 的前三个位置分别表示 A/B/C。正常原版／Everest 保存路径通过 `XmlSerializer` 写出非 null 的三项 `Modes`，没有该字段的条件省略规则；严格模式因此拒绝省略整个字段。宽松模式沿用游戏读取时的 `AreaStats` 构造器语义，创建三个独立的空 `MapProgress()`，而非三个 None；这些对象的可变收集集合也彼此独立。写出时会显式生成三项 `Modes`，不承诺保留该字段原先省略的写法。

显式存在的 `Modes` 数量不是三项或存在 null 项时，严格模式抛出 `BadProgressSave`；宽松模式逐项读取所有位置，保留实际长度，不补零、不补 None、不重排。只有显式 null 子项才表示为 None。因此 `<Modes/>` 得到空 list，一项输入只保留一项，不等同于省略字段得到的三个空进度。索引 ≥ 3 的异常项同样可观察；缺少的位置按普通 list 行为产生 IndexError。构造器保留传入位置，三项约束统一在写出边界校验。

`<Modes xsi:nil="true"/>`（包括等价的 `xsi:nil="1"`）同样不是正常游戏写出形态，严格模式拒绝；宽松模式按已验证的反序列化行为生成三个独立空进度，不按空数组处理。即使 nil 容器包含 `AreaModeStats`，也按序列化器规则忽略这些子项，不解析其中字段，不因其计数或时长非法而报错，不将它们提升为有效进度；nil 容器本身的未知属性及成员仍遵循 unknown 策略。写出从领域对象建立三项有效进度，不重新激活被忽略的内容，也不保留注释、处理指令或未知扩展。`xsi:nil="false"`／`"0"` 不启用默认值，仍按实际子项读取。

`LevelProgress` 与 Campaign 一样不实现集合协议：`level.maps` 直接公开普通 `list[MapProgress | None]`。调用方可以索引、切片、替换、追加、删除、重排或整体赋值，承担修复决策；这些修改不自动调整 Campaign 或存档累计。`.copy()`／`copy.copy(level.maps)` 遵循普通 list 的浅复制语义，位置存储独立、成员仍共享引用。

需要丢弃多余项时显式执行 `del level.maps[3:]`；需要补足位置时自行使用 `append()`／`extend()`，显式 null 项通过位置赋值替换，不由库推断修复方案。已取得的进度引用不因删除而失效。Level 不提供 `.copy()` 或定制 `__copy__`，`copy.copy(level)` 采用默认浅复制、共享 maps 列表；需要隔离位置变更时显式复制 maps，需要递归隔离时使用 deepcopy。写出要求恰好三个非 None 条目，未修复时抛出 `BadProgressSave`，不自动截断或补齐；输出只编码当前领域值；未知字段已在读取边界处理，不恢复注释或处理指令。

扁平 Map 查询与 Pist 浏览投影只暴露 A/B/C 并跳过 None，不为多余项伪造 LevelSide，也不因此删除原始 Level 中的异常项。涉及缺项的编辑／转移必须报告 `BadProgressSave`，不能将其视为零进度；保留多余项的结果同样不能通过写出校验。

```python
save = slot.main(strict=False)
level = save.flat_levels[sid]
extras = level.maps[3:]  # 检查后再决定是否丢弃。
del level.maps[3:]
level.maps.extend(MapProgress() for _ in range(3 - len(level.maps)))
for i, progress in enumerate(level.maps):
    if progress is None:
        level.maps[i] = MapProgress()
save.write(output_path)  # 显式写出；截断本身不改动文件或累计。
```

`LevelSide` 是 `IntEnum`，A/B/C 的值分别为 0/1/2，直接作为序列位置使用；`level.maps[LevelSide.B]` 与 `level.maps[1]` 是同一种索引语义，列表下标只接受整数／切片。迭代和成员测试均针对 `MapProgress | None` 值，不以 side 判断成员。字符表示通过 `side.name` 或 `LevelSide['B']` 显式编解码，数字表示通过 `int(side)` 或 `LevelSide(1)` 使用。地图路径、诗句／Ender's Blender 键及 Pist 的记录／大厅配置使用 A/B/C 文本。A 的数值为零，不能用 truthiness 判断是否提供了 side。

```python
class BadProgressSave(ValueError):
    """已有进度结构、归属无效，或存在未解决的重复身份。"""

    ...
```

加载已有进度时，根据可确定的最终 SID 推导 CampaignProgress 名称，并与记录实际所在的分区比较；发现不匹配且 `strict=True` 时，在构造返回前抛出 `BadProgressSave`，不等到某次 Map 查询或保存才发现。校验包括活动地图集和回收区，不需要当前安装 Catalog，也不模拟 Everest 的 `AfterInitialize()`。只检查归属所需的结构与身份，不为此提前解析所有会话或统计字段。缺少 SID 的原版记录仍走显式数字 ID 映射规则，不凭空推断最终 SID 或把“未能确定身份”冒充归属不匹配。

异常信息须包含可用的文件／流来源、相关 SID、实际 CampaignProgress 名称和按 SID 推导的预期名称，便于用户定位；路径输入保留路径上下文，外部流没有文件名时不能捏造路径。`BadProgressSave` 属于 `ValueError`，不是 I/O 错误；`FileNotFoundError`、`PermissionError` 等继续按原生分类传播。

`strict=False` 用于检查和修复已有异常数据：保留原始分区与记录，不自动搬迁、删除、重排归属或补空白进度，不会隐式写回。普通分层访问应仍能观察到记录实际所在的 CampaignProgress，不能先按 SID 重新分组再把异常隐藏掉。它不放宽 XML/YAML 语法和其他独立解析要求，也不豁免写出边界的校验；内存集合的新写入拒绝新的归属不匹配，宽松加载的旧错放记录仍可观察和移除，修复和提交仍是另外的显式操作。

```python
try:
    save = slot.main()
    print(save.name)
except BadProgressSave as error:
    print(error)
    save = slot.main(strict=False)
    for campaign in (save.vanilla, *save.mods.campaigns.values()):
        for level in campaign.levels.values():
            print(campaign.name, level.sid)  # 观察原始归属，不自动修复。

# 直接打开与槽位入口使用相同的契约。
backup = ModBackupSave.read(backup_path, strict=False)
for campaign in backup.mods.recycled_campaigns.values():
    print(campaign.name)
```

参考源码中，Everest 的加载流程按 SID 修正数字 ID、排序和逐位置补缺，并非跨 LevelSet 搬迁修复。在错放记录排到合法记录之前等条件下，新建空白条目可能将合法旧记录挤到尾部，后续保存裁剪可能删除它们。这里只把该算法风险作为严格加载的动机，不宣称已经用游戏异常存档实测；严格加载失败不得采用相同的破坏性“修复”。依据：[AfterInitialize 的排序和补缺](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L474)、[BeforeSave 的裁剪](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L608)。

### 非三项 Modes 的游戏行为核对（2026-10-20）

证据来自本地原版反编译 `Celeste/AreaStats.cs`、`SaveData.AfterInitialize()`、`Session` 构造器，以及 Everest `790e3a54112c5a18f12e51b338ddeac2c6bfcd8e` 的 `Patches/AreaStats.cs`、`SaveData.cs` 和 `UserIO.cs`。另用隔离的 .NET 10.0.11 小样本复现相同数组字段、构造器和索引／克隆循环；这是机制验证，不是启动真实游戏的端到端验证，未修改真实存档。

| Modes 情况 | 原版路径 | Everest 路径 | 其他影响 |
| --- | --- | --- | --- |
| 长度 0 | `AfterInitialize()` 访问 `Modes[0]` 越界 | 活动地图同样访问 `Modes[0]` 越界 | 反序列化成功不等于能初始化 |
| 长度 1 或 2，成员非 null | `CleanCheckpoints()` 按 AreaMode 的三个值访问，对标准原版三槽地图越界 | 清理改按 `Modes.Length` 遍历，可能通过初始化；没有自动补齐 | 后续若访问缺少的 B/C 进度仍越界，不能据此认定整个游戏流程安全 |
| 长度大于 3，成员非 null | 初始化未必报错，多余项不会在此被裁剪 | `CleanCheckpoints()` 遍历实际长度，`HasMode()` 对不存在的 side 返回 false，没有裁剪 | `AreaStats.Clone()` 仍创建三项目标却按源长度复制，第四项导致越界；常规新 Session 构造会调用 Clone；按实际长度的累计统计也会计入多余项 |
| 数组中含 null | 被遍历时解引用失败 | 清理同样未跳过 null | Clone 和部分累计统计也会空引用 |

`AreaStats` 的无参构造器先创建三个非 null 默认项。小样本中，省略整个 Modes 字段保留默认三项，显式 `<Modes/>` 则得到零项；两者不能混为一谈。相同 .NET 样本的 `<Modes xsi:nil="true"/>` 也保留构造默认值，但未据此推广为其他游戏运行时的兼容保证。`Clone()` 对短源会返回一个补足默认项的副本，并不修改原存档记录；不存在加载时统一修复 Modes 长度的逻辑。回收区或未安装地图的记录可能暂不经过上述活动地图初始化，重新使用时风险仍在。

nil 包含子项的补充核对：原版／Everest 的 `UserIO` 读取委托给 `XmlSerializer`；[.NET 10 的 `XmlSerializationReader.ReadNull()`](https://github.com/dotnet/runtime/blob/v10.0.0/src/libraries/System.Private.Xml/src/System/Xml/Serialization/XmlSerializationReader.cs#L795) 在 nil 成立时消费容器内容而不解码其进度字段。等价字段和构造器的 .NET 10.0.11 样本确认，nil 为 true／1 时，即使内含 Deaths=9 或 Deaths=invalid 的进度子项，结果仍是三个零死亡默认对象，所模拟的初始化与 Clone 均成功；nil=false 时才读取子项。此次验证没有启动真实游戏，不宣称覆盖其他运行时的所有反序列化差异。宽松解析据此不额外拒绝 nil 子项，写出则移除 nil 并输出当前有效进度，不能重新激活被游戏忽略的旧字段。

上述证据用于区分“可检查的异常输入”和“可安全写回的三项结构”：宽松读取保留异常位置以供修复，不代表游戏能安全使用这些数据；显式列表修复完成修复后，才允许按三项结构写出。未启动真实游戏验证异常输入的完整生命周期。

### 重复身份的逐项处理

`MainSave`、`ModBackupSave` 及槽位的 `main()`、`backup()` 分别接受 `on_campaign_conflict: CampaignConflictResolver | None = None` 与 `on_level_conflict: LevelConflictResolver | None = None`。默认遇到同一集合内的重复 CampaignProgress 名称或同一 CampaignProgress 内的重复 SID 时抛出 `BadProgressSave`，包含来源、集合或 CampaignProgress 及重复身份。原版数字 ID 映射后得到的重复 SID 也遵循此规则。活动区与回收区是独立集合，两边同名不会触发回调；活动 Celeste 占位项单独处理同名冲突；回收区 Celeste 项使用普通 CampaignProgress 消歧并保留 RECYCLED 来源。

严格模式拒绝重复，不调用回调。`strict=False` 且显式提供回调时，每读到一个重复项就调用对应的 `on_campaign_conflict(old, new)` 或 `on_level_conflict(old, new)`：Campaign 冲突接收两个 `CampaignConflictContext`，Level 冲突接收两个 `LevelConflictContext`，分别携带当前集合里保留的结果和这次新读到的对象；返回的进度对象替换当前项，后续重复继续与该结果比较。不额外维护候选集合，不自动模拟游戏的排序、补缺或尾部裁剪。

`CampaignProgress.key` 与 `LevelProgress.key` 是统一的只读身份入口，分别返回 `name` 与 `sid`，不另存一份身份。冲突回调的两个参数分别表示现有候选和新读入候选的上下文：`CampaignConflictContext` 保存 `progress: CampaignProgress` 和 `partition`；`LevelConflictContext` 保存 `progress: LevelProgress`、`partition` 与实际存放位置 `campaign`，该位置可能与 SID 推导的归属不同。两个上下文类相互独立，不使用泛型或继承，不提供 key 转发属性，身份通过 `context.progress.key` 访问。上下文的 frozen dataclass 只固定字段绑定，其中的领域对象仍可编辑，不是独立快照。两种上下文及 `ProgressPartition` 均可从 `berries.game.saves` 导入。

`partition` 使用字符串枚举 `ProgressPartition`：`VANILLA`、`MODS`、`RECYCLED` 的值分别为 `vanilla`、`mods`、`recycled`。它表达存储来源：根部原版 Areas 为 VANILLA，LevelSets 内的条目包括 Celeste 均为 MODS，LevelSetRecycleBin 内的同名历史为 RECYCLED。解析入口显式提供分区，进度对象不保存它，也不根据 Campaign 名称推导。合并检查原版与活动 Mod 时，两份 LevelConflictContext 分别保留对应候选的来源；后续冲突中的已解决结果按当前归属提供上下文。单 Campaign 的 resolve_level() 直接使用显式分区；跨 Campaign 的加载扫描自行构造两个上下文、调用回调并复用结果身份校验，不增加来源包装或名称分类函数。

```python
type CampaignConflictResolver = Callable[
    [CampaignConflictContext, CampaignConflictContext], CampaignProgress
]
type LevelConflictResolver = Callable[[LevelConflictContext, LevelConflictContext], LevelProgress]


def keep_campaign(old: CampaignConflictContext, new: CampaignConflictContext) -> CampaignProgress:
    return new.progress


def keep_level(old: LevelConflictContext, new: LevelConflictContext) -> LevelProgress:
    return new.progress


save = slot.main(strict=False, on_campaign_conflict=keep_campaign, on_level_conflict=keep_level)
print(save.vanilla.name)
save.write(save_slot_path(slot.saves_dir, slot.number))
```

回调可以返回旧项、新项或自行合并的同类对象，但必须保持原身份；返回错误类型、None 或不同身份会抛出 `BadProgressSave`。回调自身抛出的异常原样传播，不被当作文件解析错误重新包装，也不提交文件。层级按读取顺序解析，因此新 CampaignProgress 内的 SID 重复先处理，再处理该 CampaignProgress 的名称重复。回调可能丢弃数据，应由调用方自行记录所需诊断；显式写出时仅编码消歧后的对象，不把舍弃项作为未知 XML 残留，已有文件集合的顺序保留首次出现的位置。

回调结果校验只要求类型和身份一致，不要求在消歧时一并修复所有结构问题。宽松读取允许保留的异常 Modes 长度、缺失 side 或 Campaign 中无关的错放成员，在选择或合并候选后仍可保留，供调用方加载后显式修复；写出时仍校验三个非空 side 及成员归属，未修复则拒绝编码，不打开或改写目标文件。区内唯一性与下述跨 Campaign 冲突的归属协调仍在加载时完成，不能由此绕过。

候选上下文不在领域对象上保存来源副本。两个解析参数独立处理 Campaign 名称与 Level SID；后者包括同一区内跨 Campaign 的 SID 冲突。未提供相应回调时该类冲突仍报错，不会调用另一类回调；严格模式忽略两个回调。回调返回同身份对象后，加载阶段安装结果并移除另一重复记录；跨 Campaign 合并结果按 SID 放入已有正确 Campaign，无该父结构则报错，不隐藏错放记录。活动区和回收区独立，跨区同名不触发回调。

所有重复必须在加载返回前解决，索引查询不会重复触发回调。唯一但错放的宽松输入仍可观察，写出前必须显式修复。

跨 Campaign 的 Level SID 冲突先扫描、后统一安装结果，避免把合并结果写入尚未扫描的 Campaign 后覆盖未读候选或重复处理。回调可以选择或合并进度值，但不得在扫描过程中增删正在处理的 Campaign／Level 集合成员；成员归属由加载器在扫描完成后协调。宽松读取中没有发生冲突的错放项保持原状，供调用方检查和修复。

### 回收区的外部语义

Everest 的 `LevelSetRecycleBin` 保存当前安装环境中已不存在的整个地图集进度，不是用户删除记录的垃圾桶。`AfterInitialize()` 在发现地图集消失时将其移入，在重新发现同名地图集时恢复；单个地图消失走另一套条目清理，不单独进入这个分区。`recycled_campaigns` 表示文件中的存储分区，普通删除不自动回收，普通读取不模拟当前安装环境协调。

源码依据：[Everest SaveData.AfterInitialize](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L425)。备份快照仍包含回收区及其他相关字段；原版在 `LevelSets` 中的附加元数据也必须保留，不能因提供 `vanilla` 而覆盖或重复持有原版地图进度。

## 5. 进度集合 API

CampaignMembers、LevelMembers 和 LevelIndex 是组合 KeyedDict 的领域 Mapping，不继承其可绕行的写入方法。按 key 迭代，成员判断针对 key；get 支持 default，keys／values／items 返回活视图。add 覆盖、adddefault 保留、replace 要求存在；update、del、pop、popitem、clear 同样经过协调。索引直接引用实际 Level，不复制进度或在查询时重建结果。

```python
campaign = save.mods.campaigns['Pack']
level = LevelProgress('Pack/New')
campaign.levels.add(level)
assert save.flat_levels[level.sid] is level
save.flat_levels.replace(LevelProgress(level.sid))
assert campaign.levels[level.sid] is save.flat_levels[level.sid]
del campaign.levels[level.sid]
assert level.sid not in save.flat_levels

active = save.flat_levels[sid]
recycled = save.recycled_flat_levels.get(sid)
progress = active.maps[LevelSide.B]
if progress is not None:
    progress.completed = False
```

扁平新增由 SID 推导父 Campaign，父不存在时报 KeyError，不创建默认父对象。campaign.levels 和 vanilla.levels 可整体替换，但必须预检并同步索引。mods.campaigns／recycled_campaigns 的绑定不能整体赋值，修改成员使用集合入口；恢复等整体业务操作使用 save.install_mod_progress(snapshot)，批量安装准备好的 Campaign 使用 install_campaigns。vanilla／mods 视图本身不可覆盖，安装完成后原视图继续投影新字段。移除最后一个 Level 不隐式删除 Campaign，移除成员不清空被移除对象。

LevelProgress 是领域对象，其 maps 成员为普通 list，允许观察、修复异常长度和 None。直接使用 level.maps[side]，由调用方处理 IndexError 或 None；写出要求三个非空标准位置。分区索引按 SID 定位 Level，side 由列表位置确定，冲突以 Level SID 为单位处理。

`LevelProgress.from_xml(root, *, vanilla=False, strict=True, unknown='error', path='AreaStats')` 从元素自身读取身份，不接受调用方提供 SID。只有 vanilla=True 才允许以自身数字 ID 查询固定原版表，显式 SID 始终优先。身份无法解析时抛出 `UnresolvedLevelIdentityError`（BadProgressSave 子类）；Campaign 的宽松读取只将这类失败保留为 UnresolvedLevelProgress，不掩盖其他解析错误。Session 的 OldStats 通过注解复用此入口，使用 vanilla=True 解码自身身份，再由 Session.model_post_init 检查其 SID 与会话 SID 一致；写出也复核这一关系。

未知原版数字 ID 不生成虚构身份；Level 通过 unresolved_levels 提供结构化修复，无法确定身份的主会话拒绝读取。重复身份在加载时消歧，输出不保留舍弃项，两区允许同名独立历史。容器及复制契约见[存档容器与成员关系](saves-containers.md)。

## 6. 会话与 CollabUtils2

### 会话浮点字段的有限值约束

出生点 X／Y、DarkRoomAlpha、LightingAlphaAdd、BloomBaseAdd、音频 Music／Ambience 参数及 AmbienceVolume 使用 XmlFiniteSingle 注解：普通构造和严格读取拒绝 NaN、正负无穷及转换后溢出为无穷的值。宽松 XML 读取保留这些值，允许用户检查和修复，但未修复前拒绝写出；不自动夹取、归零或替换出生点。允许为 null 的出生点及环境音量仍保留其独立语义。

依据是游戏消费路径，而非 XML 是否接受 NaN：Level.LoadLevel 将已有出生点交给 Player 构造器进行坐标转换；光照值进入 LightingRenderer 的 Clamp／颜色计算，而 Clamp 不消除 NaN；Bloom.Base 的 NaN 会使 Base < 1 分支不执行；音频值直接交给 FMOD，游戏不检查返回错误，FMOD 的 ERR_INVALID_FLOAT 包括 NaN／Inf。具体异常表现可能依赖运行时、图形及音频后端，上述为源码链分析，未进行真实游戏加载实验。

Everest Slider 是开放的 Mod 数值存储，没有足够信息统一规定有限性，暂时继续允许 NaN／Inf 读写；底层 Single codec 也继续支持完整 IEEE32 特殊值。字段语义约束与底层词法／精度编解码分开，不能由后者接受 NaN 推断前者安全，也不借此扩展 Session 的相等语义。


```python
class Session(XmlModel):
    sid: str  # 只读；构造时可由原版数字 ID 补齐
    side: LevelSide  # 必填，冻结

    @classmethod
    def from_xml(
        cls,
        root: etree.Element,
        *,
        strict: bool = False,
        unknown: UnknownFieldPolicy = 'error',
        path: str | None = None,
    ) -> Self: ...

    in_level: bool  # XML InArea；缺失时为 True，可编辑，与身份独立
    summit_gems: list[bool]  # 缺失／nil 容器采用六项 False 默认值
    room: str
    respawn: tuple[float, float] | None
    time_played: Duration
    deaths: int
    room_deaths: int
    level_id: int
    audio: AudioState
    inventory: PlayerInventory
    flags: set[str]
    room_flags: set[str]
    strawberries: set[MapEntityID]
    do_not_load: set[MapEntityID]
    keys: set[MapEntityID]
    counters: list[SessionCounter]
    old_stats: LevelProgress | None
    sliders: dict[str, float]
    first_room: bool
    dark_room_alpha: float
    started_from_beginning: bool
    dashes: int
    dashes_at_room_start: int
    start_checkpoint: str | None
    cassette: bool
    heart: bool
    dreaming: bool
    color_grade: str | None
    lighting_alpha_add: float
    bloom_base_add: float
    core_mode: Literal['None', 'Hot', 'Cold']
    grabbed_golden: bool
    hit_checkpoint: bool
    unlocked_c_side: bool
    furthest_seen_room: str | None
    beat_best_time: bool
    restarted_from_golden: bool

    def validate_map_totals(
        self,
        *,
        time_played: Duration | None,
        deaths: int | None,
    ) -> None: ...


class SessionInput:
    def __init__(
        self,
        sid: str,
        orig_xml: str,
        *,
        unknown: UnknownFieldPolicy = 'error',
    ) -> None: ...

    @property
    def sid(self) -> str: ...

    @property
    def orig_xml(self) -> str: ...

    @property
    def session(self) -> Session: ...

    @session.deleter
    def session(self) -> None: ...

    def tostring(self) -> str: ...


class CollabUtils2Save(ModSave):
    mod_name = 'CollabUtils2'

    sessions: KeyedDict[str, SessionInput]
    mod_sessions: dict[str, dict[str, str]]
    mod_sessions_binary: dict[str, dict[str, str]]
    speed_berry_pbs: dict[str, Duration]
    opened_mini_heart_doors: set[str]
    combined_rainbow_berries: set[str]
    completed_warp_pedestals: set[str]
    learned_tech: dict[str, set[str]]
    speedberry_option_message_shown: bool
    visited_lobby_positions: dict[str, str]
    reveal_map: bool
    pause_visiting_points: bool
    show_visited_points: bool
```

sessions 是保存并返回大厅的保留会话，不作为整个 Collab 文件的默认 get()。mod_sessions／mod_sessions_binary 保存 SID → Mod metadata 名 → 原文／base64 字符串，不解释各 Mod 私有载荷。大厅访问位置、速度莓提示及显示开关也已建模；未知字段按外层及 Session 的 error／warn 策略处理。

`sessions` 直接使用 `KeyedDict[str, SessionInput]`，按已知 SID 索引；`[sid]`、`get(sid)` 返回 SessionInput，`sid in sessions` 只检查成员，迭代返回 SID，遍历输入使用 `.values()`。每个 SID 只能保存一个会话，`add()` 覆盖已有项，`adddefault()` 保留已有项，删除使用 `del sessions[sid]` 或 `pop(sid)`。新增 XML 输入使用 `sessions.add(SessionInput(sid, orig_xml))`。是否进行中，取得 `.session` 后检查 `session.in_level and session.side is side`。InArea=false 不清除 SID／side，缺失 InArea 按游戏构造器默认 true，nil 不构成 Session。字典查找、遍历和集合浅复制均不触发 XML 解析。

Session 支持正常的 Pydantic keyword 构造，side、room、time_played、deaths、room_deaths 必填；身份需要提供 sid，或显式提供原版 level_id。sid／level_id 的构造输入先保留缺失，model_post_init 确定身份后才补数字 ID 的默认 0，不依赖字段声明顺序或出现集合；其余字段按模型声明提供默认值。成功构造后的 sid 始终为 str，使用只读 property，赋值或删除抛出 AttributeError；level_id 提供 int getter 和可编辑 setter，side 仍是不能赋值或删除的冻结字段。Session 不重写 from_xml()，直接复用 XmlModel 的 Element 入口；读取时临时传递 XmlReadContext，model_post_init 确认只有 CurrentSession 可根据非空数字 ID 补足缺失 SID，并检查 OldStats 身份和当前会话结构，再建立基准。解析全部已知字段后走相同模型校验和初始化流程，不保留树或 XML 快照。构造不根据 Catalog 补齐游戏运行状态；写出仍检查范围及 OldStats 三项结构。旧 CurrentSession 可用内置原版数字表，持有文本／ElementTree 的调用方先解析／取根元素。未知扩展、注释、PI、CDATA 词法不属于保真契约。

```python
session = Session(
    sid='Pack/Map',
    side=LevelSide.A,
    room='start',
    time_played=Duration(),
    deaths=0,
    room_deaths=0,
    old_stats=LevelProgress('Pack/Map'),
)
assert not any(session.iter_changed_fields())
```

会话嵌入主文件／Mod 备份时，由 XML 会话编码层适配目标协议。原版 `CurrentSession` 按确切的原版 SID 补全／校正数字 `Area.ID`；安全会话确保 `Area.SID` 存在，并将 SID 属性放在保留的数字 ID 之后，遵循 Everest 的 SID setter 解析顺序。从纯原版会话转入安全会话时，不能只改根标签而保留缺失 SID。通过 `MainSave.session` 替换身份时，仅在 SID 确实属于 `VANILLA_SIDS` 时创建原版副本，不能只凭 `Celeste/` 前缀判断；这不依赖会话来自原版、Everest 还是其他容器。仅编辑已有 legacy 会话不凭空创建 safe 会话。适配不修改来源 Session 或其扩展数据；身份转换只适配已有会话，不初始化新的游戏会话。

`SessionInput` 位于 `saves.session_input`，SID 和 `orig_xml` 只读，保证其集合键与原文稳定；公开 `.session` 是只读 property，内部 `_session` 使用 cached_property，首次访问时完整解析并校验 Session，同时核对 SID。成功后缓存，异常不缓存；不允许给 `.session` 赋值，但允许修改其领域字段。`del input.session` 直接删除内部缓存，丢弃其内存编辑；下次访问重新解析 `orig_xml`。尚未求值、求值失败或重复删除时均抛 AttributeError，不自动求值或静默忽略。写出时检查所有保留会话：未求值的输入临时解析并检查 SID 与必要结构，不填充惰性缓存；已有缓存则编码当前 Session。解析失败或缺少必要 OldStats/Modes 时拒绝写出。清除缓存会放弃缓存中的编辑，恢复原始输入。集合浅复制共享 SessionInput；该类型只负责已有 XML 的延迟解析和编辑。

Session.iter_changed_fields() 返回 Iterator[str]，按遍历时的当前值逐项与独立读取基准比较并产生变更字段名；如需判空可用 any()，如需保留结果则显式收集。比较范围由字段元数据统一决定，包括身份、音频、背包、OldStats、Counters 和 Sliders；compare=False 的基准、缓存或附加值不参与比较。普通有限值的同值赋值、改后恢复不算修改；不为异常 NaN 数据承诺稳定等价，基准只用于双会话协调，不是最小文本 diff 服务。输出从当前完整值建立新元素，坐标按 float32 编码，不回传原始文本或恢复未知扩展。

会话及相关附加 Mod 会话的跨存档移动不是简单移走一个 `Session` 就算完成；相关 API 和清理策略实施前单独确定。源码依据：[CollabSaveData](https://github.com/EverestAPI/CelesteCollabUtils2/blob/1064db99b8820195eac0e23460fba73ac09f923e/CollabSaveData.cs)，对应本地 `.pist/references/CollabUtils2/CollabSaveData.cs` 的字段定义。

```python
main = slot.main()
collab = slot.mod(CollabUtils2Save)
pending = collab.sessions.get(sid)
if main.current_map == (sid, side):
    in_progress = True
elif pending is None:
    in_progress = False
else:
    session = pending.session
    in_progress = session.in_level and session.side is side

collab = slot.mod(CollabUtils2Save)
session = collab.sessions[sid].session
if session.side is not side:
    raise ValueError('The retained session does not match the requested side.')
session.room = 'room-12'
session.time_played = Duration.from_seconds(180)
session.deaths = 42
session.room_deaths = 0
collab.write(mod_save_path(slot.saves_dir, slot.number, CollabUtils2Save.mod_name))
```

这段补录只修改保留会话，不暗中调整地图、全局累计或 Blender 统计；主文件当前会话通过 `main.session` 使用相同字段。未知实体／会话标志和收集品恢复不能从录像估计量自动推导。

## 7. Ender's Blender

```python
class EndersBlenderSave(ModSave):
    mod_name = 'EndersBlender'

    first_clear: dict[str, RoomStats]
    last_saved: dict[str, RoomStats]
    room_names: dict[str, dict[str, str]]
    room_colors: dict[str, dict[str, int]]


class RoomStats:
    room_order: list[str]
    room_deaths: dict[str, int]
    room_times: dict[str, Duration]
    room_rta_times: dict[str, Duration]
    room_strawberries: dict[str, int]
    pause_flags: dict[str, bool]

    def reset(self) -> None: ...
```

`first_clear` 与 `last_saved` 并列，不提供默认代表初见统计的 `EndersBlenderSave.get()`。源码 `latestSession` 已注明实际是“上次保存”，不是当前最新会话，因此公共命名用 `last_saved`。字段事实依据 [EndHelperModuleSaveData](https://github.com/EnderallyGolem/Enders-Blender/blob/d8381238e6b10e37a9225ef2b7ecbe4f1048ea8c/Source/EndHelperModuleSaveData.cs)，实际读取和写出规则还需结合 `RoomStatisticsDisplayer.cs` 等源码确认，不根据字段名推断完整算法。

`room_order`、死亡、游戏用时、RTA 用时、收集品和暂停标志可以分别缺项，不引入要求每个房间都有完整字段的模型。`pause_flags` 可以包含并非普通房间名的特殊键。写入一个字典不会暗中改写 `room_order` 或其他统计，修改游戏用时不推测 RTA 用时。顶层 `room_names`／`room_colors` 使用原始地图协议键，名称集合的顺序具有保留意义，不能因普通映射接口丢掉顺序。

first_clear 和 last_saved 均为普通 dict[str, RoomStats]，只存实际领域值，按原始协议键查询和赋值。room_key(sid, side) 显式编码：A 使用 SID，B／C 分别追加 _B／_C；协议键不可反解为唯一身份。查询、覆盖、删除和复制均使用标准 dict 接口。

加载时转换全部已建模统计，非法值立即报错且不变成零；关联缺项仍可加载后修复，写出检查已证实的消费约束。名称与颜色均已建模，未知字段默认拒绝或显式 warn 后丢弃，已知字段的显式 tag 保存为稀疏元数据。非法值修复入口另行设计，不把错误值或解码器混入领域字典。

```python
key = room_key('Example', LevelSide.B)
stats = blender.first_clear.get(key)
if stats is not None:
    blender.first_clear[key] = deepcopy(stats)
    del blender.first_clear[key]
    blender.first_clear[key] = stats
```

### 地图预览用法

以下保留了当前 Pist 的用途：打开预览时加载一次，然后在预览切换地图时复用查询入口，不每次重新读取文件，也不把该用途当成整个文件的默认语义。

```python
blender = slot.mod(EndersBlenderSave)
stats = blender.first_clear.get(room_key(sid, side))
await MapPreview(
    active_map,
    layout,
    first_clear_rooms=() if stats is None else stats.room_order,
    first_clear_lookup=lambda sid, side: blender.first_clear.get(room_key(sid, side)),
    # 其余预览参数省略。
).preview()
```

实际迁移仍需保留“文件缺失可不显示统计，内容损坏或权限失败给出可见警告后继续预览”的现有工作流。上例仅展示成功路径，不代表删除异常处理。预览的类型边界应与新对象同步，不为适配旧 `FirstClearStats` 而维护镜像数据。

## 8. 使用示例：复制并裁剪地图集

```python
from copy import deepcopy

saves = SavesDir.from_game_dir(game_dir)

if not saves.slots[1] and saves.slots[2]:
    target = MainSave()
    source = saves.slots[2].main()
    target.install_campaigns((target, deepcopy(source.vanilla.snapshot())))

    campaign = source.mods.campaigns.get('Cdog/ExtendedCSides')
    if campaign is not None:
        campaign = deepcopy(campaign)
        levels = list(campaign.levels.values())
        for level in levels[6:]:
            level.reset()

        a_side, b_side, c_side = levels[5].maps
        assert b_side is not None and c_side is not None
        c_side.reset()
        b_side.completed = False
        b_side.strawberries.clear()
        target.mods.campaigns.add(campaign)
    target.write(save_slot_path(saves.slots[1].saves_dir, saves.slots[1].number))
```

这是低层对象编辑示例；假定选中 LevelProgress 存在三个 Map。赋值采用引用语义，所以需要独立编辑的地方明确 `deepcopy()`。`target.vanilla` 替换后应立即反映到已取得的 `target.flat_levels`；`add()` 保留引用，不暗中复制或搬走原对象。

示例不自动更新全局累计或地图集已保存的收集累计，不能将其直接当作“累计一定一致”的完整转移流程。需要此保证时使用显式业务操作；如何对部分裁剪后数据建立一致累计仍需按已支持字段逐项确定，不引入无条件重算全部游戏统计的接口。

## 9. 使用示例：校正地图累计与全局累计

```python
def correct_map_progress(
    save: MainSave,
    sid: str,
    side: LevelSide,
    *,
    time_played: Duration | None = None,
    deaths: int | None = None,
) -> tuple[Duration, int]:
    """更新目标 Map 及全局累计，返回时间差和死亡差。"""
    ...
```

`None` 表示该项不修改；没有变化返回零差值，不为未修改字段要求额外累计数据。操作先核对和校验所有将受影响的本文件字段，再应用修改；不改最佳成绩、会话、Mod 备份或 Blender。全局数据按本次差值调整，不假定它等于当前可枚举 Map 的总和，不丢掉历史或未安装地图的累计。

```python
save = slot.main()
time_delta, death_delta = correct_map_progress(
    save,
    sid,
    LevelSide.B,
    time_played=Duration.from_seconds(1234),
    deaths=280,
)
save.write(save_slot_path(slot.saves_dir, slot.number))
```

### 可选联动 Ender's Blender

如果缺失增量明确全部属于一个已记录房间，可显式分配同一差值；不存在对应记录时先报错，不把缺失数据擅自按零补齐。

```python
save = slot.main()
blender = slot.mod(EndersBlenderSave)
stats = blender.first_clear[room_key(sid, side)]
room = 'room-12'
old_room_time = stats.room_times[room]
old_room_deaths = stats.room_deaths[room]

time_delta, death_delta = correct_map_progress(
    save,
    sid,
    side,
    time_played=Duration.from_seconds(1234),
    deaths=280,
)
stats.room_times[room] = old_room_time + time_delta
stats.room_deaths[room] = old_room_deaths + death_delta
save.write(save_slot_path(slot.saves_dir, slot.number))
blender.write(mod_save_path(slot.saves_dir, slot.number, EndersBlenderSave.mod_name))
```

这只是明确业务前提下的组合用法，不表示校正 Map 总量总能得到正确的房间统计。只知道 Map 总量、无法分配到房间时不修改 Blender；希望改上次保存数据时选择 `last_saved`。崩溃时 Blender 也可能未及时保存，不能拿它的现有记录冒充崩溃前状态。RTA、收集品、房间顺序及会话数据均不从时间／死亡差值推导；录像估计和补录数值由用户提供，不实现视频分析工作流。

## 10. 使用示例：跨存档移动

业务接口位于 `saves.operations`，只组合传入对象，不读取目录或自动提交文件。公开名称中的 `progress` 表示操作对象是存档进度，而非地图资源文件。

### 独立操作与组合

| 操作 | Map | LevelProgress | CampaignProgress |
| --- | --- | --- | --- |
| 叠加，不修改源 | `merge_map_progress` | `merge_level_progress` | `merge_campaign_progress` |
| 覆盖，不修改源 | `overwrite_map_progress` | `overwrite_level_progress` | `overwrite_campaign_progress` |
| 查询是否为空 | `is_empty_map_progress` | `is_empty_level_progress` | `is_empty_campaign_progress` |
| 清空并联动累计 | `clear_map_progress` | `clear_level_progress` | `clear_campaign_progress` |
| 按 mode 复制，保留源 | `copy_map_progress` | `copy_level_progress` | `copy_campaign_progress` |
| 按 mode 移动，清空源 | `move_map_progress` | `move_level_progress` | `move_campaign_progress` |

`copy_*`、`move_*`、`merge_*` 和 `overwrite_*` 的前两项参数统一为 `source, target`，表示转移方向，也可通过 keyword 明确角色。`merge_*`／`overwrite_*` 是不带 Blender 参数的主进度快捷入口，完整伴随文件协调使用 `copy_*(..., mode=...)`，不隐式加载文件。`is_empty_*/clear_*` 接收一个 `save`；Map 再接收 SID、side 和必选 `poem_id`，LevelProgress 接收 SID 和必选 `poem_ids`，CampaignProgress 接收名称。

这些入口处理存档级联动，不等同于成员的 reset()。清空扣减所选历史对应的全局累计与莓实例，处理显式 poem，保留身份、结构及原有莓累计差额。有历史差额时清空后不保证全空；父结构缺失时清空报 KeyError，判空把缺失视为空，缺损值不可证明为空时返回 false，不自动修复。未知字段已在读取边界处理。

`copy_*` 与对应 `move_*` 的参数、三种 mode 和目标规则相同，但不清空源，不扣减源累计，不更换源成员引用，也不要求源全局累计足以进行扣减。复制使用独立目标状态，目标后续修改不连带修改源；属于所选范围的活动／保留会话随进度复制或移动，目标冲突默认拒绝。`copy_*/move_*` 可以显式传入两端 Blender；复制时这些源统计、名称与颜色同样保留不变。merge／overwrite 也接受显式 Collab 两端，clear 接受单个 collab；任何伴随文件均不隐式加载或修改。

移动复用目标复制准备与独立清空准备，在任何结果安装前完成双方校验和 Blender 预检，不是先执行已修改目标的公开 copy 再尝试 clear。分别调用公开操作只保证每次调用的预检，不自动构成多个调用或多个文件的事务。

```python
merge_map_progress(
    target=target,
    source=source,
    sid=sid,
    side=LevelSide.B,
    poem_id=None,
    goldenberry_ids=goldenberry_ids,
)
copy_level_progress(
    source,
    target,
    sid,
    mode='exclusive',
    poem_ids={},
    goldenberry_ids=goldenberry_ids,
)
if not is_empty_map_progress(source, sid, LevelSide.A, poem_id=None):
    clear_map_progress(source, sid, LevelSide.A, poem_id=None, goldenberry_ids=goldenberry_ids)
```

### 复制与移动的公共参数

下列为移动签名；对应 `copy_*` 具有相同参数和类型。


```python
DEFAULT_POEM_ID = Sentinel('DEFAULT_POEM_ID')
type PoemID = str | None | DEFAULT_POEM_ID
type TransferMode = Literal['overwrite', 'merge', 'exclusive']
type GoldenberryIDs = Mapping[MapKey, AbstractSet[MapEntityID]]


def move_map_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    side: LevelSide,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_id: PoemID,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None: ...


def move_level_progress(
    source: MainSave,
    target: MainSave,
    sid: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: bool = False,
    discard_current_session: bool = False,
    poem_ids: Mapping[LevelSide, PoemID] | tuple[PoemID, PoemID, PoemID],
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None: ...


def move_campaign_progress(
    source: MainSave,
    target: MainSave,
    name: str,
    *,
    goldenberry_ids: GoldenberryIDs,
    custom_summit_gem_ids: CustomSummitGemIDs | None = None,
    source_communal: CommunalHelperSave | None = None,
    target_communal: CommunalHelperSave | None = None,
    source_collab: CollabUtils2Save | None = None,
    target_collab: CollabUtils2Save | None = None,
    discard_collab_session: Mapping[str, bool] | None = None,
    discard_current_session: bool = False,
    mode: TransferMode,
    source_blender: EndersBlenderSave | None = None,
    target_blender: EndersBlenderSave | None = None,
    unlock_requirements: Mapping[str, int] | None = None,
) -> None: ...
```

`move_map_progress()` 只移动一个 side；磁带虽存放在 LevelProgress 字段中，但只归属 A-side 进度，移动 A 时转移并清空源磁带，覆盖使用源值，叠加取或，移动 B/C 完全不改磁带。目标 CampaignProgress 必须已存在；覆盖／叠加要求目标 LevelProgress 已存在，`exclusive` 则可创建不存在的 LevelProgress，并让未移动的 side 保持零进度。`move_level_progress()` 移动 SID 下全部 A/B/C 进度和磁带，根据 SID 推导归属，不承担跨 CampaignProgress 改名；目标 CampaignProgress 必须已存在，但可新增该 LevelProgress。`move_campaign_progress()` 移动整个活动地图集，目标不存在时创建。父结构缺失均在修改前报告 `KeyError`，不让扁平集合 `add()` 暗中创建父对象。

`goldenberry_ids` 为全部复制、移动、叠加、覆盖和清空操作的必选参数，示例中的同名变量表示调用方已整理的金莓实例集合映射。缺失分组或未列入的实例按非金莓处理，未知分类及映射完整性由调用方负责；明确不涉及金莓时传 `{}`。主文件全局金莓累计按收集实例差量更新，不重算历史累计。具体公式及边界见[收集品联动](saves-collectible-linkage.md)。

原版标准 7A 的六个宝石位随包含它的进度操作自动联动；其他 side 不改动。Custom 宝石的三个可选参数必须同时提供或省略：映射按 `(SID, side)` 保存显式字符串键，`CommunalHelperSave.summit_gems` 是持久集合。clear 和 is_empty_* 使用 `communal` 与同一映射；不传时不处理 Custom。共享 side 键是否参与由调用方决定；不会自动解析 Manager、修改 modsession 或由历史推导本次收集。完整读写、null 与集合运算契约及示例见[收集品联动](saves-collectible-linkage.md)。

### 会话随进度处理

map／level 的 `discard_collab_session: bool = False` 控制目标同 SID 会话的冲突；campaign 接受 `Mapping[str, bool] | None = None`，以完整 Level SID 为键，缺失项视为 False。各入口另有 `discard_current_session: bool = False`，默认拒绝主存档活动会话冲突。clear 使用相同策略，但作用于待清除的当前文档，不是另一份目标文档。

来源主存档只有 `in_level=True` 的选中会话参与转移；单 map 按 SID＋side 选择，level 按 SID，campaign 按最终 Campaign 名称选择。接收来源活动会话时，目标的任何活动会话都会冲突，不限 SID；没有来源活动会话时，目标活动会话仅在其进度受本次修改影响时冲突。显式丢弃会清除旧／安全双会话，再安装来源候选，不留下旧会话回退；目标非活动会话不占用活动槽位。True 不是无条件清除无关会话的指令。

Collab 每个 SID 只保存一个会话，不按 side 分配槽位。来源单 map 按 SID＋side 选择保留会话，level 选择该 SID 的会话，campaign 还包含同归属但已没有 Level 记录的保留会话。目标同 SID 槽位只要被占用就冲突，即使旧会话属于另一个 side；若来源没有待安装会话，也仍检查本次进度修改是否影响已有目标会话。对应丢弃开关允许删除这些目标会话及其附带载荷，不影响范围外的会话。仅因 InArea=false 不删除保留会话，其来源选择仍按身份判断。

转移入口的 `source_collab`／`target_collab` 必须同时提供或同时省略；clear 的参数为 `collab`。省略表示不协调 Collab 文件，不表示已确认其中没有会话；调用方需要此保证时须显式加载并传入文档。Session XML、同 SID 的 Mod 文本载荷和 binary 载荷成组复制／移动，目标旧载荷完整替换，不逐 Mod 合并；来源缺失的载荷映射会移除目标残留。复制保留来源且不共享可写状态，移动成功后删除来源三组记录。受影响的孤立 Mod 载荷缺少 Session XML、无法确定 side 时拒绝，调用方先显式修复；不解析范围外的会话 XML。

会话不与目标会话合并，也不额外累计其中的用时、死亡数和收集品：游戏已在游玩中写入对应进度。丢弃会话不回滚这些已保存进度，进度仍由 merge／overwrite／clear 的规则决定。因此不提供无实质区别的 exit 策略，也不模拟游戏退出钩子。源码依据是原版 SaveData.AddTime／AddDeath／AddStrawberry 与 LevelExit 的保存路径，以及 CollabUtils2 ReturnToLobbyHelper 在保存／恢复时成组处理 SessionsPerLevel 和两组 Mod 载荷的路径。

范围、会话身份和待输出内容、累计、成员安装及关联索引全部在修改前检查；拒绝不删除任何会话，也不安装部分进度。仅提供同步内存预检，不保证多个文件的磁盘事务，也不执行任意 Mod 的会话恢复代码；主存档旁独立的 modsession 文件由调用方显式组合第 3 节的文件组接口处理。完整保留已建模的 OldStats／DoNotLoad 等会话上下文，不宣称已实现从事件推导这些字段或完整游戏验收。

```python
source_collab = source_slot.mod(CollabUtils2Save)
target_collab = target_slot.mod(CollabUtils2Save)
move_level_progress(
    source,
    target,
    sid,
    mode='merge',
    poem_ids={},
    goldenberry_ids=goldenberry_ids,
    source_collab=source_collab,
    target_collab=target_collab,
    discard_collab_session=True,  # 允许替换目标这个 SID 的保留会话
    discard_current_session=False,  # 目标已有活动会话时仍报错
)
# 各内存文档均需由调用方显式 write()；不自动处理备份或跨文件提交。
```

### 诗句选择必须明确


`move_map_progress()` 的 `poem_id` 和 `move_level_progress()` 的 `poem_ids` 均为必选 keyword-only 参数，不提供默认值。`None` 表示不处理；字符串表示显式 PoemID，按游戏注册规则转小写；`DEFAULT_POEM_ID` 是调用方显式选择平台默认规则的 sentinel，不是参数默认值。`move_level_progress()` 接受映射或固定三元组：映射以 `LevelSide` 为键，缺失的键视为 `None`，空映射表示全部不处理；三元组按 A/B/C 顺序，必须完整提供三项，不接受其他长度或任意序列。`PoemID` 和 sentinel 均可从 `saves.operations` 导入。

默认规则对 Mod 的 A/B 使用 SID 的 Dialog key 加 `_A/_B`，原版使用游戏明确配置的 ID。C side 没有自动生成的默认 ID，sentinel 在 C 上等同不处理，但调用方仍可传入自定义 C side PoemID。默认推导不会读取地图元数据，不能发现自定义配置、显式空配置或过去版本的 PoemID；这些情况须由调用方显式选择。只有源列表中已登记的选中项才转移，不从收心标志凭空创建 poem。

```python
from berries.game.saves.operations import DEFAULT_POEM_ID, move_level_progress, move_map_progress

source = saves.slots[2].main()
target = saves.slots[6].main()
move_map_progress(
    source,
    target,
    'Cdog/ExtendedCSides/Example',
    LevelSide.B,
    mode='merge',
    poem_id=DEFAULT_POEM_ID,
    goldenberry_ids=goldenberry_ids,
)
source.write(save_slot_path(saves.slots[2].saves_dir, saves.slots[2].number))
target.write(save_slot_path(saves.slots[6].saves_dir, saves.slots[6].number))

source = saves.slots[2].main()
target = saves.slots[6].main()
move_level_progress(
    source,
    target,
    'Cdog/ExtendedCSides/Example',
    mode='overwrite',
    poem_ids={LevelSide.A: DEFAULT_POEM_ID, LevelSide.C: 'custom_c_poem'},
    goldenberry_ids=goldenberry_ids,
)
source.write(save_slot_path(saves.slots[2].saves_dir, saves.slots[2].number))
target.write(save_slot_path(saves.slots[6].saves_dir, saves.slots[6].number))
# 缺失的 B 不处理；若全部不处理，显式传 poem_ids={}。

# 与上面映射等价的三元组写法；按需选择一种形式调用。
# poem_ids=(DEFAULT_POEM_ID, None, 'custom_c_poem')
```

选中诗句在叠加时保留目标列表并按源顺序追加缺失项；覆盖时还移除源中未登记的选中目标项。两种方式在移动成功后移除源中选中的登记，复制则保留源登记，其他条目不变。整 CampaignProgress 无需按 side 选择：覆盖使用源完整列表，叠加稳定去重追加，源列表清空；不猜测旧 PoemID 与新 PoemID 的对应关系。

### 修改前检查参与对象重叠

copy／move 入口均拒绝 `source is target`；提供 Blender 两端时也拒绝相同文档。两个根文档不同仍须检查参与的 CampaignProgress、LevelProgress、Map、最佳成绩及可变集合是否共享。跨两端共享待修改状态时报 `ProgressConflictError`，不偷偷复制一端来决定历史含义。可以先显式 `deepcopy()` 所需领域成员，再安装独立进度，也可以对整个 `Save` 使用标准 `deepcopy()`。

检查仅针对参与范围，无关的共享进度不会造成拒绝。重叠、范围、编码及累计检查在安装任何结果前完成；失败不留下部分领域修改。这不替代两个独立文件会话指向同一路径时的写回冲突检测，也不是跨文件磁盘事务。

```python
shared = source.mods.campaigns['Cdog/ExtendedCSides']
target.mods.campaigns.add(shared)
try:
    move_campaign_progress(
        source,
        target,
        shared.name,
        mode='merge',
        goldenberry_ids=goldenberry_ids,
    )
except ProgressConflictError:
    assert source.mods.campaigns[shared.name] is shared
    assert target.mods.campaigns[shared.name] is shared
```

### 数据转移规则

1. `mode` 必须明确为 `'overwrite'`、`'merge'` 或 `'exclusive'`。覆盖替换所选目标范围，整 CampaignProgress 覆盖会移除仅存在于目标的 LevelProgress；叠加保留不匹配的目标成员。`exclusive` 先断言目标不存在或全空，通过后按覆盖规则安装；不是以零用时或零死亡数单独判断。
2. 叠加时用时和死亡数相加，完成、单次完成、FullClear 和收心标志取或，收集品／检查点取并集，合法最佳成绩取较优值，包括零死亡／冲刺。整 CampaignProgress 解锁数取较大值；不从两份累计数据伪造一次 Session 的事件顺序。
3. 成功后清空源受支持进度，保留 CampaignProgress、LevelProgress、Map 的身份和定长结构，不删除源结构或移入回收区。单 Map 的 A-side 移动和整 LevelProgress 移动清源磁带，B/C 不动磁带；整 CampaignProgress 另清解锁数和 poem。未知字段已经由读取策略处理，不在这里假装清空其语义。
4. 源全局扣除转移 Map 的原用时／死亡数；目标叠加增加同样总量，覆盖则先扣除被替换目标范围的原总量。负时长按底层有符号精度处理，死亡数和草莓累计不得溢出或扣成负数。
5. 草莓累计按每个 `(SID, side)` 的精确实例差量联动，不跨 Map 去重：源扣原集合数量，目标叠加增加源中新增实例数，覆盖增加新集合数量并扣被替换集合数量。保留已有累计与实例数之间的差额；新建目标不复制源历史差额。Map 的协议累计也遵循对应差量，而不是重算并抹去历史。
6. 覆盖、新增与叠加均处理完整已建模进度；未知字段默认已在读取时拒绝，warn 模式已明确丢弃，不留到合并时推断。整个 CampaignProgress 转移必须先解析全部 LevelProgress 身份，不在遗漏未知 ID 记录的同时调整全局累计。
7. 范围内的来源活动会话和显式提供的 Collab 保留会话随进度转移，目标冲突默认拒绝，可通过 discard 参数允许丢弃；不额外累计会话统计。未传入的 Collab 等外部文件无法检测，不宣称自动完成所有 Mod 会话迁移，具体规则见上文“会话随进度处理”。
8. 结果以独立、已通过编码预检的成员替换安装；原先持有的参与 CampaignProgress／LevelProgress／Map 引用仍可使用，但已脱离根文档，不能用旧引用观察“清空后的源”。已有扁平活视图解析新状态；未修改的 LevelProgress／side 保留原引用。不把这种业务替换语义泛化成普通属性赋值的隐式复制。

### exclusive 的全空定义

`move_map_progress()`／`copy_map_progress()` 只检查目标选中 side：用时和死亡数为零，完成、单次完成、FullClear、收心均为 false，莓和检查点为空，成绩原始值均为零且单次完成标志为 false，协议莓累计差额为零；仅 A-side 另要求磁带为 false，B/C 不检查磁带或其他 side 的进度。`move_level_progress()`／`copy_level_progress()` 检查整个同 SID 的 LevelProgress，即上述 A/B/C 全空并且无磁带。合法的零死亡最佳成绩仍是成绩，不算空；负用时也不是零。缺损数据不能证明为空时拒绝，不以身份字段、定长结构和合法默认值本身视作已游玩。

目标 LevelProgress 不存在时仍检查显式选中的目标 poem 登记；存在时也要求选中项未登记。未选择的诗句、父 CampaignProgress 的其他 LevelProgress 与全局累计不参与单 LevelProgress 判空；exclusive 对进度判空后另行执行会话冲突检查，允许显式丢弃目标冲突会话，但不能借 discard 跳过非空进度检查。公开 is_empty_* 查询仍将相关主存档活动会话视为非空；Collab 文件不在这些查询的输入内。`move_campaign_progress()` 检查整个目标 CampaignProgress 的所有 LevelProgress、解锁数、草莓累计和 poem，缺失则可直接创建。所有检查在修改前完成，拒绝不改变两端历史。

```python
move_level_progress(
    source,
    target,
    sid,
    mode='exclusive',
    poem_ids=(DEFAULT_POEM_ID, DEFAULT_POEM_ID, None),
    goldenberry_ids=goldenberry_ids,
)
```

### 可选联动 Ender's Blender

`source_blender` 与 `target_blender` 同时提供或同时省略。省略完全不处理；提供则转移所选 side 的初见、上次保存、名称和颜色，不把其中一类作为默认记录。叠加和 exclusive 时目标已有相关 Blender 记录则报冲突（包括已存在的空记录），不相加或拼接两条时间线；覆盖时替换相应目标记录，源没有的对应记录从目标移除。只有移动在成功预检后移除源记录，复制保留源记录。协议键歧义、两端集合共享、受影响记录含无法安全处理的 YAML 标签等情况先拒绝；未知字段在读取时按 error／warn 策略处理。

```python
source = saves.slots[2].main()
target = saves.slots[6].main()
source_blender = saves.slots[2].mod(EndersBlenderSave)
target_blender = saves.slots[6].mod(EndersBlenderSave)
move_campaign_progress(
    source,
    target,
    'Cdog/ExtendedCSides',
    mode='overwrite',
    goldenberry_ids=goldenberry_ids,
    source_blender=source_blender,
    target_blender=target_blender,
    unlock_requirements=unlock_requirements,
)
source.write(save_slot_path(saves.slots[2].saves_dir, saves.slots[2].number))
target.write(save_slot_path(saves.slots[6].saves_dir, saves.slots[6].number))
source_blender.write(
    mod_save_path(saves.slots[2].saves_dir, saves.slots[2].number, EndersBlenderSave.mod_name)
)
target_blender.write(
    mod_save_path(saves.slots[6].saves_dir, saves.slots[6].number, EndersBlenderSave.mod_name)
)
```

目标伴随文件不存在时由调用方显式构造空白文档；已经存在时先读取并编辑，不能无条件用空白对象覆盖其他记录。多次 `write()` 不保证磁盘跨文件原子性，调用方应另行安排文件保存及恢复策略。Mod 备份不暗中刷新。

## 11. 使用示例：整体恢复与备份刷新

```python
from copy import deepcopy

main = slot.main()
backup = slot.backup()
if main.needs_mod_recovery:
    main.install_mod_progress(deepcopy(backup).mods)
    # 仅在内存中恢复，供读取和浏览，不写回主文件。
```

```python
from copy import deepcopy

main = slot.main()
backup = slot.backup()
backup.install_mod_progress(deepcopy(main).mods)
backup.write(mod_save_data_path(slot.saves_dir, slot.number))
```

普通字段赋值仍是引用语义，视图不支持整项赋值。原版视图的 snapshot() 提供 Campaign 外壳，Mod 数据直接通过显式 deepcopy() 隔离；install_mod_progress 安装准备好的字段引用并协调索引，不暗中复制。Everest 恢复条件是主文件尚无 Mod 数据标记、活动 LevelSet 数不超过 1 且回收区为空，而不是逐地图补缺。主文件安装 Mod 进度时协调 HasModdedSaveData，不把标记散落给 UI。

## 12. 编解码边界与兼容策略

- 五种格式均建模已知字段；未知字段／属性默认 error，显式 warn 后丢弃，不提供 ignore。调用方可使用标准库 warning filter。
- XML 注释、PI、CDATA 词法、混合文本与命名空间前缀不作领域保真，未知 QName 随其未知材料拒绝或显式丢弃，不拼接片段保留。
- 不承诺原始词法形式或最小文本 diff。用户比较编辑前后的 tostring()；开发验证另需源码、.NET 与原输入的独立证据，不能仅以自身模型再次解析成功证明正确。
- YAML 已知字段的显式 tag 使用稀疏元数据保留，数值和集合由领域值管理；不长期保存节点或原始 YAML 片段。未知字段整体按策略拒绝或警告后丢弃，不恢复其中的别名。
- Collab 嵌套 XML 可以延迟解释；解析为可编辑会话后，以 Session 领域值作为权威可写状态，原始 XML 仅作不可变输入保留。
- 本版 API 不暴露原始节点作为成员的身份或所有权基础；序列化生成目标文档，不从源文档搬走节点。
- warn 不是未知扩展的无损往返。协议明确保存的其他 Mod 字符串／binary 载荷按原载荷值保存，不执行它们。

历史对比见[存档 XML 后端](finished/save-xml-backends.md)，其中片段保真方案已由[完整建模](finished/saves-modeling.md)取代，不再作为当前接口要求。

## 13. 实现核对与已知边界

### 2026-10-21：审查后的输入与联动约束

- 严格加载主存档要求 Name、Time、TotalDeaths、AssistMode、VariantMode 以及原版进度容器和累计字段存在，并检查原版 11 章的完整性与顺序；严格加载 Map 要求正常输出的已知统计属性、Strawberries 与 Checkpoints 容器，并立即校验已知成绩和收集实例。宽松加载的缺失累计按游戏默认零值解释，缺失名称按 Madeline 解释；非法字符串仍报错。独立 Map 编解码的 `from_xml(strict=False)` 默认允许检查缺损片段，整文件入口显式传递所选 strict。
- 写出会话要求 OldStats 包含三个非 null Modes；这是已有源码消费路径的最低结构约束，构造领域对象并不承诺验证任意 Mod 的运行时状态。Collab 仍在读取时延迟解析会话，但写出不能绕过这项检查，未求值的输入临时解码且不改变惰性缓存。
- Map 的 TotalStrawberries 在读取和写出时均校验，缺失值统一按零处理；修改收集实例时对原累计做增减，不根据实体集合重算累计。`berry_offset` 保留累计与已收集实例数的差额，因此可以为负；合法且未修改的累计保留原有数值文本。
- `correct_map_progress()` 不猜测或修改成绩及会话；调整后的用时不能小于已存最佳时间／全收集最佳时间，死亡数不能小于最佳死亡数。同身份保留会话还要求累计不小于其 OldStats 历史加本次尝试的用时／死亡数，即使 InArea 为 false 也适用；该检查由 `Session.validate_map_totals()` 提供。需要降低到这些边界以下时，调用方先显式协调相关状态。所有拒绝均发生在 Map 和全局累计修改之前。
- copy／move／merge／overwrite 的所有转移入口新增 keyword-only `unlock_requirements: Mapping[str, int] | None = None`。映射值表示该 SID 任意 side 通关后所需的最低解锁位置，已包含当前地图集顺序、MaxArea 和相关元数据的影响；它不是由存档成员顺序推断出的值。原版 SID 使用固定章节表，Mod 的所选已完成记录缺少映射时在修改前报错；调用方准备映射，操作内部不扫描 Catalog。局部转移保留目标原有解锁并满足映射，源局部清空不收回历史解锁；整集转移按覆盖／叠加语义处理解锁历史，再满足本次完成记录的要求。
- Blender 非空房间顺序中的非空名称必须有死亡和计时，读取可以保留缺损记录，写出和转移拒绝缺项，不填造数值；按消费规则提供可为空的草莓数量和暂停标记字典，RTA 仍可由 Mod 补缺。文件写出还为已知记录补齐空名称字典，为可显示记录补齐空颜色和暂停标记字典；last_saved 即使房间顺序为空，只要存在顺序键也需要暂停字典。这些辅助容器仅补入输出，不修改领域对象，也不猜测统计数值。受影响记录或其容器带未知 YAML tag 时拒绝转移，避免丢标签或继承目标旧标签；其他记录上的标签不妨碍转移。
- Blender 字典的 copy()／copy.copy() 复制本层存储、共享 RoomStats。Celeste 占位项按声明字段建模，主文件输出协调其 UnlockedAreas／TotalStrawberries 根字段镜像。
- 全局 TotalJumps、TotalWallJumps、TotalDashes 继续保留全档历史语义，不随局部转移重新分摊。主存档 `total_goldenberries` 对应 TotalGoldenStrawberries，全部 copy／move／merge／overwrite／clear 操作现需显式传入 `goldenberry_ids: GoldenberryIDs`，按实例交集差量更新全局金莓累计；不含金莓时传空映射。GoldenberryIDs 从 operations 导入，按 `(SID, side)` 分组保存 MapEntityID 集合；规则解释和未知实体处理由外部负责。根 TotalStrawberries 仍由 vanilla.total_strawberries 管理，各 Mod Campaign 各自管理累计，不另设跨 Campaign 草莓总数。宝石持久历史已联动；CommunalHelper 独立 modsession、DoNotLoad 与剧情标记等尚未实现完整业务联动；Collab 保留会话及其 Mod 载荷已支持显式转移，不能宣称所有游戏不变量均已覆盖。金草莓与原版／CommunalHelper 宝石的源码背景及支持假设见[收集品联动调研](saves-collectible-linkage.md)。

### 2026-10-19 补充：覆盖、叠加与地图版本变化

移动提供覆盖、叠加及断言空目标的 exclusive 三种模式，接口见第 10 节。覆盖取代所选目标范围，目标全局累计应先扣除被替换的历史再加上源历史；叠加保留目标历史，按源历史增加累计。二者都在成功后清空源受支持进度，不自动更新备份或未传入的伴随文件。接口和专项回归已实现；实际临时副本已验证覆盖提交／重读及独立历史叠加，尚不宣称通过游戏运行验证。

本地原版反编译 `SaveData.cs` 的 `AddDeath/AddTime/AddStrawberry/RegisterCompletion/RegisterPoemEntry` 与 Everest 补丁共同表明：普通游玩按增量增加死亡数和用时；莓收集按同一 Map 的精确 EntityID 去重，新 ID 才加入集合并增加 Map 与地图集累计。地图版本变化没有通用的“旧实体 ID 对应新 ID”迁移；不能仅因当前地图不再包含旧莓就从历史集合中删除它，也不能识别同 ID 已变成另一颗莓。这里讨论正常游玩，不含 DebugMode 的 `StartSession` 清莓分支或第三方 Mod 自行改写行为。

Poem 注册先将新 ID 转小写，再在当前地图集列表中按已存字符串检查和追加；不替换旧 ID 或重建整个列表。因此 PoemID 改变后，旧项不会因下一次收心注册自动消失。Everest 的 `AreaData` 为未显式配置的 A/B side 填充默认 PoemID，也允许显式覆盖或空字符串；存档的 poem 列表本身没有保存每项所属 SID／side。整 CampaignProgress 可以处理完整列表，单 LevelProgress 不能从存档反推哪些 poem 项属于它；因此使用第 10 节的必选诗句参数，不能复制整集 poem 冒充单地图移动。

检查点与莓并不同：Everest 的 `AreaStats.CleanCheckpoints()` 根据当前已安装地图的 side 和检查点定义过滤旧检查点，并由 `SaveData.AfterInitialize()` 调用。仅有存档对象的操作不知道这些当前定义，不应暗中扫描安装目录或把莓和 poem 也照此清理。最佳成绩在正常通关时按会话的起点、完成状态、计时与死亡／冲刺更新，`FullClear` 还有独立标志；合并两份已汇总的历史不是重放一条 Session，不应假称可以复原全部事件顺序。实现包括 FullClear 标志及独立最佳成绩，未知字段的拒绝或显式丢弃在读取边界完成。

调研依据：本地 `.pist/references/Celeste/Celeste/Celeste/SaveData.cs` 的 165–310 行、`HeartGem.cs` 的 `RegisterAsCollected`，以及 Everest 固定版本的 [原版／Mod 字段路由](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L128)、[CleanCheckpoints](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaStats.cs#L63)、[默认 PoemID](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaData.cs#L396)。本次是源码行为核对，不是已用游戏加载跨版本存档验证。

### 2026-10-19 契约验收记录（历史）

以下模块路径、测试数量和计时对应 2026-10-19 的验收实现，用于保留验证证据，不代表后续每次修改均已重新执行相同验收。其中持有文件会话的设计于 2026-10-20 调整为一次性读写，成员容器与分区索引于 2026-10-21 调整；调用接口以正文现行契约及 [存档容器](saves-containers.md) 为准。

| 范围 | 当时实现与验证 |
| --- | --- |
| 目录与槽位 | `SavesDir.slots` 保留空位和 debug 入口，`SaveSlot` 只定位；四种格式由显式文件会话读取，无隐式伴随文件加载。覆盖目录发现、参数传递和错误分类测试。 |
| 文件生命周期 | 路径／借用文本和二进制流、编码输入输出、流所有权、当前游标及编码失败时不打开目标已有回归；不提供复制和备份 API，不承诺跨文件原子性。 |
| 进度与活视图 | 独立原版与 Mod／回收区，定长 A/B/C、身份只读、普通引用语义、浅／深复制、重置与移除、根和成员替换后的视图均已覆盖；`strict=False` 只放宽观察，不豁免写回约束。 |
| 主文件与备份 | 编解码支持根原版数字 ID 显式映射、完整 Mod 快照恢复、标记协调及双当前会话规则；Celeste 占位项不重复建模。真实 CampaignProgress 的解锁和莓累计按 XML 子元素读取／写出，已纠正错误的属性式夹具并增加回归。 |
| Collab 与 Blender | 保留会话按需解释，其他已建模集合可编辑；Blender 两组统计并列，原始协议键重载、各统计独立缺项、已知别名解码后的独立编辑均有覆盖。 |
| 校正与转移 | `correct_map_progress` 联动显式指定累计；三个移动入口覆盖覆盖／叠加、诗句、差量累计、参与对象重叠、会话冲突、未知扩展拒绝及 Blender 可选联动；拒绝前不安装部分结果。 |
| Pist 接入 | 应用读取链和预览使用文件／集合入口，自己的只读统计投影留在 Pist。 |

真实副本功能检查包括：主文件、备份、29 条 Collab 保留会话、Blender 的已建模统计，以及 CampaignProgress 覆盖写回／重读、独立历史叠加、LevelProgress 和 Blender 联动提交／重读。原始游戏文件及作为输入的临时副本均未改写；为了验证含当前会话地图的转移，测试仅在另一个工作副本中显式清除当前会话，移动接口本身仍拒绝冲突。

### 最终验证记录（2026-10-19）

本轮全量 `uv run --all-packages pytest -n auto --dist worksteal --basetemp .pist/pytest` 为 **2025 项通过、1 项跳过**。唯一跳过项是未配置 `PIST_TEST_SHEET_URL` 的腾讯表格在线写入测试，与存档接口验收无关；未为验收开启正式表格写入。此前某轮 UI 刷新等待超时本轮未复现，不把“本轮通过”表述成已确定并修复其时序根因。Ruff 检查、格式检查、Pyright 及两包 sdist／wheel 构建通过。

逐项检查正文代码块中 122 个已声明成员／函数的实际可达性，并核对必选 keyword-only 参数仍无默认值。这个 API 检查只证明入口和契约形状存在，行为依据是下列专项回归及真实副本验证，不用它替代测试。

| 正文要求 | 实现与直接回归证据 |
| --- | --- |
| 第 1–2、5 节：身份、普通引用、浅／深复制、集合顺序、重置／移除与活视图 | `progress/`、`views.py`；[领域集合回归](../berries/tests/test_progress_domain.py)、[主文件根替换与共享子对象](../berries/tests/test_main_save_domain.py) |
| 第 3 节：连续槽位、空位、debug、路径与借用流、失败不提交 | `directory.py/slot.py/files/doc.py`；[目录及槽位回归](../berries/tests/test_save_groups.py)、[文件模式和失败生命周期](../berries/tests/test_save_access.py) |
| 第 4、11 节：严格／宽松加载、原版 ID、双会话及 Mod 快照恢复 | `files/main.py`、`files/mod_backup.py`、`progress/campaign.py`、`progress/mods.py`、`progress/session.py`；[进度 XML 边界](../berries/tests/test_progress_xml.py)、[主文件协调](../berries/tests/test_main_save_domain.py)、[恢复与累计校正](../berries/tests/test_save_operations.py) |
| 第 6–7 节：Collab 惰性会话、Blender 两组统计、原始键重载、独立缺项 | `sessions.py/rooms.py/files/collab_utils2.py/enders_blender.py`；[Collab 领域回归](../berries/tests/test_collab_save_domain.py)、[会话与房间集合](../berries/tests/test_save_collections.py)、[Blender 协议回归](../berries/tests/test_enders_blender.py) |
| 第 8–10 节：领域复制编辑、校正、独立 merge／overwrite／clear／判空、copy／move、mode 与诗句 | `operations/transfer.py`、`operations/edit.py`、`operations/poem.py`；[独立操作回归](../berries/tests/test_progress_operations.py)、[转移与原子拒绝](../berries/tests/test_save_moves.py)、[校正回归](../berries/tests/test_save_operations.py) |
| 第 12 节：未知字段 error／warn、规范化输出、数值与别名隔离 | [文件编解码回归](../berries/tests/test_save_files.py)、[进度 XML](../berries/tests/test_progress_xml.py)、[Collab](../berries/tests/test_collab_save_domain.py)；新增外部 `berries-slot` PI 不能冒充解码器内部位置的三项回归 |
| 应用调用链与示例迁移 | [Pist 存档投影](../pist/tests/test_save_progress.py)、[可执行示例](../berries/tests/test_examples.py)、[架构边界](../tests/test_arch.py)；全量还覆盖预览及地图浏览调用链 |

真实副本再次验证主文件 352 个进度分区、4416 个 Map 的无修改规范 XML 一致性、校正写出、备份快照重载、CampaignProgress 覆盖／叠加、LevelProgress＋Blender 联动，以及非破坏性复制。Collab 的全部保留会话和 Blender 两组全部记录经物化、写回内存流及重载后值一致，未支持的 YAML 也参与完整值比较。源临时副本摘要均未变化；未打开游戏原存档，也未修改数据库。

独立于测试和 profiler 的单次工作量计时使用同一 1,790,807 字节主文件副本、Python 3.14.7、Windows 环境，各重复 5 次：读取中位数 0.2035 秒（0.2016–0.2070），读取＋编码＋提交到内存流中位数 0.9907 秒（0.9859–1.0111）。另行启用 tracemalloc 检查 Python 分配，读取峰值约 9.55 MiB，四次读取并回收后的留存为 152／184／184／184 字节；不包含 libxml2 的 C 堆，不据此宣称进程总内存上界、前后性能提升或全部场景无泄漏。

### 2026-10-19 四类复查结论

- 功能实现：确认范围的对象、集合、文件模式和操作均已落地并有对应证据；未知扩展、附加会话、跨文件原子性和游戏运行加载仍按下节边界处理，不假称自动解决。
- 代码质量：修正只按 PI target 判断内部位置的保护缺口，现同时核对解码时记录的位置身份；新增回归确认拒绝后两端不变。未发现需继续修复的同范围问题。
- 代码组织：文件编解码、领域值、目录／槽位、单存档编辑准备、跨存档转移及公开操作分别归属对应模块；`__init__.py` 只重导出，公共包不依赖 Pist。
- 测试：正常、缺失、无效、冲突、写回失败、复制隔离和重读分别有覆盖；真实副本用于核对外部结构，不把合成夹具或 API 存在性当作协议证据。全量通过不覆盖显式排除的运行环境和外部在线测试。

### 明确保留的范围边界

- 未知字段默认在读取时拒绝，显式 warn 后不进入领域值；不推导各 Mod 私有载荷的合并或联动，业务转移不是重放游戏加载和通关事件。
- 不模拟安装环境中的地图重排、检查点清理、Mod 会话恢复或所有历史实体／PoemID 迁移；不自动读取 Collab 附加会话或更新 Everest 备份。
- 多个文件分别提交，无法保证跨文件磁盘事务；调用方自行安排复制、保存及失败恢复。没有游戏实际加载所生成存档的验证结论。
- 活视图后续查询反映当前根，但迭代过程中结构修改不提供事务快照保证。领域普通赋值仍是引用语义，移动业务的预检后成员替换另按第 10 节说明。
- 性能检查与功能验证分开；已有真实大存档的读取／写出检查不等同于全部操作的基准测试，不能由一次功能用时推导普遍性能结论。
