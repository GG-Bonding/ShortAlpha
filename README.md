# ShortAlpha

美股开盘前的多因子信号研究系统。V0.1 在每个交易日 09:00 ET，只用当时已经公开的数据给股票打分、排序，并留下不可变的 Signal Snapshot，再用随后 1、2、3、5 个交易日的收益检验分数是否有效。

这不是交易系统。它不会连接券商，也不会下单。

当前进度：**Phase 1**。配置、领域模型、SQLite、NYSE 日历和 Fixture Provider 已实现。`scan`、回放和评估在后续阶段加入。规则见 [docs/plan.md](docs/plan.md)、[docs/point-in-time-rules.md](docs/point-in-time-rules.md) 和 [docs/data-source-decision.md](docs/data-source-decision.md)。

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

shortalpha version
shortalpha check
shortalpha check --date 2024-07-03

ruff format --check src tests
ruff check src tests
pytest
```

`check` 会校验配置、执行数据库迁移，并报告该日是不是交易日、是不是提前收市、信号时点落在哪里。默认数据库路径是 `data/shortalpha.db`，已在 `.gitignore` 中忽略。

API 密钥只放在环境变量 `APCA_API_KEY_ID` 和 `APCA_API_SECRET_KEY`。Phase 1 的默认 Provider 是 Fixture，不需要密钥。

## 时间规则

信号时点是交易日 09:00 `America/New_York`。任何因子输入都要满足 `available_at <= signal_time`。当天 09:15 的新闻不能进入当天 09:00 的信号。详情见 point-in-time 文档。

权重之和必须等于 100，否则程序拒绝启动。
