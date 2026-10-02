"""Web画面（標準ライブラリの HTTP サーバ）。

GET  /                    画面
GET  /api/state           最新スキャン結果 + 仕入れキュー + 見送り + 取引履歴 + 利益ルール
POST /api/scan            再スキャン（バックグラウンド）
POST /api/ebay-login      ツール用ブラウザを開いて eBay にログインしてもらう
POST /api/queue           仕入れキューに追加          {"item_key": ...}
DELETE /api/queue?key=    仕入れキューから外す
POST /api/dismiss         仕入れない（見送り）        {"item_key": ..., "reason": ...}
DELETE /api/dismiss?key=  見送りを取り消す
POST /api/purchased       仕入れ済みにする（取引履歴へ）{"item_key": ..., "actual_buy_jpy": ...}
DELETE /api/purchased?key= 取引履歴から戻す
GET  /api/queue.csv       仕入れキューを CSV で書き出し
GET  /api/history.csv     取引履歴を CSV で書き出し
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from . import db
from .config import Config, load_config
from .interactive import ebay_login_until_ready
from .pipeline import scan_all
from .sources import sample_targets

INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class AppState:
    def __init__(self, cfg_path: str, source: Optional[str]) -> None:
        self.cfg_path = cfg_path
        self.source = source
        self.lock = threading.Lock()
        # ブラウザプロファイルは同時に1つしか開けないので、スキャンとログインは排他
        self.busy: Optional[str] = None  # "scan" | "login" | None
        self.last_error: Optional[str] = None
        self.login_result: Optional[str] = None

    @property
    def cfg(self) -> Config:
        # 毎回読み直す（config.yaml を編集したら再スキャンで反映）
        return load_config(self.cfg_path)

    def _run_bg(self, kind: str, fn) -> bool:
        with self.lock:
            if self.busy:
                return False
            self.busy = kind

        def run() -> None:
            follow_up = None
            try:
                follow_up = fn()
            finally:
                with self.lock:
                    self.busy = None
            if follow_up:
                follow_up()

        threading.Thread(target=run, daemon=True).start()
        return True

    def start_scan(self) -> bool:
        def scan() -> None:
            try:
                asyncio.run(scan_all(self.cfg, data_source=self.source, sort_by="roi"))
                self.last_error = None
            except Exception as e:  # noqa: BLE001 - 画面に出す
                self.last_error = repr(e)

        return self._run_bg("scan", scan)

    def start_login(self) -> bool:
        def login() -> None:
            try:
                self.login_result = asyncio.run(ebay_login_until_ready(self.cfg))
            except Exception as e:  # noqa: BLE001
                self.login_result = f"error: {e!r}"
            # ログインできたら eBay 相場を取り直す
            return self.start_scan if self.login_result == "ok" else None

        self.login_result = None
        return self._run_bg("login", login)

    def _con(self):
        con = db.connect(self.cfg.app.db_path)
        db.migrate(con)
        return con

    def snapshot(self) -> dict[str, Any]:
        cfg = self.cfg
        con = self._con()
        try:
            row = con.execute("SELECT id, summary_json FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            summary: dict[str, Any] = json.loads(row["summary_json"]) if row else {}
            cands = []
            if row:
                cands = [
                    json.loads(r["payload_json"])
                    for r in con.execute("SELECT payload_json FROM candidates WHERE scan_id=? ORDER BY id", (row["id"],))
                ]
            history = [
                {"finished_at": r["finished_at"], **json.loads(r["summary_json"]).get("stats", {})}
                for r in con.execute("SELECT finished_at, summary_json FROM scans ORDER BY id DESC LIMIT 14")
            ][::-1]
            queue = [
                {"added_at": r["added_at"], **json.loads(r["payload_json"])}
                for r in con.execute("SELECT added_at, payload_json FROM purchase_queue ORDER BY added_at")
            ]
            dismissed = [
                {"dismissed_at": r["dismissed_at"], "reason": r["reason"], **json.loads(r["payload_json"])}
                for r in con.execute("SELECT * FROM dismissed ORDER BY dismissed_at DESC")
            ]
            purchases = [
                {"purchased_at": r["purchased_at"], "actual_buy_jpy": r["actual_buy_jpy"], **json.loads(r["payload_json"])}
                for r in con.execute("SELECT * FROM purchases ORDER BY purchased_at DESC")
            ]
        finally:
            con.close()
        return {
            "scanning": self.busy == "scan",
            "logging_in": self.busy == "login",
            "login_result": self.login_result,
            "last_error": self.last_error,
            "summary": summary,
            "candidates": cands,
            "history": history,
            "queue": queue,
            "dismissed": dismissed,
            "purchases": purchases,
            "rules": {
                "fx": cfg.fx.model_dump(),
                "fees": cfg.fees.model_dump(),
                "thresholds": cfg.thresholds.model_dump(),
                "exclude_keywords": cfg.exclude_keywords,
                "genres": [
                    t.model_dump()
                    for t in (sample_targets() if summary.get("data_source") == "sample" else cfg.all_targets)
                ],
                "data_source": self.source or cfg.app.data_source,
            },
        }

    def _latest_payload(self, con, item_key: str) -> Optional[str]:
        for sql in (
            "SELECT payload_json FROM candidates WHERE item_key=? ORDER BY id DESC LIMIT 1",
            "SELECT payload_json FROM purchase_queue WHERE item_key=?",
        ):
            row = con.execute(sql, (item_key,)).fetchone()
            if row:
                return row["payload_json"]
        return None

    def queue_add(self, item_key: str) -> bool:
        con = self._con()
        try:
            payload = self._latest_payload(con, item_key)
            if not payload:
                return False
            con.execute(
                "INSERT INTO purchase_queue(item_key, added_at, payload_json) VALUES(?, ?, ?) "
                "ON CONFLICT(item_key) DO NOTHING",
                (item_key, db.utc_now_iso(), payload),
            )
            con.execute("DELETE FROM dismissed WHERE item_key=?", (item_key,))
            con.commit()
            return True
        finally:
            con.close()

    def dismiss(self, item_key: str, reason: str) -> bool:
        con = self._con()
        try:
            payload = self._latest_payload(con, item_key)
            if not payload:
                return False
            con.execute(
                "INSERT INTO dismissed(item_key, dismissed_at, reason, payload_json) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(item_key) DO UPDATE SET reason=excluded.reason, dismissed_at=excluded.dismissed_at",
                (item_key, db.utc_now_iso(), reason or "その他", payload),
            )
            con.execute("DELETE FROM purchase_queue WHERE item_key=?", (item_key,))
            con.commit()
            return True
        finally:
            con.close()

    def purchased(self, item_key: str, actual_buy_jpy: Optional[int]) -> bool:
        con = self._con()
        try:
            payload = self._latest_payload(con, item_key)
            if not payload:
                return False
            p = json.loads(payload)
            con.execute(
                "INSERT INTO purchases(item_key, purchased_at, actual_buy_jpy, payload_json) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(item_key) DO NOTHING",
                (item_key, db.utc_now_iso(), int(actual_buy_jpy or p["buy_price_jpy"]), payload),
            )
            con.execute("DELETE FROM purchase_queue WHERE item_key=?", (item_key,))
            con.commit()
            return True
        finally:
            con.close()

    def delete(self, table: str, item_key: str) -> None:
        assert table in ("purchase_queue", "dismissed", "purchases")
        con = self._con()
        try:
            con.execute(f"DELETE FROM {table} WHERE item_key=?", (item_key,))
            con.commit()
        finally:
            con.close()

    def to_csv(self, kind: str) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        snap = self.snapshot()
        if kind == "queue":
            w.writerow(["追加日時", "ジャンル", "タイトル", "仕入(円)", "想定売価(USD)", "利益(円)", "ROI", "損益分岐(円)", "メルカリURL"])
            for q in snap["queue"]:
                w.writerow([q["added_at"], q["genre"], q["mercari_title"], q["buy_price_jpy"], q["market_usd"],
                            q["profit_jpy"], f"{q['roi']*100:.1f}%", q["breakeven_buy_jpy"], q["mercari_url"]])
        else:
            w.writerow(["仕入日時", "ジャンル", "タイトル", "実際の仕入(円)", "想定売価(USD)", "想定利益(円)", "メルカリURL"])
            for q in snap["purchases"]:
                w.writerow([q["purchased_at"], q["genre"], q["mercari_title"], q["actual_buy_jpy"], q["market_usd"],
                            q["profit_jpy"] - (q["actual_buy_jpy"] - q["buy_price_jpy"]), q["mercari_url"]])
        return "﻿" + buf.getvalue()  # Excel で文字化けしないよう BOM 付き


def _make_handler(state: AppState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # 静かに
            pass

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict[str, str]] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(state.snapshot())
            elif path in ("/api/queue.csv", "/api/history.csv"):
                kind = "queue" if "queue" in path else "history"
                self._send(
                    200,
                    state.to_csv(kind).encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{"purchase_queue" if kind == "queue" else "purchase_history"}.csv"'},
                )
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            key = str(body.get("item_key", ""))
            if path == "/api/scan":
                self._json({"started": state.start_scan()})
            elif path == "/api/ebay-login":
                self._json({"started": state.start_login()})
            elif path == "/api/queue":
                ok = state.queue_add(key)
                self._json({"ok": ok}, 200 if ok else 404)
            elif path == "/api/dismiss":
                ok = state.dismiss(key, str(body.get("reason", "")))
                self._json({"ok": ok}, 200 if ok else 404)
            elif path == "/api/purchased":
                price = body.get("actual_buy_jpy")
                ok = state.purchased(key, int(price) if price not in (None, "") else None)
                self._json({"ok": ok}, 200 if ok else 404)
            else:
                self._send(404, b"not found", "text/plain")

        def do_DELETE(self) -> None:
            u = urlparse(self.path)
            key = (parse_qs(u.query).get("key") or [""])[0]
            table = {"/api/queue": "purchase_queue", "/api/dismiss": "dismissed", "/api/purchased": "purchases"}.get(u.path)
            if table:
                state.delete(table, key)
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def serve(cfg_path: str, *, source: Optional[str] = None, port: Optional[int] = None, initial_scan: bool = True) -> int:
    console = Console()
    state = AppState(cfg_path, source)
    cfg = state.cfg
    host, port = cfg.app.web_host, port or cfg.app.web_port
    if initial_scan:
        state.start_scan()
    httpd = ThreadingHTTPServer((host, port), _make_handler(state))
    console.print(f"[bold green]Web画面: http://{host}:{port}/[/bold green]  （Ctrl+C で終了）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[bold]stopped[/bold]")
    finally:
        httpd.server_close()
    return 0
