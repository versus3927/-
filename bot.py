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

BOT_VERSION = "v50-final10-never-delete-before-confirmation-2026-09-12"

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
if AI_API_STYLE != "gemini":
    logging.warning(
        f"AI_API_STYLE установлен на '{AI_API_STYLE}', но рекомендуется 'gemini' для использования моделей Gemini. "
        "Пожалуйста, убедитесь, что ваши переменные GEMINI_BASE_URL и GEMINI_MODEL настроены правильно для выбранного стиля API."
    )

GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    os.getenv("GOOGLE_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"),
).rstrip("/")

# Проверяем и очищаем имена моделей от minimax/minimax-m3, если они случайно попали
_gemini_model_candidate = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
if "minimax" in _gemini_model_candidate:
    logging.warning(
        f"Обнаружена модель '{_gemini_model_candidate}' в GEMINI_MODEL. "
        "Это модель OpenRouter, а не Gemini. Изменяем на 'gemini-3.8-flash'."
    )
    _gemini_model_candidate = "gemini-3.8-flash"
GEMINI_MODEL = _gemini_model_candidate

_gemini_fallback_model_candidate = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash")
if "minimax" in _gemini_fallback_model_candidate:
    logging.warning(
        f"Обнаружена модель '{_gemini_fallback_model_candidate}' в GEMINI_FALLBACK_MODEL. "
        "Это модель OpenRouter, а не Gemini. Изменяем на 'gemini-3.7-flash'."
    )
    _gemini_fallback_model_candidate = "gemini-3.7-flash"
GEMINI_FALLBACK_MODEL = _gemini_fallback_model_candidate


GEMINI_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_MODELS",
        f"{GEMINI_MODEL},{GEMINI_FALLBACK_MODEL}",
    ).split(",")
    if model.strip() and "minimax" not in model.strip() # Дополнительная фильтрация
]
if not GEMINI_MODELS:
    GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash"]
    logging.warning(
        "GEMINI_MODELS пуст или содержит только модели minimax. Установлены значения по умолчанию: gemini-3.8-flash, gemini-3.7-flash."
    )


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
    # Scoreboards often contain a harmless creator/platform prefix while the
    # Discord roster contains the same nick with trailing digits.  For
    # example, `yt: Shamin` must match `Shamin336` instead of being registered
    # as an absent 0/0/13 player.
    text = re.sub(
        r"^\s*(?:yt|youtube|ttv|twitch|vk)\s*(?:[:|._\-–—]+\s*|\s+)",
        "",
        text,
        flags=re.I,
    )
    text = unicodedata.normalize("NFKD", text).casefold()
    text = text.translate(_CYRILLIC_TO_LATIN)
    return "".join(character for character in text if character.isalnum())


def strip_leading_clan_tags(value: str) -> str:
    """Drop clan/league prefixes before the real nickname on every code path."""
    text = str(value).strip()
    previous = None
    while previous != text:
        previous = text
        # OLD is a league/clan tag, never part of the game nickname. Remove it
        # globally in all common renderings, regardless of case or decoration:
        # `OLD | Nick`, `old Nick`, `[OLD] Nick`, `🔴 OLD — Nick`.
        text = re.sub(
            r"^\s*[^\w\[({]*[\[({]?\s*OLD\s*[\])}]?"
            r"(?=\s|[|:·•\-–—])\s*(?:[|:·•\-–—]+\s*|\s+)",
            "",
            text,
            flags=re.I,
        )
        # A visible separator is authoritative for arbitrary clan/league tags,
        # including mixed-case forms which cannot safely be recognized by
        # capitalization alone: `MCRW|ReNiTe`, `noob | Yaksty`,
        # `sley｜Future`, and chained `OLD | MCRW | Shkiper`.
        pipe_tag = re.match(
            r"^\s*([^|｜¦]{1,24}?)\s*[|｜¦]\s*(\S.*)$",
            text,
        )
        if pipe_tag:
            prefix = pipe_tag.group(1).strip(" [](){}<>@#`*_.,:;·•-–—")
            nickname = pipe_tag.group(2).strip()
            if prefix and nickname:
                text = nickname
                continue
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