"""計画タスクを AI（Claude Code の CLI）で実行する。

画面の「AIで実行」ボタンから、シート「タスク管理」の1行を渡して `claude -p` をバックグラウンドで動かす。
- 実行できるのは 実行区分 が「AI」「AI＋人」で、未完了のタスクだけ
- 同時に動くのは1件だけ。結果は data/ai_runs/ に保存し、画面で確認する
- AIは eBay・メルカリへアクセスしない、購入・出品・送信をしない、git commit/push をしない
- シートのステータスは書き換えない（人が結果を確認して更新する）
- 終わったら成果物をGoogleドキュメントにしてDriveのフォルダに置き、シートの「AI成果物」列にリンクを書く
  （Claude Code にログインしたアカウントの Google Drive / Sheets 連携を使う。ツール自身はGoogleの認証情報を持たない）
"""
from __future__ import annotations

import json
import os
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
## シートのステータスの提案（完了／進行中／未着手のどれか、と理由）
"""

PUBLISH_TOOLS = [
    "Read", "ToolSearch", "mcp__claude_ai_Google_Drive__create_file",
    "mcp__claude_ai_Google_Sheets__get_values", "mcp__claude_ai_Google_Sheets__update_values",
]

PUBLISH_PROMPT = """次の手順だけを行ってください。ほかの操作はしないでください。
使うツールは ToolSearch で読み込んでください（Google Drive の create_file、Google Sheets の get_values と update_values）。

1. ファイル {path} を Read で読む
2. Google Drive の create_file で、その内容を Googleドキュメントとして作る
   - title: {title}
   - parentId: {folder}
   - contentMimeType: text/markdown（失敗したら text/plain）
   - textContent: 読んだ内容そのまま
3. Google Sheets の get_values で、スプレッドシート {sheet} の範囲 'タスク管理'!A1:A500 を読み、A列が {task_id} の行番号を探す。
   見つからなければ 4 は行わない
4. update_values で、その行の T列だけ（'タスク管理'!T<行番号>）に次の式を書く。ほかのセルは絶対に変えない
   =HYPERLINK("<作ったドキュメントのURL>","{label}")
5. 最後の行に、作ったドキュメントのURLだけを出力する
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


FORECAST_TASK = {
    "実行区分": "AI",
    "成果物・完了条件": "翌月（{next}）の見込み（仕入れファネル・販売数・貢献利益/件・営業利益・必要資金）と、12か月目（{final}）の目標との差、"
                       "その差を埋めるための利益率の改善案（カテゴリ配分・価格・費用・転換率）を、数値の根拠つきでまとめる",
    "KPI": "翌月見込みと12か月目目標の差、利益率",
    "AIの担当範囲・人の作業": "AI: 実績（data/flips.db）と計画シートのキャッシュ（data/plan_cache.json）から見込みと改善案を作成／人: 採用する改善案と翌月の仕入上限を決める",
    "備考": "当月の目標を達成していても毎月作る。計画シートの数値は書き換えない。改善案は効果の大きい順に、試し方と止める条件も書く",
}


def forecast_task(month_key: str, next_label: str, final_label: str) -> dict[str, Any]:
    t = {k: v.format(next=next_label, final=final_label) for k, v in FORECAST_TASK.items()}
    return {**t, "ID": f"AUTO-{month_key}", "タスク": f"翌月（{next_label}）の見込みと利益率の改善案", "期限": "—", "ステータス": "未着手"}


def has_run(task_id: str) -> bool:
    return any(r["task_id"] == task_id and r["status"] in ("running", "done") for r in _load())


def _cfg():
    from .config import load_config

    return load_config(ROOT / "config.yaml")


