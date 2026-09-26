"""Evidence-aware content generation and quality gates.

The built-in generator writes a **structured material digest** (素材整理稿) — grouped,
collapsed, reader-facing — and never marketing prose: it cannot write a publishable
article. An optional OpenAI-compatible endpoint produces the real draft. Both are bound
by the same source bundle and quality checks, and no operator-facing text (briefs,
to-do lists, "待确认" notes) is allowed into the body.
"""
from datetime import datetime, timezone
import json
import os
import re
from urllib import request, error


PLACEHOLDER_RE = re.compile(r'待补充|待填写|TODO|placeholder|lorem ipsum|[【\[]待核实[】\]]', re.I)
SOURCE_RE = re.compile(r'https?://|(?:来源|出处|文件|报告|官方页面)\s*[:：]', re.I)
INSTRUCTION_TITLE_RE = re.compile(r'^(?:待填写[:：]?\s*)?补充问题相关的事实与内容[:：]?', re.I)

#: 从素材原文里挑“像事实”的一行：含数字、年份、资质、交付类关键词。
#: 这是给编辑看的阅读辅助，不是对真实性的断言。
FACT_HINTS = re.compile(
    r'(\d|年|月|认证|资质|专利|软著|标准|案例|客户|交付|覆盖|支持|服务|系统|平台|方案|'
    r'团队|规模|经验|实施|验收|指标|型号|版本|协议|ISO|GB|CMMI|AAA)')
FACT_NOISE = re.compile(r'^(首页|登录|注册|下一篇|上一篇|更多|分享|收藏|评论|点赞|关注|'
                        r'Copyright|©|版权所有|免责声明|广告|相关推荐|热门|导航)')

#: 表格类素材（功能清单/参数表）的判定与处理
CELL_SPLIT = re.compile(r'\s*[|｜\t]\s*')
NUMERIC_CELL = re.compile(r'^\d+(\.\d+)?$')
#: 说明列里大量同模板句：前 N 字相同就当成同一类，合并成一条 + 计数
COLLAPSE_PREFIX = 18

#: 本地引擎的产出边界：它是整理，不是成稿。
def _local_engine_note():
    return ('本地引擎只做素材整理，不写营销文案。要可直接发布的成稿，'
            '请配置 GEO_CONTENT_LLM_BASE_URL / GEO_CONTENT_LLM_MODEL 后再生成。')


#: 面向操作员的括号注释（来源行里会有），不能出现在正文里
OPERATOR_PAREN_RE = re.compile(r'（[^（）]*(?:待人工确认|待确认|已录入|已提取|已抓取|待核实)[^（）]*）')
#: 正文里绝对不该出现的操作员用语。测试用它守门。
OPERATOR_PHRASES = ('本文的目标是', '这篇内容讨论', '发布前仍需', '建议的下一步',
                    '待人工确认', '本地引擎', '写作任务书', '请配置 GEO_CONTENT')


def _clean_source_lines(facts):
    """给正文用的来源行：去掉“（已提取 N 字，待人工确认）”这类操作员注释。"""
    lines = []
    for raw in str(facts or '').splitlines():
        line = OPERATOR_PAREN_RE.sub('', raw).strip()
        if line:
            lines.append(line)
    return lines


def candidate_facts(materials, limit=14, max_len=200):
    """Pick short, factual-looking lines out of raw material excerpts."""
    picked, seen = [], set()
    for material in materials or []:
        for raw_line in str(material.get('excerpt') or '').split('\n'):
            line = str(raw_line).strip()
            if not (12 <= len(line) <= max_len):
                continue
            if FACT_NOISE.match(line) or not FACT_HINTS.search(line):
                continue
            key = re.sub(r'\W+', '', line)[:60]
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append({
                'text': line,
                'from': (material.get('label') or material.get('file_name')
                         or material.get('url') or '素材'),
            })
            if len(picked) >= limit:
                return picked
    return picked


