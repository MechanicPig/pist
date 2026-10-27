# 游戏数据约束

## 地图类型与来源

- `LevelSide` 是 A=0、B=1、C=2 的 `IntEnum`，直接表达地图或进度序列中的位置，不提供 `position`。外部 A/B/C 文本使用 `.name`／`LevelSide[name]` 显式编解码；原版 XML 的 mode 名继续使用协议映射，Pist 的记录与大厅配置保持原有文本格式。不能因内部枚举改为整数就改写已有存储协议，也不能用 truthiness 判断可选 side 是否存在。`LevelProgress` 不实现集合协议，成员由 `maps: list[MapProgress | None]` 承载；其索引只处理整数／切片，枚举自然参与索引，索引、切片、迭代及成员测试均面对 MapProgress／None 值，缺项不重排、不另设 get 入口。只读 Map 投影跳过 None；编辑或转移依赖缺项时明确报错，不能把缺项视为零进度。异常 Modes 长度的游戏行为证据见 `docs/saves-domain-api.md`，不能以反序列化成功或单次初始化成功推断完整游戏流程安全。

- Map 是共享路径语义与 open_content() 契约的抽象类型，VanillaMap 保存 content_dir，ModMap 保存 mod；不保留 MapInfo、通用 content 字段、ContentSource 或 GameContent 包装。InstalledMod 只保存经过校验的 map_files，不提供临时包装的 maps property；Catalog 在整合路径时逐个创建 ModMap(path, mod)，来源查询直接使用 ModMap.mod。预览已解码的布局以及仅需文件身份的接口使用 ContentPath，不为查询路径构造地图对象；读取接口使用 Map。扫描清单导出 map_files，不序列化父子来源引用。

## Mod 扫描与应用策略

- `ModMetadata` 与 `Dependency` 是不可变、可哈希的 manifest 值，依赖集合使用 tuple；外部 YAML/JSON 数组仍在校验边界转换，编辑时创建新值，不修改已读快照。metadata 按值相等，不包含物理包身份；全体安装候选可能有相等 metadata，不能用单值映射丢弃重复来源。已选加载结果可使用 metadata 到所属包的映射，物理内容加载顺序另行保存。

- `InstalledMod` 的目录与 ZIP 行为由 `DirMod`、`ZipMod` 的 `open()` 多态提供，不保存冗余 source 判别字段。扫描结果保存内存中的 InstalledMod 对象；JSON 仅导出扫描清单与诊断，不承诺从清单还原可操作的 Mod 子类，不为无调用方的往返还原维护判别联合。

- Berries 的 `ModScanner.scan()` 负责 Everest 包发现顺序、黑白名单选择和内容读取，`include_disabled` 与 `read_dialogs` 分别控制范围和读取内容，不通过另一个扫描入口隐式改变语义；单包读取使用 `read_mod()`。进度通过 `on_progress` 通知，不把扫描循环和报告组装暴露给 UI 重写。
- 预期的包、manifest、文本、Dialog 和地图枚举失败使用 `ModScanError`，保留物理来源、相对条目、阶段、禁用状态及原始异常。默认抛出；构造时提供 `on_error`，正常返回则允许跳过失败输入、保留已读结果或使用 Everest 的 dummy manifest 回退，抛出则终止。Berries 仅调用回调，不收集 warning，扫描报告只承载扫描事实；回调异常不得被读取错误捕获。名单配置读取失败不按单包失败跳过。
- `ModScanner` 不实施自然排序、A/B/C 展示排序或 HeartSide 后置，不生成 Pist 的中文诊断。Pist 在 `mod_scan` 中组合扫描器，通过错误回调收集应用诊断，显式选择继续策略并处理排序和展示；带 warning 的报告、报告序列化和将诊断合入 Catalog 的组合入口由 Pist 拥有，Berries 不反向读取这些应用字段。包发现与覆盖顺序不得因展示排序改变。更改扫描接口时同时检查 Catalog、GameInstallation、CLI 和测试替换点。
- CollabUtils2 的 journal 图标读取与排序归 `berries.game.collab`，不混入 Mod 扫描；Pist 的缓存、存档进度排序及作者来源选择留在应用层。通过路径是否包含 `lobb/gym` 判断是否优先提供 Dialog 作者是 Pist 的启发式，不得用它赋予大厅或地图集身份。

