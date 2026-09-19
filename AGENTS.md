# Pist 协作指南

## 项目目标与边界

Pist 用于扫描本地 Celeste 与已启用 Mod，辅助记录地图初见数据、校正路线和可收集物统计，并将确认后的记录同步到腾讯智能表格。

核心能力是：本地游戏/存档/地图数据读取、可审计的实体规则、路线与草稿的本地保存，以及表格写入。规则与统计应保守：无法确认的实体或属性不得自动计入结果。

## 工程约束

- 使用 Python 3.14 及以上版本；依赖、虚拟环境和锁文件统一由 `uv` 管理。
- 源码位于 `src/pist/`，测试位于 `tests/`，打包的共享数据位于 `src/pist/data/`，用户可复用的技能位于 `skills/`，设计与分析文档位于 `docs/`。
- 运行时本地数据、检查报告、临时脚本、参考仓库及构建产物均位于被忽略的 `.pist/`；其中 `.pist/local-data.sqlite3`、扫描报告和构建产物不得手工修改。
- 仅面向内部开发的本地数据结构变更时，先确认没有进程占用对应的 `.pist/*.sqlite3`，再直接迁移当前数据库；不要在源码中保留一次性迁移或旧字段兼容逻辑。
- 变更 SQLite 的表、字段或其语义前，先向用户列出 schema diff 与当前本地数据迁移方案供审查；获确认后再实施，并删除旧 schema，不保留运行时兼容层。
- 新增依赖前先确认标准库或既有依赖无法合理解决问题；通过 `uv add` 或 `uv add --group dev` 更新 `pyproject.toml` 与 `uv.lock`，不要手改锁文件。
- 项目使用 Ruff 作为代码格式化与静态检查工具，行宽为 100，使用单引号；使用 Pyright 作为类型检查工具。
- 模块和包名按职责语义命名：集合、规则或记录用复数，单一模型、协议或过程用单数；不为形式上的单复数一致性重命名。
- 外部输入在边界处用 Pydantic 校验，内部代码保持精确类型，避免无必要的 `Any` 或 `object`。类型注解应表达接口与数据的意图：仅需要可挂载组件时标为 `Widget`，确实保证或区分具体类型时保留具体类型；不要为了满足 typing 而枚举偶然的实现分支。
- Pydantic 模型放在拥有其外部协议、配置或持久化语义的模块；`types.py` 只保留不属于特定领域的共享值类型，不作为模型汇集处。
- 新增或修改的文本文件统一使用 UTF-8。

## 常用命令

```sh
# 安装锁定的开发环境
uv sync --frozen

# 运行 CLI
uv run pist --help

# 完整验证
uv run pytest --basetemp .pist/pytest
uv run ruff check src/pist tests
uv run ruff format --check src/pist tests
uv run pyright src/pist tests

# 验证打包产物
uv build --out-dir .pist/build
```

当环境不允许 pytest 写入系统临时目录时，测试必须统一复用项目内的临时根目录：

```sh
uv run pytest --basetemp .pist/pytest
```

临时诊断、检查或全量扫描脚本放入 `.pist/scripts/`。需要拉取公开参考代码库时，统一浅克隆到 `.pist/references/`；该目录仅用于研究和对照，不纳入项目包或测试输入。

## 修改与验证规则

- 修改领域逻辑、解析、持久化或规则生成后，运行对应测试；影响面不明确或跨层时运行完整测试、Ruff 与 Pyright。
- 修改 Textual UI 或 TCSS 后，至少运行相应 UI 测试；涉及布局、焦点或浏览器预览时还应人工验证关键交互。
- Textual 的静态样式统一放在 `src/pist/ui/styles/*.tcss`，通过应用的 `CSS_PATH` 加载；不要在 Python 类中定义内联 `CSS`。地图预览的浏览器样式保留在其 `static/*.css` 中。
- 修改 CLI 参数、命令语义或用户可见工作流后，同步更新 `README.md`；若改变了技能涵盖的工作流，也同步更新对应 `skills/*/SKILL.md`。
- 每次新增一项完整能力后，主动检查它是否让现有模块混入新的稳定职责、让 `__init__.py` 承担实现细节，或使浏览器协议的两端失配；满足任一情况时，应在同一任务中拆分，或在交付时说明暂不拆分的原因与后续边界。
- 不手改由 `uv`、测试、构建、扫描命令或 TUI 生成的文件。共享规则和模板数据是版本控制下的源数据，修改时应通过审查流程或配套测试验证。
- 内部开发阶段的本地数据路径或 schema 变更应直接迁移现有开发数据，并删除旧实现；除非用户明确要求兼容，不保留旧路径探测、双读或长期回退分支。
- 只修改与当前任务相关的文件；工作区已有的无关改动属于用户，不覆盖、不重置。

## 架构原则

- `game` 只承载游戏文件、存档、Mod 与地图的读取；`entities` 与 `map_entrances` 承载 Pist 的可配置地图解释规则，前者还包含实体统计分析与审计知识。它们及数据模型均不依赖 Textual、浏览器页面或其他 UI；UI 只协调用户交互与领域服务。
- `ui` 按用户可见工作流组织子包；仅跨工作流复用的 Textual 基类或控件保留在 `ui` 根部，避免将不同界面的实现平铺在一起。
- 包的 `__init__.py` 只提供稳定的公共入口；具体实现放在职责明确的子模块中。内部调用优先导入实现所属模块，而不是依赖包入口的偶然重导出。
- 实体规则以明确的实体 ID 和属性条件为准，不能仅根据名称、前缀或显示文本推断语义。保留“属性未写入”与“显式写入默认值”的差异。
- 静态 Lua 分析与求值解耦：解析尽量兼容 Loenn 可接受的语法，求值仅接受可证明的静态结果，未知值不得使整个文件或表失效。
- 网络访问、凭证、游戏文件读取与 SQLite 本地数据均属于边界能力；保持可替换、可诊断，且不泄露凭证。
- 共享规则库可随包发布；用户本地数据和本地增补规则必须与包内数据分离，更新包时不得覆盖用户数据。
- 地图预览是随进程启动、仅监听 loopback 的单功能 Web 界面；Python 服务端与静态资源保持在 `map_preview/` 内，不因其存在而引入独立前端工程。变更浏览器 JSON 状态或操作请求时，须同步更新前后两端并覆盖协议测试。
- `archive` 保存已验证且有回归测试、但当前不参与产品工作流的休眠子系统；活动代码不得依赖它。若要恢复使用，先重新评估边界并迁回对应活动领域，而不是直接重新建立跨层依赖。

## 特殊注意事项

- Mod 可能使用非 UTF-8 的文本；解码失败应在界面或报告中形成可见警告，而不是让整个扫描崩溃。
- 地图和 Mod 文件可能很大；避免在浏览列表或启动阶段无条件解析全部 `.bin`，按需读取并缓存安全的结果。
- 使用完 ZIP、游戏文件或临时 HTTP 服务后及时关闭，避免占用 Loenn 或游戏正在使用的文件。
- 提交信息保持 Conventional Commits 格式，但说明文本使用中文，例如：`fix: 修复草稿字段回读`。
- `main` 只接收面向发布的压缩提交，`dev` 保留细粒度历史。除非用户明确要求，不执行推送、强制更新、重写分支或破坏性 Git 操作。

## Pyright 相关

### Sentinel

项目使用 Pyright 1.1.411 的实验性 Sentinel 支持。在包含 Sentinel 的递归类型别名中，未显式标注的容器字面量会重新展开并推断值类型，因而产生不必要的类型错误。

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
