# Collab 会话集合与输入模型的演进（已完成）

> 状态：已完成，当前设计仍有效。本文记录截至 2026-10-20、`106ba99` 的设计演进与判断依据，后续不作为接口维护文档；若核心结论被后续设计取代，再移入 archive。
> 当前接口以源码、测试及 [存档领域 API](../saves-domain-api.md) 为准；通用容器语义见 [KeyedDict](../keyed-dict.md)。

2026-10-20 后续调整：输入与领域对象分离的结论不变；已解析 Session 的 XML 快照及输出统一经过 lxml，不再直接回传原文或承诺逐坐标词法保真。字段变化基准仍用于主文件双会话联动，未求值 SessionInput 仍保留原始输入。下文关于原文直返的说明仅记录当时实现。

## 背景与最终结论

CollabUtils2 的 `SessionsPerLevel` 按 SID 保存会话 XML 字符串，每个 SID 只有一项，side 保存在 XML 内部。加载文件时，SID 已经可用，但 XML 还不必解析。浏览可能只查询其中少量会话；修改其他字段并保存时，也不应被未使用的损坏会话阻断。

这次演进从专用 `Sessions` 容器开始，经过将惰性解析并入 `Session`、尝试通用惰性包装等方案，最终收敛为 `KeyedDict[str, SessionInput]`。集合管理身份与成员，SessionInput 保存原始输入并按需产生 Session，Session 提供已经解析的领域字段。

这个结果不是仅凭“一个类职责太多”得出的。关键依据是实际调用只需要延迟解析不同会话，不需要逐个延迟解析同一会话的字段。分析实际使用，才能决定把惰性边界放在输入转为领域对象的位置。

## 1. 最初的手动封装

`31db0f5` 接入了可编辑 Collab 进度和专用 `Sessions` 集合。后续整理后，集合内部大致为：

```python
dict[str, Session | _SessionSource]
```

其中 `_SessionSource` 提供身份查询和可调用的完整解析入口。未解析项由 PendingSession 一类的对象持有；需要领域值时调用来源对象，再将字典内的值替换为 Session。

专用集合承担了以下职责：

- 管理 SID 唯一性、添加、替换、删除和清空。
- 对外提供 `get(sid, side)` 和 `(sid, side)` 下标、成员检查。
- 根据对象类型判断是否已解析，触发解析并缓存结果。
- 在遍历时将来源对象转换为 Session。
- 后期通过 `from_yaml()`、`to_yaml()` 直接协调 YAML 编解码和原文保留。

它能够实现惰性加载，但容器协议、求值策略和外部格式边界混在一起。普通遍历可能成为加载操作，查询也需要处理“输入对象还是领域对象”的状态分支。

底层实际只按 SID 存储一项，外部却采用 `(sid, side)` 访问。这个接口用于检查当前项的 side，并不意味着能同时保存同一 SID 的多个 side；将这种筛选藏在集合协议里，也让集合身份与查询条件不够清晰。

## 2. 引入通用容器后的职责转移

通用进度集合先收敛为 KeyedMap，之后在 `ed02f3e` 中重命名为 KeyedDict。`faee69f` 删除了专用 Sessions，改用按 SID 取键的通用集合：

```python
KeyedDict[str, Session]
```

为了在插入和取键时不解析 XML，Session 同时接收 YAML 中已知的 SID 与尚未解析的 XML：

```python
Session.from_xml(xml, sid=known_sid, lazy=True)
```

此时 Session 的 SID 可以先来自外部索引；真实 XML 身份和可编辑字段则通过 cached_property 延迟解析。原本由 Sessions、PendingSession 协作承担的状态，转移到了 Session 内部。

集合本身确实简化了，但这一步没有消除求值与输入保留的复杂度，只是改变了复杂度的归属。

### Session 暴露出的设计问题

**类型不能明确表达对象所处阶段。** 一个 Session 可能是已经解析的领域对象，也可能只是未经验证、甚至损坏的 XML。拿到 Session 不代表可以正常访问其字段。

**构造路径不一致。** 普通构造器初始化领域字段；惰性分支通过 `__new__()` 绕过构造器，保存输入和解析上下文，后续靠属性访问逐步补全状态。同一种类型需要维护两套初始化条件。

**身份与活动状态混淆。** 当时身份读取会受 `InArea` 影响，`InArea=false` 导致无身份结果，side 因而允许为 None。但“不在地图中”不等于“不知道 SID 和 side”。需要独立的 `in_level` 表达状态，而不是让状态改变身份属性的类型。

**缓存与修改检测相互影响。** 字段可能尚未访问、已经读取、直接赋值或改后恢复。实现既要避免读取被视作修改，又要避免后续解析覆盖已赋值字段，还要决定修改检测是否强制解析其他字段。后来甚至尝试过按字段单独校验和比较，进一步增加状态组合。

**求值策略掩盖了输入来源的差异。** 主存档已经有 XML 节点，Collab 则持有多份 XML 字符串。为了统一对象表示而让两者经过相同的惰性流程，会给主存档带来不必要的延后报错或序列化、重解析步骤。

