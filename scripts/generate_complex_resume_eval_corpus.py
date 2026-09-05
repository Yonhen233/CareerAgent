from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageEnhance, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evals" / "complex_resume_corpus"
PDF_DIR = OUTPUT / "pdfs"
RENDER_DIR = ROOT / "tmp" / "pdfs" / "complex-resume-corpus"
PAGE_SIZE = (810.0, 1087.0)
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD_PATH = Path(r"C:\Windows\Fonts\msyhbd.ttc")


CASES = [
    ("runtime_agent", "周昕", "Agent Runtime 开发实习生", ["Python", "LangGraph", "Redis", "PostgreSQL", "OpenTelemetry"], "实现基于租约的任务接管协议", "完成一百二十次故障注入实验", "面向长任务构建 checkpoint 恢复与幂等工具网关", "standard_one_page"),
    ("rag_engineer", "林然", "RAG 检索工程实习生", ["Python", "Elasticsearch", "Milvus", "BGE", "RRF"], "实现查询级 RRF 融合", "Recall@5 从 0.81 提升至 0.94", "构建中文企业知识库的混合召回与重排链路", "standard_one_page"),
    ("llm_safety", "赵一宁", "大模型安全评测实习生", ["Python", "Guardrails", "Prompt Injection", "pytest", "LLM Judge"], "构造三百六十条提示词注入样例", "高风险指令召回率达到 96.7%", "建立越权工具调用与事实幻觉的发布门禁", "standard_one_page"),
    ("enterprise_agent", "王璐", "企业 Agent 应用开发实习生", ["Python", "FastAPI", "MCP", "MySQL", "Docker"], "交付合同审核 Agent 工作流", "人工复核耗时降低 38%", "把文档检索、条款比对和审批节点接入企业流程", "standard_one_page"),
    ("agent_infra", "陈航", "Agent 基础设施实习生", ["Python", "Kubernetes", "gRPC", "Redis", "Prometheus"], "实现工具执行沙箱与资源配额", "P95 调用延迟稳定在 420 毫秒", "建设多租户 Agent 网关和运行观测平台", "two_column_one_page"),
    ("ai_product", "孙悦", "AI Agent 产品实习生", ["需求分析", "原型设计", "SQL", "A/B 测试", "质量评测"], "设计客服转人工的置信度策略", "试点问题解决率提升 17 个百分点", "从用户访谈到灰度上线推动智能客服工作流", "two_column_one_page"),
    ("multimodal_agent", "李思远", "多模态 Agent 算法实习生", ["Python", "PyTorch", "OCR", "Vision Language Model", "RAG"], "实现图文证据对齐模块", "票据字段准确率达到 93.4%", "开发面向复杂文档的视觉问答 Agent", "two_column_one_page"),
    ("speech_agent", "郑清", "语音 Agent 开发实习生", ["Python", "ASR", "TTS", "WebSocket", "VAD"], "实现可打断的流式语音状态机", "首字延迟由 1.8 秒降低至 760 毫秒", "构建支持实时打断和上下文续接的语音助手", "two_column_one_page"),
    ("data_agent", "方可", "数据分析 Agent 实习生", ["Python", "SQL", "dbt", "Airflow", "Text-to-SQL"], "实现 SQL 静态检查与只读执行网关", "复杂查询执行正确率达到 88.2%", "让 Agent 在数仓语义层生成可审计分析结果", "dense_two_page"),
    ("nlp_research", "黄昭", "NLP 与 Agent 研究实习生", ["Python", "PyTorch", "Transformers", "RAG", "LoRA"], "提出证据覆盖约束的检索训练目标", "在中文长文问答集上 nDCG@10 提升 6.3%", "研究长上下文 Agent 的检索和引用一致性", "dense_two_page"),
    ("coding_agent", "陆鸣", "Coding Agent 开发实习生", ["Python", "Tree-sitter", "Docker", "MCP", "Git"], "实现仓库级符号索引和增量上下文", "修复任务一次通过率由 42% 提升至 68%", "构建可执行测试并回放工具轨迹的代码 Agent", "dense_two_page"),
    ("service_agent", "宋雨", "智能客服 Agent 后端实习生", ["Java", "Spring Boot", "Python", "Kafka", "RAG"], "实现会话摘要与坐席接管协议", "峰值吞吐达到每秒 860 个事件", "建设客服知识检索、工具调用和人工接管链路", "dense_two_page"),
    ("mobile_agent", "顾晨", "端侧 Agent 工程实习生", ["C++", "Python", "ONNX", "Android", "量化"], "完成端侧意图模型 INT8 量化", "模型包体积减少 61%", "研究移动端 Agent 的离线推理与隐私保护", "research_two_page"),
    ("compliance_agent", "唐琪", "金融合规 Agent 研究实习生", ["Python", "知识图谱", "RAG", "规则引擎", "审计"], "构建法规条款时效性追踪图谱", "引用可追溯率达到 99.1%", "研究证据约束生成在金融合规审查中的应用", "research_two_page"),
    ("robotics_agent_scan", "吴桐", "机器人 Agent 算法实习生", ["Python", "ROS2", "强化学习", "行为树", "仿真"], "实现行为树与语言规划器的双向校验", "仿真任务成功率达到 84.6%", "构建可恢复的移动机器人任务规划 Agent", "scan_two_column"),
    ("bilingual_agent_mixed", "Emma Zhou", "Bilingual Agent Platform Intern", ["Python", "LangGraph", "TypeScript", "RAG", "Evaluation"], "Built a bilingual tool-result grounding pipeline", "Reduced unsupported claims from 11.8% to 2.1%", "Delivered a Chinese-English research assistant with citation verification", "mixed_text_scan"),
]


