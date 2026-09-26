---
name: geo-diagnosis-single
description: Stable single-batch GEO visibility diagnosis workflow for one company at a time. Use when the user wants to run or prepare a one-shot GEO diagnosis across DeepSeek, Doubao, Qianwen, and Metaso with 8 frozen questions, browser evidence, structured observations, metrics, and reports. Enforces one active run per company workspace and supports companies with no official web presence.
---

# GEO Diagnosis Single

稳定单批版 GEO 一次性可见度诊断。用于“一家公司、一批 8 题、4 平台、每题一次”的可见度快照。

> **路径说明**：下文命令按“已把本 skill 安装到 agent 的 skill 目录”写，即
> `$env:USERPROFILE\.agents\skills\geo-diagnosis-single`。如果你是在 [geo-workspace](https://github.com/justin-0101/geo-workspace)
> 仓库里直接使用，把命令里的 `$env:USERPROFILE\.agents\skills\geo-diagnosis-single` 换成
> `skill/geo-diagnosis-single`（在仓库根目录执行即可）。

开始前阅读：

- `references/single-batch-rules.md`
- `references/question-design.md`
- `references/optimization-framework.md`

## 工作区模型

每家公司必须使用独立工作区，避免混批：

```text
<company-dir>/
├─ input/                 # 公司资料，如 docx/pdf/md
├─ config/run-config.json  # 已人工确认并冻结的问题配置
├─ runs/<run_id>/          # initialize-run.ps1 自动生成
└─ report/                 # 可选：汇总材料
```

不要在通用 skill 目录中保存客户 run 数据。

## 阶段 A：准备配置

1. 从公司资料提取：标准名、明确别名、官网域名/官方页面、业务范围、服务区域、竞品或替代方案。
2. 基于 `templates/run-config.template.json` 创建 `<company-dir>/config/run-config.json`。
3. 若是财税代理/代理记账行业，可参考 `templates/questions.tax-agency.json`。
4. 人工确认 8 道问题后才可冻结。

无官网/公众号/官方页面时，必须显式声明：

```json
"official_domains": [],
"official_pages": [],
"no_official_web_presence": true
```

## 阶段 B：初始化和预检

初始化：

```powershell
& "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\initialize-run.ps1" `
  -ConfigPath '<company-dir>\config\run-config.json' `
  -RunsRoot '<company-dir>\runs'
```

脚本会返回 `run_dir`。之后预检：

```powershell
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\geo_driver.py" preflight '<run_dir>' all
```

门禁要求：

- 浏览器工具能打开登录后网页；
- 配置声明平台均可访问；
- 登录态可用（登录态仅为提醒，不阻断开始）；
- 输出目录可写。

任一失败：写 `BLOCKERS.md`，停止，不提交正式问题。

## 阶段 C：执行 32 个任务

```powershell
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\geo_run.py" '<run_dir>'
```

可调试小批量，但正式诊断必须完整跑完且不重试：

```powershell
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\geo_run.py" '<run_dir>' --limit 1
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\geo_run.py" '<run_dir>' --question Q01
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\geo_run.py" '<run_dir>' --platform deepseek
```

正式报告不得把调试小批量当成完整诊断。

## 阶段 D/E：生成报告

```powershell
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\render-report.py" '<run_dir>'
```

输出：

- `report/diagnosis.md`
- `report/metrics.json`
- `report/manual-review.md`
- `report/optimization-plan.md`
- `report/QUALITY_REPORT.md` — 数据质量报告（完整性、缺失、重复、熔断、引用域名概览）
- `report/TEXT_CHECK.md` — 文案体检：报告里出现了“不属于本主体”的行业词时标 `FAIL`

### 文案只能来自本主体（重要）

报告里的行业、场景、渠道措辞只能来自本批次的 `frozen-config.json`（`brand` 事实 + 问题原文）：

- `brand.business` / `brand.region` / `brand.audience` 是**可选**字段；填了就用，没填则用中性措辞，**不得**回落到某个写死的行业模板。
- 真实事故：渲染器里写死的“低价代账风险 / 本地财税公司 / 自聘会计”出现在**设备资产管理软件**项目的 `diagnosis.md` 里，客户看到的是另一个项目的行业内容。
- `TEXT_CHECK.md` 是最后一道防线：命中“与本批次配置无关的行业词”就写 `FAIL`，命中词表只用于**检测**，不得用于生成文案。平台会把 `FAIL` 的批次标为 `degraded`，不得直接对外交付。
- 未配置业务词就去跑拓词时，`expanded-questions.json` 里会出现 `业务词待填` 占位符（`summary.needs_business_words=true`），不再用某个行业的词兜底。

同时更新根目录数据资产：

- `manifest.json` — run 数据包索引，含任务数、成功/失败数、引用记录数、熔断平台和输出文件校验值
- `citations.jsonl` — 来源引用明细，面向跨客户/跨行业统计

## 阶段 F：最终校验

```powershell
& "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\validate-run.ps1" -RunDir '<run_dir>'
```

只有校验通过、任务均为 `success` 或 `failed`、报告文件存在，才能宣布完成。

**不要求所有平台都成功。** 单平台被验证拦住时按“平台熔断”处理：该平台剩余任务记 `failed`（未提交，附 `evidence/<task_id>_skipped.txt`），**批次继续跑其他平台**，写 `PLATFORM_DEGRADED.md`。被熔断平台的指标在报告中标为**不可用**，不得写成 `0`，不得声称覆盖全部平台。`manual_required` 仅用于全局阻断（浏览器/连接层不可用）。

## 登录辅助

需要人工登录时：

```powershell
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\open_login.py" deepseek doubao qianwen metaso
```

默认浏览器 profile：`E:\geo-profile\UserData`。如需切换：

```powershell
$env:GEO_BROWSER_USER_DATA = 'E:\geo-profiles\company-a\UserData'
```

稳定单批版默认共用单 profile，不支持同 profile 并发。

## 优化方案交付

`optimization-plan.md` 是面向客户的核心交付物，应把诊断缺口转为：

- 身份缺口：品牌事实源、统一 NAP、主体纠错；
- 内容缺口：围绕 Q01-Q05 的高意图内容资产；
- 权威缺口：客户案例、服务数据、第三方平台背书；
- 结构缺口：FAQ、表格、案例块、结构化数据、清晰内链；
- 30/60/90 天执行清单和下次复测验收指标。

交付时强调：GEO 优化不是操纵模型，而是把企业真实优势结构化、公开化、可验证化。

## 问题分类标签

`scope.questions[]` 除 `id/type/business_value/prompt` 外，推荐填写以下标签，便于复测和跨客户统计：

- `layer`：建议取 `edge-real-world`、`query-intent`、`industry`、`prompt-style`、`time-sensitivity`、`trigger-intensity`、`uncategorized`
- `subcat`：子类别，例如 `recommendation`、`pricing`、`competitive-comparison`
- `intent`：中文意图说明，例如 `购买决策/推荐`
- `time_sensitivity`：`low` / `medium` / `high`
- `trigger_intensity`：`low` / `medium` / `high`

老配置没有这些字段时仍兼容，脚本会写空串。

## 报告边界

必须写明：

- 这是测试时点快照，不代表趋势；
- 每题每平台只提交一次，不做复测；
- 不做平台综合排名；
- 自然提及/明确推荐只统计非品牌题；
- 事实准确性是初判，争议项进入人工复核。

## 阶段 G(可选,v2.0 新增):沉淀资产

诊断+报告完成后，可选生成结构化资产，借鉴 GEORank 模块 5(拓展)和模块 6(结构化)。**此阶段为可选,不影响现有 6 阶段主流程。**

```powershell
# 1. 拓词(默认 dry-run,不污染 frozen-config)
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\expand-keywords.py" `
  '<company-dir>\config\keywords-expansion.json' `
  --out '<run_dir>\config\expanded-questions.json'

# 2. 生成 JSON-LD + llms.txt(输出到 run_dir/assets/)
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\generate-schema.py" `
  '<run_dir>' --out '<run_dir>\assets\'

# 3. AI 友好度评分(默认离线,可加 --online 启用 Playwright)
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\ai-friendliness-score.py" `
  '<run_dir>'

# 4. 内容资产矩阵(从 observations.jsonl 抽取)
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\content-asset-matrix.py" `
  '<run_dir>'

# 5. 重新渲染报告(包含新段 9-12)
python "$env:USERPROFILE\.agents\skills\geo-diagnosis-single\scripts\render-report.py" `
  '<run_dir>'
```

**阶段 G 产出物清单**(全部为可选,不破坏 v1.0 流程):

- `<run_dir>/assets/schema.jsonld` — JSON-LD(Organization/LocalBusiness/FAQPage/Service/Review 5 类型)
- `<run_dir>/assets/llms.txt` — llms.txt(7 段结构)
- `<run_dir>/assets/company_kb.md` — 知识库草稿
- `<run_dir>/config/expanded-questions.json` — 拓词后的 16-30 题长尾题库
- `<run_dir>/report/ai-friendliness.json` — 6 维度 AI 友好度评分
- `<run_dir>/report/content-asset-list.csv` — 内容资产矩阵
- `<run_dir>/report/optimization-plan.md` — **12 段**(v1.0 的 8 段 + v2.0 新增 4 段:GEORank 对齐、AI 友好度、工具产出物清单、拓词建议)

**硬约束**:
- 阶段 G 全部为只读 frozen-config.json / observations.jsonl / raw/,只写 `assets/` 和 `report/` 和 `config/expanded-questions.json`
- `expand-keywords.py` 默认 `--dry-run`,需显式 `--write` 才合并到 run-config
- 阶段 G 不影响 validate-run.ps1 的最终校验逻辑

## GEORank 对齐声明(v2.0 新增)

本 skill 与 GEORank 开源工作台(yaojingang/GEORank,Apache 2.0)7 大模块方法论对齐,详细对应关系见 `references/georank-7-module-alignment.md`。

**借鉴而不复制**:不引入 GEORank 完整代码栈(FastAPI/Celery/Postgres/Qdrant/Neo4j),仅复用方法论和 JSON 模板。

**新增文档索引**(v2.0):

- `references/georank-7-module-alignment.md` — 7 模块对齐总览
- `references/keyword-expansion.md` — 4 层拓词法(业务词→问题词→场景词→意图词→推荐型关键词)
- `references/tools-output-spec.md` — 5 大工具产出物规范(JSON-LD/llms.txt/AI 友好度/标题生成/知识库)
- `templates/keywords-expansion.template.json` — 拓词模板
- `templates/schema-org.template.jsonld` — JSON-LD 5 类型模板
- `templates/llms.txt.template` — llms.txt 7 段模板
- `templates/content-asset-list.template.csv` — 内容资产矩阵模板
- `templates/questions.expanded.template.json` — 16-30 题长尾题库模板

**新增脚本索引**(v2.0):

- `scripts/expand-keywords.py` — 拓词本地实现
- `scripts/generate-schema.py` — JSON-LD + llms.txt 生成器
- `scripts/ai-friendliness-score.py` — 6 维度 AI 友好度评分
- `scripts/content-asset-matrix.py` — 内容资产矩阵生成器
- `scripts/render-report.py`(改) — 新增段 9-12(GEORank 对齐/AI 友好度/工具产出物清单/拓词建议)

**正式评审清单**:本目录 `REVIEW-CHECKLIST-v1.0.md`(47 项 A-J)。
