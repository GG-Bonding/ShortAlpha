# ShortAlpha

美股开盘前的多因子信号研究系统。V0.1 在每个交易日 09:00 ET，只用当时已经公开的数据给股票打分、排序，并留下不可变的 Signal Snapshot，再用随后 1、2、3、5 个交易日的收益检验分数是否有效。

这不是交易系统。它不会连接券商，也不会下单。

当前进度：**Phase 6**。五项因子可以合成总分并排出候选。快照、回放和评估还在后面。规则见 [docs/plan.md](docs/plan.md)、[docs/point-in-time-rules.md](docs/point-in-time-rules.md) 和 [docs/data-source-decision.md](docs/data-source-decision.md)。

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

shortalpha version
shortalpha check
shortalpha check --date 2024-07-03
shortalpha universe
shortalpha universe --date 2024-06-20

ruff format --check src tests
ruff check src tests
pytest
```

`check` 会校验配置、执行数据库迁移，并报告该日是不是交易日、是不是提前收市、信号时点落在哪里。默认数据库路径是 `data/shortalpha.db`，已在 `.gitignore` 中忽略。

默认股票池是 2026-09-21 的 S&P 500 与 2026-08-09 的 Nasdaq-100 快照，去重后使用。这不是当时的历史成分，`point_in_time_membership` 固定为 false。

`shortalpha universe` 只列出成分。加上 `--date` 后，用该日 09:00 ET 之前已经收盘的 20 个交易日计算价格和 20 日平均成交额。价格默认用前收盘；调用方如果已经有盘前价，可以把它传进过滤器。历史不足会记为 missing，不会当成不合格的 0 分，也不会把信号日当天的日线算进去。

默认行情 Provider 仍是 Fixture，所以对真实成分跑 `--date` 时，绝大多数股票会显示 missing。接入 Alpaca 时把 `providers.market` 设为 `alpaca`，并设置 `APCA_API_KEY_ID` 和 `APCA_API_SECRET_KEY`。日线使用 `adjustment=raw`。Alpaca 的 bar 时间是区间起点，系统把 `available_at` 写成当天收盘。

## 时间规则

信号时点是交易日 09:00 `America/New_York`。任何因子输入都要满足 `available_at <= signal_time`。当天 09:15 的新闻不能进入当天 09:00 的信号。详情见 point-in-time 文档。

权重之和必须等于 100，否则程序拒绝启动。

动量只用已经收盘的交易日，不用盘前价。`R5 > 25%` 时动量分减半。相对强弱比较同一窗口里的个股、SPY 和行业 ETF。行业映射在 `config/sector_map.yaml`，来自 S&P 500 的 GICS 行业；只在 Nasdaq-100、不在这份 S&P 500 快照里的股票没有映射，相对强弱会报缺失，不会改用 SPY 充数。价格行为在缺口超过 3% 之后开始惩罚，更大的缺口不会得到更高的分。没有盘前价格时，这一项记为权重的一半，并写明是中性降级，不编造缺口。

成交量优先用当天 04:00 到 09:00 的盘前量，除以过去 20 个交易日同一窗口的平均量。默认的 Alpaca IEX 行情不是合并盘前成交，这时 `premarket_volume_available` 为 false，改用最近一个完整交易日的成交量除以再往前 20 日的平均成交量。分母是 0 或历史不够时直接报错，不会记成 0 分。

事件分不调用模型。标题和摘要按 `config/event_rules.yaml` 归类，新鲜度是 `exp(-0.05 * 小时)`，超过 72 小时的稿件丢掉。同一族里正负说法同时出现则该族不计分。多条事件的贡献相加后夹到 -1 到 1，再映射到 0–25。没有合格事件时是 12.5 分，原因是 `no qualifying events`，这不是故障。09:15 发布的新闻不会进入 09:00 的信号。新鲜的重大利空会标成 `SEVERE_NEGATIVE`。

总分是五项分数之和。`>= 80` 是 `LONG_CANDIDATE`，`70–80` 是 `WATCH`，更低是 `PASS`。只在达到 80 的股票里取前 3。没有达到 80 的股票时，结果是 `NO_TRADE`，不会把观察名单顶上去。`SEVERE_NEGATIVE` 不参与排序。`EXTREME_RISK_OFF` 仍会留下分数，同时把这次运行标成 `NO_TRADE`。VIX 缺失时不把它当成 0。
