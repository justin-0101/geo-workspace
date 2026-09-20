"""平台凭据的本地存储。

约束：
- 只写本机 SQLite（workspace_preferences），不写日志、不返回完整值；
- 读取只发生在调用平台接口的那一刻；
- 对外一律返回“是否已配置 + 掩码”，避免凭据出现在界面或接口响应里。
"""
import workspace_store as store

PREFIX = 'cred:'


def _key(platform_id, name):
    return f'{PREFIX}{platform_id}:{name}'


def mask(value):
    value = str(value or '')
    if not value:
        return ''
    if len(value) <= 6:
        return '****'
    return f'{value[:3]}****{value[-3:]}'


def get(platform_id, name, default=''):
    with store.connection() as c:
        row = c.execute('SELECT value FROM workspace_preferences WHERE key=?',
                        (_key(platform_id, name),)).fetchone()
    return row['value'] if row else default


def set_values(platform_id, values):
    """values 里空字符串表示“保持原值不变”，便于界面只改其中一项。"""
    with store.connection() as c:
        for name, value in values.items():
            value = str(value or '').strip()
            if not value:
                continue
            c.execute('INSERT INTO workspace_preferences(key,value) VALUES(?,?) '
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (_key(platform_id, name), value))
        store.record_event(c, None, 'platform_credentials_saved', f'{platform_id} 凭据已更新')
    return True


def clear(platform_id, names):
    with store.connection() as c:
        for name in names:
            c.execute('DELETE FROM workspace_preferences WHERE key=?', (_key(platform_id, name),))
    return True


def status(platform_id, names):
    """返回每个凭据项是否已配置与掩码值；永不返回明文。"""
    out = {}
    for name in names:
        value = get(platform_id, name)
        out[name] = {'configured': bool(value), 'masked': mask(value)}
    return out


def credentials_ready(platform_id, names):
    return all(bool(get(platform_id, name)) for name in names)
