# 国内真实岗位源接入说明

本文记录 CareerAgent 对国内企业自有招聘站的实测结论、接入方式、运行边界和评测结果。结论最近核验于 2026-09-04；招聘批次、岗位数量及页面协议都可能变化，应以定期 smoke 结果为准。

## 当前正式接入

| Source | 官方入口 | 获取方式 | JD 完整性 | 默认启用 | 实测边界 |
| --- | --- | --- | --- | --- | --- |
| `tencent` | [腾讯招聘](https://careers.tencent.com/) | 公开 JSON 搜索接口 | 职责、要求 | 是 | Agent 搜索结果混有社招，后续统一排序和实习过滤 |
| `baidu` | [百度招聘](https://talent.baidu.com/jobs/list) | SSR `window.__INITIAL_DATA__` | 职责、要求、项目、地点 | 是 | 直接限定 `INTERN`，当前 Agent 实习岗位密度高 |
| `meituan` | [美团招聘](https://zhaopin.meituan.com/web/position/list) | 公开搜索 JSON + 并发详情 JSON | 详情接口完整 JD | 是 | 搜索后并发补全详情，详情失败会显式记录 source error |
| `bytedance` | [字节跳动校园招聘](https://jobs.bytedance.com/campus/position) | Playwright 打开官网并捕获签名后的结构化 JSON | 职责、要求、城市、招聘类型 | 是 | `_signature` 由官网前端动态生成，不复用过期签名，不抓 DOM 文本 |
| `alibaba` | [阿里巴巴校园招聘](https://campus-talent.alibaba.com/campus/position) | HTTP 获取 XSRF cookie，动态发现实习批次，再并发搜索 | 职责、要求、业务、批次、地点 | 是 | 同时查询 2027 届、日常和研究型实习，不硬编码单一批次 |
| `jd` | [京东招聘](https://zhaopin.jd.com/) | 官方公开岗位搜索 JSON | 职责、要求、地点、职位类型 | 是 | 当前以社招岗位为主，适合浏览和技术栈调研；实习优先筛选会自然过滤社招 |
| `china_telecom` | [中国电信招聘](https://wejob.chinatelecom.com.cn/wt/TELE/mobweb/v8/position/list?brandCode=1&recruitType=1&request_locale=zh_CN) | 官方关键词 POST 返回的服务端 HTML | 职责、要求、单位、地点、招聘项目 | 是 | 列表响应已包含完整 JD；当前 Agent 查询可发现集团及下属单位校园岗位 |
| `huawei` | [华为社会招聘 AI 岗位专区](https://career.huawei.com/reccampportal/portal5/social-recruitment-ai.html) | 官方 AI 列表页 + 并发公开详情 JSON | 职责、要求、部门、职位族、地点 | 是 | AI 专区规模有限但结构稳定，当前以社招专家岗位为主 |
| `iflytek` | [科大讯飞招聘](https://iflytek.zhiye.com/) | 官方 Beisen JSON，社招/校招/实习三类并发 | 职责、要求、类别、地点 | 是 | 当前含 Harness、代码 Agent、Agent 安全和应用研发岗位；忽略上游错误的 `Total=0`，以 `Data` 为准 |
| `tcl` | [TCL 校园招聘](https://zhaopin.tcl.com/campus/recruiting.html?id=57) | 官方搜索接口 + 并发详情接口 | 职责、要求、公司、地点 | 是 | 可返回 TCL 及旗下企业岗位，已实测 `Agent工程师` 完整 JD |
| `midea` | [美的招聘](https://recruit.midea.com/recruitOut/ihr/social/socialHome) | 官方公开岗位 JSON，多关键词并发召回 | 职责、要求、组织、地点 | 是 | 当前 Agent/大模型相关结果以社招为主 |
| `xiaomi` | [小米招聘探索机会](https://hr.xiaomi.com/website/opportunities.html) | 官网 `searchJobPage` JSON，多关键词并发召回 | 职责、要求、部门、地点、招聘类型 | 是 | 当前可发现 Agent 应用、评估、Coding Agent、Harness 与多智能体实习岗位；投递 URL 指向小米官方飞书招聘页 |
| `oppo` | [OPPO 校园招聘](https://careers.oppo.com/university/oppo/campus) | 官网 `position/pageNew` JSON，多关键词并发召回 | 职责、要求、知识技能、AI 能力等级、地点、招聘项目 | 是 | 当前可发现 Coding Agent、MCP、Skills、Harness Engineering、智能体评测及系统级 AI Agent 岗位 |
| `skyworth` | [创维 2027 届校园招聘](https://skyworth.hotjob.cn/) | 动态发现 HotJob suite + 官方列表接口 + 受限并发详情接口 | 职责、要求、专业、学历、子公司、地点 | 是 | 当前酷开算法岗位明确覆盖 RAG、Agentic Workflow、多智能体与 Function Calling |
| `wind` | [万得招聘](https://www.wind.com.cn/portal/zh/JoinUs/index.html) | 官网发布的岗位数据文件 | 完整职位描述、要求、地点 | 是 | 静态数据版本会变化，解析异常直接作为 source error 暴露 |
| `moka_cn` | 13 个企业官方 Moka 招聘门户 | Playwright 搜索官网并读取职位详情 | 标题、完整 JD、地点、官方详情 URL | 是 | 覆盖韶音、DeepSeek、壁仞、锐捷、完美世界、新浪、苏商银行、LINE MAN Technology、月之暗面、大疆、金山办公和中兴社招/校招；支持企业自定义招聘域名 |
| `didi` | [滴滴招聘](https://talent.didiglobal.com/) | 公开列表 JSON + 并发详情 JSON | 职责、要求、部门、地点、类型 | 是 | 当前 Agent 岗位密度高，以社招为主；详情协议变化直接报 source error |
| `honor` | [荣耀招聘](https://www.honor.com/cn/career/) | 官方 HotJob 实习列表 + 详情接口 | 职责、要求、学历、部门、地点 | 是 | 当前按实习项目检索，标题可能不含 Agent，需在完整 JD 上判相关性 |
| `kuaishou` | [快手招聘](https://zhaopin.kuaishou.cn/) | 官网公开 HMAC 签名算法 + JSON | 完整职责、要求、地点、岗位 ID | 是 | 并发查询社招与日常实习；签名按当前参数和时间戳实时生成，不保存静态签名 |
| `lenovo` | [联想招聘](https://talent.lenovo.com.cn/position?projectType=1) | 公开校招 JSON | 完整职责、要求、类别、学历、地点 | 是 | 搜索字段必须使用 `keyword`；错误使用 `jobName` 会被上游静默忽略 |
| `vivo` | [vivo 招聘](https://career.vivo.com/jobs) | 公开社招 JSON | 完整岗位描述、组织、地点、学历 | 是 | 上游可能返回弱相关行，适配器在统一排序前执行 query relevance 过滤 |
| `netease` | [网易校园招聘](https://campus.163.com/) | 当前项目发现后的公开岗位 JSON | 完整职责、要求、项目、地点 | 是 | 覆盖网易互联网、网易互娱和丹子 AI 实习专项，按项目 ID 保留 provenance |
| `xiaohongshu` | [小红书招聘](https://job.xiaohongshu.com/jobs) | 社招、校招官方 JSON 并发查询 | 完整职责、要求、类别、地点 | 是 | 当前 Agent 岗位密度高；列表响应已含完整 JD，无需浏览器补详情 |
| `bilibili` | [哔哩哔哩招聘](https://jobs.bilibili.com/) | 官方 CSRF bootstrap + 校招/社招 JSON | 完整职位描述、类别、地点 | 是 | 每次搜索先获取临时 CSRF token，不缓存或写死会话凭据 |
| `antgroup` | [蚂蚁集团招聘](https://talent.antgroup.com/campus-full-list) | 官方校园招聘 JSON，有限并发分页 | 完整职责、要求、批次、地点 | 是 | 官网接口限制分页大小，固定使用 20 条并只读取前三页后在完整 JD 上做相关性过滤 |
| `qihu360` | [360招聘](https://hr.360.cn/hr/list) | 官方列表 JSON + 有界并发详情 JSON | 完整职责、要求、类别、地点 | 是 | 详情接口要求网页端请求头；当前招聘总量较小，先补全详情再过滤 |
| `minimax` | [MiniMax 招聘](https://www.minimaxi.com/careers) | Playwright 读取官方飞书门户的职位卡片 | 标题、招聘项目、地点、卡片职责 | 是 | 飞书门户未暴露稳定单岗位链接，payload 明确标记 `official_job_card`，不冒充完整详情 JD |
| `zhipu` | [智谱招聘](https://www.zhipuai.cn/zh/joinus) | 结构化解析官网岗位类别卡片 | 类别、简述、可选城市、官方申请表 | 是 | 官网只公开类别级机会，payload 标记 `category`，前端与后续分析可识别其证据粒度 |

默认中文搜索会通过 `asyncio.gather` 并发运行 28 个 Source 适配器，覆盖 39 个企业官方招聘门户。每个适配器只负责把官方数据映射为统一 `JobPosting`；跨来源排序、实习过滤、JD 解析、SQLite upsert 和 chunk 索引由后续服务统一处理。

`moka_cn` 被设计成一个聚合适配器，而不是同时启动 13 个独立浏览器门户：它共享一个 Chromium 实例，最多并行打开 3 个企业上下文。岗位仍以 `moka_shokz`、`moka_deepseek`、`moka_dji` 等细分来源写入 payload，便于追踪具体企业。这个选择以较高的浏览器尾延迟换取可控的内存和浏览器进程数。

## 字节与阿里的实现选择

### 字节跳动

字节岗位详情页和搜索页都能返回完整 JD，但 `/api/v1/search/job/posts` 要求官网前端实时生成 `_signature`。直接复制某次 URL 的签名只能短期工作，也无法解释签名失效。

当前实现使用无头 Playwright 打开官方搜索页，等待状态码为 200 的岗位 JSON 响应，再解析 `data.job_post_list`。这比 DOM selector 抓取更稳定，同时保留了真实浏览器请求链路。浏览器、响应超时或 JSON 协议变化都会直接抛错并进入 `source_errors`。

### 阿里巴巴

阿里校园招聘页会设置 `XSRF-TOKEN`，随后通过 `/searchCondition/listBatch` 返回当前实习批次，通过 `/position/search` 返回带完整 `description` 和 `requirement` 的岗位。

当前实现每次搜索都先发现有效的 `internship` 批次，再并发查询各批次。这样招聘年度变化时不需要修改固定 `batchId`。阿里搜索接口按单个检索词工作，`Agent 开发实习生` 这种自然语言整句会返回空结果，因此 Source 会先提取 `Agent`、`RAG`、`LLM`、`大模型` 等核心检索词，最终仍使用完整用户 query 做统一相关性重排。

## 数据粒度边界

来源接入不再把“必须是单岗位完整 JD”误当成所有官网都能满足的统一前提。滴滴、荣耀、快手、联想、vivo 和网易提供岗位级完整 JD；MiniMax 当前提供官方职位卡片级职责；智谱只提供岗位类别级机会。后二者仍有浏览价值，但必须在 payload 和正文中显式标明粒度，匹配分析不能把类别简述当成完整任职要求，也不能生成不存在的技能差距。

## 真实评测

运行：

```powershell
python scripts/run_real_job_source_eval.py
```

数据集 `evals/real_job_source_cases.json` 包含 8 类中文主场景查询：

- Agent 开发、Coding Agent、RAG 知识库。
- 大模型评测、Agent 产品、Agent 后端。
- 多模态 Agent、Prompt/Agent 安全。

2026-07-20 的旧五源基线运行 `#40-#47`：

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

2026-09-04 新增独立四源验证为 EvaluationRun `#182`，查询 `Agent 大模型开发`、每源最多 5 条。小红书、哔哩哔哩、蚂蚁集团和 360 为 4/4 可达且有结果，共得到 20 条岗位；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，总墙钟约 12.0 秒。大疆另以真实浏览器验证返回 2 条完整 Agent JD；金山办公和中兴当前协议正常但该关键词为空。上述验证不调用 LLM，也不消耗模型 Token。

扩展后的全默认 production gate 为 EvaluationRun `#183`：28/28 适配器可达且有结果，source error 为 0，共返回 123 条岗位；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.5203，总墙钟约 40.2 秒。`moka_cn` 的 source 级“有结果”表示聚合源至少有一家命中；运维排查仍应查看 `moka_*` 企业级 provenance，不能据此断言其中每家此刻都有 Agent 岗位。

新增八源最终独立验证为 EvaluationRun `#180`：8/8 可达且均有结果，共 31 条；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.5484，耗时 7.1 秒。第一轮 `#177` 暴露滴滴列表实际使用 `data.items` 而不是 `records/list`，修复并重跑后才允许进入默认门控。

评测集保留旧五源的 8 类查询用于纵向对比，并增加一个全默认来源 production gate：`reachable_source_rate` 必须为 1.0000；`result_source_rate` 允许不低于 0.7000，因为“官网正常但当前没有匹配岗位”不是协议故障。可达率与有结果率必须分开判断。

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
JD_CAREERS_ENABLED=true
CHINA_TELECOM_CAREERS_ENABLED=true
HUAWEI_CAREERS_ENABLED=true
IFLYTEK_CAREERS_ENABLED=true
TCL_CAREERS_ENABLED=true
MIDEA_CAREERS_ENABLED=true
XIAOMI_CAREERS_ENABLED=true
SKYWORTH_CAREERS_ENABLED=true
WIND_CAREERS_ENABLED=true
MOKA_CHINA_CAREERS_ENABLED=true
DIDI_CAREERS_ENABLED=true
HONOR_CAREERS_ENABLED=true
KUAISHOU_CAREERS_ENABLED=true
LENOVO_CAREERS_ENABLED=true
VIVO_CAREERS_ENABLED=true
NETEASE_CAREERS_ENABLED=true
MINIMAX_CAREERS_ENABLED=true
ZHIPU_CAREERS_ENABLED=true
JOB_SOURCE_BROWSER_HEADLESS=true
JOB_SOURCE_BROWSER_TIMEOUT_MS=30000
```

生产监控至少应关注：

- 各 Source 的 `reachable_source_rate`、`result_source_rate` 和 `latency_ms`。
- JD 非空率、投递链接率、实习岗位率和 query relevance。
- 字节 Playwright 启动失败、签名请求超时和协议字段变化。
- 阿里 XSRF token、批次发现和 `content.datas` 协议变化。
- Moka 各企业站点的搜索响应、详情长度、空结果率和聚合源 p95 延迟。
- 中国电信服务端 HTML 字段、华为 AI 列表与详情 JSON、科大讯飞 `Data` 数组的协议变化。
- 小米 `searchJobPage` 的分页/字段协议、创维 HotJob suite 发现、列表与详情接口协议变化。
- 京东、美的社招源被“只看实习/校招”过滤后的有效结果率，避免把正常过滤误报为来源失效。
- 快手签名算法、滴滴 `data.items`、荣耀 HotJob suite、网易当前项目 ID 和联想 `keyword` 参数的协议变化。
- MiniMax `official_job_card` 与智谱 `category` 的证据粒度，避免在后续 RAG 中被误当成完整 JD。

Source 网络错误只作为来源层指标记录，不会伪装成岗位搜索成功；核心 Agent 回归仍使用可控岗位源，避免招聘站临时波动掩盖业务逻辑回归。
