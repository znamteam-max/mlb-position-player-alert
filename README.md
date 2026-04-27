# MLB position-player pitching alert bot

This bot checks live MLB games and sends a Telegram alert when the current pitcher is **not** a primary-position pitcher.

It uses MLB's public live game feed and schedule endpoints to:
- find live MLB games
- identify the current defensive pitcher
- compare that player's **primary position** against `P`
- send a Telegram message when a position player is on the mound

## Files

- `bot.py` — polling logic and Telegram alerts
- `app.py` — small Flask entrypoint for Vercel deployments
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

Add these environment variables in Vercel:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `CRON_SECRET` — a random string of at least 16 characters

The `/api/cron` endpoint requires this header:

```bash
Authorization: Bearer $CRON_SECRET
```

Manual test example:

```bash
curl -H "Authorization: Bearer $CRON_SECRET" https://your-project.vercel.app/api/cron
```

Vercel Hobby plans only allow built-in cron jobs to run once per day. This bot is designed for frequent polling, so keep GitHub Actions enabled for the default 5-minute schedule unless you use Vercel Pro or an external scheduler that can call `/api/cron` with the authorization header.

## Default behavior

The workflow runs every 5 minutes and is currently configured to:
- alert only from the 7th inning on
- not require a blowout score

You can change these values in the workflow env block:

- `ALERT_ONLY_LATE_INNINGS`
- `LATE_INNING_THRESHOLD`
- `INCLUDE_BLOWOUT_ONLY`
- `SCORE_DIFF_THRESHOLD`

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
