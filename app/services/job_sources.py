from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
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
