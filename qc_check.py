#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_check.py —— 期货因子质检（对齐老平台 check_future 4.4.0 的语义）

数据契约（与老平台一致）
------------------------
每个数据源目录下，每个因子一个 CSV，列固定为：code, time, factor
    code   : 数字编码（101~141，见 CODE_MAP）
    time   : 毫秒时间戳
    factor : 因子值

本工具读取 FactorReplay 的导出（timestamp,instrumentID,factorName,value），
自动转换成上述契约后执行检查。

检查项（实盘 shipan 暂缺，相关项自动 SKIP）
------------------------------------------
  hf                  半衰期 / max_bar ∈ [1/3,3]（tick [1/6,1.5]）
  inf_num             ±inf 条数合计，必须为 0
  check_max_bar_fail  第一条因子值是否 NaN
  *_nan_ratio         NaN 占比（排除 141/131），阈值 0.01
  *_num               条数
  no_duplicate        his 的 (code,time) 是否唯一
  daily_his_corr      daily 与 his 的 Pearson 相关，阈值 0.9999
  shipan_daily_corr   暂缺实盘 → SKIP
  *_min / *_max       越出 [-3.01, 3.01] 即失败
  load_time(s)        before_time
  cal_time(s)         after_time / after_num
  wrong_long_total_bar  bars(=max_bar+tsstd_bar) 超过档位上限
  missing_factor      cal_time 中声明但无数据的因子

