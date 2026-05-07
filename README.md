# MLB position-player pitching alert bot

This bot checks MLB games and sends Telegram alerts in three separate stages:

1. A blowout warning when the score differential reaches the configured threshold.
2. An immediate `!!! ALERT !!!` when a position player is detected as the current pitcher.
3. A separate outing summary after the appearance is complete and the final boxscore is available.

The GitHub Actions runner polls live games repeatedly during each scheduled run. This avoids waiting a full 5 minutes between checks and makes the confirmed alert arrive much closer to the actual pitching change.

## Files

- `bot.py` — live polling logic, message formatting, Telegram sending, MLB helpers
- `scheduled_run.py` — scheduled entrypoint; runs frequent live polling plus final-game outing summaries
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

In GitHub Actions it is configured to poll live games for 240 seconds, every 15 seconds.

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

For Vercel serverless cron, keep `LIVE_POLL_WINDOW_SECONDS=0` unless your plan allows long-running functions. GitHub Actions is the better scheduler for the frequent live polling loop.

## Settings

Common environment variables:

- `ALERT_ONLY_LATE_INNINGS=true`
- `LATE_INNING_THRESHOLD=7`
- `SCORE_DIFF_THRESHOLD=6`
- `ENABLE_BLOWOUT_WARNING=true`
- `INCLUDE_BLOWOUT_ONLY=true`
- `CONFIRMED_ALERT_REPEAT_COUNT=3`
- `CONFIRMED_ALERT_REPEAT_DELAY_SECONDS=3`
- `LIVE_POLL_WINDOW_SECONDS=240`
- `LIVE_POLL_INTERVAL_SECONDS=15`
- `FINAL_GAME_RECHECK_LOOKBACK_HOURS=48`
- `RECENT_CASE_LOOKBACK_DAYS=90`
- `RECENT_CASE_MIN_SCORE_DIFF=6`
- `COMMAND_CACHE_TTL_SECONDS=1800`
- `SEASON_CASE_CACHE_TTL_SECONDS=21600`
- `STATE_FILE=.position_player_alert_state.json`

`LIVE_POLL_WINDOW_SECONDS` controls how long one scheduled run keeps polling live games. `LIVE_POLL_INTERVAL_SECONDS` controls the delay between live checks.

`FINAL_GAME_RECHECK_LOOKBACK_HOURS` controls how far back the runner scans final games for missed confirmations and outing summaries.

## Alert Behavior

Blowout warning includes:

- score differential
- current situation and score
- current-season position-player pitching history for both teams
- defensive team
- `gamePk`

Immediate confirmed alert includes:

- `!!! ALERT !!!`
- player name
- team
- primary position
- opponent
- score and score differential
- whether the player had pitched earlier this season
- `gamePk`

It does not include the pitching line or outcome, because those are not known at the moment of the pitching change.

Outing summary includes:

- player name
- team and opponent
- final score and score differential
- pitching line
- runs, earned runs, hits, and walks allowed
- how the stint ended
- `gamePk`

Confirmed alerts are sent 3 times, 3 seconds apart by default. Outing summaries are sent once.

## Local Run

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python scheduled_run.py
```

## Telegram Commands

- `/live` — live MLB games with score and inning
- `/recent` — last 5 detected position-player pitching appearances
- `/blowouts` — last 5 completed blowout watch games
- `/help` — command list

## Notes

- GitHub Actions cron is not truly instant, but the internal 15-second polling loop closes most of the gap.
- The state file is cached between workflow runs to avoid duplicate alerts.
- If the live alert was missed and a blowout warning was already sent, final-game catch-up sends an immediate-style `!!! ALERT !!!` first, then a separate outing summary.
- Vercel's filesystem is ephemeral, so GitHub Actions remains the better primary scheduler for dedupe state.
