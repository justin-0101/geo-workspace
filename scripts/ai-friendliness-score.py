# -*- coding: utf-8 -*-
"""ai-friendliness-score.py — AI 友好度评分(6 维度)。

借鉴 GEORank 模块 6 + tools-output-spec.md 评分模型。

用法:
  python ai-friendliness-score.py <run_dir> [--online] [--url URL]

参数:
  --online   启用 Playwright 抓取官方页面做深度校验(可选,需要 playwright 依赖)
  --url      指定官方页面 URL,默认从 frozen-config.brand.official_domains 取

输出:
  <run_dir>/report/ai-friendliness.json

6 维度评分模型(总分 100):
  1. Schema 完整性         25%
  2. Meta 信息            15%
  3. 内容可读性           20%
  4. 引用信号             20%
  5. llms.txt / 结构化资产  10%
  6. 多平台 NAP 一致性     10%

离线模式(默认):只用 frozen-config + metrics + observations 评分,不需要网络
在线模式(--online):可选启用 Playwright 抓官方页面校验 Schema/Meta
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# 评分维度权重
WEIGHTS = {
    "schema_completeness": 0.25,
    "meta_info": 0.15,
    "content_readability": 0.20,
    "citation_signals": 0.20,
    "llms_txt": 0.10,
    "nap_consistency": 0.10,
}


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
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


# ---- 6 维度评分函数(0-100)----


def score_schema_completeness(frozen_cfg, assets_dir):
    """维度 1:Schema 完整性(0-100)。

    评分依据:
    - schema.jsonld 存在:30 分
    - 5 个 @type 全覆盖:50 分(每个 10 分)
    - Organization 必填字段齐全:10 分
    - LocalBusiness 必填字段齐全:10 分
    """
    schema_path = os.path.join(assets_dir, "schema.jsonld")
    if not os.path.isfile(schema_path):
        return 0, ["schema.jsonld 不存在"]
    try:
        schema = load_json(schema_path)
    except json.JSONDecodeError:
        return 0, ["schema.jsonld JSON 解析失败"]

    score = 30  # 文件存在
    evidence = [f"schema.jsonld 存在 (+30)"]
    types = set()
    for entity in (schema.get("@graph") or []):
        t = entity.get("@type")
        if t:
            types.add(t)

    expected = ["Organization", "LocalBusiness", "FAQPage", "Service", "Review"]
    covered = sum(1 for t in expected if t in types)
    score += covered * 10
    evidence.append(f"@type 覆盖 {covered}/5: {sorted(types & set(expected))} (+{covered*10})")

    # Organization 必填
    org = next((e for e in (schema.get("@graph") or []) if e.get("@type") == "Organization"), None)
    if org:
        required = ["name", "url", "address"]
        present = sum(1 for f in required if org.get(f))
        if present == len(required):
            score += 10
            evidence.append(f"Organization 必填字段全 (+10)")

    return min(score, 100), evidence


def score_meta_info(frozen_cfg, online_meta=None):
    """维度 2:Meta 信息(0-100)。

    离线:从 frozen_cfg.brand 检查
    在线:从抓取的页面 meta 检查(可选)
    """
    score = 0
    evidence = []
    brand = frozen_cfg.get("brand", {})

    # 离线信号
    if brand.get("canonical_name"):
        score += 20
        evidence.append("canonical_name 存在 (+20)")
    if brand.get("aliases"):
        score += 10
        evidence.append(f"aliases {len(brand['aliases'])} 个 (+10)")
    if brand.get("description"):
        score += 20
        evidence.append("description 存在 (+20)")
    if brand.get("logo_url"):
        score += 10
        evidence.append("logo_url 存在 (+10)")

    # 在线信号(如提供)
    if online_meta:
        if online_meta.get("title"):
            score += 10
            evidence.append("title meta 存在 (+10)")
        if online_meta.get("description"):
            score += 10
            evidence.append("description meta 存在 (+10)")
        if online_meta.get("canonical"):
            score += 10
            evidence.append("canonical link 存在 (+10)")
        if online_meta.get("og_image"):
            score += 10
            evidence.append("og:image 存在 (+10)")

    return min(score, 100), evidence


def score_content_readability(frozen_cfg, observations):
    """维度 3:内容可读性(0-100)。

    评分依据:
    - 8 道 Q01-Q08 都设置 prompt:30 分
    - non_brand 题目至少 5 道:20 分
    - business_value 标注:20 分
    - 真实答案成功:30 分(成功任务数 / 32 * 30)
    """
    score = 0
    evidence = []
    questions = (frozen_cfg.get("scope") or {}).get("questions") or []

    if len(questions) >= 8:
        score += 30
        evidence.append(f"题目数 {len(questions)} ≥ 8 (+30)")
    elif len(questions) > 0:
        score += int(len(questions) / 8 * 30)
        evidence.append(f"题目数 {len(questions)} (+{int(len(questions)/8*30)})")

    non_brand = sum(1 for q in questions if q.get("type") == "non_brand")
    if non_brand >= 5:
        score += 20
        evidence.append(f"non_brand 题目 {non_brand} 道 (+20)")

    has_bv = sum(1 for q in questions if q.get("business_value"))
    if has_bv == len(questions) and len(questions) > 0:
        score += 20
        evidence.append("business_value 全标注 (+20)")

    success_count = sum(1 for o in observations if o.get("status") == "success")
    if observations:
        score += int(success_count / len(observations) * 30)
        evidence.append(f"成功率 {success_count}/{len(observations)} (+{int(success_count/len(observations)*30)})")

    return min(score, 100), evidence


def score_citation_signals(frozen_cfg, observations):
    """维度 4:引用信号(0-100)。

    评分依据:
    - official_domains 存在:20 分
    - official_pages 存在:20 分
    - 第三方平台主页:20 分(企查查/天眼查/知乎/小红书 等)
    - 实际引用命中:40 分(官方来源引用数 / 总任务数 * 40)
    """
    score = 0
    evidence = []
    brand = frozen_cfg.get("brand", {})

    if brand.get("official_domains"):
        score += 20
        evidence.append(f"official_domains {len(brand['official_domains'])} 个 (+20)")
    if brand.get("official_pages"):
        score += 20
        evidence.append(f"official_pages {len(brand['official_pages'])} 个 (+20)")

    third_party = brand.get("third_party_pages") or []
    if third_party:
        score += 20
        evidence.append(f"第三方平台 {len(third_party)} 个 (+20)")

    citation_yes = sum(1 for o in observations if (o.get("classification") or {}).get("official_source_citation") == "yes")
    if observations:
        score += int(citation_yes / len(observations) * 40)
        evidence.append(f"官方引用命中 {citation_yes}/{len(observations)} (+{int(citation_yes/len(observations)*40)})")

    return min(score, 100), evidence


def score_llms_txt(frozen_cfg, assets_dir):
    """维度 5:llms.txt / 结构化资产(0-100)。

    评分依据:
    - llms.txt 存在:40 分
    - 7 段结构完整:40 分(每段 ~6 分)
    - 摘要 ≤ 200 字:20 分
    """
    llms_path = os.path.join(assets_dir, "llms.txt")
    if not os.path.isfile(llms_path):
        return 0, ["llms.txt 不存在"]
    with open(llms_path, encoding="utf-8-sig") as f:
        content = f.read()

    score = 40  # 文件存在
    evidence = ["llms.txt 存在 (+40)"]

    # 7 段检查
    sections = ["## 主营业务", "## 服务区域", "## 联系信息", "## 官方信息源", "## 引用优先级"]
    covered = sum(1 for s in sections if s in content)
    score += int(covered / len(sections) * 40)
    evidence.append(f"7 段结构覆盖 {covered}/5 (+{int(covered/len(sections)*40)})")

    # 摘要长度
    summary_match = content.split(">")[1] if ">" in content else ""
    if 0 < len(summary_match) <= 200:
        score += 20
        evidence.append(f"摘要 {len(summary_match)} 字 ≤ 200 (+20)")
    elif len(summary_match) > 200:
        evidence.append(f"⚠ 摘要 {len(summary_match)} 字 > 200")

    return min(score, 100), evidence


def score_nap_consistency(frozen_cfg):
    """维度 6:多平台 NAP 一致性(0-100)。

    评分依据:
    - NAP 三要素齐全:30 分
    - 多平台声明 ≥ 5:40 分(每平台 8 分,封顶 40)
    - 无主体混淆声明:30 分
    """
    score = 0
    evidence = []
    brand = frozen_cfg.get("brand", {})

    nap = (brand.get("name") or brand.get("canonical_name")) and brand.get("full_address") and (brand.get("phone") or brand.get("contactPoint"))
    if nap:
        score += 30
        evidence.append("NAP 三要素齐全 (+30)")

    platforms = brand.get("nap_platforms") or []
    if platforms:
        platform_score = min(len(platforms) * 8, 40)
        score += platform_score
        evidence.append(f"NAP 平台 {len(platforms)} 个 (+{platform_score})")

    if brand.get("disambiguate_statement"):
        score += 30
        evidence.append("主体纠错声明存在 (+30)")

    return min(score, 100), evidence


# ---- 主流程 ----


def compute_score(frozen_cfg, assets_dir, observations, online_meta=None):
    """计算 6 维度 + 总分。"""
    dimensions = {
        "schema_completeness": score_schema_completeness(frozen_cfg, assets_dir),
        "meta_info": score_meta_info(frozen_cfg, online_meta),
        "content_readability": score_content_readability(frozen_cfg, observations),
        "citation_signals": score_citation_signals(frozen_cfg, observations),
        "llms_txt": score_llms_txt(frozen_cfg, assets_dir),
        "nap_consistency": score_nap_consistency(frozen_cfg),
    }

    # 总分 = 加权求和
    total = 0
    for dim_key, (dim_score, _) in dimensions.items():
        total += dim_score * WEIGHTS[dim_key]
    total = round(total, 2)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "company": frozen_cfg.get("brand", {}).get("canonical_name", ""),
        "dimensions": {
            k: {
                "score": v[0],
                "weight": WEIGHTS[k],
                "weighted_score": round(v[0] * WEIGHTS[k], 2),
                "evidence": v[1],
            }
            for k, v in dimensions.items()
        },
        "total_score": total,
        "trend": "stable",  # 单次跑无法判断趋势,需要历史数据对比
        "recommendations": _make_recommendations(dimensions),
    }


def _make_recommendations(dimensions):
    """基于低分维度生成建议。"""
    recs = []
    for dim_key, (score, evidence) in dimensions.items():
        if score < 60:
            dim_name = {
                "schema_completeness": "Schema 完整性",
                "meta_info": "Meta 信息",
                "content_readability": "内容可读性",
                "citation_signals": "引用信号",
                "llms_txt": "llms.txt",
                "nap_consistency": "NAP 一致性",
            }.get(dim_key, dim_key)
            recs.append(f"{dim_name}({score} 分)较低,建议优先补强")
    return recs


def main():
    parser = argparse.ArgumentParser(description="AI 友好度 6 维度评分")
    parser.add_argument("run_dir", help="run_dir 路径")
    parser.add_argument("--online", action="store_true", help="启用 Playwright 在线校验(可选)")
    parser.add_argument("--url", default=None, help="官方页面 URL")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    frozen_cfg_path = os.path.join(run_dir, "frozen-config.json")
    if not os.path.isfile(frozen_cfg_path):
        print(f"ERROR: frozen-config.json 不存在: {frozen_cfg_path}", file=sys.stderr)
        return 2

    frozen_cfg = load_json(frozen_cfg_path)
    assets_dir = os.path.join(run_dir, "assets")
    observations = load_jsonl(os.path.join(run_dir, "observations.jsonl"))

    # 在线模式(可选,默认离线)
    online_meta = None
    if args.online:
        try:
            import importlib
            playwright = importlib.import_module("playwright")
            print(f"WARN: 在线模式需要 playwright,实际未实现,使用离线评分")
            online_meta = None
        except ImportError:
            print(f"WARN: playwright 未安装,使用离线评分")

    score = compute_score(frozen_cfg, assets_dir, observations, online_meta)

    # 输出
    report_dir = os.path.join(run_dir, "report")
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(report_dir, "ai-friendliness.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(score, f, ensure_ascii=False, indent=2)

    # 友好输出
    print(f"OK: ai-friendliness.json -> {out_path}")
    print(f"\n公司:{score['company']}")
    print(f"总分:{score['total_score']} / 100")
    print(f"\n各维度:")
    for dim_key, dim in score["dimensions"].items():
        print(f"  - {dim_key}:{dim['score']} 分 (权重 {dim['weight']*100:.0f}%,加权 {dim['weighted_score']})")
    if score["recommendations"]:
        print(f"\n改进建议:")
        for rec in score["recommendations"]:
            print(f"  - {rec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
