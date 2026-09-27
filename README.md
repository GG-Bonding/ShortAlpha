# ShortAlpha

美股开盘前的多因子信号研究系统。V0.1 在每个交易日 09:00 ET，只用当时已经公开的数据给股票打分、排序，并留下不可变的 Signal Snapshot，再用随后 1、2、3、5 个交易日的收益检验分数是否有效。

这不是交易系统。它不会连接券商，也不会下单。

当前进度：**Phase 2**。配置、日历、SQLite、股票池和流动性过滤已实现。因子、扫描、回放和评估在后续阶段加入。规则见 [docs/plan.md](docs/plan.md)、[docs/point-in-time-rules.md](docs/point-in-time-rules.md) 和 [docs/data-source-decision.md](docs/data-source-decision.md)。

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