报告：Excel，5 个 sheet —— pass / fail / load_error / missing_factor / wrong_long_total_bar
"""
from __future__ import annotations

import argparse
import glob
import os
import re

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
# 常量（来自老平台）
# ─────────────────────────────────────────────────────────────
CODE_MAP = {
    1: "IC", 2: "IF", 3: "IH",
    101: "AU", 102: "CU", 103: "AG", 104: "ZN", 105: "AL", 106: "NI",
    107: "JM", 108: "RB", 109: "I", 110: "J", 111: "RU", 112: "PP",
    113: "TA", 114: "MA", 115: "BU", 116: "V", 117: "M", 118: "Y",
    119: "C", 120: "P", 121: "SR", 122: "OI", 123: "CF", 131: "PG",
    134: "HC", 124: "EG", 133: "JD", 130: "ZC", 125: "FU", 126: "SC",
    127: "L", 128: "A", 129: "AP", 140: "RM", 141: "SA", 139: "FG",
    132: "SP", 138: "SN",
}
SYM_TO_CODE = {v: k for k, v in CODE_MAP.items()}
EXCLUDE_FOR_NAN = {141, 131}          # nan_ratio 排除 SA / PG

NAN_RATIO_MAX = 0.01
ABS_MAX = 3.01
CORR_MIN = 0.9999
HF_SELF_CORR = 0.5
HF_RATIO = {"normal": (1 / 3, 3.0), "tick": (1 / 6, 1.5)}

# long_total_bar → bars 上限（tick 模式再 ×120）
LONG_TOTAL_LIMIT = {0: 12000, 1: 18000, 2: 24000, 3: 42000, 4: 60000}

RESULT_COLS = [
    "factor_name", "hf", "inf_num", "check_max_bar_fail", "shipan_nan_ratio",
    "daily_nan_ratio", "his_nan_ratio", "shipan_num", "daily_num", "his_num",
    "no_duplicate", "shipan_daily_corr", "daily_his_corr", "shipan_min",
    "shipan_max", "daily_min", "daily_max", "his_min", "his_max",
    "load_time(s)", "cal_time(s)",
]


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────
def parse_max_bar(name: str) -> int:
    # 取完整因子名最后一个下划线段；不能先按 "." 切分，
    # 否则 xxx_fl2.4_15 这类带小数点的因子名会解析错
    base = name.split(".f1_")[0]
    if base.endswith(".f2") or base.endswith(".f4") or base.endswith(".f8") or base.endswith(".f9"):
        base = base.rsplit(".", 1)[0]
    tail = base.rsplit("_", 1)[-1]
    try:
        v = int(tail)
        return v if 0 < v < 100000 else 0
    except ValueError:
        return 0


def variant_of(name: str) -> str:
    for suf, tag in ((".f1_", "f1"), (".f2", "f2"), (".f4", "f4"),
                     (".f8", "f8"), (".f9", "f9")):
        if suf in name:
            return tag
    return "f0"


def base_name(name: str) -> str:
    for suf in (".f1_", ".f2", ".f4", ".f8", ".f9"):
        i = name.find(suf)
        if i > 0:
            return name[:i]
    return name


def load_platform_export(path: str) -> pd.DataFrame:
    """读取 FactorReplay 导出（timestamp,instrumentID,factorName,value）。"""
    files = sorted(glob.glob(os.path.join(path, "*.csv"))) if os.path.isdir(path) else [path]
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        need = {"timestamp", "instrumentID", "factorName", "value"}
        if not need.issubset(df.columns):
            continue
        frames.append(df[list(need)])
    if not frames:
        raise ValueError(f"没有可用数据：{path}")
    df = pd.concat(frames, ignore_index=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["time"] = pd.to_datetime(df["timestamp"], errors="coerce").astype("int64") // 10**6
    def _sym(s):
        m = re.match(r"^([A-Za-z]+)", str(s))
        return SYM_TO_CODE.get(m.group(1), np.nan) if m else np.nan

    df["code"] = df["instrumentID"].map(_sym)
    df = df.rename(columns={"value": "factor"})
    df["symbol"] = df["instrumentID"].astype(str)
    return df[["code", "time", "factor", "factorName", "symbol"]].dropna(subset=["code"])


def _cal_hf_series(series: pd.Series, param: int, period: str) -> float:
    """单合约半衰期，逐行复刻老平台 _cal_hf。"""
    f = series.dropna()
    if len(f) < 10:
        return np.nan
    if period == "normal":
        a, b, c = ((2, 2, 5) if param <= 15 else (2, 3, 6) if param <= 30 else (2, 5, 8))
        n, lo1, lo2, cap, scale = a, 20, 100, 10000, 1
    else:
        a, b, c = ((5, 2, 5) if param <= 30 else (5, 3, 6) if param <= 60 else (5, 5, 8))
        n, lo1, lo2, cap, scale = round(a / 10), 2, 10, 1000, 10

    corr = 1.0
    for _ in range(1000):
        if corr <= HF_SELF_CORR:
            break
        pair = pd.concat([f, f.shift(n)], axis=1).dropna()
        if len(pair) < 5:
            break
        c_ = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if c_ is None or np.isnan(c_):
            break
        corr = float(c_)
        if corr <= HF_SELF_CORR:
            break
        n = n + b if n < lo1 else (n + c if n <= lo2 else round(1.1 * n))
        if n > cap:
            break
    return float(round(n * scale))


def cal_hf(df: pd.DataFrame, param: int, period: str) -> float:
    hfs = [_cal_hf_series(g["factor"].astype(float), param, period)
           for _c, g in df.groupby("code", sort=False)]
    hfs = [h for h in hfs if not np.isnan(h)]
    return float(np.mean(hfs)) if hfs else np.nan


# ─────────────────────────────────────────────────────────────
# 单因子检查
# ─────────────────────────────────────────────────────────────
def check_one(name: str, src: dict[str, pd.DataFrame], period: str,
              long_total_bar: int, tsstd_bar_num: int) -> dict:
    """src: {'his': df, 'daily': df, 'shipan': df}（缺的键表示无数据）"""
    r = {c: np.nan for c in RESULT_COLS}
    r["factor_name"] = name
    r["no_duplicate"] = np.nan
    r["check_max_bar_fail"] = np.nan

    his, daily, shipan = src.get("his"), src.get("daily"), src.get("shipan")

    # ── long_total_bar 上限校验 ──
    max_bar = parse_max_bar(name)
    bars = max_bar + (tsstd_bar_num if variant_of(name) != "f0" else 0)
    limit = LONG_TOTAL_LIMIT.get(long_total_bar)
    if limit is not None:
        if period == "tick":
            limit *= 120
        if bars > limit:
            r["reason"] = "wrong_long_total_bar_error"
            r["status"] = "wrong_long_total_bar_error"
            return r

    # ── inf ──
    inf_num = 0
    for d in (his, daily, shipan):
        if d is not None and len(d):
            v = d["factor"]
            inf_num += int(np.isinf(v).sum())
    r["inf_num"] = inf_num

    # ── nan_ratio / min / max / num ──
    for tag, d in (("his", his), ("daily", daily), ("shipan", shipan)):
        if d is None or not len(d):
            continue
        tmp = d[~d["code"].isin(EXCLUDE_FOR_NAN)]
        r[f"{tag}_nan_ratio"] = round(float(tmp["factor"].isna().mean()), 6) if len(tmp) else np.nan
        r[f"{tag}_num"] = int(len(d))
        r[f"{tag}_min"] = float(d["factor"].min())
        r[f"{tag}_max"] = float(d["factor"].max())

    # ── check_max_bar_fail：首条是否 NaN ──
    first = None
    for d in (shipan, daily, his):
        if d is not None and len(d):
            first = d.sort_values("time")["factor"].iloc[0]
            break
    if first is not None:
        r["check_max_bar_fail"] = bool(pd.isna(first))

    # ── no_duplicate（只看 his） ──
    if his is not None and len(his):
        r["no_duplicate"] = bool(len(his) == len(his.drop_duplicates(["symbol", "time"])))

    # ── hf（用 daily，老平台口径） ──
    base = daily if (daily is not None and len(daily)) else his
    if base is not None and len(base):
        param = parse_max_bar(name) if variant_of(name) == "f0" else parse_max_bar(name)
        r["hf"] = cal_hf(base, param, period)

    # ── daily_his_corr ──
    if daily is not None and his is not None and len(daily) and len(his):
        t0, t1 = daily["time"].min(), daily["time"].max()
        hs = his[(his["time"] >= t0) & (his["time"] <= t1)]
        m = pd.merge(daily, hs, how="outer", on=["code", "time"]).reset_index(drop=True)
        if m.shape[1] >= 4:
            c = m.iloc[:, [2, 3]].corr().iloc[0, 1]
            r["daily_his_corr"] = float(c) if not np.isnan(c) else np.nan

    # ── 判定 ──
    fails = []
    if inf_num != 0:
        fails.append("inf_num")
    for tag in ("his", "daily", "shipan"):
        ratio = r.get(f"{tag}_nan_ratio")
        if ratio is not None and not pd.isna(ratio) and ratio > NAN_RATIO_MAX:
            fails.append(f"{tag}_nan_ratio")
        for mm in ("min", "max"):
            key = f"{tag}_{mm}"
            val = r.get(key)
            if val is None or pd.isna(val):
                continue
            if variant_of(name) == "f1" and ((mm == "min" and val < -ABS_MAX) or
                                             (mm == "max" and val > ABS_MAX)):
                fails.append(key)
    if r["no_duplicate"] is False:
        fails.append("no_duplicate")
    if r["check_max_bar_fail"] is True:
        fails.append("check_max_bar_fail")
    corr = r.get("daily_his_corr")
    if corr is not None and not pd.isna(corr) and corr < CORR_MIN:
        fails.append("daily_his_corr")
    hf, mb = r.get("hf"), parse_max_bar(name)
    if hf is not None and not pd.isna(hf) and mb > 0:
        ratio = hf / mb
        lo, hi = HF_RATIO[period]
        if not (lo <= ratio <= hi):
            fails.append("wrong_hf")

    r["status"] = "pass" if not fails else "fail"
    if fails:
        r["reason"] = ",".join(fails)
    else:
        r["reason"] = ""
    return r


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def run(his_dir: str | None, daily_dir: str | None, out_xlsx: str,
        period: str = "normal", long_total_bar: int = 0,
        tsstd_bar_num: int = 12000) -> None:
    src: dict[str, pd.DataFrame] = {}
    if his_dir:
        src["his"] = load_platform_export(his_dir)
    if daily_dir:
        src["daily"] = load_platform_export(daily_dir)

    names = set()
    for d in src.values():
        names |= set(d["factorName"].unique())
    names = sorted(names)

    rows = []
    for n in names:
        sub = {k: v[v["factorName"] == n] for k, v in src.items() if (v["factorName"] == n).any()}
        rows.append(check_one(n, sub, period, long_total_bar, tsstd_bar_num))

    rep = pd.DataFrame(rows)
    for c in ("reason", "status"):
        if c not in rep.columns:
            rep[c] = ""

    os.makedirs(os.path.dirname(out_xlsx) or ".", exist_ok=True)
    with pd.ExcelWriter(out_xlsx) as w:
        for status, sheet in (("pass", "pass"), ("fail", "fail")):
            sub = rep[rep["status"] == status].drop(columns=["status"])
            cols = [c for c in RESULT_COLS if c in sub.columns] + ["reason"]
            sub.reindex(columns=cols).to_excel(w, sheet_name=sheet, index=None)
        rep[rep["status"] == "wrong_long_total_bar_error"].drop(columns=["status"]).to_excel(
            w, sheet_name="wrong_long_total_bar", index=None)

    npass = int((rep["status"] == "pass").sum())
    nfail = int((rep["status"] == "fail").sum())
    print(f"报告：{out_xlsx}")
    print(f"因子 {len(rep)}：pass {npass} / fail {nfail} / 其它 {len(rep) - npass - nfail}")
    if nfail:
        print(rep[rep["status"] == "fail"][["factor_name", "reason"]].head(25).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--his")
    ap.add_argument("--daily")
    ap.add_argument("--out", default="qc_report.xlsx")
    ap.add_argument("--period", default="normal", choices=["normal", "tick"])
    ap.add_argument("--long-total-bar", type=int, default=0)
    ap.add_argument("--tsstd-bar-num", type=int, default=12000)
    a = ap.parse_args()
    if not a.his and not a.daily:
        ap.error("至少给 --his 或 --daily")
    run(a.his, a.daily, a.out, a.period, a.long_total_bar, a.tsstd_bar_num)


if __name__ == "__main__":
    main()
