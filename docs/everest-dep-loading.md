# Everest 依赖加载语义与接口设计依据

## 范围

本次核对基于 Everest 源码提交 `790e3a54112c5a18f12e51b338ddeac2c6bfcd8e`，结论来自源码阅读与流程推导，未运行游戏验证。对应接口草案位于 [deps.pyi](api_drafts/deps.pyi)，尚未实现为公共 API。

Berries 只按扫描输入、启用范围和引擎版本静态推演依赖加载，假定通过依赖检查的模块能正常加载。不执行 DLL，不模拟模块初始化失败、动态注册或卸载，也不处理 DLL 失败后资源已经扫描的运行时状态。

## 物理包与 metadata

启用和禁用作用于目录或 ZIP；依赖条件和同名身份作用于每条 metadata。同一包中的各条 metadata 按 manifest 顺序分别调用 `LoadModDelayed()`，因此允许部分加载，不能将包作为依赖加载的原子节点。

包内条目共用一次性资源扫描回调，首个通过加载检查的条目触发整个包的资源扫描；资源的 metadata 标签仍取 manifest 首条，不一定是触发扫描的条目。依赖判断不能因此只读取首条 metadata，资源顺序也不能简单使用包发现顺序。

源码：[ZIP 条目与共用回调](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Loader.cs#L312-L340)、[目录包对应逻辑](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Loader.cs#L391-L422)。

## 顺序与同名候选

Everest 分别排序 ZIP 路径和目录路径，先遍历 ZIP，再遍历目录。初次尝试先检查是否已有同名模块，再检查必需和可选依赖；未满足则加入延迟队列。模块注册后触发队列重试，每处理掉一个就从头重新检查。

同名候选不是选择最高版本，而是先注册的身份占位。较早发现的高版本如果等待依赖，可能输给较晚发现、能够立即加载的低版本。名称精确匹配，不按大小写折叠；`EverestCore` 有显式核心别名处理。manifest 的 `PostParse()` 还会处理旧 `API` 别名，并在没有核心必需依赖时补入当前核心 metadata；实现必须核对现有读取边界是否已完成这些步骤，不改写原始依赖声明图来冒充 manifest 输入事实。

延迟队列的同名检查发生在 `DependenciesLoaded()` 通过之后。因此，一个始终缺少必需依赖的延迟项，即使最终出现已加载的同名身份，也不能仅凭最终名称表将其改判成加载器已经跳过的重复项。

源码：[发现顺序与最终放宽阶段](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Loader.cs#L179-L206)、[初次尝试](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Loader.cs#L441-L474)、[延迟重试](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.cs#L680-L734)、[manifest 后处理](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Module/EverestModuleMetadata.cs#L101-L125)。

## 版本与可选依赖

现有 `Version.is_compatible_with()` 已采用 Everest 的版本规则，本次调研确认其依据，不新增另一套比较：已安装版本为 `0.0.*` 时总是满足；其余情况要求主版本相同，并按 minor、build、revision 判断不低于要求。版本字符串的 `-suffix` 不参与数值比较。

初始阶段会等待尚未加载的可选依赖；发现全部包后，允许同名模块未加载的可选依赖缺失，但已有同名模块且版本不兼容时仍阻塞。这意味着“存在禁用候选”不自动构成可选依赖问题。

延迟处理还会递归检查可选依赖是否在等待队列中具有可加载的依赖链，尽量让它先加载。该辅助查找使用同名的首个延迟项，而不是选最高版本或遍历所有候选；实现优化不能悄悄改变这一选择。若一轮没有进展且出现这类等待，则关闭额外的顺序等待再试，但不会跳过 `DependenciesLoaded()` 的必需依赖或已加载版本兼容检查。

源码：[版本兼容及依赖查询](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.Loader.cs#L929-L1013)、[版本字符串解析](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Module/EverestModuleMetadata.cs#L48-L59)、[递归等待与打破等待](https://github.com/EverestAPI/Everest/blob/790e3a54112c5a18f12e51b338ddeac2c6bfcd8e/Celeste.Mod.mm/Mod/Everest/Everest.cs#L693-L762)。

## 循环不能脱离边类型和候选讨论

| 声明与前提 | 按源码推导的结果 |
| --- | --- |
| A、B 是唯一可用身份，彼此必需依赖，且没有预先加载的兼容身份 | 两者始终无法通过必需依赖检查，无法自行加载。 |
| A、B 彼此可选依赖，版本相互兼容 | 最终放宽缺失及额外顺序等待后，可以按延迟队列顺序加载。 |
| 依次发现 A₁、B、A₂，A₁ 必需依赖 B，B 必需依赖 A，A₂ 无额外依赖且满足 B 的版本要求 | A₂ 先加载，随后 B 加载，A₁ 最终因同名身份已加载而跳过；声明图虽含 A₁ 与 B 的环，最终加载关系不成环。 |

不提供公开的 `cycles()` 查询：强连通分量不等于具体循环路径，也不直接等于加载失败，当前没有独立调用需求。必需依赖构成且没有替代提供者的封闭环可以证明阻塞，但不能将所有候选分量或可选依赖环一律判为不可加载。

实现优先复现有序队列语义。反向依赖索引可用于减少无关重查，但不能改变等待项重新获得机会的先后顺序；是否采用优化依据测量决定，不将拓扑排序或强连通分量当作加载过程的替代。

## 草案中的结果与诊断

- `DepDeclaration` 保存物理包、声明所属 metadata、依赖要求和可选性；它不是已连接到某个提供者的图边。`dependencies(mod)` 和 `dependents(name)` 均返回声明，`recursive` 控制递归。
- 声明查询保留禁用包和重复候选。正向递归展开全部同名候选 metadata 的声明；反向递归继续查询声明者的 metadata 名称，两者均不扩展同包无关 metadata。按扫描及声明顺序深度优先遍历，防止循环，每处声明只返回一次；不同物理包中的相等声明不合并。它们都不表示最终的提供者绑定或安全启停方案。
- `loaded` 保存静态推演加载的 metadata 到物理包的映射；引擎身份只参与判断，不伪造 `InstalledMod`。
- `content_order` 单独保存首次触发资源加载的物理包顺序；`disabled_mods` 单独保存未启用包。
- `LoadIssue` 使用 `UnsatisfiedDeps` 和 `DuplicateName` 子类；单条 metadata 未加载不意味着整个包未加载。
- `DepIssue` 使用 `MissingDep`、`DisabledDep`、`IncompatibleDep` 和 `UnloadedDep` 子类。先判断已加载身份是否不兼容；没有已加载身份时，依次区分无候选、存在已启用兼容候选、仅禁用包中有兼容候选、所有候选不兼容。候选自身的阻塞原因从加载问题查询，不递归包装问题树。
- 多个原因可以同时存在于候选集合，但每条未满足声明只选择一个主问题类型，完整候选仍保留用于展示。允许缺失的可选声明不生成问题。

## 后续实现的验证重点

使用合成包覆盖同包部分加载、重复候选等待后被抢占、延迟项依赖未满足时的诊断、核心身份与别名、版本兼容边界、可选依赖缺失和不兼容、可选依赖相互等待以及重复候选打破声明环。验证资源顺序独立于发现顺序，且一个包只出现一次。无需为范围外的 DLL 加载失败增加模拟、模型或测试。