## Dialog 批量读取

- Berries 的 `CampaignCatalog.issues` 仅保存结构化的 Mod 依赖加载问题、保留 LevelSet/SID 冲突及隐藏规则读取失败，不保存应用文案；隐藏规则问题保留规则、路径、失败阶段和原始原因。`resolve_campaign_refs()` 返回已解析地图集及缺失的原始引用；`JournalRefs` 只保存引用和无效入口数量。Pist 的 `campaign_catalog` 负责将这些事实与扫描、Dialog 读取错误组合为中文诊断，不在公共层拼接“官图”“配置”“日志引用”等展示文本。应用加载结果通过 `CatalogLoadResult` 组合公共 Catalog 和诊断，不继承 Catalog 或转发其属性；浏览器分别接收公共 Catalog 和诊断。

- `read_dialogs()` 接收一个 `ContentEntry` 目录，自行枚举、筛选并解析直接位于其中的语言文件，不接受文件列表或读取回调。`encoding` 指定解码方式；`strict=False` 默认允许自定义目录名，`strict=True` 要求名称精确为 `Dialog`，与错误处理策略无关，Mod 和原版加载均启用它。目录不存在时返回空结果，已存在但不是目录的输入报错。预期的目录枚举、文件读取、解码或解析失败默认抛出携带源根路径、条目相对路径和原始原因的 `DialogReadError`，并保留异常链；未知编码配置和编程错误直接传播。
- `on_error` 只决定失败策略，不接管读取：回调立即接收 `DialogReadError`，正常返回则跳过失败文件（枚举中断则保留已读结果），抛出异常则终止。Mod 扫描及 Catalog 的原版读取均将回调交给调用方；Pist 通过回调将失败收录到可见诊断，不捕获或转发无关 warning；后来发生异常时，之前已收录的诊断不能丢失。每个文件解析完成后才合并条目，失败文件不得部分覆盖已有内容。
- `berries.game.localization.Localization` 持有最终 Dialog 映射与单个 `lang`（公共层默认 `en`，Pist 默认 `zh-cn`），基础接口 `translations(key)` 和 `get(key, default=None)` 适用于任意 Dialog 文本，而不限于名称；`select()` 复用相同的语言选择处理已有翻译映射。优先选择指定语言，非英语时仅回退到 `en`，不遍历其他语言；空字符串与缺失不同，仅缺失时使用 default。Map、Level、Campaign 通过只读 `dialog_key` property 提供派生查询键，不接受独立 key 构造参数；Map 保留精确文件路径的 key，Level 使用组装后 A 面的 key，原版内容按数字 H/X 或 LostLevels 命名提供 AREA key，Campaign 从 directory 派生 key，Collab hub 直接继承，仅原版 Campaign 重写为 Celeste。名称查询优先级及中性回退建立在这些 key 和通用查询之上；作者、标签等文本直接按 key 查询，不另设按字段分类的查询接口。映射借用且不缓存查询结果，`langs` 根据 `lang` 派生且英语不重复；Pist 设置使用 `dialog_lang`，CLI 为 `settings dialog-lang <lang>`。Level 和 Campaign 只承载领域身份与结构，不保留本地化方法或显示文案。Dialog 模块负责文件处理和 key 转换，不依赖 Localization 或领域名称解析。Pist 的 B/C 面后缀、“官图”“未分类”及 Mod 区分后缀由 `pist.map_names` 管理；公共预览不依赖这些应用策略。

## BinaryPacker 解析与解码

