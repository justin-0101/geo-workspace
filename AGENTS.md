# AGENTS.md

给 coding agent 的作业手册。**项目是什么、怎么用**见 [README.md](README.md)，本文件不重复。
本机执行记录（含真实客户/单位信息）不在此仓库内。

## 命令

```bash
python start.py            # 启动前端(4173) + API(8798)
python start.py --status   # 只看状态
python stop.py             # 只停由本启动器记录的进程

# 平台测试（存储、API、编辑流程、内容生成与门禁、素材、引擎、文案体检、平台登录、端口）
python -m unittest test_workspace_core test_workflow_api test_editorial_flow \
  test_content_generation test_cover_gen test_source_materials test_engine_gates \
  test_report_text_check test_platform_login test_port_probe test_deployment \
  test_frontend_server test_start_stop

node --check workspace.js                     # 前端语法
node test_workspace_progress.mjs              # 前端长任务进度条的行为测试（无 node 时跳过）
python test_browser_flow.py                   # 浏览器全流程：要真浏览器，默认不要跑
```

## 测试基线：先把这条读完，别把环境问题当回归

上面那条 `unittest` 命令在本机的实测结果：**238 项 → 2 失败 + 23 跳过**。

**这 25 项全部与「本机没装 RapidOCR」有关，不是回归：**

- 23 项跳过，原因原文：`本机没有可用的 RapidOCR 或中文字体，跳过 OCR 用例`
- 2 项失败：`test_image_is_queued_for_ocr`、`test_scanned_pdf_without_ocr_reports_why`

判断方法：改动前先在干净树复现一次（`git stash` → 跑 → `git stash pop`），确认失败集合一致再动手。
**不要为了"让测试全绿"去改这两条断言或装 OCR**，那是环境差异，不是缺陷。

## 解释器

本机可能有多个 python。`data/service-pids.json` 记录了当前服务实际用的解释器路径；
报 `ImportError` / 缺 FastAPI、Playwright 时先对照它换解释器，**不要当业务缺陷**。

## 环境变量

| 变量 | 作用 |
|---|---|
| `GEO_CONTENT_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | 内容模型端点/模型/密钥，**按字段覆盖**界面设置 |
| `GEO_CONTENT_LLM_TIMEOUT` | 内容模型读超时（秒），默认 `300`（一次成稿实测约 140 秒） |
| `GEO_REDESIGN_DATA` | 数据目录，默认 `./data` |
| `GEO_BIND_HOST` / `GEO_FRONTEND_PORT` / `GEO_API_PORT` | 绑定地址与端口，仅允许回环 |

**改环境变量必须重启服务**（进程启动时才读）；界面里改设置则立即生效，不用重启。
密钥只写进环境变量或本机数据库，**永远不要写进仓库里的任何文件**。

## 铁律

1. **本仓库是公开仓库：禁止 `git add -A` / `git add .`。**
   本机存在含真实客户名、单位名与实拍截图的文件（已被 `.gitignore` 拦住）。
   只暂存明确列出的文件；`git add -u` + 显式加新文件是安全做法。
2. **客户名、单位名、密钥、会话原文永不入库。** 测试夹具用 `example.com` 之类占位。
3. **动这三样先问，不要自己决定：** `data/`（真实客户数据）、`.gitignore`、`skill/geo-diagnosis-single/scripts/render-report.py`。

## 行尾（踩过的坑）

`.gitattributes` 强制 LF。**用 Python `write_text` 批量改文件会把整个文件转成 CRLF，制造幽灵 diff。**
改完用 `git diff --numstat` 核对增删行数是否只是你要改的那几行。

## 记录分工

| 内容 | 写哪 |
|---|---|
| 用户能感知的变化 | `CHANGELOG.md`（提交前加一条；纯内部改动不加） |
| 难回退的决策 | `docs/decisions/NNNN-*.md`（ADR；写了不改，变了就新写一份标 `superseded by`） |
| 过程、踩坑、客户相关 | 只留本机，**不进仓库** |
| 提交信息 | 约定式提交：`type(scope): 说明`，type 用 `feat` / `fix` / `chore` / `docs` / `test` / `refactor` |

## 不要做

- 不要把 `data/` 下的任何东西复制进仓库（真实主体资料、平台回答原文、截图、SQLite）。
- 不要绕开 `.gitignore`（`-f`、手工 `git add` 被忽略的路径）。
- 不要改 `data/runs/**`：那是冻结的诊断证据快照，动了哈希校验会失败。
- 不要在无人值守时跑 `test_browser_flow.py`（会打开真浏览器）。
