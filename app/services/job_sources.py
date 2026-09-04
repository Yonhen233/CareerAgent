from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.services.job_relevance import (
    is_query_relevant_posting,
    rank_postings_for_query,
    source_posting_haystack,
)


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


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "li", "div"} and self.parts and self.parts[-1] != "\n":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(part for part in self.parts if part != "\n").strip()


class _TCLJobListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.item_depth = 0
        self.capture: str | None = None
        self.capture_depth = 0
        self.tag_index = 0
        self.tag_section_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if self.current is None and tag == "div" and "proInfoConList" in classes:
            self.current = {}
            self.item_depth = 1
            self.tag_index = 0
            return
        if self.current is None:
            return
        self.item_depth += 1
        if tag == "div" and "head" in classes:
            self.current["external_id"] = values.get("data-postid", "").strip()
        if tag == "div" and "name" in classes:
            self.capture = "title"
            self.capture_depth = self.item_depth
        elif tag == "div" and "tag" in classes:
            self.tag_section_depth = self.item_depth
        elif tag == "span" and self.tag_section_depth:
            self.capture = f"tag_{self.tag_index}"
            self.capture_depth = self.item_depth
            self.tag_index += 1
        elif tag == "a" and "tool-btn" in classes:
            self.current["apply_url"] = values.get("href", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture and self.capture_depth == self.item_depth:
            self.capture = None
            self.capture_depth = 0
        if self.tag_section_depth == self.item_depth:
            self.tag_section_depth = 0
        self.item_depth -= 1
        if self.item_depth == 0:
            self.rows.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None or not self.capture:
            return
        text = data.strip()
        if text:
            self.current[self.capture] = " ".join(
                part for part in [self.current.get(self.capture, ""), text] if part
            )


class _ChinaTelecomListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.item_depth = 0
        self.capture: str | None = None
        self.capture_depth = 0
        self.first_row_depth = 0
        self.detail_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if self.current is None and tag == "li" and "position_list-list-demo" in classes:
            self.current = {"row_parts": [], "details": []}
            self.item_depth = 1
            return
        if self.current is None:
            return
        if tag in {"br", "img", "input", "meta", "link", "hr"}:
            if tag == "br" and self.detail_depth:
                self.current.setdefault("current_detail", []).append("\n")
            return
        self.item_depth += 1
        onclick = values.get("onclick", "")
        if "toDetailPostUrl(" in onclick and "onclick" not in self.current:
            self.current["onclick"] = onclick
        if tag == "div" and "position_list-list-demo-title" in classes:
            self.capture = "title"
            self.capture_depth = self.item_depth
        elif tag == "div" and "position_list-first-row" in classes:
            self.first_row_depth = self.item_depth
        elif tag == "span" and self.first_row_depth:
            self.capture = "row_part"
            self.capture_depth = self.item_depth
        elif tag == "div" and "detailedInformation" in classes:
            self.detail_depth = self.item_depth
            self.capture = "detail"
            self.capture_depth = self.item_depth
            self.current["current_detail"] = []

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag in {"br", "img", "input", "meta", "link", "hr"}:
            return
        if self.capture and self.capture_depth == self.item_depth and self.capture != "detail":
            self.capture = None
            self.capture_depth = 0
        if self.first_row_depth == self.item_depth:
            self.first_row_depth = 0
        if self.detail_depth == self.item_depth:
            detail = "".join(self.current.pop("current_detail", [])).strip()
            if detail:
                self.current["details"].append(detail)
            self.detail_depth = 0
            self.capture = None
            self.capture_depth = 0
        self.item_depth -= 1
        if self.item_depth == 0:
            self.rows.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None or not self.capture:
            return
        text = data.strip()
        if not text:
            return
        if self.capture == "title":
            self.current["title"] = " ".join(
                part for part in [self.current.get("title", ""), text] if part
            )
        elif self.capture == "row_part":
            self.current["row_parts"].append(text)
        elif self.capture == "detail":
            self.current.setdefault("current_detail", []).append(text)


class _HuaweiAIListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        href = values.get("href", "")
        if "social-recruitment-detail.html" in href:
            self.hrefs.append(href)


class _HeadingSummaryParser(HTMLParser):
    """Extract adjacent h3/p cards without depending on presentation classes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.capture: str | None = None
        self.parts: list[str] = []
        self.pending_title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h3", "p"}:
            self.capture = tag
            self.parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag != self.capture:
            return
        text = " ".join(self.parts).strip()
        if tag == "h3":
            self.pending_title = text or None
        elif self.pending_title and text:
            self.rows.append({"title": self.pending_title, "summary": text})
            self.pending_title = None
        self.capture = None
        self.parts = []

    def handle_data(self, data: str) -> None:
        if self.capture and data.strip():
            self.parts.append(data.strip())

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


class BaiduCareersSource(JobSource):
    """Baidu Careers SSR source with public internship postings and full JD fields."""

    name = "baidu"
    endpoint = "https://talent.baidu.com/jobs/list"
    initial_data_marker = "window.__INITIAL_DATA__ ="

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }
        params = {
            "recruitType": "INTERN",
            "search": query,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()

        initial_data = self._extract_initial_data(response.text)
        rows = initial_data.get("listData", {}).get("listDetailData", [])
        if not isinstance(rows, list):
            raise ValueError("Baidu Careers response does not contain listData.listDetailData")

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            posting = self._map_row(row)
            if not posting.title or posting.external_id in seen:
                continue
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            seen.add(posting.external_id)
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _extract_initial_data(self, html: str) -> dict[str, Any]:
        marker_index = html.find(self.initial_data_marker)
        if marker_index < 0:
            raise ValueError("Baidu Careers SSR marker window.__INITIAL_DATA__ is missing")
        raw = html[marker_index + len(self.initial_data_marker) :]
        # The page occasionally emits JavaScript's undefined literal inside an
        # otherwise JSON-compatible object.
        raw = raw.replace(":undefined,", ":null,").replace(":undefined}", ":null}")
        value, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        if not isinstance(value, dict):
            raise ValueError("Baidu Careers SSR payload is not an object")
        return value

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        title = str(row.get("name") or "").strip()
        post_id = str(row.get("postId") or row.get("jobId") or title).strip()
        location = str(row.get("workPlace") or "").strip() or None
        project_type = str(row.get("projectType") or "").strip()
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "百度"),
            ("工作地点", location),
            ("招聘项目", project_type),
            ("岗位职责", row.get("workContent")),
            ("任职要求", row.get("serviceCondition")),
            ("业务组", row.get("bgShortName") or row.get("orgName")),
            ("发布日期", row.get("publishDate")),
        )
        return JobPosting(
            source=self.name,
            external_id=post_id,
            title=title,
            company="百度",
            location=location,
            job_type=project_type or "实习生招聘",
            apply_url=f"https://talent.baidu.com/jobs/detail/INTERN/{post_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class MeituanCareersSource(JobSource):
    """Meituan Careers public JSON search plus concurrent detail enrichment."""

    name = "meituan"
    search_endpoint = "https://zhaopin.meituan.com/api/official/job/getJobList"
    detail_endpoint = "https://zhaopin.meituan.com/api/official/job/getJobDetail"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Origin": "https://zhaopin.meituan.com",
            "Referer": "https://zhaopin.meituan.com/web/position/list",
        }
        request_size = min(max(limit * 3, 20), 50)
        body = {
            "page": {"pageNo": 1, "pageSize": request_size},
            "keywords": query,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.post(self.search_endpoint, json=body)
            response.raise_for_status()
            payload = response.json()
            rows = self._extract_rows(payload)
            preliminary = [self._map_row(row) for row in rows if isinstance(row, dict)]
            preliminary = [
                posting
                for posting in preliminary
                if posting.title
                and posting.external_id
                and (not location or location.lower() in source_posting_haystack(posting))
            ]
            selected = rank_postings_for_query(preliminary, query)[:limit]

            semaphore = asyncio.Semaphore(min(max(settings.job_ingest_concurrency, 1), 8))

            async def _load_detail(posting: JobPosting) -> JobPosting:
                async with semaphore:
                    detail_response = await client.post(
                        self.detail_endpoint,
                        json={"jobUnionId": posting.external_id},
                    )
                    detail_response.raise_for_status()
                    detail_payload = detail_response.json()
                    detail = detail_payload.get("data") if isinstance(detail_payload, dict) else None
                    if (
                        not isinstance(detail_payload, dict)
                        or detail_payload.get("status") != 1
                        or not isinstance(detail, dict)
                    ):
                        message = detail_payload.get("message") if isinstance(detail_payload, dict) else None
                        raise ValueError(
                            f"Meituan Careers detail failed for {posting.external_id}: "
                            f"{message or 'missing data'}"
                        )
                    return self._map_row(detail)

            enriched = await asyncio.gather(*[_load_detail(posting) for posting in selected])
        return rank_postings_for_query(enriched, query)[:limit]

    def _extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or payload.get("status") != 1:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ValueError(f"Meituan Careers search failed: {message or 'invalid response'}")
        data = payload.get("data")
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Meituan Careers response does not contain data.list")
        return rows

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("jobUnionId") or "").strip()
        title = str(row.get("name") or "").strip()
        locations = [
            str(item.get("name") or "").strip()
            for item in row.get("cityList") or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        departments = [
            str(item.get("name") or "").strip()
            for item in row.get("department") or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        job_type_code = str(row.get("jobType") or "").strip()
        special_code = str(row.get("jobSpecialCode") or "").strip()
        if "实习" in title or special_code in {"3", "6", "8"}:
            job_type = "实习/校园招聘"
            highlight_type = "campus"
        elif job_type_code == "2":
            job_type = "校园招聘"
            highlight_type = "campus"
        else:
            job_type = "社会招聘"
            highlight_type = "social"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "美团"),
            ("工作地点", "、".join(locations)),
            ("招聘类型", job_type),
            ("职位类别", " / ".join(part for part in [row.get("jobFamily"), row.get("jobFamilyGroup")] if part)),
            ("所属部门", "、".join(departments)),
            ("岗位职责", row.get("jobDuty")),
            ("任职要求", row.get("jobRequirement")),
            ("部门介绍", row.get("departmentIntro")),
            ("岗位亮点", row.get("highLight")),
            ("其他信息", row.get("otherInfo")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="美团",
            location="、".join(locations) or None,
            job_type=job_type,
            apply_url=(
                "https://zhaopin.meituan.com/web/position/detail"
                f"?jobUnionId={external_id}&highlightType={highlight_type}"
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class ByteDanceCareersSource(JobSource):
    """ByteDance campus source backed by the official page's signed JSON request."""

    name = "bytedance"
    search_page = "https://jobs.bytedance.com/campus/position"
    search_api_path = "/api/v1/search/job/posts"

    def __init__(
        self,
        payload_loader: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.payload_loader = payload_loader

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        loader = self.payload_loader or self._load_signed_payload
        payload = await loader(_job_source_search_key(query), min(max(limit * 3, 20), 50))
        if not isinstance(payload, dict) or payload.get("code") != 0:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ValueError(f"ByteDance Careers search failed: {message or 'invalid response'}")
        data = payload.get("data")
        rows = data.get("job_post_list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("ByteDance Careers response does not contain data.job_post_list")

        postings: list[JobPosting] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            posting = self._map_row(row)
            if not posting.title or not posting.external_id:
                continue
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    async def _load_signed_payload(self, query: str, request_size: int) -> dict[str, Any]:
        settings = get_settings()
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "ByteDance Careers requires Playwright and a Chromium browser. "
                "Install them with `pip install playwright` and `playwright install chromium`."
            ) from exc

        page_query = urlencode({"keywords": query, "current": 1, "limit": request_size})
        page_url = f"{self.search_page}?{page_query}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=settings.job_source_browser_headless)
            page = await browser.new_page(user_agent=settings.user_agent)
            try:
                async with page.expect_response(
                    lambda response: self.search_api_path in response.url and response.status == 200,
                    timeout=settings.job_source_browser_timeout_ms,
                ) as response_info:
                    await page.goto(
                        page_url,
                        wait_until="domcontentloaded",
                        timeout=settings.job_source_browser_timeout_ms,
                    )
                response = await response_info.value
                payload = await response.json()
            finally:
                await browser.close()
        if not isinstance(payload, dict):
            raise ValueError("ByteDance Careers signed response is not an object")
        return payload

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        city_rows = row.get("city_list") or []
        cities = [
            str(item.get("i18n_name") or item.get("name") or "").strip()
            for item in city_rows
            if isinstance(item, dict)
            and str(item.get("i18n_name") or item.get("name") or "").strip()
        ]
        if not cities and isinstance(row.get("city_info"), dict):
            city = str(
                row["city_info"].get("i18n_name")
                or row["city_info"].get("name")
                or ""
            ).strip()
            if city:
                cities.append(city)
        recruit_type = _nested_text(row.get("recruit_type"), "i18n_name", "name")
        subject_name = _nested_text(row.get("job_subject"), "name.i18n", "name.zh_cn")
        category = _nested_text(row.get("job_category"), "i18n_name", "name")
        job_type = " / ".join(part for part in [subject_name, recruit_type] if part) or "校园招聘"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "字节跳动"),
            ("工作地点", "、".join(cities)),
            ("招聘类型", job_type),
            ("职位类别", category),
            ("岗位职责", row.get("description")),
            ("任职要求", row.get("requirement")),
            ("岗位编号", row.get("code")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="字节跳动",
            location="、".join(cities) or None,
            job_type=job_type,
            apply_url=f"https://jobs.bytedance.com/campus/position/{external_id}/detail",
            raw_jd_text=raw_jd,
            payload=row,
        )


class AlibabaCareersSource(JobSource):
    """Alibaba campus source with dynamic internship-batch discovery."""

    name = "alibaba"
    landing_page = "https://campus-talent.alibaba.com/campus/position"
    batch_endpoint = "https://campus-talent.alibaba.com/searchCondition/listBatch"
    search_endpoint = "https://campus-talent.alibaba.com/position/search"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        search_key = _job_source_search_key(query)
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Origin": "https://campus-talent.alibaba.com",
            "Referer": self.landing_page,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            landing = await client.get(self.landing_page)
            landing.raise_for_status()
            csrf_token = client.cookies.get("XSRF-TOKEN")
            if not csrf_token:
                raise ValueError("Alibaba Careers did not issue an XSRF-TOKEN cookie")
            batch_rows = await self._load_internship_batches(client, csrf_token)
            if not batch_rows:
                raise ValueError("Alibaba Careers did not return any active internship batches")

            request_size = min(max(limit * 3, 20), 50)

            async def _search_batch(batch: dict[str, Any]) -> list[JobPosting]:
                batch_id = int(batch["id"])
                response = await client.post(
                    self.search_endpoint,
                    params={"_csrf": csrf_token},
                    json={
                        "batchId": batch_id,
                        "searchKey": search_key,
                        "pageIndex": 1,
                        "pageSize": request_size,
                        "customDeptCode": "",
                        "channel": "campus_group_official_site",
                        "language": "zh",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload.get("content") if isinstance(payload, dict) else None
                if payload.get("success") is not True or not isinstance(content, dict):
                    raise ValueError(
                        f"Alibaba Careers search failed for batch {batch_id}: "
                        f"{payload.get('errorMsg') or 'missing content.datas'}"
                    )
                rows = content.get("datas") or []
                if not isinstance(rows, list):
                    raise ValueError(
                        f"Alibaba Careers search failed for batch {batch_id}: "
                        "content.datas is not a list"
                    )
                return [
                    self._map_row(row, batch_id=batch_id)
                    for row in rows
                    if isinstance(row, dict)
                ]

            batch_results = await asyncio.gather(*[_search_batch(batch) for batch in batch_rows])

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for posting in (item for rows in batch_results for item in rows):
            if not posting.title or not posting.external_id or posting.external_id in seen:
                continue
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            seen.add(posting.external_id)
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    async def _load_internship_batches(
        self,
        client: httpx.AsyncClient,
        csrf_token: str,
    ) -> list[dict[str, Any]]:
        response = await client.post(
            self.batch_endpoint,
            params={"_csrf": csrf_token},
            json={"channel": "campus_group_official_site", "language": "zh"},
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("content") if isinstance(payload, dict) else None
        rows = content.get("internship") if isinstance(content, dict) else None
        if payload.get("success") is not True or not isinstance(rows, list):
            raise ValueError(
                "Alibaba Careers batch discovery failed: "
                f"{payload.get('errorMsg') or 'missing content.internship'}"
            )
        return [
            row
            for row in rows
            if isinstance(row, dict) and row.get("id") is not None
        ]

    def _map_row(self, row: dict[str, Any], *, batch_id: int) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("name") or "").strip()
        locations = [
            str(item).strip()
            for item in row.get("workLocations") or []
            if str(item).strip()
        ]
        businesses = [
            str(item).strip()
            for item in row.get("circleNames") or []
            if str(item).strip()
        ]
        batch_name = str(row.get("batchName") or "").strip()
        category = str(row.get("categoryName") or "").strip()
        job_type = " / ".join(part for part in [batch_name, category] if part) or "实习生招聘"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "阿里巴巴"),
            ("工作地点", "、".join(locations)),
            ("招聘批次", batch_name),
            ("职位类别", category),
            ("在招业务", "、".join(businesses)),
            ("岗位职责", row.get("description")),
            ("任职要求", row.get("requirement")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="阿里巴巴",
            location="、".join(locations) or None,
            job_type=job_type,
            apply_url=(
                f"https://campus-talent.alibaba.com/campus/position/{external_id}"
                f"?batchId={batch_id}"
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class JDCareersSource(JobSource):
    """JD Careers public JSON search with complete social-job descriptions."""

    name = "jd"
    endpoint = "https://zhaopin.jd.com/web/job/job_list"
    landing_page = "https://zhaopin.jd.com/web/job/job_info_list/3"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://zhaopin.jd.com",
            "Referer": self.landing_page,
            "X-Requested-With": "XMLHttpRequest",
        }
        request_size = min(max(limit * 3, 20), 50)
        form = {
            "pageIndex": "0",
            "pageSize": str(request_size),
            "workCityJson": "[]",
            "jobTypeJson": "[]",
            "jobSearch": _job_source_search_key(query),
            "depTypeJson": "[]",
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.post(self.endpoint, data=form)
            response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError("JD Careers response is not a job list")

        postings: list[JobPosting] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            posting = self._map_row(row)
            if not posting.title or not posting.external_id:
                continue
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("requirementId") or row.get("reqNumber") or "").strip()
        title = str(row.get("positionNameOpen") or row.get("positionName") or "").strip()
        location = str(row.get("workCity") or "").strip() or None
        job_type = str(row.get("jobType") or "").strip() or "社会招聘"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "京东"),
            ("工作地点", location),
            ("招聘类型", job_type),
            ("所属部门", row.get("positionDeptName") or row.get("reqDepartment")),
            ("岗位职责", row.get("workContent")),
            ("任职要求", row.get("qualification")),
            ("岗位编号", row.get("reqNumber") or row.get("positionCode")),
            ("发布日期", row.get("formatPublishTime")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="京东",
            location=location,
            job_type=job_type,
            apply_url=(
                f"https://zhaopin.jd.com/web/job-info-detail?requementId={external_id}"
                if external_id
                else None
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class ChinaTelecomCareersSource(JobSource):
    """China Telecom campus source with complete JD content in search results."""

    name = "china_telecom"
    endpoint = "https://wejob.chinatelecom.com.cn/wt/TELE/mobweb/v8/position/list"
    landing_page = f"{endpoint}?brandCode=1&recruitType=1&request_locale=zh_CN"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Origin": "https://wejob.chinatelecom.com.cn",
            "Referer": self.landing_page,
        }
        form = {
            "workPlaceCode": "",
            "workPlaceCodeName": "",
            "cityName": "地点",
            "postTypeCode": "",
            "keyWord": _job_source_search_key(query),
            "recruitType": "1",
            "brandCode": "1",
            "fromCamChooseSearchType": "false",
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.post(
                self.endpoint,
                params={"brandCode": "1", "request_locale": "zh_CN"},
                data=form,
            )
            response.raise_for_status()

        postings = self._parse_results(response.text)
        postings = [
            posting
            for posting in postings
            if (not location or location.lower() in source_posting_haystack(posting))
            and is_query_relevant_posting(posting, query)
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _parse_results(self, html: str) -> list[JobPosting]:
        parser = _ChinaTelecomListParser()
        parser.feed(html)
        postings: list[JobPosting] = []
        for item in parser.rows:
            title = str(item.get("title") or "").strip()
            onclick = str(item.get("onclick") or "")
            match = re.search(r"toDetailPostUrl\((\d+),(\d+),(\d+)\)", onclick)
            if not title or not match:
                continue
            external_id, recruit_type, current_page = match.groups()
            row_parts = [str(part).strip() for part in item.get("row_parts") or [] if str(part).strip()]
            company = row_parts[0] if row_parts else "中国电信"
            job_location = row_parts[1] if len(row_parts) > 1 else None
            fields: dict[str, str] = {}
            for detail in item.get("details") or []:
                text = str(detail).strip()
                label, separator, value = text.partition(":")
                if not separator:
                    label, separator, value = text.partition("：")
                if separator and value.strip():
                    fields[label.strip()] = value.strip()
            job_type = fields.get("招聘项目") or "校园招聘"
            raw_jd = _join_jd_sections(
                ("岗位名称", title),
                ("公司", company),
                ("工作地点", job_location),
                ("招聘类型", job_type),
                ("职位类别", fields.get("职位类别")),
                ("学历要求", fields.get("学历要求")),
                ("专业要求", fields.get("专业要求")),
                ("岗位职责", fields.get("工作描述")),
                ("任职要求", fields.get("职位要求")),
            )
            apply_url = (
                f"{self.endpoint.rsplit('/list', 1)[0]}/detail?safe=Y&canBack=true"
                f"&recruitType={recruit_type}&postIdsAry={external_id}"
                f"&postCanApply=0&entityPage.currentPage={current_page}&brandCode=1"
            )
            postings.append(
                JobPosting(
                    source=self.name,
                    external_id=external_id,
                    title=title,
                    company=company,
                    location=job_location,
                    job_type=job_type,
                    apply_url=apply_url,
                    raw_jd_text=raw_jd,
                    payload={"fields": fields, "recruit_type": recruit_type},
                )
            )
        return postings


class HuaweiCareersSource(JobSource):
    """Huawei official AI job zone plus public JSON detail endpoint."""

    name = "huawei"
    landing_page = "https://career.huawei.com/reccampportal/portal5/social-recruitment-ai.html"
    detail_endpoint = (
        "https://career.huawei.com/reccampportal/services/portal/portalpub/getJobDetail/newHr"
    )

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/html, */*",
            "Referer": self.landing_page,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.get(self.landing_page)
            response.raise_for_status()
            candidates = self._parse_candidates(response.text)
            semaphore = asyncio.Semaphore(min(max(settings.job_ingest_concurrency, 1), 8))

            async def _load(candidate: dict[str, str]) -> JobPosting:
                async with semaphore:
                    detail_response = await client.get(
                        self.detail_endpoint,
                        params={
                            "jobId": candidate["external_id"],
                            "dataSource": candidate["data_source"],
                        },
                    )
                    detail_response.raise_for_status()
                    payload = detail_response.json()
                    if not isinstance(payload, dict) or not payload.get("jobname"):
                        raise ValueError(
                            f"Huawei Careers detail failed for {candidate['external_id']}"
                        )
                    return self._map_detail(payload, candidate)

            postings = await asyncio.gather(*[_load(candidate) for candidate in candidates])

        postings = [
            posting
            for posting in postings
            if (not location or location.lower() in source_posting_haystack(posting))
            and is_query_relevant_posting(posting, query)
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _parse_candidates(self, html: str) -> list[dict[str, str]]:
        parser = _HuaweiAIListParser()
        parser.feed(html)
        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        for href in parser.hrefs:
            match = re.search(r"jobId=(\d+)(?:&amp;|&).*?dataSource=(\d+)", href)
            if not match:
                match = re.search(r"jobId=(\d+)", href)
            if not match:
                continue
            external_id = match.group(1)
            if external_id in seen:
                continue
            seen.add(external_id)
            candidates.append(
                {
                    "external_id": external_id,
                    "data_source": match.group(2) if match.lastindex and match.lastindex >= 2 else "1",
                }
            )
        if not candidates:
            raise ValueError("Huawei Careers AI zone contains no job detail links")
        return candidates

    def _map_detail(self, row: dict[str, Any], candidate: dict[str, str]) -> JobPosting:
        external_id = str(row.get("jobId") or candidate["external_id"]).strip()
        title = str(row.get("jobname") or row.get("externalJobName") or "").strip()
        location = str(row.get("jobArea") or row.get("jobAddress") or "").strip() or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "华为"),
            ("工作地点", location),
            ("招聘类型", "社会招聘 / AI 岗位专区"),
            ("所属部门", row.get("deptName")),
            ("职位族", row.get("jobFamilyName")),
            ("岗位职责", row.get("mainBusiness")),
            ("任职要求", row.get("jobRequire")),
            ("岗位编号", row.get("jobCode")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="华为",
            location=location,
            job_type="社会招聘 / AI 岗位专区",
            apply_url=(
                "https://career.huawei.com/reccampportal/portal5/"
                f"social-recruitment-detail.html?dataSource={candidate['data_source']}"
                f"&jobId={external_id}"
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class IFlytekCareersSource(JobSource):
    """iFlytek public Beisen API across social, campus and internship categories."""

    name = "iflytek"
    endpoint = "https://iflytek.zhiye.com/api/Jobad/GetJobAdPageList"
    landing_page = "https://iflytek.zhiye.com/"
    category_routes = {"1": "social", "2": "campus", "3": "intern"}

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://iflytek.zhiye.com",
            "Referer": self.landing_page,
        }
        request_size = min(max(limit * 3, 20), 50)
        search_key = _job_source_search_key(query)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        self.endpoint,
                        json={
                            "PageIndex": 0,
                            "PageSize": request_size,
                            "Category": [category_id],
                            "KeyWords": search_key,
                            "SpecialType": 0,
                            "PortalId": "",
                            "DisplayFields": [
                                "Category",
                                "Kind",
                                "LocId",
                                "ClassificationOne",
                                "WorkWeChatQrCode",
                            ],
                        },
                    )
                    for category_id in self.category_routes
                ]
            )

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or int(payload.get("Code") or 0) != 200:
                raise ValueError("iFlytek Careers search returned an invalid response")
            # The upstream currently reports Total=0 while Data is populated.
            # Data is therefore the authoritative collection contract.
            rows = payload.get("Data")
            if not isinstance(rows, list):
                raise ValueError("iFlytek Careers response does not contain Data list")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                posting = self._map_row(row)
                if not posting.title or not posting.external_id or posting.external_id in seen:
                    continue
                if location and location.lower() not in source_posting_haystack(posting):
                    continue
                if not is_query_relevant_posting(posting, query):
                    continue
                seen.add(posting.external_id)
                postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("Id") or row.get("JobAdId") or "").strip()
        title = str(row.get("JobAdName") or "").strip()
        category_id = str(row.get("CategoryId") or "").strip()
        category = str(row.get("Category") or "").strip()
        locations = [str(value).strip() for value in row.get("LocNames") or [] if str(value).strip()]
        job_type = " / ".join(
            part for part in [category, str(row.get("Kind") or "").strip()] if part
        ) or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "科大讯飞"),
            ("工作地点", "、".join(locations)),
            ("招聘类型", job_type),
            ("职位类别", row.get("ClassificationOne")),
            ("岗位职责", row.get("Duty")),
            ("任职要求", row.get("Require")),
            ("发布日期", row.get("ChangeDate")),
        )
        route = self.category_routes.get(category_id, "jobs")
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="科大讯飞",
            location="、".join(locations) or None,
            job_type=job_type,
            apply_url=f"https://iflytek.zhiye.com/{route}/jobdetail/{external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class TCLCareersSource(JobSource):
    """TCL campus source backed by its public search and detail endpoints."""

    name = "tcl"
    landing_page = "https://zhaopin.tcl.com/campus/recruiting.html?id=57"
    search_endpoint = "https://zhaopin.tcl.com/Ajax/campus_search.html"
    detail_endpoint = "https://zhaopin.tcl.com/Ajax/job_detail.html"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://zhaopin.tcl.com",
            "Referer": self.landing_page,
            "X-Requested-With": "XMLHttpRequest",
        }
        form = {
            "keyType": "1",
            "cate_id": "100",
            "keys": _job_source_search_key(query),
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.post(self.search_endpoint, params={"page": 1}, data=form)
            response.raise_for_status()
            payload = response.json()
            rows = self._extract_rows(payload)
            preliminary = [self._map_list_row(row) for row in rows]
            preliminary = [
                posting
                for posting in preliminary
                if posting.title
                and posting.external_id
                and (not location or location.lower() in source_posting_haystack(posting))
            ]
            selected = rank_postings_for_query(preliminary, query)[:limit]
            semaphore = asyncio.Semaphore(min(max(settings.job_ingest_concurrency, 1), 8))

            async def _load_detail(posting: JobPosting) -> JobPosting:
                async with semaphore:
                    detail_response = await client.post(
                        self.detail_endpoint,
                        params={"postid": posting.external_id, "recruitType": "1"},
                    )
                    detail_response.raise_for_status()
                    detail = detail_response.json()
                    if not isinstance(detail, dict) or detail.get("title") != "success":
                        raise ValueError(
                            f"TCL Careers detail failed for {posting.external_id}: invalid response"
                        )
                    return self._merge_detail(posting, detail)

            postings = await asyncio.gather(*[_load_detail(posting) for posting in selected])
        return rank_postings_for_query(postings, query)[:limit]

    def _extract_rows(self, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict) or payload.get("title") != "success":
            raise ValueError("TCL Careers search failed: invalid response")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("TCL Careers response does not contain HTML content")
        parser = _TCLJobListParser()
        parser.feed(content)
        return [
            {
                "external_id": row.get("external_id", ""),
                "title": row.get("title", ""),
                "company": row.get("tag_0", "TCL"),
                "location": row.get("tag_1", ""),
                "category": row.get("tag_2", ""),
                "headcount": row.get("tag_3", ""),
                "published_at": row.get("tag_4", ""),
                "apply_url": row.get("apply_url", ""),
            }
            for row in parser.rows
        ]

    def _map_list_row(self, row: dict[str, str]) -> JobPosting:
        raw_jd = _join_jd_sections(
            ("岗位名称", row.get("title")),
            ("公司", row.get("company") or "TCL"),
            ("工作地点", row.get("location")),
            ("招聘类型", "校园招聘"),
            ("职位类别", row.get("category")),
            ("招聘人数", row.get("headcount")),
            ("发布日期", row.get("published_at")),
        )
        return JobPosting(
            source=self.name,
            external_id=row.get("external_id", ""),
            title=row.get("title", ""),
            company=row.get("company") or "TCL",
            location=row.get("location") or None,
            job_type="校园招聘",
            apply_url=row.get("apply_url") or None,
            raw_jd_text=raw_jd,
            payload=dict(row),
        )

    def _merge_detail(self, posting: JobPosting, detail: dict[str, Any]) -> JobPosting:
        posting.raw_jd_text = _join_jd_sections(
            ("岗位名称", posting.title),
            ("公司", posting.company),
            ("工作地点", posting.location),
            ("招聘类型", posting.job_type),
            ("职位类别", posting.payload.get("category")),
            ("岗位职责", _html_text(detail.get("workContent"))),
            ("任职要求", _html_text(detail.get("serviceCondition"))),
            ("发布日期", posting.payload.get("published_at")),
        )
        posting.payload = {**posting.payload, "detail": detail}
        return posting


class MideaCareersSource(JobSource):
    """Midea public recruitment API with complete social-job descriptions."""

    name = "midea"
    endpoint = "https://recruit.midea.com/backend/rec/home/out/official/position/list"
    landing_page = "https://recruit.midea.com/recruitOut/ihr/social/socialHome"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://recruit.midea.com",
            "Referer": self.landing_page,
        }
        search_keys = _job_source_search_keys(query)[:3]
        request_size = min(max(limit * 2, 10), 50)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        self.endpoint,
                        data={
                            "pageSize": str(request_size),
                            "pageIndex": "1",
                            "publicationName": key,
                        },
                    )
                    for key in search_keys
                ]
            )

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Midea Careers response does not contain data list")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                posting = self._map_row(row)
                if not posting.title or not posting.external_id or posting.external_id in seen:
                    continue
                if location and location.lower() not in source_posting_haystack(posting):
                    continue
                seen.add(posting.external_id)
                postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("positionId") or row.get("demandCode") or "").strip()
        title = str(row.get("publicationName") or row.get("demandPositionName") or "").strip()
        location = str(row.get("workingPlace") or "").strip() or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "美的集团"),
            ("工作地点", location),
            ("招聘类型", "社会招聘"),
            ("所属事业部", row.get("superiorUnitName")),
            ("组织信息", row.get("unitFullInfo")),
            ("岗位职责", row.get("postDuties")),
            ("任职要求", row.get("qualification")),
            ("学历要求", row.get("education")),
            ("岗位编号", row.get("demandCode")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="美的集团",
            location=location,
            job_type="社会招聘",
            apply_url=(
                "https://recruit.midea.com/recruitOut/ihr/social/jobApplication"
                f"?positionId={external_id}"
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class XiaomiCareersSource(JobSource):
    """Xiaomi's public recruitment search API used by its opportunities page."""

    name = "xiaomi"
    endpoint = "https://hr.xiaomi.com/website/api/agent/searchJobPage"
    landing_page = "https://hr.xiaomi.com/website/opportunities.html"
    job_type_names = {1: "社会招聘", 2: "校园招聘", 3: "实习", 4: "顶尖人才"}

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Referer": self.landing_page,
        }
        search_keys = _job_source_search_keys(query)[:3]
        request_size = min(max(limit * 3, 10), 50)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.get(
                        self.endpoint,
                        params={
                            "keyword": key,
                            "cityZhNames": location or "",
                            "pageSize": request_size,
                            "pageNum": 1,
                        },
                    )
                    for key in search_keys
                ]
            )

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = data.get("list") if isinstance(data, dict) else None
            if payload.get("code") != 0 or not isinstance(rows, list):
                raise ValueError(str(payload.get("message") or "Xiaomi Careers response is invalid"))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                posting = self._map_row(row)
                if not posting.title or not posting.external_id or posting.external_id in seen:
                    continue
                if location and location.lower() not in source_posting_haystack(posting):
                    continue
                seen.add(posting.external_id)
                postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("jobPostId") or row.get("jobId") or row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        cities = [str(city).strip() for city in row.get("cityZhNames") or [] if str(city).strip()]
        job_type = self.job_type_names.get(row.get("type"), str(row.get("type") or "").strip()) or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "小米集团"),
            ("工作地点", "、".join(cities)),
            ("招聘类型", job_type),
            ("一级部门", row.get("levelOneDeptName")),
            ("岗位职责", row.get("description")),
            ("任职要求", row.get("requirement")),
            ("发布日期", row.get("publishTime")),
            ("岗位编号", row.get("larkJobCode")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="小米集团",
            location="、".join(cities) or None,
            job_type=job_type,
            apply_url=str(row.get("url") or "").strip() or None,
            raw_jd_text=raw_jd,
            payload=row,
        )


class OPPOCareersSource(JobSource):
    """OPPO campus jobs from the public API backing its official careers site."""

    name = "oppo"
    endpoint = "https://careers.oppo.com/openapi/position/pageNew"
    landing_page = "https://careers.oppo.com/university/oppo/campus"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Referer": self.landing_page,
        }
        search_keys = _job_source_search_keys(query)[:3]
        request_size = min(max(limit * 3, 10), 50)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        self.endpoint,
                        json={
                            "pageNum": 1,
                            "pageSize": request_size,
                            "positionName": key,
                            "projectList": [],
                            "positionTypeList": [],
                            "workCityCodeList": [],
                            "shareId": "",
                        },
                    )
                    for key in search_keys
                ]
            )

        postings: list[JobPosting] = []
        seen: set[str] = set()
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = data.get("records") if isinstance(data, dict) else None
            if payload.get("code") != 0 or not isinstance(rows, list):
                raise ValueError(str(payload.get("msg") or "OPPO Careers response is invalid"))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                posting = self._map_row(row)
                if not posting.title or not posting.external_id or posting.external_id in seen:
                    continue
                if location and location.lower() not in source_posting_haystack(posting):
                    continue
                seen.add(posting.external_id)
                postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(
            row.get("idProjPosition")
            or row.get("idRecruitPosition")
            or row.get("projectPositionId")
            or ""
        ).strip()
        title = str(row.get("positionName") or row.get("projectPositionName") or "").strip()
        location = str(row.get("workCityName") or "").strip() or None
        job_type = str(
            row.get("recruitmentTypeName") or row.get("projectName") or ""
        ).strip() or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "OPPO"),
            ("工作地点", location),
            ("招聘项目", row.get("projectName")),
            ("招聘类型", job_type),
            ("职位描述", row.get("positionDesc") or row.get("projectPositionDesc")),
            ("任职要求", row.get("positionRequire") or row.get("projectPositionRequire")),
            ("知识技能", row.get("knowledgeSkill")),
            ("加分项", row.get("bonusItem")),
            ("AI 能力等级", row.get("aiCapabilityLevelDesc")),
            ("发布日期", row.get("releaseTime")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="OPPO",
            location=location,
            job_type=job_type,
            apply_url=f"{self.landing_page}/post/{external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class SkyworthCareersSource(JobSource):
    """Skyworth campus jobs from its official Dayee/HotJob recruitment site."""

    name = "skyworth"
    root_url = "https://skyworth.hotjob.cn"
    suite_endpoint = f"{root_url}/wecruit/common/getSLD"
    endpoint_base = "https://skyworth.hotjob.cn/wecruit/positionInfo"
    landing_page = f"{root_url}/"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": self.landing_page,
        }
        search_keys = _job_source_search_keys(query)[:3]
        request_size = min(max(limit * 3, 15), 45)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            suite_key = await self._discover_suite_key(client)
            list_url = f"{self.endpoint_base}/listPosition/SU{suite_key}"
            responses = await asyncio.gather(
                *[
                    client.post(
                        list_url,
                        data={
                            "isFrompb": "true",
                            "recruitType": "1",
                            "pageSize": str(request_size),
                            "currentPage": "1",
                            "postKey": key,
                        },
                    )
                    for key in search_keys
                ]
            )
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for response in responses:
                response.raise_for_status()
                payload = response.json()
                page_form = (payload.get("data") or {}).get("pageForm") if isinstance(payload, dict) else None
                page_rows = page_form.get("pageData") if isinstance(page_form, dict) else None
                if str(payload.get("state")) != "200" or not isinstance(page_rows, list):
                    raise ValueError(str(payload.get("msg") or "Skyworth Careers response is invalid"))
                for row in page_rows:
                    external_id = str(row.get("postId") or "").strip() if isinstance(row, dict) else ""
                    if not external_id or external_id in seen:
                        continue
                    preview = self._map_row(row, suite_key=suite_key)
                    if location and location.lower() not in source_posting_haystack(preview):
                        continue
                    seen.add(external_id)
                    rows.append(row)

            candidates = rank_postings_for_query(
                [self._map_row(row, suite_key=suite_key) for row in rows], query
            )[
                : min(max(limit * 2, 6), 20)
            ]
            detail_url = f"{self.endpoint_base}/listPositionDetail/SU{suite_key}"
            semaphore = asyncio.Semaphore(6)

            async def _load_detail(posting: JobPosting) -> JobPosting:
                async with semaphore:
                    response = await client.post(detail_url, data={"postId": posting.external_id})
                    response.raise_for_status()
                    payload = response.json()
                    detail = payload.get("data") if isinstance(payload, dict) else None
                    if str(payload.get("state")) != "200" or not isinstance(detail, dict):
                        raise ValueError(str(payload.get("msg") or "Skyworth job detail is invalid"))
                    return self._map_row({**posting.payload, **detail}, suite_key=suite_key)

            postings = await asyncio.gather(*[_load_detail(posting) for posting in candidates])
        return rank_postings_for_query(postings, query)[:limit]

    async def _discover_suite_key(self, client: httpx.AsyncClient) -> str:
        response = await client.post(self.suite_endpoint, data={"sld": "skyworth.hotjob.cn"})
        response.raise_for_status()
        payload = response.json()
        link_data = (payload.get("data") or {}).get("linkData") if isinstance(payload, dict) else None
        link = str(link_data.get("link") or "") if isinstance(link_data, dict) else ""
        match = re.search(r"/SU([A-Za-z0-9]+)/pb(?:/|$)", link)
        if str(payload.get("state")) != "200" or not match:
            raise ValueError("Skyworth Careers suite discovery response is invalid")
        return match.group(1)

    def _map_row(self, row: dict[str, Any], *, suite_key: str) -> JobPosting:
        external_id = str(row.get("postId") or "").strip()
        title = str(row.get("postName") or "").strip()
        location = str(row.get("workPlaceStr") or "").strip() or None
        project_name = str(row.get("projectName") or "").strip()
        job_type = " / ".join(part for part in [project_name, "校园招聘"] if part) or None
        company = str(row.get("orgName") or row.get("department") or row.get("company") or "创维集团").strip()
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", company),
            ("集团", row.get("company")),
            ("工作地点", location),
            ("招聘类型", job_type),
            ("职位类别", row.get("postTypeName")),
            ("岗位职责", row.get("workContent")),
            ("任职要求", row.get("serviceCondition")),
            ("学历要求", row.get("education") or row.get("educationStr")),
            ("专业要求", row.get("subject")),
            ("发布日期", row.get("publishDate")),
            ("岗位编号", row.get("postCode")),
        )
        apply_url = (
            f"https://skyworth.hotjob.cn/SU{suite_key}/pb/posDetail.html"
            f"?postId={external_id}&postType={row.get('recruitType') or 1}"
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company=company or "创维集团",
            location=location,
            job_type=job_type,
            apply_url=apply_url,
            raw_jd_text=raw_jd,
            payload=row,
        )


class WindCareersSource(JobSource):
    """Wind careers source backed by its public generated position dataset."""

    name = "wind"
    endpoint = "https://www.wind.com.cn/portal/zh/JoinUs/js/channelPositions.js?v=20251210"
    landing_page = "https://www.wind.com.cn/portal/zh/JoinUs/recruit.html"
    marker = "var channelPositions ="

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "application/javascript, text/javascript, */*",
            "Referer": self.landing_page,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.get(self.endpoint)
            response.raise_for_status()
        marker_index = response.text.find(self.marker)
        if marker_index < 0:
            raise ValueError("Wind Careers position dataset marker is missing")
        raw = response.text[marker_index + len(self.marker) :].lstrip()
        rows, _ = json.JSONDecoder().raw_decode(raw)
        if not isinstance(rows, list):
            raise ValueError("Wind Careers position dataset is not a list")

        postings: list[JobPosting] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("InUse") is False:
                continue
            posting = self._map_row(row)
            if not posting.title or not posting.external_id:
                continue
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            if not is_query_relevant_posting(posting, query):
                continue
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("ChannelPositionID") or row.get("PositionID") or "").strip()
        title = str(row.get("ChannelPositionName") or row.get("PositionName") or "").strip()
        locations = [
            str(item.get("Name") or "").strip()
            for item in row.get("WorkPlace") or []
            if isinstance(item, dict) and str(item.get("Name") or "").strip()
        ]
        projects = [
            str(item.get("Name") or "").strip()
            for item in row.get("Projects") or []
            if isinstance(item, dict) and str(item.get("Name") or "").strip()
        ]
        job_type = " / ".join(
            part for part in [str(row.get("PositionTypeText") or "").strip(), *projects] if part
        ) or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "Wind 万得"),
            ("工作地点", "、".join(locations)),
            ("招聘类型", job_type),
            ("职位类别", row.get("PositionClassName")),
            ("岗位职责", row.get("ChannelPositionDesc")),
            ("任职要求", row.get("ChannelPositionRequirement")),
            ("发布日期", row.get("PublishDate")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="Wind 万得",
            location="、".join(locations) or None,
            job_type=job_type,
            apply_url=(
                f"{self.landing_page}?channelPositionId={external_id}"
                f"&positionType={row.get('PositionType') or ''}"
            ),
            raw_jd_text=raw_jd,
            payload=row,
        )


class DidiCareersSource(JobSource):
    """DiDi official recruitment API with per-position detail enrichment."""

    name = "didi"
    list_endpoint = "https://talent.didiglobal.com/recruit-portal-service/api/job/front/list"
    detail_endpoint = "https://talent.didiglobal.com/recruit-portal-service/api/job/front/view"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent, "Referer": "https://talent.didiglobal.com/"}
        params = {
            "jobName": _job_source_search_key(query),
            "page": 1,
            "size": min(max(limit * 3, 20), 50),
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.get(self.list_endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = (
                data.get("items") or data.get("records") or data.get("list")
                if isinstance(data, dict)
                else None
            )
            if not isinstance(rows, list):
                raise ValueError("DiDi Careers response does not contain data.records")

            async def _detail(row: dict[str, Any]) -> dict[str, Any]:
                job_id = str(row.get("jdId") or row.get("id") or "").strip()
                if not job_id:
                    return row
                detail_response = await client.get(f"{self.detail_endpoint}/{job_id}")
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
                detail = detail_payload.get("data") if isinstance(detail_payload, dict) else None
                if not isinstance(detail, dict):
                    raise ValueError(f"DiDi Careers detail {job_id} is missing data")
                return {**row, **detail}

            details = await asyncio.gather(*[_detail(row) for row in rows if isinstance(row, dict)])

        postings = [self._map_row(row) for row in details]
        postings = [
            posting for posting in postings
            if posting.title
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("jdId") or row.get("id") or row.get("jdNo") or "").strip()
        title = str(row.get("jobName") or "").strip()
        work_area = str(row.get("workArea") or "").strip()
        recruit_type = str(row.get("recruitType") or "").strip()
        job_type = {"1": "社会招聘", "2": "校园招聘", "3": "实习招聘"}.get(
            recruit_type, recruit_type
        ) or str(row.get("jobType") or "").strip() or None
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "滴滴"),
            ("工作地点", work_area),
            ("所属部门", row.get("deptName")),
            ("招聘类型", job_type),
            ("岗位职责", row.get("jobDesc")),
            ("任职要求", row.get("qualification")),
            ("岗位编号", row.get("jdNo")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="滴滴",
            location=work_area or None,
            job_type=job_type,
            apply_url=f"https://talent.didiglobal.com/social/detail/{external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class HonorCareersSource(JobSource):
    """Honor internship source backed by its public HotJob endpoints."""

    name = "honor"
    suite = "SU61b9ba97bef57c13bca5cffd"
    base_url = "https://career.honor.com/wecruit/positionInfo"
    landing_page = f"https://career.honor.com/{suite}/mc/position/intern"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent, "Referer": self.landing_page}
        list_url = f"{self.base_url}/listPosition/{self.suite}"
        detail_url = f"{self.base_url}/listPositionDetail/{self.suite}"
        common_params = {"iSaJAx": "isAjax", "request_locale": "zh_CN"}
        form = {
            "recruitType": "12",
            "externalProjectStr": "101401,101601",
            "currentPage": "1",
            "pageSize": str(min(max(limit * 3, 20), 50)),
            "coordinateLat": "",
            "coordinateLng": "",
            "externalOrgStr": "",
            "externalPostIdsStr": "",
            "postKey": _job_source_search_key(query),
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            response = await client.post(list_url, params=common_params, data=form)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            page_form = data.get("pageForm") if isinstance(data, dict) else None
            rows = page_form.get("pageData") if isinstance(page_form, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Honor Careers response does not contain data.pageForm.pageData")

            async def _detail(row: dict[str, Any]) -> dict[str, Any]:
                post_id = str(row.get("postId") or "").strip()
                detail_response = await client.post(
                    detail_url,
                    params=common_params,
                    data={"postId": post_id, "recruitType": "12"},
                )
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
                detail = detail_payload.get("data") if isinstance(detail_payload, dict) else None
                if not isinstance(detail, dict):
                    raise ValueError(f"Honor Careers detail {post_id} is missing data")
                return {**row, **detail}

            details = await asyncio.gather(*[_detail(row) for row in rows if isinstance(row, dict)])

        postings = [self._map_row(row) for row in details]
        postings = [
            posting for posting in postings
            if posting.title
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("postId") or "").strip()
        title = str(row.get("postName") or "").strip()
        company = str(row.get("company") or row.get("orgName") or "荣耀").strip()
        location = str(row.get("workPlaceStr") or "").strip()
        job_type = " / ".join(
            str(value).strip()
            for value in [row.get("workTypeStr"), row.get("projectName")]
            if str(value or "").strip()
        ) or "实习招聘"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", company),
            ("工作地点", location),
            ("招聘类型", job_type),
            ("职位类别", row.get("postTypeName")),
            ("岗位职责", row.get("workContent")),
            ("任职要求", row.get("serviceCondition")),
            ("学历要求", row.get("education") or row.get("educationStr")),
            ("发布日期", row.get("publishDate")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company=company,
            location=location or None,
            job_type=job_type,
            apply_url=f"https://career.honor.com/{self.suite}/mc/detail?postId={external_id}&recruitType=12",
            raw_jd_text=raw_jd,
            payload=row,
        )


class KuaishouCareersSource(JobSource):
    """Kuaishou social and daily-internship source using the site's public signing algorithm."""

    name = "kuaishou"
    endpoint = "https://zhaopin.kuaishou.cn/recruit/e/api/v1/open/positions/simple"
    signing_key = "652f962a-0575-4575-98d2-f04e2291bee2"
    city_names = {
        "Beijing": "北京", "Shanghai": "上海", "Guangzhou": "广州", "Shenzhen": "深圳",
        "Hangzhou": "杭州", "Chengdu": "成都", "Wuhan": "武汉", "Xian": "西安",
        "Tianjin": "天津", "Wuxi": "无锡", "Yantai": "烟台",
    }

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    @classmethod
    def _signature(cls, params: dict[str, str], timestamp_ms: int) -> str:
        canonical = urlencode(sorted(params.items()))
        message = f"{timestamp_ms}{canonical}{cls.signing_key}".encode()
        return hmac.new(cls.signing_key.encode(), message, hashlib.sha256).hexdigest()

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        request_size = min(max(limit * 2, 20), 50)
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers={"User-Agent": settings.user_agent, "Referer": "https://zhaopin.kuaishou.cn/"},
            transport=self.transport,
        ) as client:
            async def _load(position_nature: str) -> list[dict[str, Any]]:
                params = {
                    "name": _job_source_search_key(query),
                    "pageNum": "1",
                    "pageSize": str(request_size),
                    "positionNatureCode": position_nature,
                    "workLocationCode": "domestic",
                }
                timestamp_ms = int(time.time() * 1000)
                response = await client.get(
                    self.endpoint,
                    params=params,
                    headers={
                        "sign": self._signature(params, timestamp_ms),
                        "signTimestamp": str(timestamp_ms),
                    },
                )
                response.raise_for_status()
                payload = response.json()
                result = payload.get("result") if isinstance(payload, dict) else None
                rows = result.get("list") if isinstance(result, dict) else None
                if payload.get("code") != 0 or not isinstance(rows, list):
                    raise ValueError(f"Kuaishou Careers search failed: {payload.get('message')}")
                return [row for row in rows if isinstance(row, dict)]

            row_groups = await asyncio.gather(_load("C001"), _load("C002"))

        seen: set[str] = set()
        postings: list[JobPosting] = []
        for row in [item for group in row_groups for item in group]:
            posting = self._map_row(row)
            if posting.external_id in seen:
                continue
            seen.add(posting.external_id)
            if location and location.lower() not in source_posting_haystack(posting):
                continue
            postings.append(posting)
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("name") or "").strip()
        raw_locations = row.get("workLocationsCode") or row.get("workLocationCode") or []
        if not isinstance(raw_locations, list):
            raw_locations = re.split(r"[,，;；]", str(raw_locations))
        location = "、".join(
            self.city_names.get(str(code).strip(), str(code).strip())
            for code in raw_locations
            if str(code).strip()
        )
        nature = str(row.get("positionNatureCode") or "").strip()
        job_type = "日常实习" if nature == "C002" else "社会招聘"
        route = "trainee" if nature == "C002" else "social"
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "快手"),
            ("工作地点", location),
            ("招聘类型", job_type),
            ("职位类别", row.get("positionCategoryCode")),
            ("岗位职责", row.get("description")),
            ("任职要求", row.get("positionDemand")),
            ("更新时间", row.get("updateTime")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="快手",
            location=location or None,
            job_type=job_type,
            apply_url=f"https://zhaopin.kuaishou.cn/#/official/{route}/job-info/{external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class LenovoCareersSource(JobSource):
    """Lenovo China campus source; the list API already carries complete JD fields."""

    name = "lenovo"
    endpoint = "https://talent.lenovo.com.cn/gateway/jobBase/list"
    city_names = {
        "1": "北京", "2": "上海", "3": "广州", "4": "重庆", "5": "深圳",
        "6": "天津", "7": "成都", "8": "武汉", "9": "惠州", "10": "厦门",
        "12": "杭州", "13": "大连", "16": "西安", "20": "南京", "22": "合肥",
        "23": "苏州", "26": "长沙", "32": "无锡", "34": "海口",
    }

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        params = {
            "projectType": 1,
            "pageNum": 1,
            "pageSize": min(max(limit * 3, 20), 50),
            "keyword": _job_source_search_key(query),
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            transport=self.transport,
        ) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        rows = result.get("rows") if isinstance(result, dict) else None
        if payload.get("code") != 0 or not isinstance(rows, list):
            raise ValueError(f"Lenovo Careers search failed: {payload.get('message')}")
        postings = [self._map_row(row) for row in rows if isinstance(row, dict)]
        postings = [
            posting for posting in postings
            if posting.title
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("jobName") or "").strip()
        city_codes = re.split(r"[,，;；]", str(row.get("workPlace") or ""))
        location = "、".join(self.city_names.get(code.strip(), code.strip()) for code in city_codes if code.strip())
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "联想"),
            ("工作地点", location),
            ("招聘类型", "校园招聘"),
            ("职位类别", row.get("typeName")),
            ("岗位职责", _html_text(row.get("jobDuties"))),
            ("任职要求", _html_text(row.get("jobRequirement"))),
            ("学历要求", row.get("educationRequired")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="联想",
            location=location or None,
            job_type="校园招聘",
            apply_url=f"https://talent.lenovo.com.cn/position/detail?id={external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class VivoCareersSource(JobSource):
    """vivo social source whose search rows include the full JD."""

    name = "vivo"
    endpoint = "https://career.vivo.com/api/social/webSite/portal/page"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        body = {
            "city_code_list": [],
            "company_id": 1,
            "group_id": 1,
            "user_id": None,
            "job_category_id_list": [],
            "keyword": _job_source_search_key(query),
            "max_results": min(max(limit * 3, 20), 50),
            "page": 1,
            "yoe_list": [],
            "loading": True,
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers={"User-Agent": settings.user_agent, "Referer": "https://career.vivo.com/jobs"},
            transport=self.transport,
        ) as client:
            response = await client.post(self.endpoint, json=body)
            response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("code") != 0 or not isinstance(rows, list):
            raise ValueError(f"vivo Careers search failed: {payload.get('message')}")
        postings = [self._map_row(row) for row in rows if isinstance(row, dict)]
        postings = [
            posting for posting in postings
            if posting.title
            and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("job_id") or row.get("job_code") or "").strip()
        title = str(row.get("job_title") or "").strip()
        locations = []
        for item in row.get("job_location_list") or []:
            if isinstance(item, dict):
                value = str(item.get("city") or item.get("location") or "").strip()
                if value and value not in locations:
                    locations.append(value)
        location = "、".join(locations)
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", "vivo"),
            ("工作地点", location),
            ("招聘类型", "社会招聘"),
            ("所属部门", row.get("requirement_org_name")),
            ("职位类别", row.get("job_category")),
            ("岗位描述", row.get("job_desc")),
            ("学历要求", row.get("degree_range_name")),
        )
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="vivo",
            location=location or None,
            job_type="社会招聘",
            apply_url=f"https://career.vivo.com/jobs/{external_id}",
            raw_jd_text=raw_jd,
            payload=row,
        )


class NetEaseCareersSource(JobSource):
    """NetEase current campus and AI-special projects from official public APIs."""

    name = "netease"
    navigation_endpoint = "https://campus.163.com/api/campuspc/project/navigation/list"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            transport=self.transport,
        ) as client:
            navigation_response = await client.get(self.navigation_endpoint)
            navigation_response.raise_for_status()
            navigation_payload = navigation_response.json()
            navigation = navigation_payload.get("data") if isinstance(navigation_payload, dict) else None
            projects: list[tuple[str, str, int]] = []
            for group in navigation or []:
                if not isinstance(group, dict):
                    continue
                for item in group.get("children") or []:
                    if not isinstance(item, dict):
                        continue
                    link = str(item.get("link") or "").strip()
                    match = re.search(r"https://(campus(?:\.game)?\.163\.com)/.+[?&]id=(\d+)", link)
                    if not match:
                        continue
                    projects.append(
                        (str(item.get("title") or "网易").strip(), f"https://{match.group(1)}", int(match.group(2)))
                    )
            if not projects:
                raise ValueError("NetEase Careers navigation contains no supported current projects")

            async def _load(company: str, host: str, project_id: int) -> list[JobPosting]:
                response = await client.get(
                    f"{host}/api/campuspc/position/getJobList",
                    params={"pageSize": 100, "currentPage": 1, "projectId": project_id},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                rows = data.get("list") if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    raise ValueError(f"NetEase project {project_id} response is missing data.list")
                return [self._map_row(company, host, project_id, row) for row in rows if isinstance(row, dict)]

            groups = await asyncio.gather(*[_load(*project) for project in projects])
        postings = [posting for group in groups for posting in group]
        postings = [
            posting for posting in postings
            if posting.title
            and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(
        self, company: str, host: str, project_id: int, row: dict[str, Any]
    ) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("positionName") or "").strip()
        location = str(row.get("workPlaceName") or row.get("interviewCityName") or "").strip()
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", company),
            ("工作地点", location),
            ("招聘类型", row.get("positionTypeName") or "校园招聘"),
            ("岗位职责", row.get("positionDescription")),
            ("任职要求", row.get("positionRequirement")),
            ("更新时间", row.get("updateTime")),
        )
        return JobPosting(
            source=self.name,
            external_id=f"{project_id}:{external_id}",
            title=title,
            company=company,
            location=location or None,
            job_type=str(row.get("positionTypeName") or "校园招聘").strip(),
            apply_url=f"{host}/app/detail/index?id={external_id}&projectId={project_id}",
            raw_jd_text=raw_jd,
            payload={"project_id": project_id, **row},
        )


