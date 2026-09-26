"""Unit tests for the evidence-aware generation layer and its quality gate."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import content_generation as generation
import workspace_store as store


ASSET = {
    'title': '设备资产管理系统预算核对指南',
    'body': '',
    'facts': '来源：项目资料 https://example.com/facts',
    'channel': '公众号长文',
    'audience': '制造企业设备负责人',
    'objective': '帮助读者建立可核验的预算核对框架',
    'tone': '专业、克制、具体',
    'keywords': '设备资产管理系统',
    'target_length': 1200,
}

BUNDLE = {
    'project_slug': 'demo',
    'profile': {'canonical_name': '示例主体', 'business': '设备资产管理系统',
                'region': '全国', 'audience': '制造型企业',
                'official_pages': ['https://example.com/about']},
    'source': 'diagnosis',
    'question_id': 'Q02',
    'question': '购买设备资产管理系统一般需要多少预算？',
    'suggested_title': '费用构成与价格风险：区间、包含项与低价陷阱',
    'observations': [],
    'evidence_files': [],
    'notice': '诊断平台回答只用于理解内容缺口，不作为企业事实。',
}


def _with_env(env):
    """Apply an env overlay and return a cleanup callable."""
    previous = {key: os.environ.get(key) for key in env}
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def restore():
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return restore


RICH_BUNDLE = dict(BUNDLE,
                   materials=[{'kind': 'file', 'label': '产品说明', 'file_name': '产品说明.docx',
                               'status': 'ok', 'chars': 900,
                               'excerpt': '本系统支持设备台账、点检、预测性维护与备件管理。\n'
                                          '已通过 ISO 9001 认证，标准交付周期为 8 周。\n'
                                          '实施配置包含数据治理，交付后提供培训与运维支持。\n'
                                          '公司现有服务团队 60 人，覆盖 12 个省份。'}],
                   materials_text='产品说明原文')


class IsolatedStore(unittest.TestCase):
    """把 store 指向临时库。

    必须隔离：内容模型配置现在存在本机数据库里，不隔离就会读到真实设置，
    使测试随用户配置变红或变绿。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='geo-gen-test-')
        self.old = store.DATA, store.DB
        store.DATA = Path(self.tmp.name)
        store.DB = store.DATA / 'test.db'
        store.migrate()

    def tearDown(self):
        store.DATA, store.DB = self.old
        self.tmp.cleanup()


class QualityGateTests(IsolatedStore):
    def test_placeholder_title_and_instruction_title_are_blocked(self):
        for title in ('待填写：补充问题相关的事实与内容：预算怎么核对？',
                      '待补充：选型指南',
                      '补充问题相关的事实与内容：预算怎么核对？'):
            report = generation.quality_check(dict(ASSET, title=title, body='x' * 500))
            self.assertFalse(report['passed'], title)
            self.assertTrue(any('标题' in e for e in report['errors']), title)
        self.assertTrue(generation.quality_check(dict(ASSET, body='内容' * 300))['passed'])

    def test_short_body_is_blocked_per_channel(self):
        short = '## 小节\n\n' + '内容' * 70
        report = generation.quality_check(dict(ASSET, body=short))
        self.assertFalse(report['passed'])
        self.assertTrue(any('低于当前渠道最低要求' in e for e in report['errors']), report['errors'])
        # 小红书门槛更低，同一段正文在小红书渠道可以通过长度检查
        self.assertTrue(generation.quality_check(dict(ASSET, channel='小红书', body=short))['passed'])

    def test_facts_need_a_traceable_source(self):
        cases = ('刚刚发生的', '行业普遍如此', '参考同类项目经验')
        for facts in cases:
            report = generation.quality_check(dict(ASSET, body='内容' * 300, facts=facts))
            self.assertFalse(report['passed'], facts)
        for facts in ('来源：https://example.com/a', '出处：产品说明书第 3 页',
                      '文件：客户提供的验收报告', '报告：QUALITY_REPORT.md 第 2 节'):
            report = generation.quality_check(dict(ASSET, body='内容' * 300, facts=facts))
            self.assertTrue(report['passed'], (facts, report['errors']))

    def test_warnings_do_not_block(self):
        report = generation.quality_check(dict(ASSET, body='内容' * 300, audience='', objective='',
                                               summary='', keywords='未出现的词'))
        self.assertTrue(report['passed'])
        self.assertIn('未填写目标读者', report['warnings'])
        self.assertIn('未填写写作目标', report['warnings'])
        self.assertTrue(any('关键词' in w for w in report['warnings']))

    def test_plain_length_ignores_markdown_and_links(self):
        body = '## 标题\n\n[链接](https://example.com/very/long/url) 正文内容'
        self.assertEqual(generation.quality_check(dict(ASSET, body=body))['plain_length'], len('标题链接正文内容'))


