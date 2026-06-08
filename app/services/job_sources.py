from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.job_relevance import is_query_relevant_posting, rank_postings_for_query, source_posting_haystack


@dataclass
class JobPosting:
    source: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    job_type: str | None
    apply_url: str | None
    raw_jd_text: str
    payload: dict[str, Any] = field(default_factory=dict)


class JobSource:
    name = "base"

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        raise NotImplementedError


class TencentCareersSource(JobSource):
    name = "tencent"
    endpoint = "https://careers.tencent.com/tencentcareer/api/post/Query"

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent}
        params = {
            "pageIndex": 1,
            "pageSize": min(max(limit * 3, 20), 50),
            "keyword": query,
            "language": "zh-cn",
            "area": "cn",
        }
        async with httpx.AsyncClient(timeout=settings.job_search_timeout_seconds, headers=headers) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
        data = response.json()
        posts = data.get("Data", {}).get("Posts", []) if isinstance(data, dict) else []
        results: list[JobPosting] = []
        for post in posts:
            title = str(post.get("RecruitPostName") or post.get("PostName") or "").strip()
            if not title:
                continue
            city = str(post.get("LocationName") or "").strip() or None
            if location and city and location.lower() not in city.lower():
                continue

            post_id = str(post.get("PostId") or post.get("RecruitPostId") or title)
            responsibility = str(post.get("Responsibility") or "").strip()
            requirement = str(post.get("Requirement") or "").strip()
            raw_jd = "\n\n".join(part for part in [title, responsibility, requirement] if part)
            apply_url = f"https://careers.tencent.com/jobdesc.html?postId={post_id}"
            results.append(
                JobPosting(
                    source=self.name,
                    external_id=post_id,
                    title=title,
                    company="Tencent",
                    location=city,
                    job_type=str(post.get("CategoryName") or "").strip() or None,
                    apply_url=apply_url,
                    raw_jd_text=raw_jd,
                    payload=post,
                )
            )
        return rank_postings_for_query(results, query)[:limit]


class LeverCareersSource(JobSource):
    name = "lever"

    def __init__(self, company_slugs: list[str] | None = None) -> None:
        self.company_slugs = company_slugs or get_settings().lever_slugs

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent}
        query_tokens = [token.lower() for token in query.split() if token.strip()]
        postings: list[JobPosting] = []
        per_company_limit = max(4, limit // max(len(self.company_slugs), 1) + 2)
        async with httpx.AsyncClient(timeout=settings.job_search_timeout_seconds, headers=headers) as client:
            for slug in self.company_slugs:
                url = f"https://api.lever.co/v0/postings/{slug}"
                try:
                    response = await client.get(url, params={"mode": "json"})
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    rows = response.json()
                except Exception:
                    continue
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    posting = self._map_row(slug, row)
                    if query_tokens and not is_query_relevant_posting(posting, query):
                        continue
                    haystack = source_posting_haystack(posting)
                    if location and location.lower() not in haystack:
                        continue
                    postings.append(posting)
                    if len(postings) >= per_company_limit * len(self.company_slugs):
                        break
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, slug: str, row: dict[str, Any]) -> JobPosting:
        categories = row.get("categories") or {}
        lists = row.get("lists") or []
        list_text = []
        for item in lists:
            if isinstance(item, dict):
                content = item.get("content") or ""
                if content:
                    list_text.append(str(content))
        description = "\n\n".join(
            str(part)
            for part in [row.get("descriptionPlain"), row.get("description"), *list_text]
            if part
        )
        external_id = str(row.get("id") or row.get("hostedUrl") or row.get("text") or "")
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=str(row.get("text") or "").strip(),
            company=slug,
            location=str(categories.get("location") or "").strip() or None,
            job_type=str(categories.get("commitment") or categories.get("team") or "").strip() or None,
            apply_url=str(row.get("hostedUrl") or row.get("applyUrl") or "").strip() or None,
            raw_jd_text=description.strip() or str(row.get("text") or ""),
            payload=row,
        )


class JobSourceRegistry:
    def __init__(self) -> None:
        settings = get_settings()
        self.sources: dict[str, JobSource] = {}
        if settings.tencent_careers_enabled:
            self.sources[TencentCareersSource.name] = TencentCareersSource()
        if settings.lever_careers_enabled:
            self.sources[LeverCareersSource.name] = LeverCareersSource()

    def select(self, names: list[str] | None = None) -> list[JobSource]:
        if not names:
            return list(self.sources.values())
        return [self.sources[name] for name in names if name in self.sources]
