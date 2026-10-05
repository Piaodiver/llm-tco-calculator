"""Cost model for running a Claude workload in production.

Deliberately free of any UI import: everything here is a pure function over
plain data, so it can be unit-tested and reused. app.py is a thin rendering
layer on top.

The model answers one question: given a described workload, what does a year of
it cost, and where does the money actually go?
"""

from dataclasses import dataclass, replace

import pricing

DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Workload:
    """One deployment scenario, described in the terms a customer would use.

    Input tokens are split in two because they are priced differently once
    caching is on:

      cached_prefix_tokens  -- the stable head of every request (system prompt,
                               tool definitions, retrieved policy documents).
                               Eligible for prompt caching.
      variable_input_tokens -- the part that differs per request (the user's
                               actual question, retrieved chunks). Never cached.
    """

    name: str
    requests_per_day: int
    cached_prefix_tokens: int
    variable_input_tokens: int
    output_tokens: int
    cache_hit_rate: float = 0.0   # 0.0-1.0; ignored when use_caching is False
    use_caching: bool = False
    use_batch: bool = False

    @property
    def total_input_tokens(self) -> int:
        return self.cached_prefix_tokens + self.variable_input_tokens

    @property
    def context_tokens(self) -> int:
        """Peak tokens in one request -- used to check it fits the window."""
        return self.total_input_tokens + self.output_tokens


@dataclass(frozen=True)
class CostBreakdown:
    """Annual cost of one workload on one model, split by where it is spent."""

    model_id: str
    model_label: str
    workload_name: str

    prefix_cost: float      # the cacheable head of each request
    variable_cost: float    # per-request unique input
    output_cost: float      # generation

    fits_context: bool
    context_tokens: int
    context_window: int

    # Two separate limits. A request can sit well inside the context window and
    # still ask for more generation than the model emits in one response, so
    # fitting the window is not on its own a green light.
    fits_output_limit: bool
    output_tokens: int
    max_output_tokens: int

    @property
    def is_runnable(self) -> bool:
        """Whether this model can actually serve the request, on both limits."""
        return self.fits_context and self.fits_output_limit

    @property
    def input_cost(self) -> float:
        return self.prefix_cost + self.variable_cost

    @property
    def annual(self) -> float:
        return self.prefix_cost + self.variable_cost + self.output_cost

    @property
    def monthly(self) -> float:
        return self.annual / 12

    @property
    def output_share(self) -> float:
        return self.output_cost / self.annual if self.annual else 0.0


def _prefix_rate(model: pricing.Model, workload: Workload) -> float:
    """Effective $/MTok for the cacheable prefix, given the caching settings.

    With caching off, the prefix is billed as ordinary input. With caching on,
    each request either hits (cache_read) or misses (cache_write) -- and a miss
    costs more than not caching at all, which is why a low hit rate is a net
    loss rather than merely a wash.
    """
    if not workload.use_caching:
        return model.input
    h = workload.cache_hit_rate
    return h * model.cache_read + (1 - h) * model.cache_write_5m


def annual_cost(workload: Workload, model_id: str) -> CostBreakdown:
    """Cost of running `workload` on `model_id` for one year."""
    model = pricing.get(model_id)
    reqs = workload.requests_per_day * DAYS_PER_YEAR

    # Batch processing discounts input and output alike.
    #
    # NOTE: the model lets you switch batch and caching on together because the
    # price sheet allows it, but see README -- in practice a batch job spread
    # over hours rarely lands inside the 5-minute cache TTL, so the hit rate you
    # enter should be near zero. The UI warns about this rather than silently
    # producing an optimistic number.
    multiplier = (1 - pricing.BATCH_DISCOUNT) if workload.use_batch else 1.0

    def cost(tokens: int, rate: float) -> float:
        return reqs * tokens / pricing.PER_MTOK * rate * multiplier

    return CostBreakdown(
        model_id=model.id,
        model_label=model.label,
        workload_name=workload.name,
        prefix_cost=cost(workload.cached_prefix_tokens, _prefix_rate(model, workload)),
        variable_cost=cost(workload.variable_input_tokens, model.input),
        output_cost=cost(workload.output_tokens, model.output),
        fits_context=workload.context_tokens <= model.context_window,
        context_tokens=workload.context_tokens,
        context_window=model.context_window,
        fits_output_limit=workload.output_tokens <= model.max_output_tokens,
        output_tokens=workload.output_tokens,
        max_output_tokens=model.max_output_tokens,
    )


def compare_models(workload: Workload, model_ids: list[str] | None = None) -> list[CostBreakdown]:
    """Same workload priced across several models, cheapest first.

    Includes models the workload does not fit -- each row carries its own
    `fits_context` flag, and a comparison table is the right place to show that
    a cheaper model exists but is too small. Anything that draws a *conclusion*
    from this list should filter it through `viable()` first.
    """
    ids = model_ids or pricing.DISPLAY_ORDER
    return sorted((annual_cost(workload, m) for m in ids), key=lambda b: b.annual)


