from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx

from app.core.config import get_settings


INTERVIEW_SIGNAL_TERMS = (
    "面经",
    "面试",
    "一面",
    "二面",
    "三面",
    "笔试",
    "追问",
    "offer",
)


@dataclass
class InterviewExperienceSearchResult:
    source: str
    title: str
    url: str | None
    snippet: str
    raw_text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class InterviewExperienceSource:
    name = "base"

    async def search(self, *, query: str, limit: int) -> list[InterviewExperienceSearchResult]:
        raise NotImplementedError


class HtmlInterviewExperienceSource(InterviewExperienceSource):
    """Best-effort public HTML probe for interview experience platforms.

    These sources are intentionally used only by source smoke evaluation. They
    do not bypass login, anti-bot or client-side rendering restrictions.
    """

    search_url: str
    query_param: str = "q"
    extra_params: dict[str, str] = {}

    async def search(self, *, query: str, limit: int) -> list[InterviewExperienceSearchResult]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent}
        params = {self.query_param: query, **self.extra_params}
        async with httpx.AsyncClient(timeout=settings.job_search_timeout_seconds, headers=headers) as client:
            response = await client.get(self.search_url, params=params)
            response.raise_for_status()
        return self._extract_html_results(response.text, query=query, limit=limit)

    def _extract_html_results(
        self,
        html: str,
        *,
        query: str,
        limit: int,
    ) -> list[InterviewExperienceSearchResult]:
        results: list[InterviewExperienceSearchResult] = []
        for href, raw_title in re.findall(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, flags=re.I | re.S):
            title = self._clean_html(raw_title)
            if not title:
                continue
            url = urljoin(self.search_url, unescape(href))
            snippet = self._nearby_snippet(html, raw_title)
            haystack = f"{title} {url} {snippet}".lower()
            if not self._looks_like_interview_hit(haystack, query):
                continue
            results.append(
                InterviewExperienceSearchResult(
                    source=self.name,
                    title=title[:180],
                    url=url,
                    snippet=snippet[:260],
                    payload={"mode": "public_html_anchor"},
                )
            )
            if len(results) >= limit:
                break
        return self._dedupe(results)

    def _looks_like_interview_hit(self, haystack: str, query: str) -> bool:
        signal_hit = any(term.lower() in haystack for term in INTERVIEW_SIGNAL_TERMS)
        query_tokens = self._query_tokens(query)
        query_hit = any(token in haystack for token in query_tokens)
        return signal_hit and query_hit

    def _query_tokens(self, query: str) -> list[str]:
        tokens = [part.lower() for part in re.split(r"[\s,，/]+", query) if len(part.strip()) >= 2]
        if not tokens:
            tokens = ["面经"]
        return tokens

    def _clean_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value)
        text = unescape(text)
        return " ".join(text.split())

    def _nearby_snippet(self, html: str, needle: str) -> str:
        index = html.find(needle)
        if index < 0:
            return ""
        start = max(index - 180, 0)
        end = min(index + len(needle) + 220, len(html))
        return self._clean_html(html[start:end])

    def _dedupe(self, rows: list[InterviewExperienceSearchResult]) -> list[InterviewExperienceSearchResult]:
        seen: set[str] = set()
        deduped: list[InterviewExperienceSearchResult] = []
        for row in rows:
            key = row.url or row.title
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped


class NowcoderInterviewSource(HtmlInterviewExperienceSource):
    name = "nowcoder"
    search_url = "https://www.nowcoder.com/search"
    query_param = "query"
    extra_params = {"type": "post"}


class OfferShowInterviewSource(HtmlInterviewExperienceSource):
    name = "offershow"
    search_url = "https://www.offershow.cn/search"
    query_param = "keyword"


class XiaohongshuInterviewSource(HtmlInterviewExperienceSource):
    name = "xiaohongshu"
    search_url = "https://www.xiaohongshu.com/search_result"
    query_param = "keyword"


class InterviewExperienceSourceRegistry:
    def __init__(self) -> None:
        self.sources: dict[str, InterviewExperienceSource] = {
            NowcoderInterviewSource.name: NowcoderInterviewSource(),
            OfferShowInterviewSource.name: OfferShowInterviewSource(),
            XiaohongshuInterviewSource.name: XiaohongshuInterviewSource(),
        }

    def select(self, names: list[str] | None = None) -> list[InterviewExperienceSource]:
        if not names:
            return list(self.sources.values())
        return [self.sources[name] for name in names if name in self.sources]
