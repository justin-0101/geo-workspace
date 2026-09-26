# GEORank 7 模块对齐说明

本 skill 与 GEORank 开源工作台(yaojingang/GEORank,Apache 2.0)7 大模块的对齐关系,供优化方案对照使用。

## GEORank 7 模块速览

1. **发现**:收录 GEO 公司、工具、专家、教程与案例。
2. **诊断**:检查 Schema、页面结构、Meta、内容可读性与引用信号。
3. **问答**:围绕 GEO 与品牌可见性做结构化问答。
4. **规划**:把诊断结果转化为可执行的 30/60/90 天优化计划。
5. **拓展**:生成关键词、问题词、场景词、商业意图词与推荐型关键词资产。
6. **结构化**:生成 JSON-LD、llms.txt、标题与知识库草稿。
7. **管理**:后台管理公司、诊断、问答、拓词、专家、教程、用户、API 与模块。

## 本 skill 与 7 模块的对齐状态

| # | GEORank 模块 | skill 状态 | 对齐方式 | 缺口 |
|---|---|---|---|---|
| 1 | 发现 | 部分对齐 | 8 题设计隐含"拓词前的业务盘点" | 缺公司目录;不计划引入 |
| 2 | 诊断 | 已对齐 + 增强 | 现有 Q01-Q08 + AI 友好度评分 | — |
| 3 | 问答 | 已对齐 | 每题每平台 1 次回答即单条问答 | — |
| 4 | 规划 | 已对齐 + 增强 | 30/60/90 计划新增工具产出物子项 | — |
| 5 | 拓展 | 新增 | 4 层拓词模型 + 扩题模板 | — |
| 6 | 结构化 | 新增 | JSON-LD + llms.txt + AI 友好度 + 标题生成 + 知识库 | — |
| 7 | 管理 | 不对齐 | 本 skill 是单批执行器,不涉及持续管理后台 | 私有化部署不引入 |

## 与 SKILL.md 工作流阶段的映射

| SKILL.md 阶段 | 对应 GEORank 模块 | 关键产出 |
|---|---|---|
| 阶段 A 准备配置 | 模块 1 部分 | `run-config.json` + 8 题冻结 |
| 阶段 B 初始化预检 | 模块 2 部分 | `manifest.json` + 预检通过 |
| 阶段 C 执行 32 任务 | 模块 2 + 3 | `observations.jsonl` + 截图证据 |
| 阶段 D/E 生成报告 | 模块 2 + 4 | `diagnosis.md` + `metrics.json` + `optimization-plan.md`(8 段) |
| 阶段 F 最终校验 | 模块 2 | `validate-run.ps1` 通过 |
| **阶段 G 沉淀资产(新增,可选)** | **模块 5 + 6** | **JSON-LD + llms.txt + AI 友好度 + 拓词 + 内容矩阵** |

## 借鉴而不复制原则

本 skill **不引入** GEORank 完整代码栈(FastAPI / Celery / Postgres / Qdrant / Neo4j / MinIO / Playwright 部署),仅借鉴:

- 7 模块的工作流方法论
- 4 层拓词法(业务词 → 问题词 → 场景词 → 意图词 → 推荐型关键词)
- JSON-LD 与 llms.txt 模板结构
- AI 友好度 6 维度评分模型

理由:本 skill 是单批诊断执行器,不需要后台运行时;客户报告与产物用文件方式沉淀即可。

## 同步策略

- **季度对齐**:每季度拉一次 GEORank main 分支,看 README / 模块定义变化
- **方法论同步**:GEORank 若升级 4 层拓词,跟进到 `references/keyword-expansion.md`
- **工具同步**:JSON-LD 类型 / llms.txt 规范如有升级,同步 `references/tools-output-spec.md`
- **代码不复用**:不复用 GEORank Python/TS 代码,只复用方法论和 JSON 模板

## 引用关系

```
georank-7-module-alignment.md  ← 本文件(总览)
        ↓ 引用
keyword-expansion.md           ← 模块 5 拓词细节
tools-output-spec.md           ← 模块 6 工具细节
optimization-framework.md      ← 模块 4 规划细节(已扩展)
```
