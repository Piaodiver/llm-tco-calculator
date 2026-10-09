"""Enterprise LLM deployment TCO calculator.

A pre-sales tool: describe a workload the way a customer describes it, get the
annual bill, and see which architectural choices actually move the number.

All arithmetic lives in tco.py; this file only renders.
"""

import altair as alt
import pandas as pd
from dataclasses import replace as _replace
import streamlit as st

import pricing
import tco

st.set_page_config(page_title="LLM Deployment TCO Calculator", layout="wide")

USD = "${:,.0f}"


def money(x: float) -> str:
    return USD.format(x)


# --- Sidebar: describe the workload ----------------------------------------

st.sidebar.title("Workload")

# Both selectboxes are keyed so the "Show me" buttons on the findings cards can
# drive them. Seed the keys rather than passing index=, which session state
# would override anyway.
st.session_state.setdefault("scenario", "customer_support")
st.session_state.setdefault("model", "claude-sonnet-5-5")

preset_labels = {k: v.name for k, v in tco.PRESETS.items()}
preset_key = st.sidebar.selectbox(
    "Start from a scenario",
    options=list(tco.PRESETS),
    key="scenario",
    format_func=lambda k: preset_labels[k],
    help="Presets are illustrative shapes of enterprise workload, not measured figures. Adjust everything below.",
)
preset = tco.PRESETS[preset_key]

st.sidebar.caption("Volume")
requests_per_day = st.sidebar.number_input(
    "Requests per day", min_value=1, max_value=100_000_000,
    value=preset.requests_per_day, step=1_000,
)

st.sidebar.caption("Tokens per request")


def token_hint(tokens: int) -> None:
    """Restate a token count in pages and words, under the input that set it.

    The form asks for tokens three times. Nobody walks into a pricing
    conversation knowing their prompt in tokens -- they know it as "our claims
    policy, about forty pages". Without this line the form is only fillable by
    people who already have the answer, which is the wrong audience for a
    pre-sales tool.
    """
    st.sidebar.caption(tco.describe_tokens(tokens, st.session_state.model))


cached_prefix_tokens = st.sidebar.number_input(
    "Cacheable prefix", min_value=0, max_value=2_000_000,
    value=preset.cached_prefix_tokens, step=500,
    help="The stable head of every request: system prompt, tool definitions, policy documents. This is the only part caching can help.",
)
token_hint(cached_prefix_tokens)

variable_input_tokens = st.sidebar.number_input(
    "Variable input", min_value=0, max_value=2_000_000,
    value=preset.variable_input_tokens, step=500,
    help="The part that differs every request: the user's question, retrieved chunks. Never cacheable.",
)
token_hint(variable_input_tokens)

output_tokens = st.sidebar.number_input(
    "Output", min_value=1, max_value=200_000,
    value=preset.output_tokens, step=100,
    help="Generated tokens. Priced ~5x input, so this is usually where the money goes.",
)
token_hint(output_tokens)

st.sidebar.caption("Architecture")
use_caching = st.sidebar.checkbox("Prompt caching", value=preset.use_caching)
# Streamlit formats the slider label from the raw value, so a 0.0-1.0 float
# renders as "1%". Keep the widget in whole percent and convert once.
cache_hit_rate = st.sidebar.slider(
    "Cache hit rate (%)", 0, 100, value=int(round(preset.cache_hit_rate * 100)),
    step=5, disabled=not use_caching,
) / 100
use_batch = st.sidebar.checkbox(
    "Batch API", value=preset.use_batch,
    help="50% off input and output, for work nobody is waiting on.",
)

model_id = st.sidebar.selectbox(
    "Primary model",
    options=pricing.DISPLAY_ORDER,
    key="model",
    format_func=lambda m: pricing.get(m).label,
)

workload = tco.Workload(
    name=preset.name,
    requests_per_day=requests_per_day,
    cached_prefix_tokens=cached_prefix_tokens,
    variable_input_tokens=variable_input_tokens,
    output_tokens=output_tokens,
    cache_hit_rate=cache_hit_rate,
    use_caching=use_caching,
    use_batch=use_batch,
)

model = pricing.get(model_id)
breakdown = tco.annual_cost(workload, model_id)

# --- Headline ---------------------------------------------------------------

