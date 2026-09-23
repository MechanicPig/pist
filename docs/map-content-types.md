# 地图与内容来源类型梳理

本文记录地图扫描、内容访问、来源追踪和地图集组织相关类型的最终结构与设计依据。
下述领域模型均已实现。路径身份的底层约定仍以
[路径与标识符比较语义](path-identity-semantics.md) 为准。

## 目标

- 用类型表达游戏 Content、目录 Mod 和 ZIP Mod 的来源差异，不在业务代码中通过字符串判断来源。
- 统一三种来源下的 `Maps`、`Dialog` 等内容树访问方式。
- 分开可持久化的扫描事实与带文件句柄的短生命周期访问对象。
- 分开地图扫描元数据、活动来源、地图集组织和覆盖诊断，避免一个模型混入多个阶段的职责。
- 明确虚拟资源路径与宿主文件系统路径的不同身份语义。

## 背景与旧实现问题

### `game.mods.LocalMap`

`LocalMap` 表示一个已扫描地图文件及其本地化信息，包含：

- `file_path`、`dialog_key`、`sid` 和 B/C 面；
- 地图显示名称；
- `<dialog_key>_author` 对应的本地化作者文本；
- `<dialog_key>_collabcreditstags` 对应的合集标签。

`author_texts` 来自最终合并的游戏 Dialog，不是 GameBanana credit，也不是用户筛选后的作者；
`collab_credit_tags` 同样是 Dialog 数据。`names`、`author_texts` 和 `collab_credit_tags` 都不是地图
文件的扫描事实，不应保存在地图信息模型中。SID、Side 和 Dialog key 也只有经过 Level 组装
后才能确定，不属于单个扫描资产。

### `game.campaigns.LoadedMap`

旧 `LoadedMap` 通过三个字段表达两种互斥状态：

```python
mod: InstalledMod | None
map_info: LocalMap
content_dir: Path | None
```

Mod 地图使用 `mod`，原版地图使用 `content_dir`。这种结构需要运行时断言，并把来源差异和物理
路径拼接暴露给所有调用方。

### `game.mods.InstalledMod`

旧 `InstalledMod` 同时保存 `source: 'zip' | 'directory'` 与物理 `path`，调用方据此选择 ZIP 或
目录访问逻辑。这是用字符串模拟子类型，并造成重复的来源分支。扫描报告所需的 discriminator
与业务控制流没有被分开。

### `game.mods.LocalCampaign`

旧 `LocalCampaign` 保存 `directory`、`dialog_key`、`kind` 和 `maps`，并作为 `InstalledMod` 的
字段存在。它描述的是单个物理 Mod 扫描出的目录，不能直接表达经过依赖顺序与内容覆盖后、可能
由多个 Mod 共同提供地图的最终 Campaign。

旧 `LoadedCampaign` 还同时保存普通地图、`lobbies` 和 `collab_map_groups`。这些目录推导出的
Collab 分组不能表达大厅各 Side 中日志实际引用的 Campaign，也容易让目录组织与 UI 展开结构
成为另一套关系来源。

## 当前实现结构

### `game.maps.MapInfo`

`MapInfo` 只表示一个扫描到的地图资产，唯一字段是经过校验的 `file_path: ContentPath`。
路径必须位于 `Maps/` 下并使用 `.bin` 扩展名。

- SID、Side 和 Dialog key 由 Level 组装产生，不属于单个扫描资产；
- 显示名称、作者和合集标签在使用时从最终合并的 Dialog 字典查询；
- 原版与 Mod 的差异属于活动来源，因此不为 `MapInfo` 建立空的来源子类。

### `game.levels.LoadedMap`

`LoadedMap` 表示经过 Everest 内容覆盖解析后的活动地图资产，并由两个子类表达互斥来源：

```text
LoadedMap
├── LoadedVanillaMap(info, content: GameContent)
└── LoadedModMap(info, mod: InstalledMod)
```

调用方通过 `open_content()` 读取资产，不再组合可空的 `mod` / `content_dir` 字段。

### `game.mods.InstalledMod`

`InstalledMod` 是公共基类，`DirMod` 和 `ZipMod` 表达物理载体并各自实现内容访问。
`source` discriminator 仅供 Pydantic 扫描报告序列化；业务控制流通过多态或具体类型收窄，
不比较该字符串。

### `game.campaigns.MapOverride`

