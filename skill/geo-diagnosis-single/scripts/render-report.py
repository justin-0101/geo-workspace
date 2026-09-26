# -*- coding: utf-8 -*-
"""Render GEO one-shot reports from tasks.jsonl + observations.jsonl.

Usage:
  python render-report.py <run_dir>

Outputs:
  report/diagnosis.md
  report/metrics.json
  report/manual-review.md
  report/optimization-plan.md
  report/QUALITY_REPORT.md
  report/TEXT_CHECK.md
  manifest.json
  citations.jsonl
"""
import json
import os
import re
import sys
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def cls(obs, key, default=""):
    c = obs.get("classification") or {}
    aliases = {
        "recommendation": ["recommendation", "recommend_strength"],
        "accuracy": ["accuracy", "description_accuracy"],
        "official_source_citation": ["official_source_citation", "source_citation"],
    }
    keys = aliases.get(key, [key])
    for k in keys:
        if k in c and c[k] not in (None, ""):
            return c[k]
    return default


def rel_join(*parts):
    return "/".join(str(p).strip("/\\") for p in parts if str(p).strip("/\\"))


def stable_file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def stable_record_hash(obj):
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def domain_of(url):
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""


def derive_citations_from_observations(run_dir, obs, questions_cfg, brand, official_domains):
    """兼容旧 run：如果 geo_run 还没写 citations.jsonl，渲染报告时从 observations.sources 回填。"""
    rows = []
    competitor_domains = []
    for item in (brand.get("known_competitors") or []):
        if isinstance(item, dict):
            competitor_domains.extend(item.get("domains") or [])
        elif isinstance(item, str):
            competitor_domains.append(item)
    for o in obs:
        q = questions_cfg.get(o.get("question_id"), {})
        for idx, src in enumerate(o.get("sources") or [], 1):
            url = src.get("url", "")
            domain = domain_of(url)
            is_official = any(od and od.lower() in domain for od in official_domains)
            is_competitor = any(cd and cd.lower() in domain for cd in competitor_domains)
            base = {
                "citation_id": f"cite-{o.get('task_id')}-{idx:03d}",
                "task_id": o.get("task_id"),
                "company_name": brand.get("canonical_name"),
                "question_id": o.get("question_id"),
                "question_type": o.get("question_type"),
                "layer": o.get("layer") or q.get("layer", ""),
                "subcat": o.get("subcat") or q.get("subcat", ""),
                "intent": o.get("intent") or q.get("intent", ""),
                "time_sensitivity": o.get("time_sensitivity") or q.get("time_sensitivity", ""),
                "trigger_intensity": o.get("trigger_intensity") or q.get("trigger_intensity", ""),
                "platform_code": o.get("platform_id"),
                "platform_label": o.get("platform_label"),
                "quote_url": url,
                "quote_title": src.get("text", ""),
                "site_name": src.get("text", ""),
                "quote_index": idx,
                "published_at": "",
                "domain": domain,
                "snippet": "",
                "is_official_domain": is_official,
                "is_competitor_domain": is_competitor,
                "is_third_party": bool(domain and not is_official and not is_competitor),
            }
            base["record_hash"] = stable_record_hash({k: v for k, v in base.items() if k not in ("citation_id", "record_hash")})
            rows.append(base)
    with open(os.path.join(run_dir, "citations.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def update_manifest(run_dir, manifest, metrics, citations):
    """把初始化 manifest 升级为可归档的数据包索引。"""
    manifest = dict(manifest)
    manifest.update({
        "schema_version": manifest.get("schema_version") or "1.0",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "reported",
        "tasks_total": metrics.get("planned_tasks", 0),
        "tasks_success": metrics.get("success", 0),
        "tasks_failed": metrics.get("failed", 0),
        "tasks_pending": metrics.get("pending", 0),
        "tasks_running": metrics.get("running", 0),
        "tasks_manual_required": metrics.get("manual_required", 0),
        "citation_records": len(citations),
        "degraded_platforms": metrics.get("degraded_platforms") or [],
        "output_files": [
            "observations.jsonl",
            "citations.jsonl",
            "report/diagnosis.md",
            "report/metrics.json",
            "report/manual-review.md",
            "report/optimization-plan.md",
            "report/QUALITY_REPORT.md",
            "report/TEXT_CHECK.md",
        ],
    })
    checksums = {}
    for rel in manifest["output_files"] + ["frozen-config.json", "tasks.jsonl", "state.json"]:
        p = os.path.join(run_dir, rel)
        if os.path.isfile(p):
            checksums[rel] = stable_file_sha256(p)
    manifest["checksums_sha256"] = checksums
    write_json(os.path.join(run_dir, "manifest.json"), manifest)
    return manifest


def render_quality_report(report_dir, metrics, tasks, obs, citations, manifest):
    obs_by_task = {o.get("task_id"): o for o in obs}
    missing_obs = [t.get("task_id") for t in tasks if t.get("task_id") not in obs_by_task]
    empty_success = [o.get("task_id") for o in obs if o.get("status") == "success" and not (o.get("response_text") or "").strip()]
    no_evidence = [o.get("task_id") for o in obs if not (o.get("evidence_files") or [])]
    duplicate_obs_hashes = [h for h, c in Counter(o.get("record_hash") for o in obs if o.get("record_hash")).items() if c > 1]
    duplicate_citation_hashes = [h for h, c in Counter(c.get("record_hash") for c in citations if c.get("record_hash")).items() if c > 1]
    citation_domains = Counter(c.get("domain") or "(empty)" for c in citations)

    lines = [
        "# GEO 诊断数据质量报告",
        "",
        f"- Run ID：`{manifest.get('run_id')}`",
        f"- 生成时间：{metrics.get('generated_at')}",
        "",
        "## 1. 完整性",
        "",
        f"- 计划任务：{metrics.get('planned_tasks', 0)}",
        f"- 终态任务：{metrics.get('terminal_tasks', 0)}",
        f"- 成功：{metrics.get('success', 0)}",
        f"- 失败：{metrics.get('failed', 0)}",
        f"- 未终态：{metrics.get('planned_tasks', 0) - metrics.get('terminal_tasks', 0)}",
        f"- observation 记录：{len(obs)}",
        f"- citation 记录：{len(citations)}",
        "",
        "## 2. 异常与缺失",
        "",
        f"- 缺失 observation 的任务：{', '.join(missing_obs) if missing_obs else '无'}",
        f"- 成功但回答为空：{', '.join(empty_success) if empty_success else '无'}",
        f"- 缺少证据文件引用：{', '.join(no_evidence) if no_evidence else '无'}",
        f"- 熔断平台：{', '.join(metrics.get('degraded_platforms') or []) if metrics.get('degraded_platforms') else '无'}",
        "",
        "## 3. 重复与哈希",
        "",
        f"- observation 重复 hash 数：{len(duplicate_obs_hashes)}",
        f"- citation 重复 hash 数：{len(duplicate_citation_hashes)}",
        "",
        "## 4. 引用来源概览",
        "",
        "| domain | 记录数 |",
        "|---|---:|",
    ]
    for domain, count in citation_domains.most_common(20):
        lines.append(f"| {domain} | {count} |")
    if not citation_domains:
        lines.append("| 无引用记录 | 0 |")
    lines.extend([
        "",
        "## 5. 质量边界",
        "",
        "- 本报告只检查数据包完整性和结构质量，不等于事实准确性背书。",
        "- 被熔断平台的指标应标记为不可用，不得写成 0。",
        "- citation 记录来自页面可见来源链接抽取；平台未展示来源或 DOM 不稳定时可能为空。",
    ])
    with open(os.path.join(report_dir, "QUALITY_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def read_degraded_platforms(run_dir):
    """读 PLATFORM_DEGRADED.md，返回被熔断的 platform_id 列表。

    熔断平台在该批次没跑完，其指标不可用，不得写成 0 或据以声称覆盖全部平台。
    """
    path = os.path.join(run_dir, "PLATFORM_DEGRADED.md")
    if not os.path.exists(path):
        return []
    ids = []
    # 不吞异常：解析失败宁可报错，也不要静默少掉熔断标记（会产出误导报告）
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*-\s*`([^`]+)`", line)
            if m:
                ids.append(m.group(1).strip())
    return ids


def platform_table(metrics):
    lines = ["| 平台 | 计划 | 成功 | 失败 | 非品牌有效回答 | 自然提及 | 明确推荐 | 官方来源引用 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    degraded = set(metrics.get("degraded_platforms") or [])
    for p in metrics["platforms"]:
        if p.get("id") in degraded:
            lines.append(f"| {p['label']} | {p['planned']} | {p['success']} | {p['failed']} "
                         f"| 不可用 | 不可用 | 不可用 | 不可用 |")
            continue
        lines.append(f"| {p['label']} | {p['planned']} | {p['success']} | {p['failed']} | {p['non_brand_success']} | {p['non_brand_mention']} | {p['non_brand_explicit']} | {p['official_citation_yes']} |")
    if degraded:
        labels = "、".join(p["label"] for p in metrics["platforms"] if p.get("id") in degraded)
        lines.append("")
        lines.append(f"> **平台熔断**：{labels} 本批次被熔断，未跑完 8 题，其指标标记为不可用；"
                     "本报告不得据此声称覆盖全部平台。详见 `PLATFORM_DEGRADED.md`。")
    return "\n".join(lines)



def _clean_fact(value, limit):
    """主体事实只允许来自本项目配置：压缩空白、去掉首尾标点、限长。

    模板占位符（CHANGE_ME / <...> / {...}）一律当作「没填」，绝不当事实写进报告。
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.strip("，。、；：,.;: 　")
    if "CHANGE_ME" in text or re.search(r"[<{][^>}]*[>}]", text):
        return ""
    return text[:limit]


def subject_facts(cfg):
    """报告里唯一允许出现的「项目自身事实」，全部来自 frozen-config。

    旧批次没有 business/region/audience 字段时返回空串：渲染器只用中性措辞，
    绝不从别处推断行业。
    （真实事故：写死的财税/代账文案出现在设备资产管理软件的报告里。）
    """
    brand = cfg.get("brand") or {}
    return {
        "name": _clean_fact(brand.get("canonical_name"), 80),
        "business": _clean_fact(brand.get("business"), 40),
        "region": _clean_fact(brand.get("region"), 20),
        "audience": _clean_fact(brand.get("audience"), 30),
    }


def suggested_asset_title(question_id, prompt, subject=""):
    """按题目自身的提问意图取标题；subject 只能来自本项目配置。

    禁止写死行业与渠道假设（本地服务商 / 全国平台 / 代账 / 自建团队…）。
    """
    text = prompt or ""
    topic = _clean_fact(subject, 40)
    if question_id == "Q01" or "推荐 3 家" in text or "推荐3家" in text or "推荐三个" in text:
        return f"{topic}怎么选：判断标准、适用场景与避坑清单"
    if question_id == "Q02" or "收费" in text or "费用" in text or "预算" in text:
        return f"{topic}费用构成与价格风险：区间、包含项与低价陷阱"
    if any(k in text for k in ("资质", "案例", "合同条款", "合同")):
        return f"{topic}核验清单：资质、案例与合同条款"
    if any(k in text for k in ("自建", "专职", "全职", "自行处理", "自己处理")):
        return "自行处理与委托外部团队：不同阶段的选择边界"
    if any(k in text for k in ("风险", "异常", "注销", "变更", "合规")):
        return f"{topic}常见问题与处理流程：原因、影响与整改方案"
    if question_id == "Q08" or "对比" in text or "利弊" in text or "比较" in text:
        return "与同类方案的对比：利弊分析与决策建议"
    return f"围绕 {question_id} 的高意图问题解答页"


# 报告文案守卫用的「外部行业词」表：只用于【检测】，绝不用于生成文案。
# 命中规则：报告里出现了该词，而它的同义族（同一个词的常见变体）在本项目配置里一个都没有
# → 判为模板泄漏，需要人工核对。这样做是为了不误伤：财税项目的报告写「代理记账/代账」是合法的。
FOREIGN_INDUSTRY_TERMS = {
    "代理记账": ("代理记账", "代账", "记账", "账务"),
    "代账": ("代理记账", "代账", "记账", "账务"),
    "记账": ("代理记账", "代账", "记账", "账务"),
    "财税": ("财税", "财务", "税务", "涉税", "代理记账", "代账"),
    "税务": ("税务", "财税", "涉税", "纳税", "报税", "税控"),
    "纳税": ("纳税", "税务", "涉税", "报税", "小规模纳税人"),
    "报税": ("报税", "纳税", "税务", "涉税"),
    "会计": ("会计", "财务", "账务", "出纳"),
    "营业执照": ("营业执照", "工商", "注册登记", "经营范围"),
    "工商": ("工商", "营业执照", "注册登记", "经营范围"),
    "注销": ("注销", "清算", "退出登记"),
    "年报": ("年报", "年度报告", "年度公示"),
    "发票": ("发票", "开票", "票种"),
    "金税": ("金税", "税控", "税务"),
    "社保": ("社保", "社会保险", "五险一金", "公积金"),
    "公积金": ("公积金", "社保", "五险一金"),
    "自聘": ("自聘", "专职", "全职", "自建"),
    "专职会计": ("专职", "全职", "自聘", "会计"),
    "全国性平台": ("全国性平台", "全国平台", "平台型", "全国性"),
    "本地服务商": ("本地服务商", "本地公司", "本地商家", "同城", "本地"),
    "经营异常": ("经营异常", "异常名录", "地址失联"),
}

REPORT_TEXT_FILES = ("diagnosis.md", "optimization-plan.md", "manual-review.md", "QUALITY_REPORT.md")


def subject_corpus(cfg):
    """本项目自己的文本语料：品牌事实 + 本批次问题原文。

    这些词出现在报告里是合法的（财税项目本来就该写「代理记账」），
    不在语料里的行业词才可疑。
    """
    brand = cfg.get("brand") or {}
    parts = [str(brand.get(k) or "") for k in ("canonical_name", "business", "region", "audience")]
    parts.extend(str(x) for x in (brand.get("aliases") or []))
    parts.extend(str(x) for x in (brand.get("official_pages") or []))
    for q in (cfg.get("scope") or {}).get("questions") or []:
        if not isinstance(q, dict):
            continue
        parts.append(str(q.get("prompt") or ""))
        parts.extend(str(q.get(k) or "") for k in ("layer", "subcat", "intent"))
    return "\n".join(parts)


def check_report_text(report_dir, cfg):
    """扫描已生成的报告文本，把「不属于本项目」的行业词挑出来。

    无论通过与否都写 report/TEXT_CHECK.md：这是对外交付前的最后一道人工核对入口。
    返回 violations 列表（空列表表示通过）。
    """
    corpus = subject_corpus(cfg)
    violations = []
    for name in REPORT_TEXT_FILES:
        path = os.path.join(report_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        for term, family in FOREIGN_INDUSTRY_TERMS.items():
            if term not in text or any(f in corpus for f in family):
                continue
            samples = [ln.strip() for ln in text.splitlines() if term in ln][:3]
            violations.append({"file": name, "term": term, "samples": samples})

    lines = [
        "# 报告文案体检",
        "",
        f"- 状态：{'PASS' if not violations else 'FAIL'}",
        f"- 检查范围：{'、'.join(REPORT_TEXT_FILES)}",
        "- 规则：报告里出现下表行业词，而它的同义族（同一个行业词的常见变体）在本批次配置（主体事实 + 问题原文）里一个都没有，判为模板泄漏，需人工核对后才能对外交付。",
        "",
    ]
    if violations:
        lines.append("| 文件 | 命中词 | 出现位置 |")
        lines.append("|---|---|---|")
        for v in violations:
            sample = " / ".join(x[:80] for x in v["samples"]) or "（无法定位行）"
            lines.append(f"| {v['file']} | {v['term']} | {sample} |")
        lines.append("")
        lines.append("处理方式：确认该词是否与本主体无关。无关则修 `scripts/render-report.py` 的文案生成逻辑，然后重新生成报告。")
    else:
        lines.append("未发现与本批次配置无关的行业词。")
    lines.append("")
    with open(os.path.join(report_dir, "TEXT_CHECK.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return violations


def render_optimization_plan(report_dir, cfg, manifest, metrics, questions_cfg, obs):
    brand = cfg.get("brand", {})
    facts = subject_facts(cfg)
    company = facts["name"] or "目标企业"
    no_official = metrics["brand"].get("no_official_web_presence") or (not metrics["brand"].get("official_domains") and not metrics["brand"].get("official_pages"))
    nb_success = metrics.get("non_brand_success", 0)
    mentions = metrics.get("non_brand_brand_mentions", 0)
    explicit = metrics.get("non_brand_explicit_recommendations", 0)
    citation_yes = metrics.get("official_source_citation_counts", {}).get("yes", 0)
    acc_counts = metrics.get("accuracy_counts", {})

    identity_gap = "高" if no_official or acc_counts.get("unverified", 0) else "中"
    content_gap = "高" if nb_success and mentions == 0 else ("中" if explicit == 0 else "低")
    authority_gap = "高" if explicit == 0 else "中"
    structure_gap = "高" if citation_yes == 0 else "中"

    target_mentions = max(2, min(5, nb_success // 2 if nb_success else 2))
    target_recs = max(1, min(3, nb_success // 3 if nb_success else 1))
    target_citations = 1 if no_official else max(2, len(metrics.get("platforms", [])) // 2)

    lines = []
    lines.append(f"# {company} GEO 优化执行方案")
    lines.append("")
    lines.append(f"- Run ID：`{manifest.get('run_id')}`")
    lines.append("- 交付定位：本文件不是复述诊断截图，而是把诊断缺口转成企业可执行的 GEO 优化任务。")
    lines.append("")
    lines.append("## 1. 诊断后的核心判断")
    lines.append("")
    lines.append(f"- 非品牌有效回答：{nb_success} 条")
    lines.append(f"- 自然提及：{mentions} 条")
    lines.append(f"- 明确推荐：{explicit} 条")
    lines.append(f"- 官方来源引用命中：{citation_yes} 条")
    if no_official:
        lines.append("- 关键限制：当前声明无官网/公众号/官方页面，模型缺少可引用的官方事实源。")
    lines.append("")
    lines.append("## 2. 问题归因")
    lines.append("")
    lines.append("| 缺口类型 | 判断 | 表现 | 优化方向 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 身份缺口 | {identity_gap} | 品牌事实源不足或事实准确性仍需复核 | 建立统一品牌事实卡和官方/准官方主页 |")
    lines.append(f"| 内容缺口 | {content_gap} | 非品牌高意图问题中自然提及不足 | 围绕 Q01-Q05 逐题建设可被引用内容 |")
    lines.append(f"| 权威缺口 | {authority_gap} | 明确推荐不足，缺少可验证推荐理由 | 增加客户案例、服务数据、评价与第三方背书 |")
    lines.append(f"| 结构缺口 | {structure_gap} | 来源引用少，资料不够模型友好 | 用 FAQ、表格、案例块、结构化数据增强可抽取性 |")
    lines.append("")
    lines.append("## 3. 30 天优先执行清单")
    lines.append("")
    lines.append("| 优先级 | 任务 | 产出物 | 验收标准 |")
    lines.append("|---|---|---|---|")
    lines.append("| P0 | 梳理品牌标准事实卡 | 1 份公司事实卡 | 全称、简称、地址、服务区域、业务范围、服务对象、优势、联系方式统一 |")
    if no_official:
        lines.append("| P0 | 建立准官方信息源 | 官网落地页、知乎机构页、百家号等可公开访问的官方主页任选其一 | 公开可访问，能承载品牌事实卡和服务说明 |")
    else:
        lines.append("| P0 | 补强官方页面 | 官网品牌页、服务页、FAQ 页 | 页面能回答品牌题和核心服务问题 |")
    lines.append("| P0 | 发布品牌介绍页 |《公司是做什么的、服务谁、优势是什么》| 能覆盖 Q06/Q07 的事实，不靠模型猜 |")
    lines.append("| P1 | 发布 3 篇高意图内容 | 推荐/收费/风险主题文章 | 覆盖 Q01/Q02/Q05，每篇回答一个真实用户问题 |")
    lines.append("")
    lines.append("## 4. 60 天执行清单")
    lines.append("")
    lines.append("| 优先级 | 任务 | 产出物 | 验收标准 |")
    lines.append("|---|---|---|---|")
    lines.append("| P1 | 做客户案例 | 2-3 个真实案例 | 有客户类型、问题、服务过程、结果；不虚构、不夸大 |")
    lines.append("| P1 | 做对比型内容 | 与同类方案、替代方案的对比文章 | 覆盖 Q03/Q08，让模型有推荐边界 |")
    lines.append("| P1 | 第三方平台资料统一 | 企业信息、业务介绍、联系方式、服务区域同步 | 多平台 NAP 一致，减少主体混淆 |")
    lines.append("| P2 | 建 FAQ 知识库 | 10-20 个客户常问问题 | 每个问题有短答案、判断依据与注意事项 |")
    lines.append("")
    lines.append("## 5. 90 天执行清单")
    lines.append("")
    lines.append("| 优先级 | 任务 | 产出物 | 验收标准 |")
    lines.append("|---|---|---|---|")
    lines.append("| P2 | 同题复测 | 新一批 GEO 诊断报告 | 对比自然提及、明确推荐、准确性、来源引用变化 |")
    lines.append("| P2 | 扩展问题库 | 16-24 题长尾问题 | 覆盖更多服务线、区域词和高转化场景 |")
    lines.append("| P2 | 针对低表现问题补内容 | 一题一页/一题一文 | 未提及、描述错、推荐弱的问题逐条有对应页面 |")
    lines.append("")
    lines.append("## 6. 内容资产建议")
    lines.append("")
    lines.append("| 对应问题 | 建议内容标题 | 目标 | 推荐发布位置 |")
    lines.append("|---|---|---|---|")
    for qid in sorted(questions_cfg):
        q = questions_cfg[qid]
        qtype = q.get("type")
        if qtype not in ("non_brand", "comparison"):
            continue
        title = suggested_asset_title(qid, q.get("prompt", ""), facts["business"])
        target = "争取自然提及/推荐" if qtype == "non_brand" else "建立对比场景下的选择理由"
        channels = "官方/准官方主页、知乎、百家号等内容平台"
        if facts["region"]:
            channels += f"、{facts['region']}本地信息平台"
        lines.append(f"| {qid} | {title} | {target} | {channels} |")
    lines.append("")
    lines.append("## 7. 下次复测验收目标")
    lines.append("")
    lines.append(f"- 非品牌题自然提及：目标 ≥ {target_mentions} 条")
    lines.append(f"- 明确推荐：目标 ≥ {target_recs} 条")
    lines.append("- 品牌题准确性：目标全部达到 accurate 或 partly_accurate")
    lines.append(f"- 官方/准官方来源引用：目标 ≥ {target_citations} 条")
    lines.append("- 主体混淆、错误事实：目标 0 条")
    lines.append("")
    lines.append("## 8. 交付给企业时的沟通口径")
    lines.append("")
    lines.append("不要只说“AI 没有推荐你”，而要说：模型推荐依赖公开、稳定、可引用的信息源；现在缺的是让模型敢引用、能复述、能比较、能推荐的内容资产。优化目标不是操纵模型，而是把企业真实优势结构化、公开化、可验证化。")
    lines.append("")

    # ---- v2.0 新增段 9-12:GEORank 对齐 / AI 友好度 / 工具产出物 / 拓词建议 ----
    # 段 9:GEORank 7 模块对齐
    lines.append("## 9. GEORank 7 模块对齐(v2.0 新增)")
    lines.append("")
    lines.append("借鉴 GEORank(yaojingang/GEORank,Apache 2.0)7 大模块方法论:")
    lines.append("")
    lines.append("| 模块 | skill 状态 | 本次诊断缺口 | 下一阶段补强动作 |")
    lines.append("|---|---|---|---|")
    lines.append("| 1. 发现 | 部分对齐 | 业务词盘点不够 | 用拓词模板补 4 层关键词 |")
    lines.append(f"| 2. 诊断 | 已对齐 | { 'Schema/Meta/可读性需补强' if citation_yes == 0 else '诊断齐备' } | 跑阶段 G 生成 AI 友好度评分 |")
    lines.append("| 3. 问答 | 已对齐 | — | 保持现有 32 任务 |")
    lines.append(f"| 4. 规划 | 已对齐 | { '30/60/90 计划需细化为工具产出物' if no_official else '规划齐备' } | 按本报告 30/60/90 执行 |")
    lines.append(f"| 5. 拓展 | 新增 | { '无拓词清单' if not Path(os.path.join(report_dir, '..', 'config', 'expanded-questions.json')).exists() else '已拓词' } | 跑 expand-keywords.py |")
    lines.append(f"| 6. 结构化 | 新增 | { '缺 JSON-LD/llms.txt' if not Path(os.path.join(report_dir, '..', 'assets', 'schema.jsonld')).exists() else '已生成 JSON-LD' } | 跑 generate-schema.py |")
    lines.append("| 7. 管理 | 不对齐 | — | 本 skill 是单批执行器,不引入管理后台 |")
    lines.append("")

    # 段 10:AI 友好度评分(读 report/ai-friendliness.json)
    lines.append("## 10. AI 友好度评分(v2.0 新增)")
    lines.append("")
    ai_path = os.path.join(report_dir, "ai-friendliness.json")
    if os.path.isfile(ai_path):
        try:
            with open(ai_path, encoding="utf-8-sig") as f:
                ai_score = json.load(f)
            lines.append(f"- 总分:**{ai_score.get('total_score', 0)} / 100**")
            lines.append(f"- 趋势:{ai_score.get('trend', 'stable')}")
            lines.append("")
            lines.append("| 维度 | 评分 | 权重 | 加权 | 证据 |")
            lines.append("|---|---:|---:|---:|---|")
            for dim_key, dim in (ai_score.get("dimensions") or {}).items():
                ev = "; ".join((dim.get("evidence") or [])[:2])
                lines.append(f"| {dim_key} | {dim.get('score', 0)} | {dim.get('weight', 0)*100:.0f}% | {dim.get('weighted_score', 0)} | {ev} |")
            lines.append("")
            recs = ai_score.get("recommendations") or []
            if recs:
                lines.append("**改进建议**:")
                for rec in recs:
                    lines.append(f"- {rec}")
                lines.append("")
        except (json.JSONDecodeError, OSError):
            lines.append("(ai-friendliness.json 解析失败)")
            lines.append("")
    else:
        lines.append("(未生成 ai-friendliness.json,跑 `python scripts/ai-friendliness-score.py <run_dir>` 后重新生成报告)")
        lines.append("")

    # 段 11:GEO 工具产出物清单
    lines.append("## 11. GEO 工具产出物清单(v2.0 新增)")
    lines.append("")
    assets_dir = os.path.join(report_dir, "..", "assets")
    line_items = []
    for fname, desc in [
        ("schema.jsonld", "JSON-LD(5 个 @type)"),
        ("llms.txt", "llms.txt(7 段结构)"),
        ("company_kb.md", "知识库草稿"),
    ]:
        p = os.path.join(assets_dir, fname)
        if os.path.isfile(p):
            line_items.append(f"- ✅ `{fname}` — {desc}")
        else:
            line_items.append(f"- ⬜ `{fname}` — {desc}(未生成,跑 generate-schema.py)")
    lines.extend(line_items)
    lines.append("")
    for fname, desc in [
        ("ai-friendliness.json", "6 维度评分"),
        ("content-asset-list.csv", "内容资产矩阵"),
        ("../config/expanded-questions.json", "拓词后长尾题库"),
    ]:
        p = os.path.join(report_dir, fname)
        if os.path.isfile(p):
            lines.append(f"- ✅ `{fname}` — {desc}")
        else:
            lines.append(f"- ⬜ `{fname}` — {desc}(未生成)")
    lines.append("")

    # 段 12:拓词建议
    lines.append("## 12. 拓词建议(v2.0 新增)")
    lines.append("")
    exp_path = os.path.join(report_dir, "..", "config", "expanded-questions.json")
    if os.path.isfile(exp_path):
        try:
            with open(exp_path, encoding="utf-8-sig") as f:
                exp_data = json.load(f)
            summary = exp_data.get("summary") or {}
            lines.append(f"- 总题数:**{summary.get('total', 0)}**(基线 8 + 拓词 {summary.get('total', 0) - 8})")
            lines.append(f"- 推荐型关键词数:**{summary.get('recommendation_keyword_count', 0)}**")
            lines.append(f"- 层级分布:{summary.get('by_layer', {})}")
            if summary.get("needs_business_words"):
                lines.append("- ⚠ 业务词未配置：拓词结果里用的是占位符，请先在 keywords-expansion 配置里填 business_words，再重跑 expand-keywords.py。")
            lines.append("")
            flagged = summary.get("flagged_for_review") or []
            if flagged:
                lines.append(f"- ⚠ 待复核题目(可能含品牌词):{', '.join(flagged)}")
                lines.append("")
        except (json.JSONDecodeError, OSError):
            lines.append("(expanded-questions.json 解析失败)")
            lines.append("")
    else:
        lines.append("(未生成 expanded-questions.json,跑 `python scripts/expand-keywords.py <config.json> --out <run_dir>/config/` 后重新生成报告)")
        lines.append("")

    with open(os.path.join(report_dir, "optimization-plan.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    if len(sys.argv) < 2:
        print("Usage: python render-report.py <run_dir>", file=sys.stderr)
        return 2
    run_dir = os.path.abspath(sys.argv[1])
    cfg = load_json(os.path.join(run_dir, "frozen-config.json"))
    manifest = load_json(os.path.join(run_dir, "manifest.json"))
    tasks = load_jsonl(os.path.join(run_dir, "tasks.jsonl"))
    obs = load_jsonl(os.path.join(run_dir, "observations.jsonl"))
    citations_path = os.path.join(run_dir, "citations.jsonl")
    if not os.path.exists(citations_path):
        Path(citations_path).touch()
    citations = load_jsonl(citations_path)
    obs_by_task = {o.get("task_id"): o for o in obs}

    brand = cfg.get("brand", {})
    platforms_cfg = {p["id"]: p for p in cfg.get("platforms", [])}
    questions_cfg = {q["id"]: q for q in cfg.get("scope", {}).get("questions", [])}
    no_official = bool(brand.get("no_official_web_presence"))
    official_domains = brand.get("official_domains") or []
    official_pages = brand.get("official_pages") or []
    if not citations and any(o.get("sources") for o in obs):
        citations = derive_citations_from_observations(run_dir, obs, questions_cfg, brand, official_domains)

    status_counts = Counter(t.get("status") for t in tasks)
    planned = len(tasks)
    success = status_counts.get("success", 0)
    failed = status_counts.get("failed", 0)
    terminal = success + failed
    degraded_platforms = read_degraded_platforms(run_dir)

    by_platform = defaultdict(lambda: Counter())
    for t in tasks:
        pid = t.get("platform_id")
        by_platform[pid]["planned"] += 1
        by_platform[pid][t.get("status")] += 1

    for o in obs:
        pid = o.get("platform_id")
        if o.get("status") == "success" and o.get("question_type") == "non_brand":
            by_platform[pid]["non_brand_success"] += 1
            if cls(o, "brand_mention") == "yes":
                by_platform[pid]["non_brand_mention"] += 1
            if cls(o, "recommendation") == "explicit":
                by_platform[pid]["non_brand_explicit"] += 1
        if cls(o, "official_source_citation") == "yes":
            by_platform[pid]["official_citation_yes"] += 1

    non_brand_success = [o for o in obs if o.get("status") == "success" and o.get("question_type") == "non_brand"]
    nb_mentions = [o for o in non_brand_success if cls(o, "brand_mention") == "yes"]
    nb_explicit = [o for o in non_brand_success if cls(o, "recommendation") == "explicit"]
    official_yes = [o for o in obs if cls(o, "official_source_citation") == "yes"]
    citation_counts = Counter(cls(o, "official_source_citation", "unknown") for o in obs)
    accuracy_counts = Counter(cls(o, "accuracy", "unknown") for o in obs)

    platform_metrics = []
    for pid, pcfg in platforms_cfg.items():
        c = by_platform[pid]
        platform_metrics.append({
            "id": pid,
            "label": pcfg.get("label", pid),
            "planned": c.get("planned", 0),
            "success": c.get("success", 0),
            "failed": c.get("failed", 0),
            "non_brand_success": c.get("non_brand_success", 0),
            "non_brand_mention": c.get("non_brand_mention", 0),
            "non_brand_explicit": c.get("non_brand_explicit", 0),
            "official_citation_yes": c.get("official_citation_yes", 0),
        })

    metrics = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": manifest.get("run_id"),
        "brand": {
            "canonical_name": brand.get("canonical_name"),
            "aliases": brand.get("aliases") or [],
            "official_domains": official_domains,
            "official_pages": official_pages,
            "no_official_web_presence": no_official,
        },
        "planned_tasks": planned,
        "terminal_tasks": terminal,
        "success": success,
        "failed": failed,
        "pending": status_counts.get("pending", 0),
        "running": status_counts.get("running", 0),
        "manual_required": status_counts.get("manual_required", 0),
        "non_brand_success": len(non_brand_success),
        "non_brand_brand_mentions": len(nb_mentions),
        "non_brand_explicit_recommendations": len(nb_explicit),
        "official_source_citation_counts": dict(citation_counts),
        "accuracy_counts": dict(accuracy_counts),
        "platforms": platform_metrics,
        "degraded_platforms": degraded_platforms,
        "citation_records": len(citations),
        "citation_domain_counts": dict(Counter(c.get("domain") or "" for c in citations)),
        "limits": [
            "一次性测试时点快照，不代表趋势或因果。",
            "每题每平台只提交一次，不复测、不重试、不综合排名。",
            "准确性由脚本初判；错误事实、未核实信息和争议判定需要人工复核。",
        ] + (["存在被熔断的平台，其指标不可用；本报告不覆盖全部平台。"] if degraded_platforms else []),
    }

    report_dir = os.path.join(run_dir, "report")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    evidence_refs = lambda rows: ", ".join(o.get("task_id", "") for o in rows[:8]) or "无"
    lines = []
    lines.append(f"# {brand.get('canonical_name', '')} GEO 一次性可见度诊断")
    lines.append("")
    lines.append(f"- Run ID：`{manifest.get('run_id')}`")
    lines.append(f"- 生成时间：{metrics['generated_at']}")
    lines.append(f"- 计划任务：{planned}（{len(questions_cfg)} 题 × {len(platforms_cfg)} 平台 × 1 次）")
    lines.append(f"- 有效完成：成功 {success}，失败 {failed}，未终态 {planned - terminal}")
    lines.append(f"- 引用记录：{len(citations)} 条（详见 `citations.jsonl` 与 `report/QUALITY_REPORT.md`）")
    lines.append("- 报告边界：一次性测试时点快照；不复测、不推断趋势、不做平台综合排名。")
    if no_official or (not official_domains and not official_pages):
        lines.append("- 官方来源限制：配置声明无官网/官方页面，因此官方来源引用无法命中官方域名；该项不得解释为平台必然未引用官方资料。")
    lines.append("")
    lines.append("## 核心发现")
    lines.append("")
    lines.append(f"1. 非品牌题有效回答 {len(non_brand_success)} 条，其中自然提及目标品牌 {len(nb_mentions)} 条，明确推荐 {len(nb_explicit)} 条。证据：{evidence_refs(nb_mentions or nb_explicit)}。")
    lines.append(f"2. 官方来源引用命中 {len(official_yes)} 条；来源引用分布：{dict(citation_counts)}。")
    lines.append(f"3. 事实准确性初判分布：{dict(accuracy_counts)}；`unverified/unclear/incorrect` 均进入人工复核。")
    lines.append(f"4. 失败/异常任务 {failed} 条；如存在失败，需查看对应 raw 与截图证据，不允许重试补测。")
    lines.append("")
    lines.append("## 平台维度统计")
    lines.append("")
    lines.append(platform_table(metrics))
    lines.append("")
    lines.append("## 问题分类标签")
    lines.append("")
    lines.append("| 问题 | 类型 | layer | subcat | intent | 时间敏感度 | 触发强度 |")
    lines.append("|---|---|---|---|---|---|---|")
    for qid in sorted(questions_cfg):
        q = questions_cfg[qid]
        lines.append(f"| {qid} | {q.get('type', '')} | {q.get('layer', '')} | {q.get('subcat', '')} | {q.get('intent', '')} | {q.get('time_sensitivity', '')} | {q.get('trigger_intensity', '')} |")
    lines.append("")
    facts = subject_facts(cfg)
    lines.append("## P0 / P1 / P2 整改清单")
    lines.append("")
    lines.append("- P0：建立或补全可被模型引用的官方信息源（官网、官方公众号文章、权威平台主页等），并沉淀品牌标准名、服务范围、适用客户、案例与联系方式。")
    p1 = "- P1：围绕非品牌高意图问题建设内容资产，逐题覆盖推荐标准、费用构成、资质与合同核验、风险规避等决策环节。"
    if facts["business"]:
        p1 = (f"- P1：围绕非品牌高意图问题建设内容资产（主体业务：{facts['business']}），"
              f"逐题覆盖推荐标准、费用构成、资质与合同核验、风险规避等决策环节。")
    lines.append(p1)
    lines.append(f"- P2：建设可被引用的对比型内容与客户案例，说明{facts['name'] or '本主体'}与同类替代方案的适用边界。")
    lines.append("")
    lines.append("## 证据索引")
    lines.append("")
    for o in obs:
        q = questions_cfg.get(o.get("question_id"), {})
        files = ", ".join(f"`{x}`" for x in (o.get("evidence_files") or []))
        lines.append(f"- `{o.get('task_id')}`｜{o.get('platform_label')}｜{q.get('type', o.get('question_type'))}｜{o.get('status')}｜{files}")
    lines.append("")
    with open(os.path.join(report_dir, "diagnosis.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    manual = []
    manual.append(f"# 人工复核清单：{brand.get('canonical_name', '')}")
    manual.append("")
    if no_official or (not official_domains and not official_pages):
        manual.append("## 官方信息源缺失")
        manual.append("")
        manual.append("- 配置声明无官网/公众号/官方页面。建议人工确认是否确无可公开引用的官方信息源；若存在，补入下一批配置。")
        manual.append("")
    manual.append("## 待复核任务")
    manual.append("")
    for o in obs:
        reason = []
        if o.get("status") == "failed":
            reason.append("失败任务")
        acc = cls(o, "accuracy", "unverified")
        if acc in ("incorrect", "unverified", "unclear", "unknown"):
            reason.append(f"事实准确性={acc}")
        rec = cls(o, "recommendation")
        if rec == "unclear":
            reason.append("推荐语义不清")
        cite = cls(o, "official_source_citation")
        if cite == "unclear":
            reason.append("来源归属不清")
        if reason:
            files = ", ".join(f"`{x}`" for x in (o.get("evidence_files") or []))
            manual.append(f"- `{o.get('task_id')}`｜{o.get('platform_label')}｜{'; '.join(reason)}｜证据：{files}")
    manual.append("")
    with open(os.path.join(report_dir, "manual-review.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(manual))

    render_optimization_plan(report_dir, cfg, manifest, metrics, questions_cfg, obs)
    render_quality_report(report_dir, metrics, tasks, obs, citations, manifest)
    text_violations = check_report_text(report_dir, cfg)
    manifest = update_manifest(run_dir, manifest, metrics, citations)

    result = {"ok": True, "report_dir": report_dir,
              "files": ["diagnosis.md", "metrics.json", "manual-review.md", "optimization-plan.md",
                        "QUALITY_REPORT.md", "TEXT_CHECK.md"],
              "citation_records": len(citations),
              "text_check": {"ok": not text_violations, "violations": text_violations}}
    print(json.dumps(result, ensure_ascii=False))
    if text_violations:
        print(f"[warn] 报告文案体检 FAIL：{len(text_violations)} 处行业词不属于本批次配置，详见 report/TEXT_CHECK.md",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