class XiaohongshuCareersSource(JobSource):
    """Xiaohongshu social and campus jobs from its official public API."""

    name = "xiaohongshu"
    endpoint = "https://job.xiaohongshu.com/websiterecruit/position/pageQueryPosition"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent,
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": "https://job.xiaohongshu.com/jobs",
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            responses = await asyncio.gather(*[
                client.post(
                    self.endpoint,
                    json={
                        "positionName": _job_source_search_key(query),
                        "pageNum": 1,
                        "pageSize": min(max(limit * 2, 20), 100),
                        "recruitType": recruit_type,
                    },
                )
                for recruit_type in ("social", "campus")
            ])
        postings: list[JobPosting] = []
        for recruit_type, response in zip(("social", "campus"), responses, strict=True):
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError(f"Xiaohongshu {recruit_type} response is missing data.list")
            postings.extend(self._map_row(recruit_type, row) for row in rows if isinstance(row, dict))
        postings = [
            posting for posting in postings
            if posting.title and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, recruit_type: str, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("positionId") or row.get("id") or "").strip()
        title = str(row.get("positionName") or "").strip()
        location = str(row.get("workplace") or row.get("workPlace") or "").strip()
        route = "campus" if recruit_type == "campus" else "social"
        return JobPosting(
            source=self.name,
            external_id=external_id,
            title=title,
            company="小红书",
            location=location or None,
            job_type="校园招聘" if recruit_type == "campus" else "社会招聘",
            apply_url=f"https://job.xiaohongshu.com/{route}/position/{external_id}",
            raw_jd_text=_join_jd_sections(
                ("岗位名称", title), ("公司", "小红书"), ("工作地点", location),
                ("招聘项目", row.get("jobProjectName")), ("岗位类别", row.get("jobType")),
                ("岗位职责", row.get("duty")), ("任职要求", row.get("qualification")),
                ("发布时间", row.get("publishTime")),
            ),
            payload={"recruit_type": recruit_type, **row},
        )


