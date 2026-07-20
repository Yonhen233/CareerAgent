import asyncio
import json

import httpx

from app.services.job_sources import (
    AlibabaCareersSource,
    BaiduCareersSource,
    ByteDanceCareersSource,
    MeituanCareersSource,
)


def test_baidu_source_extracts_public_ssr_jobs_and_full_jd():
    row = {
        "name": "Agent算法实习生（J97505）",
        "postId": "cd423c1c-7a35-4672-b0a7-2857308efe43",
        "jobId": "job-1",
        "postType": "技术",
        "publishDate": "2026-05-26",
        "serviceCondition": "熟悉 Python、LLM Agent 框架和强化学习。",
        "workContent": "研发 Planner、Solver、Evaluator 和 Memory。",
        "workPlace": "北京市",
        "projectType": "日常实习项目",
        "bgShortName": "ACG",
    }
    initial_data = {
        "listData": {"listDetailData": [row], "total": 1},
        "detailData": {"projectType": None},
    }
    serialized = json.dumps(initial_data, ensure_ascii=False).replace(
        '"projectType": null}', '"projectType":undefined}'
    )
    html = f"<html><script>window.__INITIAL_DATA__ ={serialized};</script></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["recruitType"] == "INTERN"
        assert request.url.params["search"] == "Agent 开发实习生"
        return httpx.Response(200, text=html, request=request)

    postings = asyncio.run(
        BaiduCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发实习生",
            location="北京",
            limit=5,
        )
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "baidu"
    assert posting.company == "百度"
    assert posting.external_id == row["postId"]
    assert posting.job_type == "日常实习项目"
    assert "岗位职责" in posting.raw_jd_text
    assert "任职要求" in posting.raw_jd_text
    assert "Planner、Solver、Evaluator 和 Memory" in posting.raw_jd_text
    assert posting.apply_url == f"https://talent.baidu.com/jobs/detail/INTERN/{row['postId']}"


def test_meituan_source_searches_then_enriches_detail_jd():
    list_row = {
        "jobUnionId": "4555593816",
        "name": "Agent算法实习生",
        "jobType": "2",
        "jobSpecialCode": "6",
        "jobFamily": "技术类",
        "jobFamilyGroup": "算法",
        "cityList": [{"name": "北京市"}, {"name": "上海市"}],
        "department": [{"name": "核心本地商业-基础研发平台"}],
        "jobDuty": "优化 Agent 的任务规划、反思与执行能力。",
    }
    detail_row = {
        **list_row,
        "jobRequirement": "熟悉 LangGraph、MCP、多智能体协作和 Python。",
        "departmentIntro": "负责人工智能和大模型平台建设。",
        "highLight": "直接参与 Agentic Reasoning 研究。",
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        body = json.loads(request.content.decode("utf-8"))
        if request.url.path.endswith("/getJobList"):
            assert body["keywords"] == "Agent 开发实习生"
            assert body["page"]["pageSize"] >= 20
            return httpx.Response(
                200,
                json={"status": 1, "message": "成功", "data": {"list": [list_row]}},
                request=request,
            )
        assert request.url.path.endswith("/getJobDetail")
        assert body == {"jobUnionId": "4555593816"}
        return httpx.Response(
            200,
            json={"status": 1, "message": "成功", "data": detail_row},
            request=request,
        )

    postings = asyncio.run(
        MeituanCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发实习生",
            location="北京",
            limit=5,
        )
    )

    assert calls == [
        "/api/official/job/getJobList",
        "/api/official/job/getJobDetail",
    ]
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "meituan"
    assert posting.company == "美团"
    assert posting.job_type == "实习/校园招聘"
    assert posting.location == "北京市、上海市"
    assert "岗位职责" in posting.raw_jd_text
    assert "任职要求" in posting.raw_jd_text
    assert "LangGraph、MCP、多智能体协作和 Python" in posting.raw_jd_text
    assert "highlightType=campus" in posting.apply_url


