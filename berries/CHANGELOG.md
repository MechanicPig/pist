# Berries 更新日志

本文件记录 Berries 公共包的重要变更；Pist 应用的发布记录见 [Pist 更新日志](../CHANGELOG.md)。0.3.0 起独立记录发布说明；更早的变更保留在仓库历史和 Pist 更新日志中。

## 0.3.0 — 2026-10-27

### 存档读取与编辑

- 提供主存档、Everest Mod 进度备份、CollabUtils2、Ender's Blender 与 CommunalHelper 存档的领域模型及显式读写能力；一次性读取、内存编辑、显式写出，不持有文件会话，不自动保存或备份。
- 提供 `SavesDir`、`SaveSlots` 与 `SaveSlot`，协调槽位定位、空槽查找及伴随文件；槽位序列缓存范围并支持显式失效。
- 按 Campaign、Level、Map 组织进度，提供活动区与回收区的实时索引、冲突回调和受约束的成员修改；原版进度通过专用视图访问。
- 支持地图、Level 与 Campaign 进度的复制、移动、合并、覆盖和清空，联动死亡数、时长、草莓与金草莓计数、诗句、Summit Gems 和显式指定的 Custom Summit Gem 键。
- 支持主存档活动会话与 Collab 保留会话转移、目标会话冲突处理，以及独立 Mod session 文件的复制、移动与清理。跨文件操作不提供事务或自动回滚。
- 主会话使用完整领域对象，Collab 保留会话按需解析；Ender's Blender 房间统计使用普通字典。
- 严格读取限定游戏正常输出的数据；宽松读取允许游戏可读取的异常结构以便修复，写出拒绝已知会导致错误行为的不一致。未知字段采用报错或警告策略，不承诺保留未知内容。
- XML 改用 lxml，并统一声明式字段编解码；严格模式校验游戏计时的 17 ms 步长，RTA 计时除外。`LastSave` 以只读文本保留。

### 精确时长

- `berries.game.duration.Duration` 使用有理数秒无损表示正负时长，提供 .NET ticks、游戏计时帧、`timedelta` 的显式转换及精度对齐。
- 支持分量构造与查询、算术与比较、`strftime()`／`strptime()` 和 `__format__()`；格式支持累计小时及可选分支，内置游戏计时器显示格式。

### 地图、Mod 与通用能力

- 明确平台读取与应用策略的边界：扫描错误通过回调交给调用方，展示策略和缓存指纹不再属于公共游戏模型。
- 合并地图模型并以 `VanillaMap`／`ModMap` 表达来源；Mod 地图直接持有所属 Mod。地图集由 `CampaignCatalog` 协调加载。
- 将任意 Dialog key 的语言选择收敛至本地化模块；地图、Level 与 Campaign 通过属性提供 Dialog key。
- 修复 ZIP 原始条目定位、同名条目覆盖及文件与目录共存；忽略隐藏目录，对含相对路径段和连续斜杠的条目提供分级提示，并明确与 Everest 的兼容边界。
- 提供 `KeyedDict` 等可复用容器，补充公共接口示例与设计说明。

### 不兼容调整

- 存档读取、编辑与进度查询接口已重构，旧 Reader、文件会话及部分包装接口不再保留；迁移请参考 [存档 API](../docs/saves-domain-api.md) 和 [存档容器](../docs/saves-containers.md)。
- 地图与地图集类型、Mod 来源访问、本地化接口及部分公开名称已调整；不再提供扫描报告的 JSON 还原接口。
- `Duration` 的单位转换、格式化与精度语义已调整；不与 `timedelta` 隐式混合运算，使用显式转换。详见 [时长设计](../docs/finished/duration-design.md)。
- `LevelSide` 使用整数枚举表示 side 位置；存档中 Area／Mode／LevelSet 对应的领域名称统一为 Level／side／Campaign。

依赖图等仍处于接口草案阶段的能力不作为本次可用功能发布。
