# Berries 使用指南

以下示例在仓库中通过 `uv run --package berries python ...` 执行，不依赖 Pist 的记录数据库、表格配置或 UI。入口参数使用 `pathlib.Path`；游戏目录指包含 `Content/` 的 Celeste 安装目录，而不是 `Mods/`。

## 接口层次

根包导出是常用入口的集合，不是所有可复用能力的清单；子模块按以下边界使用：

| 层次 | 接口与职责 |
| --- | --- |
| 公共读取与解释 | GameInstallation、CampaignCatalog、Map/Level/Campaign、Localization、存档 Reader、规则模型与分类、MapLayout、MapPreview。GameBananaClient 只承诺经 WEGFan 按 Mod 元数据名查询，不是完整 GameBanana API。 |
| 公开底层事实 | BinMap/BinElement、ContentEntry/ContentPath、精确规则和结构化诊断。支持自定义检查器；调用方须遵守身份、可变性和资源所有权约定。Enders 原始映射保留协议键，不保证可无歧义地还原 SID 与 side。 |
| 高级预览扩展 | PreviewNavigation 的页面历史与目标加载、PreviewAssets 的资源选择、PreviewSession 的短期 loopback 会话可供应用组合。 |
| 内部传输与实现 | 预览 HTTP 路由、JSON 字段及请求响应模型（包括 OpenMapReq、EntranceResp 和 entrances() 的序列化适配）、WEGFan 搜索 envelope、ZIP 私有索引缓存、下划线符号。不因可以导入而承诺兼容；前后端或仓库内消费者须同步调整。 |

高级预览扩展仍使用随包提供的浏览器模块与会话机制，不是任意第三方前端的稳定 REST 协议。当前 MapPreview.preview() 会打开浏览器并等待退出；PreviewSession.run() 同样拥有服务和浏览器的生命周期，close() 请求结束等待，run() 退出时释放服务。当前没有独立的 start/URL/close 嵌入契约，不建议应用访问私有 runner 或 socket 来模拟该接口。

## 查找和读取一张地图

先用 `examples/list_maps.py GAME_DIR` 列出最终 SID 和 side，再指定目标：

```sh
uv run --package berries python examples/inspect_map.py "D:/Games/Celeste" "Celeste/1-ForsakenCity" --side A
```

核心组合如下；其中 `level` 来自 `catalog.campaigns` 中某个 Campaign 的 `levels`：

```python
from berries import GameInstallation, LevelSide, classify_map_entities
from berries.entities.config import load_default_entity_rules
from berries.game.map_data import load_map_data

game = GameInstallation(game_dir)
catalog = game.load_catalog()
map_file = level[LevelSide.A]
data = load_map_data(map_file)
rules = load_default_entity_rules()
for entity in classify_map_entities(data, rule_set=rules):
    print(entity.room, entity.name, entity.entity_id, entity.kind)
```

`Map` 表示绑定读取来源的具体 `.bin` 文件；`VanillaMap` 从原版 Content 读取，`ModMap` 持有所属 `InstalledMod`，可读取目录或 ZIP。扫描后的单个 Mod 也可通过 `ModMap(file_path, mod)` 绑定其 `map_files` 中的文件，不必加载完整目录。`Level` 将一至三个 Map 组装为 A/B/C 面并提供最终 SID；`Campaign` 将 Level 按地图集组织。只有读取所选地图时才解析其二进制内容。

文件路径推导的 `local_` 身份只是候选，不应替代 Level 的最终 SID。原版 SID 有 `Celeste/` 前缀，孤立的 `-B` 文件也不一定代表另一关卡的 B 面。分类输出只包含规则明确匹配的实体；没有输出不等于地图没有收集品，也不等于未知实体已获确认。

需要检查规则覆盖而不是只统计已分类实体时，使用全图解释入口：

```python
from berries.entities.classification import explain_map_entities

for item in explain_map_entities(data, rule_set=rules):
    print(item.room, item.element.name, item.status, item.rule)
```

`matched` 表示命中非空类别，`excluded` 表示规则明确排除，`unmatched` 表示没有命中规则。解释遍历所有房间的 entities，包含没有编辑器 ID 的实体；不将 triggers 当作实体。结果借用原始 BinElement 与规则对象，不复制内部属性；地图文件身份由调用方持有，房间与元素可定位该文件中的来源。

## 查询和编辑存档

