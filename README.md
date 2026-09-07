# FACEIT autor eg for Railway

## Railway variables

Set these in **Variables**:

- `DISCORD_USER_TOKEN` — Discord account token.
- `GEMINI_API_KEY` — Google Gemini API key.
- `WATCH_CHANNEL_IDS` — comma-separated Discord channel IDs; empty means all accessible channels.
- `MY_ACCOUNT_ID` — your Discord account ID; recommended so only you can use `старт` and `енд`.
- `MIN_CONFIDENCE` — default `0.82`.
- `GEMINI_MODEL` — primary model name.
- `GEMINI_FALLBACK_MODEL` — fallback model name.
- `GEMINI_MAX_RETRIES` — default `3`.

## Deploy

1. Unzip the archive and upload the files to a GitHub repository, or use Railway's supported source upload flow.
2. Create a Railway project and deploy the repository.
3. Add the variables above.
4. Railway uses the included `Procfile` to run `python main.py` as a worker.

Do not upload a real `.env` file or commit tokens.

> Note: automated user accounts/self-bots can violate Discord's Terms of Service and may lead to account restrictions. A normal Discord bot token is the safer supported option.
