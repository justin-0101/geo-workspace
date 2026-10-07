# GEO 工作空间

本地单用户的 GEO（生成式引擎优化）可见度诊断与内容发布工作台。

一条主线：**创建主体项目 → 配置并冻结诊断问题 → 用真实浏览器到 AI 平台逐题提问取证 → 生成报告与改善任务 → 内容人工审核 → 发布并回填回执**。原始回答、截图、结构化观测与报告全部留在本机。

> 定位：可运行、可自证的本地原型，不是托管服务。单用户、单机、SQLite、无账号系统、**不对公网监听**。诊断与发布都在你自己的浏览器会话里完成——系统不代填账号密码、不导出 cookie、不绕过平台验证。

---

## 功能

| 模块 | 做什么 |
|---|---|
| 工作台 | 跨项目待处理事项与最近活动（不绑定某个项目） |
| 诊断项目 | 创建 / 搜索 / 确认删除；项目内完成主体资料、问题配置、冻结、环境检查、执行与结果查看 |
| 改善任务 | 只从「有完整证据的成功观测」生成改善项；不把执行失败当作品牌不可见；同一诊断问题聚合成一条，不再按平台重复建题 |
| 内容生产 | 确认写作任务书与素材，一键生成初稿；不在这里写稿 |
| 内容库 | 所有内容的清单（序号 / 标题 / 来源 / 渠道 / 状态 / 字数 / 生成时间），查看、编辑、删除、审核；**正文按 Markdown 渲染**，不再把 `##` `**` 原样显示 |
| 批量发布 | 十二个平台的适配器：公众号走官方 API 写草稿箱，其余走浏览器自动化或人工发布回执；**只列已审核内容** |
| 报告 | 报告正文在线阅读；截图在结果区显示，其他证据可下载 |

---

## 仓库结构

```
geo-platform-redesign-v1/            # 本仓库根目录 = 平台本体
├─ start.py / serve.py               # 启动器（拉起就返回 / 前台常驻）
├─ runtime_config.py                 # 环境变量、回环地址与端口契约
├─ process_identity.py               # 停止服务前的 PID 身份校验
├─ frontend_server.py                # 4173 前端白名单服务
├─ workflow_api.py                   # 8798 FastAPI（项目/资料/冻结/执行/内容/发布）
├─ workspace_store.py                # SQLite 存储与增量迁移
├─ diagnosis_config.py               # 诊断平台、固定八题槽位与冻结配置校验
├─ diagnosis_question_llm.py         # 受约束问题问法生成、质量门禁与本地 fallback
├─ diagnosis_runs.py                 # 冻结批次、任务矩阵、配置哈希与目录归属校验
├─ browser_engine.py                 # 调用引擎子进程：环境检查/执行/报告/浏览器锁
├─ editorial_flow.py                 # 改善项 → 内容选题 → 证据包 → 审核 → 发布
├─ content_generation.py             # 生成引擎（本地安全稿 / OpenAI 兼容）与内容质量门禁
├─ source_materials.py               # 素材：附件解析（pdf/docx/xlsx/html）、网址抓取、OCR、粘贴文本
├─ publish_adapters.py / wechat_mp.py / browser_publisher.py / platform_login.py
├─ cover_gen.py                      # 公众号封面生成（发布运行时必需）
├─ workspace.html / workspace.css / workspace.js    # 统一前端框架
└─ skill/geo-diagnosis-single/       # 诊断引擎（也可作为独立 agent skill 使用）
    ├─ SKILL.md                      # 引擎入口文档
    ├─ scripts/                      # 浏览器驱动、执行器、报告渲染、拓词与资产工具
    ├─ templates/                    # 配置与产出物模板
    ├─ references/                   # 方法论参考
    └─ tests/                        # 引擎自身测试
```

不入库（见 `.gitignore`）：`data/`（SQLite、runs 证据、浏览器 profile、日志）、`backups/`、`test-results/`、`REBUILD-PROGRESS.md`。

---

## 快速开始

### 环境要求

