# -*- coding: utf-8 -*-
"""平台熔断在报告层的回归测试。

锁住一类危险 bug：解析熔断记录失败时**静默返回空**，会产出「假装覆盖全部平台」的报告。
（真实事故：`render-report.py` 漏 import re，被 `except Exception: return []` 吞掉。）
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", "").replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rr = load_script("render-report.py")

DEGRADED_MD = """# 平台熔断记录

以下平台在本批次被熔断，剩余任务按 `failed` 跳过；批次仍继续执行其他平台，单平台失败不阻塞后续步骤。

- `doubao`（豆包）：平台熔断：{reason}
- `qianwen`（千问）：平台熔断（页面异常）

报告不得据此声称覆盖全部平台。
""".format(reason="验证经人工接力仍未能恢复")


class DegradedPlatformParsingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="rr_deg_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_means_no_degradation(self):
        self.assertEqual(rr.read_degraded_platforms(str(self.tmp)), [])

    def test_parses_platform_ids(self):
        (self.tmp / "PLATFORM_DEGRADED.md").write_text(DEGRADED_MD, encoding="utf-8")
        self.assertEqual(rr.read_degraded_platforms(str(self.tmp)), ["doubao", "qianwen"])

    def test_regex_module_is_actually_imported(self):
        """直接锁住事故本身：函数内部用到 re，模块必须 import re。"""
        self.assertTrue(hasattr(rr, "re"))
        src = (ROOT / "scripts" / "render-report.py").read_text(encoding="utf-8")
        self.assertIn("import re", src)

    def test_malformed_file_raises_instead_of_silently_passing(self):
        """宁可报错，也不要静默产出一份「看起来很干净」的报告。"""
        (self.tmp / "PLATFORM_DEGRADED.md").write_bytes(b"\xff\xfe\x00broken")
        with self.assertRaises(Exception):
            rr.read_degraded_platforms(str(self.tmp))


class PlatformTableTests(unittest.TestCase):
    def metrics(self, degraded):
        return {
            "degraded_platforms": degraded,
            "platforms": [
                {"id": "deepseek", "label": "DeepSeek", "planned": 8, "success": 8, "failed": 0,
                 "non_brand_success": 5, "non_brand_mention": 1, "non_brand_explicit": 1,
                 "official_citation_yes": 0},
                {"id": "doubao", "label": "豆包", "planned": 8, "success": 1, "failed": 7,
                 "non_brand_success": 0, "non_brand_mention": 0, "non_brand_explicit": 0,
                 "official_citation_yes": 0},
            ],
        }

    def test_no_degradation_keeps_numbers(self):
        table = rr.platform_table(self.metrics([]))
        self.assertNotIn("不可用", table)
        self.assertIn("| 豆包 | 8 | 1 | 7 |", table)

    def test_degraded_platform_metrics_marked_unavailable(self):
        table = rr.platform_table(self.metrics(["doubao"]))
        self.assertIn("不可用", table)
        # 不能把熔断平台写成 0
        self.assertNotIn("| 豆包 | 8 | 1 | 7 | 0 | 0 | 0 | 0 |", table)
        self.assertIn("不得据此声称覆盖全部平台", table)

    def test_healthy_platform_keeps_numbers_alongside_degraded(self):
        table = rr.platform_table(self.metrics(["doubao"]))
        self.assertIn("| DeepSeek | 8 | 8 | 0 | 5 | 1 | 1 | 0 |", table)


if __name__ == "__main__":
    unittest.main()
