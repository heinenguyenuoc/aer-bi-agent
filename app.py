"""Streamlit UI: Ask page (chat) + Admin console (query log, usage, schema drift, eval results).

Run locally:  streamlit run app.py
Deploy:       see README ("Deploy"). Put API keys and ADMIN_PASSWORD in the platform Secrets, never in the repo.
"""
import hmac
import importlib.util
import json
import os
import pathlib

import pandas as pd
import streamlit as st

import ui
from agent import llm
from agent.drift import check, load_dictionary
from agent.pipeline import Agent, load_config

st.set_page_config(page_title="Energy BI Agent", page_icon="⛽", layout="wide")
ui.inject_css()

# Streamlit Cloud secrets -> environment variables (so llm.py can read the keys). Ignored if there are no secrets.
try:
    for _k in st.secrets:
        if isinstance(st.secrets[_k], str):
            os.environ.setdefault(_k, st.secrets[_k])
except Exception:
    pass

cfg = load_config()
MAX_PER_SESSION = int(cfg.get("max_questions_per_session", 15))
MAX_PER_DAY = int(cfg.get("max_queries_per_day", 200))

UI = cfg["ui"]
SAMPLES = UI["samples"]


def ensure_data() -> None:
    """If the database is missing (e.g. first deploy), build it with the dataset's build script (a few seconds)."""
    if pathlib.Path(cfg["db_path"]).exists():
        return
    spec = importlib.util.spec_from_file_location("dataset_build", cfg["build_script"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if cfg["dataset"] == "aer_synthetic":
        mod.main(cfg["db_path"], 42, 3000, 40)
    else:
        mod.main(cfg["db_path"])      # owid: uses data/owid-energy-data.csv, or downloads it from GitHub


@st.cache_resource
def get_agent(provider: str, model: str) -> Agent:
    ensure_data()
    return Agent(cfg, provider, model)


def admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "")


def is_admin() -> bool:
    return bool(st.session_state.get("is_admin"))


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    ui.brand_sidebar(UI["sidebar_sub"])
    pages = ["Ask", "Admin"]
    page = st.radio("Page", pages, label_visibility="collapsed")
    st.divider()
    default_provider = cfg["llm"]["provider"]
    provider = default_provider
    model = cfg["llm"]["model"] or llm.default_model(default_provider)
    if is_admin():   # only admin can change provider/model, so guests cannot spend your key on expensive models
        providers = list(llm.PROVIDERS)
        provider = st.selectbox("LLM provider", providers, index=providers.index(default_provider))
        model = st.text_input("Model", value=cfg["llm"]["model"] if provider == default_provider
                              else llm.default_model(provider), key=f"model_{provider}",
                              help="Model names change over time: check the provider's model list.")
    st.caption(UI["source_note"])

# ---------------------------------------------------------------- start agent
boot = st.empty()      # fixed slot, so the page layout does not shift between the first run and later runs
if "booted" not in st.session_state:
    with boot.container():
        with st.status("Starting the agent (loading data and data dictionary)...", expanded=False) as status:
            try:
                agent = get_agent(provider, model)
                status.update(label="Agent ready", state="complete")
                st.session_state["booted"] = True
            except Exception as e:
                status.update(label="Startup failed", state="error")
                st.error(f"Could not start the agent: {e}")
                st.stop()
    boot.empty()
else:
    try:
        agent = get_agent(provider, model)
    except Exception as e:
        st.error(f"Could not start the agent: {e}")
        st.stop()


def show_result(res, key: str) -> None:
    if res.status == "refused":
        st.info(f"This question is outside what the data can answer: {res.error}")
        return
    if res.status != "ok":
        st.error(res.error or "Unknown error")
        if res.sql and is_admin():
            st.code(res.sql, language="sql")
        return
    st.caption(f"{len(res.df):,} rows · {res.latency_ms / 1000:.1f}s · {res.attempts} attempt(s) · "
               f"{res.prompt_tokens + res.completion_tokens:,} tokens")
    df = res.df
    chart = ui.pastel_chart(df)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    st.dataframe(ui.style_df(df), width='stretch', hide_index=True)
    with st.expander("SQL"):
        st.code(res.sql, language="sql")
        for bad_sql, err in res.history:
            st.caption(f"Failed attempt: {err}")
            st.code(bad_sql, language="sql")
    c1, c2, _ = st.columns([1, 1, 8])
    if c1.button("👍", key=f"up_{key}"):
        agent.log.set_feedback(res.log_id, 1)
        st.toast("Feedback saved: correct")
    if c2.button("👎", key=f"down_{key}"):
        agent.log.set_feedback(res.log_id, -1)
        st.toast("Feedback saved: incorrect")


# ---------------------------------------------------------------- Ask
if page == "Ask":
    ui.hero(UI["hero_prefix"], UI["hero_highlight"])
    st.caption(f"Data coverage ({agent.coverage['label']}): {agent.coverage['min']} to {agent.coverage['max']} · "
               "English questions work best. Energy is in TWh unless stated.")
    st.session_state.setdefault("history", [])
    clicked = None
    for row in (SAMPLES[:3], SAMPLES[3:]):
        for col, sample in zip(st.columns(3), row):
            if col.button(sample, width="stretch", key=f"sample_{sample}"):
                clicked = sample
    question = st.chat_input("Ask a question about electricity, energy mix, emissions...") or clicked

    if question:
        asked = len(st.session_state["history"])
        if not is_admin() and asked >= MAX_PER_SESSION:
            st.warning(f"This demo allows {MAX_PER_SESSION} questions per session. Reload the page to start a new session.")
        elif not is_admin() and agent.log.count_today() >= MAX_PER_DAY:
            st.warning("The demo has reached today's question limit (to stay within the free API quota). Please come back tomorrow.")
        else:
            loader = st.empty()
            ui.show_loader(loader)
            result = agent.ask(question)
            loader.empty()
            st.session_state["history"].append((question, result))
    for i, (q, res) in reversed(list(enumerate(st.session_state["history"]))):
        with st.chat_message("user", avatar="🙋"):
            st.write(q)
        with st.chat_message("assistant", avatar="⛽"):
            show_result(res, str(i))

# ---------------------------------------------------------------- Admin
else:
    st.header("Admin console")
    if not is_admin():
        if not admin_password():
            st.info("The Admin page is locked. Set the `ADMIN_PASSWORD` environment variable (or Secret) and restart the app.")
            st.stop()
        pw = st.text_input("Password", type="password")
        if pw and hmac.compare_digest(pw.encode(), admin_password().encode()):
            st.session_state["is_admin"] = True
            st.rerun()
        elif pw:
            st.error("Wrong password.")
        st.stop()

    t1, t2, t3, t4 = st.tabs(["Query log", "Usage & cost", "Schema drift", "Eval results"])
    with t1:
        st.dataframe(agent.log.recent(200), width='stretch', hide_index=True)
    with t2:
        stats = agent.log.stats()
        st.dataframe(ui.style_df(stats), width='stretch', hide_index=True)
        if stats.empty:
            st.info("No queries yet.")
        st.caption(f"Today (UTC): {agent.log.count_today()}/{MAX_PER_DAY} questions. Token columns show usage; "
                   "on a free tier the cost is zero but the quota still needs watching.")
    with t3:
        if st.button("Run drift check"):
            rep = check(load_dictionary(cfg["dictionary_path"]), cfg["db_path"])
            (st.success if rep["ok"] else st.warning)(
                "Data dictionary matches the database schema." if rep["ok"] else "The data dictionary and the database differ:")
            st.json({k: v for k, v in rep.items() if k != "ok"})
    with t4:
        files = sorted(pathlib.Path("eval/results").glob("*.json"))
        if not files:
            st.info("No results yet. Run: python eval/run_eval.py")
        for f in files:
            r = json.loads(f.read_text(encoding="utf-8"))
            st.metric(f"{r['provider']} / {r['model']}", f"{r['accuracy_pct']}%", f"{r['correct']}/{r['n']} correct")
            with st.expander("Details"):
                st.dataframe(pd.DataFrame(r["details"]), width='stretch', hide_index=True)
