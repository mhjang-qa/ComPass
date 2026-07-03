from __future__ import annotations

import logging
import json
import re
import secrets
import threading
import uuid
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import config
from chatbot import (
    answer_question,
    casual_response,
    classify_intent,
    get_llm_health_status,
    normalize_honorific_response,
    sanitize_input,
)
from crawler import CommunityCrawler, REQUIRED_DOCUMENT_URLS, KnouCrawler
from curated_knowledge import curated_documents, match_curated
from notion_client import NotionClient, notion_error_message
from search_index import SearchIndex
from stats import recent_stats, record_interaction_async

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title=config.APP_NAME, description=config.APP_SUBTITLE, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mhjang-qa.github.io",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

index = SearchIndex()
crawl_lock = threading.Lock()
index_job_lock = threading.Lock()
index_load_lock = threading.Lock()
index_ready_event = threading.Event()
conversation_lock = threading.Lock()
conversation_store: dict[str, deque[dict[str, Any]]] = {}
job_state: dict[str, Any] = {
    "crawl": {
        "running": False,
        "message": "대기 중",
        "result": None,
        "percent": 0,
        "saved_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "skipped_old_count": 0,
        "skipped_no_date_count": 0,
        "static_pages": 0,
        "total_urls": 0,
        "current_title": "",
        "error": "",
        "updated_at": None,
        "progress": {"percent": 0},
    },
    "index": {"running": False, "message": "대기 중", "result": None},
    "notion": {"running": False, "message": "확인 전", "result": None},
}
runtime_state: dict[str, Any] = {
    "loading": False,
    "index_loading": False,
    "index_ready": index.status()["documents"] > 0,
    "index_state": "ready" if index.status()["documents"] > 0 else "stale",
    "index_last_error": "",
    "retry_after_ms": 1500,
    "notion_connected": False,
    "notion_document_count": 0,
    "index_document_count": index.status()["documents"],
    "last_sync_at": index.status()["built_at"],
    "last_attempt_at": None,
    "last_reason": "process_start",
    "last_error": "",
}
if runtime_state["index_ready"]:
    index_ready_event.set()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def update_crawl_state(**updates: Any) -> None:
    # 크롤링 상태
    crawl_state = job_state.setdefault("crawl", {})
    progress_update = updates.pop("progress", None)
    if progress_update is not None:
        progress = dict(crawl_state.get("progress") or {})
        progress.update(progress_update)
        crawl_state["progress"] = progress
        if "percent" in progress:
            crawl_state["percent"] = progress["percent"]
    crawl_state.update(updates)
    crawl_state["updated_at"] = now_iso()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    allow_llm: bool = False
    llm_type: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    context: dict[str, Any] | None = None
    language: str = "ko"


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class CrawlRequest(BaseModel):
    max_depth: int = Field(default=3, ge=0, le=10)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=300)


def admin_password_configured() -> bool:
    return bool(config.ADMIN_PASSWORD and config.ADMIN_PASSWORD != "change-me")


def require_admin(password: str | None) -> None:
    if not admin_password_configured():
        logger.warning("[ADMIN] ADMIN_PASSWORD 미설정으로 관리자 접근을 차단했습니다.")
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD를 먼저 안전한 값으로 설정하세요.")
    if not password or not secrets.compare_digest(password, config.ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="관리자 비밀번호가 올바르지 않습니다.")


def mask_database_id(database_id: str) -> str:
    value = (database_id or "").replace("-", "")
    if len(value) < 12:
        return "***"
    return f"{value[:6]}…{value[-6:]}"


def request_ids(req: ChatRequest) -> tuple[str, str]:
    session_id = sanitize_input(req.session_id or "", 120) or str(uuid.uuid4())
    request_id = sanitize_input(req.request_id or "", 120) or str(uuid.uuid4())
    return session_id, request_id


def attach_request_metadata(result: dict[str, Any], session_id: str, request_id: str, req: ChatRequest) -> dict[str, Any]:
    result["session_id"] = result.get("session_id") or session_id
    result["request_id"] = result.get("request_id") or request_id
    if req.llm_type and not result.get("llm_type"):
        result["llm_type"] = req.llm_type
    result["allow_llm"] = bool(req.allow_llm)
    result["requires_llm_confirmation"] = bool(result.get("requires_llm_confirmation"))
    result["language"] = "en" if req.language == "en" else "ko"
    return result


def quick_intent_from_context(context: dict[str, Any] | None) -> str:
    intent = str((context or {}).get("quick_intent") or "").strip().lower()
    return {
        "curriculum": "curriculum",
        "course_table": "curriculum",
        "notice": "notice",
        "recent_notice": "notice",
        "notice_list": "notice",
        "schedule": "schedule",
        "schedule_list": "schedule",
    }.get(intent, "")


EN_ACTION_LABELS = {
    "교육과정 확인하기": "View Curriculum",
    "교육과정 바로가기": "View Curriculum",
    "교육과정 더보기": "View Curriculum",
    "전체 교육과정 바로가기": "View Full Curriculum",
    "교수진 페이지 바로가기": "View Faculty",
    "교수진 바로가기": "View Faculty",
    "교수 홈페이지 바로가기": "Visit Faculty Homepage",
    "공지사항 바로가기": "View Notices",
    "공지 바로가기": "View Notice",
    "공지 더보기": "View More Notices",
    "학과 일정 바로가기": "View Schedule",
    "교과목 안내 바로가기": "View Course Information",
    "공식 홈페이지 바로가기": "Visit Official Website",
    "공식 페이지 바로가기": "Visit Official Website",
    "자세히 보기": "View Details",
    "자료 확인하기": "View Material",
    "PDF 보기": "View PDF",
    "원문 보기": "View Original",
    "LLM 보조 답변 사용": "Use AI Helper",
    "AI Helper 사용": "Use AI Helper",
    "지역대학 안내 바로가기": "View Regional Campus Information",
}
EN_COURSE_LABELS = {
    "인공지능": "AI",
    "데이터베이스시스템": "Database Systems",
    "운영체제": "Operating Systems",
    "이산수학": "Discrete Mathematics",
    "파이썬프로그래밍기초": "Python Programming Basics",
    "데이터정보처리입문": "Introduction to Data and Information Processing",
    "컴퓨터의이해": "Understanding Computers",
    "Java프로그래밍": "Java Programming",
}


