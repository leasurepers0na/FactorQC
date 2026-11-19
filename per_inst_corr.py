#!/usr/bin/env python3
"""把 QC 报告里 daily_his_corr 失败的因子，按合约拆成逐合约相关系数。

用法：
  python tools/per_inst_corr.py \
    --daily /tmp/qc_daily_t1/factor.20221230.csv \
    --his   /tmp/qc_his/factor.20221230.csv \
    --report /tmp/qc_both_t1/qc_report.xlsx \
    --out /tmp/per_inst_corr.csv
"""
import argparse
import pandas as pd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", required=True)
    ap.add_argument("--his", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rep = pd.read_excel(a.report, sheet_name="all")
    fails = rep[rep.daily_his_corr.notna() & (rep.daily_his_corr <= 0.9999)]
    daily = pd.read_csv(a.daily, usecols=["timestamp", "instrumentID", "factorName", "value"])
    his = pd.read_csv(a.his, usecols=["timestamp", "instrumentID", "factorName", "value"])

    rows = []
    for _, r in fails.iterrows():
        f = r.factorName
        d = daily[daily.factorName == f].set_index(["timestamp", "instrumentID"])["value"]
        h = his[his.factorName == f].set_index(["timestamp", "instrumentID"])["value"]
        m = pd.concat([d.rename("d"), h.rename("h")], axis=1).dropna()
        for inst, g in m.groupby(level="instrumentID"):
            if len(g) < 10:
                continue
            rows.append((f, r.变体, inst, len(g), g.d.corr(g.h)))
    out = pd.DataFrame(rows, columns=["factorName", "variant", "instrumentID", "rows", "corr"])
    out.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(f"fails={len(fails)} rows={len(out)} -> {a.out}")
    bad = out[out["corr"] <= 0.9999]
    print(f"per-inst corr<=0.9999: {len(bad)}")
    print(bad.sort_values("corr").head(20).to_string(index=False))


if __name__ == "__main__":
    main()