它不是地图模型，而是 Everest 风格内容加载产生的诊断记录：多个已加载 Mod 提供完全相同的
虚拟地图路径时，后扫描的资源替换先扫描的资源。它保存先前来源和最终替代来源，供浏览器提示
覆盖关系。

该能力位于 `CampaignCatalog.overrides`，不混入 `MapInfo`。覆盖诊断的范围明确限定为地图
资源，因此使用 `MapOverride`，不抽象成通用资源覆盖类型。

## 已确认的设计决策

### 内容来源与内容路径分层

来源对象保存可持久化事实。`ContentSource` 是结构化 `Protocol`，下图表示实现同一访问协议，
不是名义继承关系：

```text
ContentSource
├── GameContent
└── InstalledMod
    ├── DirMod
    └── ZipMod
```

- `GameContent` 表示当前游戏安装中的 `Content` 内容根。
- `DirMod` 表示 Mods 目录下的一个目录 Mod。
- `ZipMod` 表示 Mods 目录下的一个 ZIP Mod。
- `InstalledMod` 持有 manifest、Dialog 和扫描结果等 Mod 事实。
- 子类负责创建正确的内容访问对象；调用方不检查字符串类型，也不通过后缀猜测来源。

纯值类型 `ContentPath` 表示内容树中的相对虚拟路径。它可以表示内容树根、目录或文件，并承担
精确且区分大小写的相等、哈希和结构操作。

访问对象表示绑定到具体内容来源的一个节点：

```text
ContentEntry
├── DirContentEntry
└── ZipContentEntry
```

采用 `DirContentEntry`，不使用 `DirectoryContentEntry`。复合名称中的 `directory` 默认缩写为
`dir`；只有它单独作为一个标识符时，才使用完整的 `directory`，避免与内置函数 `dir()` 冲突。

`ContentEntry` 表示“给定一个目录或 ZIP archive，以及其中的 `at` 路径，访问相应资源”。
其 `at: ContentPath` 表示该节点位于内容树中的位置。它提供
类似只读 `pathlib` 的结构操作，例如 `joinpath`、`iterdir`、`read_bytes`、`exists`、`is_file`
和 `is_dir`。

游戏 Content 与目录 Mod 具有相同的内容布局，因此都使用 `DirContentEntry`；ZIP Mod 使用
`ZipContentEntry`。地图和 Dialog 解析器不应分别维护原版与 Mod 的文件遍历实现。

### 资源生命周期

`InstalledMod` 和 `GameContent` 不长期持有打开的 `ContentEntry`：

```python
with source.open() as root:
    data = (root / asset_path).read_bytes()
```

- `DirContentEntry` 的上下文退出无需释放实际句柄。
- `ZipContentEntry` 的根和所有子节点共享同一个 `ZipFile`、条目索引和关闭状态。
- 离开上下文后关闭 ZIP，避免 Pist 长期占用 Mod 文件。
- 扫描报告只序列化来源事实，不序列化打开的 archive、索引或路径访问状态。

### 路径语义

地图的 `file_path` 是 Everest 虚拟资源路径，不是宿主文件系统路径。目录 Mod 与 ZIP Mod 在这里
使用同一套身份语义：

- 使用 `/`；
- 精确且区分大小写；
- 不随运行平台改变相等或哈希行为；
- 只有通过 `DirContentEntry` 访问物理文件时，最终查找才遵循宿主文件系统规则。

因此该字段不能改成平台相关的 `Path`。`ContentPath` 作为通用值类型，表达 Everest 内容树内的
虚拟路径，避免裸 `str` 被误当作物理路径或被无意 `casefold()`。`MapFilePath` 是带
`AfterValidator` 的 `ContentPath` 字段类型，额外要求路径位于 `Maps/` 下且使用 `.bin` 扩展名；
这些地图约束不进入通用 `ContentPath`，也不通过没有额外运行时行为的空子类表达。
`ContentPath` 继承 `PurePosixPath` 以复用结构操作；无参数构造或单独的空字符串表示内容树根。
其他构造输入会在规范化前校验，拒绝路径内部的空片段、`.`、`..`、绝对路径和末尾分隔符，
避免 `PurePosixPath` 静默改变输入身份。

### Level/Side 解析

