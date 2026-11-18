# FactorQC (QC)

因子质检工具集，配套 **FactorReplay (FR)** 使用。

## 定位

```
输入：FactorReplay 的导出数据（CSV）或直接驱动 FR 回放
输出：Excel 报告（数据正确性 + 因子性质 + max_bar 判定）
```

## 工具

| 文件 | 作用 |
|---|---|
| `qc_main.py` | 总入口：读 bunker → 调 FR 跑 his/daily → 全套检查 → Excel |
| `qc_check.py` | 单份数据体检（nan/inf/min-max/dup/hf/long_total_bar） |
| `qc_props.py` | 因子性质：偏度 / 峰度 / 大跳 / 众数 + 阈值判定 |
| `qc_driver.py` | his/daily 双跑 + 逐点比对（max_bar 专项） |

## 用法

```bash
# 完整质检
python qc_main.py --bunker <因子清单目录> \
                  --end 20221230 --his-start 20220104 \
                  --out /tmp/qc_out

# 复用已有回放数据
python qc_main.py ... --skip-run

# 只做 max_bar 专项
python qc_driver.py --end 20190131 --his-start 20181201 --out /tmp/qc_out
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

## 依赖

```
Python 3.10+，pandas，numpy，openpyxl
系统已安装 FactorReplay（/usr/local/bin/FactorReplay）
```

## 已知限制

```
· 实盘（shipan）相关检查暂缓，等实盘数据
· TValue / TValueMean / TValueStd 需要收益数据，未接入
· 因子间相关性去冗余（|corr|>0.99）由下游脚本处理，本仓库不做
```
