# Pist

用于快速记录 Celeste Mod 地图初见数据，并同步到腾讯智能表格。

## 开发环境

### 构建环境
```bash
uv sync --frozen
```

### 验证

```bash
pist --help
```

## 首次配置

### 配置腾讯文档 API 凭证

```bash
pist credentials set
```

### 配置游戏目录

```bash
pist settings game-dir set <game-dir>
```

## 使用

```bash
pist mods browse [--game-dir <game-dir>] [--save-slot <save-slot>]
```

> `game-dir` 默认会使用 `settings` 中配置的目录
