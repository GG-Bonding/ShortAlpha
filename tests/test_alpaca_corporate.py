from datetime import date, datetime

from shortalpha.providers.alpaca_corporate import splits_from_payload
from tests.factor_setup import NY

AS_OF = datetime(2024, 6, 20, 9, 0, tzinfo=NY)


def test_split_ratios_stop_at_the_signal_session() -> None:
    payload = {
        "corporate_actions": {
            "forward_splits": [
                {
                    "symbol": "AAPL",
                    "ex_date": "2020-08-31",
                    "old_rate": 1,
                    "new_rate": 4,
                    "process_date": "2020-08-31",
                }
            ],
            "stock_dividends": [
                {"symbol": "AAPL", "ex_date": "2024-06-21", "rate": 0.05},
            ],
        }
    }
    splits = splits_from_payload(
        payload,
        symbol="AAPL",
        start=date(2020, 1, 1),
        end=date(2024, 6, 21),
        as_of=AS_OF,
    )
    assert len(splits) == 1
    assert splits[0].ex_date == date(2020, 8, 31)
    assert splits[0].old_rate == 1
    assert splits[0].new_rate == 4


def test_stock_dividend_rate_is_additional_shares() -> None:
    payload = {
        "corporate_actions": {
            "stock_dividends": [
                {"symbol": "AAPL", "ex_date": "2024-06-03", "rate": 0.05},
            ]
        }
    }
    splits = splits_from_payload(
        payload,
        symbol="AAPL",
        start=date(2024, 6, 1),
        end=date(2024, 6, 20),
        as_of=AS_OF,
    )
    assert splits[0].old_rate == 1
    assert splits[0].new_rate == 1.05
