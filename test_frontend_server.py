import shutil
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from http.server import ThreadingHTTPServer

from frontend_server import Handler


class FrontendAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def assert_not_found(self, method, path):
        request = urllib.request.Request(self.base + path, method=method)
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(context.exception.code, 404)

    def test_service_root_serves_workspace(self):
        response = urllib.request.urlopen(self.base + '/', timeout=3)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.url.endswith('/'))
        self.assertIn(b'<title>', response.read())

    def test_service_root_supports_head(self):
        request = urllib.request.Request(self.base + '/', method='HEAD')
        response = urllib.request.urlopen(request, timeout=3)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers.get('Content-Length'))

    def test_encoded_parent_path_cannot_escape_fonts_on_get(self):
        self.assert_not_found('GET', '/fonts/%2e%2e/README.md')

    def test_encoded_parent_path_cannot_escape_fonts_on_head(self):
        self.assert_not_found('HEAD', '/fonts/%2e%2e/workspace.js')

    def test_encoded_backslash_is_not_a_path_separator_bypass(self):
        self.assert_not_found('GET', '/fonts/%2e%2e%5cREADME.md')

    def test_question_generation_uses_extended_timeout_and_blocks_duplicates(self):
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        self.assertIn('const QUESTION_SUGGESTION_TIMEOUT = 150000;', source)
        self.assertIn("api(base+'/question-suggestions','POST',{},QUESTION_SUGGESTION_TIMEOUT)", source)
        self.assertIn("button.disabled=true", source)

    def test_content_generation_uses_extended_timeout(self):
        """回归：生成初稿走的是 api() 的 20 秒默认超时，而模型要 140 秒以上。

        真实事故：点击「生成初稿」20 秒后 AbortController 触发，界面弹
        「请求超时，请刷新核对是否已保存，避免重复提交」，而请求还在服务端跑。
        """
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        self.assertIn('const CONTENT_GENERATION_TIMEOUT = 320000;', source)
        self.assertIn('target_length:p.target_length},CONTENT_GENERATION_TIMEOUT)', source)
        self.assertIn('confirm_overwrite:true},CONTENT_GENERATION_TIMEOUT)', source)
        # 修的是「这两处要显式传长超时」，不是把 api() 的默认值放大
        self.assertIn("async function api(path, method='GET', body, timeout=20000)", source)

    def test_generate_shows_progress_while_it_runs(self):
        """回归：点「生成初稿」后约 140 秒内界面没有任何反馈，用户不知道是否已启动。

        要求：过程用页面内进度条（真实已用秒数），不用弹窗——<dialog> 会阻断整页交互。
        """
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        self.assertIn('function runWithProgress(', source)
        self.assertIn('setInterval(paint,1000)', source)
        self.assertIn('clearInterval(timer)', source)
        # 两处生成必须共用同一个范式，不能再各写一份
        self.assertEqual(source.count('runWithProgress('), 3, '应为 1 处定义 + 2 处接线')
        # 进度条要回答「是否已启动」「能不能走开」「好了没」
        self.assertIn('请不要重复点击', source)
        self.assertIn('期间可以切到别的页面', source)
        self.assertIn('比平时慢一些', source)

    def test_progress_does_not_write_to_the_aria_live_notice(self):
        """
        审查发现：进度原先写 #notice，而它是 aria-live 区——
        每秒改写会让读屏持续播报，且会把别处的提示（保存成功等）在 1 秒内冲掉。
        进度必须走独立的 #progress，起止各播报一次走 sr-only 的 #live。
        """
        root = Path(__file__).parent
        source = (root / 'workspace.js').read_text(encoding='utf-8')
        html = (root / 'workspace.html').read_text(encoding='utf-8')
        self.assertIn('<div id="progress" hidden></div>', html)
        self.assertIn("function progress(text) { const el=$('#progress')", source)
        self.assertIn("$('#live').textContent=`${label}已开始", source)
        # paint() 里不得再出现 notice(
        start = source.index('function runWithProgress(')
        paint = source[start:source.index('function generationFailure(')]
        self.assertNotIn('notice(', paint, '进度不得写 aria-live 的 #notice')

    def test_regenerate_closes_the_dialog_on_both_paths(self):
        """回归：重新生成原先在 <dialog> 里跑 140 秒，只看到确认按钮变灰。

        内容页的「重新生成」和内容库的「重新生成」是两条路径，都要先关弹窗，
        否则 dialog 令整页 inert，用户依旧看不到进度条。
        """
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        # 内容页：「确认重新生成」的 submit 必须先关弹窗再跑
        gen = source[source.index("$('#generate').onclick"):]
        gen = gen[:gen.index('\n')]
        self.assertIn("()=>{$('#modal').close();return runGenerate(true);}", gen,
                      '内容页的重新生成没有先关弹窗，进度条会被 dialog 挡住')
        # 内容库：同理（这个块自己是提交函数，直接看开头）
        lib = source[source.index("$('#lib-regen').onclick"):][:1200]
        self.assertIn("$('#modal').close();", lib,
                      '内容库的重新生成没有先关弹窗')

    def test_generation_failure_survives_the_post_submit_render(self):
        """审查发现的 P1：失败提示被 modal 随后触发的 render() 清掉，界面完全静默。

        机制：modal() 是 `await submit(); d.close(); await render(); notice(result.message)`，
        而 render() 第一句就是 notice('')。所以失败信息必须经 result.message 发出，
        不能只在 catch 里写一次 notice。
        """
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        # 内容库：只 return message，不能只 notice
        start = source.index("$('#lib-regen').onclick")
        lib = source[start:start + 1500]
        self.assertIn('return {message:generationFailure(err)};', lib)
        # 内容页：直连路径靠 notice，弹窗路径靠 message，两者都要
        self.assertIn('catch(err){const m=generationFailure(err);notice(m);return {message:m};}', source)
        # 锁住 render() 会清掉提示条这个前提；一旦改变，上面的约定要重新评估
        self.assertIn("const {view,params}=route();notice('');", source)

    def test_workspace_progress_behaviour(self):
        """行为级回归（不只是源码文本断言）。

        审查结论：文本断言锁不住 promise/timer 行为，已经放过了一次真实 P1。
        这个用例把 test_workspace_progress.mjs 固化进仓，直接对
        runWithProgress / generationFailure 的真实行为做断言。
        没有 node 时跳过，不把 node 变成硬依赖。
        """
        node = shutil.which('node')
        if not node:
            self.skipTest('本机没有 node，跳过 JS 行为测试')
        script = Path(__file__).with_name('test_workspace_progress.mjs')
        done = subprocess.run([node, str(script)], capture_output=True, encoding='utf-8',
                              errors='replace', cwd=str(Path(__file__).parent), timeout=60)
        self.assertEqual(done.returncode, 0, (done.stdout or '') + (done.stderr or ''))
        self.assertIn('全部通过', done.stdout)

    def test_timeout_and_real_failure_are_worded_differently(self):
        """超时意味着「可能已生成」，不能和「未生成」用同一句话让人直接重试。"""
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        self.assertIn('function generationFailure(', source)
        self.assertIn('不确定是否已生成', source)
        self.assertIn('未生成：', source)

    def test_run_actions_and_navigation_use_approved_labels(self):
        root = Path(__file__).parent
        html = (root / 'workspace.html').read_text(encoding='utf-8')
        source = (root / 'workspace.js').read_text(encoding='utf-8')
        self.assertIn('<a href="#actions">优化清单</a>', html)
        self.assertIn("actions:'优化清单'", source)
        self.assertIn("runs:'诊断执行'", source)
        self.assertNotIn('执行与结果', source)
        self.assertIn('查看诊断报告', source)
        self.assertIn("['archive','终止诊断','danger']", source)
        self.assertNotIn('关闭诊断浏览器', source)
        self.assertNotIn("'close-browser'", source)

    def test_actions_are_grouped_by_batch_and_collapsed_by_default(self):
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        # 优化清单必须按诊断批次折叠：批次清单来自接口的 batches，且默认不写 open（全部收起）
        self.assertIn("batches=d.batches||[]", source)
        self.assertIn('<details class="batch" data-batch=', source)
        self.assertNotIn('<details class="batch" open', source)
        self.assertIn('toggle-batches', source)
        # 批次标签精确到秒：分钟级标签在同一分钟内跑完两批时会重名，选批次就分不清了
        self.assertIn('localTimeSec(b.created_at)', source)
        # 任务完成后徽章要有中文，不能直接漏出英文 done
        self.assertIn("done:'已完成'", source)


if __name__ == '__main__':
    unittest.main()
