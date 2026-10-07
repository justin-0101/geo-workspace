import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workspace_store as store
from diagnosis_config import check_question_quality, freeze_config, suggest_questions
import diagnosis_question_llm as question_llm
from workflow_api import app
from fastapi.testclient import TestClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode('utf8')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class QuestionGenerationTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            'canonical_name': '广州设备云', 'business': '设备资产管理系统',
            'region': '广州', 'audience': '制造企业', 'aliases': ['设备云'],
            'official_pages': [], 'no_official_web_presence': True,
        }

    def test_optional_scenarios_change_questions_and_keep_boundary(self):
        plain = suggest_questions(self.profile)
        profiled = dict(self.profile, scenarios=['刚成立的工厂'],
                        trigger_problems=['设备故障频发'], alternatives=['自建维修团队'])
        detailed = suggest_questions(profiled)
        self.assertNotEqual([q['prompt'] for q in plain], [q['prompt'] for q in detailed])
        self.assertEqual([q['id'] for q in detailed], [f'Q{i:02}' for i in range(1, 9)])
        self.assertEqual(check_question_quality(profiled, detailed), {'ok': True, 'errors': [], 'warnings': []})
        for q in detailed[:5]:
            self.assertNotIn('广州设备云', q['prompt'])
            self.assertNotIn('设备云', q['prompt'])

    def test_quality_gate_requires_names_for_brand_questions(self):
        profile = dict(self.profile, official_pages=['https://example.com'])
        questions = suggest_questions(profile)
        questions[5]['prompt'] = 'https://example.com 是做什么的？'
        quality = check_question_quality(profile, questions)
        self.assertFalse(quality['ok'])
        self.assertTrue(any('主体名称或别名' in item for item in quality['errors']))

    def test_quality_gate_rejects_audit_copy_and_freeze_preserves_manual_prompt(self):
        questions = suggest_questions(self.profile)
        questions[0]['prompt'] = '推荐一家服务商，请注明信息来源。'
        quality = check_question_quality(self.profile, questions)
        self.assertFalse(quality['ok'])
        self.assertTrue(any('审计式' in item for item in quality['errors']))
        with self.assertRaisesRegex(ValueError, '审计式'):
            freeze_config(self.profile, questions, ['deepseek'])

        questions = suggest_questions(self.profile)
        questions[0]['prompt'] = '我想给工厂选设备管理服务，应该怎么选？'
        config = freeze_config(self.profile, questions, ['deepseek'])
        self.assertEqual(config['scope']['questions'][0]['prompt'], questions[0]['prompt'])

    def test_llm_accepts_code_block_json_and_returns_fixed_metadata(self):
        prompts = [{'prompt': f'用户想了解第{i}个问题'} for i in range(1, 6)] + [
            {'prompt': '广州设备云是做什么的？'},
            {'prompt': '广州设备云适合哪些客户？'},
            {'prompt': '广州设备云和自建团队相比怎么选？'},
        ]
        payload = {'choices': [{'message': {'content': '```json\\n' + json.dumps({'questions': prompts}, ensure_ascii=False) + '\\n```'}}]}
        with patch.object(question_llm.content_llm_config, 'resolved', return_value=(
                {'base_url': 'https://model.example/v1', 'model': 'mock-model', 'api_key': 'sk-secret'}, {})), \
             patch.object(question_llm.request, 'urlopen', return_value=_Response(payload)):
            result = question_llm.generate_questions(self.profile)
        self.assertFalse(result['generation']['fallback'])
        self.assertEqual(result['generation']['model'], 'mock-model')
        self.assertTrue(result['quality']['ok'])
        self.assertEqual([q['id'] for q in result['questions']], [f'Q{i:02}' for i in range(1, 9)])
        self.assertEqual(result['questions'][0]['subcat'], 'recommendation')

    def test_invalid_json_gets_one_repair_then_falls_back(self):
        calls = []
        bad = {'choices': [{'message': {'content': 'not json'}}]}
        with patch.object(question_llm.content_llm_config, 'resolved', return_value=(
                {'base_url': 'https://model.example/v1', 'model': 'mock-model', 'api_key': ''}, {})), \
             patch.object(question_llm.request, 'urlopen', side_effect=lambda req, timeout=None: (calls.append(req), _Response(bad))[1]):
            result = question_llm.generate_questions(self.profile)
        self.assertTrue(result['generation']['fallback'])
        self.assertIn('回退', result['generation']['warning'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(result['quality']['ok'])

    def test_quality_failure_is_not_reported_as_llm_success(self):
        bad_questions = [{'prompt': '广州设备云'}] * 8
        payload = {'choices': [{'message': {'content': json.dumps({'questions': bad_questions})}}]}
        with patch.object(question_llm.content_llm_config, 'resolved', return_value=(
                {'base_url': 'https://model.example/v1', 'model': 'mock-model', 'api_key': ''}, {})), \
             patch.object(question_llm.request, 'urlopen', return_value=_Response(payload)) as mocked:
            result = question_llm.generate_questions(self.profile)
        self.assertTrue(result['generation']['fallback'])
        self.assertEqual(mocked.call_count, 2)
        self.assertIn('质量门禁', result['generation']['warning'])

    def test_fallback_without_model_has_metadata_and_no_key(self):
        secret = 'sk-never-return-this'
        with patch.object(question_llm.content_llm_config, 'resolved', return_value=(
                {'base_url': '', 'model': '', 'api_key': secret}, {})), \
             patch.object(question_llm.request, 'urlopen') as mocked:
            result = question_llm.generate_questions(self.profile)
        self.assertTrue(result['generation']['fallback'])
        self.assertEqual(result['generation']['model'], '')
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        mocked.assert_not_called()

    def test_suggestion_api_returns_quality_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = store.DATA, store.DB
            store.DATA, store.DB = Path(tmp), Path(tmp) / 'test.db'
            try:
                with TestClient(app) as client:
                    slug = client.post('/api/projects', json={'name': '测试项目'}).json()['slug']
                    profile = dict(self.profile)
                    client.put('/api/projects/' + slug + '/profile', json={
                        'profile': profile, 'questions': [], 'platforms': [], 'revision': 0})
                    response = client.post('/api/projects/' + slug + '/question-suggestions')
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertTrue(body['quality']['ok'])
                    self.assertEqual(len(body['questions']), 8)
                    self.assertTrue(all('layer' in q and 'intent' in q for q in body['questions']))
                    self.assertIn(body['generation']['engine'], {'local-deterministic', 'openai-compatible'})
                    self.assertIn('fallback', body['generation'])
                    self.assertNotIn('api_key', json.dumps(body, ensure_ascii=False))
            finally:
                store.DATA, store.DB = old


if __name__ == '__main__':
    unittest.main()
