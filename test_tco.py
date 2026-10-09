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


def test_cheapest_model_may_be_one_that_cannot_run_the_workload():
    """Price ranking alone will name a model that does not fit. It must not be
    the one quoted.

    A 300K prefix exceeds Haiku 4.5's 200K window, but Haiku is still the
    cheapest row -- the arithmetic is right and the recommendation is
    impossible. `viable()` is what stands between that row and a headline.
    """
    big = replace(tco.PRESETS["coding_agent"], cached_prefix_tokens=300_000)
    ranked = tco.compare_models(big)

    assert ranked[0].model_id == "claude-haiku-4-5"
    assert not ranked[0].fits_context

    runnable = tco.viable(ranked)
    assert runnable[0].model_id == "claude-sonnet-5-5"
    assert all(b.fits_context for b in runnable)
    assert len(runnable) == len(ranked) - 1


def test_viable_is_empty_when_nothing_fits():
    """Beyond every window, there is no cheapest option to name at all."""
    huge = replace(tco.PRESETS["coding_agent"], cached_prefix_tokens=2_000_000)
    assert tco.viable(tco.compare_models(huge)) == []


def test_output_cap_is_checked_separately_from_the_context_window():
    """Fitting the window is not a green light; the output cap bites first.

    100K of generation sits comfortably inside every 1M context window and
    exceeds every model's single-response cap. Checking only the window quotes
    a confident annual figure for a request no model will serve.
    """
    # 150K: inside every 1M window, past every model's cap (128K at best).
    chatty = replace(
        tco.PRESETS["coding_agent"],
        cached_prefix_tokens=1_000, variable_input_tokens=1_000, output_tokens=150_000,
    )
    b = tco.annual_cost(chatty, "claude-sonnet-5-5")

    assert b.fits_context          # the window is not the problem
    assert not b.fits_output_limit  # the cap is
    assert not b.is_runnable
    assert tco.viable(tco.compare_models(chatty)) == []


def test_haiku_output_cap_is_lower_than_the_rest():
    """64K against 128K -- a workload can be runnable on three models, not four."""
    # 80K: over Haiku's 64K cap, inside the 128K the other three allow.
    w = replace(
        tco.PRESETS["coding_agent"],
        cached_prefix_tokens=1_000, variable_input_tokens=1_000, output_tokens=80_000,
    )
    assert not tco.annual_cost(w, "claude-haiku-4-5").fits_output_limit
    assert tco.annual_cost(w, "claude-sonnet-5-5").fits_output_limit
    runnable = {b.model_id for b in tco.viable(tco.compare_models(w))}
    assert runnable == {"claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}


def test_token_description_speaks_in_pages_not_tokens():
    """The form is unfillable without this bridge, so pin that it renders."""
    assert "words" in tco.describe_tokens(8_000, "claude-sonnet-5-5")
    assert "pages" in tco.describe_tokens(8_000, "claude-sonnet-5-5")
    # A short reply is sub-page; claiming "0 pages" would read as a bug.
    assert "pages" not in tco.describe_tokens(100, "claude-sonnet-5-5")


def test_a_workload_with_no_prefix_has_nothing_to_cache():
    """Caching analysis is vacuous without a prefix, and must say so.

    Savings are exactly zero, so `is_net_loss` is False and the old code
    reported "caching saves $0 (0%)" in a success box -- while a warning above
    it, keyed on the hit rate alone, said caching was costing money. Two
    contradictory claims about a workload where caching does nothing at all.
    """
    w = replace(
        tco.PRESETS["customer_support"],
        cached_prefix_tokens=0, use_caching=True, cache_hit_rate=0.15,
    )
    assert not w.has_cacheable_prefix

    result = tco.caching_savings(w, "claude-sonnet-5-5")
    assert result["annual_savings"] == 0
    assert not result["is_net_loss"]        # zero is not a loss...
    assert result["savings_pct"] == 0       # ...and not a saving either

    # The hit rate is below breakeven, which is exactly when the contradicting
    # warning used to fire. has_cacheable_prefix is what now suppresses it.
    assert w.cache_hit_rate < pricing.get("claude-sonnet-5-5").cache_breakeven_hit_rate

    # And caching genuinely cannot move the bill in either direction.
    on = tco.annual_cost(replace(w, use_caching=True), "claude-sonnet-5-5").annual
    off = tco.annual_cost(replace(w, use_caching=False), "claude-sonnet-5-5").annual
    assert on == off


def test_token_description_is_empty_when_there_is_nothing_to_describe():
    """"0 words" under an input reads as a failed render, not as a quantity."""
    assert tco.describe_tokens(0, "claude-sonnet-5-5") == ""
    assert tco.describe_tokens(-5, "claude-sonnet-5-5") == ""
    # A handful of tokens rounds to zero words; say "under 10" instead.
    assert tco.describe_tokens(1, "claude-sonnet-5-5") == "≈ under 10 words"
    # No count may render as a literal zero, at any size, on any model.
    for model_id in pricing.MODELS:
        for n in range(1, 40):
            assert tco.describe_tokens(n, model_id) != "≈ 0 words"


def test_runtime_dependencies_are_pinned():
    """Same discipline as FETCHED_AT, applied to the runtime.

    A `>=` range hands the deployment to whatever resolves on build day, so a
    demo that rebuilds on its own can stop matching the README with nobody
    touching the repository. Two of these had already drifted a major version
    past their declared floor before this was caught.
    """
    import pathlib

    for name in ("requirements.txt", "requirements-dev.txt"):
        path = pathlib.Path(__file__).parent / name
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-r "):
                continue
            assert "==" in line, f"{name}: {line!r} is not pinned"
            assert not any(c in line for c in "><~"), f"{name}: {line!r} is a range"


def test_token_conversion_is_model_dependent():
    """Tokenizers differ by generation; the same count is not the same text.

    This is the README's own caveat about cross-generation comparison, applied
    to the input side: Haiku 4.5 predates the current tokenizer and fits more
    words into the same number of tokens.
    """
    assert tco.words_to_tokens(10_000, "claude-haiku-4-5") < tco.words_to_tokens(
        10_000, "claude-sonnet-5-5"
    )
    # Round-trips back to roughly where it started.
    for model_id in pricing.MODELS:
        tokens = tco.words_to_tokens(50_000, model_id)
        words = tokens * pricing.get(model_id).words_per_token
        assert words == pytest.approx(50_000, rel=0.01)


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        pricing.get("gpt-4")