st.title("Enterprise LLM Deployment TCO Calculator")
st.caption(
    f"What a Claude workload costs to run for a year, and which decisions actually move that number. "
    f"Prices as of **{pricing.FETCHED_AT}** from [{pricing.SOURCE_URL}]({pricing.SOURCE_URL})."
)

# --- The three findings -----------------------------------------------------
# These are the reason this tool exists rather than a spreadsheet, and they
# used to live only in the README -- so anyone arriving at the deployed app saw
# a calculator and no argument. Each card loads the scenario that proves it.


def load_scenario(scenario: str, model: str):
    def _set():
        st.session_state.scenario = scenario
        st.session_state.model = model
    return _set


FINDINGS = [
    (
        "Caching can cost you money",
        "A cache *write* costs 1.25x a plain input token and every miss is a write, so "
        "below roughly a 20% hit rate caching makes the bill **bigger**. The floor is set "
        "by the write premium -- a better read discount barely moves it.",
        "low_volume_pilot", "claude-opus-5-5",
    ),
    (
        "Output is the hidden cost",
        "Caching cannot touch generation, and output is priced 5x input. A 90% hit rate on "
        "a 30K prefix cuts the bill 40%, not 90% -- and capping output length is the bigger "
        "lever nobody draws in the architecture diagram.",
        "coding_agent", "claude-opus-5-5",
    ),
    (
        "Batch is the largest single lever",
        "A flat 50% off input and output, costing nothing but latency. The question is never "
        "*can we afford it* -- it is *does anyone need the answer in under an hour*. "
        "Overnight work usually does not.",
        "document_processing", "claude-sonnet-5-5",
    ),
]

st.caption("**Three things that are easy to get wrong** -- each one loads the scenario that shows it")
for col, (title, body, scenario, model_for) in zip(st.columns(3), FINDINGS):
    with col, st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(body)
        st.button(
            "Show me", key=f"finding_{scenario}", width="stretch",
            on_click=load_scenario(scenario, model_for),
            disabled=(preset_key == scenario and model_id == model_for),
        )

st.write("")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Annual", money(breakdown.annual), help=f"On {model.label}")
c2.metric("Monthly", money(breakdown.monthly))
c3.metric("Per request", f"${tco.cost_per_request(workload, model_id):.4f}")
c4.metric("Output share", f"{breakdown.output_share:.0%}", help="How much of the bill is generation rather than input.")

if not breakdown.fits_context:
    st.error(
        f"**Does not fit.** This workload needs {breakdown.context_tokens:,} tokens per request "
        f"but {model.label} has a {model.context_window:,} token window."
    )

# A separate limit from the window, and a smaller one. Without this check the
# app will quote a confident annual figure for a request no model will serve:
# 150K of generation clears every context window and no output cap.
if not breakdown.fits_output_limit:
    st.error(
        f"**More output than the model will generate.** This workload asks for "
        f"{breakdown.output_tokens:,} output tokens per request; {model.label} emits at most "
        f"{model.max_output_tokens:,} in one response. The context window is not the binding "
        f"limit here -- splitting the request is, or a model with a higher cap."
    )

# Two independent problems, which a single `cache_hit_rate > 0.2` threshold used
# to collapse into one warning -- and that threshold did not even match any
# model's real breakeven.
#
# 1. The hit rate is below *this model's* breakeven, so caching loses money.
#    Surfaced in full in the caching section below; flagged here because the
#    headline figure above already has the loss baked into it.
if use_caching and workload.has_cacheable_prefix and cache_hit_rate < model.cache_breakeven_hit_rate:
    st.warning(
        f"**This hit rate is below breakeven.** {model.label} breaks even at "
        f"{model.cache_breakeven_hit_rate:.1%}; at {cache_hit_rate:.0%} the caching line above is "
        f"costing you money rather than saving it. See the caching section below."
    )

# 2. Batch and caching are being modelled together at a hit rate that a batch
#    queue will not deliver. Independent of whether that rate is above
#    breakeven -- the point is the rate itself is not achievable.
if use_batch and use_caching and workload.has_cacheable_prefix and cache_hit_rate > 0:
    st.warning(
        f"**Batch and caching together is optimistic.** The price sheet allows both, but batch jobs are "
        f"processed asynchronously over hours while the cache TTL is 5 minutes. A {cache_hit_rate:.0%} hit "
        f"rate is unlikely to survive contact with a real batch queue -- model this closer to 0%."
    )

