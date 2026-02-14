import os
import sys
import json
import logging
import asyncio
import time as time_module
import hashlib
import random
import uuid
import mimetypes
import textwrap
import re
import shutil
import subprocess
from typing import Optional
from collections import deque
from pathlib import Path
from datetime import datetime, time, timedelta
import numpy as np
from dotenv import load_dotenv

import requests
from PIL import Image, ImageDraw, ImageFont
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
import moviepy.editor as mpe
from moviepy.editor import VideoFileClip, AudioFileClip, ColorClip, TextClip, CompositeVideoClip, ImageClip, concatenate_videoclips, concatenate_audioclips, CompositeAudioClip
from moviepy.video.fx import all as vfx_all
from moviepy.audio.fx import all as afx_all
from openai import OpenAI
from telegram import Update
from telegram.constants import MessageEntityType
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from telegram.error import BadRequest
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)


def get_env_str(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def get_env_int(name: str) -> int:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return int(v)


TELEGRAM_BOT_TOKEN = get_env_str("TELEGRAM_BOT_TOKEN")
BUFFER_CHANNEL_ID = get_env_int("BUFFER_CHANNEL_ID")
MAIN_CHANNEL_ID = get_env_int("MAIN_CHANNEL_ID")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_ID", "5675979056") or 5675979056)
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", "5675979056") or 0)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# ElevenLabs settings
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "s756tFIFJ9r8dOGB5rlK").strip()

# Supabase settings
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "").strip()
SUPABASE_TIMEOUT_SECONDS = int(os.getenv("SUPABASE_TIMEOUT_SECONDS", "120"))

# Instagram settings
ENABLE_INSTAGRAM = os.getenv("ENABLE_INSTAGRAM", "1").strip()
IG_USER_ID = os.getenv("IG_USER_ID", "").strip()
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v21.0").strip()
IG_TIMEOUT_SECONDS = int(os.getenv("IG_TIMEOUT_SECONDS", "300"))
IG_POLL_SECONDS = int(os.getenv("IG_POLL_SECONDS", "30"))
IG_POLL_MAX_TRIES = int(os.getenv("IG_POLL_MAX_TRIES", "10"))

# Facebook settings
ENABLE_FB = os.getenv("ENABLE_FB", "1").strip() or "1"
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()
FB_GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v21.0").strip()
FB_TIMEOUT_SECONDS = int(os.getenv("FB_TIMEOUT_SECONDS", "300"))

POST_DELAY_SECONDS_RAW = int(os.getenv("POST_DELAY_SECONDS", "1800"))  # 30 минут по умолчанию
# Минимальный интервал: 1 час (3600 сек) для соблюдения правил публикации
POST_DELAY_SECONDS = max(POST_DELAY_SECONDS_RAW, 3600)

# Флаг для удаления сообщений из буфера после публикации
DELETE_FROM_BUFFER = int(os.getenv("DELETE_FROM_BUFFER", "1"))  # Включаем по умолчанию

# Флаг для отправки краткого отчёта после каждой успешной публикации
REPORT_AFTER_POST = int(os.getenv("REPORT_AFTER_POST", "1"))

CHANNEL_LINK = "https://t.me/+19xSNtVpJx1hZGQy"
FOOTER_HTML = f"\n\n| <a href=\"{CHANNEL_LINK}\">Haqiqat 🧠</a> | <a href=\"{CHANNEL_LINK}\">Kanalga obuna bo'ling</a>"
BRANDED_LINK = f"👉 Batafsil: {CHANNEL_LINK}"
HASHTAGS_BLOCK = "#haqiqat #uzbekistan #qiziqarli"
PUBLISH_INTERVAL_SECONDS = 3600  # 60 минут
LINK_BLOCK_HTML = '| <a href="https://t.me/+19xSNtVpjx1hZGQy">Haqiqat 🧠 | Kanalga obuna bo\'ling</a> |'
CAPTION_MAX_LENGTH = 900  # Лимит для caption

# Админ-чат для отчётов
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
if ADMIN_CHAT_ID:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except ValueError:
        ADMIN_CHAT_ID = None
else:
    ADMIN_CHAT_ID = None

openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

supabase_client: Optional[Client] = None

# message_id -> {emoji: count}
REACTIONS = {}
# message_id -> {user_id: emoji}
USER_REACTIONS = {}


POST_QUEUE = deque()
VIDEO_PROCESSING_QUEUE = asyncio.Queue()  # FIX B: Очередь для фоновой обработки видео
IS_POSTING = False
# Первое включение после рестарта — публикуем сразу первый пост без ожиданий
FIRST_RUN_IMMEDIATE = True

# 🎛️ MIXED QUEUE 4+4: Счетчики для чередования контента
VOICEOVER_POSTS_COUNT = 0  # Счетчик постов с озвучкой
NO_VOICEOVER_POSTS_COUNT = 0  # Счетчик постов без озвучки
CURRENT_BLOCK_TYPE = "voiceover"  # Текущий тип блока: "voiceover" или "no_voiceover"
# SMART CONTROL: Система паузы публикаций
IS_PAUSED = False

# СИСТЕМА КОНВЕЙЕР: Папка готовых постов
READY_TO_PUBLISH_DIR = Path("ready_to_publish")
READY_TO_PUBLISH_DIR.mkdir(exist_ok=True)
TARGET_READY_POSTS = 10  # Поддерживаем 10 готовых постов (5 дней автономной работы)
IS_PREPARING = False  # Флаг для контроля одновременной подготовки


async def safe_unlink(path: Path | str, retries: int = 10, delay: float = 0.4):
    """Async-safe unlink with retries to handle Windows file locks (WinError 32).
    Does not raise; only logs on failure.
    """
    p = Path(path)
    if not p.exists():
        return
    for i in range(retries):
        try:
            p.unlink()
            return
        except PermissionError:
            await asyncio.sleep(delay)
        except Exception:
            log.exception(f"[CLEANUP] Failed to delete {path}")
            return
    log.error(f"[CLEANUP] Still locked after retries: {path}")


def _clamp_t(t: float, duration: float, eps: float = 0.25) -> float:
    if duration is None:
        return t
    return max(0.0, min(float(t), max(0.0, float(duration) - eps)))

QUEUE_FILE = Path("post_queue.json")
SEEN_FILE = Path("seen_posts.json")
SEEN_HASHES = set()
SEEN_FILE_IDS = set()

# IG расписание публикаций (в памяти, обновляется ежедневно)
IG_SCHEDULE = {
    "date": None,
    "morning_videos": 0,      # до 14:00, максимум 3
    "afternoon_videos": 0,    # после 16:00, продолжение по 1 в час
    "afternoon_carousels": 0  # после 15:00, максимум 2
}

# Разовый форс-тест карусели (игнор расписания/задержек для первого carousel_pending)
FORCE_CAROUSEL_TEST = True

# Отслеживание интервалов публикаций
LAST_PHOTO_TIME = None
LAST_VIDEO_TIME = None
LAST_POST_TIME = None
LAST_POST_TIME_FILE = Path("last_post_time.json")
FORCE_POST_NOW = False  # Флаг для форс-публикации (/postnow)
POSTNOW_EVENT = asyncio.Event()  # Event для немедленного пробуждения воркера
VIDEO_MIRROR_TOGGLE = False


async def sleep_or_postnow(seconds: int) -> bool:
    """
    True  -> проснулись из-за /postnow
    False -> досидели таймер по расписанию
    """
    global FORCE_POST_NOW
    # Если /postnow активен — пропускаем паузу и считаем, что проснулись по POSTNOW
    if FORCE_POST_NOW:
        log.info("[SCHEDULER] POSTNOW override: skip cooldown sleep")
        return True
    try:
        await asyncio.wait_for(POSTNOW_EVENT.wait(), timeout=seconds)
        POSTNOW_EVENT.clear()  # ВАЖНО: сбросить, иначе будет «вечно включён»
        return True
    except asyncio.TimeoutError:
        return False


# Хранилище опубликованных текстов для проверки повторов
PUBLISHED_TEXTS_FILE = Path("published_texts.json")
PUBLISHED_TEXTS = []  # Список последних N опубликованных текстов для проверки
MAX_PUBLISHED_TEXTS = 50  # Храним последние 50 постов

# История и отчёты
HISTORY_LOG = Path("history.log")
REPORTS_DIR = Path("reports")
DAILY_COST_USD = 0.0
TRANSLATION_LAST_COST = 0.0

# Хранилище опубликованных текстов для проверки повторов
PUBLISHED_TEXTS_FILE = Path("published_texts.json")
PUBLISHED_TEXTS = []  # Список последних N опубликованных текстов для проверки
MAX_PUBLISHED_TEXTS = 50  # Храним последние 50 постов


def get_supabase_client() -> Optional[Client]:
    """Ленивая инициализация Supabase клиента."""
    global supabase_client
    if supabase_client:
        return supabase_client
    
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        log.warning("Supabase credentials are not set")
        return None
    
    try:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        log.error(f"Failed to create Supabase client: {e}")
        supabase_client = None
    
    return supabase_client


def upload_to_supabase(local_file_path: str, content_type: str) -> Optional[str]:
    """
    Загружает файл в Supabase Storage и возвращает публичный URL.
    Не меняет существующую логику бота.
    """
    client = get_supabase_client()
    if not client:
        return None
    
    if not SUPABASE_BUCKET:
        log.warning("Supabase bucket name is not set")
        return None
    
    path_obj = Path(local_file_path)
    if not path_obj.exists():
        log.warning(f"Supabase upload skipped, file not found: {local_file_path}")
        return None
    
    size_mb = path_obj.stat().st_size / (1024 * 1024)
    log.info(f"[DEBUG] File size: {size_mb:.2f} MB")

    unique_name = f"{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex}{path_obj.suffix}"
    upload_url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/{SUPABASE_BUCKET}/{unique_name}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "false",
    }
    
    try:
        with path_obj.open("rb") as f:
            resp = requests.post(
                upload_url,
                data=f,
                headers=headers,
                timeout=SUPABASE_TIMEOUT_SECONDS,
            )
        resp.raise_for_status()
        public_url = client.storage.from_(SUPABASE_BUCKET).get_public_url(unique_name)
        log.info(f"[Supabase] File uploaded: {public_url}")
        return public_url
    except Exception as e:
        log.error(f"Supabase upload failed: {e}")
        return None


def delete_supabase_file(public_url: str):
    """Удаляет файл из Supabase по публичному URL."""
    client = get_supabase_client()
    if not client:
        return
    if not (public_url and SUPABASE_BUCKET):
        return

    marker = "/storage/v1/object/public/"
    try:
        if marker not in public_url:
            raise ValueError("public url format unexpected")
        path_part = public_url.split(marker, 1)[1]
        bucket_from_url, key = path_part.split("/", 1)
        if bucket_from_url != SUPABASE_BUCKET:
            log.warning(f"[Supabase] Bucket mismatch when deleting: url_bucket={bucket_from_url}, env_bucket={SUPABASE_BUCKET}")
        if not key:
            raise ValueError("empty storage key")
        # Удаляем файл из бакета (ключ без имени бакета)
        client.storage.from_(SUPABASE_BUCKET).remove([key])
        log.info(f"INFO | [CLEANUP] Supabase storage cleared for file: {key}")
    except Exception as e:
        log.warning(f"[Supabase] File delete failed: {e}")


def delete_supabase_files(urls: list[str]):
    """Удаляет несколько файлов из Supabase."""
    for url in urls or []:
        delete_supabase_file(url)


def supabase_key_from_url(public_url: str) -> Optional[str]:
    marker = "/storage/v1/object/public/"
    if not public_url or marker not in public_url:
        return None
    try:
        path_part = public_url.split(marker, 1)[1]
        bucket_from_url, key = path_part.split("/", 1)
        if bucket_from_url != SUPABASE_BUCKET:
            return None
        return key
    except Exception:
        return None


def maybe_delete_supabase_media(item: dict, reason: str):
    """
    Удаляет файл из Supabase, если он ещё не удалён.
    Чтобы не ломать FB публикацию, после IG удаляем только если FB отключён.
    """
    if not item or item.get("supabase_deleted"):
        return
    public_url = item.get("supabase_url")
    if not public_url:
        return

    if reason == "instagram" and ENABLE_FB == "1" and not item.get("fb_published"):
        log.info("[DEBUG] Skip delete after IG publish because FB is enabled; will delete after FB.")
        return

    delete_supabase_file(public_url)
    item["supabase_deleted"] = True


