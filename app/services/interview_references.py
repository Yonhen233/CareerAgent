from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus, urlparse


class InterviewReferenceService:
    """Normalize interview references into honest source and search links."""

    _PLACEHOLDER_HOSTS = {"example.com", "www.example.com", "localhost", "127.0.0.1"}
    _PLACEHOLDER_SEGMENTS = ("example", "sample", "demo")

    @classmethod
    def is_valid_public_url(cls, value: str | None) -> bool:
        url = str(value or "").strip()
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        host = str(parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host or host in cls._PLACEHOLDER_HOSTS:
            return False
        for segment in (part.lower() for part in parsed.path.split("/") if part):
            if any(segment == marker or segment.startswith(f"{marker}-") or segment.startswith(f"{marker}_") for marker in cls._PLACEHOLDER_SEGMENTS):
                return False
        return True

    @classmethod
    def normalize_links(cls, links: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in links or []:
            item = dict(raw or {})
            kind = str(item.get("kind") or "").strip()
            if kind == "confirmed_imported_interview_experience":
                candidate = cls._normalize_imported_source(item)
            elif kind == "search_reference_link":
                candidate = cls._normalize_search_entry(item)
            else:
                candidate = cls._normalize_generic_link(item)
            if candidate is None:
                continue
            key = str(candidate.get("url") or candidate.get("title") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @classmethod
    def _normalize_imported_source(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        url = str(item.get("url") or "").strip()
        if not cls.is_valid_public_url(url):
            return None
        title = str(item.get("title") or "已导入面经").strip()
        if not title.startswith("已导入面经："):
            title = f"已导入面经：{title}"
        return item | {
            "title": title,
            "url": url,
            "reference_type": "source_article",
            "reference_type_label": "原文",
            "note": "这是用户导入的原始链接；正文真实性和时效性仍需用户自行判断。",
        }

    @classmethod
    def _normalize_search_entry(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        site = str(item.get("site") or "搜索引擎").strip()
        topic = str(item.get("topic") or cls._topic_from_title(item.get("title")) or "相关资料").strip()
        query = cls._clean_query(str(item.get("query") or "").strip())
        if not query:
            return None
        lowered = site.lower()
        if "牛客" in site or "nowcoder" in lowered:
            url = f"https://www.nowcoder.com/search/all?query={quote_plus(query)}"
            title = f"搜索牛客网：{topic}"
            note = "打开牛客网站内搜索结果，不代表某一篇面经已经核验。"
            reference_type = "site_search"
        elif "小红书" in site or "xiaohongshu" in lowered:
            url = f"https://www.xiaohongshu.com/search_result?keyword={quote_plus(query)}"
            title = f"搜索小红书：{topic}"
            note = "打开小红书关键词搜索，结果可能需要登录，并包含用户生成内容。"
            reference_type = "site_search"
        elif "offershow" in lowered:
            url = "https://offershow.cn/"
            title = f"打开 OfferShow：{topic}"
            note = "OfferShow 暂无稳定的公开关键词搜索地址；打开平台后请手动搜索公司或岗位。"
            reference_type = "platform_entry"
        else:
            url = f"https://www.baidu.com/s?wd={quote_plus(query)}"
            title = f"搜索：{topic}"
            note = "这是明确标注的搜索结果入口，不代表其中内容已经核验。"
            reference_type = "web_search"
        return item | {
            "title": title,
            "url": url,
            "query": query,
            "reference_type": reference_type,
            "reference_type_label": "搜索入口" if reference_type != "platform_entry" else "平台入口",
            "note": note,
        }

    @classmethod
    def _normalize_generic_link(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        url = str(item.get("url") or "").strip()
        if not cls.is_valid_public_url(url):
            return None
        return item | {
            "url": url,
            "reference_type": str(item.get("reference_type") or "external_reference"),
            "reference_type_label": str(item.get("reference_type_label") or "外部链接"),
        }

    @classmethod
    def _clean_query(cls, value: str) -> str:
        query = re.sub(r"\bsite:\S+", " ", value, flags=re.I)
        query = re.sub(r"^(牛客网|牛客|OfferShow|小红书)\s+", "", query, flags=re.I)
        return " ".join(query.split())

    @staticmethod
    def _topic_from_title(value: Any) -> str:
        title = str(value or "").strip()
        if "：" in title:
            return title.split("：", 1)[1].strip()
        if ":" in title:
            return title.split(":", 1)[1].strip()
        return title
