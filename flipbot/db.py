from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def migrate(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS targets (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          target_id TEXT NOT NULL,
          source TEXT NOT NULL, -- 'mercari' or 'ebay'
          captured_at TEXT NOT NULL,
          meta_json TEXT,
          FOREIGN KEY (target_id) REFERENCES targets(id)
        );

        CREATE TABLE IF NOT EXISTS mercari_listings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          price_jpy INTEGER NOT NULL,
          shipping_included INTEGER NOT NULL, -- 1:送料込み, 0:送料別/不明
          status TEXT NOT NULL, -- 'on_sale' etc
          url TEXT NOT NULL,
          item_id TEXT,
          FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        );

        CREATE TABLE IF NOT EXISTS ebay_sold (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          price_usd REAL NOT NULL,
          shipping_usd REAL,
          total_usd REAL NOT NULL,
          url TEXT NOT NULL,
          ended_at TEXT,
          FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_target_time
          ON snapshots(target_id, captured_at DESC);

        -- 新着検知: 出品を初めて見た時刻を保持
        CREATE TABLE IF NOT EXISTS seen_items (
          source TEXT NOT NULL,
          item_key TEXT NOT NULL,
          target_id TEXT NOT NULL,
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          PRIMARY KEY (source, item_key)
        );

        -- スキャン（全ジャンル1周）単位の結果
        CREATE TABLE IF NOT EXISTS scans (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          data_source TEXT NOT NULL,
          summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scan_id INTEGER NOT NULL,
          target_id TEXT NOT NULL,
          item_key TEXT NOT NULL,
          profit_jpy INTEGER NOT NULL,
          roi REAL NOT NULL,
          payload_json TEXT NOT NULL,
          FOREIGN KEY (scan_id) REFERENCES scans(id)
        );

        CREATE TABLE IF NOT EXISTS purchase_queue (
          item_key TEXT PRIMARY KEY,
          added_at TEXT NOT NULL,
          payload_json TEXT NOT NULL
        );

        -- 「仕入れない」で見送った出品（理由はマッチ精度の改善にも使う）
        CREATE TABLE IF NOT EXISTS dismissed (
          item_key TEXT PRIMARY KEY,
          dismissed_at TEXT NOT NULL,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL
        );

        -- SKU（仕入れた商品1点ごと）: 購入→到着→検品→出品→販売→発送→着金 を追跡
        CREATE TABLE IF NOT EXISTS skus (
          sku TEXT PRIMARY KEY,
          item_key TEXT,
          status TEXT NOT NULL,          -- ordered/arrived/ready/listed/sold/shipped/paid/rejected/returned
          title TEXT NOT NULL,
          genre TEXT,
          category TEXT,
          mercari_url TEXT,
          image_url TEXT,
          payload_json TEXT,             -- 仕入れ時点の候補データ（予測値の根拠）
          purchased_at TEXT NOT NULL,
          buy_price_jpy INTEGER NOT NULL,
          predicted_sell_usd REAL,
          predicted_profit_jpy INTEGER,
          breakeven_jpy INTEGER,
          arrived_at TEXT,
          inspected_at TEXT,
          inspection TEXT,               -- pass/fail
          defect_reason TEXT,
          weight_g INTEGER,
          listed_at TEXT,
          list_price_usd REAL,
          ebay_url TEXT,
          sold_at TEXT,
          sold_price_usd REAL,
          buyer_country TEXT,
          shipped_at TEXT,
          carrier TEXT,
          tracking TEXT,
          shipping_cost_jpy INTEGER,
          duty_jpy INTEGER,
          paid_at TEXT,
          payout_jpy INTEGER,            -- 換金後に口座へ入った円（eBay手数料・広告・換金差引後）
          returned_at TEXT,
          return_reason TEXT,
          notes TEXT,
          updated_at TEXT
        );

        -- 操作ログ（仕入れファネルの実績集計に使う）
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          kind TEXT NOT NULL,            -- queue/dismiss/purchase/arrive/inspect/list/sell/ship/pay/return
          ref TEXT
        );

        -- 画面から入力する設定値（現金残高など）
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        -- 仕入れ済み（旧：取引履歴。現在は skus を使用）
        CREATE TABLE IF NOT EXISTS purchases (
          item_key TEXT PRIMARY KEY,
          purchased_at TEXT NOT NULL,
          actual_buy_jpy INTEGER NOT NULL,
          payload_json TEXT NOT NULL
        );
        """
    )
    con.commit()


def upsert_target(con: sqlite3.Connection, target_id: str, name: str) -> None:
    con.execute(
        "INSERT INTO targets(id, name) VALUES(?, ?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
        (target_id, name),
    )
    con.commit()


@dataclass(frozen=True)
class Snapshot:
    id: int
    target_id: str
    source: str
    captured_at: str


def create_snapshot(
    con: sqlite3.Connection,
    *,
    target_id: str,
    source: str,
    meta_json: Optional[str] = None,
) -> Snapshot:
    captured_at = utc_now_iso()
    cur = con.execute(
        "INSERT INTO snapshots(target_id, source, captured_at, meta_json) "
        "VALUES(?, ?, ?, ?)",
        (target_id, source, captured_at, meta_json),
    )
    con.commit()
    return Snapshot(
        id=int(cur.lastrowid), target_id=target_id, source=source, captured_at=captured_at
    )



def mark_seen(con: sqlite3.Connection, *, source: str, item_key: str, target_id: str) -> str:
    """出品を記録し、初めて見た時刻(first_seen)を返す。"""
    now = utc_now_iso()
    con.execute(
        "INSERT INTO seen_items(source, item_key, target_id, first_seen, last_seen) VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(source, item_key) DO UPDATE SET last_seen=excluded.last_seen",
        (source, item_key, target_id, now, now),
    )
    row = con.execute(
        "SELECT first_seen FROM seen_items WHERE source=? AND item_key=?", (source, item_key)
    ).fetchone()
    return str(row["first_seen"])


def baseline_seen_at(con: sqlite3.Connection, target_id: str) -> Optional[str]:
    """このジャンルを初めてスキャンした時刻（それ以前からある出品は NEW 扱いしない）。"""
    row = con.execute("SELECT MIN(first_seen) AS t FROM seen_items WHERE target_id=?", (target_id,)).fetchone()
    return row["t"] if row and row["t"] else None


def log_event(con: sqlite3.Connection, kind: str, ref: str) -> None:
    con.execute("INSERT INTO events(ts, kind, ref) VALUES(?, ?, ?)", (utc_now_iso(), kind, ref))