class LocalGeneratorTests(IsolatedStore):
    def test_local_draft_uses_report_title_and_passes_the_gate(self):
        result = generation.local_generate(ASSET, RICH_BUNDLE)
        self.assertEqual(result['title'], RICH_BUNDLE['suggested_title'])
        self.assertEqual(result['engine'], 'local-safe')
        report = generation.quality_check(dict(ASSET, **result))
        self.assertTrue(report['passed'], report['errors'])

    def test_body_never_contains_operator_text(self):
        """回归门禁：写作要求、“待确认”、“下一步”这类面向写作者的话不得进正文。"""
        for bundle in (RICH_BUNDLE, BUNDLE,
                       dict(BUNDLE, materials=[], materials_text='')):
            for objective in ('功能介绍', '帮助选型'):
                asset = dict(ASSET, objective=objective)
                body = generation.local_generate(asset, bundle)['body']
                for phrase in generation.OPERATOR_PHRASES:
                    self.assertNotIn(phrase, body, f'{phrase} 不该出现在正文里')

    def test_material_rows_are_grouped_not_dumped(self):
        """表格类素材要按模块分组、同类模板句合并，不能逐行堆砌。（夹具为合成的，不取自任何真实清单）"""
        repeated = '该条目支持按流程提交，并由系统保留处理过程记录。'
        rows = ['示例版 | 1 | 模块甲 | 登记项一 | 录入或按接口获取、查看登记项一。',
                '示例版 | 2 | 模块甲 | 登记项二 | 对登记项二进行等级评定并保留评定结果。',
                '示例版 | 3 | 模块甲 | 流程项一 | ' + repeated,
                '示例版 | 4 | 模块甲 | 流程项二 | ' + repeated,
                '示例版 | 5 | 模块甲 | 流程项三 | ' + repeated,
                '示例版 | 6 | 模块乙 | 基础项一 | 按资产特征分类，可同时管理实物与虚拟两类资产。',
                '示例版 | 7 | 模块乙 | 基础项二 | 在分类基础上定义型号、规格与参数。',
                '示例版 | 8 | 模块乙 | 基础项三 | 按工艺位置建立多节点树，节点越多数据集成度越高。',
                '示例版 | 9 | 模块乙 | 基础项四 | 描述栏取自最新状态，包含已处置与盘亏记录。']
        bundle = dict(RICH_BUNDLE, materials=[{'kind': 'file', 'label': '示例清单',
                                               'file_name': '示例清单.xlsx', 'status': 'ok',
                                               'chars': 900, 'excerpt': '\n'.join(rows)}])
        body = generation.local_generate(dict(ASSET, objective='功能介绍'), bundle)['body']
        self.assertIn('### 模块甲', body)
        self.assertIn('### 模块乙', body)
        # 三句同模板的条目应合并成一条带适用范围，而不是重复三行
        self.assertEqual(body.count(repeated.rstrip('。')), 1)
        self.assertIn('适用于：流程项一、流程项二、流程项三', body)
        self.assertIn('基础项一：', body)          # 功能名不能丢

    def test_non_tabular_material_keeps_its_lines(self):
        bundle = dict(RICH_BUNDLE,
                      materials=[{'kind': 'note', 'label': '产品说明', 'status': 'ok', 'chars': 60,
                                  'excerpt': '本系统支持设备台账与点检。\n标准交付周期为 8 周。'}])
        body = generation.local_generate(ASSET, bundle)['body']
        self.assertIn('本系统支持设备台账与点检。', body)
        self.assertIn('标准交付周期为 8 周。', body)

    def test_no_source_configured_stays_empty_instead_of_faking_one(self):
        bare = dict(BUNDLE, profile={'business': '设备资产管理系统'},
                    materials=[], materials_text='')
        result = generation.local_generate(dict(ASSET, facts=''), bare)
        body = result['body']
        # 没有事实来源时既不能编造事实，也不能伪造一条“已确认来源”
        self.assertEqual(result['facts'], '')
        self.assertIn('本稿没有素材依据', body)
        for invented in ('已服务', '成功案例', '市场份额', '行业第一', '通过认证'):
            self.assertNotIn(invented, body)
        self.assertFalse(generation.quality_check(dict(ASSET, **result))['passed'])

    def test_official_pages_reach_the_body_without_operator_notes(self):
        result = generation.local_generate(dict(ASSET, facts=''), RICH_BUNDLE)
        self.assertIn('https://example.com/about', result['body'])
        self.assertNotIn('待人工确认', result['body'])
        self.assertIn('待人工确认', result['facts'])          # 来源字段仍保留待确认标记

    def test_local_draft_includes_supplied_facts_verbatim(self):
        facts = '来源：https://example.com/case 已核对范围 2024 年水务项目'
        result = generation.local_generate(dict(ASSET, facts=facts), RICH_BUNDLE)
        self.assertIn(facts, result['body'])

    def test_question_specific_structure_differs_by_question(self):
        budget = generation.local_generate(ASSET, BUNDLE)['body']
        risk = generation.local_generate(ASSET, dict(BUNDLE, question_id='Q04',
                                                      suggested_title=''))['body']
        self.assertIn('预算不只是软件报价', budget)
        self.assertIn('风险往往从基础数据开始', risk)
        self.assertNotEqual(budget, risk)

    def test_profile_audience_is_used_when_asset_has_none(self):
        result = generation.local_generate(dict(ASSET, audience=''), BUNDLE)
        self.assertIn('制造型企业', result['body'])


