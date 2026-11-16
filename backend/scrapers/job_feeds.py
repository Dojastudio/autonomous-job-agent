"""
Workflow B (Part 1): Zero-Cost Structured Job Feed Fetchers.
Pulls structured JSON job postings directly from public ATS boards and free remote APIs:
1. Greenhouse (`https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true`)
2. Lever (`https://api.lever.co/v0/postings/{company}?mode=json`)
3. Ashby (`https://api.ashbyhq.com/posting-api/job-board/{company}`)
4. Remotive (`https://remotive.com/api/remote-jobs`)
5. Arbeitnow (`https://www.arbeitnow.com/api/job-board-api`)
6. Himalayas (`https://himalayas.app/jobs/api?limit=50`)

Deduplicates all discovered postings via `(source, external_job_id)`.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore
    _HAS_HTTPX = False

from backend.core.config import settings

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def strip_html(raw_html: Optional[str]) -> str:
    """Unescapes HTML entities and strips HTML tags into clean plain text."""
    if not raw_html:
        return ""
    unescaped = html.unescape(raw_html)
    # Unescape a second time for double-encoded ATS descriptions (common in Greenhouse)
    unescaped = html.unescape(unescaped)
    text = HTML_TAG_RE.sub(" ", unescaped)
    return WHITESPACE_RE.sub(" ", text).strip()


def infer_remote_type(location_str: str, description_str: str = "", explicit_flag: Optional[bool] = None) -> str:
    if explicit_flag is True:
        return "remote"
    combined = f"{location_str} {description_str[:400]}".lower()
    if "remote" in combined or "anywhere" in combined or "work from home" in combined:
        if "hybrid" in combined:
            return "hybrid"
        return "remote"
    if "hybrid" in combined:
        return "hybrid"
    return "onsite"


@dataclass
class DiscoveredJobRecord:
    external_job_id: str
    source: str  # 'greenhouse' | 'lever' | 'ashby' | 'remotive' | 'arbeitnow' | 'himalayas'
    company_name: str
    job_title: str
    location: str
    remote_type: str
    apply_url: str
    description_text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JobFeedScraper:
    """
    Fetches and normalizes job listings from all 6 structured JSON sources without
    paid scraping proxies or headless browser overhead.
    """

    def __init__(
        self,
        http_timeout: float = 20.0,
        custom_json_fetcher: Optional[Callable[[str], Awaitable[Any]]] = None,
    ) -> None:
        self.http_timeout = http_timeout
        self._custom_json_fetcher = custom_json_fetcher

    async def _fetch_json(self, url: str) -> Any:
        if self._custom_json_fetcher is not None:
            return await self._custom_json_fetcher(url)

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AutonomousJobAgent/1.0)",
            "Accept": "application/json",
        }
        if _HAS_HTTPX and httpx is not None:
            async with httpx.AsyncClient(timeout=self.http_timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()

        def _sync_get() -> Any:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.http_timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        return await asyncio.to_thread(_sync_get)

    # --------------------------------------------------------------------------
    # 1. Greenhouse Public Board JSON API
    # --------------------------------------------------------------------------
    async def fetch_greenhouse_company(self, company_slug: str) -> List[DiscoveredJobRecord]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Greenhouse fetch failed for %s: %s", company_slug, exc)
            return []

        jobs_list = data.get("jobs", []) if isinstance(data, dict) else []
        results: List[DiscoveredJobRecord] = []
        for item in jobs_list:
            ext_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            apply_url = str(item.get("absolute_url", "")).strip()
            loc = str((item.get("location") or {}).get("name", "Remote")).strip()
            desc = strip_html(item.get("content", ""))
            if not (ext_id and title and apply_url):
                continue
            results.append(
                DiscoveredJobRecord(
                    external_job_id=f"{company_slug}:{ext_id}",
                    source="greenhouse",
                    company_name=company_slug.capitalize(),
                    job_title=title,
                    location=loc,
                    remote_type=infer_remote_type(loc, desc),
                    apply_url=apply_url,
                    description_text=desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # 2. Lever Public Postings JSON API
    # --------------------------------------------------------------------------
    async def fetch_lever_company(self, company_slug: str) -> List[DiscoveredJobRecord]:
        url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Lever fetch failed for %s: %s", company_slug, exc)
            return []

        if not isinstance(data, list):
            return []

        results: List[DiscoveredJobRecord] = []
        for item in data:
            ext_id = str(item.get("id", "")).strip()
            title = str(item.get("text", "")).strip()
            apply_url = str(item.get("applyUrl") or item.get("hostedUrl") or "").strip()
            categories = item.get("categories") or {}
            loc = str(categories.get("location") or "Remote").strip()
            workplace = str(item.get("workplaceType") or "").lower()
            desc_plain = str(item.get("descriptionPlain") or strip_html(item.get("description", ""))).strip()

            lists_text = " ".join(
                strip_html(lst.get("content", ""))
                for lst in (item.get("lists") or [])
                if isinstance(lst, dict)
            )
            full_desc = f"{desc_plain} {lists_text}".strip()

            if not (ext_id and title and apply_url):
                continue

            remote_type = (
                "remote"
                if "remote" in workplace
                else ("hybrid" if "hybrid" in workplace else infer_remote_type(loc, full_desc))
            )
            results.append(
                DiscoveredJobRecord(
                    external_job_id=f"{company_slug}:{ext_id}",
                    source="lever",
                    company_name=company_slug.capitalize(),
                    job_title=title,
                    location=loc,
                    remote_type=remote_type,
                    apply_url=apply_url,
                    description_text=full_desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # 3. Ashby Public Job Board API
    # --------------------------------------------------------------------------
    async def fetch_ashby_company(self, company_slug: str) -> List[DiscoveredJobRecord]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Ashby fetch failed for %s: %s", company_slug, exc)
            return []

        jobs_list = data.get("jobs", []) if isinstance(data, dict) else []
        results: List[DiscoveredJobRecord] = []
        for item in jobs_list:
            ext_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            apply_url = str(item.get("applyUrl") or item.get("jobUrl") or "").strip()
            loc = str(item.get("location") or "Remote").strip()
            is_remote = bool(item.get("isRemote", False))
            desc = str(item.get("descriptionPlain") or strip_html(item.get("descriptionHtml", ""))).strip()

            if not (ext_id and title and apply_url):
                continue

            results.append(
                DiscoveredJobRecord(
                    external_job_id=f"{company_slug}:{ext_id}",
                    source="ashby",
                    company_name=company_slug.capitalize(),
                    job_title=title,
                    location=loc,
                    remote_type=infer_remote_type(loc, desc, explicit_flag=is_remote),
                    apply_url=apply_url,
                    description_text=desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # 4. Remotive Public API
    # --------------------------------------------------------------------------
    async def fetch_remotive(self, limit: int = 50) -> List[DiscoveredJobRecord]:
        url = f"https://remotive.com/api/remote-jobs?limit={limit}"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Remotive fetch failed: %s", exc)
            return []

        jobs_list = data.get("jobs", []) if isinstance(data, dict) else []
        results: List[DiscoveredJobRecord] = []
        for item in jobs_list[:limit]:
            ext_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            company = str(item.get("company_name", "Unknown")).strip()
            loc = str(item.get("candidate_required_location") or "Remote").strip()
            apply_url = str(item.get("url", "")).strip()
            desc = strip_html(item.get("description", ""))
            if not (ext_id and title and apply_url):
                continue
            results.append(
                DiscoveredJobRecord(
                    external_job_id=ext_id,
                    source="remotive",
                    company_name=company,
                    job_title=title,
                    location=loc,
                    remote_type="remote",
                    apply_url=apply_url,
                    description_text=desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # 5. Arbeitnow Free Job Board API
    # --------------------------------------------------------------------------
    async def fetch_arbeitnow(self, limit: int = 50) -> List[DiscoveredJobRecord]:
        url = "https://www.arbeitnow.com/api/job-board-api"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Arbeitnow fetch failed: %s", exc)
            return []

        jobs_list = data.get("data", []) if isinstance(data, dict) else []
        results: List[DiscoveredJobRecord] = []
        for item in jobs_list[:limit]:
            ext_id = str(item.get("slug", "")).strip()
            title = str(item.get("title", "")).strip()
            company = str(item.get("company_name", "Unknown")).strip()
            loc = str(item.get("location") or "Remote").strip()
            is_remote = bool(item.get("remote", False))
            apply_url = str(item.get("url", "")).strip()
            desc = strip_html(item.get("description", ""))
            if not (ext_id and title and apply_url):
                continue
            results.append(
                DiscoveredJobRecord(
                    external_job_id=ext_id,
                    source="arbeitnow",
                    company_name=company,
                    job_title=title,
                    location=loc,
                    remote_type=infer_remote_type(loc, desc, explicit_flag=is_remote),
                    apply_url=apply_url,
                    description_text=desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # 6. Himalayas Free Remote Jobs API
    # --------------------------------------------------------------------------
    async def fetch_himalayas(self, limit: int = 50) -> List[DiscoveredJobRecord]:
        url = f"https://himalayas.app/jobs/api?limit={limit}"
        try:
            data = await self._fetch_json(url)
        except Exception as exc:
            logger.warning("[JobFeeds] Himalayas fetch failed: %s", exc)
            return []

        jobs_list = data.get("jobs", []) if isinstance(data, dict) else []
        results: List[DiscoveredJobRecord] = []
        for item in jobs_list[:limit]:
            ext_id = str(item.get("guid") or item.get("id") or item.get("applicationLink") or "").strip()
            title = str(item.get("title", "")).strip()
            company = str(item.get("companyName", "Unknown")).strip()
            loc_restrictions = item.get("locationRestrictions") or []
            loc = ", ".join(str(x) for x in loc_restrictions) if loc_restrictions else "Remote"
            apply_url = str(item.get("applicationLink") or item.get("url") or "").strip()
            desc = strip_html(item.get("description") or item.get("excerpt") or "")
            if not (ext_id and title and apply_url):
                continue
            results.append(
                DiscoveredJobRecord(
                    external_job_id=ext_id,
                    source="himalayas",
                    company_name=company,
                    job_title=title,
                    location=loc,
                    remote_type="remote",
                    apply_url=apply_url,
                    description_text=desc or title,
                )
            )
        return results

    # --------------------------------------------------------------------------
    # Unified Multi-Feed Aggregator with `(source, external_job_id)` Deduplication
    # --------------------------------------------------------------------------
    async def fetch_all_feeds(
        self,
        greenhouse_companies: Optional[List[str]] = None,
        lever_companies: Optional[List[str]] = None,
        ashby_companies: Optional[List[str]] = None,
        include_remote_boards: bool = True,
    ) -> List[DiscoveredJobRecord]:
        gh_slugs = greenhouse_companies if greenhouse_companies is not None else settings.greenhouse_slugs
        lv_slugs = lever_companies if lever_companies is not None else settings.lever_slugs
        ab_slugs = ashby_companies if ashby_companies is not None else settings.ashby_slugs

        tasks = []
        for slug in gh_slugs:
            tasks.append(self.fetch_greenhouse_company(slug))
        for slug in lv_slugs:
            tasks.append(self.fetch_lever_company(slug))
        for slug in ab_slugs:
            tasks.append(self.fetch_ashby_company(slug))

        if include_remote_boards:
            tasks.append(self.fetch_remotive())
            tasks.append(self.fetch_arbeitnow())
            tasks.append(self.fetch_himalayas())

        batches = await asyncio.gather(*tasks, return_exceptions=True)

        deduped: Dict[Tuple[str, str], DiscoveredJobRecord] = {}
        for batch in batches:
            if isinstance(batch, Exception) or not isinstance(batch, list):
                continue
            for job in batch:
                key = (job.source, job.external_job_id)
                if key not in deduped:
                    deduped[key] = job

        return list(deduped.values())
