# -*- coding: utf-8 -*-
"""GEO 一次性诊断执行器。按 tasks.jsonl 顺序执行，每题每平台只提交一次。
用法:
  python geo_run.py <run_dir> [--limit N] [--question Q01] [--platform deepseek]
"""
import sys, os, json, time, re, subprocess, argparse, threading, hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from geo_driver import open_browser, PLATFORMS, clean_text

PS_UPDATE = os.path.join(SCRIPT_DIR, "update-task-state.ps1")

NEWCHAT_TERMS = {
    "deepseek": ["开启新对话"],
    "doubao": ["新工作任务"],
    "qianwen": ["新建对话", "新对话"],
    "metaso": [],
}
COMPOSER = {
    "deepseek": "textarea",
    "doubao": '[contenteditable="true"]',
    "qianwen": '[contenteditable="true"]',
    "metaso": "textarea",
}


# ---- P1 门禁：回答区提取 + 有效性判定 + 验证弹窗检测 ----
# 目的：空白页 / 验证弹窗 / 思考中途，都不得被记为 success。
MIN_ANSWER_CHARS = int(os.environ.get("GEO_MIN_ANSWER_CHARS", "400"))
# 平台熔断阈值：同一平台连续这么多次拿不到有效回答 → 熔断该平台，跳过其剩余任务，批次继续
MAX_CONSECUTIVE_INVALID = int(os.environ.get("GEO_MAX_CONSECUTIVE_INVALID", "2"))
# 浏览器/连接层异常：单平台重试无意义，属全局阻断（仅此类才停批）
GLOBAL_ERROR_PATTERN = re.compile(
    r"(target closed|browser has been closed|browsertype\.launch|executable doesn't exist|"
    r"err_connection|econnrefused|protocol error|connection closed|context or browser has been closed)",
    re.I)
STABLE_POLLS = 4          # 回答区长度连续不变次数视为生成结束
POLL_SECONDS = 3

# ---- 验证暂停（人机接力）：暂停等人工在窗口内完成，然后继续当前任务 ----
PAUSE_TIMEOUT_S = int(os.environ.get("GEO_PAUSE_TIMEOUT_S", "1800"))
MAX_PAUSES_PER_TASK = int(os.environ.get("GEO_MAX_PAUSES_PER_TASK", "3"))
RESUME_GRACE_S = int(os.environ.get("GEO_RESUME_GRACE_S", "150"))
AUTO_RESUME_POLLS = 3     # 验证特征连续消失次数 → 自动恢复
PAUSE_POLL_SECONDS = 3
RESUME_FLAG = "resume.flag"

# 平台验证弹窗特征（只读可见文本，不读 cookie、不绕过验证）
CAPTCHA_TEXTS = (
    "完成验证", "请拖动下方滑块", "请按住滑块", "拖动到最右", "拖动到右边",
    "请选择所有符合", "拖动到下方", "点击反馈", "安全验证", "人机验证",
)
# 仅在元素 id/class 上匹配，避免误伤正文
CAPTCHA_ATTR_HINTS = ("captcha", "nc_1_n1z", "secsdk-captcha", "captcha_verify")


def page_text(page):
    """整页可见文本；读不到返回空串。"""
    try:
        return clean_text(page.inner_text("body"))
    except Exception:
        return ""


def slice_answer(text, prompt):
    """从整页文本切出回答区：取问题原文之后的内容。
    找不到问题原文时返回空串 —— 宁可判无效，也不把整页 chrome 当回答。"""
    if not text or not prompt:
        return ""
    i = text.find(prompt)
    if i < 0:
        return ""
    return text[i + len(prompt):]


def answer_length(answer_text):
    """回答区去空白后的字符数（不受侧边栏、计时器长度干扰）。"""
    return len("".join((answer_text or "").split()))


def answer_is_valid(answer_text):
    return answer_length(answer_text) >= MIN_ANSWER_CHARS


def should_trip_platform(verdict, streak, threshold=None):
    """平台熔断判定：验证经人工接力仍未通过 → 立即熔断；连续无效达阈值 → 熔断。

    熔断 ≠ 停批：熔断只跳过该平台剩余任务，批次继续跑其他平台。
    拿到有效回答（ok / ok_timeout）永不熔断。
    """
    if verdict in ("ok", "ok_timeout"):
        return False
    threshold = MAX_CONSECUTIVE_INVALID if threshold is None else threshold
    return verdict == "captcha" or streak >= threshold


