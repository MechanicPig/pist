# 存档 XML 字段绑定

状态：2026-10-23 已接入存档编解码。该机制是 `berries.game.saves.codecs.xml` 内部的字段绑定工具，不是替代 lxml 的 XML 框架，也不是新的文件会话接口。

## 背景与选择

存档完整建模后，领域对象已经不再保留原始 XML 节点，但字段模型与 XML 之间仍存在大量手写的属性取值、标量子元素读取及输出循环。直接引入 pydantic-xml 可以减少这部分代码，但实测发现额外的内部 XML 树转换、逐实体包装模型和通用序列化过程带来明显成本；空的未知子元素及 nil 集合的行为也不能直接满足现有存档契约。

因此使用现有 Pydantic 与 lxml 实现最小字段绑定：从 lxml 元素提取原始值，交给 Pydantic 校验；输出时从已校验字段直接创建 lxml 元素。不增加依赖，不生成内部 XML 节点树，不给每个草莓实体创建中间 XML 模型。

早期原型的瓶颈主要是正常字段也创建诊断生成器、所有叶节点都执行通用递归准备，以及逐实体创建模型。正式实现仅在发现未知字段时构造诊断载荷，叶节点不分配重复项计数字典；重复属性投影输出时将属性分支提到循环外，不重复选择绑定方式。字段计划按模型类缓存，不按输入文件重新分析类型与路径。

## 声明与职责

`XmlModel` 默认将 Pydantic 字段 alias 绑定为 XML 属性；声明 `elements=True` 时绑定为标量子元素。布局可被子类继承，特殊路径使用 `Annotated[..., Xml(...)]` 明确声明。例如地图的草莓直接投影 `Strawberries/EntityID/@Key` 到 `list[MapEntityID]`，不经过实体包装模型。

```python
from typing import Annotated

from pydantic import Field

from berries.game.saves.codecs.values import SaveCount
from berries.game.saves.codecs.xml.fields import XmlText
from berries.game.saves.codecs.xml.model import Xml, XmlModel


class Fields(XmlModel):
    deaths: SaveCount = Field(0, alias='Deaths')
    checkpoints: Annotated[list[XmlText], Xml('Checkpoints/string')] = Field(default_factory=list)


fields = Fields.from_xml(root, unknown='error', path='Session')
result = fields.to_xml('Session')
```

支持的路径只是直接子元素链和可选的末尾属性，不提供 XPath、任意命名空间分派或联合模型自动识别。根名称由拥有格式语义的调用方检查；输出名称传给 `to_xml()`，固定结构也可通过类属性 `xml_tag` 提供默认名称。公开元素编解码入口统一为 from_xml／to_xml，文本编码使用 tostring；需要与其他领域成员共同输出时，内部钩子 `_write_xml()` 将绑定字段写入新建的目标元素，不负责无损修改既有输入树。

字段或集合元素类型为 XmlModel 时，绑定计划自动选取该类型的 from_xml／to_xml，支持去除 Annotated 元数据及单一可空包装，不猜测多分支联合类型。普通嵌套声明只需类型与路径，例如 `Annotated[AudioState, Xml('Audio')]`；未提供 Xml 元数据时，模型类型默认按字段名／alias 绑定元素，而非属性。自动输出使用绑定路径的叶节点名，子模型无需另设 xml_tag。显式 decoder 或 element_encoder 分别覆盖对应方向；单方向定制仍可自动推导另一个方向。非 XmlModel 值类型继续显式提供适配器，CurrentSession 等有附加协议检查的字段也保留专属适配。

from_xml 的元素参数不接受 None。节点缺失时是否创建默认对象、保留 None 或报错，由拥有该字段的读取逻辑决定；普通绑定字段通过模型默认值或 Xml.missing 处理，Session 显式处理缺失的 Audio、Inventory 和 SummitGems。实际存在的空节点与缺失节点保持区别，nil 的协议含义仍由对应解码器解释。

Session.from_xml 的 root 只接受 Element，不在领域解码入口兼容文本或 ElementTree。持有文本的 SessionInput 先解析再传入根元素，持有树的调用方显式取 getroot；已经持有元素的主存档解析不会再发生序列化或重复解析。

领域 from_xml 不要求元素名等于 xml_tag，MainSaveData、ModSaveData 和 Session 可解码改名后的同结构元素。文件格式识别归 MainSave.fromstring／ModBackupSave.fromstring，分别要求 SaveData／ModSaveData，且不受 strict 开关影响。Session 仍按 CurrentSession 标签决定是否使用原版数字 ID 回退；其他名称要求可解析的 SID。当前会话由调用方按字段名选取节点后调用通用 from_xml；nil 检查由绑定层执行，严格结构检查由 model_post_init 执行。

