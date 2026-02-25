import asyncio
import importlib.util
import os
import sys
import threading
import time
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sentinel_core import run_cluster_stream
from sentinel import run_telegram_bot

app = FastAPI(title="CodeSentinel Enterprise")

ROOT       = os.path.dirname(__file__)
STATIC_DIR = os.path.join(ROOT, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Shared broadcast state
# One simulation runs at a time; every connected client (web + bot) gets the
# same SSE events from a single run_cluster_stream() call.
# ---------------------------------------------------------------------------
_svc_locked    = False           # True while the pipeline is running
_bot_triggered = False           # Set by /bot/activate, cleared after first read
_broadcast_queues: list[asyncio.Queue] = []
_bot_thread: threading.Thread | None = None


async def _run_and_broadcast(inject: bool = True) -> None:
    """Run the cluster stream once and fan-out every chunk to all subscribers."""
    global _svc_locked
    _svc_locked = True
    try:
        async for chunk in run_cluster_stream(inject=inject):
            for q in list(_broadcast_queues):
                q.put_nowait(chunk)
    finally:
        _svc_locked = False
        for q in list(_broadcast_queues):
            q.put_nowait(None)          # EOF — tell every subscriber to close


@app.on_event("startup")
async def _start_telegram_listener():
    """Start the Telegram listener in a daemon thread when the dashboard starts."""
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return
    _bot_thread = threading.Thread(
        target=run_telegram_bot,
        name="sentinel-telegram-listener",
        daemon=True,
    )
    _bot_thread.start()


@app.get("/service-status")
async def service_status():
    return {"locked": _svc_locked}


# ---------------------------------------------------------------------------
# Bot control endpoints
# ---------------------------------------------------------------------------

@app.post("/bot/activate")
async def bot_activate():
    """Triggered by the Telegram bot to start a simulation."""
    global _bot_triggered, _svc_locked
    if _svc_locked:
        return {"ok": False, "reason": "simulation already running"}
    _svc_locked    = True   # claim the lock before the task even starts
    _bot_triggered = True

    async def _delayed_start():
        # Wait 5 s so the dashboard JS (polling every 2 s) has time to open
        # its EventSource before the first SSE event is emitted.
        await asyncio.sleep(5)
        await _run_and_broadcast(inject=True)

    asyncio.create_task(_delayed_start())
    return {"ok": True}


@app.get("/bot/status")
async def bot_status():
    """Frontend polls this to know when the bot has triggered a simulation.
    The flag stays True until the stream is actually running so a browser
    that opens during the 5-second delay window still catches the trigger.
    """
    global _bot_triggered
    triggered = _bot_triggered
    # Clear the flag only once the broadcast has actually started.
    if _bot_triggered and _svc_locked:
        _bot_triggered = False
    return {"triggered": triggered, "locked": _svc_locked}


# ---------------------------------------------------------------------------
# Main SSE stream  (web button OR bot — both share the same broadcast)
# ---------------------------------------------------------------------------

@app.get("/stream")
async def stream(inject: bool = Query(default=True)):
    # If no broadcast is running yet (web button was clicked), start one.
    if not _svc_locked:
        asyncio.create_task(_run_and_broadcast(inject=inject))

    # Every caller (web tab, bot HTTP client) gets its own queue subscribed
    # to the single running broadcast.
    q: asyncio.Queue = asyncio.Queue()
    _broadcast_queues.append(q)

    async def _subscriber():
        try:
            while True:
                chunk = await q.get()
                if chunk is None:       # EOF sent by _run_and_broadcast
                    return
                yield chunk
        finally:
            try:
                _broadcast_queues.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        _subscriber(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Live /predict endpoint ────────────────────────────────────────────────
# Loads main.py directly from disk on every call via importlib.
# The *actual* predict() function runs — no file-reading tricks, no
# string-matching, no simulation.  If the bug is present it raises the
# real ZeroDivisionError.  If the AI has applied the fix, it returns the
# real response.  Whatever is on disk is what runs.

class PredictBody(BaseModel):
    features: List[float] = []


def _load_predict_module():
    """
    Force-reload main.py from disk, bypassing Python's module cache.
    Removing the key from sys.modules guarantees a fresh exec on every call,
    so bug injections and AI fixes are reflected with zero delay.
    """
    name = "_priceai_live"
    sys.modules.pop(name, None)          # drop stale cached version
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "main.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)         # actually runs main.py
    return mod


@app.post("/predict")
async def predict_live(body: PredictBody):
    t0 = time.time()
    try:
        mod    = _load_predict_module()
        req    = mod.PredictionRequest(features=body.features)
        result = mod.predict(req)        # real function, real result

        return {
            "prediction":    result.prediction,
            "feature_count": result.feature_count,
            "ms":            round((time.time() - t0) * 1000, 1),
        }

    except ZeroDivisionError:
        # Real exception propagating from main.py — not constructed by us
        raise HTTPException(
            status_code=500,
            detail={
                "error":   "ZeroDivisionError",
                "message": "division by zero",
                "file":    "main.py",
                "line":    27,
                "code":    "float(tensor.sum()) / len(request.features)",
                "cause":   "len(request.features) == 0  →  cannot divide by zero",
            },
        )

    except Exception as e:
        # Any other real exception from main.py (syntax error during inject, etc.)
        raise HTTPException(
            status_code=500,
            detail={
                "error":   type(e).__name__,
                "message": str(e),
                "file":    "main.py",
            },
        )


@app.get("/service", response_class=HTMLResponse)
async def service():
    return FileResponse(os.path.join(STATIC_DIR, "service.html"))


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard:app", host="0.0.0.0", port=8080, reload=False)
