# Pist (Pig's List Manager)

用于快速记录 Celeste Mod 地图初见数据，并同步到[腾讯智能表格](https://docs.qq.com/smartsheet/DV05RU0tncXp6T1dG)。

完整变更请见[更新日志](CHANGELOG.md)。

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

### 浏览地图

```sh
pist maps browse [--game-dir <game-dir>] [--save-slot <save-slot>] [--whitelist <file>] [--blacklist <file>] [--whitelist-full-override]
```

> `game-dir` 默认会使用 `settings` 中配置的目录

`--whitelist`、`--blacklist` 与 `--whitelist-full-override` 分别对应 Everest 的同名启动选项和 `WhitelistFullOverride` 设置；相对路径在游戏的 `Mods` 目录下解析。

目前按游戏内地图集（Campaign）组织地图，合集大厅支持展开投稿地图列表，考虑到静态识别难以覆盖游戏内行为，支持编辑大厅对应的地图集。

核心功能：

- 浏览地图日志：显示地图的名称、死亡数、用时等基本信息
- 预览地图：类 Debug Map，目前通过规则支持了一些常见的草莓、磁带与水晶之心的标注
- 地图跳转：预览界面支持从大厅地图跳转到小图（实际上不止大厅）
- 编辑路线：用于统计地图的主房间数

### 实体审计

```sh
pist entities audit [--game-dir <game-dir>] [<input-path>]
```

地图预览目前通过一系列规则命中我们关心的实体，通常是根据实体名和属性值判定，该命令提供了一个 TUI 界面，支持更方便将实体归类以自动生成规则。

由于审计数据没有上传到仓库共享，所以目前对一般用户而言可能没什么用，主要是我用来更新规则文件用。

## TODO

- [ ] 补充 `FlushelineCollab/LevelEntrance` 的静态地图入口规则；需要先核对真实交互区域和目标属性。
- [ ] 当需要支持多用户共同维护规则时，添加可回放的规则编辑操作日志。
- [ ] 若实体审计的导入、筛选或保存再次出现可感知卡顿，按实际热点继续优化，同时保持审查状态与筛选结果稳定。
- [ ] 补全水晶之心在未写入 `HeartIsEnd` 时的默认结算语义：按地图的 Side 与来源判断通关结算或额外收集，且保留显式元数据的覆盖行为。
- [ ] Python 3.15 正式发布后，统一评估扫描结果快照的冻结模型与 tuple 容器迁移。
- [ ] 低优先级：将地图预览脚本迁移为 TypeScript，并将路线排除项迁移为结构化 `MapEntityKey`。
