"""依赖声明图和加载解析草案；不负责下载、启停配置或文件删除。"""

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass

from berries.game.everest import Dependency, ModMetadata, Version
from berries.game.mods import InstalledMod

@dataclass(frozen=True, slots=True)
class DepDeclaration:
    """一条原始依赖声明，不提前选择同名提供者或合并不同条目的要求。"""

    mod: InstalledMod
    metadata: ModMetadata
    dep: Dependency
    optional: bool

@dataclass(frozen=True, slots=True)
class DepIssue:
    """某条声明在静态解析中的不满足事实，通过具体子类区分。"""

    declaration: DepDeclaration

@dataclass(frozen=True, slots=True)
class MissingDep(DepIssue):
    """没有已安装的同名候选，也没有对应的引擎身份。"""

@dataclass(frozen=True, slots=True)
class DisabledDep(DepIssue):
    """没有已加载或已启用的兼容提供者，但禁用包中有兼容候选。

    candidates 保留全部同名安装候选，不仅包含兼容的禁用候选。
    已加载同名身份版本不兼容时，优先报告 IncompatibleDep。
    """

    candidates: tuple[tuple[InstalledMod, ModMetadata], ...]

@dataclass(frozen=True, slots=True)
class IncompatibleDep(DepIssue):
    """已加载同名身份不兼容，或所有安装候选均不兼容。

    candidates 保留全部同名安装候选；selected 是静态解析中已加载的
    提供者，不是预先选择的最高版本。引擎身份用 builtin_version 表达，
    此时 selected 为 None；没有已加载身份时两者均为 None。
    """

    candidates: tuple[tuple[InstalledMod, ModMetadata], ...]
    selected: tuple[InstalledMod, ModMetadata] | None
    builtin_version: Version | None

@dataclass(frozen=True, slots=True)
class UnloadedDep(DepIssue):
    """存在已启用的兼容候选，但在本次静态解析中没有加载。

    candidates 保留全部同名安装候选。候选自身的依赖或同名竞争原因
    通过 LoadResult.issues 查询，不递归嵌入问题对象，也不推断 DLL 失败。
    """

    candidates: tuple[tuple[InstalledMod, ModMetadata], ...]

@dataclass(frozen=True, slots=True)
class LoadIssue:
    """某个已启用包中未加载的条目，各具体问题通过子类表达。

    禁用包不视为加载问题；该条目未加载不代表包内其他条目或资源
    也未加载。调用方按具体子类处理，不依赖原因枚举。
    """

    mod: InstalledMod
    metadata: ModMetadata

@dataclass(frozen=True, slots=True)
class UnsatisfiedDeps(LoadIssue):
    """依赖条件未满足，deps 保留具体未满足的声明且不能为空。"""

    deps: tuple[DepIssue, ...]

@dataclass(frozen=True, slots=True)
class DuplicateName(LoadIssue):
    """加载器因同名身份已加载而跳过该条目。

    loaded_mod 为 None 时，loaded_metadata 表达引擎身份，不伪造物理包。
    延迟项尚未通过依赖检查时仍报告 UnsatisfiedDeps，不仅根据最终
    存在同名身份就将它改判为重复项。
    """

    loaded_metadata: ModMetadata
    loaded_mod: InstalledMod | None

@dataclass(frozen=True, slots=True)
class LoadResult:
    """一次静态加载解析，假定通过依赖检查的模块能够正常加载。

    loaded 表达最终加载的 metadata 到所属包的归属关系，不承诺映射
    迭代顺序为加载顺序；content_order 单独保留物理包首次内容加载顺序。
    disabled_mods 记录未启用的物理包。其他未加载条目统一进入 issues，
    同名但来自不同包的问题分别保留，不用单值映射合并。

    metadata 按值相等；成功加载的身份名称唯一，使用单值映射不会
    丢失成功项。映射值仍是 InstalledMod，不承诺包对象递归不可变。
    引擎身份仅参与依赖满足和同名判定，不进入 loaded 或 content_order。
    不执行 DLL，也不建模 DLL 失败后资源已扫描等运行时状态。
    """

    loaded: Mapping[ModMetadata, InstalledMod]
    content_order: tuple[InstalledMod, ...]
    disabled_mods: tuple[InstalledMod, ...]
    issues: tuple[LoadIssue, ...]