def is_global_error(err):
    """浏览器/连接层异常 → 全局阻断（仅此类才允许停批）。"""
    return bool(GLOBAL_ERROR_PATTERN.search(str(err or "")))


def detect_captcha(page, body=None):
    """检测平台验证弹窗特征；命中返回特征字符串，未命中返回 None。

    弹窗常渲染在跨域 iframe 内，主文档 inner_text 读不到，因此逐帧扫描。
    只读可见文本与元素 id/class，不读 cookie、不尝试绕过验证。
    注意：检测可能漏判，真正的安全网是回答区长度门禁（漏判也不会误报成功）。
    """
    text = page_text(page) if body is None else body
    for t in CAPTCHA_TEXTS:
        if t in text:
            return t
    # 逐帧扫描（滑块/点选验证多在 iframe 内）
    try:
        main = getattr(page, "main_frame", None)
        for fr in getattr(page, "frames", []) or []:
            if main is not None and fr == main:
                continue
            try:
                frame_text = fr.inner_text("body")
            except Exception:
                continue
            for t in CAPTCHA_TEXTS:
                if t in (frame_text or ""):
                    return f"frame:{t}"
    except Exception:
        pass
    # 主文档中的验证容器元素
    try:
        for hint in CAPTCHA_ATTR_HINTS:
            if page.query_selector(f'[id*="{hint}" i], [class*="{hint}" i]'):
                return f"dom:{hint}"
    except Exception:
        pass
    return None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_hash(obj):
    """稳定内容哈希：用于 observation/citation 的复测对比与去重。"""
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def domain_of(url):
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""


def classify_source_domain(domain, official_domains, competitor_domains=None):
    d = (domain or "").lower()
    official = any(od and od.lower() in d for od in (official_domains or []))
    competitor = any(cd and cd.lower() in d for cd in (competitor_domains or []))
    return official, competitor, bool(d and not official and not competitor)


def write_citations(run_dir, obs, q, brand, official_domains):
    """把回答中的来源链接沉淀为 citations.jsonl。

    observations.jsonl 面向任务诊断；citations.jsonl 面向跨客户/跨行业统计。
    """
    sources = obs.get("sources") or []
    if not sources:
        return 0
    path = os.path.join(run_dir, "citations.jsonl")
    competitor_domains = []
    for item in (brand.get("known_competitors") or []):
        if isinstance(item, dict):
            competitor_domains.extend(item.get("domains") or [])
        elif isinstance(item, str):
            competitor_domains.append(item)
    count = 0
    with open(path, "a", encoding="utf-8") as f:
        for idx, src in enumerate(sources, 1):
            url = src.get("url", "")
            domain = domain_of(url)
            is_official, is_competitor, is_third_party = classify_source_domain(domain, official_domains, competitor_domains)
            base = {
                "task_id": obs.get("task_id"),
                "company_name": brand.get("canonical_name"),
                "question_id": obs.get("question_id"),
                "question_type": obs.get("question_type"),
                "layer": q.get("layer", ""),
                "subcat": q.get("subcat", ""),
                "intent": q.get("intent", ""),
                "time_sensitivity": q.get("time_sensitivity", ""),
                "trigger_intensity": q.get("trigger_intensity", ""),
                "platform_code": obs.get("platform_id"),
                "platform_label": obs.get("platform_label"),
                "quote_url": url,
                "quote_title": src.get("text", ""),
                "site_name": src.get("text", ""),
                "quote_index": idx,
                "published_at": "",
                "domain": domain,
                "snippet": "",
                "is_official_domain": is_official,
                "is_competitor_domain": is_competitor,
                "is_third_party": is_third_party,
            }
            base["citation_id"] = f"cite-{obs.get('task_id')}-{idx:03d}"
            base["record_hash"] = stable_hash({k: v for k, v in base.items() if k not in ("citation_id", "record_hash")})
            f.write(json.dumps(base, ensure_ascii=False) + "\n")
            count += 1
    return count


