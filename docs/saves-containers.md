# 存档容器与成员关系

状态：截至 2026-10-21，本文描述的容器、分区索引和调用方接入已实现。非法值的独立修复接口仍未设计和实现。完整领域 API 见 [存档领域 API](saves-domain-api.md)。

## 普通数据与容器协议

Ender's Blender 的 `first_clear`、`last_saved` 都是 `dict[str, RoomStats]`。键是不可反解的原始协议键；通过公开的 `room_key(sid, side)` 显式编码 SID／side。查询和修改使用原生下标赋值、del、get、update、copy、keys／values／items。加载时转换所有已建模统计，字典只保存 RoomStats；非法值的独立修复接口另行设计。

Campaign 集合与 Level 成员集合是组合 KeyedDict 的领域 Mapping，不继承 KeyedDict。按 key 迭代，成员判断针对 key，keys／values／items 为活视图；add 覆盖同身份成员，adddefault 保留已有项，replace 要求存在，del／pop／clear 移除成员。所有写入走统一预检与安装过程，不能将内部可写字典暴露给调用方。普通进度字段和 level.maps 仍可直接编辑，不自动调整父累计。

pop(key) 在成员缺失时抛出 KeyError；pop(key, default) 和 pop(key, default=...) 在缺失时原样返回 default，包括 None、False 等假值，与 KeyedDict／CaseFoldDict 的调用约定一致。key 仅限位置传入，default 可按位置或关键字传入；实现使用缺省哨兵区分未提供默认值与显式默认值。

## 分区、引用与修改入口

### 为什么需要这些约束

这里的 Campaign 对应 Everest 的 LevelSet，身份字段为 `CampaignProgress.name`（下文也称 campaign_name）；Level 身份字段是 SID。两者都是精确、区分大小写的标识，不按宿主路径规范化。对非空 SID，最后一个 `/` 前的原始文本就是其 Campaign 名称，例如 `Pack/Sub/Map` 属于 `Pack/Sub`。地图的数字 Area ID 则是当前运行时 AreaData.Areas 中的位置，Mod 启用状态改变后可能变化；初始化会按 SID 查当前地图，再刷新数字 ID，不能把数字 ID 当作持久身份。

Everest 的活动区和回收区都是 List，没有数据库式唯一约束，也没有统一的重复进度合并。单纯能够反序列化重复记录，不代表随后正常启用、禁用和保存能保住所有历史。以下例子是固定版本源码的逐步推导，尚未作为游戏端到端运行验证；它们说明拒绝危险输出的具体依据，不宣称已经覆盖所有 Mod 的不变量。

#### 同一区内 Campaign 名称重复

活动区若为 `[A1, A2]`，两者同名，初始回收区没有同名项，且当前地图集被整体禁用，初始化先处理 A1，将其放入回收区；接着处理 A2 时，又删除回收区第一个同名项 A1，再追加 A2。该次回收循环按当前列表顺序处理，活动区排序在循环之后；因此直接读取此输入并整体禁用时确定留下 A2。下一次保存后 A1 不在任一区中，没有发生进度合并。若初始回收区已有两个同名项 `[R1, R2]`，本次回收会分别删除 R1、R2，得到 `[A1, A2]`，不会当场丢掉 A1，但仍留下回收区内部重复。

回收区若为 `[R1, R2]`，恢复只取第一个同名项 R1，将其移入活动区并从回收区删除；R2 仍保留。如果随后禁用该地图集，回收流程会删除 R2，再放回 R1，保存后 R2 丢失。另一种组合是活动区 A、回收区 `[R1, R2]`：禁用后变为回收区 `[R2, A]`；重新启用恢复 R2，回收区留下 A；再次禁用则删除 A，最终只剩 R2。它不仅会丢历史，还会恢复到并非原活动进度的记录。

相反，活动区 A、回收区 R 且每区内部各自唯一时，禁用会用 A 替换 R，重新启用恢复 A，不会发生上述错误历史轮换。替换旧回收历史 R 是 Everest 选择的明确行为。因此要求的是每区名称唯一，不是两区合起来唯一；跨区同名本身不判为非法。