- **Windows**：浏览器自动化与两个校验脚本（`validate-run.ps1`、`update-task-state.ps1`）按 Windows 写；换 Linux/macOS 需要自行改写这两处。
- **Python 3.11+**（已在 3.11 验证）。
- **Google Chrome**：默认取 `C:\Program Files\Google\Chrome\Application\chrome.exe`，可用 `GEO_CHROME_PATH` 覆盖。引擎直接驱动系统 Chrome，**不需要** `playwright install`。
- **依赖**：在仓库根目录创建虚拟环境，并按发布清单安装全部运行时依赖（不要只安装几项基础包）：

```powershell
py -3.11 -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.venv\\Scripts\\python.exe check_install.py --skip-chrome
```

也可以直接运行 `install.ps1`；它会创建 `.venv`、安装 `requirements.txt` 并执行安装预检。

### 启动

```bash
python start.py            # 拉起就返回，服务脱离终端，日志落 data/frontend.log、data/workflow-api.log
python start.py --status   # 只查看状态
python serve.py            # 前台常驻（Ctrl+C = 前端与 API 一起停）
```

- 前端 http://127.0.0.1:4173/ ，API 127.0.0.1:8798。
- `start.py` 与 `serve.py` **不要同时用**：端口被占时前端会拒绝启动。
- 仅支持 IPv4 回环 `127.0.0.1`/`localhost`；`::1` 会被明确拒绝。前端与 API 端口必须不同。
- 等价的手工方式：`python -m uvicorn workflow_api:app --host 127.0.0.1 --port 8798` + `python frontend_server.py`。

### 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `GEO_REDESIGN_DATA` | 数据目录（SQLite、runs、浏览器 profile、日志） | `./data` |
| `GEO_REDESIGN_ENGINE` | 诊断引擎脚本目录 | 仓库内 `skill/geo-diagnosis-single/scripts` |
| `GEO_BIND_HOST` | 绑定地址（仅 IPv4 回环） | `127.0.0.1` |
| `GEO_FRONTEND_PORT` / `GEO_API_PORT` | 前端 / API 端口（必须不同） | `4173` / `8798` |
| `GEO_CHROME_PATH` | Chrome 可执行文件 | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| `GEO_BROWSER_USER_DATA` / `GEO_BROWSER_PROFILE_DIR` / `GEO_CDP_PORT` | 隔离浏览器 profile 与调试端口 | 平台自动设为 `data/browser-profile` / `Default` / `9348` |
| `GEO_MIN_ANSWER_CHARS` | 回答区达到多少字符才算有效（引擎） | `400` |
| `GEO_MAX_CONSECUTIVE_INVALID` | 同一平台连续多少次无效回答就熔断该平台 | `2` |
| `GEO_PAUSE_TIMEOUT_S` / `GEO_MAX_PAUSES_PER_TASK` | 遇到平台验证时的人工接力等待与次数上限 | `1800` / `3` |

### 为什么必须用白名单服务

`frontend_server.py` 只提供 `workspace.html/css/js`、`geo-config.js` 与 `fonts/` 下字体，其他旧入口重定向到统一框架；`data/`、`backups/`、脚本与测试均不可下载；启动前还会检测前端端口是否已被占用。发布包还必须包含 `cover_gen.py`、`process_identity.py`、`runtime_config.py` 与完整 `skill/geo-diagnosis-single/` 子树；不要只导出 HTML 原型文件。

**不要用 `python -m http.server` 暴露本仓库目录。** 本项目真实发生过一次：一个残留的 `python -m http.server` 与白名单服务同时监听（前者监听 `0.0.0.0`），导致 `data/redesign.db` 能被局域网直接下载。该进程已终止，并加了「启动前检测端口占用」的保护。

---

## 诊断引擎

平台以**子进程**方式调用 `skill/geo-diagnosis-single/scripts/` 下的脚本；这个目录本身也可以作为独立的 agent skill 使用，入口是 `SKILL.md`。

