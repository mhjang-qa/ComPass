"""환경변수와 공통 경로 설정."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")


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
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")

CRAWL_START_URL = env("CRAWL_START_URL", "https://cs.knou.ac.kr/sites/cs1/index.do")
ALLOWED_DOMAIN = env("ALLOWED_DOMAIN", "cs.knou.ac.kr")
ALLOWED_PATH_PREFIX = env("ALLOWED_PATH_PREFIX", "/cs1,/sites/cs1,/bbs/cs1")
CRAWL_DELAY_SECONDS = env_float("CRAWL_DELAY_SECONDS", 1.0)
CRAWL_MAX_PAGES = env_int("CRAWL_MAX_PAGES", 500)
CRAWL_TIMEOUT_SECONDS = env_int("CRAWL_TIMEOUT_SECONDS", 25)
USER_AGENT = env(
    "CRAWL_USER_AGENT",
    "KNOU-CS-AI-Navigator/1.0 (+https://cs.knou.ac.kr/sites/cs1/index.do)",
)

ADMIN_PASSWORD = env("ADMIN_PASSWORD", "change-me")
SEARCH_TOP_K = env_int("SEARCH_TOP_K", 5)
SEARCH_MIN_SCORE = env_float("SEARCH_MIN_SCORE", 18.0)
INDEX_PATH = Path(env("INDEX_PATH", str(DATA_DIR / "search_index.json")))
CRAWL_SNAPSHOT_PATH = Path(env("CRAWL_SNAPSHOT_PATH", str(DATA_DIR / "crawl_snapshot.json")))
PUBLIC_LOADER_URL = env("PUBLIC_LOADER_URL", "https://mhjang-qa.github.io/ComPass/")
