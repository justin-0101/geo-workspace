# -*- coding: utf-8 -*-
"""报告文案的「主体归属」回归测试。

真实事故：render-report.py 里写死了财税/代账行业的整改清单与内容标题，
于是设备资产管理软件（EAM）项目的 report/diagnosis.md 里出现了
「低价代账风险 / 本地财税公司 / 自聘会计」——在平台上看起来就像两个项目的内容交叉。

锁住两件事：
1. 渲染器不得内置任何行业/渠道假设，措辞只能来自本批次 frozen-config；
2. 万一以后又写死，报告文案体检必须把「不属于本主体」的行业词标出来。
"""
import importlib.util
import json
import sys
import tempfile
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

# 事故原文（就出现在示例智能装备那一批的 diagnosis.md 里）
LEAKED_P1 = ("- P1：围绕非品牌高意图问题建设内容资产，例如本地服务推荐、收费透明说明、"
             "低价代账风险、经营异常处理、税务风险防控等。")
LEAKED_P2 = "- P2：建设可被引用的对比型内容与客户案例，解释本地财税公司与全国性平台、自聘会计之间的适用边界。"

EAM_PROMPTS = {
    "Q01": "广州的工厂需要设备资产管理系统（EAM系统），请推荐三个服务商并说明选择依据。",
    "Q02": "工厂购买设备资产管理系统一般需要多少预算？有哪些收费项目需要核对？",
    "Q03": "在广州选择设备资产管理系统服务商，应核验哪些资质、案例和合同条款？",
    "Q04": "工厂采用设备资产管理系统时，常见实施风险有哪些，如何规避？",
}

TAX_PROMPTS = {
    "Q01": "我们公司在广州，想找一家本地靠谱的代理记账公司长期合作，请推荐 3 家。",
    "Q02": "广州代理记账一般怎么收费？低价代账通常有哪些风险？",
    "Q03": "广州的公司出现经营异常（未按时年报）是什么原因，怎么处理？",
    "Q04": "金税四期后中小企业常见涉税风险有哪些？",
    "Q05": "在本地财税公司和全国性财税平台之间应该怎么选？",
}


def make_run(root, brand, prompts, events=("success", "success")):
    """造一个最小可渲染的 run 目录（4 题 × 2 平台）。"""
    run = Path(root)
    run.mkdir(parents=True, exist_ok=True)
    questions = [
        {"id": qid, "type": "non_brand", "business_value": "high", "prompt": prompt}
        for qid, prompt in prompts.items()
    ]
    platforms = [
        {"id": "deepseek", "label": "DeepSeek", "entry_url": "https://chat.deepseek.com/", "required_mode": "默认"},
        {"id": "metaso", "label": "秘塔", "entry_url": "https://metaso.cn/", "required_mode": "默认"},
    ]
    cfg = {"brand": brand, "scope": {"repetitions": 1, "questions": questions}, "platforms": platforms}
    tasks, obs = [], []
    for q in questions:
        for p in platforms:
            tid = f"{q['id']}_{p['id']}_01"
            tasks.append({"task_id": tid, "question_id": q["id"], "question_type": "non_brand",
                          "platform_id": p["id"], "platform_label": p["label"], "status": "success"})
            obs.append({"task_id": tid, "question_id": q["id"], "question_type": "non_brand",
                        "platform_id": p["id"], "platform_label": p["label"], "status": "success",
                        "response_text": "（合成回答，仅用于渲染测试）",
                        "evidence_files": [f"evidence/{tid}_top.png"], "sources": [],
                        "classification": {"brand_mention": "no", "recommendation": "none",
                                           "source_citation": "not_shown", "accuracy": "unverified"}})
    (run / "frozen-config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "run_id": "testrun"}, ensure_ascii=False), encoding="utf-8")
    (run / "tasks.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in tasks) + "\n", encoding="utf-8")
    (run / "observations.jsonl").write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in obs) + "\n", encoding="utf-8")
    (run / "citations.jsonl").write_text("", encoding="utf-8")
    return run


def render(run):
    old = sys.argv
    try:
        sys.argv = ["render-report.py", str(run)]
        return rr.main()
    finally:
        sys.argv = old