引擎路径解析顺序：`GEO_REDESIGN_ENGINE` → 仓库内 `skill/geo-diagnosis-single/scripts` → `~/.agents/skills/geo-diagnosis-single/scripts`。本机开发环境用目录联接（junction）把 `~/.agents/skills/geo-diagnosis-single` 指向仓库内目录，因此 agent 的 skill 发现机制与平台读到的是同一份文件，不需要同步。

引擎产出（每批次目录 `data/runs/<run_id>/`）：

```
frozen-config.json   tasks.jsonl   observations.jsonl   citations.jsonl
state.json / manifest.json
raw/       原始回答
evidence/  截图与跳过说明
logs/      引擎日志
report/    diagnosis.md  metrics.json  manual-review.md  optimization-plan.md
           QUALITY_REPORT.md  TEXT_CHECK.md
```

---

## 诊断执行约束

1. 资料保存后生成八题建议，优先由受约束 LLM 生成自然问法；模型只生成 prompt，固定槽位和本地门禁控制题数、分类与品牌边界。不可用时明确回退本地规则生成，人工仍需逐题确认。前五题不能带品牌名、别名或官方链接。
2. 诊断平台与发布平台分开。诊断支持 DeepSeek、豆包、千问、秘塔。
3. 冻结生成配置哈希与任务矩阵；**每个项目同时只允许一个未结束批次**。
4. 在隔离浏览器窗口里自行完成平台登录，回到页面确认后再检查环境。
5. 检查通过后确认提交，调用真实浏览器执行器。**每题每平台只提交一次**，不重试提交状态不明的任务。
6. 原始回答、截图与观测落盘到本批次目录；只有报告校验通过才标记完成。
7. 中断后可以重新检查环境；仍为 `running` / `manual_required` 的任务不能再次提交。可以在任务全部终态时重新生成报告，或确认终止本批次（证据保留）。
8. **报告文案只能来自本批次的冻结配置**（主体事实 + 问题原文）。生成时同步写 `report/TEXT_CHECK.md` 做文案体检；一旦命中与本主体无关的行业词（模板泄漏），批次标为 `degraded`，不得直接对外交付。

---

## 数据隔离与安全边界

- **运行数据不入库**：`data/` 只在本机；不使用、不迁移旧版应用的数据库、客户配置、runs 或登录 profile。
- **浏览器隔离**：诊断与发布共用一个专用 profile（`data/browser-profile/`），不读取你日常浏览器的登录态，不读取或导出 cookie、令牌，不代过平台验证。
- **凭据**：公众号 AppID/AppSecret 存本机 SQLite 的 `workspace_preferences`，界面与接口只返回「是否已配置 + 掩码」，永不回传明文。**但它在磁盘上是明文**，所以不要在不设防的共享机器上填写。
- **项目删除**：级联删除结构化记录；原始证据目录保留，但不再能通过项目 API 访问（暂无回收站界面）。
- **并发**：浏览器是全局独占资源（一个进程锁 + `browser-execution.lock`），同一时刻只允许一个诊断或发布在用浏览器。

---

## 内容生成

一条链路：**诊断缺口聚合选题 → 组装证据包 → 生成初稿 → 自动质量检查 → 人工核验事实 → 审核 → 发布**。

### 两条入口

- **从改善任务**：点击「编写内容」后，草稿自动带上对应诊断问题、各平台观测摘要、证据文件索引和报告建议标题。
- **新建内容任务书**：不依赖诊断，适用于企业不需要诊断就已知的内容需求。

### 证据包

每次生成都会把当时使用的输入存成快照（`source_bundle_json`），包括：项目资料、诊断问题、各平台回答摘要（标记 `unverified-model-response`）、证据文件路径、报告建议标题、素材摘要与人工填写的事实。

> **诊断回答不作为企业事实。** 它只用于判断「用户会问什么、模型目前答什么」，不能当作主体能力证明。资质、案例、客户、价格、效果、承诺一律要求人工提供可核验来源。

### 事实与来源：素材（不再需要手填事实依据）

「事实与来源」已改为**素材区**，不再有手填的事实依据输入框：事实与来源由素材在生成时自动带出。

