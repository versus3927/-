import asyncio
import base64
import contextlib
import html
import io
import json
import logging
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from itertools import permutations
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import discord
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

BOT_VERSION = "v25-forwarded-test-only-2026-09-09"

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


_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
        "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
        "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
        "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def normalize_nickname(value: object) -> str:
    """Normalize tags, punctuation and Cyrillic/Latin spelling for matching."""
    text = strip_leading_clan_tags(str(value))
    text = unicodedata.normalize("NFKD", text).casefold()
    text = text.translate(_CYRILLIC_TO_LATIN)
    return "".join(character for character in text if character.isalnum())


def strip_leading_clan_tags(value: str) -> str:
    """Drop faded clan tags like [CLION] or plain `CLION` before the real nick."""
    text = str(value).strip()
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"^\s*[\[({][^\])}]{1,20}[\])}]\s*", "", text)
        # Discord may render a role/clan prefix without brackets, for example
        # `CLION 1331` or `CLION | 1331`. Only an all-uppercase/digit prefix is
        # removed; the actual nickname is everything after it.
        text = re.sub(
            r"^\s*[A-ZА-ЯЁ0-9]{2,16}(?:\s*[|:·•\-–—]\s*|\s+)(?=\S)",
            "",
            text,
        )
    return text.strip()


