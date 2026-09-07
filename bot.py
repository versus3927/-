import asyncio
import base64
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import discord
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# Railway environment variables
DISCORD_USER_TOKEN = os.environ["DISCORD_USER_TOKEN"]
_key_candidates: list[str] = []
for key_number in range(1, 11):
    value = os.getenv(f"GEMINI_API_KEY_{key_number}", "").strip()
    if value:
        _key_candidates.append(value)

_key_candidates.extend(
    key.strip()
    for key in os.getenv("GEMINI_API_KEYS", "").split(",")
    if key.strip()
)
_single_gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
if _single_gemini_key:
    _key_candidates.append(_single_gemini_key)

GEMINI_API_KEYS = list(dict.fromkeys(_key_candidates))
if not GEMINI_API_KEYS:
    raise RuntimeError(
        "Укажите GEMINI_API_KEY_1, GEMINI_API_KEY_2 или GEMINI_API_KEY в Railway."
    )

AI_API_STYLE = os.getenv("AI_API_STYLE", "gemini").strip().lower()
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    os.getenv("GOOGLE_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"),
).rstrip("/")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash")
GEMINI_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_MODELS",
        f"{GEMINI_MODEL},{GEMINI_FALLBACK_MODEL}",
    ).split(",")
    if model.strip()
]
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
PROCESS_CONCURRENCY = max(1, int(os.getenv("PROCESS_CONCURRENCY", "2")))

def parse_channel_ids(variable_name: str) -> set[int]:
    return {
        int(x.strip())
        for x in os.getenv(variable_name, "").split(",")
        if x.strip()
    }


