# 地图与内容来源类型重构（已归档）

> 状态：已完成并归档。本文记录 2026-09-28 时的最终设计快照，后续不再更新。
> 当前实现始终以源码和测试为准。路径身份的长期约定见
> [路径与标识符比较语义](../path-identity-semantics.md)。

## 重构目标与结果

本轮重构解决了地图扫描、内容访问、来源追踪和 Campaign 组织之间职责混杂的问题：

- 用类型表达游戏 Content、目录 Mod 和 ZIP Mod 的物理来源差异；
- 统一 `Maps`、`Dialog` 等 Everest 内容树的只读访问方式；
- 分开可持久化的扫描事实与带文件句柄的短生命周期访问对象；
- 分开地图扫描元数据、活动地图、Level/Campaign 组合关系和覆盖诊断；
- 消除 `LoadedMap`、`LoadedCampaign`、`LoadedLobby` 等不必要的 `Loaded` 类型层级；
- 不让 `Map` 反向持有 Mod，也不以 Vanilla/Mod 子类区分地图；
- 将大厅身份落实为 `Level.is_lobby`，因为它描述组装后的 SID，而不是单个地图文件。

这些目标均已落地。

## 最终领域结构

地图目录采用组合关系：

```text
Campaign
└── Level
    └── Map
        ├── MapInfo
        └── ContentSource
```

- `MapInfo` 只表示扫描到的地图资产，保存经校验的 `file_path: ContentPath`。
- `Map` 关联一个活动 `MapInfo` 与提供该资产的 `ContentSource`，并通过 `open_content()`
  打开对应内容树。
- `Level` 保存最终 SID、Dialog key、连续的 A/B/C Side 地图以及 `is_lobby`。
- `Campaign` 保存一个 Everest level set 对应的目录、名称身份和有序 Levels。
- `CampaignCatalog` 保存可见/隐藏 Campaign、地图覆盖诊断、最终 Dialog 和当前快照中的
  内容来源索引。

这里没有 `VanillaMap`、`ModMap` 或 Lobby Map 子类。地图本身只关联具体生效的 `.bin` 资源；
来源名称和 Mod 身份由 `CampaignCatalog.mod_for()`、`source_name_for()` 和
`campaign_uses_mod()` 查询。这样反向映射属于组装结果快照，而不是单个地图对象的固有职责。

原版与 Mod Campaign 的差异由 `Campaign.source` 表达；它不改变内部 Map 的类型。

## 内容来源与内容访问

`ContentSource` 是结构化 `Protocol`，不是要求实现类名义继承的领域基类：

```text
ContentSource
├── GameContent
└── InstalledMod
    ├── DirMod
    └── ZipMod
```

- `GameContent` 表示当前游戏安装的 `Content` 目录。
- `InstalledMod` 保存 manifest、Dialog、地图扫描结果和物理包路径等事实。
- `DirMod` 与 `ZipMod` 只负责各自的物理载体差异。
- `source` discriminator 只服务于 Pydantic 扫描报告；业务代码不比较该字符串来选择访问方式。

`ContentPath` 是 Everest 虚拟内容树中的精确相对路径：使用 `/`，区分大小写，且身份语义不随
宿主平台变化。它拒绝绝对路径、空片段、`.`、`..` 和结尾分隔符。地图字段在此基础上额外
要求位于 `Maps/` 下并使用 `.bin` 扩展名。

`ContentEntry` 是类似只读 `pathlib.Path` 的内容节点抽象：

```text
ContentEntry
├── DirContentEntry
└── ZipContentEntry
```

它提供 `joinpath`、`iterdir`、`walk`、`read_bytes`、`exists`、`is_file` 和 `is_dir` 等结构操作。
目录和 ZIP 节点保持同一虚拟路径语义。

物理后端通过具体 Entry 显式暴露：

- `DirContentEntry.path` 返回真实目录或文件路径；
- `ZipContentEntry.zip_file` 返回当前打开的 ZIP；
- `entry_fingerprint()` 和 `invalid_archive_member_paths()` 保持为自由函数，直接处理真实物理
  目标，而不伪装成通用 `ContentEntry` 行为。

来源对象不长期持有打开的 Entry。调用方以有界生命周期读取内容：

```python
with source.open() as root:
    data = (root / asset_path).read_bytes()
```

ZIP 根节点和派生节点共享同一个打开的 archive、成员索引和关闭状态；退出上下文后关闭 ZIP，
扫描报告不序列化句柄或索引。

## Level 与 Side 组装

Pist 使用 Celeste Mod 社区语义：Campaign 对应 level set，Level 是 Campaign 中共享 SID 和
Dialog key、并包含 A/B/C Side 的条目。