- BinaryPacker 的解析、跳过和树遍历不得依赖 Python 调用栈容量；按声明的嵌套与元素数量限制处理，并以 `BadMapBin` 报告越界或截断。深度默认限制为 32 层（包含根节点），优先使用解析函数的 `max_depth`，其次使用 `BERRIES_MAX_MAP_DEPTH`；配置须为正整数。元数据的显式提前返回仍不验证未读取的后续 payload。
- BinaryPacker 的长度前缀字符串遵循游戏 `BinaryReader` 的 UTF-8 替换行为，异常须发出携带位置和原始字节的警告；地图浏览须将警告保留到消息入口，不因字符串解码拒绝整张地图。RLE 文本按游戏逐字节转字符，不当作 UTF-8；不得为修复显示静默猜测其他编码，也不得放松截断、长度和结构校验。

## 存档读取

- 主存档和 Mod 备份的持久值由 MainSaveData／ModSaveData 直接持有；需要内部替换的 levels／campaigns／recycled_campaigns 使用私有属性和只读 property，其余值为普通字段，均参与值比较和复制，不通过 object.__setattr__ 绕过 frozen。集合通过扩展的 PrivateAttr(alias=..., default_factory=...) 声明构造输入，FieldModel 统一生成校验和静态参数提示，不重复编写 TypedDict 或转发构造器；成员完成校验并安装后，model_post_init 才建立视图、索引和基准。from_xml 走同一校验路径，不通过 context 传集合，不构造随后丢弃的默认成员。vanilla／mods 是绑定根对象的视图，不可整项赋值，不持有第二份数据。成员修改、vanilla.levels 替换及显式 install_campaigns／install_mod_progress 统一预检并同步索引。恢复显式使用 deepcopy(backup).mods 后安装，不创建临时进度快照，也不复制视图代替复制进度。独立 Mod 数据只使用 ModSaveData，不另设与其重复的领域模型。根的标准复制重新绑定视图和索引，浅复制仍共享领域成员；写出直接读取字段，不为编码构造临时快照。详细字段与语义见 docs/finished/save-xml-binding.md。

- `saves.progress` 是纯领域子包：Map 及其最佳成绩、Level、Campaign 集合、Session、Mods 快照按职责分模块；`CampaignMembers` 和 `LevelMembers` 分别管理具体成员关系，`members.py` 中的独立分区协调器维护索引及共享成员关联。包入口只重导出公共类型，内部直接引用所属模块；编解码和一次性文件读写不因拆包合并进领域对象。

- `saves.operations` 按转移、单存档编辑／查询、诗句规则分为 `transfer`、`edit`、`poem`；copy／move 等少量包装与转移实现放在同一模块，不按每组函数机械拆分。包入口只重导出公共函数及别名；不隐式加载、提交文件或创建备份。

- 存档 XML 使用 `lxml.etree` 作为临时编解码边界；已支持格式按持久化声明建模，不长期保留原始树、序列化片段或节点代理。字段与 lxml 的适配复用 `codecs.xml.model.XmlModel`，简单集合直接投影到值类型，不为实体逐项建立 XML 包装模型；严格模式、归属、冲突及联动仍由领域编解码负责，支持范围见 `docs/finished/save-xml-binding.md`。未知字段默认 error，显式 warn 后丢弃，不能宣称仍保留未知 QName 或扩展。普通成员编辑不自动联动全局累计，跨对象联动由显式业务组合完成。存档领域对象支持标准浅拷贝和深拷贝，不提供 `.copy()` 便捷方法；浅拷贝共享领域成员，但根对象的派生索引独立重建，深拷贝递归隔离可写数据并保留副本内部的关联。后端研究历史见 `docs/finished/save-xml-backends.md`，当前迁移状态以 `docs/finished/saves-modeling.md` 为准。

