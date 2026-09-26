# -*- coding: utf-8 -*-
"""平台熔断集成测试：单平台失败不得阻塞整批。

用假浏览器驱动 geo_run.main() 的真实链路（含 PowerShell 状态写入），验证：
- 被验证拦截的平台熔断后，剩余任务记为 failed 并跳过（不阻塞）
- 其他平台继续跑完并 success
- 批次全部进入终态（无 pending / running / manual_required）
- 熔断平台不产生 manual_required，因此能进入下一步
- 被跳过的任务也有 observation + 证据文件；写 PLATFORM_DEGRADED.md
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROMPT_TMPL = "问题{i}：设备管理系统的核心功能包括哪些？"
ANSWER = "设备管理系统的核心功能包括台账管理、点检巡检、维修工单、备件管理等。" * 20
SIDEBAR = "新建对话\n最近对话\n示例公司服务介绍\n南沙代理记账推荐\n"
CAPTCHA = "\n亲，请拖动下方滑块完成验证\n请按住滑块，拖动到最右边"


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


geo = load_script("geo_run.py")


class FakeKeyboard:
    def __init__(self):
        self.page = None

    def press(self, *a, **kw):
        pass

    def type(self, text, **kw):
        # contenteditable 输入框走 keyboard.type，也要把问题记到页面上
        if self.page is not None and text:
            self.page.set_prompt(text)


class FakeBox:
    """只模拟输入框；fill() 把问题记到页面上，等价于「把问题敲进输入框」。"""

    def __init__(self, page):
        self._page = page

    def click(self, **kw):
        pass

    def fill(self, text):
        if text:
            self._page.set_prompt(text)

    def get_attribute(self, name):
        return None


class FakePage:
    def __init__(self, pid, prompts, blocked):
        self.pid = pid
        self._prompts = prompts
        self._blocked = blocked
        self._q = ""
        self.main_frame = None
        self.frames = []
        self.keyboard = FakeKeyboard()
        self.keyboard.page = self

    # --- 输入框/验证容器 ---
    def query_selector(self, sel):
        # 只认输入框；验证容器一律不存在，避免验证检测被假页面误命中
        if "textarea" in sel or "contenteditable" in sel:
            return FakeBox(self)
        return None

    def query_selector_all(self, sel):
        return []

    def get_by_text(self, text, **kw):
        raise AssertionError("click_text 不应在假页面里真的点到东西")

    def evaluate(self, js):
        return []

    # --- 导航与等待 ---
    def goto(self, url, **kw):
        pass

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path=None, **kw):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n fake")

    def inner_text(self, sel):
        q = self._q or (self._prompts[0] if self._prompts else "")
        return SIDEBAR + q + (CAPTCHA if self._blocked else ANSWER)

    def set_prompt(self, q):
        self._q = q


class FakePw:
    def stop(self):
        pass


class FakeCtx:
    """每平台一个页面，分配顺序即 main() 首次 get_page 的顺序。"""

    def __init__(self, prompts, blocked_pid, platform_order):
        self._prompts = prompts
        self._blocked_pid = blocked_pid
        self._order = list(platform_order)
        self.closed = False

    def new_page(self):
        pid = self._order.pop(0) if self._order else "unknown"
        return FakePage(pid, self._prompts, pid == self._blocked_pid)

    def close(self):
        self.closed = True


class PlatformBreakerTests(unittest.TestCase):
    """两个平台 × 三道题：其中一个平台被验证拦截。"""

    PLATFORMS = [
        {"id": "doubao", "label": "豆包", "entry_url": "https://www.doubao.com/chat/",
         "required_mode": "默认模式"},
        {"id": "deepseek", "label": "DeepSeek", "entry_url": "https://chat.deepseek.com/",
         "required_mode": "默认对话模型"},
    ]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="geo_breaker_"))
        (self.tmp / "evidence").mkdir()
        (self.tmp / "raw").mkdir()
        (self.tmp / "observations.jsonl").write_text("", encoding="utf-8")
        cfg = {
            "schema_version": "1.0",
            "brand": {"canonical_name": "示例公司", "aliases": ["示例"],
                      "official_domains": [], "official_pages": []},
            "scope": {"repetitions": 1, "questions": [
                {"id": f"Q0{i}", "type": "non_brand", "business_value": "high",
                 "prompt": PROMPT_TMPL.format(i=i)} for i in (1, 2, 3)]},
            "platforms": self.PLATFORMS,
        }
        (self.tmp / "frozen-config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        tasks = []
        for i in (1, 2, 3):
            for pl in self.PLATFORMS:
                # 字段集必须与 initialize-run.ps1 产出一致：
                # update-task-state.ps1 带 Set-StrictMode，缺字段会直接报错
                tasks.append({
                    "task_id": f"Q0{i}_{pl['id']}_01", "question_id": f"Q0{i}",
                    "question_type": "non_brand", "business_value": "high",
                    "platform_id": pl["id"], "platform_label": pl["label"],
                    "repetition": 1, "status": "pending",
                    "started_at": None, "completed_at": None,
                    "observation_ref": None, "failure_reason": None,
                })
        (self.tmp / "tasks.jsonl").write_text(
            "\n".join(json.dumps(t, ensure_ascii=False) for t in tasks), encoding="utf-8")
        # main() 会遍历 pending；平台首次出现顺序决定页面分配顺序
        self.platform_order = [t["platform_id"] for t in tasks]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, blocked_pid):
        prompts = [PROMPT_TMPL.format(i=i) for i in (1, 2, 3)]
        ctx = FakeCtx(prompts, blocked_pid, self.platform_order)
        geo.open_browser = lambda: (FakePw(), ctx)
        # 人工接力一律失败 → 走熔断路径（不等 30 分钟）
        geo.human_pause = lambda page, run_dir, task_id, hit, timeout_s=None: False
        # 等回答的超时压到 1 秒：真实默认 170 秒，会让“空回答”用例拖死测试
        _orig_wait = geo.wait_complete

        def _fast_wait(page, prompt, **kw):
            kw["timeout_s"] = 1
            return _orig_wait(page, prompt, **kw)

        geo.wait_complete = _fast_wait
        old_poll, old_argv = geo.POLL_SECONDS, sys.argv
        geo.POLL_SECONDS = 0
        try:
            sys.argv = ["geo_run.py", str(self.tmp)]
            geo.main()
        finally:
            geo.wait_complete = _orig_wait
            geo.POLL_SECONDS = old_poll
            sys.argv = old_argv

    def tasks(self):
        raw = (self.tmp / "tasks.jsonl").read_text(encoding="utf-8-sig")
        return [json.loads(l) for l in raw.splitlines() if l.strip()]

    def obs(self):
        raw = (self.tmp / "observations.jsonl").read_text(encoding="utf-8")
        return [json.loads(l) for l in raw.splitlines() if l.strip()]

    def status_map(self):
        return {t["task_id"]: t["status"] for t in self.tasks()}

    # --- 核心需求：单平台失败不得阻塞整批 ---

    def test_failing_platform_does_not_stop_the_batch(self):
        self.run_main(blocked_pid="doubao")
        st = self.status_map()
        self.assertEqual(st["Q01_doubao_01"], "failed")
        self.assertEqual(st["Q01_deepseek_01"], "success")
        self.assertEqual(st["Q02_deepseek_01"], "success")
        self.assertEqual(st["Q03_deepseek_01"], "success")

    def test_remaining_tasks_of_blocked_platform_are_skipped(self):
        self.run_main(blocked_pid="doubao")
        st = self.status_map()
        for tid in ("Q02_doubao_01", "Q03_doubao_01"):
            self.assertEqual(st[tid], "failed")
        obs = {o["task_id"]: o for o in self.obs()}
        for tid in ("Q02_doubao_01", "Q03_doubao_01"):
            self.assertIn("熔断", obs[tid]["failure_reason"])
            self.assertIn("未提交", obs[tid]["notes"])

    def test_batch_reaches_terminal_state_without_manual_required(self):
        self.run_main(blocked_pid="doubao")
        statuses = {t["status"] for t in self.tasks()}
        self.assertTrue(statuses <= {"success", "failed"}, statuses)

    def test_skipped_tasks_have_existing_evidence(self):
        self.run_main(blocked_pid="doubao")
        obs = {o["task_id"]: o for o in self.obs()}
        for tid in ("Q02_doubao_01", "Q03_doubao_01"):
            self.assertTrue(obs[tid]["evidence_files"], tid)
            for rel in obs[tid]["evidence_files"]:
                self.assertTrue((self.tmp / rel).exists(), f"{tid} 证据不存在：{rel}")

    def test_observation_status_matches_task_status(self):
        """validate-run.ps1 要求 observation.status == task.status。"""
        self.run_main(blocked_pid="doubao")
        obs = {o["task_id"]: o["status"] for o in self.obs()}
        for t in self.tasks():
            self.assertEqual(obs.get(t["task_id"]), t["status"], t["task_id"])

    def test_platform_degraded_record_written(self):
        self.run_main(blocked_pid="doubao")
        note = self.tmp / "PLATFORM_DEGRADED.md"
        self.assertTrue(note.exists())
        text = note.read_text(encoding="utf-8")
        self.assertIn("doubao", text)
        self.assertIn("不阻塞", text)

    def test_successful_platform_tasks_counter_reset(self):
        """另一平台不受牵连：熔断不写进它的任务。"""
        self.run_main(blocked_pid="doubao")
        obs = {o["task_id"]: o for o in self.obs()}
        for tid in ("Q01_deepseek_01", "Q02_deepseek_01", "Q03_deepseek_01"):
            self.assertEqual(obs[tid]["status"], "success")
            self.assertIsNone(obs[tid]["failure_reason"])
            self.assertTrue(geo.answer_is_valid(obs[tid]["response_text"]))

    def test_mirror_case_other_platform_blocked(self):
        self.run_main(blocked_pid="deepseek")
        st = self.status_map()
        self.assertEqual(st["Q01_deepseek_01"], "failed")
        self.assertEqual(st["Q01_doubao_01"], "success")
        self.assertEqual(st["Q03_doubao_01"], "success")


class BreakerPredicateTests(unittest.TestCase):
    def test_captcha_trips_immediately(self):
        self.assertTrue(geo.should_trip_platform("captcha", 1))

    def test_empty_trips_at_threshold(self):
        self.assertFalse(geo.should_trip_platform("empty", 1))
        self.assertTrue(geo.should_trip_platform("empty", geo.MAX_CONSECUTIVE_INVALID))

    def test_ok_never_trips(self):
        self.assertFalse(geo.should_trip_platform("ok", 99))
        self.assertFalse(geo.should_trip_platform("ok_timeout", 99))

    def test_max_consecutive_invalid_defaults(self):
        self.assertTrue(geo.should_trip_platform("empty", 2, threshold=2))
        self.assertFalse(geo.should_trip_platform("empty", 1, threshold=2))

    def test_global_error_matching(self):
        self.assertTrue(geo.is_global_error("Target page, context or browser has been closed"))
        self.assertTrue(geo.is_global_error("net::ERR_CONNECTION_REFUSED"))
        self.assertFalse(geo.is_global_error("Timeout 170000ms exceeded"))
        self.assertFalse(geo.is_global_error("找不到输入框: textarea"))


if __name__ == "__main__":
    unittest.main()