`Level` 按 A/B/C 顺序保存一个到三个 `Map`。`LevelSide` 负责稳定索引，不增加 `SideMap` 或
`LevelMap` 包装层。

Mod 文件名中的 `-B` / `-C` 只是组装候选后缀，不是扫描阶段即可确定的 Side：

- A+B 或 A+B+C 会组装成同一 Level 的连续 Side；
- A+C 不跳过缺失的 B，未匹配文件成为独立 Level 的 A Side；
- 孤立 `Map-B.bin` 或 `Map-C.bin` 也成为自己的 Level A Side；
- SID、Dialog key 和最终 Side 因此不存入 `MapInfo`。

原版地图使用其数字与 H/X 命名约定单独组装，但输出相同的 `Level` 和 `Map` 类型。

## Dialog 与显示文本

扫描层不保存某次 Dialog 合并产生的文本快照。Campaign/Level 组装完成后，在实际显示边界从
最终合并的 Dialog 查询：

- Level 名称来自其 `dialog_key`；
- 作者和 Collab tags 来自相应后缀 key；
- Campaign 名称按 Everest level set key 规则解析；
- 缺失文本时使用稳定的文件或目录 fallback name。

GameBanana credits 是独立数据源，不与 Dialog author 文本混为一个领域字段。

## Campaign 与覆盖关系

Campaign 是应用 Everest 加载顺序和同路径覆盖之后的组装结果，不属于某个单独 Mod：

- 先按 manifest 依赖和内容 crawl 顺序确定参与加载的物理包；
- 同一虚拟地图路径由后加载资源覆盖先加载资源；
- 再把最终活动地图组装为 Level 和 Campaign；
- 一个 Campaign 因而可以包含来自多个物理 Mod 的地图。

`MapOverride` 只记录地图资源的先前来源和最终替代来源，属于 `CampaignCatalog.overrides`，
不混入 `MapInfo`、`Map` 或 Campaign 身份。

原版 Content 组装为独立官图 Campaign。直接位于 `Maps/` 下的 Mod 地图进入未分类 Campaign；
普通 Mod 地图以 `Maps/` 下直接父目录作为 Campaign 边界。

## Collab Lobby

大厅身份由 CollabUtils2 对最终组装 Level SID 的精确规则决定，并记录在 `Level.is_lobby`：

- `0-Prologue` 明确不是 Lobby；
- Lobby 身份适用于整个 Level，因此其 A/B/C Side 不需要分别转换成子类；
- `Campaign` 不保存 Lobby 字段，因为同一 `0-Lobbies` Campaign 还可以包含普通 Level；
- 具体 Lobby Side 的日志引用在展开时按需解析。

Lobby 日志中的 `JournalTrigger.levelset` 是 Campaign 身份引用。解析保持精确、区分大小写，按
源顺序去重；缺失、空值或找不到 Campaign 时只产生诊断，不猜测同名目录。

日志引用缓存只保存可重建的原始 Campaign 引用和诊断计数。UI 会针对当前
`CampaignCatalog` 重新解析和排序，不把缓存当成领域身份来源。

共享与本地 `collab_lobbies.toml` 可以按“Lobby SID + Side”提供完整覆盖；本地条目整体替换共享
条目。该配置只改变 UI 的 Lobby 投影，不改变 Campaign、Level、Map 或实际地图入口关系。

## 完成后的关键约束

- 物理路径使用 `Path`；Everest 虚拟资源路径使用 `ContentPath`；SID 和 Dialog key 保持字符串
  身份，不以 `casefold()` 改写。
- `MapInfo` 是扫描事实，`Map` 是活动资产，`Level` 和 `Campaign` 是组合关系。
- Map 不知道自己是否来自 Mod；来源反查由 `CampaignCatalog` 当前快照提供。
- Lobby 是 `Level.is_lobby`，不是 Map、Campaign 或独立子类。
- 内容访问保持短生命周期，ZIP 必须及时关闭。
- UI 消费最终 Catalog，不自行复制 Everest 加载、覆盖或 Level/Side 组装规则。
- 可持久化缓存只保存可重建事实，不成为领域身份或 UI 排序的唯一来源。

## 相关落地提交

- `145f8ee`：明确地图集加载顺序与物理包身份。
- `9de8a2a`：拆分地图布局与个人路线职责。
- `434ec77`：公开内容条目的真实物理后端。
- `97729fb`：移除 `Loaded*` 地图层级，收敛 Map、Level、Campaign、Lobby 与规则模型职责。

归档后不再在本文追踪后续架构变化。
