"""ComPass FastAPI 애플리케이션."""

from __future__ import annotations

import logging
import secrets
import threading
import uuid
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
from chatbot import answer_question, casual_response, classify_intent, sanitize_input
from crawler import CommunityCrawler, REQUIRED_DOCUMENT_URLS, KnouCrawler
from curated_knowledge import match_curated
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
job_state: dict[str, Any] = {
    "crawl": {
        "running": False,
        "message": "대기 중",
        "result": None,
        "percent": 0,
        "saved_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
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
    "notion_connected": False,
    "notion_document_count": 0,
    "index_document_count": index.status()["documents"],
    "last_sync_at": index.status()["built_at"],
    "last_attempt_at": None,
    "last_reason": "process_start",
    "last_error": "",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def update_crawl_state(**updates: Any) -> None:
    """크롤링 상태 응답을 갱신하고 updated_at/percent를 항상 동기화한다."""
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
    history: list[dict[str, str]] = Field(default_factory=list)
    allow_llm: bool = False
    llm_type: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    context: dict[str, Any] | None = None


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
    return result


def ensure_search_index(*, force: bool = False, reason: str = "lazy_chat") -> bool:
    current_count = index.status()["documents"]
    if current_count > 0 and not force:
        runtime_state["index_document_count"] = current_count
        return True
    if not index_load_lock.acquire(timeout=45):
        runtime_state["last_error"] = "검색 인덱스 로딩 대기 시간이 초과되었습니다."
        logger.error("[INDEX] load lock timeout reason=%s", reason)
        return index.status()["documents"] > 0
    if index.status()["documents"] > 0 and not force:
        index_load_lock.release()
        return True
    if reason == "lazy_chat" and index.status()["documents"] > 0:
        index_load_lock.release()
        return True

    runtime_state.update(
        loading=True,
        last_attempt_at=datetime.now().astimezone().isoformat(),
        last_reason=reason,
        last_error="",
    )
    try:
        if not config.NOTION_TOKEN:
            raise RuntimeError(
                "NOTION_TOKEN이 설정되지 않았습니다. NOTION_API_KEY 별칭도 확인했으나 값이 없습니다."
            )
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
            runtime_state["last_error"] = "Notion 지식 DB가 비어 있습니다."
            logger.warning(
                "[INDEX] Notion load succeeded but zero documents db=%s reason=%s",
                mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
                reason,
            )
            return False
        result = index.rebuild(documents)
        runtime_state.update(
            index_document_count=result["documents"],
            last_sync_at=result["built_at"],
            last_error="",
        )
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
            "[INDEX] Notion load success documents=%d indexed=%d db=%s reason=%s",
            len(documents),
            result["documents"],
            mask_database_id(config.NOTION_KNOWLEDGE_DB_ID),
            reason,
        )
        return result["documents"] > 0
    except Exception as exc:
        runtime_state.update(
            notion_connected=False,
            notion_document_count=0,
            index_document_count=index.status()["documents"],
            last_error=notion_error_message(exc, "지식 DB"),
        )
        job_state["notion"] = {
            "running": False,
            "message": runtime_state["last_error"],
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
        index_load_lock.release()


def initialize_notion_schemas() -> None:
    if not config.NOTION_TOKEN:
        job_state["notion"] = {
            "running": False,
            "message": "NOTION_TOKEN이 없어 자동 구성을 건너뛰었습니다.",
            "result": None,
        }
        runtime_state["last_error"] = job_state["notion"]["message"]
        logger.error("[STARTUP] %s", job_state["notion"]["message"])
        return
    job_state["notion"] = {"running": True, "message": "Notion 지식 DB 로딩 중", "result": None}
    ensure_search_index(force=True, reason="startup")


@app.on_event("startup")
def startup_initialize_notion() -> None:
    if not admin_password_configured():
        logger.warning("[STARTUP] ADMIN_PASSWORD가 설정되지 않아 모든 관리자 기능을 차단합니다.")
    threading.Thread(target=initialize_notion_schemas, daemon=True).start()


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
                    f"수집 {progress['documents']}"
                ),
                progress=progress,
            )

        documents = crawler.crawl(update_crawl_progress)
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
        update_crawl_state(
            message="Notion 저장 완료. 검색 인덱스를 갱신하고 있습니다.",
            progress={**job_state["crawl"]["progress"], "percent": 98},
            current_title="검색 인덱스 갱신",
        )
        index_result = index.rebuild(notion.knowledge_documents())
        runtime_state.update(
            notion_connected=True,
            notion_document_count=index_result["documents"],
            index_document_count=index_result["documents"],
            last_sync_at=index_result["built_at"],
            last_attempt_at=datetime.now().astimezone().isoformat(),
            last_reason="crawl_complete",
            last_error="",
        )
        update_crawl_state(
            running=False,
            message="크롤링 및 Notion 저장 완료",
            error="",
            current_title="",
            saved_count=int(notion_result.get("신규", 0)) + int(notion_result.get("변경", 0)),
            skipped_count=int(notion_result.get("유지", 0)),
            failed_count=int(notion_result.get("실패", 0)),
            result={
                "crawled": len(documents),
                "notion": notion_result,
                "index": index_result,
                "max_depth": max_depth,
                "official_documents": official_count,
                "community_documents": community_count,
            },
            progress={
                **job_state["crawl"]["progress"],
                "percent": 100,
                "documents": len(documents),
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


@app.get("/api/index/status")
def index_status(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return {**index.status(), "job": job_state["index"]}


@app.post("/api/search/test")
def search_test(req: SearchRequest, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return {"query": req.query, "results": index.search(req.query, req.top_k)}


@app.post("/api/chat")
def chat(req: ChatRequest):
    session_id, request_id = request_ids(req)
    session_short = session_id[:8]
    clean_question = sanitize_input(req.question)
    casual = casual_response(clean_question)
    if casual:
        casual["elapsed_ms"] = 0
        attach_request_metadata(casual, session_id, request_id, req)
        record_interaction_async(req.question, casual)
        return casual
    if match_curated(clean_question, None):
        result = answer_question(
            clean_question,
            history=None,
            allow_llm=req.allow_llm,
            llm_type=req.llm_type,
            session_id=session_id,
            request_id=request_id,
            index=index,
        )
        attach_request_metadata(result, session_id, request_id, req)
        record_interaction_async(req.question, result)
        return result
    if classify_intent(clean_question, index) == "course_difficulty":
        result = answer_question(
            clean_question,
            history=None,
            allow_llm=req.allow_llm,
            llm_type=req.llm_type,
            session_id=session_id,
            request_id=request_id,
            index=index,
        )
        attach_request_metadata(result, session_id, request_id, req)
        record_interaction_async(req.question, result)
        return result
    if index.status()["documents"] == 0:
        logger.warning(
            "[CHAT] empty index detected; attempting lazy load session=%s question_prefix=%r notion_connected=%s",
            session_short,
            clean_question[:50],
            runtime_state["notion_connected"],
        )
        loaded = ensure_search_index(force=True, reason="lazy_chat")
        if not loaded:
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
            attach_request_metadata(result, session_id, request_id, req)
            logger.error(
                "[CHAT] search unavailable session=%s mode=%s error=%s",
                session_short,
                mode,
                result["failure_reason"],
            )
            record_interaction_async(req.question, result)
            return result
    result = answer_question(
        req.question,
        history=None,
        allow_llm=req.allow_llm,
        llm_type=req.llm_type,
        session_id=session_id,
        request_id=request_id,
        index=index,
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
            f"공식 지식 DB {index.status()['documents']}개 문서를 검색했지만 충분한 근거를 찾지 못했습니다. "
            "제한된 범위에서 LLM 보조 검색을 진행할까요?"
        )
    record_interaction_async(req.question, result)
    return result


def debug_index_payload() -> dict[str, Any]:
    return {
        "notion_connected": runtime_state["notion_connected"],
        "notion_loading": runtime_state["loading"],
        "notion_document_count": runtime_state["notion_document_count"],
        "index_document_count": index.status()["documents"],
        "course_catalog_count": index.status().get("courses", 0),
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


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "ComPass",
        "meaning": config.APP_SUBTITLE,
        "index": index.status(),
        "notion_configured": bool(config.NOTION_TOKEN),
        "notion_schema": job_state["notion"],
        "runtime": debug_index_payload(),
        "llm_provider": config.LLM_PROVIDER,
    }