def enrich_question_fields(target, q):
    """把问题多维标签写入 task/observation，老配置没有这些字段时保持空串。"""
    for key in ("layer", "subcat", "intent", "time_sensitivity", "trigger_intensity"):
        target[key] = q.get(key, "")
    return target


_STDIN_RESUME = {"hit": False, "started": False}


def _start_stdin_watcher():
    """交互式终端下，读一次回车即视为操作人已处理完验证。非 TTY 不启动。"""
    if _STDIN_RESUME["started"]:
        return
    _STDIN_RESUME["started"] = True
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return
    except Exception:
        return

    def _watch():
        try:
            for _ in sys.stdin:
                _STDIN_RESUME["hit"] = True
                return
        except Exception:
            return

    try:
        threading.Thread(target=_watch, daemon=True).start()
    except Exception:
        pass


def human_pause(page, run_dir, task_id, hit, timeout_s=None):
    """遇到平台验证时暂停，等操作人在已打开的浏览器窗口内手动完成，然后继续当前任务。

    返回 True=已恢复（继续等本题回答）；False=等待超时/放弃。

    边界：
    - 不重新提交问题（每题每平台只提交一次）
    - 不读取/导出密码、Cookie、令牌；不代过验证；不改账号安全设置
    - 人工在窗口内完成验证不算「绕过验证」，自动化只负责暂停与继续

    恢复方式（任一即可）：
    1. 操作人完成验证后页面特征消失 → 自动恢复（推荐）
    2. 创建 runs/<run_id>/resume.flag → 显式恢复
    3. 交互式终端按回车 → 显式恢复
    """
    timeout_s = PAUSE_TIMEOUT_S if timeout_s is None else timeout_s
    flag = os.path.join(run_dir, RESUME_FLAG)
    body = (f"任务 {task_id} 遇到平台验证（特征：{hit}）。\n"
            f"请在已打开的浏览器窗口中手动完成验证。\n"
            f"完成后无需其他操作，页面特征消失会自动继续；\n"
            f"若未自动继续，可创建文件 {flag}，或在交互终端按回车。\n"
            f"系统不会重新提交问题；等待上限 {max(1, timeout_s // 60)} 分钟。\n")
    try:
        with open(os.path.join(run_dir, "PAUSED.md"), "a", encoding="utf-8") as f:
            f.write(f"\n## {now_iso()} · {task_id}\n\n{body}\n")
    except Exception:
        pass
    print("[pause] " + body.replace("\n", " "), flush=True)

    try:
        if os.path.exists(flag):
            os.remove(flag)
    except Exception:
        pass
    _STDIN_RESUME["hit"] = False
    _start_stdin_watcher()

    gone = 0
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _STDIN_RESUME["hit"]:
            print(f"[pause] {task_id} 收到终端确认，继续当前任务", flush=True)
            return True
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except Exception:
                pass
            print(f"[pause] {task_id} 收到 {RESUME_FLAG}，继续当前任务", flush=True)
            return True
        if detect_captcha(page, page_text(page)) is None:
            gone += 1
            if gone >= AUTO_RESUME_POLLS:
                print(f"[pause] {task_id} 页面验证特征已消失，继续当前任务", flush=True)
                return True
        else:
            gone = 0
        time.sleep(PAUSE_POLL_SECONDS)
    print(f"[pause] {task_id} 等待人工处理超时（{timeout_s}s），转 manual_required", flush=True)
    return False


def ps_update(run_dir, task_id, status, obs_ref="", reason=""):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            PS_UPDATE, "-RunDir", run_dir, "-TaskId", task_id, "-Status", status]
    if obs_ref:
        args += ["-ObservationRef", obs_ref]
    if reason:
        args += ["-FailureReason", reason]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=creationflags)
    if r.returncode != 0:
        raise RuntimeError(f"state update failed for {task_id}->{status}: {r.stderr[:300]}")