读取时的 `attributes`／`children` 是调用方明确负责的其他根成员名称；绑定层允许这些名称但不递归解释它们。Campaign、Level、会话、分区和动态成员的解析与协调仍由领域编解码负责，不能因为字段绑定而绕过它们的严格模式、冲突处理或索引维护。

已接入 Map、主存档标量、Session 标量、Assists、库存、地图引用、音频参数、音轨属性、会话计数器，以及实体与字符串集合。动态 Campaign／Level 成员、旧会话与安全会话协调、SummitGems 及 Mod 专属 YAML 规则不被强行改写成通用字段模型。

## 主存档直接建模

最初接入时，主存档仍通过 `MainFields` 中转，再逐项赋值到 `MainSaveData`；写出时又反向建立该字段快照。这虽然合并了 XML 取值循环，却保留了两份字段声明及搬运逻辑。当前已删除 `MainFields`，由 `MainSaveData(XmlModel, elements=True)` 直接声明字段、默认值和 XML 绑定。

`XmlModel` 本身允许可变领域对象；临时边界快照通过 `XmlFields` 单独设置 frozen。`MainSaveData` 的赋值不自动执行边界校验或累计联动；读取和输出时仍完整验证声明字段。LastSave 是单独的冻结字段，赋值或删除抛出 Pydantic 的 `ValidationError`，不再依赖只读 property。

Assists、LastArea 与 SummitGems 使用 `Xml` 的节点 decoder／element_encoder 适配现有领域编解码，Flags 直接投影为集合。节点适配器接收 unknown 与完整字段路径，返回领域值，不保留元素。SummitGems 继续由专用 codec 保持 nullable 数组和六项长度的规则，严格读取长度要求由 SummitGems codec 在节点解码时执行，不在根入口重复检查。

`MainSaveData.from_xml(root, ...)` 是完整 SaveData 的单一 XML 读取入口，接收本次解析的 strict、unknown 和两种冲突回调。绑定层只提取字段输入，动态进度和双会话由对应解码器处理；解析结果进入与直接构造相同的 Pydantic 校验路径，不通过 context 传递集合。普通字段和 levels／campaigns／recycled_campaigns 输入完成校验并安装后，model_post_init 检查容器身份及分区，建立绑定视图和索引；MainSave 随后建立编辑基准。未提供集合时才创建默认成员，不先构造空白默认 Campaign／Mod 快照再覆盖，字段 validator 也不重复执行。输入缺失覆盖不会被记为显式出现，例如 LastSave 缺失读为 None，而空构造保留默认日期文本。

集合输入统一声明为 `PrivateAttr(alias='campaigns', default_factory=CampaignMembers)` 等形式。FieldModel 从私有属性注解生成构造输入的 core schema，先校验输入，再将值移入私有存储，随后执行用户的 model_post_init；默认工厂只在缺少输入时执行一次。alias 不成为普通持久化字段，不进入 model_dump，也不留下第二份状态；未声明 alias 的派生私有属性不接受构造输入。FieldModel.__init_subclass__ 包装初始化回调，先通过私有属性的 get_default 初始化尚未建立的存储，再转移 alias，最后调用原回调；Pydantic 已有包装遇到初始化后的存储不会再次执行默认工厂。需要完整字段元数据的签名生成和 setter 包装则放在 __pydantic_init_subclass__。直接构造使用领域字段名，主存档的 XML 协议名称使用 Xml 元数据表达。

保留的 _FieldModelMeta 只通过 dataclass_transform 注册字段说明符，不重写 __new__ 或其他运行时方法。曾尝试直接装饰 FieldModel，但当前 Pyright 在继承 Pydantic 元类的模型上不识别新增的私有 alias 构造参数，因此仍需这一声明层，并仍依赖 Pydantic 的 ModelMetaclass 私有入口；这不是运行时初始化必须使用元类的理由。core schema 扩展使用 Pydantic 提供的 schema hook。升级 Pydantic 或 Pyright 时应回归继承构造提示、alias 输入校验、默认工厂次数、初始化顺序、序列化及比较／复制策略，再评估能否移除声明层，不能仅验证正常字段仍能读取。

完整入口曾通过 read_xml 原地替换空对象，并增加旧来源解绑、活视图更新和 model_post_read 钩子。实际应用没有需要保持原对象身份的 XML 重载调用，这些机制只有工厂内部和专属测试使用，因此已删除。需要重新读取时创建新对象；此前持有的视图仍属于原对象，不自动迁移到新对象。领域成员 setter 的活视图同步和标准复制契约不受影响。

