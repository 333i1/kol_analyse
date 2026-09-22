# KOL / YouTube single-video analysis (kol_analyse)

Resume-oriented public snapshot: **backend** (FastAPI) + **frontend**.

## Features
- YouTube URL ingest (metadata / captions / comments)
- LLM structured breakdown + comment sentiment
- Task status and error codes

## Quick start
1. Copy `.env.example` to `.env` and fill LLM / YouTube keys (never commit `.env`)
2. `pip install -r requirements.txt`
3. Start API from `backend`: `python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
4. Open `frontend/index.html` in a browser

## Layout
- `backend/` API, worker, prompts
- `frontend/` UI and session adapters
- `.env.example` env template

No local DB, secrets, or unrelated dumps are included.
