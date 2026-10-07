"""Validated diagnostic configuration and deterministic local question generation."""
from urllib.parse import urlparse
import re

import publish_adapters

DIAGNOSIS_PLATFORMS = [
    dict(id='deepseek', label='DeepSeek', entry_url='https://chat.deepseek.com/', required_mode='默认对话', login_required=True),
    dict(id='doubao', label='豆包', entry_url='https://www.doubao.com/chat/', required_mode='默认助手', login_required=True),
    dict(id='qianwen', label='千问', entry_url='https://www.qianwen.com/', required_mode='默认助手', login_required=True),
    dict(id='metaso', label='秘塔', entry_url='https://metaso.cn/', required_mode='默认联网搜索', login_required=True),
]
# 发布平台的唯一来源：publish_adapters 为每个平台预留了接入接口契约。
PUBLISH_PLATFORMS = [dict(id=spec.id, label=spec.label) for spec in publish_adapters.PLATFORM_SPECS]

# 这些是配置页可编辑的可选场景资料。不同历史版本可能使用过不同字段名，
# 读取时兼容它们，保存时由工作台统一写入 scenarios/trigger_problems/alternatives。
_OPTIONAL_PROFILE_FIELDS = {
    'scenarios': ('scenarios', 'customer_scenarios', 'typical_scenarios', 'typical_customer_scenarios'),
    'trigger_problems': ('trigger_problems', 'customer_triggers', 'common_trigger_problems'),
    'alternatives': ('alternatives', 'alternative_solutions', 'competitors'),
}

QUESTION_SLOTS = (
    dict(subcat='recommendation', intent='购买决策/推荐', layer='query-intent', business_value='high', time_sensitivity='low', trigger_intensity='high'),
    dict(subcat='pricing', intent='预算/收费', layer='query-intent', business_value='high', time_sensitivity='medium', trigger_intensity='medium'),
    dict(subcat='alternative-comparison', intent='替代方案比较', layer='query-intent', business_value='high', time_sensitivity='low', trigger_intensity='medium'),
    dict(subcat='risk-scenario', intent='真实场景/避坑', layer='edge-real-world', business_value='medium', time_sensitivity='medium', trigger_intensity='medium'),
    dict(subcat='implementation-scenario', intent='典型场景/实施选择', layer='edge-real-world', business_value='medium', time_sensitivity='low', trigger_intensity='medium'),
    dict(subcat='brand-identity', intent='品牌认知', layer='query-intent', business_value='high', time_sensitivity='low', trigger_intensity='low'),
    dict(subcat='brand-fit', intent='品牌适用性', layer='query-intent', business_value='medium', time_sensitivity='medium', trigger_intensity='low'),
    dict(subcat='competitive-comparison', intent='品牌/竞品对比', layer='query-intent', business_value='high', time_sensitivity='low', trigger_intensity='high'),
)

# 生成题和手工题都禁止出现这些审计人员口吻；它们不是用户向 AI 的自然问题。
AUDIT_PHRASES = (
    '请注明信息来源', '注明信息来源', '信息来源',
    '哪些信息已能验证', '哪些还需核实', '哪些信息还需核实',
    '已知事实与无法核实', '请区分已知事实', '无法核实的信息',
    '请提供证据', '证据链', '审计', '核验来源',
)
_PLACEHOLDERS = ('待填写', '待补充', '请填写', 'CHANGE_ME', 'TODO', '示例主体', 'xxx', 'XXX')


def _profile_texts(profile, names):
    """Accept an optional list or newline/comma separated text without a migration."""
    for name in names:
        if name not in profile or profile[name] in (None, ''):
            continue
        value = profile[name]
        if isinstance(value, list):
            result = [str(item).strip() for item in value if str(item).strip()]
            if result:
                return result
            continue
        if isinstance(value, str):
            result = [item.strip() for item in re.split(r'[\n,，、;；]+', value) if item.strip()]
            if result:
                return result
    return []


def _one(values, fallback):
    return values[0] if values else fallback


def _safe_context(values, profile):
    """Keep optional context from leaking brand terms into Q01-Q05."""
    terms = _brand_terms(profile)
    safe = []
    for value in values:
        text = str(value).strip()
        lowered = text.casefold()
        if any(term.casefold() in lowered for term in terms):
            continue
        if 'http://' in lowered or 'https://' in lowered or any(phrase in text for phrase in AUDIT_PHRASES):
            continue
        safe.append(text)
    return safe


