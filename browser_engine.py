"""Serial, isolated adapter for the installed browser diagnosis engine.
No legacy database/config/profile is used. Only pending tasks may be submitted.
"""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
import workspace_store as store
from diagnosis_runs import verify_frozen_run, get_run
from process_guard import ProcessGuard
from browser_paths import chrome_executable

def _default_scripts():
    """引擎脚本位置：优先本仓库内的 skill/geo-diagnosis-single/scripts，
    其次本机已安装的 ~/.agents/skills/geo-diagnosis-single/scripts。

    开发机上两者是同一份（home 那个是指向仓库的目录联接），外部分发时只用仓库内这份。
    """
    in_repo = store.ROOT / 'skill' / 'geo-diagnosis-single' / 'scripts'
    if (in_repo / 'geo_driver.py').is_file():
        return in_repo
    return Path.home() / '.agents' / 'skills' / 'geo-diagnosis-single' / 'scripts'


SCRIPTS = Path(os.environ.get('GEO_REDESIGN_ENGINE') or _default_scripts())
_LOCK = threading.Lock()
_ACTIVE = None


def environment():
    env = os.environ.copy()
    env.update(GEO_BROWSER_USER_DATA=str(store.DATA/'browser-profile'),
               GEO_BROWSER_PROFILE_DIR='Default', GEO_CDP_PORT='9348', PYTHONIOENCODING='utf-8',
               GEO_REDESIGN_ENGINE=str(SCRIPTS), GEO_REDESIGN_DATA=str(store.DATA))
    chrome = chrome_executable()
    if chrome:
        env['GEO_CHROME_PATH'] = str(chrome)
    return env


def dependencies():
    missing = []
    for f in ['geo_driver.py','geo_run.py','render-report.py','validate-run.ps1','update-task-state.ps1']:
        if not (SCRIPTS/f).is_file(): missing.append(f)
    if importlib.util.find_spec('playwright') is None: missing.append('Playwright Python')
    if chrome_executable() is None:
        missing.append('Chrome (set GEO_CHROME_PATH or install Chrome/Chromium)')
    return missing


def read_json(path, fallback):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError): return fallback


def rows(path):
    try:
        return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    except (OSError, ValueError): return []


def status(slug, run_id):
    row = get_run(slug, run_id)
    path = verify_frozen_run(slug, run_id)
    config = json.loads(row.pop('config_json'))
    row.pop('run_path', None)
    prompts = {q['id']:q['prompt'] for q in config['scope']['questions']}
    row['tasks'] = [{**t,'prompt':prompts.get(t.get('question_id'),'')} for t in rows(path/'tasks.jsonl')]
    waiting = [t['task_id'] for t in row['tasks'] if t.get('status') in {'running','manual_required'}]
    # The engine signals human verification with PAUSED.md; resume.flag is cleared after use.
    paused_file = path/'PAUSED.md'
    row['paused'] = paused_file.exists() and bool(waiting)
    row['waiting_tasks'] = waiting if row['paused'] else []
    row['pause_note'] = paused_file.read_text(encoding='utf-8',errors='replace')[-600:].strip() if row['paused'] else ''
    row['state'] = read_json(path/'state.json', {})
    row['observations'] = rows(path/'observations.jsonl')
    row['preflight'] = read_json(path/'preflight.json', [])
    return row


def transition(slug, run_id, state, blocker=None):
    with store.connection() as c:
        c.execute('UPDATE execution_runs SET status=?,blocker=?,updated_at=? WHERE id=? AND project_slug=?',
                  (state,blocker,store.now(),run_id,slug))
        store.record_event(c,slug,'diagnosis_'+state,blocker or {'ready':'环境检查通过','running':'诊断执行中','completed':'诊断已完成','degraded':'报告已生成（证据不完整）','stopped':'诊断已停止'}.get(state,state))


def _command(script, path, args=()):
    return [sys.executable, str(SCRIPTS/script), str(path), *args]


def profile_dir():
    return store.DATA / 'browser-profile'