| 方式 | 支持 | 解析 |
|---|---|---|
| 上传附件（可多选） | `.txt` `.md` `.csv` `.json` `.html` `.pdf` `.docx` `.xlsx`，以及 `.png` `.jpg` `.webp` `.bmp` `.tif` | pdf 用 `pypdf`，docx 用 `python-docx`，xlsx 用 `openpyxl`，html 去脚本/导航/页脚；单个文件上限 20MB |
| 添加网址 | 抓取页面正文（最多 3MB） | 去标签后存文本；PDF 与图片链接也支持 |
| 粘贴文本 | 任何手头已有的介绍、参数、案例 | 直接在界面上粘 |

### OCR（图片与扫描件）

图片和不带文字层的 PDF 会自动做 OCR，**全部在本机跑，不联网**：

- 引擎：`rapidocr_onnxruntime`（ONNX）+ `PyMuPDF` 渲染页面；中文识别实测可用。
- 成本：本机约 **8 秒/页**，所以 OCR **在后台线程里跑**：素材先以 `OCR 识别中` 入库，界面每 4 秒自动刷新直到完成，不会把上传请求卡住。
- 限额：单份最多识别前 6 页（`GEO_OCR_MAX_PAGES`），渲染 150 DPI（`GEO_OCR_DPI`）；整开关 `GEO_OCR=0`。
- 识别结果会明确标注：`OCR 识别 N 页，可能有错字`。实测会把「已通过 ISO 9001 认证」识成「已通过ISO9001认证」（空格不可靠），所以不能拿 OCR 结果当逐字引文。
- 服务重启后不可能还有 OCR 线程在跑，启动时会把 `ocr_pending` 重置为 `pending` 并提示重新提取。

素材列表逐条显示状态：`已提取 N 字` / `OCR 识别中` / `没提取到文字` / `提取失败（原因）` / `需人工说明`，每条可重新提取、下载原件、删除。

生成的机制（不编造事实）：

- 从素材原文里挑出「像事实」的短行（含数字、年份、资质、交付类关键词），逐字放进稿件的小节里，**每条都标出来自哪个素材**；
- 素材索引自动写成稿件的「来源」，形如 `文件：产品说明书.docx（已提取 3200 字，待人工确认）`；
- 没有可用素材时，稿件会写明「当前没有可用的素材」，而不是编一个来源；质量门禁也会拦住不让审核。
- 素材还在 OCR 中时生成：生成结果会给出警告「还有 N 个素材在 OCR 识别中，本次生成没有用到它们」。

边界（必须知道）：

- **素材不是已验证事实。** 它只是写作输入；审核时仍需人工核对口径、时效与可公开范围。
- 抓取是普通 HTTP，不带登录态；需要登录或纯前端渲染的页面会返回「没提取到文字」，此时请改用粘贴文本。
- 默认**不抓本机/内网地址**（防误把本机服务当素材）；如确需抓内网，设 `GEO_ALLOW_LOCAL_FETCH=1`。这是字符串判断，不做 DNS 解析。
- 素材的增删**不会**让已通过的审核失效（它不改变已审的正文与事实）；重新生成会把稿件退回草稿，需要重新审核。

### 生成引擎

有两个引擎，产出差很多；界面会直接告诉你现在用的是哪个，按钮文案也跟着变。

| 引擎 | 触发条件 | 产出 | 能不能直接发 |
|---|---|---|---|
| 本地引擎 | 默认（未配模型） | **素材整理稿**：按模块分组、同类条目合并、带来源的结构化摘录 | 不能。按钮写的就是「整理素材」 |
| 模型引擎 | 配了 `GEO_CONTENT_LLM_*` | **初稿**：可编辑、可发布的成文 | 需人工核对后审核 |

**本地引擎为什么写不出成稿：** 它只能拼模板和摘录，没有写作能力。但它做的事并不无用——把上传的表格/文档/网页整理成可核对的结构：例如 15 行功能清单归成 4 个模块、三句同模板的说明合并成一条「（适用于：A、B、C 共 3 项）」、并保留功能名。它不编造事实，也不把素材里没有的资质、案例、报价写进去。