校验已初始化对象或输出可变对象时，直接复用普通模型字段的 model-fields core schema，连同 definitions 和 config 一起编译并缓存，不构造第二个 MainSave 或初始化新的默认进度及索引；没有 Xml 声明的私有 alias 不参与 XML 字段校验；显式绑定的私有输入按其公开 property 取值并复用已有 schema 校验。字段 validator 仍在该 schema 中；存在包裹模型的 validator 时使用完整模型校验，不绕过它。新增的 core schema 适配由测试约束，升级 Pydantic 时需连同 definitions、alias、模型 validator 及初始化副作用回归一起验证。

`ModSaveData` 拥有活动／回收 Campaign、safe_last_map_ref、safe_session 和 vanilla_entry，供 MainSaveData 与 ModBackupSave 共用；MainSaveData 另拥有原版 levels、unresolved_levels、poem、unlocked_levels、total_strawberries 和 vanilla_session。需要内部替换的 levels／campaigns／recycled_campaigns 使用私有属性及只读 property，不声明为冻结字段；安全会话及最后地图引用通过 Xml 路径绑定，Campaign 分区和占位项交给领域编解码。私有集合仍参与 FieldModel 的值比较和标准复制，不是另一份完整快照。vanilla／mods 分别是 VanillaProgressView／ModsProgressView，只绑定根对象，不保存第二份进度；它们没有 setter，不能整项赋值。字段和受控成员仍可通过视图编辑，变动直接反映在根模型中。

集合 property 不提供 setter，防止直接换掉容器绕过索引协调；内部在预检后直接更新私有属性，不使用 object.__setattr__ 绕过 frozen。普通成员修改和 vanilla.levels 替换使用原有预检。显式安装准备好的数据使用 install_campaigns 或 install_mod_progress，后者用于恢复等整体业务操作，不替换视图本身。已持有的视图及索引保持活性。视图与索引声明为 `PrivateAttr(compare=False, copy=False)`，副本建立指向新根的视图；浅复制仍共享原有集合及进度值，深复制隔离可写数据。VanillaProgressView.snapshot() 将分散的原版字段组合为 CampaignProgress；ModsProgressView 不再提供 snapshot()，独立 Mod 数据统一由 ModSaveData 表达。需要递归隔离时显式 deepcopy 数据模型，复制视图本身不是复制进度的入口。

ModSaveData 只持有活动 Mod 和回收区索引，MainSaveData 自己增加原版与活动 Mod 的合并索引；不通过两个私有字段先引用同一索引、再由子类覆盖来表达范围。备份的 flat_levels 和 mods.flat_levels 返回同一活视图，主存档两者范围不同。整体安装先收集全部相关索引的预检结果，再改字段并逐项应用；每个索引只处理一次，合并区冲突也不会部分安装 Mod 数据。

to_xml 从实际字段输出完整数据：包括原版位置 ID、Mod 进度、双会话变化镜像及 Celeste 占位累计同步。原版编码器接受原版视图，Mod 编码器直接读取 ModSaveData 的 Campaign 分区，输出不创建临时进度快照或索引。MainSave 只增加文本／文件适配、读取后的 Mod 编辑基准及输出标记判定。基准按 ModSaveData 的比较元数据选取实际字段值并保存独立副本，不包含根对象、视图或索引；成员容器本身保留顺序比较语义。标准 read／fromstring／write／tostring 工作流不变。

### 删除重复的 Mod 进度模型

此前 ModsProgress.from_xml() 创建空快照和两套索引，再解析、替换成员并重新配置索引；根模型随后接收这些数据并再次建立索引。该类型与 ModSaveData 重复维护同一组数据，snapshot() 又只分离成员关系，没有提供递归隔离。因此删除 ModsProgress、to_elements() 和 Mod 视图的 snapshot()，不是仅迁移或重命名它们。

活动／回收 Campaign 的解码与跨 Campaign 冲突处理移入 Mod codec，返回带精确类型的构造输入字典，不创建临时根模型或索引。safe_last_map_ref 与 safe_session 使用 Xml 注解；会话输出仍调用当前安全会话编码器，保持 SID 位于数字 ID 之后的协议要求。MainSaveData 和 ModSaveData 将这些输入交给最终模型校验，再建立自身索引。成员顺序、宽松读取后的修复能力、冲突回调异常传播和两区独立性保持不变。

ModSaveData 是唯一可独立持有这一组 Mod 数据的模型，整体安装接受该模型或 ModsProgressView，并沿用显式输入的引用语义。恢复备份时 deepcopy(backup).mods 提供独立数据，不再先创建快照外壳、建立临时索引再深复制。相等与复制复用 FieldModel 的字段元数据，不保留旧模型的手写比较。

### 完整性核对与实际边界

此前虽已删除 MainFields，MainSaveData 的绑定仍只覆盖普通字段，MainSave 继续补读四类 PrivateAttr 并在输出时拼装进度；因此“普通字段绑定已迁移”不能视为“完整领域编解码已迁移”。本次以完整 XML 入口重新核对持久化字段、结构检查及输出协调，保留下列明确分工，不把动态成员和读取策略硬塞进通用 Xml 元数据。

