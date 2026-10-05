"""Enterprise LLM deployment TCO calculator.

A pre-sales tool: describe a workload the way a customer describes it, get the
annual bill, and see which architectural choices actually move the number.

All arithmetic lives in tco.py; this file only renders.
"""

import altair as alt
import pandas as pd
import streamlit as st

import pricing
import tco

st.set_page_config(page_title="LLM Deployment TCO Calculator", layout="wide")

USD = "${:,.0f}"


def money(x: float) -> str:
    return USD.format(x)


# --- Sidebar: describe the workload ----------------------------------------

st.sidebar.title("Workload")

preset_labels = {k: v.name for k, v in tco.PRESETS.items()}
preset_key = st.sidebar.selectbox(
    "Start from a scenario",
    options=list(tco.PRESETS),
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
cached_prefix_tokens = st.sidebar.number_input(
    "Cacheable prefix", min_value=0, max_value=2_000_000,
    value=preset.cached_prefix_tokens, step=500,
    help="The stable head of every request: system prompt, tool definitions, policy documents. This is the only part caching can help.",
)
variable_input_tokens = st.sidebar.number_input(
    "Variable input", min_value=0, max_value=2_000_000,
    value=preset.variable_input_tokens, step=500,
    help="The part that differs every request: the user's question, retrieved chunks. Never cacheable.",
)
output_tokens = st.sidebar.number_input(
    "Output", min_value=1, max_value=200_000,
    value=preset.output_tokens, step=100,
    help="Generated tokens. Priced ~5x input, so this is usually where the money goes.",
)

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
    format_func=lambda m: pricing.get(m).label,
    index=pricing.DISPLAY_ORDER.index("claude-sonnet-5-5"),
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

if use_batch and use_caching and cache_hit_rate > 0.2:
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
            "Per request": b.annual / (workload.requests_per_day * tco.DAYS_PER_YEAR),
            "Fits context": "yes" if b.fits_context else "NO",
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

    cheapest, dearest = comparison[0], comparison[-1]
    if cheapest.annual:
        st.caption(
            f"{dearest.model_label} costs **{dearest.annual / cheapest.annual:.1f}x** what "
            f"{cheapest.model_label} does for this workload -- a spread of "
            f"{money(dearest.annual - cheapest.annual)} a year. Whether that buys anything is an "
            f"evaluation question, not a pricing question."
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

cache = tco.caching_savings(workload, model_id)
breakeven = cache["breakeven_hit_rate"]

k1, k2, k3 = st.columns(3)
k1.metric("Without caching", money(cache["annual_without_caching"]))
k2.metric("With caching", money(cache["annual_with_caching"]))
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

# Sensitivity curve: cost against hit rate, with the breakeven marked.
from dataclasses import replace as _replace

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
no_cache_rule = alt.Chart(pd.DataFrame({"y": [baseline]})).mark_rule(
    strokeDash=[6, 4], color="gray"
).encode(y="y:Q")
breakeven_rule = alt.Chart(pd.DataFrame({"x": [breakeven]})).mark_rule(
    strokeDash=[2, 3], color="firebrick"
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
