"""
Workflow B (Part 2): Two-Stage Job Matching & Ingestion Pipeline.
1. Pulls deduplicated jobs from `JobFeedScraper`, generates 384-d `job_embedding`,
   and upserts into `jobs_discovered` on `(source, external_job_id)`.
2. Stage 1 Filter (Free Math): Computes cosine similarity (via Postgres `pgvector` RPC
   `match_jobs_for_candidate` or in-memory cosine similarity) between
   `candidate_profiles.profile_embedding` and `jobs_discovered.job_embedding`.
   Immediately discards any job below `0.55` similarity without spending LLM tokens.
3. Stage 2 Scoring (LLM): For surviving candidates, calls `ModelRouter` (`job_scorer` ->
   `gemini-3.5-flash-lite`) with PII redaction to score `0–100` against target roles,
   hard skills, and location constraints.
4. If `match_score >= user.min_match_score`, inserts into `applications` with `status = 'queued'`
   and `ON CONFLICT (user_id, job_id) DO NOTHING` to guarantee zero duplicate applications.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.core.config import settings
from backend.core.database import get_supabase_client
from backend.core.model_router import BaseModel, ModelRouter, PIIRedactor
from backend.scrapers.job_feeds import DiscoveredJobRecord, JobFeedScraper

logger = logging.getLogger(__name__)


class LLMJobScoreSchema(BaseModel):
    match_score: int
    match_reasoning: str
    tailored_cover_letter: Optional[str] = None


@dataclass
class TwoStageMatchSummary:
    jobs_fetched: int = 0
    jobs_passed_stage1_vector: int = 0
    jobs_discarded_stage1_vector: int = 0
    jobs_scored_stage2_llm: int = 0
    applications_queued: List[Dict[str, Any]] = field(default_factory=list)


JOB_SCORER_SYSTEM_PROMPT = """You are a strict, objective technical hiring evaluator.
Compare the anonymized candidate profile (target roles, skills, experience years, location preference)
against the job posting (title, company, location, remote_type, description).

