"""报告文案守卫在平台侧的回归测试。

真实事故：共享渲染脚本（geo-diagnosis-single/scripts/render-report.py）里写死的
财税/代账文案，出现在设备资产管理软件项目的 report/diagnosis.md 里；
在报告中心页面看起来就像"两个项目的内容交叉"。

平台侧锁两件事：
1. 冻结配置必须带上项目自己的主体事实（渲染器唯一的合法文案来源）；
2. 报告文案体检 FAIL 时，平台必须能读到并把批次判为 degraded，而不是 completed。
"""
import shutil
import tempfile
import unittest
from pathlib import Path

import browser_engine as engine
from diagnosis_config import freeze_config, suggest_questions


class FreezeConfigSubjectFactsTests(unittest.TestCase):
    def profile(self):
        return dict(canonical_name='示例智能装备（深圳）有限公司',
                    business='设备资产管理系统（EAM系统）', region='华南', audience='制造企业',
                    aliases=['示例智能装备'], official_pages=[], no_official_web_presence=True)

    def test_brand_block_carries_subject_facts(self):
        profile = self.profile()
        cfg = freeze_config(profile, suggest_questions(profile), ['deepseek'])
        brand = cfg['brand']
        self.assertEqual(brand['business'], '设备资产管理系统（EAM系统）')
        self.assertEqual(brand['region'], '华南')
        self.assertEqual(brand['audience'], '制造企业')
        for key in ('business', 'region', 'audience'):
            self.assertNotIn('CHANGE_ME', brand[key])

    def test_facts_do_not_leak_into_the_non_brand_gate(self):
        """主体事实进配置，但前五题仍然不能出现主体名（原有门禁不能被这次改动放松）。"""
        profile = self.profile()
        questions = suggest_questions(profile)
        cfg = freeze_config(profile, questions, ['deepseek'])
        self.assertEqual(len(cfg['scope']['questions']), 8)
        questions[0]['prompt'] += '示例智能装备'
        with self.assertRaises(ValueError):
            freeze_config(profile, questions, ['deepseek'])


class TextCheckReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='textcheck_'))
        (self.tmp / 'report').mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, body):
        (self.tmp / 'report' / 'TEXT_CHECK.md').write_text(body, encoding='utf-8')

    def test_missing_file_is_not_a_failure(self):
        """旧批次没有这个文件，不能因此全部变 degraded。"""
        self.assertEqual(engine.text_check_failures(self.tmp), [])

    def test_pass_marker_is_not_a_failure(self):
        self.write('# 报告文案体检\n\n- 状态：PASS\n')
        self.assertEqual(engine.text_check_failures(self.tmp), [])

    def test_fail_rows_are_parsed(self):
        self.write('# 报告文案体检\n\n- 状态：FAIL\n\n'
                   '| 文件 | 命中词 | 出现位置 |\n|---|---|---|\n'
                   '| diagnosis.md | 代账 | - P1：低价代账风险 |\n'
                   '| optimization-plan.md | 财税 | - P2：本地财税公司 |\n')
        self.assertEqual(engine.text_check_failures(self.tmp),
                         ['代账（diagnosis.md）', '财税（optimization-plan.md）'])

    def test_fail_without_table_still_blocks(self):
        """标了 FAIL 却读不到词条：宁可当成有问题（fail-closed），也不要静默放过。"""
        self.write('# 报告文案体检\n\n- 状态：FAIL\n')
        self.assertEqual(len(engine.text_check_failures(self.tmp)), 1)


if __name__ == '__main__':
    unittest.main()
