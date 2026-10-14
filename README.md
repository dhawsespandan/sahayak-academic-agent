# Sahayak - Agentic Academic Helpdesk

Sahayak ("helper") is an AI agent for students of Division C. It answers questions on academic
rules from the institute's regulation PDFs, looks up the logged-in student's own attendance,
CA marks and exam timetable, and prepares leave / CA re-evaluation / bonafide certificate
requests. A request is sent to the faculty **only after the student approves it**; the
sending is done by an n8n workflow (Gmail + Google Sheets).

> All student records and regulations in this repository are **sample data** created for the
> prototype. They are not the records or rules of any real institute.

## Architecture

```
START -> guard -> router -+-> policy_rag ------------------------------------------> END
                          +-> personal_agent <-> tools ----------------------------> END
                          +-> extract_request -> check_rules -> human_approval -> dispatch -> END
                          +-> general_reply -------------------------------------> END
```

| Node | Type | Role |
|---|---|---|
| `guard` | Python rules | Blocks requests for another student's PRN and prompt-injection phrases before any LLM call |
| `router` | LLM (gpt-oss-20b, structured output) | Classifies the message: policy / personal / action / general / out_of_scope |
| `policy_rag` | Retrieval + LLM (gpt-oss-120b) | Searches the regulation PDFs in Qdrant and answers with citations; says "not found" below a similarity threshold |
| `personal_agent` + `tools` | Tool-calling loop | Calls `get_my_attendance`, `get_my_ca_marks`, `get_exam_schedule`, `get_my_requests`, `search_regulations`. The PRN is injected from state, so the LLM cannot read another student's data |
| `extract_request` | LLM (structured output) | Pulls request fields out of the conversation (can take several turns) |
| `check_rules` | Python rules | Leave length -> mentor or HoD, medical certificate notes, CA re-evaluation 5-working-day window |
| `human_approval` | LangGraph `interrupt()` | Pauses the graph until the student approves or cancels |
| `dispatch` | HTTP -> n8n | Posts the request to the n8n webhook (email to faculty, row in Google Sheet), saves it in SQLite with a reference number |

Conversation memory uses a LangGraph SQLite checkpointer, so follow-up questions work.

## Tech stack

Python 3.12+, LangGraph, LangChain (Groq integration), Groq API (OpenAI gpt-oss-120b and gpt-oss-20b open-weight models),
Qdrant (local mode) with fastembed `BAAI/bge-small-en-v1.5` embeddings, SQLite, n8n, Streamlit.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # put your GROQ_API_KEY in .env
streamlit run streamlit_app.py
```

Demo PRNs: `22070126001` (Aarav, borderline attendance in Cloud Computing) and
`22070126002` (Isha, Machine Learning attendance needs condonation).

## Tests

```bash
pytest -q                     # 26 deterministic tests (rules, tools, guard, nodes) - no API key needed
python -m eval.run_eval       # 24 end-to-end cases with the real LLM -> eval/results.md
```

## n8n workflow

Import `n8n/sahayak_request_workflow.json` into n8n, then:
1. Create a *Header Auth* credential with name `X-Sahayak-Token` and a secret value, and select it in the webhook node.
2. Connect Gmail and Google Sheets credentials; create a sheet with a tab named `Requests` and columns
   `Reference, Received, Student, Type, Details, Sent to, Status`, and paste its URL in the Sheets node.
3. Activate the workflow and set `N8N_WEBHOOK_URL` (production URL) and `N8N_SHARED_SECRET` for the app.

If n8n is not configured, approved requests are still saved with status `stored_pending_dispatch`.

## Deploy on Streamlit Community Cloud

New app -> this repository -> main file `streamlit_app.py`. In *Advanced settings -> Secrets* add:

```toml
GROQ_API_KEY = "..."
N8N_WEBHOOK_URL = "..."
N8N_SHARED_SECRET = "..."
FACULTY_EMAIL_OVERRIDE = ""   # optional: send all faculty mail to one inbox for the demo
```

## Project layout

```
app/        config, SQLite data, rules, RAG, tools, prompts, LangGraph graph, n8n client
data/       regulation sources (docs_src/*.md) and the PDFs that are indexed (docs/*.pdf)
eval/       end-to-end evaluation cases and results
n8n/        exported n8n workflow
scripts/    builds the regulation PDFs
tests/      pytest suite
streamlit_app.py
```
