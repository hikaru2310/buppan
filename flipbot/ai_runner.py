"""計画タスクを AI（Claude Code の CLI）で実行する。

画面の「AIで実行」ボタンから、シート「タスク管理」の1行を渡して `claude -p` をバックグラウンドで動かす。
- 実行できるのは 実行区分 が「AI」「AI＋人」で、未完了のタスクだけ
- 実行区分「AI」のタスクは、ボタンを押さなくても自動で順に実行する（auto_tick。1日の上限・依存タスク・開始日を守る）
- 「AI＋人」のタスクは、AIの実行が終わると報告の「人が確認・対応すること」を人のタスクとして追加する
  （ツール側に保存。シートは書き換えない）
- 同時に動くのは1件だけ。結果は data/ai_runs/ に保存し、画面で確認する
- AIは eBay・メルカリへアクセスしない、購入・出品・送信をしない、git commit/push をしない
- シートのステータスは書き換えない（人が結果を確認して更新する）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = Path("./data/ai_runs")
INDEX = RUN_DIR / "index.json"
HUMAN = RUN_DIR / "human_tasks.json"
TIMEOUT_SEC = 40 * 60
AI_KINDS = ("AI", "AI＋人")

ALLOWED_TOOLS = [
    "Read", "Glob", "Grep", "Edit", "Write", "WebSearch", "WebFetch",
    "Bash(.venv/bin/python tests/*)", "Bash(.venv/bin/python -m flipbot.run:*)",
    "Bash(sqlite3 -readonly:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)",
]
DISALLOWED_TOOLS = [
    "Bash(git commit:*)", "Bash(git push:*)",
    "Read(./config.local.yaml)", "Read(./data/ebay_token.json)", "Read(./data/bot-profile/**)",
]

PROMPT = """あなたは物販ツール「Arbiter」（このリポジトリ）で作業するAIです。
事業計画シート「タスク管理」の次のタスクを、AIが担当できる範囲で実行してください。

ID: {ID}
タスク: {タスク}
成果物・完了条件: {成果物・完了条件}
KPI: {KPI}
期限: {期限}
実行区分: {実行区分}
AIの担当範囲・人の作業: {AIの担当範囲・人の作業}
備考: {備考}

守ること:
- eBay・メルカリのサイトにはアクセスしない（規約で自動アクセスが禁止されています）。購入・出品・メッセージ送信など外部への操作もしない
- git commit / git push はしない。config.local.yaml や data/ の認証情報は読まない・書き出さない
- ツールのデータは data/flips.db を `sqlite3 -readonly` で参照する
- コードを変えたら `.venv/bin/python tests/test_core.py` と `.venv/bin/python tests/test_matching.py` を実行して合格を確認する
- 文書の成果物は docs/ai-tasks/{ID}.md に日本語で書く
- 人が判断・操作すべきことは自分で決めず、報告に挙げる
- データが足りず完了できない場合は、足りないものと集まる時期を報告する

