# ShortAlpha V0.1 计划

ShortAlpha 是开盘前信号研究系统，不是交易系统。它要回答三件事：高分股票未来 1/2/3/5 个交易日是否好于低分股票；Top 3 相对 SPY 是否有稳定超额；哪个持有期更好。若分数和未来收益没有稳定关系，评估必须把失败打印出来。

不接券商，不下单，不使用机器学习，不引入 MulFactorTradingModel。

## 技术栈

- Python 3.11+（当前开发机 3.14）
- 标准库：`sqlite3`、`zoneinfo`、`argparse`、`dataclasses`
- PyYAML 读取配置
- pytest、ruff
- httpx 只用于 Alpaca 行情客户端。测试用 MockTransport，不访问网络

选择 Python 而不是规格草案里的 Go 目录，是因为因子、夹具和测试都更直接。领域代码与 Provider 仍分开。

已锁定、不允许为了回测好看而修改的定义写在本文后半和 `docs/point-in-time-rules.md`。改公式只能来自事先写明的新假设，并保留旧快照。

## 目录

```text
config/                 默认配置与行业 ETF 映射
docs/
fixtures/               测试与离线回放输入
migrations/             SQLite 迁移
src/shortalpha/
  calendar.py           NYSE 日历
  cli.py
  config.py
  domain.py
  pit.py                available_at 过滤
  storage.py
  providers/            接口与 Fixture；真实客户端按阶段加入
tests/
```

后续阶段才添加 `factors/`、`scoring.py`、`replay.py`、`evaluation.py`。不预先建空框架。

## 阶段

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 1 | 配置、领域模型、SQLite、交易日历、Fixture Provider | 完成 |
| 2 | 历史日线、股票池、流动性过滤 | 完成 |
| 3 | 动量、相对强弱、价格行为 | 完成 |
| 4 | 成交量与盘前 | 完成 |
| 5 | 新闻、事件分类、新鲜度、事件分 | 完成 |
| 6 | 总分、排序、LONG_CANDIDATE / WATCH / PASS / NO_TRADE | 完成 |
| 7 | Signal Snapshot、持久化、解释 | 完成 |
| 8 | 向前收益、SPY、超额收益 | 完成 |
| 9 | 历史回放 | 完成 |
| 10 | 评估、分数桶、因子归因 | 未开始 |

每个阶段的完成标准：ruff format、ruff check、单元测试、集成测试、程序能运行、README 和本文件更新。然后单独提交。不跳过测试，不用 `--no-verify`。

## Phase 1 测试清单

1. 默认配置能加载，信号时间是 09:00，而不是 YAML 把 `09:00` 解析成数字。
2. 权重之和不是 100 时启动失败。
3. 动量内部权重之和不是 1 时失败。
4. 价格行为的分项上限之和必须等于价格行为权重。
5. 评估持有期不是 `1,2,3,5` 时失败。
6. 配置哈希对同一文件稳定。
7. 周末不是交易日。
8. NYSE 休市（含元旦周六提前到周五、六月节从 2022 年起、耶稣受难日）不是交易日。
9. 提前收市（独立日前夕、感恩节次日、平安夜）与“当天其实全天休市”区分开。
10. `T+n` 跳过周末和休市。
11. 迁移可重复执行；六张业务表都在。
12. 快照、运行、行情、新闻、因子、向前收益都不能 UPDATE 或 DELETE。
13. 同一 `run_id` 不能写两次；新的 `run_id` 保留旧结果。
14. 快照外键：没有 run 就不能写快照。
15. `available_at <= signal_time` 的新闻和日线保留，更晚的丢弃。边界等于信号时点时保留。
16. 时间顺序 `event_time <= published_at <= available_at` 被破坏时拒绝加载。
17. Fixture 未知行情标的是错误；新闻成功但为空是空列表。
18. 盘前记录写明 `available=false` 时保持 false；信号时点之后的盘前观察不可用。
19. 成分列表带 `point_in_time_membership=false`。
20. 失败日志包含 symbol、provider、operation、timestamp、reason。
21. `shortalpha version` 与 `shortalpha check` 可以运行。

## 锁定的因子定义

实现阶段必须按这里写，不能在看到收益后再改阈值。

总分是五项之和，范围 0–100。标签：`>= 80` 为 `LONG_CANDIDATE`，`70–80` 为 `WATCH`，更低为 `PASS`。没有达到 80 的股票时，运行级 `NO_TRADE`，禁止把阈值降下来凑满 Top 3。`EXTREME_RISK_OFF` 同样是运行级 `NO_TRADE`。排序键是总分降序、代码升序。

