# MLB position-player pitching alert bot

This bot checks live MLB games and sends Telegram alerts in two steps:

1. A blowout warning when the score differential reaches the configured threshold and a position-player pitching appearance becomes more likely.
2. A follow-up alert when the current pitcher is **not** a primary-position pitcher.

It uses MLB's public live game feed and schedule endpoints to:
- find live MLB games
- detect blowout score situations
- identify the current defensive pitcher
- compare that player's **primary position** against `P`
- send Telegram messages in the warning-then-confirmation order

## Files

- `bot.py` — polling logic and Telegram alerts
- `app.py` — small Flask entrypoint for Vercel deployments
- `command_extensions.py` — extra Telegram commands for historical blowout/watch checks
- `requirements.txt` — Python dependency list
- `.env.example` — local environment variables
- `.github/workflows/position_player_alert.yml` — GitHub Actions scheduler

## What you need

1. A Telegram bot token
2. Your Telegram chat ID
3. GitHub repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

## GitHub setup

Add these repository secrets in GitHub:

- `Settings` → `Secrets and variables` → `Actions`
- create `TELEGRAM_BOT_TOKEN`
- create `TELEGRAM_CHAT_ID`

Then enable GitHub Actions for the repository.

## Vercel setup

The repository includes `app.py` so Vercel can detect a Flask app entrypoint. After deployment:

- `/` returns a healthcheck response
- `/api/cron` runs the alert bot
- `/api/telegram` handles Telegram commands

Add these environment variables in Vercel:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `CRON_SECRET` — a random string of at least 16 characters
- `TELEGRAM_WEBHOOK_SECRET` — a separate random string for Telegram webhook validation

The `/api/cron` endpoint requires this header:

```bash
Authorization: Bearer $CRON_SECRET
```

Manual test example:

```bash
curl -H "Authorization: Bearer $CRON_SECRET" https://your-project.vercel.app/api/cron
```

Vercel Hobby plans only allow built-in cron jobs to run once per day. This bot is designed for frequent polling, so keep GitHub Actions enabled for the default 5-minute schedule unless you use Vercel Pro or an external scheduler that can call `/api/cron` with the authorization header.

Telegram command setup:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://your-project.vercel.app/api/telegram" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

Supported commands:

- `/live` — currently live MLB games with teams, score, and inning. If there are no live games, the bot replies `Live матчей пока нет`.
- `/recent` — last 5 detected position-player pitching appearances, including innings pitched, how the outing ended, hits, runs, and earned runs allowed.
- `/blowouts` — last 5 completed blowout games where the score differential crossed the watch threshold and a position-player pitching appearance became more likely. Aliases: `/watch`, `/разгромы`, `/вероятность`.
- `/help` — command list.

`/recent` and `/blowouts` scan recently completed MLB games and cache the answer briefly. Tune these values if needed:

- `RECENT_CASE_LOOKBACK_DAYS`
- `RECENT_CASE_MIN_SCORE_DIFF`
- `COMMAND_CACHE_TTL_SECONDS`

## Default behavior

The workflow runs every 5 minutes and is currently configured to:
- alert only from the 7th inning on
- send a blowout warning once the score differential reaches 6 runs
- require a blowout score before sending the position-player pitching confirmation

You can change these values in the workflow env block:

- `ALERT_ONLY_LATE_INNINGS`
- `LATE_INNING_THRESHOLD`
- `SCORE_DIFF_THRESHOLD`
- `ENABLE_BLOWOUT_WARNING`
- `INCLUDE_BLOWOUT_ONLY`
- `RECENT_CASE_LOOKBACK_DAYS`
- `RECENT_CASE_MIN_SCORE_DIFF`
- `COMMAND_CACHE_TTL_SECONDS`

The state file stores separate dedupe keys for the blowout warning and the confirmed position-player pitching alert. If both conditions are first detected during the same run, the warning is sent first and the confirmation is sent immediately after it.

## Local run

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python bot.py
```

## Notes

- GitHub Actions cron is good for lightweight polling, but it is not truly instant.
- The state file is cached between workflow runs to avoid duplicate alerts for the same game and pitcher.
- Vercel's filesystem is ephemeral, so `/api/cron` uses `/tmp` for temporary state and should not be relied on as the primary duplicate-alert store.
- MLB live-feed field names can shift over time. If the bot stops detecting the current pitcher correctly, the first thing to check is the `liveData.linescore.defense.pitcher` path and the player metadata path in `gameData.players`.

## Good Codex follow-up task

```text
Turn this starter into a production-ready MLB position-player pitching alert bot.

Tasks:
1. Add Slack and Discord notification options alongside Telegram.
2. Improve detection by verifying the current pitcher from both linescore and play-by-play.
3. Add richer alert text with score differential, outs, base state, and leverage context.
4. Add tests with saved sample MLB live-feed payloads.
5. Add logging and retry handling.
6. Make the bot configurable for late innings only, blowouts only, or all position-player pitching appearances.
7. Keep the existing simple GitHub Actions workflow, but also add a Railway or Render deployment option for near-real-time polling.
```