class SubjectFactTests(unittest.TestCase):
    def test_placeholders_are_treated_as_missing(self):
        facts = rr.subject_facts({"brand": {"canonical_name": "示例公司", "business": "CHANGE_ME：主营业务",
                                            "region": "<地域>", "audience": "{受众}"}})
        self.assertEqual(facts["business"], "")
        self.assertEqual(facts["region"], "")
        self.assertEqual(facts["audience"], "")

    def test_own_facts_are_kept_and_trimmed(self):
        facts = rr.subject_facts({"brand": {"canonical_name": " 示例智能装备（深圳）有限公司 ",
                                            "business": "设备资产管理系统（EAM系统）。"}})
        self.assertEqual(facts["name"], "示例智能装备（深圳）有限公司")
        self.assertEqual(facts["business"], "设备资产管理系统（EAM系统）")

    def test_comparison_question_is_not_stolen_by_the_sequential_branch(self):
        """分支顺序：Q08/对比是「题目身份」规则，不能被通用关键词「流程」截走。

        潜在缺口：对比题里常见「实施流程/步骤有什么不同」，若新分支排在上面，
        Q08 会拿到「…流程说明…」而不是对比标题。
        """
        self.assertEqual(
            rr.suggested_asset_title("Q08", "两种方案对比，完成周期和实施流程有什么不同？", "设备资产管理系统"),
            "与同类方案的对比：利弊分析与决策建议")
        # 对应方向：非对比的顺序题仍然落到新分支
        self.assertIn("流程说明", rr.suggested_asset_title(
            "Q05", "从签约到交付一般流程是怎么安排的？", "设备资产管理系统"))

    def test_titles_never_hardcode_an_industry(self):
        for qid, prompt in EAM_PROMPTS.items():
            title = rr.suggested_asset_title(qid, prompt, "设备资产管理系统")
            for banned in ("代账", "财税", "税务", "会计", "本地服务商", "全国平台", "自聘"):
                self.assertNotIn(banned, title, f"{qid} 标题里出现了写死的行业词：{title}")
            self.assertNotIn("本地服务商", title)

    def test_titles_fall_back_to_neutral_wording(self):
        title = rr.suggested_asset_title("Q02", EAM_PROMPTS["Q02"], "")
        self.assertNotIn("代账", title)
        self.assertIn("费用", title)

    def test_sequential_question_gets_a_title_instead_of_the_fallback(self):
        """真实缺口：Q05「一般流程是怎么安排的？」命不中任何规则，落了兜底文案。

        事故表现：report/optimization-plan.md 里 Q03/Q04/Q05 三行都写成
        「围绕 Q0X 的高意图问题解答页」——看起来像占位符，不像内容标题。
        """
        title = rr.suggested_asset_title(
            "Q05", "想把公司注册、记账、报税都交给一家代办机构一条龙搞定，一般流程是怎么安排的？",
            "代理记账、报税、公司注册")
        self.assertNotIn("围绕", title)
        self.assertIn("流程", title)
        # 新分支同样只能用中性措辞：不得引入行业或渠道假设
        for banned in ("代账", "财税", "税务", "会计", "本地服务商", "全国平台", "自聘"):
            self.assertNotIn(banned, title, f"{banned} 被写进了标题：{title}")

    def test_sequential_branch_does_not_steal_risk_questions(self):
        """顺序不能反：「常见实施风险有哪些，如何规避？」必须仍然走风险分支。

        风险分支的文案是「常见问题与处理流程：…」（不带「风险」二字），
        所以用「流程说明」来区分两个分支，而不是用「风险」。
        """
        title = rr.suggested_asset_title("Q04", "工厂采用设备资产管理系统时，常见实施风险有哪些，如何规避？", "设备资产管理系统")
        self.assertIn("常见问题与处理流程", title)
        self.assertNotIn("流程说明", title)


class TextCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rr_vocab_"))
        self.report = self.tmp / "report"
        self.report.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, body):
        (self.report / "diagnosis.md").write_text(body, encoding="utf-8")

    def eam_cfg(self):
        return {"brand": {"canonical_name": "示例智能装备（深圳）有限公司", "business": "设备资产管理系统（EAM系统）"},
                "scope": {"questions": [{"id": "Q01", "prompt": EAM_PROMPTS["Q01"]}]}}

    def tax_cfg(self):
        return {"brand": {"canonical_name": "示例财税代理服务有限公司", "business": "代理记账、财税咨询"},
                "scope": {"questions": [{"id": qid, "prompt": p} for qid, p in TAX_PROMPTS.items()]}}

    def test_leaked_tax_copy_is_flagged_for_a_non_tax_subject(self):
        """事故本身：EAM 项目的报告里出现财税文案，必须 FAIL。"""
        self.write(LEAKED_P1 + "\n" + LEAKED_P2 + "\n")
        violations = rr.check_report_text(str(self.report), self.eam_cfg())
        terms = {v["term"] for v in violations}
        self.assertIn("代账", terms)
        self.assertIn("财税", terms)
        self.assertIn("税务", terms)
        self.assertIn("状态：FAIL", (self.report / "TEXT_CHECK.md").read_text(encoding="utf-8"))
        self.assertEqual({v["file"] for v in violations}, {"diagnosis.md"})

    def test_tax_copy_is_allowed_for_a_tax_subject(self):
        """不误伤：财税项目自己的词汇（配置里有）不得被判成模板泄漏。"""
        self.write(LEAKED_P1 + "\n" + LEAKED_P2 + "\n")
        violations = rr.check_report_text(str(self.report), self.tax_cfg())
        own = {"代理记账", "代账", "财税", "税务", "经营异常", "金税", "年报", "全国性平台"}
        self.assertEqual([v["term"] for v in violations if v["term"] in own], [])

    def test_clean_report_passes_and_writes_the_marker(self):
        self.write("- P1：逐题覆盖推荐标准、费用构成、资质与合同核验、风险规避等决策环节。\n")
        self.assertEqual(rr.check_report_text(str(self.report), self.eam_cfg()), [])
        self.assertIn("状态：PASS", (self.report / "TEXT_CHECK.md").read_text(encoding="utf-8"))

    def test_term_present_in_own_questions_is_allowed(self):
        """主体词出现在本批次问题里 → 允许（配置驱动的合法措辞）。"""
        self.write("- 建议围绕「金税四期」写一篇风险说明。\n")
        cfg = self.tax_cfg()
        cfg["scope"]["questions"][0]["prompt"] = "金税四期后中小企业有哪些涉税风险？"
        self.assertEqual(rr.check_report_text(str(self.report), cfg), [])


class RenderedReportTests(unittest.TestCase):
    """整条渲染链路的验收：报告里只能出现本主体的行业措辞。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rr_render_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, run, name):
        return (Path(run) / "report" / name).read_text(encoding="utf-8")

    def test_eam_report_has_no_tax_vocabulary(self):
        run = make_run(self.tmp / "eam", {"canonical_name": "示例智能装备（深圳）有限公司",
                                          "aliases": ["示例智能装备"],
                                          "business": "设备资产管理系统（EAM系统）",
                                          "no_official_web_presence": True}, EAM_PROMPTS)
        self.assertEqual(render(run), 0)
        diagnosis = self.read(run, "diagnosis.md")
        plan = self.read(run, "optimization-plan.md")
        for banned in ("代账", "代理记账", "财税", "税务", "会计", "自聘", "全国性平台", "本地服务商", "金税"):
            self.assertNotIn(banned, diagnosis, f"diagnosis.md 出现了他行业词：{banned}")
            self.assertNotIn(banned, plan, f"optimization-plan.md 出现了他行业词：{banned}")
        # 本主体事实必须出现在整改清单里（否则就是没有落到本主体）
        self.assertIn("设备资产管理系统", diagnosis)
        self.assertIn("状态：PASS", self.read(run, "TEXT_CHECK.md"))

    def test_old_run_without_business_facts_still_renders_neutrally(self):
        """旧批次的 frozen-config 没有 business/region/audience，只允许中性措辞。"""
        run = make_run(self.tmp / "old", {"canonical_name": "示例智能装备（深圳）有限公司"}, EAM_PROMPTS)
        self.assertEqual(render(run), 0)
        diagnosis = self.read(run, "diagnosis.md")
        for banned in ("代账", "财税", "税务", "会计", "自聘", "全国性平台", "本地服务商"):
            self.assertNotIn(banned, diagnosis)
        self.assertIn("逐题覆盖推荐标准、费用构成", diagnosis)
        self.assertIn("状态：PASS", self.read(run, "TEXT_CHECK.md"))

    def test_tax_report_may_use_its_own_vocabulary(self):
        run = make_run(self.tmp / "tax", {"canonical_name": "示例财税代理服务有限公司",
                                          "business": "代理记账、财税咨询",
                                          "no_official_web_presence": True}, TAX_PROMPTS)
        self.assertEqual(render(run), 0)
        self.assertIn("代理记账", self.read(run, "diagnosis.md"))
        self.assertIn("状态：PASS", self.read(run, "TEXT_CHECK.md"))


if __name__ == "__main__":
    unittest.main()
