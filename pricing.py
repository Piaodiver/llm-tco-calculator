"""Claude API pricing data.

SINGLE SOURCE OF TRUTH for all prices in this project. Nothing else in the
codebase should hardcode a dollar figure.

Prices change. This file records where the numbers came from and when, so a
reader can tell at a glance whether they are stale. Every figure below was read
off the official pricing page on FETCHED_AT -- none of it is from memory.
"""

from dataclasses import dataclass

# --- Provenance -------------------------------------------------------------
# Update these together whenever you refresh the numbers.
#
# Two sources, because the figures come from two places. The pricing page does
# not publish per-model context windows, so citing it for them would be exactly
# the kind of unsourced number this file exists to prevent.
SOURCE_URL = "https://claude.com/pricing"                    # every dollar figure
CONTEXT_SOURCE_URL = (                                        # context windows only
    "https://platform.claude.com/docs/en/about-claude/models/overview"
)
FETCHED_AT = "2026-10-05"
FETCHED_NOTE = (
    "Dollar figures read from the pricing page; context windows from the models "
    "overview, which is where they are actually published. Cache-write figures "
    "are the 5-minute TTL rate; a 1-hour TTL is offered at a higher rate that "
    "this model does not yet cover. The lineup below is the one the pricing page "
    "lists as current -- Opus 5 and Sonnet 5 moved to its legacy section on "
    "2026-10-05 and are no longer carried here."
)

# All prices are US dollars per 1,000,000 tokens.
PER_MTOK = 1_000_000

# The Batch API discount applies to input and output alike.
BATCH_DISCOUNT = 0.50


# Rough words-per-token for English prose, taken from the context-window note
# on the models overview: 1M tokens is about 555K words on the current
# tokenizer (introduced with Opus 4.7), about 750K on the generation before it.
#
# This exists so that someone who knows their documents in pages can fill in
# the form at all. It is an order-of-magnitude aid, not a tokenizer -- code,
# tables, and non-English text all land well outside it. The app says so.
WORDS_PER_TOKEN_CURRENT = 0.555
WORDS_PER_TOKEN_LEGACY = 0.750

# A page of single-spaced prose. Arbitrary but stated, which beats a number
# that is arbitrary and hidden.
WORDS_PER_PAGE = 500


@dataclass(frozen=True)
class Model:
    """Per-token prices and the model facts the cost model needs.

    Prices come from SOURCE_URL; context_window, max_output_tokens and
    words_per_token come from CONTEXT_SOURCE_URL.
    """

    id: str
    label: str
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    context_window: int
    # The synchronous Messages API output cap. Separate from the context
    # window, and smaller: a request can fit the window and still ask for more
    # generation than the model will produce in one response.
    max_output_tokens: int
    words_per_token: float

    @property
    def cache_read_ratio(self) -> float:
        """Cache-read price as a fraction of the base input price."""
        return self.cache_read / self.input

    @property
    def cache_write_ratio(self) -> float:
        """Cache-write price as a multiple of the base input price."""
        return self.cache_write_5m / self.input

    @property
    def cache_breakeven_hit_rate(self) -> float:
        """Cache hit rate below which caching costs MORE than not caching.

        A cached prefix is billed at cache_write on a miss and cache_read on a
        hit. Writing costs more than a plain input token, so a low hit rate is a
        net loss. Solving

            h * cache_read + (1 - h) * cache_write = input

        for h gives the point where caching exactly breaks even.
        """
        return (self.cache_write_5m - self.input) / (self.cache_write_5m - self.cache_read)


MODELS = {
    "claude-opus-5-5": Model(
        id="claude-opus-5-5",
        label="Claude Opus 5.5",
        input=4.00,
        output=20.00,
        # Note the ratio: a cache read is 5% of input here, where the other
        # models sit at 10% (Fable 5.1 at 2.5%). That is why Opus 5.5 has its
        # own breakeven and why these four no longer scale as one price curve.
        cache_read=0.20,
        cache_write_5m=5.00,
        context_window=1_000_000,
        max_output_tokens=128_000,
        words_per_token=WORDS_PER_TOKEN_CURRENT,
    ),
    "claude-sonnet-5-5": Model(
        id="claude-sonnet-5-5",
        label="Claude Sonnet 5.5",
        input=2.00,
        output=10.00,
        cache_read=0.20,
        cache_write_5m=2.50,
        context_window=1_000_000,
        max_output_tokens=128_000,
        words_per_token=WORDS_PER_TOKEN_CURRENT,
    ),
    "claude-haiku-4-5": Model(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5",
        input=1.00,
        output=5.00,
        cache_read=0.10,
        cache_write_5m=1.25,
        context_window=200_000,
        max_output_tokens=64_000,
        words_per_token=WORDS_PER_TOKEN_LEGACY,
    ),
    "claude-fable-5-1": Model(
        id="claude-fable-5-1",
        label="Claude Fable 5.1",
        input=10.00,
        output=50.00,
        cache_read=0.25,
        cache_write_5m=12.50,
        context_window=1_000_000,
        max_output_tokens=128_000,
        words_per_token=WORDS_PER_TOKEN_CURRENT,
    ),
}

# Order used wherever models are displayed as a list.
DISPLAY_ORDER = [
    "claude-haiku-4-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
]


def get(model_id: str) -> Model:
    if model_id not in MODELS:
        raise KeyError(f"unknown model {model_id!r}; known: {sorted(MODELS)}")
    return MODELS[model_id]


def all_models() -> list[Model]:
    return [MODELS[m] for m in DISPLAY_ORDER]
