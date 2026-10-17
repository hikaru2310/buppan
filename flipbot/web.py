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
POST /api/sku            SKUの工程を進める         {"sku": ..., "action": arrive/inspect_pass/.../pay, "fields": {...}}
POST /api/plan-sync       事業計画シートを今すぐ読み直す
POST /api/watch           マスター（継続仕入れ）を登録・更新 {"id"?, "label", "mercari_query", ...}
DELETE /api/watch?id=     マスターを削除
POST /api/notify-test     通知のテスト送信
POST /api/evaluate        メルカリの商品URLを貼って判定
POST /api/label           照合の正誤（✓/✗）を記録
POST /api/month-actual    月次の実績（固定費・入金総額・オーナー稼働）を記録
POST /api/genre           ジャンルの停止・再開・追加・削除
POST /api/ebay-import     eBayのCSV（注文・取引レポート）を取り込み {"text":..., "apply": bool}
GET  /api/listing-draft?sku= 英語の出品下書き
POST /api/settings        画面入力の設定値（現金残高など）{"cash_jpy": ...}
GET  /api/queue.csv       仕入れキューを CSV で書き出し
GET  /api/skus.csv        全SKU（在庫・取引）を CSV で書き出し
GET  /api/kpi.tsv?month=N 計画シート「KPI実績」に貼り付ける1行（タブ区切り）
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from rich.console import Console

import yaml

from . import db
from . import fx as fx_mod
from . import ops
from . import plan as plan_mod
from .config import Config, load_config
from .interactive import ebay_login_until_ready
from .pipeline import active_genres, all_genres, scan_all
from .sources import sample_targets

INDEX_HTML = Path(__file__).parent / "web" / "index.html"
ENV_YAML = Path(__file__).parent / "knowledge" / "environment.yaml"


