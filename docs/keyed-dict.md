# KeyedDict：具有派生 key 不变量的映射

`berries.containers.KeyedDict[K, T]` 是具体的 `Mapping[K, T]` 实现，不继承 `MutableMapping`。每个存储项必须满足 `stored_key == key(value)`；key 可散列，value 可以不可散列，存储期间不得改变其派生 key。容器按 key 首次插入顺序遍历，不提供集合运算符。

## 实例级取键规则与写入

```python
from operator import itemgetter

from berries.containers import KeyedDict

values: KeyedDict[str, tuple[str, int]] = KeyedDict([('a', 1), ('b', 2)], key=itemgetter(0))
values.add(('a', 3))
assert list(values) == ['a', 'b']
assert values['a'] == ('a', 3)
assert values.adddefault(('a', 4)) == ('a', 3)
assert values.adddefault(('c', 5)) == ('c', 5)
```

取键函数通过构造器的必选 keyword 参数 `key=` 配置，只读属性 `key` 可查询该函数。它是实例的写入策略，不是类型身份。不同类型和不同取键规则的 KeyedDict 可以互相更新，按目标规则接纳 values。

| 接口 | 语义 |
| --- | --- |
| `add(value)` | 派生 key 后写入，已有 key 直接覆盖，位置不变 |
| `update(*others)` | 按顺序消费零个或多个 value iterable／KeyedDict，覆盖语义与 add 一致 |
| `adddefault(value) -> T` | 已有 key 时返回原值；否则插入并返回传入值，类似 dict.setdefault |
| `replace(value)` | 要求派生 key 已存在，否则报 KeyError；只替换不新增 |
| `del mapping[key]`、`pop(key)` | 按 key 移除，缺失时报 KeyError |
| `pop(key, default)` | 缺失时返回 default，允许显式 None |
| `popitem()` | 移除并返回最后插入的键值对，空映射时报 KeyError |
| `clear()` | 清空成员关系，不重置 value 内容 |
| `copy()`、`copy.copy()` | 构造同类型的新映射，共享 values 和取键函数，内部 dict 独立 |
| `reversed(mapping)` | 按 key 的逆插入顺序迭代，覆盖不移动位置，删除重加会移动位置 |

`__len__`、`__iter__`、`__getitem__` 提供核心映射协议；`get()` 和 `__contains__` 直接查询内部 dict。`__eq__` 对 KeyedDict 和 dict 直接比较底层字典，不生成临时映射；其他 Mapping 只将对方的 items 转为 dict，非 Mapping 返回 NotImplemented，以保留反向比较。相等比较键值对而非只比较 key，与插入顺序及取键函数无关，value 可以不可散列；容器本身不可散列。`__ne__` 沿用 Python 对 `__eq__` 的反转与反向比较协议。`keys()`、`items()`、`values()` 覆写为返回内部 dict 的原生视图，支持原生视图操作和反向迭代，并持续反映添加、替换、删除和清空。普通和反向遍历均返回 key，对象遍历使用 `.values()`；删除后重加的 key 排到末尾。没有任意 `mapping[key] = value`、`remove(value)`、`discard(value)`、集合运算或自定义冲突策略。

### 查询转发的性能依据

`Mapping` 基类通过通用映射协议实现 get 和相等比较；KeyedDict 持有实际 dict，可以直接转发这些查询。2026-10-20 在 Windows、CPython 3.14.7 上，以相同预建数据比较继承的 Mapping 实现和直接转发实现，timeit 重复 7 次取中位数，不启用 profiler。1000 项相等 KeyedDict 比较为 48.001 → 8.111 µs，与 dict 比较为 47.391 → 8.123 µs；get 未命中为 0.200 → 0.058 µs，命中为 0.081 → 0.065 µs。比较各重复 2000 次，get 同样重复 2000 次；这些是内存微基准，不代表整个应用获得相同比例的加速。

## 更新、复制与约束边界

`update()` 将 KeyedDict 解释为 value 来源，不消费其 key iterable；普通 Mapping 不作特殊适配，调用方应显式传 `.values()`。`other is self` 时直接跳过当前输入，不重新调用子类的 add，也不阻断后续输入。取键函数为同一对象时，已有 key 可复用，普通 KeyedDict 直接调用内部 dict.update，空与非空目标均可走此路径；规则不同则重新计算目标 key。函数身份只用于优化，不用于拒绝输入。

构造器和 `update()` 接受 `KeyedDict[U, T]`，源 key 类型 U 独立于目标 key 类型 K；一次 update 也可以混用不同源 key 类型。源有多个不同 key 的 value 在目标取键后发生冲突时，按源迭代顺序后写覆盖，已有目标 key 的位置不变。源映射不被修改。

通用 add 直接执行 dict 赋值，adddefault 直接使用 dict.setdefault。子类通过重写公开写入方法维护自己的约束；update 只在目标未重写 add 时使用 dict 快路径，否则逐项调用目标的 add。adddefault 和 replace 各有独立语义，子类同步维护相应校验。adddefault 命中已有 key 时只返回原值，不写入。批量更新不承诺回滚，失败前已完成的项保留，空输入不阻断后续输入。

通用父类的浅复制调用 `type(self)(self, key=self.key)`，不负责保留未知子类状态；子类有额外构造参数或状态时自行实现 `__copy__`。常规深复制交给 `copy.deepcopy()`。

## 存档接入

`ModSaveData.campaigns`、`recycled_campaigns` 使用 CampaignMembers，`CampaignProgress.levels` 使用 LevelMembers。这些领域 Mapping 组合 KeyedDict 保存成员关系，负责归属校验、分区索引协调和修改失败时的原子性；它们不是普通 KeyedDict 的别名或子类。成员对象保持引用语义，集合自身的存储和订阅按领域契约管理。

通用 add 的同 key 覆盖不等于取消输入审查：文件解析的重复记录由 strict／on_campaign_conflict／on_level_conflict 契约处理。宽松读取可以保留唯一但错放的输入供修复，新增／替换必须检查新成员归属，写出要求修复所有错误关系。具体契约见 [存档容器](saves-containers.md)。

`CollabUtils2Save.sessions` 使用 `KeyedDict[str, SessionInput]`，按输入中已知的 SID 取键，不触发 XML 解析。SessionInput 的 SID 和原文只读，`.session` 只读，通过内部 cached_property 缓存；首次访问时解析并核对 SID，`orig_xml` 保存未修改的原文。索引、查找、遍历和集合浅复制均不解析 XML；需要领域值时访问 `.session`。添加原始输入使用 `sessions.add(SessionInput(sid, orig_xml))`。未求值的原文和已求值会话的写出都由 SessionInput 自身协调。输入与领域对象分离的背景见 [Collab 会话集合与输入模型的演进](finished/collab-sessions-evolution.md)。