def _brand_names(profile):
    name = str(profile.get('canonical_name', '')).strip()
    aliases = profile.get('aliases', [])
    if not isinstance(aliases, list):
        aliases = []
    return list(dict.fromkeys(x for x in [name] + [str(a).strip() for a in aliases if str(a).strip()] if x))


def _brand_terms(profile):
    pages = profile.get('official_pages', [])
    if not isinstance(pages, list):
        pages = []
    terms = _brand_names(profile)
    terms += [str(x).strip() for x in pages if str(x).strip()]
    terms += [urlparse(str(x)).hostname or '' for x in pages]
    return list(dict.fromkeys(x for x in terms if x))


def _question(slot, prompt, index):
    return dict(id=f'Q{index:02}', type='non_brand' if index <= 5 else 'brand_cognition' if index <= 7 else 'comparison',
                prompt=prompt, **slot)


def suggest_questions(profile):
    """Generate eight stable, local-only questions from facts and optional scenarios.

    The profile is data, not an instruction: optional scenario text is used only to
    make the user's situation concrete. No network or model call is made here.
    """
    required = [('canonical_name', '主体名称'), ('business', '主营业务'),
                ('region', '服务区域'), ('audience', '目标客户')]
    values = {key: str(profile.get(key, '')).strip() for key, _ in required}
    if not all(values.values()):
        raise ValueError('请先填写主体名称、主营业务、服务区域和目标客户')
    name, business, region, audience = (values[k] for k, _ in required)
    scenarios = _safe_context(_profile_texts(profile, _OPTIONAL_PROFILE_FIELDS['scenarios']), profile)
    triggers = _safe_context(_profile_texts(profile, _OPTIONAL_PROFILE_FIELDS['trigger_problems']), profile)
    alternatives = _safe_context(_profile_texts(profile, _OPTIONAL_PROFILE_FIELDS['alternatives']), profile)
    scenario = _one(scenarios, f'{audience}第一次考虑{business}')
    trigger = _one(triggers, f'{audience}遇到{business}相关问题')
    alternative = _one(alternatives, f'自行处理或采用其他{business}方案')
    # 每题只设一个主意图；后半句仅提供一个必要的决策约束。
    prompts = [
        f'我在{region}，{scenario}，想找{business}服务，应该怎么选？',
        f'{region}的{business}一般怎么收费？预算主要受哪些因素影响？',
        f'{audience}选择{business}服务，和{alternative}相比应该怎么取舍？',
        f'如果{trigger}，找{business}服务时最该先解决什么？',
        f'针对{scenario}，{business}服务通常怎样落地更合适？',
        f'{name}是做什么的？主要服务哪些{audience}？',
        f'{name}适合{scenario}吗？什么情况下值得优先考虑？',
        f'在{region}选择{name}，和{alternative}相比各适合什么情况？',
    ]
    return [_question(slot, prompt, index) for index, (slot, prompt) in
            enumerate(zip(QUESTION_SLOTS, prompts), 1)]


def check_question_quality(profile, questions):
    """Return human-readable quality gate results without raising an exception."""
    errors = []
    warnings = []
    if not isinstance(questions, list) or len(questions) != 8:
        return {'ok': False, 'errors': ['需要确认八道诊断问题'], 'warnings': []}
    brand_terms = _brand_terms(profile)
    brand_names = _brand_names(profile)
    seen = set()
    for index, question in enumerate(questions, 1):
        qid = f'Q{index:02}'
        if not isinstance(question, dict):
            errors.append(f'问题 {qid} 格式错误')
            continue
        prompt = question.get('prompt') if isinstance(question.get('prompt'), str) else ''
        prompt = prompt.strip()
        expected_type = 'non_brand' if index <= 5 else 'brand_cognition' if index <= 7 else 'comparison'
        if question.get('id') != qid or question.get('type') != expected_type:
            errors.append(f'问题 {qid} 的编号或分类不正确')
        if not prompt:
            errors.append(f'请填写问题 {qid}')
            continue
        if prompt in seen:
            errors.append('诊断问题不能重复')
        seen.add(prompt)
        if any(marker.casefold() in prompt.casefold() for marker in _PLACEHOLDERS):
            errors.append(f'问题 {qid} 仍有待填写内容')
        if any(phrase in prompt for phrase in AUDIT_PHRASES):
            errors.append(f'问题 {qid} 含有不适合用户提问的审计式文案')
        if len(prompt) > 160:
            errors.append(f'问题 {qid} 过长，请保留一个主要问题')
        if len(prompt) < 8:
            errors.append(f'问题 {qid} 太短，无法形成完整提问')
        if prompt.count('?') + prompt.count('？') > 2:
            errors.append(f'问题 {qid} 包含多个完整问题，请只保留一个主意图')
        # A single question can have one constraint, but not a second independent question.
        if prompt.count('；') > 1 or prompt.count(';') > 1:
            errors.append(f'问题 {qid} 包含多个并列问题，请只保留一个主意图')
        if question.get('business_value') not in {'high', 'medium', 'low'}:
            errors.append(f'问题 {qid} 缺少业务价值')
        # New labels are validated when supplied; absent labels remain valid for old projects.
        for field in ('layer', 'subcat', 'intent', 'time_sensitivity', 'trigger_intensity'):
            if field in question and not isinstance(question[field], str):
                errors.append(f'问题 {qid} 的分类说明格式不正确')
        expected = QUESTION_SLOTS[index - 1]
        if 'subcat' in question and question['subcat'] != expected['subcat']:
            errors.append(f'问题 {qid} 的意图分类不匹配')
        if index <= 5 and any(term.casefold() in prompt.casefold() for term in brand_terms):
            errors.append(f'非品牌题 {qid} 不能包含主体名称、别名或官方链接')
        if index >= 6 and brand_names and not any(term.casefold() in prompt.casefold() for term in brand_names):
            errors.append(f'品牌题 {qid} 需要明确提到主体名称或别名')
    if len(seen) != 8 and not any('不能重复' in error for error in errors):
        errors.append('诊断问题不能重复')
    return {'ok': not errors, 'errors': list(dict.fromkeys(errors)), 'warnings': warnings}