| 数据或检查 | 声明／读取 | 校验与输出 |
| --- | --- | --- |
| Name、Time、TotalDeaths、TotalGoldenStrawberries、AssistMode、VariantMode、CheatMode、Version、TheoSisterName、TotalJumps、TotalWallJumps、TotalDashes、RevealedChapter9、LastSave | MainSaveData 的 Pydantic 字段及 XML alias | 共享标量适配；必需节点由 Xml.strict_required 声明，17 ms 精度由 XmlTimerDuration 注解校验；互斥模式在 model_post_init 检查；字段绑定写出 |
| HasModdedSaveData | 普通字段 has_modded_save_data，直接绑定，不再另行补读私有值 | MainSaveData 输出存储值；MainSave 结合独立编辑基准判定输出标记 |
| Assists、Flags、LastArea、SummitGems | MainSaveData 的 Xml 节点绑定／集合投影 | 对应值模型／codec；SummitGems codec 检查正常长度 |
| 根 Areas、Poem、UnlockedAreas、TotalStrawberries | CampaignProgress.from_main_xml，由完整数据入口调用 | 原版 codec 检查必需节点和原始条目数／最终顺序；编码原版位置 ID |
| LevelSets、LevelSetRecycleBin、Celeste 占位项 | Mod codec 解码 Campaign 分区，返回根模型构造输入 | 分区冲突和归属按现有策略处理；encode_campaigns(vanilla=...) 在临时副本中同步占位累计，不改输入对象 |
| LastArea_Safe、CurrentSession_Safe | ModSaveData 的 Xml 字段绑定，主文件继承同一声明 | 自动选择 MapRef／Session 的解析；安全会话输出保留 SID 最后写入的顺序 |
| CurrentSession | vanilla_session 的 Xml 节点绑定，按 Session 类型自动选择 from_xml | strict／unknown／path 沿绑定传入；身份输入通过注解校验并保留缺失，model_post_init 按分支补齐；缺少原版 ID 不按默认 0 推导；完整数据输出协调既有同身份双会话，不创建额外分支 |
| 活动／回收根索引 | 构造方法在成员就绪后为实际进度建立一次 | 不持久化、不比较、不复制旧索引；成员替换仍遵循活视图契约 |
| Mod 编辑基准 | 仅 MainSave 在构造末尾按实际数据建立 | 不持久化，不参与数据相等；保留标准复制行为 |

解码仍会访问临时 XML，但访问发生在完整数据入口及对应领域解码器，而不是由文件适配层在“完成读取”之后补漏。必需节点是否省略／nil、原始 Areas 条目数等信息必须在转换成默认值或消歧集合前检查；最终数据不能反推出这些输入事实。固定字段的通用绑定、动态进度的领域解码以及原始结构检查是三个明确步骤，不宣称全部字段已经自动声明式解码。

MainSaveData 不再手写 __eq__，而是显式加入 `berries.model_behavior.FieldModel`，统一按字段声明处理比较与复制。实际 PrivateAttr 存档数据默认参与值比较和复制；派生索引使用扩展的 `PrivateAttr(compare=False, copy=False)`，不参与比较，也不遍历或复制旧索引容器与订阅。不再通过 slots 或普通运行属性的位置间接决定行为。构造方法在成员就绪后立即建立索引，保持加载时检查唯一性的时序，不把索引改成惰性查询；统一复制过程完成后调用 model_post_copy，为共享或深复制后的领域成员重建独立根索引。copy.copy／deepcopy 以及 model_copy 的两种模式遵循相同规则。MainSave 的编辑基准使用 `PrivateAttr(default=None, compare=False)`，在构造时按实际数据建立，默认仍复制：浅拷贝共享该基准，深拷贝隔离它。基准仍可影响后续写出标记，因此数据相等不承诺文件协调历史或输出文本相同。

这些选项只在显式使用 FieldModel 的模型上生效，不改写其他 Pydantic 基类。普通字段使用 `Annotated[T, FieldOptions(compare=False, copy=False)]` 元数据，可与原生 `pydantic.Field(...)` 的默认值、别名和验证声明组合；不复制 Field 的参数签名，也不把选项放进 JSON Schema。两个选项独立，默认为 True，序列化仍只由原生 Field(exclude=...) 等配置控制。copy=False 表示省略旧值而非自动恢复默认值，由 model_post_copy 负责补齐此类派生状态；该钩子在浅／深复制时均执行，必须只重建副本派生状态，不修改共享领域成员。深复制在遍历前登记源根与副本的对应，保留循环和共享关系。冻结模型的自动 hash 排除不比较的普通字段，显式自定义 hash 则由声明者负责。

## 固定结构直接建模

