---
name: celeste-collectible-entity-analysis
description: "审查实体审计无法定性的 Celeste 地图实体：通过本地地图、Everest 注册和静态 .NET 证据判断其可收集语义，并将结论录入 Pist 实体审计。"
---

# Celeste 可收集实体分析

仅在 `pist entities audit` 无法判断一个指定实体的真实语义，或用户明确要求检查其源码实现时使用。本 Skill 是实体审计的证据补充，不代替审计 TUI、规则生成或人工游戏测试。

不要运行 Mod DLL，也不要把 DLL 分析包加入 Pist 的项目依赖。

## 工作流程

1. 先查询当前类别树 `src/pist/data/kinds.toml`、共享规则 `src/pist/data/entities.toml` 和实体审计中的现有结论。规则与类别树是当前归类的唯一来源；本 Skill 的参考文档不重复维护完整实体清单。
2. 明确待审计的精确实体 ID、出现地图、房间、坐标、原始属性和相关地图元数据。必要时用 `pist.game.binmap.parse_map_bin` 读取指定 `.bin` 地图；保留“属性未写入”和“显式默认值”的差异。
3. 读取 Mod 的 `everest.yaml` 与依赖。实体前缀只是标识，不能证明存在同名 Mod；从 Mod 压缩包及其声明依赖中定位实现 DLL。
4. 静态读取 .NET 元数据，为精确实体 ID 查找 `CustomEntity` 注册，并记录程序集、实现类型和继承链。需要时在临时环境使用 `uv run --with dnfile`；不得修改 `pyproject.toml` 或 `uv.lock`。
5. 仅当注册和继承链不足以定性时审查 IL。对心，查找实际收集路径是否调用 `SaveData.RegisterHeartGem`；对其他收集物，确认对应的原版存档或收集调用。字符串、实体名称或命名空间出现本身不是证据。
6. 将结论及证据来源录入实体审计：可确认的分类、明确排除的情形，或仍无法确定的运行时条件。需要区分“影响类别”“影响行为”和“尚未确认”的属性。
7. 只有审计覆盖了当前报告中的所有相关变体后，才由实体审计的规则刷新功能生成共享规则。不得绕过审计直接为未知实体手写规则；刷新或发布共享规则前先征求用户同意。

## 判定原则

- 继承 `Celeste.Strawberry` 可以证明草莓收集语义，但不能单独决定最终类别。`moon`、`golden` 等属性是否影响类别，必须由实体审计或源码证据分别确认。
- 继承 `Celeste.HeartGem` 可以证明心的收集语义；自定义 `Monocle.Entity` 若在实际收集路径调用 `SaveData.RegisterHeartGem`，同样是真正的心。
- 心是否“通关收集”或“额外收集”是规则层结论，可能取决于实体属性和地图元数据（例如 `meta.HeartIsEnd`）；不要仅凭 `endLevel`、实体名称或单个地图实例下结论。
- 不要仅凭 `heart`、`berry`、`cassette` 等名称、实体前缀或 Loenn 显示名称推断语义。
- 面对动态代码、条件注册或无法静态证明的行为，应保留为未知并说明限制，而不是扩大匹配范围。

在对已有源码证据作出新建议前，先阅读[已知实体](references/known-entities.md)。