**两类产出都不会做的事：** 不把面向写作者的话写进正文。写作任务书、「待确认」标记、「下一步」建议、本地引擎说明，全部放在**独立的「生成说明」区域**，与正文分开。

配置模型：**在界面上填就行**。打开「模型与平台」页，或在诊断问题配置页点「配置模型」→ 选常用端点（DeepSeek / MiniMax / Moonshot / 自定义）→ 填模型名与 API Key → 先点「测试连接」确认，再保存。该配置可被内容初稿和诊断问题候选复用，但两者状态分开显示；没有模型也不影响使用工作台。

诊断问题候选只发送主体名称、业务、地区、目标客户及用户填写的可选场景资料到已配置的 OpenAI 兼容端点。模型返回必须是八题 JSON，系统会解析、最多修复一次并执行本地质量门禁；失败就使用本地 fallback。模型不会接收或决定平台执行结构，也不会自动提交问题。生成问法不等于事实核验，冻结前仍须人工编辑、保存和确认。

- 密钥存在本机 SQLite（`workspace_preferences`），接口只回是否已配置 + 掩码，**永不回显明文**；
- 环境变量 `GEO_CONTENT_LLM_BASE_URL` / `GEO_CONTENT_LLM_MODEL` / `GEO_CONTENT_LLM_API_KEY` 优先于界面设置，方便临时覆盖；
- 保存后立即生效，**不用重启服务**（配置在调用那一刻才读）；
- 「测试连接」只发一句问候，不发任何素材内容。

未配置时不会报错，也不会把整理稿伪称为初稿：`generation_meta_json` 记录实际使用的引擎与模型。重新生成会覆盖正文，因此必须显式确认覆盖。

> **配模型后素材会发往该端点。** 传公司内部资料前，先确认这个外发范围你能不能接受。

另外，提示词里**不会出现素材的文件名或网址**——实测过一次：把文件名当素材标题送进去，模型就把「内部文档名」写进了正文。现在素材一律用「素材 1 / 素材 2」的中性编号传入，文件名只留在「来源」区。

### 从内容库到发布

发布只接受**已审核的当前版本**（批量发布页也只列已审核内容）。这条前置条件在界面上是明说的，不会留一个点不动的入口：

- 未审核时，内容库里的「发布安排」是**禁用按钮**，旁边写「需先通过审核才能发布；未审核时批量发布页不会列出它」；
- 查看模式也直接给「审核当前版本」（只要质量门禁通过），不用先进编辑页；
- 审核通过后它变成真链接，并带上 `&asset=`；到了批量发布页会**自动勾上这篇**；
- 直接手改地址带着未审核的稿件进发布页，页顶会出现一条提示条：《标题》还没有通过审核，所以不在这里，并给一个回内容库审核的链接——不会“就这么少一条”。

### 质量门禁

审核和发布前都会跑同一套检查（`content_generation.quality_check`）。

阻断项：

- 标题为空，或仍是「待填写 / 待补充 / 补充问题相关的事实与内容」这类工作指令；
- 正文为空，或包含「待补充 / 待填写 / TODO」等占位内容；
- 正文有效长度（去掉 Markdown 与链接后）低于渠道下限：公众号/官网/知乎 400 字，小红书 120 字，其他 300 字；
- 事实依据为空，或没有任何链接、`来源：` / `出处：` / `文件：` / `报告：` 标记。

提示项（不阻断）：缺目标读者/写作目标/摘要、缺小节标题、关键词未出现在正文。

**发布前会重新检查**，因此旧数据即使曾被宽松门禁放行，也不会被当作可发布内容送出去。

### 相关接口