class DepGraph:
    """基于扫描事实建立声明图；结构是图，树只是调用方的一种展示。

    mods 可包含禁用包，须保留扫描顺序。图不重新扫描，也不解析地图。
    名称按 Everest 身份精确匹配；不套用 CeleMod 的类别和清理策略。
    返回具体条目时直接使用 (InstalledMod, ModMetadata)，不以名称或
    manifest 下标替代对象；元数据是不可变值，包本身不承诺递归不可变。
    构图后替换包的 manifest 需重新构图，不混用不同扫描结果的包对象。
    """

    def __init__(self, mods: Sequence[InstalledMod]) -> None: ...
    def providers(self, name: str) -> Iterator[tuple[InstalledMod, ModMetadata]]:
        """返回声明该精确名称的全部条目，不擅自选取最高版本。"""
    def dependencies(
        self,
        mod: InstalledMod,
        *,
        recursive: bool = False,
        include_optional: bool = False,
    ) -> Iterator[DepDeclaration]:
        """查询指定包声明的依赖，递归时展开同名候选提供者的声明。

        直接查询包含该包全部 metadata 的声明，保留各自归属与版本要求。
        递归查询遍历所有同名候选 metadata，不擅自选择最高版本，也不因
        同包另有 metadata 就展开无关声明。缺失依赖仍保留声明。
        结果按扫描及声明顺序深度优先遍历，防止循环，每处声明仅返回一次；
        相等声明来自不同物理包时分别保留。禁用候选也参与查询，结果
        不是启用方案或实际加载关系。mod 须为构图时传入的对象。
        """
    def dependents(
        self, name: str, *, recursive: bool = False, include_optional: bool = False
    ) -> Iterator[DepDeclaration]:
        """查询依赖该名称的声明，递归时继续查询声明者的上游依赖者。

        按声明中的精确依赖名称匹配，不要求已有兼容提供者。递归以
        匹配声明的 metadata 名称继续反查，不扩展同包无关 metadata。
        遍历顺序与去重规则同 dependencies；禁用包中的声明也保留。
        结果保留声明者、版本要求及可选性，不聚合成物理包列表，
        也不代表最终绑定了哪个同名提供者。
        """
    def resolve(
        self,
        enabled: Collection[InstalledMod],
        *,
        builtin_versions: Mapping[str, Version],
    ) -> LoadResult:
        """按启用的物理包、扫描顺序及 Everest 依赖语义解析加载。

        启用范围按包生效，不允许只启用包内的某个 manifest 条目；
        非本图的包对象报错。builtin_versions 显式提供引擎版本，EverestCore
        及旧 API 别名、缺省核心依赖按平台规则处理；不修改原始声明，
        不按普通包名猜测。具体源码依据见 docs/everest-dep-loading.md。

        同一包的 metadata 按 manifest 顺序分别尝试，允许部分加载；
        首个通过检查的条目触发该包一次内容加载。同名身份按注册时序
        占位，不预选最高版本。依赖就绪后按 Everest 的有序延迟队列重试，
        最后放宽可选依赖缺失与相互等待；已加载但不兼容者仍阻塞。

        未满足声明依次按已加载身份不兼容、没有候选、存在已启用兼容
        候选、仅禁用包中存在兼容候选、其余候选不兼容进行分类。
        可选依赖最终允许缺失时不生成问题。复用现有 Version 兼容判断，
        不将声明图拓扑排序或循环分量直接当作加载结果。
        """
