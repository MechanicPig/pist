# Pist (Pig's List Manager)

用于快速记录 Celeste Mod 地图初见数据，并同步到[腾讯智能表格](https://docs.qq.com/smartsheet/DV05RU0tncXp6T1dG)。
## 开发环境

### 构建环境
```sh
uv sync --frozen
```

然后激活项目虚拟环境。Bash 或 zsh：

```sh
source .venv/bin/activate
```

PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

激活后可直接运行下面的 `pist` 命令。若没有激活环境则需要在命令前加上 `uv run`，例如 `uv run pist --help`。

### 验证

```sh
pist --help
```

## 首次配置

### 配置腾讯文档 API 凭证

```sh
pist credentials set
```

### 配置游戏目录

```sh
pist settings game-dir set <game-dir>
```

## 使用

```sh
pist mods browse [--game-dir <game-dir>] [--save-slot <save-slot>]
```

> `game-dir` 默认会使用 `settings` 中配置的目录
