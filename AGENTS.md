# Pist 协作指南

## 项目目标与边界

Pist 用于扫描本地 Celeste 与已启用 Mod，辅助记录地图初见数据、校正路线和可收集物统计，并将确认后的记录同步到腾讯智能表格。

核心能力是：本地游戏/存档/地图数据读取、可审计的实体规则、路线与草稿的本地保存，以及表格写入。规则与统计应保守：无法确认的实体或属性不得自动计入结果。

## 工程约束

- 使用 Python 3.14 及以上版本；依赖、虚拟环境和锁文件统一由 `uv` 管理。
- 公共包 Berries 位于 `berries/src/berries/`，个人应用 Pist 位于 `pist/src/pist/`，两包测试分别位于 `berries/tests/` 与 `pist/tests/`，架构验证位于 `tests/test_arch.py`，共享测试工厂位于 `tests/test_support/`，扫描测试合成数据位于 `berries/tests/data/`；公共共享数据位于 `berries/src/berries/data/`，活动应用数据统一位于 `pist/src/pist/data/`，归档资源随所属模块放入 `pist/src/pist/archive/`，用户可复用的技能位于 `skills/`，设计与分析文档位于 `docs/`。
- Pist 运行时本地数据位于名为 `.pist` 的目录：源码工作区使用仓库根目录，安装包使用 `platformdirs` 选择的平台用户数据目录；`PIST_DATA_DIR` 可覆盖其完整路径。Berries 单独安装时使用自己的平台用户数据目录和 `BERRIES_DATA_DIR`，在本仓库源码 workspace 中则与 Pist 共用根目录 `.pist/`。开发用检查报告、临时脚本、参考仓库及构建产物仍放在仓库内被忽略的 `.pist/`；其中 `local-data.sqlite3`、扫描报告和构建产物不得手工修改。
- 仅面向内部开发的本地数据结构变更时，先确认没有进程占用对应的 `.pist/*.sqlite3`，再直接迁移当前数据库；不要在源码中保留一次性迁移或旧字段兼容逻辑。
- 变更 SQLite 的表、字段或其语义前，先向用户列出 schema diff 与当前本地数据迁移方案供审查；获确认后再实施，并删除旧 schema，不保留运行时兼容层。
- 新增依赖前先确认标准库或既有依赖无法合理解决问题；通过 `uv add` 或 `uv add --group dev` 更新 `pyproject.toml` 与 `uv.lock`，不要手改锁文件。
- 项目使用 Ruff 作为代码格式化与静态检查工具，行宽为 100，使用单引号，并启用 ANN 检查源码与测试的函数类型注解；使用 Pyright 作为类型检查工具。
- 模块和包名按职责语义命名：集合、规则或记录用复数，单一模型、协议或过程用单数；不为形式上的单复数一致性重命名。
- 迭代索引变量使用 `i`、`j`、`k` 等单字母，未使用的索引用 `_`；元素变量与有业务含义的编号保留语义名称。
- 当同一概念同时存在“根据孤立输入得到的候选结果”和“结合完整集合的加载、覆盖或组合关系得到的最终结果”时，候选结果使用 `local_` 前缀，最终结果不加前缀。这里的 `local` 表示尚未全局整合，不表示宿主文件系统；只有确实存在这两个阶段时才使用。Manifest 声明、扫描路径、`InstalledMod.map_files`、依赖列表等直接输入事实不加 `local_`；此类名称按声明或结果语义使用 `required`、`optional`、`loaded`、`resolved`、`satisfied` 等限定词。
- 涉及路径、SID、Dialog key、扩展名等外部标识的比较或派生时，先查 `docs/path-identity-semantics.md`；按实现与平台证据选择 `Path`、`PurePosixPath` 或字符串匹配，`casefold()` 只能用于明确非身份语义。
- 外部输入在边界处用 Pydantic 校验，内部代码保持精确类型，避免无必要的 `Any` 或 `object`。类型注解应表达接口与数据的意图：仅需要可挂载组件时标为 `Widget`，确实保证或区分具体类型时保留具体类型；不要为了满足 typing 而枚举偶然的实现分支。
- 容器初始化需要显式类型时，将类型写在变量或属性注解中：内置空容器使用 `values: list[T] = []`、`groups: dict[K, V] = {}`、`items: tuple[T, ...] = ()`；没有字面量的容器使用普通构造器，如 `seen: set[T] = set()`、`members: KeyedDict[K, T] = KeyedDict(key=...)`。不要仅为静态类型提示使用 `list[T]()`、`dict[K, V]()` 等运行时泛型构造；非空初始化能可靠推导出所需类型时省略冗余注解，已声明属性的后续赋值不重复标注。确实消费运行时类型参数的模型或自定义类型按其契约构造，不据此机械移除类型实参。
- 明确为副作用调用、无需返回值的接口（如容器修改、文件写入或定位、SQL 执行、UI 操作）直接调用；Pyright 的 `reportUnusedCallResult` 仅在 `berries/src` 和 `pist/src` 启用，源码中若触发此警告，在该行使用 `# pyright: ignore[reportUnusedCallResult]`，不以 `_ =` 伪装消费返回值。测试和示例不启用该条警告，不添加相应屏蔽注释，其他类型检查照常保留。纯查询、解析或校验不据此一律屏蔽；Pydantic 等明确用于校验且无需保留转换结果的调用，可用 `_ =` 表达丢弃结果。
- Pydantic 模型放在拥有其外部协议、配置或持久化语义的模块；`types.py` 只保留不属于特定领域的共享值类型，不作为模型汇集处。
- 新增或修改的文本文件统一使用 UTF-8。

