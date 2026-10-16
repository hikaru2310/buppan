"""1件あたりの貢献利益。

事業計画シート「ユニットエコノミクス」と同じ費用構成にしている:
  売価 − eBay手数料(FVF+固定+海外手数料) − 広告費 − 米国関税(DDP) − 換金スプレッド
       − 返品・値下げ・為替引当 − 仕入価格 − 国際送料 − 梱包資材 − 作業費
  仕入ROI = 貢献利益 ÷ 仕入価格（シートのKPI定義）
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config, Target


@dataclass(frozen=True)
class ProfitEstimate:
    target_id: str
    target_name: str
    mercari_title: str
    mercari_url: str
    buy_price_jpy: int
    sell_price_usd: float
    sell_price_jpy: int  # 売価の円換算（売上）
    ebay_fee_usd: float  # FVF + 固定費 + 海外手数料
    ebay_fee_jpy: int
    payment_fixed_fee_jpy: int
    ad_fee_jpy: int
    duty_jpy: int  # 米国向けDDPの関税見込み（買い手比率で按分）
    received_jpy: int  # 手数料・広告・関税・換金スプレッド控除後の受取額
    fx_spread_jpy: int
    reserve_jpy: int  # 返品・値下げ・為替の引当
    shipping_cost_jpy: int
    packing_jpy: int  # 梱包資材 + ジャンル固有の雑費
    labor_jpy: int
    extra_buy_cost_jpy: int
    mercari_fee_jpy: int
    total_cost_jpy: int  # 仕入 + 送料 + 梱包 + 作業 + 購入手数料
    profit_jpy: int  # 貢献利益
    roi: float  # 仕入ROI = 貢献利益 ÷ 仕入価格
    margin: float  # 売上高貢献利益率
    breakeven_buy_jpy: int  # この仕入れ値までなら貢献利益がマイナスにならない


def jpy_from_usd(usd: float, *, jpy_per_usd: float) -> int:
    return int(round(usd * jpy_per_usd))


def estimate_profit(
    *,
    cfg: Config,
    target: Target,
    buy_price_jpy: int,
    sell_total_usd: float,
    mercari_title: str,
    mercari_url: str,
) -> ProfitEstimate:
    fx = cfg.fx.jpy_per_usd
    ebay, costs = cfg.fees.ebay, cfg.costs
    fvf_rate = target.final_value_fee_rate if target.final_value_fee_rate is not None else ebay.final_value_fee_rate
    duty_rate = target.us_duty_rate if target.us_duty_rate is not None else costs.us_duty_rate

    sell = float(sell_total_usd)
    ebay_fee_usd = sell * (fvf_rate + ebay.international_fee_rate) + ebay.payment_fixed_fee_usd
    ad_usd = sell * ebay.ad_rate
    duty_usd = sell * costs.us_buyer_share * duty_rate
    net_usd = max(sell - ebay_fee_usd - ad_usd - duty_usd, 0.0)
    gross_net_jpy = net_usd * fx
    received_jpy = int(round(gross_net_jpy * (1 - ebay.payout_fx_spread_rate)))
    fx_spread_jpy = int(round(gross_net_jpy)) - received_jpy
    reserve_jpy = int(round(sell * fx * costs.reserve_rate))

    mercari_fee_jpy = cfg.fees.mercari.buyer_fee_jpy + int(round(buy_price_jpy * cfg.fees.mercari.buyer_fee_rate))
    packing_jpy = costs.packing_material_jpy + target.extra_buy_cost_jpy
    other_costs = target.shipping_cost_jpy + packing_jpy + costs.labor_jpy + cfg.fees.mercari.buyer_fee_jpy
    total_cost = buy_price_jpy + mercari_fee_jpy + target.shipping_cost_jpy + packing_jpy + costs.labor_jpy
    profit = received_jpy - reserve_jpy - total_cost
    sell_jpy = jpy_from_usd(sell, jpy_per_usd=fx)
    roi = profit / buy_price_jpy if buy_price_jpy > 0 else -1.0
    breakeven = int((received_jpy - reserve_jpy - other_costs) / (1 + cfg.fees.mercari.buyer_fee_rate))

    return ProfitEstimate(
        target_id=target.id,
        target_name=target.name,
        mercari_title=mercari_title,
        mercari_url=mercari_url,
        buy_price_jpy=buy_price_jpy,
        sell_price_usd=sell,
        sell_price_jpy=sell_jpy,
        ebay_fee_usd=round(ebay_fee_usd, 2),
        ebay_fee_jpy=jpy_from_usd(ebay_fee_usd, jpy_per_usd=fx),
        payment_fixed_fee_jpy=jpy_from_usd(ebay.payment_fixed_fee_usd, jpy_per_usd=fx),
        ad_fee_jpy=jpy_from_usd(ad_usd, jpy_per_usd=fx),
        duty_jpy=jpy_from_usd(duty_usd, jpy_per_usd=fx),
        received_jpy=received_jpy,
        fx_spread_jpy=fx_spread_jpy,
        reserve_jpy=reserve_jpy,
        shipping_cost_jpy=target.shipping_cost_jpy,
        packing_jpy=packing_jpy,
        labor_jpy=costs.labor_jpy,
        extra_buy_cost_jpy=target.extra_buy_cost_jpy,
        mercari_fee_jpy=mercari_fee_jpy,
        total_cost_jpy=total_cost,
        profit_jpy=profit,
        roi=roi,
        margin=profit / sell_jpy if sell_jpy > 0 else 0.0,
        breakeven_buy_jpy=max(breakeven, 0),
    )


def sort_key(x: ProfitEstimate) -> tuple[float, int]:
    return (x.profit_jpy, x.roi)
