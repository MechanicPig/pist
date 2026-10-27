"""待审阅的 Berries 接口草案，不是已实现或随包发布的 API。

deps 对应拟议的 berries.game.deps；content_index 对应内容来源查询。
saves/ 保留 slot/saves_dir 的文件组与目录接口，operations.py 组合单文件业务。
文件层已在 berries.game.saves.files 实现，模型归 berries.game.saves.models。
编号和目录层仍只有声明，尚未迁移应用现有读取工作流。
时长字段和参数使用公共 Duration，ticks 只在存档协议边界转换。
所有省略号均表示未实现，不接入 CLI、UI、包导出或数据库。
方法只含 docstring 时同样是接口声明，不是运行时的空操作。

这版仅约定依赖／来源查询与存档基础编辑，不提前固定恢复、补录、
完整地图移动或启停管理工作流。序列化不写磁盘，文件替换等策略
由调用方负责；文件层只做内存编辑及序列化，不自动写回游戏存档。
"""
