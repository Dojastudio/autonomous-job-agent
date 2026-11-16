"""
Workflow A: CV Ingestion, Structured JSON Parsing, 384-d Embedding Generation,
and Dynamic Gap-Question Generator.
"""
from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.core.database import get_supabase_client
from backend.core.model_router import BaseModel, ModelRouter, PIIRedactor

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. SCHEMAS FOR PARSED CV & GAP QUESTIONS
# ==============================================================================

DEFAULT_GAP_QUESTIONS: List[Dict[str, str]] = [
    {
        "question_key": "work_authorization",
        "question_label": "Are you legally authorized to work in your target job country without restrictions?",
    },
    {
        "question_key": "visa_sponsorship_required",
        "question_label": "Will you now or in the future require visa sponsorship for employment?",
    },
    {
        "question_key": "salary_expectation_usd",
        "question_label": "What is your minimum annual base salary expectation (in USD)?",
    },
    {
        "question_key": "notice_period_days",
        "question_label": "What is your current notice period or earliest available start date (in days)?",
    },
    {
        "question_key": "willing_to_relocate",
        "question_label": "Are you willing to relocate for a hybrid/on-site role, or do you require 100% remote?",
    },
]


class ParsedCVAndGapsSchema(BaseModel):
    skills: List[str]
    experience_years: int
    work_history: List[Dict[str, Any]]
    education: List[Dict[str, Any]]
    target_roles: List[str]
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    gap_questions: List[Dict[str, str]]


@dataclass
class CVIngestionResult:
    user_id: str
    full_name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    location: Optional[str]
    linkedin_url: Optional[str]
    github_url: Optional[str]
    portfolio_url: Optional[str]
    resume_raw_text: str
    parsed_cv_json: Dict[str, Any]
    profile_embedding: List[float]
    gap_questions_inserted: List[Dict[str, Any]] = field(default_factory=list)


# ==============================================================================
# 2. PDF TEXT EXTRACTION (PyMuPDF `fitz` + Pure-Python Fallback)
# ==============================================================================

URL_LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.IGNORECASE)
URL_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/?", re.IGNORECASE)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extracts raw text from PDF bytes using PyMuPDF (`fitz`).
    Includes a lightweight fallback parser for uncompressed/FlateDecode PDF streams
    or plain-text test fixtures when `fitz` is not installed in a minimal test environment.
    """
    if not pdf_bytes:
        raise ValueError("Empty PDF file bytes provided.")

    # 1. Primary: PyMuPDF (`fitz`) or `pypdf`
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text: List[str] = []
        for page in doc:
            pages_text.append(page.get_text("text"))
        doc.close()
        full_text = "\n".join(pages_text).strip()
        if full_text:
            return full_text
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("[CVParser] PyMuPDF extraction failed (%s); attempting pypdf/fallback.", exc)

    try:
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n".join(pages_text).strip()
        if full_text:
            return full_text
    except Exception:
        pass

    # 2. Fallback: Extract text literals from PDF content streams or UTF-8 text
    extracted_chunks: List[str] = []
    for stream_match in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", pdf_bytes, flags=re.DOTALL):
        raw_stream = stream_match.group(1)
        for candidate in (raw_stream, _try_flate_decompress(raw_stream)):
            if not candidate:
                continue
            for txt_match in re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", candidate):
                extracted_chunks.append(txt_match.decode("utf-8", errors="ignore"))

    if extracted_chunks:
        return " ".join(extracted_chunks).strip()

    # Plain UTF-8 fallback (useful for text-wrapped PDFs in unit tests)
    decoded = pdf_bytes.decode("utf-8", errors="ignore").strip()
    if decoded:
        return decoded

    raise ValueError("Unable to extract readable text from the provided PDF.")


def _try_flate_decompress(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except Exception:
        return b""


def extract_local_contact_metadata(raw_text: str) -> Dict[str, Optional[str]]:
    """
    Extracts candidate contact details (full_name, email, phone, linkedin, github)
    locally BEFORE PII redaction so `candidate_profiles` stores accurate contact info
    without leaking PII to free-tier LLMs.
    """
    redactor = PIIRedactor()
    redactor.redact_text(raw_text)

    email = redactor.placeholder_map.get("{{EMAIL}}")
    phone = redactor.placeholder_map.get("{{PHONE}}")

    linkedin_match = URL_LINKEDIN_RE.search(raw_text)
    github_match = URL_GITHUB_RE.search(raw_text)

    # Heuristic for candidate name: first non-empty line that isn't a heading/email/URL
    inferred_name: Optional[str] = None
    for line in raw_text.splitlines():
        clean = line.strip()
        if not clean or len(clean) > 55:
            continue
        if "@" in clean or "http" in clean.lower() or "resume" in clean.lower() or "curriculum" in clean.lower():
            continue
        words = clean.split()
        if 2 <= len(words) <= 4 and all(w.replace(".", "").replace("-", "").isalpha() for w in words):
            inferred_name = clean
            break

    return {
        "full_name": inferred_name,
        "email": email,
        "phone": phone,
        "linkedin_url": linkedin_match.group(0) if linkedin_match else None,
        "github_url": github_match.group(0) if github_match else None,
    }


# ==============================================================================
# 3. CV PARSER & DYNAMIC GAP-QUESTION SERVICE
# ==============================================================================

CV_PARSER_SYSTEM_PROMPT = """You are an expert technical recruiter and deterministic CV parser.
Analyze the anonymized candidate resume text and return a strict JSON object with the following keys:
- "skills": array of strings (normalized technical & domain skills, e.g., ["Python", "TypeScript", "FastAPI", "PostgreSQL"])
- "experience_years": integer (total years of professional relevant experience)
- "work_history": array of objects, each with {"company": str, "role": str, "duration": str, "highlights": [str]}
- "education": array of objects, each with {"institution": str, "degree": str, "year": str}
- "target_roles": array of 2 to 5 job titles this candidate is best suited for
- "location": string (city/country or "Remote" if mentioned, else "")
- "linkedin_url": string (if present, else "")
- "github_url": string (if present, else "")
- "portfolio_url": string (if present, else "")
- "gap_questions": array of 4 to 6 objects, each with {"question_key": snake_case_str, "question_label": human_friendly_question_str}.

