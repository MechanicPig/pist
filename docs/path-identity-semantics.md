# 路径与标识符比较语义

Pist 同时处理宿主文件系统路径、ZIP 内的 Everest 虚拟资源路径，以及 SID、Dialog key 等字符串标识。它们不能共享一套“先规范化再比较”的默认规则：分隔符、大小写和扩展名是否有语义，取决于最终消费它们的实现。

每次新增或修改路径、路径衍生字符串（扩展名、SID、Dialog key、Collab ID 等）的比较、去重、索引或解析时，先回答：

1. 它是物理文件系统位置、虚拟资源路径，还是普通标识字符串？
2. 此处是在判断身份，还是只做展示排序、模糊筛选？
3. 外部实现或平台对分隔符、大小写、`.` / `..` 和扩展名的规则是什么？
4. Pist 是否有意偏离该规则？若有，偏离范围和原因是什么？

不要把 `Path`、`PurePosixPath`、`casefold()` 或 `suffix` 的便利行为误当成协议本身。

## 当前约定与证据

| 对象 | 结论 | 证据 |
| --- | --- | --- |
| 本地游戏、Mod、设置和数据路径 | 使用 `Path`；身份遵循宿主文件系统。 | Python `pathlib`；Everest 使用宿主路径 API。 |
| 目录内容条目的相对定位路径 | 使用宿主平台的 `PurePath`；打开时与根 `Path` 拼接。 | Python `pathlib`；Everest 使用宿主路径 API。 |
| ZIP 条目的虚拟定位路径 | 接纳规范的 `PurePosixPath`，索引另存原始名称和 `ZipInfo`；非规范条目先提示再忽略，不能由 pathlib 静默折叠。 | ZIP 格式；`Everest.Content.cs`、`.NET ZipArchive.GetEntry`。 |
| Everest 虚拟资源路径 | 使用 `ContentPath` 保留精确大小写，反斜杠转为 `/`；Pist 明确不支持连续斜杠等非规范身份。 | `Everest.Content.cs`；下文的产品支持边界。 |
| SID、Collab ID、LevelSet、大厅关联 | 是标识字符串；精确且区分大小写。 | `CollabUtils2/LobbyHelper.cs`。 |
| SID 到 LevelSet 的归属 | 非空 SID 取最后一个 `/` 之前的原始文本；没有 `/` 时归属为空字符串。不按宿主路径规范化。 | Everest `Patches/AreaData.cs` 的 `LevelSet` getter。 |
| 原版存档数字 Area ID | 是 `AreaData` 固定列表的 `0–10` 索引，不是显示章节号；按包内 `VANILLA_SIDS` tuple 解析为 SID，不提供自定义映射，已存 SID 优先。主文件原版章节写出要求完整固定顺序，并派生正确的数字 ID；旧会话按确切原版 SID 编码 ID，安全会话保证 SID 且置于保留的 ID 属性之后。不得将 Mod 区域的同号 ID 套用原版规则，也不凭 `Celeste/` 前缀推断原版身份。 | Celeste `AreaStats.cs`、`AreaKey.cs`、`AreaData.Load`；Everest `Patches/AreaData.cs` 原版 SID 初始化及 `Patches/AreaKey.cs` 的 SID setter。 |
| Level side | 内部 `LevelSide` 是值为 0/1/2 的 `IntEnum`，对应 A/B/C 序列位置；外部 A/B/C 文本通过 `.name` 和按名称查询显式转换，不依赖 `str(side)`。Pist 记录与大厅配置保持文本存储，不接受原始数字代替文本；游戏 XML 的 Normal/BSide/CSide 使用独立映射。 | Celeste `AreaMode` 顺序及存档 `Modes` 数组；项目 `game.levels`、存档 codecs 与 Pist 边界模型。 |
| Mod metadata 与 dependency 名称 | 是 Everest 身份；精确且区分大小写。 | `Everest.Loader.cs`。 |
| `dialog_key` | 是由 SID 等文本派生的查询键，不是 SID 或资源路径。 | Everest `Extensions.DialogKeyify`。 |
| Dialog 语言文件名 | `Dialog/` 与 `.txt` 精确匹配；完整语言文件名大小写不敏感。 | Everest `Everest.Content.cs`、`Patches/Language.cs`。 |
| Dialog 查询 | 游戏使用 .NET `OrdinalIgnoreCase`，Pist 使用 Python `casefold()`；ASCII 大小写匹配一致，但部分 Unicode 键不同，不能宣称完全等价。 | Celeste `Language.cs`；Everest `patch_Dialog.cs`；下文的 Unicode 边界。 |
| UI 排序、显示筛选 | 可用 `casefold()`；不得复用为身份匹配。 | Pist 的展示策略。 |
| CommunalHelper 宝石存档键 | 原始 `SID/房间名/index` 字符串，不含 side；Pist 按精确字符串参与显式范围操作，不规范化路径。没有模拟 .NET InvariantCulture 的排序与全部 Unicode 等价，文化比较碰撞由调用方处理。 | CommunalHelper `CustomSummitGem`、SaveData／Session 的 SortedSet；范围与限制见[收集品联动](saves-collectible-linkage.md)。 |