def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("ResumeCN", str(FONT_PATH)))
    pdfmetrics.registerFont(TTFont("ResumeCN-Bold", str(FONT_BOLD_PATH if FONT_BOLD_PATH.exists() else FONT_PATH)))


def _sections(case: tuple) -> tuple[list[tuple[str, str]], list[dict]]:
    key, name, role, stack, fact_a, fact_b, focus, _layout = case
    english = key == "bilingual_agent_mixed"
    if english:
        sections = [
            ("PROFILE", f"{name} | {role} | emma.zhou.eval@example.com | Shanghai / Remote | Available for 6 months. Focused on grounded Agent workflows, durable execution, multilingual retrieval and observable tool use. Comfortable turning ambiguous user goals into typed plans while keeping evidence, permissions and completion criteria explicit."),
            ("EDUCATION", "Tongji University, M.Eng. in Software Engineering, 2025.09-2028.06, GPA 3.86/4.00. Research topics include LLM application engineering, information retrieval and reliable distributed workflows. South China University of Technology, B.Eng. in Computer Science, 2021.09-2025.06, outstanding graduate. Coursework: databases, distributed systems, machine learning, natural language processing, software testing and human-computer interaction."),
            ("TECHNICAL SKILLS", "Languages: Python, TypeScript and SQL. Agent: LangGraph state graphs, tool contracts, checkpoint recovery, bounded retries, human approval and completion gates. Retrieval: RAG, multilingual embeddings, BM25, exact match, multi-query, RRF, reranking and citation verification. Engineering: FastAPI, Redis, PostgreSQL, Docker, pytest and OpenTelemetry. Evaluation: Recall@K, MRR, nDCG, task success, grounding rate, latency percentiles and provider token accounting."),
            ("INTERNSHIP EXPERIENCE", f"AI Platform Intern, Mock Cloud Lab, 2026.03-2026.08. {focus}. {fact_a}. Added typed tool schemas, idempotency keys, bounded exponential backoff and trace dashboards; collaborated with product and QA to convert twelve failure reports into regression cases. Separated transient network retries from semantic repair, and prevented outbound side effects from being replayed after uncertain responses.\nResearch Engineering Intern, Mock Knowledge Systems Group, 2025.07-2025.12. Implemented document ingestion, metadata filters and citation-level evaluation for bilingual technical reports. Diagnosed two-column reading-order failures and mixed text-scan PDFs, preserving page provenance through extraction and chunking. Built a review queue for unsupported claims and helped annotate hard negatives that share technical vocabulary but answer different user intents."),
            ("RESEARCH", "Studied query rewriting for mixed Chinese-English terminology. Compared single-query, multi-query and HyDE-style retrieval under the same candidate pool; reported Recall@5, MRR, nDCG and latency instead of relying on answer fluency. Designed ablations for query source, chunk boundaries, reranker input and evidence thresholds. Distinguished a relevant but insufficient passage from a contradictory passage, and required every generated conclusion to map back to a source span."),
            ("PROJECTS", f"Bilingual Research Assistant: {fact_b}. The system preserves source spans and rejects answers whose citations cannot be found verbatim. It rewrites long requests into complementary retrieval views, merges candidates with reciprocal-rank fusion and records which query retrieved each passage.\nAgent Replay Console: reconstructed state transitions and tool receipts from checkpoints, while separating business artifacts from conversational memory. Added stale-run recovery, checkpoint history inspection and idempotent artifact reconciliation after process crashes.\nEvaluation Harness: created adversarial cases for prompt injection, early stopping, repeated tool calls, malformed JSON, weak evidence and cross-user context leakage. Reports stage-level failures, pass^k, latency, token cost and regression provenance rather than a single opaque score.\nDocument Intake Pipeline: routed native text pages and scanned pages independently, detected repeated headers, retained page numbers and produced cross-page context only when a sentence or bullet genuinely continued."),
            ("LEADERSHIP & AWARDS", "Maintained an open-source evaluation toolkit, reviewed pull requests and wrote reproducible failure reports. Organized a twelve-week Agent engineering reading group covering retrieval, tool governance, durable execution and evaluation. Received a university innovation scholarship and a regional software design award. English CET-6; comfortable presenting technical design reviews in Chinese and English and writing bilingual engineering documentation."),
        ]
    else:
        sections = [
            ("个人概况", f"{name}｜{role}｜{key}@example.com｜北京 / 上海 / 深圳 / 远程｜可连续实习 6 个月。关注可验证的 Agent 工作流、检索增强生成、工具治理和工程落地，能够从需求澄清、原型实现推进到评测与运行观测。习惯把模糊需求拆成可检查的目标、证据和完成条件，并对模型输出保持可回溯的事实边界。"),
            ("教育背景", "浙江大学 软件工程硕士 2025.09-2028.06，GPA 3.88/4.00，研究方向为大模型应用工程与智能系统，参与可信生成与检索评测课题组。西安电子科技大学 计算机科学与技术学士 2021.09-2025.06，GPA 3.82/4.00，获优秀毕业设计。核心课程包括数据库系统、分布式系统、机器学习、自然语言处理、软件测试、计算机网络和人机交互；课程设计完成高并发任务队列与全文检索服务。"),
            ("专业技能", f"技术栈：{'、'.join(stack)}。掌握 Agent 状态编排、Tool Calling、结构化输出、Checkpoint、人工审批和运行级 Trace；熟悉向量检索、BM25、Exact Match、Multi-query、RRF、Reranker 与引用校验；具备 FastAPI 接口开发、数据库建模、并发控制、故障注入、单元测试和离线评测经验。能够区分传输重试、结构修复和业务补偿，为工具定义输入输出合同、权限、超时、幂等策略与熔断边界；使用 Recall@K、MRR、nDCG、任务完成率、事实支持率、P95 延迟和 Token 成本评价系统。"),
            ("实习经历", f"模拟云智实验室｜{role}｜2026.03-2026.08。{focus}。{fact_a}；将工具输入输出改为强类型合同，为外部动作增加审批、幂等键和审计记录；与产品、测试共同复盘十二类失败轨迹，将重复调用、证据不足和异常早停固化为回归样例。针对限流、连接中断和服务端错误实现有界指数退避，对参数错误和引用门控失败直接停止，避免多层重试放大调用量。建设运行看板，按模型路由、节点、成功状态统计延迟和 Token。\n模拟知识工程中心｜研发实习生｜2025.07-2025.12。负责文档接入、字段清洗、向量索引和质量看板，处理表格、双栏论文、扫描页和中英文混排材料；为每个结论保留来源页码与原文片段。发现先按关键词筛选再做向量召回会漏掉同义表达，改为语义候选与词法候选取并集，再统一重排。对无文字层、乱码文字层、重复页眉和跨页断句分别建立诊断信息，不让解析失败静默污染下游。"),
            ("科研经历", f"围绕“{focus}”开展小规模研究，比较单 Query、Multi-query 和查询改写在相同候选池中的差异；使用 Recall@K、MRR、nDCG、引用正确率与 P95 延迟评估，不以回答流畅度代替检索质量。设计困难负例，区分课程学习、计划尝试与真实交付，避免把未完成能力写成项目成果。进一步对 Chunk 长度、段落合并、跨页桥接和 Reranker 输入做消融，记录每次实验的数据版本、模型版本和阈值来源；当结果下降时逐例检查是抽取缺字、Query 偏移、召回遗漏还是重排反转。"),
            ("项目经历", f"核心项目：{focus}。{fact_b}；系统记录 Query、候选 Chunk、重排分数、工具回执和最终引用，质量门控不通过时明确失败。针对长指令生成互补 Query，将地点和排除条件作为有原文证据的元数据约束，避免把现居地误当求职地点。\n运行回放控制台：从持久化 Checkpoint 重建最小状态，展示计划、工具调用、产物、异常和恢复位置；外发动作只允许在审批后执行一次。增加 stale run 扫描、心跳、业务产物幂等对账和历史 checkpoint 回溯，处理“业务写入成功但图状态尚未保存”的崩溃窗口。\n评测工具箱：覆盖中文、英文和混合语言样例，加入长文档、相似术语、无证据声明、Prompt Injection、循环调用与跨用户上下文污染等 Bad Case。分别报告 PDF 抽取、Chunk 召回、工具轨迹、任务终态、事实引用、延迟和成本，不用单一平均分掩盖关键失败。\n证据审查器：把模型给出的匹配项映射到 JD 和简历原文，要求关键结论具有双向引用；同一要求不能同时出现在匹配和缺口中，替代条件满足后不再把其余选项误报为短板。"),
            ("校园与开源", "担任学院智能系统协会技术负责人，组织十二周 Agent 工程读书会和三次开源工作坊；维护一套简历 RAG 评测脚本，为新成员讲解检索、重排、证据门控和错误分析。参与开源项目 Issue 分类、测试补齐与文档审阅，能够写出可复现的错误步骤、期望行为和回归样例。获校级创新奖学金、软件设计竞赛二等奖，英语 CET-6；曾负责跨专业五人团队的迭代排期和技术评审。还负责整理需求变更、数据许可和模型版本，发布前逐项核对隐私脱敏、回滚预案与监控告警，并把线上反馈转化为可重复执行的验收场景。"),
        ]
    two_page = _layout in {"dense_two_page", "research_two_page", "mixed_text_scan"}
    expectations = [
        {
            "id": "distinctive_implementation",
            "query": f"这位{role}候选人在核心系统中完成了什么有辨识度的工程实现？",
            "expected_text": fact_a,
            "expected_page_no": 1,
        },
        {
            "id": "quantified_outcome",
            "query": f"这位{role}候选人最重要的量化成果是什么？",
            "expected_text": fact_b,
            "expected_page_no": 2 if two_page else 1,
        },
        {
            "id": "project_focus",
            "query": f"这位{role}候选人的核心项目解决了什么业务或技术问题？",
            "expected_text": (
                f"Bilingual Research Assistant: {fact_b}. The system preserves source spans"
                if english
                else f"核心项目：{focus}"
            ),
            "expected_page_no": 2 if two_page else 1,
        },
        {
            "id": "retrieval_evaluation",
            "query": "候选人如何评估检索质量和事实可靠性？",
            "expected_text": (
                "reported Recall@5, MRR, nDCG and latency"
                if english
                else "使用 Recall@K、MRR、nDCG、引用正确率与 P95 延迟评估"
            ),
            "expected_page_no": 2 if two_page else 1,
        },
    ]
    return sections, expectations