线性映射：`raw <= low` 得 0，`raw >= high` 得满权重，中间线性。然后夹到 `[0, weight]`。`normalized_value = score / weight`。`raw_value` 保存映射前的原始数。

### 动量，权重 25

```text
MomentumRaw = 0.20*R1 + 0.35*R3 + 0.45*R5
```

先映射到 0–25（`raw_low=-0.05`，`raw_high=0.08`）。若 `R5 > 0.25`，分数再乘 0.5。

### 成交量，权重 20

有 SIP 盘前时，比率是信号日 04:00–09:00 成交量，除以过去 20 个交易日同一窗口的平均成交量。否则用 point-in-time 规则里的日线比率，并标记 `premarket_volume_available=false`。比率从 0.5 映射到 0 分、3.0 映射到 20 分。

### 事件，权重 25

```text
单条 = Direction * Importance * Freshness * Confidence
Freshness = exp(-lambda * age_hours)
```

`lambda = 0.05`。超过 72 小时的事件丢弃。同一股票、同一类型、12 小时内、标题 token Jaccard `>= 0.6` 的重复稿只保留最早的一条。一篇稿可以命中多个不同类型；同一族同时命中正负两面则该族视为含糊，不记分。

多条合格事件先各自计算 `Direction * Importance * Freshness * Confidence`，再求和并夹到 `[-1, 1]`。这个和就是原始值。原始值从 -1 映射到 0 分、+1 映射到 25 分。没有合格事件时原始值为 0，分数是 12.5，原因写 `no qualifying events`。这是空集，不是故障。关键词表在 `config/event_rules.yaml`，是事先写定的规则，不能为了回测好看再改。

`Direction < 0` 且 `Importance >= 0.8` 且 `Freshness >= 0.5` 为 `SEVERE_NEGATIVE`，硬过滤剔除，无论总分多高。

分类表在 Phase 5 写成配置，规则基于标题和摘要关键词，不调用 LLM。

### 相对强弱，权重 15

```text
RS = 0.4 * (股票5日收益 - SPY5日收益) + 0.6 * (股票5日收益 - 行业ETF5日收益)
```

`raw_low=-0.05`，`raw_high=0.05`。行业映射缺失则该股票不评分。

### 价格行为，权重 15

```text
Gap = PreMarketPrice / PreviousClose - 1
```

`PreviousHigh` 是 `T-1` 的最高价，`20DHigh` 是 `T-20…T-1` 的最高价。ATR 是截止 `T-1` 的 14 日真实波幅均值。

缺口分在 `gap_penalty_start`（3%）处达到上限 10，之后不再因为缺口更大而加分。两处突破各 2.5 分。缺口超过 3% 后，整段结构分乘以惩罚系数：在 3% 为 1，在 8% 线性降到 0.25，超过 8% 保持 0.25。ATR 占前收盘超过 4% 时再乘 0.8，并写入风险。因此超过 3% 之后，更大的缺口不会得到更高的价格行为分。

数据源不支持盘前价格时，不编造 Gap。该因子分数取权重的一半，`raw_value` 为空，原因写明中性降级。Provider 错误仍然是错误，不是中性分。

### 向前收益与评估

入场价是 `Open(T)`，出场比较的是 `Open(T+n)`。MAE 使用持有区间内、退出开盘之前的日低点：交易日 `T … T+(n-1)` 的最低价相对入场价的最小收益率。MFE 用同一区间的最高价。退出日的盘中高低点不使用，因为定义的出场是开盘。

评估至少给出规格中的命中率、平均收益、中位数、超额、盈亏比、MAE、MFE，以及四个分数桶。桶的区间是左闭右开，最后一桶包含 100。样本不足时打印 `INSUFFICIENT SAMPLE`。高分桶的平均超额没有严格上升时打印 `NO MONOTONIC RELATIONSHIP`。盈亏比在没有亏损时打印 `UNDEFINED`，不打印无穷大冒充结果。

因子归因看每个因子分数与超额收益的 Spearman 相关，以及因子分数上半部分与下半部分的平均超额之差。不把总分解释成某一个因子已经“被证明有效”。

## 非目标

券商、自动下单、组合优化、止损、期权、做空、深度学习、技术指标大全、WebSocket、微服务、Redis、Kafka、仪表盘。
