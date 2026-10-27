# Saves 三层接口设计（已过时）

> 状态：前一轮设计已结束，接口已被后续方案取代。本文仅保留历史设计与协议调研，不作为当前调用指南；当前接口见 [存档领域 API](../saves-domain-api.md)。

本文完整保留前一版三层接口的历史设计与格式调研，以下文件继承、`saves.models` 快照、查询 setter、文档复制和 `parse/read_file/serialize` 均已被后续实现移除，不再作为当前调用指南。当前领域对象、槽位与文件访问 API 及分阶段实现状态见 [Saves 领域对象与集合 API](../saves-domain-api.md)；该文中仍标为待实现的部分不能视为当前可运行代码。本文中的源码调研仍可供核对协议事实，但不能用历史接口示例恢复已删除的实现。

单文件层、编号／目录协调层和基础组合业务在 `berries.game.saves` 实现，支持独立解析、内存编辑和序列化；共享值归 `berries.game.saves.models`。CLI、UI 已通过 Pist 的应用读取策略迁移到这些接口，旧的单体 `SaveReader` 和 Ender's Blender 读取器已移除，没有兼容层，也没有自动写回游戏存档。编解码、文件组和组合操作都有合成数据回归，但尚未以游戏实际加载修改结果验证可玩性。

## 结构与依赖

```text
berries/game/saves/
├── files/                       单文件格式接口
│   ├── doc.py                   SaveDoc：文件生命周期
│   ├── progress.py              ProgressSave、VanillaProgressSave、ModProgressSave：进度能力
│   ├── main.py                  MainSave：主进度文件
│   ├── mod_backup.py            ModBackupSave：Mod 进度备份
│   ├── collab_utils2.py         CollabUtils2 YAML 与内嵌会话 XML
│   └── enders_blender.py        Ender's Blender YAML
├── models.py                    共享值类型与协议时长
├── paths.py                     已有路径与文件名规则
├── slot.py                      SaveSlot：可编辑文件组
├── saves_dir.py                 SavesDir：目录发现与文件加载
└── operations.py                显式恢复与跨文件进度查询
```

文件层不依赖编号层或业务层；编号层只关联文件、协调文件名和组装，不处理统计合并；业务层显式接收所需单文件对象，不依赖 `SaveSlot` 或具体文件名，不解析 XML/YAML、不寻找文件、不修改私有结构，也不写磁盘。`SaveDoc` 只表示单文件，不同时表示整个 slot。

新文件组从 `berries.game.saves.slot` 导入 `SaveSlot`，目录从 `berries.game.saves.saves_dir` 导入 `SavesDir`，包入口也重导出这两个类型。`SaveSlot` 始终表示文件组，不提供统计查询；浏览用索引投影由 `pist.saves.SaveProgress` 承载，不是另一个存档格式或编解码器。

`pist.saves.SaveProgressReader` 组合 `SavesDir`，明确纳入 CollabUtils2 文件，并在读取后显式调用 `restore_mod_progress()`。主文件若不满足恢复条件，就不从备份逐地图补缺。应用从主文件一次枚举构建统计索引，并取得当前身份；列表查询不重复扫描 XML，也不继续保存另一份主 XML 状态。保留会话仍由原 Collab 文件按需解释；无效请求默认报错，UI 用错误回调明确选择提示并跳过，同一快照中仅提示一次，重新加载才重建失败缓存。Ender's Blender 文件仅在打开预览时读取，预览消费 `FirstClearStats` 查询函数，不依赖具体 Mod 存档容器。

## 主进度与备份的职责

`<n>.celeste` 是 `MainSave` 主进度文件，不是只保存原版地图的文件。Everest 在主文件中扩展 `LevelSets`、`LevelSetRecycleBin`、`LastArea_Safe`、`CurrentSession_Safe` 和 `HasModdedSaveData`，原版 `Areas`、全局累计及其他字段也仍属于主文件。

`<n>-modsavedata.celeste` 是 `ModBackupSave`，只备份前述四个 Mod 字段，不含全局用时、全局死亡数或 `HasModdedSaveData`。两种文件通过 `ModProgressSave` 共享这四个字段的查询与整组替换契约；不是两份同等地位的主存档，也不逐地图互相补缺。依据为 [Everest ModSaveData](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/ModSaveData.cs)。

