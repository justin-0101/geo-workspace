"""Validated diagnostic configuration, separate from publishing destinations."""
from urllib.parse import urlparse

DIAGNOSIS_PLATFORMS = [
    dict(id='deepseek', label='DeepSeek', entry_url='https://chat.deepseek.com/', required_mode='默认对话', login_required=True),
    dict(id='doubao', label='豆包', entry_url='https://www.doubao.com/chat/', required_mode='默认助手', login_required=True),
    dict(id='qianwen', label='千问', entry_url='https://www.qianwen.com/', required_mode='默认助手', login_required=True),
    dict(id='metaso', label='秘塔', entry_url='https://metaso.cn/', required_mode='默认联网搜索', login_required=True),
]
PUBLISH_PLATFORMS = [dict(id=k, label=v) for k, v in [
    ('wechat_mp','微信公众号'), ('zhihu','知乎'), ('baijia','百家号'), ('toutiao','今日头条'),
    ('csdn','CSDN'), ('xiaohongshu','小红书'), ('official_site','官网'), ('sohu','搜狐号'),
    ('dayu','大鱼号'), ('baike','百度百科')]]


def suggest_questions(profile):
    name = str(profile.get('canonical_name', '')).strip()
    business = str(profile.get('business', '')).strip()
    region = str(profile.get('region', '')).strip()
    audience = str(profile.get('audience', '')).strip()
    if not all([name, business, region, audience]):
        raise ValueError('请先填写主体名称、主营业务、服务区域和目标客户')
    prompts = [
        f'{region}的{audience}需要{business}，请推荐三个服务商并说明适用场景和选择依据。',
        f'{audience}购买{business}一般需要多少预算？有哪些收费项目需要核对？',
        f'在{region}选择{business}服务商，应核验哪些资质、案例和合同条款？',
        f'{audience}采用{business}时，常见实施风险有哪些，如何规避？',
        f'针对{audience}的{business}需求，自行处理与委托服务商分别适合什么情况？',
        f'{name}是什么主体，主要提供哪些产品或服务，面向哪些客户？请注明信息来源。',
        f'{name}是否适合{audience}的{business}需求？哪些信息已能验证，哪些还需核实？',
        f'选择{name}与同类{business}服务商时应如何比较？请区分已知事实与无法核实的信息。',
    ]
    return [dict(id=f'Q{i:02}', type='non_brand' if i <= 5 else 'brand_cognition' if i <= 7 else 'comparison',
                 business_value='high', prompt=p) for i, p in enumerate(prompts, 1)]


def freeze_config(profile, questions, platform_ids):
    errors = []
    required = [('canonical_name','主体名称'), ('business','主营业务'), ('region','服务区域'), ('audience','目标客户')]
    for key, label in required:
        if not isinstance(profile.get(key), str) or not profile[key].strip(): errors.append(f'请填写{label}')
    aliases = profile.get('aliases', [])
    pages = profile.get('official_pages', [])
    if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases): errors.append('别名格式错误'); aliases = []
    if not isinstance(pages, list) or any(not isinstance(x, str) for x in pages): errors.append('官方链接格式错误'); pages = []
    for page in pages:
        url = urlparse(page)
        if url.scheme not in {'http','https'} or not url.hostname: errors.append('官方链接必须是完整的 HTTP/HTTPS 地址')
    if not pages and profile.get('no_official_web_presence') is not True: errors.append('请填写官方链接，或确认暂无官方页面')
    if pages and profile.get('no_official_web_presence') is True: errors.append('已有官方链接，不能同时勾选暂无官方页面')
    allowed = {x['id'] for x in DIAGNOSIS_PLATFORMS}
    if not isinstance(platform_ids, list) or not platform_ids or any(not isinstance(x,str) or x not in allowed for x in platform_ids):
        errors.append('请选择有效的诊断平台'); platform_ids = []
    if len(set(platform_ids)) != len(platform_ids): errors.append('诊断平台不能重复')
    if not isinstance(questions, list) or len(questions) != 8:
        errors.append('需要确认八道诊断问题'); questions = []
    terms = [str(profile.get('canonical_name', '')).strip()] + aliases + pages
    prompts = []
    for i, q in enumerate(questions, 1):
        if not isinstance(q,dict): errors.append(f'问题 Q{i:02} 格式错误'); continue
        expected_type = 'non_brand' if i <= 5 else 'brand_cognition' if i <= 7 else 'comparison'
        prompt = str(q.get('prompt','')).strip()
        if q.get('id') != f'Q{i:02}' or q.get('type') != expected_type: errors.append(f'问题 Q{i:02} 的编号或分类不正确')
        if not prompt: errors.append(f'请填写问题 Q{i:02}')
        if q.get('business_value') not in {'high','medium','low'}: errors.append(f'问题 Q{i:02} 缺少业务价值')
        if i <= 5 and any(t and t.casefold() in prompt.casefold() for t in terms): errors.append(f'非品牌题 Q{i:02} 不能包含主体名称、别名或官方链接')
        prompts.append(prompt)
    if prompts and len(set(prompts)) != 8: errors.append('诊断问题不能重复')
    if errors: raise ValueError('；'.join(dict.fromkeys(errors)))
    platforms = [dict(p) for p in DIAGNOSIS_PLATFORMS if p['id'] in platform_ids]
    return dict(brand=dict(canonical_name=profile['canonical_name'].strip(), aliases=aliases,
                          official_pages=pages, official_domains=sorted({urlparse(p).hostname for p in pages}),
                          no_official_web_presence=profile.get('no_official_web_presence') is True,
                          known_competitors=profile.get('competitors', [])),
                scope=dict(repetitions=1, expected_questions=8, expected_platforms=len(platforms),
                           expected_tasks=8*len(platforms), questions=questions),
                platforms=platforms, evidence=dict(save_screenshot=True,
                screenshot_must_include_question=True, screenshot_must_include_full_answer=True))
