# PDF Chunk 方案

## 目标

PDF Chunk 的目标不是简单按固定长度截断，而是让 RAG 证据可召回、可解释、可追踪：

- 知道技能来自结构化字段还是 PDF 页文本。
- 知道项目经历来自 PDF 第几页。
- 知道 chunk 的字符范围和切分策略。
- 能用评测数据选择合理的 chunk 参数。

## 当前选择

```text
paragraph_page_900_overlap160
```

也就是：

- 先按 PDF 页提取文本。
- 每页内部按段落合并。
- 如果段落过长，使用滑动窗口。
- chunk size = 900。
- overlap = 160。
- 保存 page_no、char_start、char_end、strategy 等 metadata。

## 为什么选择这个策略

在当前 22 个复杂 PDF 案例、89 条检索期望上，当前 `paragraph_page_900_overlap160` 通过了页数、章节、关键事实和页码回溯验收；其中包含扫描页、双栏、表格、文本框和句中跨页样本。历史参数对比保留在下表，后续会用真实 Embedding 和人工相关性标注集重新校准：

| 策略 | Top3 关键词 | Top3 页码 | Top3 上下文 | Top1 平均字符 | 平均 Chunk 数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fixed_window_450_overlap80 | 1.0000 | 1.0000 | 0.9583 | 438.49 | 7.90 |
| paragraph_page_900_overlap160 | 1.0000 | 1.0000 | 1.0000 | 755.93 | 3.90 |
| paragraph_page_1200_overlap200 | 1.0000 | 1.0000 | 1.0000 | 808.98 | 3.00 |
| section_aware_700_overlap120 | 0.9417 | 0.9667 | 0.9417 | 507.02 | 10.70 |

选择 `paragraph_page_900_overlap160` 的理由：

- 关键词命中率、页码命中率、上下文命中率都达到 1.0。
- 比固定窗口更能保留完整项目/经历上下文。
- 比 1200 大窗口更少引入无关噪声。
- 比 section-aware 更稳定，因为合成简历标题格式不总是完全规范。
- 平均 chunk 数较少，检索成本可控。

## Chunk 类型

### 结构化 Profile Chunk

来源：

- `profile.skills`
- `profile.projects`
- `profile.work_experience`
- `profile.education`
- `profile.research_experience`
- `profile.publications`
- `profile.patents`

特点：

- 信息密度高。
- 适合技能和项目匹配。
- metadata 保存字段名和列表索引。

### PDF Page Chunk

来源：

- `profile.pdf_page_text`

特点：

- 保存页码。
- 适合证据回溯。
- metadata 保存字符范围和切分策略。

### Raw Text Chunk

来源：

- `profile.raw_resume_text`

用于问答式 Profile 或无法保留页码的文本来源。

## 存储字段

`resume_chunks`：

- `profile_id`
- `chunk_uid`
- `chunk_type`
- `source`
- `text`
- `token_count`
- `embedding_json`
- `metadata_json`

结构化 Chunk 与 PDF 原文 Chunk 不是两份独立经历。结构化字段会获得稳定的
`metadata.fact_id`（例如 `projects:0`），它代表一个底层事实而不是一条文本。
原文关联采用“精确锚点优先、细节扩展次之”的两阶段流程：页级 Chunk 先尝试匹配
规范化原文片段；如果结构化摘要很短，则在项目/实习/科研候选事实中检查多个技术或
实体锚点、交付动词和歧义边界，建立 `match_type=semantic_detail` 的一对多关联。
计划学习、课程阅读和没有交付动词的段落不会被自动挂到已交付事实上。

检索结果和质量门禁按 `fact_id` 只计算一次相关性，但不会丢弃详细原文：同一事实的
结构化视图和原文视图会折叠成一条结果，并在 `metadata.evidence_views` 中保留来源、
原文片段、分数和关联类型。无法可靠建立归属的 Chunk 仍可用于召回和页码回溯，但
不会被强行合并。

这是一种“一个事实对应多个证据”的关系，而不是把一整个项目的所有段落粗暴拼成一
句话。后续生成时仍应引用具体证据视图，不能因为它们共享 `fact_id` 就推断所有细节
都已被证明。

metadata 示例：

```json
{
  "page_no": 2,
  "source_format": "pdf",
  "char_start": 120,
  "char_end": 840,
  "strategy": "paragraph_then_sliding_window",
  "chunk_size": 900,
  "chunk_overlap": 160
}
```

## 复杂版式和跨页

PDF 页先由 PyMuPDF 按 block 提取；检测到双栏时按列恢复阅读顺序。若发现表格，则把表格单元格按行恢复为 `cell | cell`，并从普通 block 中排除重叠区域，避免重复索引。多行长 block 记录为 `text_box` 布局信号，但不会改写原文。

每页诊断包含：

- `extraction_method`：`text_layer`、`ocr` 或 `blank`。
- `layout_mode`：普通 block、双栏或 OCR 排序模式。
- `table_count`、`text_box_count`、`layout_regions`：用于质量门控和 bad case 定位。
- `character_count`、可打印率、替换字符率、OCR 置信度：用于判断是否需要 OCR。

当非空文字层质量不足时，即使 PDF 没有 image object，也会把页面渲染后送入 OCR；这是为了覆盖异常字体映射的浏览器导出 PDF。相邻页只有在上一页尾部不是完整句、或下一页从列表项继续时才生成有限跨页桥接 Chunk：

```json
{
  "strategy": "cross_page_semantic_bridge",
  "page_no": 2,
  "page_start": 1,
  "page_end": 2
}
```

桥接只服务于召回和上下文连续性，展示和事实校验仍回到页级原文；这样不会把模型拼出的上下文误当成候选人的原话。

## 评测数据的两条轨道

`evals/complex_resume_corpus/` 使用 ReportLab 生成可控制的扫描、双栏、表格边界和句中跨页压力样本；`evals/vibe_resume_corpus/` 使用本机 VibeResume 模板和 Chromium 导出器生成更接近真实投递的标准单页、密集双页和科研长页。前者适合定位算法边界，后者适合检查正常用户 PDF 是否因版式或导出器差异而退化。

真实相关性基准位于 `evals/real_profile_job_relevance_annotations.json`，标签按整体语义相关性分为 0-3 级，并记录支持事实和风险缺口。它不直接给出模型分数；模型评测应在同一岗位快照上计算 Recall@K、MRR、nDCG，并对每个错排样本回看 JD、简历证据和 Query。

## 检索评分

当前 Chunk 一阶段生产检索策略为：

```text
score = vector_score * 0.45 + lexical_score * 0.50 + type_boost * 0.05
```

岗位发现有简历时同时使用用户需求 Query 和简历交付证据 Query；两者参与真实岗位源和本地岗位库召回。岗位发现的岗位级候选融合为语义 0.72、词法 0.28，选中岗位后的简历证据检索使用多 Query RRF 和单次综合重排。`retrieval_context` 只用于检索表示，原始 Chunk 负责证据引用。

## 后续优化

- 加入真实 PDF 简历人工标注集。
- 增加 layout-aware PDF parser，处理多栏、表格和复杂项目符号。
- 接入真实 embedding 模型后重新评估 chunk size。
- 引入 reranker，对 TopK chunk 进行二阶段排序。
