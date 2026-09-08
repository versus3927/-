import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
from collections import Counter
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
        if value is None or depth > 7:
            continue
        if isinstance(value, str):
            if "# CT" in value.upper() and "# T" in value.upper():
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

        # discord.py-self versions expose modal fields through slightly
        # different wrappers. Only inspect the known, bounded attributes.
        for attribute in (
            "value", "default", "text", "content", "data", "modal",
            "interaction", "message", "response", "response_message",
            "successful", "result", "messages", "components", "children", "items",
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

        def interaction_check(interaction: object) -> bool:
            interaction_custom_id = str(
                getattr(interaction, "custom_id", "")
                or (getattr(interaction, "data", {}) or {}).get("custom_id", "")
            )
            return not custom_id or not interaction_custom_id or interaction_custom_id == custom_id

        # discord.py-self dispatches `interaction_finish` after the private
        # component response has been finalized and Interaction.successful
        # has been populated. `interaction` is retained as a compatibility
        # fallback for builds that only expose the first event.
        def helper_message_check(candidate: object) -> bool:
            if getattr(getattr(candidate, "channel", None), "id", None) != message.channel.id:
                return False
            text = extract_player_modal_text(candidate)
            if not text:
                return False
            if expected_match_id is None:
                return True
            return bool(re.search(rf"=g\s+{expected_match_id}\b", text, re.I))

        waiters = [
            asyncio.create_task(client.wait_for("message", check=helper_message_check)),
            asyncio.create_task(client.wait_for("interaction_finish", check=interaction_check)),
            asyncio.create_task(client.wait_for("interaction", check=interaction_check)),
        ]
        try:
            click_result = await button.click()
            direct_text = extract_player_modal_text(click_result)
            if direct_text:
                return direct_text, extract_interaction_image_urls(click_result)

            deadline = asyncio.get_running_loop().time() + PLAYER_MODAL_TIMEOUT
            pending = set(waiters)
            while pending:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return None
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    return None
                for completed in done:
                    with contextlib.suppress(Exception):
                        response_text = extract_player_modal_text(completed.result())
                        if response_text:
                            return (
                                response_text,
                                extract_interaction_image_urls(completed.result()),
                            )
            return None, []
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
    line_pattern = re.compile(
        r"^\s*(\d{1,5})\s+(.+?)\s*=\s*(\d+)\s+(\d+)\s+(\d+)\s*$"
    )

    for raw_line in modal_text.splitlines():
        line = raw_line.strip()
        header = re.fullmatch(r"#?\s*(CT|T)\s*", line, re.I)
        if header:
            current_side = header.group(1).upper()
            continue
        if current_side is None:
            continue
        found = line_pattern.fullmatch(line)
        if not found:
            continue
        player_id = int(found.group(1))
        kills, assists, deaths = map(int, found.group(3, 4, 5))
        # Preserve the existing absent-player rule used by registration.
        if kills == assists == deaths == 0:
            deaths = 13
        sides[current_side].append({
            "id": player_id,
            "nickname": found.group(2).strip(),
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
            r"(?:CT|T)?\s*[·•:|\-–—]\s*(\d+)\s*[·•:|\-–—]\s*K[/\\]A[/\\][CD]",
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
    explicit_title = re.search(
        r"^\s*Результат\s+матча\s*#\s*\d+[^\n]*\bна\s+проверку\b",
        message_text,
        re.I | re.M,
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


async def recognize_match(
    images: list[bytes],
    message_text: str = "",
    score_only: bool = False,
) -> dict:
    card_result = None if score_only else parse_complete_card(message_text)
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

    if score_only:
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
    if score_only:
        return result
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
    return "готово" in response_text.lower(), response_text[:500]


async def process_upload(message: discord.Message) -> None:
    urls = image_urls(message)
    if not urls:
        return

    context = await message_context(message)
    context_match = re.search(r"(?:матч|матча)\s*#\s*(\d+)", context, re.I)
    reserved_match_id = int(context_match.group(1)) if context_match else None
    if reserved_match_id is not None:
        async with processing_match_lock:
            if reserved_match_id in processing_match_ids:
                log.info(
                    "Матч #%s уже обрабатывается — повторная карточка пропущена",
                    reserved_match_id,
                )
                return
            processing_match_ids.add(reserved_match_id)

    async with message.channel.typing():
        try:
            # The button workflow is reserved strictly for cards whose own
            # title/status says `на проверку`. Ordinary matches must go
            # directly through the normal registration path even if the
            # phrase appears elsewhere in the message context.
            review_card = is_review_result_card(context)
            if review_card:
                modal_text, helper_image_urls = await get_players_response(message)
                if not modal_text:
                    log.error(
                        "Карточка на проверку пропущена: не удалось открыть/прочитать «Получить игроков»."
                    )
                    return
                result = result_from_players_modal(context, modal_text)
                if result is None and not re.search(
                    r"(?<!\d)\d{1,2}\s*:\s*\d{1,2}(?!\d)", context
                ):
                    # `Получить игроков` deliberately returns
                    # `=g <match> <счёт A> <счёт B>`. Read the real final
                    # score from the attached result screenshot, then insert
                    # it into our own registration command.
                    score_image_urls = helper_image_urls or urls
                    timeout = aiohttp.ClientTimeout(total=30)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        raw_images = await asyncio.gather(
                            *(download_image(session, url) for url in score_image_urls[:4])
                        )
                    score_result = await recognize_match(
                        raw_images,
                        context,
                        score_only=True,
                    )
                    expected_match = re.search(
                        r"(?:матч|матча)\s*#\s*(\d+)", context, re.I
                    )
                    recognized_match = score_result.get("match_id")
                    score_a = score_result.get("score_a")
                    score_b = score_result.get("score_b")
                    score_confidence = float(
                        score_result.get("overall_confidence", 0) or 0
                    )
                    score_is_valid = (
                        score_result.get("is_match_result")
                        and isinstance(score_a, int)
                        and isinstance(score_b, int)
                        and 0 <= score_a <= 99
                        and 0 <= score_b <= 99
                        and score_confidence >= 0.70
                        and (
                            not expected_match
                            or recognized_match is None
                            or int(recognized_match) == int(expected_match.group(1))
                        )
                    )
                    if score_is_valid:
                        log.info(
                            "Матч #%s: со скриншота прочитан счёт %s:%s (%.2f)",
                            expected_match.group(1) if expected_match else "?",
                            score_a,
                            score_b,
                            score_confidence,
                        )
                        result = result_from_players_modal(
                            context,
                            modal_text,
                            score_override=(score_a, score_b),
                            visual_verified=True,
                        )
                if result is None:
                    log.error(
                        "Карточка на проверку пропущена: не удалось надёжно собрать ID, стороны и реальный счёт."
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
                    log.error(
                        "Матч #%s пропущен: ID модели %s не совпали с карточкой %s",
                        result.get("match_id"),
                        returned_ids,
                        expected_ids,
                    )
                    return
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
                or result.get("ct_team") not in ("A", "B")
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

            all_players = [*result["team_a"], *result["team_b"]]
            player_ids = [player.get("id") for player in all_players]
            if (
                len(player_ids) != 10
                or any(not isinstance(value, int) or value <= 0 for value in player_ids)
                or len(set(player_ids)) != 10
            ):
                log.error(
                    "Матч #%s не отправлен: недопустимые или повторяющиеся ID %s",
                    result.get("match_id"),
                    player_ids,
                )
                return

            command_text = format_registration(result)
            match_id = int(result["match_id"])
            if not await record_registration(match_id):
                log.info("Матч #%s уже зарегистрирован — повтор пропущен", match_id)
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
        finally:
            if reserved_match_id is not None:
                async with processing_match_lock:
                    processing_match_ids.discard(reserved_match_id)


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