def _style(size: float, *, bold: bool = False, color: str = "#172033", leading: float | None = None) -> ParagraphStyle:
    return ParagraphStyle(
        name=f"s-{size}-{bold}-{color}",
        fontName="ResumeCN-Bold" if bold else "ResumeCN",
        fontSize=size,
        leading=leading or size * 1.34,
        textColor=HexColor(color),
        alignment=TA_LEFT,
        spaceAfter=0,
        splitLongWords=True,
    )


def _draw_paragraph(c: canvas.Canvas, text: str, x: float, y: float, width: float, style: ParagraphStyle) -> float:
    paragraph = Paragraph(text.replace("\n", "<br/>"), style)
    _w, height = paragraph.wrap(width, y - 28)
    paragraph.drawOn(c, x, y - height)
    return y - height


def _draw_header(c: canvas.Canvas, name: str, role: str, *, compact: bool = False) -> float:
    width, height = PAGE_SIZE
    top = height - 36
    c.setStrokeColor(HexColor("#182235"))
    c.setLineWidth(2)
    c.line(34, top, width - 34, top)
    y = _draw_paragraph(c, name, 36, top - 13, width - 72, _style(24 if not compact else 18, bold=True))
    y = _draw_paragraph(c, role, 36, y - 3, width - 72, _style(10, color="#36506f"))
    return y - 8