def _collapse(lines, limit=None):
    """同前缀合并：表格说明列常见「支持用户发起流程或通过数据接口获取和查看X信息。」这类模板句。

    返回 [{'text': 代表句, 'count': 同类条数}]。
    """
    merged, order = {}, []
    for raw in lines:
        line = str(raw).strip()
        if len(line) < 4 or FACT_NOISE.match(line):
            continue
        key = line[:COLLAPSE_PREFIX]
        if key in merged:
            merged[key]['count'] += 1
            continue
        merged[key] = {'text': line, 'count': 1}
        order.append(key)
        if limit and len(order) >= limit:
            break
    return [merged[key] for key in order]


def _material_lines(materials):
    lines = []
    for material in materials or []:
        for raw in str(material.get('excerpt') or '').split('\n'):
            line = str(raw).strip()
            if line:
                lines.append(line)
    return lines


def _looks_tabular(materials):
    lines = _material_lines(materials)
    if len(lines) < 8:
        return False
    rows = sum(1 for line in lines if len(CELL_SPLIT.split(line)) >= 3)
    return rows / len(lines) >= 0.35


def _collapse_items(items, limit=None):
    """按说明列前缀合并同类项，并把它们各自的功能名收在一起。"""
    merged, order = {}, []
    for item in items:
        detail = str(item.get('detail') or '').strip()
        if len(detail) < 4 or FACT_NOISE.match(detail):
            continue
        key = detail[:COLLAPSE_PREFIX]
        if key not in merged:
            merged[key] = {'detail': detail, 'labels': [], 'count': 0}
            order.append(key)
        entry = merged[key]
        entry['count'] += 1
        label = str(item.get('label') or '').strip()
        if label and label not in entry['labels']:
            entry['labels'].append(label)
    return [merged[key] for key in order[:limit] if limit] or [merged[key] for key in order]


def _detail_and_label(row, group_index, group_name):
    """从一行里取「功能名」与「说明」：说明取最后一个长单元格，功能名取它前面最近的那个。"""
    usable = [(pos, cell) for pos, cell in enumerate(row)
              if cell and not NUMERIC_CELL.match(cell)]
    usable = [(pos, cell) for pos, cell in usable if pos != group_index]
    if not usable:
        return '', ''
    detail_pos, detail = max(usable, key=lambda pair: (len(pair[1]), pair[0]))
    if len(detail) < 6:
        return '', ''
    label = ''
    for pos, cell in usable:
        if pos < detail_pos and cell != group_name and len(cell) <= 24:
            label = cell
    return label, detail


def _group_column(rows, max_groups):
    """选一个“分类列”：取值不多、不长、不是纯数字的那一列（通常是模块名）。"""
    width = min(len(row) for row in rows)
    best, best_score = -1, None
    for index in range(width):
        values = [row[index] for row in rows]
        if any(NUMERIC_CELL.match(v) for v in values):
            continue
        distinct = {v for v in values if v}
        if not 2 <= len(distinct) <= max_groups * 2:
            continue
        if max((len(v) for v in distinct), default=0) > 24:
            continue
        score = len(values) / len(distinct)          # 平均每组行数，越大越像分类列
        if best_score is None or score > best_score:
            best, best_score = index, score
    return best


def material_overview(materials, max_groups=10, samples=4):
    """把素材整理成「分组 → 代表条目」，而不是逐行堆砌。"""
    lines = _material_lines(materials)
    overview = {'rows': len(lines), 'kind': 'text', 'groups': [], 'others': []}
    if not lines:
        return overview
    if _looks_tabular(materials):
        rows = [CELL_SPLIT.split(line) for line in lines]
        rows = [[cell.strip() for cell in row] for row in rows if len(row) >= 3]
        index = _group_column(rows, max_groups)
        if index >= 0:
            buckets, order = {}, []
            for row in rows:
                name = row[index]
                if not name or NUMERIC_CELL.match(name):
                    continue
                label, detail = _detail_and_label(row, index, name)
                buckets.setdefault(name, {'name': name, 'count': 0, 'items': []})
                if name not in order:
                    order.append(name)
                buckets[name]['count'] += 1
                if detail:
                    buckets[name]['items'].append({'label': label, 'detail': detail})
            overview['kind'] = 'table'
            overview['group_column'] = index
            for name in order[:max_groups]:
                bucket = buckets[name]
                overview['groups'].append({
                    'name': name, 'count': bucket['count'],
                    'samples': _collapse_items(bucket['items'], limit=samples),
                })
            overview['group_total'] = len(order)
            return overview
    overview['others'] = _collapse(lines, limit=16)
    return overview


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _text(value):
    return str(value or '').strip()


