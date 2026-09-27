"""发布平台适配器：优先官方 API，其次浏览器自动化，最后才是人工。

- 微信公众号：官方「新建草稿」接口，内容进公众号草稿箱（仍未群发，因此不算已上线）。
- 其他平台：没有可核验的官方发文接口，走浏览器自动化（复用隔离浏览器 profile，登录一次）。
- 百度百科 / 官网：没有通用发文接口，保持人工发布并回填链接。

任何失败都会如实返回状态与原因，绝不把“点了按钮”当作发布成功。
"""
from dataclasses import dataclass

import platform_credentials as credentials

MODES = ('api', 'browser', 'manual')
MODE_LABEL = {'api': '官方 API', 'browser': '浏览器自动化', 'manual': '人工发布'}
ELEMENT_LABEL = {'title': '标题', 'body': '正文', 'facts': '事实依据', 'cover': '封面图'}


@dataclass(frozen=True)
class PlatformSpec:
    id: str
    label: str
    mode: str
    requires: tuple = ('title', 'body')
    credentials: tuple = ()
    entry: str = ''
    note: str = ''


@dataclass(frozen=True)
class PublishResult:
    status: str                    # draft_created | submitted | manual_required | failed
    message: str
    published_url: str = ''
    evidence: str = ''


PLATFORM_SPECS = (
    PlatformSpec('wechat_mp', '微信公众号', 'api', ('title', 'body'), ('appid', 'secret'),
                 '公众号草稿箱（cgi-bin/draft/add）',
                 '内容进草稿箱，仍需在公众号后台群发；需要 IP 白名单与草稿接口权限'),
    PlatformSpec('zhihu', '知乎', 'browser', ('title', 'body'), (), '知乎创作中心', '无官方发文接口'),
    PlatformSpec('baijia', '百家号', 'browser', ('title', 'body'), (), '百家号后台', '无官方发文接口'),
    PlatformSpec('toutiao', '今日头条', 'browser', ('title', 'body'), (), '头条号后台', '无官方发文接口'),
    PlatformSpec('csdn', 'CSDN', 'browser', ('title', 'body'), (), 'CSDN 创作中心', '无官方发文接口'),
    PlatformSpec('xiaohongshu', '小红书', 'browser', ('title', 'body', 'cover'), (), '小红书创作服务平台',
                 '无官方发文接口，且需要封面图'),
    PlatformSpec('sohu', '搜狐号', 'browser', ('title', 'body'), (), '搜狐号后台', '无官方发文接口'),
    PlatformSpec('dayu', '大鱼号', 'browser', ('title', 'body'), (), '大鱼号后台', '无官方发文接口'),
    PlatformSpec('official_site', '官网', 'manual', ('title', 'body'), (), '官网 CMS', '由官网后台人工发布'),
    PlatformSpec('baike', '百度百科', 'manual', ('title', 'body', 'facts'), (), '百科词条编辑',
                 '只有合作渠道，无通用编辑接口'),
)