st.divider()

# --- Model comparison -------------------------------------------------------

left, right = st.columns([3, 2])

with left:
    st.subheader("Same workload, every model")
    comparison = tco.compare_models(workload)
    df = pd.DataFrame([
        {
            "Model": b.model_label,
            "Annual": b.annual,
            "Monthly": b.monthly,
            # Via tco, not re-derived here: "a year is 365 days" is a rule of
            # the cost model, and a second copy in the renderer is how the two
            # drift apart silently (see DECISIONS.md, Decision 2).
            "Per request": tco.cost_per_request(workload, b.model_id),
            "Can run it": "yes" if b.is_runnable else "NO",
        }
        for b in comparison
    ])
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Annual": st.column_config.NumberColumn(format="$%.0f"),
            "Monthly": st.column_config.NumberColumn(format="$%.0f"),
            "Per request": st.column_config.NumberColumn(format="$%.4f"),
        },
    )

    # Only models that can actually hold the workload may be named in a
    # conclusion -- see tco.viable(). The table above still lists everything,
    # because "cheaper but too small" is useful to see.
    runnable = tco.viable(comparison)
    excluded = [b for b in comparison if not b.is_runnable]

    if not runnable:
        st.error(
            "**No model can serve this request.** "
            + (
                f"It needs {breakdown.context_tokens:,} tokens of context; the largest window "
                f"available is {max(b.context_window for b in comparison):,}. "
                if not any(b.fits_context for b in comparison) else ""
            )
            + (
                f"It asks for {breakdown.output_tokens:,} output tokens; the highest cap "
                f"available is {max(b.max_output_tokens for b in comparison):,}. "
                if not any(b.fits_output_limit for b in comparison) else ""
            )
            + "Split the request before pricing it."
        )
    elif len(runnable) >= 2 and runnable[0].annual:
        cheapest, dearest = runnable[0], runnable[-1]
        st.caption(
            f"{dearest.model_label} costs **{dearest.annual / cheapest.annual:.1f}x** what "
            f"{cheapest.model_label} does for this workload -- a spread of "
            f"{money(dearest.annual - cheapest.annual)} a year. Whether that buys anything is an "
            f"evaluation question, not a pricing question."
        )

    if excluded:
        # Say which limit each model failed; "too small" is two different
        # problems with two different fixes.
        reasons = []
        for b in excluded:
            why = []
            if not b.fits_context:
                why.append(f"needs {b.context_tokens:,} context, window holds {b.context_window:,}")
            if not b.fits_output_limit:
                why.append(f"asks for {b.output_tokens:,} output, cap is {b.max_output_tokens:,}")
            reasons.append(f"**{b.model_label}** ({'; '.join(why)})")
        st.caption(
            f":warning: Excluded from that comparison: {', '.join(reasons)}. "
            f"Cheaper, but {'they cannot' if len(excluded) > 1 else 'it cannot'} run the job."
        )

with right:
    st.subheader("Where the money goes")
    parts = pd.DataFrame([
        {"Component": "Cacheable prefix", "Cost": breakdown.prefix_cost},
        {"Component": "Variable input", "Cost": breakdown.variable_cost},
        {"Component": "Output", "Cost": breakdown.output_cost},
    ])
    chart = (
        alt.Chart(parts)
        .mark_bar()
        .encode(
            x=alt.X("Cost:Q", title="Annual cost (USD)", axis=alt.Axis(format="$,.0f")),
            y=alt.Y("Component:N", title=None, sort="-x"),
            tooltip=[alt.Tooltip("Cost:Q", format="$,.0f")],
        )
        .properties(height=180)
    )
    st.altair_chart(chart, width="stretch")

st.divider()

# --- Caching analysis: the part most cost models get wrong -------------------

st.subheader("Is prompt caching worth it here?")

