# 存档完整建模迁移

状态：2026-10-22 完成实现、全量集成和三位独立代理审查，已修复审查发现的问题。五种格式、进度与 Session 均按已知字段建模，删除 XML 片段保真、位置标记和空模板解析。本文记录已完成的演进；字段覆盖、内存往返和协议探针不等同于真实游戏端到端验收。

## 背景与目标

原实现仅解释部分字段，将其他材料序列化为 `_opaque`／`_encoding`，输出时重新解析并合入领域值。源码核对表明，多数未解释材料其实是结构确定的已知字段，不需要长期保留原始树或序列化片段。目标是按原版、Everest 及对应 Mod 的持久化声明建立完整值模型，XML／YAML 节点只出现在编解码边界。

完整建模不要求实现游戏运行时服务、地图查询、事件重放或初始化一个可以直接游玩的新 Session。`XmlIgnore`／`YamlIgnore` 的运行时字段不进入存档模型；协议明确保存的其他 Mod 文本／binary 载荷则作为字符串建模，不递归解释其私有格式。

## 已完成：YAML 文件

| 格式 | 对照范围 | 当前状态 |
| --- | --- | --- |
| CommunalHelper | `CommunalHelperSaveData` 的 `SummitGems` | nullable 字符串集合已建模 |
| Ender's Blender | `EndHelperModuleSaveData` 的 14 个字段 | 两组各 6 类统计、名称及颜色均已建模 |
| CollabUtils2 | `CollabSaveData` 的 13 个字段 | 原有 8 个字段及新增 5 个字段均已建模 |

Collab 新增的公开值为 `speedberry_option_message_shown`、`visited_lobby_positions`、`reveal_map`、`pause_visiting_points`、`show_visited_points`。四个布尔值遵循源码默认 false，访问位置保持 `dict[str, str]` 的 base64 载荷，不自动解码为地图访问点。

三个格式均不再保存 `_opaque` 或完整 YAML 节点树。输出根据当前领域值建立临时节点；字段存在性、顺序和已知路径的显式标签仍以不含节点和值载荷的元数据保存。已知字段之间的 YAML 别名解码为独立领域值，不以别名连接两份可编辑状态。Ender's Blender 的名称映射保留其有业务意义的顺序，缺损房间记录仍允许读取后修复；输出前继续验证直接索引所需的关联统计。

Collab 的 `SessionsPerLevel` 在外层 YAML 协议中是 SID 到 XML 字符串的映射。`SessionInput.orig_xml` 继续保存该协议字符串并惰性解析，不能因外层完整建模就提前解析所有会话。请求 Session 时才解释完整字段，外层选择的未知字段策略一并传入；未知字段异常保留具体路径和异常类型。

### 未知字段

五个文件入口接受 `unknown: Literal['error', 'warn'] = 'error'`；该参数同样可经当前的槽位入口转发。未知字段诊断包含模型与字段路径，例如 `CollabUtils2Save.Future`、`SaveData.Assists.Future` 或 `ModSaveData.LevelSets[0].Areas[0].Modes[0].@Future`。当前 YAML 的固定字段都在根映射中；SID、房间名、Mod 名和技巧名等动态映射键不是未知字段。

- `error` 抛出 `UnknownSaveFieldError`，默认拒绝可能造成数据丢失的读取。
- `warn` 发出 `UnknownSaveFieldWarning` 并忽略未知字段；重新写出不会包含该字段。用户可通过标准库 warning filter 忽略警告，或将其提升为异常，不再提供独立的 `ignore` 策略。
- 策略不放宽已知字段的类型、数值或结构校验，也不改变格式错误的处理。未知 YAML 载荷只被 compose 为临时节点，不构造其自定义 Python 对象。

```python
import warnings

from berries.game.saves import UnknownSaveFieldWarning
from berries.game.saves.files import CollabUtils2Save

with warnings.catch_warnings():
    warnings.simplefilter('ignore', UnknownSaveFieldWarning)
    save = CollabUtils2Save.read(path, unknown='warn')

# 注意：再次输出时不会包含被忽略的未知字段。
text = save.tostring()
```

XML 的属性及嵌套成员也遵循该策略。注释、处理指令、CDATA 的词法形式及命名空间前缀不属于存档领域值，不承诺原文保真；不再通过拼接序列化片段保留未知扩展。

## 已接入：XML 与 Session 值模型

字段模型与 lxml 之间已采用直接绑定，统一处理属性、标量子元素及实体／字符串集合，删除对应的手工读取与输出循环；严格模式和领域协调仍由原模块负责。动机、支持范围及验证边界见 [存档 XML 字段绑定](save-xml-binding.md)。

