"""微信公众号官方接口客户端：把内容写入公众号草稿箱。

只用官方服务端接口（不模拟登录、不导出 Cookie）：
- GET  /cgi-bin/token              获取 access_token
- POST /cgi-bin/draft/add          新增草稿（草稿箱）
- POST /cgi-bin/material/add_material  上传永久图片素材（封面用，可选）

文档：https://developers.weixin.qq.com/doc/subscription/api/draftbox/draftmanage/api_draft_add

注意：
- AppSecret 只从本机凭据存储读取，异常信息中不会带出密钥；
- 官方要求调用来源 IP 在公众号后台的 IP 白名单内；
- 新建草稿成功后内容是“草稿箱里的草稿”，还需要在公众号后台群发，因此不算已发布上线。
"""
import json
import time
import html as html_lib

import httpx

API_BASE = 'https://api.weixin.qq.com'
TOKEN_ERROR_CODES = {40001, 40014, 42001}      # access_token 失效，需要重取

ERROR_HINTS = {
    40001: 'access_token 无效，已自动重试；若持续失败请重新保存 AppSecret。',
    40164: '调用来源 IP 不在白名单。请在公众号后台把本机公网 IP 加入 IP 白名单后重试。',
    40013: 'AppID 无效，请检查是否填错（注意不要填成原始 ID 或公众号名称）。',
    40125: 'AppSecret 无效，请在公众号后台重置后重新填写。',
    48001: '该公众号没有“新建草稿”接口权限：需要认证的服务号/订阅号并开通接口权限。',
    45009: '接口调用次数已达上限，请稍后再试。',
    53500: '草稿内容不合法：常见原因是标题超长或正文为空。',
    40007: 'media_id 不合法。',
}


class WechatError(RuntimeError):
    def __init__(self, errcode, errmsg=''):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f'serrcode {errcode}：{ERROR_HINTS.get(errcode, errmsg or "未知错误")}')


def markdown_to_html(text):
    """把纯文本/Markdown 粗转成公众号可接受的 HTML。只做结构转换，不改写内容。"""
    lines = str(text or '').replace('\r\n', '\n').split('\n')
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('### '):
            out.append(f'<h3>{html_lib.escape(stripped[4:])}</h3>')
        elif stripped.startswith('## '):
            out.append(f'<h2>{html_lib.escape(stripped[3:])}</h2>')
        elif stripped.startswith('# '):
            out.append(f'<h2>{html_lib.escape(stripped[2:])}</h2>')
        elif stripped.startswith(('- ', '* ')):
            out.append(f'<p>· {html_lib.escape(stripped[2:])}</p>')
        else:
            out.append(f'<p>{html_lib.escape(stripped)}</p>')
    return ''.join(out) or '<p></p>'


class WechatMpClient:
    def __init__(self, appid, secret, timeout=20.0, client=None):
        self.appid = appid
        self.secret = secret
        self._client = client or httpx.Client(timeout=timeout)
        self._token = None
        self._expires_at = 0.0

    # --- 底层调用 -----------------------------------------------------------
    def _request(self, method, path, params=None, payload=None):
        url = API_BASE + path
        if method == 'GET':
            response = self._client.get(url, params=params or {})
        else:
            body = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
            response = self._client.post(url, params=params or {}, content=body)
        try:
            data = response.json()
        except ValueError:
            raise WechatError(0, f'返回内容无法解析（HTTP {response.status_code}）')
        if data.get('errcode'):
            raise WechatError(data['errcode'], data.get('errmsg', ''))
        return data

    def access_token(self, force=False):
        if not force and self._token and time.time() < self._expires_at:
            return self._token
        if not self.appid or not self.secret:
            raise WechatError(0, 'AppID 或 AppSecret 未配置')
        data = self._request('GET', '/cgi-bin/token', params={
            'grant_type': 'client_credential', 'appid': self.appid, 'secret': self.secret})
        self._token = data['access_token']
        self._expires_at = time.time() + int(data.get('expires_in', 7200)) - 300
        return self._token

    # --- 业务接口 -----------------------------------------------------------
    def check(self):
        """只验证凭据是否可用，不写任何内容。"""
        self.access_token(force=True)
        return {'ok': True, 'message': '凭据可用，access_token 获取成功'}

    def add_draft(self, title, content, digest='', author='', thumb_media_id='', source_url=''):
        title = (title or '').strip()
        if not title:
            raise WechatError(0, '标题为空')
        if len(title) > 64:
            title = title[:64]
        article = {
            'title': title,
            'author': (author or '')[:8],
            'digest': (digest or '')[:120],
            'content': markdown_to_html(content) if '<' not in str(content or '') else str(content),
            'content_source_url': source_url or '',
            'need_open_comment': 0,
            'only_fans_can_comment': 0,
        }
        if thumb_media_id:
            article['thumb_media_id'] = thumb_media_id
        payload = {'articles': [article]}
        last_error = None
        for attempt in (1, 2):
            token = self.access_token(force=(attempt == 2))
            try:
                data = self._request('POST', '/cgi-bin/draft/add',
                                     params={'access_token': token}, payload=payload)
                return {'media_id': data.get('media_id', ''), 'title': title}
            except WechatError as exc:
                last_error = exc
                if exc.errcode in TOKEN_ERROR_CODES and attempt == 1:
                    continue
                raise
        raise last_error

    def upload_image(self, filename, content_bytes):
        """上传永久图片素材，返回 media_id（封面用）。"""
        token = self.access_token()
        files = {'media': (filename, content_bytes)}
        response = self._client.post(API_BASE + '/cgi-bin/material/add_material',
                                     params={'access_token': token, 'type': 'image'}, files=files)
        data = response.json()
        if data.get('errcode'):
            raise WechatError(data['errcode'], data.get('errmsg', ''))
        return data.get('media_id', '')