## 文档编写约定

- `docs/` 根目录保留当前核心指南、约束、API 说明、TODO 与仍在推进的设计；已完成且核心结论仍有效的演进、选型和验证总结放入 `docs/finished/`，已被后续设计取代的历史文档放入 `docs/archive/`。不能仅因完成就归档为过时，也不能将仍未完成的设计标为完成；迁移文档时同步更新状态说明、索引及引用链接。
- 中文 Markdown 的每个自然段保持一行，使用编辑器软换行阅读，不按固定行宽或句子人为拆行，避免渲染时在中文之间引入空格。Python 的行宽限制不适用于 Markdown。
- 用空行分隔自然段；列表项中的连续正文同样保持一行。标题、列表层级、表格、代码块等保留各自的结构；仅在确实需要显示换行时使用明确的 Markdown 换行语法。
- 合并既有段落时保留原有措辞，以及中英文、行内代码等内容之间必要的空格，不将换行一律替换为空格。

## 常用命令

```sh
# 安装锁定的开发环境
uv sync --all-packages --frozen

# 运行 CLI
uv run --package pist pist --help

# 完整验证
uv run --all-packages pytest -n auto --dist worksteal --basetemp .pist/tests/pytest
uv run --all-packages ruff check berries pist tests examples
uv run --all-packages ruff format berries pist tests examples
uv run --all-packages pyright

# 单独验证公共包（包含根目录开发依赖）
uv run --package berries --group dev pytest berries/tests --basetemp .pist/tests/pytest

# 验证打包产物
uv build --all-packages --out-dir .pist/build
```

测试运行产生的临时文件、隔离环境与验证产物统一放在 `.pist/tests/` 下，不改变测试源码的位置。通常复用 `.pist/tests/pytest` 作为 `--basetemp`，不为每次运行另建目录。全量测试使用 `-n auto` 自动选择 worker 数；专项测试默认串行，避免进程启动与重复收集的开销：

```sh
uv run --all-packages pytest --basetemp .pist/tests/pytest
```

同一工作树同一时间只有一个会话执行测试；多会话通过独立工作树并发，不为此另设测试目录协调机制。测试内并行由单次 pytest 的 xdist 调度，各 worker 自动使用独立子目录。高权限测试始终使用独立的 `.pist/tests/pytest-admin`，不得复用普通测试目录，避免 Windows 权限变化影响后续普通测试。专用临时测试目录及产物无保留需求时应及时清理；高权限目录由对应高权限进程清理，不通过修改普通测试目录权限或不断换目录绕过问题。排查顺序或并发问题时用 `-n 0` 串行复现。执行前确保 `.pist/tests/` 存在；测试耗时对照与资源隔离约束见 [测试维护](docs/testing.md)。

