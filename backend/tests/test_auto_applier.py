"""
Unit tests for Step 5 (`/backend/worker/auto_applier.py`):
1. Successful deterministic DOM form mapping via Groq (`openai/gpt-oss-120b`),
   local PII re-injection right before DOM fill, PDF resume file attachment,
   and PNG proof screenshot capture (`status = 'applied'`).
2. Anti-Hallucination Circuit Breaker (`NEEDS_HUMAN_INPUT`):
   When a form contains a required custom screening question not covered by the CV
   or `candidate_qa_bank`, the worker aborts submission WITHOUT clicking Submit,
   saves the new question for `candidate_qa_bank` (`is_answered = false`), and marks
   the application status as `needs_human_input`.
"""
import json
import unittest
from typing import Any, Dict, List, Optional

from backend.core.model_router import ModelRouter, ProviderEndpoint
from backend.worker.auto_applier import AutoApplierWorker


class MockBrowserDriver:
    def __init__(self, mock_schema: List[Dict[str, Any]]) -> None:
        self.mock_schema = mock_schema
        self.submitted_values: Optional[Dict[str, Any]] = None
        self.submit_clicked: bool = False
        self.closed: bool = False

    async def open_and_extract_schema(self, apply_url: str) -> List[Dict[str, Any]]:
        return self.mock_schema

    async def fill_and_submit(
        self,
        form_fields_schema: List[Dict[str, Any]],
        field_values: Dict[str, Any],
        resume_pdf_path: Optional[str] = None,
    ) -> bytes:
        self.submitted_values = dict(field_values)
        self.submit_clicked = True
        # Return valid 1x1 PNG header bytes as confirmation screenshot
        return b"\x89PNG\r\n\x1a\nMOCK_CONFIRMATION_SCREENSHOT"

    async def close(self) -> None:
        self.closed = True


class TestAutoApplierWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sample_schema = [
            {
                "selector": "#first_name",
                "tag": "input",
                "type": "text",
                "label": "First Name",
                "required": True,
            },
            {
                "selector": "#last_name",
                "tag": "input",
                "type": "text",
                "label": "Last Name",
                "required": True,
            },
            {
                "selector": "#email",
                "tag": "input",
                "type": "email",
                "label": "Email",
                "required": True,
            },
            {
                "selector": "#phone",
                "tag": "input",
                "type": "tel",
                "label": "Phone",
                "required": True,
            },
            {
                "selector": "#resume_upload",
                "tag": "input",
                "type": "file",
                "label": "Resume/CV",
                "required": True,
            },
            {
                "selector": "#work_auth",
                "tag": "select",
                "type": "select",
                "label": "Are you legally authorized to work in the United States?",
                "required": True,
                "options": [
                    {"value": "Yes", "text": "Yes"},
                    {"value": "No", "text": "No"},
                ],
            },
        ]

        self.candidate_profile = {
            "id": "00000000-0000-0000-0000-000000000001",
            "full_name": "Margaret Hamilton",
            "email": "margaret@apollo.mit.edu",
            "phone": "+1 (617) 555-1969",
            "location": "77 Massachusetts Ave",
            "linkedin_url": "https://linkedin.com/in/margarethamilton",
            "parsed_cv_json": {
                "skills": ["Python", "Playwright", "Flight Software"],
                "experience_years": 10,
                "target_roles": ["Principal Software Engineer"],
            },
        }

        self.qa_bank = [
            {
                "question_key": "work_authorization",
                "question_label": "Are you legally authorized to work in the US?",
                "answer_value": "Yes",
                "is_answered": True,
            }
        ]

        self.job_record = {
            "id": "00000000-0000-0000-0000-000000000999",
            "company_name": "Anthropic",
            "job_title": "Principal Systems Engineer",
            "apply_url": "https://boards.greenhouse.io/anthropic/jobs/999",
        }

        self.application = {
            "id": "00000000-0000-0000-0000-000000000555",
            "user_id": self.candidate_profile["id"],
            "job_id": self.job_record["id"],
            "status": "queued",
            "tailored_cover_letter": "Excited to apply to Anthropic.",
        }

    async def test_deterministic_auto_apply_reinjects_pii_and_captures_proof(self) -> None:
        used_endpoints: List[str] = []

        async def mock_groq_mapper(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            used_endpoints.append(f"{endpoint.provider}:{endpoint.model}")
            # Verify raw PII was NOT sent to Groq
            prompt_blob = " ".join(m["content"] for m in messages)
            self.assertNotIn("Margaret Hamilton", prompt_blob)
            self.assertNotIn("margaret@apollo.mit.edu", prompt_blob)

            return json.dumps(
                {
                    "mapped_fields": {
                        "#first_name": "{{FIRST_NAME}}",
                        "#last_name": "{{LAST_NAME}}",
                        "#email": "{{EMAIL}}",
                        "#phone": "{{PHONE}}",
                        "#resume_upload": "__RESUME_FILE__",
                        "#work_auth": "Yes",
                    },
                    "NEEDS_HUMAN_INPUT": None,
                }
            )

        driver = MockBrowserDriver(self.sample_schema)
        router = ModelRouter(custom_transport_handler=mock_groq_mapper)
        worker = AutoApplierWorker(router=router, driver_factory=lambda: driver)

        result = await worker.process_single_application(
            application=self.application,
            candidate_profile=self.candidate_profile,
            qa_bank_rows=self.qa_bank,
            job_record=self.job_record,
            persist_to_supabase=False,
        )

        # Verify Groq (`openai/gpt-oss-120b`) was used as primary low-latency mapper
        self.assertEqual(used_endpoints, ["groq:openai/gpt-oss-120b"])
        self.assertEqual(result.status, "applied")
        self.assertTrue(driver.submit_clicked)
        self.assertTrue(driver.closed)

        # Verify PII placeholders were re-injected locally before filling the DOM
        assert driver.submitted_values is not None
        self.assertEqual(driver.submitted_values["#first_name"], "Margaret")
        self.assertEqual(driver.submitted_values["#last_name"], "Hamilton")
        self.assertEqual(driver.submitted_values["#email"], "margaret@apollo.mit.edu")
        self.assertEqual(driver.submitted_values["#phone"], "+1 (617) 555-1969")
        self.assertEqual(driver.submitted_values["#work_auth"], "Yes")
        self.assertIsNotNone(result.proof_screenshot_url)

    async def test_needs_human_input_circuit_breaker_aborts_submission_cleanly(self) -> None:
        # Add an unknown custom screening question to the DOM schema
        schema_with_unknown_q = list(self.sample_schema) + [
            {
                "selector": "#custom_ts_sci_clearance",
                "tag": "input",
                "type": "text",
                "label": "Do you hold an active TS/SCI Polygraph clearance? Provide adjudication date.",
                "required": True,
            }
        ]

        async def mock_groq_circuit_breaker(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            return json.dumps(
                {
                    "mapped_fields": {
                        "#first_name": "{{FIRST_NAME}}",
                        "#last_name": "{{LAST_NAME}}",
                    },
                    "NEEDS_HUMAN_INPUT": "Do you hold an active TS/SCI Polygraph clearance? Provide adjudication date.",
                }
            )

        driver = MockBrowserDriver(schema_with_unknown_q)
        router = ModelRouter(custom_transport_handler=mock_groq_circuit_breaker)
        worker = AutoApplierWorker(router=router, driver_factory=lambda: driver)

        result = await worker.process_single_application(
            application=self.application,
            candidate_profile=self.candidate_profile,
            qa_bank_rows=self.qa_bank,
            job_record=self.job_record,
            persist_to_supabase=False,
        )

        # Verify circuit breaker tripped: status is 'needs_human_input', Submit was NEVER clicked,
        # and the new question is formatted for insertion into `candidate_qa_bank` (is_answered=False).
        self.assertEqual(result.status, "needs_human_input")
        self.assertFalse(driver.submit_clicked)
        self.assertIsNone(driver.submitted_values)
        self.assertTrue(driver.closed)
        self.assertEqual(len(result.needs_human_input_questions), 1)
        new_q = result.needs_human_input_questions[0]
        self.assertEqual(
            new_q["question_label"],
            "Do you hold an active TS/SCI Polygraph clearance? Provide adjudication date.",
        )
        self.assertFalse(new_q["is_answered"])


if __name__ == "__main__":
    unittest.main()
