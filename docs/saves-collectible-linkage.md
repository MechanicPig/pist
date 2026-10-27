# 存档收集品联动：金草莓与 Summit Gems

状态：2026-10-21 的源码调研，实施更新至 2026-10-22。金莓累计及 Summit Gems 持久历史联动已实现；原版 Session 宝石数组支持独立编辑。主存档／Collab 会话快照及附带 Mod 载荷已支持转移，独立 modsession 文件组提供显式原生文件操作，不按 Mod 解析或另建会话模型。DoNotLoad 与宝石等事件语义的完整联动仍待实施，不宣称完整游戏恢复已完成。当前存档操作见 [存档领域 API](saves-domain-api.md)，实施待办见 [TODO](TODO.md)。

## 背景与职责

跨存档复制、移动、覆盖或叠加地图进度时，仅处理地图的草莓集合、用时和死亡数不足以覆盖所有收集历史。金草莓还涉及全局累计，Summit Gems 则分别存在原版主存档、原版会话和 CommunalHelper 的持久数据及会话中。

地图实体负责确定存档键及其含义，存档中的对应值才表示实际收集状态。二者不能混为一谈：地图包含某个实体，不表示已收集；Summit Gems 也不保存在地图进度的已收集草莓集合中。

实体识别复用现有规则匹配机制，不另写一套按名称前缀或未经确认的属性猜测收集品类别的逻辑。实体解释层负责输出精确的分类和身份映射；文件层负责读写真实字段；operations 接收显式事实和范围，协调领域修改，不自行扫描 Catalog、加载地图或操作磁盘。本次不添加新的实体规则类别或自动整理接口，参与的 custom 宝石键由调用方提供。

## 金草莓

### 游戏数据与联动方向

地图进度的 `Strawberries` 保存 EntityID，没有为每条记录保存红莓／金莓类别。原版 `SaveData.AddStrawberry()` 在首次加入实体时增加草莓累计，并在 golden 参数成立时增加根 `TotalGoldenStrawberries`。Everest 的 LevelSet 金莓数则是派生查询：将已收集 EntityID 与当前地图的 Goldenberries、DashlessGoldenberries 定义匹配，不是另一个需要写出的地图集累计字段。

操作应根据规则识别出的金莓实例，对修改前后的已收集集合计算差量，并调整全局累计，而非重算整个存档。merge 只增加目标新增的金莓；overwrite 扣除被覆盖范围的金莓，再计入来源；move 和 clear 还需扣除源侧被清除的金莓。原有累计与可解释实例之间的历史差额应保留。

当前地图文件不保证包含全部历史分类事实。地图更新可能删除实体、改变 ID 或复用 ID；找不到的已收集历史 ID 不能直接归为非金莓。源、目标的数据来源版本不同时，也不能假定一份当前分类表适用于双方。实现前需明确缺少可靠分类事实时的拒绝或显式补充机制。

收集实体分类不能代替通关事件语义。不因金莓 ID 存在就自动设置最佳死亡数、SingleRunCompleted 或其他成绩，相关联动需另行核对实际注册路径。