`ProgressSave` 只提供与存放位置无关的地图统计和会话读写机制，区域遍历与会话定位由抽象方法约定。`VanillaProgressSave` 和 `ModProgressSave` 是兄弟能力类型：前者解释原版 `Areas`／`CurrentSession`，并提供全局 `Time`／`TotalDeaths` 的直接读写；后者解释 Everest 的四个 Mod 字段。全局累计在 Everest 下也可能包含 Mod 游玩数据，字段归属不表示只统计原版地图。两个分支不各自创建或复制 XML 文档。`MainSave` 同时继承两者，共享一份文档，并显式协调原版优先的区域遍历、安全会话优先的选择、同身份双会话编辑、地图与全局累计的差值联动和 Mod 标记；不靠 MRO 或协作式 `super()` 隐式规定两个分支的顺序。`ModBackupSave` 只继承 Mod 分支；两个能力类型本身不选择文件根节点，具体文件类型负责解析入口。

Everest 仅在主文件 `LevelSets.Count <= 1`、`LevelSetRecycleBin.Count == 0` 且 `HasModdedSaveData == false` 时尝试恢复备份；存在备份则整体复制四个字段，而不是只复制主文件缺少的地图或非空会话。随后游戏会设置已包含 Mod 数据的标记。依据为 [SaveData.AfterInitialize](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L401)。

恢复放在显式业务操作 `restore_mod_progress(main, backup)` 中，满足条件时原位修改主文件，不修改备份。普通查询和单文件编辑不暗中恢复备份；需要恢复时由调用方先执行恢复，再进行查询或累计修正。这里仅复现上述恢复条件，不模拟游戏剩余初始化、安装地图协调或 LastArea 回退；没有备份时不调用恢复操作，也不会仅为模拟初始化而设置标记。

## 文件层

每个文件对象独立支持 `read_file()`、`parse()` 和 `serialize()`，不要求存档编号，不读相邻文件。文件对象是可变的编辑文档，不是每次修改都返回副本的值对象；setter 先完成输入、目标结构及受影响字段的校验，再原位写入。编辑针对本文件中已经存在的受支持字段，保留未知字段，不自动联动其他文件。查询返回值快照，外部不能通过修改快照绕过文档接口；只有确需独立分支时才显式 `copy()`。

文件对象和 `SaveSlot` 支持标准 `copy.deepcopy()`；`.copy()` 使用同一条深复制路径。复制隔离可写状态，并用同一个 memo 保留文件组内重复引用的关系，不把同一文档复制成多个互不关联的对象。XML 副本仍通过完整文档序列化重解析保留根节点外的顺序，Collab 副本不携带已解析的会话缓存。浅复制不承诺文档隔离。

`read_file()`、`SavesDir(...)` 和 `SavesDir.from_game_dir()` 接受 `str | os.PathLike[str]`，入口转换为宿主 `Path`，内部字段保持 `Path`。文件与目录 I/O 保留原生 `OSError` 分类（包括缺失和权限错误），不要求调用方拆异常链判断；内容解析错误仍是携带文件路径的 `ValueError`。目录发现对不存在的目录返回空列表，未指定或不存在的可选伴随文件仍按缺失处理。Pist 在应用边界把 I/O 失败转成现有界面能显示的错误，只有缺失的可选 Ender's Blender 文件返回 `None`。

XML 使用 `lxml.etree` 保留未知节点、注释、处理指令、CDATA 和命名空间上下文，后端选择与复制约束见[存档 XML 后端选择](../finished/save-xml-backends.md)；YAML 保留节点及标量 tag，不把未建模内容先转成 Python 值再重建，不执行自定义 tag 的构造器。原始文本格式、YAML 注释和锚点名称不保证原样，有意义的文本空白必须保留。文件层拒绝不属于存档协议的 XML DTD；`read_file()` 的错误包含路径，字段错误包含相应的结构或校验位置。

XML 统计和会话在查询时校验；重复的 SID/side 视为歧义并报错，不任意选择一个副本。当前支持的统计仍是子集：例如最佳成绩、全局收集总数或机关状态不会因编辑受支持字段而自动重算。缺少 SID 的原版 `AreaStats` 保留在文档中，读取可通过调用方提供的显式原版身份映射解决，但不从未知 ID 猜测 SID。