#### 同一 Campaign 内 Level SID 重复

假定 Pack 有两张已加载地图 Map1、Map2，当前数字 ID 为 11、12，存档成员却为 `[Map1进度P, Map1重复进度Q, Map2进度R]`。初始化按 SID 更新 ID 并排序，得到 `[P(11), Q(11), R(12)]`。补位循环检查位置 0 的预期 ID 11，吻合；位置 1 的预期 ID 12，却读到 Q 的 11，于是在位置 1 插入新建的 Map2 空进度，得到 `[P(11), Map2空(12), Q(11), R(12)]`。保存前只保留该 Campaign 实际根地图数 2，连续裁掉尾部，最终是 `[P, Map2空]`。Q 被删除，原本有效的 R 也被删除；相同 ID 的排序不保证 P 和 Q 谁先，所以 Map1 的哪份历史留下也没有可靠承诺。

此处重复的是存档进度记录，不是资源文件。多个 Mod 提供同 SID 的地图定义时，AreaData.Load 会保留数组中后出现的定义，最终只有一个地图身份，不应据此生成两份存档进度；这不是对任意 Mod 加载优先级的保证。Mod 覆盖原版 SID 时归属仍是 Celeste，而不是另一份独立的 Mod Campaign。

#### Level SID 与所属 Campaign 不一致

假定已加载 Alpha/Map、Beta/Map，当前数字 ID 分别为 11、12，各自 Campaign 只有一张地图。若 Beta 中错放一条 Alpha/Map 进度 Q，初始化按 Q 的 SID 找到 Alpha 地图，将其 ID 更新为 11；Beta 的 offset 为 12，补位时发现位置 0 的 11 不等于预期 12，于是插入 Beta/Map 空进度，成为 `[Beta空(12), Q(11)]`。保存前按 Beta 的根地图数 1 裁尾，Q 被删除，不会移回 Alpha，也不会合并到 Alpha 已有进度。两张地图都保持启用，加载后保存就足以触发此路径，不需要额外启停操作。

如果有多个不同 Campaign，且每个 Level 的 SID 都与所属 Campaign 匹配，那么同一个 SID 不可能同时属于两个 Campaign；所以每区 Campaign 名称唯一、各 Campaign 内 SID 唯一、SID 归属正确三个约束共同保证分区内 SID 唯一。扁平索引仍在构造时检查全区重复，以便宽松输入中的错放记录不能把冲突延迟到查询时才暴露。

#### 独立 Mod 共用 Campaign 的另一种风险

即使存档没有重复或错放，两个 Mod 分别提供 Pack/A1、Pack/A2 和 Pack/B1、Pack/B2，只禁用其中一个时，Pack 仍有已加载地图，不会整组回收。找不到的地图进度被设为 int.MaxValue、排序后当作 leftovers 删除；保存后这部分历史丢失。若两个 Mod 同时禁用，Pack 整体移入回收区，保留的是当时尚存的成员。若交替禁用并保存，两个 Mod 的历史都可能先后被清理。回收区保护的是整个 Campaign，不是每个临时消失的 Level。

这不能靠存档唯一性检查阻止，因为该输入本身完全正常；独立启停的地图包应使用独立 Campaign 命名空间，或明确协调生命周期和存档兼容性。存档库不扫描安装目录猜测谁的地图稍后会恢复，也不暗中改变 Everest 的启停行为。

#### 源码依据与验证边界

