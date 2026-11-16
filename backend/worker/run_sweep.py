"""
CLI Entrypoint for GitHub Actions 4-Hour Autonomous Job Discovery & Playwright Auto-Apply Sweep.
Can be triggered by:
1. GitHub Actions `schedule` (every 4 hours) -> sweeps all `auto_apply_enabled = true` profiles.
2. GitHub Actions `workflow_dispatch` (manual trigger from Vercel UI) -> runs immediately for `--user-id`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from backend.api.routes import execute_agent_pipeline_for_user
from backend.core.database import get_supabase_client
from backend.main import scheduled_discovery_and_apply_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Autonomous Job Discovery & Playwright Auto-Apply Worker."
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default="",
        help="Optional specific candidate user UUID to run for (otherwise sweeps all active profiles).",
    )
    args = parser.parse_args()

    target_user_id = args.user_id.strip()
    if target_user_id:
        logger.info("[WorkerCLI] Running Discovery + Auto-Apply for user_id=%s", target_user_id)
        result = await execute_agent_pipeline_for_user(
            user_id=target_user_id,
            run_discovery=True,
            run_auto_apply=True,
        )
        logger.info("[WorkerCLI] Completed pipeline: %s", result)
        return 0

    # Otherwise sweep all active profiles (`auto_apply_enabled = true`), or fallback to default demo user
    supabase = get_supabase_client()
    res = (
        supabase.table("candidate_profiles")
        .select("id")
        .eq("auto_apply_enabled", True)
        .execute()
    )
    active_users = res.data or []
    if not active_users:
        logger.info("[WorkerCLI] No active profiles found with auto_apply_enabled=true.")
        return 0

    await scheduled_discovery_and_apply_job()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