def _plain_length(markdown):
    text = re.sub(r'https?://\S+', '', _text(markdown))
    text = re.sub(r'[`#>*_\-\[\]()]', '', text)
    return len(re.sub(r'\s+', '', text))


def channel_min_length(channel):
    value = _text(channel).casefold()
    if any(token in value for token in ('小红书', 'xiaohongshu', 'rednote')):
        return 120
    if any(token in value for token in ('公众号', '微信', '官网', '知乎', 'wechat', 'official', 'zhihu')):
        return 400
    return 300


def plain_length(markdown):
    """Exposed so callers (e.g. the draft library) can size a draft without a full gate run."""
    return _plain_length(markdown)


def quality_check(asset):
    """Return blocking errors and non-blocking warnings for the current asset version."""
    title = _text(asset.get('title'))
    body = _text(asset.get('body'))
    facts = _text(asset.get('facts'))
    summary = _text(asset.get('summary'))
    audience = _text(asset.get('audience'))
    objective = _text(asset.get('objective'))
    channel = _text(asset.get('channel'))
    keywords = [x.strip() for x in re.split(r'[,，;；\n]+', _text(asset.get('keywords'))) if x.strip()]
    errors = []
    warnings = []

    if not title:
        errors.append('标题不能为空')
    elif PLACEHOLDER_RE.search(title) or INSTRUCTION_TITLE_RE.search(title):
        errors.append('标题仍是占位符或工作指令，请改成可发布标题')
    if not body:
        errors.append('正文不能为空')
    else:
        if PLACEHOLDER_RE.search(body):
            errors.append('正文仍包含“待补充/待核实/TODO”等占位内容')
        minimum = channel_min_length(channel)
        actual = _plain_length(body)
        if actual < minimum:
            errors.append(f'正文有效长度约 {actual} 字，低于当前渠道最低要求 {minimum} 字')
    if not facts:
        errors.append('事实依据与来源不能为空（可在内容生产里上传素材或添加网址，生成时自动带出来）')
    elif not SOURCE_RE.search(facts):
        errors.append('事实依据需要包含链接，或使用“来源：/出处：/文件：/报告：”标明出处')

    if not audience:
        warnings.append('未填写目标读者')
    if not objective:
        warnings.append('未填写写作目标')
    if not channel:
        warnings.append('未填写目标渠道，当前按通用长文检查')
    if not summary:
        warnings.append('摘要为空，发布前建议补充')
    if body and not re.search(r'^#{2,3}\s+\S+', body, re.M):
        warnings.append('正文缺少清晰的小节标题')
    missing_keywords = [word for word in keywords if word.casefold() not in body.casefold()]
    if missing_keywords:
        warnings.append('以下关键词未出现在正文：' + '、'.join(missing_keywords[:8]))

    return {
        'passed': not errors,
        'errors': errors,
        'warnings': warnings,
        'checked_at': now(),
        'plain_length': _plain_length(body),
        'minimum_length': channel_min_length(channel),
    }


def capability():
    base = _text(os.environ.get('GEO_CONTENT_LLM_BASE_URL'))
    model = _text(os.environ.get('GEO_CONTENT_LLM_MODEL'))
    key = _text(os.environ.get('GEO_CONTENT_LLM_API_KEY'))
    configured = bool(base and model)
    return {
        'engine': 'openai-compatible' if configured else 'local-safe',
        'configured': configured,
        'model': model if configured else '',
        'auth_configured': bool(key),
        'message': ('已配置内容模型：生成可编辑的初稿（仍需人工核对与审核）' if configured else
                    '未配置内容模型：只能生成素材整理稿（分组摘录），不能成稿；配置 GEO_CONTENT_LLM_BASE_URL 与 GEO_CONTENT_LLM_MODEL 后可生成初稿'),
    }