### 原版身份与只读进度查询

原版 `AreaStats` 的 `ID` 和 `Session.Area.ID` 对应 `AreaData.Load` 初始化后的列表索引，side 对应 `Modes` 顺序；它不是用于显示的章节序号。例如原版 ID 8 是尾声，ID 9 才是 Core，ID 10 是 `LostLevels`。Everest 在原版 `AreaData` 中按 A-side 路径生成 `Celeste/...` SID，然后为存档扩充 SID 字段。证据：本地 Celeste 反编译参考的 `AreaStats.cs`、`AreaKey.cs`、`AreaData.cs`，以及 [Everest AreaData.Load](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaData.cs#L196) 和 [AreaStats.ID/SID](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaStats.cs#L14)。

`iter_map_stats()`、`get_map_stats()`、`get_current_map()`、`get_session()` 以及下述只读查询接受 `vanilla_sids: Mapping[int, str] | None`。`None` 保持仅 SID 查询；显式空映射表示不认识任何原版 ID。`game.vanilla.VANILLA_SIDS` 是标准游戏 ID→SID 的只读映射，应用显式选择它，不作为所有文件默认的猜测表。ID 为非负 Int32，映射键必须是真正整数，SID 必须是非空字符串且不得相互冲突；文件中已存在的 SID 始终优先。映射仅作用于主文件根 `Areas` 内缺 SID 的记录及原版 `CurrentSession`，对 Mod `LevelSets`、回收区、Mod 备份和 `CurrentSession_Safe` 不生效。未知 ID 返回缺失；缺失／非法 ID、会话 mode 和结果身份冲突仍报错，不把损坏与未游玩混为一谈。

`current_map` 保留为无映射的便捷查询；需要映射时调用 `get_current_map(vanilla_sids=...)`。查询映射不附着到文件，不插入 SID，不参与序列化与复制，也不放宽 setter 的定位方式。Pist 在建立浏览索引和当前身份时显式传入标准原版映射，文件层不依赖 Catalog。

`get_personal_bests(sid, side, *, vanilla_sids=None)` 返回 `MapPersonalBests | None`：`time`、`full_clear_time` 为 `Duration | None`，`deaths`、`dashes` 为 `int | None`。不存在地图返回 `None`，地图存在但未建立某项成绩则对应字段为 `None`。独立读取成绩不要求累计字段存在，也不会扩展 `MapStats` setter 的可编辑字段。`get_checkpoints(...)` 独立返回 `frozenset[str] | None`，保存的检查点是房间名集合，而非经过当前地图或 Debug/Cheat/Event 模式处理后的最终可选入口。

`iter_personal_bests(*, vanilla_sids=None)` 一次遍历区域，依次返回 `(sid, side, MapPersonalBests)`，不先调用 `iter_map_stats()`，也不对每条身份重新查询整份文档；累计时间、死亡或收集品字段缺失不阻断成绩导出。它遵循同样的原版映射、回收区域和身份冲突规则，保留尚无有效成绩的地图条目，不把它们过滤掉。

成绩有效性来自游戏实际读写：`SaveData.RegisterCompletion` 在从头游玩结束时设置 `SingleRunCompleted` 并分别更新 `BestTime`、`BestDeaths`、`BestDashes`，全收集时间写入 `BestFullClearTime`；`OuiJournalSpeedrun` 仅显示正 ticks 的时间，`OuiJournalDeaths` 用 `SingleRunCompleted` 判断死亡成绩是否建立。因此正时间独立于完成标志返回；非正时间视为未建立，零死亡／零冲刺在单次完整通关标志存在时有效。缺失字段遵循游戏零／false 初始值，已有但不合法的字段报错。没有 Catalog 或规则输入时不按 `CanFullClear` 隐藏已保存的成绩，也不根据金莓收集推导零死亡。各项最佳值不保证属于同一趟游玩。协议对照来自本地 Celeste 反编译参考的 `AreaModeStats.cs`、`SaveData.cs:270–322`、`OuiJournalSpeedrun.cs:36–43`、`OuiJournalDeaths.cs:46–62`；Everest 的版本迁移还可能清零旧时间而保留单次通关标志，见 [SaveData.AfterInitialize](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L580)。这解释了为什么不能把“已通关”或默认零时间直接视为有效成绩。

这两项新增能力只读取存档已保存的事实；没有成绩编辑、检查点编辑、缺失地图创建、跨存档合并、全局重算或自动落盘。合成回归覆盖原版身份、Mod 隔离、缺失、无效、歧义、计时精度和序列化不变；不会把这些验证描述为游戏加载可玩性验证。

会话编辑先通过边界模型检查输入并生成实际写入的属性与坐标文本，再检查全部目标，最后原位应用编码结果；不创建临时 XML 节点试写，也不重复编码时长。房间名的 XML 1.0 字符合法性由字段约束表达，所有受影响目标仍在第一次修改前完成校验。

Ender's Blender 只以 YAML 节点保存状态，不长期保存解析模型。`parse()` 检查 YAML 语法与根节点类型；统计字段和映射键的校验在查询或编辑时进行，查询一个协议键只解析该键的数据，完整枚举才解析全部受支持统计。解析模型仅作为当前操作的中间结果使用；编辑先完成输入与目标校验、准备字段节点，再更新 YAML，失败或同值编辑不改变状态，显式副本不共享可写数据。

`ModProgressSave.replace_mod_progress()` 整体复制四个共有字段，包括回收列表、空会话和未建模的嵌套内容；不按 `MapStats` 重建 XML，不共享可变子对象，不触及主文件专属字段。该原语只定义替换内容，何时恢复或刷新备份由业务层决定。主文件的实现另外设置 `HasModdedSaveData`。

主文件当前会话优先采用本文件 `CurrentSession_Safe`，缺失时回退本文件旧 `CurrentSession`，不无条件优先使用备份。编辑选中的已有会话时，同步本文件另一份同身份会话，但不创建缺失会话，也不覆盖另一地图的旧会话。

`MainSave.set_map_totals(sid, side, ..., update_global_totals=True)` 修改本文件某张地图的用时和死亡数，并按一次差值联动本文件全局累计；先检查输入精度和范围、目标记录、全局字段及累计溢出／下溢，再应用修改。省略的计数保持原值，没有有效差值时不要求全局字段存在；设置 `update_global_totals=False` 可仅修改地图。低层 `set_map_stats()` 仍只替换指定地图统计，不自动联动全局、会话或备份。

`MapStats` 与 `FirstClearStats` 已从原模块移入 `saves.models`，不复制另一套同义模型。`SessionState` 表达会话身份、房间、复活点和计数；不保存 `storage` 标志，来源由拥有它的文件确定。

所有时长字段和参数使用 `Duration`，读取保留有符号精度，文件序列化才转回协议 ticks。不能整 tick 表达或超出字段范围时拒绝，不隐式舍入、不自动按 17 ms 对齐。游戏状态是否合理由业务调用方决定，不把显示格式或非负时长假设写进通用值类型。

存档边界模型统一复用 `SaveDuration = Annotated[Duration, BeforeValidator(parse_duration)]`，仅附加输入转换规则，不是另一种时长类型，也不再叠加多个时长或 ticks 别名。外部整数或十进制字符串在输入边界校验 Int64 范围并解析为 `Duration`；已经是 `Duration` 的内部值保持原精度。准备写出字段时再显式检查能否无损表示为 ticks，并在修改文档前完成检查，兼顾边界隔离与编辑失败不留部分结果。

CollabUtils2 的保留会话 XML 按需解析；单纯查询进度只需要目标身份，不构造所有完整会话。底层无效输入报错，不伪装成缺失；应用若选择继续，可以把该错误转为可见警告。未来迁移现有消费者时需显式适配这条错误边界，不重新在文件层同时报告 warning 和异常。

Ender's Blender 按原始协议键读取和替换既有统计，不把 `_B`、`_C` 后缀解释成唯一地图身份。是否一起修改这份数据由调用方决定，不从地图／会话累计数推算房间统计。

## 编号与目录层

编号接受非负整数及调试编号 `-1`（主文件为 `debug.celeste`），拒绝布尔值或更小的负数。发现时只接受可按编号重新定位的规范主文件名，忽略 `01.celeste` 等别名；文件名大小写仍按宿主 `Path` 比较，不自行折叠。

`SaveSlot` 固定 `number`、必需的 `main: MainSave`、可选的 `backup: ModBackupSave | None` 与 `mods: Mapping[str, SaveDoc]`。`backup` 是 Everest 平台的配套进度备份，不属于某个具体 Mod，因此不放在 `mods` 中。其他 Mod 文件按实际文件名关联，不按类型或 Mod 名索引，同一种格式也允许有多份文件。新增 Mod 格式不要求给 `SaveSlot` 增加字段。构造时复制映射但借用文件对象，关联只读、文件可编辑；需要完全隔离的文件组时显式调用 `copy()`，不在构造时偷偷复制全部文档。

`SavesDir.available_numbers()` 只按主 `.celeste` 文件发现编号，孤立备份与其他 Mod 文件不构成主存档。`load(number, mod_files=...)` 固定加载主文件，并在 `<number>-modsavedata.celeste` 存在时将其作为 `backup` 加载，缺失为 `None`。其他文件通过实际单文件名到 `type[SaveDoc]` 的映射配置，统一调用各类型的 `read_file()`；不内置具体 Mod 分支或 `include_enders_blender` 等开关。Mod 文件名规则与发现策略由调用方组合：可以根据编号构造名称，也可以先检查 `SavesDir.path` 后提供映射，不引入模板语言或加载插件注册框架。

主文件必须存在；指定但不存在的 Mod 文件不纳入 `mods`，包括备份在内的已存在文件读取失败则报错。文件名必须是保存目录内的单文件名，拒绝空名、路径段及与主文件、Everest 备份或其他 key 的名称冲突；即使没有纳入备份，也不能将它的专用文件名配置为普通 Mod 文件。名称比较遵循宿主 `Path` 规则，不额外 `casefold()`。内存组装不访问文件系统，不承诺识别硬链接或符号链接别名。目录层不自动恢复、不扫描安装 Mod、不缓存游戏进度。孤立文件可以通过文件接口独立检查，不必虚构缺少主文件的 `SaveSlot`。

`SaveSlot.serialize()` 组装全部已纳入文件的序列化结果，返回保存目录相对文件名到 `bytes` 的映射，不维护修改基准，不执行业务协调或磁盘写入。需要只保存变化文件时，由调用方比较编辑前后的结果；重复序列化不会确认或清除修改。文件层不承担“这个文件属于哪个编号”的规则，内存解析的单文件也不需要为了序列化虚构文件名。

## 跨文件组合

当前 `operations.py` 只保留跨文件事实或策略，均有实际函数体：

| 函数 | 组合行为 |
| --- | --- |
| `restore_mod_progress(main, backup)` | 满足 Everest 恢复条件时原位更新主文件；不修改显式备份 |
| `is_in_progress(main, sid, side, collab=...)` | 组合当前身份与可选 Collab 保留身份，不读取全部会话 |

单文件编辑直接使用对应 Save 对象：地图和全局联动使用 `MainSave.set_map_totals()`；会话计数和位置通过 `get_session()` 取得 `SessionState`，用 `dataclasses.replace()` 修改所需字段后交给 `set_session()`；Ender's Blender 直接调用 `set_stats()`。不为计数、位置和不同来源分别再包装一批单文件自由函数，也不通过 `storage` 标志挑选文件。值快照的 `replace()` 不会复制其所属 XML 文档。

备份刷新直接显式调用 `backup.replace_mod_progress(main)`，不为单次方法调用再加转发函数。恢复、主文件编辑、备份刷新是各自独立的内存操作，后一步失败不会撤销前一步；调用方可自行决定是否复制参与文件以获得独立编辑分支，但没有自动复制、跨调用回滚、编辑计划或磁盘提交框架。

地图累计的差值只按当前主记录计算一次；先执行恢复时自然基于恢复后的记录，不分别按主文件和备份计入全局。不根据地图求和补全全局值，不创建缺失地图或全局字段。

主进度编辑后的备份刷新仅针对显式传入的备份，采用整体四字段快照，不保留备份独有的过期地图或会话，不创建缺失备份。这是本接口的编辑协调策略，与游戏保存 Mod 快照的职责一致，但不代表实现了游戏保存前的全部处理。游戏写入路径见 [UserIO](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/UserIO.cs#L163)。

当前会话与 Collab 保留会话是两个位置，即使 SID 与 side 相同也不默认同时修改。单独编辑 Collab 或 Ender's Blender 不触发主文件恢复或备份刷新；不推断收集品、flags 或其他 Mod 状态。

## 使用形态

文件层可以独立读取与原位修改；下面不需要目录或编号层，也不写磁盘：

```python
from berries.game.duration import Duration
from berries.game.saves.files.main import MainSave

main = MainSave.read_file(path)
main.set_totals(time_played=Duration.from_units(hours=1), deaths=56)
contents = main.serialize()
```

目录层只负责加载；恢复、修改和备份刷新均由调用方显式组合：

```python
from berries.game.levels import LevelSide
from berries.game.saves import operations
from berries.game.saves.saves_dir import SavesDir

saves_dir = SavesDir.from_game_dir(game_dir)
slot = saves_dir.load(6)

if slot.backup is not None:
    operations.restore_mod_progress(slot.main, slot.backup)
slot.main.set_map_totals(
    sid,
    LevelSide.A,
    time_played=Duration.from_units(minutes=12, seconds=34),
    deaths=56,
)
if slot.backup is not None:
    slot.backup.replace_mod_progress(slot.main)
contents = slot.serialize()
```

业务函数显式接受精确的主文件和备份类型，不遍历或识别 Mod 容器；目录层加载备份不等于业务层自动恢复它。关联保持不变，修改作用于原文件对象；需要保留原组时在编辑前显式复制。调用方决定是否保存 `contents`、保存到哪里以及如何备份和替换；公共接口不提供 `FileChange`、`SaveEdit`、`SaveWritePlan` 或自动提交能力。不写回就不会改变磁盘存档，但不等于已自动回滚内存编辑。

## 验证与未承诺范围

文件对象不提供 `modified`，也不保存内部修改基准；调用方需要判断变化时，显式比较编辑前后的序列化结果并自行管理基准生命周期。已使用 .NET 实际输出验证缩进、换行、编码和空元素差异，并进行独立 .NET 回读，结果见[后端对比](../finished/save-xml-backends.md)。序列化结果比较仍是文本比较，不是 XML 语义比较：同值写回不重写字段，但实际修改后还原、清除后重建不保证恢复原有文本形式和位置；未知文本中的空白不能直接删除。迁移 XML 后端时必须重跑相关回归，不能把同一后端的普通往返通过视为完整证明。

组合测试使用真实编解码对象验证恢复条件、主文件字段保留、显式恢复后的累计修正及备份刷新。文件层测试直接构造合成 XML/YAML，检查序列化后重新读取、精度与范围、未知字段和命名空间保留、原始协议键、会话按需解析、显式复制隔离、单次编辑失败隔离及 `read_file()` 不写回。文件组与目录测试覆盖编号发现、缺失与损坏文件、文件名冲突、开放的 Mod 文件类型、复制隔离、完整序列化和无磁盘写入；不以这些测试证明游戏加载后的完整行为。

2026-10-17 另外对本机编号 6 的四种文件做了只读内存往返：主文件 4416 条地图统计、备份 4383 条地图统计、CollabUtils2 29 条会话、Ender's Blender 200 个协议键，重新解析后的受支持数据一致。没有向游戏目录写入结果，也未用游戏加载修改后的文件。

2026-10-18 迁移应用读取流程后，以旧实现为基线只读核对编号 6：4416 条地图统计、当前身份及 29 条保留会话查询一致，四份文件的 SHA-256 未变化。本机 Windows／CPython 3.14.7，交替执行旧／新加载各 5 次，热缓存中位数分别约 128 ms／139 ms；此数据只覆盖该样本，不宣称迁移提升性能，切换仍保留单路加载及最新目标合并。

完整地图／Campaign 跨编号迁移、缺失记录创建、最佳成绩合并、收集品合并或推断、录像时间估算、任意房间可玩性验证及磁盘写入事务均未实现，也不作为这版基础接口的隐含承诺。`AGENTS.md` 的边界已允许内存编辑和序列化，磁盘写回与备份策略仍由调用方负责。
