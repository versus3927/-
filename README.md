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
- `DELETE_SOURCE_AFTER_REGISTRATION` — delete the processed source card if it still exists after successful registration; default `true`.
- `SOURCE_DELETE_DELAY` — delay before trying to delete the source card; default `1.0` second.
- `REGISTRATION_CONFIRM_TIMEOUT` — seconds to wait for the game bot's `Готово` or `Не вышло` response; default `25.0`.
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
6. Every complete result card (`внесён` or `на проверку`) is parsed deterministically: IDs, Team A/B membership, roster order, score and K/A/D are copied from the card and can no longer be rearranged by AI. AI is used only to identify which card team is CT/DEFENSE from the scoreboard; if that side cannot be identified reliably, the match is skipped rather than registered incorrectly. The source card is deleted and counted only after the game bot replies `Готово`. On `Не вышло` or timeout, the source card remains available for retry and the match is removed from the duplicate registry.
7. Games are distributed across the configured models and keys. Each game is handled by exactly one assigned model and key; different games can run simultaneously.
8. For raw long numeric Discord mentions, the bot keeps the roster slot's K/A/D from the card and matches it only to an unused scoreboard player on the same team with exactly the same K/A/D. It then restores the short 2–5 digit registration ID and preserves roster order. Ambiguous matches are skipped instead of guessed. Duplicate matches and placeholder IDs are rejected. Send `стата`, `статистика`, or `stats` for totals.

Do not upload a real `.env` file or commit tokens.

> Note: automated user accounts/self-bots can violate Discord's Terms of Service and may lead to account restrictions. A normal Discord bot token is the safer supported option.