Assists、PlayerInventory、MapRef、AudioParameter、AudioTrackState、AudioState 和 SessionCounter 同样直接继承 `XmlModel`，不再保留对应的 `*Fields` 中转模型。字段使用 `Xml` 元数据声明协议名称，构造器使用领域字段名的 keyword 参数，避免 XML alias 影响静态构造提示。嵌套对象及音频参数列表直接返回领域对象，不建立第二份数据模型；列表保持原顺序和重复成员，nil 集合继续忽略其载荷，未知字段的完整路径保持不变。

这些对象仍按字段值比较；MapRef 保持不可变、可哈希，其他对象允许编辑，在构造／读取及写出边界校验。未提供 XML 节点时使用空构造默认值；存在空 Assists 元素时 GameSpeed 按游戏反序列化默认值读取为 0，而 `Assists()` 的默认值仍为 10。音频音量的 nullable 写出与 float32 转换继续由绑定和既有数值适配处理。

Session 也直接继承 XmlModel，普通属性、Audio、Inventory、Counters、收集品集合、SummitGems 和 Sliders 均通过 Xml 声明读写，删除 SessionFields 中转模型及字段搬运。RespawnPoint 的坐标元组和 Sliders 的键值表由专属节点 codec 适配；nil 出生点仍写出显式 nil，缺失 Audio／Inventory 使用字段的默认工厂。Session 使用 Pydantic 正常构造，side、room、time_played、deaths、room_deaths 必填；sid 为只读 property，side 为冻结字段。sid 可以显式提供，也可以省略并由显式提供的原版 level_id 推导；不允许两个身份输入都缺失，不提供可用的无参空会话。Session 不再重写 from_xml，使用通用字段提取和 model_validate，不使用 self_instance 或绕过构造。构造和 XML 读取均在 model_post_init 中按完整初始状态建立变更基准；读取策略不进入对象。写出边界校验全部普通模型字段，包括没有 XML 绑定的必填身份，再只编码声明绑定的部分。

XmlDecoder 接收本次读取的 strict、unknown 和 path，通用绑定逐层传递，不将其存到模型。MainSaveData 的 vanilla_session 注解绑定 CurrentSession，完整读取入口不再补读该节点；输出通过 `_xml_value()` 投影先准备同身份双会话的临时合并值，再交给绑定层校验和编码，避免先编码旧分支再删除重写，也不修改原会话。该投影只改变本次编码输入，不替换存储字段。

Session 的 sid、side、level_id 分别直接绑定 Area/@SID、Area/@Mode、Area/@ID，不经过 MapRef 的构造、展开或输出重建。Mode 缺失时仅在 XML 读取中采用 A-side，直接构造 Session 时仍必须指定 side；Mode 文本转换由 XmlSide 类型与 MapRef 共享。身份使用带 Xml 元数据的 PrivateAttr(alias=...) 接收构造输入：_sid 和 _level_id 在输入阶段允许为 None，非空值仍由注解验证文本及 Int32 范围。model_post_init 直接检查值之间的依赖：缺 SID 时必须有原版数字 ID，且 XML 只能来自 CurrentSession；确定 SID 后才将缺失数字 ID 补为游戏默认 0。对外 sid: str 只读，level_id: int 仍可编辑，不再用字段出现集合判断默认 0 是否真实提供，也不依赖字段声明顺序。MapRef 保留用于 LastArea 等可未解析的持久引用。

LevelProgress.from_xml(root, *, vanilla=False, strict=True, unknown='error', path='AreaStats') 统一解析自身身份，不再接受外部 sid 参数。显式 SID 优先，vanilla=True 时才允许缺失 SID 的条目通过自身 ID 查询 VANILLA_SIDS；无法确定身份时抛出 UnresolvedLevelIdentityError，属于 BadProgressSave。Campaign 只捕获这一身份异常，在宽松模式下解码为 UnresolvedLevelProgress，字段损坏等其他错误仍传播，不重新解析已经成功的 Level。

Session 的 OldStats 通过 Xml 注解绑定，decoder 为 partial(LevelProgress.from_xml, vanilla=True)，encoder 为 LevelProgress.to_xml，不再由 from_xml 补读或手动拼装输出。解码只依据 OldStats 自身的 SID／ID；model_post_init 要求解码结果的 SID 与 Session.sid 一致，再建立变更基准，因此 Mod 缺失 SID 而被数字表解析成原版的情况会在领域校验中被拒绝，不依赖父节点或来源标记。未知 ID 仍由 Level 解码器报错。该身份关系是构造不变量，不受 strict 影响；OldStats 可被用户替换，写出时也重新检查关系，避免不一致的编辑状态进入文件。