```python
from berries.game.levels import LevelSide
from berries.game.saves import operations

directory = game.saves()
for slot in directory.slots.iter(filter_empty=True):
    print(slot.number)

slot = directory.slots[0]
save = slot.main()
try:
    backup = slot.backup()
except FileNotFoundError:
    backup = None
if backup is not None:
    operations.restore_mod_progress(save, backup)

progress_level = save.flat_levels.get(level.sid)
stats = None if progress_level is None else progress_level.maps[LevelSide.A]
active = operations.is_in_progress(save, level.sid, LevelSide.A)
```

`game.saves()` 和 `SavesDir.from_game_dir(game_dir)` 按平台及 `EVEREST_SAVEPATH` 定位活动保存目录；普通 `SavesDir(saves_dir)` 直接接受保存目录。槽位只定位文件，`main()`、`backup()` 和 `mod(SaveType)` 各自一次性读取，不自动加载伴随文件或恢复。数据只保存在内存，读取或编辑均不自动写回。

单文件可以直接读路径、二进制流或文本流；已有文本／字节使用 `fromstring()`，输出文本使用 `tostring()`。字符串传给 `read()` 表示路径，传给 `fromstring()` 表示内容。下面的编辑对比不需要了解 XML 或 YAML：

```python
from berries.game.saves.files.main import MainSave

save = MainSave.read('0.celeste')
before = save.tostring()
save.deaths += 1
after = save.tostring()
show_diff(before, after)
# 确认后再显式写出；也可以保存到其他路径。
save.write('edited.celeste')
```

`write()` 接受路径、`BytesIO`、`StringIO` 及实际文件流。路径／二进制流写 UTF-8，文本流接收字符串并自行决定编码；XML 文本不携带可能与流编码冲突的声明。借用流不自动定位、截断、刷新或关闭。备份、原子替换、并发检查和 diff 展示由调用方安排，不属于文档生命周期。

Mod 备份及附加数据使用各自类型：

```python
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.rooms import room_key

backup = ModBackupSave.read('0-modsavedata.celeste')
for campaign in backup.mods.campaigns.values():
    for level in campaign.levels.values():
        for side, progress in zip(LevelSide, level.maps, strict=False):
            if progress is not None:
                print(level.sid, side, progress.deaths)

blender = slot.mod(EndersBlenderSave)
first_clear = blender.first_clear.get(room_key(level.sid, LevelSide.A))
last_saved = blender.last_saved.get(room_key(level.sid, LevelSide.A))
```

Ender's Blender 的 `first_clear` 与 `last_saved` 为并列的普通字典，不把初见数据作为整个文件的默认查询。按 SID 和 side 查询时先通过 `room_key` 显式编码 Mod 的协议键，再调用字典接口；全量遍历保留原协议键，因为 `_B/_C` 后缀可能与 A-side SID 歧义。Collab 会话通过 `slot.mod(CollabUtils2Save).sessions` 按 SID 查询，首次访问成员的 `.session` 才解析嵌套 XML。

自定义 `ModSave` 可以要求构造参数：`slot.mod(CustomSave, *args, **kwargs)` 会通过该类型的 `read()` 原样转发，默认由通用入口交给构造器。直接调用 `CustomSave.read(path, *args, **kwargs)` 或 `CustomSave.fromstring(contents, *args, **kwargs)` 使用相同约定；路径／内容必须作为位置参数传入。槽位不解释具体格式选项，非法参数由构造器报错，不会先读取文件；通用转发保持具体返回类型，但不提供构造参数签名的静态检查。

### 原版 ID、最佳成绩与检查点

`LevelSide.A/B/C` 是值为 `0/1/2` 的 `IntEnum`。读取地图时使用 `level[side]`，读取存档进度时使用 `progress_level.maps[side]`；整数和枚举均表示相同的序列位置。需要 A/B/C 字符时用 `side.name`，从字符解析用 `LevelSide['B']`；`LevelSide(1)` 从数字位置得到 B。不要用 side 的真假判断其存在性，因为 A 的值为零；可选 side 应使用 `is None` 判断。Pist 的记录和配置按 A/B/C 文本保存。

无 SID 的原版进度使用包内 `VANILLA_SIDS` tuple；数字 ID 是原版固定的 `0–10` AreaData 列表索引，而不是章节号，不提供自定义映射参数。已有 SID 优先；数字身份解析只用于原版 `Areas` 和旧 `CurrentSession`，不套用到 Mod 区域或安全会话，也不向文件补写 SID。未知 ID 保留未解释原文，负数及其他非法 ID 报错。

```python
save = MainSave.read('0.celeste')
level = save.flat_levels.get('Celeste/1-ForsakenCity')
progress = None if level is None else level.maps[LevelSide.A]
if progress is not None:
    print(progress.bests, progress.checkpoints)
```

