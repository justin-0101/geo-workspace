"""文章封面图：本地确定性渲染，不依赖任何外部生图 API。

为什么不用 AI 生图
------------------
1. 封面上的中文标题必须是准确的字。AI 生图写中文经常出错字或缺笔画，
   而"封面写错字"对公众号是硬伤 —— 生成得再好看也不能用。
2. 本机当前没有可用的生图额度：MiniMax 返回 `status_code 2056`
   （"已达到 Token Plan 用量上限"）。把封面押在外部额度上，
   额度一断就退回人工补封面，自动化等于白做。

所以这里用 PIL 做确定性渲染：**背景由代码画，标题由代码画**，
字形一定正确、出图可复现、零成本、断网也能出图。

出图规格
--------
- 1080x864 JPEG（与 `python-wechat-publish` 已验证可用的尺寸一致，微信上限 2MB）；
- 深色渐变底 + 白色粗体标题，保证正文对比度；
- 无 Emoji、无虚构数据、无假 logo —— 只画标题与副标题。

将来要换成"AI 生成无文字背景 + 本地叠字"，只需替换 `_background()`；
`build()` 的对外契约（产出一张本地 JPEG 的 Path）不用动。
"""
from __future__ import annotations

import zlib
from pathlib import Path

CANVAS = (1080, 864)
MARGIN = 96
TITLE_MAX_LINES = 3
TITLE_MAX_SIZE = 84
TITLE_MIN_SIZE = 44
SUBTITLE_SIZE = 34

#: 深色渐变，全部保证白字对比度足够。顺序即优先级，不要随意重排（影响出图可复现）。
PALETTES = (
    ((14, 42, 86), (32, 96, 168)),      # 深蓝：设备/制造/工业默认
    ((28, 32, 84), (86, 52, 152)),      # 蓝紫：AI/模型/数据
    ((6, 58, 66), (18, 118, 120)),      # 深青：能源/环保/园区
    ((92, 24, 32), (176, 62, 48)),      # 暗红：风险/安全/合规
    ((12, 54, 40), (30, 110, 80)),      # 墨绿：成本/效率/增长
    ((24, 32, 48), (58, 74, 104)),      # 石墨蓝：通用兜底
)

#: 关键词 -> 调色板下标。只在标题/摘要里命中才用，命中不了按标题哈希取，保证可复现。
KEYWORD_PALETTE = (
    (0, ('设备', '资产', '台账', '制造', '工业', '点检', '维保', '备件')),
    (1, ('AI', 'ai', '智能', '模型', '算法', '数据', '大模型', ' Agent')),
    (2, ('能源', '园区', '双碳', '能耗', '环保', '电力')),
    (3, ('风险', '安全', '合规', '隐患', '事故', '丢失', '漏洞')),
    (4, ('成本', '效率', '增长', '降本', '提效', '收益', '回报')),
)

FONT_CANDIDATES = (
    ('C:/Windows/Fonts/msyhbd.ttc', 0),      # 微软雅黑 粗体
    ('C:/Windows/Fonts/msyh.ttc', 0),        # 微软雅黑
    ('C:/Windows/Fonts/simhei.ttf', 0),      # 黑体
    ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 0),
)

_FONT_CACHE: dict = {}
_FONT_WARNING = ''


class CoverError(RuntimeError):
    """封面不可用（既没有现成的，也无法生成）。"""


def _font(size):
    """按候选顺序取一个能画中文的字体；全失败才退回默认字体并记下警告。"""
    global _FONT_WARNING
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    from PIL import ImageFont
    for path, index in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
        _FONT_CACHE[size] = font
        return font
    _FONT_WARNING = '没有找到可用的中文字体，封面文字可能显示为方块'
    font = ImageFont.load_default(size)
    _FONT_CACHE[size] = font
    return font