class PublishAdapter:
    """所有平台共用的接口。"""

    def __init__(self, spec):
        self.spec = spec

    def health(self):
        if self.spec.mode == 'api':
            missing = [name for name in self.spec.credentials
                       if not credentials.get(self.spec.id, name)]
            if missing:
                return {'ready': False, 'known': True,
                        'reason': f"未配置 {', '.join(missing)}"}
            return {'ready': True, 'known': True,
                    'reason': '凭据已配置；发布时校验权限与 IP 白名单'}
        if self.spec.mode == 'browser':
            return {'ready': False, 'known': False,
                    'reason': '需要先在隔离浏览器登录该平台；发布时自动打开并检测'}
        return {'ready': False, 'known': True, 'reason': '该平台没有自动发布接口'}

    def check_asset(self, asset):
        missing = []
        for element in self.spec.requires:
            value = asset.get(element) if isinstance(asset, dict) else None
            if not str(value or '').strip():
                missing.append({'key': element, 'label': ELEMENT_LABEL.get(element, element)})
        return missing

    def capability(self):
        state = self.health()
        return {
            'id': self.spec.id, 'label': self.spec.label,
            'mode': self.spec.mode, 'mode_label': MODE_LABEL.get(self.spec.mode, self.spec.mode),
            'entry': self.spec.entry, 'note': self.spec.note,
            'requires': list(self.spec.requires),
            'credentials': [dict(credentials.status(self.spec.id, [name])[name], name=name)
                            for name in self.spec.credentials],
            'implemented': self.spec.mode in {'api', 'browser'},
            'can_attempt': state.get('ready') or self.spec.mode == 'browser',
            'health': state,
        }

    def publish(self, asset, job=None, evidence_dir=None):
        missing = self.check_asset(asset)
        if missing:
            labels = '、'.join(item['label'] for item in missing)
            return PublishResult('manual_required', f'缺少{labels}，无法自动发布')
        state = self.health()
        if self.spec.mode == 'api':
            if not state.get('ready'):
                return PublishResult('manual_required', state.get('reason', '凭据未配置'))
            return self.submit_api(asset, evidence_dir)
        if self.spec.mode == 'browser':
            return self.submit_browser(asset, evidence_dir)
        return PublishResult('manual_required', state.get('reason', '该平台需要人工发布'))

    # --- 具体实现 -----------------------------------------------------------
    def submit_api(self, asset, evidence_dir=None):
        """微信公众号：备好封面 → 上传永久素材 → 写入草稿箱。

        图文消息（article_type=news）的封面是**必填**的。原来的实现直接调
        草稿接口不带 thumb_media_id，只会换来一个 40007，看不出到底是哪里错。
        所以这里把三件事拆开，每一步的失败原因分开报。
        """
        import cover_gen
        import wechat_mp
        appid = credentials.get(self.spec.id, 'appid')
        secret = credentials.get(self.spec.id, 'secret')

        # 1) 封面：资产里给了就用，没给就按标题本地生成（不依赖外部生图额度）
        try:
            cover_path = cover_gen.ensure_cover(asset, evidence_dir)
        except cover_gen.CoverError as exc:
            return PublishResult('manual_required', f'封面不可用：{exc}')

        client = wechat_mp.WechatMpClient(appid, secret)
        # 2) 上传为永久素材，拿 thumb_media_id（临时素材 3 天就过期）
        try:
            thumb_media_id = client.upload_thumb(cover_path.name, cover_path.read_bytes())
        except Exception as exc:
            return PublishResult('failed', f'封面素材上传失败：{str(exc)[:200]}')
        if not thumb_media_id:
            return PublishResult('failed', '封面素材上传后没有拿到 media_id，已中止（未写草稿）')

        # 3) 写草稿
        try:
            out = client.add_draft(title=asset.get('title', ''), content=asset.get('body', ''),
                                   digest=(asset.get('brief') or '')[:120],
                                   thumb_media_id=thumb_media_id)
        except Exception as exc:
            return PublishResult('failed', f'公众号接口调用失败：{str(exc)[:200]}')
        media_id = out.get('media_id', '')
        return PublishResult('draft_created',
                             f'已写入公众号草稿箱（media_id {media_id[:12]}…，封面 {cover_path.name}）。'
                             f'内容仍是草稿，需在公众号后台群发后才算发布。',
                             evidence=str(cover_path))

    def submit_browser(self, asset, evidence_dir=None):
        """其他平台：浏览器自动化。"""
        import browser_publisher
        evidence_dir = evidence_dir or (store_dir() / 'publish')
        try:
            out = browser_publisher.publish(self.spec.id, asset.get('title', ''),
                                            asset.get('body', ''), evidence_dir)
        except Exception as exc:
            return PublishResult('failed', f'浏览器自动化异常：{str(exc)[:180]}')
        return PublishResult(out.get('status', 'failed'), out.get('message', ''),
                             out.get('url', ''), out.get('evidence', ''))


def store_dir():
    import workspace_store as store
    return store.DATA


REGISTRY = {spec.id: PublishAdapter(spec) for spec in PLATFORM_SPECS}


def get_adapter(platform_id):
    adapter = REGISTRY.get(platform_id)
    if adapter is None:
        raise KeyError('未知发布平台')
    return adapter


def capabilities():
    return [adapter.capability() for adapter in REGISTRY.values()]


def describe(asset, platform_id):
    adapter = get_adapter(platform_id)
    data = adapter.capability()
    missing = adapter.check_asset(asset or {})
    data['missing'] = missing
    if missing:
        labels = '、'.join(item['label'] for item in missing)
        data['action'] = f'缺 {labels}，无法自动发布'
    elif data['mode'] == 'api':
        data['action'] = ('确认后调用官方接口写入公众号草稿箱' if data['can_attempt']
                          else f"需先配置凭据：{data['health'].get('reason', '')}")
    elif data['mode'] == 'browser':
        data['action'] = '确认后由浏览器自动化创建（首次需在该平台登录）'
    else:
        data['action'] = '无自动接口，发布后回填链接'
    return data
