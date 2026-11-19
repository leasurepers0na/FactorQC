# FactorQC (QC)

因子质检工具集，配套 **FactorReplay (FR)** 使用。入口风格对齐老 check 平台：
**一个 import + 一段参数 + 一行调用**。

## 结构

```
FactorQC/
  main.py            入口：只做参数配置与一次调用
  funcs/             所有实现与依赖
    __init__.py      对外 API（import funcs as qc）
    cfg.py           路径与环境变量（唯一硬编码处，可覆盖）
    core.py          总流程：读 bunker → 调 FR → 检查 → 汇总 → Excel
    props.py         因子性质（偏度/峰度/大跳/众数）+ 阈值判定
    accel.py         numba 加速（不可用时自动退回 numpy）
    checks.py        旧版单份数据体检（保留参考，core 未引用）
    driver.py        旧版 his/daily 双跑（保留参考，core 未引用）
```

## 三种模式

`--mode` 控制回放，不再无条件跑两次独立回放：

| mode | his 回放 | daily 回放 | 用途 |
|---|---|---|---|
| `both`（默认） | `mode=his`，`[his_start, end]` | `mode=daily`，`[end, end]` | max_bar 对比（生产用） |
| `his` | `mode=his`，`[his_start, end]` | 跳过 | 只看长历史 |
| `daily` | 跳过 | `mode=daily`，`[end, end]` | 日更单日体检，不需要 his_start |

`daily` 使用 FR 原生 daily 语义：平台按 `max_bar + 2*tsstd_bar` 自动前摇
（本样本 12015 → `replay_days=108`，含目标日），每个目标日 reset 因子状态，
只导出目标日。不再用「短窗口 his」冒充 daily。

比对口径：`daily_his_corr` / NaN 不一致前，his 会先裁到 daily 的时间范围
（老平台 `check_factor` 的做法），避免 daily 单日运行时把 his 多出来的交易日
全部算成「一侧 NaN」。

## 用法

```python
# main.py
import funcs as qc

qc.all_check(bunker='/path/to/bunker',
             end=20221230, his_start=20220104,
             mode='both',                 # both / his / daily
             f_list=['f0', 'f1'],
             out_dir='/tmp/qc_out')
```

```bash
python main.py

# 日更单日体检（不需要 his_start）
python -m funcs.core --bunker ... --end 20221230 --mode daily --out /tmp/qc_daily

# 复用已有回放数据，只重算报告
python -m funcs.core --bunker ... --end 20221230 --his-start 20220104 \
       --mode both --out /tmp/qc_out --skip-run

# 按因子追加 daily 前摇（his 参考保持 his_start，不改 f1 公式/列）
python -m funcs.core --bunker ... --end 20221230 --his-start 20220104 \
       --mode both --out /tmp/qc_out \
       --prewarm-extra '{"zugzw4ng_OUTER_004_36.5.2.rcv_d_tsstd_fl2.2_15": 28000}'
```

## 检查项与阈值

| 检查 | 判定 |
|---|---|
| `inf_num` | == 0 |
| `*_nan_ratio` | < 0.01（排除 SA=141 / PG=131） |
| `wrong_*_min/max` | f1/f6：±3.01；f2/f4：±6.01 |
| `f0_value_explosion` | 仅 f0：原始值 `max_abs > 1e7` 判失败（数值爆炸，不碰 f1） |
| `no_duplicate` | (合约, 时刻) 不可重复 |
| `daily_his_corr` | > 0.9999（his 裁到 daily 时间范围后 outer merge 的 Pearson） |
| `hf` | `floor(param/3) <= hf <= ceil(param*3)`；hf 取 daily（无 daily 才退回 his） |
| `long_total_bar` | bars 不超档位上限（tick ×120） |
| `check_max_bar_fail` | 当前口径：NaN 不一致 > 0（待真 daily 重跑后校准为 A/B） |
| `SkewAll` / `KurtAll` | \|skew\|<=3；-1<=kurt<=50 |
| `big_than6/12/18` | f1→1e-4，f9→1e-5，f8→1e-6，其余→0 |

## 配置

`funcs/cfg.py` 集中管理路径，全部可用环境变量覆盖：

```
FR_BIN            FactorReplay 可执行文件（默认走 PATH）
FR_WORKDIR        FR 的工作目录（存放 config/ 与因子库）
FR_REPLAY_ROOT    交易日历/数据根目录
FR_MAIN_DIR       主力合约与复权数据
FR_INFO_DIR       合约信息
LD_LIBRARY_PATH   动态库搜索路径
```

## 加速

`funcs/accel.py` 用 numba `njit` 加速两处热点：

- 半衰期的成对 Pearson（逐 lag 计算）
- 大跳的逐合约差分计数

没有 numba 时自动退回纯 numpy，行为一致。

## 依赖

```
Python 3.10+，pandas，numpy，openpyxl
可选：numba（没有也能跑，只是慢一些）
系统：FactorReplay 已安装（/usr/local/bin）
```

## 已知限制

```
· 实盘（shipan）相关检查暂缓，等实盘数据
· TValue / TValueMean / TValueStd 需要收益数据，未接入
· 因子间相关性去冗余（|corr|>0.99）由下游脚本处理
```
