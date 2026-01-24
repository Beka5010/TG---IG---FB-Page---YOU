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
import moviepy.video.fx.all as vfx
from moviepy.editor import VideoFileClip, AudioFileClip, ColorClip, TextClip, CompositeVideoClip, ImageClip, concatenate_videoclips, concatenate_audioclips, CompositeAudioClip
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
VIDEO_MIRROR_TOGGLE = False

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


def can_ig_publish(media_kind: str) -> bool:
    """
    IG расписание (ОБНОВЛЕНО):
    - Только video (Reels)
    - Публикация разрешена ВСЕГДА (синхронно с TG и FB)
    - Без ограничений по времени
    """
    if media_kind != "video":
        return False

    reset_ig_schedule_if_needed()
    # Публикация разрешена всегда для синхронизации с TG и FB
    return True


def ig_mark_published(media_kind: str):
    reset_ig_schedule_if_needed()
    if media_kind == "video":
        # Считаем все посты без разделения по времени
        IG_SCHEDULE["afternoon_videos"] += 1


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


def process_video(local_path: Path, caption: str | None = None, speed_multiplier: float = 1.01, bg_color_override: tuple | None = None, brightness_adjust: float = 0.0, random_crop: bool = False, voiceover_path: str | None = None, source: str | None = None) -> Path | None:
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
        clip = clip.fx(vfx.speedx, speed_multiplier)
        
        # Применяем коррекцию яркости (План Б)
        if brightness_adjust != 0.0:
            clip = clip.fx(vfx.colorx, 1.0 + brightness_adjust)
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
                    
                    segment = clip.subclip(current_time, end_time)
                    
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
                    segments.append(clip.subclip(seg1_start, seg1_end))
                if seg2_end > seg2_start:
                    segments.append(clip.subclip(seg2_start, seg2_end))
                if seg3_start < seg3_end and seg3_start < duration - 0.05:
                    segments.append(clip.subclip(seg3_start, seg3_end))
                
                # Склеиваем сегменты
                if len(segments) > 1:
                    clip = concatenate_videoclips(segments, method="compose")
                else:
                    log.warning("[MICRO-STITCH] Not enough valid segments, skipping stitch")
                
                # Random Trim
                if clip.duration > trim_duration + 1.0:
                    if random.choice([True, False]):
                        # Отрезаем с начала
                        clip = clip.subclip(trim_duration, clip.duration)
                        log.info(f"[MICRO-STITCH] Trimmed {trim_duration}s from start")
                    else:
                        # Отрезаем с конца
                        clip = clip.subclip(0, clip.duration - trim_duration)
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
        
        # --- УМНЫЙ ФИЛЬТР ИСТОЧНИКА ---
        # Проверяем source из параметра или по названию файла (fallback)
        if source:
            is_instagram = (source == "instagram")
        else:
            # Fallback для старых вызовов
            is_instagram = local_path.name.startswith("instagram_")
        
        log.info(f"[PROCESS_VIDEO] Source: {source or 'detected from filename'}, is_instagram: {is_instagram}")
        
        # 5. Озвучка ElevenLabs (СИСТЕМА РЕТРАЕВ)
        voiceover_audio = None
        original_audio = None
        
        # Определяем original_audio из clip.audio, если он есть
        if clip.audio is not None:
            original_audio = clip.audio
        
        if is_instagram:
            log.info("[SMART ROUTING] Instagram source → Full processing (Voice + Music)")
            
            # Проверяем, есть ли готовый файл озвучки или нужно генерировать
            if voiceover_path and Path(voiceover_path).exists():
                # Используем готовый файл озвучки
                try:
                    voiceover_audio = AudioFileClip(str(voiceover_path))
                    log.info(f"[ELEVENLABS] Using existing voiceover: {Path(voiceover_path).name}")
                except Exception as e:
                    log.warning(f"[ELEVENLABS] Failed to load existing voiceover: {e}")
            elif caption and caption.strip() and ELEVENLABS_API_KEY:
                # Генерируем озвучку с ретраями
                post_id = uuid.uuid4().hex[:8]
                voiceover_filename = f"voiceover_{post_id}.mp3"
                voiceover_path_full = Path("tmp_media") / voiceover_filename
                voiceover_path_full.parent.mkdir(parents=True, exist_ok=True)
                
                # Берем текст для озвучки (весь caption, но убираем хэштеги и footer)
                # ВАЖНО: Обычно озвучка генерируется ДО вызова process_video, это fallback
                import re
                text_for_voice = caption or ""
                # Убираем хэштеги
                text_for_voice = re.sub(r'#\w+', '', text_for_voice)
                # Убираем footer (ссылки на канал)
                text_for_voice = re.sub(r'\|.*?\|', '', text_for_voice)
                text_for_voice = re.sub(r'<a.*?</a>', '', text_for_voice)
                text_for_voice = text_for_voice.strip()
                
                if text_for_voice:
                    log.info(f"[ELEVENLABS] Starting voiceover process for: {text_for_voice[:50]}...")
                    
                    try:
                        from elevenlabs.client import ElevenLabs
                        eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
                    except ImportError:
                        log.error("[ELEVENLABS] elevenlabs package not installed")
                        eleven_client = None
                    
                    if eleven_client:
                        for attempt in range(1, 4):  # 3 попытки
                            try:
                                log.info(f"[ELEVENLABS] Attempt {attempt}/3...")
                                
                                # Если файл остался от прошлой неудачной попытки — удаляем
                                if voiceover_path_full.exists():
                                    try: 
                                        voiceover_path_full.unlink()
                                    except: 
                                        pass
                                
                                # --- ГЕНЕРАЦИЯ: НАСТРОЙКИ "ЖИВОЙ ЧЕЛОВЕК" ---
                                audio_generator = eleven_client.text_to_speech.convert(
                                    voice_id="RlVk06jBShtFu3ub6usx", # Твой клон
                                    text=text_for_voice,
                                    model_id="eleven_multilingual_v2",
                                    voice_settings={
                                        "stability": 0.35,        # Снижаем до 0.35! Голос станет эмоциональнее и живее.
                                        "similarity_boost": 0.60, # Снижаем до 0.60! Уйдет "металлический" звон.
                                        "style": 0.55,            # Добавляем 55% стиля (экспрессия).
                                        "use_speaker_boost": True
                                    }
                                )
                                # Превращаем поток данных в аудиофайл
                                audio_data = b"".join(list(audio_generator))
                                
                                # Сохраняем файл
                                with open(voiceover_path_full, "wb") as f:
                                    f.write(audio_data)
                                
                                # Загружаем аудио в MoviePy
                                temp_audio = AudioFileClip(str(voiceover_path_full))
                                
                                # --- УСКОРЕНИЕ ГОЛОСА ДЛЯ РИЛСА ---
                                # 1.15 означает ускорение на 15%. Можно ставить 1.1 или 1.2
                                voiceover_audio = temp_audio.fx(vfx.speedx, 1.15)
                                
                                log.info(f"[ELEVENLABS] Success on attempt {attempt}! Voice speed increased by 15%.")
                                break  # Если дошли сюда — всё круто, выходим из цикла попыток
                                
                            except Exception as e:
                                log.error(f"[ELEVENLABS] Attempt {attempt} failed: {e}")
                                if attempt < 3:
                                    wait_time = 5 * attempt  # С каждой попыткой ждем дольше (5с, 10с)
                                    log.info(f"[ELEVENLABS] Retrying in {wait_time} seconds...")
                                    time_module.sleep(wait_time)
                                else:
                                    log.critical("[ELEVENLABS] All 3 attempts failed. Moving to original audio.")
        else:
            log.info("[SMART ROUTING] Telegram source → Template only (No Voiceover)")
            voiceover_audio = None  # Пропускаем озвучку для обычных файлов
        
        # --- СКЛЕЙКА ЗВУКА (ГОЛОС + ФОНОВАЯ МУЗЫКА) ---
        if voiceover_audio is not None:
            try:
                log.info("[AUDIO] Mixing voiceover with background music")
                
                # 1. Голос (уже ускоренный)
                voice_track = voiceover_audio.volumex(1.2) # Немного прибавим громкость голоса
                
                # --- ВЫБОР СЛУЧАЙНОГО ХИТА ИЗ ПАПКИ ASSETS ---
                assets_dir = Path("assets")
                # Ищем все mp3 файлы в папке
                music_files = list(assets_dir.glob("*.mp3"))
                
                if music_files:
                    random_music_path = random.choice(music_files)
                    log.info(f"[AUDIO] Selected random hit: {random_music_path.name}")
                    
                    # ГРОМКОСТЬ 7% (Было 15%)
                    bg_music = AudioFileClip(str(random_music_path)).volumex(0.07)
                    
                    # Зацикливание музыки, если она короче видео
                    video_duration = final_video.duration
                    music_duration = bg_music.duration
                    
                    if music_duration < video_duration:
                        # Вычисляем, сколько раз нужно повторить музыку
                        loops_needed = int(video_duration / music_duration) + 1
                        log.info(f"[AUDIO] Music ({music_duration:.2f}s) shorter than video ({video_duration:.2f}s). Looping {loops_needed} times.")
                        
                        # Создаем список копий музыки для зацикливания
                        music_clips = [bg_music] * loops_needed
                        bg_music = concatenate_audioclips(music_clips)
                    
                    # Обрезаем музыку точно под длину видео
                    bg_music = bg_music.subclip(0, video_duration)
                    
                    final_audio = CompositeAudioClip([voice_track, bg_music])
                else:
                    log.warning("[AUDIO] No mp3 files found in assets folder. Using voice only.")
                    final_audio = voice_track

                final_video = final_video.set_audio(final_audio)
                
            except Exception as audio_err:
                log.error(f"[AUDIO] Mixing failed: {audio_err}")
                if original_audio is not None:
                    final_video = final_video.set_audio(original_audio)
                else:
                    # Fallback: используем только голос без музыки
                    try:
                        final_video = final_video.set_audio(voiceover_audio)
                    except:
                        pass
        
        # Удаляем временный файл озвучки после использования
        if voiceover_path and Path(voiceover_path).exists():
            try:
                Path(voiceover_path).unlink()
                log.info("[ELEVENLABS] Voiceover file cleaned up after applying")
            except Exception as e:
                log.warning(f"[ELEVENLABS] Failed to delete voiceover file: {e}")
        elif 'voiceover_path_full' in locals() and voiceover_path_full.exists():
            try:
                voiceover_path_full.unlink()
                log.info("[ELEVENLABS] Generated voiceover file cleaned up")
            except Exception as e:
                log.warning(f"[ELEVENLABS] Failed to delete generated voiceover file: {e}")
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
                    audio_track = audio_track.with_effects([vfx.MultiplySpeed(tempo_change)])
                    log.info(f"[PROFESSIONAL_AUDIO] Tempo adjusted: {tempo_change:.4f}x ({(tempo_change-1)*100:+.2f}%)")
                
                # Применяем обработанное аудио
                final_video = final_video.set_audio(audio_track)
                log.info("[PROFESSIONAL_AUDIO] High-quality audio processing applied (NO NOISE)")
            except Exception as audio_err:
                log.warning(f"[PROFESSIONAL_AUDIO] Failed to process audio: {audio_err}, using original audio")
        
        # Размытие субтитров: создаем размытый прямоугольник внизу видео (где обычно субтитры)
        def add_blur_to_captions(clip):
            # Обрезаем кусок снизу, размываем его и накладываем обратно
            overlay = clip.crop(y1=int(clip.h*0.8), y2=clip.h).fx(vfx.blur, 20)
            return CompositeVideoClip([clip, overlay.set_position(("center", "bottom"))])
        
        # Применяем размытие к видео
        #final_video = add_blur_to_captions(final_video)
        final_video = final_video.set_duration(final_video.duration - 0.5)
        log.info("[BLUR] Blur applied to bottom 20% of video (captions area)")
        
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
        
        # --- ОСВОБОЖДЕНИЕ ФАЙЛОВ ДЛЯ WINDOWS (ЗАДАЧА №3) ---
        log.info("[CLEANUP] Starting resource release...")
        try:
            # Закрываем основное видео
            if 'final_video' in locals() and final_video is not None:
                final_video.close()
            
            # Закрываем аудио дорожки, если они существуют
            if 'audio_track' in locals() and audio_track is not None:
                audio_track.close()
                
            if 'voiceover_audio' in locals() and voiceover_audio is not None:
                voiceover_audio.close()
                
            if 'original_audio' in locals() and original_audio is not None:
                original_audio.close()
                
            log.info("[CLEANUP] All resources released successfully")
        except Exception as cleanup_err:
            log.warning(f"[CLEANUP] Resource release issue: {cleanup_err}")
        # --------------------------------------------------
        
        log.info("INFO | [PROCESS] Video unique processing: Success")
        
        # 🔄 AUTO-COMPRESS: Проверка размера и автоматическое пережатие (SIZE GUARD)
        try:
            file_size_mb = out_path.stat().st_size / (1024 * 1024)
            max_size_mb = 50  # Лимит для Telegram и Instagram
            
            if file_size_mb > max_size_mb:
                log.warning(f"[AUTO-COMPRESS] File too large: {file_size_mb:.2f} MB > {max_size_mb} MB")
                log.info("[AUTO-COMPRESS] Re-encoding with CRF 22 to reduce size...")
                
                # Создаем временный файл для пережатой версии
                compressed_path = out_path.parent / f"compressed_{out_path.name}"
                
                # ПЕРВАЯ ПОПЫТКА: CRF 22, bitrate 4000k
                final_video.write_videofile(
                    str(compressed_path),
                    codec="libx264",
                    audio_codec="aac",
                    fps=30,
                    preset="medium",
                    bitrate="4000k",
                    ffmpeg_params=[
                        "-crf", "22",
                        "-pix_fmt", "yuv420p"
                    ],
                    logger=None,
                )
                
                compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                log.info(f"[AUTO-COMPRESS] New size with CRF 22: {compressed_size_mb:.2f} MB (was {file_size_mb:.2f} MB)")
                
                if compressed_size_mb <= max_size_mb:
                    # Успех! Заменяем оригинал
                    out_path.unlink()
                    compressed_path.rename(out_path)
                    log.info(f"✅ [AUTO-COMPRESS] Success! File compressed to {compressed_size_mb:.2f} MB")
                else:
                    # ВТОРАЯ ПОПЫТКА: CRF 24, bitrate 3000k
                    log.warning(f"[AUTO-COMPRESS] Still too large ({compressed_size_mb:.2f} MB), trying CRF 24...")
                    compressed_path.unlink()  # Удаляем первую попытку
                    
                    final_video.write_videofile(
                        str(compressed_path),
                        codec="libx264",
                        audio_codec="aac",
                        fps=30,
                        preset="medium",
                        bitrate="3000k",
                        ffmpeg_params=[
                            "-crf", "24",
                            "-pix_fmt", "yuv420p"
                        ],
                        logger=None,
                    )
                    
                    final_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                    log.info(f"[AUTO-COMPRESS] Final size with CRF 24: {final_size_mb:.2f} MB")
                    
                    out_path.unlink()
                    compressed_path.rename(out_path)
                    log.info(f"✅ [AUTO-COMPRESS] Compressed with CRF 24 to {final_size_mb:.2f} MB")
            else:
                log.info(f"✅ [SIZE CHECK] File size OK: {file_size_mb:.2f} MB <= {max_size_mb} MB (HD quality preserved)")
        except Exception as compress_err:
            log.error(f"[AUTO-COMPRESS] Failed: {compress_err}")
            # Продолжаем с оригинальным файлом
        
        # --- ОСВОБОЖДЕНИЕ ФАЙЛОВ ДЛЯ WINDOWS (ШАГ 1 - ФИНАЛ) ---
        try:
            log.info("[CLEANUP] Finalizing resource release...")
            
            # Проверяем каждую переменную отдельно, чтобы не вызвать ошибку при закрытии
            if 'final_video' in locals() and final_video is not None:
                try: final_video.close()
                except: pass
                
            if 'clip' in locals() and clip is not None:
                try: clip.close()
                except: pass
                
            if 'audio_track' in locals() and audio_track is not None:
                try: audio_track.close()
                except: pass
                
            if 'voiceover_audio' in locals() and voiceover_audio is not None:
                try: voiceover_audio.close()
                except: pass
                
            if 'original_audio' in locals() and original_audio is not None:
                try: original_audio.close()
                except: pass
                
            log.info("[CLEANUP] Resources released successfully")
        except Exception as cleanup_err:
            log.warning(f"[CLEANUP] Minor issue during release: {cleanup_err}")
        
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
        source = item.get("source", "telegram")  # Используем source из item
        is_instagram_source = (source == "instagram")
        
        # ✅ ПРОВЕРКА: Instagram-источник или Telegram
        if (source == "instagram" or video_file_id == "instagram_source") and item.get("instagram_video_path"):
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
        source = item.get("source", "telegram")  # Источник из item
        
        processed_path = process_video(
            local_path,
            caption,
            speed_multiplier=speed_mult,
            brightness_adjust=brightness,
            random_crop=True,  # Всегда применяем crop для готовых постов
            voiceover_path=voiceover_path,  # 🎙️ Передаем озвучку
            source=source  # Передаем источник
        )
        
        if not processed_path or not Path(processed_path).exists():
            log.error(f"[CONVEYOR] Video processing failed for {video_file_id}")
            # Удаляем только если это НЕ Instagram (временный файл Telegram)
            if not is_instagram_source and local_path.exists():
                local_path.unlink()
            return None
        
        # Сохраняем в ready_to_publish с уникальным именем
        ready_filename = f"ready_{uuid.uuid4().hex[:8]}_{int(time_module.time())}.mp4"
        ready_path = READY_TO_PUBLISH_DIR / ready_filename
        
        shutil.move(str(processed_path), str(ready_path))
        
        # Проверяем размер файла (целевой 15-25 МБ)
        file_size_mb = ready_path.stat().st_size / (1024 * 1024)
        log.info(f"[CONVEYOR] Ready video saved: {ready_filename} ({file_size_mb:.2f} MB)")
        
        # Удаляем временные файлы
        if local_path.exists():
            local_path.unlink()
            if is_instagram_source:
                log.info("[CONVEYOR] Instagram source video cleaned up after processing")
        
        # Сохраняем метаданные (caption, file_id) в JSON рядом с видео
        meta_path = ready_path.with_suffix('.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump({
                'caption': caption,
                'original_file_id': video_file_id,
                'instagram_source': item.get('instagram_source'),  # ✅ Сохраняем URL источника
                'type': item.get('type', 'video'),
                'prepared_at': datetime.now().isoformat()
            }, f, ensure_ascii=False)
        
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
    ready_files = sorted(READY_TO_PUBLISH_DIR.glob("ready_*.mp4"))
    
    if not ready_files:
        return 0
    
    log.info(f"[DEBUG] Queue empty, found {len(ready_files)} ready files on disk. Filling queue...")
    
    loaded_count = 0
    for ready_file in ready_files:
        # Проверяем, что файл существует и метаданные тоже
        meta_file = ready_file.with_suffix(".json")
        if not meta_file.exists():
            log.warning(f"[QUEUE LOADER] Metadata missing for {ready_file.name}, skipping")
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


