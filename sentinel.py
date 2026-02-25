import os
import re
import subprocess
import sys
import threading
import time

import requests
from dotenv import load_dotenv

# 1) Load secrets from .env (Bot Token and Chat ID)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Settings ---
TEST_COMMAND = [sys.executable, "-m", "pytest", "tests/test_api.py"]
TARGET_FILE = "main.py"
OPENCLAW = "/home/nia/.npm-global/bin/openclaw"
TELEGRAM_API = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else None
_BOT_BUSY      = False   # True while simulation is running
_OPENCLAW_BUSY = False   # True while an OpenClaw edit is in progress
_LAST_RESULT   = "No simulation has been run yet."


def send_telegram(message: str, chat_id: str | None = None):
    """Send a message to Telegram."""
    if not TOKEN or not TELEGRAM_API:
        print("Telegram token not found in .env, skipping alert.")
        return

    target_chat = chat_id or CHAT_ID
    if not target_chat:
        print("Telegram chat id not found in .env, skipping alert.")
        return

    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            data={"chat_id": target_chat, "text": message},
            timeout=10,
        )
        print("Telegram notification sent.")
    except Exception as exc:
        print(f"Failed to send Telegram: {exc}")


def send_telegram_reply(message: str, chat_id: str, reply_to_message_id: int | None = None):
    """Send a message and optionally reply to a specific message."""
    if not TOKEN or not TELEGRAM_API:
        return

    data = {"chat_id": chat_id, "text": message}
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = reply_to_message_id

    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", data=data, timeout=10)
    except Exception as exc:
        print(f"Failed to send Telegram reply: {exc}")