- XML 词法格式不作为原文保真契约：输出统一从领域值建立新元素，不为声明、换行、空标签、注释或逐坐标数值写法实现适配层。有游戏语义的默认值差异、累计偏移和主文件双会话变化基准仍需明确建模；未知扩展按读取策略处理，不隐藏在原始材料中。用户通过编辑前后的 `tostring()` 自行生成 diff，不必了解具体格式。独立验证编解码正确性属于开发测试，需对照源码与 .NET 协议样本；不能仅以自己的编码结果重新解析成功作为正确性证据。

- `berries.game.saves.files` 实现独立单文件编解码：`MainSave`、`ModBackupSave`、`CollabUtils2Save`、`EndersBlenderSave`、`CommunalHelperSave`。五种格式已接入 `Save` 一次性读写及领域编解码；组合业务仍按 `docs/saves-domain-api.md` 分阶段实现，勿把全部草案当成已实现接口。五种格式均已接入声明字段值模型并完成集成验证，不保存原始树或序列化片段，未知字段默认 error，显式 warn 发出 UnknownSaveFieldWarning 后丢弃，过滤／升级由标准库 warning filter 控制，不提供 ignore 策略；编码失败不改变领域值，独立领域副本不共享可写状态。时长以 `Duration` 保留有符号 ticks，协议转换拒绝精度损失和整数溢出。Collab 嵌套 XML 按需解释，无效内容在请求时抛出可诊断异常，不在文件层吞错或发 warning。进度对象和当前会话归 `saves.progress`，会话输入归 `saves.session_input`，房间统计归 `saves.rooms`，成员管理归 `saves.progress.members`；旧 `saves.models` 快照和节点代理已删除。原版通过 `main.vanilla` 访问，Mod 通过 `main.mods.campaigns`／`recycled_campaigns` 访问；不提供统一的 `main.campaigns`，仅活动区 Celeste 持久化条目通过 vanilla_entry 建模，不执行运行时转发，也不进入活动 Mod Campaign 集合；缺失保持 None，不自动补建，原版视图独立存在。严格读取与写出要求该条目的 Areas／未解析 Level／Poem 为空，宽松输入保留供显式修复；主文件在输出副本中镜像原版累计，备份保留自身合法累计；回收区同名项作为普通 recycled_campaigns['Celeste'] 保留，不设专用字段或原版联动。`flat_levels`／`recycled_flat_levels` 为分区索引 property；加载时通过解析参数 `on_campaign_conflict`／`on_level_conflict` 处理区内重复，查询不再消歧。Campaign 成员和扁平索引两种写入口统一预检并同步所有相关索引，组合 KeyedDict 而不继承可绕行的修改实现；活动区与回收区允许同 SID 的独立历史。详细数据引用与游戏丢进度背景见 docs/saves-containers.md。Level 直接公开 maps 列表，允许用户自由调整；strict=False 保留显式 Modes 的实际长度，不补足三项，只有显式 null 子项保留 None；省略或 nil 容器按游戏构造默认值生成三个空进度。删除多余项用 del maps[3:]，补项由用户显式 append/extend；写回要求恰好三个非 None 条目。删除 flat_maps，按分区索引取 Level 后显式访问 maps[side]，不能用查询静默丢弃异常项。Ender's Blender 使用并列的 `first_clear/last_saved`，六类统计可分别缺项，名称映射保留顺序；两组统计均为 dict[str, RoomStats]，加载时转换全部已建模数值，room_key 显式编码协议键，不保留 RoomRecords 或惰性解码器；缺少关联项仍可加载后修复，非法数值修复入口尚未实现。已知 YAML 别名解码为独立领域值；未知字段不进入领域模型，不能宣称 warn 模式仍无损保留扩展。完整建模迁移、float32 探针范围及独立的构造入口边界见 docs/finished/saves-modeling.md。