class AppState:
    def __init__(self, cfg_path: str, source: Optional[str]) -> None:
        self.cfg_path = cfg_path
        self.source = source
        self.lock = threading.Lock()
        # ブラウザプロファイルは同時に1つしか開けないので、スキャンとログインは排他
        self.busy: Optional[str] = None  # "scan" | "login" | None
        self.last_error: Optional[str] = None
        self.login_result: Optional[str] = None
        self.next_scan_at: Optional[float] = None  # 次の自動スキャン（UNIX秒）

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
                asyncio.run(scan_all(self.cfg, data_source=self.source, sort_by="score", rotate=True))
                self.last_error = None
            except Exception as e:  # noqa: BLE001 - 画面に出す
                self.last_error = repr(e)

        return self._run_bg("scan", scan)

    def evaluate(self, url: str, target_id: Optional[str]) -> dict[str, Any]:
        """URLを貼って判定。ブラウザのプロファイルは同時に1つしか開けないので、スキャン中は待ってもらう。"""
        from .pipeline import evaluate_url

        with self.lock:
            if self.busy:
                return {"ok": False, "message": "スキャン中・ログイン中のため、終わってからもう一度お試しください"}
            self.busy = "evaluate"
        try:
            return asyncio.run(evaluate_url(self.cfg, url, target_id=target_id or None))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"判定できませんでした: {e!r}"}
        finally:
            with self.lock:
                self.busy = None

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
            row = con.execute("SELECT id, data_source, summary_json FROM scans WHERE data_source!='manual' ORDER BY id DESC LIMIT 1").fetchone()
            summary: dict[str, Any] = json.loads(row["summary_json"]) if row else {}
            cands = []
            if row:
                # ジャンルを順番に回すので、ジャンルごとに「最後に取得したスキャン」の候補を集める
                for r in con.execute(
                    "SELECT c.payload_json, s.finished_at FROM candidates c "
                    "JOIN (SELECT st.target_id, MAX(st.scan_id) sid FROM scan_targets st JOIN scans s2 ON s2.id=st.scan_id "
                    "      WHERE s2.data_source=? GROUP BY st.target_id) m ON c.target_id=m.target_id AND c.scan_id=m.sid "
                    "JOIN scans s ON s.id=c.scan_id ORDER BY c.id",
                    (row["data_source"],),
                ):
                    p = json.loads(r["payload_json"])
                    p["scanned_at"] = r["finished_at"]
                    cands.append(p)
                if not cands and not con.execute("SELECT 1 FROM scan_targets LIMIT 1").fetchone():
                    # 旧形式（scan_targets がない頃）のスキャン
                    cands = [json.loads(r["payload_json"]) for r in con.execute("SELECT payload_json FROM candidates WHERE scan_id=? ORDER BY id", (row["id"],))]
                live_ids = {t.id for t in active_genres(con, cfg)} | {f"m{r['id']}" for r in con.execute("SELECT id FROM watch_items WHERE active=1")}
                if row["data_source"] != "sample":
                    cands = [c for c in cands if c.get("target_id") in live_ids]
            pause_until = db.get_setting(con, "mercari_pause_until")
            pause = {"until": pause_until, "reason": db.get_setting(con, "mercari_pause_reason"),
                     "strikes": int(db.get_setting(con, "mercari_strikes", "0") or 0),
                     "active": bool(pause_until and pause_until > db.utc_now_iso())}
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
            watch = [dict(r) for r in con.execute("SELECT * FROM watch_items ORDER BY active DESC, id DESC")]
            labels = {f"{r['mercari_title']}\t{r['ebay_url'] or r['ebay_title']}": r["label"] for r in con.execute("SELECT * FROM match_labels")}
            genres_all = [{**t.model_dump(), "active": on, "custom": custom} for t, on, custom in all_genres(con, cfg)]
            active_list = active_genres(con, cfg)
            rate, rate_src, rate_at = fx_mod.current_rate(cfg)
            cfg.fx.jpy_per_usd = round(rate, 2)
            plan = plan_mod.load(cfg)
            from_sheet = plan_mod.apply_assumptions(cfg, plan)
            m = ops.metrics(con, cfg, plan)
        finally:
            con.close()
        try:
            env = yaml.safe_load(ENV_YAML.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            env = {"error": str(e)}
        return {
            "fx": {"rate": rate, "source": rate_src, "at": rate_at},
            "plan": {k: v for k, v in plan.items() if k not in ("benchmarks",)},
            "ops": m,
            "env": env,
            "costs": {**cfg.costs.model_dump(), "from_sheet": from_sheet},
            "watch": watch,
            "labels": labels,
            "mercari_pause": pause,
            "settings": {
                "mercari_source": cfg.app.mercari_source,
                "notify_configured": bool(cfg.notify.slack_webhook_url),
                "notify": {k: v for k, v in cfg.notify.model_dump().items() if k != "slack_webhook_url"},
                "category_excludes": cfg.category_excludes,
                "auto_scan_minutes": cfg.app.auto_scan_minutes,
                "genres_per_scan": cfg.app.genres_per_scan,
                "scan_mode": cfg.app.scan_mode,
                "handling_days": cfg.app.handling_days,
                "backup_dir": cfg.app.backup_dir,
            },
            "scanning": self.busy == "scan",
            "evaluating": self.busy == "evaluate",
            "next_scan_at": self.next_scan_at,
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
                    for t in (sample_targets() if summary.get("data_source") == "sample" else active_list)
                ],
                # 戦略ページ用：サンプル表示中でも config.yaml の監視ジャンルを使う
                "config_genres": [g for g in genres_all if g["active"]],
                "all_genres": genres_all,
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
            db.log_event(con, "queue", item_key)
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
            db.log_event(con, "dismiss", item_key)
            con.commit()
            return True
        finally:
            con.close()

    def purchased(self, item_key: str, actual_buy_jpy: Optional[int]) -> Optional[str]:
        """仕入れ確定 → SKUを採番して「到着待ち」で在庫管理へ。"""
        con = self._con()
        try:
            payload = self._latest_payload(con, item_key)
            if not payload:
                return None
            if con.execute("SELECT 1 FROM skus WHERE item_key=?", (item_key,)).fetchone():
                return None
            p = json.loads(payload)
            sku = ops.create_from_candidate(con, p, actual_buy_jpy=actual_buy_jpy, category=p.get("category"))
            con.execute("DELETE FROM purchase_queue WHERE item_key=?", (item_key,))
            con.commit()
            return sku
        finally:
            con.close()

    def sku_action(self, sku: str, action: str, fields: dict[str, Any]) -> tuple[bool, str]:
        con = self._con()
        try:
            if action == "delete":
                con.execute("DELETE FROM skus WHERE sku=?", (sku,))
                con.commit()
                return True, "OK"
            return ops.apply_action(con, sku, action, fields)
        finally:
            con.close()

    WATCH_FIELDS = ("label", "category", "mercari_query", "ebay_query", "max_buy_jpy", "shipping_jpy", "min_profit_jpy", "exclude", "source_ref", "active")

    def save_watch(self, body: dict[str, Any]) -> tuple[bool, str]:
        vals = {k: body.get(k) for k in self.WATCH_FIELDS if k in body}
        for k in ("max_buy_jpy", "shipping_jpy", "min_profit_jpy", "active"):
            if k in vals:
                vals[k] = int(float(vals[k])) if vals[k] not in (None, "") else None
        con = self._con()
        try:
            if body.get("id"):
                vals["updated_at"] = db.utc_now_iso()
                con.execute(f"UPDATE watch_items SET {', '.join(f'{k}=?' for k in vals)} WHERE id=?", (*vals.values(), int(body["id"])))
            else:
                if not (vals.get("label") and vals.get("mercari_query") and vals.get("ebay_query")):
                    return False, "名前・メルカリ検索語・eBay検索語は必須です"
                vals.setdefault("active", 1)
                vals["created_at"] = db.utc_now_iso()
                con.execute(f"INSERT INTO watch_items({', '.join(vals)}) VALUES({', '.join('?' * len(vals))})", tuple(vals.values()))
            con.commit()
            return True, "保存しました（次のスキャンから監視します）"
        finally:
            con.close()

    def delete_watch(self, wid: int) -> None:
        con = self._con()
        try:
            con.execute("DELETE FROM watch_items WHERE id=?", (wid,))
            con.commit()
        finally:
            con.close()

    def save_label(self, b: dict[str, Any]) -> dict[str, Any]:
        con = self._con()
        try:
            con.execute("DELETE FROM match_labels WHERE mercari_title=? AND ebay_title=? AND IFNULL(ebay_url,'')=IFNULL(?, '')",
                        (b.get("mercari_title"), b.get("ebay_title"), b.get("ebay_url")))
            if b.get("label") in (0, 1, "0", "1"):
                con.execute("INSERT INTO match_labels(target_id, mercari_title, ebay_title, ebay_url, label, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                            (b.get("target_id"), b.get("mercari_title"), b.get("ebay_title"), b.get("ebay_url"), int(b["label"]), db.utc_now_iso()))
            con.commit()
            return {"ok": True, "message": "記録しました（✗は次のスキャンから比較対象を外します）" if str(b.get("label")) == "0" else "記録しました"}
        finally:
            con.close()

    def save_month_actual(self, b: dict[str, Any]) -> dict[str, Any]:
        def num(v):
            return float(v) if v not in (None, "") else None
        con = self._con()
        try:
            con.execute(
                "INSERT INTO month_actuals(month, fixed_cost_man, payout_man, owner_hours, note, updated_at) VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(month) DO UPDATE SET fixed_cost_man=excluded.fixed_cost_man, payout_man=excluded.payout_man, "
                "owner_hours=excluded.owner_hours, note=excluded.note, updated_at=excluded.updated_at",
                (int(b["month"]), num(b.get("fixed_cost_man")), num(b.get("payout_man")), num(b.get("owner_hours")), b.get("note"), db.utc_now_iso()))
            con.commit()
            return {"ok": True, "message": "月次の実績を保存しました"}
        finally:
            con.close()

    def save_genre(self, b: dict[str, Any]) -> dict[str, Any]:
        from .config import Target

        con = self._con()
        try:
            if b.get("delete"):
                con.execute("DELETE FROM genre_overrides WHERE target_id=? AND custom_json IS NOT NULL", (b["id"],))
            elif b.get("custom"):
                c = dict(b["custom"])
                c.setdefault("id", "u-" + re.sub(r"[^a-z0-9]+", "-", (c.get("ebay_query") or c.get("name") or "genre").lower()).strip("-")[:40])
                for k in ("shipping_cost_jpy", "min_profit_jpy", "max_buy_jpy"):
                    if c.get(k) in ("", None):
                        c.pop(k, None)
                t = Target.model_validate(c)
                con.execute("INSERT INTO genre_overrides(target_id, active, custom_json, updated_at) VALUES(?, 1, ?, ?) "
                            "ON CONFLICT(target_id) DO UPDATE SET custom_json=excluded.custom_json, updated_at=excluded.updated_at",
                            (t.id, t.model_dump_json(), db.utc_now_iso()))
            else:
                con.execute("INSERT INTO genre_overrides(target_id, active, updated_at) VALUES(?, ?, ?) "
                            "ON CONFLICT(target_id) DO UPDATE SET active=excluded.active, updated_at=excluded.updated_at",
                            (b["id"], 1 if b.get("active") else 0, db.utc_now_iso()))
            con.commit()
            return {"ok": True, "message": "ジャンル設定を保存しました（次のスキャンから反映）"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"保存できませんでした: {e}"}
        finally:
            con.close()

    def ebay_import(self, text: str, do_apply: bool) -> dict[str, Any]:
        from . import ebay_import as ei

        cfg = self.cfg
        cfg.fx.jpy_per_usd = round(fx_mod.current_rate(cfg)[0], 2)
        con = self._con()
        try:
            return ei.apply(con, text) if do_apply else ei.plan_updates(con, text)
        finally:
            con.close()

    def listing_draft(self, sku: str) -> dict[str, Any]:
        from .listing import draft

        con = self._con()
        try:
            r = con.execute("SELECT * FROM skus WHERE sku=?", (sku,)).fetchone()
            return draft(dict(r)) if r else {"error": "SKUが見つかりません"}
        finally:
            con.close()

    def set_setting(self, key: str, value: Any) -> None:
        con = self._con()
        try:
            con.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, "" if value is None else str(value)))
            con.commit()
        finally:
            con.close()

    def kpi_tsv(self, month: Optional[int]) -> str:
        """計画シート「KPI実績」の列順で1行（計画値はシートの値、差異=実績−計画）。"""
        snap = self.snapshot()
        o, plan = snap["ops"], snap["plan"]
        idx = month or o["month_index"]
        a = next((x for x in o["months"] if x["month"] == idx), None) or {}
        k = next((x for x in plan.get("kpi_plan", []) if int(x.get("月", 0) or 0) == idx), {})
        pm = next((x for x in plan.get("monthly", []) if int(x.get("月", 0) or 0) == idx), {})
        ind = o["indicators"]
        op = a.get("op_profit_man", "")
        def diff(x, y):
            return round(x - y, 1) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else ""
        pct = lambda v: f"{v*100:.1f}%" if isinstance(v, (int, float)) else ""  # noqa: E731
        row = [
            idx,
            k.get("販売数_計画", ""), a.get("sales", ""), diff(a.get("sales"), k.get("販売数_計画")),
            k.get("売上_計画(万円)", ""), a.get("revenue_man", ""), diff(a.get("revenue_man"), k.get("売上_計画(万円)")),
            k.get("営業利益_計画(万円)", ""), op, diff(op, k.get("営業利益_計画(万円)")),
            k.get("在庫日数_計画", ""), a.get("inventory_days"), diff(a.get("inventory_days"), k.get("在庫日数_計画")),
            k.get("貢献利益/件_計画", ""), a.get("profit_per_unit"), diff(a.get("profit_per_unit"), k.get("貢献利益/件_計画")),
            k.get("仕入成功数_計画", ""), a.get("purchases", ""), diff(a.get("purchases"), k.get("仕入成功数_計画")),
            pct(ind.get("return_rate")),
            pct(1 - ind["mispurchase_rate"]) if ind.get("mispurchase_rate") is not None else "",
            pct(ind.get("forecast_error")),
            pct(ind.get("over90_rate")),
        ]
        return "\t".join("" if v is None else str(v) for v in row) + "\n"

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
        if kind == "skus":
            cols = ["sku", "status_label", "category", "genre", "title", "purchased_at", "buy_price_jpy", "predicted_sell_usd",
                    "predicted_profit_jpy", "arrived_at", "inspection", "defect_reason", "listed_at", "list_price_usd", "sold_at",
                    "sold_price_usd", "buyer_country", "shipped_at", "carrier", "tracking", "shipping_cost_jpy", "duty_jpy",
                    "paid_at", "payout_jpy", "realized_jpy", "returned_at", "return_reason", "mercari_url", "ebay_url"]
            w.writerow(cols)
            for x in snap["ops"]["skus"]:
                w.writerow([x.get(c, "") for c in cols])
        elif kind == "queue":
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
            elif path.startswith("/static/"):
                name = path[len("/static/"):]
                f = INDEX_HTML.parent / name
                ctype = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(f.suffix)
                if ctype and "/" not in name and f.is_file():
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
            elif path == "/api/state":
                self._json(state.snapshot())
            elif path in ("/api/queue.csv", "/api/history.csv", "/api/skus.csv"):
                kind = "queue" if "queue" in path else "skus" if "skus" in path else "history"
                self._send(
                    200,
                    state.to_csv(kind).encode("utf-8"),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{kind}.csv"'},
                )
            elif path == "/api/listing-draft":
                self._json(state.listing_draft((parse_qs(urlparse(self.path).query).get("sku") or [""])[0]))
            elif path == "/api/kpi.tsv":
                q = parse_qs(urlparse(self.path).query)
                month = int(q["month"][0]) if q.get("month") else None
                self._send(200, state.kpi_tsv(month).encode("utf-8"), "text/plain; charset=utf-8")
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
                sku = state.purchased(key, int(float(price)) if price not in (None, "") else None)
                self._json({"ok": bool(sku), "sku": sku}, 200 if sku else 404)
            elif path == "/api/sku":
                ok, msg = state.sku_action(str(body.get("sku", "")), str(body.get("action", "")), body.get("fields") or {})
                self._json({"ok": ok, "message": msg}, 200 if ok else 400)
            elif path == "/api/plan-sync":
                p = plan_mod.load(state.cfg, force=True)
                self._json({"ok": "error" not in p, "message": p.get("error") or f"同期しました（{p.get('_synced_at')}）"})
            elif path == "/api/watch":
                ok, msg = state.save_watch(body)
                self._json({"ok": ok, "message": msg}, 200 if ok else 400)
            elif path == "/api/label":
                self._json(state.save_label(body))
            elif path == "/api/month-actual":
                self._json(state.save_month_actual(body))
            elif path == "/api/genre":
                self._json(state.save_genre(body))
            elif path == "/api/ebay-import":
                self._json(state.ebay_import(str(body.get("text", "")), bool(body.get("apply"))))
            elif path == "/api/evaluate":
                self._json(state.evaluate(str(body.get("url", "")).strip(), body.get("target_id")))
            elif path == "/api/notify-test":
                from .notify import send_test

                ok, msg = send_test(state.cfg)
                self._json({"ok": ok, "message": msg})
            elif path == "/api/settings":
                for k in ("cash_jpy",):
                    if k in body:
                        state.set_setting(k, body[k])
                self._json({"ok": True})
            elif path == "/api/mercari-resume":
                state.set_setting("mercari_pause_until", "")
                state.set_setting("mercari_strikes", 0)
                self._json({"ok": True, "message": "メルカリの取得を再開しました（次のスキャンから）"})
            else:
                self._send(404, b"not found", "text/plain")

        def do_DELETE(self) -> None:
            u = urlparse(self.path)
            key = (parse_qs(u.query).get("key") or [""])[0]
            table = {"/api/queue": "purchase_queue", "/api/dismiss": "dismissed", "/api/purchased": "purchases"}.get(u.path)
            if u.path == "/api/watch":
                state.delete_watch(int((parse_qs(u.query).get("id") or ["0"])[0]))
                self._json({"ok": True})
            elif table:
                state.delete(table, key)
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def _daily_tasks(state: "AppState") -> None:
    """1日1回の作業: DBのバックアップと、外部環境の再確認期限のチェック。"""
    from . import backup
    from .notify import check_environment_reviews

    cfg = state.cfg
    try:
        backup.run(cfg)
    except Exception as e:  # noqa: BLE001
        state.last_error = f"バックアップに失敗しました: {e!r}"
    try:
        con = state._con()
        try:
            check_environment_reviews(cfg, con, yaml.safe_load(ENV_YAML.read_text(encoding="utf-8")))
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        pass