def nickname_similarity(first: object, second: object) -> float:
    """Match names such as versus/версус/versustop/111versus."""
    left = normalize_nickname(first)
    right = normalize_nickname(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    # Numeric nicknames are valid in STANDOFF 2 (for example `51`).  They are
    # safe only as an exact match; fuzzy numeric matching would confuse them
    # with player IDs and scoreboard values.
    if left.isdigit() or right.isdigit():
        return 0.0

    left_without_edge_digits = re.sub(r"^\d+|\d+$", "", left)
    right_without_edge_digits = re.sub(r"^\d+|\d+$", "", right)
    if (
        left_without_edge_digits
        and right_without_edge_digits
        and left_without_edge_digits == right_without_edge_digits
    ):
        return 0.99

    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return 0.94 + 0.06 * len(shorter) / len(longer)
    return SequenceMatcher(None, left, right).ratio()


def nicknames_match(first: object, second: object) -> bool:
    return nickname_similarity(first, second) >= 0.72


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
REGISTRATION_CONFIRM_TIMEOUT = float(
    os.getenv("REGISTRATION_CONFIRM_TIMEOUT", "25.0")
)
PLAYER_MODAL_TIMEOUT = float(os.getenv("PLAYER_MODAL_TIMEOUT", "12.0"))
STATS_FILE = os.getenv("STATS_FILE", "/data/registration_stats.json")
STATS_TIMEZONE = ZoneInfo(os.getenv("STATS_TIMEZONE", "Europe/Moscow"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("faceit-reg-self")

client = discord.Client()
BOT_STARTED_AT = datetime.now(timezone.utc)
is_active = False
active_channel_ids: set[int] = set()
processed_message_ids: set[int] = set()
gemini_assignment_index = 0
processing_semaphore = asyncio.Semaphore(PROCESS_CONCURRENCY)
stats_lock = asyncio.Lock()
player_modal_lock = asyncio.Lock()
processing_match_lock = asyncio.Lock()
processing_match_ids: set[int] = set()


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


async def registration_exists(match_id: int) -> bool:
    """Return whether this match is already present in persistent history."""
    async with stats_lock:
        return any(
            str(item.get("match_id")) == str(match_id)
            for item in load_registration_records()
        )


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


async def forget_registration(match_id: int) -> None:
    """Remove a failed reservation so the source match can be retried."""
    async with stats_lock:
        records = load_registration_records()
        remaining = [
            item for item in records
            if str(item.get("match_id")) != str(match_id)
        ]
        if len(remaining) == len(records):
            return
        directory = os.path.dirname(STATS_FILE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary_file = f"{STATS_FILE}.tmp"
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(remaining, file, ensure_ascii=False, indent=2)
        os.replace(temporary_file, STATS_FILE)


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


def registration_status_counts() -> dict[str, int]:
    """Return compact registration counters for the status report."""
    now = datetime.now(timezone.utc)
    valid_times: list[datetime] = []
    for item in load_registration_records():
        try:
            value = datetime.fromisoformat(str(item["registered_at"]))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            valid_times.append(value.astimezone(timezone.utc))
        except (KeyError, TypeError, ValueError):
            continue
    local_now = now.astimezone(STATS_TIMEZONE)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc)
    return {
        "total": len(valid_times),
        "today": sum(value >= today_start_utc for value in valid_times),
        "hour": sum(value >= now - timedelta(hours=1) for value in valid_times),
    }


def format_uptime(seconds: int) -> str:
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours or days:
        parts.append(f"{hours} ч")
    if minutes or hours or days:
        parts.append(f"{minutes} мин")
    parts.append(f"{secs} сек")
    return " ".join(parts)


def build_status_html(status: dict[str, object]) -> str:
    """Build a self-contained, secret-free HTML readiness report."""
    esc = lambda value: html.escape(str(value), quote=True)
    channel_items = "".join(
        f"<li><span>{esc(name)}</span></li>"
        for name in status.get("channels", [])
    ) or "<li><span>Каналы не выбраны</span></li>"
    active_class = "ok" if status.get("active") else "idle"
    active_text = "Авторег запущен" if status.get("active") else "Авторег ожидает команду"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FACEIT AutoReg — статус</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f9f8f7; --surface:#fff; --soft:#f0efed; --text:#2c2c2b; --muted:#7d7a75; --border:#e6e5e3; --blue:#2783de; --green:#46a171; --orange:#d5803b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; padding:32px 20px; background:var(--bg); color:var(--text); font:16px/1.5 Arial,system-ui,sans-serif; }}
    main {{ width:min(960px,100%); margin:0 auto; }}
    header {{ display:flex; align-items:flex-start; justify-content:space-between; gap:24px; margin-bottom:24px; }}
    h1 {{ margin:0 0 6px; font-size:32px; line-height:1.15; letter-spacing:-.02em; }}
    .sub {{ margin:0; color:var(--muted); }}
    .badge {{ display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border:1px solid color-mix(in srgb,var(--green) 35%,var(--border)); border-radius:999px; background:color-mix(in srgb,var(--green) 10%,var(--surface)); color:var(--green); font-weight:700; white-space:nowrap; }}
    .dot {{ width:9px; height:9px; border-radius:50%; background:currentColor; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    .card {{ padding:20px; border:1px solid var(--border); border-radius:12px; background:var(--surface); box-shadow:0 1px 2px rgba(0,0,0,.04); }}
    .card.wide {{ grid-column:1/-1; }}
    h2 {{ margin:0 0 16px; font-size:18px; }}
    dl {{ display:grid; grid-template-columns:minmax(130px,.8fr) minmax(0,1.2fr); gap:10px 16px; margin:0; }}
    dt {{ color:var(--muted); }} dd {{ margin:0; font-weight:700; overflow-wrap:anywhere; }}
    .state {{ color:var(--green); }} .state.idle {{ color:var(--orange); }}
    ul {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:0; padding:0; list-style:none; }}
    li {{ padding:10px 12px; border-radius:8px; background:var(--soft); overflow-wrap:anywhere; }}
    footer {{ margin-top:20px; color:var(--muted); font-size:14px; }}
    code {{ font-family:Consolas,Menlo,monospace; font-size:.92em; }}
    @media (max-width:700px) {{ body {{ padding:20px 16px; }} header {{ display:block; }} .badge {{ margin-top:16px; }} .grid {{ grid-template-columns:1fr; }} .card.wide {{ grid-column:auto; }} ul {{ grid-template-columns:1fr; }} dl {{ grid-template-columns:1fr; gap:3px; }} dd {{ margin-bottom:8px; }} }}
    @media (prefers-color-scheme:dark) {{ :root {{ --bg:#191919; --surface:#202020; --soft:#2b2b2a; --text:#fff; --muted:rgba(255,255,255,.65); --border:rgba(255,255,255,.20); --blue:#5e9fe8; --green:#72bc8f; --orange:#de9255; }} .card {{ box-shadow:none; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div><h1>FACEIT AutoReg</h1><p class="sub">Отчёт готовности без токенов и API-ключей</p></div>
    <div class="badge"><span class="dot"></span>Бот на связи</div>
  </header>
  <section class="grid">
    <article class="card"><h2>Состояние</h2><dl>
      <dt>Режим</dt><dd class="state {active_class}">{active_text}</dd>
      <dt>Версия</dt><dd><code>{esc(status['version'])}</code></dd>
      <dt>Аптайм</dt><dd>{esc(status['uptime'])}</dd>
      <dt>Задержка Discord</dt><dd>{esc(status['latency'])}</dd>
      <dt>Сейчас обрабатывается</dt><dd>{esc(status['processing'])}</dd>
    </dl></article>
    <article class="card"><h2>Пользователь из Variables</h2><dl>
      <dt>Ник в Discord</dt><dd>{esc(status['configured_name'])}</dd>
      <dt>MY_ACCOUNT_ID</dt><dd><code>{esc(status['configured_id'])}</code></dd>
      <dt>Текущая сессия</dt><dd>{esc(status['session_user'])}</dd>
      <dt>Команды</dt><dd>Доступны всем пользователям</dd>
    </dl></article>
    <article class="card"><h2>Регистрации</h2><dl>
      <dt>Всего</dt><dd>{esc(status['registrations_total'])}</dd>
      <dt>Сегодня</dt><dd>{esc(status['registrations_today'])}</dd>
      <dt>За последний час</dt><dd>{esc(status['registrations_hour'])}</dd>
      <dt>Минимум игроков</dt><dd>6 совпадений</dd>
    </dl></article>
    <article class="card"><h2>Распознавание</h2><dl>
      <dt>API-режим</dt><dd>{esc(status['api_style'])}</dd>
      <dt>Модели</dt><dd>{esc(status['models'])}</dd>
      <dt>Ключей настроено</dt><dd>{esc(status['key_count'])}</dd>
      <dt>Параллельность</dt><dd>{esc(status['concurrency'])}</dd>
      <dt>Мин. уверенность</dt><dd>{esc(status['confidence'])}</dd>
    </dl></article>
    <article class="card wide"><h2>Каналы текущего режима</h2><ul>{channel_items}</ul></article>
  </section>
  <footer>Сформировано: {esc(status['generated_at'])} · Часовой пояс: {esc(status['timezone'])}</footer>
</main>
</body>
</html>"""


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


def is_forwarded_message(message: discord.Message) -> bool:
    """Detect a Discord forwarded message without changing card parsing."""
    if getattr(message, "message_snapshots", None):
        return True
    flags = getattr(message, "flags", None)
    return bool(getattr(flags, "forwarded", False))


async def resolve_member_mentions(text: str, message: discord.Message) -> str:
    """Replace long raw mentions with visible server display names."""
    mention_ids = list(dict.fromkeys(
        int(value) for value in re.findall(r"<@!?(\d{15,22})>", text)
    ))
    guild = getattr(message, "guild", None)
    known = {int(member.id): member for member in (message.mentions or [])}
    for member_id in mention_ids:
        member = known.get(member_id) or (guild.get_member(member_id) if guild else None)
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(member_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
        display_name = str(getattr(member, "display_name", "") or "").strip()
        if display_name:
            text = re.sub(rf"<@!?{member_id}>", f"@{display_name}", text)
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


def find_get_players_button(message: discord.Message):
    """Find the existing `Получить игроков` component on a result card."""
    stack: list[object] = []
    for part in message_parts(message):
        stack.extend(getattr(part, "components", None) or [])

    visited: set[int] = set()
    while stack:
        component = stack.pop(0)
        identity = id(component)
        if identity in visited:
            continue
        visited.add(identity)

        label = str(getattr(component, "label", "") or "").strip().lower()
        if "получить игроков" in label and callable(getattr(component, "click", None)):
            return component

        stack.extend(getattr(component, "children", None) or [])
        stack.extend(getattr(component, "components", None) or [])
    return None


def extract_player_modal_text(root: object) -> Optional[str]:
    """Read the prefilled text area from a modal returned by discord.py-self."""
    queue: list[tuple[object, int]] = [(root, 0)]
    visited: set[int] = set()
    candidates: list[str] = []

    while queue:
        value, depth = queue.pop(0)
        if value is None or depth > 12:
            continue
        if isinstance(value, str):
            upper = value.upper()
            has_match = bool(re.search(r"=G\s+\d+", upper))
            has_ct = bool(re.search(r"(?m)^\s*#?\s*CT\s*$", upper))
            has_t = bool(re.search(r"(?m)^\s*#?\s*T\s*$", upper))
            numeric_rows = len(
                re.findall(r"(?m)^\s*\d{1,5}\s+\d+\s+\d+\s+\d+\s*$", value)
            )
            if has_match and has_ct and has_t and numeric_rows >= 8:
                candidates.append(value)
            continue
        if isinstance(value, (bytes, bytearray, int, float, bool)):
            continue

        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)

        if isinstance(value, dict):
            queue.extend((item, depth + 1) for item in value.values())
            continue
        if isinstance(value, (list, tuple, set)):
            queue.extend((item, depth + 1) for item in value)
            continue

        # Some discord.py-self interaction wrappers keep the response only in
        # private instance fields. Inspect their bounded __dict__ values too.
        with contextlib.suppress(Exception):
            object_values = list(vars(value).values())[:80]
            queue.extend((item, depth + 1) for item in object_values)

        # discord.py-self versions expose modal fields through slightly
        # different wrappers. Only inspect the known, bounded attributes.
        for attribute in (
            "value", "default", "text", "content", "data", "modal", "payload",
            "raw_data", "values", "embeds", "embed", "fields", "description",
            "interaction", "message", "response", "response_message",
            "original_response", "followup", "successful", "result", "messages",
            "components", "children", "items",
        ):
            with contextlib.suppress(Exception):
                child = getattr(value, attribute)
                if child is not value:
                    queue.append((child, depth + 1))

    return max(candidates, key=len) if candidates else None


def extract_interaction_image_urls(root: object) -> list[str]:
    """Collect image attachments from a private interaction response."""
    queue: list[tuple[object, int]] = [(root, 0)]
    visited: set[int] = set()
    found: list[str] = []
    while queue:
        value, depth = queue.pop(0)
        if value is None or depth > 7:
            continue
        if isinstance(value, str):
            lowered = value.lower().split("?", 1)[0]
            if value.startswith("http") and lowered.endswith((".png", ".jpg", ".jpeg", ".webp")):
                found.append(value)
            continue
        if isinstance(value, (bytes, bytearray, int, float, bool)):
            continue
        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)
        if isinstance(value, dict):
            queue.extend((item, depth + 1) for item in value.values())
            continue
        if isinstance(value, (list, tuple, set)):
            queue.extend((item, depth + 1) for item in value)
            continue
        for attribute in (
            "url", "proxy_url", "attachments", "embeds", "image", "thumbnail",
            "data", "message", "response", "response_message", "successful",
            "result", "messages", "components", "children", "items",
        ):
            with contextlib.suppress(Exception):
                child = getattr(value, attribute)
                if child is not value:
                    queue.append((child, depth + 1))
    return list(dict.fromkeys(found))


async def get_players_response(message: discord.Message) -> tuple[Optional[str], list[str]]:
    """Click `Получить игроков` and capture its private helper message."""
    button = find_get_players_button(message)
    if button is None:
        return None, []

    async with player_modal_lock:
        custom_id = str(getattr(button, "custom_id", "") or "")
        source_match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", await message_context(message), re.I)
        expected_match_id = int(source_match.group(1)) if source_match else None

        def modal_matches_expected(text: Optional[str]) -> bool:
            if not text:
                return False
            if expected_match_id is None:
                return True
            return bool(re.search(rf"=g\s+{expected_match_id}\b", text, re.I))

        def interaction_check(interaction: object) -> bool:
            interaction_custom_id = str(
                getattr(interaction, "custom_id", "")
                or (getattr(interaction, "data", {}) or {}).get("custom_id", "")
            )
            if custom_id and interaction_custom_id and interaction_custom_id != custom_id:
                return False
            interaction_text = extract_player_modal_text(interaction)
            return not interaction_text or modal_matches_expected(interaction_text)

        # discord.py-self dispatches `interaction_finish` after the private
        # component response has been finalized and Interaction.successful
        # has been populated. `interaction` is retained as a compatibility
        # fallback for builds that only expose the first event.
        def helper_message_check(candidate: object) -> bool:
            text = extract_player_modal_text(candidate)
            # Ephemeral responses may expose no channel or a synthetic
            # channel. The exact match number is a safer binding.
            return modal_matches_expected(text)

        waiters = [
            asyncio.create_task(client.wait_for("message", check=helper_message_check)),
            asyncio.create_task(client.wait_for("interaction_finish", check=interaction_check)),
            asyncio.create_task(client.wait_for("interaction", check=interaction_check)),
        ]
        try:
            click_result = await button.click()
            observed_roots: list[object] = [button, message]
            if click_result is not None:
                observed_roots.append(click_result)
            deadline = asyncio.get_running_loop().time() + PLAYER_MODAL_TIMEOUT
            pending = set(waiters)
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    log.error(
                        "Матч #%s: тайм-аут захвата ephemeral-ответа «Получить игроков»; observed=%s",
                        expected_match_id or "?",
                        [type(item).__name__ for item in observed_roots],
                    )
                    return None, []

                # Poll already returned interaction objects because their
                # response fields can be populated after button.click exits.
                cached = list(getattr(client, "cached_messages", None) or [])[-25:]
                for root in [*observed_roots, *cached]:
                    response_text = extract_player_modal_text(root)
                    if modal_matches_expected(response_text):
                        log.info(
                            "Матч #%s: ephemeral-ответ «Получить игроков» захвачен из %s",
                            expected_match_id or "?",
                            type(root).__name__,
                        )
                        return response_text, extract_interaction_image_urls(root)

                if pending:
                    done, still_pending = await asyncio.wait(
                        pending,
                        timeout=min(0.35, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    pending = set(still_pending)
                    for completed in done:
                        try:
                            event_result = completed.result()
                        except Exception:
                            log.exception(
                                "Матч #%s: ошибка получения interaction event",
                                expected_match_id or "?",
                            )
                            continue
                        if event_result is not None:
                            observed_roots.append(event_result)
                else:
                    await asyncio.sleep(min(0.2, remaining))
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            for waiter in waiters:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await waiter


async def get_players_modal_text(message: discord.Message) -> Optional[str]:
    """Backward-compatible wrapper used by older tests/integrations."""
    text, _ = await get_players_response(message)
    return text


def parse_players_modal(modal_text: str) -> Optional[dict[str, list[dict]]]:
    """Parse authoritative starting-side groups from the player modal."""
    sides: dict[str, list[dict]] = {"CT": [], "T": []}
    current_side: Optional[str] = None
    named_pattern = re.compile(r"^\s*(\d{1,5})\s+(.+?)\s*=\s*(\d+)\s+(\d+)\s+(\d+)\s*$")
    numeric_pattern = re.compile(r"^\s*(\d{1,5})\s+(\d+)\s+(\d+)\s+(\d+)\s*$")

    for raw_line in modal_text.splitlines():
        line = raw_line.strip()
        header = re.fullmatch(r"#?\s*(CT|T)\s*", line, re.I)
        if header:
            current_side = header.group(1).upper()
            continue
        if current_side is None:
            continue
        named = named_pattern.fullmatch(line)
        numeric = numeric_pattern.fullmatch(line)
        if not named and not numeric:
            continue
        if named:
            player_id, nickname = int(named.group(1)), named.group(2).strip()
            kills, assists, deaths = map(int, named.group(3, 4, 5))
        else:
            player_id, nickname = int(numeric.group(1)), ""
            kills, assists, deaths = map(int, numeric.group(2, 3, 4))
        sides[current_side].append({
            "id": player_id,
            "nickname": nickname,
            "kills": kills,
            "assists": assists,
            "deaths": deaths,
        })

    players = [*sides["CT"], *sides["T"]]
    player_ids = [player["id"] for player in players]
    if (
        len(sides["CT"]) != 5
        or len(sides["T"]) != 5
        or any(player_id <= 0 for player_id in player_ids)
        or len(set(player_ids)) != 10
    ):
        return None
    return sides


def result_from_players_modal(
    message_text: str,
    modal_text: str,
    score_override: Optional[tuple[int, int]] = None,
    visual_verified: bool = False,
) -> Optional[dict]:
    """Build a registration only after checking the modal against the card."""
    parsed = parse_players_modal(modal_text)
    if parsed is None:
        return None

    match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", message_text, re.I)
    score = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)", message_text)
    if not match or (score is None and score_override is None):
        return None

    if score_override is not None:
        score_a, score_b = score_override
    else:
        score_a, score_b = int(score.group(1)), int(score.group(2))
    if not (0 <= score_a <= 99 and 0 <= score_b <= 99):
        return None

    # The visible result card must contain the same ten K/A/D rows. We compare
    # a multiset because the modal uses starting CT/T while the card may show
    # the teams after a side swap.
    card_slots = parse_card_roster_slots(message_text)
    if card_slots is None and not visual_verified:
        return None
    card_kad = Counter(
        (player["kills"], player["assists"], player["deaths"])
        for team in (card_slots or {}).values() for player in team
    )
    modal_kad = Counter(
        (player["kills"], player["assists"], player["deaths"])
        for player in [*parsed["CT"], *parsed["T"]]
    )
    # A missing player is displayed as 0/0/0 in the modal but registered as
    # 0/0/13. Normalize the card the same way for comparison.
    if card_slots is not None:
        normalized_card_kad = Counter()
        for kad, count in card_kad.items():
            normalized_card_kad[(0, 0, 13) if kad == (0, 0, 0) else kad] += count
        if normalized_card_kad != modal_kad:
            return None

    return {
        "is_match_result": True,
        "match_id": int(match.group(1)),
        "score_a": score_a,
        "score_b": score_b,
        # Modal groups are authoritative starting sides. Store them directly
        # as A=CT and B=T so format_registration cannot invert them.
        "ct_team": "A",
        "team_a": parsed["CT"],
        "team_b": parsed["T"],
        "overall_confidence": 1.0,
        "notes": "ID и стартовые стороны взяты из окна «Получить игроков»; результат сверен с карточкой.",
    }


def result_from_visual_audit(
    message_text: str,
    modal_text: str,
    audit: dict,
) -> Optional[dict]:
    """Accept a review card only when its modal exactly matches the screenshot."""
    parsed = parse_players_modal(modal_text)
    match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", message_text, re.I)
    if parsed is None or match is None or not audit.get("is_scoreboard"):
        return None

    try:
        confidence = float(audit.get("overall_confidence", 0) or 0)
        score_left = int(audit["score_left"])
        score_right = int(audit["score_right"])
        left_players = audit["left_players"]
        right_players = audit["right_players"]
    except (KeyError, TypeError, ValueError):
        return None

    if (
        confidence < 0.90
        or not (0 <= score_left <= 99 and 0 <= score_right <= 99)
        or len(left_players) != 5
        or len(right_players) != 5
    ):
        return None

    def nickname_key(value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value)).casefold()
        return "".join(character for character in text if character.isalnum())

    def nickname_score(first: object, second: object) -> float:
        left = nickname_key(first)
        right = nickname_key(second)
        if not left or not right or left.isdigit() or right.isdigit():
            return 0.0
        if left == right:
            return 1.0
        if min(len(left), len(right)) >= 3 and (left in right or right in left):
            return 0.92 + 0.08 * min(len(left), len(right)) / max(len(left), len(right))
        return SequenceMatcher(None, left, right).ratio()

    def kad(player: dict) -> Optional[tuple[int, int, int]]:
        try:
            values = (
                int(player["kills"]),
                int(player["assists"]),
                int(player["deaths"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if any(value < 0 or value > 100 for value in values):
            return None
        return (0, 0, 13) if values == (0, 0, 0) else values

    if any(kad(player) is None for player in [*left_players, *right_players]):
        return None

    def best_alignment(
        modal_players: list[dict], visual_players: list[dict]
    ) -> tuple[tuple[int, ...], float, float]:
        best_order: tuple[int, ...] = tuple(range(5))
        best_average = -1.0
        best_minimum = -1.0
        for order in permutations(range(5)):
            scores: list[float] = []
            for modal_player, visual_index in zip(modal_players, order):
                visual_player = visual_players[visual_index]
                name_match = nickname_similarity(
                    modal_player.get("nickname", ""),
                    visual_player.get("nickname", ""),
                )
                # Exact K/A/D is useful for mentions or decorated nicknames,
                # but a name match remains authoritative when modal stats are
                # one round stale.
                stat_match = 0.88 if kad(modal_player) == kad(visual_player) else 0.0
                scores.append(max(name_match, stat_match))
            average = sum(scores) / 5
            minimum = min(scores)
            if (average, minimum) > (best_average, best_minimum):
                best_order = tuple(order)
                best_average = average
                best_minimum = minimum
        return best_order, best_average, best_minimum

    # Current review cards contain only `#ID nickname` roster lines, while
    # «Получить игроков» contains the same IDs split into the authoritative
    # starting CT/T groups and zeroed statistics.  Enrich those IDs with card
    # nicknames, then take score and K/A/D exclusively from the screenshot.
    card_rosters = parse_card_roster_identities(message_text)
    if card_rosters is not None:
        card_a = card_rosters["team_a"]
        card_b = card_rosters["team_b"]
        ids_a = {int(player["id"]) for player in card_a}
        ids_b = {int(player["id"]) for player in card_b}
        ids_ct = {int(player["id"]) for player in parsed["CT"]}
        ids_t = {int(player["id"]) for player in parsed["T"]}

        if ids_a == ids_ct and ids_b == ids_t:
            ct_team = "A"
        elif ids_a == ids_t and ids_b == ids_ct:
            ct_team = "B"
        else:
            direct_ids = len(ids_a & ids_ct) + len(ids_b & ids_t)
            swapped_ids = len(ids_a & ids_t) + len(ids_b & ids_ct)
            if max(direct_ids, swapped_ids) < 8 or abs(direct_ids - swapped_ids) < 2:
                log.error(
                    "Матч #%s: ID карточки неоднозначно сопоставлены с CT/T "
                    "(direct=%s swapped=%s).",
                    match.group(1), direct_ids, swapped_ids,
                )
                return None
            ct_team = "A" if direct_ids > swapped_ids else "B"

        direct_a = best_alignment(card_a, left_players)
        direct_b = best_alignment(card_b, right_players)
        swapped_a = best_alignment(card_a, right_players)
        swapped_b = best_alignment(card_b, left_players)
        direct_names = direct_a[1] + direct_b[1]
        swapped_names = swapped_a[1] + swapped_b[1]

        if direct_names >= swapped_names:
            chosen_names, other_names = direct_names, swapped_names
            alignment_a, alignment_b = direct_a, direct_b
            visual_a, visual_b = left_players, right_players
            score_a, score_b = score_left, score_right
            visual_side_a = str(audit.get("side_left") or "").upper()
            visual_side_b = str(audit.get("side_right") or "").upper()
        else:
            chosen_names, other_names = swapped_names, direct_names
            alignment_a, alignment_b = swapped_a, swapped_b
            visual_a, visual_b = right_players, left_players
            score_a, score_b = score_right, score_left
            visual_side_a = str(audit.get("side_right") or "").upper()
            visual_side_b = str(audit.get("side_left") or "").upper()

        expected_side_a = "CT" if ct_team == "A" else "T"
        expected_side_b = "T" if ct_team == "A" else "CT"
        if (
            visual_side_a in {"CT", "T"}
            and visual_side_a != expected_side_a
        ) or (
            visual_side_b in {"CT", "T"}
            and visual_side_b != expected_side_b
        ):
            log.error(
                "Матч #%s: стороны скриншота %s/%s противоречат ID окна "
                "игроков (Team A=%s).",
                match.group(1), visual_side_a, visual_side_b, expected_side_a,
            )
            return None

        if (
            chosen_names / 2 < 0.78
            or min(alignment_a[2], alignment_b[2]) < 0.55
            or chosen_names - other_names < 0.08
        ):
            log.error(
                "Матч #%s: ники карточки неоднозначно сопоставлены со "
                "скриншотом (direct=%.3f swapped=%.3f).",
                match.group(1), direct_names, swapped_names,
            )
            return None

        def merge_card_team(
            card_players: list[dict],
            visual_players: list[dict],
            order: tuple[int, ...],
        ) -> list[dict]:
            merged: list[dict] = []
            for card_player, visual_index in zip(card_players, order):
                visual_player = visual_players[visual_index]
                kills = int(visual_player["kills"])
                assists = int(visual_player["assists"])
                deaths = int(visual_player["deaths"])
                if kills == 0 and assists == 0 and deaths == 0:
                    deaths = 13
                merged.append(
                    {
                        "id": int(card_player["id"]),
                        "nickname": str(
                            visual_player.get("nickname")
                            or card_player.get("nickname", "")
                        ),
                        "kills": kills,
                        "assists": assists,
                        "deaths": deaths,
                    }
                )
            return merged

        return {
            "is_match_result": True,
            "match_id": int(match.group(1)),
            "score_a": score_a,
            "score_b": score_b,
            "ct_team": ct_team,
            "team_a": merge_card_team(card_a, visual_a, alignment_a[0]),
            "team_b": merge_card_team(card_b, visual_b, alignment_b[0]),
            "overall_confidence": confidence,
            "notes": (
                "ID и стороны взяты из «Получить игроков»; ники — из "
                "карточки; счёт и K/A/D — только из исходного скриншота."
            ),
        }

    direct_ct = best_alignment(parsed["CT"], left_players)
    direct_t = best_alignment(parsed["T"], right_players)
    swapped_ct = best_alignment(parsed["CT"], right_players)
    swapped_t = best_alignment(parsed["T"], left_players)
    direct_score = direct_ct[1] + direct_t[1]
    swapped_score = swapped_ct[1] + swapped_t[1]

    if direct_score >= swapped_score:
        chosen_score, other_score = direct_score, swapped_score
        ct_alignment, t_alignment = direct_ct, direct_t
        visual_ct, visual_t = left_players, right_players
        score_a, score_b = score_left, score_right
    else:
        chosen_score, other_score = swapped_score, direct_score
        ct_alignment, t_alignment = swapped_ct, swapped_t
        visual_ct, visual_t = right_players, left_players
        score_a, score_b = score_right, score_left

    if (
        chosen_score / 2 < 0.78
        or min(ct_alignment[2], t_alignment[2]) < 0.55
        or chosen_score - other_score < 0.08
    ):
        log.error(
            "Матч #%s: не удалось однозначно сопоставить игроков скриншота с окном игроков (direct=%.3f swapped=%.3f).",
            match.group(1),
            direct_score,
            swapped_score,
        )
        return None

    def merge_audited_stats(
        modal_players: list[dict],
        visual_players: list[dict],
        order: tuple[int, ...],
    ) -> list[dict]:
        merged: list[dict] = []
        for modal_player, visual_index in zip(modal_players, order):
            visual_player = visual_players[visual_index]
            merged.append(
                {
                    "id": int(modal_player["id"]),
                    "nickname": str(visual_player.get("nickname") or modal_player["nickname"]),
                    "kills": int(visual_player["kills"]),
                    "assists": int(visual_player["assists"]),
                    "deaths": int(visual_player["deaths"]),
                }
            )
        return merged

    audited_ct = merge_audited_stats(parsed["CT"], visual_ct, ct_alignment[0])
    audited_t = merge_audited_stats(parsed["T"], visual_t, t_alignment[0])

    return {
        "is_match_result": True,
        "match_id": int(match.group(1)),
        "score_a": score_a,
        "score_b": score_b,
        "ct_team": "A",
        "team_a": audited_ct,
        "team_b": audited_t,
        "overall_confidence": confidence,
        "notes": "ID взяты из окна игроков; счёт и K/A/D взяты только из исходного скриншота и сопоставлены по никам.",
    }


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
            values = re.findall(r"(?:^|\n|[@•]\s*)(\d{2,5})\s*\|", area, re.M)
        for value in values:
            player_id = int(value)
            if match_id is not None and player_id == int(match_id):
                continue
            if player_id not in ids:
                ids.append(player_id)
        if len(ids) == 10:
            return ids
    return []


def explicit_ct_team_from_card(message_text: str) -> Optional[str]:
    """Trust explicit Team A/B CT/T labels in review cards."""
    header_a = re.search(r"Команда\s*A[^\n]*", message_text, re.I)
    header_b = re.search(r"Команда\s*B[^\n]*", message_text, re.I)
    if not header_a or not header_b:
        return None
    side_a = re.search(r"\b(CT|T)\b", header_a.group(0), re.I)
    side_b = re.search(r"\b(CT|T)\b", header_b.group(0), re.I)
    if side_a and side_a.group(1).upper() == "CT":
        return "A"
    if side_b and side_b.group(1).upper() == "CT":
        return "B"
    return None


def parse_card_roster_slots(message_text: str) -> Optional[dict[str, list[dict]]]:
    """Read ten roster slots and K/A/D even when a slot is a long mention."""
    header_a = re.search(r"Команда\s*A[^\n]*", message_text, re.I)
    header_b = re.search(r"Команда\s*B[^\n]*", message_text, re.I)
    if not header_a or not header_b or header_b.start() <= header_a.start():
        return None

    line_pattern = re.compile(
        r"^\s*[•·-]?\s*(.*?)\s*[—–]\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
        re.M,
    )

    def parse_section(section: str) -> list[dict]:
        slots: list[dict] = []
        for found in line_pattern.finditer(section):
            label = found.group(1).strip()
            short_id_match = re.search(r"(?<!\d)#\s*(\d{1,5})(?!\d)", label)
            short_id = int(short_id_match.group(1)) if short_id_match else None
            nickname = re.sub(r"<@!?\d{15,22}>", "", label)
            nickname = re.sub(r"(?<!\d)#\s*\d{1,5}(?!\d)", "", nickname)
            nickname = nickname.strip(" @|`*_.,")
            nickname = re.sub(r"^[^\w\[({]+", "", nickname, flags=re.UNICODE)
            nickname = strip_leading_clan_tags(nickname)
            slots.append({
                "id": short_id,
                "nickname": nickname,
                "kills": int(found.group(2)),
                "assists": int(found.group(3)),
                "deaths": int(found.group(4)),
            })
            if len(slots) == 5:
                break
        return slots

    team_a = parse_section(message_text[header_a.end():header_b.start()])
    team_b = parse_section(message_text[header_b.end():])
    if len(team_a) != 5 or len(team_b) != 5:
        return None
    return {"team_a": team_a, "team_b": team_b}


def parse_card_roster_identities(
    message_text: str,
) -> Optional[dict[str, list[dict]]]:
    """Read review-card rosters that contain only `#ID nickname` lines.

    New review cards no longer print K/A/D in their text.  The IDs and names
    still identify the two card teams; the actual score and statistics must
    be read from the original scoreboard image.
    """
    header_a = re.search(r"Команда\s*A[^\n]*", message_text, re.I)
    header_b = re.search(r"Команда\s*B[^\n]*", message_text, re.I)
    if not header_a or not header_b or header_b.start() <= header_a.start():
        return None

    def parse_section(section: str) -> list[dict]:
        players: list[dict] = []
        for raw_line in section.splitlines():
            line = raw_line.strip().strip("`*_")
            found = re.match(
                r"^[•·-]?\s*@?\s*#\s*(\d{1,5})\s*(?:\|\s*)?(.+?)\s*$",
                line,
            )
            if not found:
                continue
            nickname = re.sub(
                r"\s*[—–-]\s*\d+\s*/\s*\d+\s*/\s*\d+\s*$",
                "",
                found.group(2),
            ).strip(" @|`*_.,")
            if not nickname:
                continue
            players.append(
                {
                    "id": int(found.group(1)),
                    "nickname": strip_leading_clan_tags(nickname),
                    "kills": 0,
                    "assists": 0,
                    "deaths": 0,
                }
            )
            if len(players) == 5:
                break
        return players

    team_a = parse_section(message_text[header_a.end():header_b.start()])
    team_b = parse_section(message_text[header_b.end():])
    if len(team_a) != 5 or len(team_b) != 5:
        return None
    all_ids = [player["id"] for player in [*team_a, *team_b]]
    if len(set(all_ids)) != 10:
        return None
    return {"team_a": team_a, "team_b": team_b}


def result_from_card_and_visual_audit(
    message_text: str,
    audit: dict,
) -> Optional[dict]:
    """Build a result from card IDs/nicks and the original scoreboard only."""
    rosters = parse_card_roster_identities(message_text)
    match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", message_text, re.I)
    if rosters is None or match is None or not audit.get("is_scoreboard"):
        return None

    try:
        confidence = float(audit.get("overall_confidence", 0) or 0)
        score_left = int(audit["score_left"])
        score_right = int(audit["score_right"])
        left_players = list(audit["left_players"])
        right_players = list(audit["right_players"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        confidence < 0.90
        or not (0 <= score_left <= 99 and 0 <= score_right <= 99)
        or not (1 <= len(left_players) <= 5)
        or not (1 <= len(right_players) <= 5)
    ):
        return None

    def valid_visual_player(player: dict) -> bool:
        try:
            values = [int(player[key]) for key in ("kills", "assists", "deaths")]
        except (KeyError, TypeError, ValueError):
            return False
        return all(0 <= value <= 100 for value in values)

    if not all(valid_visual_player(player) for player in [*left_players, *right_players]):
        return None

    def best_alignment(
        card_players: list[dict], visual_players: list[dict]
    ) -> tuple[tuple[Optional[int], ...], int, float, float]:
        best_order: tuple[Optional[int], ...] = tuple([None] * 5)
        best_count = 0
        best_average = -1.0
        best_minimum = -1.0
        # Match only reliable nickname pairs. A card player with no reliable
        # row (missing from the screenshot or shown under a wrong nickname)
        # remains unmatched and is registered as 0/0/13. Unmatched visual
        # rows are ignored instead of donating their stats to another ID.
        from itertools import combinations

        max_pairs = min(5, len(visual_players))
        for pair_count in range(max_pairs, 0, -1):
            for card_indices in combinations(range(5), pair_count):
                for visual_indices in permutations(range(len(visual_players)), pair_count):
                    scores = [
                        nickname_similarity(
                            card_players[card_index].get("nickname", ""),
                            visual_players[visual_index].get("nickname", ""),
                        )
                        for card_index, visual_index in zip(card_indices, visual_indices)
                    ]
                    if any(score < 0.72 for score in scores):
                        continue
                    average = sum(scores) / pair_count
                    minimum = min(scores)
                    metric = (pair_count, average, minimum)
                    if metric > (best_count, best_average, best_minimum):
                        mapping: list[Optional[int]] = [None] * 5
                        for card_index, visual_index in zip(card_indices, visual_indices):
                            mapping[card_index] = visual_index
                        best_order = tuple(mapping)
                        best_count = pair_count
                        best_average = average
                        best_minimum = minimum
            if best_count == pair_count:
                break
        return best_order, best_count, best_average, best_minimum

    card_a = rosters["team_a"]
    card_b = rosters["team_b"]
    direct_a = best_alignment(card_a, left_players)
    direct_b = best_alignment(card_b, right_players)
    swapped_a = best_alignment(card_a, right_players)
    swapped_b = best_alignment(card_b, left_players)
    direct_count = direct_a[1] + direct_b[1]
    swapped_count = swapped_a[1] + swapped_b[1]
    direct_names = direct_a[2] + direct_b[2]
    swapped_names = swapped_a[2] + swapped_b[2]

    if (direct_count, direct_names) >= (swapped_count, swapped_names):
        chosen_count, other_count = direct_count, swapped_count
        chosen_names, other_names = direct_names, swapped_names
        alignment_a, alignment_b = direct_a, direct_b
        visual_a, visual_b = left_players, right_players
        score_a, score_b = score_left, score_right
        side_a = str(audit.get("side_left") or "").upper()
    else:
        chosen_count, other_count = swapped_count, direct_count
        chosen_names, other_names = swapped_names, direct_names
        alignment_a, alignment_b = swapped_a, swapped_b
        visual_a, visual_b = right_players, left_players
        score_a, score_b = score_right, score_left
        side_a = str(audit.get("side_right") or "").upper()

    if (
        chosen_count < 6
        or min(alignment_a[1], alignment_b[1]) < 1
        or min(alignment_a[3], alignment_b[3]) < 0.72
        or (
            chosen_count == other_count
            and chosen_names - other_names < 0.08
        )
        or side_a not in {"CT", "T"}
    ):
        log.error(
            "Матч #%s: карточка неоднозначно сопоставлена с исходным табло "
            "(direct=%s/%.3f swapped=%s/%.3f side_a=%s).",
            match.group(1), direct_count, direct_names,
            swapped_count, swapped_names, side_a or "?",
        )
        return None

    def merge_team(
        card_players: list[dict],
        visual_players: list[dict],
        order: tuple[Optional[int], ...],
    ) -> list[dict]:
        merged: list[dict] = []
        for card_player, visual_index in zip(card_players, order):
            if visual_index is None:
                merged.append(
                    {
                        "id": int(card_player["id"]),
                        "nickname": str(card_player.get("nickname", "")),
                        "kills": 0,
                        "assists": 0,
                        "deaths": 13,
                    }
                )
                continue
            visual_player = visual_players[visual_index]
            kills = int(visual_player["kills"])
            assists = int(visual_player["assists"])
            deaths = int(visual_player["deaths"])
            if kills == 0 and assists == 0 and deaths == 0:
                deaths = 13
            merged.append(
                {
                    # The card itself is authoritative for registration IDs.
                    "id": int(card_player["id"]),
                    "nickname": str(
                        visual_player.get("nickname")
                        or card_player.get("nickname", "")
                    ),
                    "kills": kills,
                    "assists": assists,
                    "deaths": deaths,
                }
            )
        return merged

    return {
        "is_match_result": True,
        "match_id": int(match.group(1)),
        "score_a": score_a,
        "score_b": score_b,
        "ct_team": "A" if side_a == "CT" else "B",
        "team_a": merge_team(card_a, visual_a, alignment_a[0]),
        "team_b": merge_team(card_b, visual_b, alignment_b[0]),
        "overall_confidence": confidence,
        "notes": (
            "ID и ники взяты из карточки; счёт, стороны и K/A/D — только "
            "из исходного скриншота. Отсутствующий на табло игрок получает "
            "0/0/13. «Получить игроков» не использовалось."
        ),
    }


def result_from_review_card_and_modal(
    message_text: str,
    modal_text: str,
    visual_audit: Optional[dict] = None,
) -> Optional[dict]:
    """Use short IDs from `Получить игроков`; fuzzy-match names and absent rows."""
    modal = parse_players_modal(modal_text)
    slots = parse_card_roster_slots(message_text)
    match = re.search(r"Результат\s+матча\s*#\s*(\d+)", message_text, re.I)
    headers = [re.search(rf"Команда\s*{x}[^\n]*", message_text, re.I) for x in "AB"]
    if modal is None or slots is None or match is None or not all(headers):
        return None

    def score(header: str) -> Optional[int]:
        found = re.search(r"(?:CT|T)?\s*[-–—:|·]\s*(\d+)\s*[-–—:|·]\s*[KК][/\\][AА][/\\][CDСД]", header, re.I)
        return int(found.group(1)) if found else None

    score_a, score_b = score(headers[0].group(0)), score(headers[1].group(0))
    if score_a is None or score_b is None:
        found = re.search(r"Распознано\s+со\s+скриншота\s*:\s*(\d+)\s*[-:]\s*(\d+)", message_text, re.I)
        if not found:
            return None
        score_a, score_b = map(int, found.groups())

    def kad(player: dict) -> tuple[int, int, int]:
        value = tuple(int(player.get(key, 0)) for key in ("kills", "assists", "deaths"))
        return (0, 0, 13) if value in {(0, 0, 0), (0, 0, 13)} else value

    ids_a = {p["id"] for p in slots["team_a"] if p.get("id")}
    ids_b = {p["id"] for p in slots["team_b"] if p.get("id")}
    ids_ct = {p["id"] for p in modal["CT"]}
    ids_t = {p["id"] for p in modal["T"]}
    direct = len(ids_a & ids_ct) + len(ids_b & ids_t)
    swapped = len(ids_a & ids_t) + len(ids_b & ids_ct)
    stated = re.search(r"(?:команда\s*)?A\s+начинала\s+за\s+(CT|T)\b", message_text, re.I)
    if stated:
        side_a = stated.group(1).upper()
    elif direct != swapped:
        side_a = "CT" if direct > swapped else "T"
    else:
        return None
    side_b = "T" if side_a == "CT" else "CT"

    visual_players: list[dict] = []
    if visual_audit and visual_audit.get("is_scoreboard"):
        visual_players = [
            *list(visual_audit.get("left_players", [])),
            *list(visual_audit.get("right_players", [])),
        ]

    def visual_stats_for_nickname(nickname: str) -> Optional[tuple[int, int, int]]:
        if not nickname or not visual_players:
            return None
        ranked = sorted(
            (
                (nickname_similarity(nickname, player.get("nickname", "")), player)
                for player in visual_players
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 0.72:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            return None
        return kad(ranked[0][1])

    def assign(card: list[dict], helper: list[dict]) -> Optional[list[dict]]:
        assigned: list[Optional[int]] = [None] * 5
        unused = {int(p["id"]) for p in helper}
        by_id = {int(p["id"]): p for p in helper}
        for i, player in enumerate(card):
            if player.get("id") is not None:
                player_id = int(player["id"])
                # IDs rendered inside Discord display names can be stale.
                # Trust them only when that ID is present in «Получить игроков».
                if player_id in unused:
                    assigned[i] = player_id
                    unused.remove(player_id)
        # Named helper formats: versus also matches версус/versustop/111versus.
        for i, player in enumerate(card):
            if assigned[i] is not None:
                continue
            candidates = [pid for pid in unused if by_id[pid].get("nickname") and nicknames_match(player.get("nickname", ""), by_id[pid]["nickname"])]
            if len(candidates) == 1:
                assigned[i] = candidates[0]; unused.remove(candidates[0])
        # The helper preserves roster order; then use elimination.
        for i, helper_player in enumerate(helper):
            pid = int(helper_player["id"])
            if assigned[i] is None and pid in unused:
                assigned[i] = pid; unused.remove(pid)
        missing = [i for i, pid in enumerate(assigned) if pid is None]
        if len(missing) == len(unused) == 1:
            assigned[missing[0]] = unused.pop()
        if any(pid is None for pid in assigned) or unused:
            return None
        output = []
        for player, pid in zip(card, assigned):
            helper_player = by_id[int(pid)]
            helper_raw = (
                int(helper_player.get("kills", 0) or 0),
                int(helper_player.get("assists", 0) or 0),
                int(helper_player.get("deaths", 0) or 0),
            )
            # Non-zero statistics from «Получить игроков» are already known
            # and must not be read again from the screenshot.
            if helper_raw != (0, 0, 0):
                kills, assists, deaths = helper_raw
            else:
                # Only a 0/0/0 row needs recovery. Find its cleaned nickname
                # on the final scoreboard (tags such as CLION/OLD are ignored).
                recovered = visual_stats_for_nickname(str(player.get("nickname", "")))
                card_stats = kad(player)
                if recovered is not None and recovered != (0, 0, 13):
                    kills, assists, deaths = recovered
                elif card_stats != (0, 0, 13):
                    kills, assists, deaths = card_stats
                else:
                    kills, assists, deaths = (0, 0, 13)
            output.append({"id": int(pid), "nickname": player.get("nickname", ""), "kills": kills, "assists": assists, "deaths": deaths, "confidence": 1.0})
        return output

    team_a = assign(slots["team_a"], modal[side_a])
    team_b = assign(slots["team_b"], modal[side_b])
    if team_a is None or team_b is None:
        return None
    if len({p["id"] for p in [*team_a, *team_b]}) != 10:
        return None
    return {"is_match_result": True, "match_id": int(match.group(1)), "score_a": score_a, "score_b": score_b, "ct_team": "A" if side_a == "CT" else "B", "team_a": team_a, "team_b": team_b, "overall_confidence": 1.0, "notes": "ID и ненулевая статистика взяты из «Получить игроков»; только строки 0/0/0 восстановлены по очищенному нику на скриншоте или зарегистрированы 0/0/13."}


def reconcile_numeric_mentions(result: dict, message_text: str) -> bool:
    """Match long numeric mentions to scoreboard players by exact K/A/D."""
    slots_by_team = parse_card_roster_slots(message_text)
    if slots_by_team is None:
        return True

    for team_key in ("team_a", "team_b"):
        slots = slots_by_team[team_key]
        candidates = result.get(team_key, [])
        if len(candidates) != 5:
            return False
        unused = set(range(5))
        reconciled: list[dict] = []

        for slot in slots:
            exact = [
                index for index in unused
                if candidates[index].get("kills") == slot["kills"]
                and candidates[index].get("assists") == slot["assists"]
                and candidates[index].get("deaths") == slot["deaths"]
            ]
            chosen: Optional[int] = None
            recovered_id = slot["id"]

            if recovered_id is not None:
                same_id = [
                    index for index in unused
                    if candidates[index].get("id") == recovered_id
                ]
                chosen = same_id[0] if same_id else (exact[0] if len(exact) == 1 else None)
            elif len(exact) == 1:
                chosen = exact[0]
                candidate = candidates[chosen]
                nickname_id = re.search(
                    r"(?:^|\[|#)(\d{2,5})(?:\]|\s|\|)",
                    str(candidate.get("nickname", "")),
                )
                recovered_id = int(nickname_id.group(1)) if nickname_id else candidate.get("id")

            if chosen is None or not isinstance(recovered_id, int) or recovered_id <= 0:
                return False

            unused.remove(chosen)
            player = dict(candidates[chosen])
            player.update({
                "id": recovered_id,
                "kills": slot["kills"],
                "assists": slot["assists"],
                "deaths": slot["deaths"],
            })
            if slot["nickname"] and not player.get("nickname"):
                player["nickname"] = slot["nickname"]
            reconciled.append(player)

        result[team_key] = reconciled
    return True


def parse_complete_card(message_text: str) -> Optional[dict]:
    """Read exact IDs/KAD from any complete result card; AI only selects CT/T."""
    match = re.search(r"Результат\s+матча\s*#\s*(\d+)", message_text, re.I)
    header_a = re.search(r"Команда\s*A[^\n]*", message_text, re.I)
    header_b = re.search(r"Команда\s*B[^\n]*", message_text, re.I)
    if not match or not header_a or not header_b or header_b.start() <= header_a.start():
        return None

    def header_score(header: str) -> Optional[int]:
        found = re.search(
            r"(?:CT|T)?\s*[·•:|\-–—]\s*(\d+)\s*[·•:|\-–—]\s*"
            r"[KК][/\\][AА][/\\][CDСД]",
            header,
            re.I,
        )
        return int(found.group(1)) if found else None

    score_a = header_score(header_a.group(0))
    score_b = header_score(header_b.group(0))
    if score_a is None or score_b is None:
        recognized = re.search(
            r"Распознано\s+со\s+скриншота\s*:\s*(\d+)\s*[-:]\s*(\d+)",
            message_text,
            re.I,
        )
        if not recognized:
            return None
        score_a, score_b = int(recognized.group(1)), int(recognized.group(2))

    player_pattern = re.compile(
        r"#\s*(\d{1,5})\s*(?:\|\s*)?([^\n—–]+?)\s*[—–-]\s*"
        r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
        re.I,
    )

    def parse_team(section: str) -> list[dict]:
        team: list[dict] = []
        for found in player_pattern.finditer(section):
            kills, assists, deaths = map(int, found.group(3, 4, 5))
            if kills == 0 and assists == 0 and deaths == 0:
                deaths = 13
            team.append(
                {
                    "id": int(found.group(1)),
                    "nickname": found.group(2).strip(" `*_.,"),
                    "kills": kills,
                    "assists": assists,
                    "deaths": deaths,
                    "confidence": 0.99,
                }
            )
            if len(team) == 5:
                break
        return team

    team_a = parse_team(message_text[header_a.end():header_b.start()])
    team_b = parse_team(message_text[header_b.end():])
    if len(team_a) != 5 or len(team_b) != 5:
        return None

    ct_team = explicit_ct_team_from_card(message_text)

    return {
        "is_match_result": True,
        "match_id": int(match.group(1)),
        "score_a": score_a,
        "score_b": score_b,
        "ct_team": ct_team,
        "team_a": team_a,
        "team_b": team_b,
        "overall_confidence": 0.99,
        "notes": "ID, команды и K/A/D взяты напрямую из карточки.",
    }


def is_review_result_card(message_text: str) -> bool:
    """Detect only an explicit review status on the current result card.

    Do not trigger on an incidental phrase like `на проверку` in instructions,
    history, replies, or other embedded text.
    """
    # The title starts with a shield emoji in real Discord embeds, therefore
    # it must not be anchored to the beginning of a plain-text line.
    explicit_title = re.search(
        r"Результат\s+матча\s*#\s*\d+[^\n]{0,160}?на\s+проверку\b",
        message_text,
        re.I,
    )
    if explicit_title:
        return True

    lines = [line.strip() for line in message_text.splitlines() if line.strip()]
    for line in lines[:12]:
        if re.fullmatch(
            r"(?:⚠️\s*)?(?:статус\s*[:—–-]\s*)?на\s+проверку[.!]?",
            line,
            re.I,
        ):
            return True
    return False


def readable_score_from_context(message_text: str) -> Optional[tuple[int, int]]:
    """Extract the best already-readable A:B score for diagnostic logs."""
    recognized = re.search(
        r"Распознано\s+со\s+скриншота\s*:\s*(\d{1,2})\s*[-:]\s*(\d{1,2})",
        message_text,
        re.I,
    )
    if recognized:
        return int(recognized.group(1)), int(recognized.group(2))
    headers = [re.search(rf"Команда\s*{team}[^\n]*", message_text, re.I) for team in "AB"]
    values: list[int] = []
    for header in headers:
        if not header:
            return None
        found = re.search(
            r"(?:CT|T)?\s*[-–—:|·]\s*(\d{1,2})\s*[-–—:|·]\s*[KК][/\\][AА][/\\][CDСД]",
            header.group(0),
            re.I,
        )
        if not found:
            return None
        values.append(int(found.group(1)))
    return values[0], values[1]


def full_match_diagnostics(
    message_text: str,
    modal_text: Optional[str] = None,
    result: Optional[dict] = None,
) -> str:
    """Render score and every available player row for Railway error logs."""
    lines: list[str] = []
    score = readable_score_from_context(message_text)
    if result and result.get("score_a") is not None and result.get("score_b") is not None:
        lines.append(f"СЧЁТ A:B = {result['score_a']}:{result['score_b']}")
    elif score:
        lines.append(f"СЧЁТ A:B = {score[0]}:{score[1]}")
    else:
        lines.append("СЧЁТ A:B = НЕ ПРОЧИТАН")

    def add_players(title: str, players: list[dict]) -> None:
        lines.append(title)
        if not players:
            lines.append("  игроков разобрать не удалось")
            return
        for position, player in enumerate(players, 1):
            kills = int(player.get("kills", 0) or 0)
            assists = int(player.get("assists", 0) or 0)
            deaths = int(player.get("deaths", 0) or 0)
            if kills == assists == deaths == 0:
                deaths = 13
            player_id = player.get("id")
            nickname = str(player.get("nickname", "") or "неизвестный ник")
            lines.append(
                f"  {position}. ID={player_id if player_id is not None else 'длинный/не найден'} "
                f"ник={nickname} K/A/D={kills}/{assists}/{deaths}"
            )

    if result:
        add_players("КОМАНДА A:", list(result.get("team_a", [])))
        add_players("КОМАНДА B:", list(result.get("team_b", [])))
    else:
        slots = parse_card_roster_slots(message_text)
        if slots:
            add_players("КОМАНДА A ИЗ КАРТОЧКИ:", slots["team_a"])
            add_players("КОМАНДА B ИЗ КАРТОЧКИ:", slots["team_b"])
        else:
            identities = parse_card_roster_identities(message_text)

            def add_identities(title: str, players: list[dict]) -> None:
                lines.append(title)
                if not players:
                    lines.append("  игроков разобрать не удалось")
                    return
                for position, player in enumerate(players, 1):
                    lines.append(
                        f"  {position}. ID={player['id']} "
                        f"ник={player.get('nickname', '')} K/A/D=ИЗ СКРИНШОТА"
                    )

            add_identities(
                "КОМАНДА A ИЗ КАРТОЧКИ:",
                identities["team_a"] if identities else [],
            )
            add_identities(
                "КОМАНДА B ИЗ КАРТОЧКИ:",
                identities["team_b"] if identities else [],
            )

    if modal_text:
        modal = parse_players_modal(modal_text)
        add_players("ПОЛУЧИТЬ ИГРОКОВ — CT:", modal["CT"] if modal else [])
        add_players("ПОЛУЧИТЬ ИГРОКОВ — T:", modal["T"] if modal else [])
    return "\n".join(lines)


async def recognize_match(
    images: list[bytes],
    message_text: str = "",
    score_only: bool = False,
    visual_audit: bool = False,
) -> dict:
    if score_only and visual_audit:
        raise ValueError("score_only и visual_audit нельзя включать одновременно")
    card_result = None if (score_only or visual_audit) else parse_complete_card(message_text)
    if card_result is not None and card_result.get("ct_team") in ("A", "B"):
        log.info(
            "Матч #%s разобран напрямую без запроса к ИИ",
            card_result["match_id"],
        )
        return card_result

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
            "ct_team": {"type": ["string", "null"], "enum": ["A", "B", None]},
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
            "ct_team",
            "team_a",
            "team_b",
            "overall_confidence",
            "notes",
        ],
        "additionalProperties": False,
    }

    if visual_audit:
        visual_player_schema = {
            "type": "object",
            "properties": {
                "nickname": {"type": "string"},
                "kills": {"type": "integer", "minimum": 0, "maximum": 100},
                "assists": {"type": "integer", "minimum": 0, "maximum": 100},
                "deaths": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["nickname", "kills", "assists", "deaths"],
            "additionalProperties": False,
        }
        response_schema = {
            "type": "object",
            "properties": {
                "is_scoreboard": {"type": "boolean"},
                "score_left": {"type": ["integer", "null"], "minimum": 0, "maximum": 99},
                "score_right": {"type": ["integer", "null"], "minimum": 0, "maximum": 99},
                "side_left": {"type": ["string", "null"], "enum": ["CT", "T", None]},
                "side_right": {"type": ["string", "null"], "enum": ["CT", "T", None]},
                "left_players": {"type": "array", "items": visual_player_schema, "minItems": 1, "maxItems": 5},
                "right_players": {"type": "array", "items": visual_player_schema, "minItems": 1, "maxItems": 5},
                "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "notes": {"type": "string"},
            },
            "required": ["is_scoreboard", "score_left", "score_right", "side_left", "side_right", "left_players", "right_players", "overall_confidence", "notes"],
            "additionalProperties": False,
        }

    if visual_audit:
        prompt = """Strictly transcribe the attached STANDOFF 2 scoreboard from the pixels.
Copy the two large score numbers in visible LEFT-to-RIGHT order. Never add the current or next round: if the image displays 8 and 13, return 8 and 13, never 8 and 14.
Return side_left and side_right as CT or T. Transcribe every VISIBLE player per side, top to bottom. A side can contain from one to five visible rows when players are absent; never invent missing rows. The match may be accepted when at least six card players are reliably matched in total.
Russian columns У, П, С mean kills, assists, deaths. On the T/ATTACK side a MONEY column appears before У/П/С; ignore money. Ignore score/points and ping after deaths.
For nicknames, ignore the faded clan/tag prefix before the actual nickname. Examples: `[CLION] Zerro` and `CLION | Zerro` mean nickname `Zerro`; `[swean] Кредо` means nickname `Кредо`.
Do not infer, increment, normalize, or copy statistics from Discord text. Only the attached game screenshot is evidence.
Set confidence below 0.90 if any score or K/A/D digit is unclear. Return only valid JSON."""
    elif score_only:
        prompt = """Read ONLY the final score of this FACEIT/CS2 match from the attached result screenshot.
The Discord card text identifies Team A and Team B. Return score_a and score_b as rounds won by those exact teams, mapping the scoreboard sides to A/B by player nicknames when needed.
Ignore any helper template containing `<счёт A> <счёт B>`: those are placeholders, not a score.
Read the large final scoreboard/result score from the image. Typical valid results are 13:0 through overtime scores.
Set is_match_result=true when a final match scoreboard is visible. Return the visible match number when available, otherwise null.
For this score-only request return team_a=[] and team_b=[], ct_team=null. overall_confidence describes confidence in the two score numbers. Explain briefly in notes."""
    else:
        prompt = """You receive one or more screenshots of the SAME FACEIT/CS2 match result.
The Discord result card contains match number and two rosters: Team A and Team B, with numeric IDs like #37 and nicknames. The small CS2 scoreboard contains each nickname and columns K, A, D.
Build a registration result:
- match_id: number after 'Результ����т матча #'.
- Team A must always be returned in team_a; Team B in team_b.
- score_a and score_b are rounds won by Team A and Team B. The CS2 scoreboard may label sides ATTACK/DEFENSE or T/CT and teams can be on either side; map score to A/B by matching player nicknames.
- ct_team MUST be `A` when Team A is on the CT/DEFENSE side of the screenshot, or `B` when Team B is on CT/DEFENSE. Never assume Team A is CT. Determine it by matching roster nicknames and scores to the CT/DEFENSE half of the scoreboard.
- Cards titled 'на проверку' are valid match results and MUST be registered when match number, score and rosters can be recovered. These cards often already contain short # IDs and K/A/D next to every player; use those values directly even when the attached scoreboard is small or blurry.
- In review cards, strings like `@#64 | kanei — 8/2/12` mean registration id=64, nickname=kanei, kills=8, assists=2, deaths=12. The @ formatting does not turn the short # number into a Discord user ID.
- If a review card says a player was not found and shows 0/0/0, apply the required absent-row default 0/0/13 and still keep that player.
- For every roster player return the SHORT registration ID printed with # immediately before the nickname/mention. It is usually 2, 3 or 4 digits (for example #37, #539, #1639). Use the complete short # number. NEVER use a long Discord mention/user ID such as 1524375653149966517.
- NEVER invent positional IDs such as 1,2,3,4,5 or 5,4,3,2,1. Array position is not a player ID. If the card contains a line like `Нет статистики для игроков: #89, #124, ...`, those ten short # numbers are the roster IDs in displayed order and must be returned exactly.
- Some roster names are Discord mentions or contain only digits. A numeric-only mention is NOT the nickname. Identify that player by the K/A/D printed beside or below the roster entry, then match those K/A/D values to the unique scoreboard row and recover the real nickname from the scoreboard.
- Review cards can mix normal entries and raw mentions, for example `<@1277880356242067460> — 17/1/10`. The 17/1/10 belongs to that exact roster slot. Match it only against a scoreboard row on the same team/side with the same K/A/D.
- After matching a raw numeric mention to its scoreboard row, read the complete leading 2-, 3-, or 4-digit registration number immediately before the nickname (often displayed as `#89 Nick`, `[89] Nick`, or `89 | Nick`). Use that number as id. Never use the row number 1-5 and never take digits from the long Discord mention.
- Explicit side labels in the card are authoritative. `Команда A - T` and `Команда B - CT` means ct_team=B; `Команда A - CT` means ct_team=A. Never reverse explicit labels based on assumptions.
- K/A/D printed in a review card is authoritative. Copy it exactly for every roster slot; use the image only to recover the nickname and short ID for numeric mentions.
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
        prompt += f"\nAuthoritative Discord card text:\n{message_text[:6000]}"

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
    result = json.loads(output_text)
    if score_only or visual_audit:
        return result
    # Любой полностью нулевой игрок регистрируется как отсутствующий 0/0/13.
    for team_key in ("team_a", "team_b"):
        for player in result.get(team_key, []):
            if (
                int(player.get("kills", 0) or 0) == 0
                and int(player.get("assists", 0) or 0) == 0
                and int(player.get("deaths", 0) or 0) == 0
            ):
                player["deaths"] = 13
    explicit_ct_team = explicit_ct_team_from_card(message_text)
    if explicit_ct_team in ("A", "B"):
        result["ct_team"] = explicit_ct_team
    if not reconcile_numeric_mentions(result, message_text):
        result["overall_confidence"] = 0.0
        result["notes"] = (
            "Не удалось однозначно сопоставить цифровые упоминания "
            "со строками таблицы по K/A/D; команда не будет отправлена."
        )
    if card_result is not None:
        ct_team = explicit_ct_team or result.get("ct_team")
        if ct_team not in ("A", "B"):
            card_result["overall_confidence"] = 0.0
            card_result["notes"] = "Не удалось надёжно определить сторону CT."
        else:
            card_result["ct_team"] = ct_team
            card_result["notes"] += f" CT определена как команда {ct_team}."
        return card_result
    return result


def format_registration(result: dict) -> str:
    ct_team = result.get("ct_team")
    if ct_team not in ("A", "B"):
        raise ValueError("Не определена команда, игравшая за CT")
    ct_players = result["team_a"] if ct_team == "A" else result["team_b"]
    t_players = result["team_b"] if ct_team == "A" else result["team_a"]
    lines = [
        f"=g {result['match_id']} {result['score_a']} {result['score_b']}",
        "",
        "CT",
    ]
    for player in ct_players:
        lines.append(
            f"{player['id']} {player['kills']} {player['assists']} {player['deaths']}"
        )
    lines.extend(["", "T"])
    for player in t_players:
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


async def send_processing_error_log(
    match_id: object,
    source_message: discord.Message,
    reason: str,
    diagnostics: str,
) -> None:
    """Send processing errors, score and all player stats to the Discord log channel."""
    if not LOG_CHANNEL_ID:
        return
    try:
        log_channel = client.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            log_channel = await client.fetch_channel(LOG_CHANNEL_ID)
        header = (
            f"❌ Ошибка регистрации игры #{match_id}\n"
            f"Причина: {reason[:500]}\n"
            f"Источник: <#{source_message.channel.id}>\n"
        )
        # Discord messages are limited to 2000 characters. Send the complete
        # diagnostics in ordered chunks so no player row is lost.
        chunks = [diagnostics[index:index + 1700] for index in range(0, len(diagnostics), 1700)] or ["Диагностика отсутствует"]
        for index, chunk in enumerate(chunks):
            prefix = header if index == 0 else f"❌ Игра #{match_id}, продолжение {index + 1}\n"
            await log_channel.send(f"{prefix}```text\n{chunk}\n```")
    except Exception:
        log.exception(
            "Не удалось отправить Discord-лог ошибки матча #%s в канал %s",
            match_id,
            LOG_CHANNEL_ID,
        )


def plain_message_text(message: discord.Message) -> str:
    chunks: list[str] = []
    for part in message_parts(message):
        content = getattr(part, "content", "")
        if content:
            chunks.append(str(content))
        for embed in getattr(part, "embeds", None) or []:
            if embed.title:
                chunks.append(str(embed.title))
            if embed.description:
                chunks.append(str(embed.description))
            for field in embed.fields:
                chunks.append(f"{field.name}\n{field.value}")
    return "\n".join(chunks)


async def delete_duplicate_match_cards(
    source_message: discord.Message,
    match_id: int,
    keep_current: bool = False,
) -> int:
    """Delete duplicate source cards for one match from this channel.

    Only image result cards with the exact `Результат матча #N` title are
    touched. Registration commands and confirmation messages are preserved.
    """
    candidates: dict[int, discord.Message] = {source_message.id: source_message}
    try:
        async for candidate in source_message.channel.history(limit=BACKFILL_LIMIT):
            candidates[candidate.id] = candidate
    except Exception:
        log.exception(
            "Не удалось просмотреть канал для удаления дублей матча #%s",
            match_id,
        )

    deleted = 0
    for candidate in candidates.values():
        if keep_current and candidate.id == source_message.id:
            continue
        if not image_urls(candidate):
            continue
        text = plain_message_text(candidate)
        found = re.search(r"Результат\s+матча\s*#\s*(\d+)", text, re.I)
        if not found or int(found.group(1)) != int(match_id):
            continue
        try:
            await candidate.delete()
            deleted += 1
        except discord.NotFound:
            pass
        except discord.Forbidden:
            log.warning(
                "Нет права удалить дубль сообщения %s матча #%s",
                candidate.id,
                match_id,
            )
        except Exception:
            log.exception(
                "Не удалось удалить дубль сообщения %s матча #%s",
                candidate.id,
                match_id,
            )
    return deleted


def is_registration_success_confirmation(message: discord.Message) -> bool:
    """Detect only `Готово — Матч #N закрыт со счётом X:Y` messages."""
    text = plain_message_text(message)
    has_ready_title = bool(
        re.search(r"(?:^|\n)\s*(?:✅\s*)?Готово\b", text, re.I)
    )
    has_closed_match = bool(
        re.search(
            r"Матч\s*#\s*\d+\s+закрыт\s+со\s+сч[её]том\s+"
            r"\d{1,2}\s*[:\-]\s*\d{1,2}",
            text,
            re.I,
        )
    )
    return has_ready_title and has_closed_match


async def delete_all_registration_confirmations(
    channel_ids: set[int],
) -> tuple[int, int, int]:
    """Delete all successful registration confirmations in configured channels."""
    deleted = 0
    scanned_channels = 0
    failed_channels = 0
    for channel_id in sorted(channel_ids):
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)
            scanned_channels += 1
            async for candidate in channel.history(limit=None):
                if not is_registration_success_confirmation(candidate):
                    continue
                try:
                    await candidate.delete()
                    deleted += 1
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    log.warning(
                        "Нет права удалить подтверждение %s в канале %s",
                        candidate.id,
                        channel_id,
                    )
                except Exception:
                    log.exception(
                        "Не удалось удалить подтверждение %s в канале %s",
                        candidate.id,
                        channel_id,
                    )
        except Exception:
            failed_channels += 1
            log.exception(
                "Не удалось очистить подтверждения в канале %s",
                channel_id,
            )
    return deleted, scanned_channels, failed_channels


async def wait_for_registration_confirmation(
    sent_registration: discord.Message,
    match_id: int,
) -> tuple[bool, str]:
    """Wait for the game bot's reply before deleting/counting the source card."""
    def check(candidate: discord.Message) -> bool:
        if candidate.channel.id != sent_registration.channel.id:
            return False
        if client.user and candidate.author.id == client.user.id:
            return False
        text = plain_message_text(candidate).lower()
        if "готово" not in text and "не вышло" not in text:
            return False
        reference = getattr(candidate, "reference", None)
        references_command = bool(
            reference and reference.message_id == sent_registration.id
        )
        names_match = f"#{match_id}" in text
        return references_command or names_match

    try:
        response = await client.wait_for(
            "message",
            check=check,
            timeout=REGISTRATION_CONFIRM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return False, "тайм-аут ожидания ответа регистрационного бота"

    response_text = plain_message_text(response)
    confirmed = "готово" in response_text.lower()
    if confirmed:
        # Remove the registration bot's visible `Готово` card after we have
        # read it. Failure responses are kept for diagnostics and retry.
        try:
            await response.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            log.warning(
                "Нет права удалить подтверждение регистрации матча #%s",
                match_id,
            )
        except Exception:
            log.exception(
                "Не удалось удалить подтверждение регистрации матча #%s",
                match_id,
            )
    return confirmed, response_text[:500]


async def process_upload(message: discord.Message, test_only: bool = False) -> None:
    urls = image_urls(message)
    if not urls:
        return

    context = await message_context(message)
    context_match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", context, re.I)
    reserved_match_id = int(context_match.group(1)) if context_match else None
    if reserved_match_id is not None and not test_only:
        async with processing_match_lock:
            if reserved_match_id in processing_match_ids:
                log.info(
                    "Матч #%s уже обрабатывается — повторная карточка пропущена",
                    reserved_match_id,
                )
                # Do not mark this duplicate as permanently inspected. If the
                # active copy fails, the next `старт` may retry this one.
                processed_message_ids.discard(message.id)
                return
            processing_match_ids.add(reserved_match_id)

    processing_completed = False
    async with message.channel.typing():
        try:
            # If this match was already confirmed earlier, no recognition or
            # second registration is needed. Remove every repeated result
            # card for it from the registration channel immediately.
            if (
                not test_only
                and
                reserved_match_id is not None
                and await registration_exists(reserved_match_id)
            ):
                deleted = await delete_duplicate_match_cards(
                    message,
                    reserved_match_id,
                )
                log.info(
                    "Матч #%s уже зарегистрирован; удалено карточек-дублей: %s",
                    reserved_match_id,
                    deleted,
                )
                processing_completed = True
                return

            # There are two independent registration formats:
            # 1) complete `внесён` cards already containing score and K/A/D;
            # 2) `на проверку` cards containing IDs/nicks plus a scoreboard.
            # A helper button may exist on both and must not choose the mode.
            has_players_button = find_get_players_button(message) is not None
            review_card = is_review_result_card(context)
            complete_card = parse_complete_card(context)
            score_hint = readable_score_from_context(context)
            log.info(
                "Матч #%s: complete_card=%s review_card=%s players_button=%s "
                "score_hint=%s | версия %s",
                reserved_match_id or "?",
                complete_card is not None,
                review_card,
                has_players_button,
                f"{score_hint[0]}:{score_hint[1]}" if score_hint else "не прочитан",
                BOT_VERSION,
            )
            modal_text: Optional[str] = None
            if complete_card is not None:
                # Complete cards keep their exact card K/A/D. AI is used only
                # when needed to identify which card team was CT/T.
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    raw_images = await asyncio.gather(
                        *(download_image(session, url) for url in urls[:4])
                    )
                result = await recognize_match(raw_images, context)
            elif review_card or has_players_button:
                card_rosters = parse_card_roster_identities(context)
                if card_rosters is not None:
                    # This format already contains all ten registration IDs.
                    # Do not click «Получить игроков»: read only score, sides
                    # and K/A/D from the original scoreboard screenshot.
                    timeout = aiohttp.ClientTimeout(total=30)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        raw_images = await asyncio.gather(
                            *(download_image(session, url) for url in urls[:4])
                        )
                    audit = await recognize_match(
                        raw_images,
                        visual_audit=True,
                    )
                    result = result_from_card_and_visual_audit(context, audit)
                    if result is None:
                        diagnostics = full_match_diagnostics(context)
                        log.error(
                            "Матч #%s: не удалось сопоставить ID/ники карточки "
                            "с исходным табло. score_hint=%s context=%r "
                            "audit=%r.\n%s",
                            reserved_match_id or "?",
                            f"{score_hint[0]}:{score_hint[1]}" if score_hint else "не прочитан",
                            context[:4000],
                            audit,
                            diagnostics,
                        )
                        await send_processing_error_log(
                            reserved_match_id or "?", message,
                            "Не удалось сопоставить карточку с исходным табло.",
                            diagnostics,
                        )
                        return
                else:
                    # Keep support for old review cards whose IDs/statistics
                    # can be recovered only through the helper button.
                    modal_text, _helper_image_urls = await get_players_response(message)
                    if not modal_text:
                        diagnostics = full_match_diagnostics(context)
                        log.error(
                            "Матч #%s: не удалось открыть/прочитать «Получить игроков». "
                            "Прочитанный счёт=%s. context=%r\n%s",
                            reserved_match_id or "?",
                            f"{score_hint[0]}:{score_hint[1]}" if score_hint else "не прочитан",
                            context[:4000],
                            diagnostics,
                        )
                        await send_processing_error_log(
                            reserved_match_id or "?", message,
                            "Не удалось открыть/прочитать «Получить игроков».",
                            diagnostics,
                        )
                        return
                    result = result_from_review_card_and_modal(context, modal_text)
                    if result is None:
                        diagnostics = full_match_diagnostics(context, modal_text)
                        await send_processing_error_log(
                            reserved_match_id or "?", message,
                            "Не удалось сопоставить старую карточку с «Получить игроков».",
                            diagnostics,
                        )
                        return
            else:
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
            returned_ids = [player.get("id") for player in returned_players]
            if len(expected_ids) == 10 and len(returned_ids) == 10:
                if set(returned_ids) != set(expected_ids):
                    diagnostics = full_match_diagnostics(context, modal_text if review_card else None, result)
                    log.error(
                        "Матч #%s пропущен: ID модели %s не совпали с карточкой %s\n%s",
                        result.get("match_id"),
                        returned_ids,
                        expected_ids,
                        diagnostics,
                    )
                    await send_processing_error_log(
                        result.get("match_id") or reserved_match_id or "?", message,
                        "ID результата не совпали с ID карточки.", diagnostics,
                    )
                    return
            positional_ids = [1, 2, 3, 4, 5, 5, 4, 3, 2, 1]
            if returned_ids == positional_ids or (
                len(returned_ids) == 10
                and all(isinstance(value, int) and 1 <= value <= 5 for value in returned_ids)
            ):
                diagnostics = full_match_diagnostics(context, modal_text if review_card else None, result)
                log.error(
                    "Матч #%s пропущен: модель выдумала позиционные ID %s\n%s",
                    result.get("match_id"),
                    returned_ids,
                    diagnostics,
                )
                await send_processing_error_log(
                    result.get("match_id") or reserved_match_id or "?", message,
                    "Получены выдуманные позиционные ID.", diagnostics,
                )
                return

            fatal = (
                not result.get("is_match_result")
                or result.get("match_id") is None
                or result.get("score_a") is None
                or result.get("score_b") is None
                or result.get("ct_team") not in ("A", "B")
                or len(result.get("team_a", [])) != 5
                or len(result.get("team_b", [])) != 5
            )
            if fatal:
                diagnostics = full_match_diagnostics(context, modal_text if review_card else None, result)
                log.error(
                    "Матч #%s: не удалось собрать структуру. Прочитанный счёт=%s:%s. result=%r Notes=%s\n%s",
                    result.get("match_id") or reserved_match_id or "?",
                    result.get("score_a"),
                    result.get("score_b"),
                    result,
                    result.get("notes", ""),
                    diagnostics,
                )
                await send_processing_error_log(
                    result.get("match_id") or reserved_match_id or "?", message,
                    "Не удалось собрать полную структуру матча.", diagnostics,
                )
                return

            confidence = float(result.get("overall_confidence", 0))
            if confidence < MIN_CONFIDENCE:
                diagnostics = full_match_diagnostics(context, modal_text if review_card else None, result)
                log.warning(
                    "Матч #%s распознан с низкой уверенностью %.2f. Прочитанный счёт=%s:%s. result=%r Notes=%s\n%s",
                    result.get("match_id") or reserved_match_id or "?",
                    confidence,
                    result.get("score_a"),
                    result.get("score_b"),
                    result,
                    result.get("notes", ""),
                    diagnostics,
                )
                await send_processing_error_log(
                    result.get("match_id") or reserved_match_id or "?", message,
                    f"Низкая уверенность распознавания: {confidence:.2f}.", diagnostics,
                )
                return

            all_players = [*result["team_a"], *result["team_b"]]
            player_ids = [player.get("id") for player in all_players]
            if (
                len(player_ids) != 10
                or any(not isinstance(value, int) or value <= 0 for value in player_ids)
                or len(set(player_ids)) != 10
            ):
                diagnostics = full_match_diagnostics(context, modal_text if review_card else None, result)
                log.error(
                    "Матч #%s не отправлен: недопустимые или повторяющиеся ID %s\n%s",
                    result.get("match_id"),
                    player_ids,
                    diagnostics,
                )
                await send_processing_error_log(
                    result.get("match_id") or reserved_match_id or "?", message,
                    "Недопустимые или повторяющиеся ID игроков.", diagnostics,
                )
                return

            command_text = format_registration(result)
            if test_only:
                # Forwarded cards outside registration channels are a safe
                # preview: return only the generated =g command. Do not save
                # stats, wait for confirmation or delete any source message.
                await message.channel.send(command_text)
                processing_completed = True
                return

            match_id = int(result["match_id"])
            if not await record_registration(match_id):
                deleted = await delete_duplicate_match_cards(message, match_id)
                log.info(
                    "Матч #%s уже зарегистрирован — удалено карточек-дублей: %s",
                    match_id,
                    deleted,
                )
                processing_completed = True
                return

            await asyncio.sleep(SEND_DELAY)
            sent_registration = await message.channel.send(command_text)
            confirmed, confirmation_text = await wait_for_registration_confirmation(
                sent_registration,
                match_id,
            )
            if not confirmed:
                await forget_registration(match_id)
                log.warning(
                    "Матч #%s не подтверждён; исходная карточка сохранена. Ответ: %s",
                    match_id,
                    confirmation_text,
                )
                try:
                    await sent_registration.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    log.exception(
                        "Не удалось удалить отклонённую команду матча #%s",
                        match_id,
                    )
                return

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

            await asyncio.sleep(SOURCE_DELETE_DELAY)
            deleted_duplicates = await delete_duplicate_match_cards(
                message,
                match_id,
                keep_current=not DELETE_SOURCE_AFTER_REGISTRATION,
            )
            log.info(
                "Матч #%s подтверждён; удалено карточек-дублей: %s",
                match_id,
                deleted_duplicates,
            )
            processing_completed = True
            log.info(
                "Матч #%s ��спешно отправлен в канал %s",
                result["match_id"],
                message.channel.id,
            )
        except Exception:
            diagnostics = full_match_diagnostics(
                context,
                locals().get("modal_text"),
                locals().get("result"),
            )
            log.exception(
                "Ошибка обработки файла в process_upload.\n%s",
                diagnostics,
            )
            await send_processing_error_log(
                reserved_match_id or "?", message,
                "Необработанное исключение в process_upload.", diagnostics,
            )
        finally:
            if not processing_completed:
                # Keep failed cards retryable during the same bot session.
                processed_message_ids.discard(message.id)
            if reserved_match_id is not None and not test_only:
                async with processing_match_lock:
                    processing_match_ids.discard(reserved_match_id)


async def process_message_once(
    message: discord.Message,
    test_only: bool = False,
) -> bool:
    """Process an image message once during the current bot session."""
    if message.id in processed_message_ids:
        return False
    if test_only:
        if client.user and message.author.id == client.user.id:
            return False
        if not is_forwarded_message(message) or not image_urls(message):
            return False
    elif not allowed_for_parsing(message) or not image_urls(message):
        return False

    processed_message_ids.add(message.id)
    async with processing_semaphore:
        await process_upload(message, test_only=test_only)
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
    log.info("Селф-бот успешно авторизован: %s | версия %s", client.user, BOT_VERSION)


@client.event
async def on_message(message: discord.Message) -> None:
    global is_active

    command = message.content.strip().lower()

    if re.fullmatch(r"бот\s*,?\s*ты\s+тут\s*\?*", command, re.I):
        configured_user = None
        if MY_ACCOUNT_ID:
            guild = getattr(message, "guild", None)
            if guild is not None:
                configured_user = guild.get_member(MY_ACCOUNT_ID)
            if configured_user is None:
                configured_user = client.get_user(MY_ACCOUNT_ID)
            if configured_user is None:
                try:
                    configured_user = await client.fetch_user(MY_ACCOUNT_ID)
                except Exception:
                    log.exception(
                        "Не удалось получить пользователя MY_ACCOUNT_ID=%s",
                        MY_ACCOUNT_ID,
                    )

        if configured_user is None:
            configured_name = (
                "Не найден в Discord" if MY_ACCOUNT_ID else "MY_ACCOUNT_ID не указан"
            )
        else:
            configured_name = str(
                getattr(configured_user, "display_name", None)
                or getattr(configured_user, "global_name", None)
                or getattr(configured_user, "name", configured_user)
            )

        channel_names: list[str] = []
        for channel_id in sorted(active_channel_ids):
            channel = client.get_channel(channel_id)
            channel_name = getattr(channel, "name", None)
            channel_names.append(
                f"#{channel_name} · {channel_id}" if channel_name else str(channel_id)
            )

        counts = registration_status_counts()
        now = datetime.now(timezone.utc)
        uptime = format_uptime(int((now - BOT_STARTED_AT).total_seconds()))
        latency_value = getattr(client, "latency", None)
        latency = (
            f"{round(float(latency_value) * 1000)} мс"
            if isinstance(latency_value, (int, float)) and latency_value >= 0
            else "неизвестно"
        )
        session_user = (
            f"{client.user} · {getattr(client.user, 'id', 'неизвестно')}"
            if client.user
            else "сессия не определена"
        )
        generated_at = now.astimezone(STATS_TIMEZONE).strftime("%d.%m.%Y %H:%M:%S")
        status = {
            "active": is_active,
            "version": BOT_VERSION,
            "uptime": uptime,
            "latency": latency,
            "processing": len(processing_match_ids),
            "configured_name": configured_name,
            "configured_id": MY_ACCOUNT_ID or "не указан",
            "session_user": session_user,
            "registrations_total": counts["total"],
            "registrations_today": counts["today"],
            "registrations_hour": counts["hour"],
            "api_style": AI_API_STYLE,
            "models": ", ".join(GEMINI_MODELS),
            "key_count": len(GEMINI_API_KEYS),
            "concurrency": PROCESS_CONCURRENCY,
            "confidence": f"{MIN_CONFIDENCE:.2f}",
            "channels": channel_names,
            "generated_at": generated_at,
            "timezone": str(STATS_TIMEZONE),
        }
        report_bytes = build_status_html(status).encode("utf-8")
        report_file = discord.File(
            io.BytesIO(report_bytes),
            filename=f"faceit-bot-status-{now.strftime('%Y%m%d-%H%M%S')}.html",
        )
        active_text = "запущен" if is_active else "ожидает `старт ...`"
        await message.channel.send(
            "🟢 **Я тут и готов к работе**\n"
            f"Версия: `{BOT_VERSION}`\n"
            f"Сессия Discord: **{client.user}** (`{getattr(client.user, 'id', '—')}`)\n"
            f"MY_ACCOUNT_ID: `{MY_ACCOUNT_ID or 'не указан'}` → **{configured_name}**\n"
            f"Авторег: **{active_text}** · обрабатывается игр: **{len(processing_match_ids)}**\n"
            f"Регистраций: всего **{counts['total']}**, сегодня **{counts['today']}**\n"
            f"Моделей: **{len(GEMINI_MODELS)}** · API-ключей: **{len(GEMINI_API_KEYS)}**\n"
            "Команды доступны **всем пользователям**. Подробный HTML-отчёт прикреплён.",
            file=report_file,
        )
        return

    if re.fullmatch(
        r"удалить\s+рег(?:истрационные)?\s+соо(?:бщения)?",
        command,
        re.I,
    ):
        registration_channel_ids = NORMAL_CHANNEL_IDS | PRIORITY_CHANNEL_IDS
        if not registration_channel_ids:
            await message.channel.send(
                "❌ В Railway не указаны NORMAL_CHANNEL_IDS или PRIORITY_CHANNEL_IDS."
            )
            return

        await message.channel.send(
            "🧹 Удаляю сообщения `Готово — Матч #… закрыт со счётом…` "
            "во всех каналах регистрации."
        )
        deleted, scanned, failed = await delete_all_registration_confirmations(
            registration_channel_ids
        )
        suffix = f" Ошибок каналов: **{failed}**." if failed else ""
        await message.channel.send(
            f"✅ Очистка завершена. Каналов проверено: **{scanned}**, "
            f"сообщений удалено: **{deleted}**.{suffix}"
        )
        return

    forget_match = re.fullmatch(r"забыть\s+#?(\d+)", command, re.I)
    if forget_match:
        match_id = int(forget_match.group(1))
        async with processing_match_lock:
            if match_id in processing_match_ids:
                await message.channel.send(
                    f"⏳ Игра #{match_id} сейчас обрабатывается. Повтори команду чуть позже."
                )
                return

            existed = any(
                str(item.get("match_id")) == str(match_id)
                for item in load_registration_records()
            )
            if not existed:
                await message.channel.send(
                    f"ℹ️ Игры #{match_id} нет в памяти — забывать нечего."
                )
                return

            await forget_registration(match_id)
            processing_match_ids.discard(match_id)
            # Разрешаем следующему `старт ...` снова проверить старую карточку
            # в рамках текущего запуска бота. Остальные игры от повторной
            # регистрации всё равно защищены registration_stats.json.
            processed_message_ids.clear()

        await message.channel.send(
            f"✅ Игра #{match_id} забыта. Теперь её можно зарегистрировать заново."
        )
        return

    if command.startswith("забыть"):
        await message.channel.send("Формат команды: `забыть 2548`")
        return

    if command in ("стата", "статистика", "stats"):
        await message.channel.send(registration_stats_text())
        return

    if command == "енд" or command.startswith("старт"):
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

    # A forwarded game card outside the active registration channels is a
    # test request. It is recognized by the unchanged parser and receives
    # only the generated registration command as a reply.
    if (
        is_forwarded_message(message)
        and message.channel.id not in active_channel_ids
    ):
        await process_message_once(message, test_only=True)
        return

    if is_active:
        await process_message_once(message)


if __name__ == "__main__":
    client.run(DISCORD_USER_TOKEN)