class BilibiliCareersSource(JobSource):
    """Bilibili jobs with the official CSRF bootstrap contract."""

    name = "bilibili"
    csrf_endpoint = "https://jobs.bilibili.com/api/auth/v1/csrf/token"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {"User-Agent": settings.user_agent, "X-UserType": "2", "X-AppKey": "ops.ehr-api.auth"}
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds, headers=headers, transport=self.transport
        ) as client:
            csrf_response = await client.get(self.csrf_endpoint)
            csrf_response.raise_for_status()
            csrf_payload = csrf_response.json()
            csrf = str(csrf_payload.get("data") or "") if isinstance(csrf_payload, dict) else ""
            if not csrf:
                raise ValueError("Bilibili CSRF bootstrap returned no token")
            body = {
                "pageSize": min(max(limit * 2, 20), 100), "pageNum": 1,
                "positionName": _job_source_search_key(query), "postCode": "", "postCodeList": "",
                "workLocationList": [], "workTypeList": [], "positionTypeList": [],
                "deptCodeList": "", "recruitType": None, "practiceTypes": None, "onlyHotRecruit": 0,
            }
            responses = await asyncio.gather(*[
                client.post(
                    f"https://jobs.bilibili.com/api/{route}/position/positionList",
                    headers={"X-CSRF": csrf, "Content-Type": "application/json"}, json=body,
                )
                for route in ("campus", "srs")
            ])
        postings: list[JobPosting] = []
        for route, response in zip(("campus", "srs"), responses, strict=True):
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError(f"Bilibili {route} response is missing data.list")
            postings.extend(self._map_row(route, row) for row in rows if isinstance(row, dict))
        postings = [
            posting for posting in postings
            if posting.title and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, route: str, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("positionName") or "").strip()
        location_value = row.get("workLocation")
        location = (
            "、".join(str(item).strip() for item in location_value if str(item).strip())
            if isinstance(location_value, list) else str(location_value or "").strip()
        )
        web_route = "campus" if route == "campus" else "social"
        return JobPosting(
            source=self.name, external_id=external_id, title=title, company="哔哩哔哩",
            location=location or None,
            job_type=str(row.get("recruitType") or ("校园招聘" if route == "campus" else "社会招聘")),
            apply_url=f"https://jobs.bilibili.com/{web_route}/positions/{external_id}",
            raw_jd_text=_join_jd_sections(
                ("岗位名称", title), ("公司", "哔哩哔哩"), ("工作地点", location),
                ("岗位类别", row.get("positionTypeName") or row.get("postCodeName")),
                ("职位描述", row.get("positionDescription")), ("发布时间", row.get("pushTime")),
            ),
            payload={"recruit_route": route, **row},
        )