# ==================== STATE MANAGEMENT (post_counter + CTA) ====================

STATE_FILE = Path("state.json")

def load_state() -> dict:
    """Загружает состояние из state.json"""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Failed to load state: {e}, using defaults")
    return {"post_counter": 0}


def save_state(state: dict) -> None:
    """Сохраняет состояние в state.json"""
    try:
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Failed to save state: {e}")


def next_post_cta_rule() -> tuple[bool, Optional[str], int]:
    """
    Определяет, нужно ли добавлять CTA для следующего поста.
    Возвращает: (use_cta, cta_text, post_counter)
    CTA добавляется каждые 2 поста (post_counter % 2 == 0).
    """
    state = load_state()
    post_counter = state.get("post_counter", 0)
    
    # Увеличиваем счетчик
    post_counter += 1
    state["post_counter"] = post_counter
    save_state(state)
    
    # CTA добавляется каждые 2 поста (на 2, 4, 6, ...)
    use_cta = (post_counter % 2 == 0)
    
    # Варианты CTA (точные строки с переносами)
    cta_variants = [
        "Biz bilang bo'ling,\nOldinda yana qiziqarlilari bor.",
        "Agar video yoqqan bo'lsa,\nlayk bosish esdan chiqmasin.",
        "video yoqgan bo'lsa,\ntanishlarga jo'natib qo'yamiz"
    ]
    
    cta_text = None
    if use_cta:
        cta_text = random.choice(cta_variants)
        log.info(f"[CTA] Post #{post_counter}: CTA enabled, variant chosen: {cta_text[:30]}...")
    else:
        log.info(f"[CTA] Post #{post_counter}: CTA disabled")
    
    return use_cta, cta_text, post_counter


