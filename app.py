"""Giao diện Streamlit: trang hỏi đáp (chat) + Admin console (log, token, feedback, schema drift, eval).

Chạy: streamlit run app.py
"""
import json
import pathlib

import pandas as pd
import streamlit as st

from agent import llm
from agent.drift import check, load_dictionary
from agent.pipeline import Agent, load_config

st.set_page_config(page_title="Alberta Energy BI Agent", page_icon="⛽", layout="wide")
cfg = load_config()

SAMPLES = [
    "Top 5 operators by oil production in 2025",
    "Monthly total oil production for the last 12 months",
    "Which fields have the highest water cut in the latest month?",
    "How did total gas flared change in 2025 compared with 2024?",
    "Correlation between monthly oil production and WTI price",
]

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("⛽ Energy BI Agent")
    page = st.radio("Page", ["Ask", "Admin"], label_visibility="collapsed")
    st.divider()
    providers = list(llm.PROVIDERS)
    provider = st.selectbox("LLM provider", providers, index=providers.index(cfg["llm"]["provider"]))
    model = st.text_input("Model", value=cfg["llm"]["model"] or llm.default_model(provider),
                          key=f"model_{provider}", help="Tên model thay đổi theo thời gian: kiểm tra trang của nhà cung cấp.")
    st.caption("API key đọc từ biến môi trường (xem README). Data trong demo là dữ liệu giả.")


@st.cache_resource
def get_agent(provider: str, model: str) -> Agent:
    return Agent(cfg, provider, model)


try:
    agent = get_agent(provider, model)
except Exception as e:
    st.error(f"Chưa khởi tạo được agent: {e}\n\nĐã chạy `python scripts/generate_fake_data.py` chưa?")
    st.stop()


def show_result(res, key: str) -> None:
    if res.status == "refused":
        st.info(f"Câu hỏi nằm ngoài phạm vi dữ liệu: {res.error}")
        return
    if res.status != "ok":
        st.error(res.error or "Lỗi không xác định")
        if res.sql:
            st.code(res.sql, language="sql")
        return
    st.caption(f"{len(res.df):,} dòng · {res.latency_ms} ms · {res.attempts} lần thử · "
               f"{res.prompt_tokens + res.completion_tokens} token")
    st.dataframe(res.df, width='stretch', hide_index=True)
    df = res.df
    if 2 <= df.shape[1] <= 4 and 2 <= len(df) <= 200 and pd.api.types.is_numeric_dtype(df.iloc[:, -1]):
        x = df.set_index(df.columns[0])[df.columns[1:]].select_dtypes("number")
        if not x.empty:
            (st.line_chart if pd.api.types.is_datetime64_any_dtype(df.iloc[:, 0]) else st.bar_chart)(x)
    with st.expander("SQL"):
        st.code(res.sql, language="sql")
        for bad_sql, err in res.history:
            st.caption(f"Lần thử bị lỗi: {err}")
            st.code(bad_sql, language="sql")
    c1, c2, _ = st.columns([1, 1, 8])
    if c1.button("👍", key=f"up_{key}"):
        agent.log.set_feedback(res.log_id, 1)
        st.toast("Đã ghi nhận: đúng")
    if c2.button("👎", key=f"down_{key}"):
        agent.log.set_feedback(res.log_id, -1)
        st.toast("Đã ghi nhận: sai")


# ---------------------------------------------------------------- Ask
if page == "Ask":
    st.header("Hỏi dữ liệu bằng ngôn ngữ tự nhiên")
    st.caption(f"Dữ liệu từ {agent.coverage['min_month']} đến {agent.coverage['max_month']} · "
               f"đơn vị: dầu/nước m³, khí e³m³ (nghìn m³). Hỏi bằng tiếng Anh cho kết quả ổn định nhất.")
    st.session_state.setdefault("history", [])
    cols = st.columns(len(SAMPLES))
    clicked = next((s for c, s in zip(cols, SAMPLES) if c.button(s, width='stretch')), None)
    question = st.chat_input("Ask a question about wells, facilities, production, flaring, prices...") or clicked

    if question:
        with st.spinner("Đang sinh và chạy SQL..."):
            st.session_state["history"].append((question, agent.ask(question)))
    for i, (q, res) in reversed(list(enumerate(st.session_state["history"]))):
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            show_result(res, str(i))

# ---------------------------------------------------------------- Admin
else:
    st.header("Admin console")
    t1, t2, t3, t4 = st.tabs(["Query log", "Usage & cost", "Schema drift", "Eval results"])
    with t1:
        log = agent.log.recent(200)
        st.dataframe(log, width='stretch', hide_index=True)
    with t2:
        stats = agent.log.stats()
        st.dataframe(stats, width='stretch', hide_index=True)
        if stats.empty:
            st.info("Chưa có truy vấn nào.")
        st.caption("Cột token cho biết lượng dùng; với gói free thì chi phí bằng 0, nhưng cần theo dõi hạn mức.")
    with t3:
        if st.button("Run drift check"):
            rep = check(load_dictionary(cfg["dictionary_path"]), cfg["db_path"])
            (st.success if rep["ok"] else st.warning)(
                "Dictionary khớp schema DB." if rep["ok"] else "Có chênh lệch giữa dictionary và DB:")
            st.json({k: v for k, v in rep.items() if k != "ok"})
    with t4:
        files = sorted(pathlib.Path("eval/results").glob("*.json"))
        if not files:
            st.info("Chưa có kết quả. Chạy: python eval/run_eval.py")
        for f in files:
            r = json.loads(f.read_text(encoding="utf-8"))
            st.metric(f"{r['provider']} / {r['model']}", f"{r['accuracy_pct']}%", f"{r['correct']}/{r['n']} đúng")
            with st.expander("Chi tiết"):
                st.dataframe(pd.DataFrame(r["details"]), width='stretch', hide_index=True)