class RemoteGeneratorTests(IsolatedStore):
    def test_capability_reports_local_engine_without_configuration(self):
        restore = _with_env({'GEO_CONTENT_LLM_BASE_URL': None, 'GEO_CONTENT_LLM_MODEL': None,
                             'GEO_CONTENT_LLM_API_KEY': None})
        try:
            status = generation.capability()
            self.assertFalse(status['configured'])
            self.assertEqual(status['engine'], 'local-safe')
            # 说清楚它能做什么、不能做什么
            self.assertIn('只能整理素材', status['message'])
            self.assertIn('不能成稿', status['message'])
        finally:
            restore()

    def test_missing_configuration_falls_back_to_local_engine(self):
        restore = _with_env({'GEO_CONTENT_LLM_BASE_URL': None, 'GEO_CONTENT_LLM_MODEL': None})
        try:
            with patch.object(generation, 'openai_generate', side_effect=AssertionError('不应调用远端')):
                self.assertEqual(generation.generate(ASSET, BUNDLE)['engine'], 'local-safe')
        finally:
            restore()

    def test_configured_engine_is_used_and_metadata_is_recorded(self):
        restore = _with_env({'GEO_CONTENT_LLM_BASE_URL': 'https://llm.example.com/v1',
                             'GEO_CONTENT_LLM_MODEL': 'demo-model',
                             'GEO_CONTENT_LLM_API_KEY': 'secret-token'})
        remote = {'title': '远端标题', 'summary': '远端摘要',
                  'body': '## 小节\n\n' + '远端正文内容' * 60, 'facts': ASSET['facts'],
                  'engine': 'openai-compatible', 'model': 'demo-model', 'warnings': []}
        try:
            self.assertTrue(generation.capability()['configured'])
            with patch.object(generation, 'openai_generate', return_value=dict(remote)) as mocked:
                result = generation.generate(ASSET, BUNDLE)
            self.assertTrue(mocked.called)
            self.assertEqual(result['engine'], 'openai-compatible')
            self.assertEqual(result['model'], 'demo-model')
            self.assertIn('generated_at', result)
        finally:
            restore()

    def test_remote_http_failure_is_reported_without_leaking_the_token(self):
        restore = _with_env({'GEO_CONTENT_LLM_BASE_URL': 'https://llm.example.com/v1',
                             'GEO_CONTENT_LLM_MODEL': 'demo-model',
                             'GEO_CONTENT_LLM_API_KEY': 'super-secret-token'})
        try:
            with patch.object(generation.request, 'urlopen', side_effect=OSError('connection refused')):
                with self.assertRaises(ValueError) as ctx:
                    generation.openai_generate(ASSET, BUNDLE)
            message = str(ctx.exception)
            self.assertIn('内容模型请求失败', message)
            self.assertNotIn('super-secret-token', message)
        finally:
            restore()

    def test_malformed_remote_payload_is_rejected(self):
        restore = _with_env({'GEO_CONTENT_LLM_BASE_URL': 'https://llm.example.com/v1',
                             'GEO_CONTENT_LLM_MODEL': 'demo-model',
                             'GEO_CONTENT_LLM_API_KEY': None})
        try:
            class Response:
                def read(self):
                    return json.dumps({'choices': [{'message': {'content': 'not json at all'}}]}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False
            with patch.object(generation.request, 'urlopen', return_value=Response()):
                with self.assertRaises(ValueError) as ctx:
                    generation.openai_generate(ASSET, BUNDLE)
            self.assertIn('JSON', str(ctx.exception))
        finally:
            restore()

    def test_endpoint_is_normalised(self):
        self.assertEqual(generation._endpoint('https://x.example.com/v1'),
                         'https://x.example.com/v1/chat/completions')
        self.assertEqual(generation._endpoint('https://x.example.com/v1/chat/completions'),
                         'https://x.example.com/v1/chat/completions')


if __name__ == '__main__':
    unittest.main()