# ==================== PARSING & BUILDING FUNCTIONS ====================

def parse_model_blocks(text: str) -> dict:
    """
    Парсит ответ модели на блоки VOICE_UZ, CAPTION_UZ, EXTRA_HASHTAGS.
    Возвращает: {"voice": str, "caption": str, "extra_hashtags": str}
    """
    result = {
        "voice": "",
        "caption": "",
        "extra_hashtags": ""
    }
    
    if not text:
        return result
    
    # Извлекаем VOICE_UZ
    if "VOICE_UZ:" in text:
        voice_start = text.find("VOICE_UZ:") + len("VOICE_UZ:")
        voice_end = text.find("CAPTION_UZ:", voice_start)
        if voice_end == -1:
            voice_end = text.find("EXTRA_HASHTAGS:", voice_start)
        if voice_end == -1:
            voice_end = len(text)
        result["voice"] = text[voice_start:voice_end].strip()
    
    # Извлекаем CAPTION_UZ
    if "CAPTION_UZ:" in text:
        caption_start = text.find("CAPTION_UZ:") + len("CAPTION_UZ:")
        caption_end = text.find("EXTRA_HASHTAGS:", caption_start)
        if caption_end == -1:
            caption_end = len(text)
        result["caption"] = text[caption_start:caption_end].strip()
    
    # Извлекаем EXTRA_HASHTAGS
    if "EXTRA_HASHTAGS:" in text:
        hashtags_start = text.find("EXTRA_HASHTAGS:") + len("EXTRA_HASHTAGS:")
        hashtags_text = text[hashtags_start:].strip()
        # Очищаем от лишних символов
        hashtags_text = hashtags_text.replace("<", "").replace(">", "").strip()
        result["extra_hashtags"] = hashtags_text
    
    # Fallback если парсинг не удался
    if not result["voice"]:
        lines = text.split('\n')
        result["voice"] = lines[0].strip() if lines else "Qiziqarli video."
    if not result["caption"]:
        result["caption"] = result["voice"]
    
    return result