async def cleanup_supabase_orphans(dry_run: bool = True) -> list[str]:
    """
    Сравнивает содержимое бакета media с текущей POST_QUEUE и удаляет (или возвращает) лишние файлы.
    dry_run=True — только логирует и возвращает список сирот.
    """
    client = get_supabase_client()
    if not client:
        log.warning("[Supabase] cleanup aborted: client not available")
        return []
    if not SUPABASE_BUCKET:
        log.warning("[Supabase] cleanup aborted: bucket not configured")
        return []

    keep_keys = set()
    for it in POST_QUEUE:
        k = supabase_key_from_url(it.get("supabase_url"))
        if k:
            keep_keys.add(k)

    orphans: list[str] = []
    offset = 0
    page_size = 1000
    while True:
        try:
            files = client.storage.from_(SUPABASE_BUCKET).list(
                path="",
                options={"limit": page_size, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
            )
        except Exception as e:
            log.error(f"[Supabase] cleanup list failed: {e}")
            break
        if not files:
            break
        for f in files:
            name = f.get("name")
            if name and name not in keep_keys:
                orphans.append(name)
        if len(files) < page_size:
            break
        offset += page_size

    if dry_run:
        log.info(f"[Supabase] cleanup dry-run: orphans={orphans}")
        return orphans

    for name in orphans:
        try:
            client.storage.from_(SUPABASE_BUCKET).remove([name])
            log.info(f"INFO | [CLEANUP] Supabase storage cleared for file: {name}")
        except Exception as e:
            log.warning(f"[Supabase] cleanup remove failed for {name}: {e}")
    return orphans


def ig_post(path: str, data: dict) -> dict:
    """POST к Instagram Graph API с логированием."""
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{path.lstrip('/')}"
    try:
        resp = requests.post(url, data=data, timeout=IG_TIMEOUT_SECONDS)
        text = (resp.text or "")[:500]
        log.info(f"IG_POST url={url} status={resp.status_code} resp={text}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"IG_POST_FAIL url={url} error={e}")
        return {}


def ig_get(path: str, params: dict) -> dict:
    """GET к Instagram Graph API с логированием."""
    url = f"https://graph.facebook.com/{IG_GRAPH_VERSION}/{path.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=IG_TIMEOUT_SECONDS)
        text = (resp.text or "")[:500]
        log.info(f"IG_GET url={resp.url} status={resp.status_code} resp={text}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"IG_GET_FAIL url={url} error={e}")
        return {}


async def publish_to_instagram(item: dict):
    """Публикация медиа в Instagram по публичному URL из Supabase. Возвращает True при успехе, False при ошибке."""
    if ENABLE_INSTAGRAM != "1":
        return True
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        log.warning("Instagram disabled: missing IG_USER_ID or IG_ACCESS_TOKEN")
        return True

    media_type = item.get("type")
    if media_type == "text":
        log.info("Instagram skip: text post")
        return True
    
    if media_type not in ("video",):
        log.info(f"Instagram skip: unsupported type {media_type}")
        return True
    
    supabase_url = item.get("supabase_url")
    if not supabase_url:
        log.warning("Instagram skip: no supabase_url")
        return True
    
    caption = item.get("caption") or item.get("text") or ""
    # Clean strong-markdown and log final caption for IG
    caption = (caption or "").replace("**", "")
    log.info(f"CAPTION_TO_IG: {caption[:300]}")
    safe_caption = clean_social_text(caption)
    log.info(f"IG_CAPTION len={len(safe_caption)} text={safe_caption[:300]}")

    # Создаём контейнер
    if media_type == "photo":
        payload = {
            "image_url": supabase_url,
            "caption": safe_caption,
            "access_token": IG_ACCESS_TOKEN,
        }
    else:
        payload = {
            "media_type": "REELS",
            "video_url": supabase_url,
            "caption": safe_caption,
            "audio_type": "ORIGINAL",
            "access_token": IG_ACCESS_TOKEN,
        }
    
    res = ig_post(f"{IG_USER_ID}/media", payload)
    log.info(f"IG_CREATE_RESP: {res}")
    creation_id = res.get("id")
    if not creation_id:
        log.error(f"IG_CREATE_CONTAINER_FAIL resp={res}")
        return False
    log.info(f"IG_CREATE_CONTAINER_OK creation_id={creation_id}")

    # Для видео ждём, пока контейнер обработается
    if media_type == "video":
        tries = IG_POLL_MAX_TRIES
        while tries > 0:
            status_res = ig_get(creation_id, {"fields": "status_code", "access_token": IG_ACCESS_TOKEN})
            status_code = status_res.get("status_code")
            log.info(f"IG_STATUS creation_id={creation_id} status_code={status_code} resp={status_res}")
            if status_code == "FINISHED":
                break
            if status_code in ("ERROR", "FAILED", "EXPIRED"):
                # Одна повторная попытка после 30 секунд
                await asyncio.sleep(30)
                status_res_retry = ig_get(creation_id, {"fields": "status_code", "access_token": IG_ACCESS_TOKEN})
                status_code_retry = status_res_retry.get("status_code")
                log.info(f"IG_STATUS_RETRY creation_id={creation_id} status_code={status_code_retry} resp={status_res_retry}")
                if status_code_retry == "FINISHED":
                    break
                log.error(f"IG_STATUS_FAIL creation_id={creation_id} status_code={status_code_retry} - Smart Skip activated")
                return False
            tries -= 1
            await asyncio.sleep(IG_POLL_SECONDS)
        if tries == 0:
            log.warning(f"IG_STATUS_TIMEOUT creation_id={creation_id} after 5 minutes - trying media_publish anyway (Smart Skip improved)")
    
    # Пауза перед публикацией, чтобы Meta успела подготовить контейнер
    time_module.sleep(10)

    # Публикуем
    publish_res = ig_post(f"{IG_USER_ID}/media_publish", {"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN})
    log.info(f"IG_PUBLISH_RESP: {publish_res}")
    media_id = publish_res.get("id")
    if media_id:
        log.info(f"IG_PUBLISH_OK media_id={media_id}")
        item["ig_published"] = True
        ig_mark_published("video")
        return True
    else:
        log.error("IG_PUBLISH_FAIL - Smart Skip activated")
        return False


async def publish_to_instagram_carousel(item: dict, image_urls: list[str]):
    """Публикация карусели (альбом) в Instagram."""
    if ENABLE_INSTAGRAM != "1":
        return
    if not IG_USER_ID or not IG_ACCESS_TOKEN:
        log.warning("Instagram disabled: missing IG_USER_ID or IG_ACCESS_TOKEN")
        return
    if not image_urls:
        log.warning("Instagram carousel: no images to publish")
        return

    caption = item.get("caption") or item.get("text") or ""
    caption = (caption or "").replace("**", "")
    log.info(f"CAPTION_TO_IG: {caption[:300]}")
    safe_caption = clean_social_text(caption)

    child_ids = []
    for url in image_urls:
        res = ig_post(
            f"{IG_USER_ID}/media",
            {
                "image_url": url,
                "is_carousel_item": "true",
                "access_token": IG_ACCESS_TOKEN,
            },
        )
        media_id = res.get("id")
        if media_id:
            child_ids.append(media_id)
        else:
            log.error("IG_CAROUSEL_CHILD_FAIL")

    if not child_ids:
        log.error("IG_CAROUSEL_CHILDREN_EMPTY")
        return

    parent_res = ig_post(
        f"{IG_USER_ID}/media",
        {
            "media_type": "CAROUSEL",
            "children": child_ids,
            "caption": safe_caption,
            "access_token": IG_ACCESS_TOKEN,
        },
    )
    creation_id = parent_res.get("id")
    if not creation_id:
        log.error("IG_CAROUSEL_PARENT_FAIL")
        return

    publish_res = ig_post(
        f"{IG_USER_ID}/media_publish",
        {"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN},
    )
    media_id = publish_res.get("id")
    if media_id:
        log.info(f"IG_PUBLISH_CAROUSEL_OK media_id={media_id}")
        item["ig_published"] = True
        ig_mark_published("carousel")
        if ENABLE_FB != "1":
            delete_supabase_files(image_urls)
    else:
        log.error("IG_PUBLISH_CAROUSEL_FAIL")


def fb_post(path: str, data: dict) -> dict:
    """POST к Facebook Graph API (Page) с логированием."""
    url = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{path.lstrip('/')}"
    try:
        resp = requests.post(url, data=data, timeout=FB_TIMEOUT_SECONDS)
        text = (resp.text or "")[:500]
        log.info(f"FB_POST url={url} status={resp.status_code} resp={text}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"FB_POST_FAIL url={url} error={e}")
        return {}


async def publish_to_facebook(item: dict):
    """Публикация медиа в Facebook Page по публичному URL из Supabase."""
    if ENABLE_FB != "1":
        return
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        log.warning("Facebook disabled: missing FB_PAGE_ID or FB_PAGE_TOKEN")
        return

    media_type = item.get("type")
    if media_type == "text":
        log.info("Facebook skip: text post")
        return
    
    if media_type not in ("photo", "video"):
        log.info(f"Facebook skip: unsupported type {media_type}")
        return
    
    supabase_url = item.get("supabase_url")
    if not supabase_url:
        log.warning("Facebook skip: no supabase_url")
        return
    
    caption = item.get("caption") or item.get("text") or ""
    caption = (caption or "").replace("**", "")
    log.info(f"CAPTION_TO_IG: {caption[:300]}")
    safe_caption = clean_social_text(caption)

    try:
        if media_type == "photo":
            res = fb_post(f"{FB_PAGE_ID}/photos", {
                "url": supabase_url,
                "caption": safe_caption,
                "access_token": FB_PAGE_TOKEN,
            })
            media_id = res.get("id")
            if media_id:
                log.info(f"FB_PUBLISH_PHOTO_OK id={media_id}")
            else:
                log.error("FB_PUBLISH_PHOTO_FAIL")
        else:
            res = fb_post(f"{FB_PAGE_ID}/videos", {
                "file_url": supabase_url,
                "description": safe_caption,
                "access_token": FB_PAGE_TOKEN,
            })
            media_id = res.get("id")
            if media_id:
                log.info(f"FB_PUBLISH_VIDEO_OK id={media_id}")
                item["fb_published"] = True
            else:
                log.error("FB_PUBLISH_VIDEO_FAIL")
    except Exception as e:
        log.error(f"Facebook publish error: {e}")


async def publish_to_facebook_carousel(item: dict, image_urls: list[str]):
    """Публикация набора фото как альбом/серия в Facebook Page."""
    if ENABLE_FB != "1":
        return
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        log.warning("Facebook disabled: missing FB_PAGE_ID or FB_PAGE_TOKEN")
        return
    if not image_urls:
        log.warning("Facebook carousel: no images to publish")
        return

    caption = item.get("caption") or item.get("text") or ""
    safe_caption = clean_social_text(caption)

    success = False
    for idx, url in enumerate(image_urls):
        res = fb_post(
            f"{FB_PAGE_ID}/photos",
            {
                "url": url,
                "caption": safe_caption if idx == 0 else "",
                "access_token": FB_PAGE_TOKEN,
            },
        )
        media_id = res.get("id")
        if media_id:
            success = True
            log.info(f"FB_PUBLISH_CAROUSEL_PHOTO_OK id={media_id} idx={idx}")
        else:
            log.error(f"FB_PUBLISH_CAROUSEL_PHOTO_FAIL idx={idx}")

    if success:
        item["fb_published"] = True
        delete_supabase_files(image_urls)

# Статистика для отчётов
STATS_FILE = Path("daily_stats.json")
DAILY_STATS = {
    "date": None,  # Текущая дата
    "morning": 0,  # До обеда (до 14:00)
    "afternoon": 0,  # После обеда (с 14:00)
    "video": 0,
    "photo": 0,
    "text": 0,
    "total": 0,
    "tokens": {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0
    },
    "cost_usd": 0.0
}


def save_queue():
    try:
        with QUEUE_FILE.open("w", encoding="utf-8") as f:
            json.dump(list(POST_QUEUE), f, ensure_ascii=False)
    except Exception as e:
        print("Failed to save queue", e)


def load_queue():
    if not QUEUE_FILE.exists():
        return
    try:
        with QUEUE_FILE.open("r", encoding="utf-8") as f:
            items = json.load(f)
            for it in items:
                POST_QUEUE.append(it)
    except Exception as e:
        print("Failed to load queue", e)


def load_seen():
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Новая структура: {"hashes": [...], "buffer_message_ids": [...], "file_ids": [...]}
                SEEN_HASHES.update(data.get("hashes", []))
                SEEN_FILE_IDS.update(data.get("file_ids", []))
                # Загружаем обработанные message_id (если есть)
                if "buffer_message_ids" in data:
                    # Сохраняем для совместимости, но не используем активно
                    pass
            else:
                # Старая структура: просто список хешей
                SEEN_HASHES.update(data)
        except Exception:
            pass


def save_seen():
    # Сохраняем в новом формате с поддержкой обратной совместимости
    data = {
        "hashes": list(SEEN_HASHES),
        "file_ids": list(SEEN_FILE_IDS),
        "buffer_message_ids": []  # Будет заполняться при публикации
    }
    SEEN_FILE.write_text(json.dumps(data), encoding="utf-8")


def load_last_post_time():
    global LAST_POST_TIME
    if LAST_POST_TIME_FILE.exists():
        try:
            data = json.loads(LAST_POST_TIME_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("last_post_time"):
                LAST_POST_TIME = datetime.fromisoformat(data["last_post_time"])
        except Exception:
            pass


def save_last_post_time():
    if LAST_POST_TIME:
        try:
            LAST_POST_TIME_FILE.write_text(
                json.dumps({"last_post_time": LAST_POST_TIME.isoformat()}),
                encoding="utf-8"
            )
        except Exception as e:
            log.warning(f"Failed to save last_post_time: {e}")


def mark_file_id_seen(file_id: str):
    if not file_id:
        return
    if file_id in SEEN_FILE_IDS:
        return
    SEEN_FILE_IDS.add(file_id)
    save_seen()


def reset_ig_schedule_if_needed():
    today = datetime.now().strftime("%Y-%m-%d")
    if IG_SCHEDULE["date"] != today:
        IG_SCHEDULE["date"] = today
        IG_SCHEDULE["morning_videos"] = 0
        IG_SCHEDULE["afternoon_videos"] = 0
        IG_SCHEDULE["afternoon_carousels"] = 0


def can_ig_publish(media_kind: str, force: bool = False) -> bool:
    """
    IG расписание (9 постов/день):
    - Только video (Reels)
    - Утро (до 14:00): максимум 3 поста
    - Пауза (14:00-16:00): публикация запрещена
    - Вечер (16:00-21:00): максимум 6 постов (по 1 каждый час)
    - После 21:00: публикация запрещена до следующего дня
    - Обязательно выдерживаем интервал 60 минут между постами
    """
    if force:
        log.info("[IG_SCHEDULE] POSTNOW override: force publish (ignoring working hours)")
        return True

    if media_kind != "video":
        return False
    
    reset_ig_schedule_if_needed()
    
    now = datetime.now()
    current_time = now.time()
    current_hour = now.hour
    
    # Проверяем временные окна
    # После 21:00 - публикация запрещена
    if current_hour > 21 or current_hour < 8:
        log.info(f"[IG_SCHEDULE] DENY: outside working hours (current_hour={current_hour})")
        return False
    
    # Пауза 14:00-16:00
    if 14 <= current_hour < 16:
        log.info(f"[IG_SCHEDULE] DENY: pause window 14:00-16:00 (current_hour={current_hour})")
        return False
    
    # Утро (до 14:00): максимум 3 поста
    if current_hour < 14:
        if IG_SCHEDULE["morning_videos"] >= 3:
            log.info(f"[IG_SCHEDULE] DENY: morning limit reached ({IG_SCHEDULE['morning_videos']}/3)")
            return False
    # Вечер (16:00-21:00): максимум 6 постов
    elif 16 <= current_hour <= 21:
        if IG_SCHEDULE["afternoon_videos"] >= 6:
            log.info(f"[IG_SCHEDULE] DENY: evening limit reached ({IG_SCHEDULE['afternoon_videos']}/6)")
            return False
    
    # Проверяем интервал 60 минут между постами (для IG последний пост)
    if LAST_POST_TIME is not None:
        time_since_last = (now - LAST_POST_TIME).total_seconds()
        if time_since_last < 3600:  # 60 минут = 3600 секунд
            remaining = 3600 - time_since_last
            log.info(f"[IG_SCHEDULE] DENY: cooldown active ({remaining:.0f}s remaining)")
            return False
    
    log.info(f"[IG_SCHEDULE] ALLOW: can publish (hour={current_hour}, morning={IG_SCHEDULE['morning_videos']}/3, evening={IG_SCHEDULE['afternoon_videos']}/6)")
    return True


def ig_mark_published(media_kind: str):
    """Отмечает, что пост опубликован, и увеличивает счётчик по времени суток."""
    reset_ig_schedule_if_needed()
    
    if media_kind == "video":
        now = datetime.now()
        current_hour = now.hour
        
        # Определяем, какой счётчик увеличить, на основе текущего времени
        if current_hour < 14:
            # Утро (до 14:00)
            IG_SCHEDULE["morning_videos"] += 1
            log.info(f"[IG_SCHEDULE] Morning video published. Counter: {IG_SCHEDULE['morning_videos']}/3")
        elif 16 <= current_hour <= 21:
            # Вечер (16:00-21:00)
            IG_SCHEDULE["afternoon_videos"] += 1
            log.info(f"[IG_SCHEDULE] Evening video published. Counter: {IG_SCHEDULE['afternoon_videos']}/6")
        else:
            # Вне расписания (14:00-16:00 или после 21:00) - не должно быть
            log.warning(f"[IG_SCHEDULE] Video published outside schedule window (hour={current_hour})")


def load_published_texts():
    """Загружает список опубликованных текстов"""
    global PUBLISHED_TEXTS
    if PUBLISHED_TEXTS_FILE.exists():
        try:
            with PUBLISHED_TEXTS_FILE.open("r", encoding="utf-8") as f:
                PUBLISHED_TEXTS = json.load(f)
                # Оставляем только последние MAX_PUBLISHED_TEXTS
                if len(PUBLISHED_TEXTS) > MAX_PUBLISHED_TEXTS:
                    PUBLISHED_TEXTS = PUBLISHED_TEXTS[-MAX_PUBLISHED_TEXTS:]
        except Exception as e:
            log.warning(f"Failed to load published texts: {e}")
            PUBLISHED_TEXTS = []


def save_published_texts():
    """Сохраняет список опубликованных текстов"""
    try:
        with PUBLISHED_TEXTS_FILE.open("w", encoding="utf-8") as f:
            json.dump(PUBLISHED_TEXTS, f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"Failed to save published texts: {e}")


async def check_similar_content(text: str) -> tuple[bool, float]:
    """Проверяет semantic similarity с опубликованными постами. Возвращает (is_similar, similarity_score)"""
    if not openai_client or not text or not PUBLISHED_TEXTS:
        return (False, 0.0)
    
    # Берем последние 20 постов для проверки (увеличено для более строгой проверки)
    recent_texts = PUBLISHED_TEXTS[-20:] if len(PUBLISHED_TEXTS) > 20 else PUBLISHED_TEXTS
    
    if not recent_texts:
        return (False, 0.0)
    
    try:
        # Проверяем через OpenAI semantic similarity
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — строгий эксперт по обнаружению дубликатов контента в Telegram-канале.\n\n"
                        "Твоя задача: определить, является ли новый текст ДУБЛИКАТОМ уже опубликованного поста.\n\n"
                        "Верни ТОЛЬКО валидный JSON в формате:\n"
                        '{"is_similar": true/false, "similarity_score": 0.0-1.0, "reason": "краткое объяснение"}\n\n'
                        "КРИТИЧЕСКИ ВАЖНО: is_similar = true, если:\n"
                        "1. ОДИНАКОВАЯ ТЕМА/ИДЕЯ (даже если слова разные):\n"
                        "   - 'избегай таких людей' = 'держись подальше от таких людей' = 'не общайся с такими'\n"
                        "   - 'советы по успеху' = 'как добиться успеха' = 'правила успеха'\n"
                        "   - 'признаки токсичных людей' = 'как распознать плохих людей' = 'избегай этих людей'\n\n"
                        "2. ОДИНАКОВЫЕ КЛЮЧЕВЫЕ ФАКТЫ/ПРИМЕРЫ:\n"
                        "   - одинаковые списки признаков/характеристик\n"
                        "   - одинаковые примеры/ситуации\n"
                        "   - одинаковые выводы/советы\n\n"
                        "3. ПОХОЖИЙ ПЕРЕВОД ОДНОГО И ТОГО ЖЕ ИСТОЧНИКА:\n"
                        "   - если оба текста перевод одного и того же русского поста\n"
                        "   - даже если формулировки немного отличаются\n\n"
                        "4. similarity_score >= 0.65 (снижен порог для более строгой проверки)\n\n"
                        "is_similar = false ТОЛЬКО если:\n"
                        "- РАЗНЫЕ темы (например, 'про успех' vs 'про отношения')\n"
                        "- РАЗНЫЕ факты/примеры\n"
                        "- РАЗНАЯ основная идея\n"
                        "- similarity_score < 0.65\n\n"
                        "ПРИМЕРЫ ДУБЛИКАТОВ (is_similar = true):\n"
                        "- 'Избегай таких людей: они не держат секреты' vs 'Держись подальше от людей, которые не умеют хранить тайны'\n"
                        "- '5 признаков токсичных людей' vs 'Как распознать токсичного человека: 5 признаков'\n"
                        "- 'Советы по успеху: работай усердно' vs 'Как добиться успеха: усердная работа'\n\n"
                        "ПРИМЕРЫ НЕ ДУБЛИКАТОВ (is_similar = false):\n"
                        "- 'Как заработать деньги' vs 'Как найти работу'\n"
                        "- 'Признаки токсичных людей' vs 'Как улучшить отношения'\n"
                        "- 'Советы по карьере' vs 'Советы по здоровью'\n\n"
                        "БУДЬ СТРОГИМ: если есть хоть малейшее сомнение, что это один и тот же пост/тема — верни is_similar = true."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Новый текст:\n{text}\n\nОпубликованные тексты (последние 10):\n" + "\n---\n".join(recent_texts[:10])
                },
            ],
            response_format={"type": "json_object"},
        )
        
        result = json.loads(resp.choices[0].message.content or "{}")
        similarity_score = float(result.get("similarity_score", 0.0))
        is_similar = result.get("is_similar", False) or similarity_score >= 0.65  # Снижен порог с 0.75 до 0.65
        
        if is_similar:
            log.warning(f"SKIP: semantic duplicate (similarity={similarity_score:.2f}): {result.get('reason', '')}")
        
        return (is_similar, similarity_score)
        
    except Exception as e:
        log.warning(f"Failed to check similar content: {e}")
        return (False, 0.0)


def remove_comment_phrases(text: str) -> str:
    """Удаляет фразы про комментарии из текста"""
    if not text:
        return text
    
    import re
    phrases_to_remove = [
        r"оставьте комментарий[^\n]*",
        r"напишите ниже[^\n]*",
        r"что думаете[^\n]*",
        r"ваше мнение[^\n]*",
        r"обсудим[^\n]*",
        r"комментируйте[^\n]*",
        r"пишите в комментариях[^\n]*",
        r"fikringiz[^\n]*",
        r"yozing[^\n]*",
        r"muloqot[^\n]*",
    ]
    
    cleaned = text
    for phrase in phrases_to_remove:
        cleaned = re.sub(phrase, "", cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()


def clean_social_text(text: str) -> str:
    """
    Удаляет HTML-теги и обрезает всё после вертикальной черты для соцсетей.
    Телеграм остаётся без изменений — этот фильтр применяется только при публикации в IG/FB.
    """
    if not text:
        return ""
    # жёстко убираем служебные слова сразу, до других преобразований
    cleaned = re.sub(r"qiziqarlidunyo", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bmain\.py\b", "", cleaned, flags=re.IGNORECASE)
    # убираем теги
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    # обрезаем по первому разделителю |
    if "|" in cleaned:
        cleaned = cleaned.split("|", 1)[0]
    # схлопываем лишние пробелы и обрезаем пунктуацию по краям
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n.,;:!-|")
    return cleaned.strip()


def ensure_utf8_text(text: str) -> str:
    """Пытается привести строку к корректной UTF-8, убирая битые символы."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="ignore")
    try:
        return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return str(text)


def split_text_for_carousel(text: str, max_chars: int = 700) -> list[str]:
    """Делит текст на части для слайдов, чтобы каждая была умеренного размера."""
    chunks = []
    current = []
    total = 0
    # разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    for sent in sentences:
        if not sent:
            continue
        if total + len(sent) > max_chars and current:
            chunks.append(" ".join(current).strip())
            current = [sent]
            total = len(sent)
        else:
            current.append(sent)
            total += len(sent)
    if current:
        chunks.append(" ".join(current).strip())
    return chunks or [text.strip()]


def wrap_lines_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Перенос строк с учётом реальной ширины."""
    words = text.split()
    lines = []
    line = ""
    for word in words:
        candidate = (line + " " + word).strip()
        if not candidate:
            continue
        bbox = draw.textbbox((0, 0), candidate, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def parse_accent_tokens(text: str) -> list[tuple[str, bool]]:
    """Парсит *выделенные* слова: возвращает список (token, is_accent)."""
    tokens = []
    parts = text.split("*")
    # если количество частей чётное, значит нет корректных пар — трактуем как обычный текст
    if len(parts) < 3:
        return [(text, False)]
    accent = False
    for part in parts:
        if part == "":
            accent = not accent
            continue
        tokens.append((part, accent))
        accent = not accent
    return tokens


def wrap_tokens_to_width(draw: ImageDraw.ImageDraw, tokens: list[tuple[str, bool]], font: ImageFont.FreeTypeFont, max_width: int) -> list[list[tuple[str, bool]]]:
    """Перенос строк с учётом ширины для токенов с подсветкой."""
    lines: list[list[tuple[str, bool]]] = []
    line: list[tuple[str, bool]] = []

    def measure(line_tokens: list[tuple[str, bool]]) -> float:
        if not line_tokens:
            return 0
        joined = " ".join(t[0] for t in line_tokens)
        bbox = draw.textbbox((0, 0), joined, font=font)
        return bbox[2] - bbox[0]

    for tok in tokens:
        if not line:
            line.append(tok)
            if measure(line) > max_width and len(tok[0]) > 0:
                lines.append(line)
                line = []
            continue
        candidate = line + [tok]
        if measure(candidate) <= max_width:
            line.append(tok)
        else:
            lines.append(line)
            line = [tok]
    if line:
        lines.append(line)
    return lines


def create_carousel_images(text: str) -> list[str]:
    """
    Создаёт изображения с текстом для карусели.
    Возвращает список путей к временным PNG-файлам.
    """
    base_dir = Path("D:/Project/Auto Telegramm")
    backgrounds_dir = base_dir / "backgrounds"
    fonts_dir = base_dir / "fonts"
    tmp_dir = Path("tmp_media") / "carousel"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    bg_files = [p for p in backgrounds_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    font_files = [p for p in fonts_dir.glob("*.ttf")]
    if not bg_files or not font_files:
        log.error("Carousel assets missing: backgrounds or fonts not found")
        return []

    slides = []
    chunks = split_text_for_carousel(text)

    for idx, chunk in enumerate(chunks, start=1):
        bg_path = random.choice(bg_files)
        font_path = random.choice(font_files)
        img = Image.open(bg_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        max_text_width = int(img.width * 0.55)  # компактный блок текста
        max_text_height = int(img.height * 0.8)

        # подбираем размер шрифта (крупный, не ниже 50)
        font_size = 72
        min_font = 50
        chosen_lines = []
        chosen_font = ImageFont.truetype(str(font_path), font_size)

        while font_size >= min_font:
            font = ImageFont.truetype(str(font_path), font_size)
            lines = wrap_lines_to_width(draw, chunk, font, max_text_width)
            text_block = "\n".join(lines)
            bbox = draw.multiline_textbbox((0, 0), text_block, font=font, align="center")
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            if text_w <= max_text_width and text_h <= max_text_height and len(lines) <= 18:
                chosen_lines = lines
                chosen_font = font
                break
            font_size -= 2

        # если не уложились, жёстко режем строки по 18
        if not chosen_lines:
            lines = wrap_lines_to_width(draw, chunk, chosen_font, max_text_width)
            chosen_lines = lines[:18]

        final_text = "\n".join(chosen_lines)
        bbox = draw.multiline_textbbox((0, 0), final_text, font=chosen_font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (img.width - text_w) / 2
        y = (img.height - text_h) / 2

        # тень
        shadow_offset = 2
        draw.multiline_text(
            (x + shadow_offset, y + shadow_offset),
            final_text,
            font=chosen_font,
            fill="black",
            align="center",
        )
        # основной текст
        draw.multiline_text(
            (x, y),
            final_text,
            font=chosen_font,
            fill="white",
            align="center",
        )

        out_path = tmp_dir / f"carousel_{uuid.uuid4().hex}.png"
        img.save(out_path, format="PNG")
        log.info(f"[PILLOW] Слайд №{idx} успешно создан и сохранен")
        slides.append(str(out_path))

    return slides


def summarize_for_image(text: str) -> str:
    """Краткое и ёмкое описание для одного слайда (узбекский, коротко)."""
    txt = (text or "").strip()
    if not txt:
        return ""
    if len(txt) <= 260:
        return txt
    if not openai_client:
        return txt[:260]


def append_history(social: str, media_type: str, url: str, cost: float):
    """Пишет строку истории в history.log"""
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        line = f"[{ts}] | Соцсеть: {social} | Тип: {media_type} | Ссылка: {url or '-'} | Цена перевода: ${cost:.4f}\n"
        with HISTORY_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        log.warning(f"Failed to append history: {e}")


def send_admin_error(error_message: str):
    """Отправляет ошибку админу в Telegram (синхронно через Telegram Bot API)."""
    if not ADMIN_TELEGRAM_ID:
        return
    try:
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        payload = {
            "chat_id": ADMIN_TELEGRAM_ID,
            "text": f"[ERROR {ts}]\n{error_message}",
        }
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data=payload, timeout=10)
    except Exception as e:
        log.warning(f"Failed to notify admin: {e}")


def send_report_message(text: str):
    """Отправляет дневной отчёт в REPORT_CHAT_ID."""
    chat_id = REPORT_CHAT_ID or ADMIN_TELEGRAM_ID
    if not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except Exception as e:
        log.warning(f"Failed to send report message: {e}")


def rotate_history_log():
    """Копирует history.log в reports/report_YYYY_MM_DD.log и очищает основной лог."""
    try:
        if not HISTORY_LOG.exists():
            return
        REPORTS_DIR.mkdir(exist_ok=True)
        today = datetime.now().strftime("%Y_%m_%d")
        target = REPORTS_DIR / f"report_{today}.log"
        target.write_text(HISTORY_LOG.read_text(encoding="utf-8"), encoding="utf-8")
        HISTORY_LOG.write_text("", encoding="utf-8")
        log.info(f"History rotated to {target}")
    except Exception as e:
        log.warning(f"Failed to rotate history log: {e}")


def create_single_art_image(text: str) -> str:
    """
    Создает одно изображение с цитатой.
    Возвращает путь к PNG файлу.
    """
    base_dir = Path("D:/Project/Auto Telegramm")
    backgrounds_dir = base_dir / "backgrounds"
    fonts_dir = base_dir / "fonts"
    tmp_dir = Path("tmp_media") / "single_art"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    bg_files = [p for p in backgrounds_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    font_files = [p for p in fonts_dir.glob("*.ttf")]
    if not bg_files or not font_files:
        log.error("Single art assets missing: backgrounds or fonts not found")
        return ""

    bg_path = random.choice(bg_files)
    # Пытаемся найти Bold-шрифт
    bold_fonts = [p for p in font_files if "bold" in p.name.lower()]
    font_path = bold_fonts[0] if bold_fonts else font_files[0]

    img = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Очистка HTML и укороченный текст для изображения
    plain = re.sub(r"<[^>]+>", "", text or "")
    summary = summarize_for_image(plain).upper()

    max_text_width = int(img.width * 0.45)  # компактный блок ~45%
    max_text_height = int(img.height * 0.8)

    font_size = 140
    min_font = 100
    chosen_lines = []
    chosen_font = ImageFont.truetype(str(font_path), font_size)
    tokens = parse_accent_tokens(summary)

    def measure_lines(lines_tokens: list[list[tuple[str, bool]]], font: ImageFont.FreeTypeFont) -> tuple[float, float, list[float], list[float]]:
        spacing_px_inner = int(font.size * 0.6)
        line_heights_inner = []
        line_widths_inner = []
        for line in lines_tokens:
            text_line = " ".join(t[0] for t in line)
            bbox = draw.textbbox((0, 0), text_line, font=font)
            line_widths_inner.append(bbox[2] - bbox[0])
            line_heights_inner.append(bbox[3] - bbox[1])
        total_h_inner = sum(line_heights_inner) + spacing_px_inner * (len(lines_tokens) - 1 if lines_tokens else 0)
        max_w_inner = max(line_widths_inner) if line_widths_inner else 0
        return max_w_inner, total_h_inner, line_widths_inner, line_heights_inner

    while font_size >= min_font:
        font = ImageFont.truetype(str(font_path), font_size)
        lines_tokens = wrap_tokens_to_width(draw, tokens, font, max_text_width)
        max_w, total_h, line_widths, line_heights = measure_lines(lines_tokens, font)

        if max_w <= max_text_width and total_h <= max_text_height and len(lines_tokens) <= 12:
            chosen_lines = lines_tokens
            chosen_font = font
            chosen_line_widths = line_widths
            chosen_line_heights = line_heights
            break
        font_size -= 2

    if not chosen_lines:
        lines_tokens = wrap_tokens_to_width(draw, tokens, chosen_font, max_text_width)
        chosen_lines = lines_tokens[:12]
        max_w, total_h, chosen_line_widths, chosen_line_heights = measure_lines(chosen_lines, chosen_font)
    else:
        max_w, total_h = max(chosen_line_widths), sum(chosen_line_heights) + int(chosen_font.size * 0.6) * (len(chosen_lines) - 1 if chosen_lines else 0)

    # Если всё ещё не влезает — уменьшаем шрифт дополнительно
    reduce_steps = 0
    while (max_w > max_text_width or total_h > max_text_height) and chosen_font.size > min_font:
        new_size = max(min_font, int(chosen_font.size * 0.9))
        chosen_font = ImageFont.truetype(str(font_path), new_size)
        chosen_lines = wrap_tokens_to_width(draw, tokens, chosen_font, max_text_width)[:12]
        max_w, total_h, chosen_line_widths, chosen_line_heights = measure_lines(chosen_lines, chosen_font)
        reduce_steps += 1
        if reduce_steps > 10:
            break

    spacing_px = int(chosen_font.size * 0.6)
    x_start = (img.width - max_w) / 2
    y_start = (img.height - total_h) / 2

    # Лёгкое затемнение под текст для читаемости (50%)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, int(255 * 0.50)))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    accent_color = "#D4AF37"  # золотисто-бежевый

    y = y_start
    for line_idx, line in enumerate(chosen_lines):
        text_line = " ".join(t[0] for t in line)
        line_bbox = draw.textbbox((0, 0), text_line, font=chosen_font)
        line_width = line_bbox[2] - line_bbox[0]
        x = (img.width - line_width) / 2

        cursor_x = x
        for i, (tok, is_accent) in enumerate(line):
            fill_color = accent_color if is_accent else "white"
            tok_bbox = draw.textbbox((0, 0), tok, font=chosen_font)
            tok_width = tok_bbox[2] - tok_bbox[0]
            space_bbox = draw.textbbox((0, 0), " ", font=chosen_font)
            space_w = space_bbox[2] - space_bbox[0]
            draw.text(
                (cursor_x, y),
                tok,
                font=chosen_font,
                fill=fill_color,
                stroke_width=5,
                stroke_fill="black",
            )
            cursor_x += tok_width
            if i != len(line) - 1:
                cursor_x += space_w

        y += line_heights[line_idx]
        if line_idx != len(chosen_lines) - 1:
            y += spacing_px

    out_path = tmp_dir / f"single_art_{uuid.uuid4().hex}.png"
    img.save(out_path, format="PNG")
    log.info("[PILLOW] Single art post created successfully")
    return str(out_path)


def _rounded_mask(size: tuple[int, int], radius: int) -> np.ndarray:
    """Создает маску с закругленными углами (0..1)."""
    w, h = size
    mask_img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask_img)
    draw.rounded_rectangle([(0, 0), (w, h)], radius=radius, fill=255)
    arr = np.array(mask_img).astype("float32") / 255.0
    return arr


def _render_caption_image(text: str, width: int = 1080, height: int = 200) -> Path | None:
    """Рендерит текст заголовка в PNG и возвращает путь."""
    if not text:
        return None
    try:
        tmp_dir = Path("tmp_media") / "captions"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        # Берём первую строку
        first_line = text.splitlines()[0].strip()
        # Ограничиваем длину
        if len(first_line) > 80:
            first_line = first_line[:80] + "..."
        bbox = draw.textbbox((0, 0), first_line, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pos = ((width - tw) // 2, (height - th) // 2)
        draw.text(pos, first_line, font=font, fill=(255, 215, 0, 255))
        out_path = tmp_dir / f"caption_{uuid.uuid4().hex}.png"
        img.save(out_path, "PNG")
        return out_path
    except Exception as e:
        log.warning(f"Caption render failed: {e}")
        return None


def process_video(local_path: Path, caption: str | None = None, speed_multiplier: float = 1.01, bg_color_override: tuple | None = None, brightness_adjust: float = 0.0, random_crop: bool = False, voiceover_path: str | None = None) -> Path | None:
    """
    Собирает видео в стиле Reels:
    - Канвас 1080x1920 тёмный
    - Видео ~80% ширины, по центру, скруглённые углы
    - Логотип поверх
    - Опционально: заголовок из первой строки caption
    
    Параметры для "Плана Б":
    - speed_multiplier: множитель скорости (1.01, 1.02, 1.03)
    - bg_color_override: принудительный цвет фона для повторных попыток
    - brightness_adjust: коррекция яркости (0.0 до 0.03)
    - random_crop: случайная обрезка 5-15px с каждой стороны для обхода алгоритмов Meta
    
    Возвращает путь к обработанному файлу или None при ошибке.
    """
    # === IRONCLAD CONFIGURATION: DO NOT ALTER ===
    # BITRATE: 5000k (Strict limit for Supabase)
    # PRESET: slow (High quality encoding)
    # CRF: 19 (Optimal quality/size balance)
    # STITCHES: Checked for duration (No crashes)
    # AUDIO: Pro processing Pitch 0.2 / Tempo 0.5
    # ============================================
    try:
        header_path = (Path(__file__).parent / "header.gif").resolve()
        clip = VideoFileClip(str(local_path))
        duration = clip.duration
        
        # ПЛАН Б: Случайная обрезка (Random Crop) для обхода алгоритмов Meta
        if random_crop:
            original_w, original_h = clip.w, clip.h
            crop_pixels = random.randint(5, 15)
            
            # Обрезаем со всех сторон
            x1 = crop_pixels
            y1 = crop_pixels
            x2 = original_w - crop_pixels
            y2 = original_h - crop_pixels
            
            clip = clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)
            # Растягиваем обратно до исходного размера
            clip = clip.resize((original_w, original_h))
            log.info(f"[PLAN B] Random crop applied: {crop_pixels}px from each side, resized back to {original_w}x{original_h}")

        canvas_size = (1080, 1920)
        dark_palette = [
            (0, 0, 0),
            (10, 10, 20),
            (20, 20, 30),
            (12, 8, 24),
            (6, 12, 18),
        ]
        bg_color = bg_color_override if bg_color_override is not None else random.choice(dark_palette)
        
        # Логирование параметров обработки
        if bg_color_override is not None or speed_multiplier > 1.01 or brightness_adjust != 0.0:
            log.info(f"[PLAN B] Video processing with unique parameters: speed={speed_multiplier:.3f}, bg={bg_color}, brightness={brightness_adjust:+.3f}")
        
        # ПЛАН Б: Случайная обрезка (Random Crop) для обхода алгоритмов Meta
        if brightness_adjust != 0.0:
            crop_pixels = random.randint(5, 15)
            original_w, original_h = clip.w, clip.h
            
            # Обрезаем со всех сторон
            x1 = crop_pixels
            y1 = crop_pixels
            x2 = original_w - crop_pixels
            y2 = original_h - crop_pixels
            
            clip = clip.crop(x1=x1, y1=y1, x2=x2, y2=y2)
            log.info(f"[PLAN B] Random crop applied: {crop_pixels}px from each side ({original_w}x{original_h} -> {clip.w}x{clip.h})")
        
        # Золотой шаблон: одинаковые поля со всех сторон (10% margin)
        margin = 0.10
        target_w = int(canvas_size[0] * (1 - margin))
        target_h = int(canvas_size[1] * (1 - margin))
        scale = min(target_w / clip.w, target_h / clip.h)
        new_w = int(clip.w * scale)
        new_h = int(clip.h * scale)
        log.info(f"[DEBUG] Golden Template: Resizing video to {new_w}x{new_h} on canvas {canvas_size} with equal margins")

        # После crop видео ресайзится обратно до нужного размера для 1080x1920 канваса
        clip = clip.resize(width=new_w, height=new_h)
        clip = clip.fx(vfx_all.speedx, speed_multiplier)
        
        # Применяем коррекцию яркости (План Б)
        if brightness_adjust != 0.0:
            clip = clip.fx(vfx_all.colorx, 1.0 + brightness_adjust)
            log.info(f"[PLAN B] Brightness adjusted: {brightness_adjust:+.3f}")
        
        # SMART SLICER & ZOOM: Нарезка на сегменты с Crossfade и легким зумом (замена шума)
        if brightness_adjust != 0.0 or speed_multiplier > 1.01 or random_crop:
            try:
                segment_duration = random.uniform(3.5, 4.0)  # Длина сегмента
                fade_duration = 0.25  # Длина переходов (фиксированная)
                zoom_factor = 1.03  # Легкий зум для изменения цифровой подписи
                
                segments = []
                current_time = 0
                
                while current_time < duration:
                    # ПРОВЕРКА: end_time никогда не больше duration
                    end_time = min(current_time + segment_duration, duration)
                    
                    # Убеждаемся, что сегмент имеет минимальную длину
                    if end_time - current_time < 0.5:
                        break
                    
                    start_t = _clamp_t(current_time, clip.duration)
                    end_t = _clamp_t(end_time, clip.duration)
                    if end_t <= start_t:
                        end_t = _clamp_t(start_t + 0.5, clip.duration)
                    segment = clip.subclip(start_t, end_t)
                    
                    # Применяем легкий зум к каждому сегменту
                    segment = segment.resize(zoom_factor)
                    
                    # Добавляем fade-in и fade-out для плавных переходов
                    segment_duration_actual = segment.duration
                    if len(segments) > 0 and segment_duration_actual > fade_duration * 2:
                        # Fade-in для всех сегментов кроме первого
                        segment = segment.fadein(fade_duration)
                    
                    if segment_duration_actual > fade_duration * 2:
                        # Fade-out для всех сегментов
                        segment = segment.fadeout(fade_duration)
                    
                    segments.append(segment)
                    current_time = end_time
                
                if len(segments) > 1:
                    from moviepy import concatenate
                    clip = concatenate_videoclips(segments, method="compose")
                    log.info(f"[SMART SLICER] Video sliced into {len(segments)} segments with Fade transitions & Zoom 1.03x")
                elif len(segments) == 1:
                    clip = segments[0]
                    log.info(f"[SMART SLICER] Single segment with Zoom 1.03x applied")
            except Exception as e:
                log.warning(f"[SMART SLICER] Failed to apply: {e}, using original clip")

        # MICRO-STITCHES: Невидимые переходы (разделение на 3 сегмента + удаление 2 кадров)
        if duration > 3.0:  # Применяем только для видео длиннее 3 секунд
            try:
                fps = clip.fps or 30
                frame_duration = 1.0 / fps
                
                # Duration Guard: Для коротких видео снижаем интенсивность вырезов
                if duration < 10.0:
                    cut_frames = 1  # Короткое видео: удаляем только 1 кадр
                    trim_duration = 0.3  # Короткое видео: обрезаем только 0.3 сек
                else:
                    cut_frames = 2  # Длинное видео: удаляем 2 кадра
                    trim_duration = 1.5  # Длинное видео: обрезаем 1.5 сек
                
                cut_time = cut_frames * frame_duration
                
                # Определяем 3 случайных точки разреза
                segment_1_end = random.uniform(duration * 0.2, duration * 0.4)
                segment_2_end = random.uniform(duration * 0.6, duration * 0.8)
                
                # Безопасные границы: гарантируем, что не выходим за пределы duration
                seg1_start = 0
                seg1_end = min(segment_1_end - cut_time, duration)
                seg2_start = min(segment_1_end + cut_time, duration)
                seg2_end = min(segment_2_end - cut_time, duration)
                seg3_start = min(segment_2_end + cut_time, duration - 0.1)
                seg3_end = duration
                
                # Создаем 3 сегмента с микро-вырезами (если seg3 валидный)
                segments = []
                if seg1_end > seg1_start:
                    s1 = _clamp_t(seg1_start, clip.duration)
                    e1 = _clamp_t(seg1_end, clip.duration)
                    if e1 > s1:
                        segments.append(clip.subclip(s1, e1))
                if seg2_end > seg2_start:
                    s2 = _clamp_t(seg2_start, clip.duration)
                    e2 = _clamp_t(seg2_end, clip.duration)
                    if e2 > s2:
                        segments.append(clip.subclip(s2, e2))
                if seg3_start < seg3_end and seg3_start < duration - 0.05:
                    s3 = _clamp_t(seg3_start, clip.duration)
                    e3 = _clamp_t(seg3_end, clip.duration)
                    if e3 > s3:
                        segments.append(clip.subclip(s3, e3))
                
                # Склеиваем сегменты
                if len(segments) > 1:
                    clip = concatenate_videoclips(segments, method="compose")
                else:
                    log.warning("[MICRO-STITCH] Not enough valid segments, skipping stitch")
                
                # Random Trim
                    if clip.duration > trim_duration + 1.0:
                        if random.choice([True, False]):
                            # Отрезаем с начала
                            s = _clamp_t(trim_duration, clip.duration)
                            e = _clamp_t(clip.duration, clip.duration)
                            if e <= s:
                                e = _clamp_t(s + 0.5, clip.duration)
                            clip = clip.subclip(s, e)
                            log.info(f"[MICRO-STITCH] Trimmed {trim_duration}s from start")
                        else:
                            # Отрезаем с конца
                            s = _clamp_t(0, clip.duration)
                            e = _clamp_t(clip.duration - trim_duration, clip.duration)
                            if e <= s:
                                e = _clamp_t(s + 0.5, clip.duration)
                            clip = clip.subclip(s, e)
                            log.info(f"[MICRO-STITCH] Trimmed {trim_duration}s from end")
                
                duration = clip.duration
                log.info(f"[MICRO-STITCH] Applied 3 segments with frame cuts. New duration: {duration:.2f}s")
            except Exception as stitch_err:
                log.warning(f"[MICRO-STITCH] Failed to apply: {stitch_err}, using original clip")

        # Маска скругленных углов
        radius = 45
        mask_arr = _rounded_mask((new_w, new_h), radius)
        mask_clip = ImageClip(mask_arr).set_duration(duration)
        mask_clip.ismask = True  # MoviePy 2.1: явное указание маски
        clip = clip.set_mask(mask_clip)

        layers = []
        canvas_clip = ColorClip(canvas_size, color=bg_color).set_duration(duration)
        layers.append(canvas_clip)
        layers.append(clip.set_position("center"))

        # Логотип отключён по требованию

        out_path = Path("tmp_media") / f"proc_{local_path.stem}.mp4"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        final_video = CompositeVideoClip(layers)

        # PROFESSIONAL AUDIO: Озвучка ElevenLabs ИЛИ обработка оригинального аудио
        if voiceover_path and Path(voiceover_path).exists():
            try:
                # 🎙️ ОЗВУЧКА: Используем ElevenLabs вместо оригинального аудио
                
                voiceover_audio = AudioFileClip(str(voiceover_path))
                
                # Подгоняем длительность озвучки под длительность видео
                if voiceover_audio.duration < duration:
                    # Если озвучка короче - повторяем оригинальное аудио после неё
                    if clip.audio is not None:
                        remaining_duration = duration - voiceover_audio.duration
                        audio_end = min(clip.audio.duration, remaining_duration)
                        audio_end = _clamp_t(audio_end, clip.audio.duration)
                        original_audio = clip.audio.subclip(0, audio_end)
                       # from moviepy import concatenate_audioclips
                        audio_track = concatenate_audioclips([voiceover_audio, original_audio])
                    else:
                        # Если нет оригинального аудио - просто тишина после озвучки
                        audio_track = voiceover_audio
                elif voiceover_audio.duration > duration:
                    # Видео короче голоса — замедляем видео, чтобы они совпали
                     new_speed = duration / voiceover_audio.duration
                     final_video = final_video.fx(vfx_all.speedx, new_speed)
                     audio_track = voiceover_audio
                     log.info(f"[SYNC] Видео замедлено до {new_speed:.2f} для совпадения с голосом")
                else:
                    audio_track = voiceover_audio
                
                # Принудительно задаем fps для аудио перед наложением на видео
                if audio_track is not None:
                    audio_track = audio_track.set_fps(44100)
                final_video = final_video.set_audio(audio_track)
                log.info(f"[ELEVENLABS] ✅ Voiceover applied to video: {Path(voiceover_path).name}")
                
                # Удаляем временный файл озвучки
                Path(voiceover_path).unlink()
                log.info("[ELEVENLABS] Voiceover file cleaned up after applying")
            except Exception as voiceover_err:
                log.warning(f"[ELEVENLABS] Failed to apply voiceover: {voiceover_err}, using original audio")
                # Fallback: используем оригинальное аудио
                if clip.audio is not None:
                    final_video = final_video.set_audio(clip.audio)
        elif clip.audio is not None:
            try:
                audio_track = clip.audio
                
                # Smart Pitch Shift: -0.2 до +0.2 полутона (всегда применяем)
                semitones = random.uniform(-0.2, 0.2)
                pitch_factor = 2 ** (semitones / 12)
                original_fps = audio_track.fps or 44100
                new_fps = int(original_fps * pitch_factor)
                
                # Изменяем fps аудио для эффекта pitch shift
                audio_track = audio_track.with_fps(new_fps)
                log.info(f"[PROFESSIONAL_AUDIO] Pitch shifted: {semitones:+.3f} semitones (fps: {original_fps} -> {new_fps})")
                
                # Tempo Shift: ±0.5% изменение скорости аудио
                tempo_change = random.uniform(0.995, 1.005)  # 99.5% - 100.5%
                if abs(tempo_change - 1.0) > 0.001:
                    # Меняем скорость аудио через speedx
                    audio_track = audio_track.fx(afx_all.audio_speedx, tempo_change)
                    log.info(f"[PROFESSIONAL_AUDIO] Tempo adjusted: {tempo_change:.4f}x ({(tempo_change-1)*100:+.2f}%)")
                
                # Применяем обработанное аудио
                final_video = final_video.set_audio(audio_track)
                log.info("[PROFESSIONAL_AUDIO] High-quality audio processing applied (NO NOISE)")
            except Exception as audio_err:
                log.warning(f"[PROFESSIONAL_AUDIO] Failed to process audio: {audio_err}, using original audio")
        
        # Размытие субтитров: создаем размытый прямоугольник внизу видео (где обычно субтитры)
        def add_blur_to_captions(clip):
            # Обрезаем кусок снизу, размываем его и накладываем обратно
            overlay = clip.crop(y1=int(clip.h*0.8), y2=clip.h).fx(vfx_all.blur, 20)
            return CompositeVideoClip([clip, overlay.set_position(("center", "bottom"))])
        
        # Применяем размытие к видео
        #final_video = add_blur_to_captions(final_video)
        final_video = final_video.set_duration(final_video.duration - 0.5)
        log.info("[BLUR] Blur applied to bottom 20% of video (captions area)")
        
        # === SAFE_DURATION_FIX: гарантированное закрытие и безопасная длительность ===
        eps = 0.25  # Safety margin для избежания WinError 32 при доступе за границы
        safe_duration = final_video.duration - eps
        
        # Проверяем, есть ли аудио и синхронизируем длительность видео и аудио
        if final_video.audio is not None:
            audio_duration = final_video.audio.duration
            safe_duration = min(safe_duration, audio_duration - eps)
            log.info(f"[SAFE_DURATION] Video: {final_video.duration:.2f}s, Audio: {audio_duration:.2f}s → Safe: {safe_duration:.2f}s (eps={eps})")
            s = _clamp_t(0, final_video.duration)
            e = _clamp_t(safe_duration, final_video.duration)
            if e <= s:
                e = _clamp_t(s + 0.5, final_video.duration)
            final_video = final_video.subclip(s, e)
            final_video.audio = final_video.audio.subclip(s, e)
        else:
            log.info(f"[SAFE_DURATION] No audio track. Trimming video: {final_video.duration:.2f}s → {safe_duration:.2f}s")
            s = _clamp_t(0, final_video.duration)
            e = _clamp_t(safe_duration, final_video.duration)
            if e <= s:
                e = _clamp_t(s + 0.5, final_video.duration)
            final_video = final_video.subclip(s, e)
        
        # Запись видео с гарантированным закрытием ресурсов
        try:
            final_video.write_videofile(
                str(out_path),
                codec="libx264",
                audio_codec="aac",
                fps=30,
                preset="slow",
                bitrate="6000k",
                ffmpeg_params=[
                    "-crf", "18",
                    "-pix_fmt", "yuv420p"
                ],
                logger=None,
            )
            log.info("INFO | [PROCESS] Video unique processing: Success")
        finally:
            # Гарантированное закрытие всех открытых клипов (избегаем WinError 32)
            try:
                if hasattr(final_video, 'close'):
                    final_video.close()
                if hasattr(final_video, 'audio') and final_video.audio is not None and hasattr(final_video.audio, 'close'):
                    final_video.audio.close()
            except Exception as close_err:
                log.warning(f"[SAFE_DURATION] Error closing video/audio clips: {close_err}")
        
        log.info("[SAFE_DURATION] All clips closed successfully")
        
        # 🔄 AUTO-COMPRESS: Проверка размера и автоматическое пережатие (SIZE GUARD)
        try:
            file_size_mb = out_path.stat().st_size / (1024 * 1024)
            max_size_mb = 50  # Лимит для Telegram и Instagram
            
            if file_size_mb > max_size_mb:
                log.warning(f"[AUTO-COMPRESS] File too large: {file_size_mb:.2f} MB > {max_size_mb} MB")
                log.info("[AUTO-COMPRESS] Re-encoding with CRF 22 to reduce size...")
                
                # Создаем временный файл для пережатой версии
                compressed_path = out_path.parent / f"compressed_{out_path.name}"
                
                # ПЕРВАЯ ПОПЫТКА: CRF 22, bitrate 4000k через ffmpeg (file-based)
                cmd_crf22 = [
                    "ffmpeg", "-y",
                    "-i", str(out_path),
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-b:v", "4000k",
                    "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    str(compressed_path)
                ]
                
                try:
                    subprocess.run(cmd_crf22, check=True, capture_output=True, timeout=600)
                    compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                    log.info(f"[AUTO-COMPRESS] New size with CRF 22: {compressed_size_mb:.2f} MB (was {file_size_mb:.2f} MB)")
                    
                    if compressed_size_mb <= max_size_mb:
                        # Успех! Заменяем оригинал
                        out_path.unlink()
                        compressed_path.rename(out_path)
                        log.info(f"✅ [AUTO-COMPRESS] Success! File compressed to {compressed_size_mb:.2f} MB")
                    else:
                        # ВТОРАЯ ПОПЫТКА: CRF 24, bitrate 3000k через ffmpeg
                        log.warning(f"[AUTO-COMPRESS] Still too large ({compressed_size_mb:.2f} MB), trying CRF 24...")
                        compressed_path.unlink()  # Удаляем первую попытку
                        
                        cmd_crf24 = [
                            "ffmpeg", "-y",
                            "-i", str(out_path),
                            "-c:v", "libx264",
                            "-preset", "medium",
                            "-b:v", "3000k",
                            "-crf", "24",
                            "-pix_fmt", "yuv420p",
                            "-c:a", "aac",
                            "-b:a", "128k",
                            str(compressed_path)
                        ]
                        
                        subprocess.run(cmd_crf24, check=True, capture_output=True, timeout=600)
                        final_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                        log.info(f"[AUTO-COMPRESS] Final size with CRF 24: {final_size_mb:.2f} MB")
                        
                        out_path.unlink()
                        compressed_path.rename(out_path)
                        log.info(f"✅ [AUTO-COMPRESS] Compressed with CRF 24 to {final_size_mb:.2f} MB")
                
                except subprocess.TimeoutExpired:
                    log.error("[AUTO-COMPRESS] Compression timeout (600s), keeping original file")
                except subprocess.CalledProcessError as ffmpeg_err:
                    log.error(f"[AUTO-COMPRESS] ffmpeg compression failed: {ffmpeg_err}, keeping original file")
                    if compressed_path.exists():
                        compressed_path.unlink()
            else:
                log.info(f"✅ [SIZE CHECK] File size OK: {file_size_mb:.2f} MB <= {max_size_mb} MB (HD quality preserved)")
        except Exception as compress_err:
            log.error(f"[AUTO-COMPRESS] Failed: {compress_err}")
            # Продолжаем с оригинальным файлом
        
        return out_path
    except Exception as e:
        log.error(f"Video processing failed, not sending original: {e}")
        try:
            clip.close()
        except Exception:
            pass
        return None


async def prepare_video_for_ready(application, item: dict) -> Path | None:
    """
    СИСТЕМА КОНВЕЙЕР: Подготавливает видео заранее с уникализацией.
    - Скачивает сырое видео из Telegram ИЛИ использует Instagram-источник
    - Применяет микро-зум 2%, случайную обрезку, pitch ±0.5
    - Сжимает до 15-25 МБ (bitrate 2500k)
    - Сохраняет в ready_to_publish
    - Возвращает путь к готовому файлу или None
    """
    try:
        tmp_dir = Path("tmp_media")
        tmp_dir.mkdir(exist_ok=True)
        
        video_file_id = item["file_id"]
        is_instagram_source = False
        
        # ✅ ПРОВЕРКА: Instagram-источник или Telegram
        if video_file_id == "instagram_source" and item.get("instagram_video_path"):
            # Используем уже скачанное видео из Instagram
            instagram_path = Path(item["instagram_video_path"])
            if not instagram_path.exists():
                log.error(f"[CONVEYOR] Instagram video not found: {instagram_path}")
                return None
            local_path = instagram_path
            is_instagram_source = True
            log.info(f"[CONVEYOR] Using Instagram video: {local_path.name}")
        else:
            # Стандартный путь: скачиваем из Telegram
            file_obj = await application.bot.get_file(video_file_id)
            remote_path = getattr(file_obj, "file_path", "") or ""
            suffix = Path(remote_path).suffix or ".mp4"
            local_path = tmp_dir / f"{video_file_id}{suffix}"
            
            # Скачиваем сырое видео
            await file_obj.download_to_drive(custom_path=str(local_path))
            log.info(f"[CONVEYOR] Downloaded raw video: {local_path.name}")
        
        # Уникализация: микро-зум 2% + случайная обрезка + pitch
        caption = item.get("caption", "")
        speed_mult = random.uniform(1.01, 1.03)  # Случайная скорость 1.01-1.03
        brightness = random.uniform(0.01, 0.03)  # Случайная яркость
        voiceover_path = item.get("voiceover_path")  # 🎙️ Путь к озвучке
        
        processed_path = process_video(
            local_path,
            caption,
            speed_multiplier=speed_mult,
            brightness_adjust=brightness,
            random_crop=True,  # Всегда применяем crop для готовых постов
            voiceover_path=voiceover_path  # 🎙️ Передаем озвучку
        )
        
        if not processed_path or not Path(processed_path).exists():
            log.error(f"[CONVEYOR] Video processing failed for {video_file_id}")
            # Удаляем только если это НЕ Instagram (временный файл Telegram)
            if not is_instagram_source and local_path.exists():
                await safe_unlink(local_path)
            return None
        
        # Сохраняем в ready_to_publish с уникальным именем
        ready_filename = f"ready_{uuid.uuid4().hex[:8]}_{int(time_module.time())}.mp4"
        ready_path = READY_TO_PUBLISH_DIR / ready_filename
        
        # 🔍 DIAGNOSTICS: Логируем пути при сохранении
        log.info(f"[CONVEYOR] Saving ready video: {ready_filename}")
        log.info(f"[CONVEYOR] Ready directory: {READY_TO_PUBLISH_DIR.resolve()}")
        log.info(f"[CONVEYOR] Ready path (absolute): {ready_path.resolve()}")
        
        shutil.move(str(processed_path), str(ready_path))
        
        # Проверяем размер файла (целевой 15-25 МБ)
        file_size_mb = ready_path.stat().st_size / (1024 * 1024)
        log.info(f"[CONVEYOR] Ready video saved: {ready_filename} ({file_size_mb:.2f} MB)")
        log.info(f"[CONVEYOR] Saved to (absolute): {ready_path.resolve()}")
        log.info(f"[CONVEYOR] File exists after save: {ready_path.exists()}")

        # ГАРАНТИЯ: Сразу сохраняем sidecar meta (.json) — не полагаемся на дальнейшие шаги
        try:
            meta_path = ready_path.with_suffix('.mp4.json')
            caption_tg_local = prepare_caption_for_publish_tg(caption) if caption else ""
            caption_meta_local = prepare_caption_for_publish_meta(caption) if caption else ""
            meta_obj = {
                "ready_file": ready_path.name,
                "created_at": datetime.utcnow().isoformat(),
                "caption": caption or "",
                "caption_tg": caption_tg_local or "",
                "caption_meta": caption_meta_local or "",
                "source_id": item.get("id") or item.get("video_file_id") or item.get("ig_media_id") or ""
            }
            meta_path.write_text(json.dumps(meta_obj, ensure_ascii=False, indent=2), encoding='utf-8')
            log.info(f"[CONVEYOR] Ready meta saved: {meta_path.name} (exists={meta_path.exists()})")
        except Exception as meta_err:
            log.error(f"[CONVEYOR] Failed to write ready meta sidecar: {meta_err}")

        # Удаляем временные файлы (безопасно)
        if local_path.exists():
            await safe_unlink(local_path)
            if is_instagram_source:
                log.info("[CONVEYOR] Instagram source video cleaned up after processing")
        
        return ready_path
        
    except Exception as e:
        error_msg = str(e)
        
        # 🚨 CRITICAL: Проверка на Invalid file_id (НО НЕ для Instagram!)
        if ("Invalid file_id" in error_msg or "file_id" in error_msg.lower()) and item.get('file_id') != "instagram_source":
            log.critical(f"🚨 CRITICAL | [CONVEYOR] Skipping broken post due to Invalid file_id: {item.get('file_id', 'unknown')[:20]}")
            return None
        
        log.error(f"[CONVEYOR] prepare_video_for_ready failed: {e}")
        return None


def process_photo(local_path: Path) -> Path | None:
    """Накладывает логотип на фото (нижний левый угол, 15% ширины, полупрозрачный)."""
    try:
        img = Image.open(local_path).convert("RGBA")
        dark_palette = [
            (0, 0, 0),
            (10, 10, 20),
            (20, 20, 30),
            (12, 8, 24),
            (6, 12, 18),
        ]
        # Здесь логотип убран; возвращаем оригинал с возможным будущим расширением
        out_path = local_path
        return out_path
    except Exception as e:
        log.error(f"Photo processing failed (logo): {e}")
        return None


def load_stats():
    """Загружает статистику из файла"""
    global DAILY_STATS
    if STATS_FILE.exists():
        try:
            with STATS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                today = datetime.now().strftime("%Y-%m-%d")
                if data.get("date") == today:
                    DAILY_STATS.update(data)
                else:
                    # Новый день - сбрасываем статистику
                    reset_stats()
        except Exception as e:
            log.warning(f"Failed to load stats: {e}")
            reset_stats()
    else:
        reset_stats()


def save_stats():
    """Сохраняет статистику в файл"""
    try:
        with STATS_FILE.open("w", encoding="utf-8") as f:
            json.dump(DAILY_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Failed to save stats: {e}")


def reset_stats():
    """Сбрасывает статистику на новый день"""
    global DAILY_STATS
    today = datetime.now().strftime("%Y-%m-%d")
    DAILY_STATS = {
        "date": today,
        "morning": 0,
        "afternoon": 0,
        "video": 0,
        "photo": 0,
        "text": 0,
        "total": 0,
        "tokens": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        },
        "cost_usd": 0.0
    }
    save_stats()


def log_tokens(prompt_tokens: int, completion_tokens: int, total_tokens: int):
    """Логирует токены, обновляет статистику и возвращает стоимость запроса."""
    global DAILY_STATS, DAILY_COST_USD, TRANSLATION_LAST_COST
    today = datetime.now().strftime("%Y-%m-%d")
    
    if DAILY_STATS.get("date") != today:
        reset_stats()
    
    # Обновляем счётчики токенов
    DAILY_STATS["tokens"]["prompt_tokens"] += prompt_tokens
    DAILY_STATS["tokens"]["completion_tokens"] += completion_tokens
    DAILY_STATS["tokens"]["total_tokens"] += total_tokens
    
    # Рассчитываем стоимость для gpt-4o-mini
    # input: $0.15/1M, output: $0.60/1M
    input_cost = (prompt_tokens / 1_000_000) * 0.15
    output_cost = (completion_tokens / 1_000_000) * 0.60
    total_cost = input_cost + output_cost
    
    DAILY_STATS["cost_usd"] += total_cost
    DAILY_COST_USD += total_cost
    TRANSLATION_LAST_COST += total_cost
    
    log.info(f"TOKENS USED: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}, cost=${total_cost:.6f}")
    save_stats()
    return total_cost


def increment_stat(post_type: str):
    """Увеличивает счётчик для типа поста"""
    global DAILY_STATS
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Если новый день - сбрасываем
    if DAILY_STATS.get("date") != today:
        reset_stats()
    
    # Определяем время суток
    now = datetime.now()
    if now.hour < 14:
        DAILY_STATS["morning"] += 1
    else:
        DAILY_STATS["afternoon"] += 1
    
    # Увеличиваем счётчик типа
    if post_type in ["video", "photo", "text"]:
        DAILY_STATS[post_type] += 1
    
    DAILY_STATS["total"] += 1
    save_stats()


async def send_daily_report(application):
    """Отправляет ежедневный отчёт"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    if DAILY_STATS.get("date") != today:
        return
    
    stats = DAILY_STATS
    tokens = stats.get("tokens", {})
    cost = stats.get("cost_usd", 0.0)
    
    report = (
        f"📊 Отчёт Haqiqat ({today})\n\n"
        f"До обеда: {stats['morning']} постов\n"
        f"После обеда: {stats['afternoon']} постов\n"
        f"Видео: {stats['video']}\n"
        f"Фото: {stats['photo']}\n"
        f"Текст: {stats['text']}\n"
        f"Всего за день: {stats['total']}\n\n"
        f"Токены:\n"
        f"  Prompt: {tokens.get('prompt_tokens', 0):,}\n"
        f"  Completion: {tokens.get('completion_tokens', 0):,}\n"
        f"  Всего: {tokens.get('total_tokens', 0):,}\n\n"
        f"Стоимость: ${cost:.4f}"
    )
    
    try:
        # Отправляем отчёт админу, если указан, иначе в лог
        if ADMIN_CHAT_ID:
            await application.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=report
            )
            log.info("Daily report sent to admin")
        else:
            log.info(f"Daily report:\n{report}")
    except Exception as e:
        log.error(f"Failed to send daily report: {e}")
        log.info(f"Daily report (fallback):\n{report}")


async def send_progress_report(application):
    """Отправляет краткий отчёт сразу после публикации поста"""
    if not (REPORT_AFTER_POST and ADMIN_CHAT_ID):
        return

    stats = DAILY_STATS
    tokens = stats.get("tokens", {})

    report = (
        "✅ Публикация выполнена\n"
        f"Всего сегодня: {stats.get('total', 0)}\n"
        f"Видео: {stats.get('video', 0)}, фото: {stats.get('photo', 0)}, текст: {stats.get('text', 0)}\n"
        f"Токены: prompt {tokens.get('prompt_tokens', 0)}, completion {tokens.get('completion_tokens', 0)}\n"
        f"Стоимость (оценка): ${stats.get('cost_usd', 0.0):.4f}"
    )

    try:
        await application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
        log.info("Progress report sent to admin")
    except Exception as e:
        log.warning(f"Failed to send progress report: {e}")


async def send_daily_stats(application):
    """Отправляет ежедневную статистику в 23:30 по серверному времени."""
    today = datetime.now().strftime("%Y-%m-%d")
    if DAILY_STATS.get("date") != today:
        reset_stats()
    total_posts = DAILY_STATS.get("total", 0)
    cost = DAILY_STATS.get("cost_usd", 0.0)
    report = f"Всего постов сегодня: {total_posts}. Затраты на OpenAI: ${cost:.2f}."
    try:
        if ADMIN_CHAT_ID:
            await application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
            log.info("Daily stats sent to admin")
        else:
            log.info(f"Daily stats: {report}")
    except Exception as e:
        log.error(f"Failed to send daily stats: {e}")
        log.info(f"Daily stats (fallback): {report}")


async def daily_report_scheduler(application):
    """Планировщик для ежедневной отправки отчёта"""
    while True:
        now = datetime.now()
        target_time = datetime.combine(now.date(), time(hour=23, minute=30))

        if now >= target_time:
            await send_daily_stats(application)
            target_time = datetime.combine(now.date() + timedelta(days=1), time(hour=23, minute=30))

        wait_seconds = (target_time - datetime.now()).total_seconds()
        await asyncio.sleep(max(wait_seconds, 60))


async def history_log_scheduler():
    """Планировщик для ежедневной ротации history.log в 23:50."""
    while True:
        now = datetime.now()
        target_time = datetime.combine(now.date(), time(hour=23, minute=50))

        if now >= target_time:
            # Формируем краткий отчёт перед ротацией
            total_posts = DAILY_STATS.get("total", 0)
            cost = DAILY_STATS.get("cost_usd", 0.0)
            report_text = (
                f"📊 Kunlik hisobot\n"
                f"Postlar: {total_posts}\n"
                f"OpenAI xarajatlari: ${cost:.4f}\n"
            )
            send_report_message(report_text)
            rotate_history_log()
            target_time = datetime.combine(now.date() + timedelta(days=1), time(hour=23, minute=50))

        wait_seconds = (target_time - datetime.now()).total_seconds()
        await asyncio.sleep(max(wait_seconds, 60))


def load_ready_files_to_queue():
    """
    Загружает готовые видео из ready_to_publish в POST_QUEUE.
    Вызывается когда POST_QUEUE пустая, но есть готовые файлы.
    """
    # 🔍 DIAGNOSTICS: Проверяем пути
    cwd = Path.cwd()
    ready_dir_resolved = READY_TO_PUBLISH_DIR.resolve()
    log.info(f"[QUEUE LOADER] Current working directory: {cwd}")
    log.info(f"[QUEUE LOADER] Ready directory (absolute): {ready_dir_resolved}")
    log.info(f"[QUEUE LOADER] Ready directory exists: {ready_dir_resolved.exists()}")
    
    ready_files = sorted(READY_TO_PUBLISH_DIR.glob("ready_*.mp4"))
    
    if not ready_files:
        # Диагностика если папка пустая
        all_files_in_dir = list(READY_TO_PUBLISH_DIR.glob("*"))[:20]
        log.warning(f"[QUEUE LOADER] No ready files found in {ready_dir_resolved}")
        log.warning(f"[QUEUE LOADER] Directory contents (first 20): {[f.name for f in all_files_in_dir]}")
        return 0
    
    log.info(f"[DEBUG] Queue empty, found {len(ready_files)} ready files on disk. Filling queue...")
    
    loaded_count = 0
    for ready_file in ready_files:
        # Проверяем, что файл существует и метаданные тоже
        # READY_META_EXT_FIX: Try both .json and .mp4.json formats
        meta_file_a = ready_file.with_suffix(".json")
        meta_file_b = ready_file.with_suffix(".mp4.json")
        meta_file = meta_file_a if meta_file_a.exists() else (meta_file_b if meta_file_b.exists() else None)
        
        file_exists = ready_file.exists()
        meta_exists = meta_file is not None
        
        log.info(f"[QUEUE LOADER] Processing {ready_file.name}: file_exists={file_exists}, meta_exists={meta_exists}")
        log.info(f"[QUEUE LOADER] File path (absolute): {ready_file.resolve()}")
        log.info(f"[QUEUE LOADER] meta picked: {meta_file.name if meta_file else 'NONE'}")
        
        if not meta_file:
            log.warning(f"[QUEUE LOADER] Metadata missing for {ready_file.name} (tried .json and .mp4.json), skipping")
            continue
        
        try:
            # Загружаем метаданные
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))

            # Создаем item для очереди
            item = {
                "type": "video",
                "file_id": meta_data.get("file_id", "unknown"),
                "caption": meta_data.get("caption", ""),
                "ready_file_path": str(ready_file),
                "ready_metadata": meta_data,
                "from_ready_folder": True  # Флаг, что это готовый файл
            }

            POST_QUEUE.append(item)
            loaded_count += 1
            log.info(f"[QUEUE LOADER] Added {ready_file.name} to queue")

        except Exception as e:
            log.error(f"[QUEUE LOADER] Failed to load metadata for {ready_file.name}: {e}")
            continue
    
    if loaded_count > 0:
        save_queue()
        log.info(f"[QUEUE LOADER] Loaded {loaded_count} ready files into queue. Queue size: {len(POST_QUEUE)}")
    
    return loaded_count


async def maintain_ready_posts_worker(application):
    """
    СИСТЕМА КОНВЕЙЕР: Фоновый процесс поддержания 5 готовых постов.
    - Проверяет количество готовых файлов в ready_to_publish
    - Если меньше 5, берет видео из POST_QUEUE и подготавливает
    - Рендерит строго по одному файлу за раз
    """
    global IS_PREPARING
    
    log.info("[CONVEYOR] Maintain ready posts worker started")
    
    while True:
        try:
            # Считаем готовые видео (только .mp4 файлы)
            ready_files = list(READY_TO_PUBLISH_DIR.glob("ready_*.mp4"))
            ready_count = len(ready_files)
            
            # Если меньше целевого количества и есть видео в очереди
            if ready_count < TARGET_READY_POSTS and POST_QUEUE and not IS_PREPARING:
                IS_PREPARING = True
                log.info(f"[CONVEYOR] Ready posts: {ready_count}/{TARGET_READY_POSTS}. Preparing new video...")
                
                # Ищем первое видео в очереди (только СЫРЫЕ, не готовые)
                video_item = None
                for idx, item in enumerate(POST_QUEUE):
                    # Берём только сырые видео (не из ready_to_publish)
                    if item.get("type") == "video" and not item.get("from_ready_folder", False):
                        video_item = item
                        # Удаляем из очереди
                        POST_QUEUE.remove(item)
                        save_queue()
                        log.info(f"[CONVEYOR] Took RAW video from queue position {idx}, queue size: {len(POST_QUEUE)}")
                        break
                
                if video_item:
                    # Подготавливаем видео
                    ready_path = await prepare_video_for_ready(application, video_item)
                    
                    if ready_path:
                        log.info(f"[CONVEYOR] Successfully prepared: {ready_path.name}")
                        # Удаляем из буфера
                        try:
                            await delete_from_buffer(application, video_item)
                        except Exception as e:
                            log.warning(f"[CONVEYOR] Failed to delete from buffer: {e}")
                    else:
                        # НЕ возвращаем в очередь - prepare_video_for_ready вернула None из-за ошибки
                        log.critical(f"🚨 CRITICAL | [CONVEYOR] Failed to prepare video, SKIPPING (not returning to queue)")
                        # Пытаемся удалить из буфера
                        try:
                            await delete_from_buffer(application, video_item)
                        except Exception as e:
                            log.warning(f"[CONVEYOR] Failed to delete from buffer: {e}")
                
                IS_PREPARING = False
                
            elif ready_count >= TARGET_READY_POSTS:
                log.info(f"[CONVEYOR] Ready posts: {ready_count}/{TARGET_READY_POSTS}. Target reached.")
            
            # Проверяем каждые 30 секунд
            await asyncio.sleep(30)
            
        except Exception as e:
            log.error(f"[CONVEYOR] maintain_ready_posts_worker error: {e}")
            IS_PREPARING = False
            await asyncio.sleep(60)


def post_hash(item: dict) -> str:
    base = item.get("type", "")
    if item["type"] == "text":
        base += item.get("text", "")
    else:
        base += item.get("file_id", "") + (item.get("caption") or "")

    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def clean_text_before_translation(text: str) -> str:
    """Удаляет служебные хвосты (названия каналов, подписи) перед переводом"""
    if not text:
        return text
    
    import re
    
    # Паттерны для удаления служебных хвостов
    patterns_to_remove = [
        r"Церебра[^\n]*",
        r"Подписывайтесь[^\n]*",
        r"Подписка[^\n]*",
        r"Подписаться[^\n]*",
        r"Канал[^\n]*",
        r"@[a-zA-Z0-9_]+",  # Упоминания каналов
        r"https?://[^\s]+",  # Ссылки
        r"t\.me/[^\s]+",  # Telegram ссылки
        r"Подписывайся[^\n]*",
        r"Подписывайтесь на[^\n]*",
        r"Подпишись[^\n]*",
        r"Подписка на[^\n]*",
    ]
    
    cleaned = text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    
    # Убираем повторяющиеся мысли (одинаковые предложения)
    lines = cleaned.split('\n')
    seen_lines = set()
    unique_lines = []
    for line in lines:
        line_stripped = line.strip().lower()
        # Пропускаем пустые строки и очень короткие
        if len(line_stripped) < 10:
            unique_lines.append(line)
            continue
        # Проверяем на похожесть (если строка уже была, пропускаем)
        if line_stripped not in seen_lines:
            seen_lines.add(line_stripped)
            unique_lines.append(line)
    
    return '\n'.join(unique_lines).strip()


async def translate_text(text: str) -> str:
    """Умный режим перевода с self-check"""
    if not openai_client or not text:
        return text

    # Очищаем от служебных хвостов перед переводом
    cleaned_text = clean_text_before_translation(text)

    attempts = 3
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            # Первый проход: перевод
            TRANSLATION_LAST_COST = 0.0
            resp1 = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                max_tokens=800,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sen master aforizmlar va hikmatli so'zlar ijodkorisan. Maqsad: rus tilidagi matnni o'zbek (lotin) tilida ma'noli, qisqa, tabiiy va kuchli ohangda berish. "
                            "So'zma-so'z tarjimadan qoch, ma'no ustuvor. Masalan, 'Тихая сила' — 'Vazmin quvvat' yoki 'Sokin qudrat', lekin 'Jim kuch' emas.\n"
                            "Qoidalar:\n"
                            "- qisqa, ravon, ta'sirli; ortiqcha so'zlar yo'q\n"
                            "- tuzilmani saqla (abzas, quote >), emoji qolgani joyida\n"
                            "- kanallar, xeshteglar va xizmat belgilarini tarjima qilma\n"
                            "- so'rov/komment so'ramagin\n"
                            "- 1-2 kuchli so'zni *yulduzcha* bilan belgilashing mumkin\n"
                            "- Matn oxirida 3-5 tegishli xeshteg (#hikmatlar #motivation #uzb #muvaffaqiyat kabi), o'zbek va ingliz tili aralash. Xeshteglar faqat caption uchundir, rasmga emas.\n"
                            "Natija: faqat yakuniy tayyor matn, oxirida xeshteglar bilan."
                        ),
                    },
                    {"role": "user", "content": cleaned_text},
                ],
            )
            
            translated = (resp1.choices[0].message.content or cleaned_text).strip()
            
            # Логируем токены первого запроса
            usage = resp1.usage
            if usage:
                log_tokens(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
            
            # Если пусто — повторяем
            if not translated:
                raise RuntimeError("Empty translation")

            # Второй проход: self-check
            resp2 = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                max_tokens=800,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — эксперт по оценке качества перевода на узбекский язык.\n\n"
                            "Оцени переведённый текст и верни ТОЛЬКО валидный JSON в таком формате:\n"
                            '{"readability": 1-10, "logic": 1-10, "style": 1-10, "no_repeat": 1-10, "issues": ["проблема1", "проблема2"], "improved_text": "улучшенный текст"}\n\n'
                            "Критерии оценки:\n"
                            "- readability: читаемость (1-10)\n"
                            "- logic: логика и связность (1-10)\n"
                            "- style: естественность Telegram-стиля (1-10)\n"
                            "- no_repeat: отсутствие повторов с другими постами (1-10)\n\n"
                            "Если ЛЮБАЯ оценка < 7 или средняя < 7, то improved_text должен содержать переписанную версию:\n"
                            "- упростить\n"
                            "- укоротить\n"
                            "- убрать лишние слова\n"
                            "- сделать более живым\n\n"
                            "Только если ВСЕ оценки >= 7, improved_text может быть равен исходному тексту."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Оцени этот перевод:\n\n{translated}"
                    },
                ],
                response_format={"type": "json_object"},
            )
            
            # Логируем токены второго запроса
            usage2 = resp2.usage
            if usage2:
                log_tokens(usage2.prompt_tokens, usage2.completion_tokens, usage2.total_tokens)
            
            # Парсим JSON ответ
            try:
                check_result = json.loads(resp2.choices[0].message.content or "{}")
                
                readability = check_result.get("readability", 10)
                logic = check_result.get("logic", 10)
                style = check_result.get("style", 10)
                no_repeat = check_result.get("no_repeat", 10)  # Отсутствие повтора
                
                # Минимальная оценка должна быть >= 7
                min_score = min(readability, logic, style, no_repeat)
                avg_score = (readability + logic + style + no_repeat) / 4
                
                improved_text = check_result.get("improved_text", translated)
                
                log.info(f"Translation self-check: readability={readability}, logic={logic}, style={style}, no_repeat={no_repeat}, min={min_score:.2f}, avg={avg_score:.2f}")
                
                # Если любая оценка < 7 → переписать
                if min_score < 7 or avg_score < 7:
                    log.warning(f"REWRITE: low score (min={min_score:.2f}, avg={avg_score:.2f}), using improved_text")
                    return improved_text
                else:
                    log.info(f"OK: translation approved (min={min_score:.2f}, avg={avg_score:.2f})")
                    return improved_text
                    
            except (json.JSONDecodeError, KeyError) as e:
                log.warning(f"Failed to parse self-check JSON: {e}, using original translation")
                return translated
        except Exception as e:
            last_error = e
            log.warning(f"Translate attempt {attempt}/{attempts} failed: {e}")
            if attempt == attempts:
                log.error(f"Translate error after {attempts} attempts: {e}")
                send_admin_error(f"OpenAI translation failed: {e}")
                return text
            await asyncio.sleep(1)
    
    # fallback
    if last_error:
        log.error(f"Translate fatal: {last_error}")
    return text


# ==================== WHISPER AUDIO-TO-TEXT ====================

def extract_audio_from_video(video_path):
    """
    Извлекает аудиодорожку из видео и сохраняет во временный mp3 файл.
    
    Args:
        video_path: Путь к видеофайлу
        
    Returns:
        Путь к временному аудиофайлу или None при ошибке
    """
    try:
        from moviepy.editor import VideoFileClip
        
        tmp_audio_path = Path("tmp_media") / "whisper_temp.mp3"
        tmp_audio_path.parent.mkdir(exist_ok=True)
        
        # Загружаем видео и извлекаем аудио
        video = VideoFileClip(str(video_path))
        if video.audio is None:
            log.warning(f"[WHISPER] No audio track in video: {video_path}")
            video.close()
            return None
        
        # Сохраняем аудио во временный файл
        video.audio.write_audiofile(
            str(tmp_audio_path),
            codec='mp3',
            bitrate='128k',
            logger=None  # Отключаем verbose логи
        )
        video.close()
        
        log.info(f"[WHISPER] Audio extracted: {tmp_audio_path.name} ({tmp_audio_path.stat().st_size / 1024:.1f} KB)")
        return tmp_audio_path
        
    except Exception as e:
        log.error(f"[WHISPER] Audio extraction failed: {e}")
        return None


def get_video_transcript(video_path):
    """
    Получает текстовую транскрибацию видео через OpenAI Whisper API.
    
    Args:
        video_path: Путь к видеофайлу
        
    Returns:
        Транскрибированный текст или None при ошибке
    """
    if not openai_client:
        log.warning("[WHISPER] OpenAI client not initialized")
        return None
    
    audio_path = None
    try:
        # 1. Извлекаем аудио из видео
        audio_path = extract_audio_from_video(video_path)
        if not audio_path or not audio_path.exists():
            log.warning("[WHISPER] Audio extraction failed, skipping transcription")
            return None
        
        # 2. Отправляем в Whisper API
        log.info("[WHISPER] Sending audio to Whisper API...")
        with open(audio_path, "rb") as audio_file:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru"  # Указываем русский для точности
            )
        
        transcript_text = transcript.text.strip()
        log.info(f"[WHISPER] Transcription received: {len(transcript_text)} chars")
        log.info(f"[WHISPER] Preview: {transcript_text[:100]}...")
        
        return transcript_text
        
    except Exception as e:
        log.error(f"[WHISPER] Transcription failed: {e}")
        return None
        
    finally:
        # 3. Cleanup: Удаляем временный аудиофайл
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
                log.info("[WHISPER] Temporary audio file deleted")
            except Exception as e:
                log.warning(f"[WHISPER] Failed to delete temp audio: {e}")


# ==================== END WHISPER ====================


# ==================== ELEVENLABS VOICE ====================

def generate_voiceover(text):
    """
    Генерирует озвучку текста через ElevenLabs API.
    
    Args:
        text: Текст для озвучки (узбекский)
        
    Returns:
        Путь к сохраненному аудиофайлу или None при ошибке
    """
    if not ELEVENLABS_API_KEY:
        log.warning("[ELEVENLABS] API key not configured, skipping voiceover")
        return None
    
    try:
        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs
        
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

        # Путь для сохранения озвучки
        tmp_voiceover_path = Path("tmp_media") / "voiceover.mp3"
        tmp_voiceover_path.parent.mkdir(exist_ok=True)
        
        log.info(f"[ELEVENLABS] Generating voiceover for {len(text)} chars...")
        log.info(f"[ELEVENLABS] Text preview: {text[:100]}...")
        
        # Генерируем озвучку
        response = client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            output_format="mp3_44100_128",
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                style=0.0,
                use_speaker_boost=True
            )
        )
        
        # Сохраняем аудио
        with open(tmp_voiceover_path, "wb") as f:
            for chunk in response:
                if chunk:
                    f.write(chunk)
        
        file_size_kb = tmp_voiceover_path.stat().st_size / 1024
        log.info(f"[ELEVENLABS] ✅ Voiceover generated: {tmp_voiceover_path.name} ({file_size_kb:.1f} KB)")
        
        return tmp_voiceover_path
        
    except ImportError:
        log.error("[ELEVENLABS] elevenlabs package not installed. Run: pip install elevenlabs")
        return None
    except Exception as e:
        log.error(f"[ELEVENLABS] Voiceover generation failed: {e}")
        return None


# ==================== END ELEVENLABS ====================


# ==================== SMART INSTAGRAM DOWNLOADER ====================

def download_from_instagram(url):
    """
    Скачивает видео из Instagram через yt-dlp.
    
    Args:
        url: URL Instagram поста/reels
        
    Returns:
        Путь к скачанному видеофайлу или None при ошибке
    """
    try:
        import yt_dlp
        
        tmp_dir = Path("tmp_media")
        tmp_dir.mkdir(exist_ok=True)
        
        # Генерируем уникальное имя файла
        import hashlib
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        output_template = str(tmp_dir / f"instagram_{url_hash}.%(ext)s")
        
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        log.info(f"[INSTAGRAM] Downloading video from: {url[:50]}...")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
        if not Path(filename).exists():
            log.error(f"[INSTAGRAM] Downloaded file not found: {filename}")
            return None
        
        file_size_mb = Path(filename).stat().st_size / (1024 * 1024)
        log.info(f"[INSTAGRAM] ✅ Video downloaded: {Path(filename).name} ({file_size_mb:.1f} MB)")
        
        return Path(filename)
        
    except ImportError:
        log.error("[INSTAGRAM] yt-dlp package not installed. Run: pip install yt-dlp")
        return None
    except Exception as e:
        log.error(f"[INSTAGRAM] Download failed: {e}")
        return None


# ==================== END INSTAGRAM DOWNLOADER ====================


# ==================== MIXED QUEUE 4+4 LOGIC ====================

def get_next_post_from_queue():
    """
    Выбирает следующий пост из очереди по логике 4+4:
    - 4 поста с voiceover: True
    - 4 поста с voiceover: False
    - Если нужного типа нет, берёт то, что есть
    
    Returns:
        Пост из очереди или None
    """
    global VOICEOVER_POSTS_COUNT, NO_VOICEOVER_POSTS_COUNT, CURRENT_BLOCK_TYPE
    
    if not POST_QUEUE:
        return None
    
    # Определяем, какой тип поста нужен сейчас
    if CURRENT_BLOCK_TYPE == "voiceover":
        target_voiceover = True
        needed = 4 - VOICEOVER_POSTS_COUNT
    else:
        target_voiceover = False
        needed = 4 - NO_VOICEOVER_POSTS_COUNT
    
    log.info(f"[MIXED QUEUE] Current block: {CURRENT_BLOCK_TYPE}, progress: {VOICEOVER_POSTS_COUNT if CURRENT_BLOCK_TYPE == 'voiceover' else NO_VOICEOVER_POSTS_COUNT}/4")
    
    # Ищем пост нужного типа
    for idx, item in enumerate(POST_QUEUE):
        if item.get("voiceover", False) == target_voiceover:
            # Нашли нужный тип
            post = POST_QUEUE[idx]
            del POST_QUEUE[idx]
            
            # Обновляем счётчики
            if target_voiceover:
                VOICEOVER_POSTS_COUNT += 1
                log.info(f"[MIXED QUEUE] Selected voiceover post ({VOICEOVER_POSTS_COUNT}/4)")
                if VOICEOVER_POSTS_COUNT >= 4:
                    CURRENT_BLOCK_TYPE = "no_voiceover"
                    VOICEOVER_POSTS_COUNT = 0
                    log.info("[MIXED QUEUE] ✅ Voiceover block complete, switching to no_voiceover")
            else:
                NO_VOICEOVER_POSTS_COUNT += 1
                log.info(f"[MIXED QUEUE] Selected no_voiceover post ({NO_VOICEOVER_POSTS_COUNT}/4)")
                if NO_VOICEOVER_POSTS_COUNT >= 4:
                    CURRENT_BLOCK_TYPE = "voiceover"
                    NO_VOICEOVER_POSTS_COUNT = 0
                    log.info("[MIXED QUEUE] ✅ No_voiceover block complete, switching to voiceover")
            
            return post
    
    # Если нужного типа нет, берём что есть
    log.warning(f"[MIXED QUEUE] No {CURRENT_BLOCK_TYPE} posts available, taking any post")
    return POST_QUEUE.popleft()


# ==================== END MIXED QUEUE ====================


SYSTEM_PROMPT_UZ = (
    "Ты — профессиональный сценарист контента для узбекской аудитории (SCENARIST MODE). "
    "ВАЖНО: Используй предоставленный текст (транскрибацию из видео) как первоисточник. Создай на его основе вовлекающий сценарий на узбекском языке (латиница). "
    "\n"
    "🎣 КРЮЧОК (HOOK) — ОБЯЗАТЕЛЬНО! Начни текст с одного из этих крючков, выбери самый подходящий под контекст видео:\n"
    "1. Siz buni bilarmidingiz... (А вы знали...)\n"
    "2. Bunga ishonish qiyin, lekin bu haqiqat... (Трудно поверить, но это правда...)\n"
    "3. Buni ko'pchilikdan yashirishgan! (Это скрывали от многих!)\n"
    "4. Oxirigacha ko'ring, natijasi hayratlanarli! (Досмотрите до конца, результат поразителен!)\n"
    "5. Sizningcha, bu qanday sodir bo'ldi? (Как вы думаете, как это произошло?)\n"
    "6. Hech kim kutmagan voqea sodir bo'ldi... (Случилось то, чего никто не ожидал...)\n"
    "7. Buni ko'rib hayratda qolasiz! (Вы будете в шоке, увидев это!)\n"
    "8. Dunyodagi eng g'alati narsalardan biri... (Одна из самых странных вещей в мире...)\n"
    "9. Siz buni o'z ko'zingiz bilan ko'rishingiz kerak! (Вы должны увидеть это своими глазами!)\n"
    "\n"
    "Адаптируй крючок под контекст для максимального удержания в первые 3 секунды. "
    "Сохраняй смысл оригинала, но адаптируй стиль под узбекскую аудиторию — естественно, живо, с эмоцией. "
    "ВАЖНО: Никогда не путай животных с растениями. Если в тексте 🐙 или описание животных — используй термины для животных (Sakkizoyoq, hayvonlar), а не для растений (o'simliklar). "
    "Sen master aforizmlar va hikmatli so'zlar ijodkorisan. "
    "So'zma-so'z tarjimadan qoch, ma'no ustuvor. Masalan, 'Тихая сила' — bu 'Vazmin quvvat' yoki 'Sokin qudrat', lekin 'Jim kuch' emas. "
    "Matnni qisqa, ravon, ta'sirli uslubda yoz, ortiqcha so'zlarsiz. "
    "Kerak bo'lsa satr tashlash mumkin, savol-javob ohangi ham mos. "
    "ХЭШТЕГИ: На основе смысла видео выбери только ОДИН самый точный тематический хэштег на узбекском языке (например: #texnologiya, #tarix, #tabiat, #fan, #sport, #san'at). Добавь его в начало текста. "
    "Agar satr `>` bilan boshlangan bo'lsa, shu belgini saqla. "
    "Emojilar: 0–2 ta, faqat juda mos bo'lsa. "
    "Hech qanday izoh bermagin — faqat yakuniy matnni qaytar. "
    "1-2 eng kuchli so'zni *yulduzcha* bilan belgilab (masalan, *SOKIN QUDRAT*) keyinchalik ajratish mumkin bo'lsin."
)


def _translate_sync(text: str) -> str:
    assert openai_client is not None
    resp = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_UZ},
            {"role": "user", "content": text},
        ],
    )
    out = (resp.choices[0].message.content or "").strip()
    return out or text


async def translate_and_adapt(text: str, logger) -> str:
    text = (text or "").strip()
    if not text:
        return text

    if not openai_client:
        return text

    try:
        # OpenAI SDK вызов синхронный — уводим в отдельный поток, чтобы не блокировать event loop PTB.
        return await asyncio.to_thread(_translate_sync, text)
    except Exception as e:
        logger.warning("Translate failed, sending original text. Error=%s", e)
        return text


def sanitize_post(text: str) -> str:
    """Очищает текст от мусора, НЕ трогая emoji и Unicode"""
    if not text:
        return text

    # Убираем мусорные теги и лишние символы
    import re
    # Убираем HTML-теги (кроме нужных)
    text = re.sub(r'<[^>]+>', '', text)
    # Убираем множественные >>> (более 3 подряд)
    text = re.sub(r'>{3,}', '>>>', text)
    # Убираем двойные пробелы (но не переносы строк)
    text = re.sub(r' +', ' ', text)
    # Убираем пробелы в начале/конце строк
    lines = [line.rstrip() for line in text.split("\n")]

    cleaned = []
    empty = 0
    for line in lines:
        if line.strip() == "":
            empty += 1
            if empty <= 2:
                cleaned.append("")
        else:
            empty = 0
            cleaned.append(line)

    result = "\n".join(cleaned).strip()
    return result or text


def append_branding(text: str) -> str:
    """Добавляет ссылку бренда в конец caption после хэштегов."""
    if not text:
        return BRANDED_LINK
    if BRANDED_LINK in text:
        return text
    return f"{text}\n{BRANDED_LINK}"


def append_hashtags(text: str) -> str:
    """Гарантирует обязательные хэштеги в самом конце поста."""
    if not text:
        return HASHTAGS_BLOCK
    if HASHTAGS_BLOCK in text:
        return text
    return f"{text.rstrip()}\n{HASHTAGS_BLOCK}"


def clean_caption(text: str) -> str:
    """Удаляет старые хэштеги, ссылки и упоминания сторонних каналов."""
    if not text:
        return ""
    import re
    cleaned = re.sub(r'https?://\S+|www\.\S+|t\.me/\S+|@\w+', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'#\S+', '', cleaned)
    cleaned = re.sub(r'церебра', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Haqiqat\s*🧠', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Batafsil[:\s]*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Kanalga obuna bo'ling", '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Подпис(?:аться|аться на|ка|ки|ы|ывайтесь|аться!?)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'👉', '', cleaned)
    cleaned = re.sub(r'\|\|', '', cleaned)
    cleaned = re.sub(r'\|', '', cleaned)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    # Удаляем любые HTML-теги целиком, чтобы избежать битых ссылок <a>
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    # Удаляем пустые строки
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    return "\n".join(lines).strip()


def finalize_caption_tg(text: str) -> str:
    """Финальная зачистка и принудительный HTML-блок ссылки перед хэштегами."""
    cleaned = clean_caption(text)
    # Повторно убираем t.me и прочие ссылки, мусорные слова
    cleaned = re.sub(r'https?://\S+|www\.\S+|t\.me/\S+|@\w+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Batafsil[:\s]*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'👉', '', cleaned)
    cleaned = re.sub(r'\|\|', '', cleaned)
    cleaned = re.sub(r'\|', '', cleaned)
    cleaned = re.sub(r'Подпис(?:аться|аться на|ка|ки|ы|ывайтесь|аться!?)', '', cleaned, flags=re.IGNORECASE)

    # Добавляем обязательный блок ссылки перед хэштегами
    link_block = LINK_BLOCK_HTML
    cleaned = cleaned.rstrip()
    # Удаляем странные символы в конце (например, одиночные значки)
    cleaned = re.sub(r"[^\w\s\[\]\(\)\\\/.,!?-]+$", "", cleaned)
    # Гарантируем одиночный перевод строки перед ссылкой и хэштегами
    cleaned = f"{cleaned}\n\n{link_block}\n\n{HASHTAGS_BLOCK}"
    return cleaned.strip()


def finalize_caption_meta(text: str) -> str:
    """Стерильный caption без ссылок/telegram блока для Meta: только текст + хэштеги."""
    cleaned = clean_caption(text)
    # Удаляем все ссылки и упоминания
    cleaned = re.sub(r'https?://\S+|www\.\S+|t\.me/\S+|@\w+', '', cleaned, flags=re.IGNORECASE)
    # Удаляем telegram-специфичные фразы
    cleaned = re.sub(r'Haqiqat\s*🧠', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Kanalga obuna bo[\'`]?ling', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'Batafsil[:\s]*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'👉', '', cleaned)
    cleaned = re.sub(r'\|\|', '', cleaned)
    cleaned = re.sub(r'\|', '', cleaned)
    cleaned = re.sub(r'Подпис(?:аться|аться на|ка|ки|ы|ывайтесь|аться!?)', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.rstrip()
    cleaned = re.sub(r"[^\w\s\[\]\(\)\\\/.,!?-]+$", "", cleaned)
    # Только текст + хэштеги, без ссылочного блока
    cleaned = f"{cleaned}\n\n{HASHTAGS_BLOCK}"
    return cleaned.strip()


def prepare_caption_for_publish_tg(raw: str) -> str:
    """Caption для TG: полный текст + ссылка + хэштеги (HTML)."""
    text = ensure_utf8_text(raw or "")
    text = remove_comment_phrases(text)
    text = clean_caption(text)
    text = ensure_footer(text)
    text = append_branding(text)
    text = append_hashtags(text)
    text = finalize_caption_tg(text)
    return text


def prepare_caption_for_publish_meta(raw: str) -> str:
    """Caption для IG/FB: без ссылок/telegram блока, только текст + хэштеги."""
    text = ensure_utf8_text(raw or "")
    text = remove_comment_phrases(text)
    text = clean_caption(text)
    text = ensure_footer(text)
    text = append_branding(text)
    text = append_hashtags(text)
    text = finalize_caption_meta(text)
    return text




def remove_quote_markers(text: str) -> str:
    """Преобразует строки с > в формат с эмодзи 🗨"""
    if not text:
        return text
    
    lines = text.split("\n")
    result = []
    
    for line in lines:
        if line.strip().startswith(">"):
            # Убираем > и добавляем эмодзи
            quote_text = line.strip()[1:].strip()
            if quote_text:
                result.append(f"🗨 {quote_text}")
        else:
            result.append(line)
    
    return "\n".join(result)


def remove_duplicate_footers(text: str) -> str:
    """Удаляет дублирующие футеры и рубрики"""
    if not text:
        return text
    
    # Паттерны для удаления
    patterns_to_remove = [
        "Qiziqarli faktlar",
        "Qiziqarli fakt",
        "Faktlar",
        "Fakt",
    ]
    
    lines = text.split("\n")
    cleaned = []
    
    for line in lines:
        line_lower = line.strip().lower()
        should_remove = False
        
        for pattern in patterns_to_remove:
            if pattern.lower() in line_lower:
                should_remove = True
                break
        
        if not should_remove:
            cleaned.append(line)
    
    return "\n".join(cleaned)


def format_post_structure(text: str) -> str:
    """Выстраивает визуальную иерархию текста"""
    if not text:
        return text
    
    # Убираем дублирующие футеры
    text = remove_duplicate_footers(text)
    
    # Обрабатываем цитаты
    text = remove_quote_markers(text)
    
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    if not lines:
        return text
    
    # Первая строка - хук
    hook = lines[0] if lines else ""
    
    # Остальной текст
    rest = lines[1:] if len(lines) > 1 else []
    
    # Формируем структуру
    result = [hook]
    
    if rest:
        result.append("")  # Пустая строка
        result.extend(rest)
    
    return "\n".join(result)


def entities_to_markers(text, entities):
    if not entities:
        return text

    offset_shift = 0
    text = text

    for e in entities:
        if e.type == MessageEntityType.BLOCKQUOTE:
            start = e.offset + offset_shift
            end = start + e.length
            block = text[start:end]
            marked = "\n".join("> " + l for l in block.split("\n"))
            text = text[:start] + marked + text[end:]
            offset_shift += len(marked) - e.length

    return text


def markers_to_entities(text):
    lines = text.split("\n")
    cleaned = []
    for l in lines:
        if l.startswith("> "):
            cleaned.append(l[2:])
        else:
            cleaned.append(l)
    return "\n".join(cleaned)


def ensure_footer(text: str) -> str:
    """Гарантирует наличие футера в тексте (работает и для text, и для caption)"""
    if not text:
        return FOOTER_HTML.strip()
    # Проверяем наличие футера по ключевым словам
    if "Haqiqat" not in text or "Kanalga obuna" not in text:
        return text + FOOTER_HTML
    return text


def trim_caption_with_footer(text: str, max_len: int = CAPTION_MAX_LENGTH) -> str:
    """Обрезает caption до max_len, гарантируя что футер не обрезается"""
    if len(text) <= max_len:
        return ensure_footer(text)
    
    # Проверяем, есть ли футер
    has_footer = "Haqiqat" in text and "Kanalga obuna" in text
    
    if has_footer:
        # Находим начало футера
        footer_start = text.find("— — —")
        if footer_start == -1:
            footer_start = text.find("🧠 Haqiqat")
        
        if footer_start > 0:
            main_text = text[:footer_start].strip()
            footer = text[footer_start:].strip()
            footer_len = len(footer)
            
            # Обрезаем основной текст, оставляя место для футера
            if len(main_text) + footer_len > max_len:
                available_len = max_len - footer_len - 10  # Запас
                if available_len > 0:
                    main_text = main_text[:available_len].rstrip() + "..."
                else:
                    # Если футер слишком длинный, оставляем только футер
                    return footer[:max_len]
            
            return main_text + "\n\n" + footer
    
    # Если футера нет, обрезаем и добавляем
    trimmed = text[:max_len - len(FOOTER_HTML) - 10].rstrip() + "..."
    return ensure_footer(trimmed)


async def delete_from_buffer(application, item: dict) -> None:
    """Удаляет исходное сообщение из буферного канала после успешной публикации"""
    if not DELETE_FROM_BUFFER:
        return
    
    buffer_message_id = item.get("buffer_message_id")
    buffer_chat_id = item.get("buffer_chat_id", BUFFER_CHANNEL_ID)
    
    if not buffer_message_id:
        log.warning("delete_from_buffer: buffer_message_id not found in item")
        return
    
    try:
        # Добавляем buffer_message_id в seen_posts.json
        if SEEN_FILE.exists():
            try:
                data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if "buffer_message_ids" not in data:
                        data["buffer_message_ids"] = []
                    if buffer_message_id not in data["buffer_message_ids"]:
                        data["buffer_message_ids"].append(buffer_message_id)
                else:
                    # Старая структура, конвертируем
                    data = {
                        "hashes": data if isinstance(data, list) else [],
                        "buffer_message_ids": [buffer_message_id]
                    }
                SEEN_FILE.write_text(json.dumps(data), encoding="utf-8")
            except Exception as e:
                log.warning(f"Failed to save buffer_message_id to seen_posts.json: {e}")
        
        # Удаляем сообщение из буфера
        await application.bot.delete_message(
            chat_id=buffer_chat_id,
            message_id=buffer_message_id
        )
        log.info(f"delete_from_buffer_ok: message_id={buffer_message_id}, chat_id={buffer_chat_id}")
        
    except Exception as e:
        # Не падаем при ошибке удаления, просто логируем
        error_msg = str(e)
        error_code = getattr(e, 'error_code', None)
        log.warning(f"delete_from_buffer_fail: message_id={buffer_message_id}, chat_id={buffer_chat_id}, error={error_msg}, code={error_code}")


# FIX B: Worker для неблокирующей обработки видео
async def video_processing_worker():
    """
    Фоновый воркер для обработки видео.
    Берет работы из VIDEO_PROCESSING_QUEUE и обрабатывает их.
    Не блокирует основные операции Telegram/scheduler.
    """
    log.info("[WORKER] Video processing worker started")
    while True:
        try:
            job = await VIDEO_PROCESSING_QUEUE.get()
            try:
                log.info(f"[QUEUE] video dequeued: type={job.get('type', 'unknown')}")
                # job содержит information для обработки видео
                # Тяжелая обработка (rendering, ffmpeg) идет здесь
                await asyncio.sleep(0.1)  # Placeholder для работы
                log.info(f"[QUEUE] video processed: type={job.get('type', 'unknown')}")
            except Exception as e:
                log.error(f"[WORKER] job failed: {e}")
            finally:
                VIDEO_PROCESSING_QUEUE.task_done()
        except Exception as e:
            log.error(f"[WORKER] unexpected error: {e}")
            await asyncio.sleep(1)


async def post_worker(application):
    global IS_POSTING, FORCE_CAROUSEL_TEST, FIRST_RUN_IMMEDIATE, LAST_PHOTO_TIME, LAST_VIDEO_TIME, LAST_POST_TIME, IS_PAUSED, FORCE_POST_NOW, POSTNOW_EVENT

    if IS_POSTING:
        return

    IS_POSTING = True

    while True:
        try:
            # === IG SCHEDULE: Проверка расписания публикаций (9 постов/день) ===
            now = datetime.now()
            reset_ig_schedule_if_needed()
            
            ready = False
            postnow_mode = FORCE_POST_NOW  # Local flag to track POSTNOW mode throughout this cycle
            
            # === POSTNOW BYPASS: Обход всех проверок расписания ===
            if postnow_mode:
                log.info("[SCHEDULER] POSTNOW override: immediate publish (bypass schedule windows)")
                ready = True
            else:
                # === NORMAL SCHEDULE MODE: Проверка расписания и окон ===
                
                # Проверка временных окон и лимитов
                hour = now.hour
                
                # Пауза 14:00-16:00
                if 14 <= hour < 16:
                    log.info("[SCHEDULER] Outside schedule window: sleeping until 16:00")
                    await sleep_or_postnow(3600)  # Sleep 1 hour
                    continue
                
                # После 21:00 - спим до утра
                if hour > 21:
                    log.info("[SCHEDULER] After 21:00: sleeping until tomorrow 08:00")
                    sleep_hours = (24 - hour) + 8
                    await sleep_or_postnow(sleep_hours * 3600)
                    continue
                
                # Утро (до 14:00): максимум 3 поста
                if hour < 14:
                    if IG_SCHEDULE["morning_videos"] >= 3:
                        log.info("[SCHEDULER] Morning limit reached (3/3): sleeping until 16:00")
                        await sleep_or_postnow(3600)  # Sleep 1 hour, will check again
                        continue
                    ready = True
                # Вечер (16:00-21:00): максимум 6 постов
                elif 16 <= hour <= 21:
                    if IG_SCHEDULE["afternoon_videos"] >= 6:
                        log.info("[SCHEDULER] Evening limit reached (6/6): sleeping until tomorrow 08:00")
                        await sleep_or_postnow(8 * 3600)  # Sleep 8 hours
                        continue
                    ready = True
                
                # Проверка интервала (1 час между постами) — только в режиме обычного расписания
                if ready and LAST_POST_TIME is not None:
                    time_since_last = (now - LAST_POST_TIME).total_seconds()
                    if time_since_last < PUBLISH_INTERVAL_SECONDS:
                        sleep_time = PUBLISH_INTERVAL_SECONDS - time_since_last
                        log.info(f"[SCHEDULER] Cooldown: sleeping {sleep_time:.0f}s until next publish window")
                        await sleep_or_postnow(sleep_time)
                        continue

            # SMART CONTROL: Проверка паузы публикаций (НЕ обходится при POSTNOW)
            if IS_PAUSED and not postnow_mode:
                log.info("[PAUSE] Conveyor paused. Sleeping for 10 seconds...")
                await sleep_or_postnow(10)
                continue
            
            # STATUS LOG: Состояние системы
            ready_count = len(list(READY_TO_PUBLISH_DIR.glob("ready_*.mp4")))
            last_post_str = LAST_POST_TIME.strftime('%Y-%m-%d %H:%M:%S') if LAST_POST_TIME else "Never"
            log.info(f"STATUS | Queue: {len(POST_QUEUE)} | Ready: {ready_count}/10 | Last post: {last_post_str}")

            if POST_QUEUE:
                # Первое включение: публикуем сразу ОДИН РАЗ
                if FIRST_RUN_IMMEDIATE:
                    # 🎯 PERSISTENT FIRST STRIKE: Пробуем файлы один за другим до первого успеха
                    first_strike_success = False
                    first_strike_attempts = 0
                    max_first_strike_attempts = 50  # Максимум 50 попыток
                    
                    log.warning("[FIRST STRIKE] Starting persistent post attempt. Will try files until one succeeds...")
                    
                    while not first_strike_success and POST_QUEUE and first_strike_attempts < max_first_strike_attempts:
                        first_strike_attempts += 1
                        item = POST_QUEUE.popleft()
                        save_queue()
                        item["first_strike"] = True
                        log.warning(f"[FIRST STRIKE] Attempt #{first_strike_attempts}: Trying post from queue. Remaining: {len(POST_QUEUE)}")
                        
                        # Проверяем тип поста - First Strike работает только с видео
                        if item["type"] != "video":
                            log.warning(f"[FIRST STRIKE] Skipping non-video post (type={item['type']})")
                            continue
                        
                        # Пробуем обработать и опубликовать видео
                        post_attempt_failed = False
                        
                        # === НАЧАЛО БЛОКА ОБРАБОТКИ FIRST STRIKE ВИДЕО ===
                        # FIX: If item comes from ready folder, initialize captions empty
                        if item.get("from_ready_folder", False):
                            caption = ""
                            caption_tg = ""
                            caption_meta = ""
                        else:
                            caption = item.get("caption", "")
                            caption_tg = prepare_caption_for_publish_tg(caption)
                            caption_meta = prepare_caption_for_publish_meta(caption)

                            if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                                caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                        
                        tmp_dir = Path("tmp_media")
                        tmp_dir.mkdir(exist_ok=True)
                        video_file_id = item["file_id"]
                        public_url = None
                        local_path = None
                        processed_path = None
                        upload_path = None
                        
                        try:
                            # Проверяем: это готовый файл или сырой?
                            if item.get("from_ready_folder", False):
                                # ✅ Это готовый файл - берём напрямую с диска
                                ready_video_path = Path(item["ready_file_path"])
                                
                                # 🔍 DIAGNOSTICS: Проверяем существование файла
                                file_exists = ready_video_path.exists()
                                file_absolute = ready_video_path.resolve()
                                log.info(f"[FIRST STRIKE] Ready file check: name={ready_video_path.name}, exists={file_exists}")
                                log.info(f"[FIRST STRIKE] Ready file absolute path: {file_absolute}")
                                
                                if not file_exists:
                                    # Выводим содержимое папки для диагностики
                                    ready_dir = READY_TO_PUBLISH_DIR.resolve()
                                    dir_contents = list(READY_TO_PUBLISH_DIR.glob("*"))[:20]
                                    log.error(f"[FIRST STRIKE] Ready file not found: {file_absolute}")
                                    log.error(f"[FIRST STRIKE] Ready directory: {ready_dir}")
                                    log.error(f"[FIRST STRIKE] Directory contents (first 20): {[f.name for f in dir_contents]}")
                                    log.error(f"[FIRST STRIKE] Drop missing ready file and continue: {ready_video_path}")
                                    continue
                                
                                upload_path = ready_video_path
                                
                                # Загружаем метаданные (поддерживаем .json и .mp4.json)
                                ready_meta_a = ready_video_path.with_suffix('.json')
                                ready_meta_b = ready_video_path.with_suffix('.mp4.json')
                                ready_meta_path = ready_meta_a if ready_meta_a.exists() else (ready_meta_b if ready_meta_b.exists() else None)
                                caption = ""
                                caption_tg = ""
                                caption_meta = ""

                                if ready_meta_path and ready_meta_path.exists():
                                    try:
                                        meta = json.loads(ready_meta_path.read_text(encoding='utf-8'))
                                        caption = meta.get('caption', '') or ""
                                        caption_tg = meta.get('caption_tg', '') or ""
                                        caption_meta = meta.get('caption_meta', '') or ""
                                        if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                                            caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                                        log.info(f"[FIRST STRIKE] Loaded ready meta: {ready_meta_path.name}")
                                    except Exception as e:
                                        log.error(f"[FIRST STRIKE] Failed to read meta json: {ready_meta_path.name} -> {e}")
                                else:
                                    log.error(f"[FIRST STRIKE] Meta json missing for ready file: {ready_video_path.name} -> using empty caption")
                                
                                log.info(f"[FIRST STRIKE] Using ready file: {ready_video_path.name}")
                                
                                # Загружаем в Supabase (если еще не загружено)
                                if not item.get("supabase_url"):
                                    content_type = "video/mp4"
                                    public_url = upload_to_supabase(str(upload_path), content_type)
                                    if public_url:
                                        log.info(f"[FIRST STRIKE] Supabase URL OK: {public_url}")
                                        item["supabase_url"] = public_url
                                    else:
                                        raise RuntimeError("[FIRST STRIKE] Supabase upload failed")
                                else:
                                    public_url = item["supabase_url"]
                                    log.info(f"[FIRST STRIKE] Using existing Supabase URL: {public_url}")
                            else:
                                # ⚠️ Это сырой файл - проверяем источник
                                
                                # ✅ ДОБАВЛЕНО: Обработка Instagram для First Strike
                                if video_file_id == "instagram_source" and item.get("instagram_video_path"):
                                    instagram_path = Path(item["instagram_video_path"])
                                    if not instagram_path.exists():
                                        log.error(f"[FIRST STRIKE] Видео не найдено по пути: {instagram_path}")
                                        continue
                                    local_path = instagram_path
                                    log.info(f"[FIRST STRIKE] Использую локальный файл Instagram: {local_path.name}")
                                else:
                                    # Стандартный путь: скачиваем из Telegram
                                    file_obj = await application.bot.get_file(video_file_id)
                                    remote_path = getattr(file_obj, "file_path", "") or ""
                                    suffix = Path(remote_path).suffix or ".mp4"
                                    local_path = tmp_dir / f"{video_file_id}{suffix}"
                                    
                                    # Скачиваем сырое видео из TG
                                    await file_obj.download_to_drive(custom_path=str(local_path))
                                    log.info(f"[FIRST STRIKE] Видео скачано из Telegram: {local_path.name}")
                                
                                # Обрабатываем видео
                                processed_path = process_video(local_path, caption)
                                if not processed_path or not Path(processed_path).exists():
                                    raise RuntimeError("[FIRST STRIKE] Video processing failed")
                                upload_path = processed_path

                                # Загружаем в Supabase
                                content_type = mimetypes.guess_type(str(upload_path))[0] or "video/mp4"
                                public_url = upload_to_supabase(str(upload_path), content_type)
                                if public_url:
                                    log.info(f"[FIRST STRIKE] Supabase URL OK: {public_url}")
                                    item["supabase_url"] = public_url
                                else:
                                    raise RuntimeError("[FIRST STRIKE] Supabase upload failed")
                                
                        except Exception as e:
                            error_msg = str(e)
                            log.error(f"[FIRST STRIKE] Processing error: {e}")
                            
                            # Удаляем временные файлы
                            for p in [local_path, processed_path]:
                                if p and Path(p).exists():
                                    await safe_unlink(p)
                            
                            # Проверяем на Invalid file_id или критические ошибки
                            if "Invalid file_id" in error_msg or "file_id" in error_msg.lower() or "Supabase" in error_msg:
                                log.critical(f"🚨 CRITICAL | [FIRST STRIKE] Broken file detected: {error_msg[:100]}")
                                log.critical("🚨 CRITICAL | [FIRST STRIKE] Skipping to next file immediately...")
                                post_attempt_failed = True
                                continue  # Переходим к следующему файлу
                            
                            # Для других ошибок тоже пробуем следующий
                            post_attempt_failed = True
                            continue
                        
                        # Если дошли сюда - файл обработан успешно, пробуем публиковать
                        if not post_attempt_failed and item.get("supabase_url"):
                            try:
                                # Публикация в Telegram
                                with open(upload_path, "rb") as f:
                                    await application.bot.send_video(
                                        chat_id=MAIN_CHANNEL_ID,
                                        video=f,
                                        caption=caption_tg if caption_tg else None,
                                        parse_mode="HTML",
                                        supports_streaming=True,
                                        width=1080,
                                        height=1920,
                                    )
                                    log.info("[FIRST STRIKE] Telegram format: VIDEO_STREAMING_ON")
                                
                                # Публикация в Facebook
                                try:
                                    item_fb = dict(item)
                                    item_fb["caption"] = caption_meta
                                    await publish_to_facebook(item_fb)
                                    append_history("FB", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                                except Exception as e:
                                    log.error(f"[FIRST STRIKE] Facebook publish error: {e}")
                                
                                # === DIAGNOSTIC: IG Schedule Check (First Strike) ===
                                now_fs_check = datetime.now()
                                log.info(f"[DIAGNOSTICS PRE-DECISION] [FIRST STRIKE]")
                                log.info(f"  FORCE_POST_NOW={FORCE_POST_NOW}")
                                log.info(f"  Current time={now_fs_check.strftime('%Y-%m-%d %H:%M:%S')} (hour={now_fs_check.hour})")
                                log.info(f"  IG_SCHEDULE: morning={IG_SCHEDULE['morning_videos']}/3, evening={IG_SCHEDULE['afternoon_videos']}/6")
                                
                                # Instagram публикация (без Plan B для First Strike - просто одна попытка)
                                if can_ig_publish("video", force=FORCE_POST_NOW):
                                    try:
                                        item_ig = dict(item)
                                        item_ig["caption"] = caption_meta
                                        ig_result = await publish_to_instagram(item_ig)
                                        if ig_result:
                                            log.info("[FIRST STRIKE] Instagram published successfully")
                                            append_history("IG", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                                    except Exception as e:
                                        log.error(f"[FIRST STRIKE] Instagram publish error: {e}")
                                
                                # Cleanup временных файлов
                                if item.get("from_ready_folder", False):
                                    # Для готовых файлов: удаляем файл и метаданные после публикации
                                    if upload_path and Path(upload_path).exists():
                                        await safe_unlink(upload_path)
                                        log.info(f"[FIRST STRIKE] Deleted ready file: {Path(upload_path).name}")
                                    # Удаляем метаданные (READY_META_EXT_FIX: try both formats)
                                    if upload_path:
                                        meta_path_a = Path(upload_path).with_suffix('.json')
                                        meta_path_b = Path(upload_path).with_suffix('.mp4.json')
                                        meta_path = meta_path_a if meta_path_a.exists() else (meta_path_b if meta_path_b.exists() else None)
                                        if meta_path and meta_path.exists():
                                            await safe_unlink(meta_path)
                                            log.info(f"[FIRST STRIKE] Deleted metadata: {meta_path.name}")
                                else:
                                    # Для сырых файлов: удаляем только временные файлы
                                    for p in [local_path, processed_path]:
                                        if p and Path(p).exists():
                                            await safe_unlink(p)
                                
                                # Удаляем из буфера
                                await delete_from_buffer(application, item)
                                await send_progress_report(application)
                                
                                # Обновляем статистику
                                increment_stat("video")
                                append_history("TG", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                                if caption:
                                    PUBLISHED_TEXTS.append(caption)
                                    if len(PUBLISHED_TEXTS) > MAX_PUBLISHED_TEXTS:
                                        PUBLISHED_TEXTS.pop(0)
                                    save_published_texts()
                                
                                # 🎯 УСПЕХ! Помечаем флаг и обновляем время
                                first_strike_success = True
                                now_publish = datetime.now()
                                LAST_POST_TIME = now_publish
                                save_last_post_time()
                                
                                # NOTE: IG_SCHEDULE counters incremented at the end of post_worker (no double increment)
                                log.info(f"✅ [FIRST STRIKE] SUCCESS after {first_strike_attempts} attempt(s)! Published one post. Cooldown active.")
                                
                            except Exception as e:
                                log.error(f"[FIRST STRIKE] Publication error: {e}")
                                # Cleanup (только временные файлы, готовые НЕ удаляем)
                                if not item.get("from_ready_folder", False):
                                    for p in [local_path, processed_path]:
                                        if p and Path(p).exists():
                                            await safe_unlink(p)
                                continue  # Пробуем следующий файл
                        # === КОНЕЦ БЛОКА ОБРАБОТКИ FIRST STRIKE ВИДЕО ===
                    
                    # После цикла First Strike
                    if first_strike_success:
                        log.info("[FIRST STRIKE] Completed! Next post in 60 minutes.")
                    else:
                        log.error(f"[FIRST STRIKE] FAILED after {first_strike_attempts} attempts. No successful post.")
                    
                    # СБРАСЫВАЕМ ФЛАГ (теперь First Strike завершен)
                    FIRST_RUN_IMMEDIATE = False
                    continue  # Возвращаемся к началу цикла worker
                else:
                    now = datetime.now()
                    ready = (LAST_POST_TIME is None) or ((now - LAST_POST_TIME) >= timedelta(seconds=PUBLISH_INTERVAL_SECONDS))
                    if not ready:
                        if POST_QUEUE and POST_QUEUE[0].get("type") == "photo" and LAST_POST_TIME:
                            next_time = LAST_POST_TIME + timedelta(seconds=PUBLISH_INTERVAL_SECONDS)
                            log.info(f"INFO | [NEXT] Type: Photo. Scheduled at: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
                        # === POSTNOW Wake-up: спим, но просыпаемся по событию ===
                        POSTNOW_EVENT.clear()
                        try:
                            await asyncio.wait_for(POSTNOW_EVENT.wait(), timeout=60)
                            log.info("[POSTNOW] Woken up by POSTNOW_EVENT!")
                        except asyncio.TimeoutError:
                            pass  # Обычный timeout, продолжаем работу
                        continue
                    
                    # 🎛️ MIXED QUEUE 4+4: Выбираем пост по логике чередования
                    # FIX A: Безопасный выбор ready-файлов
                    max_attempts = 10
                    attempts = 0
                    item = None
                    while attempts < max_attempts:
                        item = get_next_post_from_queue()
                        if not item:
                            log.warning("[MIXED QUEUE] No posts available in queue")
                            break
                        
                        # Проверяем, если это готовый файл из ready_to_publish
                        if item.get("from_ready_folder", False):
                            ready_path = Path(item.get("ready_file_path", ""))
                            if ready_path and not ready_path.exists():
                                log.error(f"[SCHEDULER] missing file, drop from queue: {ready_path.name}")
                                log.info(f"[SCHEDULER] pick_ready: name={ready_path.name}, exists=False")
                                item = None
                                attempts += 1
                                continue  # Попробуем следующий файл
                            else:
                                log.info(f"[SCHEDULER] pick_ready: name={ready_path.name}, exists=True")
                                break  # Файл существует, используем его
                        else:
                            # Это не ready-файл, можем использовать
                            break
                    
                    if not item:
                        if attempts >= max_attempts:
                            log.warning("[SCHEDULER] attempts_exhausted (10), skipping post cycle")
                        # === POSTNOW Wake-up: спим, но просыпаемся по событию ===
                        POSTNOW_EVENT.clear()
                        try:
                            await asyncio.wait_for(POSTNOW_EVENT.wait(), timeout=60)
                            log.info("[POSTNOW] Woken up by POSTNOW_EVENT!")
                        except asyncio.TimeoutError:
                            pass  # Обычный timeout, продолжаем работу
                        continue
                    
                    save_queue()
                    log.info("Worker pop type=%s voiceover=%s size_after_pop=%s (scheduled)", 
                            item["type"], item.get("voiceover", False), len(POST_QUEUE))

                try:
                    if item["type"] == "carousel_pending":
                        log.info("Carousel posts temporarily disabled; skipping.")
                        await delete_from_buffer(application, item)
                        await send_progress_report(application)
                        continue
                    if item["type"] == "text":
                        text = prepare_caption_for_publish(item.get("text", ""))
                        msg = await application.bot.send_message(
                            chat_id=MAIN_CHANNEL_ID,
                            text=text,
                            parse_mode="HTML"
                        )
                        increment_stat("text")
                        PUBLISHED_TEXTS.append(text)
                        if len(PUBLISHED_TEXTS) > MAX_PUBLISHED_TEXTS:
                            PUBLISHED_TEXTS.pop(0)
                        save_published_texts()
                        log.info("published_ok (text)")
                        await delete_from_buffer(application, item)
                        await send_progress_report(application)
                        LAST_POST_TIME = datetime.now()
                        save_last_post_time()
                    elif item["type"] == "photo":
                        upload_path = None
                        caption_tg = prepare_caption_for_publish_tg(item.get("caption", ""))
                        caption_meta = prepare_caption_for_publish_meta(item.get("caption", ""))
                        if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                            caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                            log.info(f"Caption trimmed to {len(caption_tg)} chars (was {len(item.get('caption', ''))})")

                        tmp_dir = Path("tmp_media")
                        tmp_dir.mkdir(exist_ok=True)
                        photo_file_id = item["file_id"]
                        public_url = None
                        local_path = None
                        processed_photo = None
                        log.info(f"[DEBUG] Starting Supabase upload for post (photo) file_id={photo_file_id}")
                        try:
                            file_obj = await application.bot.get_file(photo_file_id)
                            remote_path = getattr(file_obj, "file_path", "") or ""
                            suffix = Path(remote_path).suffix or ".jpg"
                            local_path = tmp_dir / f"{photo_file_id}{suffix}"
                            await file_obj.download_to_drive(custom_path=str(local_path))
                            
                            processed_photo = process_photo(local_path)
                            upload_path = processed_photo if processed_photo and Path(processed_photo).exists() else local_path
                            if upload_path == local_path and not processed_photo:
                                log.warning("Photo watermark skipped (processing failed); sending original photo.")

                            content_type = mimetypes.guess_type(str(upload_path))[0] or "image/jpeg"
                            public_url = upload_to_supabase(str(upload_path), content_type)
                            if public_url:
                                log.info(f"SUPABASE_URL_OK: {public_url}")
                                item["supabase_url"] = public_url
                            else:
                                log.error("SUPABASE_UPLOAD_FAILED")
                        except Exception as e:
                            log.error(f"SUPABASE_UPLOAD_FAILED: {e}")
                            send_admin_error(f"Supabase upload failed (photo): {e}")
                            await sleep_or_postnow(5)
                            continue
                        if not upload_path or not Path(upload_path).exists():
                            log.error("Photo upload_path missing; skipping send.")
                        else:
                            try:
                                with open(upload_path, "rb") as f:
                                    await application.bot.send_photo(
                                        chat_id=MAIN_CHANNEL_ID,
                                        photo=f,
                                        caption=caption_tg if caption_tg else None,
                                        parse_mode="HTML"
                                    )
                            except Exception as e:
                                log.error(f"Telegram send photo failed: {e}")
                            try:
                                item_fb = dict(item)
                                item_fb["caption"] = caption_meta
                                await publish_to_facebook(item_fb)
                                append_history("FB", "Photo", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                            except Exception as e:
                                log.error(f"Facebook publish error (photo): {e}")
                                send_admin_error(f"Facebook publish error (photo): {e}")

                            increment_stat("photo")
                            append_history("TG", "Photo", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                            if caption_tg:
                                PUBLISHED_TEXTS.append(caption_tg)
                                if len(PUBLISHED_TEXTS) > MAX_PUBLISHED_TEXTS:
                                    PUBLISHED_TEXTS.pop(0)
                                save_published_texts()
                            log.info("published_ok (photo)")
                        
                        # cleanup после отправки
                        for p in [local_path, processed_photo, upload_path]:
                            if p and Path(p).exists():
                                await safe_unlink(p)

                        await delete_from_buffer(application, item)
                        await send_progress_report(application)
                        LAST_PHOTO_TIME = datetime.now()
                        LAST_POST_TIME = datetime.now()
                        save_last_post_time()
                        
                        # NOTE: IG_SCHEDULE counters incremented at the end of post_worker (no double increment)
                        # IG: только видео, пропускаем фото
                        maybe_delete_supabase_media(item, reason="telegram")
                    elif item["type"] == "video":
                        # Инициализация переменных для всех путей обработки
                        local_path = None
                        processed_path = None
                        upload_path = None
                        
                        # Проверяем, это готовый файл из ready_to_publish или нужно обработать
                        if item.get("from_ready_folder", False):
                            # Берем готовый файл, который уже был загружен в очередь
                            log.info("[CONVEYOR] Using pre-loaded ready video from queue")
                            
                            ready_video_path = Path(item["ready_file_path"])
                            
                            # 🔍 DIAGNOSTICS: Проверяем существование файла
                            file_exists = ready_video_path.exists()
                            file_absolute = ready_video_path.resolve()
                            log.info(f"[CONVEYOR] Ready file check: name={ready_video_path.name}, exists={file_exists}")
                            log.info(f"[CONVEYOR] Ready file absolute path: {file_absolute}")
                            
                            if not file_exists:
                                # Выводим содержимое папки для диагностики
                                ready_dir = READY_TO_PUBLISH_DIR.resolve()
                                dir_contents = list(READY_TO_PUBLISH_DIR.glob("*"))[:20]
                                log.error(f"[CONVEYOR] Ready file not found: {file_absolute}")
                                log.error(f"[CONVEYOR] Ready directory: {ready_dir}")
                                log.error(f"[CONVEYOR] Directory contents (first 20): {[f.name for f in dir_contents]}")
                                continue
                            
                            # Загружаем метаданные (поддерживаем .json и .mp4.json)
                            ready_meta_a = ready_video_path.with_suffix('.json')
                            ready_meta_b = ready_video_path.with_suffix('.mp4.json')
                            ready_meta_path = ready_meta_a if ready_meta_a.exists() else (ready_meta_b if ready_meta_b.exists() else None)
                            
                            # Загружаем метаданные
                            caption = item.get("caption", "")
                            if ready_meta_path and ready_meta_path.exists():
                                try:
                                    with open(ready_meta_path, 'r', encoding='utf-8') as f:
                                        meta = json.load(f)
                                        caption = meta.get('caption', caption)
                                        log.info(f"[CONVEYOR] Loaded metadata from {ready_meta_path.name}")
                                except Exception as e:
                                    log.warning(f"[CONVEYOR] Failed to load metadata: {e}")
                            
                            caption_tg = prepare_caption_for_publish_tg(caption)
                            caption_meta = prepare_caption_for_publish_meta(caption)
                            
                            if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                                caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                            
                            upload_path = ready_video_path
                            # ✅ FIX: Устанавливаем local_path для Plan B Instagram
                            local_path = ready_video_path
                        else:
                            # ⚠️ Это сырой файл - скачиваем и обрабатываем
                            log.info("[CONVEYOR] Processing raw video file")
                            
                            tmp_dir = Path("tmp_media")
                            tmp_dir.mkdir(exist_ok=True)
                            video_file_id = item["file_id"]
                            
                            caption = item.get("caption", "")
                            caption_tg = prepare_caption_for_publish_tg(caption)
                            caption_meta = prepare_caption_for_publish_meta(caption)
                            
                            if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                                caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                            
                            try:
                                # Скачиваем файл из Telegram
                                file_obj = await application.bot.get_file(video_file_id)
                                remote_path = getattr(file_obj, "file_path", "") or ""
                                suffix = Path(remote_path).suffix or ".mp4"
                                local_path = tmp_dir / f"{video_file_id}{suffix}"
                                await file_obj.download_to_drive(custom_path=str(local_path))
                                
                                # Обрабатываем видео
                                processed_path = process_video(local_path, caption)
                                if not processed_path or not Path(processed_path).exists():
                                    log.error("[CONVEYOR] Video processing failed")
                                    # Cleanup
                                    if local_path and Path(local_path).exists():
                                        await safe_unlink(local_path)
                                    continue
                                
                                upload_path = processed_path
                                log.info(f"[CONVEYOR] Raw video processed: {Path(upload_path).name}")
                            except Exception as e:
                                log.error(f"[CONVEYOR] Failed to process raw video: {e}")
                                # Cleanup
                                for p in [local_path, processed_path]:
                                    if p and Path(p).exists():
                                        await safe_unlink(p)
                                continue
                        
                        # Загружаем готовое видео в Supabase (если еще не загружено)
                        if not item.get("supabase_url"):
                            # ПРОВЕРКА: Файл должен существовать перед загрузкой
                            if not upload_path or not Path(upload_path).exists():
                                log.critical(f"🚨 CRITICAL | File not found for upload: {upload_path}")
                                log.critical("🚨 CRITICAL | Skipping broken post due to missing file")
                                # Удаляем метаданные если есть (READY_META_EXT_FIX: try both formats)
                                if upload_path:
                                    meta_path_a = Path(str(upload_path)).with_suffix('.json')
                                    meta_path_b = Path(str(upload_path)).with_suffix('.mp4.json')
                                    meta_path = meta_path_a if meta_path_a.exists() else (meta_path_b if meta_path_b.exists() else None)
                                    if meta_path.exists():
                                        await safe_unlink(meta_path)
                                save_queue()
                                await sleep_or_postnow(300)
                                continue
                            
                            public_url = None
                            try:
                                content_type = "video/mp4"
                                public_url = upload_to_supabase(str(upload_path), content_type)
                                if public_url:
                                    log.info(f"[SUPABASE] Upload OK: {public_url}")
                                    item["supabase_url"] = public_url
                                else:
                                    log.error("[SUPABASE] Upload failed")
                                    if item.get("from_ready_folder"):
                                        # Удаляем битый файл (READY_META_EXT_FIX: try both formats)
                                        if upload_path.exists():
                                            await safe_unlink(upload_path)
                                        meta_path_a = upload_path.with_suffix('.json')
                                        meta_path_b = upload_path.with_suffix('.mp4.json')
                                        meta_path = meta_path_a if meta_path_a.exists() else (meta_path_b if meta_path_b.exists() else None)
                                        if meta_path and meta_path.exists():
                                            await safe_unlink(meta_path)
                                    log.critical("🚨 CRITICAL | Skipping broken post due to Supabase upload failure")
                                    save_queue()
                                    await sleep_or_postnow(300)
                                    continue
                            except Exception as e:
                                log.error(f"[SUPABASE] Upload error: {e}")
                                log.critical("🚨 CRITICAL | Skipping broken post due to Supabase exception")
                                save_queue()
                                await sleep_or_postnow(300)
                                continue
                        
                        # Публикация в Telegram
                        try:
                            with open(upload_path, "rb") as f:
                                await application.bot.send_video(
                                    chat_id=MAIN_CHANNEL_ID,
                                    video=f,
                                    caption=caption_tg if caption_tg else None,
                                    parse_mode="HTML",
                                    supports_streaming=True,
                                    width=1080,
                                    height=1920,
                                )
                            log.info("Telegram format: VIDEO_STREAMING_ON")
                        except Exception as e:
                            log.error(f"Telegram send video failed: {e}")
                            try:
                                item_fb = dict(item)
                                item_fb["caption"] = caption_meta
                                await publish_to_facebook(item_fb)
                                append_history("FB", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                            except Exception as e:
                                log.error(f"Facebook publish error (video): {e}")
                                send_admin_error(f"Facebook publish error (video): {e}")
                        # INSTAGRAM ПУБЛИКАЦИЯ С ПЛАНОМ Б (Гарантированная публикация)
                        ig_success = False
                        ig_publish_attempts = 0
                        max_ig_attempts = 3
                        
                        # === DIAGNOSTIC: IG Schedule Check ===
                        now_before_check = datetime.now()
                        ready_count = len(list(READY_TO_PUBLISH_DIR.glob("ready_*.mp4")))
                        last_post_str = LAST_POST_TIME.strftime('%Y-%m-%d %H:%M:%S') if LAST_POST_TIME else "Never"
                        log.info(f"[DIAGNOSTICS PRE-DECISION]")
                        log.info(f"  FORCE_POST_NOW={FORCE_POST_NOW}")
                        log.info(f"  Current time={now_before_check.strftime('%Y-%m-%d %H:%M:%S')} (hour={now_before_check.hour})")
                        log.info(f"  IG_SCHEDULE: morning={IG_SCHEDULE['morning_videos']}/3, evening={IG_SCHEDULE['afternoon_videos']}/6")
                        log.info(f"  LAST_POST_TIME={last_post_str}")
                        log.info(f"  Queue size={len(POST_QUEUE)}, Ready count={ready_count}")
                        
                        # Проверка успешности Supabase ПЕРЕД попыткой IG публикации
                        if can_ig_publish("video", force=FORCE_POST_NOW):
                            if not item.get("supabase_url"):
                                log.error("[IG_BLOCKED] Supabase upload failed - skipping Instagram publish to avoid empty URL")
                            else:
                                dark_palette = [(0, 0, 0), (10, 10, 20), (20, 20, 30), (12, 8, 24), (6, 12, 18)]
                                
                                while ig_publish_attempts < max_ig_attempts and not ig_success:
                                    ig_publish_attempts += 1
                                    
                                    try:
                                        # Первая попытка - используем уже обработанное видео
                                        if ig_publish_attempts == 1:
                                            log.info(f"[IG_ATTEMPT_{ig_publish_attempts}] Publishing with original processed video")
                                            item_ig = dict(item)
                                            item_ig["caption"] = caption_meta
                                            ig_result = await publish_to_instagram(item_ig)
                                            
                                            if ig_result is True:
                                                ig_success = True
                                                append_history("IG", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                                                log.info("[IG_SUCCESS] Video published successfully on first attempt")
                                                break
                                            else:
                                                log.warning(f"[IG_ATTEMPT_{ig_publish_attempts}] Failed, preparing Plan B")
                                        
                                        # ПЛАН Б: Повторные попытки с изменением параметров
                                        else:
                                            log.warning(f"[PLAN B] Instagram retry attempt {ig_publish_attempts}/{max_ig_attempts} with new unique parameters...")
                                            
                                            # Параметры для Плана Б
                                            speed_mult = 1.01 + (ig_publish_attempts - 1) * 0.01  # 1.01, 1.02, 1.03
                                            bg_color_new = dark_palette[(ig_publish_attempts - 1) % len(dark_palette)]
                                            brightness_adj = 0.01 * ig_publish_attempts  # 0.01, 0.02, 0.03
                                            
                                            log.info(f"[PLAN B] Reprocessing video: speed={speed_mult:.3f}, bg={bg_color_new}, brightness={brightness_adj:+.3f}")
                                            
                                            # Перерабатываем видео с новыми параметрами
                                            processed_path_retry = process_video(
                                                local_path, 
                                                caption, 
                                                speed_multiplier=speed_mult, 
                                                bg_color_override=bg_color_new, 
                                                brightness_adjust=brightness_adj,
                                                random_crop=True  # Случайная обрезка для обхода алгоритмов Meta
                                            )
                                            
                                            if not processed_path_retry or not Path(processed_path_retry).exists():
                                                log.error(f"[PLAN B] Video reprocessing failed on attempt {ig_publish_attempts}")
                                                continue
                                            
                                            # Загружаем новую версию в Supabase
                                            content_type_retry = mimetypes.guess_type(str(processed_path_retry))[0] or "video/mp4"
                                            public_url_retry = upload_to_supabase(str(processed_path_retry), content_type_retry)
                                            
                                            if not public_url_retry:
                                                log.error(f"[PLAN B] Supabase upload failed on attempt {ig_publish_attempts}")
                                                # Удаляем временный файл повторной обработки
                                                if Path(processed_path_retry).exists():
                                                    await safe_unlink(processed_path_retry)
                                                continue
                                            
                                            # Удаляем старый файл из Supabase перед новой попыткой
                                            old_url = item.get("supabase_url")
                                            if old_url:
                                                delete_supabase_file(old_url)
                                            
                                            # Обновляем URL в item
                                            item["supabase_url"] = public_url_retry
                                            item_ig = dict(item)
                                            item_ig["caption"] = caption_meta
                                            
                                            log.info(f"[PLAN B] Attempting publish with new URL: {public_url_retry[:60]}...")
                                            ig_result = await publish_to_instagram(item_ig)
                                            
                                            if ig_result is True:
                                                ig_success = True
                                                append_history("IG", "Video", public_url_retry, item.get("translation_cost", 0.0))
                                                log.info(f"[PLAN B SUCCESS] Video published on attempt {ig_publish_attempts}")
                                                
                                                # Удаляем временный файл повторной обработки
                                                if Path(processed_path_retry).exists():
                                                    await safe_unlink(processed_path_retry)
                                                break
                                            else:
                                                log.warning(f"[PLAN B] Attempt {ig_publish_attempts} failed")
                                                # Удаляем временный файл повторной обработки
                                                if Path(processed_path_retry).exists():
                                                    await safe_unlink(processed_path_retry)
                                                
                                                if ig_publish_attempts >= max_ig_attempts:
                                                    log.error(f"[PLAN B EXHAUSTED] All {max_ig_attempts} attempts failed, giving up on this post")
                                                    send_admin_error(f"Instagram: Failed after {max_ig_attempts} attempts (Plan B exhausted)")
                                    
                                    except Exception as e:
                                        log.error(f"[IG_ATTEMPT_{ig_publish_attempts}] Exception: {e}")
                                        send_admin_error(f"Instagram publish error (attempt {ig_publish_attempts}): {e}")
                                        
                                        if ig_publish_attempts >= max_ig_attempts:
                                            log.error("[PLAN B EXHAUSTED] Maximum attempts reached, moving to next post")
                        
                        # ОТЛОЖЕННОЕ УДАЛЕНИЕ: Только после успеха Instagram или исчерпания попыток
                        if ig_success:
                            log.info("[IG_SUCCESS] Waiting 300 seconds before cleanup (guaranteed publish protocol)")
                            await sleep_or_postnow(300)
                        
                        # cleanup после отправки
                        # CONVEYOR: Удаляем готовый файл из ready_to_publish
                        if upload_path and upload_path.parent == READY_TO_PUBLISH_DIR:
                            try:
                                if upload_path.exists():
                                    await safe_unlink(upload_path)
                                    log.info(f"[CONVEYOR] Deleted ready file: {upload_path.name}")
                                # Удаляем метаданные (READY_META_EXT_FIX: try both formats)
                                meta_path_a = upload_path.with_suffix('.json')
                                meta_path_b = upload_path.with_suffix('.mp4.json')
                                meta_path = meta_path_a if meta_path_a.exists() else (meta_path_b if meta_path_b.exists() else None)
                                if meta_path and meta_path.exists():
                                    await safe_unlink(meta_path)
                                    log.info(f"[CONVEYOR] Deleted metadata: {meta_path.name}")
                            except Exception as e:
                                log.warning(f"[CONVEYOR] Failed to delete ready file: {e}")
                        else:
                            # FIRST STRIKE: Удаляем временные файлы (local_path, processed_path)
                            try:
                                if 'local_path' in locals() and local_path and Path(local_path).exists():
                                    await safe_unlink(local_path)
                                    log.info(f"[FIRST STRIKE] Deleted temp file: {Path(local_path).name}")
                                if 'processed_path' in locals() and processed_path and Path(processed_path).exists():
                                    await safe_unlink(processed_path)
                                    log.info(f"[FIRST STRIKE] Deleted processed file: {Path(processed_path).name}")
                                if upload_path and Path(upload_path).exists():
                                    await safe_unlink(upload_path)
                                    log.info(f"[FIRST STRIKE] Deleted upload file: {Path(upload_path).name}")
                            except Exception as e:
                                log.warning(f"[FIRST STRIKE] Failed to delete temp files: {e}")
                        
                        # Удаление из Supabase ТОЛЬКО если IG успешна или попытки исчерпаны
                        if ig_success or ig_publish_attempts >= max_ig_attempts:
                            maybe_delete_supabase_media(item, reason="all_platforms_complete")
                            log.info(f"[CLEANUP] Supabase cleanup executed (ig_success={ig_success}, attempts={ig_publish_attempts})")
                        else:
                            log.warning("[CLEANUP] Supabase cleanup skipped - IG publish pending")
                        
                        increment_stat("video")
                        append_history("TG", "Video", item.get("supabase_url", "-"), item.get("translation_cost", 0.0))
                        if caption:
                            PUBLISHED_TEXTS.append(caption)
                            if len(PUBLISHED_TEXTS) > MAX_PUBLISHED_TEXTS:
                                PUBLISHED_TEXTS.pop(0)
                            save_published_texts()
                        log.info("published_ok (video)")
                        
                        await delete_from_buffer(application, item)
                        await send_progress_report(application)
                        LAST_VIDEO_TIME = datetime.now()
                        LAST_POST_TIME = datetime.now()
                        save_last_post_time()
                        
                        # Increment schedule counters (9 posts/day: 3 morning + 6 evening)
                        now_publish = datetime.now()
                        if now_publish.hour < 14:
                            IG_SCHEDULE["morning_videos"] += 1
                            log.info(f"[SCHEDULER] Morning counter: {IG_SCHEDULE['morning_videos']}/3")
                        elif 16 <= now_publish.hour <= 21:
                            IG_SCHEDULE["afternoon_videos"] += 1
                            log.info(f"[SCHEDULER] Evening counter: {IG_SCHEDULE['afternoon_videos']}/6")
                        
                        # === FINAL POSTNOW RESET (after full publish cycle) ===
                        if FORCE_POST_NOW:
                            FORCE_POST_NOW = False
                            log.info("[POSTNOW] Final reset after full multi-platform publish")
                except Exception as e:
                    log.error(f"Failed to send post: {e}")
                    error_msg = str(e)
                    
                    # Не зацикливаемся на битых постах
                    if isinstance(e, BadRequest) or "Bad Request" in error_msg or "Invalid file_id" in error_msg:
                        log.critical("🚨 CRITICAL | Skipping broken post due to BadRequest/Invalid file_id")
                        try:
                            maybe_delete_supabase_media(item, reason="bad_request")
                            await delete_from_buffer(application, item)
                            await send_progress_report(application)
                        except Exception as e2:
                            log.error(f"Failed to cleanup after BadRequest: {e2}")
                        # НЕ возвращаем в очередь
                        save_queue()
                        await sleep_or_postnow(300)
                    else:
                        # Только для неизвестных ошибок возвращаем в очередь
                        POST_QUEUE.appendleft(item)
                        save_queue()
                        await sleep_or_postnow(60)
            else:
                # Очередь пустая - проверяем, есть ли готовые файлы
                loaded = load_ready_files_to_queue()
                if loaded == 0:
                    log.info("[DEBUG] Queue empty and no ready files. Waiting...")
                await sleep_or_postnow(60)
        except Exception as e:
            log.exception(f"[POST_WORKER] Loop error (will continue): {e}")
            await asyncio.sleep(1)
            continue


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаленный рестарт бота (только для админа)"""
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized restart attempt from user_id={user_id}")
        return
    
    log.info(f"[RESTART] Remote restart initiated by admin (user_id={user_id})")
    
    try:
        await update.message.reply_text(
            "🚀 Рестарт запущен... Обновляю систему.",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[RESTART] Failed to send confirmation message: {e}")
    
    # Перезапуск процесса Python
    log.info("[RESTART] Executing restart...")
    os.execv(sys.executable, ['python'] + sys.argv)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаленная остановка бота (только для админа)"""
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized stop attempt from user_id={user_id}")
        return
    
    log.info(f"[STOP] Remote shutdown initiated by admin (user_id={user_id})")
    
    try:
        await update.message.reply_text(
            "🛑 Система остановлена админом. Выход из процесса...",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[STOP] Failed to send confirmation message: {e}")
    
    # Немедленная остановка процесса (прекращает работу всех фоновых воркеров)
    log.info("[STOP] Executing shutdown... All workers and conveyor system will be terminated.")
    os._exit(0)


async def postnow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Форс-публикация сразу (переопределяет расписание)"""
    global FORCE_POST_NOW, POSTNOW_EVENT
    
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized postnow attempt from user_id={user_id}")
        return
    
    FORCE_POST_NOW = True
    POSTNOW_EVENT.set()  # Пробуждаем воркер немедленно
    
    log.info(f"[POSTNOW] Force post override activated by admin (user_id={user_id}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        await update.message.reply_text(
            "✅ POSTNOW: воркер разбужен, пробую публиковать сейчас.",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[POSTNOW] Failed to send confirmation message: {e}")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ставит конвейер на паузу (только для админа)"""
    global IS_PAUSED
    
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized pause attempt from user_id={user_id}")
        return
    
    IS_PAUSED = True
    log.info(f"[PAUSE] Conveyor paused by admin (user_id={user_id})")
    
    try:
        await update.message.reply_text(
            "⏸ Конвейер на паузе. Посты остановлены.",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[PAUSE] Failed to send confirmation message: {e}")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возобновляет работу конвейера (только для админа)"""
    global IS_PAUSED
    
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized resume attempt from user_id={user_id}")
        return
    
    IS_PAUSED = False
    log.info(f"[RESUME] Conveyor resumed by admin (user_id={user_id})")
    
    try:
        await update.message.reply_text(
            "▶️ Конвейер запущен! Продолжаем работу.",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[RESUME] Failed to send confirmation message: {e}")


async def interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Изменяет интервал публикаций (только для админа)"""
    global PUBLISH_INTERVAL_SECONDS
    
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized interval attempt from user_id={user_id}")
        return

    # Получаем новый интервал из аргументов команды
    try:
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "❌ Укажите интервал в минутах.\nПример: /interval 60",
                parse_mode='HTML'
            )
            return
        
        new_interval_minutes = int(context.args[0])
        
        if new_interval_minutes < 1 or new_interval_minutes > 1440:
            await update.message.reply_text(
                "❌ Интервал должен быть от 1 до 1440 минут (24 часа).",
                parse_mode='HTML'
            )
            return
        
        PUBLISH_INTERVAL_SECONDS = new_interval_minutes * 60
        log.info(f"[INTERVAL] Changed to {new_interval_minutes} minutes by admin (user_id={user_id})")
        
        await update.message.reply_text(
            f"⏰ Интервал обновлен: {new_interval_minutes} мин. Следующий пост подстроится под это время.",
            parse_mode='HTML'
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Укажите число.\nПример: /interval 60",
            parse_mode='HTML'
        )
    except Exception as e:
        log.error(f"[INTERVAL] Error: {e}")
        await update.message.reply_text(
            "❌ Ошибка при изменении интервала.",
            parse_mode='HTML'
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает мониторинг системы (только для админа)"""
    user_id = update.effective_user.id if update.effective_user else None
    
    if user_id != ADMIN_TELEGRAM_ID:
        log.warning(f"[SECURITY] Unauthorized status attempt from user_id={user_id}")
        return

    try:
        # Состояние системы
        status_text = "✅ РАБОТАЕТ" if not IS_PAUSED else "⏸ ПАУЗА"
        
        # Интервал в минутах
        interval_minutes = PUBLISH_INTERVAL_SECONDS // 60
        
        # Время до следующего поста
        if LAST_POST_TIME is None:
            time_remaining = "⚡ Готов к немедленной публикации"
        else:
            next_post_time = LAST_POST_TIME + timedelta(seconds=PUBLISH_INTERVAL_SECONDS)
            now = datetime.now()
            time_diff = next_post_time - now
            
            if time_diff.total_seconds() <= 0:
                time_remaining = "⚡ Готов к публикации"
            else:
                minutes = int(time_diff.total_seconds() // 60)
                seconds = int(time_diff.total_seconds() % 60)
                time_remaining = f"{minutes:02d}:{seconds:02d}"
        
        # Количество готовых видео на складе
        ready_files = list(READY_TO_PUBLISH_DIR.glob("ready_*.mp4"))
        ready_count = len(ready_files)
        
        # Количество видео в очереди
        queue_count = len(POST_QUEUE)
        video_queue_count = sum(1 for item in POST_QUEUE if item.get("type") == "video")
        
        # Формируем красивое сообщение
        status_message = (
            f"📊 <b>МОНИТОРИНГ СИСТЕМЫ:</b>\n\n"
            f"● Статус: {status_text}\n"
            f"● Интервал: {interval_minutes} мин.\n"
            f"● СЛЕДУЮЩИЙ ПОСТ ЧЕРЕЗ: {time_remaining}\n"
            f"● Готовых HD-видео (склад): {ready_count}/5\n"
            f"● Видео в очереди (база): {video_queue_count}\n"
            f"● Всего в очереди: {queue_count}\n"
        )
        
        await update.message.reply_text(
            status_message,
            parse_mode='HTML'
        )
        
        log.info(f"[STATUS] System status requested by admin (user_id={user_id})")
        
    except Exception as e:
        log.error(f"[STATUS] Error: {e}")
        await update.message.reply_text(
            "❌ Ошибка при получении статуса системы.",
            parse_mode='HTML'
        )


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.channel_post
    if not msg:
        return

    chat_id = msg.chat_id
    message_id = msg.message_id

    log.info(f"channel_post received: chat_id={chat_id}, message_id={message_id}")

    # реагируем только на буферный канал
    if chat_id != BUFFER_CHANNEL_ID:
        log.info(f"Ignored channel_post from chat_id={chat_id} (not BUFFER)")
        return

    # получаем текст поста
    post = update.channel_post
    # CAPTION_SOURCE_PRIORITY: prefer caption over text
    src_text_raw = (post.caption or post.text or "")
    src_text = ensure_utf8_text(src_text_raw).strip()
    log.info("RAW_CAPTION_SOURCE: %s", src_text[:200] if src_text else "(empty)")
    text_for_translate = src_text
    entities = post.entities or post.caption_entities
    
    # 🔍 SMART ROUTING: Проверяем наличие Instagram URL
    instagram_url = None
    instagram_video_path = None
    is_url_source = False
    
    # Ищем Instagram URL в тексте
    if text_for_translate:
        import re
        instagram_pattern = r'https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/[\w-]+'
        match = re.search(instagram_pattern, text_for_translate)
        if match:
            instagram_url = match.group(0)
            is_url_source = True
            log.info(f"[SMART ROUTING] Instagram URL detected: {instagram_url[:50]}...")
            
            # Скачиваем видео из Instagram
            try:
                instagram_video_path = download_from_instagram(instagram_url)
                if not instagram_video_path:
                    error_msg = f"❌ [INSTAGRAM] Не удалось скачать видео из URL: {instagram_url}"
                    log.error(error_msg)
                    # Отправляем отчет админу
                    try:
                        await context.bot.send_message(
                            chat_id=ADMIN_TELEGRAM_ID,
                            text=f"🚨 <b>Instagram Download Failed</b>\n\n{error_msg}",
                            parse_mode='HTML'
                        )
                    except:
                        pass
                    return  # Завершаем обработку для этого сообщения
                log.info(f"[SMART ROUTING] ✅ Video downloaded from Instagram: {instagram_video_path.name}")
            except Exception as e:
                error_msg = f"❌ [INSTAGRAM] Ошибка при скачивании: {e}"
                log.error(error_msg)
                # Отправляем отчет админу
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=f"🚨 <b>Instagram Download Error</b>\n\n{error_msg}",
                        parse_mode='HTML'
                    )
                except:
                    pass
                return  # Завершаем обработку

    # 🎤 WHISPER: Если это видео (Telegram или Instagram), пытаемся получить транскрибацию
    whisper_transcript = None
    video_source_path = None
    
    if instagram_video_path:
        # Используем скачанное видео из Instagram
        video_source_path = instagram_video_path
        log.info("[WHISPER] Processing Instagram video...")
    elif post.video:
        # Используем видео из Telegram
        log.info("[WHISPER] Processing Telegram video...")
    
    # Only attempt Whisper transcription if no src_text provided
    if (video_source_path or post.video) and not text_for_translate.strip():
        try:
            if not video_source_path:
                # Скачиваем видео из Telegram
                log.info("[WHISPER] Video detected, attempting transcription...")
                tmp_dir = Path("tmp_media")
                tmp_dir.mkdir(exist_ok=True)
                video_file = await context.bot.get_file(post.video.file_id)
                tmp_video_path = tmp_dir / f"whisper_video_{post.video.file_id[:10]}.mp4"
                await video_file.download_to_drive(custom_path=str(tmp_video_path))
                video_source_path = tmp_video_path
            
            # Получаем транскрибацию
            whisper_transcript = get_video_transcript(video_source_path)
            
            # Удаляем временное видео (только если это из Telegram, Instagram удалим позже)
            if post.video and video_source_path.exists():
                video_source_path.unlink()
                log.info("[WHISPER] Temporary Telegram video file deleted")
            
            if whisper_transcript:
                log.info(f"[WHISPER] ✅ Transcription successful: {len(whisper_transcript)} chars")
                # Используем транскрибацию как основной текст
                text_for_translate = whisper_transcript
            else:
                log.warning("[WHISPER] Transcription failed, using caption text")
        except Exception as e:
            log.error(f"[WHISPER] Video transcription error: {e}")
            # Продолжаем с обычным текстом при ошибке

    log.info("RAW before translate: %s", text_for_translate[:200] if text_for_translate else "(empty)")

    # ГАРАНТИРУЕМ перевод ВСЕХ постов
    if text_for_translate.strip():
        # преобразуем entities в маркеры перед переводом
        prepared = entities_to_markers(text_for_translate, entities)
        translated = await translate_text(prepared)
    else:
        translated = ""
    
    final_text = sanitize_post(translated)
    
    # Убираем фразы про комментарии
    final_text = remove_comment_phrases(final_text)

    log.info("FINAL after translate: %s", final_text[:200] if final_text else "(empty)")

    # 🎙️ ELEVENLABS: SMART ROUTING - генерируем озвучку только для Instagram URL
    voiceover_path = None
    has_voiceover = False
    
    if is_url_source and final_text.strip():
        # IF URL (Instagram): Generate ElevenLabs voiceover
        try:
            log.info("[SMART ROUTING] Instagram source → Generating ElevenLabs voiceover...")
            # Извлекаем чистый текст без хэштегов для озвучки
            text_for_voice = final_text.split('\n')[0]  # Берем первую строку (основной текст)
            voiceover_path = generate_voiceover(text_for_voice)
            
            if voiceover_path:
                has_voiceover = True
                log.info(f"[ELEVENLABS] ✅ Voiceover ready: {voiceover_path.name} (voiceover: True)")
            else:
                log.warning("[ELEVENLABS] Voiceover generation failed, continuing without voice")
        except Exception as e:
            log.error(f"[ELEVENLABS] Voiceover generation error: {e}")
            # Продолжаем без озвучки при ошибке
    elif post.video:
        # IF FILE (Telegram): SKIP ElevenLabs
        log.info("[SMART ROUTING] Telegram source → Skipping ElevenLabs (voiceover: False)")
        has_voiceover = False

    # форматируем финальный текст
    final_text = format_post_structure(final_text)
    
    # Глубокая очистка: убираем старые ссылки/хэштеги/упоминания перед добавлением наших блоков
    final_text = clean_caption(final_text)
    
    # ГАРАНТИРУЕМ наличие футера ПОСЛЕ очистки
    final_text = ensure_footer(final_text)
    final_text = append_branding(final_text)
    final_text = append_hashtags(final_text)

    # отправляем в основной канал
    if post.photo:
        # если есть фото, добавляем в очередь
        item = {
            "type": "photo",
            "file_id": post.photo[-1].file_id,
            "caption": final_text,
            "buffer_message_id": message_id,
            "buffer_chat_id": chat_id,
            "translation_cost": TRANSLATION_LAST_COST,
        }
    elif post.video or instagram_video_path:
        # если есть видео (Telegram или Instagram), добавляем в очередь
        item = {
            "type": "video",
            "file_id": post.video.file_id if post.video else "instagram_source",
            "caption": final_text,
            "instagram_video_path": str(instagram_video_path) if instagram_video_path else None,  # ДОБАВЬ ЭТО
            "buffer_message_id": message_id,
            "buffer_chat_id": chat_id,
            "translation_cost": TRANSLATION_LAST_COST,
            "voiceover": has_voiceover,  # 🎙️ Флаг для Smart Routing
            "voiceover_path": str(voiceover_path) if voiceover_path else None,  # 🎙️ Путь к озвучке
            "instagram_source": instagram_url if instagram_url else None,
        }
    else:
        # если только текст, включаем режим карусели
        log.info("[DEBUG] Режим карусели для текста активен")
        item = {
            "type": "carousel_pending",
            "text": final_text,
            "buffer_message_id": message_id,
            "buffer_chat_id": chat_id,
            "translation_cost": TRANSLATION_LAST_COST,
        }

    # Проверка дублей включена
    h = post_hash(item)
    if h in SEEN_HASHES:
        log.info("Duplicate skipped")
        return
    SEEN_HASHES.add(h)
    save_seen()
    log.info("Queue push type=%s size_before=%s", item["type"], len(POST_QUEUE))
    POST_QUEUE.append(item)
    save_queue()
    log.info("Post queued. Queue size=%s", len(POST_QUEUE))
    
    # 🎙️ ОЗВУЧКА: НЕ удаляем - она понадобится при обработке видео в CONVEYOR
    # Удаление произойдет после успешной обработки в prepare_video_for_ready
    if voiceover_path:
        log.info(f"[ELEVENLABS] Voiceover saved for later use: {voiceover_path.name}")
    
    # ✅ Instagram видео НЕ удаляем - оно понадобится для обработки в CONVEYOR
    # Удаление произойдет после успешной подготовки в prepare_video_for_ready


def main() -> None:
    load_queue()
    load_seen()
    load_stats()
    load_published_texts()
    load_last_post_time()
    log.info(f"INFO | [CONFIG] Current publish interval: {PUBLISH_INTERVAL_SECONDS // 60} minutes")
    log.info("System ready. All social networks optimized.")
    log.info("Golden Template Active. Content Separated.")
    video_count = sum(1 for it in POST_QUEUE if it.get("type") == "video")
    est_hours = (video_count + 59) // 60  # 1 per hour -> videos count hours
    log.info(f"INFO | [QUEUE] Found {video_count} posts for Instagram. Estimated completion time: {est_hours} hours.")

    async def post_init(app: Application) -> None:
        # 🚨 TOTAL QUEUE PURGE: Полностью очищаем очередь при каждом запуске
        global POST_QUEUE
        try:
            original_size = len(POST_QUEUE)
            POST_QUEUE.clear()  # Полная очистка всех старых данных
            save_queue()
            
            if original_size > 0:
                log.info(f"🧹 [TOTAL PURGE] Cleared entire queue ({original_size} old items removed)")
            else:
                log.info("[TOTAL PURGE] Queue was already empty.")
        except Exception as e:
            log.error(f"[TOTAL PURGE] Error during queue cleanup: {e}")
        
        # 🔄 STARTUP SYNC: Загружаем только свежие готовые файлы из ready_to_publish
        try:
            log.info("[STARTUP] Loading fresh ready files from disk...")
            loaded = load_ready_files_to_queue()
            if loaded > 0:
                log.info(f"✅ [SUCCESS] Queue refreshed from disk. Starting instant post with 4 hashtags (incl. #qiziqarli) + AI tag.")
                log.info(f"✅ [STARTUP] Loaded {loaded} ready files into queue. First Strike ready.")
            else:
                log.warning("[STARTUP] No ready files found on disk.")
        except Exception as e:
            log.error(f"[STARTUP] Error loading ready files: {e}")
        
        # Разовая очистка Supabase от сиротских файлов перед стартом
        try:
            await cleanup_supabase_orphans(dry_run=False)
        except Exception as e:
            log.error(f"[Supabase] cleanup_supabase_orphans failed at startup: {e}")
        
        log.info("[CONVEYOR] System initialization...")
        
        # AUTO-PURGE: Удаляем слишком тяжелые файлы из ready_to_publish
        try:
            ready_files = list(READY_TO_PUBLISH_DIR.glob("ready_*.mp4"))
            purged_count = 0
            for ready_file in ready_files:
                file_size_mb = ready_file.stat().st_size / (1024 * 1024)
                if file_size_mb > 95:
                    log.warning(f"[AUTO-PURGE] Deleting oversized file: {ready_file.name} ({file_size_mb:.2f} MB)")
                    ready_file.unlink()
                    # Удаляем метаданные тоже (READY_META_EXT_FIX: try both formats)
                    meta_file_a = ready_file.with_suffix('.json')
                    meta_file_b = ready_file.with_suffix('.mp4.json')
                    meta_file = meta_file_a if meta_file_a.exists() else (meta_file_b if meta_file_b.exists() else None)
                    if meta_file and meta_file.exists():
                        meta_file.unlink()
                    purged_count += 1
            if purged_count > 0:
                log.info(f"[AUTO-PURGE] Removed {purged_count} oversized files. Conveyor will regenerate them.")
            else:
                log.info("[AUTO-PURGE] No oversized files found. All clear.")
        except Exception as e:
            log.error(f"[AUTO-PURGE] Error during cleanup: {e}")
        
        # 🧹 TMP_MEDIA CLEANUP: Удаляем старые временные файлы при старте
        try:
            tmp_media_dir = Path("tmp_media")
            if tmp_media_dir.exists():
                old_files = []
                # Удаляем старые .mp4 и .MP4 файлы (кроме подпапок)
                for pattern in ["*.mp4", "*.MP4"]:
                    for file in tmp_media_dir.glob(pattern):
                        if file.is_file():
                            try:
                                file.unlink()
                                old_files.append(file.name)
                            except Exception as e:
                                log.warning(f"[TMP_CLEANUP] Failed to delete {file.name}: {e}")
                
                if old_files:
                    log.info(f"🧹 [TMP_CLEANUP] Removed {len(old_files)} old temporary files from tmp_media/")
                else:
                    log.info("[TMP_CLEANUP] No old temporary files found in tmp_media/")
        except Exception as e:
            log.error(f"[TMP_CLEANUP] Error during tmp_media cleanup: {e}")
        
        # Запускаем workers
        asyncio.create_task(video_processing_worker())  # FIX B: Video processing worker
        asyncio.create_task(post_worker(app))
        asyncio.create_task(daily_report_scheduler(app))
        asyncio.create_task(history_log_scheduler())
        asyncio.create_task(maintain_ready_posts_worker(app))  # CONVEYOR worker
        
        log.info("[CONVEYOR] All workers started. First Strike and Conveyor system active.")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(60)
        .connect_timeout(60)
        .pool_timeout(60)
        .write_timeout(60)
        .post_init(post_init)
        .build()
    )

    # обработчики удаленного управления (только для админа)
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("postnow", postnow_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    app.add_handler(CommandHandler("interval", interval_command))
    app.add_handler(CommandHandler("status", status_command))

    # обработчик постов из каналов
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    log.info("✅ Bot is running. Waiting for channel posts...")
    log.info("🔧 Remote management active. New Instagram schedule applied.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