# 名称较直观，供调用方和测试使用；旧调用仍只需使用 suggest_questions。
validate_question_quality = check_question_quality


def _validate_profile(profile):
    errors = []
    required = [('canonical_name', '主体名称'), ('business', '主营业务'), ('region', '服务区域'), ('audience', '目标客户')]
    for key, label in required:
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            errors.append(f'请填写{label}')
    aliases = profile.get('aliases', [])
    pages = profile.get('official_pages', [])
    if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
        errors.append('别名格式错误'); aliases = []
    if not isinstance(pages, list) or any(not isinstance(x, str) for x in pages):
        errors.append('官方链接格式错误'); pages = []
    for page in pages:
        url = urlparse(page)
        if url.scheme not in {'http', 'https'} or not url.hostname:
            errors.append('官方链接必须是完整的 HTTP/HTTPS 地址')
    if not pages and profile.get('no_official_web_presence') is not True:
        errors.append('请填写官方链接，或确认暂无官方页面')
    if pages and profile.get('no_official_web_presence') is True:
        errors.append('已有官方链接，不能同时勾选暂无官方页面')
    return errors, aliases, pages


def freeze_config(profile, questions, platform_ids):
    errors, aliases, pages = _validate_profile(profile)
    allowed = {x['id'] for x in DIAGNOSIS_PLATFORMS}
    if not isinstance(platform_ids, list) or not platform_ids or any(not isinstance(x, str) or x not in allowed for x in platform_ids):
        errors.append('请选择有效的诊断平台'); platform_ids = []
    if len(set(platform_ids)) != len(platform_ids):
        errors.append('诊断平台不能重复')
    quality = check_question_quality(profile, questions)
    errors.extend(quality['errors'])
    if errors:
        raise ValueError('；'.join(dict.fromkeys(errors)))
    platforms = [dict(p) for p in DIAGNOSIS_PLATFORMS if p['id'] in platform_ids]
    official_domains = sorted({urlparse(p).hostname for p in pages if urlparse(p).hostname})
    competitors = profile.get('alternatives', profile.get('competitors', []))
    if not isinstance(competitors, list):
        competitors = _profile_texts({'value': competitors}, ('value',))
    return dict(brand=dict(canonical_name=profile['canonical_name'].strip(), aliases=aliases,
                          official_pages=pages, official_domains=official_domains,
                          no_official_web_presence=profile.get('no_official_web_presence') is True,
                          known_competitors=competitors,
                          # 报告渲染只允许用本项目自己的事实；有这几个字段，文案就落到本主体。
                          business=profile['business'].strip(), region=profile['region'].strip(),
                          audience=profile['audience'].strip()),
                scope=dict(repetitions=1, expected_questions=8, expected_platforms=len(platforms),
                           expected_tasks=8 * len(platforms), questions=questions),
                platforms=platforms, evidence=dict(save_screenshot=True,
                screenshot_must_include_question=True, screenshot_must_include_full_answer=True))
