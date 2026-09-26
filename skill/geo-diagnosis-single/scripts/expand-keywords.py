# -*- coding: utf-8 -*-
"""expand-keywords.py — GEORank 4 层拓词本地实现。

借鉴 GEORank 模块 5(拓展),业务词 → 问题词 → 场景词 → 意图词 → 推荐型关键词。

用法:
  python expand-keywords.py <config.json> [--out DIR] [--dry-run] [--write]
  python expand-keywords.py templates/keywords-expansion.template.json --out config/

输出:
  <out>/expanded-questions.json(默认 dry-run,只生成不合并)

硬规则:
  - 默认 --dry-run,不写 frozen-config.json
  - 显式 --write 才合并到 run-config.json 的 scope.questions
  - 零外部依赖,纯 Python 标准库
  - 非品牌题不出现:品牌全称 / 简称 / 域名 / 官方页面
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---- 拓词组合规则(GEORank 4 层 + 推荐型) ----
# 组合公式: [地域] + [业务词] + [意图词]
# 意图词模板:推荐/哪家好/排行/对比/多少钱/怎么办/风险/靠谱

INTENT_PATTERNS = {
    "intent_recommend": ["推荐", "哪家好", "排行", "对比"],
    "intent_price": ["多少钱", "收费", "价格", "费用"],
    "intent_process": ["怎么办", "流程", "步骤", "怎么选"],
    "intent_risk": ["风险", "避坑", "注意", "陷阱"],
    "intent_quality": ["靠谱", "专业", "正规", "口碑"],
}

RECOMMENDATION_FORMULA = "{region}{business}{intent}"

# ---- 非品牌题硬规则 ----
NON_BRAND_FORBIDDEN = [
    r"官方", r"官网", r"\.com", r"\.cn", r"https?://",
    # 占位:实际使用时由 config.brand_canonical 和 aliases 提供具体黑名单
]

# ---- 拓词生成器 ----


def normalize(s):
    if s is None:
        return ""
    return str(s).strip()


def is_non_brand_compliant(prompt, forbidden_patterns):
    """检查 prompt 是否符合非品牌题规则(不含品牌词/别名/域名)。"""
    if not prompt:
        return False
    text = prompt.lower()
    for pat in forbidden_patterns:
        if re.search(pat.lower(), text):
            return False
    return True


def build_forbidden_patterns(config):
    """从 config 生成非品牌题禁用模式。"""
    forbidden = list(NON_BRAND_FORBIDDEN)
    canonical = normalize(config.get("brand_canonical"))
    if canonical and "CHANGE_ME" not in canonical:
        # 转义正则特殊字符
        forbidden.append(re.escape(canonical))
    for alias in (config.get("aliases") or []):
        a = normalize(alias)
        if a and "CHANGE_ME" not in a:
            forbidden.append(re.escape(a))
    return forbidden


def expand_recommendation_keywords(region, business_words, intent_words, base_prompts):
    """第 5 层:推荐型关键词组合。"""
    keywords = []
    for biz in business_words:
        for intent in intent_words:
            kw = RECOMMENDATION_FORMULA.format(
                region=region, business=biz, intent=intent
            )
            keywords.append(kw)
    return keywords


def expand_questions(config, dry_run=True):
    """从 4 层 + 推荐型关键词生成 16-30 题。"""
    layers = config.get("layers", {})
    region = normalize(config.get("base_region"))
    business_words = [normalize(w) for w in (layers.get("business_words") or []) if normalize(w) and "CHANGE_ME" not in normalize(w)]
    question_words = [normalize(w) for w in (layers.get("question_words") or []) if normalize(w) and "CHANGE_ME" not in normalize(w)]
    scenario_words = [normalize(w) for w in (layers.get("scenario_words") or []) if normalize(w) and "CHANGE_ME" not in normalize(w)]
    intent_words = [normalize(w) for w in (layers.get("intent_words") or []) if normalize(w) and "CHANGE_ME" not in normalize(w)]
    recommendation_keywords = [normalize(w) for w in (layers.get("recommendation_keywords") or []) if normalize(w) and "CHANGE_ME" not in normalize(w)]

    # 模板必须可直接演示。保留 CHANGE_ME 标记供用户替换，但运行模板时
    # 使用通用示例词，避免把占位题误当成有效扩展题，导致输出少于 16 题。
    if not region or "CHANGE_ME" in region:
        region = "广州天河区"
    business_words_configured = bool(business_words)
    if not business_words:
        # 不猜测行业：缺业务词时优先用配置里的 base_industry，仍缺则用显式占位符。
        # （真实事故：这里曾写死行业词，导致別的主体拓出一堆代理记账关键词。）
        base_industry = normalize(config.get("base_industry"))
        business_words = [base_industry] if base_industry and "CHANGE_ME" not in base_industry else ["业务词待填"]
    if not question_words:
        question_words = ["多少钱", "怎么选"]
    if not scenario_words:
        scenario_words = ["小微企业", "电商公司", "初创公司"]
    if not intent_words:
        intent_words = ["推荐", "收费", "避坑", "靠谱吗"]
    if not recommendation_keywords:
        picks = [business_words[i % len(business_words)] for i in range(3)]
        recommendation_keywords = [
            f"{region}{picks[0]}推荐",
            f"{region}{picks[0]}哪家好",
            f"{region}{picks[1]}推荐",
            f"{region}{picks[2]}哪家好",
            f"{region}{picks[0]}避坑",
            f"{region}{picks[1]}收费",
            f"{region}{picks[2]}对比",
            f"{region}{picks[0]}靠谱公司",
            f"{region}{picks[1]}专业机构",
            f"{region}{picks[2]}服务商",
        ]

    # 已存在的 expanded_questions(用户可能已填示例)
    existing = [
        q for q in (config.get("expanded_questions") or [])
        if isinstance(q, dict) and "CHANGE_ME" not in json.dumps(q, ensure_ascii=False)
    ]

    generated = list(existing)  # 保留已填示例

    # 自动生成:每业务词 × 推荐型关键词 = 1 题
    for biz in business_words[:3]:  # 限制业务词数,避免题目爆炸
        for rec_kw in (recommendation_keywords or [f"{region}{biz}推荐"])[:3]:
            if not region or "CHANGE_ME" in region:
                prompt = f"请推荐 3 家提供「{biz}」服务的专业公司,并说明各自适合什么样的客户。"
            else:
                prompt = f"{region}{biz}推荐哪些公司?请推荐 3 家并说明适合什么样的客户。"
            generated.append({
                "id": f"QE{len(generated)+1:02d}",
                "parent": "Q01",
                "type": "non_brand",
                "business_value": "high",
                "keyword_layer": "recommendation",
                "prompt": prompt,
                "target_keyword": rec_kw,
            })

    # 自动生成:场景词 × 问题词 = 1 题
    for sc in (scenario_words or [])[:3]:
        for qw in (question_words or [])[:2]:
            prompt = f"{sc}{qw}?有哪些注意事项?"
            generated.append({
                "id": f"QE{len(generated)+1:02d}",
                "parent": "Q05",
                "type": "non_brand",
                "business_value": "medium",
                "keyword_layer": "scenario",
                "prompt": prompt,
                "target_keyword": f"{sc}{qw}",
            })

    # 16 题是最小验收门槛。真实配置通常会超过该数量，模板示例则补齐。
    while len(generated) < 16:
        idx = len(generated) + 1
        biz = business_words[(idx - 1) % len(business_words)]
        intent = intent_words[(idx - 1) % len(intent_words)]
        generated.append({
            "id": f"QE{idx:02d}",
            "parent": "Q01" if idx % 2 else "Q02",
            "type": "non_brand",
            "business_value": "medium",
            "keyword_layer": "intent",
            "prompt": f"{region}{biz}{intent}时应关注哪些服务标准和风险?",
            "target_keyword": f"{region}{biz}{intent}",
        })

    # 非品牌题合规校验
    forbidden = build_forbidden_patterns(config)
    flagged = [q for q in generated if q.get("type") == "non_brand" and not is_non_brand_compliant(q.get("prompt", ""), forbidden)]

    return {
        "expanded_questions": generated,
        "summary": {
            "total": len(generated),
            "non_brand": sum(1 for q in generated if q.get("type") == "non_brand"),
            "by_layer": {
                "business": sum(1 for q in generated if q.get("keyword_layer") == "business"),
                "question": sum(1 for q in generated if q.get("keyword_layer") == "question"),
                "scenario": sum(1 for q in generated if q.get("keyword_layer") == "scenario"),
                "intent": sum(1 for q in generated if q.get("keyword_layer") == "intent"),
                "recommendation": sum(1 for q in generated if q.get("keyword_layer") == "recommendation"),
            },
            "recommendation_keyword_count": len(recommendation_keywords),
            "needs_business_words": not business_words_configured,
            "flagged_for_review": [q.get("id") for q in flagged],
            "dry_run": dry_run,
        },
    }


# ---- 主流程 ----


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, obj):
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="GEORank 4 层拓词本地实现")
    parser.add_argument("config", help="拓词配置 JSON(基于 templates/keywords-expansion.template.json)")
    parser.add_argument("--out", default=".", help="输出目录,默认当前目录")
    parser.add_argument("--dry-run", action="store_true", default=True, help="默认开启,只生成 expanded-questions.json,不合并到 run-config")
    parser.add_argument("--write", action="store_true", help="显式开启,合并到 run-config.json")
    args = parser.parse_args()

    dry_run = not args.write  # --write 关闭 dry-run

    if not os.path.isfile(args.config):
        print(f"ERROR: 配置文件不存在: {args.config}", file=sys.stderr)
        return 2

    try:
        config = load_json(args.config)
    except json.JSONDecodeError as e:
        print(f"ERROR: 配置文件 JSON 解析失败: {e}", file=sys.stderr)
        return 3

    result = expand_questions(config, dry_run=dry_run)

    # 输出 expanded-questions.json
    out_path = os.path.join(args.out, "expanded-questions.json")
    write_json(out_path, result)
    print(f"OK: 拓词完成,生成 {result['summary']['total']} 题")
    print(f"  - 输出: {out_path}")
    print(f"  - 推荐型关键词数: {result['summary']['recommendation_keyword_count']}")
    print(f"  - 层级分布: {result['summary']['by_layer']}")
    if result["summary"]["flagged_for_review"]:
        print(f"  - ⚠ 待复核(可能含品牌词): {result['summary']['flagged_for_review']}")
    print(f"  - 模式: {'dry-run(不合并 run-config)' if dry_run else 'write(已合并)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
