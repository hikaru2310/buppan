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
    sell_price_jpy: int  # 売価をそのまま円換算した額（参考）
    ebay_fee_usd: float  # FVF + 固定費
    ebay_fee_jpy: int
    payment_fixed_fee_jpy: int
    received_jpy: int  # eBay手数料・換金スプレッド控除後の受取額
    fx_spread_jpy: int
    shipping_cost_jpy: int
    extra_buy_cost_jpy: int
    mercari_fee_jpy: int
    total_cost_jpy: int  # 総原価（仕入 + 送料 + 梱包 + 購入手数料）
    profit_jpy: int
    roi: float  # profit / 総原価
    breakeven_buy_jpy: int  # この仕入れ値までなら赤字にならない


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
    ebay = cfg.fees.ebay
    fvf_rate = target.final_value_fee_rate if target.final_value_fee_rate is not None else ebay.final_value_fee_rate

    ebay_fee_usd = sell_total_usd * fvf_rate + ebay.payment_fixed_fee_usd
    net_usd = max(sell_total_usd - ebay_fee_usd, 0.0)
    gross_net_jpy = net_usd * fx
    received_jpy = int(round(gross_net_jpy * (1 - ebay.payout_fx_spread_rate)))
    fx_spread_jpy = int(round(gross_net_jpy)) - received_jpy

    mercari_fee_jpy = cfg.fees.mercari.buyer_fee_jpy + int(round(buy_price_jpy * cfg.fees.mercari.buyer_fee_rate))
    other_costs = target.shipping_cost_jpy + target.extra_buy_cost_jpy + cfg.fees.mercari.buyer_fee_jpy
    total_cost = buy_price_jpy + mercari_fee_jpy + target.shipping_cost_jpy + target.extra_buy_cost_jpy
    profit = received_jpy - total_cost
    roi = profit / total_cost if total_cost > 0 else -1.0
    breakeven = int((received_jpy - other_costs) / (1 + cfg.fees.mercari.buyer_fee_rate))

    return ProfitEstimate(
        target_id=target.id,
        target_name=target.name,
        mercari_title=mercari_title,
        mercari_url=mercari_url,
        buy_price_jpy=buy_price_jpy,
        sell_price_usd=float(sell_total_usd),
        sell_price_jpy=jpy_from_usd(sell_total_usd, jpy_per_usd=fx),
        ebay_fee_usd=round(ebay_fee_usd, 2),
        ebay_fee_jpy=jpy_from_usd(ebay_fee_usd, jpy_per_usd=fx),
        payment_fixed_fee_jpy=jpy_from_usd(ebay.payment_fixed_fee_usd, jpy_per_usd=fx),
        received_jpy=received_jpy,
        fx_spread_jpy=fx_spread_jpy,
        shipping_cost_jpy=target.shipping_cost_jpy,
        extra_buy_cost_jpy=target.extra_buy_cost_jpy,
        mercari_fee_jpy=mercari_fee_jpy,
        total_cost_jpy=total_cost,
        profit_jpy=profit,
        roi=roi,
        breakeven_buy_jpy=max(breakeven, 0),
    )


def sort_key(x: ProfitEstimate) -> tuple[float, int]:
    return (x.roi, x.profit_jpy)
