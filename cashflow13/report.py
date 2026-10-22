"""Charts and Excel export for the 13-week forecast."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .model import Company, summary_table

SCENARIO_COLORS = {"Base": "#1f4e79", "Downside": "#e08e0b", "Upside": "#4f9d69", "Severe stress": "#c0392b"}


def plot_overview(c: Company, results: Dict[str, pd.DataFrame], path: str | Path) -> None:
    base = results["Base"]
    fig, axes = plt.subplots(1, 3, figsize=(19, 5.6))

    ax = axes[0]
    inflow = base["receipts"]
    outflow_cols = ["supplier_payments", "payroll", "rent_overheads", "taxes", "capex", "debt_service"]
    colors = ["#c0392b", "#e67e22", "#f1c40f", "#8e7cc3", "#7f8c8d", "#34495e"]
    ax.bar(base.index, inflow, color="#4f9d69", label="Customer receipts")
    bottom = pd.Series(0.0, index=base.index)
    for col, colr in zip(outflow_cols, colors):
        ax.bar(base.index, base[col], bottom=bottom, color=colr, label=col.replace("_", " ").capitalize())
        bottom = bottom + base[col]
    ax.plot(base.index, base["closing_cash"], color="black", marker="o", label="Closing cash")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("Base case: weekly cash flows by category and closing cash (k EUR)")
    ax.set_xlabel("Week"); ax.set_xticks(base.index)
    ax.legend(fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12))

    ax = axes[1]
    for name, df in results.items():
        ax.plot(df.index, df["liquidity"], marker="o", ms=3, color=SCENARIO_COLORS.get(name), label=name)
    ax.axhline(c.covenant_min_liquidity, color="black", ls="--", label=f"Covenant minimum ({c.covenant_min_liquidity:,.0f})")
    ax.set_title("Liquidity (cash + undrawn RCF) by scenario (k EUR)")
    ax.set_xlabel("Week"); ax.set_xticks(base.index); ax.legend(fontsize=8)
    ax = axes[2]
    for name, df in results.items():
        ax.plot(df.index, df["leverage"], marker="o", ms=3, color=SCENARIO_COLORS.get(name), label=name)
    ax.axhline(c.covenant_max_leverage, color="black", ls="--", label=f"Covenant maximum ({c.covenant_max_leverage:.1f}x)")
    for w in c.leverage_test_weeks:
        ax.axvline(w, color="grey", lw=0.8, ls=":")
    ax.set_title("Net debt / LTM EBITDA by scenario (tested at week 13)")
    ax.set_xlabel("Week"); ax.set_xticks(base.index); ax.legend(fontsize=8)
    for a in axes:
        a.grid(alpha=0.25); a.spines["top"].set_visible(False); a.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def export_excel(c: Company, results: Dict[str, pd.DataFrame], path: str | Path) -> None:
    """Write one sheet per scenario plus a summary. Values only (no live formulas)."""
    path = Path(path)
    summary = summary_table(c, results)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary")
        for name, df in results.items():
            df.T.round(1).to_excel(xl, sheet_name=name[:31])
        wb = xl.book
        for ws in wb.worksheets:
            ws.column_dimensions["A"].width = 46
            for col in "BCDEFGHIJKLMN":
                ws.column_dimensions[col].width = 14
            ws.freeze_panes = "B2"
            for row in ws.iter_rows(min_row=2, min_col=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        cell.number_format = "#,##0;[Red]-#,##0"
        for ws in wb.worksheets:
            ws.sheet_properties.tabColor = "1F4E79"
