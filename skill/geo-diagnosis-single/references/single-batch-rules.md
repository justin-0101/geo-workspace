# GEO 稳定单批执行规则

## 执行边界

- 每个公司工作区一次只允许一个活动 run。
- 任务量固定为：8 道问题 × 配置平台数 × 1 次重复。默认平台为 DeepSeek、豆包、千问、秘塔，共 32 个任务。
- 每题每平台只提交一次，不追问、不改写、不点击重新生成、不失败重试。
- 每条任务必须新会话，按 question-major 顺序执行：Q01 跑完全部平台后再进入 Q02。
- 遇到验证码、扫码、短信验证、登录失效、账号异常、风控或页面结构不可靠，立即停止整批，标记人工处理。

## 证据要求

每条任务完成后按顺序落盘：

1. 完整回答文本：`raw/<task_id>.txt`
2. 截图证据：`evidence/<task_id>_top.png`、`evidence/<task_id>_full.png`
3. 结构化 observation：`observations.jsonl`
4. 状态更新：`tasks.jsonl` 和 `state.json`
5. 若页面展示来源链接：追加 `citations.jsonl`

最终报告必须能从结论追溯到 `task_id`、原始回答和截图；来源引用分析必须能追溯到 `citations.jsonl` 的 `citation_id` 和 `record_hash`。

## 禁止替代

不得用以下方式替代平台真实产品入口：

- web_search / fetch_content
- 搜索引擎结果页
- 模型 API
- Pi 自身模型回答
- 其他平台的回答

## 无官方页面公司

若公司确无官网、公众号、认证主页或其他官方页面：

```json
"brand": {
  "official_domains": [],
  "official_pages": [],
  "no_official_web_presence": true
}
```

报告必须说明：官方来源引用无法命中官方域名；该项不得解释为平台必然未引用官方资料。

## 单批锁

`initialize-run.ps1` 会扫描当前工作区的 `runs/`。若发现任何 run 仍有 `pending`、`running` 或 `manual_required`，会拒绝创建新 run。需要先完成、验收或人工归档旧 run，再创建下一批。
