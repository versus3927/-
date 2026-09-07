import asyncio
import base64
import io
import json
import logging
import os
from typing import Optional

import aiohttp
import discord
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# Railway environment variables
DISCORD_USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))

def parse_channel_ids(variable_name: str) -> set[int]:
    return {
        int(x.strip())
        for x in os.getenv(variable_name, "").split(",")
        if x.strip()
    }


NORMAL_CHANNEL_IDS = parse_channel_ids("NORMAL_CHANNEL_IDS")
PRIORITY_CHANNEL_IDS = parse_channel_ids("PRIORITY_CHANNEL_IDS")
MY_ACCOUNT_ID = int(os.getenv("MY_ACCOUNT_ID", "0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.82"))
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "500"))
SEND_DELAY = float(os.getenv("SEND_DELAY", "0.25"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("faceit-reg-self")

client = discord.Client()
is_active = False
active_channel_ids: set[int] = set()
processed_message_ids: set[int] = set()


def allowed_for_parsing(message: discord.Message) -> bool:
    """Return True if this message may be parsed automatically."""
    if client.user and message.author.id == client.user.id:
        return False
    if not active_channel_ids or message.channel.id not in active_channel_ids:
        return False
    return True


def message_parts(message: discord.Message) -> list[object]:
    parts: list[object] = [message]
    for snapshot in getattr(message, "message_snapshots", None) or []:
        parts.append(getattr(snapshot, "message", snapshot))
    return parts


def message_context(message: discord.Message) -> str:
    chunks: list[str] = []
    for part in message_parts(message):
        content = getattr(part, "content", "")
        if content:
            chunks.append(str(content))
        for embed in getattr(part, "embeds", None) or []:
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
    valid_ext = (".png", ".jpg", ".jpeg", ".webp")
    urls: list[str] = []
    for part in message_parts(message):
        for attachment in getattr(part, "attachments", None) or []:
            filename = str(getattr(attachment, "filename", "")).lower()
            content_type = str(getattr(attachment, "content_type", "") or "")
            if content_type.startswith("image/") or filename.endswith(valid_ext):
                url = getattr(attachment, "url", None)
                if url:
                    urls.append(str(url))
        for embed in getattr(part, "embeds", None) or []:
            if embed.image and embed.image.url:
                urls.append(str(embed.image.url))
            if embed.thumbnail and embed.thumbnail.url:
                urls.append(str(embed.thumbnail.url))
    return list(dict.fromkeys(urls))


async def download_image(session: aiohttp.ClientSession, url: str) -> bytes:
    async with session.get(url) as response:
        response.raise_for_status()
        return await response.read()


def prepare_image(raw: bytes) -> tuple[str, str]:
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
        "required": [
            "id",
            "nickname",
            "kills",
            "assists",
            "deaths",
            "confidence",
        ],
        "additionalProperties": False,
    }
    response_schema = {
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
            "is_match_result",
            "match_id",
            "score_a",
            "score_b",
            "team_a",
            "team_b",
            "overall_confidence",
            "notes",
        ],
        "additionalProperties": False,
    }

    prompt = """You receive one or more screenshots of the SAME FACEIT/CS2 match result.
The Discord result card contains match number and two rosters: Team A and Team B, with numeric IDs like #37 and nicknames. The small CS2 scoreboard contains each nickname and columns K, A, D.
Build a registration result:
- match_id: number after 'Результат матча #'.
- Team A must always be returned in team_a; Team B in team_b.
- score_a and score_b are rounds won by Team A and Team B. The CS2 scoreboard may label sides ATTACK/DEFENSE or T/CT and teams can be on either side; map score to A/B by matching player nicknames.
- For every roster player return the numeric ID from the Discord card and K/A/D from the scoreboard.
- Fuzzy nickname matching is REQUIRED. Ignore case, spaces, punctuation, clan tags, decorative prefixes/suffixes and extra text. A roster nickname contained inside a scoreboard nickname is a match: for example `versus`, `versusproto`, `[TAG]versus` and `versus_123` refer to the same player when there is no conflicting roster nickname.
- Match obvious Cyrillic/Latin phonetic spellings too. For example Latin `versus` may appear as Cyrillic `версус`.
- Never assign one scoreboard row to two roster players. Prefer the unique strongest nickname match across all ten roster players.
- If a roster player has NO matching scoreboard row, return that player with kills=0, assists=0, deaths=13. Keep confidence at least 0.90 when absence is clear.
- Scoreboard columns are usually kills, assists, deaths, score/points, ping. Return ONLY kills, assists, deaths.
- Keep all five roster players and their roster order exactly as shown in Team A and Team B.
- If several screenshots are supplied, combine their information.
- If a matching row exists but an individual number is unreadable, lower confidence and explain in notes; do not use 0/0/13 unless the whole row is absent.
- is_match_result=false for unrelated images; then use null IDs/scores and empty teams.
- A valid result has exactly five players in each team."""
    if message_text.strip():
        prompt += f"\nOptional uploader text: {message_text[:1000]}"

    parts: list[dict] = [{"text": prompt}]
    for raw in images[:4]:
        image_b64, mime = prepare_image(raw)
        parts.append({"inline_data": {"mime_type": mime, "data": image_b64}})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
        },
    }

    timeout = aiohttp.ClientTimeout(total=120)
    retryable_statuses = {429, 500, 502, 503, 504}
    models = list(dict.fromkeys([GEMINI_MODEL, GEMINI_FALLBACK_MODEL]))
    data: Optional[dict] = None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in models:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}"
            )
            for attempt in range(GEMINI_MAX_RETRIES):
                async with session.post(url, json=payload) as response:
                    body = await response.text()
                    if response.status < 400:
                        data = json.loads(body)
                        break
                    if response.status not in retryable_statuses:
                        raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
                if attempt + 1 < GEMINI_MAX_RETRIES:
                    await asyncio.sleep(3 * (2**attempt))
            if data is not None:
                break

    if data is None:
        raise RuntimeError("Gemini API недоступен или исчерпан лимит.")

    try:
        output_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini не вернул результат распознавания") from exc
    return json.loads(output_text)


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
    if not urls:
        return

    context = message_context(message)
    async with message.channel.typing():
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                raw_images = await asyncio.gather(
                    *(download_image(session, url) for url in urls[:4])
                )

            result = await recognize_match(raw_images, context)
            fatal = (
                not result.get("is_match_result")
                or result.get("match_id") is None
                or result.get("score_a") is None
                or result.get("score_b") is None
                or len(result.get("team_a", [])) != 5
                or len(result.get("team_b", [])) != 5
            )
            if fatal:
                log.error(
                    "Не удалось корректно собрать структуру. Notes: %s",
                    result.get("notes", ""),
                )
                return

            confidence = float(result.get("overall_confidence", 0))
            if confidence < MIN_CONFIDENCE:
                log.warning(
                    "Матч распознан с низкой уверенностью %.2f: %s",
                    confidence,
                    result.get("notes", ""),
                )
                return

            await asyncio.sleep(SEND_DELAY)
            await message.channel.send(format_registration(result))
            log.info(
                "Матч #%s успешно отправлен в канал %s",
                result["match_id"],
                message.channel.id,
            )
        except Exception:
            log.exception("Ошибка обработки файла в process_upload")