因此，问题不是 KeyedDict 不适合会话，而是为了让 KeyedDict 从值取键，把“已知索引的未解析输入”伪装成了领域 Session。

## 3. 为什么可以分离：实际调用分析

拆分不能只从类型结构出发，需要先检查实际消费者在什么时候需要哪些数据。

| 实际调用位置 | 所需信息和行为 | 是否需要解析会话 |
| --- | --- | --- |
| `CollabUtils2Save._read_root()` | 从 YAML 获得 SID 和 XML 标量，建立索引。 | 不需要；SID 已知。 |
| 集合下标、`get()`、成员检查、遍历与浅复制 | 定位输入或操作成员。 | 不需要。 |
| `CollabUtils2Save._encode()` 保存未访问项 | 将原始 XML 写回 YAML，不解释其中未知内容。 | 不需要。 |
| `pist.saves.SaveProgress.is_in_progress()` | 先匹配主存档当前地图；必要时按 SID 查询一份 Collab 输入，检查 `in_level` 和 side。 | 只需要解析被查询的那份输入。 |
| `saves.operations.is_in_progress()` | 组合主存档当前状态和可选 Collab 保留会话。 | 同样只在需要时解析目标输入。 |
| 主存档与备份的 Session 字段解码（演进时为 `MainSave._decode()`、`ModsProgress.from_xml()`，当前为根模型的 Xml 字段绑定） | 从已经解析的主存档或备份节点中提取会话。 | 可以直接立即提取字段。 |
| `MainSave._encode_sessions()` | 比较领域值的修改并同步同身份旧／安全会话，分别保留扩展内容。 | 需要明确身份和已解析的领域值，不需要逐字段延迟。 |

这里要区分已有产品调用与公共编辑能力：Pist 当前的 Collab 消费主要是进行中查询；对 Collab 会话字段的编辑和写回是文件层已经提供并由回归测试覆盖的能力，不能把测试示例说成已经存在的 UI 工作流。

### 由调用分析得到的结论

第一，惰性加载的主要价值是避免无条件解析所有保留会话，而不是避免读取一份会话中的几个字段。当前没有独立昂贵字段的需求或性能证据，足以支持在单个 Session 内维持逐字段求值。

第二，未解析阶段的消费者只需要 SID 和原文；真正进入领域查询或编辑时，可以一次完成这份会话的解析。没有实际调用要求把未经验证的 XML 当作完整 Session 使用。

第三，容器不需要依赖已解析 Session 来获得键。SID 已经存在于 YAML 索引中，可以由输入对象稳定提供，从而既保留 KeyedDict，又不触发解析。

第四，输入与当前领域值的生命周期不同。原文是保真和重新解析的基准，Session 是可以编辑的当前状态；丢弃解析缓存不应该同时丢弃原始输入。

第五，主存档和 Collab 的求值策略不必强行统一。主存档已有节点，直接构造 Session；Collab 保留多份输入，只在访问某份输入的领域值时构造 Session。两条路径最终得到同一种明确的领域对象。

据此，合理的边界是“按整个会话延迟输入转换”，而不是“让 Session 的每个属性分别延迟求值”。这保留了需要的惰性收益，也缩小了状态空间。

这一结论仅针对当前用例。若未来某个字段确实需要独立读取大量外部数据，或完整解析存在显著成本，应重新分析消费者和性能证据，不能把本次结论推广成所有领域对象都不应惰性求值。

## 4. 中间方案及其收敛

确定分离方向后，曾在同一轮未提交调整中尝试多个方案。这些尝试最终合并为 `d645ced`，不是主线中依次保留的公共接口。

最初拆出 LazySession，由它持有 XML 并缓存完整 Session，不再向外逐个转发领域属性。这解决了逐字段求值的问题，但仍需要判断哪些状态属于一般惰性求值，哪些属于会话输入。

随后尝试通用 `Lazy[T]`，让 Collab 使用 `dict[str, Lazy[Session]]`，通过 partial 捕获 SID 和原文。求值缓存通用了，但 SID、原文和结果之间的领域关系仍然存在，文件层还需要额外维护原始输入映射，避免替换同 SID 的值时误写旧原文。

partial 中已经存在一组稳定的领域状态，因此将它显式表达为 SessionInput，比散落的闭包参数和映射更清楚。SessionInput 提供 SID 后，集合也可以恢复为 KeyedDict。

进一步检查后发现，SessionInput 自己已经拥有完整的求值输入，其 `session` 直接使用 cached_property 就足够。再组合一个 Lazy 只是多一层间接访问，最终删除了通用 Lazy 和额外的原始输入映射。

曾补充的 `from_session()` 同样被删除：没有实际消费者需要把现成领域 Session 包装回输入对象，配套测试本身不构成新增公共工厂的需求依据。

## 5. 最终结构与契约

```text
CollabUtils2Save.sessions
└── KeyedDict[str, SessionInput]
    └── SessionInput
        ├── sid：只读的已知索引
        ├── orig_xml：只读的原始 XML
        └── session：完整解析后缓存的 Session
```

使用方式如下：

