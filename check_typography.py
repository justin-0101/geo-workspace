#!/usr/bin/env python
"""字体与文案门禁：用真实浏览器把页面上每一段可见文字的档位、颜色、用词逐条核对。

用法：
    python check_typography.py                     # 默认查 4173 正式批量发布页
    python check_typography.py <url> [<url> ...]

退出码：
    0 = 全部通过
    1 = 有违规

设计说明（与 check_contrast.py 同一套做法）：
    规则写成本文件里的显式契约，不写在注释里。要放宽规则就改这里，不要在页面上就地写一个数字。
    字号只允许 --fs-* 那 9 档；颜色只允许 token 里定义的值，出现字面量色即失败。
    这份脚本是「改完自己先跑一遍」用的，不是给人肉扫的。
"""
import json
import re
import sys
import urllib.request
import urllib.parse
from collections import defaultdict

from playwright.sync_api import sync_playwright
from browser_paths import chrome_executable
from runtime_config import API_URL, FRONTEND_URL


def default_url():
    """从本地 API 取一个真实项目，避免把某个项目 slug 写死在门禁里。"""
    with urllib.request.urlopen(f'{API_URL}/api/projects', timeout=10) as response:
        projects = json.loads(response.read().decode('utf-8')).get('projects', [])
    if not projects:
        return f'{FRONTEND_URL}/workspace.html#publications'
    slug = urllib.parse.quote(projects[0]['slug'])
    return f'{FRONTEND_URL}/workspace.html#publications?project={slug}'


URLS = sys.argv[1:] or [default_url()]

# ---- 契约 ----------------------------------------------------------------
# 字号只允许这 9 档（与 --fs-* 一一对应）
FONT_SIZES = {'30px': '--fs-page', '22px': '--fs-metric', '18px': '--fs-brand',
              '17px': '--fs-section', '15px': '--fs-card', '14px': '--fs-body',
              '13px': '--fs-note', '12px': '--fs-meta', '10px': '--fs-eyebrow'}

# 文字颜色只允许这些（全部来自 :root 的 token）
COLORS = {
    'rgb(26, 26, 26)': '--ink',
    'rgb(105, 112, 119)': '--muted',
    'rgb(100, 107, 114)': '--muted-ink',
    'rgb(130, 136, 143)': '--faint',
    'rgb(23, 107, 136)': '--blue',
    'rgb(18, 90, 115)': '--blue-deep',
    'rgb(252, 252, 251)': '--on-accent',
    'rgb(191, 68, 68)': '--red',
    'rgb(37, 122, 86)': '--green',
    'rgb(169, 90, 38)': '--orange',
    'rgb(151, 83, 37)': '--notice-ink',
    'rgb(107, 116, 124)': '--line-hover',
    'rgb(215, 224, 229)': '--side-ink',
    'rgb(184, 199, 207)': '--side-muted',
    'rgb(95, 176, 204)': '--side-accent',
}

# --faint 只准出现在这些 class 里（分隔符与坐标轴标签）
FAINT_ALLOWED_CLASSES = {'dot', 'crumb-sep', 'xlabels'}

# 句末标点只查这几种「行文容器」，标题、表格单元、标签不受这条约束
PROSE = re.compile(r'(^| )(gate-note|submit-facts|sheet-lede|field-error|hint|note|lede)( |$)')

# 按钮与标签的字号字重契约：(选择器特征) -> (字号, 字重)
BUTTON_CONTRACT = [
    ('.btn-sm', ('12px', '600')),
    ('.btn-quiet', ('12px', '600')),
    ('.linkish', ('12px', '600')),
]

# 角色契约：每个「角色的字号与字重是定死的」。
# 光有「只允许 9 档」白名单不够：15px 在档位内，但它是卡片标题档，
# 跑到准入条标签上就是错的。这张表才是「同类元素必须一致」的真正执行方式。
ROLE_CONTRACT = [
    ('.page-lead h1',      ('30px', '700'), '页面标题'),
    ('.gate-facts strong', ('22px', '700'), '准入条大数字'),
    ('.submit-facts strong', ('22px', '700'), '提交条里的条数'),
    ('.dialog-head h2',    ('17px', '700'), '弹窗标题'),
    ('.pick-head h2',      ('15px', '700'), '卡片标题'),
    ('.rec-head h2',       ('15px', '700'), '记录区标题'),
    ('.submit-facts',      ('15px', '400'), '提交条主句'),
    ('.pick-title',        ('14px', '600'), '列表项标题'),
    ('#tabs a:not([aria-current])', ('14px', '400'), '页签（未选中）'),
    ('#tabs a[aria-current]',       ('14px', '700'), '页签（选中）'),
    ('.page-lead p',       ('13px', '400'), '页头副标'),
    ('.gate-note',         ('13px', '400'), '准入条说明'),
    ('.pick-chip',         ('13px', '600'), '平台芯片'),
    ('#notice',            ('13px', '400'), '提示条'),
    ('.gate-facts span',   ('12px', '400'), '准入条标签'),
    ('.pick-meta',         ('12px', '400'), '元信息行'),
    ('.pick-list-tail',    ('12px', '400'), '列表脚注'),
    ('#breadcrumb',        ('13px', '400'), '面包屑'),
    ('.badge',             ('12px', '600'), '徐标'),
    ('.chip',              ('12px', '600'), '芯片'),
    ('.tabs-count',        ('14px', '600'), '页签计数'),
    ('.gate-details summary', ('13px', '600'), '平台明细展开按钮'),
    ('.rec-item-main small', ('12px', '400'), '记录原因行'),
    ('th',                 ('12px', '600'), '表头'),
    ('td',                 ('13px', '400'), '表格正文'),
]

