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

```sh
pist mods browse [--game-dir <game-dir>] [--save-slot <save-slot>] [--whitelist <file>] [--blacklist <file>] [--whitelist-full-override]
```

> `game-dir` 默认会使用 `settings` 中配置的目录

`--whitelist`、`--blacklist` 与 `--whitelist-full-override` 分别对应 Everest 的同名启动选项和 `WhitelistFullOverride` 设置；相对路径在游戏的 `Mods` 目录下解析。

## 后续方向

- [ ] 补充 `FlushelineCollab/LevelEntrance` 的静态地图入口规则；需要先核对真实交互区域和目标属性。
- [ ] 复现 Everest 的全局 Dialog 合并规则，让 MapModifier 等无 Dialog 的地图复用已加载 Mod 的同 key 名称；实现前先明确加载顺序和重复 key 覆盖语义。
- [ ] 当需要支持多用户共同维护规则时，添加可回放的规则编辑操作日志。
- [ ] 若实体审计的导入、筛选或保存再次出现可感知卡顿，按实际热点继续优化，同时保持审查状态与筛选结果稳定。
- [ ] Python 3.15 正式发布后，统一评估扫描结果快照的冻结模型与 tuple 容器迁移。
- [ ] 低优先级：将地图预览脚本迁移为 TypeScript，并将路线排除项迁移为结构化 `MapEntityKey`。
