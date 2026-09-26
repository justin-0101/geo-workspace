# -*- coding: utf-8 -*-
"""P1 回答门禁回归测试。

覆盖 2026-09-17 假成功事故的四个环节：
1. 回答区提取（只取问句之后，不含侧边栏）
2. 回答有效性阈值（空白页/思考中途必须判无效）
3. 验证弹窗检测（滑块 / 图片点选）
4. 分类窗口不再退化成整页文本

不需要浏览器；用 FakePage 驱动 wait_complete。
"""
import importlib.util
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SIDEBAR = (
    "新建对话\n最近对话\n示例财税服务及适用企业类型\n南沙代理记账推荐\n"
    "广州代理记账服务选择指南\n"
)
PROMPT = "广州有哪些靠谱的代理记账公司？"
ANSWER = "广州的代理记账公司主要有宏智财税、品誉财税、企安财税等。" * 20  # >400 字


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


geo = load_script("geo_run.py")


class FakeFrame:
    def __init__(self, text):
        self._text = text

    def inner_text(self, selector):
        return self._text


class FakePage:
    """按顺序返回文本；用尽后保持最后一个值。"""

    def __init__(self, texts, captcha_selector=None, frames=()):
        self._texts = list(texts)
        self._i = 0
        self._captcha_selector = captcha_selector
        self.main_frame = "main"
        self.frames = [self.main_frame] + list(frames)

    def inner_text(self, selector):
        text = self._texts[min(self._i, len(self._texts) - 1)]
        self._i += 1
        return text

    def query_selector(self, selector):
        return object() if self._captcha_selector and self._captcha_selector in selector else None


