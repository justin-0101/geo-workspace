#!/usr/bin/env python
"""对比度门禁：按 WCAG 相对亮度公式检查 workspace.css 的文字与底色搭配。

用法：
    python check_contrast.py

退出码：
    0 = 正文对全部 >= 4.5:1，且所有被引用的 token 都存在
    1 = 有正文对不达标，或有 token 缺失

设计说明：
    检查清单是一份显式契约（PAIRS）。改配色时改这里，不要改下限。
    正文对按 4.5:1（WCAG AA 普通文字）；装饰对按 3.0:1（仅用于坐标轴标签一类的非信息文字）。
    颜色一律通过 var() 引用 token；token 缺失按失败处理，避免规则里又散落字面量。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS = ROOT / 'workspace.css'

# (名称, 前景, 背景, 阈值, 类别)
PAIRS = [
    ('正文 ink / 面板', 'var(--ink)', 'var(--panel)', 4.5, '正文'),
    ('正文 ink / 页面底', 'var(--ink)', 'var(--bg)', 4.5, '正文'),
    ('次级 muted / 面板', 'var(--muted)', 'var(--panel)', 4.5, '正文'),
    ('次级 muted / 页面底', 'var(--muted)', 'var(--bg)', 4.5, '正文'),
    ('表格表头 muted / 面板', 'var(--muted)', 'var(--panel)', 4.5, '正文'),
    ('链接 blue / 面板', 'var(--blue)', 'var(--panel)', 4.5, '正文'),
    ('主色文字 / 主色浅底', 'var(--blue)', 'var(--blue2)', 4.5, '正文'),
    ('徽标 warn / 浅底', 'var(--orange)', 'var(--orange2)', 4.5, '正文'),
    ('徽标 good / 浅底', 'var(--green)', 'var(--green2)', 4.5, '正文'),
    ('徽标 bad / 浅底', 'var(--red)', 'var(--red2)', 4.5, '正文'),
    ('提示条文字 / 提示条底', 'var(--notice-ink)', 'var(--notice-bg)', 4.5, '正文'),
    ('侧栏 导航未选 / 侧栏底', 'var(--side-muted)', 'var(--navy)', 4.5, '正文'),
    ('侧栏 导航选中 / 侧栏底', 'var(--side-ink)', 'var(--navy)', 4.5, '正文'),
    ('侧栏 品牌字 / 侧栏底', 'var(--side-ink)', 'var(--navy)', 4.5, '正文'),
    ('装饰 faint / 面板', 'var(--faint)', 'var(--panel)', 3.0, '装饰'),
    ('装饰 faint / 页面底', 'var(--faint)', 'var(--bg)', 3.0, '装饰'),
    ('图表标记 orange-mark / 面板', 'var(--orange-mark)', 'var(--panel)', 3.0, '装饰'),
    ('页签计数 muted-ink / 计数底', 'var(--muted-ink)', 'var(--line-soft)', 4.5, '正文'),
    ('预览台状态条 muted-ink / 状态条底', 'var(--muted-ink)', 'var(--line-soft)', 4.5, '正文'),
]


def linearize(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast(hex_a, hex_b):
    la, lb = luminance(hex_a), luminance(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def parse_tokens(text):
    block = re.search(r':root\s*\{([^}]*)\}', text)
    if not block:
        return None
    return {k: v.strip() for k, v in re.findall(r'--([a-zA-Z0-9-]+)\s*:\s*([^;}]+)', block.group(1))}


def resolve(expr, tokens):
    """返回 (hex6, None) 或 (None, 原因)。"""
    expr = expr.strip()
    m = re.fullmatch(r'var\(\s*--([a-zA-Z0-9-]+)\s*\)', expr)
    if m:
        name = m.group(1)
        if name not in tokens:
            return None, 'token --%s 缺定义' % name
        expr = tokens[name].strip()
    m = re.fullmatch(r'#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})', expr)
    if not m:
        return None, '无法解析颜色 %s' % expr
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    return h.lower(), None


def main():
    if not CSS.exists():
        print('找不到 %s' % CSS)
        return 1
    text = CSS.read_text(encoding='utf-8')
    tokens = parse_tokens(text)
    if tokens is None:
        print('workspace.css 里找不到 :root 变量块')
        return 1

    print('对比度门禁  file=%s  tokens=%d' % (CSS.name, len(tokens)))
    print('')
    print('%-28s %-14s %-14s %7s %5s %s' % ('检查项', '前景', '背景', '实测', '下限', '结果'))

    failures, unknown = [], []
    for name, fg_expr, bg_expr, floor, kind in PAIRS:
        fg, err_fg = resolve(fg_expr, tokens)
        bg, err_bg = resolve(bg_expr, tokens)
        if err_fg or err_bg:
            reason = err_fg or err_bg
            unknown.append((name, reason))
            print('%-28s %-14s %-14s %7s %5s %s' % (name, fg_expr, bg_expr, '-', floor, 'TOKEN 缺失: ' + reason))
            continue
        value = contrast(fg, bg)
        ok = value >= floor
        if not ok:
            failures.append((name, value, floor, kind))
        print('%-28s %-14s %-14s %7.2f %5.1f %s' % (
            name, '#' + fg, '#' + bg, value, floor, 'PASS' if ok else 'FAIL'))

    literals = sorted(set(re.findall(r'#[0-9a-fA-F]{3}\b|#[0-9a-fA-F]{6}\b', text)))
    print('')
    print('字面量颜色（信息项，不计入退出码）：%d 个' % len(literals))
    print('  ' + ' '.join(literals))

    print('')
    if failures or unknown:
        for name, value, floor, kind in failures:
            print('不达标：%s 实测 %.2f < %.1f（%s）' % (name, value, floor, kind))
        for name, reason in unknown:
            print('缺 token：%s（%s）' % (name, reason))
        print('')
        print('结论：FAIL  不达标 %d 项，缺 token %d 项' % (len(failures), len(unknown)))
        return 1
    print('结论：PASS  全部 %d 项达标' % len(PAIRS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
