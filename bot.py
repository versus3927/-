[07.09.2026 17:06] versus: import asyncio
import io
import json
import logging
import os
import re
from typing import Optional

import aiohttp
import discord
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# Загрузка токенов, моделей и параметров из панели Railway
DISCORD_USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))

# Настройка каналов и вашего ID для контроля команд
WATCH_CHANNEL_IDS = {
    int(x.strip()) for x in os.getenv("WATCH_CHANNEL_IDS", "").split(",") if x.strip()
}
MY_ACCOUNT_ID = int(os.getenv("MY_ACCOUNT_ID", "0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.82"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("faceit-reg-self")

# Селф-боты инициализируются через базовый класс Client библиотеки discord.py-self
client = discord.Client()

# Переменная состояния автоматизации (старт / енд)
is_active = False


def allowed_for_parsing(message: discord.Message) -> bool:
    """Проверяет, нужно ли автоматически парсить это сообщение."""
    if message.author.id == client.user.id:
        return False  # Не парсим сообщения своего собственного аккаунта
    if WATCH_CHANNEL_IDS and message.channel.id not in WATCH_CHANNEL_IDS:
        return False
    return True


def message_parts(message: discord.Message) -> list[object]:
    parts: list[object] = [message]
    for snapshot in (getattr(message, "message_snapshots", None) or []):
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
    import base64
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    longest = max(image.width, image.height)
[07.09.2026 17:06] versus: if longest < 2400:
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
- Fuzzy nickname matching is REQUIRED. Ignore case, spaces, punctuation, clan tags, decorative prefixes/suffixes and extra text. A roster nickname contained inside a scoreboard nickname is a match: for example versus, versusproto, [TAG]versus and versus_123 refer to the same player when there is no conflicting roster nickname.
- Match obvious Cyrillic/Latin phonetic spellings too. For example Latin versus may appear as Cyrillic версус. Use the scoreboard stats when the identity is reasonably clear from the nickname and roster context.
- Never assign one scoreboard row to two roster players. Prefer the unique strongest nickname match across all ten roster players.
- If a player is listed in the Discord roster but has NO matching row on the scoreboard, ALWAYS return that roster player with kills=0, assists=0, deaths=13. This is the required registration default, not an error. Keep confidence at least 0.90 when absence is clear.
- The scoreboard columns are usually: kills, assists, deaths, score/points, ping. Return ONLY kills, assists, deaths; never confuse points or ping with deaths.
[07.09.2026 17:06] versus: - Keep all five roster players and their roster order exactly as shown in Team A and Team B.
- If several screenshots are supplied, combine their information.
- If a matching row exists but an individual number is genuinely unreadable, lower confidence and explain in notes; do not use 0/0/13 unless the whole player row is absent.
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

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in models:
            url = f"https://googleapis.com{model}:generateContent?key={GEMINI_API_KEY}"
            for attempt in range(GEMINI_MAX_RETRIES):
                async with session.post(url, json=payload) as response:
                    body = await response.text()
                    if response.status < 400:
                        data = json.loads(body)
                        break
                    if response.status not in retryable_statuses:
                        raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
                if attempt + 1 < GEMINI_MAX_RETRIES:
                    await asyncio.sleep(3 * (2 ** attempt))
            if data is not None:
                break

    if data is None:
        raise RuntimeError("Gemini API недоступен или исчерпан лимит.")

    try:
        output_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
raise RuntimeError("Gemini не вернул результат распознавания")return json.loads(output_text)def format_registration(result: dict) -> str:lines = [f"=g {result['match_id']} {result['score_a']} {result['score_b']}","","CT",]for player in result["team_a"]:lines.append(f"{player['id']} {player['kills']} {player['assists']} {player['deaths']}")lines.extend(["", "T"])for player in result["team_b"]:lines.append(f"{player['id']} {player['kills']} {player['assists']} {player['deaths']}")return "\n".join(lines)async def process_upload(message: discord.Message) -> None:urls = image_urls(message)context = message_context(message)if not urls:return# Симулируем эффект набора текста в текущем каналеasync with message.channel.typing():try:raw_images = [await download_image(url) for url in urls[:4]]result = await recognize_match(raw_images, context)fatal = (not result.get("is_match_result")or result.get("match_id") is Noneor result.get("score_a") is Noneor result.get("score_b") is Noneor len(result.get("team_a", [])) != 5or len(result.get("team_b", [])) != 5)if fatal:log.error(f"Не удалось корректно собрать структуру. Notes: {result.get('notes', '')}")returncommand_text = format_registration(result)await asyncio.sleep(1.0)# ОТПРАВЛЯЕМ ОТ ВАШЕГО ИМЕНИ В ЭТОТ ЖЕ КАНАЛawait message.channel.send(command_text)log.info(f"Матч #{result['match_id']} успешно отправлен в канал {message.channel.id}")except Exception as exc:log.exception("Ошибка обработки файла в функции process_upload")@client.eventasync def on_ready():log.info("Селф-бот успешно авторизован: %s", client.user)@client.eventasync def on_message(message: discord.Message):global is_activecommand = message.content.strip().lower()# Проверка команд управления (только от вашего MY_ACCOUNT_ID)if command in ["старт", "енд"]:if MY_ACCOUNT_ID and message.author.id != MY_ACCOUNT_ID:returnif
[07.09.2026 17:06] versus: command == "старт":is_active = Trueawait message.channel.send("✅ Авторег запущен. Начинаю парсинг приходящих матчей.")returnelif command == "енд":is_active = Falseawait message.channel.send("🛑 Авторег остановлен.")return# Автоматическая обработка (работает, если активен режим "старт")if is_active and allowed_for_parsing(message):if image_urls(message):await process_upload(message)if name == "main":client.run(DISCORD_USER_TOKEN)