Pist 的领域命名优先与 Celeste Mod 社区用语对齐，不照搬 Celeste/Everest 的源码类型名。
Campaign 与社区所称的 Level Set 对应；Campaign 中共享 SID 和 Dialog key、并拥有 A/B/C Side
的地图条目称为 `Level`。源码中的 `AreaData` / `AreaMode` 只在外部协议边界使用；运行时
`Celeste.Level` 是加载 Room 的 gameplay scene，也不决定 Pist 的静态领域命名。

`Level` 直接保存从 `LevelSide` 到 `LoadedMap` 的映射。`LevelSide` 只是 A/B/C 枚举；不为这一
映射关系建立 `SideMap` 或 `LevelMap` 包装类型。Mod 地图文件名中的 `-B` / `-C` 只是 Level
组装时的候选 Side 后缀，不是该文件独立、稳定的 Side。Pist 先以无 Side 后缀的地图作为基础，
再从完整候选集合中查找对应 Side。因此：

- 孤立 `Map-B.bin` 仍是自己的 Level，并作为该 Level 的 A Side；
- 完整 A+B 组合中，`Map-B.bin` 才是 A 地图所在 Level 的 B Side；
- 完整 A+B+C 组合会组装为同一 Level 的三个 Side；
- A+C、B+C 等不完整组合不推断缺失 Side，未匹配的文件各自成为独立 Level 的 A Side。

所以扫描层的 `MapInfo` 不应保存或提供最终 `side`、`sid` 与 `dialog_key`。文件名只暴露
`MapSideSuffix`，用于 Level 组装，不得当作最终 `LevelSide`。最终 SID、Side 和 Dialog key
属于 Everest 式加载结果。

### Dialog 解析

扫描层的地图信息不保存 Dialog key，也不保存某次 Dialog 合并产生的文本快照。
加载层先解出地图实际归属的 Level/SID，再由该身份派生 Dialog key。
以下文本都在实际使用时，按当前语言设置的优先级从最终合并的 Dialog 字典查询：

- 地图显示名称：`<dialog_key>`；
- 作者文本：`<dialog_key>_author`；
- 合集标签：`<dialog_key>_collabcreditstags`。

地图 B/C 面的后缀显示以及缺失 Dialog 时的 fallback name 在查询层处理。GameBanana
credit 属于另一数据源，不得与 Dialog author 文本合并为同一字段。

### 活动地图类型

以子类型表达地图来源：

```text
LoadedMap
├── LoadedVanillaMap
│   ├── info: MapInfo
│   └── content: GameContent
└── LoadedModMap
    ├── info: MapInfo
    └── mod: InstalledMod
```

`LoadedVanillaMap` 持有 `GameContent`，与 `LoadedModMap` 持有 `InstalledMod` 对称。两者都通过来源
对象打开资源，而不是自行拼接物理路径。`Level.maps_by_side` 在活动地图之上表达最终 SID、
Dialog key 与 A/B/C Side；UI 直接消费组装结果，不再自行重组文件名。

### Level 命名

`Chapter` 偏向原版的有序叙事章节，不能自然覆盖无序合集、独立 Mod 图和大厅。Pist 使用社区
更通用的 `Level` 表示 Campaign 中的地图条目，并采用社区中与 Level Set 同义的 `Campaign`
表达外层边界。Campaign 内的 Level 顺序有意义，但 Campaign 不具有数学或 Python set 语义。

### Campaign 语义