def translate_action_label_en(label: str) -> str:
    label = (label or "").strip()
    if label in EN_ACTION_LABELS:
        return EN_ACTION_LABELS[label]
    match = re.match(r"^(.+?)\s*과목\s*바로가기$", label)
    if match:
        course_name = match.group(1).strip()
        return f"View {EN_COURSE_LABELS.get(course_name, course_name)} Course"
    return label


def localize_response(result: dict[str, Any], language: str) -> dict[str, Any]:
    if language != "en":
        return result
    if result.get("mode") in {"DB_LOAD_ERROR", "INDEX_EMPTY"}:
        result["answer"] = (
            "The official knowledge database could not be loaded. Please try again later."
            if result.get("mode") == "DB_LOAD_ERROR"
            else "The backend is reachable, but the search index is empty. Please run crawling or rebuild the index from the admin menu."
        )
        return result
    answer_type = str(result.get("answer_type") or "")
    course_name = result.get("course_name") or ""
    if answer_type == "llm_pending":
        message = "The AI helper answer is being generated."
        result["answer"] = message
        result["user_message"] = message
        result["message"] = message
        return result
    if answer_type == "llm_fallback":
        message = (
            "The AI helper answer could not be completed right now.\n"
            "I will show only the information confirmed from official data."
        )
        result["answer"] = message
        result["user_message"] = message
        result["message"] = message
        result["summary"] = "Official course information is still available."
        for action in result.get("actions") or []:
            label = action.get("label") or ""
            action["label"] = translate_action_label_en(label)
        return result
    defaults = {
        "faculty": ("Computer Science faculty information.", "Here are the main faculty details from the official department data."),
        "faculty_detail": ("Faculty profile information.", "Here is the official faculty profile information."),
        "curriculum_by_grade": ("Computer Science curriculum information.", "You can check courses by year and study flow in the Curriculum menu on the official department page."),
        "course_table": ("Computer Science curriculum information.", "You can check courses by year and study flow in the Curriculum menu on the official department page."),
        "notice_list": ("Latest department notices.", "Here are the 3 latest Computer Science department notices."),
        "schedule_list": ("Department schedule information.", "Here are 3 key Computer Science department schedule items."),
        "course_difficulty": (f"{course_name or 'This course'} study workload guide.", "Difficulty and workload are reference guidance, not an official standard."),
        "course_recommendation": ("Recommended courses for beginners and transfer students.", "Here are representative courses based on the official curriculum."),
        "campus_location": ("Regional campus location information.", "You can check regional campus and learning center locations on the official KNOU website."),
        "document_list": ("Official material search results.", "Here are relevant past exam, exam material, and PDF documents from official data."),
        "llm_confirmation_required": ("AI helper confirmation is required.", "Official data does not define perceived difficulty. AI helper guidance can be used as reference only."),
        "text": ("Official department information.", result.get("summary") or "Here is information based on official department data."),
    }
    answer, summary = defaults.get(answer_type, defaults["text"])
    result["answer"] = answer
    if result.get("summary"):
        original_summary = str(result.get("summary") or "")
        if answer_type == "notice_list" and not (result.get("items") or []):
            result["summary"] = "No recent notices were found in the saved official data.\nYou can check notices on the official department page."
        elif answer_type == "schedule_list" and not (result.get("items") or []):
            result["summary"] = "No schedule items were found in the saved official data.\nYou can check the academic schedule on the official department page."
        elif answer_type == "campus_location":
            result["summary"] = summary
            result["items"] = [
                {
                    "label": "How to check",
                    "value": "You can check addresses and contact information for each regional campus and learning center on the official KNOU website.",
                },
                {
                    "label": "Additional guidance",
                    "value": "If you enter a region such as Seoul, Gyeonggi, or Busan, I can guide you more specifically.",
                },
                {
                    "label": "Note",
                    "value": "Campus addresses and operating information may change, so please confirm the latest information on the official page before visiting.",
                },
            ]
        elif "3건" in original_summary or "3개" in original_summary or answer_type in {"notice_list", "schedule_list"}:
            result["summary"] = summary
        else:
            result["summary"] = summary
    for action in result.get("actions") or []:
        label = action.get("label") or ""
        action["label"] = translate_action_label_en(label)
    for item in result.get("items") or []:
        if item.get("link_label"):
            item["link_label"] = translate_action_label_en(item["link_label"])
    return result


