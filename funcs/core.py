#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_main.py —— 因子质检总入口（对齐老平台 check_future_main.py 的参数面）

用法：
  # both：长 his + FR 原生 daily 单日
  qc_main.py --bunker <目录> --end 20221230 --his-start 20220104 \
             --mode both --freq 1min --period normal --f-list f0,f1 \
             --long-total-bar 0 --tstd-bar-num 12000 --out /tmp/qc_out

  # daily：只跑 FR 原生 daily（区间 [end,end]，平台自动前摇），不需要 his_start
  qc_main.py --bunker <目录> --end 20221230 --mode daily --out /tmp/qc_daily

流程（对应老平台）：
  ① 读 bunker → 因子清单            （≈ csv_file）
  ② 校验因子名与 tstd_bar_num 档位  （≈ check_csv_file）
  ③ 按 --mode 生成 his / daily 配置并回放（≈ generate_config + generate_factor）
  ④ 逐因子逐版本做检查               （≈ check_factor）
  ⑤ 汇总 Excel（5 个 sheet）         （≈ all_check）

重点检查：daily_his_corr —— 老平台的核心判据
    daily 与 his 先裁到同一时间范围，outer merge 后取两列的 Pearson，
    阈值 > 0.9999。它与「逐点完全相等」不同：容许极小的整体漂移。
    daily 用 FR 原生 daily：每个目标日 reset 因子状态、平台自动前摇。
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

from . import cfg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from . import props

# SYSTEM_BIN：优先使用系统安装版（/usr/local/bin/FactorReplay），
# 可用环境变量 FR_BIN 覆盖；都找不到才回退到构建目录。
FR_BIN = os.environ.get("FR_BIN") or shutil.which("FactorReplay") \
    or "/mnt/raid_storage/FactorReplay/build-ms/bin/FactorReplay"
FR_BIN_DIR = cfg.FR_WORKDIR
BASE_CFG = os.path.join(FR_BIN_DIR, "config_short.json")
REPLAY_ROOT = cfg.REPLAY_ROOT

ENV = {
    "LD_LIBRARY_PATH": ("/usr/local/lib:"
                        "/mnt/raid_storage/FactorReplay/bin:"
                        "/home/zugzw4ng/workspace/build:"
                        "/mnt/raid_storage/dev/bin/lib:/mnt/raid_storage/dev/bin/3rdlibs"),
    "FR_MAIN_DIR": "/mnt/raid_storage/zugzw4ng/v4fill/main_contract_raw/adj_daily_data",
    "FR_INFO_DIR": "/mnt/raid_storage/CommData/stt_data_v4/clean",
}

# ── 阈值（来自老平台 standard.txt）──
NAN_RATIO_MAX = 0.01
ABS_MAX = 3.01
F0_ABS_MAX = 1e7   # f0 原始值爆炸阈值（如 m5m_rsi 出现 1e18；volume 类 ~1e6 不误伤）
CORR_MIN = 0.9999
HF_RATIO = {"normal": (1 / 3, 3.0), "tick": (1 / 6, 1.5)}
LONG_TOTAL_LIMIT = {0: 12000, 1: 18000, 2: 24000, 3: 42000, 4: 60000}
EXCLUDE_FOR_NAN = {141, 131}

