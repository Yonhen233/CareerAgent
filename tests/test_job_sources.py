import asyncio
import json
from urllib.parse import parse_qs

import httpx

from app.services.job_sources import (
    AlibabaCareersSource,
    AntGroupCareersSource,
    BaiduCareersSource,
    BilibiliCareersSource,
    ByteDanceCareersSource,
    ChinaTelecomCareersSource,
    DidiCareersSource,
    HonorCareersSource,
    HuaweiCareersSource,
    IFlytekCareersSource,
    JDCareersSource,
    KuaishouCareersSource,
    LenovoCareersSource,
    MeituanCareersSource,
    MideaCareersSource,
    MokaChinaCareersSource,
    MokaCareerSite,
    MiniMaxCareersSource,
    NetEaseCareersSource,
    OPPOCareersSource,
    Qihu360CareersSource,
    SkyworthCareersSource,
    TCLCareersSource,
    WindCareersSource,
    XiaomiCareersSource,
    VivoCareersSource,
    XiaohongshuCareersSource,
    ZhipuCareersSource,
    JobPosting,
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


def test_jd_source_maps_public_json_with_complete_agent_jd():
    row = {
        "requirementId": 221760,
        "positionNameOpen": "AI Agent开发工程师",
        "positionDeptName": "京东集团",
        "workCity": "北京市",
        "jobType": "研发类",
        "workContent": "负责 Agent Loop、工具调用和 Graph RAG。",
        "qualification": "熟悉 Python、LangGraph、Rerank 和模型评测。",
        "reqNumber": "ZP2607308944",
        "formatPublishTime": "2026-07-30",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/web/job/job_list"
        assert b"jobSearch=Agent" in request.content
        return httpx.Response(200, json=[row], request=request)

    postings = asyncio.run(
        JDCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发",
            location="北京",
            limit=5,
        )
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "jd"
    assert posting.company == "京东"
    assert posting.external_id == "221760"
    assert posting.location == "北京市"
    assert "Graph RAG" in posting.raw_jd_text
    assert "LangGraph、Rerank" in posting.raw_jd_text
    assert posting.apply_url.endswith("requementId=221760")


def test_tcl_source_parses_official_html_then_loads_detail():
    list_html = """
    <div class="item proInfoConList">
      <div class="head" data-postid="agent-1" data-recruitType="1">
        <div class="name">Agent工程师</div>
        <div class="tag">
          <span>格创东智（深圳）科技有限公司</span><span>武汉市</span>
          <span>研发技术类</span><span>若干</span><span>2026-08-26 09:15:34</span>
        </div>
      </div>
      <a data-id="agent-1" href="https://wecruit.hotjob.cn/pos/agent-1" class="tool-btn">立即申请</a>
    </div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/campus_search.html"):
            assert b"keys=Agent" in request.content
            return httpx.Response(
                200,
                json={"title": "success", "total_counts": 1, "content": list_html},
                request=request,
            )
        assert request.url.path.endswith("/job_detail.html")
        assert request.url.params["postid"] == "agent-1"
        return httpx.Response(
            200,
            json={
                "title": "success",
                "workContent": "1. 负责 <b>Agent</b> 应用研发；<br/>2. 建设 RAG。",
                "serviceCondition": "熟悉 Python、LangGraph 与模型评测。",
            },
            request=request,
        )

    postings = asyncio.run(
        TCLCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发",
            location="武汉",
            limit=5,
        )
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "tcl"
    assert posting.company == "格创东智（深圳）科技有限公司"
    assert posting.location == "武汉市"
    assert posting.job_type == "校园招聘"
    assert "负责\nAgent\n应用研发" in posting.raw_jd_text
    assert "Python、LangGraph" in posting.raw_jd_text
    assert posting.apply_url == "https://wecruit.hotjob.cn/pos/agent-1"


def test_wind_source_reads_generated_position_dataset():
    rows = [
        {
            "ChannelPositionID": 662,
            "ChannelPositionName": "大模型算法工程师（实习）",
            "PositionType": 9002,
            "PositionTypeText": "校招",
            "PositionClassName": "软件研发类",
            "WorkPlace": [{"Name": "上海"}],
            "Projects": [{"Name": "实习生招聘"}],
            "ChannelPositionDesc": "研发 LLM、Agent 与 AI 应用。",
            "ChannelPositionRequirement": "熟悉 Python、RAG 和向量检索。",
            "PublishDate": "2026-08-01",
            "InUse": True,
        }
    ]
    script = f"(function () {{ var channelPositions = {json.dumps(rows, ensure_ascii=False)}; }})();"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=script, request=request)

    postings = asyncio.run(
        WindCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发实习生",
            location="上海",
            limit=5,
        )
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "wind"
    assert posting.company == "Wind 万得"
    assert posting.job_type == "校招 / 实习生招聘"
    assert "Agent 与 AI 应用" in posting.raw_jd_text
    assert posting.apply_url.endswith("channelPositionId=662&positionType=9002")


def test_midea_source_uses_multiple_agent_query_terms_and_deduplicates():
    row = {
        "positionId": "midea-agent-1",
        "demandCode": "P20260001",
        "publicationName": "具身智能机器人智能体平台高级研究员",
        "superiorUnitName": "中央研究院",
        "workingPlace": "上海市-上海市,广东省-佛山",
        "postDuties": "负责智能体平台、工具编排和多智能体协作。",
        "qualification": "熟悉 Python、大模型、RAG 与 Agent 评测。",
        "education": "硕士及以上",
    }
    calls: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(200, json={"data": [row], "total": 1}, request=request)

    postings = asyncio.run(
        MideaCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发",
            location="上海",
            limit=5,
        )
    )

    assert len(calls) == 2
    assert any(b"publicationName=Agent" in call for call in calls)
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "midea"
    assert posting.company == "美的集团"
    assert "工具编排和多智能体协作" in posting.raw_jd_text
    assert posting.apply_url.endswith("positionId=midea-agent-1")


def test_xiaomi_source_uses_public_search_api_and_deduplicates():
    row = {
        "id": 315048,
        "title": "agent应用实习生-2027届",
        "cityZhNames": ["北京"],
        "levelOneDeptName": "手机部",
        "description": "参与设计 Agent 任务规划、工具调用与多步推理能力。",
        "requirement": "熟悉 Python、Prompt Engineering 和主流 Agent 框架。",
        "publishTime": "2026-07-14",
        "larkJobCode": "A24998",
        "type": 3,
        "url": "https://xiaomi.jobs.f.mioffice.cn/internship/position/7662271237599725834/detail",
        "jobPostId": "7662271237599725834",
    }
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={"code": 0, "message": "成功", "data": {"list": [row], "total": 1}},
            request=request,
        )

    postings = asyncio.run(
        XiaomiCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发",
            location="北京",
            limit=5,
        )
    )

    assert {call["keyword"] for call in calls} == {"Agent", "大模型"}
    assert all(call["cityZhNames"] == "北京" for call in calls)
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "xiaomi"
    assert posting.company == "小米集团"
    assert posting.job_type == "实习"
    assert "任务规划、工具调用" in posting.raw_jd_text
    assert "Prompt Engineering" in posting.raw_jd_text
    assert posting.apply_url == row["url"]


def test_oppo_source_uses_public_campus_api_and_maps_complete_jd():
    row = {
        "idProjPosition": 1850,
        "projectName": "2027届应届生校园招聘",
        "recruitmentTypeName": "应届生",
        "positionName": "AI Agent 研发工程师（研发效能方向）",
        "positionDesc": "参与 Coding Agent、测试 Agent 和多智能体协同开发。",
        "positionRequire": "熟悉 Python、MCP、Agent Skills 和 GitHub。",
        "knowledgeSkill": "理解 Harness Engineering 和 Agent 评测。",
        "workCityName": "深圳市",
        "releaseTime": "2026-07-15",
    }
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"code": 0, "data": {"records": [row], "total": 1}, "msg": "success"},
            request=request,
        )

    postings = asyncio.run(
        OPPOCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发",
            location="深圳",
            limit=5,
        )
    )

    assert {call["positionName"] for call in calls} == {"Agent", "大模型"}
    assert all(call["projectList"] == [] for call in calls)
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "oppo"
    assert posting.company == "OPPO"
    assert posting.job_type == "应届生"
    assert "多智能体协同" in posting.raw_jd_text
    assert "Harness Engineering" in posting.raw_jd_text
    assert posting.apply_url.endswith("/post/1850")


def test_skyworth_source_searches_campus_jobs_then_enriches_detail():
    list_row = {
        "postId": "6a7184c368cc6f624f1a2add",
        "postName": "算法工程师（酷开）",
        "company": "创维集团有限公司",
        "department": "深圳市酷开网络科技股份有限公司",
        "workPlaceStr": "深圳市",
        "postTypeName": "算法",
        "recruitType": 1,
        "projectName": "2027届秋季校园招聘",
        "educationStr": "硕士研究生及以上",
    }
    detail_row = {
        **list_row,
        "orgName": "深圳市酷开网络科技股份有限公司",
        "workContent": "参与构建多智能体协作框架、Agentic Workflow 和工具调用机制。",
        "serviceCondition": "熟悉 Python、LangChain、RAG 和 Agent 运行机制。",
        "education": "硕士研究生及以上",
        "subject": "计算机科学、人工智能、软件工程",
        "publishDate": "2026-07-31 15:50:40",
        "postCode": "Skyworthhr005550",
    }
    list_calls: list[dict[str, str]] = []
    detail_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        if request.url.path.endswith("/wecruit/common/getSLD"):
            assert body == {"sld": "skyworth.hotjob.cn"}
            return httpx.Response(
                200,
                json={
                    "state": "200",
                    "data": {
                        "linkData": {
                            "link": "https://skyworth.hotjob.cn/SU668b8b251c240e2e76ea71d8/pb/index.html#/"
                        }
                    },
                },
                request=request,
            )
        if "/listPosition/" in request.url.path:
            list_calls.append(body)
            return httpx.Response(
                200,
                json={
                    "state": "200",
                    "data": {
                        "pageForm": {"pageData": [list_row], "currentPage": 1, "totalPage": 1},
                        "positonNum": 1,
                    },
                },
                request=request,
            )
        detail_calls.append(body["postId"])
        return httpx.Response(200, json={"state": "200", "data": detail_row}, request=request)

    postings = asyncio.run(
        SkyworthCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发",
            location="深圳",
            limit=5,
        )
    )

    assert {call["postKey"] for call in list_calls} == {"Agent", "%E5%A4%A7%E6%A8%A1%E5%9E%8B"}
    assert all(call["recruitType"] == "1" for call in list_calls)
    assert detail_calls == [list_row["postId"]]
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "skyworth"
    assert posting.company == "深圳市酷开网络科技股份有限公司"
    assert "2027届秋季校园招聘" in posting.job_type
    assert "Agentic Workflow" in posting.raw_jd_text
    assert "Python、LangChain、RAG" in posting.raw_jd_text
    assert "posDetail.html?postId=6a7184c368cc6f624f1a2add" in posting.apply_url


def test_moka_china_source_preserves_per_company_source_identity():
    async def loader(query: str, limit: int) -> list[JobPosting]:
        assert query == "Agent"
        assert limit == 5
        return [
            JobPosting(
                source="moka_shokz",
                external_id="agent-intern-1",
                title="AI 创新专项实习生（Agent 开发方向）",
                company="韶音科技",
                location="深圳",
                job_type="实习",
                apply_url="https://app.mokahr.com/campus-recruitment/aftershokzhr/36940#/job/agent-intern-1",
                raw_jd_text="开发 Agent、RAG、工具调用和评测能力。",
            )
        ]

    postings = asyncio.run(
        MokaChinaCareersSource(posting_loader=loader).search(
            query="Agent 开发实习生",
            location="深圳",
            limit=5,
        )
    )

    assert len(postings) == 1
    assert postings[0].source == "moka_shokz"
    assert postings[0].company == "韶音科技"


def test_moka_apply_site_builds_moonshot_jobs_url():
    site = MokaCareerSite("moonshot", "月之暗面", "apply", "moonshot", 148506)

    assert site.jobs_url == (
        "https://app.mokahr.com/apply/moonshot/148506"
        "#/jobs?page=1&anchorName=jobsList"
    )


def test_moka_site_supports_company_owned_careers_host():
    site = MokaCareerSite(
        "dji", "大疆创新", "social", "dji", 170070, "apply.careers.dji.com"
    )

    assert site.jobs_url == (
        "https://apply.careers.dji.com/social-recruitment/dji/170070"
        "#/jobs?page=1&anchorName=jobsList"
    )


def test_didi_source_enriches_list_row_with_complete_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/front/list"):
            assert request.url.params["jobName"] == "Agent"
            return httpx.Response(
                200,
                json={"data": {"items": [{"jdId": 65493, "jobName": "Agent平台研发工程师"}]}},
                request=request,
            )
        assert request.url.path.endswith("/front/view/65493")
        return httpx.Response(
            200,
            json={
                "data": {
                    "jdId": 65493,
                    "jobName": "Agent平台研发工程师",
                    "workArea": "北京市",
                    "deptName": "AI平台部",
                    "recruitType": "1",
                    "jobDesc": "建设 Agent Runtime、工具注册、记忆和上下文压缩。",
                    "qualification": "熟悉 Python、RAG、向量数据库和 Agent 评测。",
                    "jdNo": "A65493",
                }
            },
            request=request,
        )

    postings = asyncio.run(
        DidiCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发", location="北京", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].company == "滴滴"
    assert "上下文压缩" in postings[0].raw_jd_text
    assert "向量数据库" in postings[0].raw_jd_text
    assert postings[0].apply_url.endswith("/social/detail/65493")


def test_honor_source_enriches_internship_detail():
    row = {
        "postId": "honor-agent-1",
        "postName": "大模型算法工程师",
        "workPlaceStr": "深圳市",
        "company": "AI与软件业务部",
        "workTypeStr": "实习",
        "projectName": "2026年实习生",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        if "/listPosition/" in request.url.path:
            assert body["postKey"] == ["Agent"]
            return httpx.Response(
                200,
                json={"data": {"pageForm": {"pageData": [row]}}},
                request=request,
            )
        assert body["postId"] == ["honor-agent-1"]
        return httpx.Response(
            200,
            json={
                "data": {
                    **row,
                    "workContent": "研发 Agentic LLM、任务规划和工具调用。",
                    "serviceCondition": "熟悉 Python、RAG 和模型评测。",
                    "education": "硕士研究生",
                }
            },
            request=request,
        )

    postings = asyncio.run(
        HonorCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发实习生", location="深圳", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].company == "AI与软件业务部"
    assert "Agentic LLM" in postings[0].raw_jd_text
    assert "模型评测" in postings[0].raw_jd_text
    assert "recruitType=12" in postings[0].apply_url


def test_kuaishou_source_signs_and_combines_social_and_intern_jobs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("sign")
        assert request.headers.get("signTimestamp")
        nature = request.url.params["positionNatureCode"]
        row = {
            "id": 32418 if nature == "C001" else 32419,
            "name": "Agent Infra 研发工程师" if nature == "C001" else "Agent开发实习生",
            "positionNatureCode": nature,
            "positionCategoryCode": "工程类",
            "workLocationCode": "Beijing",
            "description": "建设 Agent Runtime、任务规划、工具调用和多 Agent 协同。",
            "positionDemand": "熟悉 Python、RAG、LangGraph 和 Prompt 安全。",
        }
        return httpx.Response(
            200,
            json={"code": 0, "message": "ok", "result": {"list": [row], "total": 1}},
            request=request,
        )

    postings = asyncio.run(
        KuaishouCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="北京", limit=5
        )
    )

    assert len(postings) == 2
    assert {posting.job_type for posting in postings} == {"社会招聘", "日常实习"}
    assert all("LangGraph" in posting.raw_jd_text for posting in postings)
    assert any("/trainee/job-info/" in posting.apply_url for posting in postings)


def test_lenovo_source_maps_html_jd_and_city_code():
    row = {
        "id": 2404,
        "jobName": "AI Agent开发工程师",
        "workPlace": "1",
        "typeName": "AI开发类",
        "educationRequired": "本科及以上",
        "jobDuties": "<p>建设 Agent Runtime、Skill 和工具调用。<br>优化上下文管理。</p>",
        "jobRequirement": "<p>熟悉 Python、LangGraph、RAG 和多智能体。</p>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keyword"] == "Agent"
        return httpx.Response(
            200, json={"code": 0, "message": "成功", "result": {"rows": [row]}}, request=request
        )

    postings = asyncio.run(
        LenovoCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="北京", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].location == "北京"
    assert "上下文管理" in postings[0].raw_jd_text
    assert "<p>" not in postings[0].raw_jd_text
    assert postings[0].apply_url.endswith("detail?id=2404")


def test_vivo_source_filters_irrelevant_rows_and_keeps_complete_jd():
    rows = [
        {
            "job_id": "vivo-agent-1",
            "job_title": "AI解决方案FDE",
            "requirement_org_name": "AI平台",
            "job_location_list": [{"city": "深圳", "location": "宝安区"}],
            "job_desc": "负责 Agent 编排、RAG、Skill、向量数据库与评测平台。",
        },
        {
            "job_id": "vivo-sales-1",
            "job_title": "渠道销售",
            "job_location_list": [{"city": "深圳"}],
            "job_desc": "负责渠道销售。",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["keyword"] == "Agent"
        return httpx.Response(200, json={"code": 0, "message": "success", "data": rows}, request=request)

    postings = asyncio.run(
        VivoCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="深圳", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].external_id == "vivo-agent-1"
    assert "向量数据库" in postings[0].raw_jd_text


def test_netease_source_searches_current_projects_and_filters_locally():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/project/navigation/list"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": [
                        {
                            "children": [
                                {
                                    "title": "网易互联网2027届校园招聘",
                                    "link": "https://campus.163.com/app/job/position?id=103",
                                },
                                {
                                    "title": "网易互娱2027届校园招聘",
                                    "link": "https://campus.game.163.com/app/job/position?id=102",
                                },
                            ]
                        }
                    ],
                },
                request=request,
            )
        project_id = int(request.url.params["projectId"])
        rows = []
        if project_id == 103:
            rows.append(
                {
                    "id": 4858,
                    "positionName": "AI Agent 应用开发工程师-网易有道",
                    "positionTypeName": "校园招聘",
                    "workPlaceName": "北京市",
                    "positionDescription": "研发 Agent 工作流、RAG 和工具调用。",
                    "positionRequirement": "熟悉 Python、LLM、向量检索和评测。",
                }
            )
        return httpx.Response(200, json={"data": {"list": rows, "total": len(rows)}}, request=request)

    postings = asyncio.run(
        NetEaseCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="北京", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].company == "网易互联网2027届校园招聘"
    assert postings[0].external_id == "103:4858"
    assert "向量检索" in postings[0].raw_jd_text
    assert "projectId=103" in postings[0].apply_url


def test_xiaohongshu_source_queries_social_and_campus_with_complete_jd():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["positionName"] == "Agent"
        row = {
            "positionId": f"xhs-{body['recruitType']}",
            "positionName": "Agent开发实习生",
            "workplace": "上海",
            "jobProjectName": "技术实习",
            "duty": "负责 Agent Runtime、工具调用和 RAG。",
            "qualification": "熟悉 Python、评测与向量检索。",
        }
        return httpx.Response(200, json={"statusCode": 200, "data": {"list": [row]}}, request=request)

    postings = asyncio.run(
        XiaohongshuCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="上海", limit=5
        )
    )

    assert len(postings) == 2
    assert {posting.job_type for posting in postings} == {"社会招聘", "校园招聘"}
    assert all("向量检索" in posting.raw_jd_text for posting in postings)
    assert all("/position/" in (posting.apply_url or "") for posting in postings)


def test_bilibili_source_bootstraps_csrf_and_maps_both_recruitment_routes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/csrf/token"):
            return httpx.Response(200, json={"code": 0, "data": "csrf-token"}, request=request)
        assert request.headers["X-CSRF"] == "csrf-token"
        body = json.loads(request.content)
        assert body["positionName"] == "Agent"
        route = "campus" if "/campus/" in request.url.path else "social"
        return httpx.Response(
            200,
            json={"data": {"list": [{
                "id": f"bili-{route}", "positionName": "AI Agent 研发实习生",
                "workLocation": ["上海"], "positionTypeName": "技术",
                "positionDescription": "建设 Agent Harness、RAG 与自动评测。",
            }]}},
            request=request,
        )

    postings = asyncio.run(
        BilibiliCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="上海", limit=5
        )
    )

    assert len(postings) == 2
    assert all("Agent Harness" in posting.raw_jd_text for posting in postings)
    assert {posting.apply_url.split("/")[3] for posting in postings} == {"campus", "social"}


def test_antgroup_source_uses_bounded_pages_and_filters_complete_jd():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["pageSize"] == 20
        assert body["pageIndex"] in {1, 2, 3}
        rows = []
        if body["pageIndex"] == 1:
            rows.append({
                "id": "ant-agent-1", "name": "智能体算法实习生",
                "workLocations": [{"name": "杭州"}], "categoryName": "算法",
                "batchTypeDesc": "实习生", "description": "研发 Long-horizon Agent。",
                "requirement": "熟悉强化学习、LLM 和 Python。",
            })
        return httpx.Response(200, json={"success": True, "content": rows}, request=request)

    postings = asyncio.run(
        AntGroupCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="杭州", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].company == "蚂蚁集团"
    assert "Long-horizon Agent" in postings[0].raw_jd_text


def test_qihu360_source_loads_official_details_before_filtering():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"
        if request.url.path.endswith("/getlistsearch"):
            return httpx.Response(
                200,
                json={"code": 0, "data": [
                    {"id": 1, "title": "大模型算法工程师", "area": "北京", "type": "技术"},
                    {"id": 2, "title": "销售经理", "area": "北京", "type": "销售"},
                ]},
                request=request,
            )
        job_id = request.url.params["id"]
        details = {
            "1": {"id": 1, "title": "大模型算法工程师", "area": "北京", "description": "负责 Agent 平台。", "qualification": "熟悉 RAG 与 Python。"},
            "2": {"id": 2, "title": "销售经理", "area": "北京", "description": "负责销售。"},
        }
        return httpx.Response(200, json={"code": 0, "data": details[job_id]}, request=request)

    postings = asyncio.run(
        Qihu360CareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发", location="北京", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].external_id == "1"
    assert "熟悉 RAG" in postings[0].raw_jd_text
    assert postings[0].apply_url == "https://hr.360.cn/hr/detail/1"


def test_minimax_source_preserves_official_card_granularity():
    async def loader(query: str, limit: int) -> list[JobPosting]:
        assert query == "Agent"
        assert limit == 20
        return [
            JobPosting(
                source="minimax",
                external_id="card-1",
                title="Agent 服务端开发实习生",
                company="MiniMax",
                location="北京、上海",
                job_type="实习",
                apply_url="https://vrfi1sk8a0.jobs.feishu.cn/379481/?keywords=Agent",
                raw_jd_text="开发 AI Agent / AI APP，建设 Agent Runtime。",
                payload={"granularity": "official_job_card"},
            )
        ]

    postings = asyncio.run(
        MiniMaxCareersSource(posting_loader=loader).search(
            query="Agent 开发实习生", location="上海", limit=5
        )
    )

    assert len(postings) == 1
    assert postings[0].payload["granularity"] == "official_job_card"


def test_zhipu_source_exposes_category_level_data_honestly():
    html = """
    <h3>算法/研发（社招）</h3><p>持续改进大模型训练框架和策略。</p>
    <h3>算法（校招）</h3><p>面向大模型算法、强化学习与评测。</p>
    <h3>运营（校招）</h3><p>结合大模型技术设计创新的 AI 解决方案。</p>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    postings = asyncio.run(
        ZhipuCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型开发", location="北京", limit=5
        )
    )

    assert len(postings) == 3
    assert all(posting.company == "智谱AI" for posting in postings)
    assert all(posting.payload["granularity"] == "category" for posting in postings)
    assert all("官网未公开单岗位完整 JD" in posting.raw_jd_text for posting in postings)


def test_china_telecom_source_parses_complete_jd_from_official_search_html():
    html = """
    <form id="searchForm"></form>
    <ul>
      <li class="position_list-list-demo">
        <div onclick="javascript:toDetailPostUrl(139062,1,1)">
          <div class="position_list-list-demo-title">算法工程师（AI Agent方向）</div>
          <div class="position_list-first-row"><span>中通服软件科技有限公司</span><span>上海市</span></div>
        </div>
        <div id="hidden139062">
          <div class="detailedInformation">招聘项目:<br>2027年度秋季校园招聘</div>
          <div class="detailedInformation">职位类别:<br>研发类</div>
          <div class="detailedInformation">学历要求:<br>硕士研究生及以上</div>
          <div class="detailedInformation">工作描述:<br>负责 Agent 任务规划、工具调用、记忆和错误恢复。</div>
          <div class="detailedInformation">职位要求:<br>熟悉 Python、RAG、LangChain 和评测。</div>
        </div>
      </li>
    </ul>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert b"keyWord=Agent" in request.content
        return httpx.Response(200, text=html, request=request)

    postings = asyncio.run(
        ChinaTelecomCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发",
            location="上海",
            limit=5,
        )
    )

    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "china_telecom"
    assert posting.external_id == "139062"
    assert posting.company == "中通服软件科技有限公司"
    assert posting.location == "上海市"
    assert "任务规划、工具调用" in posting.raw_jd_text
    assert "Python、RAG" in posting.raw_jd_text
    assert "postIdsAry=139062" in posting.apply_url


def test_huawei_source_loads_ai_zone_then_enriches_public_json_details():
    landing_html = """
    <ul>
      <li><a href="/reccampportal/portal5/social-recruitment-detail.html?jobId=27323&amp;dataSource=1">
        <h6>算法专家（多模态/大模型）</h6><p>中国/北京</p>
      </a></li>
    </ul>
    """
    detail = {
        "jobId": 27323,
        "jobCode": "AD2025092300009",
        "jobname": "算法专家（多模态/大模型）",
        "jobArea": "中国/北京",
        "deptName": "行业垂直作战组织",
        "jobFamilyName": "研发族",
        "mainBusiness": "建设 Agentic 大模型训练和推理系统。",
        "jobRequire": "熟悉 Python、大模型、RAG 和高并发服务。",
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("social-recruitment-ai.html"):
            return httpx.Response(200, text=landing_html, request=request)
        assert request.url.params["jobId"] == "27323"
        assert request.url.params["dataSource"] == "1"
        return httpx.Response(200, json=detail, request=request)

    postings = asyncio.run(
        HuaweiCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 大模型",
            location="北京",
            limit=5,
        )
    )

    assert len(calls) == 2
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "huawei"
    assert posting.company == "华为"
    assert posting.external_id == "27323"
    assert "Agentic 大模型" in posting.raw_jd_text
    assert "Python、大模型、RAG" in posting.raw_jd_text
    assert posting.apply_url.endswith("dataSource=1&jobId=27323")


def test_iflytek_source_uses_data_when_upstream_total_is_incorrect():
    row = {
        "Id": "agent-harness-1",
        "JobAdId": 190840001,
        "JobAdName": "Agent研发工程师-Harness方向(J13347)",
        "Category": "校园招聘",
        "CategoryId": "2",
        "LocNames": ["安徽省·合肥市", "北京市"],
        "Duty": "负责 Agent Harness、工具调用、上下文工程与错误恢复。",
        "Require": "熟悉 Python、LangGraph、RAG 和 Agent 评测。",
        "ClassificationOne": "AI研发类",
        "Kind": "",
        "ChangeDate": "2026-08-24T11:16:28",
    }
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        rows = [row] if payload["Category"] == ["2"] else []
        return httpx.Response(
            200,
            json={"Code": 200, "Message": "operation success", "Data": rows, "Total": 0},
            request=request,
        )

    postings = asyncio.run(
        IFlytekCareersSource(transport=httpx.MockTransport(handler)).search(
            query="Agent 开发",
            location="北京",
            limit=5,
        )
    )

    assert len(calls) == 3
    assert {call["Category"][0] for call in calls} == {"1", "2", "3"}
    assert all(call["KeyWords"] == "Agent" for call in calls)
    assert len(postings) == 1
    posting = postings[0]
    assert posting.source == "iflytek"
    assert posting.company == "科大讯飞"
    assert posting.location == "安徽省·合肥市、北京市"
    assert "Agent Harness" in posting.raw_jd_text
    assert "Python、LangGraph" in posting.raw_jd_text
    assert posting.apply_url.endswith("/campus/jobdetail/agent-harness-1")