async def process_message_once(message: discord.Message) -> bool:
    """Process an image message once during the current bot session."""
    if message.id in processed_message_ids:
        return False
    if not allowed_for_parsing(message) or not image_urls(message):
        return False

    processed_message_ids.add(message.id)
    await process_upload(message)
    return True


async def backfill_one_channel(channel_id: int, before_time) -> int:
    """Read old image messages from one channel in chronological order."""
    found = 0
    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)

        log.info(
            "Читаю до %s старых сообщений из канала %s",
            BACKFILL_LIMIT,
            channel_id,
        )
        async for old_message in channel.history(
            limit=BACKFILL_LIMIT,
            before=before_time,
            oldest_first=True,
        ):
            if await process_message_once(old_message):
                found += 1
    except Exception:
        log.exception("Не удалось прочитать историю канала %s", channel_id)
    return found


async def backfill_channels(channel_ids: set[int], before_time) -> int:
    """Scan selected channels concurrently while preserving order per channel."""
    if not channel_ids:
        return 0
    counts = await asyncio.gather(
        *(backfill_one_channel(channel_id, before_time) for channel_id in channel_ids)
    )
    return sum(counts)


@client.event
async def on_ready() -> None:
    log.info("Селф-бот успешно авторизован: %s", client.user)


@client.event
async def on_message(message: discord.Message) -> None:
    global is_active

    command = message.content.strip().lower()
    if command == "енд" or command.startswith("старт"):
        if MY_ACCOUNT_ID and message.author.id != MY_ACCOUNT_ID:
            return

        if command == "енд":
            is_active = False
            active_channel_ids.clear()
            await message.channel.send("🛑 Авторег остановлен.")
            return

        mode = command.removeprefix("старт").strip()
        modes = {
            "обычный": ("обычный", NORMAL_CHANNEL_IDS),
            "приоритет": ("приоритет", PRIORITY_CHANNEL_IDS),
            "все": ("обычный + приоритет", NORMAL_CHANNEL_IDS | PRIORITY_CHANNEL_IDS),
        }
        if mode not in modes:
            await message.channel.send(
                "Выберите режим: `старт обычный`, `старт приоритет` или `старт все`."
            )
            return

        mode_name, selected_ids = modes[mode]
        if not selected_ids:
            await message.channel.send(
                f"❌ Для режима «{mode_name}» не указаны ID каналов в Railway."
            )
            return

        active_channel_ids.clear()
        active_channel_ids.update(selected_ids)
        is_active = True
        await message.channel.send(
            f"✅ Запущен режим «{mode_name}». Читаю старые игры, затем новые."
        )
        count = await backfill_channels(active_channel_ids, message.created_at)
        await message.channel.send(
            f"✅ Архив режима «{mode_name}» проверен. Найдено изображений: {count}."
        )
        return

    if is_active:
        await process_message_once(message)


if __name__ == "__main__":
    client.run(DISCORD_USER_TOKEN)