def _draw_section(c: canvas.Canvas, title: str, body: str, x: float, y: float, width: float, *, dense: bool) -> float:
    y = _draw_paragraph(c, title, x, y, width, _style(11.0 if dense else 13.0, bold=True, color="#075f8f"))
    c.setStrokeColor(HexColor("#9fb4c8"))
    c.setLineWidth(0.45)
    c.line(x, y - 2, x + width, y - 2)
    y = _draw_paragraph(c, body, x, y - 7, width, _style(8.5 if dense else 10.8, leading=11.4 if dense else 15.0))
    return y - (8 if dense else 12)


def _footer(c: canvas.Canvas, page_no: int, total: int) -> None:
    width, _height = PAGE_SIZE
    c.setFont("ResumeCN", 6.5)
    c.setFillColor(HexColor("#6b778c"))
    c.drawCentredString(width / 2, 17, f"CareerAgent 合成评测简历 | 第 {page_no} 页 / {total}")


def _render_text_pdf(case: tuple, target: Path, *, force_layout: str | None = None) -> None:
    key, name, role, _stack, _fact_a, _fact_b, _focus, layout = case
    layout = force_layout or layout
    sections, _ = _sections(case)
    c = canvas.Canvas(str(target), pagesize=PAGE_SIZE, pageCompression=1)
    c.setTitle(f"CareerAgent synthetic evaluation resume - {key}")
    width, _height = PAGE_SIZE
    if layout == "standard_one_page":
        y = _draw_header(c, name, role)
        y = _draw_paragraph(c, sections[0][1], 38, y, width - 76, _style(8.5, color="#36506f", leading=11.4)) - 7
        for title, body in sections[1:]:
            y = _draw_section(c, title, body, 38, y, width - 76, dense=True)
        _footer(c, 1, 1)
        c.showPage()
    elif layout in {"two_column_one_page", "scan_two_column"}:
        y = _draw_header(c, name, role)
        y = _draw_paragraph(c, sections[0][1], 38, y, width - 76, _style(8.2, color="#36506f", leading=11.0)) - 8
        gap = 18
        column = (width - 76 - gap) / 2
        left_y = y
        right_y = y
        for index, (title, body) in enumerate(sections[1:]):
            if index in {0, 1, 4}:
                left_y = _draw_section(c, title, body, 38, left_y, column, dense=True)
            else:
                right_y = _draw_section(c, title, body, 38 + column + gap, right_y, column, dense=True)
        _footer(c, 1, 1)
        c.showPage()
    else:
        groups = [sections[1:4], sections[4:]]
        for page_no, group in enumerate(groups, start=1):
            y = _draw_header(c, name if page_no == 1 else f"{name} | 经历续页", role, compact=page_no > 1)
            if page_no == 1:
                y = _draw_paragraph(c, sections[0][1], 44, y, width - 88, _style(10.2, color="#36506f", leading=14.0)) - 10
            for title, body in group:
                y = _draw_section(c, title, body, 44, y, width - 88, dense=False)
            _footer(c, page_no, 2)
            c.showPage()
    c.save()