def _clean_title(value):
    title = INSTRUCTION_TITLE_RE.sub('', _text(value)).strip(' ：:，,。')
    if len(title) > 72:
        title = title[:70].rstrip('，,；;：: ') + '…'
    return title


def _title_for(asset, bundle):
    suggested = _text(bundle.get('suggested_title'))
    if suggested:
        return _clean_title(suggested)
    current = _clean_title(asset.get('title'))
    if current:
        return current
    question = _text(bundle.get('question'))
    business = _text((bundle.get('profile') or {}).get('business'))
    if question:
        return _clean_title(question)
    return (business + '实用指南') if business else '实用决策指南'


def _facts_text(asset, bundle):
    """Never invent a source. Empty means 'no verifiable source yet', which the gate blocks."""
    facts = _text(asset.get('facts'))
    if facts:
        return facts
    lines = []
    for material in bundle.get('materials') or []:
        label = _text(material.get('label'))
        if material.get('kind') == 'url' and _text(material.get('url')):
            lines.append('来源：' + _text(material['url']) + '（待人工确认）')
        elif material.get('kind') == 'file' and _text(material.get('file_name')):
            lines.append('文件：' + _text(material['file_name']) + '（待人工确认）')
        elif label:
            lines.append('来源：粘贴素材「' + label + '」（待人工确认）')
    profile = bundle.get('profile') or {}
    for page in (profile.get('official_pages') or [])[:5]:
        url = page.get('url') or page.get('link') or '' if isinstance(page, dict) else str(page)
        if _text(url):
            lines.append('官方页面：' + _text(url))
    return '\n'.join(lines)


def _section_library(question_id, question):
    qid = _text(question_id).upper()
    if qid == 'Q01':
        return [
            ('先判断需求是否具体', '不要先从服务商名单出发，而要先明确设备类型、管理范围、使用角色、现有系统和预期改善指标。需求越模糊，后续推荐越容易停留在品牌罗列。'),
            ('比较服务商时看五个维度', '建议依次核对产品覆盖范围、行业适配经验、数据集成能力、实施与运维机制、合同和退出安排。每个维度都应要求对应的页面、文档、案例或演示记录。'),
            ('把“适合”落到可验证场景', '同一家服务商未必适合所有组织。应说明适用的资产规模、团队能力、部署条件和实施阶段，同时写清不适用边界，避免只有结论而没有选择依据。'),
        ]
    if qid == 'Q02':
        return [
            ('预算不只是软件报价', '完整预算通常需要分别核对软件许可或订阅、实施配置、数据治理、接口集成、硬件与环境、培训、运维和后续扩展。只比较一个总价，容易漏掉上线后的持续成本。'),
            ('用统一口径比较报价', '让候选方在同一需求清单和时间范围内报价，并区分一次性费用、周期性费用和按使用量变化的费用。无法明确边界的项目，应列为待确认项而不是直接计入低价优势。'),
            ('提前识别价格风险', '重点核对用户数、设备数、接口数、实施人天、差旅、升级、二次开发和数据迁移是否另行收费，并在合同中写清验收条件、变更机制和续费规则。'),
        ]
    if qid == 'Q03':
        return [
            ('资质只能证明部分能力', '资质证书可以作为基础核验材料，但不能替代产品演示、项目方法和交付团队验证。应同时检查证书主体、有效期、适用范围以及与本项目的关联。'),
            ('案例要核对可比性', '案例核验应关注行业、资产类型、项目范围、上线时间、实际使用部门和可联系的证明主体。只有客户名称而没有实施范围，不能直接证明方案适配。'),
            ('合同条款决定交付边界', '合同应明确功能范围、数据责任、接口清单、里程碑、验收方法、服务等级、知识产权、变更费用和退出机制，避免把关键承诺留在口头沟通中。'),
        ]
    if qid == 'Q04':
        return [
            ('风险往往从基础数据开始', '设备编码、台账口径、组织权限和历史数据如果没有先统一，系统上线后会把原有问题放大。项目启动前应先做数据盘点和责任确认。'),
            ('实施不是单纯安装软件', '业务流程、岗位分工、系统配置、接口联调和用户培训需要同步推进。只关注功能清单而忽略使用机制，容易出现系统上线但一线不用的情况。'),
            ('用阶段验收降低不确定性', '可以按试点、扩围和稳定运行设置阶段目标，每个阶段保留问题清单、责任人、截止时间和验收证据，再决定是否进入下一阶段。'),
        ]
    if qid == 'Q05':
        return [
            ('先看内部是否具备持续能力', '自行处理不仅需要一次性的技术人员，还需要长期的数据维护、流程管理、培训和问题响应能力。若关键工作无人负责，短期节省的外部费用可能转化为长期停滞。'),
            ('适合委托外部团队的情况', '当需求涉及多系统集成、跨部门推进、行业方法沉淀或明确交付周期时，外部团队通常更容易提供完整资源，但仍需由内部负责人掌握目标、数据和验收权。'),
            ('常见做法是明确分工而非二选一', '企业可以保留需求决策、数据责任和验收能力，把产品实施、接口开发或专项咨询交给外部团队，并在合同中约定知识转移和退出安排。'),
        ]
    return [
        ('先把问题拆成可核验条件', '将需求、适用范围、约束条件和验收结果分别列出，避免用“专业、领先、成熟”等形容词替代事实。'),
        ('用证据支持每个判断', '涉及主体能力、资质、案例、价格或效果的内容，应提供官方页面、合同材料、项目文件或其他可追溯来源。无法核实的信息应明确标注并交由人工确认。'),
        ('给出选择边界', '高质量内容不仅要说明什么情况下适合，也要说明限制、风险和不适用场景，让读者可以据此做出下一步判断。'),
    ]


