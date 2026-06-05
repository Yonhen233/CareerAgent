# PDF Chunk 方案

## 目标

PDF Chunk 的目标不是简单按固定长度截断，而是让后续 RAG 能回答：

- 某个技能来自简历哪个结构化字段。
- 某段项目经历来自 PDF 哪一页。
- 定制简历中的表述能否回溯到原始证据。
- 检索命中的 chunk 为什么与 JD 相关。

## 输入

上传 PDF 后，系统使用 `pypdf` 提取每页文本：

```python
PDFPageText(page_no=1, text="...")
```

然后合并为完整 raw resume text，用于结构化解析。

## Chunk 类型

### 结构化 Profile Chunk

来源：

- `profile.skills`
- `profile.projects`
- `profile.work_experience`
- `profile.education`

特点：

- 信息密度高。
- 适合匹配技能、项目和经历。
- metadata 中保留字段名和列表索引。

### PDF Page Chunk

来源：

- `profile.pdf_page_text`

特点：

- 保留页码。
- 适合做证据回溯。
- metadata 中保留 `page_no`、字符范围、切分策略。

### Raw Text Chunk

来源：

- `profile.raw_resume_text`

特点：

- 用于问答式 Profile 或无法保留页码的文本来源。

## 切分策略

当前策略：

1. 先按空行分段。
2. 如果段落合并后不超过 `CHUNK_SIZE`，尽量保留完整段落。
3. 如果单段过长，使用滑动窗口。
4. 滑窗步长为 `CHUNK_SIZE - CHUNK_OVERLAP`。
5. 每个 chunk 记录：
   - `char_start`
   - `char_end`
   - `chunk_size`
   - `chunk_overlap`
   - `strategy`

默认配置：

```env
CHUNK_SIZE=900
CHUNK_OVERLAP=160
```

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

## 检索评分

当前 SQLite 检索分数：

```text
score = vector_score * 0.70 + lexical_score * 0.30
```

- `vector_score`：确定性 hash embedding 的 cosine similarity。
- `lexical_score`：query token 与 chunk token 的重合率。

如果 Chroma 可用，chunk 会同步写入 Chroma，后续可以扩展为真正的 ANN 检索。

## 为什么不是只用固定长度

固定长度切分有两个问题：

- 会切断项目经历或技能上下文。
- 很难解释证据来自哪里。

当前方案保留结构化字段和 PDF 页码，牺牲一点实现复杂度，换来更好的可解释性和 guardrail 能力。

## 后续优化

- 使用标题识别做 section-aware chunk。
- 加入表格和多栏 PDF 的布局恢复。
- 使用真实 embedding 模型替换 hash embedding。
- 用 Chroma/FAISS/HNSW 做 ANN 检索主路径。
- 为 chunk 建立引用格式，例如 `page:2#char:120-480`。
