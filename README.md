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
| 改善任务 | 只从「有完整证据的成功观测」生成改善项；不把执行失败当作品牌不可见 |
| 内容生产 | 从改善任务生成，或直接创建；正文 + 事实来源齐备并人工审核后才可发布 |
| 批量发布 | 十个平台的适配器：公众号走官方 API 写草稿箱，其余走浏览器自动化或人工发布回执 |
| 报告 | 报告正文在线阅读；截图在结果区显示，其他证据可下载 |

---

## 仓库结构

```
geo-platform-redesign-v1/            # 本仓库根目录 = 平台本体
├─ start.py / serve.py               # 启动器（拉起就返回 / 前台常驻）
├─ frontend_server.py                # 4173 前端白名单服务
├─ workflow_api.py                   # 8798 FastAPI（项目/资料/冻结/执行/内容/发布）
├─ workspace_store.py                # SQLite 存储与增量迁移
├─ diagnosis_config.py               # 诊断平台、八题生成与冻结配置校验
├─ diagnosis_runs.py                 # 冻结批次、任务矩阵、配置哈希与目录归属校验
├─ browser_engine.py                 # 调用引擎子进程：环境检查/执行/报告/浏览器锁
├─ editorial_flow.py                 # 改善项 → 草稿 → 审核 → 发布
├─ publish_adapters.py / wechat_mp.py / browser_publisher.py / platform_login.py
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
- **依赖**：

```bash
pip install fastapi uvicorn playwright httpx pydantic
```

### 启动

```bash
python start.py            # 拉起就返回，服务脱离终端，日志落 data/frontend.log、data/workflow-api.log
python start.py --status   # 只查看状态
python serve.py            # 前台常驻（Ctrl+C = 前端与 API 一起停）
```

- 前端 http://127.0.0.1:4173/ ，API 127.0.0.1:8798。
- `start.py` 与 `serve.py` **不要同时用**：端口被占时前端会拒绝启动。
- 等价的手工方式：`python -m uvicorn workflow_api:app --host 127.0.0.1 --port 8798` + `python frontend_server.py`。

### 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `GEO_REDESIGN_DATA` | 数据目录（SQLite、runs、浏览器 profile、日志） | `./data` |
| `GEO_REDESIGN_ENGINE` | 诊断引擎脚本目录 | 仓库内 `skill/geo-diagnosis-single/scripts` |
| `GEO_CHROME_PATH` | Chrome 可执行文件 | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| `GEO_BROWSER_USER_DATA` / `GEO_BROWSER_PROFILE_DIR` / `GEO_CDP_PORT` | 隔离浏览器 profile 与调试端口 | 平台自动设为 `data/browser-profile` / `Default` / `9348` |
| `GEO_MIN_ANSWER_CHARS` | 回答区达到多少字符才算有效（引擎） | `400` |
| `GEO_MAX_CONSECUTIVE_INVALID` | 同一平台连续多少次无效回答就熔断该平台 | `2` |
| `GEO_PAUSE_TIMEOUT_S` / `GEO_MAX_PAUSES_PER_TASK` | 遇到平台验证时的人工接力等待与次数上限 | `1800` / `3` |

### 为什么必须用白名单服务

`frontend_server.py` 只提供 `workspace.html/css/js`，其余旧入口重定向到统一框架；`data/`、`backups/`、脚本与测试均不可下载；启动前还会检测 4173 是否已被占用。

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

1. 资料保存后生成八题建议，人工逐题确认。前五题不能带品牌名、别名或官方链接。
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

## 发布平台适配器

十个平台的唯一定义在 `publish_adapters.py`：每个平台声明接入方式、需要的内容要素、需要的凭据与入口。

| 平台 | 接入方式 | 实现状态 | 需要内容要素 |
|---|---|---|---|
| 微信公众号 | 官方 API | `wechat_mp.py` 调 `cgi-bin/draft/add` 写入草稿箱 | 标题、正文 |
| 知乎 / 百家号 / 今日头条 / CSDN / 搜狐号 / 大鱼号 | 浏览器自动化 | `browser_publisher.py`，复用隔离浏览器 profile | 标题、正文 |
| 小红书 | 浏览器自动化 | 同上 | 标题、正文、**封面图** |
| 官网 | 人工发布 | 无通用接口，回填链接 | 标题、正文 |
| 百度百科 | 人工发布 | 只有合作渠道 | 标题、正文、**事实依据** |

状态语义（不把「点了按钮」当成功）：

- `draft_created`：公众号接口明确返回 `media_id`。**草稿 ≠ 群发上线。**
- `submitted`：浏览器自动化点到了保存/发布按钮**且**页面出现成功字样，仍要求人工去平台后台核验。
- `manual_required`：缺内容要素、编辑器定位不到、未登录、无自动接口、未配置凭据——一律附原因与截图。
- `failed`：接口报错或自动化异常，附原始错误（凭据不会出现在消息里）。

接入新平台三步：`PLATFORM_SPECS` 加一条 → 实现 `PublishAdapter` 子类并登记到 `REGISTRY` → `browser_publisher.TARGETS` 补选择器。

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
# 平台：存储、API、编辑流程、引擎门禁、文案体检、平台登录、端口探测
python -m unittest test_workspace_core test_workflow_api test_editorial_flow \
                   test_engine_gates test_report_text_check test_platform_login test_port_probe

# 浏览器流程（会起临时前端/API 与临时浏览器，不向外部平台发送任何问题）
python test_browser_flow.py

# 引擎自身
cd skill/geo-diagnosis-single && python -m unittest discover -s tests
```

当前规模（2026-09-26）：平台侧 **85 项**、引擎侧 **67 项**，全部通过。

测试一律使用临时数据目录、临时端口与临时浏览器，不接触真实项目数据；清理只作用于自身临时目录。

---

## 验证状态与已知限制

已验证：

- 平台侧：配置修订乐观锁、跨项目访问隔离、确认删除、冻结配置哈希不可篡改、项目级活动批次锁、发布审核门禁、发布快照与人工回执、进程锁、登录失败门禁。
- 浏览器流程：创建项目 → 资料 → 问题 → 冻结 → 刷新；合成证据 → 改善 → 内容 → 审核 → 人工安排 → 回执；项目上下文与跨项目隔离。
- 引擎：`update-task-state.ps1` 拒绝 `pending → success`、拒绝缺观测引用的成功；报告渲染与 `validate-run.ps1` 校验；段落级文案体检。
- 已跑通一次「真实平台执行 → 证据落盘 → 报告生成 → 生成改善任务」闭环。**具体主体名称、批次 ID 与诊断数值只留在本机 `data/`，不写入仓库。**

限制与未验证项：

- **登录之后的编辑器选择器未逐个实跑确认**：7 个浏览器平台的实测结果是全部落在登录页；登录后能否定位编辑器、能否真的存草稿，必须逐个验证。跑不通只会转 `manual_required` 并留截图，不会静默失败。
- **公众号 API 从未用真实 AppID/AppSecret 调通**（单测里是 mock HTTP 客户端），且需要该出口 IP 在公众号后台白名单、账号有草稿接口权限。
- 这类中文平台会因无头标识拒绝响应：检查与发布必须带真实 Chrome UA，否则页面直接超时。
- 登录预检是启发式判断，可能误判，需要现场核验。
- 改善项的自动提取目前只覆盖「成功回答中未提及主体」；事实准确性、权威性与结构问题尚未覆盖，不能称完整原因分析。
- 八题建议当前针对**服务型业务**；产品 / 个人 / 案例类主体需人工调整题目。
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