class AntGroupCareersSource(JobSource):
    """Ant Group campus jobs from the official public careers API."""

    name = "antgroup"
    endpoint = "https://hrcareersweb.antgroup.com/api/campus/position/search"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent, "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds, headers=headers, transport=self.transport
        ) as client:
            responses = await asyncio.gather(*[
                client.post(
                    self.endpoint, params={"ctoken": "career-agent-public-search"},
                    json={
                        "channel": "campus_group_official_site", "language": "zh", "regions": "",
                        "subCategories": "", "bgCode": "", "pageIndex": page_index, "pageSize": 20,
                        "recruitType": [], "batchIds": [],
                    },
                )
                for page_index in range(1, 4)
            ])
        postings: list[JobPosting] = []
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Ant Group response is missing content")
            postings.extend(self._map_row(row) for row in rows if isinstance(row, dict))
        postings = [
            posting for posting in postings
            if posting.title and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("name") or "").strip()
        locations = row.get("workLocations") or []
        location = (
            "、".join(
                str(item.get("name") if isinstance(item, dict) else item).strip()
                for item in locations
                if str(item.get("name") if isinstance(item, dict) else item).strip()
            ) if isinstance(locations, list) else str(locations).strip()
        )
        return JobPosting(
            source=self.name, external_id=external_id, title=title, company="蚂蚁集团",
            location=location or None, job_type=str(row.get("batchTypeDesc") or "校园招聘").strip(),
            apply_url=f"https://talent.antgroup.com/campus-full-list?positionId={external_id}",
            raw_jd_text=_join_jd_sections(
                ("岗位名称", title), ("公司", "蚂蚁集团"), ("工作地点", location),
                ("岗位类别", row.get("categoryName")), ("招聘批次", row.get("batchName")),
                ("岗位职责", row.get("description")), ("任职要求", row.get("requirement")),
            ),
            payload=row,
        )


