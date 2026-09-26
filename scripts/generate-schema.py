# -*- coding: utf-8 -*-
"""generate-schema.py — JSON-LD + llms.txt + 知识库草稿生成器。

借鉴 GEORank 模块 6(结构化),从 frozen-config.json + 模板生成:
  - schema.jsonld(5 个 @type:Organization/LocalBusiness/FAQPage/Service/Review)
  - llms.txt(7 段结构)
  - company_kb.md(知识库草稿)

用法:
  python generate-schema.py <run_dir> [--out DIR]
  python generate-schema.py 'E:\\GEO\\示例财税代理服务有限公司\\runs\\20260909-005338'

输出:
  <out>/schema.jsonld
  <out>/llms.txt
  <out>/company_kb.md

硬规则:
  - 只读 frozen-config.json,绝不修改
  - 输出到 run_dir/assets/,不污染 raw/ observations.jsonl tasks.jsonl
  - 零外部依赖,纯 Python 标准库
  - UTF-8 + LF 换行
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


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "templates"))
SKILL_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_text(path, content):
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def replace_placeholders(text, mapping):
    """逐项做字面替换。用于 llms.txt 等纯文本模板。

    模板占位符格式:{key}
    例如 mapping = {"品牌全称": "XX 财税"}
    模板中:{品牌全称} → XX 财税
    """
    for key, val in mapping.items():
        text = text.replace("{" + key + "}", str(val))
    return text


def _replace_in_obj(obj, mapping):
    """递归遍历 dict/list,对每个 string 字段做 CHANGE_ME 替换。

    用于 JSON-LD 模板:不会跨越 JSON 字符串边界。
    """
    if isinstance(obj, dict):
        return {k: _replace_in_obj(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_in_obj(v, mapping) for v in obj]
    if isinstance(obj, str):
        result = obj
        for key, val in mapping.items():
            # 匹配 CHANGE_ME: <key> 后接 0-80 个非中英文逗号/句号/右括号字符
            pattern = rf"CHANGE_ME:\s*{re.escape(key)}[^，,。.\u3002)\）]{{0,80}}"
            result = re.sub(pattern, str(val), result)
        return result
    return obj


def _sanitize_schema_placeholders(obj):
    """将模板残留占位符变为草稿值，避免 CHANGE_ME 进入可交付文件。"""
    if isinstance(obj, dict):
        return {k: _sanitize_schema_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_schema_placeholders(v) for v in obj]
    if isinstance(obj, str) and (
        "CHANGE_ME" in obj
        or re.search(r"<[^>]+>|\{[^}]+\}", obj)
    ):
        return "待补充"
    return obj


def derive_summary_from_brand(brand):
    """从 brand 字段生成 200 字摘要。"""
    name = brand.get("canonical_name") or "待补充的公司名称"
    aliases = brand.get("aliases") or []
    desc = brand.get("description") or ""
    region = brand.get("region") or ""
    industry = brand.get("industry") or ""
    years = brand.get("years_in_business") or ""
    if not desc:
        # 不推断业态：region/industry 没配就留空，不写「本地服务商」这类别的项目才成立的描述。
        desc = f"{region}{industry}".strip()
    head = f"{name}({','.join(aliases[:2])})" if aliases else name
    summary = f"{head}:{desc}" if desc else head
    if years:
        summary += f",成立 {years} 年"
    # 只保留事实摘要，不把内部模板说明写进公开资产；按字符截断并去掉
    # 残留占位符，确保满足 llms.txt 的 200 字摘要上限。
    summary = re.sub(r"CHANGE_ME[^，。]*|<[^>]+>|\{[^}]+\}", "", summary)
    summary = re.sub(r"\s+", "", summary).strip("，。:：")
    return summary[:200]


# ---- schema.jsonld 生成 ----


def generate_schema(frozen_cfg, template_path, output_path):
    """从 frozen_cfg + template 生成 schema.jsonld。"""
    brand = frozen_cfg.get("brand", {})
    if not os.path.isfile(template_path):
        schema = _minimal_schema(frozen_cfg)
    else:
        template = load_json(template_path)
        # 用递归 dict 替换,避免 JSON 字符串边界跨越问题
        mapping = {
            "品牌标准全称,如'示例财税代理服务有限公司'": brand.get("canonical_name", "<公司全称>"),
            "简称 1,如'示例财税'": (brand.get("aliases") or ["<简称>"])[0],
            "城市,如'广州市'": brand.get("address_city", "<城市>"),
            "区,如'天河区'": brand.get("address_region", "<区>"),
            "街道地址,如'天河路 123 号 XX 大厦 8 楼 801'": brand.get("address_street", "<街道>"),
            "邮编,如'510000'": brand.get("address_postal", "<邮编>"),
        }
        schema = _replace_in_obj(template, mapping)

    schema = _sanitize_schema_placeholders(schema)

    # 用 brand 字段覆盖关键字段(确保事实正确)
    org = _find_graph_entity(schema, "Organization")
    if org:
        if brand.get("canonical_name"):
            org["name"] = brand["canonical_name"]
        if brand.get("aliases"):
            org["alternateName"] = brand["aliases"]
        if brand.get("official_domains"):
            org["url"] = f"https://{brand['official_domains'][0]}"
            org["@id"] = f"https://{brand['official_domains'][0]}#org"

    write_text(output_path, json.dumps(schema, ensure_ascii=False, indent=2))
    return schema


def _minimal_schema(frozen_cfg):
    """模板缺失时的兜底最小 schema。"""
    brand = frozen_cfg.get("brand", {})
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": "#org",
                "name": brand.get("canonical_name", "<公司全称>"),
                "alternateName": brand.get("aliases", []),
                "url": f"https://{brand['official_domains'][0]}" if brand.get("official_domains") else "<url>",
            }
        ],
    }


def _find_graph_entity(schema, type_name):
    """在 @graph 中找到指定 @type 的实体。"""
    for entity in (schema.get("@graph") or []):
        if entity.get("@type") == type_name:
            return entity
    return None


# ---- llms.txt 生成 ----


def generate_llms_txt(frozen_cfg, template_path, output_path):
    """从 frozen_cfg + template 生成 llms.txt。"""
    if not os.path.isfile(template_path):
        return _minimal_llms_txt(frozen_cfg, output_path)
    with open(template_path, encoding="utf-8-sig") as f:
        template = f.read()
    brand = frozen_cfg.get("brand", {})
    scope = frozen_cfg.get("scope", {})
    mapping = {
        "品牌全称": brand.get("canonical_name", "<公司全称>"),
        "200 字摘要:含地域+行业+服务范围+差异化优势。例:广州本地 10 年代理记账服务商,专注小微企业与电商公司,服务天河/海珠/越秀/南沙 800+ 客户,擅长金税四期风控与电商行业税务筹划。": derive_summary_from_brand(brand),
        "区域清单,逗号分隔。例:广州天河区、海珠区、越秀区、白云区、番禺区、南沙区": brand.get("region", "<区域>"),
        "品牌全称,与抖音/头条/西瓜/百度地图/高德地图完全一致": brand.get("canonical_name", "<公司全称>"),
        "完整地址,含城市+区+街道+门牌号": brand.get("full_address", "<完整地址>"),
        "电话": brand.get("phone", "<电话>"),
        "营业时间,如 周一至周五 9:00-18:00": brand.get("business_hours", "周一至周五 9:00-18:00"),
    }
    content = replace_placeholders(template, mapping)
    # 未配置的字段统一改为可识别的草稿标记，避免把模板语法误发布为事实。
    content = re.sub(r"\{[^}\n]+\}|<[^>\n]+>", "待补充", content)
    content = content.replace("https://待补充", "待补充")
    # 摘要行严格限制为 200 字以内。
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("> "):
            lines[i] = "> " + line[2:][:200]
            break
    content = "\n".join(lines) + "\n"
    write_text(output_path, content)
    return content


def _minimal_llms_txt(frozen_cfg, output_path):
    """模板缺失时的兜底 llms.txt。"""
    brand = frozen_cfg.get("brand", {})
    content = f"""# {brand.get('canonical_name', '<公司全称>')}

