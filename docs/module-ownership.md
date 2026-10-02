# 模块归属与依赖边界

本文记录 workspace 中两个发行项目的模块归属。依赖方向只能由 Pist 应用指向 Berries 公共包。

仓库根目录仅管理 workspace 与开发工具。公共包位于 `berries/src/berries/`，个人应用位于 `pist/src/pist/`；两个项目各自维护发行配置与包内资源。

公共能力测试归 `berries/tests/`，应用工作流测试归 `pist/tests/`。根目录 `tests/test_arch.py` 验证仓库级架构约束；两包复用的测试工厂及辅助工具位于 `tests/test_support/`，扫描测试的合成 Mod 数据位于 `berries/tests/data/`，不纳入任何发行包。测试目录不作为同名 Python 包导入；使用 pytest 默认导入模式时，跨目录测试文件名须保持唯一。Berries 测试应能在没有 Pist、Textual 及 keyring 的环境中独立运行。

### 验证边界与性能约束

[CI](../.github/workflows/ci.yaml) 已在独立环境中验证 Berries 不依赖 Pist、Textual 或 keyring，并验证最小 wheel 安装与包内资源可用。这些检查与 pytest 中的静态架构断言互补；审查时须区分未找到覆盖、本次未执行和已经存在的其他验证机制。

ZIP 遍历测试保护成员枚举的工作量，而不固定 `walk()` 是否通过 `iterdir()` 或其他缓存查询实现：首次多层遍历不得为每个目录重新扫描整个归档，成员访问量应受条目数的线性上界约束；同一打开归档的重复遍历不应重新枚举成员或重建完整索引，并应保持输出一致。常规 CI 不使用精确耗时作为这一约束的判据。

loopback 预览的 HTTP 集成测试使用共享辅助工具约束启动与请求总等待时间，启动提前失败时传播原始异常，并在任何断言失败或超时后取消、等待后台任务，避免测试挂起或泄漏监听端口。

## 公共 Celeste 能力

- `berries.installation`：面向第三方应用的高层只读入口；
- `berries.game.*`：游戏、Mod、存档、Campaign、Level、Map 与 Collab 事实读取；
- `berries.entities.rules`、`berries.entities.classification`：共享识别规则与分类；
- `berries.map_layout`、`berries.map_entrances`：客观地图布局与入口识别；
- `berries.gamebanana`：外部投稿元数据查询；
- `berries.map_preview`：只读 loopback 地图预览，以及可组合的会话、地图导航与基础画布能力；

公共模块不得导入下述个人应用或审计模块。

### 原版事实与 Everest 生效内容

公共包内部保留一条更细的单向依赖边界：

```text
Celeste 原版格式与磁盘事实
              ↓
Everest 激活、覆盖与 Mod 组合语义
```

`game.content`、`game.vanilla`、地图二进制格式和原生存档读取属于基础层；`game.everest`、`game.mods` 以及基于加载顺序构建 Campaign 目录的能力建立在基础层之上。共享的 Map、Level 等领域模型不因内容来源而复制。

这条边界用于约束依赖和测试，不代表面向两类用户提供两个并列产品。首版不拆分单独的原版发行包，也不增加 `VanillaInstallation` 一类稳定入口。`GameInstallation` 继续返回游戏实际生效的内容；没有启用 Mod 时，其结果自然退化为原版目录。只有出现真实的纯原版消费者后，才重新评估是否公开专用入口。

## 个人记录应用

- `pist.entity_stats`、`pist.record_entities`、`pist.records`：个人统计与记录投影；
- `pist.app_resources`：随个人应用版本控制、但不属于公共规则库的数据；
- `pist.routes`、`pist.route_store`：个人路线模型与持久化；
- `pist.map_preview`：组合 Berries 基础能力的地图预览，附加路线编辑、实体屏蔽及初见统计；
- `pist.record_store`、`pist.app_data`：个人数据持久化与应用组装；
- `pist.catalog_cache`：地图浏览器使用的、可丢弃且可重建的 Collab 扫描缓存；
- `pist.smartsheet`、`pist.sheet_report`、`pist.secrets`：腾讯表格和凭证；
- `pist.ui.maps`：个人记录工作流。

## 实体审计应用

- `pist.entities.audit`：审计数据库与证据模型；
- `pist.ui.entities`：审计交互、规则维护和 occurrence 预览。

审计应用可以使用公共分类和预览能力，但公共模块不得读取审计数据库或导入审计 UI。

## 地图预览的组合边界

`berries.map_preview.server.MapPreview` 和 `pist.map_preview.server.MapPreview` 不互相继承。两者组合使用 `PreviewSession` 管理 loopback 服务和浏览器生命周期，使用 `PreviewAssets` 加载页面、共享绘制模块及图片，使用 `PreviewNavigation` 解析入口目标并维护地图历史。各自的页面通过组合共享的 JavaScript `MapCanvas` 完成瓦片、图片、重生点绘制和视野适配。

Pist 保留自己的编辑协议、页面交互和个人数据叠加；公共能力不读取路线数据库、个人统计规则或表格字段。基础画布模块由 Berries 提供，Pist 不复制一份静态实现。