Session.summit_gems 为不可取 None 的 list[bool] 类型：绑定显式声明 nil_as_missing=True，缺失节点与显式 nil 都使用字段的 default_factory 生成六个 False，不将 None 引入领域值或在解析后补值；nil 也按缺失处理字段出现标记。已存在的 nil 节点仍经过解码器的结构和未知字段检查，不因采用默认值而吞掉诊断。该选项默认为 False，仅用于缺失与 nil 已确认等价的字段。宽松读取仍保留显式空数组供修复。绑定声明 many=False，表示解码器处理单个 SummitGems 节点中的整个数组，而非对多个节点逐项解码；未指定 many 时仍按字段容器类型推断。主存档的 SummitGems 不启用 nil_as_missing，保留其独立的 null 语义。

## 声明能力与入口重写审查（2026-10-26）

审查先区分单字段转换、已验证字段之间的依赖、XML 出现信息以及动态进度协调，不能只按函数长度决定是否消除重写。固定字段的范围、枚举、文本和数值转换继续放在 Annotated／Field；存在字段依赖时，输入可以保留缺失，由 model_post_init 在完成字段校验后统一补齐。Session 的身份输入与构造完成后的公开类型分开表达，不用 default_factory 提前消耗尚不能证明有效的默认 0，也不依赖 validated_data 的字段顺序。成功实例始终提供确定的 SID 和数字 ID，不通过绕过 frozen 的赋值完成补齐。

框架新增 Xml.strict_required：仅在严格 XML 读取时要求绑定属性或容器存在且非 nil，不改变普通构造默认值。主存档六个必需节点和 MapFields 的正常字段／集合容器均在声明处表达，不再由根或 MapProgress 的 from_xml 手工遍历检查。Session 声明 xml_nillable=False，由绑定层在提取字段前拒绝 nil 根；其他模型保持各自既有 nil 行为。

XmlReadContext 通过 Pydantic 的 context 参数临时传递根 tag、strict 和诊断 path，不保存 XML 树、集合或回调。它进入字段 validator 和 model_post_init，在构造结束后不成为模型成员。XmlTimerDuration 在字段注解中表达严格读取的 17 ms 约束；普通构造、宽松读取和写出不获得这项限制。MainSaveData 的 AssistMode／VariantMode 关系、Session 分支身份限制、OldStats 身份一致性及严格当前会话结构在 model_post_init 检查，随后才建立基准与索引。Pydantic 字段或初始化校验失败使用 ValidationError，属于 ValueError；绑定层的缺失／nil 结构检查仍使用 BadProgressSave。Pist 的读取错误通道和 SessionInput 均捕获 ValueError，可以继续提供来源诊断。

| 类型／入口 | 是否重写 from_xml | 原因与继续扩展的取舍 |
| --- | --- | --- |
| Session | 否 | 普通／私有构造输入绑定、临时读取上下文与 model_post_init 已足够；删除手工读 Area、再次验证 ID、身份默认工厂和出现集合补救 |
| Assists、MapRef、PlayerInventory、AudioParameter、AudioTrackState、AudioState、SessionCounter | 否 | 固定结构、嵌套模型、列表投影和构造／读取默认差异均可通过现有声明表达 |
| XmlFields 及 MapFields、MapFlags、PersonalBestFields、RespawnFields、EntityItems、StringItems | 否 | 类型与集合投影使用通用绑定；MapFields 的严格存在性与计时规则也在注解中表达 |
| MainSaveData | 是 | 同一个根同时承载原版进度、活动／回收 Mod 分区、跨集合身份冲突和两类回调；入口只协调已有领域解码器，将最终成员与普通字段一次性交给模型构造 |
| ModSaveData | 是 | 备份同样需要协调活动／回收集合及两类冲突回调；保留范围明确的组合入口，不让普通字段绑定承担跨集合仲裁 |

消除后两个根重写需要新增同根多字段解码、动态集合绑定及领域回调上下文，仍不能替代 Campaign／Level 的归属和冲突处理。当前只有两个短组合入口，继续通用化会增加框架对进度业务的了解和验证成本，因此保留它们；不保留能由注解或初始化规则表达的逐字段检查。MapProgress／LevelProgress／CampaignProgress 不是本次所审查的 Pydantic XmlModel，它们的领域编解码入口不因名称相同而机械删除；其中 MapProgress 已将正常字段存在性与计时检查委托给 MapFields。

### 私有构造输入与公开值

XmlModel 通过 FieldModel.private_input_fields() 获取构造 alias 的类型、默认值和元数据；只有明确带 Xml 声明的私有输入进入绑定计划，不将根模型的所有集合自动当成 XML 字段。读入仍使用公开 alias 调用同一构造校验，写出从同名 property 获取已经完成初始化的值，不读取其他模型的私有存储。Session 的 SID／数字 ID 使用这一机制，嵌套 XmlModel 同样可以自动绑定；没有 XML 声明的集合默认工厂不会因写出校验而被调用。

