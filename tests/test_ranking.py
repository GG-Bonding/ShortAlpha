from dataclasses import replace
from datetime import datetime

import pytest

from shortalpha.config import load_config
from shortalpha.domain import EventRisk, FactorResult, MarketRegime, SignalLabel
from shortalpha.errors import DataUnavailableError
from shortalpha.factors.scale import scale_to_weight
from shortalpha.paths import project_root
from shortalpha.scoring.rank import SymbolFactors, rank_symbols
from shortalpha.scoring.regime import classify_regime
from tests.factor_setup import signal_time


def _cfg():
    return load_config(project_root() / "config" / "default.yaml", root=project_root())


_CONFIRMED_PRICE = (
    ("degraded", "false"),
    ("gap", "0.0200000000"),
    ("price_time", "2024-06-20T09:00:00-04:00"),
    ("prior_close_time", "2024-06-18T20:00:00-04:00"),
    ("price_consolidated", "true"),
)
_CONFIRMED_EVENT = (
    ("event_risk", "NONE"),
    ("news_available_at", "2024-06-18T21:00:00-04:00"),
    ("post_event_reaction", "missing"),
)


def _factor(name: str, score: float, *, event_risk: str = "NONE") -> FactorResult:
    if name == "event":
        details = (
            ("event_risk", event_risk),
            ("news_available_at", "2024-06-18T21:00:00-04:00"),
            ("post_event_reaction", "missing"),
        )
    elif name == "price_action":
        details = _CONFIRMED_PRICE
    else:
        details = ()
    return FactorResult(
        name=name,
        raw_value=score,
        normalized_value=None,
        score=score,
        available=True,
        details=details,
    )


def _row(symbol: str, scores: dict[str, float], *, event_risk: str = "NONE", excluded=None):
    factors = tuple(_factor(name, score, event_risk=event_risk) for name, score in scores.items())
    return SymbolFactors(symbol=symbol, factors=factors, excluded_reason=excluded)


def _five(score: float) -> dict[str, float]:
    each = score / 5
    return {
        "momentum": each,
        "volume": each,
        "event": each,
        "relative_strength": each,
        "price_action": each,
    }


def _book(rows, *, regime=MarketRegime.NORMAL, vix_available=True, regime_cfg=None):
    cfg = _cfg()
    return rank_symbols(
        rows,
        as_of=signal_time(),
        ranking=cfg.ranking,
        hard_filters=cfg.hard_filters,
        regime=regime,
        regime_cfg=regime_cfg or cfg.regime,
        vix_available=vix_available,
    )


def test_default_thresholds_stay_locked() -> None:
    cfg = _cfg()
    assert cfg.ranking.top_n == 3
    assert cfg.ranking.long_threshold == 80
    assert cfg.ranking.watch_threshold == 70
    assert cfg.regime.extreme_spy_5d == -0.08
    assert cfg.regime.risk_off_spy_5d == -0.04
    assert cfg.regime.risk_off_qqq_5d == -0.05
    assert cfg.regime.risk_off_vix == 25
    assert cfg.regime.extreme_vix == 35
    assert cfg.regime.no_trade_on == "EXTREME_RISK_OFF"


def test_labels_follow_the_locked_cutoffs() -> None:
    book = _book(
        [
            _row("AAA", _five(80)),
            _row("BBB", _five(70)),
            _row("CCC", _five(69.9)),
        ]
    )
    labels = {row.symbol: row.label for row in book.ranked}
    assert labels == {
        "AAA": SignalLabel.LONG_CANDIDATE,
        "BBB": SignalLabel.WATCH,
        "CCC": SignalLabel.PASS,
    }
    assert [row.symbol for row in book.published] == ["AAA"]
    assert book.no_trade is False


def test_equal_scores_sort_by_symbol_and_top_n_does_not_stretch() -> None:
    book = _book(
        [
            _row("DDD", _five(90)),
            _row("CCC", _five(88)),
            _row("BBB", _five(81)),
            _row("AAA", _five(81)),
        ]
    )
    assert [row.symbol for row in book.ranked] == ["DDD", "CCC", "AAA", "BBB"]
    assert [row.symbol for row in book.published] == ["DDD", "CCC", "AAA"]
    assert book.no_trade is False


def test_watches_are_not_promoted_when_nobody_clears_80() -> None:
    book = _book([_row("AAA", _five(79)), _row("BBB", _five(70))])
    assert book.published == ()
    assert book.no_trade is True
    assert book.no_trade_reasons == ("no long candidate",)
    assert [row.symbol for row in book.ranked] == ["AAA", "BBB"]


def test_severe_negative_is_excluded_even_when_the_score_is_high() -> None:
    book = _book([_row("AAA", _five(95), event_risk="SEVERE_NEGATIVE")])
    assert book.ranked == ()
    assert book.published == ()
    assert book.no_trade is True
    assert book.excluded[0].label is SignalLabel.LONG_CANDIDATE
    assert book.excluded[0].event_risk is EventRisk.SEVERE_NEGATIVE
    assert book.excluded[0].excluded_reason == "severe negative event"


