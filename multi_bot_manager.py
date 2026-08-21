"""
multi_bot_manager.py
Multi-Bot Polling Manager & Background Thread Supervisor.

Manages resilient, isolated long-polling workers for all ecosystem bots:
- GlucoTrack Bot (gluco_track)
- MedFlowAssist Bot (med_flow)
- MonkeHelper Master Hub (monke_helper)
- Circadian & Biometrics Bot (biometrics)

Features:
- Webhook collision prevention (deleteWebhook with drop_pending_updates=False)
- Dynamic HTTP 409 Conflict recovery
- Jittered exponential backoff on network errors
- HTTP 429 rate limit backoff and 401/404 token validation
- Graceful startup/shutdown lifecycle management
"""

import os
import time
import random
import threading
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable, Any
from bot_client import get_bot_client, mask_token, TELEGRAM_API_BASE


class BotPollerWorker:
    """Manages an isolated long-polling loop on a dedicated thread for a single bot token."""

    def __init__(
        self,
        bot_id: str,
        name: str,
        token_getter: Callable[[], Optional[str]],
        handler: Callable[[dict], Any],
        allowed_updates: Optional[List[str]] = None,
        poll_timeout: int = 20,
        client_timeout: int = 25
    ):
        self.bot_id = bot_id
        self.name = name
        self.token_getter = token_getter
        self.handler = handler
        self.allowed_updates = allowed_updates or ["message", "callback_query", "my_chat_member"]
        self.poll_timeout = poll_timeout
        self.client_timeout = client_timeout

        self._offset = 0
        self._is_running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._webhook_cleaned = False
        self._consecutive_errors = 0
        self._status = "stopped"
        self._last_poll_time: Optional[datetime] = None
        self._last_success_time: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._updates_count = 0
        self._lock = threading.Lock()

    def start(self) -> bool:
        """Starts the background long-polling thread for this bot."""
        with self._lock:
            if self._is_running and self._thread and self._thread.is_alive():
                return True

            self._is_running = True
            self._stop_event.clear()
            self._status = "starting"
            self._webhook_cleaned = False
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"PollerWorker-{self.bot_id}",
                daemon=True
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Signals the worker to stop and waits for thread completion."""
        with self._lock:
            if not self._is_running:
                return True

            self._status = "stopping"
            self._is_running = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

        self._status = "stopped"
        return True

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def get_status(self) -> dict:
        """Returns diagnostic status of the worker."""
        token = None
        try:
            token = self.token_getter()
        except Exception:
            token = None

        return {
            "bot_id": self.bot_id,
            "name": self.name,
            "status": self._status,
            "is_alive": self.is_alive(),
            "offset": self._offset,
            "updates_count": self._updates_count,
            "consecutive_errors": self._consecutive_errors,
            "has_token": bool(token),
            "masked_token": mask_token(token),
            "last_poll_time": self._last_poll_time.isoformat() if self._last_poll_time else None,
            "last_success_time": self._last_success_time.isoformat() if self._last_success_time else None,
            "last_error": self._last_error
        }

    def _delete_webhook(self, token: str) -> bool:
        """Deletes any registered webhook on Telegram's servers to permit getUpdates polling."""
        url = f"{TELEGRAM_API_BASE}{token}/deleteWebhook"
        try:
            resp = requests.post(url, json={"drop_pending_updates": False}, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                print(f"[{self.name}] Webhook cleanly deleted for long-polling.")
                self._webhook_cleaned = True
                return True
            else:
                print(f"[{self.name}] deleteWebhook response: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            print(f"[{self.name}] Error executing deleteWebhook: {e}")
            return False

    def _run_loop(self):
        """Dedicated execution loop for long-polling."""
        print(f"[{self.name}] Polling worker loop initiated.")

        while not self._stop_event.is_set():
            token = None
            try:
                token = self.token_getter()
            except Exception as te:
                print(f"[{self.name}] Error resolving token: {te}")

            if not token:
                self._status = "paused_no_token"
                self._stop_event.wait(5.0)
                continue

            # Clean webhook state once on startup or when needed
            if not self._webhook_cleaned:
                self._delete_webhook(token)

            url = f"{TELEGRAM_API_BASE}{token}/getUpdates"
            params = {
                "offset": self._offset,
                "timeout": self.poll_timeout,
                "allowed_updates": self.allowed_updates
            }

            try:
                self._status = "running"
                resp = requests.get(url, params=params, timeout=self.client_timeout)
                self._last_poll_time = datetime.now(timezone.utc)

                # 1. Success (HTTP 200)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        results = data.get("result", [])
                        self._consecutive_errors = 0
                        self._last_success_time = datetime.now(timezone.utc)
                        self._last_error = None

                        for update in results:
                            if self._stop_event.is_set():
                                break
                            up_id = update.get("update_id", 0)
                            self._offset = max(self._offset, up_id + 1)
                            self._updates_count += 1
                            try:
                                self.handler(update)
                            except Exception as he:
                                print(f"[{self.name}] Handler error on update {up_id}: {he}")
                    else:
                        err_desc = data.get("description", "Unknown Telegram error")
                        self._handle_api_error(token, 200, err_desc, data)

                # 2. Webhook Collision Conflict (HTTP 409)
                elif resp.status_code == 409:
                    print(f"[{self.name}] HTTP 409 Conflict: Webhook active. Calling deleteWebhook...")
                    self._delete_webhook(token)
                    self._stop_event.wait(1.0)

                # 3. Rate Limited (HTTP 429)
                elif resp.status_code == 429:
                    retry_after = 5
                    try:
                        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                    except Exception:
                        pass
                    print(f"[{self.name}] HTTP 429 Too Many Requests. Sleeping {retry_after}s...")
                    self._status = "backoff"
                    self._stop_event.wait(retry_after + 1.0)

                # 4. Auth / Not Found (HTTP 401 / 404)
                elif resp.status_code in [401, 404]:
                    self._status = "auth_failed"
                    self._last_error = f"HTTP {resp.status_code}: Invalid or revoked Bot Token"
                    print(f"[{self.name}] Auth error: {self._last_error}. Pausing 30s...")
                    self._stop_event.wait(30.0)

                # 5. Server Errors (HTTP 5xx)
                else:
                    self._handle_network_failure(f"HTTP {resp.status_code}: {resp.text[:100]}")

            # 6. Expected Long-Polling Timeout
            except requests.exceptions.Timeout:
                self._last_poll_time = datetime.now(timezone.utc)
                self._consecutive_errors = 0
                continue

            # 7. Network / Connection Exceptions
            except (requests.exceptions.ConnectionError, requests.exceptions.RequestException, Exception) as e:
                self._handle_network_failure(str(e))

        self._status = "stopped"
        print(f"[{self.name}] Polling worker loop terminated.")

    def _handle_api_error(self, token: str, code: int, desc: str, payload: dict):
        """Processes non-OK API response bodies."""
        if "conflict" in desc.lower() or payload.get("error_code") == 409:
            print(f"[{self.name}] Telegram Conflict Notice: {desc}. Deleting webhook...")
            self._delete_webhook(token)
            self._stop_event.wait(1.0)
        else:
            self._handle_network_failure(f"API Error {code}: {desc}")

    def _handle_network_failure(self, error_msg: str):
        """Implements jittered exponential backoff on consecutive network failures."""
        self._consecutive_errors += 1
        self._last_error = error_msg
        self._status = "backoff"

        backoff = min(60.0, 2.0 * (2 ** min(self._consecutive_errors - 1, 5))) + random.uniform(0, 1.0)
        print(f"[{self.name}] Network issue ({error_msg}). Backoff {backoff:.1f}s (Attempt #{self._consecutive_errors}).")
        self._stop_event.wait(backoff)


class MultiBotPollingManager:
    """Supervisor managing multiple isolated BotPollerWorker instances."""

    def __init__(self):
        self._workers: Dict[str, BotPollerWorker] = {}
        self._lock = threading.Lock()

    def register_bot(
        self,
        bot_id: str,
        name: str,
        token_getter: Callable[[], Optional[str]],
        handler: Callable[[dict], Any],
        allowed_updates: Optional[List[str]] = None,
        auto_start: bool = False
    ) -> BotPollerWorker:
        """Registers a bot worker in the supervisor."""
        with self._lock:
            if bot_id in self._workers:
                worker = self._workers[bot_id]
                worker.token_getter = token_getter
                worker.handler = handler
                if auto_start:
                    worker.start()
                return worker

            worker = BotPollerWorker(
                bot_id=bot_id,
                name=name,
                token_getter=token_getter,
                handler=handler,
                allowed_updates=allowed_updates
            )
            self._workers[bot_id] = worker

        if auto_start:
            worker.start()
        return worker

    def start_all(self):
        """Starts all registered bot polling workers."""
        with self._lock:
            workers = list(self._workers.values())
        for w in workers:
            w.start()

    def stop_all(self, timeout: float = 5.0):
        """Stops all registered bot polling workers."""
        with self._lock:
            workers = list(self._workers.values())
        for w in workers:
            w.stop(timeout=timeout)

    def start_bot(self, bot_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(bot_id)
        if worker:
            return worker.start()
        return False

    def stop_bot(self, bot_id: str, timeout: float = 5.0) -> bool:
        with self._lock:
            worker = self._workers.get(bot_id)
        if worker:
            return worker.stop(timeout=timeout)
        return False

    def restart_bot(self, bot_id: str, timeout: float = 5.0) -> bool:
        with self._lock:
            worker = self._workers.get(bot_id)
        if worker:
            worker.stop(timeout=timeout)
            return worker.start()
        return False

    def get_status(self, bot_id: Optional[str] = None) -> dict:
        """Returns health status for one or all workers."""
        with self._lock:
            if bot_id:
                w = self._workers.get(bot_id)
                return w.get_status() if w else {"error": f"Bot {bot_id} not registered"}
            return {b_id: w.get_status() for b_id, w in self._workers.items()}

    def watchdog_check(self):
        """Watchdog routine that restarts any worker threads that died unexpectedly."""
        with self._lock:
            for b_id, worker in self._workers.items():
                if worker._is_running and not worker.is_alive():
                    print(f"[Watchdog] Worker for '{worker.name}' ({b_id}) died unexpectedly. Restarting...")
                    worker.start()


# Global Singleton Supervisor
multi_bot_manager = MultiBotPollingManager()


def setup_default_bots():
    """Registers standard ecosystem bots with safe fallback handlers."""
    # 1. GlucoTrack Bot
    def get_gt_token():
        return get_bot_client("gluco_track").token

    def handle_gt_update(update):
        import telegram_bot
        return telegram_bot.handle_telegram_update(update)

    multi_bot_manager.register_bot(
        bot_id="gluco_track",
        name="GlucoTrack Bot",
        token_getter=get_gt_token,
        handler=handle_gt_update
    )

    # 2. MedFlowAssist Bot
    def get_med_token():
        return get_bot_client("med_flow").token

    def handle_med_update(update):
        try:
            import med_bot
            return med_bot.handle_med_webhook(update)
        except Exception as e:
            print(f"[MedBot] Handler error: {e}")
            return {"status": "error", "message": str(e)}

    multi_bot_manager.register_bot(
        bot_id="med_flow",
        name="MedFlowAssist Bot",
        token_getter=get_med_token,
        handler=handle_med_update
    )

    # 3. MonkeHelperBot (Master Hub)
    def get_monke_token():
        return get_bot_client("monke_helper").token

    def handle_monke_update(update):
        try:
            import monke_bot
            return monke_bot.handle_monke_webhook(update)
        except ImportError:
            print("[MonkeHelperBot] monke_bot module not yet available.")
            return {"status": "ok", "action": "unimplemented"}
        except Exception as e:
            print(f"[MonkeHelperBot] Handler error: {e}")
            return {"status": "error", "message": str(e)}

    multi_bot_manager.register_bot(
        bot_id="monke_helper",
        name="MonkeHelperBot",
        token_getter=get_monke_token,
        handler=handle_monke_update
    )

    # 4. Circadian & Biometrics Bot
    def get_bio_token():
        return get_bot_client("biometrics").token

    def handle_bio_update(update):
        try:
            import biometrics_bot
            return biometrics_bot.handle_biometrics_webhook(update)
        except ImportError:
            print("[BiometricsBot] biometrics_bot module not yet available.")
            return {"status": "ok", "action": "unimplemented"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    multi_bot_manager.register_bot(
        bot_id="biometrics",
        name="Circadian & Biometrics Bot",
        token_getter=get_bio_token,
        handler=handle_bio_update
    )


# Run default registration on import
setup_default_bots()