def _template_for(asset, bundle):
    """按写作目标/问题选模板。产品介绍与“怎么选”是两类不同的文章，不能共用一套骨架。"""
    haystack = ' '.join(_text(asset.get(key)) for key in ('objective', 'brief', 'title', 'keywords'))
    haystack += ' ' + _text(bundle.get('question'))
    if re.search(r'功能|模块|介绍|产品|能做什么|能力|方案说明|系统概述', haystack):
        return 'product'
    qid = _text(bundle.get('question_id')).upper()
    return qid if qid in {'Q01', 'Q02', 'Q03', 'Q04', 'Q05'} else 'generic'


def _group_label(value):
    return {'Q01': '选型', 'Q02': '预算', 'Q03': '核验', 'Q04': '风险', 'Q05': '自建与委托'}.get(value, value)


def _material_section(overview, facts):
    """素材部分：按分组写，表格类用分组+合并同类项，其他用折叠后的条目。

    产品介绍与「怎么选」两类文章都需要它：后者同样要用自己主体的事实来支撑判断。
    """
    parts = []
    groups = overview.get('groups') or []
    total = overview.get('rows') or 0
    if groups:
        names = '、'.join(group['name'] for group in groups)
        total_groups = overview.get('group_total', len(groups))
        lead = (f'素材中共 {total} 条功能记录，分为 {total_groups} 个模块：{names}。'
                if total_groups <= len(groups) else
                f'素材中共 {total} 条功能记录，可归入 {total_groups} 个模块，主要包括：{names}。')
        parts.append(lead + '以下按模块列出可核对的功能条目。')
        for group in groups:
            bullets = []
            for item in group['samples']:
                labels = item['labels']
                if item['count'] > 1 and len(labels) > 1:
                    shown = '、'.join(labels[:6])
                    bullets.append(f'- {item["detail"]}（适用于：{shown} 共 {item["count"]} 项）')
                elif labels and labels[0] not in item['detail']:
                    bullets.append(f'- {labels[0]}：{item["detail"]}')
                else:
                    bullets.append(f'- {item["detail"]}')
            parts.extend([f'### {group["name"]}（{group["count"]} 条）',
                          '\n'.join(bullets) if bullets else '- 这个模块素材里没有可引用的说明'])
    else:
        others = overview.get('others') or []
        if others:
            parts.append('素材里可直接引用的条目：')
            parts.append('\n'.join(
                f'- {item["text"]}' + (f'（同类 {item["count"]} 条）' if item['count'] > 1 else '')
                for item in others))
    lines = _clean_source_lines(facts)
    if lines:
        parts.extend(['## 内容依据', '\n'.join(f'- {line}' for line in lines[:8])])
    if not parts:
        parts.append('## 内容依据')
        parts.append('本稿没有素材依据（本次没有上传附件、网址或粘贴文本），不能作为发布内容。')
    return parts