最後に日本語で次の形で報告してください:
## やったこと
## 成果物（ファイルのパス）
## 人が確認・対応すること
（1行に1つの作業を「- 」で始めて書く。ここに書いた行は、そのまま人のタスクとしてツールに追加されます。なければ「- なし」）
## シートのステータスの提案（完了／進行中／未着手のどれか、と理由）
"""

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}


def claude_bin() -> Optional[str]:
    return shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")) if p.exists()), None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save(runs: list[dict[str, Any]]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(runs[-100:], ensure_ascii=False, indent=1), encoding="utf-8")


def can_run(task: dict[str, Any]) -> Optional[str]:
    """実行できない理由（できるなら None）。"""
    if (task.get("実行区分") or "") not in AI_KINDS:
        return "このタスクは人が対応するタスクです（シートの実行区分が「AI」「AI＋人」のものだけ実行できます）"
    if any(s in (task.get("ステータス") or "") for s in ("完了", "中止")):
        return "このタスクは完了（または中止）になっています"
    return None


def build_prompt(task: dict[str, Any]) -> str:
    keys = ("ID", "タスク", "成果物・完了条件", "KPI", "期限", "実行区分", "AIの担当範囲・人の作業", "備考")
    return PROMPT.format(**{k: (str(task.get(k)) if task.get(k) not in (None, "") else "—") for k in keys})


def command(prompt: str, exe: str) -> list[str]:
    return [exe, "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
            "--allowedTools", *ALLOWED_TOOLS, "--disallowedTools", *DISALLOWED_TOOLS]


def start(task: dict[str, Any], *, popen=subprocess.Popen, auto: bool = False) -> dict[str, Any]:
    why = can_run(task)
    if why:
        return {"ok": False, "message": why}
    exe = claude_bin()
    if not exe:
        return {"ok": False, "message": "Claude Code（claude コマンド）が見つかりません。インストールしてログインしてください"}
    with _lock:
        poll()
        runs = _load()
        if any(r["status"] == "running" for r in runs):
            return {"ok": False, "message": "ほかのタスクをAIが実行中です。終わってから実行してください"}
        run_id = f"{task['ID']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        out = open(RUN_DIR / f"{run_id}.json", "w", encoding="utf-8")
        err = open(RUN_DIR / f"{run_id}.err", "w", encoding="utf-8")
        env = {**os.environ, "PATH": f"{Path(exe).parent}:{os.environ.get('PATH', '')}"}
        proc = popen(command(build_prompt(task), exe), cwd=str(ROOT), stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=env)
        _procs[run_id] = proc
        runs.append({"id": run_id, "task_id": task["ID"], "task": task.get("タスク"), "kind": task.get("実行区分"),
                     "auto": auto, "status": "running", "started_at": _now(), "started_ts": time.time(), "pid": proc.pid})
        _save(runs)
    how = "自動で" if auto else ""
    return {"ok": True, "message": f"{task['ID']} を{how}AIで実行し始めました。数分〜数十分かかります", "run_id": run_id}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _finish(r: dict[str, Any], code: Optional[int]) -> None:
    raw = ""
    try:
        raw = (RUN_DIR / f"{r['id']}.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        report, failed = str(data.get("result") or ""), bool(data.get("is_error"))
        r["cost_usd"] = data.get("total_cost_usd")
    except Exception:  # noqa: BLE001
        errtxt = ""
        try:
            errtxt = (RUN_DIR / f"{r['id']}.err").read_text(encoding="utf-8")[-2000:]
        except OSError:
            pass
        report, failed = (raw[-4000:] or errtxt or "出力がありませんでした"), True
    if code not in (0, None):
        failed = True
    if failed and "authenticate" in report:
        report += "\n\nClaude Code のログインが切れています。ターミナルで `claude` を起動し、/login でログインし直してから再実行してください。"
    (RUN_DIR / f"{r['id']}.md").write_text(report, encoding="utf-8")
    r.update(status="failed" if failed else "done", finished_at=_now(), report=report[:20000])
    if not failed and r.get("kind") == "AI＋人":
        r["human_task_ids"] = add_human_tasks(r, parse_human_items(report))


# ---------- 「AI＋人」: AIのあとに人がやるタスク

_NONE = re.compile(r"^(なし|特になし|ありません|該当なし|なし。|—|-)$")
FALLBACK_HUMAN = "AIの報告と成果物を確認し、シートのステータスを更新する"


def parse_human_items(report: str) -> list[str]:
    """報告の「## 人が確認・対応すること」の箇条書きを1行ずつ取り出す。"""
    items: list[str] = []
    inside = False
    for line in report.splitlines():
        s = line.strip()
        if s.startswith("#"):
            inside = "人が確認" in s or "人の作業" in s or "人が対応" in s
            continue
        if not inside or not s:
            continue
        m = re.match(r"^(?:[-*・●]|\d+[.)．、])\s*(?:\[[ xX]\]\s*)?(.+)$", s)
        text = (m.group(1) if m else s).strip().strip("*").strip()
        if text and not _NONE.match(text):
            items.append(text[:300])
    return items[:15]


def load_human() -> list[dict[str, Any]]:
    try:
        return json.loads(HUMAN.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save_human(items: list[dict[str, Any]]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    HUMAN.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=1), encoding="utf-8")


def add_human_tasks(run: dict[str, Any], texts: list[str]) -> list[str]:
    """AIの実行1回分の人のタスクを追加する。報告に何もなければ「結果を確認する」を1件。"""
    items = load_human()
    parent = run["task_id"]
    n = sum(1 for h in items if h["parent"] == parent)
    ids = []
    for text in texts or [FALLBACK_HUMAN]:
        n += 1
        hid = f"{parent}-H{n}"
        items.append({"id": hid, "parent": parent, "parent_task": run.get("task"), "run_id": run["id"],
                      "text": text, "created_at": _now(), "done": False, "done_at": None})
        ids.append(hid)
    _save_human(items)
    return ids


def set_human_done(hid: str, done: bool = True) -> dict[str, Any]:
    with _lock:
        items = load_human()
        h = next((x for x in items if x["id"] == hid), None)
        if not h:
            return {"ok": False, "message": "その人のタスクが見つかりません"}
        h["done"], h["done_at"] = bool(done), (_now() if done else None)
        _save_human(items)
    left = sum(1 for x in items if x["parent"] == h["parent"] and not x["done"])
    msg = f"{hid} を完了にしました" if done else f"{hid} を未完了に戻しました"
    if done and not left:
        msg += f"。{h['parent']} の人の作業はすべて完了です。シートのステータスも更新してください"
    return {"ok": True, "message": msg}


# ---------- 「AI」: ボタンを押さなくても自動で実行する

_PRIORITY = {"最優先": 0, "高": 1, "中": 2, "低": 3}