if not workload.has_cacheable_prefix:
    # Nothing to cache, which is a normal way for a workload to be shaped. But
    # every statement this section makes is then vacuous: savings are exactly
    # zero, the hit-rate slider governs nothing, and the breakeven rate
    # describes a trade that is not on offer. Rendering "caching saves $0 (0%)"
    # in a success box above a flat curve and a breakeven marker reads as a
    # broken panel -- and below breakeven it used to contradict a warning at the
    # top of the page that fired on the hit rate without checking for content.
    st.info(
        "**Nothing in this workload is cacheable.** The cacheable prefix is 0 tokens, so prompt "
        "caching has nothing to act on and cannot move the bill either way. The hit rate does not "
        "matter here.\n\n"
        "Caching pays only for content that repeats verbatim across requests -- a system prompt, "
        "tool definitions, a policy document carried in context. If this workload does have a "
        "stable head that you are currently counting under **Variable input**, move those tokens "
        "to **Cacheable prefix** to see what caching would be worth."
    )
else:
    cache = tco.caching_savings(workload, model_id)
    breakeven = cache["breakeven_hit_rate"]

    # When caching is off, the hit-rate slider is greyed out -- but it still holds
    # a value, and this panel quietly prices "with caching" at that value. A reader
    # then gets a confident comparison against an assumption the UI is actively
    # hiding from them. Name the assumption instead.
    if not use_caching:
        st.caption(
            f"Caching is off for this workload, so the comparison below asks what *would* happen if "
            f"you switched it on at a **{cache_hit_rate:.0%}** hit rate -- the value in the greyed-out "
            f"slider. Enable caching to change it."
        )

    k1, k2, k3 = st.columns(3)
    k1.metric("Without caching", money(cache["annual_without_caching"]))
    k2.metric(
        "With caching", money(cache["annual_with_caching"]),
        help=f"At a {cache_hit_rate:.0%} hit rate",
    )
    k3.metric(
        "Difference", money(abs(cache["annual_savings"])),
        delta=f"{-cache['savings_pct']:.0%}" if cache["annual_savings"] else None,
        delta_color="inverse",
    )

    if cache["is_net_loss"]:
        st.error(
            f"**Caching is losing you {money(abs(cache['annual_savings']))} a year at this hit rate.** "
            f"A cache write costs {model.cache_write_ratio:.2f}x a plain input token; a read costs "
            f"{model.cache_read_ratio:.0%} of one. Below a **{breakeven:.1%}** hit rate you pay more in "
            f"write premiums than you recover in reads."
        )
    else:
        st.success(
            f"Caching saves **{money(cache['annual_savings'])}** a year "
            f"({cache['savings_pct']:.0%}). Breakeven for {model.label} is a **{breakeven:.1%}** hit rate."
        )

    curve = pd.DataFrame([
        {
            "Hit rate": h / 100,
            "Annual cost": tco.annual_cost(
                _replace(workload, use_caching=True, cache_hit_rate=h / 100), model_id
            ).annual,
        }
        for h in range(0, 101, 2)
    ])
    baseline = cache["annual_without_caching"]

    line = alt.Chart(curve).mark_line(strokeWidth=2).encode(
        x=alt.X("Hit rate:Q", axis=alt.Axis(format="%"), title="Cache hit rate"),
        y=alt.Y("Annual cost:Q", axis=alt.Axis(format="$,.0f"), title="Annual cost (USD)",
                scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("Hit rate:Q", format=".0%"), alt.Tooltip("Annual cost:Q", format="$,.0f")],
    )
    # These two rules carry the whole argument of this chart, so they have to stay
    # legible in both themes. Altair gets a literal colour -- it has no access to
    # Streamlit's theme tokens, so unlike the cost line above (which Streamlit
    # themes for us) anything hardcoded here is on its own.
    #
    # The previous values were "gray" and "firebrick", picked while developing
    # against the light theme. On the dark theme users actually get by default,
    # firebrick lands at 2.8:1 against #0E1117 -- under the 3:1 floor for non-text
    # marks, and that is before accounting for a 1px dashed stroke. The breakeven
    # marker, the single most important thing on the chart, was the least visible.
    #
    # #E03131 clears 3:1 on both grounds (4.2:1 dark, 4.5:1 light) rather than
    # being ideal on either, which beats branching on a theme the app cannot always
    # detect. Widths match the 2px cost line so the dashes actually read.
    BREAKEVEN_COLOR = "#E03131"
    BASELINE_COLOR = "#808080"   # 4.8:1 dark, 4.0:1 light -- already clears both

    no_cache_rule = alt.Chart(pd.DataFrame({"y": [baseline]})).mark_rule(
        strokeDash=[6, 4], color=BASELINE_COLOR, strokeWidth=2
    ).encode(y="y:Q")
    breakeven_rule = alt.Chart(pd.DataFrame({"x": [breakeven]})).mark_rule(
        strokeDash=[4, 3], color=BREAKEVEN_COLOR, strokeWidth=2
    ).encode(x="x:Q")

    st.altair_chart(line + no_cache_rule + breakeven_rule, width="stretch")
    st.caption(
        f"Solid line: annual cost with caching on, against hit rate. "
        f"Grey dashed: cost with caching off. Red dashed: the **{breakeven:.1%}** breakeven. "
        f"Everything left of the red line is a region where turning caching on makes the bill bigger."
    )