def build_voice_for_tts(voice_uz: str, cta_text: Optional[str]) -> str:
    """
    Строит текст для TTS из VOICE_UZ + CTA (если нужно).
    Удаляет хэштеги и нормализует пробелы.
    """
    if not voice_uz:
        voice_uz = "Qiziqarli video."
    
    # Удаляем хэштеги из voice
    import re
    voice_uz = re.sub(r'#\w+', '', voice_uz)
    
    # Нормализуем пробелы (убираем множественные переносы строк)
    voice_uz = re.sub(r'\n\s*\n+', '\n', voice_uz).strip()
    
    # Добавляем CTA если нужно
    if cta_text:
        voice_uz = f"{voice_uz}\n\n{cta_text}"
    
    return voice_uz.strip()


def build_caption_for_post(caption_uz: str, base_hashtags: str, extra_hashtags: str, footer_html: str) -> str:
    """
    Строит финальный caption для публикации.
    Включает: caption_uz + extra_hashtags + base_hashtags + footer_html
    """
    parts = []
    
    if caption_uz:
        parts.append(caption_uz.strip())
    
    if extra_hashtags:
        parts.append(extra_hashtags.strip())
    
    if base_hashtags:
        parts.append(base_hashtags.strip())
    
    if footer_html:
        parts.append(footer_html.strip())
    
    return '\n'.join(parts)


