# FACEIT autor eg for Railway

## Railway variables

Set these in **Variables**:

- `DISCORD_USER_TOKEN` — Discord account token.
- `GEMINI_API_KEYS` — comma-separated Gemini-compatible API keys. Consecutive games rotate across keys; keys are never printed in logs. A single `GEMINI_API_KEY` is also supported.
- `GEMINI_BASE_URL` — Gemini-compatible provider root. For AI STAR use `https://ai.starimg.ru/gemini`; for official Google omit it.
- `NORMAL_CHANNEL_IDS` — comma-separated IDs of ordinary-game channels.
- `PRIORITY_CHANNEL_IDS` — comma-separated IDs of priority-game channels.
- `LOG_CHANNEL_ID` — ID of the channel that receives every successful registration, including match number, score, source, and full command.
- `MY_ACCOUNT_ID` — your Discord account ID; recommended so only you can use `старт` and `енд`.
- `MIN_CONFIDENCE` — default `0.82`.
- `BACKFILL_LIMIT` — how many previous messages to inspect in each selected channel; default `500`.
- `SEND_DELAY` — delay before sending each result; default `0.25` seconds.
- `DELETE_AFTER_REGISTRATION` — delete the temporary registration command after it is sent; default `true`.
- `DELETE_DELAY` — seconds to wait before deleting the registration command; default `3.0`.
- `GEMINI_MODEL` — legacy primary model setting.
- `GEMINI_FALLBACK_MODEL` — legacy fallback model setting.
- `GEMINI_MODELS` — comma-separated model pool. For AI STAR use available names such as `gemini-3.8-flash,gemini-3.7-flash`.
- `PROCESS_CONCURRENCY` — maximum games recognized at the same time; use `2` for two models.
- `GEMINI_MAX_RETRIES` — default `3`.

## Deploy

1. Unzip the archive and upload the files to a GitHub repository, or use Railway's supported source upload flow.
2. Create a Railway project and deploy the repository.
3. Add the variables above.
4. Railway uses the included `Procfile` to run `python bot.py` as a worker.
5. Send `старт обычный`, `старт приоритет`, or `старт все` in Discord. The script scans the selected channels' history and then watches new messages there. Use `енд` to stop.
6. Review cards and numeric-only mentions are matched against scoreboard nicknames and K/A/D. After registration, the temporary command is deleted; the permanent result remains in the log channel.
7. Games are distributed across `GEMINI_MODELS` and `GEMINI_API_KEYS` in round-robin order. Each game is handled by exactly one assigned model and one assigned key; another model or key never processes the same game. Up to `PROCESS_CONCURRENCY` different games can run simultaneously.

Do not upload a real `.env` file or commit tokens.

> Note: automated user accounts/self-bots can violate Discord's Terms of Service and may lead to account restrictions. A normal Discord bot token is the safer supported option.
