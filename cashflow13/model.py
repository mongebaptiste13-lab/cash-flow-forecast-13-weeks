"""13-week direct cash flow forecast with scenarios, revolver (RCF) logic and covenant tests.

Amounts are in thousands of euros. Week 1 is the first forecast week. Invoices issued in weeks
before week 1 (history) are collected during the horizon, which is what makes a 13-week forecast
reliable in its first weeks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

HORIZON = 13


@dataclass(frozen=True)
class Scenario:
    name: str
    sales_factor: float = 1.0               # multiplies forecast sales
    collection_delay_weeks: int = 0         # customers pay later
    supplier_acceleration_weeks: int = 0    # suppliers demand faster payment
    bad_debt_extra: float = 0.0             # additional share of invoices never collected
    ebitda_factor: float = 1.0              # scales LTM EBITDA used in the leverage covenant


SCENARIOS: Dict[str, Scenario] = {
    "Base": Scenario("Base"),
    "Downside": Scenario("Downside", sales_factor=0.88, collection_delay_weeks=1,
                         supplier_acceleration_weeks=1, bad_debt_extra=0.01, ebitda_factor=0.80),
    "Upside": Scenario("Upside", sales_factor=1.05, collection_delay_weeks=0, ebitda_factor=1.05),
    "Severe stress": Scenario("Severe stress", sales_factor=0.80, collection_delay_weeks=2,
                              supplier_acceleration_weeks=1, bad_debt_extra=0.03, ebitda_factor=0.65),
}


@dataclass(frozen=True)
class Company:
    name: str
    opening_cash: float
    sales_history: List[float]              # weekly invoiced sales before week 1 (oldest first)
    sales_forecast: List[float]             # weekly invoiced sales for weeks 1..13
    collection_curve: Dict[int, float]      # lag in weeks -> share of invoice collected
    bad_debt_pct: float
    purchases_pct_of_sales: float
    supplier_curve: Dict[int, float]        # lag in weeks -> share of purchases paid
    payroll: Dict[int, float]               # week -> amount
    rent_overheads_weekly: float
    taxes: Dict[int, float]                 # week -> VAT / corporate tax payments
    capex: Dict[int, float]
    term_loan_balance: float
    term_loan_amortisation: Dict[int, float]
    term_loan_rate: float
    interest_payment_weeks: List[int]
    rcf_limit: float
    rcf_drawn_opening: float
    rcf_rate: float
    min_operating_cash: float               # the RCF is drawn to keep cash at this level
    repay_threshold: float                  # RCF is repaid when cash exceeds this level
    covenant_min_liquidity: float           # cash + undrawn RCF must stay above this
    covenant_max_leverage: float            # net debt / LTM EBITDA, tested at quarter end
    ltm_ebitda: float
    leverage_test_weeks: List[int] = field(default_factory=lambda: [13])


def load_company(path: str | Path) -> Company:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("collection_curve", "supplier_curve", "payroll", "taxes", "capex", "term_loan_amortisation"):
        d[key] = {int(k): float(v) for k, v in d[key].items()}
    return Company(**d)


def _spread(values: np.ndarray, curve: Dict[int, float], offset: int, horizon: int) -> np.ndarray:
    """Cash received/paid in each forecast week from amounts dated `values[i]` (index i - offset = week)."""
    out = np.zeros(horizon)
    for i, amount in enumerate(values):
        week0 = i - offset                       # week index (0-based) of the invoice; negative = history
        for lag, share in curve.items():
            t = week0 + lag
            if 0 <= t < horizon:
                out[t] += amount * share
    return out


def _shift_curve(curve: Dict[int, float], shift: int) -> Dict[int, float]:
    return {max(lag + shift, 0): share for lag, share in curve.items()}


def run_forecast(c: Company, scenario: Scenario = SCENARIOS["Base"]) -> pd.DataFrame:
    """Return the weekly cash flow table for one scenario."""
    hist = np.asarray(c.sales_history, dtype=float)
    fcst = np.asarray(c.sales_forecast, dtype=float) * scenario.sales_factor
    sales = np.concatenate([hist, fcst])
    offset = len(hist)
    if len(fcst) != HORIZON:
        raise ValueError(f"sales_forecast must contain {HORIZON} weeks")
    if max(c.collection_curve) + scenario.collection_delay_weeks > offset + HORIZON:
        raise ValueError("collection curve longer than available history")

    collect_curve = _shift_curve(c.collection_curve, scenario.collection_delay_weeks)
    supplier_curve = _shift_curve(c.supplier_curve, -scenario.supplier_acceleration_weeks)
    collected_share = 1 - c.bad_debt_pct - scenario.bad_debt_extra

    receipts = _spread(sales, collect_curve, offset, HORIZON) * collected_share
    suppliers = _spread(sales * c.purchases_pct_of_sales, supplier_curve, offset, HORIZON)

    weeks = np.arange(1, HORIZON + 1)
    payroll = np.array([c.payroll.get(w, 0.0) for w in weeks])
    rent = np.full(HORIZON, c.rent_overheads_weekly)
    taxes = np.array([c.taxes.get(w, 0.0) for w in weeks])
    capex = np.array([c.capex.get(w, 0.0) for w in weeks])
    amort = np.array([c.term_loan_amortisation.get(w, 0.0) for w in weeks])

    rows = []
    cash = c.opening_cash
    rcf = c.rcf_drawn_opening
    term = c.term_loan_balance
    for i, w in enumerate(weeks):
        term_open = term
        term -= amort[i]
        interest_term = term_open * c.term_loan_rate / 4 if w in c.interest_payment_weeks else 0.0
        interest_rcf = rcf * c.rcf_rate / 52
        debt_service = amort[i] + interest_term + interest_rcf

        operating = receipts[i] - suppliers[i] - payroll[i] - rent[i] - taxes[i]
        net_before_financing = operating - capex[i] - debt_service
        cash_pre = cash + net_before_financing

        draw = repay = 0.0
        if cash_pre < c.min_operating_cash:                       # draw to restore the buffer
            draw = min(c.rcf_limit - rcf, c.min_operating_cash - cash_pre)
        elif cash_pre > c.repay_threshold and rcf > 0:           # repay with excess cash
            repay = min(rcf, cash_pre - c.repay_threshold)
        rcf = rcf + draw - repay
        closing = cash_pre + draw - repay

        rows.append({
            "week": int(w), "receipts": receipts[i], "supplier_payments": -suppliers[i],
            "payroll": -payroll[i], "rent_overheads": -rent[i], "taxes": -taxes[i],
            "operating_cash_flow": operating, "capex": -capex[i], "debt_service": -debt_service,
            "net_cash_flow_before_financing": net_before_financing,
            "rcf_draw": draw, "rcf_repayment": -repay, "opening_cash": cash, "closing_cash": closing,
            "rcf_drawn": rcf, "rcf_undrawn": c.rcf_limit - rcf, "term_loan": term,
        })
        cash = closing

    df = pd.DataFrame(rows).set_index("week")
    df["liquidity"] = df["closing_cash"] + df["rcf_undrawn"]
    df["liquidity_headroom"] = df["liquidity"] - c.covenant_min_liquidity
    df["net_debt"] = df["term_loan"] + df["rcf_drawn"] - df["closing_cash"]
    df["leverage"] = df["net_debt"] / (c.ltm_ebitda * scenario.ebitda_factor)
    return df


def run_all(c: Company, scenarios: Dict[str, Scenario] | None = None) -> Dict[str, pd.DataFrame]:
    return {name: run_forecast(c, s) for name, s in (scenarios or SCENARIOS).items()}


def covenant_report(c: Company, df: pd.DataFrame) -> dict:
    """Minimum liquidity every week, leverage at the test weeks."""
    liq_breach = df.index[df["liquidity"] < c.covenant_min_liquidity].tolist()
    lev_breach = [w for w in c.leverage_test_weeks if w in df.index and df.loc[w, "leverage"] > c.covenant_max_leverage]
    wmin = int(df["liquidity"].idxmin())
    return {
        "min_liquidity": float(df["liquidity"].min()), "min_liquidity_week": wmin,
        "min_liquidity_headroom": float(df["liquidity_headroom"].min()),
        "liquidity_breach_weeks": liq_breach,
        "leverage_tested": {w: float(df.loc[w, "leverage"]) for w in c.leverage_test_weeks if w in df.index},
        "leverage_breach_weeks": lev_breach,
        "peak_rcf_drawn": float(df["rcf_drawn"].max()),
        "closing_cash_week_13": float(df["closing_cash"].iloc[-1]),
        "compliant": not liq_breach and not lev_breach,
    }


def summary_table(c: Company, results: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {}
    for name, df in results.items():
        r = covenant_report(c, df)
        wk = c.leverage_test_weeks[-1]
        rows[name] = {
            "Cumulative net cash flow (before financing)": df["net_cash_flow_before_financing"].sum(),
            "Closing cash, week 13": r["closing_cash_week_13"],
            "Peak RCF drawn": r["peak_rcf_drawn"],
            "Minimum liquidity": r["min_liquidity"],
            "Week of minimum liquidity": r["min_liquidity_week"],
            "Liquidity headroom vs covenant": r["min_liquidity_headroom"],
            f"Net debt / EBITDA, week {wk}": r["leverage_tested"].get(wk, np.nan),
            "Covenants": "OK" if r["compliant"] else "BREACH",
        }
    return pd.DataFrame(rows).T
