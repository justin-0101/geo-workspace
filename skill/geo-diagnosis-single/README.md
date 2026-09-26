# geo-diagnosis-single（GEO 一次性诊断引擎）

GEO 一次性诊断的执行引擎：浏览器驱动、任务状态机、报告渲染、拓词与资产工具。
由本仓库根目录的 `geo-workspace` 平台以子进程方式调用。

用法与流程见 `SKILL.md`。

## 两个路径，同一份文件

| 路径 | 用途 |
|---|---|
| `skill/geo-diagnosis-single/`（本目录） | 唯一真源，随仓库版本化 |
| `~/.agents/skills/geo-diagnosis-single/` | 指向本目录的 Windows 目录联接（junction），供 agent 的 skill 发现机制读取 |

平台解析顺序：`GEO_REDESIGN_ENGINE` 环境变量 → 本目录 → 已安装的 `~/.agents/skills/...`（见 `browser_engine._default_scripts`）。
所以换机器或联接失效时，平台仍能用仓库内这份跑，只是 agent 的 skill 列表会看不到它。

重建联接：

```powershell
cmd /c mklink /J "$env:USERPROFILE\.agents\skills\geo-diagnosis-single" "E:\GEO\geo-platform-redesign-v1\skill\geo-diagnosis-single"
```

## 约定

- 报告里的行业/场景/渠道措辞只能来自本批次 `frozen-config.json` 的 `brand` 事实 + 问题原文，
  渲染器不得写死任何行业假设。生成时会写 `report/TEXT_CHECK.md` 做文案体检；命中与本主体
  无关的行业词判 FAIL，平台据此把批次标为 `degraded`，不得直接对外交付。
- `backups/`（改前副本，含 2026-09-26 修掉跨主体文案前的旧脚本）、`__pycache__/` 不入库。
- 真实主体资料只留本机，不进仓库。
