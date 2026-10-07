"""内容生成模型（OpenAI 兼容端点）的本地配置。

约束：
- 只写本机 SQLite（`workspace_preferences`），密钥不写日志、不在接口里回显明文；
- 读取只发生在真正调用模型的那一刻；
- 环境变量优先于界面配置，便于临时覆盖或 CI 跑一次而不动本地设置。
"""
import json
from urllib import error, request

import workspace_store as store

PREFIX = 'content_llm:'
FIELDS = ('base_url', 'model', 'api_key')
LABELS = {'base_url': '接口地址', 'model': '模型名', 'api_key': 'API Key'}

#: 常见端点提示，界面下拉用。值只是默认填充，不锁定。
PRESETS = (
    {'id': 'deepseek', 'label': 'DeepSeek', 'base_url': 'https://api.deepseek.com/v1',
     'model': 'deepseek-chat'},
    {'id': 'minimax', 'label': 'MiniMax（国内）', 'base_url': 'https://api.minimaxi.com/v1',
     'model': 'MiniMax-Text-01'},
    {'id': 'moonshot', 'label': 'Moonshot / Kimi', 'base_url': 'https://api.moonshot.cn/v1',
     'model': 'kimi-k2-0905-preview'},
    {'id': 'custom', 'label': '自定义（任何 OpenAI 兼容端点）', 'base_url': '', 'model': ''},
)


def _key(name):
    return f'{PREFIX}{name}'


def mask(value):
    value = str(value or '')
    if not value:
        return ''
    if len(value) <= 8:
        return '****'
    return f'{value[:4]}****{value[-4:]}'


def stored(name):
    with store.connection() as c:
        row = c.execute('SELECT value FROM workspace_preferences WHERE key=?',
                        (_key(name),)).fetchone()
    return row['value'] if row else ''


def resolved():
    """环境变量优先，其次是本机界面里保存的值。"""
    import os
    env = {'base_url': str(os.environ.get('GEO_CONTENT_LLM_BASE_URL') or '').strip(),
           'model': str(os.environ.get('GEO_CONTENT_LLM_MODEL') or '').strip(),
           'api_key': str(os.environ.get('GEO_CONTENT_LLM_API_KEY') or '').strip()}
    out, source = {}, {}
    for name in FIELDS:
        if env.get(name):
            out[name], source[name] = env[name], 'env'
        else:
            out[name], source[name] = stored(name), ('saved' if stored(name) else '')
    return out, source


def configured():
    values, _ = resolved()
    return bool(values['base_url'] and values['model'])


def save(values):
    """空字符串表示“保持原值不变”，便于界面只改其中一项。"""
    with store.connection() as c:
        for name in FIELDS:
            value = str(values.get(name) or '').strip()
            if not value:
                continue
            c.execute('INSERT INTO workspace_preferences(key,value) VALUES(?,?) '
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (_key(name), value))
        store.record_event(c, None, 'content_llm_saved',
                           f"内容生成模型已更新：{str(values.get('model') or '').strip() or '（沿用原模型）'}")
    return True


def clear():
    with store.connection() as c:
        for name in FIELDS:
            c.execute('DELETE FROM workspace_preferences WHERE key=?', (_key(name),))
        store.record_event(c, None, 'content_llm_cleared', '已清除内容生成模型配置')
    return True


def status():
    values, source = resolved()
    return {
        'configured': configured(),
        'base_url': values['base_url'],
        'model': values['model'],
        'api_key': {'configured': bool(values['api_key']), 'masked': mask(values['api_key'])},
        'source': source,
        'presets': [dict(item) for item in PRESETS],
        # 只说明存放位置与优先级，不回显密钥
        'note': ('密钥只保存在本机数据库；环境变量 GEO_CONTENT_LLM_* 优先于这里的设置。'
                 '此内容模型配置也可供诊断问题候选生成复用；诊断问题仍会经过本地门禁和人工确认。'),
    }


def _endpoint(base):
    value = base.rstrip('/')
    return value if value.endswith('/chat/completions') else value + '/chat/completions'


def check(candidate=None):
    """用一次最小调用验证地址、模型与密钥是否可用。不发素材内容。"""
    values, _ = resolved()
    if candidate:
        for name in FIELDS:
            value = str(candidate.get(name) or '').strip()
            if value:
                values[name] = value
    base, model, key = values['base_url'], values['model'], values['api_key']
    missing = [LABELS[name] for name in ('base_url', 'model') if not values[name]]
    if missing:
        raise ValueError('缺少' + '、'.join(missing))
    payload = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': '只回复两个字：可用'}],
        'max_tokens': 8,
    }, ensure_ascii=False).encode('utf8')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = request.Request(_endpoint(base), data=payload, headers=headers, method='POST')
    try:
        with request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode('utf8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf8', errors='replace')[:300]
        hint = '（密钥或权限问题）' if exc.code in {401, 403} else ''
        raise ValueError(f'模型返回 HTTP {exc.code}{hint}：{detail}') from exc
    except Exception as exc:
        raise ValueError(f'连不上模型端点：{type(exc).__name__}: {exc}'[:300]) from exc
    try:
        reply = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError('端点响应不是 OpenAI 兼容格式（缺少 choices[0].message.content）') from exc
    return {'message': f'连接成功：{model} 已回包「{str(reply).strip()[:20]}」',
            'model': data.get('model') or model}