依据：本地原版反编译 `.pist/references/Celeste/Celeste/Celeste/SaveData.cs` 的 AddStrawberry、RegisterCompletion；Everest `790e3a54112c5a18f12e51b338ddeac2c6bfcd8e` 的 [LevelSetStats.TotalGoldenStrawberries](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L704) 与 [选档显示统计](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/OuiFileSelectSlot.cs#L129)。

### 已实现接口：金莓累计

`MainSave.total_goldenberries: int` 对应根字段 `TotalGoldenStrawberries`，是跨 Campaign 的全局金莓累计。普通草莓累计仍由 `CampaignProgress.total_strawberries` 管理：`main.vanilla.total_strawberries` 对应主文件根 `TotalStrawberries`，各 Mod Campaign 则对应自己的 LevelSetStats 字段。主存档不增加一套重复的草莓累计入口；`MapProgress.strawberries` 继续表示实例集合，而不是数量。字段直接赋值不联动地图或其他累计，写出边界检查非负 Int32 范围；严格输入要求根金莓累计字段存在且非 nil，宽松输入缺失时采用游戏的整数初始值零。

所有 Map／Level／Campaign 的 copy、move、merge、overwrite、clear 接收必选 keyword 参数 `goldenberry_ids`，公共别名从 `berries.game.saves.operations` 导入：

```python
type GoldenberryIDs = Mapping[MapKey, AbstractSet[MapEntityID]]
```

外层只负责按 `(SID, side)` 分组，避免不同地图或 side 中相同房间名和实体 ID 混淆；内层是纯金莓 ID 集合，没有 `ID -> bool` 分类映射，也不分两套 source／target 参数。未列出的 ID 不计入金莓数，缺失分组等价于空集合。调用方负责提供涵盖本次源、目标操作范围的完整集合；不知道某条历史记录是否为金莓时如何处理，由外部整理或审计决定，操作层不为未知类别抛错，也不自行读取地图、匹配规则或猜测类别。两侧历史版本对同 ID 的含义不同时，先由调用方解决身份／分类歧义，本接口不声称可以恢复丢失的版本信息。

实体事实可以来自现有 `check_collected_entity_rules()`，并结合调用方掌握的旧版地图或人工结论整理。不为这次存档操作再增加分类器，也不依据 `special` 父类别或名称前缀猜测金莓。后续若提供快捷整理函数，它也应返回相同的集合事实，未知问题留在整理阶段。

```python
from berries.game.saves.operations import GoldenberryIDs, move_map_progress

goldenberry_ids: GoldenberryIDs = {
    (sid, LevelSide.A): {
        MapEntityID('room_a', 11),
        MapEntityID('old_room', 3),
    },
}

move_map_progress(
    source,
    target,
    sid,
    LevelSide.A,
    poem_id=None,
    mode='overwrite',
    goldenberry_ids=goldenberry_ids,
)
```

未涉及金莓时显式传 `goldenberry_ids={}`，表示调用方声明本次不包含金莓，而不是请求自动推断。判空接口不需要此参数：莓实例非空已经足以证明相应 Map 非空；全档金莓历史也不能分摊为某个局部范围的非空证据。

令 S 为源范围的已收集 ID、T 为目标原范围的已收集 ID、G(X) 为 X 与对应分组金莓集合的交集数量；逐 `(SID, side)` 计算后相加：

| 操作 | 目标全局金莓累计差量 | 源全局金莓累计差量 |
| --- | --- | --- |
| merge／merge copy | G(S − T) | 0 |
| overwrite／overwrite copy | G(S) − G(T) | 0 |
| move | 按所选 merge／overwrite 规则 | −G(S) |
| clear | 无目标 | −G(S) |

整 Campaign 覆盖将目标独有、被覆盖掉的 Level 纳入 T，不能只遍历源 Level。exclusive 先按已有局部判空契约检查目标，再按覆盖差量处理。同身份实例在 merge 时不重复计数；不同 side 的相同 MapEntityID 则是不同记录，分别计算。原有全局累计与当前可解释实例数的历史差额保留，不将累计重算为当前集合数量。

累计范围、原有进度和会话冲突等在提交前预检；金莓扣减为负数或增加后溢出时报错，不钳制数值。直接编辑 Map 的莓集合或调用其 reset() 不会自动修改全局累计；需要联动时使用上述业务操作。当前会话冲突策略不因金莓字段而放宽，也没有借此添加会话转移能力。

回归覆盖字段写出／重读、严格缺失字段与宽松默认值、非法写出计数、全部转移模式与范围、重叠去重、整集覆盖删除目标独有 Level、跨 side 计数、历史累计差额，以及两端累计预检失败前状态不变。不涉及金莓的既有操作测试显式提供空集合映射；未列入的实体仍可转移，不由操作层判定分类未知。独立 .NET 回读与真实游戏加载是不同验证，不以 Python 自身读回替代后两者。

## 原版 summitgem

### 身份与收集写入

实体的 `gem` 属性表示原版六个宝石位置之一，正常值为 0–5；实体本身还具有房间名与 EntityID。收集时同时更新：

| 状态 | 写入内容 | 语义 |
| --- | --- | --- |
| 原版 Session.DoNotLoad | 实体的房间名与 EntityID | 本次会话不重复加载该实体 |
| 原版 Session.SummitGems | 对应位置设为 true | 本次游玩的收集情况 |
| 主存档 SaveData.SummitGems | 对应位置设为 true；原数组为 null 时先建立六项数组 | 跨会话收集历史 |

此流程不将宝石加入 `AreaModeStats.Strawberries`。没有保留会话时，不能借用该草莓集合重建本次宝石收集状态；持久历史从主存档 SummitGems 读取。

### Manager 的使用方式

本次会话已经取得水晶之心时，原版 SummitGemManager 结束宝石解锁流程。否则，它根据 `Session.OldStats.Modes[0].HeartGem` 判断是否曾经拿心：尚未拿心时接受本次会话或主存档历史中的宝石；历史已拿心时只接受本次会话收集。Manager 播放宝石碎裂并推进心的解锁，不通过清除主存档宝石位来要求下次重新收集。

原版 Session.FullClear 还对山顶章节检查本次会话是否收齐六颗宝石。因此持久收集历史与本次收集状态须分别建模，不能把主存档历史直接填入 Session，或仅根据历史宝石齐全就设置全收集成绩。

依据：本地原版反编译 `.pist/references/Celeste/Celeste/Celeste/SummitGem.cs` 的构造器与 SmashRoutine、`SaveData.cs` 的 RegisterSummitGem、`SummitGemManager.cs` 的 Routine、`Session.cs` 的 FullClear 与 HasAllSummitGems。Everest 的 [Session 构造](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/Session.cs#L90) 同样初始化六项 SummitGems。这些原版反编译文件是本地调研材料，不是项目发行资源。

## CommunalHelper/CustomSummitGem

核对版本：CommunalHelper `ac542ed1ba2be0592347e4cba70b0297e2e7e933`。以下是该版本的源码行为，不宣称所有历史版本或第三方 Hook 都相同。

### 身份与存储

CustomSummitGem 继承原版 SummitGem，但通过 Hook 使用自己的收集流程。其持久身份为：

```text
最终地图 SID / 房间名 / index
```

这里的 index 不是实体 EntityID；身份不包含 side。源码使用原始 index 生成身份，颜色等展示上的 0–7 限制不能套到存档键上。SID 和房间名按来源保留，不用宿主路径工具规范化。

收集时向原版 Session.DoNotLoad 加入实体 EntityID，向 CommunalHelperSession.SummitGems 与 CommunalHelperSaveData.SummitGems 加入上述字符串；不设置原版的两组 SummitGems 布尔数组。两组字符串集合分别代表本次会话和持久历史，源码采用 `SortedSet<string>` 及 `StringComparer.InvariantCulture`；后续编解码与集合接口须核对其比较语义，不能未经确认套用 casefold 或路径相等规则。

CommunalHelper 的持久数据位于对应编号的 `modsave-CommunalHelper` 文件，会话由独立的 `modsession-CommunalHelper` 文件保存。CollabUtils2 还会保存和恢复各 Mod 的序列化会话；完整会话联动不能只处理主文件中的原版 Session。

依据：[实体身份与收集](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/Entities/HeartGems/SummitGem.cs#L39)、[持久集合](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/CommunalHelperSaveData.cs)、[会话集合](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/CommunalHelperSession.cs)、[模块类型声明](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/CommunalHelperModule.cs#L22)。文件定位来自 Everest 的 [模块会话读写](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Module/EverestModule.cs#L333)，Collab 的附加会话保留路径见本地 `.pist/references/CollabUtils2/UI/ReturnToLobbyHelper.cs` 的 SerializeSession／DeserializeSession 调用。

### Custom Manager 的跨地图读取

CustomSummitGemManager 的 gemIds 可引用当前地图或其他地图的宝石。源码按逗号拆分并去掉各项前后空白；某项包含少于两个斜杠时，将其作为相对引用，在前面补当前地图 SID。

本次会话已拿心时结束流程；否则，每颗宝石先检查 CommunalHelper 会话集合。历史尚未拿心，或者宝石来自其他 SID 时，也接受持久集合中的记录。历史已拿心且宝石来自当前地图时，只接受本次会话收集。跨地图判断从键中去掉最后两个部分，再与当前 SID 比较，不按资源提供者或 Mod 包名判断。

Manager 读取其他地图的宝石不意味着拥有那些宝石。移动某张地图时，不得把其 Manager 引用的所有宝石都随之移动；应从实际宝石实体确定参与键，再读取对应集合中的收集状态。碎裂动画不会删除持久集合中的记录。

依据：[引用解析与使用条件](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/Entities/HeartGems/SummitGemManager.cs#L26)、[跨地图判断](https://github.com/CommunalHelper/CommunalHelper/blob/ac542ed1ba2be0592347e4cba70b0297e2e7e933/src/Entities/HeartGems/SummitGemManager.cs#L185)。

## 支持假设与后续边界

正常原版内容的六颗宝石都属于 7A，不存在原版地图之间共享宝石位置的问题。本项目先采用以下支持假设：原版标准 7A 的 summitgem 联动主存档宝石位；Mod 的宝石使用 CommunalHelper/CustomSummitGem，联动 CommunalHelper 数据。这是双方确认的产品支持范围，不是引擎限制，也不是对全部已发布地图的事实断言。

若实际发现 Mod 地图使用原版 summitgem，应报告超出支持假设，不自动改动原版宝石进度，也不把它当成 custom 实体。地图归属与 side 应使用最终身份；地图文件由 Mod 提供并不必然意味着其 SID 不属于原版，覆盖原版内容的情况须在实施时明确验证边界，不能只按 ModMap 类型分流。

Custom 身份没有 side，不同 side 使用相同 SID、房间名与 index 时会共享同一记录。单 side 操作只处理调用方显式列出的键；将共享键列入某个 side 即表示允许移动／清除这条共享历史，不代表该键是此 side 私有的。调用方负责评估其他 side 的使用，接口不自动猜测或阻止；不希望移动共享历史时，不将其列入参与键。地图改版后的旧键不能因当前扫描未发现就自动删除。

持久历史层已实现按位／集合的合并、覆盖、复制和移动；会话层必须同时考虑对应宝石集合、DoNotLoad、拿心及 FullClear 消费逻辑，不能复制历史状态冒充本次收集。当前会话及保留会话的完整联动仍未实现。所有参与对象应在修改前完成预检，文件写出与跨文件失败恢复仍由调用方负责。

## 已实现的 Summit Gems 接口

`MainSave.summit_gems: list[bool] | None` 对应原版主存档历史；尚未收集时为 None，不能与六项 false 在文件表示上混为一谈。严格读取非 null 数组要求六项，宽松读取保留异常长度以便修复；写出要求 None 或恰好六个 bool，不补项、裁剪或转换数字。缺失和 nil 按游戏反序列化语义得到 None；nil 中的子项不生效。手工置为 None 时移除已有非 null 数组。

`Session.summit_gems: list[bool] | None` 是本次会话，不是主存档历史。缺失时采用游戏构造器默认的六项 false，显式 nil 则保留 None 供修复；写出只允许六项非 null 数组。列表原地修改会被 iter_changed_fields() 识别，恢复原值后不再视作修改，主文件的同身份双会话编码会协调此字段。赋值不自动改变 DoNotLoad、HeartGem、FullClear 或主存档历史。缺失字段且未修改时保持缺失，不为此改变无关 XML。

`CommunalHelperSave` 从 `berries.game.saves.files` 导入，继承单文件读写接口，`mod_name = 'CommunalHelper'`，可用 `slot.mod(CommunalHelperSave)` 定位。其 `summit_gems: set[str] | None` 保留持久集合与 null 的区别，未知 YAML 内容及其别名保留原值，不随已解释集合的编辑变化；这里不读取 modsession 文件。字符串保持原文，不解析为路径、不把数值样式键转为数字。输出采用确定的 Python 字符串排序，不承诺复刻 .NET InvariantCulture 的排列或 Unicode 文化比较等价；潜在文化比较碰撞由调用方处理，不能将这里的精确字符串比较宣称为完整 SortedSet 比较器模拟。

标准原版 7A 拥有六个持久宝石位，因此 Map／Level／Campaign 操作包含该 Map 时自动联动六项；B/C 和其他原版地图不改它。merge 按位或，overwrite 使用源历史，move 成功后清除源六项，clear 仅清除所选范围；目标有宝石历史时 exclusive 拒绝。源从未收集且目标被覆盖时可回到 None，不从存档莓集合推测是否取得宝石。覆盖原版 7A 而改变宝石归属的 Mod 不在此内置范围规则的支持假设内，需要调用方另行处理。

Custom 联动是显式可选能力：转移入口成对接收 `source_communal`／`target_communal`，并提供 `custom_summit_gem_ids: CustomSummitGemIDs`；三者必须同时提供或同时省略。clear／is_empty_* 使用单个 `communal` 及同一映射。省略时不加载或修改任何 CommunalHelper 文件，不隐式猜测参与键。别名从 operations 导入，其定义为：

```python
type CustomSummitGemIDs = Mapping[MapKey, AbstractSet[str]]
```

```python
from berries.game.saves.files import CommunalHelperSave
from berries.game.saves.operations import CustomSummitGemIDs, move_map_progress

gem_ids: CustomSummitGemIDs = {
    (sid, LevelSide.A): {f'{sid}/room_a/0', f'{sid}/old_room/9'},
}
source_custom = source_slot.mod(CommunalHelperSave)
target_custom = target_slot.mod(CommunalHelperSave)
move_map_progress(
    source,
    target,
    sid,
    LevelSide.A,
    mode='merge',
    poem_id=None,
    goldenberry_ids=goldenberry_ids,
    custom_summit_gem_ids=gem_ids,
    source_communal=source_custom,
    target_communal=target_custom,
)
# 仅修改传入的内存文档；四个文件的写出与恢复安排由调用方负责。
```

merge 只并入源已收集且被选中的键；overwrite 先移除目标所选范围的键，再并入来源；move 再移除源所选键，copy 不改源；clear 只移除显式键。整 Campaign 范围按映射分组 SID 的最终归属选取所有显式键，包括已经没有 Level 记录的旧地图，以及目标独有、将被覆盖的历史；不从 custom 字符串本身或 Manager 反推归属。Map／Level 仍按指定身份选择，范围外和未提供的历史始终保留。主存档本身没有该 Campaign 时，判空仍检查这些显式历史；clear／copy／move 等写操作保留父 Campaign 必须存在的契约，来源父缺失时抛出 KeyError 且不修改任何文档，不隐式创建来源结构或静默忽略宝石。Manager 引用其他地图不赋予这些键的归属，操作不解析 Manager。exclusive 和 is_empty_* 将参与范围内的宝石历史纳入判空；共享文档／集合、参数配对及累计等预检失败不修改参与文档。

### 2026-10-22：审查后的失败与 XML 边界

copy／move／clear 的预检覆盖成员安装及全部相关索引，不只计算累计和编码候选；所有参与根的检查通过后才安装，不能在源已清空或目标已替换后再进行可能失败的根校验。Mod 标记仍随成功安装更新。该保证是同步内存操作的预检契约，不是多个磁盘文件的写入事务。

XML 布尔词法使用小写 true／false、1／0，允许首尾 XML 空白（空格、tab、CR、LF），拒绝大写拼写和非 XML 空白，严格／宽松输入一致；不再因保留原文而写出游戏无法解析的 TRUE。Ender's Blender YAML 的大小写布尔拼写由其 YAML 边界处理，不随 XML 限制一并收窄。

数组编辑按位置复用已有 boolean 节点，仅更新已解释文本，保留该位置的未知属性、注释和命名空间上下文；宽松异常长度修复时才新增缺失位置或移除显式裁掉的位置。双会话编码先构造已镜像修改的 legacy 候选，再完整校验／编码一次；同身份 legacy 的 nil 或异常长度可借 safe 的显式数组修改修复，不改变来源 Session 或 baseline。不同身份不镜像，未改字段仍保留各自值。

新增回归覆盖 null、异常数组修复、Session 原地修改与双会话编码、YAML 原文标量与未知别名、全部转移模式／范围、目标独有键、共享 side 的显式选择、判空与失败不变。.NET XmlSerializer 的独立布尔数组探针确认主存档 null 字段省略输出、缺失／nil 得到 null、显式空数组得到零项，非法空 boolean 值会报错；这不等于启动游戏验证完整流程。

金莓与宝石持久历史联动已通过合成存档回归与写出回读验证。主存档活动会话、Collab 保留会话及其文本／binary Mod 载荷已支持成组转移和显式 discard 冲突处理，接口与边界见[会话随进度处理](saves-domain-api.md#会话随进度处理)；不重新累计会话统计，不模拟退出事件。独立 modsession 文件由调用方显式组合[文件组接口](saves-domain-api.md#独立-mod-会话文件组)处理，整组原生复制／移动／删除，不解析内容、不提供磁盘事务或自动回滚。完整的宝石／DoNotLoad 等事件联动仍待实现。以上均未通过真实游戏加载验证，也未修改真实存档；实际游戏读取及恢复行为仍需按后续变更风险另行验证。
