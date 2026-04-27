from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bot import (
    BOXSCORE_URL,
    COMMAND_CACHE_TTL_SECONDS,
    HELP_TEXT,
    RECENT_CASE_LOOKBACK_DAYS,
    RECENT_CASE_MIN_SCORE_DIFF,
    build_telegram_command_response,
    extract_position_player_pitching_cases,
    format_score,
    game_score_diff,
    get_json,
    get_schedule_games,
    load_state,
    normalize_command,
    safe_get,
    save_state,
    schedule_team_name,
    schedule_team_score,
    send_telegram_message,
)

BLOWOUT_HELP_LINE = "/blowouts — последние 5 разгромов с watch-сигналом на полевого питчера"
BLOWOUT_COMMANDS = {"/blowouts", "/blowout", "/watch", "/разгромы", "/разгром", "/вероятность"}


def find_recent_blowout_watch_games(
    limit: int = 5,
    lookback_days: int = RECENT_CASE_LOOKBACK_DAYS,
    min_score_diff: int = RECENT_CASE_MIN_SCORE_DIFF,
) -> List[Dict[str, Any]]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=lookback_days)
    games = get_schedule_games(start_date, end_date)
    games.sort(key=lambda item: item.get("gameDate", ""), reverse=True)

    watch_games: List[Dict[str, Any]] = []
    for game in games:
        status = game.get("status", {})
        if status.get("abstractGameState") != "Final":
            continue

        score_diff = game_score_diff(game)
        if score_diff is None or score_diff < min_score_diff:
            continue

        away_team = schedule_team_name(game, "away")
        home_team = schedule_team_name(game, "home")
        away_score = schedule_team_score(game, "away")
        home_score = schedule_team_score(game, "home")
        leading_team = "Unknown"
        trailing_team = "Unknown"
        if isinstance(away_score, int) and isinstance(home_score, int):
            if away_score > home_score:
                leading_team = away_team
                trailing_team = home_team
            elif home_score > away_score:
                leading_team = home_team
                trailing_team = away_team

        position_player_cases: List[Dict[str, Any]] = []
        try:
            boxscore = get_json(BOXSCORE_URL.format(game_pk=game["gamePk"]))
            position_player_cases = extract_position_player_pitching_cases(game, boxscore)
        except Exception as exc:
            print(f"Skipped boxscore for gamePk {game.get('gamePk')}: {exc}")

        watch_games.append(
            {
                "game_date": game.get("officialDate") or str(game.get("gameDate", ""))[:10],
                "game_date_time": game.get("gameDate") or "",
                "game_pk": game.get("gamePk"),
                "score_text": format_score(away_team, away_score, home_score, home_team),
                "score_diff": score_diff,
                "leading_team": leading_team,
                "trailing_team": trailing_team,
                "position_player_cases": position_player_cases,
            }
        )

        if len(watch_games) >= limit:
            return watch_games[:limit]

    return watch_games[:limit]


def get_cached_blowout_watch_games(
    state: Dict[str, Any],
    limit: int,
    lookback_days: int,
    min_score_diff: int,
) -> Optional[List[Dict[str, Any]]]:
    cache = safe_get(state, "command_cache", "recent_blowout_watch_games")
    if not isinstance(cache, dict):
        return None

    generated_at = cache.get("generated_at")
    now = datetime.now(timezone.utc).timestamp()
    if not isinstance(generated_at, (int, float)) or now - generated_at > COMMAND_CACHE_TTL_SECONDS:
        return None

    if cache.get("limit") != limit or cache.get("lookback_days") != lookback_days or cache.get("min_score_diff") != min_score_diff:
        return None

    games = cache.get("games")
    if not isinstance(games, list):
        return None
    return games[:limit]


def cache_blowout_watch_games(
    state: Dict[str, Any],
    games: List[Dict[str, Any]],
    limit: int,
    lookback_days: int,
    min_score_diff: int,
) -> None:
    command_cache = state.setdefault("command_cache", {})
    command_cache["recent_blowout_watch_games"] = {
        "generated_at": datetime.now(timezone.utc).timestamp(),
        "limit": limit,
        "lookback_days": lookback_days,
        "min_score_diff": min_score_diff,
        "games": games,
    }


def format_position_player_case_summary(position_player_cases: List[Dict[str, Any]]) -> str:
    if not position_player_cases:
        return "Полевой питчер: не зафиксирован в boxscore"

    summaries = []
    for case in position_player_cases:
        summaries.append(
            f"{case['player_name']} ({case['positions']}), {case['team_name']}: "
            f"{case['innings_pitched']} IP{case['pitches_text']}"
        )
    return "Полевой питчер: " + "; ".join(summaries)


def format_blowout_watch_games_message(limit: int = 5) -> str:
    state = load_state()
    games = get_cached_blowout_watch_games(state, limit, RECENT_CASE_LOOKBACK_DAYS, RECENT_CASE_MIN_SCORE_DIFF)
    if games is None:
        games = find_recent_blowout_watch_games(
            limit=limit,
            lookback_days=RECENT_CASE_LOOKBACK_DAYS,
            min_score_diff=RECENT_CASE_MIN_SCORE_DIFF,
        )
        cache_blowout_watch_games(state, games, limit, RECENT_CASE_LOOKBACK_DAYS, RECENT_CASE_MIN_SCORE_DIFF)
        save_state(state)

    if not games:
        return (
            f"За последние {RECENT_CASE_LOOKBACK_DAYS} дней разгромов с разницей "
            f"{RECENT_CASE_MIN_SCORE_DIFF}+ не нашёл."
        )

    lines = ["Последние разгромы с watch-сигналом на полевого питчера:"]
    for index, game in enumerate(games[:limit], start=1):
        position_player_text = format_position_player_case_summary(game.get("position_player_cases", []))
        lines.append(
            (
                f"{index}. {game['game_date']}: {game['score_text']}\n"
                f"Разница: {game['score_diff']}; лидировал: {game['leading_team']}; "
                f"проигрывал: {game['trailing_team']}\n"
                f"Watch-сигнал: разгром {game['score_diff']} >= {RECENT_CASE_MIN_SCORE_DIFF}, "
                "значит вероятность выхода полевого питчера была повышенной\n"
                f"{position_player_text}\n"
                f"gamePk: {game['game_pk']}"
            )
        )

    return "\n\n".join(lines)


def extend_help_text(response: str) -> str:
    if BLOWOUT_HELP_LINE in response:
        return response
    if response == HELP_TEXT or response.endswith(HELP_TEXT):
        return f"{response}\n{BLOWOUT_HELP_LINE}"
    return response


def build_extended_telegram_command_response(text: str) -> str:
    command = normalize_command(text)
    lowered = text.strip().lower()

    if command in {"/start", "/help"}:
        return extend_help_text(build_telegram_command_response(text))
    if command in BLOWOUT_COMMANDS:
        return format_blowout_watch_games_message(limit=5)

    if not command.startswith("/"):
        if "разгром" in lowered or "вероят" in lowered or "watch" in lowered:
            return format_blowout_watch_games_message(limit=5)

    return extend_help_text(build_telegram_command_response(text))


def handle_telegram_update(update: Dict[str, Any]) -> Dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    chat_id = safe_get(message, "chat", "id")

    if not text or chat_id is None:
        return {"handled": False, "reason": "no text message"}

    response_text = build_extended_telegram_command_response(text)
    send_telegram_message(response_text, chat_id=chat_id)
    return {"handled": True, "chat_id": chat_id, "command": normalize_command(text)}
