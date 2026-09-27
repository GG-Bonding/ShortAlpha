# 数据源决策

调研日期：2026-09-27。价格与限额以供应商当时公开页面为准，后续若改套餐，只改 Provider，不改因子公式。

V0.1 的选择标准，按优先级：

1. 本机可以跑起来，不签企业合同。
2. 历史日线能覆盖至少 2024–2025 的回放。
3. 新闻有可比较的发布时间，能执行 `available_at <= signal_time`。
4. 成本低。盘前全市场成交不是免费就能可靠拿到的，缺失就降级，不伪造。

## 候选

| 维度 | Alpaca Market Data | Massive（原 Polygon.io） | Finnhub |
| --- | --- | --- | --- |
| 历史 OHLCV | 约 2016 年起。免费档只有 IEX，大约占全美成交的 2.5% | 免费 Basic：约 2 年、日终、5 次/分钟。Starter $29/月：5 年、15 分钟延迟分钟线 | 免费档有美股日线。分钟线主要在付费档（约 $50/月起） |
| 盘前 | 请求常规时段以外的 bar 会返回当时有成交的 bar。免费 IEX 的盘前不能代表全市场。全市场 SIP 在 Algo Trader Plus，约 $99/月 | 有。扩展时段 04:00–20:00 ET 的成交会进聚合。免费档没有可用的盘前分钟线 | 免费档没有可靠的盘前聚合 |
| 新闻 | Benzinga。`created_at` / `updated_at` 为 RFC3339。历史大约到 2015。基础账户可用，限额跟随行情套餐（免费约 200 次/分钟） | 新闻不在股票套餐内，公开报价是按数据集另购（约 $99/月量级） | `company-news` 带 Unix `datetime`。免费历史大约 1 年，60 次/分钟 |
| 时间戳质量 | 新闻时间可用。K 线时间是 bar 起点，不能直接当成可用时间 | 高。SIP，聚合时间规则公开 | 新闻是秒级时间戳，但查询窗口是日期，过滤必须在客户端做 |
| 速率 | 免费约 200 次/分钟 | 免费 5 次/分钟；付费档公开为不限 | 免费 60 次/分钟 |
| 成本 | 基础 $0；全市场行情约 $99/月 | $0 / $29 / $79 / $199 | $0 起；完整基本面套餐很贵 |
| 对 2025 全年回放 | 日线和新闻历史都够 | 免费历史偏短，且新闻要另买 | 以 2026-09 为“今天”，免费新闻的 1 年窗口盖不住 2025 上半年 |

公开材料：

- Alpaca Market Data：<https://docs.alpaca.markets/us/docs/about-market-data-api>
- Alpaca News API：<https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/>
- Massive 股票与定价：<https://massive.com/stocks> ，<https://massive.com/pricing>
- Finnhub 定价：<https://finnhub.io/pricing>

## 决定

真实数据只用 **Alpaca** 一个 Provider。

原因：一个免费账户同时有长期日线和带 `created_at` 的新闻，足够把研究跑起来。Massive 的盘前和分钟线更好，但新闻要另买，免费调用额度也不适合先扫一整年。Finnhub 的免费新闻历史太短，盖不住 V0.1 要做的 2025 回放。

业务代码只依赖 Provider 接口。测试、回放的确定性用例走 Fixture。没有 API key 时，扫描必须失败并说明缺的环境变量，不能退回一套伪装的行情。

## 环境变量

```text
APCA_API_KEY_ID
APCA_API_SECRET_KEY
```

只从环境读取。仓库不提交 `.env`，配置文件里没有密钥字段。

## 明确降级

这些缺口保持可见。不把它们填成“看起来像真的”的数。

1. **盘前。** `alpaca.feed` 默认 `iex`。IEX 不是合并行情，盘前成交量不能当成全市场盘前量。`feed != sip` 时，`premarket_volume_available = false`，成交量因子改用最近一个完整交易日的 `Volume / AvgVolume20`。价格行为因子不编造 gap；全市场缺少盘前价格时，该因子记为中性分，并在快照和日志里写明降级。`feed = sip` 才使用 04:00 到信号时点的盘前窗口。
2. **日线可用时间。** Alpaca 日线时间戳是 bar 起点。`available_at` 必须写成当日收盘（常规 16:00 ET，提前收市 13:00 ET），否则会把当天收盘价泄漏给 09:00 的信号。
3. **复权。** 配置只接受 `adjustment: raw`。用“今天的前复权序列”回放过去，会把未来拆分因子写进历史价格，价格过滤会失真。拆分只在 `ex_date <= 信号日` 时生效。Provider 如果给不出公司行动，就返回明确错误，不用复权价冒充原始价。
4. **VIX。** 波动率标的配置为 `VIX`，不用 `VIXY` 代替。股票接口没有 VIX 时，`vix_available = false`，状态只用 SPY 与 QQQ 的已完成收益，不把 VIX 当成 0。
5. **指数成分。** 没有免费的 point-in-time 成分。宇宙文件带 `list_as_of`，并且 `point_in_time_membership: false`。用当前成分回放过去有幸存者偏差，每次运行都要把这个标记写进记录。
6. **新闻修订。** `available_at = created_at`。不用 `updated_at <= signal_time` 做过滤，否则一篇当时已经发布、后来被编辑的文章会从历史信号里消失。正文一旦写入本地就冻结；回放读本地行，不重新下载覆盖。源站在我们入库前改过正文，是已知残余偏差。

## 股票池快照

V0.1 没有免费的 point-in-time 成分，所以仓库里放的是两份静态名单，配置里写明日期：

| 来源 | 文件 | 快照日期 | 取得方式 |
| --- | --- | --- | --- |
| S&P 500 | `fixtures/universe/sp500.csv` | 2026-09-21 | `datasets/s-and-p-500-companies` 当日自动更新，503 只 |
| Nasdaq-100 | `fixtures/universe/nasdaq100.csv` | 2026-08-09 | `unliftedq/index-constitution` 的 `latest/nasdaq100.csv`，102 只 |

两份合并去重后是 518 只。`point_in_time_membership` 必须保持 false。用这份名单回放 2025 会有幸存者偏差，评估时不能把它说成无偏。

## 已接入的行情客户端

Phase 2 接入 Alpaca 日线。`adjustment` 只允许 `raw`，因此价格过滤用的是当时的成交价，而不是今天的前复权价。bar 时间戳是区间起点；`available_at` 改写成 NYSE 收盘（提前收市为 13:00 ET）。信号日 09:00 读到的“当天日线”会被丢掉。

新闻客户端仍在 Phase 5。拆分调整只影响跨 ex-date 的收益，留到计算动量时处理；流动性过滤不需要它，因为原始收盘价就是当时的价格。