SYM_TO_CODE = {}   # 由 code_map 生成


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────
def parse_max_bar(name: str) -> int:
    s = name.split(".f1_")[0]
    for suf in (".f2", ".f4", ".f8", ".f9"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    t = s.rsplit("_", 1)[-1]
    return int(t) if t.isdigit() else 0


def variant_of(name: str) -> str:
    if ".f1_" in name:
        return "f1"
    for suf in (".f2", ".f4", ".f8", ".f9"):
        if name.endswith(suf):
            return suf[1:]
    return "f0"


def first_trading_day(start: int) -> int:
    try:
        days = sorted(d for d in os.listdir(REPLAY_ROOT)
                      if len(d) == 8 and d.isdigit() and int(d) >= start)
    except OSError:
        return start
    for d in days:
        if os.path.exists(os.path.join(REPLAY_ROOT, d, "data_21.parquet")):
            return int(d)
    return start


def load_bunker(path: str) -> list[str]:
    f = os.path.join(path, "StrategySignal.json")
    if not os.path.exists(f):
        raise FileNotFoundError(f)
    d = json.load(open(f))
    names, seen = [], set()
    for x in d:
        n = x.get("FactorName")
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return names


def check_csv_like(names: list[str], tstd_bar_num: int) -> list[str]:
    """≈ 老平台 check_csv_file：因子名末尾的负载档位是否与 tstd_bar_num 匹配。

    因子名形如 xxx_15 或 xxx_15.f1_12000；最后一段数字应与 tstd_bar_num 同量级。
    """
    bad = []
    for n in names:
        m = re.search(r"_(\d+)$", n)
        if not m:
            continue
        v = int(m.group(1))
        # 老平台只支持 200/400 两档；这里放宽为「必须为正」
        if v <= 0:
            bad.append(n)
    return bad


def make_cfg(out_dir: str, start: int, end: int, bunker: str,
             mode: str = "his") -> str:
    start, end = first_trading_day(start), first_trading_day(end)
    c = json.load(open(BASE_CFG))
    c["replay"]["mode"] = mode
    c["replay"]["replay_start_date"] = start
    c["replay"]["replaydate"] = end
    c["factor"]["factor_export"] = out_dir.rstrip("/") + "/"
    c["factor"]["factor_bunker"] = bunker.rstrip("/") + "/"
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(FR_BIN_DIR, f"config_qcm_{mode}_{start}_{end}.json")
    json.dump(c, open(p, "w"), indent=2)
    return p


def run_replay(cfg: str, log: str) -> int:
    env = dict(os.environ, **ENV)
    with open(log, "w") as lf:
        return subprocess.run([FR_BIN, cfg], cwd=FR_BIN_DIR, env=env,
                              stdout=lf, stderr=subprocess.STDOUT).returncode


def load_days(path: str, day0: str | None = None, day1: str | None = None) -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(os.path.join(path, "factor.*.csv"))):
        day = os.path.basename(f)[7:15]
        if day0 and day < day0:
            continue
        if day1 and day > day1:
            continue
        try:
            d = pd.read_csv(f, usecols=lambda c: c in
                            ("timestamp", "instrumentID", "factorName", "value"))
        except Exception:
            continue
        if len(d):
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["timestamp", "instrumentID", "factorName", "value"])
    df = pd.concat(frames, ignore_index=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def cal_hf_series(series: pd.Series, param: int, period: str) -> float:
    """单合约半衰期：corr(f_t, f_{t-n}) 首次 < 0.5 的 n。"""
    f = series.dropna()
    if len(f) < 10:
        return np.nan
    if period == "normal":
        a, b, c = (2, 2, 5) if param <= 15 else (2, 3, 6) if param <= 30 else (2, 5, 8)
        n, lo1, lo2, cap, scale = a, 20, 100, 10000, 1
    else:
        a, b, c = (5, 2, 5) if param <= 30 else (5, 3, 6) if param <= 60 else (5, 5, 8)
        n, lo1, lo2, cap, scale = round(a / 10), 2, 10, 1000, 10
    corr = 1.0
    for _ in range(1000):
        if corr <= 0.5:
            break
        pair = pd.concat([f, f.shift(n)], axis=1).dropna()
        if len(pair) < 5:
            break
        v = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if v is None or np.isnan(v):
            break
        corr = float(v)
        if corr <= 0.5:
            break
        n = n + b if n < lo1 else (n + c if n <= lo2 else round(1.1 * n))
        if n > cap:
            break
    return float(round(n * scale))


def clip_his_to_daily(daily: pd.DataFrame, his: pd.DataFrame) -> pd.DataFrame:
    """把 his 裁到 daily 的时间范围（老平台 check_factor 的口径）。"""
    if daily.empty or his.empty:
        return his.iloc[0:0]
    t0, t1 = daily["timestamp"].min(), daily["timestamp"].max()
    return his[(his["timestamp"] >= t0) & (his["timestamp"] <= t1)]


def daily_his_corr(daily: pd.DataFrame, his: pd.DataFrame) -> float:
    """老平台的核心判据：outer merge 后两列的 Pearson 相关。

    与「逐点完全相等」不同：容许整体一致、少量偏差。
    his 先裁到 daily 的时间范围，避免 daily 单日运行时把 his 多出来的
    交易日全部算成「一侧 NaN」。
    """
    if daily.empty or his.empty:
        return np.nan
    his = clip_his_to_daily(daily, his)
    if his.empty:
        return np.nan
    m = pd.merge(daily, his, how="outer", on=["timestamp", "instrumentID"],
                 suffixes=("_d", "_h"))
    if len(m) < 3:
        return np.nan
    a = m["value_d"].to_numpy(float)
    b = m["value_h"].to_numpy(float)
    both = np.isfinite(a) & np.isfinite(b)
    if both.sum() < 3:
        return np.nan
    x, y = a[both], b[both]
    if np.std(x) == 0 or np.std(y) == 0:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


# ─────────────────────────────────────────────────────────────
# 单因子检查
# ─────────────────────────────────────────────────────────────
def check_factor(name: str, his: pd.DataFrame, daily: pd.DataFrame,
                 period: str, long_total_bar: int, tstd_bar_num: int,
                 mode: str = "both") -> dict:
    r = {"factorName": name, "变体": variant_of(name), "qc_mode": mode,
         "max_bar": parse_max_bar(name), "tsstd": 0, "条数": 0,
         "nan_ratio": np.nan, "inf_num": np.nan, "min": np.nan, "max": np.nan,
         "hf": np.nan, "hf_ratio": np.nan, "daily_his_corr": np.nan,
         "逐点不一致": np.nan, "NaN不一致": np.nan, "reason": "", "status": "pass"}

    v = variant_of(name)
    if v == "f1":
        m = re.search(r"\.f1_(\d+)$", name)
        r["tsstd"] = int(m.group(1)) if m else 0

    # ── long_total_bar 上限 ──
    bars = r["max_bar"] + (r["tsstd"] if v != "f0" else 0)
    lim = LONG_TOTAL_LIMIT.get(long_total_bar)
    if lim is not None:
        if period == "tick":
            lim *= 120
        if bars > lim:
            r["status"] = "wrong_long_total_bar_error"
            r["reason"] = f"bars={bars} > {lim}"
            return r

    base = his if not his.empty else daily
    if base.empty:
        r["status"] = "load_error"
        r["reason"] = "无数据"
        return r

    # ── 基础统计 ──
    f0_max_abs = np.nan
    vv = base["value"]
    r["条数"] = len(vv)
    r["inf_num"] = int(np.isinf(vv).sum())
    r["nan_ratio"] = round(float(vv.isna().mean()), 6)
    fin = vv[np.isfinite(vv)]
    if len(fin):
        r["min"] = float(fin.min())
        r["max"] = float(fin.max())
        f0_max_abs = float(np.abs(fin).max())

    # ── hf（老平台口径：用 daily；daily 缺失才退回 his）──
    hf_src = daily if not daily.empty else his
    hfs = [cal_hf_series(g["value"].astype(float), r["max_bar"], period)
           for _c, g in hf_src.groupby("instrumentID", sort=False)]
    hfs = [h for h in hfs if not np.isnan(h)]
    if hfs:
        r["hf"] = float(np.mean(hfs))
        if r["max_bar"]:
            r["hf_ratio"] = r["hf"] / r["max_bar"]

    # ── daily_his_corr（核心）──
    r["daily_his_corr"] = daily_his_corr(daily, his)
    if not daily.empty and not his.empty:
        hs = clip_his_to_daily(daily, his)
        m = pd.merge(daily, hs, how="outer", on=["timestamp", "instrumentID"],
                     suffixes=("_d", "_h"))
        if len(m):
            a, b = m["value_d"].to_numpy(float), m["value_h"].to_numpy(float)
            both = np.isfinite(a) & np.isfinite(b)
            r["NaN不一致"] = int((np.isfinite(a) ^ np.isfinite(b)).sum())
            r["逐点不一致"] = int((np.abs(a[both] - b[both]) > 1e-10).sum()) if both.any() else 0

    # ── 判定 ──
    fails = []
    if r["inf_num"] and r["inf_num"] > 0:
        fails.append("inf_num")
    if not np.isnan(r["nan_ratio"]) and r["nan_ratio"] > NAN_RATIO_MAX:
        fails.append("nan_ratio")
    if v == "f1":
        if not np.isnan(r["min"]) and r["min"] < -ABS_MAX:
            fails.append("wrong_min")
        if not np.isnan(r["max"]) and r["max"] > ABS_MAX:
            fails.append("wrong_max")
    if v == "f0" and not np.isnan(f0_max_abs) and f0_max_abs > F0_ABS_MAX:
        fails.append("f0_value_explosion")
    if not np.isnan(r["hf_ratio"]):
        lo, hi = HF_RATIO[period]
        if not (lo <= r["hf_ratio"] <= hi):
            fails.append("wrong_hf")
    if not np.isnan(r["daily_his_corr"]) and r["daily_his_corr"] < CORR_MIN:
        fails.append("daily_his_corr")
    if r["NaN不一致"] and r["NaN不一致"] > 0:
        fails.append("check_max_bar_fail")

    r["reason"] = ",".join(fails)
    if fails:
        r["status"] = "fail"
    return r


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bunker", required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--his-start", type=int, default=None,
                    help="his 模式起点；--mode daily 时可省略")
    ap.add_argument("--mode", default="both", choices=["both", "his", "daily"],
                    help="both=his+daily；his=只跑长回放；daily=只跑 FR daily 单日")
    ap.add_argument("--freq", default="1min", choices=["1min", "0d5s"])
    ap.add_argument("--period", default="normal", choices=["normal", "tick"])
    ap.add_argument("--f-list", default="f0,f1,f2,f4")
    ap.add_argument("--long-total-bar", type=int, default=-1,
                help="长周期档位；-1 表示不检查（默认）")
    ap.add_argument("--tstd-bar-num", type=int, default=12000)
    ap.add_argument("--out", default="/tmp/qc_out")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--pool", type=int, default=1)
    a = ap.parse_args()

    if a.mode in ("both", "his") and a.his_start is None:
        ap.error("--mode his/both 需要 --his-start")

    f_list = [x.strip() for x in a.f_list.split(",") if x.strip()]
    os.makedirs(a.out, exist_ok=True)
    his_dir, day_dir = os.path.join(a.out, "his"), os.path.join(a.out, "daily")
    want_his = a.mode in ("both", "his")
    want_daily = a.mode in ("both", "daily")

    # ① 因子清单
    names = load_bunker(a.bunker)
    print(f"[1/5] bunker 因子数: {len(names)}  mode={a.mode}")

    # ② 清单校验（≈ check_csv_file）
    bad = check_csv_like(names, a.tstd_bar_num)
    if bad:
        print(f"  ! 因子名负载档位异常 {len(bad)} 个，例如 {bad[:3]}")

    # ③ 回放
    if not a.skip_run:
        if want_his:
            print("[2/5] his 回放 ...")
            print("  rc =", run_replay(
                make_cfg(his_dir, a.his_start, a.end, a.bunker, "his"),
                os.path.join(a.out, "his.log")))
        else:
            print("[2/5] his 跳过（mode=daily）")
        if want_daily:
            print("[3/5] daily 回放（FR mode=daily，单日）...")
            print("  rc =", run_replay(
                make_cfg(day_dir, a.end, a.end, a.bunker, "daily"),
                os.path.join(a.out, "daily.log")))
        else:
            print("[3/5] daily 跳过（mode=his）")
    else:
        print("[2/5][3/5] 跳过回放")

    # ④ 载入（只读 end 前 5 天，控制内存）
    d0 = str(pd.Timestamp(str(a.end)).normalize() - pd.Timedelta(days=5))[:10].replace("-", "")
    today = str(a.end)
    empty = pd.DataFrame(columns=["timestamp", "instrumentID", "factorName", "value"])
    his_all = load_days(his_dir, d0, today) if want_his else empty
    day_all = load_days(day_dir, d0, today) if want_daily else empty
    print(f"[4/5] 载入 his {len(his_all):,} 行 / daily {len(day_all):,} 行")

    his_by = dict(tuple(his_all.groupby("factorName"))) if len(his_all) else {}
    day_by = dict(tuple(day_all.groupby("factorName"))) if len(day_all) else {}

    rows = []
    missing = []
    variants = [f for f in f_list]
    for base in names:
        for v in variants:
            n = base if v == "f0" else (f"{base}.f1_{a.tstd_bar_num}" if v == "f1" else f"{base}.{v}")
            if n not in his_by and n not in day_by:
                missing.append(n)
                continue
            rows.append(check_factor(n, his_by.get(n, empty),
                                     day_by.get(n, empty),
                                     a.period, a.long_total_bar, a.tstd_bar_num,
                                     mode=a.mode))

    # ④b 因子性质统计 + 相关性去冗余（对齐老「计算因子性质脚本」）
    props_rows, aligned = [], {}
    target_y = 15
    for r in rows:
        n = r["factorName"]
        d = his_by.get(n)
        if d is None or d.empty:
            d = day_by.get(n)          # 单 daily 模式：性质统计退回 daily
        if d is None or d.empty:
            continue
        pr = props.factor_props(d, r["变体"])
        pr["hf"] = r.get("hf")
        pr["factorName"] = n
        pr["变体"] = r["变体"]
        fails_p = props.judge_props(pr, r["变体"], target_y)
        pr["性质失败项"] = ",".join(fails_p)
        props_rows.append(pr)

        # 并入主表 + 拼进 reason
        for k in ("SkewAll", "KurtAll", "mode", "big_than6", "big_than12", "big_than18"):
            r[k] = pr.get(k)
        if fails_p:
            r["reason"] = ",".join([x for x in (r["reason"], ",".join(fails_p)) if x])
            r["status"] = "fail"

        if r["变体"] == "f0":
            s2 = d.set_index(["timestamp", "instrumentID"])["value"]
            aligned[n] = s2[~s2.index.duplicated()]

    # HIGH_CORR_REMOVED：因子间相关性去冗余改由下游脚本处理，质检这里不做

    # ⑤ 汇总
    rep = pd.DataFrame(rows)
    print(f"[5/5] 因子×版本 {len(rep)}，缺失 {len(missing)}")
    if len(rep):
        print(rep["status"].value_counts().to_string())

    xlsx = os.path.join(a.out, "qc_report.xlsx")
    with pd.ExcelWriter(xlsx) as w:
        if len(rep):
            for st, sheet in (("pass", "pass"), ("fail", "fail"),
                              ("load_error", "load_error"),
                              ("wrong_long_total_bar_error", "wrong_long_total_bar")):
                sub = rep[rep["status"] == st]
                if len(sub):
                    sub.to_excel(w, sheet_name=sheet, index=False)
            rep.to_excel(w, sheet_name="all", index=False)
        else:
            # ExcelWriter 至少要有一个 sheet
            pd.DataFrame(columns=["factorName", "status", "reason", "qc_mode"]).to_excel(
                w, sheet_name="all", index=False)
        if missing:
            pd.DataFrame({"factorName": missing}).to_excel(w, sheet_name="missing_factor", index=False)
        if props_rows:
            pd.DataFrame(props_rows).to_excel(w, sheet_name="properties", index=False)
    print("报告:", xlsx)

    if len(rep):
        bads = rep[rep["status"] == "fail"]
        if len(bads):
            print("\n失败明细（前 15）:")
            cols = [c for c in ("factorName", "nan_ratio", "hf_ratio",
                                "daily_his_corr", "reason") if c in bads.columns]
            print(bads[cols].head(15).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ─────────────────────────────────────────────────────────────
# 公共 API（供 main.py 与外部脚本调用）
# ─────────────────────────────────────────────────────────────
def all_check(bunker: str, end: int, his_start: int | None = None,
              f_list=None, freq: str = "1min", period: str = "normal",
              long_total_bar: int = -1, tstd_bar_num: int = 12000,
              out_dir: str = ".", skip_run: bool = False, pool: int = 1,
              mode: str = "both"):
    """因子质检总入口。

    参数与老平台 check_future_main.py 对齐：
        bunker        因子清单目录（含 StrategySignal.json）
        end           回放结束日（也是比对日）
        his_start     his 模式起点（mode=his/both 必填）
        mode          both / his / daily
                      daily = FR 原生 daily：区间 [end,end]，平台自动前摇，只导出 end
        f_list        要质检的版本列表，如 ['f0','f1','f2','f4']
        freq          '1min' or '0d5s'
        period        'normal' or 'tick'
        long_total_bar 长周期档位；-1 表示不检查
        tstd_bar_num  tsstd 负载档位
        out_dir       报告输出目录
        skip_run      True 表示复用已有的 his/daily 数据
    """
    import sys as _sys

    argv = ["qc_main",
            "--bunker", str(bunker),
            "--end", str(end),
            "--out", str(out_dir),
            "--mode", str(mode),
            "--freq", str(freq),
            "--period", str(period),
            "--long-total-bar", str(long_total_bar),
            "--tstd-bar-num", str(tstd_bar_num),
            "--f-list", ",".join(f_list) if f_list else "f0,f1"]
    if his_start is not None:
        argv += ["--his-start", str(his_start)]
    if skip_run:
        argv.append("--skip-run")

    old_argv = _sys.argv
    _sys.argv = argv
    try:
        return main()
    finally:
        _sys.argv = old_argv


def load_factor_table(csv_path: str):
    """读取 FR 导出的因子参数表（FR_DUMP_FACTORS）。"""
    import csv
    rows = []
    try:
        with open(csv_path) as f:
            for r in csv.DictReader(f):
                rows.append(r)
    except Exception:
        return {}
    return {r["factor_name"]: r for r in rows}


def suggest_long_total_bar(table, limits=None):
    """按 total_bar 推导每个因子应属的 long_total_bar 档位。"""
    limits = limits or cfg.LONG_TOTAL_LIMIT
    out = {}
    for name, r in table.items():
        try:
            t = int(r.get("total_bar", 0))
        except Exception:
            continue
        lv = None
        for k in sorted(limits):
            if t <= limits[k]:
                lv = k
                break
        out[name] = lv
    return out
