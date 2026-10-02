# Pist (Pig's List Manager)

用于扫描本地 Celeste 与已启用 Mod，记录地图初见数据、校正路线和可收集物统计，并将确认后的记录同步到腾讯智能表格。

Pist 基于同一仓库中的 Berries 公共包，提供个人记录工作流、实体审计 TUI 和地图预览／路线编辑界面。

在仓库根目录安装开发环境并查看命令：

```sh
uv sync --all-packages --frozen
uv run --package pist pist --help
```

完整配置与使用说明见仓库根目录的 [README](../README.md)。