def test_extreme_regime_keeps_scores_and_sets_no_trade() -> None:
    book = _book([_row("AAA", _five(90))], regime=MarketRegime.EXTREME_RISK_OFF)
    assert [row.symbol for row in book.published] == ["AAA"]
    assert book.no_trade is True
    assert book.no_trade_reasons == ("EXTREME_RISK_OFF",)


def test_risk_off_still_publishes_when_only_extreme_blocks() -> None:
    book = _book([_row("AAA", _five(90))], regime=MarketRegime.RISK_OFF)
    assert book.no_trade is False
    blocked = replace(_cfg().regime, no_trade_on="RISK_OFF")
    stricter = _book(
        [_row("AAA", _five(90))],
        regime=MarketRegime.RISK_OFF,
        regime_cfg=blocked,
    )
    assert stricter.no_trade is True
    assert stricter.no_trade_reasons == ("RISK_OFF",)


def test_incomplete_factors_are_an_error() -> None:
    row = SymbolFactors("AAA", (_factor("momentum", 10),))
    with pytest.raises(DataUnavailableError, match="incomplete") as caught:
        _book([row])
    assert caught.value.symbol == "AAA"
    assert caught.value.operation == "rank"


def test_regime_boundaries_and_a_missing_vix() -> None:
    cfg = _cfg().regime
    assert classify_regime(-0.08, 0, None, cfg)[0] is MarketRegime.EXTREME_RISK_OFF
    assert classify_regime(-0.04, 0, None, cfg)[0] is MarketRegime.RISK_OFF
    assert classify_regime(0, -0.05, None, cfg)[0] is MarketRegime.RISK_OFF
    assert classify_regime(0, 0, 35, cfg)[0] is MarketRegime.EXTREME_RISK_OFF
    assert classify_regime(0, 0, 25, cfg)[0] is MarketRegime.RISK_OFF
    regime, available = classify_regime(0, 0, None, cfg)
    assert regime is MarketRegime.NORMAL
    assert available is False
    assert classify_regime(-0.05, 0, None, cfg)[1] is False


def test_total_is_the_sum_of_the_five_scores() -> None:
    scores = {
        "price_action": 11.9,
        "momentum": 21.2,
        "event": 23.4,
        "volume": 17.8,
        "relative_strength": 13.9,
    }
    book = _book([_row("NVDA", scores)])
    assert book.published[0].total_score == pytest.approx(88.2)
    assert [factor.name for factor in book.published[0].factors] == [
        "momentum",
        "volume",
        "event",
        "relative_strength",
        "price_action",
    ]


