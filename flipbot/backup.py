"""取引記録（SQLite）の自動バックアップ。1日1回、data/backups/ に日付つきで保存し、古いものは消す。

app.backup_dir を設定すると、そこにも同じものを置く（例: iCloud Drive のフォルダ）。
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from .config import Config


def run(cfg: Config) -> Optional[Path]:
    src = Path(cfg.app.db_path)
    if not src.exists():
        return None
    out_dir = src.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}-{date.today():%Y%m%d}.db"
    if dst.exists():
        return dst  # 今日の分は作成済み
    con = sqlite3.connect(str(src))
    try:
        bak = sqlite3.connect(str(dst))
        with bak:
            con.backup(bak)  # 書き込み中でも整合の取れたコピーになる
        bak.close()
    finally:
        con.close()
    for old in sorted(out_dir.glob(f"{src.stem}-*.db"))[: -max(cfg.app.backup_keep, 1)]:
        old.unlink()
    if cfg.app.backup_dir:
        extra = Path(cfg.app.backup_dir).expanduser()
        extra.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, extra / dst.name)
        for old in sorted(extra.glob(f"{src.stem}-*.db"))[: -max(cfg.app.backup_keep, 1)]:
            old.unlink()
    return dst
