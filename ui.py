"""UI helpers: theme CSS (Quicksand + pastel), number formatting, pastel charts and the Lottie loader."""
import html
import json
import pathlib
import re

import altair as alt
import pandas as pd
import streamlit as st

PASTEL = ["#8ECAE6", "#F4A6A6", "#9BD8B5", "#F6C88F", "#B7A7E8", "#F2A7D0", "#A9D6E5"]
INK = "#2B3A4A"
FONT_URL = "https://fonts.googleapis.com/css2?family=Quicksand:wght@400;500;600;700&display=swap"
LOADER_PATH = pathlib.Path(__file__).parent / "assets" / "loader.json"

CSS = """
<style>
@import url('__FONT_URL__');

html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp input, .stApp textarea, .stApp button, .stApp a, .stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"], .stApp [data-baseweb="select"] div, .stApp [data-testid="stToast"] {
  font-family: 'Quicksand', sans-serif !important;
}
.stApp { background: #F9FBFE; color: __INK__; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: #EAF3FB; border-right: 1px solid #DAE8F4; }

/* sidebar brand */
.brand { display: flex; align-items: center; gap: .6rem; margin: .4rem 0 .2rem; }
.brand-ico { font-size: 2.3rem; line-height: 1; }
.brand-name { font-size: 2.15rem; font-weight: 700; line-height: 1.05; color: #2B4A66; letter-spacing: -.5px; }
.brand-sub { color: #6B8299; font-size: .98rem; font-weight: 500; margin: 0 0 .6rem 0; }

/* hero */
.hero-title { font-size: 2.6rem; font-weight: 700; line-height: 1.15; color: __INK__; margin: .2rem 0 .5rem; letter-spacing: -.5px; }
.hero-title .grad { background: linear-gradient(90deg, #5FA8DC, #F08FA0); -webkit-background-clip: text;
                    background-clip: text; -webkit-text-fill-color: transparent; }
.hero-sub { color: #6B8299; font-size: 1.05rem; font-weight: 500; margin-bottom: 1.2rem; }

/* sample question chips + all secondary buttons */
.stApp [data-testid="stBaseButton-secondary"] {
  border-radius: 999px; border: 1px solid #CFE3F3; background: #FFFFFF; color: #35566F; font-weight: 600;
  height: auto; min-height: 2.6rem; padding: .5rem 1.1rem; line-height: 1.3; transition: all .15s ease;
  box-shadow: 0 1px 2px rgba(60, 100, 140, .06);
}
.stApp [data-testid="stBaseButton-secondary"] p { white-space: normal; text-align: center; }
.stApp [data-testid="stBaseButton-secondary"]:hover { background: #E4F1FA; border-color: #8ECAE6; color: #24465F; transform: translateY(-1px); }

/* chat */
[data-testid="stChatMessage"] { background: rgba(255,255,255,.78); border: 1px solid #E1ECF6; border-radius: 18px;
                                 padding: 1rem 1.3rem; box-shadow: 0 2px 8px rgba(80, 120, 160, .05); }
[data-testid="stChatInput"] { border-radius: 20px; }
[data-testid="stExpander"] { border-radius: 14px; border-color: #E1ECF6; background: rgba(255,255,255,.6); }

/* loader */
.loading-text { text-align: center; color: #6B8299; font-weight: 600; margin-top: -.6rem; }
.loading-sub { text-align: center; color: #98A9B9; font-size: .9rem; margin-top: .1rem; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS.replace("__FONT_URL__", FONT_URL).replace("__INK__", INK), unsafe_allow_html=True)


def brand_sidebar(sub: str = "text-to-SQL") -> None:
    st.markdown('<div class="brand"><span class="brand-ico">⛽</span><span class="brand-name">Energy BI Agent</span></div>'
                f'<div class="brand-sub">{html.escape(sub)}</div>', unsafe_allow_html=True)


def hero(prefix: str, highlight: str) -> None:
    st.markdown(f'<div class="hero-title">{html.escape(prefix)} <span class="grad">{html.escape(highlight)}</span>?</div>'
                '<div class="hero-sub">Ask in plain English. The agent writes the SQL, runs it, and shows you the answer.</div>',
                unsafe_allow_html=True)


# ------------------------------------------------------------------ numbers
def fmt_num(v) -> str:
    """Thousands separators with a sensible number of decimals (9,082,359 / 1,234.5 / 12.34 / 0.231)."""
    if v is None or pd.isna(v):
        return "–"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:,.1f}"
    if a >= 1:
        return f"{v:,.2f}"
    return f"{v:.3f}"


_ID_LIKE = re.compile(r"year|yr|(^|_)id$|^id$|code|licen[cs]e", re.I)


def style_df(df: pd.DataFrame):
    """pandas Styler: commas in numbers, plain dates; years and ids are left alone (2025, not 2,025)."""
    fmts = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s):
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            fmts[c] = lambda v: "–" if pd.isna(v) else f"{v:%Y-%m-%d}"
        elif pd.api.types.is_integer_dtype(s):
            if _ID_LIKE.search(str(c)) or (len(s) and s.between(1900, 2100).all()):
                continue
            fmts[c] = lambda v: f"{v:,.0f}"
        elif pd.api.types.is_float_dtype(s):
            fmts[c] = fmt_num
    return df.style.format(fmts, na_rep="–")


# ------------------------------------------------------------------ charts
def pastel_chart(df: pd.DataFrame):
    """Pastel Altair chart: area for time series, bars otherwise (horizontal when labels are long). None = not chartable."""
    if df.shape[1] < 2 or not (2 <= len(df) <= 200):
        return None
    # long format (category, year, value) -> pivot to one column per category so each country becomes one line
    if df.shape[1] == 3 and not pd.api.types.is_numeric_dtype(df.iloc[:, 0]) and pd.api.types.is_integer_dtype(df.iloc[:, 1]) \
            and df.iloc[:, 1].between(1900, 2100).all() and pd.api.types.is_numeric_dtype(df.iloc[:, 2]):
        c, y, v = df.columns
        df = df.pivot_table(index=y, columns=c, values=v, aggfunc="first").reset_index()
        df.columns = [str(x) for x in df.columns]
    xcol = df.columns[0]
    ycols = [c for c in df.columns[1:] if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    if not ycols:
        return None
    lead = float(df[ycols[0]].abs().max() or 0)
    ycols = [c for c in ycols if lead == 0 or 0.05 <= float(df[c].abs().max() or 0) / lead <= 20][:4]   # same-scale series only
    is_time = pd.api.types.is_datetime64_any_dtype(df[xcol])
    if not is_time and pd.api.types.is_numeric_dtype(df[xcol]) and not df[xcol].between(1900, 2100).all():
        return None   # numeric x that is not a year: a bar chart would be meaningless
    year_axis = (not is_time and pd.api.types.is_integer_dtype(df[xcol]) and len(df) >= 6)   # integer years -> time series
    if year_axis:
        is_time, xs = True, pd.to_datetime(df[xcol].astype(str), format="%Y")
    else:
        xs = df[xcol].astype("string") if not is_time else df[xcol]
    long = pd.concat([pd.DataFrame({"x": xs, "metric": str(c), "value": df[c].astype(float)}) for c in ycols],
                     ignore_index=True)
    multi = len(ycols) > 1
    color = alt.Color("metric:N", scale=alt.Scale(range=PASTEL), legend=alt.Legend(title=None, orient="bottom") if multi else None)
    value_axis = alt.Axis(format=",.2~f", title=None, grid=True, gridColor="#EAF0F6", domain=False, ticks=False,
                          labelColor="#6B8299", labelFontSize=12)
    tip = [alt.Tooltip("x:T" if is_time else "x:N", title=str(xcol), format="%Y" if year_axis else ""), alt.Tooltip("metric:N", title="series"),
           alt.Tooltip("value:Q", title="value", format=",.2f")]
    base = alt.Chart(long)
    if is_time:
        chart = base.mark_area(line={"strokeWidth": 3}, point={"filled": True, "size": 60}, opacity=0.45,
                               interpolate="monotone").encode(
            x=alt.X("x:T", title=None, axis=alt.Axis(labelColor="#6B8299", grid=False, domain=False, ticks=False, format="%Y" if year_axis else None)),
            y=alt.Y("value:Q", axis=value_axis, stack=None), color=color, tooltip=tip)
    else:
        horizontal = len(df) <= 15 and xs.str.len().max() > 12
        cat_axis = alt.Axis(title=None, labelColor=INK, labelLimit=260, labelFontSize=12, ticks=False, domain=False,
                            labelAngle=0 if (horizontal or xs.str.len().max() <= 8) else -30)
        if horizontal:
            enc = dict(y=alt.Y("x:N", sort=None, axis=cat_axis), x=alt.X("value:Q", axis=value_axis), color=color, tooltip=tip)
            if multi:
                enc["yOffset"] = alt.YOffset("metric:N")
            chart = base.mark_bar(cornerRadiusTopRight=7, cornerRadiusBottomRight=7).encode(**enc)
        else:
            enc = dict(x=alt.X("x:N", sort=None, axis=cat_axis), y=alt.Y("value:Q", axis=value_axis), color=color, tooltip=tip)
            if multi:
                enc["xOffset"] = alt.XOffset("metric:N")
            chart = base.mark_bar(cornerRadiusTopLeft=7, cornerRadiusTopRight=7).encode(**enc)
    height = 42 * len(df) + 40 if (not is_time and len(df) <= 15 and xs.str.len().max() > 12) else 340
    return (chart.properties(height=max(height, 200), background="transparent").configure_view(strokeWidth=0)
            .configure(font="Quicksand").configure_legend(labelColor=INK, labelFontSize=12))


# ------------------------------------------------------------------ loader
@st.cache_data
def _loader_json() -> dict:
    return json.loads(LOADER_PATH.read_text(encoding="utf-8"))


def show_loader(placeholder, text: str = "Writing and running SQL…", sub: str = "Free-tier models can take a little while") -> None:
    """Render the Lottie spinner into an st.empty() placeholder; falls back to plain text if streamlit-lottie is missing."""
    with placeholder.container():
        try:
            from streamlit_lottie import st_lottie
            left, mid, right = st.columns([1, 1, 1])
            with mid:
                st_lottie(_loader_json(), height=150, key="sql_loader", quality="low")
        except Exception:
            pass
        st.markdown(f'<div class="loading-text">{text}</div><div class="loading-sub">{sub}</div>', unsafe_allow_html=True)
