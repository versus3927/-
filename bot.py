import asyncio
import io
import json
import logging
import os
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
WATCH_CHANNEL_IDS = {
    int(x.strip()) for x in os.getenv("WATCH_CHANNEL_IDS", "").split(",") if x.strip()
}
ALLOWED_USER_IDS = {
    int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()
}
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.82"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("faceit-reg")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def allowed(message: discord.Message) -> bool:
    if message.author.bot:
        return False
    if WATCH_CHANNEL_IDS and message.channel.id not in WATCH_CHANNEL_IDS:
        return False
    if ALLOWED_USER_IDS and message.author.id not in ALLOWED_USER_IDS:
        return False
    return True


def message_parts(message: discord.Message) -> list[object]:
    """Return the uploaded message and any Discord-forwarded snapshots."""
    parts: list[object] = [message]
    for snapshot in (getattr(message, "message_snapshots", None) or []):
        # discord.py exposes snapshot fields directly; this fallback also supports
        # wrappers that expose the forwarded payload under `.message`.
        parts.append(getattr(snapshot, "message", snapshot))
    return parts


def message_context(message: discord.Message) -> str:
    chunks: list[str] = []
    for part in message_parts(message):
        content = getattr(part, "content", "")
        if content:
            chunks.append(str(content))
        for embed in (getattr(part, "embeds", None) or []):
            if embed.title:
                chunks.append(embed.title)
            if embed.description:
                chunks.append(embed.description)
            for field in embed.fields:
                chunks.append(f"{field.name}\n{field.value}")
            if embed.footer and embed.footer.text:
                chunks.append(embed.footer.text)
    return "\n".join(chunks)


def image_urls(message: discord.Message) -> list[str]:
    """Collect images from uploads, embeds, and forwarded message snapshots."""
    valid_ext = (".png", ".jpg", ".jpeg", ".webp")
    urls: list[str] = []
    for part in message_parts(message):
        for attachment in (getattr(part, "attachments", None) or []):
            filename = str(getattr(attachment, "filename", "")).lower()
            content_type = str(getattr(attachment, "content_type", "") or "")
            if content_type.startswith("image/") or filename.endswith(valid_ext):
                url = getattr(attachment, "url", None)
                if url:
                    urls.append(str(url))
        for embed in (getattr(part, "embeds", None) or []):
            if embed.image and embed.image.url:
                urls.append(str(embed.image.url))
            if embed.thumbnail and embed.thumbnail.url:
                urls.append(str(embed.thumbnail.url))
    return list(dict.fromkeys(urls))


async def download_image(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()


def prepare_image(raw: bytes) -> tuple[str, str]:
    """Increase resolution while keeping the whole screenshot visible."""
    import base64

    image = Image.open(io.BytesIO(raw)).convert("RGB")
    longest = max(image.width, image.height)
    if longest < 2400:
        scale = min(3.0, 2400 / longest)
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95, optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii"), "image/jpeg"


async def recognize_match(images: list[bytes], message_text: str = "") -> dict:
    player_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "nickname": {"type": "string"},
            "kills": {"type": "integer", "minimum": 0, "maximum": 100},
            "assists": {"type": "integer", "minimum": 0, "maximum": 100},
            "deaths": {"type": "integer", "minimum": 0, "maximum": 100},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["id", "nickname", "kills", "assists", "deaths", "confidence"],
        "additionalProperties": False,
    }
    schema = {
        "name": "faceit_registration",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_match_result": {"type": "boolean"},
                "match_id": {"type": ["integer", "null"]},
                "score_a": {"type": ["integer", "null"], "minimum": 0, "maximum": 99},
                "score_b": {"type": ["integer", "null"], "minimum": 0, "maximum": 99},
                "team_a": {"type": "array", "items": player_schema, "maxItems": 5},
                "team_b": {"type": "array", "items": player_schema, "maxItems": 5},
                "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "notes": {"type": "string"},
            },
            "required": [
                "is_match_result", "match_id", "score_a", "score_b",
                "team_a", "team_b", "overall_confidence", "notes"
            ],
            "additionalProperties": False,
        },
    }

    prompt = """You receive one or more screenshots of the SAME FACEIT/CS2 match result.
The Discord result card contains match number and two rosters: Team A and Team B, with numeric IDs like #37 and nicknames. The small CS2 scoreboard contains each nickname and columns K, A, D.

Build a registration result:
- match_id: number after 'Результат матча #'.
- Team A must always be returned in team_a; Team B in team_b.
- score_a and score_b are rounds won by Team A and Team B. The CS2 scoreboard may label sides ATTACK/DEFENSE or T/CT and teams can be on either side; map score to A/B by matching player nicknames.
- For every roster player return the numeric ID from the Discord card and K/A/D from the scoreboard.
- The scoreboard columns are usually: kills, assists, deaths, score/points, ping. Return ONLY kills, assists, deaths; never confuse points or ping with deaths.
- Keep roster order exactly as shown in Team A and Team B.
- If several screenshots are supplied, combine their information.
- Do not invent unreadable values. Lower confidence and explain in notes.
- is_match_result=false for unrelated images; then use null IDs/scores and empty teams.
- A valid result has exactly five players in each team.
"""
    if message_text.strip():
        prompt += f"\nOptional uploader text: {message_text[:1000]}"

    parts = [{"text": prompt}]
    for raw in images[:4]:
        image_b64, mime = prepare_image(raw)
        parts.append({"inline_data": {"mime_type": mime, "data": image_b64}})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema["schema"],
        },
    }
    timeout = aiohttp.ClientTimeout(total=120)
    retryable_statuses = {429, 500, 502, 503, 504}
    models = list(dict.fromkeys([GEMINI_MODEL, GEMINI_FALLBACK_MODEL]))
    data: Optional[dict] = None
    last_error = ""

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in models:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}"
            )
            for attempt in range(GEMINI_MAX_RETRIES):
                async with session.post(url, json=payload) as response:
                    body = await response.text()
                    if response.status < 400:
                        data = json.loads(body)
                        break
                    last_error = f"Gemini {model}: HTTP {response.status}"
                    if response.status not in retryable_statuses:
                        raise RuntimeError(f"{last_error}: {body[:300]}")
                if attempt + 1 < GEMINI_MAX_RETRIES:
                    await asyncio.sleep(3 * (2 ** attempt))
            if data is not None:
                break

    if data is None:
        raise RuntimeError(
            f"{last_error}. Gemini временно перегружен или исчерпан бесплатный лимит. "
            "Бот уже повторил запрос и попробовал резервную модель; повторите позже."
        )

    try:
        output_text: Optional[str] = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini не вернул результат распознавания")
    if not output_text:
        raise RuntimeError("Gemini вернул пустой результат")
    return json.loads(output_text)