最佳时间仅在正 ticks 时存在；最佳死亡／冲刺数由 `SingleRunCompleted` 标识，零死亡不是缺失。成绩、检查点和草莓分别按需解释，不要求先有累计统计。模型只覆盖已支持的存档字段，未知扩展继续保留。完整集合、复制和业务联动契约见 [Saves 领域对象与集合 API](saves-domain-api.md)。

## 构造与解析时长

`Duration` 保存精确的有符号 `Fraction` 秒数；直接构造只接受该内部表示，`Duration()` 是零值。`from_seconds()` 接受 Fraction 的数值或字符串转换参数，`from_units()` 以关键字参数累加各时间单位：

```python
from fractions import Fraction

from berries.game.duration import GAME_TIMER_FORMAT, Duration

value = Duration.from_units(hours=1, minutes=30, milliseconds=250)
exact = Duration.from_units(subseconds=Fraction(1, 3), nanoseconds=1)
parsed = Duration.from_format('1:30:00.250', GAME_TIMER_FORMAT)
assert parsed == value
```

可用单位是 `hours`、`minutes`、`seconds`、`subseconds`、`milliseconds`、`microseconds` 和 `nanoseconds`；全部可以省略，也可为负数或超出通常分量范围，重叠单位直接相加。输入浮点数时保留其二进制精确值，不做十进制猜测；需要精确十进制时可用 `Fraction('0.1')`。计时帧仍通过 `from_timer_frames()` 构造。

`from_format(text, spec)` 与 `format(value, spec)` 共用格式语法，空 spec 使用 `GAME_TIMER_FORMAT`。解析要求文本为该格式的规范输出：补零宽度、字面量、符号位置及可选分支均须一致；不自动剥离空白，也不接受额外尾随内容。支持累计单位 `%Qh/%Qm/%Qs`、分量 `%H/%M/%S`、无补零分量 `%-H/%-M/%-S`、`%f/%3f` 等小数精度、可选分支与转义。缺少的分量按零处理，`%H` 只解释一天内的小时，不恢复原来的天数；小数只恢复文本表示的精度，不补回被截掉的数据，显示产生的负零解析为零。

重复或重叠字段必须一致；如 `123` 对 `%-M%-S` 可以解释成 1 分 23 秒或 12 分 3 秒，解析会抛出 `ValueError` 而非任取一种。无数字指令、非法格式、范围越界及不匹配的文本同样抛出 `ValueError`。

这是规范表示解析器，不是宽容的人类输入解析器。`str()` 默认只展示毫秒，`from_format(str(value), '')` 无法恢复更细精度，不应当作持久化编解码；需要无损保存时使用精确 `total_seconds`、Pydantic 序列化或明确的协议单位。`from_seconds('0.1')` 和 `from_units(milliseconds=100)` 表示精确十分之一秒；`from_seconds(0.1)` 则保留浮点的二进制值，可能无法精确转换为整 tick。

分量均取绝对值；符号保留在 `total_seconds` 中，格式输出只在首个选中的数字前添加一次负号。以 `a = Duration.from_seconds('-83.456')`、`b = Duration.from_units(hours=27, minutes=4, seconds=5, milliseconds=250)` 为例：

| 属性／指令 | 含义 | a | b |
| --- | --- | --- | --- |
| `hours`／`%Qh` | 累计整小时，不按天截断 | 0 | 27 |
| `%H` | 一天内小时，补两位 | 00 | 03 |
| `minutes`／`%M` | 分钟余数；指令补两位 | 1／01 | 4／04 |
| `seconds`／`%S` | 秒余数；指令补两位 | 23／23 | 5／05 |
| `subseconds` | 精确的小数秒 | 57/125 | 1/4 |
| `milliseconds`／`%3f` | 截断后的毫秒分量；指令补三位 | 456 | 250 |

Duration 定位为值类型，优先组合而非通过继承改变语义。工厂可以构造请求的子类，但 dataclass 相等与排序区分具体类，算术返回基础 `Duration`；这些行为不构成完整的数值子类型承诺。

## 预览地图

```sh
uv run --package berries python examples/inspect_map.py "D:/Games/Celeste" "Celeste/1-ForsakenCity" --preview
```

```python
import asyncio

from berries import MapLayout, MapPreview

layout = MapLayout.from_bin_map(data, entity_rules=rules)
asyncio.run(MapPreview(map_file, layout, dialogs=catalog.dialogs).preview())
```