def conversation_history(session_id: str, incoming: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    # 세션 대화
    if incoming:
        return incoming[-10:]
    with conversation_lock:
        return list(conversation_store.get(session_id, deque(maxlen=10)))


def remember_conversation(session_id: str, question: str, result: dict[str, Any]) -> None:
    with conversation_lock:
        history = conversation_store.setdefault(session_id, deque(maxlen=6))
        history.append({"role": "user", "content": sanitize_input(question, 300)})
        course_name = result.get("course_name") or ((result.get("items") or [{}])[0].get("title") if result.get("items") else "")
        history.append({
            "role": "assistant",
            "content": sanitize_input(result.get("summary") or result.get("answer") or "", 300),
            "intent": str(result.get("structured_intent") or result.get("answer_type") or ""),
            "entities": {"course_name": course_name} if course_name else {},
            "rag_sources": [str(source.get("title") or source.get("url") or "") for source in (result.get("sources") or [])[:3]],
            "created_at": now_iso(),
        })


def finalize_chat_response(req: ChatRequest, result: dict[str, Any], session_id: str, request_id: str) -> dict[str, Any]:
    result = localize_response(result, "en" if req.language == "en" else "ko")
    if req.language != "en":
        result = normalize_honorific_response(result)
    attach_request_metadata(result, session_id, request_id, req)
    remember_conversation(session_id, req.question, result)
    record_interaction_async(req.question, result)
    logger.info(
        "[CHAT][RETURN] request_id=%s answer_type=%s mode=%s",
        request_id,
        result.get("answer_type"),
        result.get("mode"),
    )
    return result


def sync_index_runtime_state(reason: str = "index_status") -> dict[str, Any]:
    # 인덱스 상태값
    status = index.status()
    documents = int(status.get("documents") or 0)
    loading = bool(runtime_state.get("index_loading") or index_load_lock.locked())
    state = "loading" if loading else ("ready" if documents > 0 else ("failed" if status.get("load_error") or runtime_state.get("last_error") else "stale"))
    runtime_state.update(
        loading=loading,
        index_loading=loading,
        index_ready=documents > 0,
        index_state=state,
        index_document_count=documents,
        last_sync_at=status.get("built_at") or runtime_state.get("last_sync_at"),
        last_reason=reason,
    )
    if documents > 0:
        runtime_state["index_last_error"] = ""
        index_ready_event.set()
        runtime_state["last_error"] = ""
        if not job_state.get("index", {}).get("running"):
            job_state["index"] = {
                "running": False,
                "message": "검색 가능",
                "result": status,
            }
        logger.info(
            "[INDEX] loaded documents=%d courses=%d excluded=%d",
            documents,
            int(status.get("courses") or 0),
            int(status.get("excluded") or 0),
        )
    elif status.get("load_error"):
        runtime_state["last_error"] = f"검색 인덱스 파일 로드 실패: {status['load_error']}"
        runtime_state["index_last_error"] = runtime_state["last_error"]
        if not job_state.get("index", {}).get("running"):
            job_state["index"] = {
                "running": False,
                "message": runtime_state["last_error"],
                "result": status,
            }
    return status


def mark_index_loading(reason: str) -> None:
    has_documents = index.status()["documents"] > 0
    runtime_state.update(
        loading=True,
        index_loading=True,
        index_ready=has_documents,
        index_state="loading",
        index_last_error="",
        last_attempt_at=datetime.now().astimezone().isoformat(),
        last_reason=reason,
        last_error="",
    )
    if not has_documents:
        index_ready_event.clear()
    job_state["index"] = {"running": True, "message": "검색 인덱스 준비 중", "result": index.status()}


def mark_index_ready(result: dict[str, Any] | None, reason: str) -> None:
    status = result or index.status()
    documents = int(status.get("documents") or index.status().get("documents") or 0)
    runtime_state.update(
        loading=False,
        index_loading=False,
        index_ready=documents > 0,
        index_state="ready" if documents > 0 else "stale",
        index_document_count=documents,
        last_sync_at=status.get("built_at") or runtime_state.get("last_sync_at"),
        last_reason=reason,
        last_error="",
        index_last_error="",
    )
    if documents > 0:
        index_ready_event.set()
        job_state["index"] = {
            "running": False,
            "message": "검색 가능",
            "result": status,
        }
        logger.info(
            "[INDEX] ready documents=%d indexed=%d",
            runtime_state.get("notion_document_count", documents),
            documents,
        )


def mark_index_failed(error_message: str, reason: str) -> None:
    documents = index.status()["documents"]
    runtime_state.update(
        loading=False,
        index_loading=False,
        index_ready=documents > 0,
        index_state="stale" if documents > 0 else "failed",
        index_document_count=documents,
        last_reason=reason,
        last_error=error_message,
        index_last_error=error_message,
    )
    if documents > 0:
        index_ready_event.set()
    else:
        index_ready_event.clear()


def rebuild_index_from_documents(documents: list[dict[str, Any]], reason: str) -> dict[str, Any] | None:
    if not index_load_lock.acquire(blocking=False):
        logger.info("[INDEX] rebuild skipped because load already running reason=%s", reason)
        return None
    mark_index_loading(reason)
    try:
        logger.info("[INDEX] rebuild started reason=%s", reason)
        result = index.rebuild(documents)
        mark_index_ready(result, reason)
        return result
    except Exception as exc:
        mark_index_failed(notion_error_message(exc, "검색 인덱스"), reason)
        raise
    finally:
        runtime_state["loading"] = False
        runtime_state["index_loading"] = False
        index_load_lock.release()


def ensure_search_index(*, force: bool = False, reason: str = "lazy_chat") -> bool:
    current_count = index.status()["documents"]
    if current_count > 0 and not force:
        sync_index_runtime_state(reason)
        return True
    if not index_load_lock.acquire(blocking=False):
        logger.info("[INDEX] waiting existing load reason=%s", reason)
        logger.info("[INDEX] rebuild skipped because load already running reason=%s", reason)
        return index.status()["documents"] > 0

    if index.status()["documents"] > 0 and not force:
        sync_index_runtime_state(reason)
        index_load_lock.release()
        return True

    mark_index_loading(reason)
    try:
        logger.info("[INDEX] rebuild started reason=%s", reason)
        if not config.NOTION_TOKEN:
            raise RuntimeError(
                "NOTION_TOKEN이 설정되지 않았습니다. NOTION_API_KEY 별칭도 확인했으나 값이 없습니다."
            )
        logger.info("[INDEX] notion sync started reason=%s", reason)
        client = NotionClient()
        schema_result = client.ensure_knowledge_schema()
        curated_result = client.upsert_curated_knowledge()
        required_documents = []
        if REQUIRED_DOCUMENT_URLS:
            required_crawler = KnouCrawler(max_pages=len(REQUIRED_DOCUMENT_URLS), max_depth=0)
            for required_url in REQUIRED_DOCUMENT_URLS:
                document = required_crawler.fetch_document(required_url)
                if document:
                    required_documents.append(document)
        required_result = client.upsert_many(required_documents)
        logger.info(
            "[INDEX] required pages synchronized requested=%d loaded=%d result=%s",
            len(REQUIRED_DOCUMENT_URLS),
            len(required_documents),
            required_result,
        )
        documents = client.knowledge_documents()
        runtime_state.update(
            notion_connected=True,
            notion_document_count=len(documents),
        )
        if not documents:
            mark_index_failed("Notion 지식 DB가 비어 있습니다.", reason)
            logger.warning(
                "[INDEX] Notion load succeeded but zero documents db=%s reason=%s",
                mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
                reason,
            )
            return False
        result = index.rebuild(documents)
        mark_index_ready(result, reason)
        job_state["index"] = {
            "running": False,
            "message": "검색 인덱스 자동 로딩 완료",
            "result": result,
        }
        job_state["notion"] = {
            "running": False,
            "message": "Notion 연결 및 지식 로딩 완료",
            "result": {
                "knowledge": schema_result,
                "curated": curated_result,
                "required": required_result,
                "documents": len(documents),
            },
        }
        logger.info(
            "[INDEX] Notion load success documents=%d indexed=%d excluded=%d db=%s reason=%s",
            len(documents),
            result["documents"],
            result.get("excluded", 0),
            mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
            reason,
        )
        return result["documents"] > 0
    except Exception as exc:
        error_message = notion_error_message(exc, "지식 DB")
        runtime_state.update(notion_connected=False, notion_document_count=0)
        mark_index_failed(error_message, reason)
        job_state["notion"] = {
            "running": False,
            "message": error_message,
            "result": None,
        }
        logger.exception(
            "[INDEX] Notion/index load failed db=%s reason=%s",
            mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
            reason,
        )
        return False
    finally:
        runtime_state["loading"] = False
        runtime_state["index_loading"] = False
        index_load_lock.release()


def initialize_search_index_on_startup() -> None:
    logger.info("[INDEX] startup load started")
    if index.path.exists():
        index.load()
    status = sync_index_runtime_state("startup_file_load")
    if status.get("documents", 0) > 0:
        job_state["notion"] = {
            "running": False,
            "message": "검색 인덱스 파일을 메모리에 로드했습니다.",
            "result": {"documents": status["documents"], "path": status.get("path")},
        }
        logger.info("[INDEX] local cache load success documents=%d path=%s", status["documents"], status.get("path"))
        return
    bootstrap_documents = bundled_bootstrap_documents()
    if bootstrap_documents:
        logger.info("[INDEX] bundled bootstrap started documents=%d", len(bootstrap_documents))
        result = rebuild_index_from_documents(bootstrap_documents, "startup_bootstrap")
        if result and int(result.get("documents") or 0) > 0:
            job_state["notion"] = {
                "running": False,
                "message": "번들된 공식 데이터로 검색 인덱스를 즉시 구성했습니다.",
                "result": {"documents": result["documents"], "path": str(index.path)},
            }
            logger.info("[INDEX] bundled bootstrap success documents=%d path=%s", result["documents"], index.path)
            return
    if not config.NOTION_TOKEN:
        job_state["notion"] = {
            "running": False,
            "message": "검색 인덱스가 비어 있고 NOTION_TOKEN이 없어 자동 재구성을 건너뛰었습니다.",
            "result": None,
        }
        mark_index_failed(job_state["notion"]["message"], "startup")
        logger.error("[STARTUP] %s", job_state["notion"]["message"])
        return
    job_state["notion"] = {"running": True, "message": "검색 인덱스가 비어 있어 Notion 기준으로 재구성 중", "result": None}
    logger.warning(
        "[STARTUP] empty or unavailable search index path=%s error=%s; rebuilding from Notion",
        status.get("path"),
        status.get("load_error") or "empty index",
    )
    ensure_search_index(force=True, reason="startup")


def bundled_bootstrap_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    snapshot_path = config.CRAWL_SNAPSHOT_PATH
    if snapshot_path.exists():
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            documents.extend(
                item
                for item in payload.get("documents", [])
                if isinstance(item, dict) and (item.get("title") or item.get("body"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[INDEX] bundled snapshot load failed path=%s error=%s", snapshot_path, exc)
    try:
        documents.extend(asdict(document) for document in curated_documents())
    except Exception as exc:
        logger.warning("[INDEX] bundled curated knowledge load failed error=%s", exc)
    deduped: dict[str, dict[str, Any]] = {}
    for document in documents:
        key = document.get("source_url") or document.get("title") or document.get("content_hash") or str(len(deduped))
        deduped[key] = document
    return list(deduped.values())


@app.on_event("startup")
def startup_initialize_notion() -> None:
    logger.info("[STARTUP] ComPass server startup started")
    if not admin_password_configured():
        logger.warning("[STARTUP] ADMIN_PASSWORD가 설정되지 않아 모든 관리자 기능을 차단합니다.")
    logger.info("[STARTUP] Health endpoint available")
    logger.info("[STARTUP] FastAPI app ready")
    if not config.AUTO_LOAD_INDEX_ON_START:
        job_state["notion"] = {
            "running": False,
            "message": "AUTO_LOAD_INDEX_ON_START=false: 서버 기동 시 인덱스 자동 로딩을 건너뛰었습니다.",
            "result": None,
        }
        status = sync_index_runtime_state("startup_skipped")
        runtime_state.update(
            loading=False,
            last_reason="startup_skipped",
            last_error="" if status.get("documents", 0) > 0 else runtime_state.get("last_error", ""),
        )
        logger.info(
            "[STARTUP] skipped index autoload. Use /api/index/rebuild or lazy chat loading."
        )
        return
    threading.Thread(target=initialize_search_index_on_startup, daemon=True).start()


def run_crawl_job(max_depth: int) -> None:
    if not crawl_lock.acquire(blocking=False):
        update_crawl_state(running=True, message="이미 작업이 진행 중입니다.")
        return
    job_state["crawl"] = {}
    update_crawl_state(
        running=True,
        message=f"크롤링 진행중입니다. Depth {max_depth} 범위를 준비하고 있습니다.",
        result=None,
        error="",
        current_title="",
        saved_count=0,
        failed_count=0,
        skipped_count=0,
        progress={
            "percent": 1,
            "visited": 0,
            "queued": 0,
            "documents": 0,
            "total_urls": 0,
            "skipped_old": 0,
            "skipped_no_date": 0,
            "static_pages": 0,
            "CORE": 0,
            "ACTIVE_NOTICE": 0,
            "TEMPORARY": 0,
            "IMPORTANT_ARCHIVE": 0,
            "NOISE": 0,
            "depth": 0,
            "max_depth": max_depth,
            "url": "",
        },
    )
    try:
        notion = NotionClient()
        update_crawl_state(message="Notion 지식 DB 컬럼을 확인하고 있습니다.", progress={"percent": 2})
        notion.ensure_knowledge_schema()
        notion.upsert_curated_knowledge()
        crawler = KnouCrawler(max_depth=max_depth)

        def update_crawl_progress(progress: dict[str, Any]) -> None:
            previous_percent = job_state["crawl"].get("progress", {}).get("percent", 0)
            raw_percent = float(progress.get("percent", 0))
            progress["percent"] = min(80, max(previous_percent, int(raw_percent * 0.8)))
            update_crawl_state(
                message=(
                    "크롤링 진행중입니다. "
                    f"Depth {progress['depth']}/{progress['max_depth']} · "
                    f"방문 {progress['visited']} · 대기 {progress['queued']} · "
                    f"수집 {progress['documents']} · "
                    f"3년 초과 제외 {progress.get('skipped_old', 0)}"
                ),
                total_urls=int(progress.get("total_urls") or progress.get("visited") or 0),
                skipped_old_count=int(progress.get("skipped_old") or 0),
                skipped_no_date_count=int(progress.get("skipped_no_date") or 0),
                static_pages=int(progress.get("static_pages") or 0),
                progress=progress,
            )

        documents = crawler.crawl(update_crawl_progress)
        crawl_stats = dict(getattr(crawler, "stats", {}) or {})
        official_count = len(documents)
        community_count = 0
        if config.COMMUNITY_CRAWL_ENABLED and max_depth >= 1:
            update_crawl_state(
                message=(
                    "공식 사이트 수집 완료. 비공식 학생 커뮤니티 공개 글을 "
                    "보조 지식으로 수집하고 있습니다."
                ),
                progress={"percent": 78},
            )

            def update_community_progress(progress: dict[str, Any]) -> None:
                update_crawl_state(
                    message=(
                        "비공식 커뮤니티 공개 글 수집중입니다. "
                        f"방문 {progress['visited']} · 대기 {progress['queued']} · "
                        f"수집 {progress['documents']}"
                    ),
                    progress={
                        **progress,
                        "percent": 94,
                        "max_depth": max_depth,
                    },
                )

            community_documents = CommunityCrawler().crawl(update_community_progress)
            community_count = len(community_documents)
            documents.extend(community_documents)
        update_crawl_state(
            message="크롤링 완료. Notion DB에 저장하고 있습니다.",
            progress={
                **job_state["crawl"]["progress"],
                "percent": 80,
                "documents": len(documents),
            },
        )

        def update_save_progress(event: dict[str, Any]) -> None:
            counts = event.get("counts") or {}
            idx = int(event.get("index") or 0)
            total = max(int(event.get("total") or len(documents) or 1), 1)
            percent = min(98, 80 + int((idx / total) * 18))
            saved_count = int(counts.get("신규", 0)) + int(counts.get("변경", 0))
            update_crawl_state(
                message="크롤링 완료. Notion DB에 저장하고 있습니다.",
                current_title=event.get("title") or "",
                saved_count=saved_count,
                skipped_count=int(counts.get("유지", 0)),
                failed_count=int(counts.get("실패", 0)),
                progress={
                    **job_state["crawl"].get("progress", {}),
                    "percent": percent,
                    "documents": len(documents),
                    "saved": saved_count,
                    "skipped": int(counts.get("유지", 0)),
                    "failed": int(counts.get("실패", 0)),
                    "url": event.get("url") or "",
                },
            )

        notion_result = notion.upsert_many(documents, progress_callback=update_save_progress)
        archived_result = notion.archive_expired_documents()
        notion_result["archived"] = int(archived_result.get("archived", 0))
        index_result: dict[str, Any] | None = None
        if config.AUTO_REBUILD_INDEX_AFTER_CRAWL:
            update_crawl_state(
                message="Notion 저장 완료. 검색 인덱스를 갱신하고 있습니다.",
                progress={**job_state["crawl"]["progress"], "percent": 98},
                current_title="검색 인덱스 갱신",
            )
            notion_documents = notion.knowledge_documents()
            runtime_state.update(notion_connected=True, notion_document_count=len(notion_documents))
            index_result = rebuild_index_from_documents(notion_documents, "crawl_complete")
            if index_result is None:
                index_result = index.status()
                job_state["index"] = {
                    "running": True,
                    "message": "다른 인덱스 작업이 진행 중이라 크롤링 후 재생성을 건너뛰었습니다.",
                    "result": index_result,
                }
            runtime_state.update(
                notion_connected=True,
                notion_document_count=len(notion_documents),
                index_document_count=index_result.get("documents", 0),
                last_sync_at=index_result.get("built_at"),
                last_attempt_at=datetime.now().astimezone().isoformat(),
                last_reason="crawl_complete",
                last_error="",
            )
            if not job_state["index"].get("running"):
                job_state["index"] = {
                    "running": False,
                    "message": "크롤링 완료 후 검색 인덱스 자동 재생성 완료",
                    "result": index_result,
                }
            logger.info(
                "[INDEX] rebuild completed after crawl documents=%d included=%d excluded=%d",
                len(notion_documents),
                index_result.get("documents", 0),
                index_result.get("excluded", 0),
            )
        else:
            logger.warning("[INDEX] AUTO_REBUILD_INDEX_AFTER_CRAWL=false: crawl completed without index rebuild")
        update_crawl_state(
            running=False,
            message="크롤링 및 Notion 저장 완료",
            error="",
            current_title="",
            saved_count=int(notion_result.get("신규", 0)) + int(notion_result.get("변경", 0)),
            skipped_count=int(notion_result.get("유지", 0)),
            failed_count=int(notion_result.get("실패", 0)),
            skipped_old_count=int(crawl_stats.get("skipped_old", 0)),
            skipped_no_date_count=int(crawl_stats.get("skipped_no_date", 0)),
            static_pages=int(crawl_stats.get("static_pages", 0)),
            total_urls=int(crawl_stats.get("total_urls", 0)),
            result={
                "crawled": len(documents),
                "crawl_stats": crawl_stats,
                "notion": notion_result,
                "archived": archived_result,
                "index": index_result or index.status(),
                "max_depth": max_depth,
                "official_documents": official_count,
                "community_documents": community_count,
            },
            progress={
                **job_state["crawl"]["progress"],
                "percent": 100,
                "documents": len(documents),
                "total_urls": int(crawl_stats.get("total_urls", 0)),
                "skipped_old": int(crawl_stats.get("skipped_old", 0)),
                "skipped_no_date": int(crawl_stats.get("skipped_no_date", 0)),
                "static_pages": int(crawl_stats.get("static_pages", 0)),
            },
        )
    except Exception as exc:
        error_message = notion_error_message(exc, "지식 DB")
        logger.exception("[CRAWL] 크롤링 작업 실패: %s", error_message)
        update_crawl_state(
            running=False,
            message="크롤링 실패",
            error=error_message,
            result=None,
            progress={**job_state["crawl"].get("progress", {})},
        )
    finally:
        update_crawl_state(running=False)
        crawl_lock.release()


def run_index_job() -> None:
    if not index_job_lock.acquire(blocking=False):
        job_state["index"].update(running=True, message="이미 작업이 진행 중입니다.")
        return
    job_state["index"] = {"running": True, "message": "Notion 데이터를 읽고 있습니다.", "result": None}
    try:
        if not ensure_search_index(force=True, reason="manual_rebuild"):
            raise RuntimeError(runtime_state["last_error"] or "검색 인덱스에 문서가 없습니다.")
        result = index.status()
        job_state["index"] = {"running": False, "message": "검색 인덱스 생성 완료", "result": result}
    except Exception as exc:
        logger.exception("인덱스 생성 실패")
        job_state["index"] = {
            "running": False,
            "message": f"실패: {notion_error_message(exc, '지식 DB')}",
            "result": None,
        }
    finally:
        index_job_lock.release()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    hostname = (request.url.hostname or "").lower()
    from_loader = request.query_params.get("from") == "github-pages"
    if hostname.endswith(".onrender.com") and not from_loader:
        return RedirectResponse(config.PUBLIC_LOADER_URL, status_code=307)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_name": config.APP_NAME, "app_subtitle": config.APP_SUBTITLE},
    )


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    require_admin(req.password)
    logger.info("[ADMIN] 관리자 화면 인증 성공")
    return {"ok": True}


@app.post("/api/crawl")
def crawl(
    background_tasks: BackgroundTasks,
    req: CrawlRequest | None = None,
    x_admin_password: str | None = Header(default=None),
):
    require_admin(x_admin_password)
    if job_state["crawl"]["running"] or crawl_lock.locked():
        return {"accepted": False, "message": "이미 작업이 진행 중입니다.", **job_state["crawl"]}
    max_depth = req.max_depth if req else 3
    background_tasks.add_task(run_crawl_job, max_depth)
    return {
        "accepted": True,
        "message": f"크롤링 진행중입니다. Depth {max_depth} 범위를 탐색합니다.",
        "max_depth": max_depth,
    }


@app.get("/api/crawl/status")
def crawl_status(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return job_state["crawl"]


@app.post("/api/notion/setup")
def setup_notion_databases(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        result = NotionClient().ensure_all_schemas()
        curated_result = NotionClient().upsert_curated_knowledge()
        ensure_search_index(force=True, reason="notion_setup")
        return {
            "ok": True,
            "message": "크롤링 지식 DB와 챗봇 통계 DB의 필수 컬럼 구성이 완료되었습니다.",
            "result": {**result, "curated": curated_result},
        }
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Notion DB 구성 실패: {notion_error_message(exc)}",
        ) from exc


@app.post("/api/index/rebuild")
def rebuild_index(background_tasks: BackgroundTasks, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    if job_state["index"]["running"] or index_job_lock.locked():
        return {"accepted": False, "message": "이미 작업이 진행 중입니다.", **job_state["index"]}
    background_tasks.add_task(run_index_job)
    return {"accepted": True, "message": "인덱스 재생성을 시작했습니다."}


@app.post("/api/data-tier/reclassify")
def reclassify_data_tiers(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        client = NotionClient()
        client.ensure_knowledge_schema()
        tier_result = client.reclassify_data_tiers()
        notion_documents = client.knowledge_documents()
        runtime_state.update(notion_connected=True, notion_document_count=len(notion_documents))
        index_result = rebuild_index_from_documents(notion_documents, "data_tier_reclassify")
        if index_result is None:
            index_result = index.status()
        runtime_state.update(
            notion_connected=True,
            notion_document_count=tier_result.get("checked", 0),
            index_document_count=index_result["documents"],
            last_sync_at=index_result["built_at"],
            last_attempt_at=datetime.now().astimezone().isoformat(),
            last_reason="data_tier_reclassify",
            last_error="",
        )
        job_state["index"] = {
            "running": False,
            "message": "데이터 계층 재분류 및 인덱스 갱신 완료",
            "result": index_result,
        }
        return {"ok": True, "message": "데이터 계층 재분류가 완료되었습니다.", "result": tier_result, "index": index_result}
    except Exception as exc:
        logger.exception("[DATA_TIER] 데이터 계층 재분류 실패")
        raise HTTPException(status_code=502, detail=f"데이터 계층 재분류 실패: {notion_error_message(exc, '지식 DB')}") from exc


@app.get("/api/index/status")
def index_status(x_admin_password: str | None = Header(default=None)):
    status = sync_index_runtime_state("index_status")
    indexed = int(status.get("documents") or 0)
    public_status = {
        **status,
        "ready": bool(runtime_state["index_ready"] and indexed > 0),
        "indexed": indexed,
        "state": runtime_state["index_state"],
        "index_loading": runtime_state["index_loading"],
        "index_ready": runtime_state["index_ready"],
        "index_last_error": runtime_state["index_last_error"],
        "retry_after_ms": runtime_state["retry_after_ms"],
        "auto_load_on_start": config.AUTO_LOAD_INDEX_ON_START,
        "auto_rebuild_after_crawl": config.AUTO_REBUILD_INDEX_AFTER_CRAWL,
    }
    if x_admin_password:
        require_admin(x_admin_password)
        return {
            **public_status,
            "job": job_state["index"],
            "runtime": runtime_state,
        }
    return public_status


def start_index_load_background(reason: str) -> None:
    if index.status()["documents"] > 0:
        sync_index_runtime_state(reason)
        return
    if runtime_state.get("index_loading") or index_load_lock.locked():
        logger.info("[INDEX] rebuild skipped because load already running reason=%s", reason)
        return
    mark_index_loading(reason)
    threading.Thread(target=ensure_search_index, kwargs={"force": True, "reason": reason}, daemon=True).start()


def index_loading_chat_response(req: ChatRequest, session_id: str, request_id: str) -> dict[str, Any]:
    logger.info("[CHAT] index loading retryable session=%s", session_id[:8])
    answer = (
        "Preparing the official information search index. I will try again shortly."
        if req.language == "en"
        else "공식 정보 검색 인덱스를 준비 중입니다. 잠시 후 자동으로 다시 시도합니다."
    )
    result = {
        "answer": answer,
        "answer_type": "text",
        "summary": answer,
        "items": [],
        "total_count": 0,
        "source_urls": [],
        "actions": [],
        "mode": "INDEX_LOADING",
        "status": "index_loading",
        "retry_after_ms": runtime_state["retry_after_ms"],
        "sources": [],
        "score": 0,
        "diagnostics": debug_index_payload(),
    }
    attach_request_metadata(result, session_id, request_id, req)
    logger.info(
        "[CHAT][RETURN] request_id=%s answer_type=%s mode=%s",
        request_id,
        result.get("answer_type"),
        result.get("mode"),
    )
    return result


@app.post("/api/search/test")
def search_test(req: SearchRequest, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return {"query": req.query, "results": index.search(req.query, req.top_k)}


@app.post("/api/chat")
def chat(req: ChatRequest):
    session_id, request_id = request_ids(req)
    session_short = session_id[:8]
    logger.info("[CHAT][RECV] request_id=%s allow_llm=%s", request_id, req.allow_llm)
    clean_question = sanitize_input(req.question)
    history = conversation_history(session_id, req.history)
    quick_intent = quick_intent_from_context(req.context)
    casual = casual_response(clean_question)
    if casual:
        casual["elapsed_ms"] = 0
        return finalize_chat_response(req, casual, session_id, request_id)
    if not quick_intent and match_curated(clean_question, history):
        result = answer_question(
            clean_question,
            history=history,
            allow_llm=req.allow_llm,
            llm_type=req.llm_type,
            session_id=session_id,
            request_id=request_id,
            index=index,
        )
        return finalize_chat_response(req, result, session_id, request_id)
    if not quick_intent and classify_intent(clean_question, index) == "course_difficulty":
        result = answer_question(
            clean_question,
            history=history,
            allow_llm=req.allow_llm,
            llm_type=req.llm_type,
            session_id=session_id,
            request_id=request_id,
            index=index,
        )
        return finalize_chat_response(req, result, session_id, request_id)
    if index.status()["documents"] == 0 and index.path.exists():
        index.load()
        sync_index_runtime_state("chat_local_load")
    if index.status()["documents"] == 0:
        logger.warning(
            "[CHAT] empty index detected session=%s question_prefix=%r notion_connected=%s state=%s",
            session_short,
            clean_question[:50],
            runtime_state["notion_connected"],
            runtime_state.get("index_state"),
        )
        if runtime_state.get("index_loading") or index_load_lock.locked():
            logger.info("[INDEX] waiting existing load reason=lazy_chat")
            return index_loading_chat_response(req, session_id, request_id)
        if config.NOTION_TOKEN:
            start_index_load_background("lazy_chat")
            return index_loading_chat_response(req, session_id, request_id)
        mode = "DB_LOAD_ERROR" if runtime_state["last_error"] else "INDEX_EMPTY"
        answer = (
            "공식 지식 DB를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
            if mode == "DB_LOAD_ERROR"
            else "공식 지식 DB는 연결되었지만 검색할 문서가 없습니다. 관리자에게 크롤링을 요청해 주세요."
        )
        result = {
            "answer": answer,
            "answer_type": "text",
            "summary": runtime_state["last_error"] or "검색 인덱스를 사용할 수 없습니다.",
            "items": [],
            "total_count": 0,
            "source_urls": [],
            "actions": [],
            "mode": mode,
            "sources": [],
            "score": 0,
            "failure_reason": runtime_state["last_error"] or "검색 인덱스 문서 0개",
            "diagnostics": debug_index_payload(),
        }
        logger.error(
            "[CHAT] search unavailable session=%s mode=%s error=%s",
            session_short,
            mode,
            result["failure_reason"],
        )
        return finalize_chat_response(req, result, session_id, request_id)
    logger.info("[CHAT] search start session=%s documents=%d", session_short, index.status()["documents"])
    result = answer_question(
        req.question,
        history=history,
        allow_llm=req.allow_llm,
        llm_type=req.llm_type,
        session_id=session_id,
        request_id=request_id,
        index=index,
        forced_intent=quick_intent,
    )
    attach_request_metadata(result, session_id, request_id, req)
    result["diagnostics"] = {
        "notion_connected": runtime_state["notion_connected"],
        "notion_documents": runtime_state["notion_document_count"],
        "index_documents": index.status()["documents"],
        "last_sync_at": runtime_state["last_sync_at"],
    }
    if result.get("requires_llm_confirmation"):
        result["answer"] = (
            "현재 공식 데이터에서 관련 정보를 찾지 못했습니다. "
            "원하시면 AI 보조 답변을 통해 관련 정보를 추가로 안내해드릴 수 있습니다."
        )
    return finalize_chat_response(req, result, session_id, request_id)


def debug_index_payload() -> dict[str, Any]:
    status = index.status()
    return {
        "notion_connected": runtime_state["notion_connected"],
        "notion_loading": runtime_state["loading"],
        "index_state": runtime_state["index_state"],
        "index_loading": runtime_state["index_loading"],
        "index_ready": runtime_state["index_ready"],
        "notion_document_count": runtime_state["notion_document_count"],
        "index_document_count": status["documents"],
        "course_catalog_count": status.get("courses", 0),
        "index_path": status.get("path"),
        "index_load_error": status.get("load_error"),
        "auto_load_on_start": config.AUTO_LOAD_INDEX_ON_START,
        "auto_rebuild_after_crawl": config.AUTO_REBUILD_INDEX_AFTER_CRAWL,
        "last_sync_at": runtime_state["last_sync_at"],
        "last_attempt_at": runtime_state["last_attempt_at"],
        "last_reason": runtime_state["last_reason"],
        "last_error": runtime_state["last_error"],
        "knowledge_db_id_masked": mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
        "stats_db_id_masked": mask_database_id(config.NOTION_STATS_DB_ID),
        "token_configured": bool(config.NOTION_TOKEN),
        "token_env_name": config.NOTION_TOKEN_SOURCE or "missing",
    }


@app.get("/api/debug/index-status")
def debug_index_status():
    return debug_index_payload()


@app.get("/api/stats")
def stats(limit: int = 30, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        return {"items": recent_stats(max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"통계 DB 조회 실패: {notion_error_message(exc, '통계 DB')}",
        ) from exc


@app.get("/api/knowledge/recent")
def recent_knowledge(limit: int = 20, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        return {"items": NotionClient().recent_knowledge(max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"지식 DB 조회 실패: {notion_error_message(exc, '지식 DB')}",
        ) from exc


def health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "running",
        "service": "ComPass",
        "meaning": config.APP_SUBTITLE,
        "index": index.status(),
        "notion_configured": bool(config.NOTION_TOKEN),
        "notion_schema": job_state["notion"],
        "runtime": debug_index_payload(),
        "llm_provider": config.LLM_PROVIDER,
        "llm": get_llm_health_status(),
    }


@app.get("/api/health")
@app.get("/health")
@app.get("/healthz")
def health():
    return health_payload()
