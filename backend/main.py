"""
FastAPI Application Entrypoint + Background 4-Hour Cron Scheduler (`/backend/main.py`).
Polls all active `candidate_profiles` (`auto_apply_enabled = true`) every 4 hours
to ingest structured job feeds, run two-stage matching, and execute Playwright auto-apply.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import execute_agent_pipeline_for_user, router as api_router
from backend.core.config import settings
from backend.core.database import get_supabase_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def scheduled_discovery_and_apply_job() -> None:
    """
    Runs every `DISCOVERY_INTERVAL_HOURS` (default 4 hours).
    Finds all users with `auto_apply_enabled = true` and runs Discovery + Auto-Apply.
    """
    logger.info("[Scheduler] Starting 4-hour autonomous job discovery & auto-apply sweep...")
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("candidate_profiles")
            .select("id")
            .eq("auto_apply_enabled", True)
            .execute()
        )
        active_users = res.data or []
        logger.info("[Scheduler] Found %d active auto-apply profile(s).", len(active_users))
        for row in active_users:
            uid = str(row["id"])
            try:
                await execute_agent_pipeline_for_user(
                    user_id=uid,
                    run_discovery=True,
                    run_auto_apply=True,
                )
            except Exception as user_exc:
                logger.error("[Scheduler] Pipeline error for user %s: %s", uid, user_exc)
    except Exception as exc:
        logger.warning("[Scheduler] Sweep skipped/failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    scheduler = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            scheduled_discovery_and_apply_job,
            trigger="interval",
            hours=settings.DISCOVERY_INTERVAL_HOURS,
            id="autonomous_job_sweep",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            "[Scheduler] APScheduler started (polling every %d hours).",
            settings.DISCOVERY_INTERVAL_HOURS,
        )
    except ImportError:
        logger.warning("[Scheduler] apscheduler not installed; background cron disabled.")

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Autonomous Job Finder & Auto-Apply AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