构造 alias 的名字保留在 model_fields_set 中以支持 exclude_unset；这只是显式输入记录，不再参与身份合法性的判断，也不会在 __dict__ 中留下第二份值。默认值不标记为显式输入；FieldModel 自动为声明在本类、对应构造 alias 的可写 property 包装 setter，成功写入后记录 alias，继承属性复用原包装。构造阶段的私有值初始化／归一化不经过公开 setter，因此不会误记为显式输入。Session 的 sid／level_id 通过 computed_field 保留 Pydantic 的公开值投影，原始可空输入不直接序列化；SID 的赋值／删除由只读 property 拒绝并抛出 AttributeError，side 继续由冻结字段拒绝并抛出 ValidationError。

本轮继续检查了默认值的实际语义：MapRef 表达可未解析的游戏 AreaKey，保留其数字 ID 默认 0；Assists 的普通构造默认 GameSpeed=10 与读取空元素默认 0 有游戏协议依据，继续通过 Xml.missing 声明。库存、音频和计数器没有需要靠输入出现集合补救的身份依赖。另移除 ModSaveData 及通用入口中已由下层完成的重复 unknown 策略检查，并修正会话比较遇到不同长度元组时错误抛出的行为。

### 元数据驱动的比较与变更基准

此前 Session._values() 只枚举 model_fields，排除 side 后又手工补回 level_id，__eq__ 再单独比较身份；主文件的 _ModProgressState 也重复列举了 ModSaveData 已声明的数据。这些字段名清单是在补偿机制不完整，已删除，而不是搬到另一个常量表。

FieldModel.iter_comparable_items() 统一按 FieldOptions(compare=...) 与 PrivateAttr(compare=...) 枚举参与比较的声明值，返回字段名和值；普通字段使用模型字段名，私有构造输入使用 alias，没有 alias 的私有值保留声明名。computed_field 只是公开投影，不重复计入；没有声明的 extra 值不在这个字段枚举中，FieldModel.__eq__ 仍按既有规则单独比较 extra。可通过 model=某个基类限定为该类型及其继承来的字段，类型必须适用于当前实例。不运行 XML 编码，也不因没有显式传入就忽略默认值。

Session 的初始基准、变更枚举及相等比较使用同一入口。sid／side 不再因为只读而被排除，level_id 不再手工拼入；_baseline 通过 compare=False 元数据排除。新增普通字段或私有构造输入会自动进入比较和变更检测，compare=False 的缓存或附加值会自动排除。model_fields_set 只表示显式输入／写入，不能用于枚举当前领域状态；容器原地修改也通过当前值与独立基准比较识别。

主文件通过 model=ModSaveData 获取 Mod 数据范围，不再维护 _ModProgressState 的重复字段声明。原版 name、累计等字段不会因此进入 Mod 变更标记，派生视图和索引依照 compare=False 排除。CampaignMembers 的顺序属于其自身比较语义，无需在主文件快照中再转换为特制 tuple；深复制成员容器会脱离原索引关联，不建立临时存档或额外扁平索引。

字段选择、比较／复制排除及 property 写入标记属于可从声明统一推导的机制。原版数字 ID 表、CurrentSession 与安全会话的身份差异、OldStats 与会话的关系、Modes 三项结构则属于游戏协议或跨字段约束，继续由对应领域代码明确检查，不为消除字面量再增加无消费方的角色标签。

## 保持的语义

