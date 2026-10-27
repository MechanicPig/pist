# 文档索引

## 分类与维护

`docs/` 根目录保留当前需要查阅和维护的核心文档，包括使用指南、工程约束、API 契约、TODO 与仍在推进的设计。文档是否完成、核心结论是否仍适用是两个不同判断，不按文件日期或是否包含历史章节简单分类。

- `finished/`：已完成且核心结论仍有效的演进、选型和验证总结，保留背景、取舍与证据；不是另一份需要同步维护的当前 API 手册。
- `archive/`：已被后续设计取代的历史文档。源码调研可作历史线索，但旧接口和旧组织方式不能作为当前实现依据。
- `api_drafts/`：尚未实现、仍待设计确认的接口草案。不因完成了调研或写出了类型签名就移入 finished。

核心文档若包含历史依据，链接到相应总结即可；不为目录整齐而拆散仍在推进的设计。已完成总结的核心结论被替代后移入 archive，并补充当前入口。调整位置时同步修复 Markdown 链接、技能参考和状态说明。

## 当前核心文档

| 文档 | 用途 |
| --- | --- |
| [TODO](TODO.md) | 当前待办与已完成阶段的进度。 |
| [Berries 使用指南](berries-usage.md) | 公共包的任务式使用说明。 |
| [存档领域 API](saves-domain-api.md) | 当前存档领域对象、内存文档与一次性读写、槽位、集合与组合业务契约。 |
| [存档容器与成员关系](saves-containers.md) | 分区唯一性与进度丢失背景、成员修改协调、Blender 普通字典及引用语义。 |
| [KeyedDict](keyed-dict.md) | 具有派生 key 不变量的通用映射契约。 |
| [模块归属与依赖边界](module-ownership.md) | Berries、Pist 和共享测试的职责与验证边界。 |
| [路径与标识符比较语义](path-identity-semantics.md) | 路径、SID、Dialog key 等外部身份的比较依据。 |
| [测试维护](testing.md) | 验证范围、并行、耗时与测试资源隔离。 |

## 仍在推进的设计与调研

以下文档保留在根目录：它们仍包含未完成的目标或未落地接口，不能整体视为已完成演进。阶段性历史记录保留原有上下文，本次不重写其业务结论。

- [本地记录与表格同步设计](data-management-design.md)：包含已完成迁移和未完成的数据管理方向。
- [公共能力包与个人应用分层路线图](public-package-roadmap.md)：包含拆分历史与仍待推进的实体审计独立应用目标。
- [Everest 依赖加载语义与接口设计依据](everest-dep-loading.md)：保留源码调研与尚未实现的依赖接口设计，关联 [接口草案目录](api_drafts/)。
- [存档收集品联动调研](saves-collectible-linkage.md)：金草莓累计、原版及 CommunalHelper 宝石的收集与会话链路、支持假设和待实现边界。

## 已完成且仍有效的总结

- [Duration 设计回顾](finished/duration-design.md)：精确时长、单位转换、格式化及应用边界的改造。
- [存档完整建模迁移](finished/saves-modeling.md)：五种文件的声明字段、未知字段策略、float32 选型、独立审查及验证边界。
- [存档 XML 后端选择](finished/save-xml-backends.md)：后端比较、保真约束、历史性能测量与 .NET 回读验证。
- [Collab 会话集合与输入模型的演进](finished/collab-sessions-evolution.md)：从实际调用分析推导惰性边界，分离 SessionInput 与 Session。

## 已过时的历史文档

- [Saves 三层接口设计](archive/saves-api-design.md)：早期文件继承、快照和公开编解码接口已被当前存档领域 API 取代，保留协议调研供复查。
- [地图与内容来源类型重构](archive/map-content-types.md)：旧的 MapInfo／ContentSource 等组织已被后续模型取代，保留当时设计快照。
