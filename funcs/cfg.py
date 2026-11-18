#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""funcs/cfg.py —— 路径与环境配置

所有外部依赖路径集中在这里，可通过环境变量覆盖，代码不再硬编码。
"""
import os

# ── 外部程序 ──
FR_BIN = os.environ.get("FR_BIN") or "FactorReplay"        # 系统 PATH 里的 FR
FR_WORKDIR = os.environ.get("FR_WORKDIR", "/mnt/raid_storage/FactorReplay/bin")

# ── 数据根目录 ──
REPLAY_ROOT = os.environ.get("FR_REPLAY_ROOT", "/mnt/raid_storage/CommData/stt_data_v4/clean")

# ── FR 运行所需环境变量（可由外部覆盖）──
ENV = {
    "FR_MAIN_DIR": os.environ.get(
        "FR_MAIN_DIR", "/mnt/raid_storage/zugzw4ng/v4fill/main_contract_raw/adj_daily_data"),
    "FR_INFO_DIR": os.environ.get(
        "FR_INFO_DIR", "/mnt/raid_storage/CommData/stt_data_v4/clean"),
    "LD_LIBRARY_PATH": os.environ.get(
        "LD_LIBRARY_PATH",
        "/usr/local/lib:/mnt/raid_storage/FactorReplay/bin:"
        "/home/zugzw4ng/workspace/build:"
        "/mnt/raid_storage/dev/bin/lib:/mnt/raid_storage/dev/bin/3rdlibs"),
}

# ── 阈值（来源：老平台 standard.txt / check() / 计算因子性质脚本）──
NAN_RATIO_MAX = 0.01
ABS_MAX_F1F6 = 3.01
ABS_MAX_F2F4 = 6.01
CORR_MIN = 0.9999
HF_SELF_CORR = 0.5
HF_RATIO = {"normal": (1 / 3, 3.0), "tick": (1 / 6, 1.5)}
LONG_TOTAL_LIMIT = {0: 12000, 1: 18000, 2: 24000, 3: 42000, 4: 60000}
EXCLUDE_FOR_NAN = {141, 131}
SKEW_MAX = 3.0
KURT_RANGE = (-1.0, 50.0)
JUMP_N = (6, 12, 18)
JUMP_TH = {"f1": 1e-4, "f9": 1e-5, "f8": 1e-6}
PKL_LENGTH = 20535549
Y1_HF = {15: 9, 30: 18, 60: 36, 90: 54, 120: 72,
         240: 144, 480: 288, 720: 432, 960: 576}
HF_LIM = (0.1, 10.0)