主存档和备份的实际进度进一步展开为 MainSaveData／ModSaveData 的普通字段，vanilla／mods 改为不可替换的绑定视图。独立 Mod 数据也统一使用 ModSaveData，删除重复的 ModsProgress；不再经过临时快照解析或复制。文件适配只负责文本入口，数据类提供完整 XML 工厂与输出；视图及派生索引在复制后重新绑定，恢复使用显式安装准备值的操作。

- 主存档补齐 Version、LastSave、CheatMode、Assists、TheoSisterName、TotalJumps、TotalWallJumps、TotalDashes、Flags、RevealedChapter9、LastArea。Assists 省略时保留初始化的 GameSpeed=10，显式空结构使用字段零值。
- Session 建模 Audio、标记与实体集合、Counters、Inventory、OldStats、冲刺／检查点／收集状态、光照与 CoreMode，以及 Everest 的 Sliders、RestartedFromGolden 和 Audio.AmbienceVolume；嵌套修改参与双会话协调的变化检测。
- `MapRef` 以 level_id、level_sid 和 side 表达持久化地图引用；`ModSaveData.safe_last_map_ref` 保存完整 LastArea_Safe，`last_map` 只是其身份投影。原版写出仍使用固定数字 ID，不额外插入 SID。
- Map 的 TotalStrawberries 保留相对实体集合的历史计数偏移。PersonalBests 保存所有原始成绩值，是否成立由正时间和 SingleRunCompleted 决定，不将未成立的原始值抹成 None。
- `ModSaveData.vanilla_entry` 表达活动 Celeste 占位项，不加入活动 Mod campaign 成员集合；回收区 Celeste 项作为普通 recycled_campaigns 成员保留；主文件写出时继续联动活动占位项的原版累计。
- `CampaignProgress.unresolved_levels` 保存无法确定 SID 的结构化进度，不再隐藏在 XML 中；调用方显式 resolve(sid)、加入正确 Campaign 并移除未解析项后才能写出。当前会话无法确定身份则拒绝读取，不伪造可活动的 Session。
- 初版曾用 `SaveDateTime` 解释 LastSave 并保留 100 ns 精度。2026-10-23 对照源码未发现原版或 Everest 使用该字段，本地 10 个主存档也均为默认时间；因此改为只读的 `MainSave.last_save_text` 字符串透传，并删除专用日期类型。字段文本不是 XML 片段，不恢复原始节点或词法材料。

上述模型只保存领域值及业务变化基准，输出建立新 XML 元素。业务基准按值比较，不通过对输入／输出 XML 做指纹比较来判断 Mod 进度是否修改。原文节点、序列化片段、导入占位符及 QName 拼接保真代码已删除。

## 集成与入口

既有测试中的未知扩展保真、逐字符数值保真及延迟校验断言已按新契约迁移。夹具改用已知字段检验隔离与联动；未知字段另行验证 error／warn，浮点数检验 binary32 位模式，不要求 Python float64 的原始文本。原有读取编码、写出失败不截断目标、集合隔离、累计联动与双会话协调仍须经过最终集成验证。

转移预检使用临时输出元素执行边界校验并形成独立值，不再先生成 XML 字符串再解析。Session 的值比较及变化检测使用普通 Python 比较，不为 NaN 定义相等语义。普通游戏浮点字段严格读取及写出时要求有限值，宽松读取保留异常值供修复；Slider 保持开放的 Single 数值契约。默认构造器直接初始化领域值，不通过生成／解析空模板建立对象；读入真实文件只解析该输入。

nil 的语义由字段声明决定，不能通用地解释为 None：Nullable<Vector2> 可表示空出生点，数组 nil 保留构造默认数组，非 nullable 的类成员即使写 nil 也可能构造默认对象。原版／安全 CurrentSession 的 nil 不当作缺失或触发旧会话回退，而是拒绝无法构成有效 Session 的人工输入，要求在 XML 层修复／移除；正常无会话时省略字段。该兼容边界有专门回归，不能据此把任意 nil 都拒绝。

## 数值实现与入口边界

float32 采用 gmpy2 2.3.2 的 IEEE32／MPFR 转换，将十进制输入直接舍入到 binary32，不再维护自写的相邻中点修正算法。每次转换使用独立 IEEE 上下文，外部舍入模式、精度与 trap 不影响转换，转换也不泄露上下文修改；项目开发环境另用 gmpy2-stubs 检查接口。适配层只负责 XmlConvert 的数字词法、普通 float 值与输出文本。负零、NaN／无穷、次正规数、溢出及中点均有位模式回归，不承诺 NaN 载荷位或 .NET 的逐字符数字写法。