Campaign 表示一个 Everest campaign / level set。对 Mod 地图，其身份由地图在 `Maps/` 下的
直接父目录决定；它包含该目录下经 Everest 式 Level/Side 解析后的有序 `Level`。Everest
Wiki 在地图选择界面中也将 campaigns 与 level sets 并列为同义词：
[First Custom Map](https://github.com/EverestAPI/Resources/wiki/First-Custom-Map#playing-your-map)。

Campaign 是加载结果，不属于某个单独 Mod：

- 先按 Everest 的依赖与内容加载顺序决定生效资产；
- 同路径地图由后加载的 Mod 覆盖；
- 再将最终生效的地图组装为 Level 和 Campaign；
- 因此一个 Campaign 可以包含来自不同 Mod 的最终地图。

Collab 不改变 Campaign 的目录边界。例如：

```text
Maps/StrawberryJam2021/0-Lobbies       -> 一个 Campaign
Maps/StrawberryJam2021/1-Beginner      -> 另一个 Campaign
Maps/StrawberryJam2021/2-Intermediate  -> 另一个 Campaign
```

### Collab Lobby 与日志引用

Pist 在普通地图集中显示 `0-Lobbies` Campaign。Campaign 和 Level 组装完成后，按
CollabUtils2 对 Lobby SID 的判断识别其中的大厅 Level；该判断不要求存在同名 Campaign。
识别出的 Level 转换为 `CollabLobby` 子类，并分别保存各 Side 的日志所引用的 Campaign：

```python
class CollabLobby(Level):
    campaigns_by_side: Mapping[LevelSide, tuple[Campaign, ...]]
```

`CollabUtils2/JournalTrigger` 的 `levelset` 属性一次引用一个 Everest Level Set。一个大厅可以
在每个 Side 中放置多份日志并引用多个 Campaign；Pist 分别扫描各 Side 的日志，并在该 Side
内部按 Campaign 身份去重。Level Set 身份遵循精确、区分大小写的字符串比较，不使用 Dialog
key 或显示名称匹配。这里表达的是当前大厅 Side 提供的日志入口，不表示 Campaign 归大厅
所有，也不保证覆盖该 Side 通过其他 trigger/entity 实际能够路由到的全部地图。

Lobby 转换必须在 Everest 的 Level/Side 组装之后，按最终 Level 的精确 SID 逐个进行。
Pist 不直接把每个大厅 `.bin` 转换成 Lobby，也不额外按基础文件名强制合并 Lobby：只有被
Pist 组装进同一 Level 的 Side 才属于同一 Level；未被组装的 `-B`、`-C` 地图仍是独立
Level。用户切换 Lobby 的 A/B/C Side 时，同时切换大厅地图和当前 Side 的日志引用，展开
列表据此重新生成；不同 Side 之间不合并日志集合，也不按 Side 文件名推断同名目录。

例如同时存在：

```text
Maps/Example/0-Lobbies/1-Beginner.bin
Maps/Example/0-Lobbies/1-Beginner-B.bin
Maps/Example/1-Beginner/...
Maps/Example/1-Beginner-B/...
```

Pist 把前两个文件组装为 SID 为 `Example/0-Lobbies/1-Beginner` 的一个 Level，其中
包含 A/B Side，因此形成一个 `CollabLobby`。A 面展开哪些 Campaign 只由 A 面大厅地图内的
日志决定，B 面同理；两者可以分别引用上述两个 Campaign，也可以引用完全不同的目录。
文件名相似本身不建立关联。

`CollabLobby` 仍是 `0-Lobbies` Campaign 拥有的 Level；被引用 Campaign 的目录身份与 Levels
保持不变。除 `0-Lobbies` 外的 Collab Campaign 统一保留在隐藏地图集中，即使它已被
Lobby 日志引用；Lobby 展开只是同一 Campaign 的另一个访问入口。这样日志遗漏、错误引用或
无法静态识别的实际路由都不会让 Campaign 从 UI 中彻底消失。实际入口关系仍由地图预览的
路由模式独立呈现。

UI 不暴露 Lobby 引用的日志或 Campaign 目录层级。展开 Lobby 时，先按日志引用顺序解析
Campaign，并按最终 Level/Side 身份保留首次出现的地图。每个 Campaign 独立应用 CollabUtils2
图标排序规则，缺少合规图标只会让当前 Campaign 保持原顺序；随后按引用顺序连接各组，最后
针对整个扁平列表稳定地按当前存档的地图进度排序。同进度地图因此保留 Campaign 内的图标顺序
以及 Campaign 之间的引用顺序。领域模型按 Side 保留有序的 Campaign 引用，不把扁平展示结构
写入数据关系。

该展开只投影一层当前 Side 所引用 Campaign 中的 Levels，不继续展开其中可能出现的
`CollabLobby` 引用。即使日志指向 `0-Lobbies` 或另一个包含 Lobby 的 Campaign，也只生成地图
行，不递归构建树或路由图。

Lobby 默认折叠，因此日志引用采用按需解析和持久化缓存：

- 构建 Campaign 与 Lobby 列表时不为日志无条件解析大厅 `.bin`；
- 用户第一次展开 Lobby 时，只解析当前 Side 的地图；切换到尚未解析的 Side 后再解析该 Side；
- `collab_journal_refs` 缓存按大厅地图资源保存 `JournalTrigger.levelset` 中按源顺序出现并去重后
  的原始字符串，在领域层称为 `campaign_refs`；不保存已解析的 Campaign 对象、扁平 Level
  列表或当前存档相关排序；
- 在新的浏览会话中读取持久化缓存后，仍针对该会话的最终 Campaign 索引解析 Level Set，并
  重新执行地图去重与 Collab 列表排序；同一浏览会话内会复用 `CollabLobby` 已解析的结果，
  因为该会话的 Campaign 索引保持不变；
- 缓存身份包含最终生效的地图资源路径及其内容来源，失效规则与日志图标缓存一致：提供该资源的
  Mod 文件或目录发生变化后重新解析；被其他 Mod 覆盖而切换最终来源时不得复用旧来源结果；
- 持久化缓存只保存可重建的扫描事实，不改变 Campaign、Lobby 或日志引用的领域身份。

极少数 Collab 没有在大厅中提供完整的 `JournalTrigger`。这类已知例外通过
`src/pist/data/collab_lobbies.toml` 声明，大厅身份使用精确 SID，并按 Side 分别列出日志格式的
Campaign 引用：

```toml
[[lobbies]]
lobby = "Example/0-Lobbies/1-Easy"
side = "A"
campaigns = ["Example/1-Easy", "Example/1-Easy-Extra"]
```

配置项存在时，其 `campaigns` 是该大厅 Side 的完整投影，完全替代地图内日志的自动查找；显式
空列表表示该 Side 不展示子地图。没有对应配置项时才按需读取地图并访问日志缓存。A/B/C Side
互不覆盖。用户可在 `.pist/collab_lobbies.toml` 中增加或替换同一“大厅 SID + Side”的本地配置；
本地条目整体替换共享条目，不与其列表合并。配置只覆盖 UI 的静态 Lobby 投影，不改变 Campaign
身份、隐藏地图集或地图预览中的实际路线关系。

地图浏览器通过 Lobby 地图项的右键菜单打开该配置的多选编辑器。当前 Side 没有显式配置时，
编辑器先按正常惰性路径读取日志，并以解析出的有效 Campaign 作为初始选择；这次读取仍可使用
持久化日志缓存。保存后写入完整投影，并立即使该 Side 已解析的内存投影失效；已展开的 Lobby
随即按新配置刷新。源码工作区可以选择保存到本地或共享配置；保存到共享配置时会移除同一项的
本地覆盖，避免刚写入的共享结果继续被遮蔽。安装包模式不显示任何共享配置操作。编辑器的“重置”
只移除本地覆盖，使共享配置或自动日志查找重新生效；源码工作区的“重置共享配置”不改动本地配置。

日志引用采用保守失败行为：

- 当前 Side 没有有效日志引用时，Lobby 没有子地图列表，不按同名目录或 Side 文件名回退；
- `levelset` 缺失、为空或找不到最终 Campaign 时，不创建虚假关联，也不阻断地图浏览；
- 无效引用形成可见诊断，用户仍可从隐藏地图集访问未关联的 Campaign；
- 多份日志重复引用同一 Campaign，以及多个 Campaign 最终包含同一 Level 时，均按最终
  身份去重。

`0-Lobbies` 中不被 CollabUtils2 视为 Lobby 的 Level（例如序章）保持普通 `Level`。

原版 Content 组装为独立的“原版地图” Campaign；直接位于 `Maps/` 下的 Mod 地图组装为
“未归类地图” Campaign。普通 Mod 地图仍严格使用直接父目录作为 Campaign 身份。

## 最终职责边界

- `ContentPath` 是精确、可序列化的虚拟路径值；`MapFilePath` 通过字段校验补充地图约束。
- `ContentEntry` 负责短生命周期资源访问，`GameContent` / `InstalledMod` 负责可持久化来源事实。
- `MapInfo` 只保存扫描事实；`LoadedMap` 保存最终活动来源；`Level` 保存 SID 及 Side 到地图的映射；
  `LoadedCampaign` 保存目录级地图集；`CampaignCatalog` 保存可见/隐藏地图集和覆盖诊断。
- `Loaded` 前缀特指已经过 Everest 加载顺序、依赖和覆盖规则解析的活动结果。
- UI 只消费 `CampaignCatalog` 与懒解析的 `CollabLobby` 日志投影，不维护另一套 Campaign 或
  Collab 目录组模型。
- 日志图标缓存与大厅日志引用缓存只保存可重建事实，不成为领域身份或 UI 排序的唯一来源。