GAP ANALYSIS RULES:
Inspect the resume for critical job-application fields that are missing or ambiguous, and include 4 to 6 questions in "gap_questions" covering:
1. "work_authorization" (Legal work authorization status)
2. "visa_sponsorship_required" (Whether visa sponsorship is needed now or in the future)
3. "salary_expectation_usd" (Minimum annual base salary expectation)
4. "notice_period_days" (Notice period / earliest start availability)
5. "willing_to_relocate" (Relocation vs. remote work requirement)
6. Any role-specific experience clarification (e.g., "years_of_experience_primary" or security clearance if relevant).

Return ONLY valid JSON matching this structure."""


class CVParserService:
    def __init__(self, router: Optional[ModelRouter] = None) -> None:
        self.router = router or ModelRouter()

    async def parse_and_ingest_cv(
        self,
        user_id: str,
        pdf_bytes: bytes,
        resume_storage_path: Optional[str] = None,
        known_full_name: Optional[str] = None,
        known_email: Optional[str] = None,
        known_phone: Optional[str] = None,
        persist_to_supabase: bool = True,
    ) -> CVIngestionResult:
        """
        Executes Workflow A end-to-end:
        1. Extracts text from PDF bytes using `pymupdf` (`fitz`).
        2. Locally extracts contact metadata and builds a `PIIRedactor` so PII is stripped before LLM call.
        3. Calls `ModelRouter` (`cv_parser` -> `gemini-3.5-flash-lite`) to parse structured JSON + gap analysis.
        4. Computes the 384-d semantic embedding (`profile_embedding`).
        5. Upserts `candidate_profiles` and inserts unanswered rows into `candidate_qa_bank`.
        """
        raw_text = extract_text_from_pdf_bytes(pdf_bytes)
        local_meta = extract_local_contact_metadata(raw_text)

        full_name = known_full_name or local_meta.get("full_name")
        email = known_email or local_meta.get("email")
        phone = known_phone or local_meta.get("phone")

        redactor = PIIRedactor(
            full_name=full_name,
            email=email,
            phone=phone,
        )

        parsed_response = await self.router.complete_json(
            task_type="cv_parser",
            system_prompt=CV_PARSER_SYSTEM_PROMPT,
            user_prompt=f"Parse the following resume text and generate gap analysis questions:\n\n{raw_text}",
            response_schema=ParsedCVAndGapsSchema,
            pii_redactor=redactor,
            reinject_pii_in_output=False,
        )

        parsed_cv_json: Dict[str, Any] = {
            "skills": parsed_response.skills or [],
            "experience_years": int(parsed_response.experience_years or 0),
            "work_history": parsed_response.work_history or [],
            "education": parsed_response.education or [],
            "target_roles": parsed_response.target_roles or [],
        }

        # Ensure 4-6 gap questions exist, merging with defaults if LLM returned fewer than 4
        gap_questions = list(parsed_response.gap_questions or [])
        existing_keys = {q.get("question_key") for q in gap_questions if isinstance(q, dict)}
        for default_q in DEFAULT_GAP_QUESTIONS:
            if len(gap_questions) >= 6:
                break
            if default_q["question_key"] not in existing_keys:
                gap_questions.append(default_q)
                existing_keys.add(default_q["question_key"])

        gap_questions = gap_questions[:6]

        # Build rich semantic summary for 384-d vector embedding
        embedding_source_text = " | ".join(
            [
                "Target Roles: " + ", ".join(parsed_cv_json["target_roles"]),
                "Skills: " + ", ".join(parsed_cv_json["skills"]),
                raw_text[:3000],
            ]
        )
        profile_embedding = self.router.embed_text(embedding_source_text)

        qa_rows_to_insert = [
            {
                "user_id": user_id,
                "question_key": q["question_key"],
                "question_label": q["question_label"],
                "answer_value": "",
                "is_answered": False,
            }
            for q in gap_questions
            if q.get("question_key") and q.get("question_label")
        ]

        location = getattr(parsed_response, "location", None) or "Remote"
        linkedin_url = local_meta.get("linkedin_url") or getattr(parsed_response, "linkedin_url", None)
        github_url = local_meta.get("github_url") or getattr(parsed_response, "github_url", None)
        portfolio_url = getattr(parsed_response, "portfolio_url", None)

        if persist_to_supabase:
            self._persist_profile_and_qa_bank(
                user_id=user_id,
                full_name=full_name,
                email=email,
                phone=phone,
                location=location,
                linkedin_url=linkedin_url,
                github_url=github_url,
                portfolio_url=portfolio_url,
                resume_url=resume_storage_path,
                resume_raw_text=raw_text,
                parsed_cv_json=parsed_cv_json,
                profile_embedding=profile_embedding,
                qa_rows=qa_rows_to_insert,
            )

        return CVIngestionResult(
            user_id=user_id,
            full_name=full_name,
            email=email,
            phone=phone,
            location=location,
            linkedin_url=linkedin_url,
            github_url=github_url,
            portfolio_url=portfolio_url,
            resume_raw_text=raw_text,
            parsed_cv_json=parsed_cv_json,
            profile_embedding=profile_embedding,
            gap_questions_inserted=qa_rows_to_insert,
        )

    @staticmethod
    def _persist_profile_and_qa_bank(
        user_id: str,
        full_name: Optional[str],
        email: Optional[str],
        phone: Optional[str],
        location: Optional[str],
        linkedin_url: Optional[str],
        github_url: Optional[str],
        portfolio_url: Optional[str],
        resume_url: Optional[str],
        resume_raw_text: str,
        parsed_cv_json: Dict[str, Any],
        profile_embedding: List[float],
        qa_rows: List[Dict[str, Any]],
    ) -> None:
        try:
            supabase = get_supabase_client()
            profile_payload: Dict[str, Any] = {
                "id": user_id,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "location": location,
                "linkedin_url": linkedin_url,
                "github_url": github_url,
                "portfolio_url": portfolio_url,
                "resume_raw_text": resume_raw_text,
                "parsed_cv_json": parsed_cv_json,
                "profile_embedding": profile_embedding,
            }
            if resume_url:
                profile_payload["resume_url"] = resume_url

            supabase.table("candidate_profiles").upsert(profile_payload).execute()

            if qa_rows:
                # ignore_duplicates=True ensures already-answered questions in candidate_qa_bank
                # are NOT overwritten when a user re-uploads their CV.
                supabase.table("candidate_qa_bank").upsert(
                    qa_rows,
                    on_conflict="user_id,question_key",
                    ignore_duplicates=True,
                ).execute()
        except Exception as exc:
            logger.warning("[CVParser] Supabase persistence skipped or failed: %s", exc)