NORMAL_CHANNEL_IDS = parse_channel_ids("NORMAL_CHANNEL_IDS")
PRIORITY_CHANNEL_IDS = parse_channel_ids("PRIORITY_CHANNEL_IDS")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
MY_ACCOUNT_ID = int(os.getenv("MY_ACCOUNT_ID", "0"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.82"))
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "500"))
SEND_DELAY = float(os.getenv("SEND_DELAY", "0.25"))
DELETE_AFTER_REGISTRATION = os.getenv("DELETE_AFTER_REGISTRATION", "true").lower() in {
    "1", "true", "yes", "on"
}
DELETE_DELAY = float(os.getenv("DELETE_DELAY", "3.0"))
DELETE_SOURCE_AFTER_REGISTRATION = os.getenv(
    "DELETE_SOURCE_AFTER_REGISTRATION", "true"
).lower() in {"1", "true", "yes", "on"}
SOURCE_DELETE_DELAY = float(os.getenv("SOURCE_DELETE_DELAY", "1.0"))
STATS_FILE = os.getenv("STATS_FILE", "/data/registration_stats.json")
STATS_TIMEZONE = ZoneInfo(os.getenv("STATS_TIMEZONE", "Europe/Moscow"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("faceit-reg-self")

client = discord.Client()
is_active = False
active_channel_ids: set[int] = set()
processed_message_ids: set[int] = set()
gemini_assignment_index = 0
processing_semaphore = asyncio.Semaphore(PROCESS_CONCURRENCY)
stats_lock = asyncio.Lock()


def load_registration_records() -> list[dict]:
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        log.exception("Не удалось прочитать файл статистики %s", STATS_FILE)
        return []


async def record_registration(match_id: int) -> bool:
    """Reserve a match ID; return False when it was already registered."""
    async with stats_lock:
        records = load_registration_records()
        if any(str(item.get("match_id")) == str(match_id) for item in records):
            return False

        records.append(
            {
                "match_id": int(match_id),
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        try:
            directory = os.path.dirname(STATS_FILE)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temporary_file = f"{STATS_FILE}.tmp"
            with open(temporary_file, "w", encoding="utf-8") as file:
                json.dump(records, file, ensure_ascii=False, indent=2)
            os.replace(temporary_file, STATS_FILE)
        except Exception:
            log.exception("Не удалось сохранить статистику в %s", STATS_FILE)
        return True


def registration_stats_text() -> str:
    now = datetime.now(timezone.utc)
    parsed: list[datetime] = []
    for item in load_registration_records():
        try:
            value = datetime.fromisoformat(str(item["registered_at"]))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            parsed.append(value.astimezone(timezone.utc))
        except (KeyError, TypeError, ValueError):
            continue

    local_now = now.astimezone(STATS_TIMEZONE)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc)

    def since(delta: timedelta) -> int:
        border = now - delta
        return sum(moment >= border for moment in parsed)

    today_count = sum(moment >= today_start_utc for moment in parsed)
    return (
        "📊 **Статистика регистраций**\n"
        f"Всего: **{len(parsed)}**\n"
        f"Сегодня: **{today_count}**\n"
        f"За 24 часа: **{since(timedelta(hours=24))}**\n"
        f"За 10 часов: **{since(timedelta(hours=10))}**\n"
        f"За 1 час: **{since(timedelta(hours=1))}**\n"
        f"За 30 минут: **{since(timedelta(minutes=30))}**"
    )


def next_gemini_assignment() -> tuple[str, str, int]:
    """Assign exactly one model and one API key to each consecutive game."""
    global gemini_assignment_index
    index = gemini_assignment_index
    gemini_assignment_index += 1
    model = GEMINI_MODELS[index % len(GEMINI_MODELS)]
    key_index = index % len(GEMINI_API_KEYS)
    api_key = GEMINI_API_KEYS[key_index]
    return model, api_key, key_index + 1


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


async def resolve_member_mentions(text: str, message: discord.Message) -> str:
    """Replace raw Discord mention IDs with visible server display names."""
    mention_ids = list(
        dict.fromkeys(
            int(value)
            for value in re.findall(r"<@!?(\d{15,22})>", text)
        )
    )
    if not mention_ids:
        return text

    known_members = {
        int(member.id): member
        for member in (getattr(message, "mentions", None) or [])
    }
    guild = getattr(message, "guild", None)
    for member_id in mention_ids:
        member = known_members.get(member_id)
        if member is None and guild is not None:
            member = guild.get_member(member_id)
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(member_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        if member is None:
            continue

        display_name = str(getattr(member, "display_name", "") or "").strip()
        if display_name:
            text = re.sub(
                rf"<@!?{member_id}>",
                f"@{display_name}",
                text,
            )
    return text


async def message_context(message: discord.Message) -> str:
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
    return await resolve_member_mentions("\n".join(chunks), message)


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


def extract_short_player_ids(message_text: str, match_id: Optional[int]) -> list[int]:
    """Extract ten short registration IDs in roster order from Discord text."""
    missing_stats = re.search(
        r"Нет\s+статистики\s+для\s+игроков\s*:\s*([^\n]+)",
        message_text,
        re.I,
    )
    search_areas = [missing_stats.group(1)] if missing_stats else []
    search_areas.append(message_text)

    for area in search_areas:
        ids: list[int] = []
        values = re.findall(r"(?<!\d)#\s*(\d{1,5})(?!\d)", area)
        if not values:
            values = re.findall(
                r"(?:^|\n|[@•]\s*)(\d{2,5})\s*\|",
                area,
                re.M,
            )
        for value in values:
            player_id = int(value)
            if match_id is not None and player_id == int(match_id):
                continue
            if player_id not in ids:
                ids.append(player_id)
        if len(ids) == 10:
            return ids
    return []


def parse_review_card(message_text: str) -> Optional[dict]:
    """Parse complete Discord 'на проверку' cards without an AI request."""
    if "на проверку" not in message_text.lower():
        return None

    match = re.search(r"Результат\s+матча\s*#\s*(\d+)", message_text, re.I)
    if not match:
        return None

    score_a_match = re.search(
        r"Команда\s*A[^\n]*?\b(?:CT|T)\b\s*[-–—:]\s*(\d+)",
        message_text,
        re.I,
    )
    score_b_match = re.search(
        r"Команда\s*B[^\n]*?\b(?:CT|T)\b\s*[-–—:]\s*(\d+)",
        message_text,
        re.I,
    )
    if not score_a_match or not score_b_match:
        recognized_score = re.search(
            r"Распознано\s+со\s+скриншота\s*:\s*(\d+)\s*[-:]\s*(\d+)",
            message_text,
            re.I,
        )
        if not recognized_score:
            return None
        score_a = int(recognized_score.group(1))
        score_b = int(recognized_score.group(2))
    else:
        score_a = int(score_a_match.group(1))
        score_b = int(score_b_match.group(1))

    player_pattern = re.compile(
        r"#\s*(\d{1,5})\s*\|\s*([^\n—–]+?)\s*[—–-]\s*"
        r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
        re.I,
    )
    players: list[dict] = []
    seen_ids: set[int] = set()
    for player_match in player_pattern.finditer(message_text):
        player_id = int(player_match.group(1))
        if player_id in seen_ids:
            continue
        seen_ids.add(player_id)
        nickname = player_match.group(2).strip(" `*_.,")
        kills = int(player_match.group(3))
        assists = int(player_match.group(4))
        deaths = int(player_match.group(5))
        if kills == 0 and assists == 0 and deaths == 0:
            deaths = 13
        players.append(
            {
                "id": player_id,
                "nickname": nickname,
                "kills": kills,
                "assists": assists,
                "deaths": deaths,
                "confidence": 0.99,
            }
        )

    if len(players) != 10:
        return None

    return {
        "is_match_result": True,
        "match_id": int(match.group(1)),
        "score_a": score_a,
        "score_b": score_b,
        "team_a": players[:5],
        "team_b": players[5:10],
        "overall_confidence": 0.99,
        "notes": "Карточка «на проверку» разобрана напрямую по ID и K/A/D.",
    }


async def recognize_match(images: list[bytes], message_text: str = "") -> dict:
    direct_result = parse_review_card(message_text)
    if direct_result is not None:
        log.info(
            "Матч #%s разобран напрямую без запроса к ИИ",
            direct_result["match_id"],
        )
        return direct_result

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
- Cards titled 'на проверку' are valid match results and MUST be registered when match number, score and rosters can be recovered. These cards often already contain short # IDs and K/A/D next to every player; use those values directly even when the attached scoreboard is small or blurry.
- In review cards, strings like `@#64 | kanei — 8/2/12` mean registration id=64, nickname=kanei, kills=8, assists=2, deaths=12. The @ formatting does not turn the short # number into a Discord user ID.
- If a review card says a player was not found and shows 0/0/0, apply the required absent-row default 0/0/13 and still keep that player.
- For every roster player return the SHORT registration ID printed with # immediately before the nickname/mention. It is usually 2, 3 or 4 digits (for example #37, #539, #1639). Use the complete short # number. NEVER use a long Discord mention/user ID such as 1524375653149966517.
- NEVER invent positional IDs such as 1,2,3,4,5 or 5,4,3,2,1. Array position is not a player ID. If the card contains a line like `Нет статистики для игроков: #89, #124, ...`, those ten short # numbers are the roster IDs in displayed order and must be returned exactly.
- Some roster names are Discord mentions or contain only digits. A numeric-only mention is NOT the nickname. Identify that player by the K/A/D printed beside or below the roster entry, then match those K/A/D values to the unique scoreboard row and recover the real nickname from the scoreboard.
- When several numeric-only roster entries exist, solve them globally: compare all visible K/A/D values and all still-unmatched scoreboard rows, and never assign one scoreboard row twice. Use team membership, roster order and remaining unmatched rows as tie-breakers.
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

    gemini_payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": response_schema,
        },
    }

    openai_content: list[dict] = [
        {
            "type": "text",
            "text": prompt
            + "\nReturn ONLY valid JSON matching this schema:\n"
            + json.dumps(response_schema, ensure_ascii=False),
        }
    ]
    for image_part in parts[1:]:
        inline = image_part["inline_data"]
        openai_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{inline['mime_type']};base64,{inline['data']}"
                },
            }
        )

    timeout = aiohttp.ClientTimeout(total=120)
    retryable_statuses = {429, 500, 502, 503, 504}
    assigned_model, assigned_api_key, assigned_key_number = next_gemini_assignment()
    # Одна игра всегда обрабатывается только одной моделью и одним ключом.
    models = [assigned_model]
    log.info(
        "Игра назначена только модели %s и ключу #%s",
        assigned_model,
        assigned_key_number,
    )
    data: Optional[dict] = None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in models:
            if AI_API_STYLE == "openai":
                url = f"{GEMINI_BASE_URL}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {assigned_api_key}",
                    "Content-Type": "application/json",
                }
                request_payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": openai_content}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
            else:
                url = (
                    f"{GEMINI_BASE_URL}/v1beta/models/"
                    f"{model}:generateContent"
                )
                headers = {"x-goog-api-key": assigned_api_key}
                request_payload = gemini_payload

            for attempt in range(GEMINI_MAX_RETRIES):
                async with session.post(
                    url, json=request_payload, headers=headers
                ) as response:
                    body = await response.text()
                    if response.status < 400:
                        data = json.loads(body)
                        break
                    log.warning(
                        "Gemini %s вернул HTTP %s: %s",
                        model,
                        response.status,
                        body[:300],
                    )
                    if response.status not in retryable_statuses:
                        raise RuntimeError(
                            f"Gemini {model}: HTTP {response.status}: {body[:300]}"
                        )
                if attempt + 1 < GEMINI_MAX_RETRIES:
                    await asyncio.sleep(3 * (2**attempt))
            if data is not None:
                break

    if data is None:
        raise RuntimeError(
            f"Gemini {assigned_model} недоступен или исчерпан лимит после "
            f"{GEMINI_MAX_RETRIES} попыток."
        )

    try:
        if AI_API_STYLE == "openai":
            output_text = data["choices"][0]["message"]["content"]
        else:
            output_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("ИИ не вернул результат распознавания") from exc

    if isinstance(output_text, list):
        output_text = "".join(
            item.get("text", "") for item in output_text if isinstance(item, dict)
        )
    output_text = str(output_text).strip()
    if output_text.startswith("```"):
        output_text = output_text.split("\n", 1)[1]
        output_text = output_text.rsplit("```", 1)[0].strip()
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


