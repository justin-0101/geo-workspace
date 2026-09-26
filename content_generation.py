"""Evidence-aware content generation and quality gates.

The built-in generator deliberately writes only decision guidance and facts supplied by the
project owner. An optional OpenAI-compatible endpoint can produce a richer draft, but it is
bound by the same source bundle and quality checks.
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
        'message': ('已配置 OpenAI 兼容内容模型' if configured else
                    '未配置内容模型，将使用本地证据安全稿生成器'),
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


def local_generate(asset, bundle):
    profile = bundle.get('profile') or {}
    title = _title_for(asset, bundle)
    audience = _text(asset.get('audience')) or _text(profile.get('audience')) or '正在评估相关方案的负责人'
    objective = _text(asset.get('objective')) or _text(bundle.get('question')) or '帮助读者建立可核验的决策框架'
    business = _text(profile.get('business')) or '相关产品与服务'
    region = _text(profile.get('region'))
    question_id = _text(bundle.get('question_id'))
    question = _text(bundle.get('question'))
    sections = _section_library(question_id, question)

    intro = (f'面向{audience}，这篇内容讨论“{question or title}”。判断{business}是否适合，'
             '不能只看功能名称或宣传口号，而应把需求、证据、实施边界和验收条件放在同一张清单里。'
             f'本文的目标是：{objective.rstrip("。")}。')
    if region:
        intro += f'涉及{region}范围内的服务与交付时，还应结合实际项目地点和服务半径复核。'

    parts = [intro]
    for heading, paragraph in sections:
        parts.extend([f'## {heading}', paragraph])

    # 素材里挑出的候选事实：逐字来自上传或抓取的原文，不当成已验证结论。
    picked = candidate_facts(bundle.get('materials'))
    parts.append('## 可从素材引用的内容')
    if picked:
        parts.append('以下内容逐字来自上面列出的素材，可直接改写进正文；口径、时效和可公开范围仍需人工确认：')
        parts.extend([f'- {item["text"]}（来自：{item["from"]}）' for item in picked])
    else:
        parts.append('当前没有可用的素材（附件、网址或粘贴文本）。发布前应补充官方页面、产品资料或已确认的项目文件。')

    facts = _facts_text(asset, bundle)
    fact_lines = [line.strip() for line in facts.splitlines() if line.strip()]
    parts.append('## 本项目需要核验的主体信息')
    if fact_lines:
        parts.append('以下信息来自素材索引或项目负责人录入的资料，发布前仍需逐条核对其适用范围和时效：')
        parts.extend([f'- {line}' for line in fact_lines[:12]])
    else:
        parts.append('当前没有足够的主体事实来源。发布前应补充官方页面、产品资料、案例证明或项目文件。')

    parts.extend([
        '## 建议的下一步',
        '先把候选方案放入同一份核验表，逐项记录“已证实、待核实、不适用”，再安排演示、访谈或试点。涉及报价、交期、效果、资质和客户案例的内容，应以正式材料和双方确认结果为准。',
        '这套方法的价值不在于快速得出一个品牌结论，而在于减少信息口径不一致、隐性费用和实施边界不清带来的决策风险。',
    ])
    body = '\n\n'.join(parts)
    summary = f'围绕{question or title}，从需求澄清、证据核验、实施边界和验收条件给出一套可执行的判断框架。'
    warnings = ['本稿由本地安全生成器生成；主体能力、案例、报价和效果仍需人工核验。']
    if picked:
        warnings.append(f'已从素材中挑出 {len(picked)} 条候选事实，请核对口径后再写进正文。')
    return {'title': title, 'summary': summary, 'body': body, 'facts': facts,
            'engine': 'local-safe', 'model': '', 'warnings': warnings}


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
        '你是企业内容编辑。只根据给定写作任务书和证据包写中文稿件。'
        '诊断平台回答只用于理解问题，不得当成企业事实。'
        '证据包里的素材（附件、网页、粘贴文本）是事实来源：可以从中提取并改写企业介绍、产品能力、'
        '交付方式、指标与案例，但不得超出素材范围添油加醋，不得编造资质、案例、客户、价格、效果或承诺。'
        '素材不足的部分写成决策方法和核验清单，不要补虚构事实。'
        '文章要具体、克制、自然，避免营销套话。'
        '返回严格 JSON，字段仅含 title、summary、body；body 使用 Markdown 二级标题，不要包含一级标题。'
    )
    material_text = _text(bundle.get('materials_text'))
    if len(material_text) > 16000:
        material_text = material_text[:16000]
    user = ('写作任务书：\n' + json.dumps(brief, ensure_ascii=False) +
            ('\n\n素材原文（事实来源，逐字抓取或提取）：\n' + material_text if material_text else
             '\n\n素材原文：无（本次没有上传附件、网址或粘贴文本）') +
            '\n\n诊断与项目背景：\n' +
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