def profile_processes():
    """PIDs of Chrome instances using only our isolated profile, never the user's own browser."""
    if os.name != 'nt':
        return []
    needle = str(profile_dir()).replace("'", "''")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
              "Select-Object -ExpandProperty ProcessId")
    try:
        result = subprocess.run(['powershell.exe','-NoProfile','-Command',script],
                                capture_output=True,text=True,timeout=60)
        return [int(x) for x in result.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


def failure_reason(path, fallback):
    """Translate the engine log tail into one actionable Chinese sentence."""
    try:
        text = (path/'logs/engine.log').read_text(encoding='utf-8',errors='replace')[-6000:]
    except OSError:
        return fallback
    if 'ProcessSingleton' in text or 'profile is already in use' in text:
        return '诊断浏览器窗口已在运行，请先点击“关闭浏览器窗口”再重试'
    if 'ERR_CONNECTION' in text.upper() or 'ERR_NAME_NOT_RESOLVED' in text.upper():
        return '无法访问平台网站，请检查网络后重试'
    if 'Timeout' in text or 'timeout' in text:
        return '浏览器响应超时，请检查平台页面状态后重试'
    if 'Executable doesn' in text:
        return '找不到 Chrome 可执行文件，请确认已安装 Chrome'
    return fallback


def preflight_reason(path):
    """Turn preflight.json into an actionable message that names the real cause."""
    checks = read_json(path/'preflight.json', [])
    if not checks:
        return '环境检查没有产生结果（浏览器可能异常退出），请重试'
    failed = [c for c in checks if c.get('check') != 'OK']
    need_login = [str(c.get('platform')) for c in checks
                  if c.get('check') == 'OK' and c.get('login_state_guess') != 'logged_in']
    parts = []
    if failed:
        labels = '、'.join(str(c.get('platform')) for c in failed)
        errors = ' '.join(str(c.get('error','')) for c in failed)
        if 'Timeout' in errors or 'timeout' in errors:
            parts.append(f'{labels} 页面加载或截图超时（常见原因是网络不稳定、平台限流或登录页卡住）')
        elif 'ProcessSingleton' in errors or 'profile is already in use' in errors:
            parts.append(f'{labels} 浏览器 profile 被占用，请稍后重试')
        else:
            parts.append(f"{labels} 无法打开：{str(failed[0].get('error',''))[:120]}")
    if need_login:
        parts.append('待登录：' + '、'.join(need_login))
    if not parts:
        return '环境检查未通过，请重试'
    return '；'.join(parts) + '。处理后可重新点击“检查环境”'


def incomplete_tasks(path):
    """Tasks whose evidence chain is incomplete; never silently treated as fine."""
    problems = []
    observed = {}
    for o in rows(path/'observations.jsonl'):
        observed.setdefault(o.get('task_id'), o)
    for t in rows(path/'tasks.jsonl'):
        if t.get('status') not in {'success','failed'}:
            continue
        o = observed.get(t['task_id'])
        if not o:
            problems.append(f"{t['task_id']}（没有观测记录）")
            continue
        files = [f for f in (o.get('evidence_files') or []) if f]
        if not files:
            problems.append(f"{t['task_id']}（没有证据文件）")
            continue
        missing = [f for f in files if not (path/f).is_file()]
        if missing:
            problems.append(f"{t['task_id']}（缺 {missing[0]}）")
    return problems


def text_check_failures(path):
    """报告文案体检：命中「不属于本主体」的行业词时返回词条列表（空列表=通过）。

    render-report.py 无论通过与否都会写 report/TEXT_CHECK.md；这里只读结论。
    缺这个文件视为「没跑过体检」（旧批次），返回空列表，不让历史批次全部变 degraded。
    """
    file = Path(path)/'report'/'TEXT_CHECK.md'
    if not file.is_file():
        return []
    text = file.read_text(encoding='utf-8-sig', errors='replace')
    if '状态：FAIL' not in text:
        return []
    hits = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 3 or cells[0] in {'文件'} or set(cells[1]) <= set('-: '):
            continue
        hits.append(f"{cells[1]}（{cells[0]}）")
    if not hits:
        # 标了 FAIL 却读不到词条：宁可当成有问题，也不要静默放过。
        return ['（状态：FAIL，但未解析到具体词条，请人工查看 report/TEXT_CHECK.md）']
    return sorted(dict.fromkeys(hits))


def close_profile_processes():
    """Terminate leftover Chrome instances bound to our isolated profile."""
    pids = profile_processes()
    for pid in pids:
        subprocess.run(['taskkill','/PID',str(pid),'/T','/F'],capture_output=True,timeout=30)
    return len(pids)


def close_browser(slug, run_id, confirmed):
    if confirmed is not True:
        raise ValueError('请确认关闭诊断浏览器窗口')
    with _LOCK:
        if _ACTIVE is not None:
            raise ValueError('诊断任务正在使用浏览器，请先停止任务')
    pids = close_profile_processes()
    with store.connection() as c:
        store.record_event(c,slug,'browser_closed',f'关闭诊断浏览器窗口 {len(pids)} 个')
    return {'closed':len(pids),'message':f'已关闭 {len(pids)} 个诊断浏览器进程' if pids else '诊断浏览器当前未打开'}


def launch(slug, run_id, mode, confirmed=False):
    global _ACTIVE
    if mode not in {'preflight','execute','login','report'}: raise ValueError('不支持的诊断操作')
    with _LOCK:
        if _ACTIVE is not None: raise ValueError('另一个诊断正在使用浏览器，请等待结束')
        probe=ProcessGuard(store.DATA/'browser-execution.lock').acquire()
        probe.release()
        path = verify_frozen_run(slug,run_id)
        row = get_run(slug,run_id)
        if mode == 'report':
            tasks = rows(path/'tasks.jsonl')
            if not tasks or not all(t.get('status') in {'success','failed'} for t in tasks):
                raise ValueError('任务尚未全部结束，不能生成最终报告')
            if row['status'] not in {'interrupted','completed','degraded'}: raise ValueError('当前批次不能重新生成报告')
        elif mode == 'execute':
            if confirmed is not True: raise ValueError('请确认向所选平台提交冻结问题')
            if row['status'] != 'ready': raise ValueError('请先完成环境检查')
            tasks = rows(path/'tasks.jsonl')
            if any(t['status'] in {'running','manual_required'} for t in tasks):
                raise ValueError('存在提交状态不明确的任务，不能重新提交')
            if not any(t['status']=='pending' for t in tasks): raise ValueError('没有待执行任务')
        elif row['status'] not in {'frozen','blocked','ready','interrupted'}:
            raise ValueError('当前批次不能进行环境检查')
        # A window can outlive a crashed job or close slowly. Clean our own leftovers
        # automatically instead of dead-ending the user on a stale profile lock.
        cleaned = 0
        if mode in {'login','preflight'}:
            for attempt in range(3):
                if not profile_processes():
                    break
                if attempt == 0:
                    time.sleep(2)      # grace: the user may have just closed the window
                    continue
                cleaned += close_profile_processes()
                time.sleep(1)
            if profile_processes():
                raise ValueError('无法关闭残留的诊断浏览器进程。请手动结束这些 Chrome 进程后重试；它们只属于诊断专用窗口，不影响你当前浏览的页面')
        missing = dependencies()
        if missing: raise ValueError('缺少运行依赖：'+'、'.join(missing))
        job = dict(slug=slug,run_id=run_id,path=path,mode=mode,process=None,stopped=False)
        _ACTIVE = job
        transition(slug,run_id,'login' if mode=='login' else 'preflight' if mode=='preflight' else 'running')
        threading.Thread(target=_worker,args=(job,),daemon=True).start()
    base = '正在检查环境' if mode == 'preflight' else '诊断已开始'
    if cleaned:
        base += f'（已自动清理 {cleaned} 个残留诊断浏览器进程）'
    return {'message': base}


def _run(job, command, timeout):
    path = job['path']
    with (path/'logs/engine.log').open('a',encoding='utf8') as log:
        with _LOCK:
            if job['stopped']: return -1
            if command[0] == sys.executable:
                command=[sys.executable,str(store.ROOT/'engine_worker.py'),*command[1:]]
            process = subprocess.Popen(command,env=environment(),cwd=SCRIPTS,stdout=log,stderr=log,stdin=subprocess.DEVNULL)
            job['process'] = process
        try: return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise RuntimeError('执行超时，请检查平台状态')


def _terminate(process):
    if process.poll() is not None: return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=30)
    else:
        process.terminate()
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill()