预览仅监听 loopback，关闭页面后结束等待并释放服务。已有异步应用应直接 `await preview.preview()`，不要嵌套 `asyncio.run()`。示例仅打开所选地图，没有注入跨地图导航集合。需要导航时，向 `MapPreview` 传入 `routable_maps`，并通过 `entity_rules`、`entrance_rules` 传入解释初始布局所用的同一组规则；后续目标读取会沿用它们，不会重新加载默认配置。`MapLayout.from_map()`、`from_path()` 和 `from_bin_map()` 均接受这两个参数；未显式传入时只加载对应的包内规则。路线编辑与个人统计不属于此公共预览能力。

## 选择规则来源

实体与入口规则的默认入口只加载包内规则，不发现用户配置、不应用本地覆盖；读取发生在实际调用时，而不是基础模块导入时：

```python
from berries.entities.config import load_default_entity_rules
from berries.map_entrances import load_default_map_entrance_rules

rules = load_default_entity_rules()
entrances = load_default_map_entrance_rules()
```

规则与分类的解析、校验、按值变换和显式层合并由 Berries 提供；文件位置、是否加载覆盖、缺失处理、写入、备份和恢复由应用决定。显式读取并合并一份实体覆盖：

```python
from pathlib import Path

from berries.entities.config import load_entity_rule_layer
from berries.entities.rules import EntityKindLayer, EntityRuleLayer, EntityRules

rules = EntityRules.from_layers(
    EntityKindLayer(kinds=rules.kinds),
    EntityRuleLayer(entities=rules.entities),
    load_entity_rule_layer(Path('overrides.toml')),
)
layout = MapLayout.from_bin_map(data, entity_rules=rules, entrance_rules=entrances)
```

显式指定但不存在的文件会报错；若应用希望缺失时跳过，应在调用前自行决定。上例的覆盖规则只能引用所选分类，例如只对 `Example/Berry` 的明确条件追加分类：

```toml
[entities."Example/Berry"]
rules = [{ kind = "moonberry", when = { moon = true } }]
```

入口规则也可显式读取并合并覆盖：

```python
from berries.map_entrances import load_map_entrance_rules

entrances = entrances.merged(load_map_entrance_rules(Path('entrance-overrides.toml')))
layout = MapLayout.from_bin_map(data, entity_rules=rules, entrance_rules=entrances)
```

示例脚本默认只使用包内规则，`--local-rules PATH` 显式增加一层实体覆盖；`--packaged-rules` 显式选择默认行为。`load_entity_rules(path, kinds_path=...)` 读取明确指定的一对文件，`parse_entity_rules(text, kinds=...)` 仅解析两份 TOML 文本；模型与匹配位于 `rules`，TOML 输出由 `serialization` 提供。`parse_map_entrance_rules(text)` 仅解析入口规则 TOML，`load_map_entrance_rules(path)` 读取显式文件。

Pist 的本地实体覆盖发现和保存由 `pist.entities.config.EntityConfigStore` 负责；共享发布和分类文件联动由 `pist.entities.maintenance.EntityRuleLibrary` 负责，审计数据库协调也留在应用中。`pist.map_entrances.load_map_entrance_rule_layers()` 合并包内入口规则和可选的 `.pist/map_entrances.toml`；本地文件缺失时跳过，无效时报告错误。这些存储策略不属于 Berries 的公共接口。包更新不保证旧的本地规则兼容；加载失败应让用户修正规则，而不是静默忽略。

## 错误、诊断与资源

`load_catalog()` 默认遇到扫描或原版 Dialog 读取错误会抛出异常。需要继续并保留部分结果时，显式传入 `on_scan_error` 与 `on_error` 回调：

```python
scan_errors = []
dialog_errors = []
catalog = game.load_catalog(on_scan_error=scan_errors.append, on_error=dialog_errors.append)
```

回调返回表示接受继续；回调抛出则中止。错误对象保留来源和原因，应用负责展示，不应丢弃列表后将部分结果描述为完整加载。`catalog.issues` 另行记录依赖、保留地图集身份和隐藏设置等已读取内容的领域问题，与读取错误不是同一来源。单图读取失败以 `ValueError` 报告，配置加载也可能失败；应在应用边界处理，避免把失败当成空地图。

`load_map_data()` 内部按上下文打开并关闭来源；自行调用 `map_file.open_content()` 时使用 `with`，不要保留未关闭的 ZIP。GameInstallation 是只读访问入口，不代表返回对象递归不可变；目录结果代表本次加载，不自动跟踪文件变化，重新调用 `load_catalog()` 获取新的加载结果。