# 可用词：动词与术语
BANNED_WORDS = ['提交', '推送', '送出', '需人工', '未配凭据', '缺少', '可以直接发', '可直接发布', '需先登录']
# 同一件事只允许一种说法
TERM_CANON = {
    '转人工': ['需人工发布', '需人工处理', '人工待处理'],
}
# 斜杠两侧必须无空格。
# 只查「写进文案的」字符串，用 body.textContent 而不是 innerText：
# 面包屑那个 / 两侧的空格来自 flex 的 gap，不是文字里敲的空格，innerText 会把它们带出来造成误报。
BAD_SLASH = re.compile(r'\S[ \t]/[ \t]\S')
# 数字与量词之间必须有半角空格
BAD_QUANT = re.compile(r'\d(篇|条|个|张|次)')
# 句末标点：完整句子必须以 。？ 结尾
SENTENCE = re.compile(r'[，；]')


def collect(page):
    return page.evaluate("""() => {
      const cs = e => getComputedStyle(e);
      const out = [];
      document.querySelectorAll('body *').forEach(e => {
        if (e.children.length) return;
        const t = (e.innerText || e.textContent || '').trim();
        if (!t) return;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        out.push({
          text: t,
          tag: e.tagName,
          cls: (typeof e.className === 'string' ? e.className : ''),
          size: cs(e).fontSize,
          weight: cs(e).fontWeight,
          color: cs(e).color,
          isBtn: e.matches('button, .button, .linkish'),
          // 近白底还是近蓝底，用来判断 muted 该不该换成 muted-ink
          bg: (() => { let n = e; while (n && n !== document.body) {
                 const b = cs(n).backgroundColor;
                 if (b && b !== 'rgba(0, 0, 0, 0)') return b; n = n.parentElement; } return 'rgb(251, 251, 250)'; })(),
        });
      });
      return out;
    }""")


