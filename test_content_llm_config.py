"""内容生成模型的本地配置：保存、掩码、环境变量优先级、连通性检测。

这些用例一律 mock HTTP，不对外发请求；只有手动点「测试连接」才会真的连。
"""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from test_workflow_api import APITests
import content_generation as generation
import content_llm_config as config
import workspace_store as store

ENV_KEYS = ('GEO_CONTENT_LLM_BASE_URL', 'GEO_CONTENT_LLM_MODEL', 'GEO_CONTENT_LLM_API_KEY')


def without_env(case):
    """暂时清掉 GEO_CONTENT_LLM_*，保证测的是本机保存值。"""
    patcher = patch.dict(os.environ, {key: '' for key in ENV_KEYS})
    patcher.start()
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    case.addCleanup(patcher.stop)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode('utf8')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ConfigStorageTests(APITests):
    def setUp(self):
        super().setUp()
        without_env(self)

    def test_defaults_to_local_engine(self):
        status = self.client.get('/api/content-llm').json()
        self.assertFalse(status['configured'])
        self.assertIn('不能成稿', generation.capability()['message'])
        self.assertEqual(generation.capability()['engine'], 'local-safe')

    def test_save_then_capability_reports_the_model(self):
        saved = self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
            'api_key': 'sk-abcdefghijklmnop'})
        self.assertEqual(saved.status_code, 200)
        body = saved.json()
        self.assertTrue(body['configured'])
        self.assertEqual(body['model'], 'deepseek-chat')
        self.assertTrue(body['api_key']['configured'])
        self.assertNotIn('sk-abcdefghijklmnop', saved.text)          # 不回显明文
        self.assertIn('****', body['api_key']['masked'])
        capability = generation.capability()
        self.assertEqual(capability['engine'], 'openai-compatible')
        self.assertTrue(capability['configured'])
        self.assertIn('deepseek-chat', capability['message'])

    def test_empty_field_keeps_the_previous_value(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
            'api_key': 'sk-abcdefghijklmnop'})
        # 只改模型名，密钥留空应保持原值
        again = self.client.put('/api/content-llm', json={'model': 'deepseek-reasoner'})
        body = again.json()
        self.assertEqual(body['model'], 'deepseek-reasoner')
        self.assertEqual(body['base_url'], 'https://api.deepseek.com/v1')
        self.assertTrue(body['api_key']['configured'])

    def test_clear_returns_to_local_engine(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'})
        cleared = self.client.delete('/api/content-llm').json()
        self.assertFalse(cleared['configured'])
        self.assertEqual(generation.capability()['engine'], 'local-safe')

    def test_missing_model_or_url_is_not_configured(self):
        self.client.put('/api/content-llm', json={'base_url': 'https://api.deepseek.com/v1'})
        self.assertFalse(self.client.get('/api/content-llm').json()['configured'])

    def test_env_var_wins_over_saved_value(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://saved.example.com/v1', 'model': 'saved-model'})
        with patch.dict(os.environ, {'GEO_CONTENT_LLM_BASE_URL': 'https://env.example.com/v1',
                                     'GEO_CONTENT_LLM_MODEL': 'env-model'}):
            status = self.client.get('/api/content-llm').json()
            self.assertEqual(status['base_url'], 'https://env.example.com/v1')
            self.assertEqual(status['model'], 'env-model')
            self.assertEqual(status['source']['model'], 'env')

    def test_status_never_returns_the_plaintext_key(self):
        secret = 'sk-super-secret-value-1234'
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
            'api_key': secret})
        for path in ('/api/content-llm',):
            self.assertNotIn(secret, self.client.get(path).text)
        with store.connection() as c:
            rows = dict(c.execute("SELECT key,value FROM workspace_preferences WHERE key LIKE 'content_llm:%'"))
        self.assertEqual(rows['content_llm:model'], 'deepseek-chat')     # 配置确实落库了