def _worker(job):
    global _ACTIVE
    slug, rid, path = job['slug'],job['run_id'],job['path']
    try:
        if job['mode']=='login':
            for name in ['login-open.flag','login-done.flag']:
                (path/name).unlink(missing_ok=True)
            code = _run(job,[sys.executable,str(store.ROOT/'browser_login.py'),str(path)],1900)
            if not job['stopped']:
                if code == 0:
                    transition(slug,rid,'frozen','已退出登录窗口，现在可以点击“检查环境”')
                else:
                    transition(slug,rid,'blocked',failure_reason(path,'登录窗口异常退出，请重新打开'))
        elif job['mode']=='preflight':
            old = path/'preflight.json'
            if old.exists(): old.unlink()  # Do not reuse stale readiness.
            command = [sys.executable,str(SCRIPTS/'geo_driver.py'),'preflight',str(path),'all']
            code = _run(job,command,400)
            checks = read_json(old,[])
            expected = {p['id'] for p in read_json(path/'frozen-config.json',{})['platforms']}
            ready = code == 0 and {p.get('platform') for p in checks} == expected and all(p.get('check')=='OK' and p.get('login_state_guess')=='logged_in' for p in checks)
            if not job['stopped']:
                transition(slug,rid,'ready' if ready else 'blocked', None if ready else preflight_reason(path))
        else:
            code = 0 if job['mode']=='report' else _run(job,_command('geo_run.py',path),14400)
            tasks = rows(path/'tasks.jsonl')
            terminal = tasks and all(t.get('status') in {'success','failed'} for t in tasks)
            if job['stopped']: return
            if code != 0 or not terminal:
                transition(slug,rid,'interrupted',failure_reason(path,'执行中断。请先核对已提交任务和平台状态，不要重复提交问题'))
                return
            if _run(job,_command('render-report.py',path),180) != 0:
                transition(slug,rid,'interrupted',failure_reason(path,'回答已保存，报告生成失败'))
                return
            validation = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(SCRIPTS/'validate-run.ps1'),'-RunDir',str(path)]
            code = _run(job,validation,180)
            if not job['stopped']:
                leaks = text_check_failures(path)
                if code != 0:
                    problems = incomplete_tasks(path)
                    detail = '、'.join(problems[:4]) if problems else '结果校验未通过'
                    transition(slug,rid,'degraded',
                               f'报告已生成，但 {len(problems) or 1} 个任务的证据不完整：{detail}。'
                               '可以查看报告与证据、从完整观测生成优化清单；已提交的问题不得重试')
                elif leaks:
                    transition(slug,rid,'degraded',
                               f'报告已生成，但文案体检发现 {len(leaks)} 处与本次主体无关的行业词：{"、".join(leaks[:5])}。'
                               '请核对 report/TEXT_CHECK.md；确认是模板残留就修渲染脚本后重新生成报告，不要直接对外交付')
                else:
                    transition(slug,rid,'completed')
    except Exception as exc:
        if not job['stopped']: transition(slug,rid,'interrupted','诊断执行异常：'+str(exc)[:250])
    finally:
        # A failed preflight/login can leave Chrome holding the profile; clean our own leftovers.
        if job['mode'] in {'preflight','login'}:
            try: close_profile_processes()
            except Exception: pass
        with _LOCK:
            if _ACTIVE is job: _ACTIVE = None