> {derive_summary_from_brand(brand)}

## 主营业务
- <主营业务 1>
- <主营业务 2>

## 服务区域
{brand.get('region', '<区域>')}

## 联系信息
- Name: {brand.get('canonical_name', '<公司全称>')}
- Address: {brand.get('full_address', '<地址>')}
- Phone: {brand.get('phone', '<电话>')}
"""
    write_text(output_path, content)
    return content


# ---- company_kb.md 生成 ----


def generate_company_kb(frozen_cfg, observations, output_path):
    """从 frozen_cfg + observations 生成 company_kb.md 知识库草稿。"""
    brand = frozen_cfg.get("brand", {})
    questions_cfg = (frozen_cfg.get("scope") or {}).get("questions") or []

    # 从 observations 抽取高频事实
    facts = []
    for obs in (observations or []):
        if obs.get("status") == "success":
            cited = obs.get("source_links") or []
            if cited:
                facts.extend(cited[:3])

    content = f"""# {brand.get('canonical_name', '<公司全称>')} 知识库

## 主体信息
- 标准全称:{brand.get('canonical_name', '<待填>')}
- 简称:{', '.join(brand.get('aliases') or ['<待填>'])}
- 成立时间:{brand.get('founding_date', '<待填>')}
- 注册地:{brand.get('full_address', '<待填>')}
- 法人代表:{brand.get('legal_rep', '<待填>')}