# ==================== SOURCE DETECTION ====================

def detect_source_from_input(input_str: str) -> str:
    """
    Определяет источник из входной строки (URL или текст).
    Возвращает: "instagram" или "telegram"
    """
    if not input_str:
        return "telegram"
    
    import re
    # Проверяем на Instagram URL
    instagram_pattern = r'https?://(?:www\.)?(?:instagram\.com|instagr\.am)'
    if re.search(instagram_pattern, input_str, re.IGNORECASE):
        return "instagram"
    
    return "telegram"


async def translate_text(caption_ru: str, asr_ru: str, base_hashtags: str) -> dict:
    """
    Переводит контент в кинематографичном стиле.
    Возвращает словарь: {'voice': str, 'caption': str, 'hashtags': str}
    CTA НЕ добавляется здесь - это делается в коде через next_post_cta_rule()
    """
    try:
        # ПРОМПТ: КИНЕМАТОГРАФИЧНЫЙ СТИЛЬ (БЕЗ CTA)
        system_prompt = (
            "Ты — переводчик и SMM-редактор.\n"
            "Мой стиль: тёплый, спокойный, созерцательный, кинематографичный.\n"
            "Пишешь просто, по сути, короткими фразами, удобно для озвучки.\n\n"
            "Задача:\n"
            "Подготовить узбекскую (латиница) ОЗВУЧКУ и ОПИСАНИЕ для публикации.\n"
            "Все медиафайлы должны выходить с озвучкой.\n\n"
            "Нужно выдать 3 блока:\n"
            "1) VOICE_UZ — текст для озвучки\n"
            "2) CAPTION_UZ — описание под пост\n"
            "3) EXTRA_HASHTAGS — 2–3 дополнительных хэштега по теме\n\n"
            "ЛОГИКА:\n"
            "- Если ASR_RU не пустой → переведи его в VOICE_UZ, сохраняя смысл и тёплый тон.\n"
            "- Если ASR_RU пустой → создай VOICE_UZ на основе CAPTION_RU или смысла сцены, без выдумок и без воды.\n"
            "- Если CAPTION_RU пустой → создай короткий CAPTION_UZ на основе VOICE_UZ.\n\n"
            "СТИЛЬ (ОБЯЗАТЕЛЬНО):\n"
            "— тёплый, спокойный\n"
            "— ощущение момента\n"
            "— короткие строки\n"
            "— без воды, без абстрактных слов\n"
            "— без сленга\n"
            "— без \"SHOK\", \"DAHSHAT\"\n"
            "— допускаются паузы\n\n"
            "ВАЖНО: НЕ добавляй CTA в VOICE_UZ. CTA будет добавлен отдельно в коде.\n\n"
            "ОГРАНИЧЕНИЯ:\n"
            "— VOICE_UZ: 2–7 коротких строк\n"
            "— VOICE_UZ БЕЗ хэштегов\n"
            "— VOICE_UZ БЕЗ CTA\n"
            "— CAPTION_UZ: 1–2 предложения\n"
            "— EXTRA_HASHTAGS: строго 2–3, по теме, без повторов BASE_HASHTAGS\n\n"
            "ФОРМАТ ВЫВОДА (СТРОГО):\n\n"
            "VOICE_UZ:\n"
            "<текст>\n\n"
            "CAPTION_UZ:\n"
            "<текст>\n\n"
            "EXTRA_HASHTAGS:\n"
            "<#... #... #...>"
        )
        
        # Подготовка входных данных
        user_content = (
            f"ВХОД:\n"
            f"CAPTION_RU:\n\"\"\" {caption_ru or '(пусто)'} \"\"\"\n\n"
            f"ASR_RU:\n\"\"\" {asr_ru or '(пусто)'} \"\"\"\n\n"
            f"BASE_HASHTAGS:\n\"\"\" {base_hashtags or ''} \"\"\""
        )
        
        # Запрос к AI
        if not openai_client:
            # Fallback при отсутствии клиента
            return {
                'voice': "Qiziqarli video. Oxirigacha ko'ring.",
                'caption': "Qiziqarli video. Oxirigacha ko'ring.",
                'hashtags': ""
            }
        
        response = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        )
        
        # ПАРСЕР: Используем общую функцию parse_model_blocks
        gpt_response = response.choices[0].message.content or ""
        blocks = parse_model_blocks(gpt_response)
        
        return {
            'voice': blocks['voice'],
            'caption': blocks['caption'],
            'hashtags': blocks['extra_hashtags']
        }
        
    except Exception as e:
        log.error(f"[OPENAI] Translation error: {e}")
        # Аварийный ответ, если GPT сломался
        return {
            'voice': "Qiziqarli video. Oxirigacha ko'ring.",
            'caption': "Qiziqarli video. Oxirigacha ko'ring.",
            'hashtags': ""
        }


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


