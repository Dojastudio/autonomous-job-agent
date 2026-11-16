"""
FastAPI REST Endpoints (`/backend/api/routes.py`) for the Autonomous Job Finder & Auto-Apply Agent:
- `POST /api/cv/upload`: Uploads PDF resume, parses structured CV + 384-d embedding, and inserts 4–6 dynamic gap questions.
- `GET  /api/dashboard/{user_id}`: Returns candidate profile, QA bank items, and live applications joined with job details.
- `POST /api/qa/answer`: Saves user answers to `candidate_qa_bank` (`is_answered = true`) and re-queues any `needs_human_input` applications.
- `PATCH /api/profile/{user_id}/settings`: Updates `auto_apply_enabled`, `min_match_score`, `daily_apply_limit`, and `target_roles`.
- `POST /api/agent/run`: Triggers a full Discovery + Two-Stage Matching + Playwright Auto-Apply cycle (respecting `daily_apply_limit`).
"""
from __future__ import annotations

import datetime
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.core.database import get_supabase_client
from backend.scrapers.job_feeds import JobFeedScraper
from backend.services.cv_parser import CVParserService
from backend.services.job_ingestor import JobIngestorService
from backend.worker.auto_applier import AutoApplierWorker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["autonomous-job-agent"])


# ==============================================================================
# REQUEST / RESPONSE PAYLOAD MODELS
# ==============================================================================

class QAAnswerItem(BaseModel):
    question_key: str
    answer_value: str


class SubmitQAAnswersRequest(BaseModel):
    user_id: str
    answers: List[QAAnswerItem]


class UpdateAgentSettingsRequest(BaseModel):
    auto_apply_enabled: Optional[bool] = None
    min_match_score: Optional[int] = Field(default=None, ge=0, le=100)
    daily_apply_limit: Optional[int] = Field(default=None, ge=1, le=200)
    target_roles: Optional[List[str]] = None