临时诊断、检查或全量扫描脚本放入 `.pist/scripts/`。需要拉取公开参考代码库时，统一浅克隆到 `.pist/references/`；该目录仅用于研究和对照，不纳入项目包或测试输入。

## 修改与验证规则

- 日常修改领域逻辑、解析、持久化或规则生成后，按调用链运行对应测试、Ruff 与 Pyright；行为变更应补充相关回归，不因修改行为就自动运行全量。跨领域协议或持久化语义变更且影响面无法界定、阶段集成及发布前，运行完整测试、Ruff 与 Pyright。
- 纯重命名、模块或方法迁移等机械调整，在确认输入输出、异常、副作用与执行时序不变，且静态引用、动态调用和测试替换点均已核对后，运行相关测试、Ruff 与 Pyright 即可；不因涉及多个模块、层级或包就额外运行全量测试。无法确认上述条件时，按影响面不明确处理。
- 修改 Textual UI 或 TCSS 后，至少运行相应 UI 测试；涉及布局、焦点或浏览器预览时还应人工验证关键交互。
- Textual 的静态样式统一放在 `pist/src/pist/ui/styles/*.tcss`，通过应用的 `CSS_PATH` 加载；不要在 Python 类中定义内联 `CSS`。地图预览的浏览器样式保留在其 `static/*.css` 中。
- 修改 `README.md` 前须获得用户明确允许；主 README 是项目门面，保留必要的功能介绍与上手步骤，不堆叠实现、测试或交互细节。修改 CLI 参数、命令语义或用户可见工作流后，若需更新 README，先向用户说明并请求允许；若改变了技能涵盖的工作流，也同步更新对应 `skills/*/SKILL.md`。
- 每次新增一项完整能力后，主动检查它是否让现有模块混入新的稳定职责、让 `__init__.py` 承担实现细节，或使浏览器协议的两端失配；满足任一情况时，应在同一任务中拆分，或在交付时说明暂不拆分的原因与后续边界。
- 不手改由 `uv`、测试、构建、扫描命令或 TUI 生成的文件。共享规则和模板数据是版本控制下的源数据，修改时应通过审查流程或配套测试验证。
- 内部开发阶段的本地数据路径或 schema 变更应直接迁移现有开发数据，并删除旧实现；除非用户明确要求兼容，不保留旧路径探测、双读或长期回退分支。
- 只修改与当前任务相关的文件；工作区已有的无关改动属于用户，不覆盖、不重置。
- 主动清理文件或目录时统一移入系统回收站，不使用永久删除命令；回收站由用户自行清空。无法移入系统回收站时，移动到仓库内 `.pist/recycled/` 下的独立位置保留，不覆盖已有内容，由用户最终清理。操作前核实准确路径，不跟随符号链接扩大范围；操作后说明移走的内容及其去向。此约定针对文件清理，不改变正常源码编辑或 pytest 等工具自身的临时文件生命周期。

## 架构原则

- `berries.game` 承载游戏文件、存档、Mod 与地图的读取，以及存档文件的内存编辑与编解码；存档通过一次性 `read()` 加载、显式 `write()` 写出，不持有文件会话，不自动保存，不提供备份 API，备份由使用方自行通过 `shutil` 等文件工具处理。`berries.entities` 与 `berries.map_entrances` 承载可复用的地图解释规则。Pist 的个人实体统计位于 `pist.entity_stats`，审计位于 `pist.entities.audit`；公共模块及数据模型均不依赖 Textual、浏览器页面或其他 UI，UI 只协调用户交互与领域服务。
- `ui` 按用户可见工作流组织子包；仅跨工作流复用的 Textual 基类或控件保留在 `ui` 根部，避免将不同界面的实现平铺在一起。
- 包的 `__init__.py` 只提供稳定的公共入口；具体实现放在职责明确的子模块中。内部调用优先导入实现所属模块，而不是依赖包入口的偶然重导出。
- 同包模块及当前包下的嵌套子包使用单层相对导入（`from .module import ...`、`from .subpackage.module import ...`）；访问父包、兄弟包或其他顶层包时使用绝对导入，不使用 `..`、`...` 向上相对导入。`__init__.py` 的公共重导出同样遵循此规则。
- 实体规则以明确的实体 ID 和属性条件为准，不能仅根据名称、前缀或显示文本推断语义。保留“属性未写入”与“显式写入默认值”的差异。
- 静态 Lua 分析与求值解耦：解析尽量兼容 Loenn 可接受的语法，求值仅接受可证明的静态结果，未知值不得使整个文件或表失效。
- 网络访问、凭证、游戏文件读取与 SQLite 本地数据均属于边界能力；保持可替换、可诊断，且不泄露凭证。
- 共享规则库可随包发布；用户本地数据和本地增补规则必须与包内数据分离，更新包时不得覆盖用户数据。
- 地图预览是随进程启动、仅监听 loopback 的单功能 Web 界面；Python 服务端与静态资源保持在 `map_preview/` 内，不因其存在而引入独立前端工程。变更浏览器 JSON 状态或操作请求时，须同步更新前后两端并覆盖协议测试。
- `archive` 保存已验证且有回归测试、但当前不参与产品工作流的休眠子系统；活动代码不得依赖它。若要恢复使用，先重新评估边界并迁回对应活动领域，而不是直接重新建立跨层依赖。

