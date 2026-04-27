import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/feed/live"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_ONLY_LATE_INNINGS = env_bool("ALERT_ONLY_LATE_INNINGS", True)
LATE_INNING_THRESHOLD = int(os.getenv("LATE_INNING_THRESHOLD", "7"))
INCLUDE_BLOWOUT_ONLY = env_bool("INCLUDE_BLOWOUT_ONLY", False)
SCORE_DIFF_THRESHOLD = int(os.getenv("SCORE_DIFF_THRESHOLD", "6"))
STATE_FILE = Path(os.getenv("STATE_FILE", ".position_player_alert_state.json"))
REQUEST_TIMEOUT = 20
USER_AGENT = "mlb-position-player-alert-bot/1.0"


class BotError(Exception):
    pass


session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"alerts": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"alerts": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def prune_state(state: Dict[str, Any], keep_hours: int = 72) -> Dict[str, Any]:
    alerts = state.get("alerts", {})
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - keep_hours * 3600
    fresh_alerts = {k: v for k, v in alerts.items() if isinstance(v, (int, float)) and v >= cutoff}
    return {"alerts": fresh_alerts}


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_live_game_pks() -> List[int]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = get_json(SCHEDULE_URL, params={"sportId": 1, "date": today})

    game_pks: List[int] = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            status = game.get("status", {})
            if status.get("abstractGameState") == "Live":
                game_pk = game.get("gamePk")
                if isinstance(game_pk, int):
                    game_pks.append(game_pk)
    return game_pks


def safe_get(dct: Dict[str, Any], *keys: str) -> Any:
    current: Any = dct
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def build_alert(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    game_data = feed.get("gameData", {})
    live_data = feed.get("liveData", {})
    linescore = live_data.get("linescore", {})
    defense = linescore.get("defense", {})
    pitcher = defense.get("pitcher", {})
    pitcher_id = pitcher.get("id")

    if not pitcher_id:
        return None

    player_key = f"ID{pitcher_id}"
    player_data = safe_get(game_data, "players", player_key) or {}
    primary_position = safe_get(player_data, "primaryPosition", "abbreviation")

    if not primary_position or primary_position == "P":
        return None

    current_inning = linescore.get("currentInning")
    is_top = bool(linescore.get("isTopInning"))

    if ALERT_ONLY_LATE_INNINGS and isinstance(current_inning, int) and current_inning < LATE_INNING_THRESHOLD:
        return None

    home_team = safe_get(game_data, "teams", "home", "name") or "Home"
    away_team = safe_get(game_data, "teams", "away", "name") or "Away"
    home_runs = safe_get(linescore, "teams", "home", "runs")
    away_runs = safe_get(linescore, "teams", "away", "runs")

    score_diff: Optional[int] = None
    if isinstance(home_runs, int) and isinstance(away_runs, int):
        score_diff = abs(home_runs - away_runs)

    if INCLUDE_BLOWOUT_ONLY and (score_diff is None or score_diff < SCORE_DIFF_THRESHOLD):
        return None

    defensive_team = home_team if is_top else away_team
    batting_team = away_team if is_top else home_team
    inning_half = "Top" if is_top else "Bottom"
    pitcher_name = pitcher.get("fullName") or safe_get(player_data, "fullName") or f"Player {pitcher_id}"
    detailed_state = safe_get(game_data, "status", "detailedState") or "Live"
    game_pk = game_data.get("game", {}).get("pk")

    score_text = "?–?"
    if isinstance(away_runs, int) and isinstance(home_runs, int):
        score_text = f"{away_team} {away_runs} – {home_runs} {home_team}"

    message = (
        "🚨 Position player pitching alert\n\n"
        f"{pitcher_name} is currently pitching for {defensive_team}.\n"
        f"Primary position: {primary_position}\n"
        f"Game: {away_team} at {home_team}\n"
        f"Situation: {inning_half} {current_inning}, {score_text}\n"
        f"Batting team: {batting_team}\n"
        f"Status: {detailed_state}\n"
        f"gamePk: {game_pk}"
    )

    return {
        "game_pk": game_pk,
        "pitcher_id": pitcher_id,
        "message": message,
        "key": f"{game_pk}:{pitcher_id}",
    }


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise BotError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    response = session.post(
        TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN),
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def run() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise BotError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running the bot.")

    state = prune_state(load_state())
    alerts = state.setdefault("alerts", {})
    live_game_pks = get_live_game_pks()

    if not live_game_pks:
        print("No live MLB games found.")
        save_state(state)
        return 0

    sent_count = 0

    for game_pk in live_game_pks:
        feed = get_json(LIVE_FEED_URL.format(game_pk=game_pk))
        alert = build_alert(feed)
        if not alert:
            continue

        key = alert["key"]
        if key in alerts:
            continue

        send_telegram_message(alert["message"])
        alerts[key] = datetime.now(timezone.utc).timestamp()
        sent_count += 1
        print(f"Sent alert for {key}")

    save_state(state)
    print(f"Done. Alerts sent: {sent_count}")
    return sent_count


if __name__ == "__main__":
    run()