class TriggerAgentRequest(BaseModel):
    user_id: str
    run_discovery: bool = True
    run_auto_apply: bool = True


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "autonomous-job-agent-backend",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.post("/cv/upload")
async def upload_and_parse_cv(
    user_id: str = Form(...),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    """
    Workflow A Endpoint:
    1. Uploads the raw PDF to Supabase Storage (`resumes/{user_id}/resume.pdf`).
    2. Extracts text via PyMuPDF, strips PII, parses JSON CV + 384-d embedding via Gemini,
       and inserts 4–6 dynamic gap questions into `candidate_qa_bank`.
    """
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF file is empty.")

    storage_path = f"{user_id}/resume.pdf"
    try:
        supabase = get_supabase_client()
        supabase.storage.from_("resumes").upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
    except Exception as exc:
        logger.warning("[API] Supabase Storage upload skipped/failed: %s", exc)

    parser_service = CVParserService()
    result = await parser_service.parse_and_ingest_cv(
        user_id=user_id,
        pdf_bytes=pdf_bytes,
        resume_storage_path=storage_path,
        persist_to_supabase=True,
    )

    return {
        "status": "ok",
        "user_id": result.user_id,
        "full_name": result.full_name,
        "email": result.email,
        "phone": result.phone,
        "location": result.location,
        "parsed_cv_json": result.parsed_cv_json,
        "gap_questions": result.gap_questions_inserted,
    }


@router.get("/dashboard/{user_id}")
async def get_dashboard_state(user_id: str) -> Dict[str, Any]:
    """
    Fetches the complete single-dashboard state for a candidate:
    - `profile`: candidate_profiles row
    - `qa_bank`: all rows in candidate_qa_bank (pending + answered)
    - `applications`: live applications joined with `jobs_discovered` metadata
    """
    try:
        supabase = get_supabase_client()
        prof_res = (
            supabase.table("candidate_profiles")
            .select("*")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        qa_res = (
            supabase.table("candidate_qa_bank")
            .select("*")
            .eq("user_id", user_id)
            .order("is_answered", desc=False)
            .execute()
        )
        app_res = (
            supabase.table("applications")
            .select("*, jobs_discovered(company_name, job_title, location, remote_type, source, apply_url)")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return {
            "profile": prof_res.data if prof_res else None,
            "qa_bank": (qa_res.data if qa_res else []) or [],
            "applications": (app_res.data if app_res else []) or [],
        }
    except Exception as exc:
        logger.warning("[API] Dashboard fetch fallback triggered: %s", exc)
        return {"profile": None, "qa_bank": [], "applications": [], "warning": str(exc)}


@router.post("/qa/answer")
async def save_qa_bank_answers(payload: SubmitQAAnswersRequest) -> Dict[str, Any]:
    """
    Updates `candidate_qa_bank` rows with the user's answers (`is_answered = true`).
    Also automatically transitions any `needs_human_input` applications for this user
    back to `queued` so the next worker run completes them.
    """
    updated_count = 0
    try:
        supabase = get_supabase_client()
        for item in payload.answers:
            if not item.answer_value.strip():
                continue
            supabase.table("candidate_qa_bank").update(
                {
                    "answer_value": item.answer_value.strip(),
                    "is_answered": True,
                }
            ).eq("user_id", payload.user_id).eq("question_key", item.question_key).execute()
            updated_count += 1

        # Re-queue applications that were paused waiting for human input
        supabase.table("applications").update(
            {"status": "queued", "error_log": None}
        ).eq("user_id", payload.user_id).eq("status", "needs_human_input").execute()
    except Exception as exc:
        logger.warning("[API] Failed to persist QA answers to Supabase: %s", exc)

    return {"status": "ok", "updated_answers": updated_count}


@router.patch("/profile/{user_id}/settings")
async def update_agent_settings(
    user_id: str,
    payload: UpdateAgentSettingsRequest,
) -> Dict[str, Any]:
    """Updates Agent Control Bar settings (`auto_apply_enabled`, `min_match_score`, `daily_apply_limit`, `target_roles`)."""
    update_fields: Dict[str, Any] = {}
    if payload.auto_apply_enabled is not None:
        update_fields["auto_apply_enabled"] = payload.auto_apply_enabled
    if payload.min_match_score is not None:
        update_fields["min_match_score"] = payload.min_match_score
    if payload.daily_apply_limit is not None:
        update_fields["daily_apply_limit"] = payload.daily_apply_limit

    try:
        supabase = get_supabase_client()
        if payload.target_roles is not None:
            prof = (
                supabase.table("candidate_profiles")
                .select("parsed_cv_json")
                .eq("id", user_id)
                .maybe_single()
                .execute()
            )
            cv_json = (prof.data or {}).get("parsed_cv_json") or {}
            cv_json["target_roles"] = payload.target_roles
            update_fields["parsed_cv_json"] = cv_json

        if update_fields:
            supabase.table("candidate_profiles").update(update_fields).eq("id", user_id).execute()
    except Exception as exc:
        logger.warning("[API] Settings update warning: %s", exc)

    return {"status": "ok", "updated": update_fields}


@router.post("/agent/run")
async def run_discovery_and_auto_apply(payload: TriggerAgentRequest) -> Dict[str, Any]:
    """
    Manual & Cron Trigger Endpoint:
    1. Runs Workflow B (pulls structured ATS feeds, runs Stage 1 pgvector filter >= 0.55,
       runs Stage 2 LLM scoring, and queues matching jobs).
    2. Runs Workflow C (processes queued applications up to `daily_apply_limit` when
       `auto_apply_enabled` is true or when manually triggered).
    """
    return await execute_agent_pipeline_for_user(
        user_id=payload.user_id,
        run_discovery=payload.run_discovery,
        run_auto_apply=payload.run_auto_apply,
    )


async def execute_agent_pipeline_for_user(
    user_id: str,
    run_discovery: bool = True,
    run_auto_apply: bool = True,
) -> Dict[str, Any]:
    supabase = get_supabase_client()
    prof_res = (
        supabase.table("candidate_profiles")
        .select("*")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    profile = prof_res.data if prof_res else None
    if not profile:
        raise HTTPException(
            status_code=404,
            detail=f"Candidate profile '{user_id}' not found. Upload a CV first.",
        )

    discovery_summary_dict: Dict[str, Any] = {}
    if run_discovery:
        scraper = JobFeedScraper()
        ingestor = JobIngestorService(scraper=scraper)
        discovered_jobs = await scraper.fetch_all_feeds()

        existing_apps = (
            supabase.table("applications")
            .select("job_id")
            .eq("user_id", user_id)
            .execute()
        )
        applied_ids = {str(r["job_id"]) for r in (existing_apps.data or [])}

        match_summary = await ingestor.score_and_queue_for_candidate(
            candidate_profile=profile,
            discovered_jobs=discovered_jobs,
            already_applied_job_ids=applied_ids,
            persist_to_supabase=True,
        )
        discovery_summary_dict = {
            "jobs_fetched": match_summary.jobs_fetched,
            "jobs_passed_stage1_vector": match_summary.jobs_passed_stage1_vector,
            "jobs_discarded_stage1_vector": match_summary.jobs_discarded_stage1_vector,
            "jobs_scored_stage2_llm": match_summary.jobs_scored_stage2_llm,
            "newly_queued_count": len(match_summary.applications_queued),
        }

    apply_results: List[Dict[str, Any]] = []
    if run_auto_apply:
        daily_limit = int(profile.get("daily_apply_limit", 15))
        today_start = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        applied_today_res = (
            supabase.table("applications")
            .select("id")
            .eq("user_id", user_id)
            .eq("status", "applied")
            .gte("applied_at", today_start)
            .execute()
        )
        applied_today_count = len(applied_today_res.data or [])
        remaining_quota = max(0, daily_limit - applied_today_count)

        if remaining_quota > 0:
            queued_res = (
                supabase.table("applications")
                .select("*, jobs_discovered(*)")
                .eq("user_id", user_id)
                .eq("status", "queued")
                .order("match_score", desc=True)
                .limit(remaining_quota)
                .execute()
            )
            qa_res = (
                supabase.table("candidate_qa_bank")
                .select("*")
                .eq("user_id", user_id)
                .execute()
            )
            qa_rows = qa_res.data or []

            # Download candidate PDF resume once to a temporary file for `<input type="file">`
            temp_pdf_path: Optional[str] = None
            resume_storage_path = profile.get("resume_url")
            if resume_storage_path:
                try:
                    pdf_bytes = supabase.storage.from_("resumes").download(resume_storage_path)
                    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                    tmp_file.write(pdf_bytes)
                    tmp_file.close()
                    temp_pdf_path = tmp_file.name
                except Exception as exc:
                    logger.warning("[API] Could not download resume PDF from storage: %s", exc)

            worker = AutoApplierWorker()
            try:
                for app_row in queued_res.data or []:
                    job_record = app_row.get("jobs_discovered") or {}
                    if not job_record.get("apply_url"):
                        continue
                    res = await worker.process_single_application(
                        application=app_row,
                        candidate_profile=profile,
                        qa_bank_rows=qa_rows,
                        job_record=job_record,
                        resume_pdf_path=temp_pdf_path,
                        persist_to_supabase=True,
                    )
                    apply_results.append(
                        {
                            "application_id": res.application_id,
                            "status": res.status,
                            "proof_screenshot_url": res.proof_screenshot_url,
                            "error_log": res.error_log,
                        }
                    )
            finally:
                if temp_pdf_path and Path(temp_pdf_path).exists():
                    Path(temp_pdf_path).unlink(missing_ok=True)

    return {
        "status": "ok",
        "discovery": discovery_summary_dict,
        "auto_apply_processed": apply_results,
    }
