#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""funcs/__init__.py —— 对外统一入口

用法（与老 check 平台一致：一个 import + 一行调用）：

    import funcs as qc

    qc.all_check(bunker='/path/to/bunker',
                 end=20221230, his_start=20220104,
                 f_list=['f0', 'f1'],
                 out_dir='/tmp/qc_out')
"""
from . import cfg
from .core import all_check, load_bunker
from .props import factor_props, judge_props

__all__ = [
    "cfg",
    "all_check", "load_bunker",
    "factor_props", "judge_props",
]