- `Save` 是支持编解码的内存存档领域对象，构造空白对象；通用路径／流适配由 `berries.text_doc.TextDocMixin` 实现，具体格式提供加载和文本编码；`read(file, ...)` 一次性读取，`fromstring(str | bytes, ...)` 读取内容，`tostring()` 返回文本，`write(file)` 显式写出，不持有源文件或流，不提供模式、关闭状态或上下文协议。编码在打开目标前完成；路径采用普通覆盖；路径／二进制流输出 UTF-8，文本流接收字符串并使用自身编码，XML 文本不带编码声明。外部流从当前游标写入，不 seek、truncate、flush 或 close。备份、原子替换、并发保护和 diff 由调用方处理，不提供隐式重试。
- `SaveSlot` 通过只读 number 和 saves_dir 保存编号及 Saves 目录路径，不使用 dataclass 或 slots，`main()/backup()/mod(SaveType, /, *args, **kwargs)` 按需一次性加载内存文档；`ModSave` 子类提供 `mod_name`，不随 Mod 格式增加槽位字段。`mod()` 原样转发解析参数给具体类型的 `read()`，由通用读取入口交给具体 `fromstring()`，不传给构造器，也不在槽位层解释格式选项。strict、unknown 和冲突回调只在当前解析调用链中传递，不作为已解析存档的实例状态；未解析的惰性 SessionInput 仍保留内层所需策略。具体格式的 read 通过 ParamSpec 保留其 fromstring 参数和返回类型的静态检查；参数错误可能发生在内容已读取之后。通用入口的文件／内容参数仅限位置传入，返回类型保持具体子类，动态 Mod 入口仍不承诺逐子类参数的静态检查。`SavesDir` 是普通目录对象；slots 字段提供 SaveSlots 有限只读序列视图，首次需要范围时按规范主文件名发现并缓存长度，之后由 slots.invalidate_cache() 显式清除缓存，下次访问范围时重新扫描；清除缓存不访问目录且可重复调用，扫描失败抛错且不缓存失败结果；范围保留空位，整数下标拒绝负数与越界，切片返回位置元组，过滤空位须显式请求，slots[...] 定位不参与普通迭代的 debug 槽位。find_empty_slot(start=0) 接受非负起点，默认从 0 开始，独立于范围缓存按当前文件寻找缺少主文件的位置，不创建或预留文件。损坏文件仍是存在的槽位，权限错误不得被“空位”吞掉；孤立伴随文件只能通过已选槽位显式访问，不冒充已有主存档。协调层不加载所有伴随文件、不自动恢复、不扫描安装 Mod。`operations` 只组合传入对象，不隐式读写磁盘。

- `SavesDir` 直接接受保存目录；`from_game_dir()` 才按平台与 `EVEREST_SAVEPATH` 定位活动存档。单文件不保存编号，不自动读取相邻补充文件。Pist 的 `SaveProgressReader` 组合目录层，显式恢复整个 Mod 快照，不逐地图补缺；只读流程中的恢复仅修改新加载的内存文件，不落盘。它一次构建浏览统计索引，`SaveProgress` 中不复制主文档或实现解析器。保留会话失败由应用错误回调决定是否跳过和提示；仅在回调正常返回后缓存该快照的失败 SID，重读清除，不吞掉回调本身的异常。
- 查询使用最终 `sid + side`，不依赖 Level 或已安装地图。原生枚举保留零用时条目与回收地图集；Ender's Blender 枚举合并各统计字典的键，保留可能有歧义的协议键，不自动把 `_B/_C` 后缀解码为唯一身份。支持的是已明确的统计子集，不宣称覆盖全部游戏或 Mod 存档字段。
- 无 SID 的原版进度使用包内 `game.vanilla.VANILLA_SIDS` tuple，不提供自定义映射参数；数字 Area ID 是固定的 `0–10` 游戏列表索引，不是章节号。解析前拒绝负数及其他非法整数，超出 tuple 范围的 Level ID 保留为 CampaignProgress.unresolved_levels 中的结构化值，写出前须显式修复；已有 SID 优先。规则仅适用于原版分区根 `Areas` 和原版 `CurrentSession`，不得套到 Mod 区域（包括名为 Celeste 的 Mod 分区）或安全会话，也不向文件补写 SID。最佳时间仅在正 ticks 时成立，最佳死亡／冲刺数由 `SingleRunCompleted` 标识，合法零值不能当作缺失；最佳成绩、检查点和莓实体分别按需解释，不能因缺少累计字段就隐藏独立成绩。不按当前地图或金莓规则推断，原始成绩值与是否成立分开，不因标志为 false 或时间非正就丢掉原始值，不由 setter 暗改其他字段。完整语义及来源见 `docs/saves-domain-api.md`。
- Pist 的只读浏览投影 `pist.saves.MapStats` 从公共可编辑 `MapProgress` 和 Level 的磁带值构建，不是 Berries 的公共快照；`save_progress` 负责“正用时才算已玩”的判断、列表排序和跨存档聚合；`SaveProgressIndex` 在工作流内惰性建立，显式 `refresh()` 才清除聚合缓存，读取其他存档不会隐式刷新。目录及应用读取每次加载当前文件，不隐式维护应用进度缓存。

