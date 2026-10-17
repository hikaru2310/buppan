"""eBay出品の下書き（英語タイトル・Item specifics・説明文）。

相場の根拠にした「実際に売れたeBayの商品タイトル」をベースに、状態のランクと "from JAPAN" を付ける。
説明文は日本からの中古品出品でよく使われる構成（状態・付属品・発送・返品）。最終確認・修正は人が行う。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .normalize import model_numbers, normalize

# メルカリの状態ID → eBayでの状態ランク表記
GRADE = {1: "[Unused]", 2: "[Near MINT]", 3: "[Exc+5]", 4: "[Exc+3]", 5: "[Exc]", 6: "[As-Is]"}
GRADE_TEXT = {
    1: "Unused item. No signs of use.",
    2: "Near mint condition with almost no signs of use.",
    3: "Excellent+++++ condition. Minor signs of use, not noticeable.",
    4: "Excellent+++ condition. Some light scratches and signs of use.",
    5: "Excellent condition. Visible scratches and signs of use.",
    6: "Sold as-is. Please check the photos carefully.",
}
BRANDS = ["Nikon", "Canon", "Pentax", "Olympus", "Contax", "Ricoh", "Fujifilm", "Sony", "Minolta", "Leica", "Mamiya",
          "Nintendo", "Sega", "Boss", "Maxon", "Ibanez", "Roland", "Korg", "Yamaha", "Seiko", "Casio", "Citizen"]
NOISE = re.compile(r"\b(excellent|exc\+*\d*|mint|near mint|n\.?mint|tested|working|w/|free shipping|fast shipping|from japan|japan|"
                   r"look|wow|rare|nice|clean|good|very|\+{2,}|!+)\b", re.I)
CAT_CHECK = {
    "カメラ・交換レンズ": ["Glass: no fungus, no haze, no scratches (light dust may exist)", "Aperture blades: work properly, no oil",
                       "Focus ring: smooth", "Shutter / meter (bodies): work properly at all speeds"],
    "ゲーム・限定品": ["Powers on and works properly (tested with a game)", "Screen: no dead pixels, no scratches",
                     "Battery cover / ports: included and clean"],
    "音響・楽器・電子機器": ["Tested and works properly", "Knobs and switches: no noise", "Power supply: not included unless pictured"],
    "その他型番商品": ["Works properly", "Please check the photos for details"],
}


def draft(sku: dict[str, Any]) -> dict[str, Any]:
    p: dict[str, Any] = {}
    try:
        p = json.loads(sku.get("payload_json") or "{}")
    except ValueError:
        pass
    comps = sorted(p.get("comps") or [], key=lambda c: c.get("score", 0), reverse=True)
    base = comps[0]["title"] if comps else sku.get("title", "")
    base = re.sub(r"\s{2,}", " ", NOISE.sub(" ", base)).strip(" -|,")
    cond = _cond_id(p.get("condition"))
    grade = GRADE.get(cond, "[Exc+4]")
    title = f"{grade} {base} from JAPAN"
    if len(title) > 80:
        title = f"{grade} {base}"[:80].rstrip()
    brand = next((b for b in BRANDS if b.lower() in base.lower()), "")
    models = sorted(model_numbers(normalize(base)))
    specifics = {"Brand": brand or "(brand)", "Model": " ".join(m.upper() for m in models) or "(model)",
                 "Country/Region of Manufacture": "Japan", "Condition": "Used" if cond != 1 else "New (Other)"}
    checks = CAT_CHECK.get(sku.get("category") or "", CAT_CHECK["その他型番商品"])
    desc = "\n".join([
        title, "",
        "[Condition]", GRADE_TEXT.get(cond, "Excellent condition. Please see the photos."),
        *[f"- {c}" for c in checks],
        f"- Notes: {sku.get('notes') or '(add details from inspection)'}", "",
        "[Included]", "- As pictured. Items not in the photos are not included.", "",
        "[Shipping]", "- Ships from Japan with tracking.",
        "- US buyers: duties are prepaid (DDP). You will not be charged extra on delivery.",
        "- Handling time: within 3 business days.", "",
        "[Returns]", "- 30-day returns accepted. Please contact us before opening a return.", "",
        "[About us]", "- We carefully inspect each item in Japan before shipping. Thank you for visiting our store!",
    ])
    return {"title": title, "title_len": len(title), "specifics": specifics, "description": desc,
            "custom_label": sku.get("sku"), "base_from": comps[0]["title"] if comps else None}


def _cond_id(label: Optional[str]) -> Optional[int]:
    m = {"新品・未使用": 1, "未使用に近い": 2, "目立った傷なし": 3, "やや傷あり": 4, "傷あり": 5, "状態が悪い": 6}
    return m.get(label or "")
