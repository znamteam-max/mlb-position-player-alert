import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
LIVE_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
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
ENABLE_BLOWOUT_WARNING = env_bool("ENABLE_BLOWOUT_WARNING", True)
RECENT_CASE_LOOKBACK_DAYS = int(os.getenv("RECENT_CASE_LOOKBACK_DAYS", "90"))
RECENT_CASE_MIN_SCORE_DIFF = int(os.getenv("RECENT_CASE_MIN_SCORE_DIFF", "6"))
COMMAND_CACHE_TTL_SECONDS = int(os.getenv("COMMAND_CACHE_TTL_SECONDS", "1800"))
STATE_FILE = Path(os.getenv("STATE_FILE", ".position_player_alert_state.json"))
REQUEST_TIMEOUT = 20
USER_AGENT = "mlb-position-player-alert-bot/1.0"
HELP_TEXT = (
    "Команды:\n"
    "/live — live матчи MLB: команды, счёт, иннинг\n"
    "/recent — последние 5 выходов полевых игроков на горку\n"
    "/help — список команд"
)


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
    pruned_state = dict(state)
    pruned_state["alerts"] = fresh_alerts
    return pruned_state


def get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_live_game_pks() -> List[int]:
    today = datetime.now(timezone.utc).date()
    data = get_json(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "startDate": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            "endDate": (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        },
    )

    game_pks: List[int] = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            status = game.get("status", {})
            if status.get("abstractGameState") == "Live":
                game_pk = game.get("gamePk")
                if isinstance(game_pk, int):
                    game_pks.append(game_pk)
    return game_pks


def get_schedule_games(start_date: date, end_date: date) -> List[Dict[str, Any]]:
    data = get_json(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
        },
    )

    games: List[Dict[str, Any]] = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))
    return games


def safe_get(dct: Dict[str, Any], *keys: str) -> Any:
    current: Any = dct
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def schedule_team_name(game: Dict[str, Any], side: str) -> str:
    return safe_get(game, "teams", side, "team", "name") or side.title()

def schedule_team_score(game: Dict[str, Any], side: str) -> Optional[int]:
    return as_int(safe_get(game, "teams", side, "score"))

def game_score_diff(game: Dict[str, Any]) -> Optional[int]:
    away_score = schedule_team_score(game, "away")
    home_score = schedule_team_score(game, "home")
    if away_score is None or home_score is None:
        return None
    return abs(away_score - home_score)


def format_score(away_team: str, away_runs: Any, home_runs: Any, home_team: str) -> str:
    away_score = away_runs if isinstance(away_runs, int) else "?"
    home_score = home_runs if isinstance(home_runs, int) else "?"
    return f"{away_team} {away_score} – {home_score} {home_team}"


def format_inning(inning_half: Any, current_inning: Any) -> str:
    if not current_inning:
        return "иннинг не указан"

    half = str(inning_half or "").lower()
    if half == "top":
        return f"верх {current_inning}-го"
    if half == "bottom":
        return f"низ {current_inning}-го"
    return f"{current_inning}-й иннинг"

def is_late_enough(current_inning: Any) -> bool:
    if ALERT_ONLY_LATE_INNINGS and isinstance(current_inning, int) and current_inning < LATE_INNING_THRESHOLD:
        return False
    return True


def build_game_context(feed: Dict[str, Any]) -> Dict[str, Any]:
    game_data = feed.get("gameData", {})
    live_data = feed.get("liveData", {})
    linescore = live_data.get("linescore", {})
    current_inning = linescore.get("currentInning")
    is_top = bool(linescore.get("isTopInning"))
    home_team = safe_get(game_data, "teams", "home", "name") or "Home"
    away_team = safe_get(game_data, "teams", "away", "name") or "Away"
    home_runs = safe_get(linescore, "teams", "home", "runs")
    away_runs = safe_get(linescore, "teams", "away", "runs")

    score_diff: Optional[int] = None
    leading_team: Optional[str] = None
    trailing_team: Optional[str] = None
    if isinstance(home_runs, int) and isinstance(away_runs, int):
        score_diff = abs(home_runs - away_runs)
        if home_runs > away_runs:
            leading_team = home_team
            trailing_team = away_team
        elif away_runs > home_runs:
            leading_team = away_team
            trailing_team = home_team

    score_text = format_score(away_team, away_runs, home_runs, home_team)

    return {
        "game_data": game_data,
        "linescore": linescore,
        "current_inning": current_inning,
        "is_top": is_top,
        "inning_half": "Top" if is_top else "Bottom",
        "home_team": home_team,
        "away_team": away_team,
        "home_runs": home_runs,
        "away_runs": away_runs,
        "score_diff": score_diff,
        "score_text": score_text,
        "leading_team": leading_team,
        "trailing_team": trailing_team,
        "defensive_team": home_team if is_top else away_team,
        "batting_team": away_team if is_top else home_team,
        "detailed_state": safe_get(game_data, "status", "detailedState") or "Live",
        "game_pk": game_data.get("game", {}).get("pk") or feed.get("gamePk"),
    }