def click_text(page, terms):
    for t in terms:
        try:
            loc = page.get_by_text(t, exact=False)
            n = loc.count()
            if n > 0:
                loc.first.click(timeout=5000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def submit_once(page, pid, text):
    sel = COMPOSER[pid]
    box = page.query_selector(sel)
    if box is None:
        raise RuntimeError(f"找不到输入框: {sel}")
    box.click()
    page.wait_for_timeout(800)
    # 清空已有内容
    if sel == "textarea":
        box.fill("")
    else:
        box.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    if sel == "textarea":
        box.fill(text)
    else:
        page.keyboard.type(text, delay=10)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")


def wait_complete(page, prompt, timeout_s=170, on_captcha=None,
                  max_pauses=MAX_PAUSES_PER_TASK, resume_grace_s=RESUME_GRACE_S):
    """轮询【回答区】长度直到稳定（不再用整页 body 长度）。

    返回 (verdict, answer_text, captcha_hit)：
      "ok"          回答区稳定且达到最小长度
      "ok_timeout"  超时，但回答区已达到最小长度（未捕获停止信号，需人工确认）
      "captcha"     验证特征无法通过人工接力恢复（超时/放弃/超过暂停次数）
      "empty"       超时且回答区始终未达标

    on_captcha: 收到验证特征且回答区未达标时调用，参数为特征字符串。
                返回 True = 人工已处理，继续等本题回答；False = 放弃。
                None = 不暂停（直接判 captcha）。
    """
    prev = -1
    stable = 0
    last_answer = ""
    captcha_hit = None
    pauses = 0
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = page_text(page)
        last_answer = slice_answer(body, prompt)
        cur = answer_length(last_answer)

        hit = detect_captcha(page, body)
        if hit:
            if not answer_is_valid(last_answer):
                if on_captcha is None or pauses >= max_pauses:
                    return "captcha", last_answer, hit
                pauses += 1
                if not on_captcha(hit):
                    return "captcha", last_answer, hit
                # 人工已处理：继续等本题回答（不重新提交问题），放宽截止时间
                captcha_hit = captcha_hit or hit
                stable = 0
                prev = -1
                deadline = time.time() + resume_grace_s
                continue
            if captcha_hit is None:
                # 只在首次检测到遮挡时重新计稳定；后续残留痕迹不再重置
                captcha_hit = hit
                stable = 0

        if cur == prev and cur >= MIN_ANSWER_CHARS:
            stable += 1
        else:
            stable = 0
        if stable >= STABLE_POLLS:
            return "ok", last_answer, captcha_hit

        prev = cur
        time.sleep(POLL_SECONDS)

    if answer_is_valid(last_answer):
        return "ok_timeout", last_answer, captcha_hit
    return "empty", last_answer, captcha_hit


def extract_sources(page):
    """只取回答区的可见来源链接：排除导航/侧边栏/历史列表。"""
    js = """() => {
      const SKIP = 'nav,aside,header,footer,[class*="sidebar" i],[class*="history" i]';
      const out = [];
      for (const a of document.querySelectorAll('a[href]')) {
        if (a.closest(SKIP)) continue;
        const href = (a.getAttribute('href') || '').trim();
        if (!href.startsWith('http')) continue;
        out.push({url: href, text: (a.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120)});
      }
      return out;
    }"""
    try:
        items = page.evaluate(js)
    except Exception:
        return []
    seen = []
    for it in items or []:
        url = (it or {}).get("url")
        if not url or url in [s["url"] for s in seen]:
            continue
        seen.append({"url": url, "text": (it or {}).get("text", "")})
    return seen[:40]


def classify(brand_terms, official_domains, response_text, sources, question_type):
    resp = response_text or ""
    mention = "no"
    hit_term = None
    for t in brand_terms:
        if t and t in resp:
            mention = "yes"
            hit_term = t
            break
    if mention == "yes":
        idx = resp.find(hit_term) if hit_term else -1
        win = resp[max(0, idx - 80):idx + 160] if idx >= 0 else resp
        if re.search(r"(推荐|建议选择|优先|首选|值得选择|推荐使用)", win):
            strength = "explicit"
        elif re.search(r"(备选|作为案例|可了解|参考|之一|例如|比如)", win):
            strength = "weak"
        else:
            strength = "mention_only"
    else:
        strength = "none"
    accuracy = "unverified"
    cited = "not_shown"
    if sources:
        domains = set()
        for s in sources:
            m = re.search(r"https?://([^/]+)", s["url"])
            if m:
                domains.add(m.group(1).lower())
        hits = [d for d in domains if any(od in d for od in official_domains)]
        cited = "yes" if hits else "no"
    return {"brand_mention": mention, "recommend_strength": strength,
            "description_accuracy": accuracy, "source_citation": cited}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--question", default="")
    ap.add_argument("--platform", default="")
    a = ap.parse_args()
    run_dir = os.path.abspath(a.run_dir)

    cfg = json.load(open(os.path.join(run_dir, "frozen-config.json"), encoding="utf-8"))
    brand = cfg.get("brand", {})
    brand_terms = [brand["canonical_name"]] + list(brand["aliases"])
    brand_terms = [t for t in brand_terms if t]
    official_domains = list(brand.get("official_domains") or [])
    qmap = {q["id"]: q for q in cfg["scope"]["questions"]}
    pmap = {p["id"]: p for p in cfg["platforms"]}

    tasks = []
    for line in open(os.path.join(run_dir, "tasks.jsonl"), encoding="utf-8-sig"):
        line = line.strip()
        if line:
            tasks.append(json.loads(line))

    pending = [t for t in tasks if t["status"] == "pending"]
    if a.question:
        pending = [t for t in pending if t["question_id"] == a.question]
    if a.platform:
        pending = [t for t in pending if t["platform_id"] == a.platform]
    if a.limit:
        pending = pending[:a.limit]

    print(f"[run] {run_dir} 待执行 {len(pending)} 个任务", flush=True)

    pw, ctx = open_browser()
    pages = {}  # platform -> page
    obs_path = os.path.join(run_dir, "observations.jsonl")

    def get_page(pid):
        if pid not in pages:
            pages[pid] = ctx.new_page()
        return pages[pid]

    stop_batch = False
    invalid_streak = {}
    blocked_platforms = {}
    finalized = set()

    def fail_remaining(pid, reason):
        """平台熔断：把该平台剩余 pending 任务统一记为 failed（未提交），并补观察与证据。

        这是「单平台失败不阻塞整批」的核心：熔断后批次继续跑其他平台。
        被跳过的任务也会写 observation 与一个说明文件，满足“每个任务都有证据”的校验。
        """
        done = []
        for t2 in pending:
            tid2 = t2["task_id"]
            if t2["platform_id"] != pid or tid2 in finalized:
                continue
            q2 = qmap[t2["question_id"]]
            ev_name = f"evidence/{tid2}_skipped.txt"
            try:
                with open(os.path.join(run_dir, ev_name), "w", encoding="utf-8") as f:
                    f.write(f"未提交（平台熔断）\n任务：{tid2}\n平台：{pmap[pid]['label']}\n"
                            f"题目：{q2['prompt']}\n原因：{reason}\n时间：{now_iso()}\n")
            except Exception:
                pass
            rec = {"task_id": tid2, "question_id": t2["question_id"], "question_type": q2["type"],
                   "platform_id": pid, "platform_label": pmap[pid]["label"],
                   "attempted_at": now_iso(), "input_prompt": q2["prompt"],
                   "status": "failed",
                   "environment": {"entry_url": pmap[pid]["entry_url"],
                                   "model_or_mode": pmap[pid]["required_mode"],
                                   "network_state": "on", "account_state": "unknown"},
                   "response_text": "", "sources": [], "evidence_files": [ev_name],
                   "classification": {}, "notes": "未提交：平台已熔断，按熔断跳过。",
                   "failure_reason": reason}
            enrich_question_fields(rec, q2)
            rec["record_hash"] = stable_hash({k: v for k, v in rec.items() if k != "record_hash"})
            try:
                with open(obs_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ps_update(run_dir, tid2, "running")   # 状态机要求 pending → running → failed
                ps_update(run_dir, tid2, "failed", obs_ref=tid2, reason=reason[:200])
            except Exception as e:
                print(f"[warn] {tid2} 熔断标记异常（继续）: {str(e)[:160]}", flush=True)
            finalized.add(tid2)
            done.append(tid2)
        return done

    for t in pending:
        tid = t["task_id"]; pid = t["platform_id"]; qid = t["question_id"]
        if tid in finalized:
            continue          # 已终态（含被平台熔断跳过的）
        q = qmap[qid]; p = pmap[pid]
        print(f"[task] {tid} 开始", flush=True)
        try:
            ps_update(run_dir, tid, "running")
        except Exception as e:
            print(f"[warn] {tid} running 状态更新异常（继续）: {str(e)[:200]}", flush=True)
        obs = {"task_id": tid, "question_id": qid, "question_type": q["type"],
               "platform_id": pid, "platform_label": p["label"],
               "attempted_at": now_iso(), "input_prompt": q["prompt"],
               "status": "success", "environment": {
                   "entry_url": p["entry_url"], "model_or_mode": p["required_mode"],
                   "network_state": "on", "account_state": "logged_in"},
               "response_text": "", "sources": [], "evidence_files": [],
               "classification": {}, "notes": "", "failure_reason": None}
        enrich_question_fields(obs, q)
        raw_file = os.path.join(run_dir, "raw", f"{tid}.txt")
        shot1 = f"evidence/{tid}_top.png"
        shot2 = f"evidence/{tid}_full.png"
        try:
            page = get_page(pid)
            page.goto(p["entry_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if NEWCHAT_TERMS.get(pid, []):
                click_text(page, NEWCHAT_TERMS.get(pid, []))
                page.wait_for_timeout(1200)
            submit_once(page, pid, q["prompt"])
            page.wait_for_timeout(3000)

            verdict, answer_text, captcha_hit = wait_complete(
                page, q["prompt"],
                on_captcha=lambda h: human_pause(page, run_dir, tid, h))
            obs["response_text"] = clean_text(answer_text)
            obs["answer_region_chars"] = answer_length(answer_text)

            # 截图：问题+回答
            try:
                page.screenshot(path=os.path.join(run_dir, "evidence", f"{tid}_top.png"), full_page=False)
            except Exception:
                pass
            try:
                page.screenshot(path=os.path.join(run_dir, "evidence", f"{tid}_full.png"), full_page=True)
            except Exception:
                pass
            obs["evidence_files"] = [shot1, shot2]

            notes = []
            if captcha_hit:
                if verdict == "captcha":
                    notes.append(f"检测到平台验证特征：{captcha_hit}；人工未能及时完成。")
                else:
                    notes.append(f"检测到平台验证特征：{captcha_hit}；已由人工处理并继续当前任务。")
            if verdict == "ok_timeout":
                notes.append("回答区达到最小长度但未捕获停止信号，可能仍在生成，需人工确认完整性。")
            obs["notes"] = " ".join(notes)

            if verdict in ("captcha", "empty"):
                # 回答区未达标：不得记为成功
                invalid_streak[pid] = invalid_streak.get(pid, 0) + 1
                if verdict == "captcha":
                    reason = (f"平台验证经人工接力仍未能恢复，回答未捕获"
                              f"（特征：{captcha_hit}）")
                else:
                    reason = (f"等待回答超时，回答区未达到最小长度"
                              f"（{answer_length(answer_text)} < {MIN_ANSWER_CHARS} 字符）")
                obs["status"] = "failed"
                obs["failure_reason"] = reason
                with open(raw_file, "w", encoding="utf-8") as f:
                    f.write(obs["response_text"])
                obs["evidence_files"].append(f"raw/{tid}.txt")
                obs["record_hash"] = stable_hash({k: v for k, v in obs.items() if k != "record_hash"})
                with open(obs_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(obs, ensure_ascii=False) + "\n")
                ps_update(run_dir, tid, "failed", obs_ref=tid, reason=reason[:200])
                finalized.add(tid)
                print(f"[task] {tid} FAILED: {reason}", flush=True)

                # 平台熔断：验证经人工接力仍未通过（立即），或连续无效达阈值
                if should_trip_platform(verdict, invalid_streak[pid]):
                    blocked_platforms[pid] = reason
                    skipped = fail_remaining(
                        pid, f"平台熔断：{reason}；连续 {invalid_streak[pid]} 次未获得有效回答，"
                             f"跳过该平台剩余任务，批次继续。")
                    print(f"[run] {p['label']} 熔断，跳过剩余 {len(skipped)} 题，继续其他平台：{skipped}", flush=True)
                continue

            invalid_streak[pid] = 0
            # 分类与来源只在【回答区】上做，不在整页 chrome 上做
            obs["sources"] = extract_sources(page)
            obs["classification"] = classify(brand_terms, official_domains,
                                             obs["response_text"], obs["sources"], q["type"])
            with open(raw_file, "w", encoding="utf-8") as f:
                f.write(obs["response_text"])
            obs["evidence_files"].append(f"raw/{tid}.txt")
            obs["record_hash"] = stable_hash({k: v for k, v in obs.items() if k != "record_hash"})
            with open(obs_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obs, ensure_ascii=False) + "\n")
            write_citations(run_dir, obs, q, brand, official_domains)
            ps_update(run_dir, tid, "success", obs_ref=tid)
            finalized.add(tid)
            print(f"[task] {tid} success | mention={obs['classification']['brand_mention']} "
                  f"strength={obs['classification']['recommend_strength']} cite={obs['classification']['source_citation']}", flush=True)
        except Exception as e:
            err = str(e)[:400]
            obs["status"] = "failed"; obs["failure_reason"] = err
            captcha_hit = None
            try:
                page = pages.get(pid)
                if page:
                    captcha_hit = detect_captcha(page)
                    page.screenshot(path=os.path.join(run_dir, "evidence", f"{tid}_full.png"), full_page=True)
                obs["evidence_files"] = [shot2]
            except Exception:
                pass

            if is_global_error(err):
                # 浏览器/连接层故障 → 所有平台都做不了，这才是真正的全局阻断
                obs["status"] = "manual_required"
                obs["failure_reason"] = f"浏览器/连接层异常：{err}"
                obs["notes"] = "全局阻断：浏览器或连接层不可用，已停止整批。"
                obs["record_hash"] = stable_hash({k: v for k, v in obs.items() if k != "record_hash"})
                with open(obs_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(obs, ensure_ascii=False) + "\n")
                ps_update(run_dir, tid, "manual_required", obs_ref=tid,
                          reason=obs["failure_reason"][:200])
                finalized.add(tid)
                print(f"[task] {tid} MANUAL_REQUIRED: {err}", flush=True)
                stop_batch = True
            else:
                # 平台级异常：本题 failed，计入熔断；不阻塞其他平台
                if captcha_hit:
                    obs["failure_reason"] = f"提交前遇平台验证（特征：{captcha_hit}）；原错误：{err}"
                    obs["notes"] = f"检测到平台验证特征：{captcha_hit}（提交前被拦截）"
                obs["record_hash"] = stable_hash({k: v for k, v in obs.items() if k != "record_hash"})
                with open(obs_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(obs, ensure_ascii=False) + "\n")
                ps_update(run_dir, tid, "failed", obs_ref=tid, reason=obs["failure_reason"][:200])
                finalized.add(tid)
                print(f"[task] {tid} FAILED: {err}", flush=True)
                invalid_streak[pid] = invalid_streak.get(pid, 0) + 1
                if captcha_hit or should_trip_platform("empty", invalid_streak[pid]):
                    blocked_platforms[pid] = obs["failure_reason"]
                    skipped = fail_remaining(
                        pid, f"平台熔断（页面异常）：{obs['failure_reason']}；跳过该平台剩余任务，批次继续。")
                    print(f"[run] {p['label']} 熔断，跳过剩余 {len(skipped)} 题，继续其他平台：{skipped}", flush=True)
        if stop_batch:
            print("[run] 触发全局阻断（浏览器/连接层），停止整批", flush=True)
            break

    if blocked_platforms:
        try:
            with open(os.path.join(run_dir, "PLATFORM_DEGRADED.md"), "w", encoding="utf-8") as f:
                f.write("# 平台熔断记录\n\n")
                f.write("以下平台在本批次被熔断，剩余任务按 `failed` 跳过；"
                        "批次仍继续执行其他平台，单平台失败不阻塞后续步骤。\n\n")
                for bp, br in blocked_platforms.items():
                    f.write(f"- `{bp}`（{pmap.get(bp, {}).get('label', bp)}）：{br}\n")
                f.write("\n报告不得据此声称覆盖全部平台。\n")
        except Exception:
            pass
        print(f"[run] 熔断平台：{list(blocked_platforms)}（见 PLATFORM_DEGRADED.md）", flush=True)

    try:
        ctx.close(); pw.stop()
    except Exception:
        pass
    print("[run] 完成本轮", flush=True)


if __name__ == "__main__":
    main()
