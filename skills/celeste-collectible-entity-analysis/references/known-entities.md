# 非直观实体映射与特殊源码证据

此处记录实体 ID 无法直接说明地图语义的高价值反例，以及需要源码分析才能建立的特殊证据；不重复维护完整规则表。当前类别、属性条件和排除规则仍以 `src/pist/data/kinds.toml`、`src/pist/data/entities.toml` 与实体审计为准。

## 非直观地图语义

| 实体 ID | 地图语义 | 关键条件或说明 |
|---|---|---|
| `blackGem` | 水晶之心 | `fake = true` 时排除；否则根据 `meta.HeartIsEnd` 归为通关收集或额外收集。 |
| `birdForsakenCityGem` | 额外收集水晶之心 | 原版实体名未直接体现水晶之心语义。 |
| `memorialTextController` | 无冲金草莓 | 原版 `Level` 在无冲刺且从头开始等条件满足时，直接构造 `Strawberry`，并设为 `Golden + Winged`。 |
| `JungleHelper/TreeDepthController` | 无抓金草莓 | JungleHelper 将其注册为 `GrablessGoldenBerry : Celeste.Strawberry`；实体 ID 沿用了控制器式命名。 |

## 特殊源码证据

| 实体 ID | 程序集 / 类型 | 静态证据 |
|---|---|---|
| `ArphimigonHelper/HeartGem` | `ArphimigonsToyBox` / `Celeste.Mod.ArphimigonHelper.ArphimigonHeartGem` | `CustomEntity` 精确注册；虽继承 `Monocle.Entity`，但 `RegisterAsCollected` 会调用 `SaveData.RegisterHeartGem` 与 `RegisterPoemEntry`。 |

`ArphimigonHelper` 是 `ArphimigonsToyBox` 注册的实体前缀，不代表存在同名独立 Mod。