def validation_issues(result: dict) -> list[str]:
    issues = []
    if not result.get("is_match_result"):
        return ["На изображении не найден результат матча"]
    if result.get("match_id") is None:
        issues.append("Не прочитан номер матча")
    if result.get("score_a") is None or result.get("score_b") is None:
        issues.append("Не прочитан итоговый счёт")
    if len(result.get("team_a", [])) != 5:
        issues.append("В команде A распознано не 5 игроков")
    if len(result.get("team_b", [])) != 5:
        issues.append("В команде B распознано не 5 игроков")

    all_players = result.get("team_a", []) + result.get("team_b", [])
    ids = [p.get("id") for p in all_players]
    if len(ids) != len(set(ids)):
        issues.append("Есть повторяющиеся ID игроков")
    if float(result.get("overall_confidence", 0)) < MIN_CONFIDENCE:
        issues.append("Низкая общая уверенность распознавания")
    for player in all_players:
        if float(player.get("confidence", 0)) < MIN_CONFIDENCE:
            issues.append(f"Проверьте статистику #{player.get('id')} {player.get('nickname', '')}")
    return issues


def format_registration(result: dict) -> str:
    lines = [
        f"=g {result['match_id']} {result['score_a']} {result['score_b']}",
        "",
        "CT",
    ]
    for player in result["team_a"]:
        lines.append(
            f"{player['id']} {player['kills']} {player['assists']} {player['deaths']}"
        )
    lines.extend(["", "T"])
    for player in result["team_b"]:
        lines.append(
            f"{player['id']} {player['kills']} {player['assists']} {player['deaths']}"
        )
    return "\n".join(lines)


async def process_upload(message: discord.Message) -> None:
    urls = image_urls(message)
    context = message_context(message)
    if not urls:
        return

    status = await message.reply("🔎 Читаю пересланный матч и собираю регистрацию…", mention_author=False)
    try:
        raw_images = [await download_image(url) for url in urls[:4]]
        result = await recognize_match(raw_images, context)
        issues = validation_issues(result)

        fatal = (
            not result.get("is_match_result")
            or result.get("match_id") is None
            or result.get("score_a") is None
            or result.get("score_b") is None
            or len(result.get("team_a", [])) != 5
            or len(result.get("team_b", [])) != 5
        )
        if fatal:
            details = "\n".join(f"• {issue}" for issue in issues)
            notes = result.get("notes", "")
            await status.edit(
                content=f"❌ Не получилось собрать регистрацию.\n{details}\n{notes[:500]}"
            )
            return

        command = format_registration(result)
        if issues:
            details = "\n".join(f"• {issue}" for issue in issues)
            await status.edit(
                content=f"⚠️ **Проверьте отмеченные значения**\n{details}\n```text\n{command}\n```"
            )
        else:
            await status.edit(
                content=f"✅ **Готовое сообщение для регистрации**\n```text\n{command}\n```"
            )
    except Exception as exc:
        log.exception("Screenshot processing failed")
        await status.edit(content=f"��� Ошибка распознавания: {str(exc)[:350]}")


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)


@bot.event
async def on_message(message: discord.Message):
    if allowed(message):
        await process_upload(message)
    await bot.process_commands(message)


@bot.command(name="reg")
async def reg(ctx: commands.Context):
    """Reply with !reg to a screenshot if automatic processing is restricted."""
    if ALLOWED_USER_IDS and ctx.author.id not in ALLOWED_USER_IDS:
        return
    if not ctx.message.reference or not ctx.message.reference.message_id:
        await ctx.reply("Ответьте `!reg` на сообщение со скриншотом матча.")
        return
    original = await ctx.channel.fetch_message(ctx.message.reference.message_id)
    await process_upload(original)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
