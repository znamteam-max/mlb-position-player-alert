import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bot

FINAL_GAME_RECHECK_LOOKBACK_HOURS = int(os.getenv("FINAL_GAME_RECHECK_LOOKBACK_HOURS", "48"))


def parse_game_datetime(game: Dict[str, Any]) -> Optional[datetime]:
    raw_value = game.get("gameDate")
    if not raw_value:
        return None

    try:
        return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_recent_final_game(game: Dict[str, Any], now: datetime) -> bool:
    status = game.get("status", {})
    if status.get("abstractGameState") != "Final":
        return False

    game_datetime = parse_game_datetime(game)
    if game_datetime is None:
        return True

    cutoff = now - timedelta(hours=FINAL_GAME_RECHECK_LOOKBACK_HOURS)
    return game_datetime >= cutoff


def get_recent_final_games() -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(hours=FINAL_GAME_RECHECK_LOOKBACK_HOURS + 12)).date()
    end_date = now.date() + timedelta(days=1)
    games = bot.get_schedule_games(start_date, end_date)
    return [game for game in games if is_recent_final_game(game, now)]


def alert_key_for_case(case: Dict[str, Any]) -> str:
    return f"position_player:{case['game_pk']}:{case['player_id']}"


def legacy_alert_key_for_case(case: Dict[str, Any]) -> str:
    return f"{case['game_pk']}:{case['player_id']}"


def already_sent_case(case: Dict[str, Any], alerts: Dict[str, Any]) -> bool:
    return alert_key_for_case(case) in alerts or legacy_alert_key_for_case(case) in alerts


def get_player_pitching_game_log(player_id: Any) -> List[Dict[str, Any]]:
    season_year = bot.current_mlb_season_start().year
    data = bot.get_json(
        bot.PLAYER_STATS_URL.format(player_id=player_id),
        params={"stats": "gameLog", "group": "pitching", "season": season_year},
    )

    splits: List[Dict[str, Any]] = []
    for stat_block in data.get("stats", []):
        splits.extend(stat_block.get("splits", []))
    return splits


def pitching_line_from_stat(stat: Dict[str, Any]) -> str:
    return (
        f"{stat.get('inningsPitched', '?')} IP, "
        f"{stat.get('hits', '?')} H, "
        f"{stat.get('baseOnBalls', '?')} BB, "
        f"{stat.get('earnedRuns', '?')} ER"
    )


def format_previous_pitching_history(player_id: Any, current_game_pk: Any) -> str:
    try:
        game_log = get_player_pitching_game_log(player_id)
    except Exception as exc:
        print(f"Could not load player pitching game log for {player_id}: {exc}")
        return "Не удалось проверить прошлые выходы в этом сезоне."

    previous_games = []
    for split in game_log:
        game_pk = bot.safe_get(split, "game", "gamePk")
        if str(game_pk) == str(current_game_pk):
            continue
        previous_games.append(split)

    if not previous_games:
        return "До этого в этом сезоне питчером не выходил."

    previous_games.sort(key=lambda item: item.get("date", ""), reverse=True)
    latest = previous_games[0]
    opponent = bot.safe_get(latest, "opponent", "name") or "opponent"
    stat = latest.get("stat", {})
    return (
        f"До этого в этом сезоне: {len(previous_games)} раз(а). "
        f"Последний: {latest.get('date', '?')} vs {opponent}, "
        f"{pitching_line_from_stat(stat)}."
    )


def format_catch_up_confirmed_message(game: Dict[str, Any], case: Dict[str, Any]) -> str:
    score_diff = bot.game_score_diff(game)
    score_diff_text = str(score_diff) if score_diff is not None else "Unknown"
    history_text = format_previous_pitching_history(case.get("player_id"), case.get("game_pk"))
    pitching_line = (
        f"{case['innings_pitched']} IP, "
        f"{case['hits']} H, "
        f"{case.get('walks', '?')} BB, "
        f"{case['earned_runs']} ER"
    )

    return (
        "!!! ALERT !!!\n\n"
        "Полевой игрок вышел питчером\n\n"
        "Догоняющая проверка после финала: live-проверка могла не успеть поймать замену.\n\n"
        f"Игрок: {case['player_name']}\n"
        f"Команда: {case['team_name']}\n"
        f"Основная позиция: {case['positions']}\n"
        f"Питчил против: {case['opponent_name']}\n\n"
        f"Счёт: {case['score_text']}\n"
        f"Разница: {score_diff_text}\n"
        f"Линия выхода: {pitching_line}\n"
        f"Итог выхода: {case['outcome']}\n\n"
        "Опыт на горке до этого:\n"
        f"{history_text}\n\n"
        f"gamePk: {case['game_pk']}"
    )


def send_catch_up_confirmed_alert(game: Dict[str, Any], case: Dict[str, Any]) -> int:
    alert = {
        "message": format_catch_up_confirmed_message(game, case),
        "repeat_count": max(1, bot.CONFIRMED_ALERT_REPEAT_COUNT),
        "repeat_delay_seconds": max(0.0, bot.CONFIRMED_ALERT_REPEAT_DELAY_SECONDS),
    }
    return bot.send_alert_messages(alert)


def send_recent_final_confirmed_alerts(state: Dict[str, Any]) -> int:
    alerts = state.setdefault("alerts", {})
    messages_sent = 0

    for game in get_recent_final_games():
        game_pk = game.get("gamePk")
        if f"blowout_warning:{game_pk}" not in alerts:
            continue

        try:
            boxscore = bot.get_json(bot.BOXSCORE_URL.format(game_pk=game_pk))
        except Exception as exc:
            print(f"Skipped final-game boxscore recheck for gamePk {game_pk}: {exc}")
            continue

        for case in bot.extract_position_player_pitching_cases(game, boxscore):
            if already_sent_case(case, alerts):
                continue

            sent_for_case = send_catch_up_confirmed_alert(game, case)
            alerts[alert_key_for_case(case)] = datetime.now(timezone.utc).timestamp()
            messages_sent += sent_for_case
            print(
                f"Sent final-game catch-up alert for {alert_key_for_case(case)} "
                f"({sent_for_case} message(s))"
            )

    return messages_sent


def run() -> int:
    live_messages_sent = bot.run()

    state = bot.prune_state(bot.load_state())
    catch_up_messages_sent = send_recent_final_confirmed_alerts(state)
    bot.save_state(state)

    total_messages_sent = live_messages_sent + catch_up_messages_sent
    print(
        "Scheduled run done. "
        f"Live messages: {live_messages_sent}; "
        f"final-game catch-up messages: {catch_up_messages_sent}; "
        f"total: {total_messages_sent}"
    )
    return total_messages_sent


if __name__ == "__main__":
    run()
