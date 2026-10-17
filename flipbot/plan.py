"""事業計画マスター（Googleスプレッドシート）との同期。

数値の正本はシート（意思決定ログ 2026-07-21「スプレッドシートを数値の正本」）。
ツールは計画値をシートから読み、実績はツール内のデータ（候補・仕入れ・SKU）から計算する。
シートは「リンクを知っている人は閲覧可」なら認証なしで xlsx として読める。
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from .config import Config

CACHE = Path("./data/plan_cache.json")


def _cell(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip()
    return v


def _rows(ws) -> list[list[Any]]:
    return [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]


def _table(rows: list[list[Any]], first_header: str, *, start: int = 0) -> tuple[list[dict[str, Any]], int]:
    """first_header で始まる見出し行を探し、空行までを dict の配列にする。"""
    for i in range(start, len(rows)):
        if rows[i] and rows[i][0] == first_header:
            header = [str(h) if h not in (None, "") else f"col{j}" for j, h in enumerate(rows[i])]
            out = []
            for r in rows[i + 1:]:
                if not any(v not in (None, "") for v in r):
                    break
                out.append({header[j]: r[j] for j in range(min(len(header), len(r))) if not header[j].startswith("col")})
            return out, i
    return [], -1


def parse_workbook(data: bytes) -> dict[str, Any]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    sheets = {ws.title: _rows(ws) for ws in wb.worksheets}

    def t(sheet: str, header: str, start: int = 0) -> list[dict[str, Any]]:
        return _table(sheets.get(sheet, []), header, start=start)[0]

    funnel_rows = sheets.get("仕入れファネル", [])
    _, fi = _table(funnel_rows, "工程")
    cat_rows = sheets.get("カテゴリ別採算", [])
    gate_rows = sheets.get("ステージゲート", [])
    dash_rows = sheets.get("ダッシュボード", [])
    return {
        "title": "AI物販_事業計画マスター",
        "assumptions": t("前提条件", "区分"),
        "monthly": [r for r in t("12ヶ月計画", "月") if isinstance(r.get("月"), (int, float))],
        "kpi_plan": [r for r in t("KPI実績", "月") if isinstance(r.get("月"), (int, float))],
        "unit_economics": t("ユニットエコノミクス", "項目"),
        "scenarios": t("シナリオ分析", "シナリオ"),
        "categories": [r for r in t("カテゴリ別採算", "カテゴリ") if r.get("カテゴリ") and "合計" not in str(r.get("カテゴリ"))],
        "allocation_rules": t("カテゴリ別採算", "ルール"),
        "funnel": t("仕入れファネル", "工程"),
        "funnel_gates": _table(funnel_rows, "月", start=max(fi, 0))[0],
        "risks": t("リスク管理", "リスク"),
        "stage_gates": t("ステージゲート", "時点"),
        "auto_stop": t("ステージゲート", "条件"),
        "decision_rules": t("ダッシュボード", "経営判断"),
        "decisions": t("意思決定ログ", "日付"),
        "tasks": [r for r in t("タスク管理", "ID") if r.get("ID")],
        "roadmap": t("開発ロードマップ", "優先度"),
        "genre_db": [r for r in t("当たりジャンル商品DB", "ID") if r.get("ID")],
        "benchmarks": [r for r in t("競合ベンチマーク", "ID") if r.get("ID")],
        "_has": {k: bool(v) for k, v in (("dashboard", dash_rows), ("gates", gate_rows), ("categories", cat_rows))},
    }


def _download(sheet_id: str) -> bytes:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if not data.startswith(b"PK"):
        raise RuntimeError("シートを読めませんでした（共有設定が「リンクを知っている人は閲覧可」になっているか確認）")
    return data


def load(cfg: Config, *, force: bool = False) -> dict[str, Any]:
    """キャッシュが新しければそれを、古ければシートから取り直す。失敗時は前回キャッシュ。"""
    cached: Optional[dict[str, Any]] = None
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    if not cfg.plan.sheet_id:
        return cached or {"error": "config.yaml の plan.sheet_id が未設定です"}
    fresh = cached and time.time() - cached.get("_synced_ts", 0) < cfg.plan.refresh_hours * 3600
    if fresh and not force:
        return cached  # type: ignore[return-value]
    try:
        plan = parse_workbook(_download(cfg.plan.sheet_id))
        plan["_synced_ts"] = time.time()
        plan["_synced_at"] = time.strftime("%Y-%m-%d %H:%M")
        plan["sheet_url"] = f"https://docs.google.com/spreadsheets/d/{cfg.plan.sheet_id}/edit"
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        return plan
    except Exception as e:  # noqa: BLE001
        if cached:
            cached["error"] = f"最新版の取得に失敗したため前回の同期内容を表示しています: {e}"
            return cached
        return {"error": f"計画シートを読めませんでした: {e}"}


def month_index(cfg: Config, today: Optional[date] = None) -> int:
    """計画の何か月目か（1始まり。開始前は0）。"""
    today = today or date.today()
    y, m = (int(x) for x in cfg.plan.start_month.split("-"))
    return (today.year - y) * 12 + (today.month - m) + 1


def month_label(cfg: Config, idx: int) -> str:
    y, m = (int(x) for x in cfg.plan.start_month.split("-"))
    total = y * 12 + (m - 1) + (idx - 1)
    return f"{total // 12}/{total % 12 + 1}"


def category_min_profit(plan: dict[str, Any], category: Optional[str]) -> Optional[int]:
    for c in plan.get("categories", []):
        if category and c.get("カテゴリ") == category and isinstance(c.get("貢献利益下限"), (int, float)):
            return int(c["貢献利益下限"])
    return None


def category_row(plan: dict[str, Any], category: Optional[str]) -> dict[str, Any]:
    return next((c for c in plan.get("categories", []) if category and c.get("カテゴリ") == category), {})


def apply_assumptions(cfg: Config, plan: dict[str, Any]) -> list[str]:
    """計画シート「前提条件」に該当する行があれば、その基準値で config の前提を上書きする（数値の正本はシート）。
    対応する項目名の例: 「米国の買い手比率」「米国関税率」「為替（円/ドル）」。"""
    applied: list[str] = []
    for r in plan.get("assumptions", []) or []:
        name = str(r.get("項目") or "")
        v = r.get("基準（主計画）")
        if not isinstance(v, (int, float)):
            continue
        unit = str(r.get("単位") or "")
        rate = v / 100 if ("%" in unit or v > 1) else v
        if "米国" in name and ("比率" in name or "割合" in name):
            cfg.costs.us_buyer_share = rate
            applied.append(f"米国の買い手比率={rate:.0%}")
        elif "関税" in name:
            cfg.costs.us_duty_rate = rate
            applied.append(f"米国関税率={rate:.1%}")
        elif "為替" in name and 50 < v < 400 and not cfg.fx.auto:
            cfg.fx.jpy_per_usd = float(v)
            applied.append(f"為替={v}円/ドル")
    return applied