- `GET /api/content-llm`：内容模型配置状态（含掩码，不回显密钥）
- `PUT /api/content-llm`：保存接口地址 / 模型名 / API Key（空值表示保持原值）
- `POST /api/content-llm/check`：用一次最小调用验证连通性，不发素材
- `DELETE /api/content-llm`：清除配置，回到本地整理模式
- `GET /api/projects/{slug}/library`：内容库清单（含序号/字数/生成时间/素材数，不含正文）
- `DELETE /api/projects/{slug}/content/{id}`：删除内容（连素材原件与发布记录，关联的改善任务回到待处理）
- `GET /api/projects/{slug}/content/{id}/context`：证据包、素材列表、生成能力、质量报告、上次生成记录
- `POST /api/projects/{slug}/content/{id}/generate`：生成或重新生成初稿（已有正文时需显式 `confirm_overwrite`）
- `PUT /api/projects/{slug}/content/{id}`：保存任务书或正文；字段可选，未提交的字段保持原值
- `POST /api/projects/{slug}/content/{id}/review`：审核当前版本（质量门禁未通过时拒绝）
- `POST /api/projects/{slug}/content/{id}/sources/file`：上传附件（multipart，字段名 `files`，可多个）
- `POST /api/projects/{slug}/content/{id}/sources/url`：添加网址并抓取
- `POST /api/projects/{slug}/content/{id}/sources/note`：粘贴文本
- `GET /api/projects/{slug}/content/{id}/sources`：素材列表（不回传全文）
- `POST /api/projects/{slug}/content/{id}/sources/{source_id}/refresh`：重新抓取/重新提取/重新 OCR
- `DELETE /api/projects/{slug}/content/{id}/sources/{source_id}`：删除素材（连同磁盘原件）
- `GET /api/projects/{slug}/content/{id}/sources/{source_id}/download`：下载原件

---

## 发布平台适配器

十二个平台的唯一定义在 `publish_adapters.py`：每个平台声明接入方式、需要的内容要素、需要的凭据与入口。

| 平台 | 接入方式 | 实现状态 | 需要内容要素 |
|---|---|---|---|
| 微信公众号 | 官方 API | `wechat_mp.py` 调 `cgi-bin/draft/add` 写入草稿箱 | 标题、正文 |
| 知乎 / 百家号 / 今日头条 / CSDN / 搜狐号 / 大鱼号 / 博客园 | 浏览器自动化 | `browser_publisher.py`，复用隔离浏览器 profile | 标题、正文 |
| 小红书 | 浏览器自动化 | 同上 | 标题、正文、**封面图** |
| 官网 / 列举网（广州） | 人工发布 | 无通用接口，回填链接；列举网还需按城市和分类填写信息 | 标题、正文 |
| 百度百科 | 人工发布 | 只有合作渠道 | 标题、正文、**事实依据** |

状态语义（不把「点了按钮」当成功）：

- `draft_created`：公众号接口明确返回 `media_id`。**草稿 ≠ 群发上线。**
- `submitted`：浏览器自动化点到了保存/发布按钮**且**页面出现成功字样，仍要求人工去平台后台核验。
- `manual_required`：缺内容要素、编辑器定位不到、未登录、无自动接口、未配置凭据——一律附原因与截图。
- `failed`：接口报错或自动化异常，附原始错误（凭据不会出现在消息里）。

接入新平台三步：`PLATFORM_SPECS` 加一条 → 实现 `PublishAdapter` 子类并登记到 `REGISTRY` → 浏览器平台再在 `browser_publisher.TARGETS` 补入口和选择器；分类信息平台若缺少通用文章字段，应先按人工发布接入，避免误自动提交。

相关接口：

- `GET /api/platforms`：诊断平台 + 发布平台的完整契约
- `GET /api/projects/{slug}/publishing/capabilities?asset_id=`：各平台对某条内容的就绪情况（含缺失要素）
- `PUT /api/platforms/{platform_id}/credentials`、`POST /api/platforms/{platform_id}/credentials/check`：填写与校验（调 `cgi-bin/token` 验证，不落日志）
- `POST /api/projects/{slug}/publish`：执行发布（按内容 × 平台逐条返回结果）
- `POST /api/projects/{slug}/publishing/{identity}/receipt`：人工回填发布链接

### 平台登录窗口与登录态识别