def _page_image(pdf_path: Path, page_index: int, *, dpi: int = 170) -> Image.Image:
    document = pymupdf.open(pdf_path)
    try:
        pixmap = document[page_index].get_pixmap(dpi=dpi, alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        document.close()
    image = ImageEnhance.Contrast(image.convert("L")).enhance(0.88)
    image = image.filter(ImageFilter.GaussianBlur(radius=0.18))
    return image.rotate(0.28 if page_index % 2 == 0 else -0.22, resample=Image.Resampling.BICUBIC, fillcolor="white")


def _image_pdf(images: list[Image.Image], target: Path) -> None:
    c = canvas.Canvas(str(target), pagesize=PAGE_SIZE, pageCompression=1)
    width, height = PAGE_SIZE
    for image in images:
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=82, optimize=True)
        stream.seek(0)
        c.drawImage(ImageReader(stream), 0, 0, width=width, height=height)
        c.showPage()
    c.save()


def _mixed_pdf(text_pdf: Path, target: Path) -> None:
    source = PdfReader(str(text_pdf))
    second_scan = _page_image(text_pdf, 1)
    scan_path = target.with_suffix(".scan-page.pdf")
    _image_pdf([second_scan], scan_path)
    scan = PdfReader(str(scan_path))
    writer = PdfWriter()
    writer.add_page(source.pages[0])
    writer.add_page(scan.pages[0])
    with target.open("wb") as stream:
        writer.write(stream)
    scan_path.unlink()