依据 Everest 提交 `790e3a54112c5a18f12e51b338ddeac2c6bfcd8e` 的 [SaveData.AfterInitialize](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L425)：恢复使用 FirstOrDefault 并移除同一个对象；整体回收删除回收区第一个同名项再追加；成员按 SID 刷新 ID、排序、删除未找到的地图，并按 offset 补位。[BeforeSave](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/SaveData.cs#L609) 按当前根地图数裁剪尾部；[AreaData.Load](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaData.cs#L319) 合并同 SID 的地图定义，LevelSet getter 按 SID 派生归属。示例数字 11、12 是为说明机制假设的运行时索引，不是固定章节编号；示例不含 Parent 子章节，以免将 Parent 的共享进度规则混入补位过程。

### 索引和修改契约

活动区（原版及活动 Mod）和回收区独立管理。每个分区内 Campaign 名称唯一、Level SID 唯一；跨区允许同名 Campaign 和同 SID 的独立历史。严格加载拒绝区内重复和归属不符；宽松加载在加载边界通过独立构造参数 `on_campaign_conflict`／`on_level_conflict` 逐项消歧，查询直接使用已建索引。宽松读取允许观察唯一但错放的记录，写出仍拒绝归属不符；新增和替换不得引入新的归属错误。

Campaign 持有其成员字典，分区的 SID 索引保存同一批 Level 对象的额外引用，必须满足 `campaign.levels[sid] is save.flat_levels[sid]`。直接修改 Level 字段无需同步；增删、替换成员则由成员管理逻辑预检并同步所有相关索引。不能让原始 dict 或普通 KeyedDict 成为绕过协调的写入路径。

MainSave 的 `flat_levels`、`recycled_flat_levels` 为 property，前者覆盖原版和活动 Mod，后者仅回收区；mods 视图的对应属性仅覆盖 Mod 两区。vanilla／mods 是根字段的绑定视图，不可整体赋值；标量及受控集合变动直接作用于根字段，安装准备好的进度后既有视图继续投影新数据。扁平集合提供 Mapping 查询及 add／adddefault／replace／update／del／pop／clear 等受控写入；已有 Campaign 的 levels 同样支持写入，两种入口使用相同协调机制。扁平新增由 SID 推导父 Campaign，父不存在时报 KeyError，不隐式创建。访问 side 使用 `save.flat_levels[sid].maps[side]`，缺项和 None 由调用方显式处理。

LevelMembers 和 CampaignMembers 保存有序进度，同类容器之间的值比较包含成员顺序，避免展开为根字段后忽略存档中的顺序变化；与普通 Mapping 比较时仍采用映射值语义，类似 OrderedDict。根构造检查原版成员容器的 Celeste 身份及 Mod 容器所属分区；宽松读取保留的成员归属问题仍可观察和修复，不因容器身份检查而丢弃。

成员结构在加载时完成索引与冲突检查，查询不重新扫描或生成字典。局部修改只准备受影响成员的索引增量，所有校验必须在安装前完成；失败不改变任何集合，成功后就地更新索引存储，使已取得的集合及其视图看到新成员。结构写入不维护字段统计累计，不自动执行 save 操作。

扁平索引按自身的插入顺序迭代：首次构建按来源及成员顺序装配，后续新增 SID 追加，替换仍存在的 SID 保持位置，删除后重新添加则追加。索引不保证始终按 Campaign 分组；Campaign 成员自身的顺序不受其他 Campaign 修改影响。整体根替换会按新来源重新构建索引顺序；需要特定展示顺序时由调用方显式排序。

Campaign 集合本身为 getter-only，禁止整体赋值，替换某个 Campaign 使用 replace；Campaign.levels 和 MainSave.vanilla／mods 保留受控整体替换，先验证新成员关系再安装，旧成员索引一并移除。原版进度的特殊写出约束不变。任何快照恢复和进度转移都通过同样的成员入口，不直接更新内部存储。回收区不能被默认为活动区的兜底查询。

整体替换同样适用于 ModBackupSave.mods：先预检两个分区的归属及索引，全部通过才安装，任何失败都保留旧根、已取得的索引及其活视图。宽松加载是另一阶段：先收集主文件原版及活动 Mod 的完整分区，再统一处理跨 Campaign 的 SID 冲突，避免正确原版父结构尚未加入时提前拒绝修复；独立 Mod 快照和备份只处理自身分区。普通 LevelMembers／CampaignMembers 构造不提供绕过归属检查的参数或工厂；保留异常输入的装配仅供解码实现使用。LevelIndex 类型可用于注解和类型判断，实例从根属性取得，其接收内部协调状态的构造器不是公共使用契约。

归属校验只检查本次新安装的部分：替换 MainSave.vanilla 不要求尚未修改的 mods 已修复，替换 MainSave.mods 也不要求保留的 vanilla 已修复，允许宽松读取后按任意顺序逐部分修复。SID 唯一性与索引预检仍检查替换后的完整活动分区，包括保留部分，不能引入新冲突。mods 替换中的新活动区与新回收区均属于待安装部分，仍须全部验证后一起安装；保留的错放记录只允许继续观察和修复，写出前仍须全部修复。

Pist 的浏览统计仍需要展示禁用地图历史，因此由应用显式遍历两个分区；同 SID 时活动区优先，只有活动区没有的 side 才取回收区记录。这是浏览投影策略，不是 Berries 的隐式跨区合并或存档写入行为。

## 成员与索引的协调

根级批量安装按“实际目标成员容器＋Campaign 名称”检查写入位置，而不只检查 MainSave 对象。不同根共享 Mods 或其中的 CampaignMembers 时，同一位置只能出现一次请求；即使重复提交同一个候选，也在成员、索引和 Mod 标记改变之前以 ProgressConflictError 拒绝。原版 vanilla 替换的实际位置是各根自己的 vanilla 字段，共享旧 Campaign 值不表示两个根写入同一位置；不同 Mod 成员容器共享旧 Campaign 值也不构成重复写入。

跨根业务安装使用 `MainSave.install_campaigns((save, campaign), ...)`：每个根只接收一次同名 Campaign 的候选替换，先完成全部根的相关约束和全部关联索引预检，再统一修改成员，更新 Mod 标记。它不调整用时、死亡数或收集品累计，也不写文件；operations 在成员安装成功后安装已经准备好的累计和伴随数据。不能逐端提交后才检查下一端；预检失败时已持有的成员、根、索引及活视图均保持原状。原版替换仍只检查待安装原版的归属，Mod 替换检查替换后的活动与回收分区，不要求保留的原版进度先修复。

`CampaignMembers.update_together((members, campaign), ...)` 将多个容器的候选集中预检，同一关联索引只生成一次覆盖整批变更的增量，通过后才安装成员及索引。它是返回 None 的批量修改入口，不向调用方暴露可独立 apply 的索引计划；普通单容器 add／update 的接口不变。

CampaignMembers 和 LevelMembers 分别实现自身的领域 Mapping，组合 KeyedDict 保存成员。CampaignMembers 安装 Campaign 时检查名称及其 Level 归属，LevelMembers 安装 Level 时检查 SID；两者各自调用分区协调器明确的 Campaign 成员变更或 Level 成员变更预检入口。LevelIndexState 独立实现索引协调，不继承成员容器；对外 LevelIndex 的修改转给实际成员容器，不能只修改派生索引。

成员归属校验由对应容器的 validate_member／validate_ownership 方法负责；私有的 LevelMembers._prepare_replacement 为 Campaign.levels 替换准备所有关联索引的预检结果，返回的内部变更对象及其 apply 操作不属于公共 API。解码实现通过私有的 LevelMembers._from_decoded／CampaignMembers._from_decoded 装配已经处理身份与冲突的输入，宽松读取可以保留错放成员；这两个类方法是内部装配约定，不是公共 API，不替代普通新增、替换或根安装时的归属校验。

成员容器登记直接依赖自己的分区协调器。同一个 Campaign 或成员容器可以被多个根共享，因此该关系允许关联多个协调器，而非唯一父对象；每次修改先让全部相关协调器准备增量，全部成功后才写入成员并安装索引。扁平批量修改跨越多个 Campaign 时，先集中准备本次新增、替换或删除的成员，再检查它们与现有索引及彼此之间的冲突，不复制每个父容器的完整字典，也不逐父提交部分结果。

add 直接校验单个值并提交其增量，不经过 update 的输入遍历与汇总；update 收集并校验全部输入后统一提交，不循环调用 add。两者复用相同的归属校验和索引预检／安装逻辑；批量输入或关联索引的校验失败时，不留下先前成员已写入的部分结果。

关联采用弱引用保存协调器，不因外部仍持有某个成员容器就使已无使用者的索引一直存活。协调器安装新结构时解除不再参与的关联，登记新成员；普通字段编辑无需通知。集合复制独立保存本层关系，不复制源协调器登记。

真实成员容器没有关联索引时，删除、pop、popitem 和 clear 直接操作内部 KeyedDict；update 仍先验证全部新值，再写入存储。有关联索引时，结构修改必须完成统一预检再安装。这个快速路径只适用于实际成员存储；扁平索引的删除始终回到所属 Campaign，不能因它不是成员容器就跳过协调。

单条 Level 增删或替换按 SID 查询既有索引，只更新该条成员和归属，不扫描其他 Campaign 或 Level，也不重新登记全部关联。设相关索引数为 R，哈希表通常条件下时间和临时空间为 O(R)；忽略标识字符串长度及字典扩容的摊销成本。Campaign 增删或替换，以及 Campaign.levels 整体替换，只遍历受影响的新旧成员；其数量为 K 时成本为 O(R(K + 1))。初次加载和整个分区／根替换才构建完整索引。clear 的成本与实际移除量相关，批量 update 的成本与本次输入及相关索引的数量相关，不能通过循环单条操作隐式退化成反复全区扫描。

## 复制和共享

集合浅复制独立保存本层成员关系，仍共享成员对象。Campaign 浅复制仍共享 levels，显式替换其 levels 可以脱离原集合；深复制得到独立成员和成员关系。成员管理的订阅不属于领域数据，复制集合不能复制源索引的订阅，也不能递归复制整个存档。若普通引用被多个根共享，所有已登记索引都须同步预检和更新，不因共享而悄悄改变普通 Python 引用语义；跨存档转移继续由既有重叠检查拒绝危险共享。

上述集合复制指 CampaignMembers 和 LevelMembers；LevelIndex 绑定根结构，不提供独立副本语义，copy／deepcopy 明确拒绝。仅需脱离根的 SID 查询快照时使用 dict(index)，需要独立进度时再深复制该字典中的领域值，不通过复制索引隐式复制根和协调状态。

成员容器的构造器支持从已有同类 Members 浅复制：LevelMembers(name, members) 要求目标与来源 name 相同才走复制路径，CampaignMembers(members) 直接复制成员存储；索引关联重新初始化为空，不复制实例字典。__copy__ 仅转发到构造器，不再自行组装内部状态。复制保留宽松读取时尚未修复的成员，不重新进行归属校验；普通可迭代输入或不同 name 的 LevelMembers 输入仍须校验，需要安装到现有根或写出时也仍须通过相应校验。深复制在初始化空容器后登记 memo，再深复制成员存储，保留同一次复制中的共享引用关系，不先建立无用的浅副本。

从分区移除 Campaign 后，外部持有的 Campaign 和 Level 不失效，也不清空历史。旧根不再订阅已脱离的成员；修改旧对象不能重新进入旧索引。由同一根替换得到的当前扁平集合仍跟随该根，不跟随已被替换的旧快照。

## 示例

```python
from copy import deepcopy

campaign = save.mods.campaigns['Pack']
campaign.levels.add(LevelProgress('Pack/New'))
assert save.flat_levels['Pack/New'] is campaign.levels['Pack/New']

save.flat_levels.replace(LevelProgress('Pack/New'))
assert save.flat_levels['Pack/New'] is campaign.levels['Pack/New']
del save.flat_levels['Pack/New']
assert 'Pack/New' not in campaign.levels

# 同 SID 的两份历史必须显式选择分区。
active = save.flat_levels['Pack/Map']
recycled = save.recycled_flat_levels['Pack/Map']

branch = deepcopy(campaign)
branch.levels.clear()
assert campaign.levels

key = room_key('Pack/Map', LevelSide.B)
blender.first_clear[key] = RoomStats()
stats = blender.first_clear.get(key)
```

## 验证范围

回归须覆盖加载时消歧、分区独立、全部容器修改路径、双向引用一致性、整体替换、旧引用脱离、浅复制／深复制及失败前状态不变。未知标签和编码存在性由编解码规则保留。当前 API 不提供非法数值修复回调。