def _publish_start(r: dict[str, Any], *, popen=subprocess.Popen, cfg=None) -> None:
    """成果物（報告＋docs/ai-tasks/ID.md）を1つのMarkdownにまとめ、DriveとシートへのせるAIを起動する。"""
    cfg = cfg or _cfg()
    exe = claude_bin()
    if not (cfg.ai_tasks.publish and cfg.ai_tasks.drive_folder_id and cfg.plan.sheet_id and exe):
        return
    doc = ROOT / "docs" / "ai-tasks" / f"{r['task_id']}.md"
    body = f"# {r['task_id']} {r.get('task') or ''}\n\nAI実行: {r['id']}（{(r.get('finished_at') or '')[:16].replace('T', ' ')} UTC 完了）\n\n" \
           f"## AIの報告\n\n{r.get('report') or ''}\n"
    if doc.exists():
        body += f"\n---\n\n## 成果物（docs/ai-tasks/{doc.name}）\n\n{doc.read_text(encoding='utf-8')}\n"
    path = RUN_DIR / f"{r['id']}.publish.md"
    path.write_text(body, encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = PUBLISH_PROMPT.format(path=str(path.resolve()), title=f"{r['task_id']} {r.get('task') or ''}（AI成果物 {stamp}）",
                                   folder=cfg.ai_tasks.drive_folder_id, sheet=cfg.plan.sheet_id, task_id=r["task_id"],
                                   label=f"成果物 {stamp[5:]}")
    out = open(RUN_DIR / f"{r['id']}.publish.json", "w", encoding="utf-8")
    env = {**os.environ, "PATH": f"{Path(exe).parent}:{os.environ.get('PATH', '')}"}
    proc = popen([exe, "-p", prompt, "--output-format", "json", "--allowedTools", *PUBLISH_TOOLS],
                 cwd=str(ROOT), stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env)
    _procs[r["id"] + ":publish"] = proc
    r.update(publish="running", publish_pid=proc.pid, publish_ts=time.time())


def _publish_finish(r: dict[str, Any], code: Optional[int]) -> None:
    import re

    text = ""
    try:
        text = str(json.loads((RUN_DIR / f"{r['id']}.publish.json").read_text(encoding="utf-8")).get("result") or "")
    except Exception:  # noqa: BLE001
        pass
    m = re.findall(r"https://docs\.google\.com/document/d/[\w-]+[^\s)\]\"'>]*", text)
    if m and code in (0, None):
        r.update(publish="done", doc_url=m[-1])
    else:
        r.update(publish="failed", publish_error=text[-500:] or "Driveへの保存に失敗しました")


def start(task: dict[str, Any], *, popen=subprocess.Popen) -> dict[str, Any]:
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
        runs.append({"id": run_id, "task_id": task["ID"], "task": task.get("タスク"), "status": "running",
                     "started_at": _now(), "started_ts": time.time(), "pid": proc.pid})
        _save(runs)
    return {"ok": True, "message": f"{task['ID']} をAIで実行し始めました。数分〜数十分かかります", "run_id": run_id}


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
            if r["status"] == "done":
                try:
                    _publish_start(r)
                except Exception as e:  # noqa: BLE001
                    r.update(publish="failed", publish_error=str(e))
    for r in runs:
        if r.get("publish") != "running":
            continue
        proc = _procs.get(r["id"] + ":publish")
        code = proc.poll() if proc else (None if _alive(r["publish_pid"]) else 0)
        if code is None and time.time() - r.get("publish_ts", 0) > 10 * 60:
            (proc.kill() if proc else os.kill(r["publish_pid"], 15))
            code = -1
        if code is not None:
            _publish_finish(r, code)
            _procs.pop(r["id"] + ":publish", None)
            changed = True
    if changed:
        _save(runs)
    return runs[::-1]


def summary(limit: int = 20) -> dict[str, Any]:
    with _lock:
        runs = poll()
    hide = ("started_ts", "pid", "publish_pid", "publish_ts")
    return {"available": bool(claude_bin()), "runs": [{k: v for k, v in r.items() if k not in hide} for r in runs[:limit]]}


def publish_again(run_id: str) -> dict[str, Any]:
    """Driveへの保存をやり直す（失敗したとき・前に実行した分を戻すとき）。"""
    with _lock:
        runs = _load()
        r = next((x for x in runs if x["id"] == run_id), None)
        if not r or r["status"] != "done":
            return {"ok": False, "message": "完了した実行が見つかりません"}
        cfg = _cfg()
        if not cfg.ai_tasks.drive_folder_id:
            return {"ok": False, "message": "config.local.yaml の ai_tasks.drive_folder_id が未設定です"}
        _publish_start(r, cfg=cfg)
        _save(runs)
    return {"ok": True, "message": "成果物をGoogleドライブに保存しています（1〜2分）"}
