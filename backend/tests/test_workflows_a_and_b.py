"""
Unit tests for Step 4 (`/backend/services/cv_parser.py`, `/backend/scrapers/job_feeds.py`,
and `/backend/services/job_ingestor.py`):
1. Workflow A: PDF CV extraction, local contact detection, PII redaction before LLM parsing,
   384-d profile embedding generation, and 4–6 dynamic gap-analysis questions in `candidate_qa_bank`.
2. Workflow B (Part 1): Structured JSON feed parsing across Greenhouse, Lever, Ashby,
   Remotive, Arbeitnow, and Himalayas + `(source, external_job_id)` deduplication.
3. Workflow B (Part 2): Stage 1 mathematical vector filter (`similarity >= 0.55` passes,
   irrelevant jobs `< 0.55` discarded without calling LLM) + Stage 2 LLM scoring and
   application queueing when `score >= user.min_match_score`.
"""
import json
import unittest
from typing import Any, Dict, List

from backend.core.model_router import ModelRouter, ProviderEndpoint
from backend.scrapers.job_feeds import DiscoveredJobRecord, JobFeedScraper
from backend.services.cv_parser import CVParserService
from backend.services.job_ingestor import JobIngestorService


class TestWorkflowsAAndB(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_a_cv_ingestion_and_gap_question_generator(self) -> None:
        captured_prompts: List[str] = []

        async def mock_cv_llm(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            combined = " ".join(m["content"] for m in messages)
            captured_prompts.append(combined)
            return json.dumps(
                {
                    "skills": ["Python", "FastAPI", "Playwright", "PostgreSQL", "Next.js"],
                    "experience_years": 6,
                    "work_history": [
                        {
                            "company": "Acme AI",
                            "role": "Senior Backend Engineer",
                            "duration": "2021 - Present",
                            "highlights": ["Built distributed Playwright workers"],
                        }
                    ],
                    "education": [
                        {
                            "institution": "MIT",
                            "degree": "B.S. Computer Science",
                            "year": "2020",
                        }
                    ],
                    "target_roles": ["Senior Backend Engineer", "AI Engineer"],
                    "location": "San Francisco, CA",
                    "gap_questions": [
                        {
                            "question_key": "work_authorization",
                            "question_label": "Are you legally authorized to work in the US?",
                        },
                        {
                            "question_key": "visa_sponsorship_required",
                            "question_label": "Will you require H-1B or visa sponsorship?",
                        },
                        {
                            "question_key": "salary_expectation_usd",
                            "question_label": "What is your target base salary in USD?",
                        },
                        {
                            "question_key": "notice_period_days",
                            "question_label": "How many days notice do you require?",
                        },
                    ],
                }
            )

        router = ModelRouter(custom_transport_handler=mock_cv_llm)
        service = CVParserService(router=router)

        sample_pdf_bytes = (
            b"Alan Turing\n"
            b"alan.turing@bletchley.org | +1 (415) 890-1234\n"
            b"https://linkedin.com/in/alanturing | https://github.com/alanturing\n"
            b"Senior Backend Engineer with 6 years experience in Python, FastAPI, Playwright, PostgreSQL."
        )

        result = await service.parse_and_ingest_cv(
            user_id="00000000-0000-0000-0000-000000000001",
            pdf_bytes=sample_pdf_bytes,
            persist_to_supabase=False,
        )

        # 1. Verify PII was extracted locally for the profile, but redacted from the LLM prompt
        self.assertEqual(result.full_name, "Alan Turing")
        self.assertEqual(result.email, "alan.turing@bletchley.org")
        self.assertEqual(result.phone, "+1 (415) 890-1234")
        self.assertNotIn("alan.turing@bletchley.org", captured_prompts[0])
        self.assertIn("{{EMAIL}}", captured_prompts[0])

        # 2. Verify 384-d embedding and parsed_cv_json structure
        self.assertEqual(len(result.profile_embedding), 384)
        self.assertIn("FastAPI", result.parsed_cv_json["skills"])
        self.assertEqual(result.parsed_cv_json["experience_years"], 6)

        # 3. Verify 4-6 unanswered gap questions were generated for candidate_qa_bank
        self.assertGreaterEqual(len(result.gap_questions_inserted), 4)
        self.assertLessEqual(len(result.gap_questions_inserted), 6)
        self.assertTrue(all(q["is_answered"] is False for q in result.gap_questions_inserted))

    async def test_workflow_b_feed_parsers_and_deduplication(self) -> None:
        async def mock_fetcher(url: str) -> Any:
            if "greenhouse.io" in url:
                return {
                    "jobs": [
                        {
                            "id": 101,
                            "title": "Senior Python AI Engineer",
                            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/101",
                            "location": {"name": "Remote - US"},
                            "content": "<p>Build FastAPI &amp; Playwright agents.</p>",
                        },
                        # Duplicate ID in same feed to verify deduplication
                        {
                            "id": 101,
                            "title": "Senior Python AI Engineer",
                            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/101",
                            "location": {"name": "Remote - US"},
                            "content": "<p>Build FastAPI &amp; Playwright agents.</p>",
                        },
                    ]
                }
            if "lever.co" in url:
                return [
                    {
                        "id": "lev-202",
                        "text": "Staff Full-Stack Engineer",
                        "applyUrl": "https://jobs.lever.co/mistral/lev-202/apply",
                        "categories": {"location": "Paris / Remote"},
                        "workplaceType": "remote",
                        "descriptionPlain": "TypeScript, Next.js, Python, Postgres.",
                    }
                ]
            if "ashbyhq.com" in url:
                return {
                    "jobs": [
                        {
                            "id": "ash-303",
                            "title": "Applied AI Engineer",
                            "applyUrl": "https://jobs.ashbyhq.com/ramp/ash-303/application",
                            "location": "New York, NY",
                            "isRemote": True,
                            "descriptionPlain": "LLM routing and browser automation.",
                        }
                    ]
                }
            if "remotive.com" in url:
                return {
                    "jobs": [
                        {
                            "id": 404,
                            "title": "Backend Platform Engineer",
                            "company_name": "RemotiveCorp",
                            "candidate_required_location": "Worldwide",
                            "url": "https://remotive.com/remote-jobs/404",
                            "description": "<p>Python and PostgreSQL</p>",
                        }
                    ]
                }
            if "arbeitnow.com" in url:
                return {
                    "data": [
                        {
                            "slug": "arb-505",
                            "title": "Infrastructure Engineer",
                            "company_name": "BerlinTech",
                            "location": "Berlin",
                            "remote": True,
                            "url": "https://arbeitnow.com/jobs/arb-505",
                            "description": "<p>Docker &amp; Python</p>",
                        }
                    ]
                }
            if "himalayas.app" in url:
                return {
                    "jobs": [
                        {
                            "guid": "him-606",
                            "title": "Machine Learning Engineer",
                            "companyName": "HimalayaAI",
                            "locationRestrictions": ["Worldwide"],
                            "applicationLink": "https://himalayas.app/jobs/him-606",
                            "description": "<p>Vector search with pgvector.</p>",
                        }
                    ]
                }
            return {}

        scraper = JobFeedScraper(custom_json_fetcher=mock_fetcher)
        jobs = await scraper.fetch_all_feeds(
            greenhouse_companies=["stripe"],
            lever_companies=["mistral"],
            ashby_companies=["ramp"],
            include_remote_boards=True,
        )

        # 6 unique jobs across all 6 sources (duplicate Greenhouse 101 collapsed)
        self.assertEqual(len(jobs), 6)
        sources = {j.source for j in jobs}
        self.assertEqual(
            sources,
            {"greenhouse", "lever", "ashby", "remotive", "arbeitnow", "himalayas"},
        )

    async def test_workflow_b_two_stage_vector_and_llm_matching(self) -> None:
        llm_scored_titles: List[str] = []

        async def mock_scorer_llm(
            endpoint: ProviderEndpoint, messages: List[Dict[str, str]], temperature: float
        ) -> str:
            user_msg = messages[-1]["content"]
            llm_scored_titles.append(user_msg)
            return json.dumps(
                {
                    "match_score": 91,
                    "match_reasoning": "Direct match on Python, FastAPI, Playwright, and PostgreSQL.",
                    "tailored_cover_letter": "Dear Hiring Team, Best regards, {{CANDIDATE_NAME}}",
                }
            )

        router = ModelRouter(custom_transport_handler=mock_scorer_llm)
        ingestor = JobIngestorService(router=router, stage1_min_similarity=0.55)

        candidate_text = (
            "Senior Backend Engineer Python FastAPI Playwright PostgreSQL Docker Supabase"
        )
        candidate_profile = {
            "id": "00000000-0000-0000-0000-000000000001",
            "full_name": "Katherine Johnson",
            "email": "kjohnson@nasa.gov",
            "location": "Remote",
            "min_match_score": 75,
            "parsed_cv_json": {
                "target_roles": ["Senior Backend Engineer"],
                "skills": ["Python", "FastAPI", "Playwright", "PostgreSQL", "Docker", "Supabase"],
                "experience_years": 7,
            },
            "profile_embedding": ModelRouter.deterministic_hash_embedding(candidate_text, dim=384),
        }

        discovered_jobs = [
            # Job 1: High semantic overlap (> 0.55 cosine similarity) -> Should pass Stage 1 & Stage 2
            DiscoveredJobRecord(
                external_job_id="stripe:101",
                source="greenhouse",
                company_name="Stripe",
                job_title="Senior Backend Engineer",
                location="Remote",
                remote_type="remote",
                apply_url="https://boards.greenhouse.io/stripe/jobs/101",
                description_text="Senior Backend Engineer Python FastAPI Playwright PostgreSQL Docker Supabase",
            ),
            # Job 2: Completely unrelated domain (< 0.55 cosine similarity) -> Must be discarded in Stage 1 without LLM call
            DiscoveredJobRecord(
                external_job_id="hospital:999",
                source="lever",
                company_name="City Hospital",
                job_title="Orthopedic Veterinary Surgeon",
                location="Miami, FL",
                remote_type="onsite",
                apply_url="https://jobs.lever.co/hospital/999",
                description_text="Veterinary surgery, equine dentistry, clinical pathology, animal anesthesia.",
            ),
        ]

        summary = await ingestor.score_and_queue_for_candidate(
            candidate_profile=candidate_profile,
            discovered_jobs=discovered_jobs,
            persist_to_supabase=False,
        )

        self.assertEqual(summary.jobs_fetched, 2)
        self.assertEqual(summary.jobs_passed_stage1_vector, 1)
        self.assertEqual(summary.jobs_discarded_stage1_vector, 1)
        # Verify LLM was ONLY called once (for the job that passed Stage 1 math filter)
        self.assertEqual(summary.jobs_scored_stage2_llm, 1)
        self.assertEqual(len(llm_scored_titles), 1)
        # Verify application was queued with score 91 and PII re-injected in cover letter
        self.assertEqual(len(summary.applications_queued), 1)
        queued_app = summary.applications_queued[0]
        self.assertEqual(queued_app["match_score"], 91)
        self.assertEqual(queued_app["status"], "queued")
        self.assertIn("Katherine Johnson", queued_app["tailored_cover_letter"])


if __name__ == "__main__":
    unittest.main()
