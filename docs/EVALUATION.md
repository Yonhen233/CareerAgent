# 量化评测方案

## 为什么需要评测

求职 Agent 不能只看单次演示结果。至少要量化回答：

- 岗位匹配是否找对了关键技能。
- 缺失技能是否判断合理。
- RAG 证据是否命中相关项目。
- 分数是否能区分强匹配和弱匹配。
- 改动后质量有没有退化。

## 样例集

内置样例：

```text
evals/sample_cases.json
```

每个 case 包含：

- 候选人 Profile。
- 目标 Job/JD。
- 期望匹配技能。
- 期望缺失技能。
- 期望证据关键词。
- 分数下限或上限。

## 运行方式

命令行测试：

```bash
pytest -q
```

API：

```http
POST /evaluations/run
GET /evaluations/results
```

## 指标

### Required Skill Precision

预测命中的 required skills 中，有多少是期望命中的。

```text
precision = expected_matched ∩ predicted_matched / predicted_matched
```

### Required Skill Recall

期望命中的 required skills 中，有多少被系统找到了。

```text
recall = expected_matched ∩ predicted_matched / expected_matched
```

### Missing Skill Precision

预测缺失技能中，有多少确实应该缺失。

### Evidence Hit Rate

期望证据关键词中，有多少出现在 RAG 返回证据里。

### Pass Rate

通过样例数 / 总样例数。

当前通过条件：

- required skill recall >= 0.6
- missing skill precision >= 0.5
- overall score 满足 case 中设置的上下界

## 结果存储

每次运行写入：

```text
evaluation_runs
```

包含：

- `summary_json`
- `case_results_json`

## 后续优化

- 增加更多真实岗位样例。
- 给每个样例加入人工标注的理想证据 chunk。
- 增加简历改写质量评分。
- 增加 LLM-as-judge，但必须配合人工抽检。
- 在 CI 中设置最低 pass rate 阈值。
