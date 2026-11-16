"""
Workflow C: Deterministic Playwright Auto-Apply Engine (`auto_applier.py`).
- Uses DOM extraction + structured mapping (NO slow vision-click loops).
- Opens `apply_url` in headless Playwright with realistic viewport, user-agent, and `playwright-stealth`.
- Extracts all visible interactive form elements (`input`, `textarea`, `select`, radio groups,
  checkboxes, file inputs) and their associated `<label>`, `aria-label`, `placeholder`, `name`,
  and `id` attributes into a clean JSON schema (`form_fields_schema`).
- Sends `form_fields_schema` + anonymized `parsed_cv_json` + `candidate_qa_bank` to Groq
  (`openai/gpt-oss-120b` via `ModelRouter` task `form_mapper`) with strict anti-hallucination rules.
- Circuit Breaker: If `NEEDS_HUMAN_INPUT` is returned for any required question that cannot be
  confidently answered from the CV or `candidate_qa_bank`, saves the unknown question to
  `candidate_qa_bank` (`is_answered = false`), updates `applications.status = 'needs_human_input'`,
  and aborts submission cleanly without clicking Submit.
- Otherwise, re-injects local PII (`{{CANDIDATE_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, etc.),
  uploads the PDF resume to `<input type="file">`, fills all fields deterministically,
  clicks Submit, waits for confirmation text/URL change, captures a full-page PNG screenshot
  to Supabase Storage (`proofs/`), and updates `applications.status = 'applied'`.
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from backend.core.config import settings
from backend.core.database import get_supabase_client
from backend.core.model_router import ModelRouter, PIIRedactor

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. DOM EXTRACTION JS SCRIPT & DATA STRUCTURES
# ==============================================================================

DOM_FORM_EXTRACTOR_JS = """
() => {
    const isVisible = (el) => {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            return false;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const getLabelText = (el) => {
        if (el.id) {
            const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (explicitLabel && explicitLabel.innerText.trim()) {
                return explicitLabel.innerText.trim();
            }
        }
        const parentLabel = el.closest('label');
        if (parentLabel && parentLabel.innerText.trim()) {
            return parentLabel.innerText.trim();
        }
        const fieldWrapper = el.closest('.field, .form-group, .application-question, [role="group"]');
        if (fieldWrapper) {
            const heading = fieldWrapper.querySelector('label, legend, .field-label, h4, h5');
            if (heading && heading.innerText.trim()) {
                return heading.innerText.trim();
            }
        }
        return '';
    };

    const buildSelector = (el, index) => {
        if (el.id) return `#${CSS.escape(el.id)}`;
        if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
        if (el.getAttribute('data-testid')) return `[data-testid="${CSS.escape(el.getAttribute('data-testid'))}"]`;
        return `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
    };

    const elements = Array.from(document.querySelectorAll('input, textarea, select'));
    const schema = [];

    elements.forEach((el, idx) => {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : 'text')).toLowerCase();
        if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
        // File inputs are frequently visually hidden behind styled dropzones, so keep them
        if (type !== 'file' && !isVisible(el)) return;

        let options = [];
        if (tag === 'select') {
            options = Array.from(el.options)
                .map(o => ({ value: o.value, text: o.text.trim() }))
                .filter(o => o.value !== '');
        }

        schema.push({
            selector: buildSelector(el, idx),
            tag: tag,
            type: type,
            name: el.getAttribute('name') || '',
            id: el.getAttribute('id') || '',
            label: getLabelText(el),
            aria_label: el.getAttribute('aria-label') || '',
            placeholder: el.getAttribute('placeholder') || '',
            required: el.required || el.getAttribute('aria-required') === 'true',
            options: options
        });
    });

    return schema;
}
"""


@dataclass
class AutoApplyResult:
    application_id: str
    status: str  # 'applied' | 'needs_human_input' | 'failed'
    submitted_answers_json: Dict[str, Any] = field(default_factory=dict)
    needs_human_input_questions: List[Dict[str, str]] = field(default_factory=list)
    proof_screenshot_url: Optional[str] = None
    error_log: Optional[str] = None


class BrowserDriverProtocol(Protocol):
    """
    Protocol allowing deterministic Playwright execution in production and
    fast in-memory DOM driver injection in unit tests.
    """

    async def open_and_extract_schema(self, apply_url: str) -> List[Dict[str, Any]]: ...

    async def fill_and_submit(
        self,
        form_fields_schema: List[Dict[str, Any]],
        field_values: Dict[str, Any],
        resume_pdf_path: Optional[str] = None,
    ) -> bytes: ...

    async def close(self) -> None: ...


# ==============================================================================
# 2. PLAYWRIGHT + STEALTH DRIVER IMPLEMENTATION
# ==============================================================================

class PlaywrightStealthDriver:
    """
    Production headless browser driver using `playwright.async_api` and `playwright-stealth`.
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 30000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def open_and_extract_schema(self, apply_url: str) -> List[Dict[str, Any]]:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=launch_args,
            )
        except Exception:
            self._browser = await self._pw.chromium.launch(
                channel="chrome",
                headless=self.headless,
                args=launch_args,
            )
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

        # Apply playwright-stealth if available
        try:
            from playwright_stealth import stealth_async

            await stealth_async(self._page)
        except ImportError:
            logger.info("[PlaywrightDriver] playwright-stealth not installed; using standard stealth args.")
        except Exception as exc:
            logger.warning("[PlaywrightDriver] stealth_async warning: %s", exc)

        await self._page.goto(apply_url, wait_until="domcontentloaded")
        await self._page.wait_for_timeout(1200)

        schema = await self._page.evaluate(DOM_FORM_EXTRACTOR_JS)
        return schema if isinstance(schema, list) else []

    async def fill_and_submit(
        self,
        form_fields_schema: List[Dict[str, Any]],
        field_values: Dict[str, Any],
        resume_pdf_path: Optional[str] = None,
    ) -> bytes:
        if self._page is None:
            raise RuntimeError("Page is not initialized. Call open_and_extract_schema first.")

        field_meta_by_selector = {
            f["selector"]: f for f in form_fields_schema if isinstance(f, dict) and "selector" in f
        }

        for selector, value in field_values.items():
            meta = field_meta_by_selector.get(selector, {})
            field_type = str(meta.get("type", "text")).lower()
            tag = str(meta.get("tag", "input")).lower()

            locator = self._page.locator(selector).first

            if field_type == "file":
                if resume_pdf_path and Path(resume_pdf_path).is_file():
                    await locator.set_input_files(resume_pdf_path)
                continue

            if value is None or value == "":
                continue

            if tag == "select" or field_type == "select":
                try:
                    await locator.select_option(label=str(value))
                except Exception:
                    await locator.select_option(value=str(value))
            elif field_type in ("checkbox", "radio"):
                truthy = str(value).lower() in ("true", "yes", "1", "checked", "on")
                if truthy:
                    await locator.check()
            else:
                await locator.fill(str(value))

        # Locate and click the Submit button deterministically
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit Application")',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
        ]
        clicked = False
        for sub_sel in submit_selectors:
            loc = self._page.locator(sub_sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                clicked = True
                break

        if not clicked:
            raise RuntimeError("Could not locate a visible Submit button on the application form.")

        # Wait for confirmation text or URL change
        try:
            await self._page.wait_for_function(
                """() => {
                    const body = (document.body && document.body.innerText || '').toLowerCase();
                    return body.includes('thank you') ||
                           body.includes('application submitted') ||
                           body.includes('application received') ||
                           body.includes('we have received your application') ||
                           window.location.href.includes('confirmation') ||
                           window.location.href.includes('thank');
                }""",
                timeout=12000,
            )
        except Exception:
            await self._page.wait_for_timeout(2500)

        screenshot_bytes: bytes = await self._page.screenshot(full_page=True, type="png")
        return screenshot_bytes

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception as exc:
            logger.debug("[PlaywrightDriver] Cleanup warning: %s", exc)


# ==============================================================================
# 3. GROQ FORM MAPPER PROMPT & DETERMINISTIC AUTO-APPLIER WORKER
# ==============================================================================

FORM_MAPPER_SYSTEM_PROMPT = """You are a deterministic job-application form mapping engine.
You receive:
1. `form_fields_schema`: JSON list of visible interactive DOM inputs (`selector`, `type`, `label`, `placeholder`, `required`, `options`).
2. `candidate_profile`: Anonymized candidate CV JSON (`skills`, `experience_years`, `work_history`, `education`, `target_roles`, social links) and PII placeholders (`{{CANDIDATE_NAME}}`, `{{FIRST_NAME}}`, `{{LAST_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, `{{STREET_ADDRESS}}`).
3. `candidate_qa_bank`: Key-value dictionary of verified candidate screening answers.
4. `tailored_cover_letter`: Optional cover letter text.

STRICT INSTRUCTIONS:
Return a valid JSON object with two top-level keys:
{
  "mapped_fields": {
    "<selector>": "<exact_value_to_fill>"
  },
  "NEEDS_HUMAN_INPUT": null | "<question_text>" | ["<question_text_1>", ...]
}

RULES:
1. For name, email, phone, or street address fields, output the exact PII placeholder (`{{CANDIDATE_NAME}}`, `{{FIRST_NAME}}`, `{{LAST_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, `{{STREET_ADDRESS}}`).
2. For `<input type="file">` resume upload fields, output `"__RESUME_FILE__"`.
3. ANTI-HALLUCINATION RULE (CRITICAL):
   If ANY required custom screening question (such as security clearance level, specific citizenship/export-control status, GPA, custom essay prompt, or unlisted domain experience) CANNOT be answered with 100% confidence from `candidate_profile` or `candidate_qa_bank`, DO NOT guess or fabricate an answer!
   Instead, set `"NEEDS_HUMAN_INPUT"` to the exact human-readable question text (or list of question texts) so the circuit breaker can pause the application and ask the user."""


def slugify_question_key(question_text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", question_text.lower()).strip("_")
    return clean[:64] or "custom_screening_question"


class AutoApplierWorker:
    """
    Executes Workflow C for queued job applications, enforcing daily apply limits,
    the `NEEDS_HUMAN_INPUT` circuit breaker, local PII re-injection, and screenshot proof upload.
    """

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        driver_factory: Optional[Callable[[], BrowserDriverProtocol]] = None,
    ) -> None:
        self.router = router or ModelRouter()
        self.driver_factory = driver_factory or (
            lambda: PlaywrightStealthDriver(
                headless=settings.PLAYWRIGHT_HEADLESS,
                timeout_ms=settings.PLAYWRIGHT_TIMEOUT_MS,
            )
        )

    async def process_single_application(
        self,
        application: Dict[str, Any],
        candidate_profile: Dict[str, Any],
        qa_bank_rows: List[Dict[str, Any]],
        job_record: Dict[str, Any],
        resume_pdf_path: Optional[str] = None,
        persist_to_supabase: bool = True,
    ) -> AutoApplyResult:
        app_id = str(application["id"])
        user_id = str(candidate_profile["id"])
        apply_url = str(job_record["apply_url"])

        if persist_to_supabase:
            self._update_application_status(app_id, status="applying")

        redactor = PIIRedactor(
            full_name=candidate_profile.get("full_name"),
            email=candidate_profile.get("email"),
            phone=candidate_profile.get("phone"),
            street_address=candidate_profile.get("location"),
        )

        # Build lookup of answered Q&A bank items
        answered_qa: Dict[str, str] = {
            str(row["question_key"]): str(row["answer_value"])
            for row in qa_bank_rows
            if row.get("is_answered") and str(row.get("answer_value", "")).strip()
        }

        driver = self.driver_factory()
        try:
            # 1. Open apply_url & extract visible interactive DOM form schema
            form_fields_schema = await driver.open_and_extract_schema(apply_url)
            if not form_fields_schema:
                raise RuntimeError(f"No visible interactive form inputs discovered at {apply_url}")

            # 2. Prepare anonymized payload for Groq (`openai/gpt-oss-120b`)
            anonymized_profile = {
                "full_name": "{{CANDIDATE_NAME}}",
                "first_name": "{{FIRST_NAME}}",
                "last_name": "{{LAST_NAME}}",
                "email": "{{EMAIL}}",
                "phone": "{{PHONE}}",
                "street_address": "{{STREET_ADDRESS}}",
                "linkedin_url": candidate_profile.get("linkedin_url") or "",
                "github_url": candidate_profile.get("github_url") or "",
                "portfolio_url": candidate_profile.get("portfolio_url") or "",
                "parsed_cv_json": redactor.redact_data(candidate_profile.get("parsed_cv_json") or {}),
            }

            user_prompt = (
                f"FORM_FIELDS_SCHEMA:\n{form_fields_schema}\n\n"
                f"CANDIDATE_PROFILE:\n{anonymized_profile}\n\n"
                f"CANDIDATE_QA_BANK:\n{answered_qa}\n\n"
                f"TAILORED_COVER_LETTER:\n{application.get('tailored_cover_letter') or ''}\n"
            )

            # Call Groq via ModelRouter WITHOUT reinjecting PII yet so we can inspect NEEDS_HUMAN_INPUT first
            llm_response = await self.router.complete_json(
                task_type="form_mapper",
                system_prompt=FORM_MAPPER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                pii_redactor=redactor,
                reinject_pii_in_output=False,
            )

            # 3. Check Anti-Hallucination Circuit Breaker (`NEEDS_HUMAN_INPUT`)
            missing_questions = self._extract_needs_human_input(llm_response)
            if missing_questions:
                new_qa_entries = [
                    {
                        "user_id": user_id,
                        "question_key": slugify_question_key(q_label),
                        "question_label": q_label,
                        "answer_value": "",
                        "is_answered": False,
                    }
                    for q_label in missing_questions
                ]
                if persist_to_supabase:
                    self._record_needs_human_input(
                        app_id=app_id,
                        new_qa_entries=new_qa_entries,
                        missing_questions=missing_questions,
                    )
                return AutoApplyResult(
                    application_id=app_id,
                    status="needs_human_input",
                    needs_human_input_questions=new_qa_entries,
                    error_log=f"Paused for human input: {'; '.join(missing_questions)}",
                )

            # 4. Re-inject local PII (`{{CANDIDATE_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, etc.) right before DOM fill
            raw_mapped_fields = llm_response.get("mapped_fields")
            if not isinstance(raw_mapped_fields, dict):
                raw_mapped_fields = {
                    k: v
                    for k, v in llm_response.items()
                    if k != "NEEDS_HUMAN_INPUT" and not str(k).startswith("_")
                }

            reinjected_fields: Dict[str, Any] = redactor.reinject_data(raw_mapped_fields)

            # 5. Deterministically fill DOM inputs, upload PDF resume, click Submit, & capture proof screenshot
            screenshot_bytes = await driver.fill_and_submit(
                form_fields_schema=form_fields_schema,
                field_values=reinjected_fields,
                resume_pdf_path=resume_pdf_path,
            )

            proof_url = f"proofs/{user_id}/{app_id}.png"
            if persist_to_supabase:
                proof_url = self._upload_proof_and_mark_applied(
                    user_id=user_id,
                    app_id=app_id,
                    screenshot_bytes=screenshot_bytes,
                    submitted_answers_json=reinjected_fields,
                )

            return AutoApplyResult(
                application_id=app_id,
                status="applied",
                submitted_answers_json=reinjected_fields,
                proof_screenshot_url=proof_url,
            )

        except Exception as exc:
            logger.error("[AutoApplier] Application %s failed: %s", app_id, exc)
            if persist_to_supabase:
                self._update_application_status(
                    app_id=app_id,
                    status="failed",
                    error_log=str(exc),
                )
            return AutoApplyResult(
                application_id=app_id,
                status="failed",
                error_log=str(exc),
            )
        finally:
            await driver.close()

    @staticmethod
    def _extract_needs_human_input(llm_response: Dict[str, Any]) -> List[str]:
        """
        Detects `NEEDS_HUMAN_INPUT` either at the top level of the JSON response
        or nested inside individual field mappings.
        """
        questions: List[str] = []
        top_val = llm_response.get("NEEDS_HUMAN_INPUT")
        if isinstance(top_val, str) and top_val.strip():
            questions.append(top_val.strip())
        elif isinstance(top_val, list):
            for item in top_val:
                if isinstance(item, str) and item.strip():
                    questions.append(item.strip())

        mapped = llm_response.get("mapped_fields")
        if isinstance(mapped, dict):
            for selector, val in mapped.items():
                if isinstance(val, str) and val.startswith("NEEDS_HUMAN_INPUT:"):
                    q_txt = val.split("NEEDS_HUMAN_INPUT:", 1)[1].strip() or selector
                    if q_txt not in questions:
                        questions.append(q_txt)
                elif isinstance(val, dict) and "NEEDS_HUMAN_INPUT" in val:
                    q_txt = str(val["NEEDS_HUMAN_INPUT"]).strip()
                    if q_txt and q_txt not in questions:
                        questions.append(q_txt)

        return questions

    @staticmethod
    def _record_needs_human_input(
        app_id: str,
        new_qa_entries: List[Dict[str, Any]],
        missing_questions: List[str],
    ) -> None:
        try:
            supabase = get_supabase_client()
            if new_qa_entries:
                supabase.table("candidate_qa_bank").upsert(
                    new_qa_entries,
                    on_conflict="user_id,question_key",
                    ignore_duplicates=True,
                ).execute()
            supabase.table("applications").update(
                {
                    "status": "needs_human_input",
                    "error_log": f"Paused for unanswered screening question(s): {'; '.join(missing_questions)}",
                }
            ).eq("id", app_id).execute()
        except Exception as exc:
            logger.warning("[AutoApplier] Failed to persist NEEDS_HUMAN_INPUT state: %s", exc)

    @staticmethod
    def _upload_proof_and_mark_applied(
        user_id: str,
        app_id: str,
        screenshot_bytes: bytes,
        submitted_answers_json: Dict[str, Any],
    ) -> str:
        storage_path = f"{user_id}/{app_id}.png"
        public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/proofs/{storage_path}"
        try:
            supabase = get_supabase_client()
            supabase.storage.from_("proofs").upload(
                path=storage_path,
                file=screenshot_bytes,
                file_options={"content-type": "image/png", "upsert": "true"},
            )
            public_url = supabase.storage.from_("proofs").get_public_url(storage_path)
            supabase.table("applications").update(
                {
                    "status": "applied",
                    "submitted_answers_json": submitted_answers_json,
                    "proof_screenshot_url": public_url,
                    "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "error_log": None,
                }
            ).eq("id", app_id).execute()
        except Exception as exc:
            logger.warning("[AutoApplier] Supabase storage/status update warning: %s", exc)
        return public_url

    @staticmethod
    def _update_application_status(
        app_id: str,
        status: str,
        error_log: Optional[str] = None,
    ) -> None:
        try:
            supabase = get_supabase_client()
            payload: Dict[str, Any] = {"status": status}
            if error_log is not None:
                payload["error_log"] = error_log
            supabase.table("applications").update(payload).eq("id", app_id).execute()
        except Exception as exc:
            logger.warning("[AutoApplier] Status update warning: %s", exc)
