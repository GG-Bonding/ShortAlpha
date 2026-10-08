# ShortAlpha

美股开盘前的多因子信号研究系统。V0.1 在每个交易日 09:00 ET，只用当时已经公开的数据给股票打分、排序，并留下不可变的 Signal Snapshot，再用随后 1、2、3、5 个交易日的收益检验分数是否有效。

这不是交易系统。它不会连接券商，也不会下单。

当前进度：**Phase 10**。可以扫描一个交易日、回放一段历史，再评估分数和随后收益的关系。规则见 [docs/plan.md](docs/plan.md)、[docs/point-in-time-rules.md](docs/point-in-time-rules.md) 和 [docs/data-source-decision.md](docs/data-source-decision.md)。

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

动量只用已经收盘的交易日，不用盘前价。`R5 > 25%` 时动量分减半。相对强弱比较同一窗口里的个股、SPY 和行业 ETF。行业映射在 `config/sector_map.yaml`，按 GICS 对应 SPDR 行业 ETF，覆盖当前股票池里的 S&P 500 和 Nasdaq-100 代码。映射里没有的股票，相对强弱会报缺失，不会改用 SPY 充数。价格行为在缺口超过 3% 之后开始惩罚，更大的缺口不会得到更高的分。没有盘前价格时，这一项仍记为权重的一半，并写明是中性降级，不编造缺口；同时当天价格确认缺失，这只股票不能入选。

成交量优先用当天 04:00 到 09:00 的盘前量，除以过去 20 个交易日同一窗口的平均量。Alpaca IEX 不是合并盘前成交，这时成交量仍退回日线，`premarket_volume_available` 为 false；如果 IEX 有一笔带时间的最新价，价格确认会使用这笔价格并标明它不是合并行情。分母是 0 或历史不够时直接报错，不会记成 0 分。

事件分不调用模型。标题和摘要按 `config/event_rules.yaml` 归类，新鲜度是 `exp(-0.05 * 小时)`，超过 72 小时的稿件丢掉。同一族里正负说法同时出现则该族不计分。收购要区分买方和标的，诉讼要区分起诉方和被诉方。其余公司级事件，包括财报、指引、增发和评级，只记到该分句里点名的公司。多条事件的贡献相加后夹到 -1 到 1，再映射到 0–25。没有合格事件时是 12.5 分，原因是 `no qualifying events`，这不是故障。09:15 发布的新闻不会进入 09:00 的信号。重大利空的硬排除看 `severe_hold_hours`（72 小时），不跟分数的新鲜度一起消失。前一交易日 16:00 的严重利空，到下一交易日 09:00 仍然排除，新闻加减分则继续衰减。

总分是五项分数之和。`>= 80` 是 `LONG_CANDIDATE`，`70–80` 是 `WATCH`，更低是 `PASS`。只在达到 80 的股票里取前 3。没有达到 80 的股票时，结果是 `NO_TRADE`，不会把观察名单顶上去。入选还要求事件净贡献为正，五日涨幅不超过 25%，并且有一条晚于新闻的价格。从事件前基准到这条价格的累计涨幅要大于 0 且小于 8%；当天跳空只用来判断入场是否已经达到 8%。基准如果跨过了一个仍在交易的时段，记为近似，不能当成确认。后来多出来的日线不会让同一个基准变可靠。新闻之前的最后一笔分钟价可以代替日线收盘充当基准；结束于新闻时刻的完整分钟可以使用。这笔价格如果离新闻还有超过 60 秒的交易时间，包括同一交易时段里更早的成交，记为近似，并写下实际间隔秒数。分钟价写在行情 fixture 的 `prices` 里，文件回放会读入。新闻之前的成交不能确认这条新闻。动量和五日相对强弱都不为正时，新催化这个标签不加分，其余三项满分也到不了 80。五日相对强弱是扫描前最后一个完整交易日为止的窗口；只有这个窗口确实在新闻之前结束时，才把它称作事件前趋势。`SEVERE_NEGATIVE` 不参与排序。`EXTREME_RISK_OFF` 仍会留下分数，同时把这次运行标成 `NO_TRADE`。VIX 缺失时不把它当成 0。权重和 80 分门槛不因这次规则调整而改变。

每次保存都会新建一份快照。正文哈希不包含 `run_id` 和 `created_at`，所以同一输入的两次运行哈希相同，但旧行还在。快照文件用独占创建，已存在的文件不会被改写。分数只在写入快照时四舍五入到 4 位小数。

```bash
shortalpha explain NVDA --date 2024-06-20
```

解释会列出五项分数、加分原因和风险。同一天有多次运行时，默认解释最新的一次。

向前收益的入场价是信号日开盘，出场价是之后第 n 个交易日的开盘。周末和 NYSE 假日不算交易日。超额收益是个股收益减去同一窗口的 SPY。最大不利波动只用入场日到出场前一日的最低价，不用出场当天的盘中低点。信号日 09:00 还看不到当天日线，所以那时不会算出向前收益。缺 SPY 时不把超额记成 0。

```bash
shortalpha replay --from 2024-06-18 --to 2024-06-20
```

回放只读取 `available_at <= 当天 09:00` 的数据。同一配置和同一输入跑两次，快照哈希相同，`run_id` 不同。周末和 NYSE 假日不会产生信号。Fixture 行情的运行备注仍是 `corporate_actions=not_loaded`。Alpaca 行情会读取拆分比例，但只使用信号日当天或更早的 ex_date，并注明没有公告时间。新闻如果 `updated_at` 晚于 `created_at`，当前正文的可见时间是 `updated_at`。

```bash
shortalpha scan --date 2024-06-20
shortalpha evaluate --as-of 2024-06-28
```

`scan` 就是回放中的一个交易日，并打印候选、分数、原因和风险。没有指定版本时，它使用登记簿里的正式版本和该版本冻结的规则；还没有正式记录时用配置里的 `v0`。默认配置用的是仓库里的 fixture。fixture 没有完整行情时，扫描会报缺失数据，而不是编出候选。接 Alpaca 时把 `providers` 改成 `alpaca`，并设置 `APCA_API_KEY_ID` 和 `APCA_API_SECRET_KEY`。免费 IEX 行情可以提供带时间的价格，但不会被当成合并盘前成交量。

`evaluate` 只用每个信号日最新的一次运行，统计全部打分股票，不只看前三名。某个持有期的行情还没到，该期就不进入命中率。高分股票的平均超额没有高于低分时，输出 `HIGH SCORE DOES NOT BEAT LOW SCORE`。分数桶的平均超额没有严格上升时，输出 `NO MONOTONIC RELATIONSHIP`。样本不够时输出 `INSUFFICIENT SAMPLE`。没有亏损时盈亏比是 `UNDEFINED`。
