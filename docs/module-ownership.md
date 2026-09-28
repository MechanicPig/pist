# 模块归属与依赖边界

本文记录 workspace 中两个发行项目的模块归属。依赖方向只能由 Pist 应用指向 Berries 公共包。

## 公共 Celeste 能力

- `berries.installation`：面向第三方应用的高层只读入口；
- `berries.game.*`：游戏、Mod、存档、Campaign、Level、Map 与 Collab 事实读取；
- `berries.entities.rules`、`berries.entities.classification`：共享识别规则与分类；
- `berries.map_layout`、`berries.map_entrances`：客观地图布局与入口识别；
- `berries.gamebanana`：外部投稿元数据查询；
- `berries.map_preview`：只读 loopback 地图预览；

公共模块不得导入下述个人应用或审计模块。

### 原版事实与 Everest 生效内容

公共包内部保留一条更细的单向依赖边界：

```text
Celeste 原版格式与磁盘事实
              ↓
Everest 激活、覆盖与 Mod 组合语义
```

`game.content`、`game.vanilla`、地图二进制格式和原生存档读取属于基础层；`game.everest`、
`game.mods` 以及基于加载顺序构建 Campaign 目录的能力建立在基础层之上。共享的 Map、Level
等领域模型不因内容来源而复制。

这条边界用于约束依赖和测试，不代表面向两类用户提供两个并列产品。首版不拆分单独的原版
发行包，也不增加 `VanillaInstallation` 一类稳定入口。`GameInstallation` 继续返回游戏实际生效的
内容；没有启用 Mod 时，其结果自然退化为原版目录。只有出现真实的纯原版消费者后，才重新评估
是否公开专用入口。

## 个人记录应用

- `pist.entity_stats`、`pist.record_entities`、`pist.records`：个人统计与记录投影；
- `pist.app_resources`：随个人应用版本控制、但不属于公共规则库的数据；
- `pist.routes`、`pist.route_store`、`pist.route_editor`：路线模型、持久化和编辑器；
- `pist.record_store`、`pist.app_data`：个人数据持久化与应用组装；
- `pist.catalog_cache`：地图浏览器使用的、可丢弃且可重建的 Collab 扫描缓存；
- `pist.smartsheet`、`pist.sheet_report`、`pist.secrets`：腾讯表格和凭证；
- `pist.ui.maps`：个人记录工作流。

## 实体审计应用

- `pist.entities.audit`：审计数据库与证据模型；
- `pist.ui.entities`：审计交互、规则维护和 occurrence 预览。

审计应用可以使用公共分类和预览能力，但公共模块不得读取审计数据库或导入审计 UI。