def main():
    fails = []
    with sync_playwright() as pw:
        executable = chrome_executable()
        if executable is None:
            raise RuntimeError('Chrome/Chromium not found; set GEO_CHROME_PATH')
        b = pw.chromium.launch(executable_path=str(executable), headless=True)
        for url in URLS:
            p = b.new_page(viewport={'width': 1440, 'height': 1000})
            p.set_default_timeout(20000)
            p.goto(url, wait_until='domcontentloaded')
            p.wait_for_timeout(900)

            # 页面分几个阶段渲染，文案散在不同阶段：弹窗开着时的内容、切到记录页签后的内容。
            # 只扫一次一定会漏，所以每个阶段都扫，最后取并集。
            rows, wholes = [], []
            def scan():
                rows.extend(collect(p))
                wholes.append(p.evaluate('document.body.innerText'))
                wholes.append(p.evaluate("""() => { const c = document.body.cloneNode(true);
                  c.querySelectorAll('script,style').forEach(e => e.remove()); return c.textContent; }"""))

            copy_exempt = p.evaluate("document.body.hasAttribute('data-copy-exempt')")

            # 阶段 1：准备发布页签 + 确认弹窗打开
            try:
                p.locator('#all-assets').check()
                p.locator('#all-platforms').check()
                p.wait_for_timeout(200)
                p.locator('#publish').click()
                p.wait_for_timeout(400)
                scan()
                p.locator('#cancel-modal').click()
                p.wait_for_timeout(200)
            except Exception:
                pass

            # 角色契约：角色分布在两个页签，两个页签各量一次
            role_hits = {}
            def measure_roles():
                for sel, want, label in ROLE_CONTRACT:
                    got = p.evaluate("""(sel) => { const e = document.querySelector(sel);
                      if (!e) return null; const c = getComputedStyle(e);
                      return {size: c.fontSize, weight: c.fontWeight}; }""", sel)
                    if got:
                        role_hits.setdefault(label, (sel, want, (got['size'], got['weight'])))
            measure_roles()
            scan()   # 阶段 2：关掉弹窗、记录页签之前的准备发布页

            # 阶段 3：发布记录页签
            try:
                p.locator('#tabs a').nth(1).click()
                p.wait_for_timeout(400)
                measure_roles()
                scan()
            except Exception:
                pass

            whole = '\n'.join(wholes)
            authored = '\n'.join(wholes[1::2])

            print('=' * 20, url.split('/')[-1], '（%d 段文字）' % len(rows), '=' * 20)
            if copy_exempt:
                print('  （本页标记了 data-copy-exempt：文案类规则跳过，字号与颜色照查）')

            bad_size, bad_color, bad_faint, bad_word, bad_slash, bad_quant = [], [], [], [], [], []
            btn_conf = defaultdict(set)
            cls_of = lambda x: set((x['cls'] or '').split())

            for w in BANNED_WORDS:
                for m in re.finditer(re.escape(w), whole):
                    bad_word.append((w, whole[max(0, m.start() - 16):m.end() + 16].replace('\n', ' ')))
            for canon, variants in TERM_CANON.items():
                for v in variants:
                    for m in re.finditer(re.escape(v), whole):
                        bad_word.append(('术语不一致：%s 应为 %s' % (v, canon), whole[max(0, m.start() - 16):m.end() + 16].replace('\n', ' ')))
            for m in BAD_SLASH.finditer(authored):
                bad_slash.append(authored[max(0, m.start() - 20):m.end() + 20].replace('\n', ' '))

            for x in rows:
                if x['size'] not in FONT_SIZES:
                    bad_size.append((x['size'], x['text'][:40], x['cls'][:24]))
                if x['color'] not in COLORS:
                    bad_color.append((x['color'], x['text'][:40], x['cls'][:24]))
                if x['color'] == 'rgb(130, 136, 143)' and not (cls_of(x) & FAINT_ALLOWED_CLASSES):
                    bad_faint.append((x['text'][:40], x['cls'][:24]))
                if BAD_QUANT.search(x['text']):
                    bad_quant.append(x['text'][:50])
                if x['tag'] == 'P' or PROSE.search(x['cls'] or ''):
                    if SENTENCE.search(x['text']) and len(x['text']) > 8 and not re.search(r'[。？！：]$', x['text']):
                        bad_word.append(('句末缺句号', x['text'][:46]))
                if x['isBtn']:
                    for sel, want in BUTTON_CONTRACT:
                        if sel.strip('.') in x['cls'].split():
                            btn_conf[sel].add((x['size'], x['weight']))
                    if not x['cls']:
                        btn_conf['(默认按钮)'].add((x['size'], x['weight']))

            key = lambda it: repr(it)
            bad_word = list(dict.fromkeys(map(key, bad_word)))
            bad_slash = list(dict.fromkeys(map(key, bad_slash)))
            bad_word = [eval(k) for k in bad_word]
            bad_slash = list(dict.fromkeys(bad_slash))
            if copy_exempt:
                bad_word, bad_slash, bad_quant = [], [], []

            def report(name, items, limit=8):
                if items:
                    print('  FAIL %-26s %d 处' % (name, len(items)))
                    for it in items[:limit]:
                        print('       ', it)
                    if len(items) > limit:
                        print('        …另有 %d 处' % (len(items) - limit))
                    fails.append(name)
                else:
                    print('  PASS %s' % name)

            report('字号只允许 9 档', bad_size)
            report('文字颜色只用 token', bad_color)
            report('--faint 不作信息文字', bad_faint)
            report('用词与术语', bad_word)
            report('斜杠两侧无空格', bad_slash)
            report('数字与量词有半角空格', bad_quant)

            print('  按钮字号字重：')
            for sel, conf in sorted(btn_conf.items()):
                ok = len(conf) == 1
                print('    %-14s %s %s' % (sel, sorted(conf), 'PASS' if ok else 'FAIL 同类按钮不一致'))
                if not ok:
                    fails.append('按钮字号字重 %s' % sel)

            tds = {x['size'] for x in rows if x['tag'] == 'TD'}
            ths = {x['size'] for x in rows if x['tag'] == 'TH'}
            ok = len(tds) <= 1 and len(ths) <= 1
            print('  表格字号一致 td=%s th=%s %s' % (sorted(tds), sorted(ths), 'PASS' if ok else 'FAIL'))
            if not ok:
                fails.append('表格字号')

            role_bad, role_missing = [], []
            for sel, want, label in ROLE_CONTRACT:
                if label not in role_hits:
                    role_missing.append((label, sel))
                    continue
                _, _, got = role_hits[label]
                if got != want:
                    role_bad.append((label, sel, '%s/%s' % got, '%s/%s' % want))
            if role_bad:
                print('  FAIL %-26s %d 处' % ('同类元素字号字重一致', len(role_bad)))
                print('       %-14s %-24s %-14s %s' % ('角色', '选择器', '实测', '期望'))
                for label, sel, got, want in role_bad:
                    print('       %-14s %-24s %-14s %s' % (label, sel, got, want))
                fails.append('同类元素字号字重一致')
            else:
                print('  PASS 同类元素字号字重一致（量到 %d / %d 个角色）' % (len(role_hits), len(ROLE_CONTRACT)))
            if role_missing:
                print('  注意：%d 个角色本次没渲染到，未校验：%s' % (
                    len(role_missing), '、'.join(l for l, _ in role_missing)))
            p.close()
        b.close()

    print('')
    if fails:
        print('结论：FAIL  %d 类违规：%s' % (len(fails), '、'.join(sorted(set(fails)))))
        return 1
    print('结论：PASS  字号档位、角色一致性、文字颜色、用词与术语、标点、按钮一致性全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