class AnswerRegionTests(unittest.TestCase):
    def test_slice_answer_returns_text_after_prompt(self):
        self.assertEqual(geo.slice_answer(SIDEBAR + PROMPT + "回答正文", PROMPT), "回答正文")

    def test_slice_answer_excludes_sidebar_prefix(self):
        region = geo.slice_answer(SIDEBAR + PROMPT + ANSWER, PROMPT)
        self.assertNotIn("示例财税", region)
        self.assertNotIn("南沙代理记账推荐", region)

    def test_slice_answer_missing_prompt_fails_closed(self):
        self.assertEqual(geo.slice_answer(SIDEBAR, PROMPT), "")
        self.assertEqual(geo.slice_answer("", PROMPT), "")

    def test_sidebar_only_page_is_invalid(self):
        """侧边栏有历史会话标题、回答区为空 —— 事故现场必须判无效。"""
        region = geo.slice_answer(SIDEBAR + PROMPT, PROMPT)
        self.assertFalse(geo.answer_is_valid(region))

    def test_threshold_boundary(self):
        self.assertFalse(geo.answer_is_valid("x" * (geo.MIN_ANSWER_CHARS - 1)))
        self.assertTrue(geo.answer_is_valid("x" * geo.MIN_ANSWER_CHARS))

    def test_whitespace_does_not_count(self):
        self.assertFalse(geo.answer_is_valid("x " * (geo.MIN_ANSWER_CHARS // 2)))

    def test_real_answer_is_valid(self):
        self.assertTrue(geo.answer_is_valid(geo.slice_answer(PROMPT + ANSWER, PROMPT)))


class CaptchaTests(unittest.TestCase):
    def test_qianwen_slider_detected(self):
        text = SIDEBAR + PROMPT + "\n亲，请拖动下方滑块完成验证\n请按住滑块，拖动到最右边"
        self.assertIsNotNone(geo.detect_captcha(FakePage([text]), text))

    def test_doubao_image_pick_detected(self):
        text = SIDEBAR + PROMPT + "\n常见的音乐乐器\n请选择所有符合上文描述的图片，并拖动到下方"
        self.assertIsNotNone(geo.detect_captcha(FakePage([text]), text))

    def test_normal_answer_is_not_captcha(self):
        text = SIDEBAR + PROMPT + ANSWER
        self.assertIsNone(geo.detect_captcha(FakePage([text]), text))

    def test_captcha_by_dom_hint(self):
        self.assertIsNotNone(geo.detect_captcha(FakePage(["正文"], "captcha")))

    def test_captcha_inside_iframe_is_detected(self):
        """事故现场：弹窗在跨域 iframe 内，主文档 inner_text 读不到。"""
        page = FakePage([SIDEBAR + PROMPT], frames=[FakeFrame("亲，请拖动下方滑块完成验证")])
        hit = geo.detect_captcha(page, SIDEBAR + PROMPT)
        self.assertIsNotNone(hit)
        self.assertTrue(hit.startswith("frame:"))


class WaitCompleteTests(unittest.TestCase):
    def setUp(self):
        self._poll = geo.POLL_SECONDS
        geo.POLL_SECONDS = 0

    def tearDown(self):
        geo.POLL_SECONDS = self._poll

    def test_stray_marker_is_not_rejected_by_captcha_check(self):
        """页面残留验证痕迹时，只要回答区已达标，仍应判成功（避免误杀）。"""
        text = SIDEBAR + PROMPT + ANSWER + "\n完成验证"
        page = FakePage([text])
        verdict, answer, hit = geo.wait_complete(page, PROMPT, timeout_s=0.5)
        self.assertIsNotNone(hit)
        self.assertEqual(verdict, "ok")
        self.assertTrue(geo.answer_is_valid(answer))

    def test_stable_real_answer_returns_ok(self):
        page = FakePage([PROMPT + ANSWER])
        verdict, answer, _ = geo.wait_complete(page, PROMPT, timeout_s=0.5)
        self.assertEqual(verdict, "ok")
        self.assertTrue(geo.answer_is_valid(answer))

    def test_captcha_page_returns_captcha(self):
        text = SIDEBAR + PROMPT + "\n亲，请拖动下方滑块完成验证"
        page = FakePage([text])
        verdict, _, hit = geo.wait_complete(page, PROMPT, timeout_s=5)
        self.assertEqual(verdict, "captcha")
        self.assertIsNotNone(hit)

    def test_empty_page_returns_empty_not_ok(self):
        """事故根因：侧边栏静止不动曾被视为「完成」。"""
        page = FakePage([SIDEBAR + PROMPT])
        verdict, answer, _ = geo.wait_complete(page, PROMPT, timeout_s=0.5)
        self.assertEqual(verdict, "empty")
        self.assertFalse(geo.answer_is_valid(answer))

    def test_thinking_timer_does_not_count_as_answer(self):
        """豆包「已处理 37 秒」中途帧：回答区过短，必须判无效。"""
        text = SIDEBAR + PROMPT + "\n已处理 37 秒\n我来帮你梳理这个问题。"
        page = FakePage([text])
        verdict, _, _ = geo.wait_complete(page, PROMPT, timeout_s=0.5)
        self.assertEqual(verdict, "empty")

    def test_partial_progress_trace_is_invalid(self):
        """Q07 豆包误报现场：252 字的执行过程文本，不是回答，必须判无效。"""
        trace = (
            "\n已处理 1 分 43 秒\n我来帮你查一下公司的工商信息、口碑评价和代理记账服务情况。"
            "先做几路并行检索。\n获取相关调研结果\n初步拿到了工商信息。我再深挖一下它的官网页面、"
            "企查查详情和用户评价。\n查询示例财税基础信息\n继续补全企查查和公司首页的剩余内容，"
            "并核实代理记账许可证资质。\n查询示例财税相关信息\n继续核实这家公司的备案资质和更多经营细节。"
            "\n梳理财税机构相关信息\n信息基本齐了。最后我读取可视化技能，把评估结果整理成一张可读的结构化图表。"
            "\n搭建评估仪表盘"
        )
        region = geo.slice_answer(SIDEBAR + PROMPT + trace, PROMPT)
        self.assertLess(geo.answer_length(region), geo.MIN_ANSWER_CHARS)
        self.assertFalse(geo.answer_is_valid(region))

    def test_min_answer_chars_matches_audit_rule(self):
        """与 2026-09-16 有效性审计的 400 字阈值保持一致。"""
        self.assertEqual(geo.MIN_ANSWER_CHARS, 400)


class PauseResumeTests(unittest.TestCase):
    """验证弹窗改为「暂停并等待人工处理，处理后继续当前任务」。"""

    CAPTCHA_PAGE = SIDEBAR + PROMPT + "\n亲，请拖动下方滑块完成验证"
    ANSWER_PAGE = SIDEBAR + PROMPT + ANSWER

    def setUp(self):
        self._poll = geo.POLL_SECONDS
        self._ppoll = geo.PAUSE_POLL_SECONDS
        geo.POLL_SECONDS = 0
        geo.PAUSE_POLL_SECONDS = 0
        self.tmp = tempfile.mkdtemp(prefix="geo_pause_")

    def tearDown(self):
        geo.POLL_SECONDS = self._poll
        geo.PAUSE_POLL_SECONDS = self._ppoll
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pause_then_resume_continues_and_succeeds(self):
        """人工完成后继续当前任务，必须能拿到回答。"""
        page = FakePage([self.CAPTCHA_PAGE, self.CAPTCHA_PAGE, self.ANSWER_PAGE])
        calls = []
        verdict, answer, hit = geo.wait_complete(
            page, PROMPT, timeout_s=0.5, resume_grace_s=2,
            on_captcha=lambda h: calls.append(h) or True)
        self.assertTrue(calls)
        self.assertEqual(verdict, "ok")
        self.assertTrue(geo.answer_is_valid(answer))
        self.assertIsNotNone(hit)

    def test_pause_rejected_falls_back_to_captcha(self):
        page = FakePage([self.CAPTCHA_PAGE])
        verdict, _, _ = geo.wait_complete(
            page, PROMPT, timeout_s=0.5,
            on_captcha=lambda h: False)
        self.assertEqual(verdict, "captcha")

    def test_max_pauses_bounds_the_loop(self):
        """验证反复出现时不得死循环。"""
        page = FakePage([self.CAPTCHA_PAGE])
        calls = []
        verdict, _, _ = geo.wait_complete(
            page, PROMPT, timeout_s=0.5, max_pauses=2, resume_grace_s=0.2,
            on_captcha=lambda h: calls.append(h) or True)
        self.assertEqual(verdict, "captcha")
        self.assertEqual(len(calls), 2)

    def test_no_callback_keeps_old_behaviour(self):
        page = FakePage([self.CAPTCHA_PAGE])
        verdict, _, _ = geo.wait_complete(page, PROMPT, timeout_s=0.5)
        self.assertEqual(verdict, "captcha")

    def test_human_pause_auto_resumes_when_marker_disappears(self):
        page = FakePage([self.CAPTCHA_PAGE, self.CAPTCHA_PAGE,
                         self.CAPTCHA_PAGE, self.ANSWER_PAGE])
        self.assertTrue(geo.human_pause(page, self.tmp, "Q01_x_01", "完成验证", timeout_s=5))

    def test_human_pause_times_out(self):
        page = FakePage([self.CAPTCHA_PAGE])
        self.assertFalse(geo.human_pause(page, self.tmp, "Q01_x_01", "完成验证", timeout_s=0.4))

    def test_human_pause_honours_resume_flag(self):
        """操作人在暂停期间创建 resume.flag → 继续（模拟真实时序）。"""
        page = FakePage([self.CAPTCHA_PAGE])
        flag = Path(self.tmp, geo.RESUME_FLAG)

        def _create():
            time.sleep(0.2)
            flag.write_text("go", encoding="utf-8")

        threading.Thread(target=_create, daemon=True).start()
        self.assertTrue(geo.human_pause(page, self.tmp, "Q01_x_01", "完成验证", timeout_s=5))

    def test_stale_resume_flag_is_cleared_before_waiting(self):
        """上一次暂停遗留的 flag 不得直接唤醒本次暂停（防陈旧信号）。"""
        page = FakePage([self.CAPTCHA_PAGE])
        Path(self.tmp, geo.RESUME_FLAG).write_text("stale", encoding="utf-8")
        self.assertFalse(geo.human_pause(page, self.tmp, "Q01_x_01", "完成验证", timeout_s=0.4))
        self.assertFalse(Path(self.tmp, geo.RESUME_FLAG).exists())

    def test_human_pause_writes_audit_trail(self):
        page = FakePage([self.CAPTCHA_PAGE])
        geo.human_pause(page, self.tmp, "Q01_x_01", "完成验证", timeout_s=0.4)
        note = Path(self.tmp, "PAUSED.md").read_text(encoding="utf-8")
        self.assertIn("Q01_x_01", note)
        self.assertIn("完成验证", note)

    def test_human_pause_does_not_resubmit(self):
        """暂停期间不得重新提交问题（每题每平台只提交一次）。"""
        src = (ROOT / "scripts" / "geo_run.py").read_text(encoding="utf-8")
        body = src.split("def human_pause(", 1)[1].split("\ndef ", 1)[0]
        for forbidden in ("submit_once", "keyboard.press", "box.fill"):
            self.assertNotIn(forbidden, body)


class ClassifyScopeTests(unittest.TestCase):
    BRAND = ["示例财税代理服务有限公司", "示例财税"]

    def test_sidebar_brand_hits_are_not_counted(self):
        region = geo.slice_answer(SIDEBAR + PROMPT + ANSWER, PROMPT)
        result = geo.classify(self.BRAND, [], region, [], "non_brand")
        self.assertEqual(result["brand_mention"], "no")
        self.assertEqual(result["recommend_strength"], "none")

    def test_real_mention_still_counted(self):
        region = geo.slice_answer(PROMPT + "推荐示例财税，适合小微企业。", PROMPT)
        result = geo.classify(self.BRAND, [], region, [], "brand_cognition")
        self.assertEqual(result["brand_mention"], "yes")
        self.assertEqual(result["recommend_strength"], "explicit")

    def test_far_away_recommend_word_does_not_make_it_explicit(self):
        """旧代码在找不到公司全称时把窗口退化成整页，导致侧边栏的「推荐」被算作明确推荐。"""
        region = "推荐" + ("无关内容" * 60) + "提到过示例财税"
        result = geo.classify(self.BRAND, [], region, [], "non_brand")
        self.assertEqual(result["brand_mention"], "yes")
        self.assertNotEqual(result["recommend_strength"], "explicit")

    def test_no_brand_means_none(self):
        result = geo.classify(self.BRAND, [], ANSWER, [], "non_brand")
        self.assertEqual(result["brand_mention"], "no")
        self.assertEqual(result["recommend_strength"], "none")


if __name__ == "__main__":
    unittest.main()
