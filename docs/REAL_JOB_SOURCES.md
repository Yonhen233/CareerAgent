# 国内真实岗位源接入说明

本文记录 CareerAgent 对国内互联网公司自有招聘站的实测结论、接入方式、运行边界和评测结果。结论基于 2026-07-20 的公开页面和接口，招聘批次、岗位数量及页面协议都可能变化，应以定期 smoke 结果为准。

## 当前正式接入

| Source | 官方入口 | 获取方式 | JD 完整性 | 默认启用 | 实测边界 |
| --- | --- | --- | --- | --- | --- |
| `tencent` | [腾讯招聘](https://careers.tencent.com/) | 公开 JSON 搜索接口 | 职责、要求 | 是 | Agent 搜索结果混有社招，后续统一排序和实习过滤 |
| `baidu` | [百度招聘](https://talent.baidu.com/jobs/list) | SSR `window.__INITIAL_DATA__` | 职责、要求、项目、地点 | 是 | 直接限定 `INTERN`，当前 Agent 实习岗位密度高 |
| `meituan` | [美团招聘](https://zhaopin.meituan.com/web/position/list) | 公开搜索 JSON + 并发详情 JSON | 详情接口完整 JD | 是 | 搜索后并发补全详情，详情失败会显式记录 source error |
| `bytedance` | [字节跳动校园招聘](https://jobs.bytedance.com/campus/position) | Playwright 打开官网并捕获签名后的结构化 JSON | 职责、要求、城市、招聘类型 | 是 | `_signature` 由官网前端动态生成，不复用过期签名，不抓 DOM 文本 |
| `alibaba` | [阿里巴巴校园招聘](https://campus-talent.alibaba.com/campus/position) | HTTP 获取 XSRF cookie，动态发现实习批次，再并发搜索 | 职责、要求、业务、批次、地点 | 是 | 同时查询 2027 届、日常和研究型实习，不硬编码单一批次 |

默认中文搜索会通过 `asyncio.gather` 并发运行五个 Source。每个适配器只负责把官方数据映射为统一 `JobPosting`；跨来源排序、实习过滤、JD 解析、SQLite upsert 和 chunk 索引由后续服务统一处理。

## 字节与阿里的实现选择

### 字节跳动

字节岗位详情页和搜索页都能返回完整 JD，但 `/api/v1/search/job/posts` 要求官网前端实时生成 `_signature`。直接复制某次 URL 的签名只能短期工作，也无法解释签名失效。

当前实现使用无头 Playwright 打开官方搜索页，等待状态码为 200 的岗位 JSON 响应，再解析 `data.job_post_list`。这比 DOM selector 抓取更稳定，同时保留了真实浏览器请求链路。浏览器、响应超时或 JSON 协议变化都会直接抛错并进入 `source_errors`。

### 阿里巴巴

阿里校园招聘页会设置 `XSRF-TOKEN`，随后通过 `/searchCondition/listBatch` 返回当前实习批次，通过 `/position/search` 返回带完整 `description` 和 `requirement` 的岗位。

当前实现每次搜索都先发现有效的 `internship` 批次，再并发查询各批次。这样招聘年度变化时不需要修改固定 `batchId`。阿里搜索接口按单个检索词工作，`Agent 开发实习生` 这种自然语言整句会返回空结果，因此 Source 会先提取 `Agent`、`RAG`、`LLM`、`大模型` 等核心检索词，最终仍使用完整用户 query 做统一相关性重排。

## 已验证但暂未进入默认链路

| 站点 | 实测结论 | 暂不接入原因 |
| --- | --- | --- |
| 滴滴招聘 | 公开列表可按 `Agent` 返回完整社招 JD | 本轮没有检索到 Agent 实习岗位，不符合当前实习优先场景 |
| OPPO 招聘 | 部分公开岗位详情能读取完整 Agent 实习 JD | 列表入口在实测中出现空结果或服务异常，尚未形成稳定发现链路 |
| 小米招聘 | 搜索引擎可发现招聘入口，当前站点链路涉及飞书招聘页面 | 尚未验证稳定、可分页且带完整 JD 的公开接口 |
| 华为招聘 | 校园招聘项目介绍公开 | 岗位列表和详情为动态页面，稳定结构化接口尚未完成验证 |
| 京东招聘 | 招聘页面公开 | 本轮没有确认可长期使用的 Agent 岗位搜索协议 |

这些站点不会用假数据或静默降级冒充成功。后续只有在“岗位发现、完整 JD、稳定投递 URL、连续 smoke”四项都通过后才进入默认 Source。

## 真实评测

运行：

```powershell
python scripts/run_real_job_source_eval.py
```

数据集 `evals/real_job_source_cases.json` 包含 8 类中文主场景查询：

- Agent 开发、Coding Agent、RAG 知识库。
- 大模型评测、Agent 产品、Agent 后端。
- 多模态 Agent、Prompt/Agent 安全。

2026-07-20 评测运行 `#40-#47`：

| 指标 | 结果 |
| --- | ---: |
| case | 8 |
| 通过 | 8 |
| pass rate | 1.0000 |
| 五源可达率 | 每个 case 均为 1.0000 |
| 五源有结果率 | 每个 case 均为 1.0000 |
| 返回岗位 | 316 |
| JD 非空率 | 每个 case 均为 1.0000 |
| 投递链接率 | 每个 case 均为 1.0000 |
| 实习岗位率 | 0.7778-0.9000 |
| query relevance | 每个 case 均为 1.0000 |
| Agent 相关率 | 0.8333-1.0000 |
| 单 case 总耗时 | 6.5-7.6 秒 |

`Agent 开发实习生` 单 case 返回 40 条岗位，实习率 0.8500、Agent 相关率 0.9750。Source 级耗时中，阿里约 1.4 秒，字节约 4.6 秒；五源并发后的总耗时约 7.3 秒，不是五个来源耗时相加。

## 部署与运维

字节 Source 需要 Playwright Chromium：

```powershell
pip install -r requirements.txt
playwright install chromium
```

可配置项：

```env
TENCENT_CAREERS_ENABLED=true
BAIDU_CAREERS_ENABLED=true
MEITUAN_CAREERS_ENABLED=true
BYTEDANCE_CAREERS_ENABLED=true
ALIBABA_CAREERS_ENABLED=true
JOB_SOURCE_BROWSER_HEADLESS=true
JOB_SOURCE_BROWSER_TIMEOUT_MS=30000
```

生产监控至少应关注：

- 各 Source 的 `reachable_source_rate`、`result_source_rate` 和 `latency_ms`。
- JD 非空率、投递链接率、实习岗位率和 query relevance。
- 字节 Playwright 启动失败、签名请求超时和协议字段变化。
- 阿里 XSRF token、批次发现和 `content.datas` 协议变化。

Source 网络错误只作为来源层指标记录，不会伪装成岗位搜索成功；核心 Agent 回归仍使用可控岗位源，避免招聘站临时波动掩盖业务逻辑回归。