st.divider()

# --- Batch -------------------------------------------------------------------

st.subheader("Is the workload batchable?")
batch = tco.batch_savings(workload, model_id)
b1, b2 = st.columns(2)
b1.metric("Synchronous", money(batch["annual_sync"]))
b2.metric("Batch API", money(batch["annual_batch"]), delta=f"-{batch['savings_pct']:.0%}")
st.caption(
    "The Batch API is a flat 50% off input and output. It is the single largest lever in this whole "
    "model, and it costs nothing but latency -- so the real question is never *can we afford it*, it is "
    "*does anyone need the answer in under an hour*. For overnight extraction, reporting, and "
    "back-catalogue processing the answer is usually no."
)

# --- Provenance --------------------------------------------------------------

with st.expander("Pricing data and assumptions"):
    st.markdown(
        f"**Prices:** [{pricing.SOURCE_URL}]({pricing.SOURCE_URL})\n\n"
        f"**Context windows:** [{pricing.CONTEXT_SOURCE_URL}]({pricing.CONTEXT_SOURCE_URL})\n\n"
        f"**Retrieved:** {pricing.FETCHED_AT}\n\n"
        f"{pricing.FETCHED_NOTE}\n\n"
        "**Assumptions this model makes:**\n"
        "- Cache-write figures are the 5-minute TTL rate; the 1-hour TTL costs more and is not modelled.\n"
        "- Batch and caching discounts are treated as stacking, which the price sheet permits but the "
        "5-minute TTL makes unrealistic in practice (the app warns when you combine them).\n"
        "- Token counts are inputs, not estimates. Tokenizers differ between model generations, so the "
        "same text is not the same token count on every model -- compare like for like before trusting "
        "a cross-generation delta.\n"
        f"- The words-and-pages hints under each token field are a ratio, not a tokenizer: "
        f"{pricing.WORDS_PER_TOKEN_CURRENT} words per token on the current generation, "
        f"{pricing.WORDS_PER_TOKEN_LEGACY} on Haiku 4.5, at {pricing.WORDS_PER_PAGE} words to a page. "
        f"They are there so you can fill the form from what you know; code, tables, and non-English "
        f"text all land outside them. Count the real thing before quoting it.\n"
        "- Retries, failed requests, and evaluation traffic are not modelled. In production these are "
        "routinely 5-15% on top.\n"
    )
    st.dataframe(
        pd.DataFrame([
            {
                "Model": m.label,
                "Input": m.input, "Output": m.output,
                "Cache read": m.cache_read, "Cache write (5m)": m.cache_write_5m,
                # Scaled to percent units on purpose: the column below formats with
                # a printf "%.1f%%", which appends a literal % rather than scaling.
                # Passing the raw 0.217 fraction renders "0.2%" -- wrong by 100x, in
                # the one table a reader opens to check the 21.7% claimed above.
                "Breakeven hit rate": m.cache_breakeven_hit_rate * 100,
                "Context": m.context_window,
            }
            for m in pricing.all_models()
        ]),
        hide_index=True, width="stretch",
        column_config={
            "Input": st.column_config.NumberColumn(format="$%.2f"),
            "Output": st.column_config.NumberColumn(format="$%.2f"),
            "Cache read": st.column_config.NumberColumn(format="$%.2f"),
            "Cache write (5m)": st.column_config.NumberColumn(format="$%.2f"),
            "Breakeven hit rate": st.column_config.NumberColumn(format="%.1f%%"),
            "Context": st.column_config.NumberColumn(format="%d"),
        },
    )
    st.caption("All prices in USD per million tokens.")
