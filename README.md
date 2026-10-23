# Energy BI Agent (free-stack text-to-SQL)

A natural-language BI agent for global energy data (real data from Our World in Data), built entirely on free tools.
Ask a question in plain English → the agent writes SQL → the SQL is checked for safety → it runs on DuckDB → you get a table, a chart and the SQL.

```
Question ──► prompt = data dictionary (YAML) + few-shot ──► LLM ──► SQL
                                                                     │
   ┌── on error: feed it back to the LLM to fix (max 2 times) ◄── run on DuckDB (read-only, timeout) ◄── SQL guard (sqlglot)
   ▼
 Streamlit: table + chart + SQL + thumbs up/down         SQLite log ──► Admin console (log, tokens, drift, eval)
```

| Component | Technology used here | Typical enterprise equivalent |
|---|---|---|
| Data warehouse | DuckDB (single file) | Amazon Redshift |
| LLM | Gemini / Groq / OpenRouter free tier, local Ollama, or Claude | Bedrock + Claude |
| Data dictionary | `datasets/<name>/data_dictionary.yaml` | Dictionary stored in S3 |
| Semantic layer + drift check | `agent/drift.py` | Auto-updating semantic layer |
| UI + Admin | Streamlit | Amplify + admin console |

## Quick start (5 minutes)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python datasets/owid/build.py               # builds data/owid.duckdb (the app also does this on first start)
export GEMINI_API_KEY=...                   # or GROQ_API_KEY, see below
streamlit run app.py
```

No API key yet? Pick the `mock` provider (admin sidebar) to see the UI; it only returns one fixed SQL query. For UI work you can slow it down or change its query with `LLM_MOCK_DELAY` (seconds) and `LLM_MOCK_SQL`.

### Choosing a free LLM
- **Gemini**: get a key in Google AI Studio and set `GEMINI_API_KEY`.
- **Groq**: get a key at console.groq.com, set `GROQ_API_KEY` and `LLM_PROVIDER=groq`.
- **Ollama (local, unlimited)**: install Ollama, run `ollama pull qwen2.5-coder:7b`, set `LLM_PROVIDER=ollama`. A 7B model runs on a 16 GB laptop but is weaker at text-to-SQL than cloud models.
- **Claude**: set `ANTHROPIC_API_KEY` and `LLM_PROVIDER=anthropic` (billed per token; cheap for a small demo).

The default model names in `agent/llm.py` and free-tier quotas change often. Check the provider's pages and override with `LLM_MODEL` or the Model box in the admin sidebar.

## Safety (two layers)
1. `agent/guard.py` (sqlglot): exactly one SELECT/CTE statement, only tables listed in the dictionary, table functions and file-reading functions (`read_csv`, ...) blocked, `LIMIT` added automatically.
2. `agent/db.py`: DuckDB opened with `read_only=True` and `enable_external_access=False`, plus a timeout. Tested: even if the guard is bypassed, the database still refuses `CREATE`, `COPY` and `read_csv`.

## Evaluation
```bash
python eval/run_eval.py --provider groq --model llama-3.3-70b-versatile
python eval/run_eval.py --provider gemini
python eval/run_eval.py --gold-only      # check the reference SQL only, no LLM calls
```
26 questions for the OWID dataset (25 with reference SQL plus 1 out-of-scope question the agent must refuse). The metric is execution accuracy: the agent's result must match the reference SQL's result (column names and row order ignored, floats rounded to 2 decimals, extra columns allowed). Results are saved in `eval/results/` and shown in the Admin "Eval results" tab. Extend the set to 30–50 questions in `datasets/<name>/questions.yaml`, and report accuracy per model and per difficulty group (multi-table joins, time comparisons) rather than a single number.

## Data

Two datasets ship with the repo; pick one with `dataset:` in `config.yaml` (or the `DATASET` env var).

**`owid` (default, real and free).** [Our World in Data energy dataset](https://github.com/owid/energy-data), licence **CC BY 4.0** (attribution required: "Our World in Data, Energy dataset"). It is compiled from the Energy Institute Statistical Review of World Energy, Ember, the US EIA and others. `datasets/owid/build.py` turns the CSV into three DuckDB tables: `dim_country`, `fact_energy_yearly` (primary energy, fuel production/consumption, emissions, 1965-2023) and `fact_electricity_yearly` (generation by source, shares, carbon intensity). It is **country-level**: there is no Alberta or provincial breakdown, so provincial questions are refused. Canada is included, e.g. its renewables share of electricity.

**`aer_synthetic` (optional, fake).** Alberta-shaped synthetic data (`scripts/generate_fake_data.py`): fictional operators, Arps decline curves, made-up prices. Use it only to show the shape of an upstream oil and gas model. Real per-well AER production data is a paid product, not open data; real Alberta public data (Petrinex, AER ST37/ST60/ST102) would need its own `datasets/<name>/` folder with a build script, a dictionary and questions.

To add your own dataset: create `datasets/<name>/` with `build.py`, `data_dictionary.yaml`, `questions.yaml`, add an entry under `datasets:` in `config.yaml`, then run the **Schema drift** tab in Admin to compare the dictionary with the database.

## Deploy (run without your laptop)

Recommended: **Streamlit Community Cloud** (free). According to Streamlit's documentation, an app with no traffic for 12 hours goes to sleep; a visitor sees a "gone to sleep" page and clicks "Yes, get this app back up!" to wake it (anyone who can view the app can do this). That page is shown by the platform and `app.py` cannot change it. Put `landing.html` (a static landing page that explains this) on GitHub Pages or your portfolio and use that link in your CV.

1. Push the repo to GitHub. `data/owid.duckdb` (2 MB) can be committed; if it is missing the app rebuilds it by downloading the CSV from GitHub.
2. On share.streamlit.io choose New app, then the repo, branch and file `app.py`.
3. Open the app's Secrets and paste (see `.streamlit/secrets.toml.example`):
   ```toml
   GEMINI_API_KEY = "..."
   ADMIN_PASSWORD = "your-password"
   ```
4. Edit two places in `landing.html`: `REPLACE_WITH_YOUR_STREAMLIT_URL` and `REPLACE_WITH_YOUR_GITHUB_REPO_URL`.

Protecting the public demo:
- Guests **cannot change the provider or model** (so they cannot spend your key on expensive models). Only an admin who enters `ADMIN_PASSWORD` sees the selector.
- The Admin page stays locked if `ADMIN_PASSWORD` is not set.
- `max_questions_per_session` (15) and `max_queries_per_day` (200) in `config.yaml`. The per-session limit can be bypassed by reloading the page; the daily limit is the real cap, but the SQLite log on Streamlit Cloud is lost when the app restarts, so the counter resets too.
- Use a dedicated API key for the demo only.

If you need it running continuously (for example a monthly data-refresh cron), use an always-on VM such as Oracle Cloud Always Free.

## Project layout
```
app.py                    Streamlit: Ask page + Admin (locked by ADMIN_PASSWORD)
ui.py                     theme (Quicksand + pastel), number formatting, pastel charts, Lottie loader
assets/loader.json        Lottie loading animation
landing.html              static landing page for GitHub Pages / portfolio
config.yaml               paths, limits, default provider
datasets/owid/             build.py, data_dictionary.yaml (semantic layer), questions.yaml (eval)
datasets/aer_synthetic/   optional fake Alberta data: dictionary + questions
  (dictionary)            semantic layer: tables, columns, units, metrics, rules, few-shot examples
agent/pipeline.py         prompt, generate/repair SQL loop, logging
agent/guard.py            SQL validation (sqlglot)
agent/db.py               read-only DuckDB + timeout
agent/llm.py              LLM client (urllib, no SDK needed)
agent/logger.py           SQLite log, feedback
agent/drift.py            compares the dictionary with the real schema
scripts/generate_fake_data.py
eval/run_eval.py
```

## Ideas for next steps
- A "Pending SQL review" tab: the admin approves correct SQL, which is then added to the few-shot examples (learning loop from thumbs-up feedback).
- Select relevant tables with embeddings when the schema grows (the whole dictionary currently fits in about 2k tokens, so it is not needed yet).
- Deploy to Streamlit Community Cloud or Hugging Face Spaces (API keys in secrets).
