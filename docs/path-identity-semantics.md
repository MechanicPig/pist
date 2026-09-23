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
| Everest 资源路径、ZIP 条目 | 使用 `/` 和 `PurePosixPath`；身份精确且区分大小写。 | `Everest.Content.cs`。 |
| SID、Collab ID、LevelSet、大厅关联 | 是标识字符串；精确且区分大小写。 | `CollabUtils2/LobbyHelper.cs`。 |
| Mod metadata 与 dependency 名称 | 是 Everest 身份；精确且区分大小写。 | `Everest.Loader.cs`。 |
| `dialog_key` | 是由 SID 等文本派生的查询键，不是 SID 或资源路径。 | Everest `Extensions.DialogKeyify`。 |
| Dialog 查询 | 游戏与 Pist 均大小写不敏感。 | Celeste `Language.cs`；Everest `patch_Dialog.cs`。 |
| UI 排序、显示筛选 | 可用 `casefold()`；不得复用为身份匹配。 | Pist 的展示策略。 |

### 本地文件系统路径

- 用 `Path` 定位、拼接、打开和遍历；不要手工替换分隔符。
- 不以 `casefold()` 推断物理路径相同；需要比较两个已存在文件时使用 `Path.samefile()`。

### Everest 虚拟资源路径

- Everest 将 `\\` 规范为 `/`，随后以默认 `Dictionary<string, ModAsset>` 查表。
- 因而可先规范分隔符，再使用 `PurePosixPath` 做结构拆分；不得折叠大小写。
- 证据：`Everest.Content.cs` 的 `Crawl`（约 390–396 行）、`Map`（约 65 行）和 `TryGet`（约 510、558 行）。

### SID 与 Collab 标识

- SID、Collab ID、LevelSet 不是本地路径；`PurePosixPath` 只可用于其结构拆分。
- CollabUtils2 以默认 `StartsWith` 与 `HashSet<string>` 匹配，故前缀、目录和相等比较均区分大小写。
- 证据：`CollabUtils2/LobbyHelper.cs` 的 `GetLobbyLevelSet`、`IsCollabLevelSet`、`GetLobbyForLevelSet`（约 61–83、88 行）。

### Mod metadata 与 dependency 名称

- metadata 名称与 dependency 名称是 Everest 加载器的身份字符串，不是 Dialog key。
- 依赖查找以普通字符串相等比较；只有精确的 `EverestCore` 名称会被当作核心 Mod 别名处理。
- 证据：`Everest.Loader.cs` 的 `TryGetDependency`（约 961–973 行）。

### Dialog key

- `Dialog.Get` 与 `Dialog.Has` 会先运行 `DialogKeyify`：将 `/`、`-`、`+`、空格替换为 `_`，然后查询。
- 游戏的 `Language.Dialog` 使用 `StringComparer.OrdinalIgnoreCase`；Pist 的 `CaseFoldDict` 与此保持一致。因此 Dialog key 的查询大小写不敏感。
- 路径到 `dialog_key` 的结构转换仍先遵循虚拟地图路径规则；Dialog 查询不敏感不能反推 SID 或资源路径不敏感。
- 证据：`Everest/Extensions.cs` 的 `DialogKeyify`（约 130 行），`Everest/Patches/Dialog.cs` 的 `Get` / `Has`（约 176–213 行），以及 Celeste `Language.cs` 的 `Language.Dialog` 初始化（约 45 行）。

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