class Qihu360CareersSource(JobSource):
    """360 current jobs from its official list and detail APIs."""

    name = "qihu360"
    list_endpoint = "https://hr.360.cn/v2/index/getlistsearch"
    detail_endpoint = "https://hr.360.cn/v2/index/getjobone"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        headers = {
            "User-Agent": settings.user_agent, "Accept": "application/json",
            "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest",
        }
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds, headers=headers, transport=self.transport
        ) as client:
            list_response = await client.post(self.list_endpoint, json={"limit": 10000, "page": 1})
            list_response.raise_for_status()
            list_payload = list_response.json()
            rows = list_payload.get("data") if isinstance(list_payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("360 Careers response is missing data")
            semaphore = asyncio.Semaphore(6)

            async def _load(row: dict[str, Any]) -> JobPosting:
                async with semaphore:
                    response = await client.get(self.detail_endpoint, params={"id": row.get("id")})
                    response.raise_for_status()
                    payload = response.json()
                    detail = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(detail, dict):
                        raise ValueError(f"360 job {row.get('id')} response is missing data")
                    return self._map_row({**row, **detail})

            postings = await asyncio.gather(*[_load(row) for row in rows if isinstance(row, dict)])
        postings = [
            posting for posting in postings
            if posting.title and is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    def _map_row(self, row: dict[str, Any]) -> JobPosting:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        location = str(row.get("area") or "").strip()
        return JobPosting(
            source=self.name, external_id=external_id, title=title, company="360集团",
            location=location or None, job_type=str(row.get("type") or "社会招聘").strip(),
            apply_url=f"https://hr.360.cn/hr/detail/{external_id}",
            raw_jd_text=_join_jd_sections(
                ("岗位名称", title), ("公司", "360集团"), ("工作地点", location),
                ("岗位类别", row.get("position")), ("岗位描述", _html_text(row.get("description"))),
                ("任职要求", _html_text(row.get("qualification"))), ("工作年限", row.get("year")),
                ("发布时间", row.get("date")),
            ),
            payload=row,
        )


class MiniMaxCareersSource(JobSource):
    """MiniMax official Feishu portal source using visible, server-populated job cards."""

    name = "minimax"
    search_page = "https://vrfi1sk8a0.jobs.feishu.cn/379481/"

    def __init__(
        self,
        posting_loader: Callable[[str, int], Awaitable[list[JobPosting]]] | None = None,
    ) -> None:
        self.posting_loader = posting_loader

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        loader = self.posting_loader or self._load_browser_postings
        postings = await loader(_job_source_search_key(query), min(max(limit * 2, 20), 50))
        postings = [
            posting for posting in postings
            if is_query_relevant_posting(posting, query)
            and (not location or location.lower() in source_posting_haystack(posting))
        ]
        return rank_postings_for_query(postings, query)[:limit]

    async def _load_browser_postings(self, query: str, limit: int) -> list[JobPosting]:
        settings = get_settings()
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "MiniMax Careers requires Playwright and Chromium. Install them with "
                "`pip install playwright` and `playwright install chromium`."
            ) from exc
        page_url = f"{self.search_page}?{urlencode({'keywords': query})}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=settings.job_source_browser_headless)
            page = await browser.new_page(user_agent=settings.user_agent)
            try:
                await page.goto(page_url, wait_until="networkidle", timeout=settings.job_source_browser_timeout_ms)
                await page.locator(".positionItem").first.wait_for(
                    state="visible", timeout=settings.job_source_browser_timeout_ms
                )
                rows = await page.locator(".positionItem").evaluate_all(
                    """elements => elements.map(element => ({
                        title: (element.querySelector('.positionItem-title-text')?.textContent || '').trim(),
                        metadata: (element.querySelector('.positionItem-subTitle')?.innerText || '').trim(),
                        description: (element.querySelector('.positionItem-jobDesc')?.innerText || '').trim()
                    }))"""
                )
            finally:
                await browser.close()
        postings: list[JobPosting] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            metadata = str(row.get("metadata") or "").strip()
            description = str(row.get("description") or "").strip()
            if not title:
                continue
            external_id = hashlib.sha256(f"{title}|{metadata}".encode()).hexdigest()[:20]
            location = metadata.splitlines()[0].strip() if metadata else None
            raw_jd = _join_jd_sections(
                ("岗位名称", title),
                ("公司", "MiniMax"),
                ("岗位元数据", metadata),
                ("官网职位卡片描述", description),
            )
            postings.append(
                JobPosting(
                    source=self.name,
                    external_id=external_id,
                    title=title,
                    company="MiniMax",
                    location=location,
                    job_type="实习" if "实习" in metadata or "实习" in title else "校园/社会招聘",
                    apply_url=page_url,
                    raw_jd_text=raw_jd,
                    payload={**row, "granularity": "official_job_card"},
                )
            )
        return postings


class ZhipuCareersSource(JobSource):
    """Zhipu official category-level opportunities; the site does not expose individual JDs."""

    name = "zhipu"
    landing_page = "https://www.zhipuai.cn/zh/joinus"
    apply_url = "https://zhipu-ai.feishu.cn/share/base/form/shrcndgpmRlJoD5rMmIavUrPwzg"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        settings = get_settings()
        async with httpx.AsyncClient(
            timeout=settings.job_search_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            transport=self.transport,
            follow_redirects=True,
        ) as client:
            response = await client.get(self.landing_page)
            response.raise_for_status()
        parser = _HeadingSummaryParser()
        parser.feed(response.text)
        cities = "北京、上海、深圳、杭州、成都、吉隆坡"
        postings: list[JobPosting] = []
        for row in parser.rows:
            title = row["title"]
            if "校招" not in title and "社招" not in title:
                continue
            summary = row["summary"]
            external_id = hashlib.sha256(title.encode()).hexdigest()[:20]
            posting = JobPosting(
                source=self.name,
                external_id=external_id,
                title=f"{title}（岗位类别）",
                company="智谱AI",
                location=cities,
                job_type="校招" if "校招" in title else "社会招聘",
                apply_url=self.apply_url,
                raw_jd_text=_join_jd_sections(
                    ("岗位类别", title),
                    ("公司", "智谱AI"),
                    ("可选城市", cities),
                    ("官网类别说明", summary),
                    ("数据粒度", "岗位类别；官网未公开单岗位完整 JD"),
                ),
                payload={**row, "granularity": "category", "source_page": self.landing_page},
            )
            if is_query_relevant_posting(posting, query):
                postings.append(posting)
        postings = [
            posting for posting in postings
            if not location or location.lower() in source_posting_haystack(posting)
        ]
        return rank_postings_for_query(postings, query)[:limit]


@dataclass(frozen=True)
class MokaCareerSite:
    key: str
    company: str
    mode: str
    org_id: str
    site_id: int
    host: str = "app.mokahr.com"

    @property
    def jobs_url(self) -> str:
        route = "apply" if self.mode == "apply" else f"{self.mode}-recruitment"
        return (
            f"https://{self.host}/{route}/"
            f"{self.org_id}/{self.site_id}#/jobs?page=1&anchorName=jobsList"
        )


class MokaChinaCareersSource(JobSource):
    """Shared-browser adapter for verified Chinese company sites hosted by Moka."""

    name = "moka_cn"
    sites = (
        MokaCareerSite("shokz", "韶音科技", "campus", "aftershokzhr", 36940),
        MokaCareerSite("deepseek", "DeepSeek", "social", "high-flyer", 140576),
        MokaCareerSite("biren", "壁仞科技", "social", "biren", 44726),
        MokaCareerSite("ruijie", "锐捷网络", "campus", "ruijie", 136206),
        MokaCareerSite("pwrd", "完美世界", "campus", "pwrd", 144582),
        MokaCareerSite("sina", "新浪集团", "campus", "sina", 43536),
        MokaCareerSite("snb", "苏商银行", "social", "snb", 45591),
        MokaCareerSite("linecorp", "LINE MAN Technology", "social", "linecorp", 150828),
        MokaCareerSite("moonshot", "月之暗面", "apply", "moonshot", 148506),
        MokaCareerSite("dji", "大疆创新", "social", "dji", 170070, "apply.careers.dji.com"),
        MokaCareerSite("wps", "金山办公", "campus", "wps", 41436),
        MokaCareerSite("zte_social", "中兴通讯", "social", "zte", 47588),
        MokaCareerSite("zte_campus", "中兴通讯", "campus", "zte", 46903),
    )

    def __init__(
        self,
        posting_loader: Callable[[str, int], Awaitable[list[JobPosting]]] | None = None,
    ) -> None:
        self.posting_loader = posting_loader

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        loader = self.posting_loader or self._load_browser_postings
        postings = await loader(_job_source_search_key(query), limit)
        postings = [
            posting
            for posting in postings
            if not location or location.lower() in source_posting_haystack(posting)
        ]
        return rank_postings_for_query(postings, query)[:limit]

    async def _load_browser_postings(self, query: str, limit: int) -> list[JobPosting]:
        settings = get_settings()
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Moka China careers sources require Playwright and Chromium. "
                "Install them with `pip install playwright` and `playwright install chromium`."
            ) from exc

        postings: list[JobPosting] = []
        errors: dict[str, str] = {}
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=settings.job_source_browser_headless)
            try:
                semaphore = asyncio.Semaphore(3)

                async def _load_one(site: MokaCareerSite) -> tuple[str, list[JobPosting], str | None]:
                    async with semaphore:
                        context = await browser.new_context(user_agent=settings.user_agent)
                        page = await context.new_page()
                        try:
                            rows = await self._load_site(page, site, query, limit)
                            return site.key, rows, None
                        except Exception as exc:  # noqa: BLE001
                            return site.key, [], str(exc)
                        finally:
                            await context.close()

                site_results = await asyncio.gather(*[_load_one(site) for site in self.sites])
                for key, rows, error in site_results:
                    postings.extend(rows)
                    if error:
                        errors[key] = error
            finally:
                await browser.close()
        if not postings and errors:
            detail = "; ".join(f"{key}: {value}" for key, value in errors.items())
            raise ValueError(f"Moka China careers sources returned no jobs: {detail}")
        return postings

    async def _load_site(self, page: Any, site: MokaCareerSite, query: str, limit: int) -> list[JobPosting]:
        settings = get_settings()
        await page.goto(
            site.jobs_url,
            wait_until="networkidle",
            timeout=settings.job_source_browser_timeout_ms,
        )
        search_inputs = page.locator(
            'input[placeholder="输入职位关键字"], input[placeholder="搜索职位关键词"]'
        )
        if await search_inputs.count() == 0:
            raise ValueError("Moka job search input is missing")
        search_input = search_inputs.last
        await search_input.fill(query)
        async with page.expect_response(
            lambda response: "/website/jobs/v2" in response.url and response.status == 200,
            timeout=settings.job_source_browser_timeout_ms,
        ):
            await search_input.press("Enter")
        await page.wait_for_timeout(500)

        links = await page.locator('a[href*="#/job/"]').evaluate_all(
            """elements => elements.map(element => ({
                text: (element.innerText || '').trim(),
                href: element.href || ''
            }))"""
        )
        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in links:
            if not isinstance(item, dict):
                continue
            href = str(item.get("href") or "").strip()
            title = _moka_link_title(item.get("text"))
            if (
                not href
                or not title
                or href in seen
                or query.lower() not in title.lower()
            ):
                continue
            seen.add(href)
            candidates.append((title, href))
        candidates = candidates[: min(max(limit, 3), 8)]

        postings: list[JobPosting] = []
        for title, href in candidates:
            detail_page = await page.context.new_page()
            try:
                await detail_page.goto(
                    href,
                    wait_until="networkidle",
                    timeout=settings.job_source_browser_timeout_ms,
                )
                await detail_page.get_by_text("职位描述", exact=True).first.wait_for(
                    state="visible",
                    timeout=settings.job_source_browser_timeout_ms,
                )
                body_text = await detail_page.locator("body").inner_text()
            finally:
                await detail_page.close()
            posting = self._map_detail(site, title, href, body_text)
            if len(posting.raw_jd_text) >= 200:
                postings.append(posting)
        return postings

    def _map_detail(
        self,
        site: MokaCareerSite,
        title: str,
        href: str,
        body_text: str,
    ) -> JobPosting:
        external_id = href.rsplit("/job/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        detail, metadata = _moka_detail_sections(body_text)
        location = metadata.get("location")
        job_type = metadata.get("job_type") or ("实习" if "实习" in title else None)
        raw_jd = _join_jd_sections(
            ("岗位名称", title),
            ("公司", site.company),
            ("工作地点", location),
            ("招聘类型", job_type),
            ("职位描述", detail),
        )
        return JobPosting(
            source=f"moka_{site.key}",
            external_id=external_id,
            title=title,
            company=site.company,
            location=location,
            job_type=job_type,
            apply_url=href,
            raw_jd_text=raw_jd,
            payload={"site": site.key, "detail_text": detail, **metadata},
        )


class LeverCareersSource(JobSource):
    """Explicit English auxiliary source; it is never part of the Chinese default path."""

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
        if settings.baidu_careers_enabled:
            self.sources[BaiduCareersSource.name] = BaiduCareersSource()
        if settings.meituan_careers_enabled:
            self.sources[MeituanCareersSource.name] = MeituanCareersSource()
        if settings.bytedance_careers_enabled:
            self.sources[ByteDanceCareersSource.name] = ByteDanceCareersSource()
        if settings.alibaba_careers_enabled:
            self.sources[AlibabaCareersSource.name] = AlibabaCareersSource()
        if settings.jd_careers_enabled:
            self.sources[JDCareersSource.name] = JDCareersSource()
        if settings.china_telecom_careers_enabled:
            self.sources[ChinaTelecomCareersSource.name] = ChinaTelecomCareersSource()
        if settings.huawei_careers_enabled:
            self.sources[HuaweiCareersSource.name] = HuaweiCareersSource()
        if settings.iflytek_careers_enabled:
            self.sources[IFlytekCareersSource.name] = IFlytekCareersSource()
        if settings.tcl_careers_enabled:
            self.sources[TCLCareersSource.name] = TCLCareersSource()
        if settings.midea_careers_enabled:
            self.sources[MideaCareersSource.name] = MideaCareersSource()
        if settings.xiaomi_careers_enabled:
            self.sources[XiaomiCareersSource.name] = XiaomiCareersSource()
        if settings.oppo_careers_enabled:
            self.sources[OPPOCareersSource.name] = OPPOCareersSource()
        if settings.skyworth_careers_enabled:
            self.sources[SkyworthCareersSource.name] = SkyworthCareersSource()
        if settings.wind_careers_enabled:
            self.sources[WindCareersSource.name] = WindCareersSource()
        if settings.moka_china_careers_enabled:
            self.sources[MokaChinaCareersSource.name] = MokaChinaCareersSource()
        if settings.didi_careers_enabled:
            self.sources[DidiCareersSource.name] = DidiCareersSource()
        if settings.honor_careers_enabled:
            self.sources[HonorCareersSource.name] = HonorCareersSource()
        if settings.kuaishou_careers_enabled:
            self.sources[KuaishouCareersSource.name] = KuaishouCareersSource()
        if settings.lenovo_careers_enabled:
            self.sources[LenovoCareersSource.name] = LenovoCareersSource()
        if settings.vivo_careers_enabled:
            self.sources[VivoCareersSource.name] = VivoCareersSource()
        if settings.netease_careers_enabled:
            self.sources[NetEaseCareersSource.name] = NetEaseCareersSource()
        if settings.xiaohongshu_careers_enabled:
            self.sources[XiaohongshuCareersSource.name] = XiaohongshuCareersSource()
        if settings.bilibili_careers_enabled:
            self.sources[BilibiliCareersSource.name] = BilibiliCareersSource()
        if settings.antgroup_careers_enabled:
            self.sources[AntGroupCareersSource.name] = AntGroupCareersSource()
        if settings.qihu360_careers_enabled:
            self.sources[Qihu360CareersSource.name] = Qihu360CareersSource()
        if settings.minimax_careers_enabled:
            self.sources[MiniMaxCareersSource.name] = MiniMaxCareersSource()
        if settings.zhipu_careers_enabled:
            self.sources[ZhipuCareersSource.name] = ZhipuCareersSource()
        if settings.lever_careers_enabled:
            self.sources[LeverCareersSource.name] = LeverCareersSource()

    def select(self, names: list[str] | None = None) -> list[JobSource]:
        if not names:
            return list(self.sources.values())
        return [self.sources[name] for name in names if name in self.sources]


def _join_jd_sections(*sections: tuple[str, Any]) -> str:
    output: list[str] = []
    for label, value in sections:
        text = str(value or "").strip()
        if text:
            output.append(f"{label}：\n{text}")
    return "\n\n".join(output)


def _nested_text(value: Any, *paths: str) -> str:
    if not isinstance(value, dict):
        return ""
    for path in paths:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        text = str(current or "").strip()
        if text:
            return text
    return ""


def _html_text(value: Any) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(str(value or ""))
    return parser.text()


def _moka_link_title(value: Any) -> str:
    ignored = {"急", "实习", "全职", "校招", "社招"}
    for line in str(value or "").splitlines():
        text = line.strip()
        if text and text not in ignored and not text.startswith("发布于"):
            return text
    return ""


def _moka_detail_sections(value: str) -> tuple[str, dict[str, str]]:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    start = lines.index("职位描述") + 1 if "职位描述" in lines else 0
    end = lines.index("职位信息", start) if "职位信息" in lines[start:] else len(lines)
    detail = "\n".join(lines[start:end]).strip()
    metadata: dict[str, str] = {}
    for line in lines[:start]:
        if "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|") if part.strip()]
        if len(parts) > 1 and parts[-1] in {"实习", "全职", "兼职", "校招", "社招"}:
            metadata["location"] = parts[0]
            metadata["job_type"] = parts[-1]
            break
        if len(parts) > 1 and _looks_like_location(parts[-1]):
            metadata["location"] = parts[-1]
            break
    for index, line in enumerate(lines[end:], start=end):
        if line == "职位性质" and index + 1 < len(lines):
            offset = index + 1
            while offset < len(lines) and lines[offset] == "职位性质":
                offset += 1
            if offset < len(lines):
                metadata["job_type"] = lines[offset]
    return detail, metadata