def _advice_body(kind, asset, bundle, overview, facts):
    """选型/预算/核验类：面向读者的判断方法 + 自己主体可核对的事实。"""
    profile = bundle.get('profile') or {}
    business = _text(profile.get('business')) or '相关方案'
    audience = _text(asset.get('audience')) or _text(profile.get('audience')) or '正在评估方案的负责人'
    parts = [f'面向{audience}，本文讨论{business}在{_group_label(kind) if kind != "generic" else "评估与落地"}'
             f'环节需要核对什么。下面每一项都可以直接拿去问供应商或写进需求清单。']
    for heading, paragraph in _section_library(kind if kind != 'generic' else '', bundle.get('question')):
        parts.extend([f'## {heading}', paragraph])
    parts.extend(_material_section(overview, facts))
    return parts


def local_generate(asset, bundle):
    """本地引擎：产出结构化「素材整理稿」，不写营销文案。

    正文里不出现任何面向操作员的文字（写作要求、「待确认」、「下一步」、核验提醒）——
    那些进 notes，由界面单独展示为「生成说明」。
    """
    profile = bundle.get('profile') or {}
    title = _title_for(asset, bundle)
    facts = _facts_text(asset, bundle)
    overview = material_overview(bundle.get('materials'))
    kind = _template_for(asset, bundle)

    if kind == 'product':
        parts = _material_section(overview, facts)
        summary = '按素材整理的系统功能与模块清单，逐条可核对。'
    else:
        parts = _advice_body(kind, asset, bundle, overview, facts)
        summary = f'围绕{_text(bundle.get("question")) or title}，列出需要核对的判断维度与依据。'
    body = '\n\n'.join(part for part in parts if str(part).strip())

    notes = [_local_engine_note()]
    materials = bundle.get('materials') or []
    if materials:
        usable = sum(1 for item in materials if item.get('chars'))
        notes.append(f'本次读取素材 {len(materials)} 个（可用 {usable} 个）；'
                     f'从 {overview.get("rows", 0)} 行里整理出 '
                     f'{overview.get("group_total") or len(overview.get("groups") or [])} 个分组。')
    else:
        notes.append('本次没有任何可用素材，正文没有事实依据，无法审核与发布。')
    notes.append('成稿还需要的材料：一句话产品定位、适用客户与规模、与替代方案的差异、'
                 '交付周期、报价口径、可公开案例、联系方式。')
    warnings = ['本稿是素材整理稿，不是可发布的成稿；主体能力、案例、报价和效果仍需人工核验。']
    return {'title': title, 'summary': summary, 'body': body, 'facts': facts,
            'engine': 'local-safe', 'model': '', 'warnings': warnings, 'notes': notes}


def _endpoint(base):
    value = base.rstrip('/')
    return value if value.endswith('/chat/completions') else value + '/chat/completions'


