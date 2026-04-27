import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

os.environ.setdefault("STATE_FILE", "/tmp/position_player_alert_state.json")

from bot import BotError, run

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


@app.get("/")
def healthcheck():
    return jsonify(
        {
            "ok": True,
            "service": "mlb-position-player-alert",
            "cron_path": "/api/cron",
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