### 本地文件系统路径

- 用 `Path` 定位、拼接、打开和遍历；不要手工替换分隔符。
- 目录内容树中的 `DirContentEntry.at` 是相对于其根目录的宿主 `PurePath`，不得包含绝对路径或父目录段。
- 不以 `casefold()` 推断物理路径相同；需要比较两个已存在文件时使用 `Path.samefile()`。

存档槽位的独立 Mod 会话按 Everest 默认的 `<编号>-modsession-<Mod名>.celeste` 定位，debug 使用 `debug`；`SaveSlot.mod_session_paths()` 只在 Saves 目录直接发现对应文件，使用宿主 `Path.match()` 的大小写规则，返回实际文件路径和原始 Mod 名称，不对 metadata 名称做 casefold。文件组整组替换时，目标旧路径是否仍被使用按宿主 Path 相等语义判断，不能仅按原始名称字典做差集，否则 Windows 上仅大小写不同的旧文件可能被误当残留而删除。同编号目录别名的自转移通过 `Path.samefile()` 比较已存在目录后拒绝，不增加并发锁或承诺竞态安全。证据：Everest `Mod/Module/EverestModule.cs` 的 ReadSession／WriteSession 默认文件路径，以及 `Patches/SaveData.cs` 的 GetFilename debug 映射。覆盖自定义会话文件位置的 Mod 不在默认发现约定中。

### Everest 虚拟资源路径

- Everest 将反斜杠规范为 `/`，随后以默认 `Dictionary<string, ModAsset>` 查表。
- Berries 的 `ContentPath` 继续继承 `PurePosixPath`，在构造前拒绝空段、`.`、`..`、绝对路径和盘符形式，避免 pathlib 静默折叠身份；不支持连续斜杠的地图读取漂移。ZIP 的虚拟定位使用 `PurePosixPath`，目录条目使用宿主 `PurePath`。
- `Map` 的具体类型 `VanillaMap`、`ModMap` 分别保存 Content 目录或 InstalledMod，读取始终使用声明路径及其所属来源。不增加 `MapReadTarget`、第二套读取路径、跨来源重定向或真实文件系统回退。外部地图路径通过 `MapFilePath` 校验；SID、Dialog key、地图集分组及已有调用接口保持不变。

`ContentPath` 是 Everest 虚拟路径的受支持子集，不是完整资源表或游戏读取行为的模拟器。外部边界必须传入原始字符串检查；若调用方先构造 `PurePath`，重复分隔符或点段可能已经丢失，之后无法恢复或识别。大小写保持精确，不做 Unicode 归一化。

正常从目录树创建的 ZIP（条目名相对于包根目录，分隔符单一且无空段、`.`／`..`、有根路径或盘符）不会触发这些路径规范化与读取漂移的不一致。原始名称索引同时避免反斜杠条目的平台差异；有效目录末尾的单个 `/` 正常支持。这里仅说明路径定位边界，不承诺所有游戏行为或任意 Unicode Dialog 查询都完全兼容；手工构造或异常打包得到的非规范条目按下文处理。

