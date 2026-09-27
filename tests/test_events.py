import math
from datetime import datetime, timedelta

import pytest

from shortalpha.config import load_config
from shortalpha.domain import EventRisk, NewsItem
from shortalpha.event_rules import load_event_rules
from shortalpha.factors.events import compute_event
from shortalpha.factors.scale import scale_to_weight
from shortalpha.paths import project_root
from shortalpha.providers.fixture_news import FixtureNewsProvider
from tests.factor_setup import NY


def _loaded():
    root = project_root()
    cfg = load_config(root / "config" / "default.yaml", root=root)
    rules = load_event_rules(root / cfg.event.rules)
    return cfg.event, rules


def _item(item_id: str, headline: str, published: datetime, *, summary: str = ""):
    return NewsItem(
        id=item_id,
        symbols=("NVDA",),
        headline=headline,
        summary=summary,
        source="fixture",
        event_time=published,
        published_at=published,
        available_at=published,
        url=None,
    )


def _score(raw: float, weight: float = 25) -> float:
    return scale_to_weight(raw, -1, 1, weight)


def test_keyword_priors_stay_locked() -> None:
    _, rules = _loaded()
    by_id = {rule.id: rule for rule in rules.types}
    assert by_id["earnings_beat"].importance == 0.9
    assert by_id["earnings_beat"].confidence == 0.7
    assert by_id["earnings_miss"].direction == -1
    assert by_id["analyst_downgrade"].importance == 0.55
    assert by_id["guidance_cut"].importance == 0.85


def test_no_articles_is_a_neutral_empty_set() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    result = compute_event("NVDA", [], as_of=as_of, event=event, rules=rules, weight=25)
    assert result.raw_value == 0
    assert result.score == pytest.approx(12.5)
    assert result.reasons == ("no qualifying events",)
    assert dict(result.details)["event_risk"] == EventRisk.NONE.value


def test_signal_time_drops_the_later_article_even_if_it_is_passed_in() -> None:
    event, rules = _loaded()
    as_of = datetime(2025, 4, 10, 9, 0, tzinfo=NY)
    early = _item("early", "NVDA earnings beat", as_of - timedelta(minutes=30))
    late = _item("late", "NVDA guidance cut", as_of + timedelta(minutes=15))
    result = compute_event("NVDA", [early, late], as_of=as_of, event=event, rules=rules, weight=25)
    freshness = math.exp(-0.05 * 0.5)
    raw = 0.9 * freshness * 0.7
    assert result.score == pytest.approx(_score(raw))
    assert result.reasons == ("Earnings beat",)
    assert "Guidance cut" not in result.risks
    assert dict(result.details)["event_risk"] == EventRisk.NONE.value


def test_fixture_provider_also_hides_the_0915_article() -> None:
    event, rules = _loaded()
    as_of = datetime(2025, 4, 10, 9, 0, tzinfo=NY)
    provider = FixtureNewsProvider.from_json(project_root() / "fixtures" / "news" / "sample_news.json")
    items = provider.news("NVDA", as_of - timedelta(hours=72), as_of, as_of)
    assert [item.id for item in items] == ["news-0830"]
    result = compute_event("NVDA", items, as_of=as_of, event=event, rules=rules, weight=25)
    assert result.reasons == ("Earnings beat",)
    assert result.risks == ()


def test_duplicate_headlines_keep_the_earlier_article() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    first = _item(
        "a",
        "NVDA earnings beat on data center demand",
        as_of - timedelta(hours=1),
    )
    second = _item(
        "b",
        "NVDA earnings beat on data center demand today",
        as_of,
    )
    result = compute_event(
        "NVDA", [second, first], as_of=as_of, event=event, rules=rules, weight=25
    )
    freshness = math.exp(-0.05 * 1)
    raw = 0.9 * freshness * 0.7
    assert result.score == pytest.approx(_score(raw))
    assert dict(result.details)["kept"] == "1"


def test_distinct_headlines_both_count_and_the_sum_is_clamped() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    first = _item("a", "NVDA earnings beat on chips", as_of)
    second = _item(
        "b",
        "A software unit posted an earnings beat after a long slump in services",
        as_of,
    )
    result = compute_event("NVDA", [first, second], as_of=as_of, event=event, rules=rules, weight=25)
    assert float(dict(result.details)["raw_sum"]) == pytest.approx(1.26)
    assert result.raw_value == pytest.approx(1)
    assert result.score == pytest.approx(25)
    assert dict(result.details)["kept"] == "2"


def test_mixed_directions_in_one_family_do_not_score() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item("a", "earnings beat and earnings miss", as_of)
    result = compute_event("NVDA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert result.score == pytest.approx(12.5)
    assert result.reasons == ("no qualifying events",)
    assert result.risks == ("Ambiguous earnings event ignored",)


def test_a_fresh_earnings_miss_is_severe_and_an_old_one_is_not() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    fresh = compute_event(
        "NVDA",
        [_item("a", "NVDA earnings miss", as_of)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert fresh.raw_value == pytest.approx(-0.63)
    assert fresh.score == pytest.approx(_score(-0.63))
    assert dict(fresh.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value
    assert "Severe negative event" in fresh.risks

    stale = compute_event(
        "NVDA",
        [_item("b", "NVDA earnings miss", as_of - timedelta(hours=20))],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    freshness = math.exp(-0.05 * 20)
    assert freshness < 0.5
    assert dict(stale.details)["event_risk"] == EventRisk.NONE.value
    assert stale.raw_value == pytest.approx(-0.9 * freshness * 0.7)


def test_analyst_downgrade_is_negative_but_not_severe() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    result = compute_event(
        "NVDA",
        [_item("a", "Shares were downgraded to hold", as_of)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert result.raw_value == pytest.approx(-0.55 * 0.6)
    assert dict(result.details)["event_risk"] == EventRisk.NONE.value


def test_articles_older_than_72_hours_are_dropped_and_the_boundary_is_kept() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    too_old = compute_event(
        "NVDA",
        [_item("a", "NVDA earnings beat", as_of - timedelta(hours=73))],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert too_old.reasons == ("no qualifying events",)

    boundary = compute_event(
        "NVDA",
        [_item("b", "NVDA earnings beat", as_of - timedelta(hours=72))],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    freshness = math.exp(-0.05 * 72)
    assert boundary.raw_value == pytest.approx(0.9 * freshness * 0.7)
    assert boundary.reasons == ("Earnings beat",)
