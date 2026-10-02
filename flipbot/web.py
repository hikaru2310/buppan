"""Web画面（標準ライブラリの HTTP サーバ）。

GET  /                 画面
GET  /api/state        最新スキャン結果 + 仕入れキュー + 利益ルール
POST /api/scan         再スキャン（バックグラウンド）
POST /api/queue        仕入れキューに追加   {"item_key": ...}
DELETE /api/queue?key= 仕入れキューから削除
GET  /api/queue.csv    仕入れキューを CSV で書き出し
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
from .pipeline import scan_all
from .sources import sample_targets

INDEX_HTML = Path(__file__).parent / "web" / "index.html"


class AppState:
    def __init__(self, cfg_path: str, source: Optional[str]) -> None:
        self.cfg_path = cfg_path
        self.source = source
        self.lock = threading.Lock()
        self.scanning = False
        self.last_error: Optional[str] = None

    @property
    def cfg(self) -> Config:
        # 毎回読み直す（config.yaml を編集したら再スキャンで反映）
        return load_config(self.cfg_path)

    def start_scan(self) -> bool:
        with self.lock:
            if self.scanning:
                return False
            self.scanning = True
        threading.Thread(target=self._scan, daemon=True).start()
        return True

    def _scan(self) -> None:
        try:
            asyncio.run(scan_all(self.cfg, data_source=self.source, sort_by="roi"))
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - 画面に出す
            self.last_error = repr(e)
        finally:
            with self.lock:
                self.scanning = False

    def snapshot(self) -> dict[str, Any]:
        cfg = self.cfg
        con = db.connect(cfg.app.db_path)
        db.migrate(con)
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
        finally:
            con.close()
        return {
            "scanning": self.scanning,
            "last_error": self.last_error,
            "summary": summary,
            "candidates": cands,
            "history": history,
            "queue": queue,
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

    def queue_add(self, item_key: str) -> bool:
        cfg = self.cfg
        con = db.connect(cfg.app.db_path)
        db.migrate(con)
        try:
            row = con.execute(
                "SELECT payload_json FROM candidates WHERE item_key=? ORDER BY id DESC LIMIT 1", (item_key,)
            ).fetchone()
            if not row:
                return False
            con.execute(
                "INSERT INTO purchase_queue(item_key, added_at, payload_json) VALUES(?, ?, ?) "
                "ON CONFLICT(item_key) DO NOTHING",
                (item_key, db.utc_now_iso(), row["payload_json"]),
            )
            con.commit()
            return True
        finally:
            con.close()

    def queue_remove(self, item_key: str) -> None:
        con = db.connect(self.cfg.app.db_path)
        try:
            con.execute("DELETE FROM purchase_queue WHERE item_key=?", (item_key,))
            con.commit()
        finally:
            con.close()

    def queue_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["追加日時", "ジャンル", "タイトル", "仕入(円)", "想定売価(USD)", "利益(円)", "ROI", "損益分岐(円)", "URL"])
        for q in self.snapshot()["queue"]:
            w.writerow([
                q["added_at"], q["genre"], q["mercari_title"], q["buy_price_jpy"], q["market_usd"],
                q["profit_jpy"], f"{q['roi']*100:.1f}%", q["breakeven_buy_jpy"], q["mercari_url"],
            ])
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
            elif path == "/api/queue.csv":
                self._send(
                    200,
                    state.queue_csv().encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": 'attachment; filename="purchase_queue.csv"'},
                )
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if path == "/api/scan":
                self._json({"started": state.start_scan()})
            elif path == "/api/queue":
                ok = state.queue_add(str(body.get("item_key", "")))
                self._json({"ok": ok}, 200 if ok else 404)
            else:
                self._send(404, b"not found", "text/plain")

        def do_DELETE(self) -> None:
            u = urlparse(self.path)
            if u.path == "/api/queue":
                key = (parse_qs(u.query).get("key") or [""])[0]
                state.queue_remove(key)
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
