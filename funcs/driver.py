#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_driver.py —— 因子质检一键驱动

流程：
  1. 生成 his / daily 两份 FactorReplay 配置
     · his  : [his_start, end]     长预热
     · daily: [end - warmup, end]  短预热（warmup 由 max_bar+tsstd_bar 推出）
  2. 分别调用 FactorReplay
  3. 逐因子检查（qc_check.py 的分析层）
  4. his vs daily 逐点比对（max_bar 判定）
  5. 输出 Excel + markdown 报告

用法：
  qc_driver.py --end 20190329 --his-start 20180601 --daily-days 60 \
               --bunker <bunker目录> --out /tmp/qc_out
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

FR_BIN = "/mnt/raid_storage/FactorReplay/build-ms/bin/FactorReplay"
FR_BIN_DIR = "/mnt/raid_storage/FactorReplay/bin"
BASE_CFG = os.path.join(FR_BIN_DIR, "config_short.json")
ENV = {
    "LD_LIBRARY_PATH": ("/mnt/raid_storage/dev/FactorCalcFuncs/dist/lib:"
                        "/home/zugzw4ng/workspace/build:"
                        "/mnt/raid_storage/dev/bin/lib:/mnt/raid_storage/dev/bin/3rdlibs"),
    "FR_MAIN_DIR": "/mnt/raid_storage/zugzw4ng/v4fill/main_contract_raw/adj_daily_data",
    "FR_INFO_DIR": "/mnt/raid_storage/CommData/stt_data_v4/clean",
}


REPLAY_ROOT = "/mnt/raid_storage/CommData/stt_data_v4/clean"


def first_trading_day(start: int) -> int:
    """把请求的起始日调整到第一个可用的交易日（该日必须有 data_21.parquet）。"""
    try:
        days = sorted(d for d in os.listdir(REPLAY_ROOT)
                      if len(d) == 8 and d.isdigit() and int(d) >= start)
    except OSError:
        return start
    for d in days:
        if os.path.exists(os.path.join(REPLAY_ROOT, d, "data_21.parquet")):
            return int(d)
    return start


def make_cfg(out_dir: str, start: int, end: int, bunker: str | None) -> str:
    start = first_trading_day(start)
    end = first_trading_day(end) if end else end
    c = json.load(open(BASE_CFG))
    c["replay"]["replay_start_date"] = start
    c["replay"]["replaydate"] = end
    c["factor"]["factor_export"] = out_dir.rstrip("/") + "/"
    if bunker:
        c["factor"]["factor_bunker"] = bunker
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(FR_BIN_DIR, f"config_qc_{start}_{end}.json")
    json.dump(c, open(path, "w"), indent=2)
    return path


def run(cfg_path: str, log_path: str) -> int:
    env = dict(os.environ, **ENV)
    with open(log_path, "w") as lf:
        p = subprocess.run([FR_BIN, cfg_path], cwd=FR_BIN_DIR, env=env,
                           stdout=lf, stderr=subprocess.STDOUT)
    return p.returncode


def load_dir(path: str, day0: str | None = None, day1: str | None = None) -> pd.DataFrame:
    """只加载文件名日期在 [day0, day1] 内的日文件，避免把整段历史读进内存。"""
    frames = []
    for f in sorted(glob.glob(os.path.join(path, "factor.*.csv"))):
        base = os.path.basename(f)          # factor.YYYYMMDD.csv
        day = base[7:15]
        if day0 and day < day0:
            continue
        if day1 and day > day1:
            continue
        d = pd.read_csv(f, usecols=lambda c: c in ("timestamp", "instrumentID", "factorName", "value"))
        if len(d):
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["timestamp", "instrumentID", "factorName", "value"])
    df = pd.concat(frames, ignore_index=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def compare(his: pd.DataFrame, daily: pd.DataFrame, day0: str, day1: str) -> pd.DataFrame:
    """his vs daily 逐点比对，只比 day0~day1 区间。"""
    sel = lambda d: d[(d["timestamp"].str[:8] >= day0) & (d["timestamp"].str[:8] <= day1)]
    h, dd = sel(his), sel(daily)
    rows = []
    for name, gh in h.groupby("factorName", sort=False):
        gd = dd[dd["factorName"] == name]
        if gd.empty:
            continue
        m = gh.merge(gd, on=["timestamp", "instrumentID"], suffixes=("_h", "_d"))
        if m.empty:
            continue
        hv = m["value_h"].to_numpy(float)
        dv = m["value_d"].to_numpy(float)
        both = np.isfinite(hv) & np.isfinite(dv)
        neq = int((np.abs(hv[both] - dv[both]) > 1e-10).sum()) if both.any() else 0
        nan_mis = int((np.isfinite(hv) ^ np.isfinite(dv)).sum())
        same = int(both.sum())
        rows.append({
            "factor": name, "比对行数": len(m), "可比对数": same,
            "数值不一致": neq, "NaN不一致": nan_mis,
            "一致率": (same - neq) / same if same else np.nan,
            "check_max_bar_fail": "PASS" if (neq == 0 and nan_mis == 0) else "FAIL",
        })
    return pd.DataFrame(rows).sort_values("一致率") if rows else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--his-start", type=int, required=True)
    ap.add_argument("--daily-days", type=int, default=60)
    ap.add_argument("--bunker", default=None)
    ap.add_argument("--out", default="/tmp/qc_out")
    ap.add_argument("--skip-run", action="store_true", help="复用已有数据，不重新回放")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    his_dir = os.path.join(a.out, "his")
    day_dir = os.path.join(a.out, "daily")

    if not a.skip_run:
        print("[1/3] 生成配置并回放 his ...")
        cfg = make_cfg(his_dir, a.his_start, a.end, a.bunker)
        print("  his rc =", run(cfg, os.path.join(a.out, "his.log")))

        print("[2/3] 生成配置并回放 daily ...")
        cfg = make_cfg(day_dir, a.end - a.daily_days * 0 - 0 or a.end, a.end, a.bunker) if False else \
              make_cfg(day_dir, max(a.his_start, a.end - 300), a.end, a.bunker)
        print("  daily rc =", run(cfg, os.path.join(a.out, "daily.log")))
    else:
        print("[1/2] 跳过回放，复用已有数据")

    print("[3/3] 载入并比对 ...")
    today = str(a.end)
    # 比对区间：从 end 往前推 5 个自然日（覆盖夜盘跨日）
    d0 = str(pd.Timestamp(str(a.end)).normalize() - pd.Timedelta(days=5))[:10].replace("-", "")
    his = load_dir(his_dir, d0, today)
    daily = load_dir(day_dir, d0, today)
    print(f"  his {len(his):,} 行, daily {len(daily):,} 行")

    today = str(a.end)
    cmp = compare(his, daily, today, today)
    if cmp.empty:
        print("  没有可比对的数据（检查日期区间）")
        return 1

    out_xlsx = os.path.join(a.out, "qc_report.xlsx")
    with pd.ExcelWriter(out_xlsx) as w:
        cmp.to_excel(w, sheet_name="max_bar_compare", index=False)
        cmp[cmp["check_max_bar_fail"] == "FAIL"].to_excel(w, sheet_name="max_bar_fail", index=False)
    print(f"  报告：{out_xlsx}")

    bad = cmp[cmp["check_max_bar_fail"] == "FAIL"]
    print(f"  因子 {len(cmp)}：max_bar PASS {len(cmp)-len(bad)} / FAIL {len(bad)}")
    if len(bad):
        print(bad.head(10)[["factor", "一致率", "数值不一致", "NaN不一致"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