def viable(breakdowns: list[CostBreakdown]) -> list[CostBreakdown]:
    """Only the models that can actually serve this request.

    Ranking on price alone will happily name a model that physically cannot run
    the request -- Haiku 4.5's 200K window against a 300K prefix is still the
    cheapest row in the table. Quoting that as "the cheapest option" is worse
    than quoting nothing: the arithmetic is right and the recommendation is
    impossible. Every headline that names a model goes through here.

    Checks both limits. The output cap bites in a different place from the
    context window -- a short prompt asking for 100K of generation clears the
    window on every model and the cap on none of them.
    """
    return [b for b in breakdowns if b.is_runnable]


def caching_savings(workload: Workload, model_id: str) -> dict:
    """What caching is actually worth for this workload -- which may be negative.

    Returns the annual delta against the same workload with caching off, plus
    the hit rate at which the two break even.
    """
    model = pricing.get(model_id)
    without = annual_cost(replace(workload, use_caching=False), model_id)
    with_cache = annual_cost(replace(workload, use_caching=True), model_id)
    delta = without.annual - with_cache.annual
    return {
        "annual_without_caching": without.annual,
        "annual_with_caching": with_cache.annual,
        "annual_savings": delta,          # negative means caching costs money
        "savings_pct": delta / without.annual if without.annual else 0.0,
        "breakeven_hit_rate": model.cache_breakeven_hit_rate,
        "is_net_loss": delta < 0,
    }


def batch_savings(workload: Workload, model_id: str) -> dict:
    """Annual delta from moving this workload to the Batch API."""
    sync = annual_cost(replace(workload, use_batch=False), model_id)
    batched = annual_cost(replace(workload, use_batch=True), model_id)
    delta = sync.annual - batched.annual
    return {
        "annual_sync": sync.annual,
        "annual_batch": batched.annual,
        "annual_savings": delta,
        "savings_pct": delta / sync.annual if sync.annual else 0.0,
    }


def describe_tokens(tokens: int, model_id: str) -> str:
    """Render a token count in units a customer actually knows.

    Nobody arrives at a pricing conversation knowing their prompt in tokens.
    They know it in pages of policy document, or in the length of a support
    reply. Without a bridge, the form in front of them is unfillable and the
    whole tool is for people who already have the answer.

    Deliberately coarse -- words to two significant figures, pages to the
    nearest half. Precision here would imply a tokenizer, and this is a ratio.
    """
    model = pricing.get(model_id)
    words = tokens * model.words_per_token
    pages = words / pricing.WORDS_PER_PAGE

    if words < 1000:
        words_text = f"{round(words / 10) * 10:,.0f} words"
    else:
        words_text = f"{words / 1000:.1f}K words".replace(".0K", "K")

    if pages < 0.5:
        return f"≈ {words_text}"
    rounded = round(pages * 2) / 2 if pages < 10 else round(pages)
    return f"≈ {words_text} · {rounded:,g} page{'' if rounded == 1 else 's'}"


def words_to_tokens(words: int, model_id: str) -> int:
    """The inverse: what a document of this length costs you in tokens."""
    return round(words / pricing.get(model_id).words_per_token)


def cost_per_request(workload: Workload, model_id: str) -> float:
    return annual_cost(workload, model_id).annual / (workload.requests_per_day * DAYS_PER_YEAR)


# --- Preset scenarios -------------------------------------------------------
# Recognisable shapes of enterprise workload, so the tool opens on something
# concrete rather than an empty form. Numbers are illustrative, not measured.

PRESETS = {
    "customer_support": Workload(
        name="Customer support agent",
        requests_per_day=50_000,
        cached_prefix_tokens=8_000,    # policy docs + tools, stable all day
        variable_input_tokens=1_200,   # the customer's message + history
        output_tokens=400,
        cache_hit_rate=0.90,
        use_caching=True,
    ),
    "document_processing": Workload(
        name="Nightly document extraction",
        requests_per_day=20_000,
        cached_prefix_tokens=2_000,    # extraction schema
        variable_input_tokens=15_000,  # the document itself -- never repeats
        output_tokens=800,
        cache_hit_rate=0.0,
        use_caching=False,
        use_batch=True,                # nobody is waiting on it
    ),
    "coding_agent": Workload(
        name="Internal coding agent",
        requests_per_day=2_000,
        cached_prefix_tokens=30_000,   # repo context, re-read every turn
        variable_input_tokens=3_000,
        output_tokens=6_000,           # agents generate a lot
        cache_hit_rate=0.75,
        use_caching=True,
    ),
    "low_volume_pilot": Workload(
        name="Low-volume pilot (caching trap)",
        requests_per_day=200,
        cached_prefix_tokens=20_000,
        variable_input_tokens=500,
        output_tokens=600,
        cache_hit_rate=0.15,           # below breakeven -- caching loses money
        use_caching=True,
    ),
}
