// runWithProgress / generationFailure 的行为测试（由 test_frontend_server.py 调用，缺 node 时跳过）。
//
// 为什么需要它：test_frontend_server.py 里的断言全是源码文本匹配，锁不住 promise/timer 行为，
// 已经放过了一次真实 P1（失败提示被 modal 随后的 render() 清掉）。这个脚本直接从
// workspace.js 切出这两个函数求值，对真实行为做断言：假时钟 + 假 setInterval + 假 DOM。
import fs from 'node:fs';

const source = fs.readFileSync(new URL('./workspace.js', import.meta.url), 'utf8');
// 切出进度相关的整块：从 progress() 定义开始（runWithProgress 依赖它），到 head() 之前。
const block = source.slice(source.indexOf("function progress(text) { const el=$('#progress')"),
                          source.indexOf('function head('));
if (!block.includes('function runWithProgress(') || !block.includes('function progress(')) {
  console.error('FAIL  没能从 workspace.js 切出 helper，文件结构变了？');
  process.exit(1);
}

// ---- 假环境 ----------------------------------------------------------------
let now = 1_000_000;
Date.now = () => now;

const elements = new Map();
function makeEl(id) {
  return { id, textContent: '', hidden: true, disabled: false, isConnected: true };
}
for (const id of ['#progress', '#live', '#generate', '#lib-regen']) elements.set(id, makeEl(id));
elements.get('#generate').textContent = '生成初稿';
elements.get('#lib-regen').textContent = '重新生成初稿';

const $ = (sel) => elements.get(sel) ?? null;

let intervalCb = null;
let created = 0, cleared = 0;
const setInterval = (cb) => { intervalCb = cb; created += 1; return 42; };
const clearInterval = () => { cleared += 1; };

const factory = new Function('$', 'setInterval', 'clearInterval',
  block + '\nreturn { runWithProgress, generationFailure, SLOW_TASK_SECONDS };');
const { runWithProgress, generationFailure } = factory($, setInterval, clearInterval);

// ---- 断言 ------------------------------------------------------------------
let failures = 0;
const check = (label, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${ok ? '' : '  <- ' + detail}`);
  if (!ok) failures += 1;
};
const progressText = () => elements.get('#progress').textContent;
const button = () => elements.get('#generate');

// 1) 点击后同步立刻有反馈，且不写 aria-live 的 #notice
let release;
const gate = new Promise((r) => { release = r; });
const task = runWithProgress('#generate', '正在生成初稿', () => gate);
check('点击后立即（同步）出现进度文本', /已用 0 秒/.test(progressText()), progressText());
check('进度含「请不要重复点击」', progressText().includes('请不要重复点击'), progressText());
check('进度含「期间可以切到别的页面」', progressText().includes('期间可以切到别的页面'), progressText());
check('按钮禁用且显示「生成中…」', button().disabled === true && button().textContent === '生成中…',
  `${button().disabled} / ${button().textContent}`);
check('开始只播报一次到 #live', elements.get('#live').textContent.includes('已开始'),
  elements.get('#live').textContent);

// 2) 计时器推进
now += 43_000; intervalCb();
check('43 秒显示真实已用秒数', /已用 43 秒/.test(progressText()), progressText());
check('43 秒不误报「比平时慢」', !progressText().includes('比平时慢'), progressText());

now += 200_000; intervalCb();
check('超 180 秒改措辞', progressText().includes('比平时慢一些'), progressText());
check('慢时仍要求不要重复提交', progressText().includes('不要重复提交'), progressText());

// 3) 任务途中 DOM 被重建（OCR 轮询会 render()）后，新按钮仍必须是禁用「生成中…」
const rebuilt = makeEl('#generate');
rebuilt.textContent = '生成初稿';
elements.set('#generate', rebuilt);
intervalCb();
check('render() 重建后新按钮被重新置为生成中', rebuilt.disabled === true && rebuilt.textContent === '生成中…',
  `${rebuilt.disabled} / ${rebuilt.textContent}`);

// 4) 成功收尾：清表、清进度、按钮复原
release({ ok: true });
const out = await task;
check('任务返回值透传', out && out.ok === true, JSON.stringify(out));
check('计时器已清除', cleared === created, `${cleared}/${created}`);
check('进度条已收起', elements.get('#progress').hidden === true, String(elements.get('#progress').hidden));
check('按钮恢复可点', rebuilt.disabled === false, String(rebuilt.disabled));
check('按钮文案复原', rebuilt.textContent === '生成初稿', rebuilt.textContent);

// 5) 失败路径同样要收尾，并把错误抛给调用方
let caught = null;
await runWithProgress('#generate', '正在生成初稿', () => Promise.reject(new Error('boom')))
  .catch((e) => { caught = e; });
check('失败时错误照常抛出', caught && caught.message === 'boom', String(caught));
check('失败时按钮也复原', button().disabled === false && button().textContent === '生成初稿',
  `${button().disabled} / ${button().textContent}`);
check('失败时计时器也清除', cleared === created, `${cleared}/${created}`);
check('失败时进度条也收起', elements.get('#progress').hidden === true);

// 6) 措辞分流：超时 ≠ 真失败
check('超时 ->「不确定是否已生成」',
  /不确定是否已生成/.test(generationFailure(new Error('请求超时，请刷新核对是否已保存，避免重复提交'))));
check('真失败 ->「未生成：…」',
  generationFailure(new Error('事实依据与来源不能为空')).startsWith('未生成：'));

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