def _named(
    symbol: str,
    scores: dict[str, float],
    *,
    raw: dict[str, float] | None = None,
    reasons: dict[str, tuple[str, ...]] | None = None,
    details: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> SymbolFactors:
    factors = []
    for name, score in scores.items():
        if details is not None and name in details:
            factor_details = details[name]
        elif name == "event":
            factor_details = _CONFIRMED_EVENT
        elif name == "price_action":
            factor_details = _CONFIRMED_PRICE
        else:
            factor_details = ()
        factors.append(
            FactorResult(
                name=name,
                raw_value=(raw or {}).get(name, score),
                normalized_value=None,
                score=score,
                available=True,
                reasons=(reasons or {}).get(name, ()),
                details=factor_details,
            )
        )
    return SymbolFactors(symbol, tuple(factors))


def test_no_classified_change_cannot_clear_the_long_threshold() -> None:
    book = _book(
        [
            _named(
                "AAA",
                _five(90),
                raw={"event": 0.0},
                reasons={"event": ("no qualifying events",)},
            )
        ]
    )
    assert book.published == ()
    assert book.ranked[0].label is SignalLabel.PASS
    assert book.ranked[0].total_score == pytest.approx(90)
    assert book.ranked[0].thesis_veto == "no positive classified change"
    assert book.no_trade is True


def test_price_and_extension_can_reject_a_positive_classification() -> None:
    lagged = _named(
        "BBB",
        _five(90),
        raw={"event": 0.4, "relative_strength": -0.01},
        reasons={"event": ("Earnings beat",)},
        details={"price_action": (("degraded", "true"),)},
    )
    extended = _named(
        "CCC",
        _five(90),
        raw={"event": 0.4, "relative_strength": 0.02},
        reasons={"event": ("Guidance raise",)},
        details={
            "event": _CONFIRMED_EVENT,
            "momentum": (("overheated", "true"),),
        },
    )
    gapped = _named(
        "DDD",
        _five(90),
        raw={"event": 0.4, "relative_strength": 0.02},
        reasons={"event": ("Contract win",)},
        details={
            "event": _CONFIRMED_EVENT,
            "price_action": (("degraded", "false"), ("gap", "0.0900000000")),
        },
    )
    book = _book([lagged, extended, gapped])
    vetoes = {row.symbol: row.thesis_veto for row in book.ranked}
    assert vetoes == {
        "BBB": "same-day price confirmation is missing",
        "CCC": "five-day move already exceeds 25%",
        "DDD": "premarket gap already exceeds 8%",
    }
    assert book.published == ()
    assert all(row.label is SignalLabel.PASS for row in book.ranked)
    missing = next(row for row in book.ranked if row.symbol == "BBB")
    assert "Same-day price is missing" in missing.thesis


def test_below_baseline_volume_does_not_veto() -> None:
    book = _book(
        [
            _named(
                "AAA",
                _five(85),
                raw={"event": 0.3, "relative_strength": 0.02, "volume": 0.4},
                reasons={"event": ("Earnings beat",)},
            )
        ]
    )
    assert book.published[0].symbol == "AAA"
    assert book.published[0].thesis_veto is None
    assert "Positioning is unknown" in book.published[0].thesis
    assert "0.4x the baseline" in book.published[0].thesis
    assert "Setup: continuation." in book.published[0].thesis
    assert "Same-day price at 09:00 is +2.0% from the prior close" in book.published[0].thesis


def test_a_confirmed_new_catalyst_does_not_need_pre_event_strength() -> None:
    book = _book(
        [
            _named(
                "EEE",
                _five(90),
                raw={"event": 0.4, "relative_strength": -0.01},
                reasons={"event": ("Earnings beat",)},
            )
        ]
    )
    assert book.published[0].symbol == "EEE"
    assert book.published[0].thesis_veto is None
    assert "Setup: new catalyst." in book.published[0].thesis
    assert "Recent relative strength through the last completed session; " in (
        book.published[0].thesis
    )
    assert "5-day excess versus SPY and the sector is -1.0%." in book.published[0].thesis
    assert "does not add points" not in book.published[0].thesis


def test_prior_strength_is_not_confirmation() -> None:
    extended = _named(
        "FFF",
        _five(90),
        raw={"event": 0.4, "relative_strength": 0.02},
        reasons={"event": ("Earnings beat",)},
        details={
            "event": (
                ("event_risk", "NONE"),
                ("news_available_at", "2024-06-18T21:00:00-04:00"),
                ("post_event_reaction", "0.0900000000"),
            ),
            "price_action": _CONFIRMED_PRICE,
        },
    )
    rejected = _named(
        "GGG",
        _five(90),
        raw={"event": 0.4, "relative_strength": 0.04},
        reasons={"event": ("Guidance raised",)},
        details={
            "event": _CONFIRMED_EVENT,
            "price_action": (
                ("degraded", "false"),
                ("gap", "-0.0100000000"),
                ("price_time", "2024-06-20T09:00:00-04:00"),
                ("prior_close_time", "2024-06-18T20:00:00-04:00"),
                ("price_consolidated", "true"),
            ),
        },
    )
    book = _book([extended, rejected])
    vetoes = {row.symbol: row.thesis_veto for row in book.ranked}
    assert vetoes == {
        "FFF": "post-event move already exceeds 8%",
        "GGG": "price has not confirmed the event",
    }
    assert "Setup: unconfirmed." in book.ranked[0].thesis
    assert book.published == ()


def test_non_positive_momentum_and_strength_cannot_reach_80() -> None:
    momentum = scale_to_weight(0, -0.05, 0.08, 25)
    relative = scale_to_weight(0, -0.05, 0.05, 15)
    book = _book(
        [
            _named(
                "AAA",
                {
                    "momentum": momentum,
                    "volume": 20,
                    "event": 25,
                    "relative_strength": relative,
                    "price_action": 15,
                },
                raw={"momentum": 0, "relative_strength": 0, "event": 0.4, "volume": 3},
                reasons={"event": ("Earnings beat",)},
                details={
                    "relative_strength": (("window_end", "2024-06-18T16:00:00-04:00"),),
                },
            )
        ]
    )
    row = book.ranked[0]
    assert row.total_score == pytest.approx(momentum + relative + 60)
    assert row.total_score < 80
    assert book.published == ()
    assert "Setup: new catalyst." in row.thesis
    assert "does not add points" in row.thesis
    assert "completed before the event" in row.thesis


def test_the_five_day_window_is_not_called_pre_event_when_it_overlaps() -> None:
    book = _book(
        [
            _named(
                "AAA",
                _five(90),
                raw={"event": 0.4, "relative_strength": 0.02},
                reasons={"event": ("Earnings beat",)},
                details={
                    "relative_strength": (("window_end", "2024-06-19T16:00:00-04:00"),),
                },
            )
        ]
    )
    assert "includes sessions after the event" in book.published[0].thesis
    assert "Pre-event trend" not in book.published[0].thesis


def test_as_of_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        rank_symbols(
            [],
            as_of=datetime(2024, 6, 20, 9, 0),
            ranking=_cfg().ranking,
            hard_filters=_cfg().hard_filters,
            regime=MarketRegime.NORMAL,
            regime_cfg=_cfg().regime,
            vix_available=False,
        )
