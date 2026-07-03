from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

APP_NAME = "ComPass"
APP_SUBTITLE = "Computer Science X Compass · 학생들의 길잡이"


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name, "true" if default else "false").lower()
    return value in {"1", "true", "yes", "on"}


def env_url(name: str, default: str = "") -> str:
    value = env(name, default)
    prefix = f"{name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value


NOTION_TOKEN = env("NOTION_TOKEN") or env("NOTION_API_KEY")
NOTION_TOKEN_SOURCE = "NOTION_TOKEN" if env("NOTION_TOKEN") else ("NOTION_API_KEY" if env("NOTION_API_KEY") else "")
NOTION_KNOWLEDGE_DB_ID = (
    env("NOTION_KNOWLEDGE_DB_ID")
    or env("NOTION_DATABASE_ID")
    or "38773fbd195180788faac9a54ae8e512"
)
NOTION_STATS_DB_ID = env("NOTION_STATS_DB_ID", "38773fbd195180708158dc38ec3fbd2f")
NOTION_VERSION = env("NOTION_VERSION", "2022-06-28")

LLM_PROVIDER = env("LLM_PROVIDER", "openai").lower()
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4.1-mini")
GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_PRIMARY_KEY = env_int("GEMINI_PRIMARY_KEY", 3)
_GEMINI_KEY_MAP = {
    1: env("GEMINI_API_KEY"),
    2: env("GEMINI_API_KEY_2"),
    3: env("GEMINI_API_KEY_3"),
    4: env("GEMINI_API_KEY_4"),
}
_GEMINI_KEY_ORDER = [GEMINI_PRIMARY_KEY, 2, 1, 3, 4]
GEMINI_API_KEY_ENTRIES = [
    (f"GEMINI_API_KEY_{number}" if number > 1 else "GEMINI_API_KEY", _GEMINI_KEY_MAP[number])
    for number in dict.fromkeys(number for number in _GEMINI_KEY_ORDER if number in _GEMINI_KEY_MAP)
    if _GEMINI_KEY_MAP[number]
]
GEMINI_API_KEYS = [key for _, key in GEMINI_API_KEY_ENTRIES]
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    model.strip()
    for model in env("GEMINI_FALLBACK_MODELS", "gemini-2.0-flash").split(",")
    if model.strip()
]
GEMINI_MAX_OUTPUT_TOKENS = env_int("GEMINI_MAX_OUTPUT_TOKENS", 1024)
LLM_TIMEOUT_SEC = env_int("LLM_TIMEOUT_SEC", 45)
ENABLE_LLM_INTENT_CLASSIFIER = env_bool("ENABLE_LLM_INTENT_CLASSIFIER", False)

CRAWL_START_URL = env("CRAWL_START_URL", "https://cs.knou.ac.kr/sites/cs1/index.do")
ALLOWED_DOMAIN = env("ALLOWED_DOMAIN", "cs.knou.ac.kr")
ALLOWED_PATH_PREFIX = env("ALLOWED_PATH_PREFIX", "/cs1,/sites/cs1,/bbs/cs1")
CRAWL_DELAY_SECONDS = env_float("CRAWL_DELAY_SECONDS", 1.0)
CRAWL_MAX_PAGES = env_int("CRAWL_MAX_PAGES", 500)
CRAWL_TIMEOUT_SECONDS = env_int("CRAWL_TIMEOUT_SECONDS", 25)
CRAWL_YEARS_LIMIT = max(0, env_int("CRAWL_YEARS_LIMIT", 3))
CRAWL_NOTICE_YEARS_LIMIT = max(0, env_int("CRAWL_NOTICE_YEARS_LIMIT", CRAWL_YEARS_LIMIT or 3))
CRAWL_TEMPORARY_YEARS_LIMIT = max(0, env_int("CRAWL_TEMPORARY_YEARS_LIMIT", 1))
ENABLE_DATA_TIERING = env_bool("ENABLE_DATA_TIERING", True)
USER_AGENT = env(
    "CRAWL_USER_AGENT",
    "KNOU-CS-AI-Navigator/1.0 (+https://cs.knou.ac.kr/sites/cs1/index.do)",
)
COMMUNITY_CRAWL_ENABLED = env_bool("COMMUNITY_CRAWL_ENABLED", True)
COMMUNITY_START_URL = env("COMMUNITY_START_URL", "https://c-knou.com/computer_science")
COMMUNITY_ALLOWED_DOMAIN = env("COMMUNITY_ALLOWED_DOMAIN", "c-knou.com")
COMMUNITY_LIST_PAGES = env_int("COMMUNITY_LIST_PAGES", 5)
COMMUNITY_MAX_DOCUMENTS = env_int("COMMUNITY_MAX_DOCUMENTS", 100)
COMMUNITY_DELAY_SECONDS = env_float("COMMUNITY_DELAY_SECONDS", 1.5)

ADMIN_PASSWORD = env("ADMIN_PASSWORD")
SEARCH_TOP_K = env_int("SEARCH_TOP_K", 5)
SEARCH_MIN_SCORE = env_float("SEARCH_MIN_SCORE", 18.0)
INDEX_PATH = Path(env("SEARCH_INDEX_PATH") or env("INDEX_PATH", str(DATA_DIR / "search_index.json")))
CRAWL_SNAPSHOT_PATH = Path(env("CRAWL_SNAPSHOT_PATH", str(DATA_DIR / "crawl_snapshot.json")))
PUBLIC_LOADER_URL = env("PUBLIC_LOADER_URL", "https://mhjang-qa.github.io/ComPass/")
AUTO_LOAD_INDEX_ON_START = env_bool("AUTO_LOAD_INDEX_ON_START", env_bool("STARTUP_INDEX_LOAD", True))
STARTUP_INDEX_LOAD = AUTO_LOAD_INDEX_ON_START
AUTO_REBUILD_INDEX_AFTER_CRAWL = env_bool("AUTO_REBUILD_INDEX_AFTER_CRAWL", True)
DEPARTMENT_HOME_URL = env_url("DEPARTMENT_HOME_URL", "https://cs.knou.ac.kr/sites/cs1/index.do")
CURRICULUM_URL = env_url("CURRICULUM_URL", "https://cs.knou.ac.kr/cs1/4789/subview.do")
SCHEDULE_URL = env_url("SCHEDULE_URL", "https://cs.knou.ac.kr/cs1/4812/subview.do")
NOTICE_URL = env_url("NOTICE_URL", "https://cs.knou.ac.kr/cs1/4812/subview.do")