def run_tests():
    """Run the test suite and return (passed: bool, log: str)."""
    print(f"Running tests: {' '.join(TEST_COMMAND)}...")
    result = subprocess.run(TEST_COMMAND, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def _maybe_commit_fix():
    """Commit only when project is a git repository."""
    if not os.path.isdir(os.path.join(os.path.dirname(__file__), ".git")):
        return
    subprocess.run(["git", "add", TARGET_FILE], check=False)
    subprocess.run(["git", "commit", "-m", "fix: handle empty input in prediction logic"], check=False)


def fix_with_openclaw(error_log: str) -> tuple[bool, str]:
    """Invoke OpenClaw agent and return (success, output)."""
    print("Activating OpenClaw agent...")

    # Derive the WSL equivalent of project root (C:\foo -> /mnt/c/foo)
    project_root_win = os.path.abspath(os.path.dirname(__file__))
    wsl_root = "/mnt/" + project_root_win[0].lower() + project_root_win[2:].replace("\\", "/")

    prompt = (
        "I have a ZeroDivisionError in main.py when the input features list is empty.\n\n"
        "Read main.py and tests/test_api.py.\n\n"
        "Fix the bug in main.py by adding a check: if the list is empty, return a default "
        "prediction of 0.0 with a 200 status.\n\n"
        "Ensure the fix is memory efficient.\n\n"
        "Run 'pytest tests/test_api.py' to verify.\n\n"
        "If tests pass, commit the change with message: 'fix: handle empty input in predict'.\n\n"
        f"Traceback for reference:\n{error_log[-800:]}"
    )

    # Write prompt to file to avoid shell escaping issues.
    prompt_file = os.path.join(project_root_win, "_sentinel_prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as file:
        file.write(prompt)

    wsl_prompt_file = "/mnt/" + prompt_file[0].lower() + prompt_file[2:].replace("\\", "/")
    bash_cmd = f'cd {wsl_root} && {OPENCLAW} agent --agent main --message "$(cat {wsl_prompt_file})"'

    print(f"Project root (WSL): {wsl_root}")
    print(f"Prompt path (WSL):  {wsl_prompt_file}")
    print("Waiting for OpenClaw to apply fix (30-120s)...")

    try:
        result = subprocess.run(
            ["wsl", "bash", "-c", bash_cmd],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return True, output
    except FileNotFoundError:
        return False, "'wsl' command not found. Make sure WSL is installed and accessible."
    except subprocess.TimeoutExpired:
        return False, "OpenClaw timed out after 180s."
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + ("\n" + exc.stderr if exc.stderr else "")
        return False, f"OpenClaw exited with code {exc.returncode}.\n{output}"


_ANSI = re.compile(r'\x1b\[[0-9;]*[mGKHFJA-Z]')
_SKIP = ("npm warn", "npm notice", "> openclaw@", "node_modules",
         "deprecated", "added ", "audited ", "found 0")
# Lines containing these words get forwarded to Telegram as they arrive
_STREAM_KEYWORDS = (
    "reading", "writing", "editing", "creating", "fixing", "patching",
    "running", "passed", "failed", "error", "added", "changed", "updated",
    "done", "complete", "verified", "test",
)


def _clean_line(raw: str) -> str:
    return _ANSI.sub('', raw).strip()


def _send_chat_action(chat_id: str, action: str = "typing") -> None:
    """Show a live 'typing…' indicator in Telegram (lasts 5 s)."""
    if not TOKEN or not TELEGRAM_API:
        return
    try:
        requests.post(
            f"{TELEGRAM_API}/sendChatAction",
            data={"chat_id": chat_id, "action": action},
            timeout=5,
        )
    except Exception:
        pass


def _ask_openclaw(user_message: str, chat_id: str, reply_to_message_id: int | None = None):
    """
    Stream OpenClaw output line-by-line to Telegram in real time.
    The user sees progress as it happens — no waiting for the full run.
    """
    global _OPENCLAW_BUSY

    if _OPENCLAW_BUSY:
        send_telegram_reply("OpenClaw is already working. Try again shortly.",
                            chat_id, reply_to_message_id)
        return
    if _BOT_BUSY:
        send_telegram_reply("Simulation in progress. Wait for it to finish first.",
                            chat_id, reply_to_message_id)
        return

    _OPENCLAW_BUSY = True

    # Keep the typing indicator alive while OpenClaw works.
    stop_typing = threading.Event()

    def _typing_loop():
        while not stop_typing.is_set():
            _send_chat_action(chat_id)
            stop_typing.wait(4)   # Telegram indicator expires after 5 s

    threading.Thread(target=_typing_loop, daemon=True).start()

    project_root_win = os.path.abspath(os.path.dirname(__file__))
    wsl_root = "/mnt/" + project_root_win[0].lower() + project_root_win[2:].replace("\\", "/")

    prompt = (
        f"Project location: {wsl_root}\n\n"
        "Project overview:\n"
        "  main.py              — FastAPI Price Prediction service (port 8000)\n"
        "  tests/test_api.py    — pytest test suite for main.py\n"
        "  services/            — additional service modules\n"
        "  dashboard.py         — dashboard server (port 8080)\n"
        "  static/index.html    — dashboard frontend\n\n"
        f"User instruction:\n{user_message}\n\n"
        "Apply the requested change. If code with existing tests is affected, "
        "run the relevant pytest suite to verify. Briefly confirm what was changed."
    )

    prompt_file = os.path.join(project_root_win, "_bot_prompt.txt")
    with open(prompt_file, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    wsl_prompt = "/mnt/" + prompt_file[0].lower() + prompt_file[2:].replace("\\", "/")
    bash_cmd = f'cd {wsl_root} && {OPENCLAW} agent --agent main --message "$(cat {wsl_prompt})"'

    try:
        proc = subprocess.Popen(
            ["wsl", "bash", "-c", bash_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )

        # Kill the process if it runs longer than 3 minutes.
        killer = threading.Timer(180, lambda: proc.kill() if proc.poll() is None else None)
        killer.start()

        collected: list[str] = []
        last_sent = 0.0

        for raw in proc.stdout:
            line = _clean_line(raw)
            if not line:
                continue
            if any(line.lower().startswith(s) for s in _SKIP):
                continue

            collected.append(line)

            # Rate-limit live updates: max one message every 6 s,
            # but always send lines with meaningful keywords immediately.
            is_key = any(kw in line.lower() for kw in _STREAM_KEYWORDS)
            now = time.time()
            if is_key and now - last_sent > 6:
                send_telegram_reply(line[:400], chat_id)
                last_sent = now

        proc.wait()
        killer.cancel()

        # Final summary — last few lines not yet sent
        tail = "\n".join(collected[-6:]) if collected else ""

        if not tail:
            send_telegram_reply("Done.", chat_id)
        elif "rate limit" in tail.lower() or "429" in tail:
            send_telegram_reply(
                "OpenClaw hit a rate limit. Wait a minute and try again.", chat_id)
        else:
            send_telegram_reply(f"Done.\n\n{tail[:600]}", chat_id)

    except FileNotFoundError:
        send_telegram_reply("WSL is not accessible. Cannot run OpenClaw.", chat_id)
    except Exception as exc:
        send_telegram_reply(f"Error: {exc}", chat_id)
    finally:
        stop_typing.set()
        _OPENCLAW_BUSY = False


def _should_activate(text: str) -> bool:
    normalized = text.lower().strip()
    trigger_words = ("activate", "run", "start", "simulate")
    has_trigger = any(word in normalized for word in trigger_words)
    has_target = ("test" in normalized) or ("simulation" in normalized) or ("sentinel" in normalized)
    return has_trigger and has_target


def _detect_intent(text: str) -> str:
    normalized = text.lower().strip()
    if normalized in {"/start", "/help", "help"}:
        return "help"
    if normalized in {"/status", "status"}:
        return "status"
    if _should_activate(normalized):
        return "activate"
    return "chat"


def _handle_activation(chat_id: str, command_text: str, reply_to_message_id: int | None = None):
    """
    Trigger simulation via the dashboard HTTP endpoint.
    The dashboard runs the single shared broadcast stream; the web page and
    this bot both consume the same run — no double simulation.
    sentinel_core sends the incident + resolution Telegram messages automatically.
    """
    global _BOT_BUSY, _LAST_RESULT
    print(f"Activation command received: {command_text}")
    if _BOT_BUSY:
        send_telegram_reply("Simulation already running. Use /status to check.", chat_id, reply_to_message_id)
        return

    _BOT_BUSY = True
    try:
        resp = requests.post("http://localhost:8080/bot/activate", timeout=5)
        data = resp.json()
        if not data.get("ok"):
            reason = data.get("reason", "unknown error")
            send_telegram_reply(f"Could not start simulation: {reason}", chat_id, reply_to_message_id)
            return

        send_telegram_reply("Simulation started.", chat_id, reply_to_message_id)

        # Wait for the simulation to finish by polling service-status.
        # sentinel_core sends the incident + resolution messages on its own.
        time.sleep(3)
        for _ in range(120):    # max ~4 minutes
            time.sleep(2)
            try:
                st = requests.get("http://localhost:8080/service-status", timeout=5).json()
                if not st.get("locked"):
                    break
            except Exception:
                pass

        _LAST_RESULT = "Last run completed."

    except requests.exceptions.ConnectionError:
        _LAST_RESULT = "Failed — dashboard not reachable."
        send_telegram_reply("Dashboard not reachable. Is it running on port 8080?", chat_id)
    except Exception as exc:
        _LAST_RESULT = f"Failed — {type(exc).__name__}."
        send_telegram_reply(f"Error: {exc}", chat_id)
    finally:
        _BOT_BUSY = False



def _get_updates(offset: int | None = None, timeout: int = 25):
    """Long-poll Telegram updates."""
    if not TOKEN or not TELEGRAM_API:
        return []

    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    try:
        response = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=timeout + 5)
        payload = response.json()
        return payload.get("result", [])
    except Exception as exc:
        print(f"Failed to fetch updates: {exc}")
        time.sleep(3)
        return []


def run_telegram_bot():
    """Listen for Telegram commands and trigger Sentinel pipeline."""
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN missing in .env.")
        return

    print("Telegram bot listener started. Waiting for commands...")
    send_telegram("CodeSentinel online.\nSend: Activate the test with simulation")

    next_offset = None
    while True:
        updates = _get_updates(offset=next_offset, timeout=25)
        for update in updates:
            update_id = update.get("update_id")
            next_offset = update_id + 1 if update_id is not None else next_offset

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            text = (message.get("text") or "").strip()
            chat_id = str(message["chat"]["id"])
            message_id = message.get("message_id")

            # If TELEGRAM_CHAT_ID is set, only accept commands from that chat.
            if CHAT_ID and str(CHAT_ID) != chat_id:
                continue

            intent = _detect_intent(text)

            if intent == "help":
                send_telegram_reply(
                    "CodeSentinel — what you can do:\n\n"
                    "Run a simulation:\n"
                    "  Activate the test with simulation\n\n"
                    "Edit the service with OpenClaw (just type naturally):\n"
                    "  add a /health endpoint to main.py\n"
                    "  change the prediction formula to use the median\n"
                    "  add input validation for negative numbers\n\n"
                    "Commands:\n"
                    "  /status — check current activity\n"
                    "  /help   — show this message",
                    chat_id,
                    message_id,
                )
                continue

            if intent == "status":
                if _BOT_BUSY:
                    send_telegram_reply("Simulation in progress.", chat_id, message_id)
                elif _OPENCLAW_BUSY:
                    send_telegram_reply("OpenClaw is editing the service.", chat_id, message_id)
                else:
                    send_telegram_reply(f"Idle.\n{_LAST_RESULT}", chat_id, message_id)
                continue

            if intent == "activate":
                # Run in a thread so the bot loop stays responsive.
                threading.Thread(
                    target=_handle_activation,
                    args=(chat_id, text, message_id),
                    daemon=True,
                ).start()
                continue

            # Everything else → forward to OpenClaw as a code-edit instruction.
            threading.Thread(
                target=_ask_openclaw,
                args=(text, chat_id, message_id),
                daemon=True,
            ).start()


def main():
    print("CodeSentinel started in one-shot mode.")

    success, log = run_tests()
    if success:
        print("No bugs found. System stable.")
        return

    print("Alert: tests failed.")
    print("\n--- Test Log ---")
    print(log)
    print("----------------\n")

    send_telegram(
        "INCIDENT REPORT\n\n"
        "Project: PricePrediction AI\n"
        "Error: tests failed.\n"
        "Status: Engaging Sentinel AI..."
    )

    openclaw_ok, claw_output = fix_with_openclaw(log)
    if not openclaw_ok:
        print(claw_output)
        send_telegram("Fix failed to start. Human intervention required.")
        return

    time.sleep(5)
    print("\nVerifying fix...")
    success_fix, log_fix = run_tests()

    if success_fix:
        print("Fix confirmed.")
        _maybe_commit_fix()
        send_telegram(
            "ISSUE RESOLVED\n\n"
            f"Fix applied to {TARGET_FILE}.\n"
            "Tests passed."
        )
    else:
        print("Fix failed. Human intervention required.")
        print(claw_output)
        print(log_fix)
        send_telegram("Fix failed. Human intervention required.")


if __name__ == "__main__":
    # Usage:
    #   python sentinel.py        -> one-shot run
    #   python sentinel.py bot    -> telegram listener mode
    if len(sys.argv) > 1 and sys.argv[1].lower() == "bot":
        run_telegram_bot()
    else:
        main()