def main() -> None:
    _register_fonts()
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in CASES:
        key, name, role, _stack, fact_a, fact_b, focus, layout = case
        output_path = PDF_DIR / f"{key}.pdf"
        base_path = RENDER_DIR / f"{key}-text.pdf"
        base_layout = "two_column_one_page" if layout == "scan_two_column" else layout
        _render_text_pdf(case, base_path, force_layout=base_layout)
        if layout == "scan_two_column":
            _image_pdf([_page_image(base_path, 0)], output_path)
        elif layout == "mixed_text_scan":
            _mixed_pdf(base_path, output_path)
        else:
            output_path.write_bytes(base_path.read_bytes())
        document = pymupdf.open(output_path)
        page_count = document.page_count
        document.close()
        sections, expectations = _sections(case)
        canonical_text = "\n\n".join(f"{title}\n{body}" for title, body in sections)
        manifest.append(
            {
                "id": key,
                "synthetic": True,
                "candidate_name": name,
                "target_role": role,
                "layout": layout,
                "expected_page_count": page_count,
                "canonical_text": canonical_text,
                "canonical_character_count": len("".join(canonical_text.split())),
                "expected_sections": [title for title, _ in sections[1:]],
                "critical_facts": [fact_a, fact_b, focus],
                "expected_profile": {
                    "name": name,
                    "target_role": role,
                    "skills": case[3],
                    "minimum_experience_entries": 2,
                    "minimum_project_entries": 4,
                    "has_research": True,
                    "has_campus_or_leadership": True,
                },
                "retrieval_expectations": expectations,
                "pdf_path": str(output_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            }
        )
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(manifest), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
