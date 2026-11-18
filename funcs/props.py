#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_props.py —— 因子性质统计与筛选（对齐老「计算因子性质脚本」）

老实现位置：
  Z:/.../计算因子性质脚本/ver1.1_20230207_windows/商品/
    sp_f0_statistic_stats.pyd   计算 SkewAll / KurtAll / big_than{N} / mode / hf / TValue
    sp_parse_result.py          按阈值筛选（本文件复刻其判定）

本模块只依赖因子值本身（不需要收益），因此可直接接入质检。

指标口径
--------
  SkewAll      因子值（按合约分组后拼接）的偏度
  KurtAll      峰度（超额峰度，pandas 默认为 Fisher 定义）
  big_than{N}  相邻差分 |Δ| > N 的**频率**（次数 / 总样本数）
  mode         众数（按 1e-3 分箱取最高频箱中心）
  hf           半衰期（复用 qc_main 的算法）

筛选阈值（来自 sp_parse_result.py）
------------------------------------
  |SkewAll| <= 3
  -1 <= KurtAll <= 50
  big_than: f1 -> <=1e-4, f9 -> <=1e-5, f8 -> <=1e-6, 其余 -> == 0
  hf: 属于 target_y 基准的 [0.1, 10] 倍
  相关性: |corr| > 0.99 的因子对，剔除其一
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from . import accel
except Exception:      # numba 不可用时退回纯 numpy
    accel = None

# 目标周期 -> 半衰期基准（来自 sp_parse_result.py 的 y1_hf）
Y1_HF = {15: 9, 30: 18, 60: 36, 90: 54, 120: 72,
         240: 144, 480: 288, 720: 432, 960: 576}
HF_LIM = (0.1, 10.0)

SKEW_MAX = 3.0
KURT_RANGE = (-1.0, 50.0)
JUMP_N = (6, 12, 18)
JUMP_TH = {"f1": 1e-4, "f9": 1e-5, "f8": 1e-6}
CORR_DEDUP = 0.99


def _jump_type(variant: str) -> str:
    return variant if variant in ("f1", "f8", "f9") else "other"


def factor_props(df: pd.DataFrame, variant: str) -> dict:
    """单个因子的性质统计。df 需含 timestamp / instrumentID / value。"""
    out = {"SkewAll": np.nan, "KurtAll": np.nan, "mode": np.nan}
    for n in JUMP_N:
        out[f"big_than{n}"] = np.nan
    if df is None or df.empty:
        return out

    v = pd.to_numeric(df["value"], errors="coerce")
    fin = v[np.isfinite(v)]
    if len(fin) < 10:
        return out

    out["SkewAll"] = float(fin.skew())
    out["KurtAll"] = float(fin.kurt())

    # 众数：按 1e-3 分箱
    try:
        bins = np.round(fin.to_numpy() / 1e-3).astype(np.int64)
        vals, cnts = np.unique(bins, return_counts=True)
        out["mode"] = float(vals[np.argmax(cnts)] * 1e-3)
    except Exception:
        pass

    # 大跳：按合约算相邻差分的绝对幅度
    tot = 0
    hits = [0] * len(JUMP_N)
    for _c, g in df.groupby("instrumentID", sort=False):
        s = pd.to_numeric(g["value"], errors="coerce").dropna()
        if len(s) < 2:
            continue
        d = np.abs(np.diff(s.to_numpy(float)))
        if accel is not None:
            t, h = accel.jump_counts(d, JUMP_N)
            tot += t
            hits = [a + b for a, b in zip(hits, h)]
        else:
            tot += len(d)
            hits = [a + int((d > n).sum()) for a, n in zip(hits, JUMP_N)]
    if tot:
        for n, h in zip(JUMP_N, hits):
            out[f"big_than{n}"] = h / tot
    return out


def judge_props(p: dict, variant: str, target_y: int) -> list[str]:
    """按老脚本的阈值判定，返回失败项列表。"""
    fails = []
    s = p.get("SkewAll")
    if s is not None and not np.isnan(s) and abs(s) > SKEW_MAX:
        fails.append("wrong_skew")
    k = p.get("KurtAll")
    if k is not None and not np.isnan(k) and not (KURT_RANGE[0] <= k <= KURT_RANGE[1]):
        fails.append("wrong_kurt")

    # 大跳：按变体选列与阈值
    jkey = {"f1": "big_than6", "f8": "big_than18"}.get(variant, "big_than12")
    th = JUMP_TH.get(variant)
    val = p.get(jkey)
    if val is not None and not np.isnan(val):
        if th is None:
            if val > 0:
                fails.append("wrong_jump")
        elif val > th:
            fails.append("wrong_jump")

    # 半衰期范围（老脚本：基准的 0.1~10 倍）
    base = Y1_HF.get(target_y)
    hf = p.get("hf")
    if base and hf is not None and not np.isnan(hf):
        if not (base * HF_LIM[0] <= hf <= base * HF_LIM[1]):
            fails.append("wrong_hf_range")
    return fails


def corr_dedup(values: dict[str, pd.Series], th: float = CORR_DEDUP) -> tuple[list[str], dict]:
    """因子间相关性去冗余：|corr| > th 的因子对，按顺序剔除后者。

    values: 因子名 -> 已按 (timestamp, instrumentID) 对齐的 value Series
    """
    names = [n for n, s in values.items() if s is not None and len(s) > 10]
    if len(names) < 2:
        return names, {}
    df = pd.DataFrame({n: values[n] for n in names})
    cm = df.corr()
    kept, dropped = [], {}
    for i, n in enumerate(names):
        if n in dropped:
            continue
        kept.append(n)
        for m in names[i + 1:]:
            if m in dropped:
                continue
            c = cm.at[n, m]
            if not np.isnan(c) and abs(c) > th:
                dropped[m] = (n, float(c))
    return kept, dropped