def _auto_scan_loop(state: "AppState", interval: int) -> None:
    """一定間隔で自動スキャン。間隔は毎回±揺らぐ。スキャン中・eBayログイン中なら次の回に回す。"""
    import random
    import time

    def nxt() -> float:
        j = max(0.0, min(state.cfg.app.auto_scan_jitter, 0.9))
        return time.time() + interval * random.uniform(1 - j, 1 + j)

    state.next_scan_at = nxt()
    while True:
        time.sleep(max(state.next_scan_at - time.time(), 5))
        if not state.start_scan():
            state.next_scan_at = time.time() + 60  # 使用中なら1分後に再挑戦
            continue
        _daily_tasks(state)  # バックアップは日付ごとに1回だけ作られる
        state.next_scan_at = nxt()


def serve(cfg_path: str, *, source: Optional[str] = None, port: Optional[int] = None, initial_scan: bool = True) -> int:
    console = Console()
    state = AppState(cfg_path, source)
    cfg = state.cfg
    host, port = cfg.app.web_host, port or cfg.app.web_port
    _daily_tasks(state)
    if initial_scan:
        state.start_scan()
    interval = int(cfg.app.auto_scan_minutes) * 60
    if interval > 0:
        threading.Thread(target=_auto_scan_loop, args=(state, interval), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), _make_handler(state))
    console.print(f"[bold green]Web画面: http://{host}:{port}/[/bold green]  （Ctrl+C で終了）")
    if interval > 0:
        console.print(f"自動スキャン: {interval // 60}分おき（config.yaml の app.auto_scan_minutes で変更。0で停止）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[bold]stopped[/bold]")
    finally:
        httpd.server_close()
    return 0
