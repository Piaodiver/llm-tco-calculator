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
    """Every number in this project must be traceable to a source and a date.

    Two URLs, because the figures come from two pages: the pricing page carries
    no per-model context windows, so those are cited separately rather than
    attributed to a page that does not state them.
    """
    assert pricing.SOURCE_URL.startswith("https://")
    assert pricing.CONTEXT_SOURCE_URL.startswith("https://")
    assert pricing.CONTEXT_SOURCE_URL != pricing.SOURCE_URL
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


# The breakeven rate each model's prices imply. These are the figures the
# README quotes; if a price change moves one, this fails rather than the README
# going quietly out of date.
#
# This used to be a single assertion. Until 2026-10-05 the lineup was Haiku 4.5,
# Sonnet 5 and Opus 5, all reading at 10% of input and writing at 1.25x, so all
# three landed on exactly 21.7% and the README could quote one number. Opus 5.5
# reads at 5% of input and broke the tie. The constant became a range, which is
# the failure this file exists to produce.
EXPECTED_BREAKEVEN = {
    "claude-haiku-4-5": 0.217,
    "claude-sonnet-5-5": 0.217,
    "claude-opus-5-5": 0.208,
    "claude-fable-5-1": 0.204,
}


@pytest.mark.parametrize("model_id,expected", sorted(EXPECTED_BREAKEVEN.items()))
def test_readme_breakeven_figure_per_model(model_id, expected):
    rate = pricing.get(model_id).cache_breakeven_hit_rate
    assert rate == pytest.approx(expected, abs=0.001)


def test_readme_breakeven_range_claim():
    """The README states a 20.4%-21.7% spread; pin both ends of it."""
    rates = [m.cache_breakeven_hit_rate for m in pricing.all_models()]
    assert min(rates) == pytest.approx(0.204, abs=0.001)
    assert max(rates) == pytest.approx(0.217, abs=0.001)


def test_every_priced_model_has_an_expected_breakeven():
    """A model added to pricing.py without a README figure is a silent gap."""
    assert set(EXPECTED_BREAKEVEN) == set(pricing.MODELS)


@pytest.mark.parametrize("model", pricing.all_models(), ids=lambda m: m.id)
def test_breakeven_floor_is_set_by_the_write_premium(model):
    """The README claims a ~20% floor that no read discount can get under.

    Breakeven is (write - input) / (write - read). Every model currently writes
    at 1.25x input, so as the read price falls to zero the expression converges
    on 0.25/1.25 = 20% and approaches it from above. That is the README's
    central claim -- that the write premium, not the advertised read discount,
    is what governs the decision -- and it is worth pinning precisely because it
    is the kind of structural statement that survives a price change.
    """
    assert model.cache_write_ratio == pytest.approx(1.25), "floor argument assumes a 1.25x write"

    free_reads = replace(model, cache_read=0.0)
    assert free_reads.cache_breakeven_hit_rate == pytest.approx(0.20)
    assert model.cache_breakeven_hit_rate > free_reads.cache_breakeven_hit_rate
    assert 0.20 < model.cache_breakeven_hit_rate < 0.22


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
    for model in pricing.all_models():
        rate = model.cache_breakeven_hit_rate
        scaled = f"{rate * 100:.1f}%"
        assert 20.0 <= float(scaled.rstrip("%")) <= 22.0, f"{model.id}: {scaled}"
        assert f"{rate:.1f}%" == "0.2%"  # the bug this guards against


def test_caching_below_breakeven_is_a_net_loss():
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.10, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5-5")
    assert result["is_net_loss"]
    assert result["annual_savings"] < 0


def test_caching_above_breakeven_saves_money():
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.90, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5-5")
    assert not result["is_net_loss"]
    assert result["savings_pct"] > 0.3


def test_caching_cannot_touch_the_output_bill():
    """Caching discounts input only, so headline savings are always diluted.

    A 90% hit rate on the prefix is nowhere near a 90% cut to the bill -- on an
    output-heavy workload it is a fraction of that. This is the arithmetic
    behind the README's "caching is not a 90% discount" claim.
    """
    w = replace(tco.PRESETS["coding_agent"], cache_hit_rate=0.90, use_caching=True)
    result = tco.caching_savings(w, "claude-opus-5-5")

    uncached = tco.annual_cost(replace(w, use_caching=False), "claude-opus-5-5")
    prefix_share = uncached.prefix_cost / uncached.annual

    # Total savings can never exceed the share of the bill the prefix represents.
    assert result["savings_pct"] < prefix_share
    # Output is priced 5x input, so 6K of generation costs at least as much as
    # a 30K prefix re-read on every turn. That is why capping output length is
    # often a bigger lever than caching.
    assert uncached.output_cost >= uncached.prefix_cost


def test_batch_halves_the_bill():
    w = tco.PRESETS["document_processing"]
    result = tco.batch_savings(w, "claude-sonnet-5-5")
    assert result["savings_pct"] == pytest.approx(pricing.BATCH_DISCOUNT)


def test_monthly_times_twelve_equals_annual():
    b = tco.annual_cost(tco.PRESETS["customer_support"], "claude-sonnet-5-5")
    assert b.monthly * 12 == pytest.approx(b.annual)


def test_breakdown_components_sum_to_total():
    b = tco.annual_cost(tco.PRESETS["coding_agent"], "claude-opus-5-5")
    assert b.prefix_cost + b.variable_cost + b.output_cost == pytest.approx(b.annual)


def test_context_window_overflow_is_flagged():
    """Haiku's 200K window should reject a workload the 1M models accept."""
    big = replace(tco.PRESETS["coding_agent"], cached_prefix_tokens=400_000)
    assert not tco.annual_cost(big, "claude-haiku-4-5").fits_context
    assert tco.annual_cost(big, "claude-sonnet-5-5").fits_context


def test_model_ranking_is_cheapest_first():
    results = tco.compare_models(tco.PRESETS["customer_support"])
    assert [b.annual for b in results] == sorted(b.annual for b in results)
    assert results[0].model_id == "claude-haiku-4-5"


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        pricing.get("gpt-4")