## 主营业务
- <待补:从 run-config.brand.business_scope 或 frozen_cfg 抽取>

## 服务区域
{brand.get('region', '<待填>')}

## 服务对象
- <待补:从 run-config.brand.target_customers 抽取>

## 核心优势(待事实化补全)
- <待补:从客户案例/服务数据抽取>

## 联系信息(NAP)
- Name:{brand.get('canonical_name', '<待填>')}
- Address:{brand.get('full_address', '<待填>')}
- Phone:{brand.get('phone', '<待填>')}

## 引用优先级
1. 公司事实卡 / 品牌页
2. FAQ 页
3. 客户案例
4. 第三方平台

## 已采集事实来源(从 observations 抽取)
{chr(10).join('- ' + f for f in facts[:20]) if facts else '- (无,需后续诊断补全)'}

## 注意事项
- 引用 Review 时必须是公开第三方平台的真实评价,不得伪造
- NAP 必须在 5+ 字节平台完全一致
- 本文件由 generate-schema.py 自动生成,人工 review 后才能对外发布
"""
    write_text(output_path, content)
    return content


# ---- 主流程 ----


def main():
    parser = argparse.ArgumentParser(description="JSON-LD + llms.txt + 知识库草稿生成器")
    parser.add_argument("run_dir", help="run_dir 路径,包含 frozen-config.json")
    parser.add_argument("--out", default=None, help="输出目录,默认 <run_dir>/assets/")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    frozen_cfg_path = os.path.join(run_dir, "frozen-config.json")
    if not os.path.isfile(frozen_cfg_path):
        print(f"ERROR: frozen-config.json 不存在: {frozen_cfg_path}", file=sys.stderr)
        return 2

    try:
        frozen_cfg = load_json(frozen_cfg_path)
    except json.JSONDecodeError as e:
        print(f"ERROR: frozen-config.json 解析失败: {e}", file=sys.stderr)
        return 3

    out_dir = args.out or os.path.join(run_dir, "assets")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # 读 observations(可选)
    observations = []
    obs_path = os.path.join(run_dir, "observations.jsonl")
    if os.path.isfile(obs_path):
        try:
            with open(obs_path, encoding="utf-8-sig") as f:
                observations = [json.loads(line) for line in f if line.strip()]
        except (json.JSONDecodeError, OSError):
            pass

    # 生成 3 个产物
    schema_path = os.path.join(out_dir, "schema.jsonld")
    schema = generate_schema(
        frozen_cfg,
        os.path.join(TEMPLATES_DIR, "schema-org.template.jsonld"),
        schema_path,
    )
    print(f"OK: schema.jsonld -> {schema_path}")

    llms_path = os.path.join(out_dir, "llms.txt")
    generate_llms_txt(
        frozen_cfg,
        os.path.join(TEMPLATES_DIR, "llms.txt.template"),
        llms_path,
    )
    print(f"OK: llms.txt -> {llms_path}")

    kb_path = os.path.join(out_dir, "company_kb.md")
    generate_company_kb(frozen_cfg, observations, kb_path)
    print(f"OK: company_kb.md -> {kb_path}")

    print(f"\n提示:用 Google Rich Results Test 校验 schema.jsonld: https://search.google.com/test/rich-results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
