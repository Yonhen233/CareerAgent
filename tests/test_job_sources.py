import asyncio
import json

import httpx

from app.services.job_sources import (
    AlibabaCareersSource,
    BaiduCareersSource,
    ByteDanceCareersSource,
    ChinaTelecomCareersSource,
    HuaweiCareersSource,
    IFlytekCareersSource,
    JDCareersSource,
    MeituanCareersSource,
    MideaCareersSource,
    MokaChinaCareersSource,
    TCLCareersSource,
    WindCareersSource,
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