def build_blowout_warning(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not ENABLE_BLOWOUT_WARNING:
        return None

    context = build_game_context(feed)
    score_diff = context["score_diff"]
    current_inning = context["current_inning"]
    game_pk = context["game_pk"]

    if score_diff is None or score_diff < SCORE_DIFF_THRESHOLD:
        return None

    if not is_late_enough(current_inning):
        return None

    leader_text = context["leading_team"] or "Unknown"
    trailing_text = context["trailing_team"] or "Unknown"
    message = (
        "⚠️ Разгромный счёт: возможен выход полевого игрока питчером\n\n"
        f"Разница в счёте достигла {score_diff}.\n"
        f"Лидирует: {leader_text}\n"
        f"Проигрывает: {trailing_text}\n"
        f"Матч: {context['away_team']} at {context['home_team']}\n"
        f"Ситуация: {context['inning_half']} {current_inning}, {context['score_text']}\n"
        f"Защищается: {context['defensive_team']}\n"
        "Следующий алерт придёт, если питчера действительно заменит полевой игрок.\n"
        f"gamePk: {game_pk}"
    )

    return {
        "alert_type": "blowout_warning",
        "game_pk": game_pk,
        "message": message,
        "key": f"blowout_warning:{game_pk}",
    }


def build_position_player_alert(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    context = build_game_context(feed)
    game_data = context["game_data"]
    linescore = context["linescore"]
    defense = linescore.get("defense", {})
    pitcher = defense.get("pitcher", {})
    pitcher_id = pitcher.get("id")

    if not pitcher_id:
        return None

    player_key = f"ID{pitcher_id}"
    player_data = safe_get(game_data, "players", player_key) or {}
    primary_position = safe_get(player_data, "primaryPosition", "abbreviation")

    if not primary_position or str(primary_position).upper() == "P":
        return None

    current_inning = context["current_inning"]

    if not is_late_enough(current_inning):
        return None

    score_diff = context["score_diff"]

    if INCLUDE_BLOWOUT_ONLY and (score_diff is None or score_diff < SCORE_DIFF_THRESHOLD):
        return None

    pitcher_name = pitcher.get("fullName") or safe_get(player_data, "fullName") or f"Player {pitcher_id}"
    game_pk = context["game_pk"]

    score_diff_text = "Unknown"
    if isinstance(score_diff, int):
        score_diff_text = str(score_diff)

    message = (
        "🚨 Полевой игрок вышел питчером\n\n"
        f"{pitcher_name} сейчас питчит за {context['defensive_team']}.\n"
        f"Основная позиция: {primary_position}\n"
        f"Матч: {context['away_team']} at {context['home_team']}\n"
        f"Ситуация: {context['inning_half']} {current_inning}, {context['score_text']}\n"
        f"Разница в счёте: {score_diff_text}\n"
        f"Бьющая команда: {context['batting_team']}\n"
        f"Статус: {context['detailed_state']}\n"
        f"gamePk: {game_pk}"
    )

    key = f"position_player:{game_pk}:{pitcher_id}"
    return {
        "alert_type": "position_player_pitching",
        "game_pk": game_pk,
        "pitcher_id": pitcher_id,
        "message": message,
        "key": key,
        "dedupe_keys": [key, f"{game_pk}:{pitcher_id}"],
    }


def build_alerts(feed: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    blowout_warning = build_blowout_warning(feed)
    if blowout_warning:
        alerts.append(blowout_warning)

    position_player_alert = build_position_player_alert(feed)
    if position_player_alert:
        alerts.append(position_player_alert)

    return alerts


def build_live_games_message() -> str:
    live_game_pks = get_live_game_pks()
    if not live_game_pks:
        return "Live матчей пока нет"

    lines = ["Live матчи MLB:"]
    for game_pk in live_game_pks:
        feed = get_json(LIVE_FEED_URL.format(game_pk=game_pk))
        context = build_game_context(feed)
        inning_text = format_inning(context["inning_half"], context["current_inning"])
        status = context["detailed_state"]
        lines.append(f"{context['score_text']}\n{inning_text}; статус: {status}")

    return "\n\n".join(lines)


def is_position_player_pitcher(player_boxscore: Dict[str, Any]) -> bool:
    all_positions = player_boxscore.get("allPositions", [])
    for position in all_positions:
        abbreviation = str(position.get("abbreviation") or "").upper()
        if abbreviation and abbreviation != "P":
            return True

    position_abbreviation = str(safe_get(player_boxscore, "position", "abbreviation") or "").upper()
    return bool(position_abbreviation and position_abbreviation != "P")


def describe_pitching_outcome(
    pitcher_ids: List[Any],
    pitcher_index: int,
    players: Dict[str, Any],
    pitching_stats: Dict[str, Any],
) -> str:
    note = pitching_stats.get("note")
    if as_int(pitching_stats.get("gamesFinished")) == 1:
        outcome = "закончил матч"
    elif pitcher_index + 1 < len(pitcher_ids):
        next_pitcher_id = pitcher_ids[pitcher_index + 1]
        next_player = players.get(f"ID{next_pitcher_id}", {})
        next_name = safe_get(next_player, "person", "fullName") or f"Player {next_pitcher_id}"
        outcome = f"его заменил {next_name}"
    else:
        outcome = "после него больше никто не питчил"

    if note:
        outcome = f"{outcome} ({note})"
    return outcome


def extract_position_player_pitching_cases(game: Dict[str, Any], boxscore: Dict[str, Any]) -> List[Dict[str, Any]]:
    away_team = schedule_team_name(game, "away")
    home_team = schedule_team_name(game, "home")
    away_score = schedule_team_score(game, "away")
    home_score = schedule_team_score(game, "home")
    score_text = format_score(away_team, away_score, home_score, home_team)
    official_date = game.get("officialDate") or str(game.get("gameDate", ""))[:10]

    cases: List[Dict[str, Any]] = []
    for side in ("away", "home"):
        team_boxscore = safe_get(boxscore, "teams", side) or {}
        players = team_boxscore.get("players", {})
        pitcher_ids = team_boxscore.get("pitchers", [])
        team_name = safe_get(team_boxscore, "team", "name") or schedule_team_name(game, side)
        opponent_name = home_team if side == "away" else away_team

        for pitcher_index, pitcher_id in enumerate(pitcher_ids):
            player = players.get(f"ID{pitcher_id}", {})
            pitching_stats = safe_get(player, "stats", "pitching") or {}
            if not pitching_stats or not is_position_player_pitcher(player):
                continue

            innings_pitched = pitching_stats.get("inningsPitched") or "?"
            pitches_thrown = pitching_stats.get("pitchesThrown") or pitching_stats.get("numberOfPitches")
            pitches_text = f", {pitches_thrown} питчей" if pitches_thrown is not None else ""
            hits = pitching_stats.get("hits", "?")
            runs = pitching_stats.get("runs", "?")
            earned_runs = pitching_stats.get("earnedRuns", "?")
            outcome = describe_pitching_outcome(pitcher_ids, pitcher_index, players, pitching_stats)
            player_name = safe_get(player, "person", "fullName") or f"Player {pitcher_id}"
            all_positions = [
                str(position.get("abbreviation"))
                for position in player.get("allPositions", [])
                if position.get("abbreviation")
            ]
            position_text = "/".join(all_positions) or safe_get(player, "position", "abbreviation") or "non-P"

            cases.append(
                {
                    "game_date": official_date,
                    "game_date_time": game.get("gameDate") or official_date,
                    "game_pk": game.get("gamePk"),
                    "pitcher_index": pitcher_index,
                    "player_name": player_name,
                    "team_name": team_name,
                    "opponent_name": opponent_name,
                    "positions": position_text,
                    "score_text": score_text,
                    "innings_pitched": innings_pitched,
                    "pitches_text": pitches_text,
                    "hits": hits,
                    "runs": runs,
                    "earned_runs": earned_runs,
                    "outcome": outcome,
                }
            )

    cases.sort(key=lambda item: (item["game_date_time"], item["pitcher_index"]), reverse=True)
    return cases


def find_recent_position_player_pitching_cases(
    limit: int = 5,
    lookback_days: int = RECENT_CASE_LOOKBACK_DAYS,
    min_score_diff: int = RECENT_CASE_MIN_SCORE_DIFF,
) -> List[Dict[str, Any]]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=lookback_days)
    games = get_schedule_games(start_date, end_date)
    games.sort(key=lambda item: item.get("gameDate", ""), reverse=True)

    cases: List[Dict[str, Any]] = []
    for game in games:
        status = game.get("status", {})
        if status.get("abstractGameState") != "Final":
            continue

        score_diff = game_score_diff(game)
        if min_score_diff > 0 and (score_diff is None or score_diff < min_score_diff):
            continue

        try:
            boxscore = get_json(BOXSCORE_URL.format(game_pk=game["gamePk"]))
        except Exception as exc:
            print(f"Skipped boxscore for gamePk {game.get('gamePk')}: {exc}")
            continue

        cases.extend(extract_position_player_pitching_cases(game, boxscore))
        cases.sort(key=lambda item: (item["game_date_time"], item["pitcher_index"]), reverse=True)
        if len(cases) >= limit:
            return cases[:limit]

    return cases[:limit]


def get_cached_recent_cases(state: Dict[str, Any], limit: int, lookback_days: int, min_score_diff: int) -> Optional[List[Dict[str, Any]]]:
    cache = safe_get(state, "command_cache", "recent_position_player_cases")
    if not isinstance(cache, dict):
        return None

    generated_at = cache.get("generated_at")
    now = datetime.now(timezone.utc).timestamp()
    if not isinstance(generated_at, (int, float)) or now - generated_at > COMMAND_CACHE_TTL_SECONDS:
        return None

    if cache.get("limit") != limit or cache.get("lookback_days") != lookback_days or cache.get("min_score_diff") != min_score_diff:
        return None

    cases = cache.get("cases")
    if not isinstance(cases, list):
        return None
    return cases[:limit]


def cache_recent_cases(
    state: Dict[str, Any],
    cases: List[Dict[str, Any]],
    limit: int,
    lookback_days: int,
    min_score_diff: int,
) -> None:
    command_cache = state.setdefault("command_cache", {})
    command_cache["recent_position_player_cases"] = {
        "generated_at": datetime.now(timezone.utc).timestamp(),
        "limit": limit,
        "lookback_days": lookback_days,
        "min_score_diff": min_score_diff,
        "cases": cases,
    }


def format_recent_position_player_cases_message(limit: int = 5) -> str:
    state = load_state()
    cases = get_cached_recent_cases(state, limit, RECENT_CASE_LOOKBACK_DAYS, RECENT_CASE_MIN_SCORE_DIFF)
    if cases is None:
        cases = find_recent_position_player_pitching_cases(
            limit=limit,
            lookback_days=RECENT_CASE_LOOKBACK_DAYS,
            min_score_diff=RECENT_CASE_MIN_SCORE_DIFF,
        )
        cache_recent_cases(state, cases, limit, RECENT_CASE_LOOKBACK_DAYS, RECENT_CASE_MIN_SCORE_DIFF)
        save_state(state)

    if not cases:
        return f"За последние {RECENT_CASE_LOOKBACK_DAYS} дней случаев выхода полевого игрока на горку не нашёл."

    lines = ["Последние выходы полевых игроков на горку:"]
    for index, case in enumerate(cases[:limit], start=1):
        lines.append(
            (
                f"{index}. {case['game_date']}: {case['player_name']} ({case['positions']}), "
                f"{case['team_name']} vs {case['opponent_name']}\n"
                f"Матч: {case['score_text']}\n"
                f"На горке: {case['innings_pitched']} IP{case['pitches_text']}\n"
                f"Итог выхода: {case['outcome']}\n"
                f"Пропустил: {case['hits']} H, {case['runs']} R ({case['earned_runs']} ER)"
            )
        )

    return "\n\n".join(lines)


def normalize_command(text: str) -> str:
    first_token = text.strip().split()[0] if text.strip() else ""
    return first_token.split("@", 1)[0].lower()


def build_telegram_command_response(text: str) -> str:
    command = normalize_command(text)
    lowered = text.strip().lower()

    if command in {"/start", "/help"}:
        return HELP_TEXT
    if command in {"/live", "/games", "/matches", "/матчи", "/игры", "/сейчас"}:
        return build_live_games_message()
    if command in {"/recent", "/last5", "/cases", "/полевые", "/последние"}:
        return format_recent_position_player_cases_message(limit=5)

    if not command.startswith("/"):
        if "матч" in lowered or "live" in lowered or "лайв" in lowered:
            return build_live_games_message()
        if "полев" in lowered or "послед" in lowered:
            return format_recent_position_player_cases_message(limit=5)

    return f"Не знаю такую команду.\n\n{HELP_TEXT}"


def handle_telegram_update(update: Dict[str, Any]) -> Dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    chat_id = safe_get(message, "chat", "id")

    if not text or chat_id is None:
        return {"handled": False, "reason": "no text message"}

    response_text = build_telegram_command_response(text)
    send_telegram_message(response_text, chat_id=chat_id)
    return {"handled": True, "chat_id": chat_id, "command": normalize_command(text)}


def send_telegram_message(text: str, chat_id: Optional[Any] = None) -> None:
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        raise BotError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    response = session.post(
        TELEGRAM_URL.format(token=TELEGRAM_BOT_TOKEN),
        json={
            "chat_id": target_chat_id,
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
        for alert in build_alerts(feed):
            key = alert["key"]
            dedupe_keys = alert.get("dedupe_keys", [key])
            if any(dedupe_key in alerts for dedupe_key in dedupe_keys):
                continue

            send_telegram_message(alert["message"])
            alerts[key] = datetime.now(timezone.utc).timestamp()
            sent_count += 1
            print(f"Sent {alert.get('alert_type', 'alert')} for {key}")

    save_state(state)
    print(f"Done. Alerts sent: {sent_count}")
    return sent_count


if __name__ == "__main__":
    run()