选型前通过临时 uv 环境评估了 NumPy 2.5.4 与 gmpy2 2.3.2：对于 `1.000000059604644775390625000001`，.NET 与 gmpy2 给出 `0x3F800001`，NumPy 构造得到 `0x3F800000`，不能直接替换本次所需的十进制转换。NumPy 没有加入项目依赖。gmpy2 的上下文与 IEEE 模式见[官方文档](https://gmpy2.readthedocs.io/en/latest/contexts.html)，本次采用它是为了缩小自写数值算法边界，不让存档库成为浮点实现库。

数值与 nil 探针运行于 .NET SDK 10.0.400；当前参考 Everest 目标为 .NET 8，探针不能等同于真实游戏验收。初版 DateTime 的 100 ns ties-to-even 舍入依据 [.NET 8 XsdDateTime](https://github.com/dotnet/runtime/blob/v8.0.0/src/libraries/System.Private.Xml/src/System/Xml/Schema/XsdDateTime.cs)，这里只保留历史调研来源；当前实现不再解析、舍入或校验 LastSave 的日期文本。

`last_save_text` 不允许公开赋值或删除；读取保存元素文本，缺失时为 None，输出时省略缺失项，空元素与缺失保持区别。空构造使用 `0001-01-01T00:00:00`。即使 strict=True，也不保证该字符串符合 DateTime 语法；XML 结构和未知字段策略仍照常生效。选择该边界是为了保留输入且避免为闲置字段维护高精度日期能力，而不是宣称任意文本都能被游戏接受。当前契约见 [Saves API](../saves-domain-api.md)。

TextDocMixin.read 现将解析参数转发给具体类型的 fromstring，通过 ParamSpec 保留该解析入口的静态参数提示和具体返回类型；不再把读取参数传给构造器。MainSave、ModBackupSave 和 EndersBlenderSave 的 strict，以及五种格式的 unknown 和 XML 冲突回调，只在本次解析中使用。Collab 的惰性 SessionInput 尚未解码 XML，继续保留其内层解析所需 unknown；这不是已解析存档的读取状态。SaveSlot.mod 的动态格式入口仍不承诺检查任意第三方子类的全部参数；Main／backup 槽位入口显式接受解析选项。空构造不解析文本，也不创建新的可游玩 Session。

## 对照与验证范围

本地参考源码固定为 CollabUtils2 `1064db99b8820195eac0e23460fba73ac09f923e`、CommunalHelper `ac542ed1ba2be0592347e4cba70b0297e2e7e933`、Ender's Blender `d8381238e6b10e37a9225ef2b7ecbe4f1048ea8c`。共同基类 `EverestModuleSaveData.Index` 标有 YamlIgnore，不属于文件字段。

回归覆盖新增 Collab 字段、全部格式的模型读写、未知字段拒绝／告警／过滤／升级、Mod 原文载荷、别名隔离、无模板解析构造、无片段重解析输出、转移与应用读取链路。真实游戏目录中只读检查 47 个文件：MainSave 10、ModBackupSave 9、CollabUtils2Save 9、EndersBlenderSave 10、CommunalHelperSave 9，均无未知字段错误；各自在内存中规范化输出、重读后再次输出均稳定。未写回真实存档或修改数据库，这不是游戏加载验收或性能 benchmark。

最终集成为 2884 项通过、2 项跳过，耗时 35.73 秒；跳过项为 Windows 目录符号链接权限和未配置专用目标的在线表格测试。随后一处重复警告的局部修复由 9 项 Session 回归验证。Ruff 检查、格式检查及 Pyright 通过。此前集成曾发现 CLI 冒烟夹具不满足严格存档结构，已用正常存档工厂修正；另一次 UI 重试对话框时序断言失败在串行专项及最终全量均通过，未改动该无关工作流的实现。

三位互不共享初始结论的代理分别检查协议字段、API／测试、数值及失败边界。合并确认三项 P2：nil 节点早退绕过未知字段策略、Campaign.reset 遗漏未解析进度、YAML null 被错误转成普通字符串；均已修复并补回归。复查再发现非 nil 出生点的重复警告，已限定新增检查只在 nil 分支执行。另补齐 Session.level_id 的变化检测，移除 Map 中遗留的 Cassette 校验字段。功能实现、代码质量、代码组织和测试均经过检查，未把未执行的真实游戏加载列为已完成验证。