```python
input = collab.sessions[sid]  # 只定位输入，不解析 XML
session = input.session  # 首次访问完整解析，核对 XML SID 与索引
session.deaths += 1  # 编辑领域字段

del input.session  # 丢弃解析缓存及其中的内存编辑
session = input.session  # 从 orig_xml 重新解析
```

KeyedDict 只负责通用成员管理。SessionInput 负责按需解析、身份核对和输入原文的写出协调，不提供 room、side 等领域属性的转发。

公开 `.session` 是无 setter 的 property；内部 `_session` 是 cached_property。不能给 `.session` 重新赋值，但可以修改取得的 Session 的领域字段。deleter 直接执行 `del self._session`：尚未求值、求值失败或重复删除时抛 AttributeError，不自动解析，也不静默忽略。清除缓存不撤销其他引用持有的旧 Session 对象上的编辑，只是不再把它作为这份输入的当前解析结果。

未访问项及解析失败项通过 `to_xml()` 原样返回 `orig_xml`，不会因保存其他字段而强制解析；已有解析结果则编码当前 Session。失败不会成为成功缓存。Session 与 XML 索引不一致时拒绝解释为对应会话。

Session 是已经解析的可编辑领域值，不再具有 lazy 参数、逐字段解析或“side 可能因非活动状态而为 None”的分支。`in_level` 独立映射 InArea，身份保持 SID 与 LevelSide；nil 输入由文件层表示为没有会话。

这里的领域对象并非完全丢弃原始格式：Session 仍保留不可变 XML 快照及初始字段值，以保全未知内容、命名空间和未变化字段的词法形式。区别在于快照只服务于输出保真和差异比较，不再作为领域字段延迟求值的来源。读取、同值赋值和修改后恢复原值均不计作修改。

## 6. 校验边界的最后调整

拆分后又发现 `from_xml()` 已经用 SessionFields、RespawnFields 解析校验，调用构造器时却重复执行相同字段校验。`106ba99` 将边界明确为：

- `from_xml()` 校验 XML 结构并转换已建模字段；文本立即解析，已有节点直接提取字段。
- `__init__()` 接收精确类型，检查 SID 非空、side 为 LevelSide 等身份不变量，之后直接保存字段。
- `to_xml()` 检查输出格式、数值范围和可表示性，涵盖构造后可能发生的编辑。

因此，直接构造 Session 不承诺所有当前字段都已经满足存档写出范围；XML 解析成功则表示当次输入的已建模字段经过边界校验。对象后续仍可编辑，写出校验不能省略。消除的是同一输入转换链上的重复校验，不是不同边界各自必要的检查。

## 7. 关键提交与回归依据

| 提交 | 作用 |
| --- | --- |
| `31db0f5` | 接入 Collab 可编辑进度与专用惰性 Sessions 集合。 |
| `faee69f` | 删除专用集合，将惰性解析收敛到 Session，改用 KeyedMap。 |
| `ed02f3e` | 将通用容器统一重命名为 KeyedDict。 |
| `d645ced` | 分离 Session 与 SessionInput，明确活动状态、求值策略、缓存删除及 XML 保真边界。 |
| `106ba99` | 移除 Session 构造器的重复字段校验。 |

回归重点包括：建立索引、遍历和浅复制不解析 XML；仅访问目标输入时解析并缓存；损坏或身份不匹配的输入按需报错且保留原文；主存档立即校验会话；InArea 与身份独立；领域编辑和恢复原值的差异判断；公开属性拒绝赋值；删除缓存后重新解析及缺失缓存的异常；未知 XML、命名空间、注释、处理指令和 CDATA 的写回保真。

可复查的实现与测试：

- [Collab 文件协调](../../berries/src/berries/game/saves/files/collab_utils2.py)、[SessionInput](../../berries/src/berries/game/saves/session_input.py)、[Session](../../berries/src/berries/game/saves/progress/session.py)。
- [主存档会话协调](../../berries/src/berries/game/saves/files/main.py)、[公共进行中查询](../../berries/src/berries/game/saves/operations/edit.py)、[Pist 进行中查询](../../pist/src/pist/saves.py)。
- [Collab 领域回归](../../berries/tests/test_collab_save_domain.py)、[对象转换回归](../../berries/tests/test_save_object_codecs.py)、[文件保真回归](../../berries/tests/test_save_files.py)、[Pist 读取回归](../../pist/tests/test_save_reader.py)。

## 经验总结

引入通用容器时，不仅要检查删除了多少集合代码，还要追踪状态和职责被搬到了哪里。为了满足容器的取键要求而削弱领域对象的含义，并不是成功的简化。

是否应该分离输入和领域对象，需要从实际消费者的访问粒度、求值成本、失败时机和生命周期推导。先确认哪些调用仅需原始输入、哪些调用真正需要领域值，再选择惰性边界；不能仅凭“职责混杂”直接决定拆分，也不能因一个通用 Lazy 能写出来就保留它。

最终保留下来的不是更多抽象层，而是三个有实际依据的边界：容器管理成员，输入协调惰性转换与原文，领域对象提供已解析字段和编辑。对没有真实使用需求的额外工厂和包装及时删除。