def pick_palette(text):
    """先按关键词，其次按文本哈希 —— 同一篇标题每次出图颜色一致。"""
    haystack = str(text or '')
    for index, words in KEYWORD_PALETTE:
        if any(word in haystack for word in words):
            return PALETTES[index]
    return PALETTES[zlib.crc32(haystack.encode('utf-8')) % len(PALETTES)]


#: 中文禁则：这些字符不放在行首（避免“核心”这类词被拆开、标点跑到行头）
NO_LINE_START = '，。、；：？！）】》」』〕〉”’%‰℃'
#: 这些字符不留在行尾
NO_LINE_END = '（【《「『〔〈“‘'


def wrap_lines(draw, text, font, max_width):
    """按字符断行，带基础中文禁则；英文尽量按词，返回行列表。"""
    text = ' '.join(str(text or '').split())
    if not text:
        return []
    lines, current = [], ''
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            # 行首禁则：该字符不能起头，宁可让上一行多带一个字符
            if char in NO_LINE_START:
                current = candidate
                continue
            # 行尾禁则：上一行以开引号/开括号结尾，把它挪到下一行
            if current[-1] in NO_LINE_END and len(current) > 1:
                lines.append(current[:-1])
                current = current[-1] + char
                continue
            lines.append(current)
            current = char.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_title(draw, title, max_width):
    """挑字号：先保证不超 TITLE_MAX_LINES 行，并尽量不出现「孤儿行」。

    中文海报最难看的一种情况是末行只剩 1-2 个字（如「…从台账到数据 / 资产」）。
    从大到小扫一遍，第一个不产生孤行的字号就是最优解；都孤行才退回最大可容纳字号。
    """
    fallback = None
    size = TITLE_MAX_SIZE
    while size >= TITLE_MIN_SIZE:
        font = _font(size)
        lines = wrap_lines(draw, title, font, max_width)
        if len(lines) <= TITLE_MAX_LINES:
            widow = len(lines) > 1 and len(lines[-1]) <= 2
            if not widow:
                return font, lines
            if fallback is None:
                fallback = (font, lines)
        size -= 4
    if fallback:
        return fallback
    font = _font(TITLE_MIN_SIZE)
    return font, wrap_lines(draw, title, font, max_width)[:TITLE_MAX_LINES]


def mix(color_a, color_b, ratio):
    """在两个颜色之间插值，ratio=0 取 color_a，=1 取 color_b。"""
    return tuple(round(color_a[i] + (color_b[i] - color_a[i]) * ratio) for i in range(3))