async def send_registration_log(
    result: dict,
    source_message: discord.Message,
    command_text: str,
) -> None:
    """Send every successful registration to the configured log channel."""
    if not LOG_CHANNEL_ID:
        return

    try:
        log_channel = client.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            log_channel = await client.fetch_channel(LOG_CHANNEL_ID)

        await log_channel.send(
            f"✅ Зарегистрирована игра #{result['match_id']}\n"
            f"Счёт: {result['score_a']}:{result['score_b']}\n"
            f"Источник: <#{source_message.channel.id}>\n"
            f"```text\n{command_text}\n```"
        )
    except Exception:
        log.exception(
            "Не удалось отправить лог матча #%s в канал %s",
            result.get("match_id"),
            LOG_CHANNEL_ID,
        )


async def process_upload(message: discord.Message) -> None:
    urls = image_urls(message)
    if not urls:
        return

    context = await message_context(message)
    async with message.channel.typing():
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                raw_images = await asyncio.gather(
                    *(download_image(session, url) for url in urls[:4])
                )

            result = await recognize_match(raw_images, context)

            expected_ids = extract_short_player_ids(context, result.get("match_id"))
            returned_players = [
                *result.get("team_a", []),
                *result.get("team_b", []),
            ]
            if len(expected_ids) == 10 and len(returned_players) == 10:
                for player, correct_id in zip(returned_players, expected_ids):
                    player["id"] = correct_id
                log.info(
                    "ID матча #%s принудительно сверены с карточкой: %s",
                    result.get("match_id"),
                    expected_ids,
                )
            else:
                returned_ids = [player.get("id") for player in returned_players]
                positional_ids = [1, 2, 3, 4, 5, 5, 4, 3, 2, 1]
                if returned_ids == positional_ids or (
                    len(returned_ids) == 10
                    and all(isinstance(value, int) and 1 <= value <= 5 for value in returned_ids)
                ):
                    log.error(
                        "Матч #%s пропущен: модель выдумала позиционные ID %s",
                        result.get("match_id"),
                        returned_ids,
                    )
                    return

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

            match_id = int(result["match_id"])
            if not await record_registration(match_id):
                log.info("Матч #%s уже зарегистрирован — повтор пропущен", match_id)
                return

            command_text = format_registration(result)
            await asyncio.sleep(SEND_DELAY)
            sent_registration = await message.channel.send(command_text)
            await send_registration_log(result, message, command_text)
            if DELETE_AFTER_REGISTRATION:
                await asyncio.sleep(DELETE_DELAY)
                try:
                    await sent_registration.delete()
                except discord.NotFound:
                    # Регистрационный бот уже успел удалить команду — это нормально.
                    pass
                except Exception:
                    log.exception(
                        "Не удалось удалить сообщение регистрации матча #%s",
                        result.get("match_id"),
                    )

            if DELETE_SOURCE_AFTER_REGISTRATION:
                await asyncio.sleep(SOURCE_DELETE_DELAY)
                try:
                    await message.delete()
                except discord.NotFound:
                    # Исходная карточка уже удалена автоматически.
                    pass
                except discord.Forbidden:
                    log.warning(
                        "Нет права удалить исходное сообщение %s в канале %s",
                        message.id,
                        message.channel.id,
                    )
                except Exception:
                    log.exception(
                        "Не удалось удалить исходное сообщение матча #%s",
                        result.get("match_id"),
                    )
            log.info(
                "Матч #%s ��спешно отправлен в канал %s",
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
    async with processing_semaphore:
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
        batch: list[discord.Message] = []
        async for old_message in channel.history(
            limit=BACKFILL_LIMIT,
            before=before_time,
            oldest_first=True,
        ):
            if (
                old_message.id not in processed_message_ids
                and allowed_for_parsing(old_message)
                and image_urls(old_message)
            ):
                batch.append(old_message)

            if len(batch) >= PROCESS_CONCURRENCY:
                results = await asyncio.gather(
                    *(process_message_once(item) for item in batch)
                )
                found += sum(bool(result) for result in results)
                batch.clear()

        if batch:
            results = await asyncio.gather(
                *(process_message_once(item) for item in batch)
            )
            found += sum(bool(result) for result in results)
    except Exception:
        log.exception("Не удалось прочитать историю ��анала %s", channel_id)
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

    if command in ("стата", "статистика", "stats"):
        if MY_ACCOUNT_ID and message.author.id != MY_ACCOUNT_ID:
            return
        await message.channel.send(registration_stats_text())
        return

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