## 特殊注意事项

- Mod 可能使用非 UTF-8 的文本；解码失败应在界面或报告中形成可见警告，而不是让整个扫描崩溃。
- 地图和 Mod 文件可能很大；避免在浏览列表或启动阶段无条件解析全部 `.bin`，按需读取并缓存安全的结果。
- 进行 Python 性能分析时，优先临时使用基于 Python 3.12+ `sys.monitoring` 的低开销 profiler（当前可用 `uv run --with tprof tprof`）；不要把 `cProfile` 的插桩耗时作为性能结论。
- 使用完 ZIP、游戏文件或临时 HTTP 服务后及时关闭，避免占用 Loenn 或游戏正在使用的文件。
- 提交信息保持 Conventional Commits 格式，但说明文本使用中文，例如：`fix: 修复草稿字段回读`。
- 每次修改前先检查工作区；若本次主题与上一次尚未提交的修改不同，先验证并提交上一主题，再开始本次修改。修改完成并通过适用的验证后，默认主动提交当前主题的变更，无需等待用户再次要求；不提交未验证的中间状态，未能提交时说明原因。用户已有的无关改动不得擅自纳入提交。
- 若当前修改与最近一次提交属于同一主题，且该提交尚未推送、合并不会混入其他主题或用户无关改动，可以使用 `git commit --amend` 合并，并按最终变更更新提交说明；否则创建新提交。
- `main` 只接收面向发布的压缩提交，`dev` 保留细粒度历史。除上述同主题的本地最新提交合并外，未经用户明确要求，不执行推送、强制更新、重写分支或破坏性 Git 操作。

## Pyright 相关

### Sentinel

项目使用 Pyright 的实验性 Sentinel 支持。以下问题在 1.1.411 和 1.1.414 中均已复现：在包含 Sentinel 的递归类型别名中，未显式标注的容器字面量会重新展开并推断值类型，因而产生不必要的类型错误。

以下述类型为例：

```python
from collections.abc import Mapping

from typing_extensions import Sentinel


UNKNOWN = Sentinel('UNKNOWN')
type Value = int | Mapping[str, Value] | UNKNOWN
```

最小可复现示例：

```python
def ok(values: dict[str, Value], key: str) -> None:
    x = values[key]
    values[key] = x


def error(values: dict[str, Value], key: str) -> None:
    x = {key: values[key]}
    values[key] = x[key]
    # error:
    # Type "UNKNOWN" is not assignable to type "UNKNOWN"
```

显式标注目标类型可以修复：

```python
def ok(values: dict[str, Value], key: str) -> None:
    x: dict[str, Value] = {key: values[key]}
    values[key] = x[key]
```

仅在这些方式无法表达真实类型时，才在边界处使用最小范围的 `cast`。

## Skill 使用说明

- 新增或修改外部解析、数据或协议边界、跨模块重构，或进行代码 review 时，先阅读 `skills/pist-development/SKILL.md`。
- 当需要判断未知 Celeste 地图实体是否是真正的草莓、月莓、特殊草莓、磁带或水晶之心，并考虑新增精确规则时，先阅读 `skills/celeste-collectible-entity-analysis/SKILL.md`。