存档重复身份默认抛出 `BadProgressSave`；`MainSave`、`ModBackupSave` 和槽位入口可在 `strict=False` 时分别传入 `on_campaign_conflict(old, new)` 和 `on_level_conflict(old, new)`；前者接收 `CampaignConflictContext`，后者接收额外携带实际 Campaign 的 `LevelConflictContext`，上下文直接携带实际存储分区，根部原版为 VANILLA、LevelSets 包括 Celeste 条目为 MODS、回收区为 RECYCLED，不按名称推导；身份通过 progress.key 访问；两个上下文是独立的 frozen dataclass，只固定字段绑定，不复制或冻结其中的领域对象，同一存档内不重复携带文件信息，解析异常仍保留文件来源；回调返回同类型、同身份的进度对象，可自行合并，不维护额外候选集合。严格模式忽略回调并报错。活动区和回收区在加载时独立，跨区同名不触发加载回调；写回仅保留加载消歧结果。同一区内跨 Campaign 的 SID 重复也须在加载时消歧；合并结果安装到已有正确归属，另一重复项移除，无正确父结构则报错。两种回调独立，未提供相应回调则该类冲突报错；不要求回调实现两种 overload。没有独立查询回调或跨区隐式合并。回调异常原样传播，不被解析包装吞掉；不得用字典赋值无声覆盖重复输入。完整契约见 `docs/saves-domain-api.md`。

- 存档领域对象的 `reset()` 保留身份和结构，只重置建模进度，不联动父累计；移除可变集合成员使用 `clear()`，存档级累计协调使用 `operations.clear_*_progress`。复制、移动、合并、覆盖统一采用 `(source, target, ...)`。分区索引按 SID 迭代，keys／values／items 为当前索引的活视图；复制成员容器不复制源根的订阅，不把 SID 冗余加入 MapProgress。公共别名 `SaveFile` 从 `saves.files` 导入，`TransferMode` 从 `saves.operations` 导入；错误类型 `ProgressConflictError`、`UnsupportedProgressError` 分别表达冲突处理、无法安全处理扩展的恢复方向，原生 I/O 和 `BadProgressSave` 分类不变。不提供显式取消提交接口。

## 收集品实例与完成状态

- 全收集判定须核对当前规则确认的红莓、月莓实例 ID，不以存档收集总数抵消缺失实例；无法确认 ID 或所需实例的 ID 在实际地图实体中重复时不得自动判定全收集。重复检查不限于单个类别，也须考虑未知或不参与统计的实体；单个实体被多个统计规则投影不等于多个实体重复。记录编辑发现重复 ID 时，须通过运行时警告展示地图与异常 ID，不得只静默改变完成状态。
