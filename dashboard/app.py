"""
Results dashboard for the energy-harvesting scheduling study.

This shows what we MEASURED. It is not a live-system mock: every number here
comes from a committed CSV or is computed by the simulator on demand. There is
no panel that renders a value no experiment produced.

    pip install streamlit
    streamlit run dashboard/app.py

Reads reports/oracle_<house>/raw.csv (from src/oracle_bound.py) and
reports/sim_<house>/metrics_raw.csv (from src/run.py --seeds N). Run those
first; the dashboard tells you which are missing rather than inventing them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yaml

def chart(c, **kw):
    """st.altair_chart across Streamlit versions.

    `width="stretch"` replaced `use_container_width` only in recent releases;
    older ones raise TypeError. Try the new name, fall back to the old one.
    """
    try:
        return st.altair_chart(c, **kw)
    except TypeError:
        return st.altair_chart(c, use_container_width=True, **kw)


def dataframe(df, **kw):
    try:
        return st.dataframe(df,  **kw)
    except TypeError:
        return st.dataframe(df, use_container_width=True, **kw)


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT / "src"))

# Resident counts: Zenodo record 15708568 publishes a per-home table. NOT
# inferable from the testbed prefix -- an earlier version of this repo asserted
# the wrong ones and it would have reached the writeup.
RESIDENTS = {"hh104": "1", "hh101": "1", "hh107": "2", "mn50": "2", "mn51": ">2"}

INK, PAPER = "#161D26", "#EEF1F4"
STORED, SILENCE, ORACLE, MUTED = "#C97A0E", "#42566B", "#1F6F6B", "#9AA7B4"

st.set_page_config(page_title="Harvested-energy scheduling — results",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown(f"""
<style>
  .stApp, .main, section[data-testid="stMain"] {{ background:{PAPER} !important; }}
  section[data-testid="stSidebar"] {{ background:#E3E8ED !important; }}
  html, body, [class*="css"], .stApp, .stMarkdown, p, li, span, label,
  h1, h2, h3, h4, h5, h6, [data-testid="stMetricValue"],
  [data-testid="stMetricLabel"], section[data-testid="stSidebar"] * {{
      color:{INK} !important;
      font-family:"Inter","Helvetica Neue",system-ui,sans-serif; }}
  h1,h2,h3 {{ font-weight:640; letter-spacing:-.015em; }}
  .num {{ font-variant-numeric:tabular-nums; font-feature-settings:"tnum"; }}
  .claim {{ border-left:3px solid {SILENCE}; padding:.55rem 0 .55rem .9rem;
            margin:.4rem 0 1rem; font-size:1.02rem; line-height:1.55; }}
  .unmeasured {{ border-left:3px solid {STORED}; background:#FBF3E5;
                 padding:.55rem .9rem; margin:.4rem 0 1rem; font-size:.92rem; }}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ loading --
@st.cache_data
def load_oracle() -> pd.DataFrame:
    frames = []
    for house in RESIDENTS:
        p = REPORTS / f"oracle_{house}" / "raw.csv"
        if p.exists():
            d = pd.read_csv(p)
            d["house"], d["residents"] = house, RESIDENTS[house]
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@st.cache_data
def load_ablation(house: str) -> pd.DataFrame:
    p = REPORTS / f"sim_{house}" / "metrics_raw.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data
def load_provenance() -> pd.DataFrame:
    """Walk config/energy.yaml and collect every `src:` tag.

    The point of this panel: the study's absolute numbers rest on constants
    nobody has measured. Showing that is more honest than a confident gauge.
    """
    cfg = yaml.safe_load((ROOT / "config" / "energy.yaml").read_text())
    rows = []

    def walk(node, path):
        if isinstance(node, dict):
            if "src" in node:
                v = node.get("value", node.get("default", node.get("range")))
                rows.append({"constant": path, "src": node["src"], "value": str(v)})
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v, f"{path}.{k}" if path else k)

    walk(cfg, "")
    return pd.DataFrame(rows)


oracle, prov = load_oracle(), load_provenance()

if oracle.empty:
    st.title("No results yet")
    st.markdown("Run the experiments first, then reload:")
    st.code("python src/fetch_casas.py hh104 hh101 hh107 mn50 mn51\n"
            "python src/build_events.py hh104 hh101 hh107 mn50 mn51\n"
            "for h in hh104 hh101 hh107 mn50 mn51; do\n"
            "  python src/oracle_bound.py $h --days 20 --seeds 5\n"
            "done", language="bash")
    st.stop()

# -------------------------------------------------------------------- chrome --
houses = sorted(oracle.house.unique(), key=lambda h: (RESIDENTS[h], h))
with st.sidebar:
    st.markdown("### Scope")
    st.caption(f"{len(houses)} CASAS homes · "
               f"{oracle.seed.nunique()} seeds · "
               f"{oracle.node.nunique()} nodes")
    sel_houses = st.multiselect("Homes", houses, default=houses,
                                format_func=lambda h: f"{h} · {RESIDENTS[h]} resident"
                                + ("s" if RESIDENTS[h] != "1" else ""))
    nodes = sorted(oracle.node.unique())
    real_only = st.checkbox("Hide the synthetic gym node", value=True,
                            help="gym harvest is a placeholder 2-3 orders of "
                                 "magnitude above every real source. It is "
                                 "reported separately, never in an average.")
    if real_only:
        nodes = [n for n in nodes if n != "gym"]
    sel_nodes = st.multiselect("Nodes", nodes, default=nodes)

df = oracle[oracle.house.isin(sel_houses) & oracle.node.isin(sel_nodes)]
if df.empty:
    st.warning("Nothing selected. Pick at least one home and node.")
    st.stop()

st.title("Does better forecasting buy better scheduling?")
st.markdown(
    '<div class="claim">Two schedulers were run against an identical trace, '
    'seed and energy budget — once with a time-of-day histogram forecaster, '
    'once with an oracle that reads the true future harvest. No forecaster can '
    'beat perfect information, so the gap between them bounds what any model — '
    'histogram, LSTM, transformer — could ever contribute.</div>',
    unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(
    ["The bound", "Scheduler ablation", "Energy trace", "What we haven't measured"])

# ------------------------------------------------------------- 1. the bound --
with tab1:
    piv = (df[df.scheduler == "S4 forecast rate"]
           .pivot_table(index=["house", "residents", "node"], columns="forecast",
                        values="capture_silence", aggfunc=["mean", "std"]))
    g = pd.DataFrame({
        "hist": piv[("mean", "hist")], "oracle": piv[("mean", "oracle")],
        "sd": (piv[("std", "hist")] ** 2 + piv[("std", "oracle")] ** 2) ** 0.5,
    }).reset_index()
    g["gain"] = g["oracle"] - g["hist"]
    g["label"] = g["house"] + " · " + g["node"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Largest gain from perfect foresight", f"{g['gain'].max():+.3f}")
    c2.metric("Typical seed-to-seed noise", f"±{g['sd'].median():.3f}")
    c3.metric("Cases where the gain clears noise",
              f"{int((g['gain'] > g['sd']).sum())} of {len(g)}")

    st.markdown("#### Every gain sits inside the noise it would have to beat")
    st.caption("Bar = what perfect foresight adds. Line = the seed-to-seed "
               "spread it must exceed to count as a result.")

    base = alt.Chart(g).encode(
        y=alt.Y("label:N", sort=alt.EncodingSortField("gain", order="descending"),
                title=None, axis=alt.Axis(labelFontSize=11)))
    bars = base.mark_bar(height=11, color=ORACLE, opacity=.85).encode(
        x=alt.X("gain:Q", title="capture gained from a perfect forecast"),
        tooltip=["house", "node", alt.Tooltip("hist:Q", format=".3f"),
                 alt.Tooltip("oracle:Q", format=".3f"),
                 alt.Tooltip("gain:Q", format="+.3f"),
                 alt.Tooltip("sd:Q", title="seed sd", format=".3f")])
    noise = base.mark_tick(thickness=2, size=20, color=STORED).encode(x="sd:Q")
    chart((bars + noise).properties(height=26 * len(g) + 40))

    st.markdown("#### Capture levels differ tenfold between homes. The bound does not.")
    lvl = g.melt(id_vars=["house", "residents", "node"],
                 value_vars=["hist", "oracle"],
                 var_name="forecaster", value_name="capture")
    chart(
        alt.Chart(lvl).mark_circle(size=95, opacity=.8).encode(
            x=alt.X("capture:Q", title="critical-event capture during silence"),
            y=alt.Y("house:N", title=None,
                    sort=alt.EncodingSortField("residents")),
            color=alt.Color("forecaster:N", title="forecaster",
                            scale=alt.Scale(domain=["hist", "oracle"],
                                            range=[SILENCE, ORACLE])),
            tooltip=["house", "residents", "node", "forecaster",
                     alt.Tooltip("capture:Q", format=".3f")],
        ).properties(height=190))
    st.caption("Homes span 1, 2 and more than 2 residents. Resident counts from "
               "the Zenodo 15708568 record description.")

# --------------------------------------------------------- 2. the ablation --
with tab2:
    st.markdown("#### What actually moved the metric")
    # Only offer homes that actually have an ablation run; silently offering
    # one that does not is a dead end for whoever is demoing.
    have = [h for h in sel_houses
            if (REPORTS / f"sim_{h}" / "metrics_raw.csv").exists()]
    if not have:
        st.info("No ablation runs found for the selected homes. Generate one:")
        st.code(f"python src/run.py {sel_houses[0]} --days 20 --seeds 5",
                language="bash")
        abl = pd.DataFrame()
    else:
        abl_house = st.selectbox(
            "Home", have,
            format_func=lambda h: f"{h} · {RESIDENTS[h]} resident"
            + ("s" if RESIDENTS[h] != "1" else ""), key="abl")
        abl = load_ablation(abl_house)
    if not abl.empty:
        a = abl[abl.node.isin(sel_nodes)]
        chart(
            alt.Chart(a).mark_bar(opacity=.9).encode(
                x=alt.X("scheduler:N", title=None, axis=alt.Axis(labelAngle=-35)),
                y=alt.Y("mean(capture_silence):Q", title="capture during silence"),
                color=alt.Color("scheduler:N", legend=None,
                                scale=alt.Scale(range=[MUTED, MUTED, SILENCE,
                                                       SILENCE, ORACLE])),
                column=alt.Column("node:N", title=None),
            ).properties(height=230, width=125))
        st.markdown(
            '<div class="claim">The jump is from S1 (spend whenever affordable) '
            'to S2–S4 (hold a reserve against future silence). Reserving is what '
            'matters. Which of S2, S3 or S4 you pick — that is, whether the '
            'reserve is fixed or forecast-driven — moves the metric far less.</div>',
            unsafe_allow_html=True)

# ------------------------------------------------------------ 3. the trace --
with tab3:
    st.markdown("#### Watch a node run out of energy")
    st.caption("Computed live by the simulator when you press the button — not "
               "a recording.")
    c1, c2, c3, c4 = st.columns(4)
    t_house = c1.selectbox("Home", sel_houses, key="t_house")
    t_node = c2.selectbox("Node", sel_nodes, key="t_node")
    t_days = c3.slider("Days", 3, 20, 7)
    t_pv = c4.select_slider("Indoor PV (µW)", [0, 10, 30, 50, 100], value=30,
                            help="0 = only the four kinetic sources the project "
                                 "is about. Indoor PV is out of scope and "
                                 "untested, but supplies most of the energy at "
                                 "the default setting.")

    if st.button("Run the simulator", type="primary"):
        with st.spinner("Simulating…"):
            from storage import load_config
            from harvesting import build_harvest
            from harvesting import DEBOUNCE_S
            from schedulers import S4_ForecastRate
            from sim import run_node
            cfg = load_config()
            # Keyword args on purpose: build_harvest's positional order is
            # (trace, cfg, timestep, footstep_mult, pv_uw, seed, days,
            # debounce_s), and passing debounce=None here silently produced an
            # empty visit frame and a NaT date_range.
            h, ev, grid = build_harvest(t_house, cfg, timestep_s=60,
                                        pv_uw=float(t_pv), days=t_days,
                                        debounce_s=DEBOUNCE_S)
            res, soc, reserve = run_node(
                t_node, h, ev, cfg,
                lambda fc: S4_ForecastRate(fc, 1.5, 720), 60, seed=0)
        trace = pd.DataFrame({"hour": [i / 60 for i in range(len(soc))],
                              "stored": soc, "reserve": reserve})
        step = max(1, len(trace) // 3000)
        trace = trace.iloc[::step]
        area = alt.Chart(trace).mark_area(opacity=.75, color=STORED).encode(
            x=alt.X("hour:Q", title="hours"),
            y=alt.Y("stored:Q", title="stored energy (fraction of capacity)"))
        line = alt.Chart(trace).mark_line(color=SILENCE, strokeDash=[4, 3],
                                          strokeWidth=1.4).encode(
            x="hour:Q", y="reserve:Q")
        chart((area + line).properties(height=260))
        m1, m2, m3 = st.columns(3)
        m1.metric("Time with an empty store", f"{res['outage_frac']:.0%}")
        m2.metric("Critical events caught in silence",
                  f"{res['capture_silence']:.3f}")
        # NaN here is meaningful, not a bug: the node never managed to
        # transmit, so there is no latency to average. Say that.
        lat = res["mean_latency_min"]
        m3.metric("Mean information latency",
                  "never reported" if pd.isna(lat) else f"{lat:.0f} min",
                  help="Time between an event happening and the hub hearing "
                       "about it. Blank when the node never transmitted.")
        if t_pv == 0:
            st.markdown(
                '<div class="unmeasured">With indoor PV off, this is the system '
                'the project actually proposes: four occupancy-coupled kinetic '
                'sources. Standing overhead exceeds daily harvest on every real '
                'node, so the store spends most of its time empty and the '
                'scheduler has nothing to allocate.</div>',
                unsafe_allow_html=True)

# ------------------------------------------------------- 4. what's unmeasured --
with tab4:
    st.markdown("#### Which constants hold this study up")
    counts = prov.src.value_counts()
    cols = st.columns(len(counts))
    for col, (src, n) in zip(cols, counts.items()):
        col.metric(src.title().replace("_", " "), n)

    st.markdown(
        '<div class="unmeasured">One class of constant here is measured: the '
        'event rates, which come from the CASAS traces. Everything converting '
        'those events into millijoules is a literature guess or a swept range. '
        'That is why no absolute number on this dashboard is claimable — only '
        'comparisons run under identical constants, which is exactly what the '
        'oracle bound is.</div>', unsafe_allow_html=True)

    order = ["MEASURED", "DATASHEET", "DERIVED", "SWEPT", "SYNTHETIC", "PLACEHOLDER"]
    present = list(dict.fromkeys([c for c in order if c in set(prov["src"])]
                                 + sorted(set(prov["src"]) - set(order))))
    view = prov.copy()
    view["src"] = pd.Categorical(view["src"], present, ordered=True)
    dataframe(view.sort_values(["src", "constant"]),
                 hide_index=True, column_config={
                     "constant": st.column_config.TextColumn("constant", width="large"),
                     "src": st.column_config.TextColumn("provenance"),
                     "value": st.column_config.TextColumn("value / swept range")})
    st.caption("Source of truth: config/energy.yaml. Every entry carries a `src:` "
               "tag; adding a value without one is a review failure.")