#### 隐藏资源

加载层与 Everest 一样静默忽略 Mod 内名称以 `.` 开头的文件和目录（包括其后代），不发提示或警告；目录 Mod 和 ZIP Mod 采用相同规则。这里指名称前缀，不是 Windows 的 Hidden 文件属性。Mod 包根目录本身即使以 `.` 开头也仍加载。通用 `ContentEntry` 文件树不隐藏这些成员，过滤发生在资源加载时；地图发现从遍历中剪除隐藏目录，隐藏 Dialog 文件也不参与加载。

已处于隐藏目录或本身为隐藏文件的 ZIP 条目，即使还带连续斜杠等不支持的形式，也静默忽略。精确的 `.`、`..` 段仍按下文单独发 info，不与 `.hidden` 名称混为一谈。证据：Everest `Everest.Content.cs` 的目录 `Crawl`（约 198 行，明确排除 Mod 根目录）与 `TryAdd`（约 558 行）。

#### ZIP 原始条目索引与忽略策略

ZIP 条目的文件和目录不互斥：`Maps/Pack.bin` 和 `Maps/Pack.bin/A.bin` 可以同时存在。同一路径的 `is_file()` 与 `is_dir()` 可同时为真；`iterdir()` 不重复列出路径，`walk()` 将其分别保留在 `dirnames` 和 `filenames` 中。显式目录和由后代文件推导的目录均受支持。

索引从 `infolist()` 取得 `orig_filename`，分别记录原始名称首次出现的 `ZipInfo` 与虚拟键最后注册的原始名称，读取按“虚拟键 → 最后注册的原始名称 → 该名称首次出现的 ZipInfo”定位，直接传给 `ZipFile.open/read()`。不使用 `NameToInfo` 决定最终内容：Python 在 Windows 读取条目时会将反斜杠改为 `/`，可能提前覆盖名称索引，而 POSIX 没有相同的转换。同名覆盖和文件／目录共存均正常处理，不额外告警。

公开的 `ContentZipFile` 在归档层提供惰性扫描索引 `member_index: ZipMemberIndex`，选中的 `ZipInfo` 与忽略的原始条目名分别通过 `member_index.entries[virtual_path]`、`member_index.invalid_paths` 取得；继承的 `getinfo()` 使用标准库名称查询语义，不替代内容索引查询。`ZipContentEntry.zip_file` 借出共享归档对象，生命周期由内容根控制。归档元数据和全归档诊断从该归档对象取得；Pist 通过 `entry.zip_file.member_index.entries[entry.at.as_posix()]` 取得选中的 `ZipInfo`，自行计算缓存指纹，指纹策略不属于 Berries。目录遍历缓存方法仅由归档与路径条目内部协作使用，保持私有，不作为公共归档能力。

| 原始条目路径 | Pist 处理 | 级别 |
| --- | --- | --- |
| 含精确的 `.` 或 `..` 路径段 | 忽略，与 Everest 的点前缀过滤结果一致 | info |
| 含连续分隔符（包括反斜杠转为 `/` 后形成的连续分隔符） | 明确告知不支持并忽略，不能折叠后当另一条普通路径处理 | warning |
| 绝对路径、盘符等其他不支持的形式 | 忽略 | warning |

除已静默过滤的隐藏项外，若同时含点路径段和连续斜杠，以 warning 为准。有效目录条目的单个结尾 `/` 不是空路径段；多个结尾分隔符仍按连续斜杠处理。路径提示不走用于失败恢复的 `on_error`：Berries 通过 `on_path_notice(root, raw_path, severity, disabled)` 通知调用方，无回调时使用标准 logging；Pist 分别保存 `infos` 和 `warnings`，加载消息中保留显式级别及来源。