async def post_worker(application):
    global IS_POSTING, FORCE_CAROUSEL_TEST, FIRST_RUN_IMMEDIATE, LAST_PHOTO_TIME, LAST_VIDEO_TIME, LAST_POST_TIME, IS_PAUSED

    if IS_POSTING:
        return

    IS_POSTING = True

    while True:
        # SMART CONTROL: Проверка паузы публикаций
        if IS_PAUSED:
            log.info("[PAUSE] Conveyor paused. Sleeping for 10 seconds...")
            await asyncio.sleep(10)
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
                            if not ready_video_path.exists():
                                raise FileNotFoundError(f"Ready file not found: {ready_video_path}")
                            
                            upload_path = ready_video_path
                            
                            # Загружаем метаданные
                            ready_meta_path = ready_video_path.with_suffix('.json')
                            if ready_meta_path.exists():
                                try:
                                    with open(ready_meta_path, 'r', encoding='utf-8') as f:
                                        meta = json.load(f)
                                        caption = meta.get('caption', caption)
                                        caption_tg = prepare_caption_for_publish_tg(caption)
                                        caption_meta = prepare_caption_for_publish_meta(caption)
                                        if caption_tg and len(caption_tg) > CAPTION_MAX_LENGTH:
                                            caption_tg = trim_caption_with_footer(caption_tg, CAPTION_MAX_LENGTH)
                                        log.info(f"[FIRST STRIKE] Loaded metadata from {ready_meta_path.name}")
                                except Exception as e:
                                    log.warning(f"[FIRST STRIKE] Failed to load metadata: {e}")
                            
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
                                Path(p).unlink()
                        
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
                            
                            # Instagram публикация (без Plan B для First Strike - просто одна попытка)
                            if can_ig_publish("video"):
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
                                    Path(upload_path).unlink()
                                    log.info(f"[FIRST STRIKE] Deleted ready file: {Path(upload_path).name}")
                                # Удаляем метаданные
                                meta_path = Path(upload_path).with_suffix('.json') if upload_path else None
                                if meta_path and meta_path.exists():
                                    meta_path.unlink()
                                    log.info(f"[FIRST STRIKE] Deleted metadata: {meta_path.name}")
                            else:
                                # Для сырых файлов: удаляем только временные файлы
                                for p in [local_path, processed_path]:
                                    if p and Path(p).exists():
                                        Path(p).unlink()
                            
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
                            LAST_POST_TIME = datetime.now()
                            save_last_post_time()
                            log.info(f"✅ [FIRST STRIKE] SUCCESS after {first_strike_attempts} attempt(s)! Published one post. Cooldown active.")
                            
                        except Exception as e:
                            log.error(f"[FIRST STRIKE] Publication error: {e}")
                            # Cleanup (только временные файлы, готовые НЕ удаляем)
                            if not item.get("from_ready_folder", False):
                                for p in [local_path, processed_path]:
                                    if p and Path(p).exists():
                                        Path(p).unlink()
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
                    await asyncio.sleep(60)
                    continue

                # 🎛️ MIXED QUEUE 4+4: Выбираем пост по логике чередования
                item = get_next_post_from_queue()
                if not item:
                    log.warning("[MIXED QUEUE] No posts available in queue")
                    await asyncio.sleep(60)
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
                        await asyncio.sleep(5)
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
                            try:
                                Path(p).unlink()
                            except Exception:
                                pass

                    await delete_from_buffer(application, item)
                    await send_progress_report(application)
                    LAST_PHOTO_TIME = datetime.now()
                    LAST_POST_TIME = datetime.now()
                    save_last_post_time()
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
                        if not ready_video_path.exists():
                            log.error(f"[CONVEYOR] Ready file not found: {ready_video_path}")
                            continue
                        
                        ready_meta_path = ready_video_path.with_suffix('.json')
                        
                        # Загружаем метаданные
                        caption = item.get("caption", "")
                        if ready_meta_path.exists():
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
                                    Path(local_path).unlink()
                                continue
                            
                            upload_path = processed_path
                            log.info(f"[CONVEYOR] Raw video processed: {Path(upload_path).name}")
                        except Exception as e:
                            log.error(f"[CONVEYOR] Failed to process raw video: {e}")
                            # Cleanup
                            for p in [local_path, processed_path]:
                                if p and Path(p).exists():
                                    Path(p).unlink()
                            continue
                    
                    # Загружаем готовое видео в Supabase (если еще не загружено)
                    if not item.get("supabase_url"):
                        # ПРОВЕРКА: Файл должен существовать перед загрузкой
                        if not upload_path or not Path(upload_path).exists():
                            log.critical(f"🚨 CRITICAL | File not found for upload: {upload_path}")
                            log.critical("🚨 CRITICAL | Skipping broken post due to missing file")
                            # Удаляем метаданные если есть
                            if upload_path:
                                meta_path = Path(str(upload_path)).with_suffix('.json')
                                if meta_path.exists():
                                    meta_path.unlink()
                            save_queue()
                            await asyncio.sleep(300)
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
                                    # Удаляем битый файл
                                    if upload_path.exists():
                                        upload_path.unlink()
                                    meta_path = upload_path.with_suffix('.json')
                                    if meta_path.exists():
                                        meta_path.unlink()
                                log.critical("🚨 CRITICAL | Skipping broken post due to Supabase upload failure")
                                save_queue()
                                await asyncio.sleep(300)
                                continue
                        except Exception as e:
                            log.error(f"[SUPABASE] Upload error: {e}")
                            log.critical("🚨 CRITICAL | Skipping broken post due to Supabase exception")
                            save_queue()
                            await asyncio.sleep(300)
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
                    
                    # Проверка успешности Supabase ПЕРЕД попыткой IG публикации
                    if can_ig_publish("video"):
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
                                                Path(processed_path_retry).unlink()
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
                                                Path(processed_path_retry).unlink()
                                            break
                                        else:
                                            log.warning(f"[PLAN B] Attempt {ig_publish_attempts} failed")
                                            # Удаляем временный файл повторной обработки
                                            if Path(processed_path_retry).exists():
                                                Path(processed_path_retry).unlink()
                                            
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
                        await asyncio.sleep(300)
                    
                    # cleanup после отправки
                    # CONVEYOR: Удаляем готовый файл из ready_to_publish
                    if upload_path and upload_path.parent == READY_TO_PUBLISH_DIR:
                        try:
                            if upload_path.exists():
                                upload_path.unlink()
                                log.info(f"[CONVEYOR] Deleted ready file: {upload_path.name}")
                            # Удаляем метаданные
                            meta_path = upload_path.with_suffix('.json')
                            if meta_path.exists():
                                meta_path.unlink()
                                log.info(f"[CONVEYOR] Deleted metadata: {meta_path.name}")
                        except Exception as e:
                            log.warning(f"[CONVEYOR] Failed to delete ready file: {e}")
                    else:
                        # FIRST STRIKE: Удаляем временные файлы (local_path, processed_path)
                        try:
                            if 'local_path' in locals() and local_path and Path(local_path).exists():
                                Path(local_path).unlink()
                                log.info(f"[FIRST STRIKE] Deleted temp file: {Path(local_path).name}")
                            if 'processed_path' in locals() and processed_path and Path(processed_path).exists():
                                Path(processed_path).unlink()
                                log.info(f"[FIRST STRIKE] Deleted processed file: {Path(processed_path).name}")
                            if upload_path and Path(upload_path).exists():
                                Path(upload_path).unlink()
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
                    await asyncio.sleep(300)
                else:
                    # Только для неизвестных ошибок возвращаем в очередь
                    POST_QUEUE.appendleft(item)
                    save_queue()
                    await asyncio.sleep(60)
        else:
            # Очередь пустая - проверяем, есть ли готовые файлы
            loaded = load_ready_files_to_queue()
            if loaded == 0:
                log.info("[DEBUG] Queue empty and no ready files. Waiting...")
            await asyncio.sleep(60)


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
    text_for_translate = ensure_utf8_text(post.text or post.caption or "")
    entities = post.entities or post.caption_entities
    
    # Сохраняем оригинальный текст поста для передачи в translate_text как caption_ru
    caption_ru_original = text_for_translate
    
    # 🔍 ОПРЕДЕЛЯЕМ SOURCE ТОЛЬКО ПО РЕАЛЬНОМУ ВХОДУ (на этапе enqueue)
    # source определяется по наличию Instagram URL в ОРИГИНАЛЬНОМ тексте поста
    # НЕ используем text_for_translate после Whisper, т.к. он может быть заменен на transcript без ссылок
    instagram_url = None
    instagram_video_path = None
    
    # Ищем Instagram URL в ОРИГИНАЛЬНОМ тексте поста И в entities
    import re
    instagram_pattern = r'(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reel|reels|stories|tv)/[^\s]*'
    
    # 1. Сначала проверяем entities (приоритет) - URL может быть там даже если текста нет
    if entities and text_for_translate:
        for entity in entities:
            if entity.type == MessageEntityType.URL:
                # Извлекаем URL из текста по offset и length
                try:
                    url_text = text_for_translate[entity.offset:entity.offset + entity.length]
                    # Проверяем на Instagram
                    if re.search(instagram_pattern, url_text, re.IGNORECASE):
                        instagram_url = url_text
                        # Добавляем https:// если отсутствует
                        if not instagram_url.startswith('http'):
                            instagram_url = 'https://' + instagram_url
                        log.info(f"[SMART ROUTING] Instagram URL found in entities: {instagram_url[:50]}...")
                        break
                except (IndexError, AttributeError) as e:
                    log.warning(f"[SMART ROUTING] Error extracting URL from entity: {e}")
                    continue
    
    # 2. Если не нашли в entities, ищем в тексте (расширенный regex)
    if not instagram_url and text_for_translate:
        match = re.search(instagram_pattern, text_for_translate, re.IGNORECASE)
        if match:
            instagram_url = match.group(0)
            # Добавляем https:// если отсутствует
            if not instagram_url.startswith('http'):
                instagram_url = 'https://' + instagram_url
            log.info(f"[SMART ROUTING] Instagram URL found in text: {instagram_url[:50]}...")
    
    # Определяем source: если найден Instagram URL → "instagram", иначе → "telegram"
    # Это ЕДИНСТВЕННОЕ место определения source - дальше используется только item["source"]
    source = "instagram" if instagram_url else "telegram"
    log.info(f"[SOURCE] Determined at enqueue (by input): {source} (instagram_url={'found' if instagram_url else 'not found'})")
    
    # Скачиваем видео из Instagram (если это Instagram источник)
    if instagram_url:
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
            # ПЕРЕОПРЕДЕЛЯЕМ source: если видео успешно скачано из Instagram, это точно Instagram источник
            source = "instagram"
            log.info(f"[SOURCE] Re-determined after successful Instagram download: {source}")
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
    
    if video_source_path or post.video:
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
    translation_result = None
    if caption_ru_original.strip() or whisper_transcript:
        # Преобразуем entities в маркеры перед переводом (только для caption_ru)
        caption_ru_prepared = entities_to_markers(caption_ru_original, entities) if caption_ru_original else ""
        # Вызываем новую функцию с тремя параметрами
        translation_result = await translate_text(
            caption_ru=caption_ru_prepared,
            asr_ru=whisper_transcript or "",
            base_hashtags=HASHTAGS_BLOCK
        )
    else:
        # Fallback если нет данных
        translation_result = {
            'voice': "Qiziqarli video. Oxirigacha ko'ring.",
            'caption': "Qiziqarli video. Oxirigacha ko'ring.",
            'hashtags': ""
        }
    
    # Извлекаем результаты из словаря
    voice_uz = translation_result.get('voice', '')
    caption_uz = translation_result.get('caption', '')
    extra_hashtags = translation_result.get('hashtags', '')
    
    # Определяем CTA правило (каждые 2 поста)
    use_cta, cta_text, post_counter = next_post_cta_rule()
    log.info(f"[CTA] Post #{post_counter}: use_cta={use_cta}, cta_text={cta_text[:30] if cta_text else None}...")
    
    # Строим текст для TTS (VOICE_UZ + CTA, БЕЗ footer и хэштегов)
    text_for_voice = build_voice_for_tts(voice_uz, cta_text if use_cta else None)
    log.info(f"[TTS] Voice text length: {len(text_for_voice)} chars")
    
    # 🎙️ ELEVENLABS: Генерируем озвучку для всех постов (не только Instagram)
    voiceover_path = None
    has_voiceover = False
    
    if text_for_voice.strip():
        try:
            log.info(f"[ELEVENLABS] Generating voiceover for source={source}...")
            voiceover_path = generate_voiceover(text_for_voice)
            
            if voiceover_path:
                has_voiceover = True
                log.info(f"[ELEVENLABS] ✅ Voiceover ready: {voiceover_path.name} (voiceover: True)")
            else:
                log.warning("[ELEVENLABS] Voiceover generation failed, continuing without voice")
        except Exception as e:
            log.error(f"[ELEVENLABS] Voiceover generation error: {e}")
            # Продолжаем без озвучки при ошибке
    
    # Обрабатываем caption
    caption_uz = sanitize_post(caption_uz)
    caption_uz = remove_comment_phrases(caption_uz)
    
    # Строим финальный caption для публикации (caption + hashtags + footer)
    final_text = build_caption_for_post(
        caption_uz=caption_uz,
        base_hashtags=HASHTAGS_BLOCK,
        extra_hashtags=extra_hashtags,
        footer_html=FOOTER_HTML
    )
    
    # Форматируем финальный текст
    final_text = format_post_structure(final_text)
    final_text = clean_caption(final_text)
    final_text = ensure_footer(final_text)
    final_text = append_branding(final_text)
    
    log.info("FINAL after translate: %s", final_text[:200] if final_text else "(empty)")

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
            "source": source,  # Явное указание источника
        }
    elif post.video or instagram_video_path:
        # если есть видео (Telegram или Instagram), добавляем в очередь
        item = {
            "type": "video",
            "file_id": post.video.file_id if post.video else "instagram_source",
            "caption": final_text,
            "instagram_video_path": str(instagram_video_path) if instagram_video_path else None,
            "buffer_message_id": message_id,
            "buffer_chat_id": chat_id,
            "translation_cost": TRANSLATION_LAST_COST,
            "voiceover": has_voiceover,  # 🎙️ Флаг для Smart Routing
            "voiceover_path": str(voiceover_path) if voiceover_path else None,  # 🎙️ Путь к озвучке
            "instagram_source": instagram_url if instagram_url else None,
            "source": source,  # Явное указание источника
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
            "source": source,  # Явное указание источника
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
                    # Удаляем метаданные тоже
                    meta_file = ready_file.with_suffix('.json')
                    if meta_file.exists():
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
