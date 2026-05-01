# MLB position-player pitching alert bot

This bot checks MLB games and sends Telegram alerts in two steps:

1. A blowout warning when the score differential reaches the configured threshold.
2. A confirmed `!!! ALERT !!!` message when a position player pitches.

The scheduled runner now also performs a catch-up pass after the live check: if a blowout warning was sent but the game reached `Final` before the next poll, it rechecks the final boxscore and sends the confirmed alert from the boxscore.

## Files

- `bot.py` — live polling logic, message formatting, Telegram sending, MLB helpers
- `scheduled_run.py` — scheduled entrypoint; runs live polling plus final-game catch-up
- `app.py` — Flask entrypoint for Vercel deployments
- `command_extensions.py` — extra Telegram commands for historical blowout/watch checks
- `requirements.txt` — Python dependency list
- `.env.example` — local environment variables
- `.github/workflows/position_player_alert.yml` — GitHub Actions scheduler

## GitHub Setup

Add these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The workflow runs every 5 minutes and executes:

```bash
python scheduled_run.py
```

## Vercel Setup

The repository includes `app.py` so Vercel can detect a Flask app entrypoint.

Routes:

- `/` — healthcheck
- `/api/cron` — runs `scheduled_run.py` logic
- `/api/telegram` — handles Telegram commands

Add these Vercel environment variables:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `CRON_SECRET`
- `TELEGRAM_WEBHOOK_SECRET`

Manual cron test:

```bash
curl -H "Authorization: Bearer $CRON_SECRET" https://your-project.vercel.app/api/cron
```

Telegram webhook setup:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://your-project.vercel.app/api/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

## Settings

Common environment variables:

- `ALERT_ONLY_LATE_INNINGS=true`
- `LATE_INNING_THRESHOLD=7`
- `SCORE_DIFF_THRESHOLD=6`
- `ENABLE_BLOWOUT_WARNING=true`
- `INCLUDE_BLOWOUT_ONLY=true`
- `CONFIRMED_ALERT_REPEAT_COUNT=3`
- `CONFIRMED_ALERT_REPEAT_DELAY_SECONDS=3`
- `FINAL_GAME_RECHECK_LOOKBACK_HOURS=48`
- `RECENT_CASE_LOOKBACK_DAYS=90`
- `RECENT_CASE_MIN_SCORE_DIFF=6`
- `COMMAND_CACHE_TTL_SECONDS=1800`
- `SEASON_CASE_CACHE_TTL_SECONDS=21600`
- `STATE_FILE=.position_player_alert_state.json`

`FINAL_GAME_RECHECK_LOOKBACK_HOURS` controls how far back the runner scans final games for missed confirmations. It only sends a catch-up confirmation when the state already contains `blowout_warning:{gamePk}`, so it should not alert on random old games.

## Alert Behavior

Blowout warning includes:

- score differential
- current situation and score
- current-season position-player pitching history for both teams
- defensive team
- `gamePk`

Confirmed alert includes:

- `!!! ALERT !!!`
- player name
- team
- primary position
- opponent
- score and score differential
- pitching line
- whether the player had pitched earlier this season
- `gamePk`

Confirmed alerts are sent 3 times, 3 seconds apart by default.

## Telegram Commands

- `/live` — live MLB games with score and inning
- `/recent` — last 5 detected position-player pitching appearances
- `/blowouts` — last 5 completed blowout watch games
- `/help` — command list

## Local Run

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python scheduled_run.py
```

## Notes

- GitHub Actions cron is good for lightweight polling, but it is not truly instant.
- The state file is cached between workflow runs to avoid duplicate alerts.
- The final-game catch-up exists specifically for cases where the position-player appearance happens and the game finalizes between two 5-minute polls.
- Vercel's filesystem is ephemeral, so GitHub Actions remains the better primary scheduler for dedupe state.
