"""Tests for the cost model.

These exist less for regression safety than to pin down the claims the README
makes. If the breakeven figure or the caching-trap example ever stops being
true, a test fails rather than the README quietly becoming wrong.
"""

from dataclasses import replace

import pytest

import pricing
import tco


def test_pricing_provenance_is_recorded():
    """Every number in this project must be traceable to a source and a date."""
    assert pricing.SOURCE_URL.startswith("https://")
    assert len(pricing.FETCHED_AT) == 10  # YYYY-MM-DD


@pytest.mark.parametrize("model", pricing.all_models(), ids=lambda m: m.id)
def test_cache_write_costs_more_than_plain_input(model):
    """The premise of the whole caching argument: a miss is worse than no cache."""
    assert model.cache_write_5m > model.input
    assert model.cache_read < model.input


@pytest.mark.parametrize("model", pricing.all_models(), ids=lambda m: m.id)
def test_breakeven_hit_rate_is_a_real_breakeven(model):
    """At the breakeven rate, caching and not caching must cost the same."""
    h = model.cache_breakeven_hit_rate
    effective = h * model.cache_read + (1 - h) * model.cache_write_5m
    assert effective == pytest.approx(model.input)
    assert 0 < h < 1


def test_readme_breakeven_claim_is_217_percent():
    """The README states 21.7% for the 10%-read / 1.25x-write models."""
    for model_id in ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"):
        rate = pricing.get(model_id).cache_breakeven_hit_rate
        assert rate == pytest.approx(0.217, abs=0.001)


def test_breakeven_needs_scaling_before_a_printf_percent_format():
    """The pricing table renders breakeven with a printf "%.1f%%" format string.

    That format appends a literal % -- it does not scale. Handing it the raw
    0.217 fraction renders "0.2%", wrong by 100x, in the one table a sceptical
    reader opens to check the 21.7% claimed in the prose above it. app.py
    multiplies by 100 for exactly this reason; this pins the arithmetic that
    fix depends on.

    Note the limit: this asserts the conversion is correct, not that app.py
    still performs it. Guarding that would mean lifting the row construction
    out of app.py, which tests deliberately never import (see Decision 2).
    """
    for model_id in ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"):
        rate = pricing.get(model_id).cache_breakeven_hit_rate
        assert f"{rate * 100:.1f}%" == "21.7%"
        assert f"{rate:.1f}%" == "0.2%"  # the bug this guards against

    fable = pricing.get("claude-fable-5-1").cache_breakeven_hit_rate
    assert f"{fable * 100:.1f}%" == "20.4%"


def test_caching_below_breakeven_is_a_net_loss():
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.10, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5")
    assert result["is_net_loss"]
    assert result["annual_savings"] < 0


def test_caching_above_breakeven_saves_money():
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.90, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5")
    assert not result["is_net_loss"]
    assert result["savings_pct"] > 0.3


def test_caching_cannot_touch_the_output_bill():
    """Caching discounts input only, so headline savings are always diluted.

    A 90% hit rate on the prefix is nowhere near a 90% cut to the bill -- on an
    output-heavy workload it is a fraction of that. This is the arithmetic
    behind the README's "caching is not a 90% discount" claim.
    """
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.90, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5")

    uncached = tco.annual_cost(replace(w, use_caching=False), "claude-opus-5")
    prefix_share = uncached.prefix_cost / uncached.annual

    # Total savings can never exceed the share of the bill the prefix represents.
    assert result["savings_pct"] < prefix_share
    # Output is priced 5x input, so 6K of generation costs at least as much as
    # a 30K prefix re-read on every turn. That is why capping output length is
    # often a bigger lever than caching.
    assert uncached.output_cost >= uncached.prefix_cost


def test_batch_halves_the_bill():
    w = tco.PRESETS["document_processing"]
    result = tco.batch_savings(w, "claude-sonnet-5")
    assert result["savings_pct"] == pytest.approx(pricing.BATCH_DISCOUNT)


def test_monthly_times_twelve_equals_annual():
    b = tco.annual_cost(tco.PRESETS["customer_support"], "claude-sonnet-5")
    assert b.monthly * 12 == pytest.approx(b.annual)


def test_breakdown_components_sum_to_total():
    b = tco.annual_cost(tco.PRESETS["coding_agent"], "claude-opus-5")
    assert b.prefix_cost + b.variable_cost + b.output_cost == pytest.approx(b.annual)


def test_context_window_overflow_is_flagged():
    """Haiku's 200K window should reject a workload the 1M models accept."""
    big = replace(tco.PRESETS["coding_agent"], cached_prefix_tokens=400_000)
    assert not tco.annual_cost(big, "claude-haiku-4-5").fits_context
    assert tco.annual_cost(big, "claude-sonnet-5").fits_context


def test_model_ranking_is_cheapest_first():
    results = tco.compare_models(tco.PRESETS["customer_support"])
    assert [b.annual for b in results] == sorted(b.annual for b in results)
    assert results[0].model_id == "claude-haiku-4-5"


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        pricing.get("gpt-4")