点「打开登录窗口」→ 在隔离浏览器里逐个打开平台**编辑器页**（登录后平台会把你带回编辑器）→ 你在窗口里自己登录 → 系统每 3 秒读一次页面判断状态 → 界面自动刷新；**请求的平台全部识别为已登录后窗口自动关闭**，同时释放浏览器锁。

| 状态 | 含义 | 判断依据 |
|---|---|---|
| 已登录 | 编辑器可识别，可自动发布 | 标题框与正文框都能定位 |
| 已离开登录页 | 登录可能成功，但编辑器没识别出来 | 不在登录 URL、无密码框、无登录页文案 |
| 需登录 | 还没登录 | 在登录 URL / 有密码框 / 有「扫码登录」类文案 |
| 未检测 | 还没测过，或页面没打开 | 空白页，或正文过短 |

判定偏保守：**没有「编辑器可识别」这级证据时绝不报已登录**，宁可报「已离开登录页」让人去核。浏览器被占用时不抢 profile，直接提示先关窗口。

---

## 测试

```bash
# 平台：存储、API、编辑流程、内容生成与质量门禁、公众号封面、素材解析与抓取、
#       引擎门禁、文案体检、平台登录、端口探测
python -m unittest test_workspace_core test_workflow_api test_editorial_flow \
                   test_content_generation test_cover_gen test_source_materials \
                   test_engine_gates test_report_text_check test_platform_login test_port_probe \
                   test_deployment test_frontend_server test_start_stop

# 浏览器流程（会起临时前端/API 与临时浏览器，不向外部平台发送任何问题）
python test_browser_flow.py

# 引擎自身
cd skill/geo-diagnosis-single && python -m unittest discover -s tests
```

当前规模（2026-09-26）：平台侧 **176 项**、引擎侧 **67 项**，全部通过；隔离浏览器验收 **14 项**通过（`page_errors` 为空、外部提问 0、未触碰真实数据）。

测试一律使用临时数据目录、临时端口与临时浏览器，不接触真实项目数据；清理只作用于自身临时目录。

`test_content_generation.py` 里的远端模型用例一律 mock HTTP，不对外发送任何请求；未配置模型时也断言不会调用远端。`test_source_materials.py` 的网址抓取用**本机临时 HTTP 服务**，OCR 用例用**真实中文图片与真扫描件 PDF**（本机识别，不联网）；没有 RapidOCR 的机器会跳过这些用例并说明原因。

浏览器验收覆盖到内容生成：诊断证据包 → 生成初稿 → 质量门禁拦住占位稿 → 显式确认覆盖 → 审核 → 发布。

---

## 验证状态与已知限制

已验证：

- 平台侧：配置修订乐观锁、跨项目访问隔离、确认删除、冻结配置哈希不可篡改、项目级活动批次锁、发布审核门禁、发布快照与人工回执、进程锁、登录失败门禁。
- 内容生产：诊断选题按问题聚合、证据包快照、素材解析（pdf/docx/xlsx/html/文本）与网址抓取、图片与扫描件 OCR（后台线程）、本地证据安全稿生成、生成覆盖确认、版本冲突拒绝、质量门禁（占位/短正文/伪来源）在审核与发布两处生效、内容库列表与删除、旧数据迁移保留且可重复执行。
- 已用**真实数据库副本**验证：迁移保留 3 条内容 / 8 条改善任务 / 2 条发布记录，且实时库中那条曾被宽松门禁放行的已审核内容（4 字正文 + 伪来源）现在会在发布前被拦住。
- 浏览器流程：创建项目 → 资料 → 问题 → 冻结 → 刷新；合成证据 → 改善 → 证据包 → 上传附件/粘贴素材 → 生成初稿 → 质量门禁 → 审核 → 人工安排 → 回执；项目上下文与跨项目隔离。
- 引擎：`update-task-state.ps1` 拒绝 `pending → success`、拒绝缺观测引用的成功；报告渲染与 `validate-run.ps1` 校验；段落级文案体检。
- 已跑通一次「真实平台执行 → 证据落盘 → 报告生成 → 生成改善任务」闭环。**具体主体名称、批次 ID 与诊断数值只留在本机 `data/`，不写入仓库。**