def _background(size, top, bottom):
    """竖直渐变，**从上到下变暗**。

    方向不能反：白的标题和副标题都压在这张图上，底部一旦变浅，
    副标题（最下面那行）就是全图对比度最低的地方。
    """
    from PIL import Image, ImageDraw
    width, height = size
    image = Image.new('RGB', size, top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line([(0, y), (width, y)],
                  fill=mix(top, bottom, y / max(height - 1, 1)))
    return image


def build(title, subtitle='', out_path=None, out_dir=None, asset_id=''):
    """渲染一张封面，返回写入的本地 Path。

    title     必填，画在封面主位
    subtitle  可选，画在标题下方的小字
    out_path  指定输出文件；给了就不用 out_dir
    out_dir   输出目录，默认 data/covers/
    asset_id  用于生成稳定文件名，避免同标题互相覆盖
    """
    from PIL import ImageDraw

    title = str(title or '').strip()
    if not title:
        raise CoverError('标题为空，无法生成封面')

    if out_path:
        target = Path(out_path)
    else:
        if out_dir is None:
            import workspace_store as store
            out_dir = store.DATA / 'covers'
        name = f'{asset_id}.jpg' if asset_id else f'cover-{zlib.crc32(title.encode("utf-8")) & 0xFFFFFFFF:08x}.jpg'
        target = Path(out_dir) / name
    target.parent.mkdir(parents=True, exist_ok=True)

    start, end = pick_palette(f'{title} {subtitle}')
    # 整张图都留在深色里（顶部略亮、底部最暗），保证白字在任何一行都够清楚
    image = _background(CANVAS, mix(start, end, 0.45), start)
    draw = ImageDraw.Draw(image)
    max_width = CANVAS[0] - MARGIN * 2

    font, lines = _fit_title(draw, title, max_width)
    line_boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    line_height = max(box[3] - box[1] for box in line_boxes) if line_boxes else 0
    leading = round(line_height * 0.34)
    block_height = line_height * len(lines) + leading * (len(lines) - 1)

    sub_font = _font(SUBTITLE_SIZE)
    sub_lines = wrap_lines(draw, subtitle, sub_font, max_width)[:2] if subtitle else []
    sub_block = 0
    if sub_lines:
        sub_box = draw.textbbox((0, 0), sub_lines[0], font=sub_font)
        sub_block = (sub_box[3] - sub_box[1]) * len(sub_lines) + 14 * (len(sub_lines) - 1) + 46

    top = max(MARGIN, (CANVAS[1] - (block_height + sub_block)) // 2)
    y = top
    # 底色已经统一压暗，不需要投影 —— 深底上的黑色投影只会让笔画发糊
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=(255, 255, 255))
        y += line_height + leading

    # 标题与副标题之间的一条强调短线
    rule_y = y + 22
    draw.rectangle([MARGIN, rule_y, MARGIN + 120, rule_y + 6], fill=(255, 255, 255))

    if sub_lines:
        y = rule_y + 26
        for line in sub_lines:
            draw.text((MARGIN, y), line, font=sub_font, fill=(226, 232, 240))
            y += (draw.textbbox((0, 0), line, font=sub_font)[3]
                  - draw.textbbox((0, 0), line, font=sub_font)[1]) + 14

    image.save(target, format='JPEG', quality=92, optimize=True)
    return target


def resolve_cover(cover, evidence_dir=None):
    """资产自带的封面字段 —— 本地文件或 http(s) 图片地址 —— 统一变成本地文件 Path。"""
    cover = str(cover or '').strip()
    if not cover:
        raise CoverError('封面字段为空')
    if cover.startswith(('http://', 'https://')):
        import httpx
        suffix = Path(cover.split('?')[0]).suffix or '.jpg'
        target_dir = Path(evidence_dir) if evidence_dir else _default_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f'cover-url{zlib.crc32(cover.encode("utf-8")) & 0xFFFFFFFF:08x}{suffix}'
        if not target.exists():
            response = httpx.get(cover, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            target.write_bytes(response.content)
        return target
    path = Path(cover)
    if not path.exists():
        raise CoverError(f'封面字段既不是 http(s) 图片地址，本地也不存在这个文件：{cover}')
    return path


def _default_dir():
    import workspace_store as store
    return store.DATA / 'covers'


def ensure_cover(asset, evidence_dir=None):
    """发布前确保有封面：资产里给了就用，没给就按标题本地生成。"""
    asset = asset or {}
    title = str(asset.get('title') or '').strip()
    cover = str(asset.get('cover') or '').strip()
    if cover:
        return resolve_cover(cover, evidence_dir)
    subtitle = str(asset.get('brief') or asset.get('summary') or '').strip()[:60]
    out_dir = Path(evidence_dir) if evidence_dir else _default_dir()
    return build(title, subtitle, out_dir=out_dir, asset_id=str(asset.get('id') or ''))


def main(argv=None):
    """手动出图，便于肉眼检查：python cover_gen.py "标题" [输出路径]。"""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('用法: python cover_gen.py "标题" [副标题] [输出路径]')
        return 1
    title = argv[0]
    subtitle = argv[1] if len(argv) > 1 and argv[1] else ''
    out = argv[2] if len(argv) > 2 else None
    path = build(title, subtitle, out_path=out)
    print(f'已生成: {path}  ({Path(path).stat().st_size} 字节)')
    if _FONT_WARNING:
        print(f'警告: {_FONT_WARNING}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
