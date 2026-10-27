"""资源与 Dialog 来源草案；依赖加载和覆盖是两种不同关系。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Self

from berries.game.content import ContentPath
from berries.game.maps import Map
from berries.game.mods import InstalledMod

from .deps import LoadResult

@dataclass(frozen=True, slots=True)
class DialogSource:
    """一个语言文件对某个 key 提供的值，包括被后续提供者覆盖的值。

    source 为原版 Content 目录或实际 Mod；file_path 为该来源中的语言
    文件相对路径，不把语言文件包装成 Map。
    """

    source: Path | InstalledMod
    file_path: ContentPath
    key: str
    value: str

class ContentIndex:
    """一次内容来源索引，不跟踪磁盘更新；不持有打开的 ZIP 或文件流。"""

    @classmethod
    def read(cls, game_dir: Path, *, loading: LoadResult) -> Self:
        """读取资源清单和 Dialog，按内容加载顺序建立索引，不解析地图 bin。

        loading 必须属于该安装。读取失败显式报错，不把部分结果当成
        完整索引；调用方重新读取以刷新。首版只承诺 Maps 和普通 Dialog，
        不宣称复现所有 Everest 资源类型的专用处理。
        """
    def map_sources(self, file_path: ContentPath) -> tuple[Map, ...]:
        """返回地图的有效提供顺序，末项为最终来源；不存在时为空。

        使用完整 Maps/...bin 路径，不把 SID 当作资源路径。
        直接返回 VanillaMap/ModMap，读取及来源访问沿用现有契约。
        结果表示来源优先级，不代表地图内容必然不同。
        """
    def dialog_sources(self, lang: str, key: str) -> tuple[DialogSource, ...]:
        """返回指定语言中该 key 的提供链，末项为最终值。

        key 遵循 Dialog 查询语义；lang 使用 en、zh-cn 等语言标识。
        不执行跨语言回退，不把其他语言的来源混入覆盖链。
        """
