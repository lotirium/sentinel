# CodeSentinel

Autonomous production incident response system built on top of [OpenClaw](https://openclaw.ai) — an AI agent (powered by Claude) that reads code, diagnoses bugs, writes patches, runs tests, and commits fixes.

When a service breaks and tests fail — **no engineer needed**. CodeSentinel detects the problem, calls OpenClaw, and resolves the incident end-to-end in minutes.

---

## How it works

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Phase 0    │    │  Phase 1    │    │  Phase 2    │
│             │    │             │    │             │
│  Bug        │───▶│  Run tests  │───▶│  Telegram   │
│  injected   │    │  (pytest)   │    │  alert sent │
└─────────────┘    └─────────────┘    └─────────────┘
                         │ tests fail
                         ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Phase 5    │    │  Phase 4    │    │  Phase 3    │
│             │    │             │    │             │
│  Incident   │◀───│  Patch      │◀───│  OpenClaw   │
│  closed     │    │  verified   │    │  fixes bug  │
└─────────────┘    └─────────────┘    └─────────────┘
```

1. **Detect** — `pytest` runs against monitored services; failures trigger an incident
2. **Alert** — Telegram receives an incident notification immediately
3. **Fix** — OpenClaw is invoked per failing service (in parallel via `asyncio.gather`), reads the code, reasons about the root cause, and applies a patch
4. **Verify** — tests run again; green means the patch is accepted
5. **Close** — Telegram confirms the incident is resolved, all changes committed

---

## Monitored services

| Service | File | Bug demonstrated |
|---------|------|-----------------|
| Price Prediction API | `main.py` | `ZeroDivisionError` on empty feature list |
| Torch Inference Worker | `services/torch_worker.py` | `RuntimeError` — tensor dtype `int32` instead of `float32` |
| Data Pipeline | `services/data_pipeline.py` | `KeyError` — direct `dict["price"]` instead of `.get()` |

Each service has a corresponding test suite under `tests/`. The dashboard also exposes a live `/predict` endpoint that reloads `main.py` from disk on every call — real code, real result, no simulation.

---

## Features

- **Parallel recovery** — multiple broken services are fixed simultaneously; total recovery time equals the slowest agent, not the sum
- **Real-time web dashboard** — streams the full incident lifecycle via SSE (Server-Sent Events) on port `8080`, including the agent's reasoning chain and a security scan of each patch
- **Telegram bot** — incident alerts, live progress streaming, and natural-language code editing (`add a /metrics endpoint to main.py`)
- **Security scan** per patch — checks for hardcoded secrets, SQL injection vectors, new network ports, path leakage, and new dependencies

---

## Project structure

```
sentinel/
├── main.py                    — Price Prediction FastAPI service (port 8000)
├── services/
│   ├── torch_worker.py        — Vector similarity service (PyTorch)
│   └── data_pipeline.py       — Batch data processing service
├── tests/
│   ├── test_api.py
│   ├── test_torch_worker.py
│   └── test_data_pipeline.py
├── sentinel_core.py           — Incident pipeline, OpenClaw invocation
├── sentinel.py                — Telegram bot + standalone runner
├── dashboard.py               — Dashboard web server (port 8080)
├── static/index.html          — Dashboard frontend
├── .env                       — TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
└── requirements.txt
```

---

## Setup

### 1. Configure `.env`

```
TELEGRAM_BOT_TOKEN=your_token_from_BotFather
TELEGRAM_CHAT_ID=your_chat_id
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the dashboard

```bash
python dashboard.py
```

Open `http://localhost:8080` in your browser. The Telegram bot starts automatically alongside the dashboard.

---

## Telegram bot commands

| Command | Action |
|---------|--------|
| `/help` | Show available commands |
| `/status` | Current system state |
| `Activate the test with simulation` | Run the full incident simulation |
| Any other text | Forwarded to OpenClaw as a code task |

---

## Tech stack

| Technology | Role |
|------------|------|
| **OpenClaw** | AI agent — reads code, writes patches, runs tests, commits |
| **FastAPI** | Web framework for services and dashboard |
| **PyTorch** | Tensor operations in Price Prediction and Torch Worker |
| **pytest** | Test suite — service health indicator |
| **SSE** | Real-time event streaming to the browser |
| **asyncio** | Parallel multi-agent execution |
| **Telegram Bot API** | Incident alerts and chat-based code editing |
| **WSL** | Bridge for running OpenClaw (Linux CLI) from Windows |
| **python-dotenv** | Token management via `.env` |

---

## The value

**Without CodeSentinel:** service goes down → on-call engineer gets paged → reads logs → finds the cause → writes a patch → deploys → verifies. 30–60 minutes of downtime.

**With CodeSentinel + OpenClaw:** service goes down → system detects it → OpenClaw diagnoses, fixes, and verifies → Telegram: "incident closed". 2–3 minutes. The engineer doesn't even wake up.