限制与未验证项：

- **登录之后的编辑器选择器未逐个实跑确认**：现有 8 个浏览器平台的实测结果曾全部落在登录页；登录后能否定位编辑器、能否真的存草稿，必须逐个验证。跑不通只会转 `manual_required` 并留截图，不会静默失败。博客园本次沿用同一保守策略。
- **公众号 API 从未用真实 AppID/AppSecret 调通**（单测里是 mock HTTP 客户端），且需要该出口 IP 在公众号后台白名单、账号有草稿接口权限。
- 这类中文平台会因无头标识拒绝响应：检查与发布必须带真实 Chrome UA，否则页面直接超时。
- 登录预检是启发式判断，可能误判，需要现场核验。
- 改善项的自动提取目前只覆盖「成功回答中未提及主体」；事实准确性、权威性与结构问题尚未覆盖，不能称完整原因分析。
- 八题建议当前针对**服务型业务**；产品 / 个人 / 案例类主体需人工调整题目。
- **内容生成不替代人工事实核验**：本地引擎产出的是素材整理稿（分组摘录），它不写营销文案；配了模型也只是写作，不是事实来源。资质、案例、客户、价格、效果、承诺仍需人工逐条确认。
- **素材整理是机械启发式**：靠「取值少的列」猜分组列、靠「前 18 字相同」合并同类项。列结构奇怪的表格会分组不准，需人工在编辑页调整。
- **素材解析只做文本提取**：不做事实核对、不做单位/时效校验，也不判断素材能不能公开。
- **OCR 结果不能当逐字引文**：本机实测约 8 秒/页，且会把空格吃掉（「ISO 9001」→「ISO9001」）；单份最多识别前 6 页，超出部分不认。
- **抓取不带登录态**：需要登录或纯前端渲染的页面抓不到文字，会明确报因，改用粘贴文本。
- **手填事实依据已移除**：事实与来源只能来自素材与项目资料；如果素材都不能用，稿件就过不了门禁（这是故意的，避免手写一个无法核对的来源）。
- **没有联网核验事实的环节**：质量门禁只校验「有没有出处标记」，不校验链接是否可达、内容是否与正文一致。
- **一稿一平台**：同一篇正文可投多个平台，但还没有平台专属的二次改写（公众号、小红书、知乎的结构差异靠人工）；封面图只能手填地址或路径，不能生成。
- 生成结果依赖项目资料完整度；资料不足时稿件会偏通用——这是有意留白，不是 bug。
- 每批次只提交一次、不复测，因此结论是**时点快照**，不代表趋势或因果。
- 多租户、账号系统、云部署、公网安全加固均未实现。

---

## 许可与致谢

本项目使用 **[PolyForm Noncommercial License 1.0.0](LICENSE)**（条款全文 `LICENSE`，中文说明 `LICENSE.zh.md`）。

| | |
|---|---|
| ✅ 可以直接做 | 非商业目的的**复用、修改、二次分发**（个人学习/研究/实验/业余项目，以及非营利组织、教育机构、政府机构等使用）。条件：保留 `LICENSE` 全文与其中的 `Required Notice` 版权行 |
| ⚠️ 需事先书面授权 | **商业用途**：对外提供付费产品/服务（含 SaaS）、接入收费业务、集成进收费产品、为客户交付、公司内部用于自身业务运营 |
| ℹ️ 性质 | 这是「**源码可见 + 非商用**」（source-available），**不是 OSI 认可的开源许可**；想商用请在本仓库提 Issue 联系所有者 |

软件按「现状」提供，不提供任何担保（见 LICENSE 的 No Liability 段）。

致谢：

- 方法论参考了 [GEORank](https://github.com/yaojingang/GEORank)（Apache-2.0）的模块划分，仅借鉴划分方式，未复制其实现；对应说明见 `skill/geo-diagnosis-single/references/georank-7-module-alignment.md`。
- 前端版面遵循 `anti-slop-preflight` 规范（禁止等宽卡片堆叠、emoji 当 UI 图标、居中堆叠）。