def stop(slug,run_id):
    with _LOCK:
        if not _ACTIVE or (_ACTIVE['slug'],_ACTIVE['run_id']) != (slug,run_id): raise ValueError('该批次没有正在运行的进程')
        job = _ACTIVE
        job['stopped'] = True
        process = job['process']
    if process: _terminate(process)
    transition(slug,run_id,'interrupted','已停止。已提交的问题不会自动重试')


def archive(slug,run_id,confirmed):
    if confirmed is not True: raise ValueError('请确认终止诊断，尚未执行的任务将不再提交')
    with _LOCK:
        if _ACTIVE and (_ACTIVE['slug'],_ACTIVE['run_id'])==(slug,run_id):
            raise ValueError('请先停止当前执行进程')
        row=get_run(slug,run_id)
        if row['status'] in {'completed','degraded'}: raise ValueError('已完成批次无需终止')
        transition(slug,run_id,'archived','批次已终止，证据保留，不作为完整诊断报告')
    return {'message':'诊断已终止'}


def shutdown():
    with _LOCK:
        job = _ACTIVE
    if job:
        try: stop(job['slug'],job['run_id'])
        except (ValueError,OSError): pass


def resume(slug,run_id):
    with _LOCK:
        if not _ACTIVE or (_ACTIVE['slug'],_ACTIVE['run_id']) != (slug,run_id):
            raise ValueError('执行进程已结束。已完成的任务不会重试，请点击“检查环境”后继续剩余任务')
        path = verify_frozen_run(slug,run_id)
        mode = _ACTIVE['mode']
        if mode == 'login':
            (path/'login-done.flag').write_text(store.now(),encoding='utf8')
            message = '已通知登录窗口完成，请点击“检查环境”核对就绪状态'
        elif mode == 'execute':
            (path/'resume.flag').write_text(store.now(),encoding='utf8')
            message = '已通知执行器继续，本题不会重新提交'
        elif mode == 'preflight':
            raise ValueError('环境检查正在进行，请稍候再操作')
        else:
            raise ValueError('当前没有等待人工处理的步骤')
    return {'message':message}