def expand_ids(text: Any) -> list[str]:
    """依存タスクの表記（P10,P11 / P07-P16 / -）をIDの一覧にする。"""
    out: list[str] = []
    for part in re.split(r"[,、，\s]+", str(text or "")):
        m = re.match(r"^([A-Za-z]+)(\d+)[-〜~]([A-Za-z]+)?(\d+)$", part)
        if m and (m.group(3) in (None, m.group(1))):
            w = len(m.group(2))
            out += [f"{m.group(1)}{i:0{w}d}" for i in range(int(m.group(2)), int(m.group(4)) + 1)]
        elif re.match(r"^[A-Za-z]+\d+$", part):
            out.append(part)
    return out


def _closed(task: dict[str, Any]) -> bool:
    return any(s in (task.get("ステータス") or "") for s in ("完了", "中止"))


def dep_done(dep: str, tasks: dict[str, dict[str, Any]], runs: list[dict[str, Any]], human: list[dict[str, Any]]) -> bool:
    """依存タスクが済んでいるか。シートで完了、またはAIの実行が完了して人の作業も残っていない。"""
    t = tasks.get(dep)
    if t is None or _closed(t):
        return True
    if (t.get("実行区分") or "") not in AI_KINDS:
        return False
    if not any(r["task_id"] == dep and r["status"] == "done" for r in runs):
        return False
    return not any(h["parent"] == dep and not h["done"] for h in human)


def pick_auto(tasks: list[dict[str, Any]], runs: list[dict[str, Any]], human: list[dict[str, Any]], *,
              kinds: list[str], today: str) -> Optional[dict[str, Any]]:
    """次に自動実行するタスク。一度でも実行したもの（失敗を含む）は自動では繰り返さない。"""
    by_id = {t.get("ID"): t for t in tasks if t.get("ID")}
    tried = {r["task_id"] for r in runs}
    ok = []
    for t in tasks:
        if (t.get("実行区分") or "") not in kinds or can_run(t) or t.get("ID") in tried:
            continue
        if t.get("開始日") and str(t["開始日"])[:10] > today:
            continue
        if not all(dep_done(d, by_id, runs, human) for d in expand_ids(t.get("依存タスク"))):
            continue
        ok.append(t)
    ok.sort(key=lambda t: (str(t.get("期限") or "9999"), _PRIORITY.get(str(t.get("優先度") or ""), 9), str(t.get("ID"))))
    return ok[0] if ok else None


def auto_count_today(runs: list[dict[str, Any]], today: str) -> int:
    return sum(1 for r in runs if r.get("auto") and _local_date(r.get("started_at")) == today)


def _local_date(iso: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(str(iso)).astimezone().date().isoformat()
    except Exception:  # noqa: BLE001
        return ""


def auto_tick(ai_cfg, tasks: list[dict[str, Any]], *, popen=subprocess.Popen,
              today: Optional[str] = None) -> Optional[dict[str, Any]]:
    """自動実行を1回試す。始めたら start() の結果、何もしなければ None。"""
    if not ai_cfg.auto_run or not claude_bin():
        return None
    today = today or datetime.now().date().isoformat()
    with _lock:
        runs = poll()
    if any(r["status"] == "running" for r in runs) or auto_count_today(runs, today) >= ai_cfg.auto_max_per_day:
        return None
    task = pick_auto(tasks, runs, load_human(), kinds=list(ai_cfg.auto_kinds), today=today)
    return start(task, popen=popen, auto=True) if task else None


def poll() -> list[dict[str, Any]]:
    """実行中のものの終了を確認して記録を更新し、新しい順に返す。"""
    runs = _load()
    changed = False
    for r in runs:
        if r["status"] != "running":
            continue
        proc = _procs.get(r["id"])
        code = proc.poll() if proc else (None if _alive(r["pid"]) else 0)
        if code is None and time.time() - r.get("started_ts", 0) > TIMEOUT_SEC:
            (proc.kill() if proc else os.kill(r["pid"], 15))
            code = -1
            r["timeout"] = True
        if code is not None:
            _finish(r, code)
            _procs.pop(r["id"], None)
            changed = True
    if changed:
        _save(runs)
    return runs[::-1]


def summary(limit: int = 20, ai_cfg=None, tasks: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    with _lock:
        runs = poll()
    human = load_human()
    out: dict[str, Any] = {
        "available": bool(claude_bin()),
        "runs": [{k: v for k, v in r.items() if k not in ("started_ts", "pid")} for r in runs[:limit]],
        "human": human[::-1][:200],
    }
    if ai_cfg is not None:
        today = datetime.now().date().isoformat()
        nxt = pick_auto(tasks or [], runs, human, kinds=list(ai_cfg.auto_kinds), today=today) if tasks is not None else None
        out["auto"] = {"enabled": ai_cfg.auto_run, "interval_minutes": ai_cfg.auto_interval_minutes,
                       "max_per_day": ai_cfg.auto_max_per_day, "kinds": list(ai_cfg.auto_kinds),
                       "today": auto_count_today(runs, today), "next": nxt.get("ID") if nxt else None}
    return out