上述忽略策略是有意收敛，不宣称完全复刻 Everest。Everest 可以注册连续斜杠资源，但地图读取经过 `Path.GetDirectoryName(string)` 后可能漂移到另一资源；紧接 `Maps/` 的连续斜杠还会令 Filename 成为有根路径，`Path.Combine` 丢弃 Content 前缀，并可能回退到真实文件系统。Pist 不为这些边界情况引入自有路径实现、第二套地图来源或操作系统文件回退。

证据：Everest `790e3a5` 的 [Crawl](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Content.cs#L389)、[TryAdd](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Content.cs#L558)、[ZipModAsset](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/ModAsset.cs#L272) 与 [.NET GetEntry 契约](https://learn.microsoft.com/en-us/dotnet/api/system.io.compression.ziparchive.getentry?view=net-8.0)。原始名称选择和相关路径构造已在游戏自带的 .NET 8.0.15 上通过合成 ZIP 验证，不是启动合成 Mod 的完整游戏验证。

#### 真实 Mods 的索引开销验证（2026-10-20）

使用实际游戏目录 `D:/Program Files (x86)/Steam/steamapps/common/Celeste` 的 `pist maps browse` CLI 加载链路：解析实际命令、扫描真实 Mods、整合 Catalog、读取存档 0，并挂载真实 MapBrowserApp。反复 benchmark 使用相同的 Textual headless 120×45 首屏，并在计时后通过 F2 打开加载消息；另各用一次实际终端驱动确认首帧可显示。两者均隔离 `PIST_DATA_DIR` 和 `BERRIES_DATA_DIR`，每轮使用新的空本地数据目录，不写个人 SQLite、游戏、Mods 或存档，不执行网络请求。

为了只测原始索引的代价，前后使用同一份最终代码、相同的路径忽略与文件／目录共存规则。基线临时恢复旧式“由 ZipInfo.filename 建立文件／目录集合、用标准库 getinfo 按名称读取”，不建立原始名称映射；实验组使用新的原始索引。不是用整份历史 commit 与新代码混比。索引计时仅包围每个 ZIP 的惰性索引构建；不启用 profiler，避免将插桩开销计入结论。

样本包含 275 个 ZIP、258,753 个条目；112 个已启用包、172 个 Mod 地图文件，整合出 10 个可见地图集与 10 个隐藏地图集。前后各预热一轮，再按 ABBA 顺序交错测量，各 6 轮、每轮独立 Python 进程。操作系统文件缓存已经预热，不能称为冷磁盘启动。每轮 Mods 顶层文件时间／大小摘要与 Catalog 身份摘要均一致。

| 阶段（中位数） | 旧式名称读取 | 原始条目索引 | 差值 |
| --- | ---: | ---: | ---: |
| ZIP 索引构建合计（包含校验、排序） | 317.9 ms | 352.2 ms | +34.3 ms（约 10.8%） |
| 完整 Mod 扫描 | 1,345.4 ms | 1,392.4 ms | +47.0 ms |
| Catalog 整合 | 81.8 ms | 79.9 ms | −1.9 ms |
| UI 挂载与稳定等待 | 204.3 ms | 207.7 ms | +3.4 ms |
| CLI 加载至 headless 首屏稳定 | 1,896.4 ms | 1,907.8 ms | +11.4 ms（约 0.6%） |

完整加载样本范围分别为 1.802–1.927 s 和 1.881–2.312 s，后者保留了一轮较慢样本，没有剔除。分阶段中位数不能直接相加得到总时长中位数，也不能把 Catalog 或整体验证中的负差值解释成索引带来的加速。结论是：在该安装与热文件缓存条件下，索引自身增加约几十毫秒；总首屏差异小于样本波动，不能精确归因。实际终端两次首帧分别为 2.195 s 和 1.947 s，仅作真实驱动冒烟验证，不用于性能收益结论。

临时脚本：`.pist/scripts/benchmark_zip_index_browser_20261020.py`，参数为 `before|after`、唯一 run ID，可加 `--terminal` 使用真实终端首帧模式。完整样本结果由运行输出中的 `ZIP_INDEX_BENCH` JSON 记录，复测须保持 Mods、存档、设置、窗口尺寸及缓存条件一致。

### SID 与 Collab 标识

- SID、Collab ID、LevelSet 不是本地路径；`PurePosixPath` 只可用于其结构拆分。
- CollabUtils2 以默认 `StartsWith` 与 `HashSet<string>` 匹配，故前缀、目录和相等比较均区分大小写。
- 证据：`CollabUtils2/LobbyHelper.cs` 的 `GetLobbyLevelSet`、`IsCollabLevelSet`、`GetLobbyForLevelSet`（约 61–83、88 行）。

### SID 到 LevelSet 的归属规则

- Campaign 在此对应 Everest 的 LevelSet。对非空 SID，归属是最后一个 `/` 之前的原始文本，不是首段，也不是 Mod metadata 名称。例如 `Cdog/ExtendedCSides/Example` 属于 `Cdog/ExtendedCSides`。
- `berries.game.saves.progress.level.campaign_name(sid)` 统一提供上述派生，空 SID 抛出 `ValueError`；存档的加载校验、成员归属、冲突修复及转移范围复用该函数，不各自实现路径拆分。
- 没有 `/` 的非空 SID 属于空字符串 LevelSet；原版已映射 SID 如 `Celeste/1-ForsakenCity` 属于 `Celeste`。不能统一把无分隔符的 SID 当作原版。
- 不折叠大小写、替换分隔符、折叠点段或删去扩展名；这里处理的是最终 SID，不是要先规范化的地图文件路径。尤其不能用宿主 `Path.parent` 改写其语义。
- Everest 在 SID 为 null 或空字符串时回退 `Celeste`，这是 getter 的回退行为，不表示存档新增接口可以缺少有效身份；无 SID 的原版数字 ID 先按内置 tuple 解析，未知 ID 不生成身份。解析前拒绝负数，不能让 Python 的负索引规则将其解释成原版地图。
- 存档 API 的扁平新增和 `move_level_progress()` 由 SID 推导归属；Campaign 成员和分区索引的新增／替换统一检查归属，所有写入同步索引。严格加载拒绝错放，宽松加载允许观察输入中的错放记录，写出要求修复；加载时完成分区内重复消歧，活动区与回收区可保留同 SID 的不同历史，查询直接使用已建索引。缺失父结构不隐式创建。具体游戏丢进度路径和约束背景见 [存档容器](saves-containers.md)。
- 证据：[Everest AreaData.LevelSet](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Patches/AreaData.cs#L79)，本地参考 `.pist/references/Everest/Celeste.Mod.mm/Patches/AreaData.cs` 约 79–94 行。

### Ender's Blender 统计键

- 按 SID 与 side 查询时保持 SID 原文；A side 使用 SID，B/C side 分别追加 `_B`、`_C`。这是 Mod 的序列化编码，不是地图文件名的 `-B`、`-C` 组装规则。
- 该编码没有转义或额外的 side 字段，因此 `Example_B` 既可能是该 SID 的 A side，也可能是 SID `Example` 的 B side。全量枚举保留原始 protocol key；不得自动剥离后缀并声称已得到唯一的最终身份。
- 证据：Enders-Blender `Source/EndHelperModule.cs` 的 `mapNameSide_Internal` 构造（约 461–463 行）。

### Mod metadata 与 dependency 名称

- metadata 名称与 dependency 名称是 Everest 加载器的身份字符串，不是 Dialog key。
- 依赖查找以普通字符串相等比较；只有精确的 `EverestCore` 名称会被当作核心 Mod 别名处理。
- 证据：`Everest.Loader.cs` 的 `TryGetDependency`（约 961–973 行）。

### Dialog key

- `Dialog.Get` 与 `Dialog.Has` 会先运行 `DialogKeyify`：将 `/`、`-`、`+`、空格替换为 `_`，然后查询。
- 游戏的 `Language.Dialog` 使用 `StringComparer.OrdinalIgnoreCase`；Pist 的 `CaseFoldDict` 使用 Python `str.casefold()`。两者均忽略大小写，但 Unicode 比较规则不完全相同，不能声称身份语义完全一致。
- 路径到 `dialog_key` 的结构转换仍先遵循虚拟地图路径规则；Dialog 查询不敏感不能反推 SID 或资源路径不敏感。
- 证据：`Everest/Extensions.cs` 的 `DialogKeyify`（约 130 行），`Everest/Patches/Dialog.cs` 的 `Get` / `Has`（约 176–213 行），以及 Celeste `Language.cs` 的 `Language.Dialog` 初始化（约 45 行）。

#### Python casefold 与 .NET OrdinalIgnoreCase 的边界

Python `casefold()` 使用 Unicode 完整大小写折叠，允许一个字符展开为多个字符，例如 `ß` 折叠为 `ss`；.NET `OrdinalIgnoreCase` 不采用这种多字符展开。因此 `Straße` 与 `STRASSE` 在 Pist 的 `CaseFoldDict` 中视为同键，在游戏的比较器中不是同键。当前实现保留这一已知差异：极少数 Unicode Dialog key 可能在 Pist 中发生游戏里不存在的覆盖或误命中，本次仅澄清文档，不替换比较器。

普通 ASCII 字母大小写比较一致；不能据此泛化到所有 Unicode 字符，不同运行时的 Unicode 数据版本也可能不同。大小写折叠也不是 NFC 等通用 Unicode 归一化。这一限制仅涉及使用 `CaseFoldDict` 的查询，不能扩展为 ZIP 路径、SID 或 Mod 名称忽略大小写的依据。

参考：[Python str.casefold](https://docs.python.org/3/library/stdtypes.html#str.casefold)、[.NET StringComparer.OrdinalIgnoreCase](https://learn.microsoft.com/en-us/dotnet/api/system.stringcomparer.ordinalignorecase?view=net-8.0)。

### Dialog 语言文件

- Everest 仅把精确位于 `Dialog/` 且扩展名为小写 `.txt` 的资源识别为普通 Dialog 文件。
- 资源被识别后，加载器使用 `OrdinalIgnoreCase` 匹配完整的无扩展名虚拟路径；因此 `English.txt`、`english.txt` 与 `ENGLISH.txt` 表示同一语言文件。
- Pist 保持目录和扩展名的精确语义，只在已支持语言的文件名匹配处忽略大小写；这不是通用虚拟资源路径规则。
- 证据：Everest `Everest.Content.cs` 的 `GuessType` / `MatchExtension`（约 736、855 行），`Patches/Language.cs` 的 `_GetLanguageText`（约 252 行），以及 `Patches/Dialog.cs` 的 `_GetFiles`（约 46 行）。

### 扩展名

- `Path.suffix` 仅适合本地路径的结构读取，`PurePosixPath.suffix` 仅适合虚拟路径的结构读取。
- 两者都不定义扩展名的大小写比较规则；模拟 Everest 资源扫描时，遵循其精确的 `.bin` 检查。
- Pist 若为用户输入刻意采用更宽松的发现规则，必须记录范围并测试。

## 产品规则与外部事实分开记录

外部实现的事实和 Pist 的产品策略不能混为一谈。例如 CollabUtils2 可把某些嵌套路径解释为大厅相关 LevelSet，而浏览器为了稳定的静态地图集展示，可能只把特定目录层级展示为大厅。前者应引用源码；后者应注明为 Pist 策略，并单独测试。产品策略不能悄悄改变 SID 或资源路径的身份比较规则。

## 维护与测试要求

- 新出现的比较类别必须在本页补充：对象、选用的表示、身份语义，以及可复查的实现/平台证据（仓库、文件和大致位置即可）。
- 如果故意偏离外部实现，记录偏离原因、仅影响的边界和回退行为；不要以全局“兼容”分支扩大偏离范围。
- 对可能分歧的身份规则补测试：大小写不同、`\\` 与 `/`、点段、扩展名大小写，或同名不同来源；测试只覆盖当前类别实际有语义的维度。
- 代码 review 时，任何新增的 `casefold()`、`lower()`、`Path(...)`、`PurePosixPath(...)`、扩展名比较或字符串拼接键，都应回链到本页的类别或在同一变更新增证据。
