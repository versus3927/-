# FACEIT autor eg for Railway

## Railway variables

Set these in **Variables**:

- `DISCORD_USER_TOKEN` — Discord account token.
- `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, etc. — separate Gemini-compatible API keys. Up to 10 numbered keys are supported and consecutive games rotate across them. Keys are never printed in logs. Legacy `GEMINI_API_KEYS` and `GEMINI_API_KEY` are also accepted.
- `AI_API_STYLE` — use `openai` for AI STAR's general API, or `gemini` for the native Google protocol.
- `GEMINI_BASE_URL` — provider base URL. For AI STAR OpenAI-compatible requests use `https://ai.starimg.ru/v1`; for official Google omit it.
- `NORMAL_CHANNEL_IDS` — comma-separated IDs of ordinary-game channels.
- `PRIORITY_CHANNEL_IDS` — comma-separated IDs of priority-game channels.
- `LOG_CHANNEL_ID` — ID of the channel that receives every successful registration, including match number, score, source, and full command.
- `MY_ACCOUNT_ID` — your Discord account ID; recommended so only you can use `старт` and `енд`.
- `MIN_CONFIDENCE` — default `0.82`.
- `BACKFILL_LIMIT` — how many previous messages to inspect in each selected channel; default `500`.
- `SEND_DELAY` — delay before sending each result; default `0.25` seconds.
- `DELETE_AFTER_REGISTRATION` — delete the temporary registration command after it is sent; default `true`.
- `DELETE_DELAY` — seconds to wait before deleting the registration command; default `3.0`.
- `STATS_FILE` — persistent registration history file; use `/data/registration_stats.json` with a Railway volume mounted at `/data`.
- `STATS_TIMEZONE` — timezone used for today's count; default `Europe/Moscow`.
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
6. Complete `на проверку` cards are parsed directly from their short # IDs, team scores, and K/A/D without calling AI. Entries shown as 0/0/0 are registered with the required 0/0/13 absence default. If direct parsing is incomplete, the configured AI is used. After registration, the temporary command is deleted and the permanent result remains in the log channel.
7. Games are distributed across the configured models and keys. Each game is handled by exactly one assigned model and key; different games can run simultaneously.
8. A match ID is reserved before sending, so duplicate screenshots or repeated history scans do not register the same match twice. Send `стата`, `статистика`, or `stats` for totals: all time, today, 24 hours, 10 hours, 1 hour, and 30 minutes.

Do not upload a real `.env` file or commit tokens.

> Note: automated user accounts/self-bots can violate Discord's Terms of Service and may lead to account restrictions. A normal Discord bot token is the safer supported option.
