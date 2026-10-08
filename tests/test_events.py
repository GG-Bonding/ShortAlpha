import math
from datetime import date, datetime, time, timedelta

import pytest

from shortalpha.config import load_config
from shortalpha.domain import DailyBar, EventRisk, NewsItem
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


def _item(
    item_id: str,
    headline: str,
    published: datetime,
    *,
    summary: str = "",
    symbols: tuple[str, ...] = ("NVDA",),
):
    return NewsItem(
        id=item_id,
        symbols=symbols,
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
    provider = FixtureNewsProvider.from_json(
        project_root() / "fixtures" / "news" / "sample_news.json"
    )
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
    result = compute_event(
        "NVDA", [first, second], as_of=as_of, event=event, rules=rules, weight=25
    )
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
    assert dict(stale.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value
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


def test_reaction_uses_the_close_before_the_headline_and_the_close_before_the_signal() -> None:
    event, rules = _loaded()
    session = date(2024, 6, 20)
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    news_at = datetime(2024, 6, 18, 15, 0, tzinfo=NY)
    bars = [
        _daily("NVDA", date(2024, 6, 17), 100),
        _daily("NVDA", date(2024, 6, 18), 110),
    ]
    result = compute_event(
        "NVDA",
        [_item("n1", "NVDA earnings beat", news_at)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
        rules_sha256="rules-hash",
        bars=bars,
        session=session,
    )
    assert len(result.events) == 1
    observed = result.events[0]
    assert observed.news_id == "n1"
    assert observed.rule_id == "earnings_beat"
    assert observed.label == "Earnings beat"
    assert observed.source == "fixture"
    assert observed.published_at == news_at
    assert observed.rules_sha256 == "rules-hash"
    assert observed.content_sha256
    assert observed.reaction == pytest.approx(0.10)
    assert dict(result.details)["post_event_reaction"] == f"{observed.reaction:.10f}"


def test_overnight_news_has_no_completed_reaction_yet() -> None:
    event, rules = _loaded()
    session = date(2024, 6, 20)
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    news_at = datetime(2024, 6, 19, 20, 0, tzinfo=NY)
    bars = [_daily("NVDA", date(2024, 6, 18), 100)]
    result = compute_event(
        "NVDA",
        [_item("n1", "NVDA earnings beat", news_at)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
        bars=bars,
        session=session,
    )
    assert result.events[0].reaction is None
    assert dict(result.details)["post_event_reaction"] == "missing"


def test_acquisition_sides_are_not_the_same_direction() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "deal",
        "AAA to acquire BBB",
        as_of,
        symbols=("AAA", "BBB"),
    )
    buyer = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    target = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert buyer.events[0].rule_id == "ma_buyer"
    assert buyer.raw_value < 0
    assert target.events[0].rule_id == "ma_announced"
    assert target.raw_value == pytest.approx(0.95 * 0.65)
    assert target.score == pytest.approx(_score(0.95 * 0.65))
    assert buyer.score != pytest.approx(target.score)


def test_a_company_name_can_identify_the_buyer() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "deal",
        "Shopify to acquire Deliveroo",
        as_of,
        symbols=("SHOP", "DLVY"),
    )
    names = {
        "SHOP": "Shopify Inc. Class A Subordinate Voting Shares",
        "DLVY": "Deliveroo plc",
    }
    buyer = compute_event(
        "SHOP", [item], as_of=as_of, event=event, rules=rules, weight=25, names=names
    )
    target = compute_event(
        "DLVY", [item], as_of=as_of, event=event, rules=rules, weight=25, names=names
    )
    assert buyer.events[0].rule_id == "ma_buyer"
    assert target.events[0].label == "Acquisition target"


def test_the_target_phrase_stays_positive_for_the_company_being_acquired() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item("deal", "BBB agrees to be acquired by AAA", as_of, symbols=("AAA", "BBB"))
    target = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    buyer = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert target.events[0].rule_id == "ma_announced"
    assert buyer.events[0].rule_id == "ma_buyer"


def test_a_merger_agreement_without_a_role_is_not_scored() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item("deal", "AAA and BBB sign a merger agreement", as_of, symbols=("AAA", "BBB"))
    result = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert result.reasons == ("no qualifying events",)
    assert "Ambiguous ma event ignored" in result.risks


def test_filing_a_lawsuit_is_not_the_same_as_being_sued() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "suit",
        "AAA files lawsuit against BBB",
        as_of,
        symbols=("AAA", "BBB"),
    )
    filer = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    defendant = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert filer.reasons == ("no qualifying events",)
    assert dict(filer.details)["event_risk"] == EventRisk.NONE.value
    assert defendant.events[0].rule_id == "legal_action"
    assert defendant.raw_value < 0
    assert dict(defendant.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value

    hit = compute_event(
        "AAA",
        [_item("hit", "AAA hit with lawsuit", as_of, symbols=("AAA",))],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert dict(hit.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value
    alone = compute_event(
        "AAA",
        [_item("file", "AAA files lawsuit", as_of, symbols=("AAA",))],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert alone.reasons == ("no qualifying events",)
    assert dict(alone.details)["event_risk"] == EventRisk.NONE.value


def test_an_earnings_sentence_does_not_score_the_other_tagged_symbol() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item("earn", "AAA earnings beat. BBB was unchanged.", as_of, symbols=("AAA", "BBB"))
    subject = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    bystander = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert subject.reasons == ("Earnings beat",)
    assert bystander.reasons == ("no qualifying events",)


def test_a_fresh_offering_outweighs_an_earnings_beat_and_a_target_cut_does_not() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    offered = compute_event(
        "NVDA",
        [_item("both", "NVDA earnings beat after a shelf registration", as_of)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert offered.raw_value < 0
    assert dict(offered.details)["event_risk"] == EventRisk.NONE.value
    cut = compute_event(
        "NVDA",
        [_item("cut", "NVDA earnings beat and the firm lowers price target", as_of)],
        as_of=as_of,
        event=event,
        rules=rules,
        weight=25,
    )
    assert cut.raw_value > 0
    assert dict(cut.details)["event_risk"] == EventRisk.NONE.value
    assert "Earnings beat" in cut.reasons
    assert "Price target cut" in cut.risks


def test_a_guidance_cut_stays_severe_through_the_next_morning() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    for hours in (13, 14, 17):
        result = compute_event(
            "NVDA",
            [_item(f"g{hours}", "NVDA guidance cut", as_of - timedelta(hours=hours))],
            as_of=as_of,
            event=event,
            rules=rules,
            weight=25,
        )
        assert dict(result.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value
        freshness = math.exp(-0.05 * hours)
        assert result.raw_value == pytest.approx(-0.85 * freshness * 0.7)

    friday = datetime(2024, 6, 14, 16, 0, tzinfo=NY)
    monday = datetime(2024, 6, 17, 9, 0, tzinfo=NY)
    assert (monday - friday).total_seconds() / 3600 == pytest.approx(65)
    weekend = compute_event(
        "NVDA",
        [_item("weekend", "NVDA guidance cut", friday)],
        as_of=monday,
        event=event,
        rules=rules,
        weight=25,
    )
    assert dict(weekend.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value
    aged = math.exp(-0.05 * 65)
    assert weekend.raw_value == pytest.approx(-0.85 * aged * 0.7)


def test_a_comma_clause_does_not_give_the_beat_to_the_other_company() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "both",
        "AAA earnings beat, while BBB was unchanged",
        as_of,
        symbols=("AAA", "BBB"),
    )
    subject = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    bystander = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert subject.reasons == ("Earnings beat",)
    assert bystander.reasons == ("no qualifying events",)
    assert dict(bystander.details)["event_risk"] == EventRisk.NONE.value


def test_a_single_company_upgrade_still_scores_without_a_second_name() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item("one", "The broker issued an analyst upgrade", as_of)
    result = compute_event("NVDA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert result.reasons == ("Analyst upgrade",)


def test_an_analyst_upgrade_does_not_score_the_other_company() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "rating",
        "AAA analyst upgrade while BBB was unchanged",
        as_of,
        symbols=("AAA", "BBB"),
    )
    subject = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    bystander = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert subject.raw_value == pytest.approx(0.55 * 0.6)
    assert subject.reasons == ("Analyst upgrade",)
    assert bystander.reasons == ("no qualifying events",)
    assert bystander.raw_value == 0


def test_each_company_keeps_the_earnings_event_in_its_own_clause() -> None:
    event, rules = _loaded()
    as_of = datetime(2024, 6, 20, 9, 0, tzinfo=NY)
    item = _item(
        "split",
        "AAA earnings beat while BBB earnings miss",
        as_of,
        symbols=("AAA", "BBB"),
    )
    beat = compute_event("AAA", [item], as_of=as_of, event=event, rules=rules, weight=25)
    miss = compute_event("BBB", [item], as_of=as_of, event=event, rules=rules, weight=25)
    assert beat.reasons == ("Earnings beat",)
    assert beat.raw_value > 0
    assert dict(beat.details)["event_risk"] == EventRisk.NONE.value
    assert miss.reasons == ()
    assert "Earnings miss" in miss.risks
    assert miss.raw_value < 0
    assert dict(miss.details)["event_risk"] == EventRisk.SEVERE_NEGATIVE.value


def _daily(symbol: str, day: date, close: float) -> DailyBar:
    stamp = datetime.combine(day, time(16, 0), tzinfo=NY)
    return DailyBar(
        symbol=symbol,
        session_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        event_time=stamp,
        published_at=stamp,
        available_at=stamp,
        source="fixture",
    )
