from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from cashflow13 import SCENARIOS, Scenario, covenant_report, load_company, run_all, run_forecast, summary_table
from cashflow13.model import _spread
from cashflow13.report import export_excel, plot_overview

DATA = Path(__file__).parent.parent / "data" / "sample_company.json"


@pytest.fixture(scope="module")
def company():
    return load_company(DATA)


def test_collection_curve_sums_to_one(company):
    assert sum(company.collection_curve.values()) == pytest.approx(1.0)
    assert sum(company.supplier_curve.values()) == pytest.approx(1.0)


def test_spread_conserves_amounts_when_horizon_is_long_enough():
    sales = np.array([100.0, 200.0])
    out = _spread(sales, {1: 0.5, 2: 0.5}, offset=0, horizon=6)
    assert out.sum() == pytest.approx(300.0)
    # invoice of 100 in week 0 pays 50 in weeks 1 and 2; invoice of 200 in week 1 pays 100 in weeks 2 and 3
    assert out.tolist() == pytest.approx([0, 50, 150, 100, 0, 0])


def test_cash_roll_forward_identity(company):
    df = run_forecast(company)
    expected = df["opening_cash"] + df["net_cash_flow_before_financing"] + df["rcf_draw"] + df["rcf_repayment"]
    assert df["closing_cash"].to_numpy() == pytest.approx(expected.to_numpy())
    assert df["opening_cash"].iloc[0] == company.opening_cash
    assert df["opening_cash"].iloc[1:].to_numpy() == pytest.approx(df["closing_cash"].iloc[:-1].to_numpy())


def test_rcf_never_exceeds_limit_and_never_negative(company):
    for df in run_all(company).values():
        assert (df["rcf_drawn"] <= company.rcf_limit + 1e-9).all()
        assert (df["rcf_drawn"] >= -1e-9).all()


def test_rcf_is_drawn_when_cash_falls_below_buffer(company):
    stressed = replace(company, opening_cash=0, min_operating_cash=150, rcf_drawn_opening=0)
    df = run_forecast(stressed)
    assert df["rcf_draw"].sum() > 0
    assert (df["closing_cash"] >= 150 - 1e-9).all() or df["rcf_drawn"].max() == pytest.approx(stressed.rcf_limit)


def test_scenarios_are_ordered_on_liquidity(company):
    res = run_all(company)
    mins = {k: v["liquidity"].min() for k, v in res.items()}
    assert mins["Severe stress"] < mins["Downside"] < mins["Base"] < mins["Upside"]


def test_collection_delay_pushes_receipts_out(company):
    base = run_forecast(company)["receipts"]
    late = run_forecast(company, Scenario("Late", collection_delay_weeks=2))["receipts"]
    assert late.sum() < base.sum()
    assert late.iloc[:2].sum() < base.iloc[:2].sum()


def test_covenant_breach_detected_and_not_flagged_when_loose(company):
    res = run_all(company)
    assert covenant_report(company, res["Base"])["compliant"]
    severe = covenant_report(company, res["Severe stress"])
    assert not severe["compliant"] and severe["leverage_breach_weeks"] == [13]
    tight = replace(company, covenant_min_liquidity=5000)
    rep = covenant_report(tight, res["Base"])
    assert rep["liquidity_breach_weeks"] == list(range(1, 14))


def test_summary_table_has_one_column_per_scenario(company):
    t = summary_table(company, run_all(company))
    assert list(t.index) == list(SCENARIOS)
    assert t.loc["Severe stress", "Covenants"] == "BREACH"


def test_excel_export_and_chart(tmp_path, company):
    res = run_all(company)
    export_excel(company, res, tmp_path / "f.xlsx")
    plot_overview(company, res, tmp_path / "f.png")
    wb = load_workbook(tmp_path / "f.xlsx")
    assert wb.sheetnames == ["Summary"] + list(SCENARIOS)
    assert (tmp_path / "f.png").stat().st_size > 10_000


def test_forecast_requires_13_weeks(company):
    with pytest.raises(ValueError):
        run_forecast(replace(company, sales_forecast=[400] * 12))