XmlFiniteSingle 为具有普通游戏数值语义的会话字段提供有限值约束：普通构造、严格读取和写出要求有限值，只有宽松读取上下文允许保留 NaN／Inf 供修复。绑定层统一执行写出校验，包括冻结的 XmlFields；冻结不能保证宽松读取后的值满足输出约束。Slider 仍使用 SaveSingle，不增加统一有限性限制，底层 Single codec 的特殊值能力不变。Session 的相等与变更检测使用普通 Python 比较，删除递归 NaN 等价及 AudioState 专属比较分支。字段范围和消费路径证据见 [Saves 领域 API](../saves-domain-api.md#会话浮点字段的有限值约束)。

- 未知属性、空的未知元素和嵌套未知成员仍遵循 `error`／`warn`，诊断保留领域路径；告警模式不放宽已知值校验。
- 缺失、空字符串与显式 nullable 元素的 nil 可分别表达，`model_fields_set` 配合 `exclude_unset=True` 保持字段是否出现。
- nil 不是通用的“丢弃该对象”指令。集合容器遵循原来的忽略载荷规则，非 nullable 标量按现有存档规则解释；不能因为父对象带 nil 而丢弃其集合字段。
- 集合投影保留顺序与重复值，是否转为 set 或按身份消歧由领域对象决定；单值字段重复仍报错。
- Duration 保持完整精度，写出时检查 ticks 的表示与范围；float32 继续使用现有 Single 适配，不通过普通字符串转换替换数值规则。
- 严格读取、17 ms 精度、进度归属与联动仍是领域层职责。字段绑定不持有文件、不自动保存、不提供备份，也不保留输入节点。

## Celeste 持久化条目与来源分区

持久化字段统一命名为 vanilla_entry，表达 LevelSets 中实际存在的 Celeste 条目；它不是运行时转发器。原版访问由始终建立的 VanillaProgressView 提供，条目默认 None，读取、视图访问和写出不自动补建。主文件输出在临时副本中镜像原版累计，备份没有对应根数据，保留自己的合法累计，均不修改内存条目。

此前严格读取拒绝条目的非空 Poem，但写出仍允许保留，导致宽松读取的异常内容可以直接写成严格读取拒绝的文件。现在读取与输出复用同一条目校验，名称必须是 Celeste，Areas／未解析 Level／Poem 均为空；宽松输入仍可读取并显式修复，写出失败发生在目标文件或流修改之前。回收区 Celeste 是普通历史记录，不受活动条目的空列表规则约束。

此前 Level 冲突上下文通过 campaign_partition() 按 Celeste 名称推导 VANILLA，无法区分根部原版进度与 LevelSets 内条目。现在删除名称分类函数，单 Campaign 解析直接使用传入分区；合并原版与 Mod 的加载扫描根据明确提供的原版 Campaign 对象区分候选来源，直接构造上下文并验证回调返回的身份。活动 Celeste 条目回调为 MODS，回收区同名项为 RECYCLED，根部原版进度为 VANILLA。

## 转移准备与 XML 编解码分离

此前 codecs/xml/editing.py 的 prepared_campaign() 会编码整份 Campaign、重新解码全部 Level，再换回调用方已准备好的 Level；prepared_level() 同样通过 XML 重建 Map 并调整历史莓计数。未涉及序列化文本重解析，但存在不保留结果的编解码和对象分配。现将准备逻辑移入 operations/preparation.py，直接构造领域结果，不以 XML 作为中间状态。

MapProgress／LevelProgress 的 validate_output() 提供无返回值的原生输出预检。Map 的字段快照仍复用 MapFields，时长检查有符号 Int64 ticks 与无损转换，实体键检查房间文本与非负 Int32 ID；Level 检查原生 ID、磁带布尔值及三项非空 Modes。Campaign 元数据由 CampaignFields 同时服务编码与领域准备。预检不施加严格读取的 17 ms 条件，不修改输入，也不取消对整个候选 Campaign 的校验。

独立副本通过领域构造和显式深复制准备；keep_sides 指定的 Map 及未修改的 Level 继续共享原引用。新目标 Map 不继承来源的历史莓计数偏移，已有目标保留自己的偏移；清空来源保留其独立偏移。范围、精度或结构错误仍在安装前拒绝，回归测试核对失败前后领域值、累计及成员引用关系，而不固定内部解析调用次数。

### 2026-10-27 合成对照

环境为 Windows、Python 3.14.9。两端各含 50 个 Level，每 Level 三个 Map；从常驻来源复制一个 A-side 到目标，目标每次从同一模板深复制，计时包含该复制成本，不涉及磁盘或网络。对照仅将当前准备函数替换为 8e3f489 的旧实现，其他依赖保持一致；每组运行 5 轮，每轮 30 次，以下为各轮单次均值的中位数，另一次独立诊断记录解码次数。

| 准备实现 | 单次 Map 操作 | Level 解码次数 |
| --- | --- | --- |
| 旧 XML 往返 | 12.90 ms | 51 |
| 直接领域准备 | 4.27 ms | 0 |

这只证明该本地合成场景减少了重复工作，不表示所有 Campaign 规模或真实游戏操作都有同样收益。整份候选进度的校验仍随成员数量增长，不宣称单 Map 转移已经成为常数时间操作。

## 验证与限制

新增绑定测试覆盖缺失／空／nil、输入树不变、重复节点、注释和处理指令、告警路径及顺序、继承布局、同节点其他成员、实体投影、数值边界与无损 ticks。既有存档读写、会话、转移、收集品联动和 Pist 读取链继续使用原有回归。

本机 52 个真实存档进行只读检查：MainSave 11、ModBackupSave 10、CollabUtils2Save 10、EndersBlenderSave 11、CommunalHelperSave 10。内存规范化输出后重读并再次输出稳定，原始文件字节未改变。这不是实际游戏加载验收，也不等同于性能 benchmark。

性能比较区分热身后的绑定／领域转换与 XML 文本解析；临时脚本及第三方库对照不作为正式依赖。字段计划首次构建有一次性成本，结果不能泛化为所有文件规模或冷启动的固定收益。
