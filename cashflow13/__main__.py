"""Command line: python -m cashflow13 --input data/sample_company.json --out outputs"""
import argparse
from pathlib import Path

import pandas as pd

from .model import load_company, run_all, summary_table
from .report import export_excel, plot_overview


def main() -> None:
    p = argparse.ArgumentParser(description="13-week cash flow forecast with scenarios and covenant tests")
    p.add_argument("--input", default="data/sample_company.json")
    p.add_argument("--out", default="outputs")
    args = p.parse_args()

    c = load_company(args.input)
    results = run_all(c)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    export_excel(c, results, out / "cash_forecast_13_weeks.xlsx")
    plot_overview(c, results, out / "forecast_overview.png")

    pd.set_option("display.float_format", lambda x: f"{x:,.1f}")
    pd.set_option("display.width", 200)
    print(f"{c.name}\n")
    print(summary_table(c, results).T.to_string())
    print(f"\nFiles written to {out}/")


if __name__ == "__main__":
    main()