Return ONLY a valid JSON object with:
- "match_score": integer from 0 to 100 representing true alignment across role seniority, required hard skills, and location/remote compatibility.
- "match_reasoning": concise 1-2 sentence explanation of why the candidate scored this rating (highlighting matching skills and any gaps).
- "tailored_cover_letter": concise 2-paragraph cover letter tailored to the company and role using placeholder {{CANDIDATE_NAME}} for sign-off."""


class JobIngestorService:
    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        scraper: Optional[JobFeedScraper] = None,
        stage1_min_similarity: Optional[float] = None,
    ) -> None:
        self.router = router or ModelRouter()
        self.scraper = scraper or JobFeedScraper()
        self.stage1_min_similarity = (
            stage1_min_similarity
            if stage1_min_similarity is not None
            else settings.STAGE1_MIN_SIMILARITY
        )

    def embed_and_prepare_jobs(
        self, discovered_jobs: List[DiscoveredJobRecord]
    ) -> List[Dict[str, Any]]:
        """
        Generates 384-d embeddings for each discovered job and returns rows ready
        for `jobs_discovered` upsert.
        """
        prepared: List[Dict[str, Any]] = []
        for job in discovered_jobs:
            embed_input = (
                f"Job Title: {job.job_title} | Company: {job.company_name} | "
                f"Location: {job.location} ({job.remote_type}) | "
                f"Description: {job.description_text[:2500]}"
            )
            embedding = self.router.embed_text(embed_input)
            row = job.to_dict()
            row["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{job.source}:{job.external_job_id}"))
            row["job_embedding"] = embedding
            prepared.append(row)
        return prepared

    def filter_stage1_vector_similarity(
        self,
        profile_embedding: List[float],
        job_rows: List[Dict[str, Any]],
        already_applied_job_ids: Optional[set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Stage 1 Filter (Zero LLM Tokens):
        Computes cosine similarity between `profile_embedding` and each job's 384-d
        `job_embedding`, discarding any job already applied to or with similarity < 0.55.
        Returns `(surviving_jobs_sorted_by_similarity_desc, discarded_count)`.
        """
        applied_ids = already_applied_job_ids or set()
        surviving: List[Dict[str, Any]] = []
        discarded = 0

        for job in job_rows:
            job_id = str(job.get("id") or job.get("job_id") or "")
            if job_id and job_id in applied_ids:
                continue

            job_emb = job.get("job_embedding")
            if not job_emb:
                discarded += 1
                continue

            similarity = self.router.cosine_similarity(profile_embedding, job_emb)
            if similarity >= self.stage1_min_similarity:
                enriched = dict(job)
                enriched["similarity"] = round(similarity, 4)
                surviving.append(enriched)
            else:
                discarded += 1

        surviving.sort(key=lambda j: j.get("similarity", 0.0), reverse=True)
        return surviving, discarded

    async def score_and_queue_for_candidate(
        self,
        candidate_profile: Dict[str, Any],
        discovered_jobs: List[DiscoveredJobRecord],
        already_applied_job_ids: Optional[set[str]] = None,
        persist_to_supabase: bool = True,
    ) -> TwoStageMatchSummary:
        """
        Executes Workflow B end-to-end for a candidate:
        1. Embeds discovered jobs (and optionally upserts into `jobs_discovered`).
        2. Runs Stage 1 mathematical filter (`cosine_similarity >= 0.55`).
        3. Runs Stage 2 LLM scoring (`gemini-3.5-flash-lite`) ONLY on Stage 1 survivors.
        4. Queues applications where `match_score >= candidate.min_match_score`.
        """
        summary = TwoStageMatchSummary(jobs_fetched=len(discovered_jobs))
        if not discovered_jobs:
            return summary

        job_rows = self.embed_and_prepare_jobs(discovered_jobs)

        if persist_to_supabase:
            self._upsert_discovered_jobs_to_supabase(job_rows)

        profile_embedding = candidate_profile.get("profile_embedding")
        if not profile_embedding:
            parsed_cv = candidate_profile.get("parsed_cv_json") or {}
            fallback_text = " ".join(
                parsed_cv.get("target_roles", []) + parsed_cv.get("skills", [])
            )
            profile_embedding = self.router.embed_text(fallback_text)

        surviving_jobs, discarded_count = self.filter_stage1_vector_similarity(
            profile_embedding=profile_embedding,
            job_rows=job_rows,
            already_applied_job_ids=already_applied_job_ids,
        )
        summary.jobs_passed_stage1_vector = len(surviving_jobs)
        summary.jobs_discarded_stage1_vector = discarded_count

        user_id = str(candidate_profile["id"])
        min_match_score = int(candidate_profile.get("min_match_score", 75))
        parsed_cv = candidate_profile.get("parsed_cv_json") or {}

        redactor = PIIRedactor(
            full_name=candidate_profile.get("full_name"),
            email=candidate_profile.get("email"),
            phone=candidate_profile.get("phone"),
            street_address=candidate_profile.get("location"),
        )

        # Stage 2: Call LLM scorer only for jobs that survived the 0.55 cosine threshold
        for job in surviving_jobs:
            summary.jobs_scored_stage2_llm += 1
            user_prompt = (
                f"CANDIDATE PROFILE:\n"
                f"- Target Roles: {parsed_cv.get('target_roles', [])}\n"
                f"- Hard Skills: {parsed_cv.get('skills', [])}\n"
                f"- Experience Years: {parsed_cv.get('experience_years', 0)}\n"
                f"- Preferred Location: {candidate_profile.get('location', 'Remote')}\n\n"
                f"JOB POSTING:\n"
                f"- Title: {job['job_title']}\n"
                f"- Company: {job['company_name']}\n"
                f"- Location: {job['location']} ({job['remote_type']})\n"
                f"- Vector Similarity: {job['similarity']}\n"
                f"- Description: {job['description_text'][:2500]}\n"
            )

            score_resp = await self.router.complete_json(
                task_type="job_scorer",
                system_prompt=JOB_SCORER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=LLMJobScoreSchema,
                pii_redactor=redactor,
                reinject_pii_in_output=True,
            )

            match_score = max(0, min(100, int(score_resp.match_score)))
            if match_score >= min_match_score:
                app_row = {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"app:{user_id}:{job['id']}")),
                    "user_id": user_id,
                    "job_id": job["id"],
                    "match_score": match_score,
                    "match_reasoning": score_resp.match_reasoning,
                    "status": "queued",
                    "tailored_cover_letter": getattr(score_resp, "tailored_cover_letter", None),
                    "submitted_answers_json": {},
                }
                summary.applications_queued.append(app_row)
                if persist_to_supabase:
                    self._queue_application_in_supabase(app_row)

        return summary

    @staticmethod
    def _upsert_discovered_jobs_to_supabase(job_rows: List[Dict[str, Any]]) -> None:
        try:
            supabase = get_supabase_client()
            clean_rows = [
                {k: v for k, v in row.items() if k != "similarity"}
                for row in job_rows
            ]
            supabase.table("jobs_discovered").upsert(
                clean_rows,
                on_conflict="source,external_job_id",
            ).execute()
        except Exception as exc:
            logger.warning("[JobIngestor] Supabase jobs_discovered upsert skipped/failed: %s", exc)

    @staticmethod
    def _queue_application_in_supabase(app_row: Dict[str, Any]) -> None:
        try:
            supabase = get_supabase_client()
            supabase.table("applications").upsert(
                app_row,
                on_conflict="user_id,job_id",
                ignore_duplicates=True,
            ).execute()
        except Exception as exc:
            logger.warning("[JobIngestor] Supabase application queue insert skipped/failed: %s", exc)