def test_meituan_source_surfaces_detail_failure():
    list_row = {
        "jobUnionId": "broken-1",
        "name": "Agent开发实习生",
        "jobType": "2",
        "jobSpecialCode": "6",
        "cityList": [{"name": "深圳市"}],
        "jobDuty": "开发 Agent。",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getJobList"):
            return httpx.Response(
                200,
                json={"status": 1, "message": "成功", "data": {"list": [list_row]}},
                request=request,
            )
        return httpx.Response(
            200,
            json={"status": 0, "message": "岗位已下线", "data": None},
            request=request,
        )

    try:
        asyncio.run(
            MeituanCareersSource(transport=httpx.MockTransport(handler)).search(
                query="Agent 开发实习生",
                location=None,
                limit=5,
            )
        )
    except ValueError as exc:
        assert "岗位已下线" in str(exc)
    else:
        raise AssertionError("detail failure must be surfaced to source_errors")


def test_bytedance_source_maps_signed_json_payload_without_dom_scraping():
    calls: list[tuple[str, int]] = []

    async def payload_loader(query: str, request_size: int) -> dict:
        calls.append((query, request_size))
        return {
            "code": 0,
            "message": "ok",
            "data": {
                "job_post_list": [
                    {
                        "id": "7617786273006717189",
                        "title": "大模型算法实习生（AI Agent方向）",
                        "description": "研发 Agent 规划、记忆和工具调用能力。",
                        "requirement": "熟悉 Python、强化学习和 Agent 评测。",
                        "code": "A258728",
                        "city_list": [{"name": "上海", "i18n_name": "上海"}],
                        "recruit_type": {"name": "实习", "i18n_name": "实习"},
                        "job_subject": {"name": {"zh_cn": "日常实习", "i18n": "日常实习"}},
                        "job_category": {"name": "算法", "i18n_name": "算法"},
                    }
                ]
            },
        }

    postings = asyncio.run(
        ByteDanceCareersSource(payload_loader=payload_loader).search(
            query="Agent 开发实习生",
            location="上海",
            limit=5,
        )
    )

    assert calls == [("Agent", 20)]
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "bytedance"
    assert posting.company == "字节跳动"
    assert posting.job_type == "日常实习 / 实习"
    assert posting.location == "上海"
    assert "Agent 规划、记忆和工具调用能力" in posting.raw_jd_text
    assert "Python、强化学习和 Agent 评测" in posting.raw_jd_text
    assert posting.apply_url.endswith("/7617786273006717189/detail")


def test_alibaba_source_discovers_internship_batches_and_searches_full_jd():
    calls: list[str] = []
    row = {
        "id": 199903480006,
        "name": "Agent Infra工程师",
        "workLocations": ["北京", "杭州", "上海"],
        "description": "建设 Agent 基础设施、记忆、工具编排和可观测体系。",
        "requirement": "熟悉 Python、LangGraph、MCP、RAG 和高并发系统。",
        "batchName": "阿里巴巴2027届实习生",
        "categoryName": "技术类",
        "circleNames": ["阿里云", "千问C端事业群"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/campus/position":
            return httpx.Response(
                200,
                text="<html>campus careers</html>",
                headers={"set-cookie": "XSRF-TOKEN=test-csrf; Path=/"},
                request=request,
            )
        assert request.url.params["_csrf"] == "test-csrf"
        body = json.loads(request.content.decode("utf-8"))
        if request.url.path == "/searchCondition/listBatch":
            assert body["channel"] == "campus_group_official_site"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "content": {
                        "internship": [
                            {
                                "id": 100000540002,
                                "name": "阿里巴巴2027届实习生",
                            }
                        ]
                    },
                },
                request=request,
            )
        assert request.url.path == "/position/search"
        assert body["batchId"] == 100000540002
        assert body["searchKey"] == "Agent"
        return httpx.Response(
            200,
            json={"success": True, "content": {"datas": [row]}},
            request=request,
        )

    postings = asyncio.run(
        AlibabaCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发实习生",
            location="杭州",
            limit=5,
        )
    )

    assert calls == [
        "/campus/position",
        "/searchCondition/listBatch",
        "/position/search",
    ]
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "alibaba"
    assert posting.company == "阿里巴巴"
    assert posting.location == "北京、杭州、上海"
    assert "Agent 基础设施、记忆、工具编排和可观测体系" in posting.raw_jd_text
    assert "Python、LangGraph、MCP、RAG 和高并发系统" in posting.raw_jd_text
    assert posting.apply_url.endswith("/199903480006?batchId=100000540002")