class ConnectivityCheckTests(APITests):
    def setUp(self):
        super().setUp()
        without_env(self)

    def test_check_reports_success(self):
        with patch.object(config.request, 'urlopen',
                          return_value=_Response({'choices': [{'message': {'content': '可用'}}],
                                                  'model': 'deepseek-flash'})):
            out = self.client.post('/api/content-llm/check', json={
                'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
                'api_key': 'sk-test'}).json()
        self.assertIn('连接成功', out['message'])
        self.assertIn('deepseek-chat', out['message'])

    def test_check_without_config_says_what_is_missing(self):
        response = self.client.post('/api/content-llm/check', json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn('接口地址', response.json()['detail'])

    def test_check_reports_auth_failure_without_leaking_the_key(self):
        def boom(*args, **kwargs):
            raise HTTPError('u', 401, 'Unauthorized', {}, None)
        with patch.object(config.request, 'urlopen', side_effect=boom):
            response = self.client.post('/api/content-llm/check', json={
                'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
                'api_key': 'sk-secret-token'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('401', response.json()['detail'])
        self.assertNotIn('sk-secret-token', response.text)

    def test_check_reports_non_openai_shape(self):
        with patch.object(config.request, 'urlopen', return_value=_Response({'error': 'nope'})):
            response = self.client.post('/api/content-llm/check', json={
                'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('OpenAI 兼容', response.json()['detail'])

    def test_check_uses_saved_key_when_field_is_left_blank(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
            'api_key': 'sk-saved'})
        seen = {}

        def capture(req, timeout=None):
            seen['auth'] = req.headers.get('Authorization')
            return _Response({'choices': [{'message': {'content': '可用'}}]})
        with patch.object(config.request, 'urlopen', side_effect=capture):
            self.client.post('/api/content-llm/check', json={'api_key': ''})
        self.assertEqual(seen['auth'], 'Bearer sk-saved')


class GeneratorUsesSavedConfigTests(APITests):
    def setUp(self):
        super().setUp()
        without_env(self)

    def test_generate_uses_the_saved_model_and_records_the_engine(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat',
            'api_key': 'sk-test'})
        slug = self.create('模型生成项目')
        base = '/api/projects/' + slug
        aid = self.client.post(base + '/content',
                               json={'title': '模型生成验证', 'channel': '公众号长文'}).json()['id']
        self.client.post(base + '/content/' + aid + '/sources/note',
                         json={'text': '本系统支持设备台账与点检。', 'label': '产品说明'})
        payload = {'choices': [{'message': {'content': json.dumps({
            'title': '模型写的标题',
            'summary': '模型写的摘要',
            'body': '## 一段\n\n' + '模型写的正文内容。' * 60})}}]}
        with patch.object(generation.request, 'urlopen', return_value=_Response(payload)):
            out = self.client.post(base + '/content/' + aid + '/generate',
                                   json={'revision': 1, 'channel': '公众号长文'})
        self.assertEqual(out.status_code, 200, out.text)
        self.assertEqual(out.json()['engine'], 'openai-compatible')
        asset = next(x for x in self.client.get(base + '/editorial/content').json()['items']
                     if x['id'] == aid)
        self.assertEqual(asset['title'], '模型写的标题')
        self.assertIn('模型写的正文内容', asset['body'])
        meta = json.loads(asset['generation_meta_json'])
        self.assertEqual(meta['engine'], 'openai-compatible')
        self.assertEqual(meta['model'], 'deepseek-chat')

    def test_material_file_name_never_reaches_the_model_prompt(self):
        """文件名/网址只是输入标识；送进提示词就会被写进正文（实测发生过）。"""
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'})
        slug = self.create('提示词项目')
        base = '/api/projects/' + slug
        aid = self.client.post(base + '/content',
                               json={'title': '提示词验证', 'channel': '公众号长文'}).json()['id']
        self.client.post(base + '/content/' + aid + '/sources/file',
                         files=[('files', ('内部资料-勿外传-v9.md', '正文内容甲乙丙丁。'.encode('utf8'),
                                           'text/markdown'))])
        seen = {}

        def capture(req, timeout=None):
            seen['payload'] = req.data.decode('utf8')
            return _Response({'choices': [{'message': {'content': json.dumps({
                'title': 't', 'summary': 's', 'body': '## 甲\n\n' + '正文。' * 300})}}]})
        with patch.object(generation.request, 'urlopen', side_effect=capture):
            self.assertEqual(self.client.post(
                base + '/content/' + aid + '/generate', json={'revision': 1}).status_code, 200)
        self.assertIn('正文内容甲乙丙丁', seen['payload'])           # 素材正文要送到
        self.assertNotIn('内部资料-勿外传-v9.md', seen['payload'])   # 文件名不能送到
        self.assertIn('素材1', seen['payload'])                     # 用中性编号代替

    def test_model_failure_is_reported_and_does_not_overwrite_the_draft(self):
        self.client.put('/api/content-llm', json={
            'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-chat'})
        slug = self.create('模型失败项目')
        base = '/api/projects/' + slug
        aid = self.client.post(base + '/content',
                               json={'title': '失败验证', 'channel': '公众号长文'}).json()['id']
        before = next(x for x in self.client.get(base + '/editorial/content').json()['items']
                      if x['id'] == aid)['body']
        with patch.object(generation.request, 'urlopen', side_effect=OSError('connection refused')):
            out = self.client.post(base + '/content/' + aid + '/generate', json={'revision': 1})
        self.assertEqual(out.status_code, 409)
        self.assertIn('内容模型请求失败', out.json()['detail'])
        after = next(x for x in self.client.get(base + '/editorial/content').json()['items']
                     if x['id'] == aid)
        self.assertEqual(after['body'], before)      # 失败不动草稿
        self.assertEqual(after['revision'], 1)


if __name__ == '__main__':
    unittest.main()