def _extract_json(text):
    value = _text(text)
    value = re.sub(r'^```(?:json)?\s*', '', value, flags=re.I)
    value = re.sub(r'\s*```$', '', value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', value, re.S)
        if not match:
            raise ValueError('内容模型没有返回可解析的 JSON')
        return json.loads(match.group(0))


def openai_generate(asset, bundle):
    base = _text(os.environ.get('GEO_CONTENT_LLM_BASE_URL'))
    model = _text(os.environ.get('GEO_CONTENT_LLM_MODEL'))
    key = _text(os.environ.get('GEO_CONTENT_LLM_API_KEY'))
    if not base or not model:
        raise ValueError('内容模型未配置')
    brief = {
        'current_title': _text(asset.get('title')),
        'channel': _text(asset.get('channel')),
        'audience': _text(asset.get('audience')),
        'objective': _text(asset.get('objective')),
        'tone': _text(asset.get('tone')),
        'keywords': _text(asset.get('keywords')),
        'target_length': int(asset.get('target_length') or 1200),
        'verified_facts': _text(asset.get('facts')),
    }
    system = (
        '你是企业内容编辑，为『{channel}』写一篇可发布的中文文章。'
        '写作任务书里的 objective 是本文要达成的目标，请直接写成读者看的内容，'
        '绝不要把任务书、写作要求、“待确认”、“下一步”之类面向写作者的话写进正文。'
        '诊断平台回答只用于理解用户会问什么，不得当成企业事实。'
        '素材（附件、网页、粘贴文本）是唯一的事实来源：可以从中提取并改写企业介绍、产品能力、'
        '功能模块、交付方式、指标与案例，但不得超出素材范围，不得编造资质、案例、客户、价格、'
        '效果或承诺；素材没写的就写决策方法与核验清单，不要补虚构事实。'
        '结构要求：开头一段直接进入主题（禁止“本文将介绍…”“本文的目标是…”这类套话）；'
        '正文用 Markdown 二级标题组织，功能/模块类内容可以带三级标题和列表；结尾给可执行的下一步。'
        '文风：具体、克制、自然，避免“赋能/闭环/降维打击/领先/专业”这类词。'
        '返回严格 JSON，字段仅含 title、summary、body；body 内不要再出现一级标题。'
    ).replace('{channel}', _text(asset.get('channel')) or '通用渠道')
    material_text = _text(bundle.get('materials_text'))
    if len(material_text) > 16000:
        material_text = material_text[:16000]
    overview = material_overview(bundle.get('materials'))
    skeleton = ''
    if overview.get('groups'):
        skeleton = '素材已整理出的模块结构（供参考，不必照抄）：\n' + '\n'.join(
            f"- {group['name']}（{group['count']} 条）：" +
            '；'.join(item['detail'][:60] for item in group['samples'][:3])
            for group in overview['groups'])
    user = ('写作任务书：\n' + json.dumps(brief, ensure_ascii=False) +
            ('\n\n' + skeleton if skeleton else '') +
            ('\n\n素材原文（事实来源，逐字抓取或提取，请从中取材）：\n' + material_text if material_text else
             '\n\n素材原文：无（本次没有上传附件、网址或粘贴文本）') +
            '\n\n诊断与项目背景（仅供参考，不是企业事实）：\n' +
            json.dumps({key: bundle.get(key) for key in
                        ('profile', 'question', 'question_id', 'observations', 'evidence_files')},
                       ensure_ascii=False)[:8000])
    payload = json.dumps({
        'model': model,
        'temperature': 0.3,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'response_format': {'type': 'json_object'},
    }, ensure_ascii=False).encode('utf8')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = request.Request(_endpoint(base), data=payload, headers=headers, method='POST')
    try:
        with request.urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode('utf8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf8', errors='replace')[:500]
        raise ValueError(f'内容模型请求失败（HTTP {exc.code}）：{detail}') from exc
    except Exception as exc:
        raise ValueError(f'内容模型请求失败：{exc}') from exc
    try:
        text = data['choices'][0]['message']['content']
        output = _extract_json(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('内容模型响应格式不符合要求') from exc
    title = _text(output.get('title'))
    body = _text(output.get('body'))
    if not title or not body:
        raise ValueError('内容模型返回的标题或正文为空')
    return {'title': title, 'summary': _text(output.get('summary')), 'body': body,
            'facts': _facts_text(asset, bundle), 'engine': 'openai-compatible',
            'model': model, 'warnings': []}


def generate(asset, bundle):
    status = capability()
    result = openai_generate(asset, bundle) if status['configured'] else local_generate(asset, bundle)
    result['generated_at'] = now()
    return result
