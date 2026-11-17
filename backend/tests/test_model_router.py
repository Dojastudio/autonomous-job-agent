"""
Unit tests for `/backend/core/model_router.py`:
1. PII Redaction & Re-injection (`{{CANDIDATE_NAME}}`, `{{EMAIL}}`, `{{PHONE}}`, `{{STREET_ADDRESS}}`)
2. Exponential Backoff on 429 Rate Limit & Automatic Multi-Provider Failover
3. Strict JSON extraction & Schema validation
4. 384-dimensional Semantic Vector Embeddings & Cosine Similarity

Compatible with both `pytest` and standard library `python3 -m unittest`.
"""
import asyncio
import unittest
from typing import Dict, List

from backend.core.model_router import (
    BaseModel,
    ModelRouter,
    ModelRouterError,
    PIIRedactor,
    ProviderEndpoint,
    RateLimitError,
    extract_clean_json,
)


class DummyJobScoreSchema(BaseModel):
    match_score: int
    match_reasoning: str
    candidate_greeting: str


class TestModelRouter(unittest.IsolatedAsyncioTestCase):
    def test_pii_redaction_and_local_reinjection(self) -> None:
        redactor = PIIRedactor(
            full_name="Ada Lovelace",
            email="ada.lovelace@analyticalengine.org",
            phone="+1 (415) 555-0199",
            street_address="42 Babbage Way",
        )

        raw_cv_text = (
            "Candidate: Ada Lovelace\n"
            "Contact: ada.lovelace@analyticalengine.org | +1 (415) 555-0199\n"
            "Address: 42 Babbage Way, London\n"
            "Experience: 2020 - 2024 Senior Distributed Systems Engineer."
        )

        redacted = redactor.redact_text(raw_cv_text)

        # Verify zero raw PII remains in text sent to free-tier LLMs
        self.assertNotIn("Ada Lovelace", redacted)
        self.assertNotIn("ada.lovelace@analyticalengine.org", redacted)
        self.assertNotIn("+1 (415) 555-0199", redacted)
        self.assertNotIn("42 Babbage Way", redacted)

        # Verify placeholders were injected and employment dates were preserved
        self.assertIn("{{CANDIDATE_NAME}}", redacted)
        self.assertIn("{{EMAIL}}", redacted)
        self.assertIn("{{PHONE}}", redacted)
        self.assertIn("{{STREET_ADDRESS}}", redacted)
        self.assertIn("2020 - 2024", redacted)

        # Simulate LLM form-mapper returning placeholders for DOM inputs
        llm_mapped_payload = {
            "#full_name": "{{CANDIDATE_NAME}}",
            "#first_name": "{{FIRST_NAME}}",
            "#last_name": "{{LAST_NAME}}",
            "#email": "{{EMAIL}}",
            "#phone": "{{PHONE}}",
            "#address": "{{STREET_ADDRESS}}",
        }

        restored = redactor.reinject_data(llm_mapped_payload)
        self.assertEqual(restored["#full_name"], "Ada Lovelace")
        self.assertEqual(restored["#first_name"], "Ada")
        self.assertEqual(restored["#last_name"], "Lovelace")
        self.assertEqual(restored["#email"], "ada.lovelace@analyticalengine.org")
        self.assertEqual(restored["#phone"], "+1 (415) 555-0199")
        self.assertEqual(restored["#address"], "42 Babbage Way")

    async def test_router_429_backoff_and_provider_failover(self) -> None:
        call_log: List[str] = []

        async def mock_transport(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            call_log.append(f"{endpoint.provider}:{endpoint.model}")
            # Verify PII was stripped from outbound prompt before reaching any provider
            combined_prompt = " ".join(m["content"] for m in messages)
            self.assertNotIn("grace@hopper.dev", combined_prompt)
            self.assertIn("{{EMAIL}}", combined_prompt)

            # Primary provider hits 429 rate limits on all attempts
            if endpoint.provider == "gemini":
                raise RateLimitError("429 Resource Exhausted")

            # Fallback provider (OpenRouter) succeeds with JSON wrapped in markdown fences
            return """```json
            {
                "match_score": 88,
                "match_reasoning": "Strong Python & Playwright architecture alignment.",
                "candidate_greeting": "Contact {{CANDIDATE_NAME}} at {{EMAIL}}"
            }
            ```"""

        router = ModelRouter(
            max_retries_per_endpoint=2,
            base_backoff_seconds=0.01,
            custom_transport_handler=mock_transport,
        )
        redactor = PIIRedactor(full_name="Grace Hopper", email="grace@hopper.dev")

        result = await router.complete_json(
            task_type="job_scorer",
            system_prompt="Score this candidate against the job description.",
            user_prompt="Candidate Grace Hopper (grace@hopper.dev) has 8 years of Python.",
            response_schema=DummyJobScoreSchema,
            pii_redactor=redactor,
            reinject_pii_in_output=True,
        )

        # Primary Gemini endpoint retried twice, then failed over to OpenRouter
        self.assertEqual(
            call_log,
            [
                "gemini:gemini-2.5-flash-lite",
                "gemini:gemini-2.5-flash-lite",
                "groq:openai/gpt-oss-120b",
            ],
        )
        self.assertIsInstance(result, DummyJobScoreSchema)
        self.assertEqual(result.match_score, 88)
        self.assertEqual(result.candidate_greeting, "Contact Grace Hopper at grace@hopper.dev")

    async def test_router_raises_when_all_providers_exhausted(self) -> None:
        async def failing_transport(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            raise RateLimitError("429 Quota exceeded everywhere")

        router = ModelRouter(
            max_retries_per_endpoint=1,
            base_backoff_seconds=0.005,
            custom_transport_handler=failing_transport,
        )

        with self.assertRaises(ModelRouterError) as ctx:
            await router.complete_json(
                task_type="form_mapper",
                system_prompt="Map fields.",
                user_prompt="Fields: ...",
            )
        self.assertIn("All providers failed for task 'form_mapper'", str(ctx.exception))

    def test_deterministic_384d_embeddings_and_similarity(self) -> None:
        vec_python_a = ModelRouter.deterministic_hash_embedding(
            "Senior Backend Engineer Python FastAPI PostgreSQL Playwright Docker", dim=384
        )
        vec_python_b = ModelRouter.deterministic_hash_embedding(
            "Backend Software Engineer Python FastAPI PostgreSQL Automation", dim=384
        )
        vec_unrelated = ModelRouter.deterministic_hash_embedding(
            "Registered Nurse Pediatric ICU Hospital Healthcare Patient Care", dim=384
        )

        self.assertEqual(len(vec_python_a), 384)
        self.assertEqual(len(vec_python_b), 384)
        self.assertEqual(len(vec_unrelated), 384)

        sim_related = ModelRouter.cosine_similarity(vec_python_a, vec_python_b)
        sim_unrelated = ModelRouter.cosine_similarity(vec_python_a, vec_unrelated)

        self.assertGreater(sim_related, 0.55)
        self.assertGreater(sim_related, sim_unrelated)

    def test_extract_clean_json_handles_fences_and_chatter(self) -> None:
        raw = 'Here is the JSON result:\n```json\n{"status": "ok", "score": 92}\n```\nThank you!'
        parsed = extract_clean_json(raw)
        self.assertEqual(parsed, {"status": "ok", "score": 92})


if __name__ == "__main__":
    unittest.main()
