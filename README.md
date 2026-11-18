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
    checks.py        单份数据体检（nan/inf/min-max/dup/hf/long_total_bar）
    props.py         因子性质（偏度/峰度/大跳/众数）+ 阈值判定
    driver.py        his/daily 双跑与逐点比对
    accel.py         numba 加速（不可用时自动退回 numpy）
```

## 用法

```python
# main.py
import funcs as qc

qc.all_check(bunker='/path/to/bunker',
             end=20221230, his_start=20220104,
             f_list=['f0', 'f1'],
             out_dir='/tmp/qc_out')
```

```bash
python main.py
```

命令行方式（兼容旧用法）：

```bash
python -m funcs.core --bunker ... --end 20221230 --his-start 20220104 --out /tmp/qc_out
```

## 检查项与阈值

| 检查 | 判定 |
|---|---|
| `inf_num` | == 0 |
| `*_nan_ratio` | < 0.01（排除 SA=141 / PG=131） |
| `wrong_*_min/max` | f1/f6：±3.01；f2/f4：±6.01 |
| `no_duplicate` | (合约, 时刻) 不可重复 |
| `daily_his_corr` | > 0.9999（outer merge 后 Pearson） |
| `hf` | `floor(param/3) <= hf <= ceil(param*3)` |
| `long_total_bar` | bars 不超档位上限（tick ×120） |
| `check_max_bar_fail` | 首条因子值为 NaN |
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
