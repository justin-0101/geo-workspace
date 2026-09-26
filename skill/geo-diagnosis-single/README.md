# geo-diagnosis-single

GEO 一次性诊断的执行引擎：浏览器驱动、任务状态机、报告渲染、拓词与内容资产工具。

入口文档是 **`SKILL.md`**（流程、约束、命令都在那里）。本文件只说明它在仓库里的位置与用法边界。

## 两种用法

1. **被平台调用**：仓库根目录的 `geo-workspace` 平台通过子进程调用本目录的脚本
   （`geo_driver.py` 环境检查、`geo_run.py` 执行 8 题 × N 平台、`render-report.py` 渲染报告、
   `validate-run.ps1` 校验证据完整性）。路径解析顺序：
   `GEO_REDESIGN_ENGINE` 环境变量 → 仓库内 `skill/geo-diagnosis-single/scripts` → `~/.agents/skills/geo-diagnosis-single/scripts`。
2. **独立 agent skill**：本目录本身就是一个可用的 skill。把它放到 agent 的 skill 目录下即可，
   例如在 Windows 上用目录联接（junction）指向仓库内这份，避免出现两份会分叉的副本：

   ```powershell
   cmd /c mklink /J "$env:USERPROFILE\.agents\skills\geo-diagnosis-single" "<repo>\skill\geo-diagnosis-single"
   ```

   换机器或联接失效时，平台仍能用仓库内这份跑，只是 agent 的 skill 列表里看不到它。

## 目录

```
SKILL.md            流程与约束（入口）
REVIEW-CHECKLIST-*  交付前复核清单
scripts/            浏览器驱动、执行器、报告渲染、拓词、资产矩阵、校验脚本
templates/          配置模板与产出物模板
references/         方法论参考（问题设计、关键词拓词、优化框架、单批规则）
tests/              引擎自身测试
```

## 一条硬约束：报告文案只能来自本主体

报告里的行业、场景、渠道措辞**只能**来自本批次 `frozen-config.json` 的 `brand` 事实
（`canonical_name` / `business` / `region` / `audience`）与问题原文。渲染器里写死任何行业假设，
都会让 A 主体项目的报告里出现 B 行业的内容——这类事故已经发生过一次。

因此：

- 渲染时同步生成 `report/TEXT_CHECK.md` 文案体检；命中「与本主体无关的行业词」判 `FAIL`，
  平台据此把批次标为 `degraded`，不得直接对外交付。
- 体检用的行业词表**只用于检测**，不得用于生成文案。
- 缺业务词时拓词工具只用占位符（`业务词待填`），不会拿某个行业的词兜底。

## 测试

```bash
cd skill/geo-diagnosis-single
python -m unittest discover -s tests
```

当前 67 项，全部通过（2026-09-26）。测试使用临时目录与合成数据，不向外部平台发送任何问题。

## 不入库

- `backups/`：改前脚本与改前报告副本，只留本机。
- `__pycache__/`。
- 真实主体资料：只留本机 `data/`，不写进仓库。

## 许可

本目录是 [geo-workspace](https://github.com/justin-0101/geo-workspace) 仓库的一部分，
适用仓库根目录的 `LICENSE`（PolyForm Noncommercial License 1.0.0）：
非商业用途可复用、修改、二次分发（需保留许可文本与 `Required Notice` 版权行）；
商业用途需事先取得仓库所有者的书面授权。
