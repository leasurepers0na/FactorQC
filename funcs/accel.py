#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""funcs/accel.py —— 可选加速（numba）

有 numba 时自动启用 JIT，没有则退回纯 numpy 实现。
热点两处：
  ① 半衰期搜索：需要对同一序列反复 shift + corr
  ② 大跳统计：逐合约差分计数
"""
import numpy as np

try:
    from numba import njit
    HAVE_NUMBA = True
except Exception:                                    # pragma: no cover
    HAVE_NUMBA = False

    def njit(*a, **k):
        def deco(f):
            return f
        if a and callable(a[0]):
            return a[0]
        return deco


@njit(cache=True)
def _corr_pair(x, y):
    """成对剔除 NaN 的 Pearson（与 pandas 行为一致）。"""
    n = 0
    sx = 0.0
    sy = 0.0
    sxx = 0.0
    syy = 0.0
    sxy = 0.0
    for i in range(x.size):
        a = x[i]
        b = y[i]
        if not (np.isnan(a) or np.isnan(b)):
            n += 1
            sx += a
            sy += b
            sxx += a * a
            syy += b * b
            sxy += a * b
    if n < 5:
        return np.nan
    cov = sxy - sx * sy / n
    vx = sxx - sx * sx / n
    vy = syy - sy * sy / n
    den = (vx * vy) ** 0.5
    if den <= 0.0:
        return np.nan
    return cov / den


@njit(cache=True)
def _jump_counts(d, hits, ns):
    """对差分序列统计 |Δ| > n 的次数。"""
    tot = 0
    for i in range(d.size):
        v = d[i]
        tot += 1
        for k in range(ns.size):
            if v > ns[k]:
                hits[k] += 1
    return tot


def corr_pair(x, y):
    return float(_corr_pair(np.ascontiguousarray(x, dtype=np.float64),
                            np.ascontiguousarray(y, dtype=np.float64)))


def jump_counts(diffs, ns):
    ns_arr = np.asarray(ns, dtype=np.float64)
    hits = np.zeros(ns_arr.size, dtype=np.int64)
    tot = _jump_counts(np.ascontiguousarray(diffs, dtype=np.float64), hits, ns_arr)
    return int(tot), hits.tolist()