def _looks_like_location(value: str) -> bool:
    text = value.strip()
    signals = (
        "北京", "上海", "天津", "重庆", "深圳", "广州", "杭州", "南京", "武汉",
        "成都", "西安", "苏州", "合肥", "长沙", "厦门", "青岛", "大连", "海外",
        "远程", "市", "省",
    )
    return any(signal in text for signal in signals)


def _job_source_search_key(query: str) -> str:
    text = str(query or "").strip()
    lowered = text.lower()
    for signal in ("agent", "rag", "llm", "prompt"):
        if signal in lowered:
            return signal.upper() if signal in {"rag", "llm"} else signal.title()
    for signal in ("智能体", "大模型", "评测", "算法", "后端", "产品", "开发"):
        if signal in text:
            return signal
    return text


def _job_source_search_keys(query: str) -> list[str]:
    text = str(query or "").strip()
    lowered = text.lower()
    keys: list[str] = []
    signals = (
        ("agent", "Agent"),
        ("智能体", "智能体"),
        ("大模型", "大模型"),
        ("llm", "大模型"),
        ("rag", "RAG"),
        ("ai", "AI"),
    )
    for signal, key in signals:
        haystack = lowered if signal.isascii() else text
        if signal in haystack and key not in keys:
            keys.append(key)
    fallback = _job_source_search_key(text)
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys or [text]
