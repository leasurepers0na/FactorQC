#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""main.py —— 质检入口（仿老 check_future_main.py：一个 import + 参数块 + 一行调用）
"""
import funcs as qc

if __name__ == '__main__':
    # =======================  参数配置  ===============================
    bunker    = '/home/zugzw4ng/workspace/FactorReplay/config/bunker/f200/'
    end       = 20221230          # 回放结束日（也是比对日）
    his_start = 20220104          # his 起点（长预热）
    f_list    = ['f0', 'f1']      # 要质检的版本：f0/f1/f2/f4
    freq      = '1min'            # '1min' or '0d5s'
    period    = 'normal'          # 'normal' or 'tick'
    out_dir   = '/tmp/qc_2022'     # 报告输出目录
    skip_run  = True             # True = 复用已有回放数据
    # =======================  参数配置 END  ===========================

    qc.all_check(bunker=bunker, end=end, his_start=his_start,
                 f_list=f_list, freq=freq, period=period,
                 out_dir=out_dir, skip_run=skip_run)
    print('Finish.')
