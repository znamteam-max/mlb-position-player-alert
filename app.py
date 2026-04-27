import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

os.environ.setdefault("STATE_FILE", "/tmp/position_player_alert_state.json")

from bot import BotError, run
from command_extensions import handle_telegram_update

app = Flask(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def authorize_cron():
    expected = os.getenv("CRON_SECRET", "")
    if not expected:
        return jsonify({"ok": False, "error": "CRON_SECRET is not configured"}), 500

    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {expected}":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return None


def authorize_telegram_webhook():
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected:
        return None

    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret_header != expected:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    return None


@app.get("/")
def healthcheck():
    return jsonify(
        {
            "ok": True,
            "service": "mlb-position-player-alert",
            "cron_path": "/api/cron",
            "telegram_webhook_path": "/api/telegram",
            "timestamp": utc_now(),
        }
    )


@app.get("/api/cron")
def cron():
    unauthorized_response = authorize_cron()
    if unauthorized_response is not None:
        return unauthorized_response

    try:
        sent_count = run()
    except BotError as exc:
        return jsonify({"ok": False, "error": str(exc), "timestamp": utc_now()}), 500
    except Exception as exc:
        app.logger.exception("MLB position-player alert run failed")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Bot run failed",
                    "error_type": type(exc).__name__,
                    "timestamp": utc_now(),
                }
            ),
            500,
        )

    return jsonify({"ok": True, "alerts_sent": sent_count, "timestamp": utc_now()})


@app.post("/api/telegram")
def telegram_webhook():
    unauthorized_response = authorize_telegram_webhook()
    if unauthorized_response is not None:
        return unauthorized_response

    update = request.get_json(silent=True) or {}
    try:
        result = handle_telegram_update(update)
    except BotError as exc:
        return jsonify({"ok": False, "error": str(exc), "timestamp": utc_now()}), 500
    except Exception as exc:
        app.logger.exception("Telegram command handling failed")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Telegram command handling failed",
                    "error_type": type(exc).__name__,
                    "timestamp": utc_now(),
                }
            ),
            500,
        )

    return jsonify({"ok": True, "result": result, "timestamp": utc_now()})
