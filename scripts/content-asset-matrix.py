# -*- coding: utf-8 -*-
"""content-asset-matrix.py — 内容资产矩阵生成器。

从 observations.jsonl 反推内容资产清单。

用法:
  python content-asset-matrix.py <run_dir>

输出:
  <run_dir>/report/content-asset-list.csv

逻辑:
  - 读取 observations.jsonl,提取 mention/recommendation/citation 命中情况
  - 读取 frozen-config.json 的 Q01-Q08 prompt
  - 按 P0/P1/P2 优先级排序
  - 输出 CSV,与 templates/content-asset-list.template.csv 表头一致

硬规则:
  - 零外部依赖
  - 不读 raw/(隐私),只读 observations.jsonl
  - 不写 frozen-config.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


CSV_HEADER = ["qid", "target_keyword", "content_title", "content_format", "publish_channel", "priority", "expected_mention"]


# 题目 → 内容标题模板(对应 render-report.py 的 suggested_asset_title 逻辑)
TITLE_TEMPLATES = {
    "Q01": "本地服务商怎么选:靠谱机构推荐标准、适用企业与避坑清单",
    "Q02": "服务收费标准说明:价格区间、服务内容与低价风险",
    "Q03": "找服务商还是自建团队:不同阶段企业的选择边界",
    "Q04": "常见异常与合规处理流程:原因、影响、材料和周期",
    "Q05": "风险防控专题:常见风险、识别方法和整改方案",
    "Q08": "本地服务商与全国平台怎么选:利弊对比与决策建议",
    "Q06": "品牌主体信息页:主营业务、服务对象与能力边界",
    "Q07": "品牌服务说明页:适用客户、服务流程与真实依据",
}


def derive_target_keyword(qid, prompt):
    """从题目推导目标关键词(简化版)。"""
    if not prompt:
        return ""
    # 去掉标点
    text = prompt.replace("?", "").replace("?", "").replace("?", "")
    # 取前 30 字作为目标关键词
    return text[:30].strip()


def derive_priority(qid, business_value, mention_hits, total_obs):
    """基于业务价值 + 提及命中数推导优先级。"""
    # P0: 高价值 + 未提及
    if business_value == "high" and mention_hits == 0:
        return "P0"
    # P0: 高价值 + 提及 < 1/3
    if business_value == "high" and mention_hits < total_obs / 3:
        return "P0"
    # P1: 中价值或高价值且已较好
    if business_value == "high":
        return "P1"
    return "P2"


def derive_expected_mention(priority):
    return "yes" if priority in ("P0", "P1") else "no"


def suggest_publish_channels(priority, qid):
    """根据优先级 + 题型建议发布渠道。"""
    base = "头条号+知乎"
    if qid in ("Q06", "Q07"):
        return "官网+公众号"  # 品牌题放官网
    if qid == "Q08":
        return "知乎+头条号"
    if priority == "P0":
        return "头条号+知乎+百家号"
    return base


def suggest_content_format(priority, qid):
    """建议内容格式。"""
    if qid in ("Q02", "Q08", "Q03"):
        return "长文+表格"
    if qid == "Q06":
        return "长文+卡片"
    return "长文"


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


def cls(obs, key, default=""):
    c = obs.get("classification") or {}
    return c.get(key, default)


def main():
    parser = argparse.ArgumentParser(description="内容资产矩阵生成器")
    parser.add_argument("run_dir", help="run_dir 路径")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    frozen_cfg_path = os.path.join(run_dir, "frozen-config.json")
    if not os.path.isfile(frozen_cfg_path):
        print(f"ERROR: frozen-config.json 不存在: {frozen_cfg_path}", file=sys.stderr)
        return 2

    frozen_cfg = load_json(frozen_cfg_path)
    observations = load_jsonl(os.path.join(run_dir, "observations.jsonl"))
    questions = (frozen_cfg.get("scope") or {}).get("questions") or []

    # 按 qid 聚合 mention/recommendation 命中
    hits = {}
    for obs in observations:
        qid = obs.get("question_id")
        if not qid:
            continue
        hits.setdefault(qid, {"mention": 0, "recommend": 0, "total": 0})
        hits[qid]["total"] += 1
        if cls(obs, "brand_mention") == "yes":
            hits[qid]["mention"] += 1
        if cls(obs, "recommendation") == "explicit":
            hits[qid]["recommend"] += 1

    total_obs = len(observations) or 1

    # 生成 CSV 行
    rows = []
    for q in questions:
        qid = q.get("id")
        if not qid:
            continue
        # Q01-Q08 全量覆盖：品牌认知题也需要事实卡/服务说明资产，
        # 不能因为不可用于自然推荐就从矩阵中丢失。

        business_value = q.get("business_value", "medium")
        q_hits = hits.get(qid, {"mention": 0, "recommend": 0, "total": 0})

        title = TITLE_TEMPLATES.get(qid, f"围绕 {qid} 的高意图问题解答页")
        priority = derive_priority(qid, business_value, q_hits["mention"], total_obs)
        expected_mention = derive_expected_mention(priority)
        target_keyword = derive_target_keyword(qid, q.get("prompt", ""))
        content_format = suggest_content_format(priority, qid)
        publish_channel = suggest_publish_channels(priority, qid)

        rows.append({
            "qid": qid,
            "target_keyword": target_keyword,
            "content_title": title,
            "content_format": content_format,
            "publish_channel": publish_channel,
            "priority": priority,
            "expected_mention": expected_mention,
        })

    # 按优先级排序:P0 → P1 → P2
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    rows.sort(key=lambda r: (priority_order.get(r["priority"], 3), r["qid"]))

    # 输出 CSV
    report_dir = os.path.join(run_dir, "report")
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(report_dir, "content-asset-list.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)

    # 友好输出
    print(f"OK: content-asset-list.csv -> {out_path}")
    print(f"  - 共 {len(rows)} 行")
    p0_count = sum(1 for r in rows if r["priority"] == "P0")
    p1_count = sum(1 for r in rows if r["priority"] == "P1")
    p2_count = sum(1 for r in rows if r["priority"] == "P2")
    print(f"  - 优先级分布:P0={p0_count}, P1={p1_count}, P2={p2_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
