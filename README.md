# geo-diagnosis-single（版本化副本）

这是本机 `~/.agents/skills/geo-diagnosis-single/` 那份 skill 的版本控制副本。
**实际被调用的运行副本仍是本机那一份**；这里的 git 历史用来记录 skill 脚本的改动。

用法与流程见 `SKILL.md`。上游调用方：`E:\GEO\geo-platform-redesign-v1`（GEO 工作空间平台）。

## 约定

- 报告里的行业/场景/渠道措辞只能来自本批次的 `frozen-config.json`（`brand` 事实 + 问题原文），
  渲染器不得写死任何行业假设。生成时会写 `report/TEXT_CHECK.md` 做文案体检；
  命中「与本主体无关的行业词」判 FAIL，平台侧据此把批次标为 `degraded`。
- `.gitignore` 排除 `backups/`（改前副本）与运行产物；真实主体资料只留本机。
