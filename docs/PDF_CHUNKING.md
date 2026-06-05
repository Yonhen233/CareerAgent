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

在 30 个合成 PDF 简历案例、120 条查询上，对比结果：

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

## 检索评分

当前生产检索策略已根据 RAG 评测调整为：

```text
score = lexical_score * 0.80 + vector_score * 0.15 + type_boost * 0.05
```

同时支持轻量 query alias expansion，例如：

- `retrieval augmented generation` -> `RAG`
- `Python API service` -> `FastAPI`
- `embedded relational storage` -> `SQLite`

## 后续优化

- 加入真实 PDF 简历人工标注集。
- 增加 layout-aware PDF parser，处理多栏、表格和复杂项目符号。
- 接入真实 embedding 模型后重新评估 chunk size。
- 引入 reranker，对 TopK chunk 进行二阶段排序。
