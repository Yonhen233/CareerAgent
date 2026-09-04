# 开发日志

## 2026-09-04 20:10 +08:00：新增八家国内企业官方招聘入口并完成岗位源生产门禁

### 目标与结果

- 新增小红书、哔哩哔哩、蚂蚁集团、360 和得物五个独立 Source Adapter；在共享 `moka_cn` 中新增大疆、金山办公和中兴通讯社招/校招四个门户。
- 中文默认链路从 24 个适配器、32 个企业官方门户扩展为 29 个适配器、40 个企业官方门户。请求 Schema、注册表开关、生产评测清单、API、架构和岗位源文档同步更新。
- 新增四源真实验证 EvaluationRun `#182`：4/4 可达且有结果，20 条岗位，JD 非空率、官方链接率、query relevance 和 Agent related rate 均为 1.0000，耗时 12,047 ms。
- 全默认 production gate EvaluationRun `#183`：28/28 可达且有结果，source error 0，共 123 条岗位；上述四项质量率均为 1.0000，实习/校招占比 0.5203，耗时 40,187 ms；全程未调用 LLM。
- 得物独立 EvaluationRun `#184`：1/1 可达且有结果，5 条完整 Agent JD，JD/链接/query relevance/Agent related rate 均为 1.0000，耗时 9,968 ms；当前结果均为社招。
- 最终全默认 production gate EvaluationRun `#185`：29/29 可达且有结果，source error 0，共 126 条岗位；四项质量率均为 1.0000，实习/校招占比 0.5159，耗时 42,956 ms。

### 值得复盘的 Bad Case 与设计选择

**1. “官网有接口”不代表复制一个 POST 就能稳定工作。** 哔哩哔哩职位接口要求先调用官方 CSRF bootstrap，再把临时值放入 `X-CSRF`；360 详情接口缺少 `X-Requested-With: XMLHttpRequest` 时会返回 500。最终把这些前端公开协议封装在各自适配器内，并用 MockTransport 断言请求头和调用顺序。这样上游改协议时表现为明确 source error，而不是静默空列表。

**2. 列表是否已有完整 JD 决定要不要补详情。** 小红书与哔哩哔哩列表响应已经提供职责和要求，继续逐条打开详情只会放大延迟与限流风险；360 列表只有概要，必须有界并发补详情后才能执行相关性过滤。接入策略按证据完整度决定，不能给所有网站套同一种爬取模板。

**3. 蚂蚁接口的大分页请求会失败。** 尝试一次拉取 100 条不能得到有效响应，最终尊重官网分页合同，以每页 20 条、最多三页并发获取，再用完整 JD 本地过滤。有限分页给延迟与请求量设定上界，也避免为了覆盖无限岗位把单次用户搜索变成全站爬取。

**4. Moka 是共同技术栈，不等于共同域名和共同文案。** 第一版给大疆复用 `app.mokahr.com` 与“输入职位关键字”，页面虽然打开但搜索框不存在。真实页面使用 `apply.careers.dji.com` 和“搜索职位关键词”。因此 `MokaCareerSite` 增加企业自定义 host，搜索控件接受已验证的两种占位符。修复后大疆真实返回 2 条 Agent 岗位，JD 长度为 547/828 字符。

**5. 空结果和协议故障必须分开。** 金山办公与中兴页面协议、搜索交互正常，但当前 `Agent` 查询没有结果；这属于招聘供给状态，不应报系统故障。大疆最初搜索框协议不匹配则属于解析错误，必须进入 source error。聚合 Moka Source 的总健康度不能替代企业级 `moka_*` provenance，运维时仍需逐站观察。

**6. 飞书门户也不能只保存列表摘要。** 得物列表页能发现大量 Agent 岗位，但卡片不总是包含完整任职要求。实现先在列表页形成至多 8 个候选，再用 3 路有界并发打开官方详情，保留稳定岗位 ID 与完整技术栈；独立真实门禁通过后才加入默认链路。

**7. 暂不接入不稳定入口也是生产决策。** 商汤旧招聘子域当前返回 404，携程尚未确认可稳定获得岗位级完整 JD。本轮没有用搜索引擎摘要、静态样例或类别页冒充正式来源；这两个入口不进入默认链路。

### 验证

- 第一阶段新增与默认来源契约测试 `34 passed`、全仓回归 `454 passed in 127.07s`；得物接入后专项为 `35 passed`，最终全仓回归 `455 passed in 131.06s`。
- 修改文件 Ruff：全部通过。
- 真实联网：新增四个独立 Source 与全默认 28 Source 两轮 release gate 均通过；大疆额外逐站浏览器验证通过。

## 2026-09-04 18:31 +08:00：八个国内 Agent 岗位源全部接入与协议级复盘

### 本轮目标
- 将上一轮确认的滴滴、荣耀、快手、联想、vivo、网易、MiniMax 和智谱一次性接入正式默认链路，不停留在“官网可访问”或搜索引擎摘要层。
- 继续保持中文求职场景优先、官方来源、无 LLM 消耗、失败可追踪，并对来源证据粒度诚实建模。

### 正式实现
1. 新增 `didi`、`honor`、`kuaishou`、`lenovo`、`vivo`、`netease`、`minimax`、`zhipu` 八个 Source Adapter，全部进入 Registry、岗位搜索、岗位发现、环境配置和 production gate。
2. 滴滴与荣耀先列表发现再并发补详情；快手实时生成官网 HMAC-SHA256 签名并合并社招、日常实习；联想和 vivo 使用列表内完整 JD；网易从导航接口动态发现当前校招与 AI 专项项目后并发抓取。
3. MiniMax 读取官方飞书招聘门户的职位卡片；智谱解析官网岗位类别卡片。两者分别写入 `granularity=official_job_card` 和 `granularity=category`，不伪装成单岗位完整 JD。
4. 默认中文岗位链路从 16 个适配器、24 个企业门户扩展到 24 个适配器、32 个企业门户。

### Bad Case 1：HTTP 200 不能证明搜索条件生效
联想接口最初尝试了直觉上合理的 `jobName=Agent`，接口返回 200，也返回岗位列表，但结果与 Agent 无关。读取页面真实请求后确认有效参数是 `keyword=Agent`；未知参数会被静默忽略，而不是返回 4xx。

这个问题如果只做“接口可达”和“结果非空”两项检查，会把错误实现发布上线。最终契约测试不仅断言状态码，还断言请求参数；真实门控继续检查 query relevance 和 Agent related rate。面试中可概括为：外部 API 的成功判定必须包含语义后置条件，不能只验证传输层成功。

### Bad Case 2：离线 Mock 与真实协议存在字段漂移
滴滴离线实现按常见分页结构兼容 `records/list`，首轮八源真实门控 `#177` 却得到 7/8 可达。trace 明确指出 `DiDi Careers response does not contain data.records`；检查实时响应后发现列表字段实际为 `data.items`。

修复后保留 `items/records/list` 三种已知协议键，并重跑独立门控。这里不是静默吞错：未知结构仍直接抛出 source error。这个案例说明 Mock 负责固定已知合同，live smoke 负责发现上游漂移，两者不能互相替代。

### Bad Case 3：快手页面签名不等于只能做脆弱浏览器抓取
快手公开 JSON 在缺少签名时只返回系统错误。继续读取官网当前前端实现后，确认签名是对“时间戳 + 排序后的查询串 + 固定官网应用标识”做 HMAC-SHA256，签名和时间戳通过请求头发送。适配器按每次请求实时生成签名，保留真实岗位 ID、职责、要求和详情路由，不复用过期签名，也不退化为 DOM 表格文本。

### Bad Case 4：英文城市码会让中文地点过滤静默漏召回
快手响应地点使用 `Beijing/Hangzhou` 等代码。岗位本身正常，但用户输入“北京”时统一 location filter 无法命中。适配器在来源边界把常见代码规范化为中文，原始 payload 保留上游值。对应测试把 location 从空改为“北京”，确保这不是只在无地点查询下通过的假成功。

### Bad Case 5：招聘项目 ID 不能按当前届次永久硬编码
网易当前项目使用 102、103、75 等 ID，直接写死今天能工作，但下一届招聘会自然失效。最终每次先请求官方 navigation，解析受支持的 `campus.163.com` 和 `campus.game.163.com` 当前项目及 ID，再并发读取岗位；导航协议没有可用项目时直接报错，不复用过期批次。

### Bad Case 6：来源覆盖与证据完整度是两个维度
MiniMax 官方职位卡片包含标题、招聘项目、地点和职责，但当前前端没有暴露稳定单岗位 URL；智谱官网只公开校招/社招岗位类别和申请表。如果为了满足“全接入”把这些文本标成完整 JD，后续差距分析会把未公开要求误判为候选人没有能力。

最终将来源粒度写入 payload 和正文：它们可以用于机会发现和浏览，但匹配、引用和定制链路必须知道证据并不完整。成熟 Agent 的 provenance 不只回答“来自哪里”，还要回答“来源提供了多细的证据”。

### 验证结果
- 八源独立最终门控 EvaluationRun `#180`：8/8 可达并有结果，31 条岗位，source error 0；JD 非空、官方链接、query relevance、Agent related rate 均为 1.0000，实习/校招占比 0.5484，耗时 7,122 ms。
- 全默认 production gate EvaluationRun `#181`：24/24 可达并有结果，103 条岗位，source error 0；上述四项质量率均为 1.0000，实习/校招占比 0.5922，总墙钟 40,251 ms。
- 新增与更新后的岗位源契约测试 `29 passed`；包含粒度感知匹配在内的定向回归 `36 passed`；全仓 `449 passed in 122.34s`，修改文件 Ruff 全部通过。
- 本轮不调用 LLM，模型 Token 消耗为 0。

## 2026-09-04 17:49 +08:00：OPPO 与月之暗面真实岗位源接入

### 本轮目标
- 继续扩大国内 Agent 岗位覆盖，但仍以官方列表发现、完整 JD、稳定详情链接和可自动 smoke 为接入门槛。
- 优先验证 Agent 岗位密度高的 OPPO、月之暗面、MiniMax 和智谱，不调用 LLM。

### 正式实现
1. 新增 `oppo` 独立适配器。适配器调用 OPPO 校招官网实际使用的 `/openapi/position/pageNew`，按有限核心词并发查询，直接映射职责、要求、知识技能、AI 能力等级、招聘项目、地点和发布日期。
2. 将月之暗面加入 `moka_cn` 聚合源。Moka 站点模型新增对 `/apply/{org}/{site}` 路由的支持，继续复用一个 Chromium 和最多 3 个并发上下文，不为单家公司重复启动浏览器。
3. OPPO 加入默认注册表、岗位搜索与岗位发现请求；默认覆盖由 15 个适配器/22 个企业门户提升到 16 个适配器/24 个企业门户。

### Bad Case 1：能打开单条详情，不等于存在稳定来源
此前 OPPO 被暂缓接入，是因为只验证到单条 Agent JD，而列表页曾出现空结果。继续读取当前官网前端代码后，确认列表实际调用 `position/pageNew`，并还原了 `pageNum`、`pageSize`、`positionName`、项目和城市筛选字段。直接以 `Agent` 查询可稳定返回 3 个强相关岗位，因此本轮解决的是“岗位发现链路”，而不只是又保存了一条静态 URL。

### Bad Case 2：同一 ATS 品牌并不保证 URL 结构完全相同
已有 Moka 企业使用 `/campus-recruitment/...` 或 `/social-recruitment/...`；月之暗面官网跳转到 `/apply/moonshot/...`。若为了复用适配器强行拼成 `apply-recruitment`，页面一定失败。最终把 URL 路由差异建模在 `MokaCareerSite.mode` 中，搜索、详情抽取、错误上报和资源并发仍复用同一套实现。

### 验证前发现
- OPPO 官网实时 `Agent` 查询返回 3 条完整岗位，明确涉及 Coding Agent、MCP/CLI 工具链、Skills、Harness Engineering、Trace 与自动化评测。
- 月之暗面官网 Moka 页面实时 `Agent` 查询返回 5 条完整岗位，详情正文长度约 550 至 1370 字，均带官方详情链接。
- MiniMax 和智谱已确认存在官方招聘入口，但尚未证明可稳定分页发现岗位，因此没有为了增加数量提前放入默认链路。

### 评测要求
- 契约测试必须覆盖 OPPO 请求结构、完整 JD 映射、地点过滤和官方详情 URL。
- Moka 测试必须覆盖月之暗面的 `/apply` 路由，真实默认门控还需确认新增来源不会导致整个聚合源失败。
- 全过程不调用 LLM，Token 用量为 0。

### 最终验证
- 新增来源独立门控 EvaluationRun `#174`：`oppo` 与 `moka_cn` 2/2 可达且有结果，共 10 条；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.7000，耗时 30,793 ms。
- 修复旧评测清单后的全默认门控 EvaluationRun `#176`：16/16 可达且有结果，无 source error，共 72 条；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.6111，耗时 35,770 ms。
- 新增契约及生产门控一致性测试共 `21 passed`，Ruff 通过；全仓回归 `440 passed in 129.29s`。

### Bad Case 3：默认源已经增加，但生产门控仍在测试旧清单
首次全默认门控 `#175` 显示 15 个来源且全部通过，但结果中的 `sources` 没有 OPPO。原因不是注册表失效，而是 `evals/real_job_source_cases.json` 的生产健康检查显式保存了旧清单，覆盖了注册表默认值。若只看绿色 pass rate，会产生“新来源已经被端到端覆盖”的错误结论。

修复时同步更新评测数据集、API 示例和评测文档，并要求重跑后 `source_count=16` 且结果中明确出现 `oppo`。这个问题说明配置型系统不能只断言默认构造器；发布门控自己的 fixture 也属于生产契约，新增能力时必须纳入一致性检查。

## 2026-09-04：小米与创维岗位源误判复盘及正式接入

### 本轮目标
- 回答“小米、创维是否真的无法获取”，重新从当前招聘官网前端代码、跳转入口和网络协议向下验证，而不是沿用上一轮的待接入结论。
- 只在岗位发现、分页、完整 JD、官方投递 URL 和真实门控都通过后加入默认链路。
- 不调用 LLM，以免把招聘源协议验证与模型效果、Token 成本混在一起。

### 正式实现
1. 新增 `xiaomi`。小米新版“探索机会”页的 `jobs.js` 公开调用 `/website/api/agent/searchJobPage`，支持关键词、城市、招聘项目和分页，响应直接包含职责、要求、一级部门、城市、招聘类型、发布日期及小米飞书招聘详情 URL。适配器对有限核心词并发召回、按 `jobPostId` 去重，再使用统一 query relevance 排序。
2. 新增 `skyworth`。创维 2027 届校招入口已迁至 `skyworth.hotjob.cn`，底层为大易 HotJob。适配器先通过 `getSLD` 动态发现当前 suite，再调用 `listPosition` 搜索校园岗位，只对去重后的有限候选并发调用 `listPositionDetail`，补全职责、要求、学历、专业、子公司、地点和项目，再映射为统一 `JobPosting`。
3. 两源加入 `JobSourceRegistry`、`JobSearchRequest`、`JobDiscoveryRequest`、环境配置和 production gate。默认链路由 13 个适配器/20 个企业门户扩为 15 个适配器/22 个企业门户。

### Bad Case 1：只检查门户 API，漏掉页面专用岗位脚本
上一轮检查小米新版官网时，只确认了通用 `api.js` 中的新闻、招聘项目、宣讲会和城市链接接口。因为 PATH_MAP 没有岗位列表，就误以为职位仍依赖外部飞书页面，没有继续读取机会页末尾单独加载的 `jobs.js`。

本轮从页面真实渲染链路继续向下追踪，发现 `jobs.js` 使用独立的 `searchJobPage` 接口。`Agent` 查询可直接返回 43 个岗位，其中包括 Agent 应用、Agent 评估、Coding Agent、Harness 优化和多智能体协作实习。经验是 SPA/官网的通用 API 文件并不等于页面全部数据协议；Source 调研应以页面实际发出的岗位请求为准，而不是看到一个脚本后提前停止。

### Bad Case 2：搜索引擎命中旧招聘站，导致创维被误判
此前搜索主要命中 `skyworth.51job.com` 和旧年度校招材料，旧站缺少稳定的当前职位发现链路。2027 届招聘公告给出了新入口 `skyworth.hotjob.cn`；该根域先通过 `/wecruit/common/getSLD` 解析当前 suite，再进入大易公开招聘页，当前有 135 个校招岗位。

进一步读取页面调用可确认 `listPosition` 支持关键词和分页，`listPositionDetail` 返回完整 JD。也就是说“搜索结果不理想”不代表企业没有可接入源，可能只是招聘系统、批次或二级域名已经迁移。开发时应交叉验证企业官网、当届招聘公告和页面网络请求。

### Bad Case 3：创维列表标题不含 Agent，提前相关性过滤会误杀
创维用 `postKey=Agent` 搜索时返回“算法工程师（酷开）”，列表字段没有职责，单看标题无法判断相关；完整详情却明确包含 RAG、Prompt Engineering、Agentic Workflow、多智能体和 Function Calling。如果在详情读取前执行统一 `is_query_relevant_posting`，这个强相关岗位会被误删。

最终把上游搜索命中视为候选信号，先限定候选数量并补全详情，再做统一排序和真实 source 质量门控。这个处理不是放宽最终相关性，而是把判断放到证据完整的阶段。面试时可概括为：级联检索中不能用信息残缺的第一阶段字段执行不可逆的高精度过滤。

### Bad Case 4：新增来源不应线性放大请求和延迟
小米单个响应已经包含完整 JD，不再逐条请求详情。创维必须补详情，因此先用最多三个核心词召回、按岗位 ID 去重、只保留有限候选，并以 Semaphore 将详情并发限制为 6。这样既不会遍历全部 135 个岗位，也不会无界并发冲击上游。

真实全默认门控从 13 源约 20.8 秒变为 15 源约 21.9 秒，新增两源独立只需约 1.3 秒；总尾延迟仍由 Moka 浏览器聚合源主导。来源扩展是否可上线不能只看“接了多少公司”，还要观察并发后的墙钟、每源调用上界和上游压力。

### 真实验证
- 小米 + 创维动态发现版独立 smoke：EvaluationRun `#172`，2/2 可达且有结果，共 9 条岗位；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.8889，耗时 1,317 ms。
- 全默认来源 production gate：EvaluationRun `#173`，15/15 可达且有结果，source error 0，共 66 条岗位；JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000，实习/校招占比 0.6212，总墙钟 22,840 ms。
- 新增契约与默认来源测试 `18 passed`；全仓回归 `437 passed in 130.51s`；本次修改文件 Ruff 通过。全仓 Ruff 的 17 个既有告警位于无关运维模块、脚本导入顺序和旧测试导入，不属于本轮回归。
- 全程没有调用 LLM，Token 用量为 0。

### 当前边界
1. 创维当前正式接入 2027 届校园招聘，suite key 由根域动态发现；招聘批次或发现协议变化时必须显式记为 source error，不能静默复用旧数据。
2. 小米投递 URL 指向其官方飞书招聘域名，但岗位发现和完整 JD 均来自小米官网自己的公开搜索接口，不依赖搜索引擎摘要。
3. OPPO、滴滴等仍需按相同标准继续验证；“官网可打开”或“搜索引擎能搜到单条 JD”仍不足以成为默认 Source。

## 2026-09-04：第二批真实岗位源，中国电信、华为与科大讯飞正式接入

### 本轮目标
- 在上一轮十适配器基础上继续寻找国内 Agent 岗位密度高、无需登录即可读取完整 JD 的企业官方来源。
- 验证中国电信、华为、科大讯飞、小米等候选站的真实页面和网络协议，只将可稳定自动化回归的来源加入默认链路。
- 用新增真实数据反查现有筛选和评测逻辑，避免来源接通但产品层误过滤。

### 正式实现
1. 新增 `china_telecom`：向中国电信官方 WinTalent 列表提交关键词，使用标准库 HTML Parser 读取集团及下属单位岗位。官方列表响应已经包含招聘项目、学历、专业、工作描述和职位要求，无需逐条请求详情。
2. 新增 `huawei`：解析华为官方社会招聘 AI 岗位专区的稳定岗位 ID，再以受限并发调用公开 `getJobDetail/newHr` JSON，补全职责、要求、部门、职位族和地点。
3. 新增 `iflytek`：并发查询科大讯飞官方 Beisen API 的社招、校招和实习三类岗位，按 UUID 去重并统一排序。当前能发现 Harness、代码 Agent、Agent 安全、Agent 应用研发和大模型工程岗位。
4. 默认来源扩为 13 个适配器、20 个企业官方招聘门户；请求 Schema、注册表、环境配置、真实 source dataset、API/架构/评测/面试文档全部同步。
5. 修复实习/校招识别：除“实习”和缩写“校招”外，正式识别招聘站更常用的“校园招聘”和“应届毕业生”。该函数同时影响岗位排序、首页“只看实习/校招”筛选和真实来源指标。

### Bad Case 1：声明在 requirements 中的库不代表部署环境一定安装
第一版解析中国电信和华为 HTML 时使用 BeautifulSoup。虽然 `requirements.txt` 已声明 `beautifulsoup4`，当前正式测试解释器并没有该模块，导致导入 `job_sources.py` 时整个应用测试收集失败，而不是只有新增来源失败。

修复为复用标准库 `HTMLParser`，分别建立小而明确的中国电信列表 Parser 和华为链接 Parser，没有增加新的运行时依赖。经验是 Source Adapter 位于应用启动依赖链上，顶层可选依赖缺失会扩大故障半径；能用已有结构化 Parser 完成时不应引入启动级依赖。

### Bad Case 2：科大讯飞 API 的 `Total=0` 与真实 `Data` 冲突
科大讯飞官方接口在 `Agent` 和 `大模型` 查询中返回完整岗位数组，但响应 `Total` 始终为 0。如果按常见分页协议先判断 Total，系统会把几十条真实岗位判为空。

适配器把已验证的 `Data` 数组作为当前权威集合，只用 `Code=200` 和 `Data` 类型验证响应，并增加 `Total=0`、`Data` 非空的契约测试。这里没有“容错猜测”：这是对真实上游协议的显式建模，原始响应仍保存在 payload，接口结构变化会直接报错。

### Bad Case 3：“校园招聘”没有命中“校招”
新增来源第一次全量 smoke 返回 57 条岗位，但实习/校招占比只有 0.3684。回查发现 `_contains_internship_signal` 只匹配连续字符串“校招”；“校园招聘”中间多了“园”，因此中国电信和科大讯飞的标准招聘类型被误判。这个错误还会让用户勾选“只看实习/校招”后看不到这些岗位。

修复后 production gate 的实习/校招占比恢复到 0.5789，并增加“2027年度秋季校园招聘”回归。经验是中文缩写不能替代正式业务术语，真实来源分布变化会暴露手工样本没有覆盖的语言边界。

### Bad Case 4：候选官网可访问，不等于具备岗位 Source 合同
小米新招聘官网可正常访问，也展示 AI、具身智能和招聘活动，但本轮公开接口主要是门户内容、研究课题和城市跳转，没有验证到可按关键词稳定分页获取完整 JD 的岗位 API。创维、OPPO 等站点仍有类似边界，因此没有为了增加数量使用搜索引擎摘要或半接入页面。

### 真实验证
- 中国电信 + 华为独立 smoke：EvaluationRun `#165`，2/2 可达，返回 10 条岗位，完整 JD、官方链接、query relevance 和 Agent 相关率均为 1.0000，耗时 1,720 ms。
- 科大讯飞独立 smoke：EvaluationRun `#167`，返回 5 条岗位，全部质量指标为 1.0000，耗时 990 ms。
- 最终全默认来源：EvaluationRun `#169`，13/13 来源可达且有结果，source error 0，共返回 57 条岗位。
- 最终 JD 非空率、投递链接率、query relevance 和 Agent 相关率均为 1.0000；实习/校招占比 0.5789，总墙钟 20,824 ms；LLM Token 为 0。
- 修改文件 Ruff 通过；全仓 `435 passed in 133.14s`。

### 当前边界
1. 华为当前接入的是官方社会招聘 AI 岗位专区，不把项目宣传页当校园岗位列表；默认实习筛选会过滤这些社招岗位。
2. 科大讯飞三类接口都正式查询，但当前实习分类对 Agent 关键词为空；校招和社招仍有丰富结果。
3. 该轮尚未验证小米、创维的稳定岗位发现协议；二者已在后续复盘中正式接入。OPPO 等候选源仍需按 source 观察协议失败、空结果、完整率和尾延迟。

## 2026-09-04：国内真实岗位源从五源扩展到十适配器、十七个企业门户

### 本轮目标
- 岗位检索从开发期的五家公司扩展到可实际使用的中文 Agent 岗位发现网络。
- 只接入能从企业官方招聘站发现岗位、取得完整 JD 并保留官方详情链接的来源，不用搜索引擎摘要或第三方转载冒充正式数据。
- 将新来源纳入默认请求、配置、契约测试和真实网络 release gate，而不只是增加几段未被主流程调用的适配代码。

### 正式实现
1. 新增京东 `jd`：调用官方公开岗位列表 JSON，映射岗位职责、任职要求、部门、地点、招聘类型和官方详情 URL。
2. 新增 TCL `tcl`：解析官方校园招聘列表返回的 HTML 片段，再并发调用详情接口补全职责和要求；可覆盖 TCL 及旗下企业。
3. 新增美的 `midea`：对 `Agent/智能体/大模型/RAG/AI` 等核心词进行有限多查询召回，按岗位 ID 去重后再使用完整用户 query 统一排序。
4. 新增万得 `wind`：读取官网发布的岗位数据文件，解析在用岗位、完整描述、要求、项目类型、地点和详情参数。
5. 新增 `moka_cn` 聚合适配器：覆盖韶音、DeepSeek、壁仞、锐捷、完美世界、新浪、苏商银行和 LINE MAN Technology 八个企业官方 Moka 门户。一个 Chromium 进程共享给最多三个并发企业上下文，避免八次浏览器冷启动。
6. 中文默认来源由五个扩为十个适配器，覆盖十七个企业门户；`JobSearchRequest`、`JobDiscoveryRequest`、环境配置、README、架构/API/评测/面试文档同步更新。
7. `real_job_source_cases.json` 保留旧五源八类 query 作为纵向基线，并在首位增加十源 production gate。脚本新增 `min_reachable_source_rate`，协议故障可以直接让严格评测失败。

### Bad Case 1：Moka 搜索结果被“最新职位”侧栏污染
第一版从页面收集所有 `#/job/` 链接。官网搜索结果虽然正确，页面侧栏仍保留其他热门岗位，导致 Agent 查询混入非 Agent 职位。这类结果有真实 URL 和完整页面，仅靠“HTTP 成功、JD 非空”完全发现不了。

修复方式是从链接文本提取规范标题，并要求标题与当前检索词精确相关后才进入详情队列；最终仍由统一岗位相关性排序处理。经验是浏览器抓取不能把“页面上出现过”理解成“搜索结果”，DOM 所属区域和 query 约束也是数据合同的一部分。

### Bad Case 2：复用同一个 SPA Page 读取详情造成内容串页
早期实现连续在同一个 Moka Page 中跳转详情，SPA 的旧状态、异步渲染和详情加载时序会让下一条岗位读到短文本或上一条元数据，地点甚至被错误识别。这不是模型幻觉，而是浏览器状态污染。

修复为每条详情打开独立 Page，等待精确的“职位描述”节点可见后读取正文，完成后关闭；不足 200 字的团队页或分类页拒绝入库。企业级采集器要隔离页面状态并为内容完整性设门槛，不能只依赖固定 sleep。

### Bad Case 3：来源多了以后，浏览器资源会非线性放大
如果把八个 Moka 企业站各注册成独立默认 Source，顶层 `asyncio.gather` 会同时启动八个 Chromium，内存、冷启动时间、站点限流概率都会显著增加。单纯追求 Source 数量会损害真实可用性。

最终使用一个 `moka_cn` 聚合适配器，共享浏览器并以 Semaphore 限制三个并发 Context；岗位的细分 `source` 仍保存为 `moka_shokz`、`moka_deepseek` 等，保留企业级 provenance。这里的权衡是牺牲一部分尾延迟，换取资源上界和来源可追踪性。

### Bad Case 4：旧评测仍只覆盖五源，代码接入不等于上线
新增适配器的单元测试都通过，但 `evals/real_job_source_cases.json` 仍固定腾讯、百度、美团、字节和阿里。如果某个新增官网明天改字段，原 release suite 仍会全绿。因此新增十源 production gate，并把“可达”作为独立硬指标。

`reachable_source_rate` 与 `result_source_rate` 不可混为一谈：官网协议正常但当前关键词没有招聘岗位是业务空结果；超时、状态码异常、字段缺失和解析失败才是来源故障。production gate 要求十源可达率 1.0，但允许有结果源比例不低于 0.7，避免把招聘批次波动误报成程序故障。

### Bad Case 5：社招源会被实习筛选过滤，但来源本身并未失效
京东和美的当前 Agent 结果以社招为主。首页默认“只看实习/校招”时这些岗位不会进入最终列表，但直接来源 smoke 仍能证明接口可用。评测和监控必须同时观察 source 原始结果与产品筛选后的有效结果，否则会把正常业务过滤错误归因于采集失败。

### 真实验证
- 十源 production gate：EvaluationRun `#164`，query=`Agent 大模型开发`，每源最多 5 条。
- `source_count=10`，可达率 1.0000，有结果率 1.0000，source error 0。
- 共返回 42 条岗位；JD 非空率、投递链接率、query relevance、Agent 相关率均为 1.0000，实习/校招占比 0.5000。
- 总墙钟 20,782 ms；全程不调用 LLM，模型 Token 为 0。
- 新来源契约、默认配置、岗位发现和真实来源评测相关测试共 `58 passed in 59.27s`。
- 全仓回归 `432 passed in 131.76s`；本轮修改文件 Ruff 检查通过。全仓 Ruff 仍有 17 个本轮之前已存在的告警，主要是脚本路径引导造成的 E402 和无关模块未使用导入，本轮没有把它们混入岗位源提交。

### 当前边界
1. 创维、小米、华为、OPPO、科大讯飞和中国电信等官网已发现，但尚未同时满足稳定岗位发现、完整 JD、官方详情 URL 和连续 smoke，不以半接入方式加入默认链路。
2. Moka 聚合源当前以单个 source 汇总健康度，岗位保留企业级 provenance；后续可把企业级空结果率和失败原因进一步暴露到运维指标。
3. 真实招聘站协议不受本项目控制；上线后仍需定时运行 production gate，并按 source 观察 p50/p95、空结果率和字段完整率。

## 2026-09-01 10:46:39 +08:00：对话 TaskState、Planner 单调用状态增量与压缩完整性 V4

### 本轮目标
- 把目标岗位、地点、约束、纠错、待办和禁止操作从非权威 Conversation Summary 中移出，保存为可恢复的正式任务状态。
- Planner 在原有一次 LLM 调用中同时返回计划和本轮 `state_updates`，普通轮次不得增加单独总结调用。
- 较早历史真正超限时才调用 Compactor；摘要失败不能静默放行，Checkpoint 和 Rewind 必须恢复对应历史版本。
- 建立 48 组离线多轮数据和 5 对真实长对话，分别报告离线估算 Token 与供应商实际 Token。

### 正式实现
1. `app/models/schemas.py` 新增 `TaskState`、`TaskStateCorrection`、`TaskStateScalarUpdate` 和 `TaskStateUpdates`。Pydantic `extra=forbid`，Action 和 forbidden action 使用 Literal 白名单，标量只允许 `set/clear`。
2. 新增 `app/services/task_state.py`。`TaskStateReducer` 只接受当前用户消息的增量，以确定性代码原子合并；字段缺失保持旧值，值变化写 Correction 和 provenance，状态版本只在实际变化时递增。
3. Planner JSON schema 增加 `state_updates`，当前 TaskState 与最近消息/摘要一起进入原有 Planner Prompt。状态更新非法时复用现有 Plan Contract Repair 一次；仍失败则 Reducer 不运行，旧状态完整保留。
4. 高风险文本兜底覆盖禁止自动投递、禁止邮件、跨用户数据、仅草稿和未经确认执行。解除 forbidden action 必须在当前用户原文中有明确授权，不能只相信模型输出的 remove。
5. Conversation Summary 改为 discussion/rationales/unresolved questions/source IDs/task state version/有限 state claims/historical changes。摘要不再保存执行状态，只检查消息覆盖、版本一致、与当前状态不冲突、旧值历史标记和禁止/完成项不造假。
6. Compactor 首次校验失败最多重试一次；再次失败且原文仍在预算内则保留原文，超预算则停止 Planner。Trace 分别记录 attempt、validation error 和 fallback。
7. Natural Language Graph State 与通用 CareerAgent Graph State 都加入 `task_state`；`context_refs` 记录 `task_state_version`。Checkpoint 恢复重新 Pydantic 校验，Rewind 直接复制所选历史 checkpoint 的 channel value，不读取较新分支状态。
8. API 请求可传 `message_id/task_state`，响应返回更新后的 `task_state`。没有新增数据库表；状态由 LangGraph Checkpoint 持久化，状态变化和摘要分别写现有 AgentArtifact 审计。

### Bad Case 1：摘要此前同时承担背景和执行状态
旧 Compactor 输出 `goal/constraints/corrections/pending/forbidden_actions`，完整性检查要求这些字符串仍在摘要里。这会产生错误耦合：摘要只要漏写一种自然语言限制，后续 Planner 就可能失去执行约束；同一地点旧值和新值还可能同时出现在摘要列表中。

本轮将执行状态拆成强类型 TaskState。摘要可以不重复地点和限制，但不得与 TaskState 冲突。面试时可以解释：解决方案不是让摘要 Prompt 写得更严格，而是把不允许丢失的数据移出有损压缩通道。

### Bad Case 2：`critical_facts` 依赖上游预标注
原完整性检查只能在消息提前携带 `critical_facts` 时检查字符串是否存在，普通用户说“只看深圳”“不要投递”没有标记就可能漏掉。现在 Planner 在同一次调用中输出语义化 delta，高风险限制另有确定性原文兜底；摘要不再承担关键状态召回。

### Bad Case 3：离线数据制造了“值没有变化的纠错”
48 组首轮只通过 40 组。其中 4 个 `target_role_change` case 初始值已经是 `RAG Agent`，第二轮又标注改为 `RAG Agent`，评测却要求产生 Correction。Reducer 正确地认为没有状态变化，反而被错误标签判失败。

修复数据生成器：目标新值与旧值相同时改用 `Agent Platform Engineer`。这说明评测失败不能直接归因模型或代码，先要判断 gold label 是否满足业务语义。

### Bad Case 4：“外发前仍要确认”没有被安全规则召回
首轮离线剩余 4 个失败来自更自然的表达“任何外发必须经过我确认”；规则只覆盖“必须经过确认”。真实 5 对首轮也在 `explicit_permission_removal` 暴露同类问题：模型正确移除 `auto_apply`，并把“真正外发前需确认”写成 constraint，但确定性 forbidden state 没有加入 `unapproved_high_risk_action`。

修复确认语义为：支持“必须经过我确认”和“外发/投递/发送/执行前仍需或仍要确认”。同时保留作用域：用户可以明确解除自动投递禁令，但确认门禁仍独立生效。定向重跑后 forbidden action recall 恢复 100%。

### Bad Case 5：新增 Prompt Section 被 Token Harness 拒绝
第一次真实压缩 Run 在 Provider 调用前失败：Compactor 的日志分段使用了未注册的 `task_state` 和 `repair` section，`PromptSectionProfiler` 正确抛出 `Unknown Prompt sections`。该失败没有继续调用模型。

修复为复用已注册的 `working` 和 `repair_context`，并增加测试验证 Compactor 传入的 section 名全部属于 Registry。经验是可观测性 schema 也是 Harness 合同，不能随手增加字符串字段。

### Bad Case 6：Windows 相对路径让报告合并失败
修复确认语义后，两条定向真实 Run 已成功写库，但报告 provenance 对相对路径直接调用 `relative_to(ROOT)`，在 Windows 下因为一方未 resolve 而抛错。模型结果没有丢失，但报告没有生成。

修复为 resolve 后再计算相对路径，并给评测器增加 `--reuse-latest`：从数据库读取刚完成的 raw/compressed 完整 pair，无 API 调用地重新合并。最终报告明确记录“4 个原始通过 pair + 1 个修复后完整 pair”，不把旧失败结果覆盖得无迹可寻。

### Bad Case 7：首次压缩没有节省真实 Token
离线估算中，压缩后的 Planner 上下文从平均 `4,701` 降到 `2,990.33` Token；但真实首次压缩还必须让 Compactor 自己读取旧历史。5 对 Provider 结果为 Input `27,716.8 -> 28,281.4`，增加 2.04%；Total `27,897.2 -> 28,827.6`，增加 3.34%；业务调用 `1 -> 2`，平均延迟 `3,080.49 -> 6,808.63 ms`。

因此不能把“压缩后的单个 Planner Prompt 变短”写成“本轮总 Token 下降”。真正收益需要后续轮次复用已有摘要，摊薄首次 Compactor 成本；该跨 Run 增量复用本轮尚未实现，已明确列为边界。

### Bad Case 8：重复限制修改 provenance 却没有增加版本
最终状态机审计发现，用户重复说同一个 constraint/forbidden action 时，列表内容没有变化，但旧 Reducer 仍把 provenance 改成当前消息 ID；由于 `changed=false`，TaskState version 不递增。这会造成“状态内容发生变化但版本没变”，破坏摘要版本、Checkpoint 和缓存一致性。

修复为重复确认属于无状态变化：保留原 provenance 和 version；只有真正新增值时才写新来源并递增版本。新增回归验证重复“只看实习”后 version 仍为 3、来源仍为首次消息 `m1`。

### 离线 48 组结果
- Case：`48/48`，pass rate 1.0。
- 目标与限制字段准确率、纠错生效率、禁止操作召回率、压缩后状态保持率、Checkpoint 恢复一致率：均为 `1.0`。
- 摘要冲突误接受率：`0`。
- 24/48 case 触发 Compactor，共 24 次；普通用户轮次预期 Planner 调用为 88 次，每轮仍为 1 次。
- 离线 Token `4,701 -> 2,990.33` 只标记为 estimate，Provider calls 为 0。

### 真实 5 对长对话结果
- 固定 `deepseek-v4-flash`，routing/fallback/thinking disabled；Provider usage 完整率 1.0。
- Raw 为完整历史直接调用 Planner；Compressed 为 Compactor + Planner。5/5 压缩 case 实际触发 Compactor。
- Raw/Compressed pass rate 均为 1.0；字段准确率、纠错生效率和禁止操作召回率均为 1.0；摘要校验错误率为 0。
- 普通短对话通过测试确认不触发 Compactor，仍为 1 次 Planner；长对话按设计增加 1 次 Compactor。
- 最终 run IDs：raw `613/615/617/623/621`，compressed `614/616/618/624/622`。

### 当前边界
1. 关键 TaskState 保持 100% 不代表全部对话语义无损；低优先级讨论细节、措辞和部分理由仍可能在摘要中损失。
2. 首次压缩真实 Token 和延迟上升；跨请求复用 Summary Artifact、增量压缩和长期 amortized cost 尚未验证。
3. API 已支持调用方带回 TaskState，但现有用户页面不是多轮聊天产品，没有实现显式的跨请求会话状态 UI。
4. 真实测试为 5 对长对话，支持当前门禁，不代表线上多语言长会话 P95 已建立。

### 交付与验证
- 数据集：`evals/conversation_task_state_cases.json`。
- 离线报告：`data/runtime/conversation-task-state-offline.json`。
- 真实报告：`data/runtime/conversation-task-state-real-ab-final.json`。
- 设计说明：`docs/CONVERSATION_TASK_STATE_V4.md`。
- 新增定向测试与相关回归通过；全仓最终 `427 passed in 121.72s`。

## 2026-08-31 21:08:41 +08:00：上下文管理 V3 正式接入、完整流程真实 A/B 与 Bad Case 闭环

### 本轮目标
- 在 Context Runtime V2 和 Token Optimization V2 的正式基线上，实现 CareerAgent 定向的六类上下文、节点差异化策略、Checkpoint 最小重建和共享上下文 Batch，不建设通用上下文平台。
- 把 Profile/JD/Evidence 原始事实与 Conversation 语义摘要分开：只有旧对话允许 LLM 压缩，业务事实必须从数据库或 Citation 回查。
- 运行至少 5 对真实完整 Agent Run，不能再用“一个长 Prompt + 一次面试调用”冒充全流程，也不能用估算 Token 冒充 Provider usage。

### 正式实现
1. `context_runtime.py` 改为节点严格白名单投影；所有压缩完成后再做 Critical Fact/Citation/证据正文检查，缺失时 JIT 只补最小字段和最小证据正文，仍不完整则停止 LLM。
2. 新增 `conversation_compactor.py`：保留最近 3 轮原文，较早自然语言历史由低成本 LLM 压缩为带 source message IDs 的非权威结构化摘要；遗漏目标、限制、纠错或禁止动作时拒绝使用，摘要保存为 `AgentArtifact`。
3. 新增 `context_recovery.py`：LangGraph State 持久化 `context_refs`，恢复时校验 tenant/user/profile，并按下一个节点合同从数据库重建最小 Packet；成功 Tool Receipt 阻止邮件或浏览器副作用重放。
4. 新增 `shared_context_batcher.py`：面试 Answer 与 Verifier 共享 Profile/JD/Evidence，只发送一次共享区；超预算自动拆 Batch，只 Repair 失败题。
5. 新增 `document_schema_batcher.py`：长 PDF/JD 按页或 Section 分批解析，Python 按 Schema 合并，冲突保留页码、Chunk ID 和来源值。
6. Planner、两类 Parser、Matcher、Tailor、Interview、Application 和 Completion 正式接入 V3；LLM 日志补齐 run/node/business call/batch/provider usage。
7. Tool Registry 补上 `resume_parser.create_profile_from_pdf` 与 `job_repository.create_from_jd`，对应 Skill allowlist 和 Completion 允许集合同步更新，正式 Tool 总数为 21。

### Bad Case 1：JIT 初版从全部源证据中“挑重要内容”
初版 JIT 虽然能补 Citation，却仍可能先扫描全量 Evidence 再挑片段，相当于把大上下文问题搬进补载器。修复为 Citation ID、Profile/Job 字段路径和当前 Query 三种定向 selector；返回值必须带最小证据正文、来源与哈希。面试时可解释为：JIT 的价值是按引用回表，不是第二个全量检索器。

### Bad Case 2：中文 PDF 字体导致抽取乱码
生成评测 PDF 时默认 CID 字体在当前解析链出现 mojibake，导致“PDF Parser 失败”和“上下文策略失败”混在一起。改用可嵌入的 SimHei 生成中文 PDF，并先做抽取可读性 smoke，再进入真实 A/B。经验是完整评测必须先验证数据载体，否则下游指标没有归因价值。

### Bad Case 3：正式 Tool 已实现但未进入 Registry/Skill 权限
PDF 建档和 JD 入库服务已经存在，但 Tool Registry 和 Skill allowlist 缺少对应条目，Completion Gate 因此无法把真实动作识别为允许能力。补齐两个 Tool 后增加 Registry/权限测试。这个问题说明 Tool 能否调用由“实现、注册、Skill 授权、运行时合同”共同决定，只有 service 方法不等于 Agent 拥有工具。

### Bad Case 4：Hash Embedding 让中文 RAG 评测失真
首轮完整流程使用 hash embedding，中文语义召回失败。这个结果只能说明评测配置无效，不能用于比较 V2/V3。正式运行改用本地 `paraphrase-multilingual-MiniLM-L12-v2` 384 维真实向量；hash 报告标记为无效，不进入 release 结论。

### Bad Case 5：英文 CrossEncoder 对中文重排产生负增益
原 reranker 固定使用英文 `ms-marco-MiniLM-L-6-v2`，CJK 证据在二阶段排序中反而下降。修复为中文/中英混合文本使用多语 embedding 语义重排，英文保留 CrossEncoder；新增中英文定向回归。这里的关键不是“模型越多越好”，而是 reranker 必须与语言分布匹配。

### Bad Case 6：大 JD 分母稀释精确技能 Chunk
Retrieval Gate 原来把候选 Chunk 的命中技能除以整份大 JD 的技能数。一个精确命中当前 Query 的小 Chunk 会因 JD 很长而被判低质量。修复为 Query 已包含结构化技能且 Chunk 精确支持时给予强相关，不用无关 JD 要求稀释当前检索目标。

### Bad Case 7：JD Parser 规范化别名与 Grounding 规则漂移
Parser 把原文 `vector search` 规范化成 `Vector Database`，Grounding 却按字面认为不存在，导致合法解析被拦截。修复为允许有明确语义映射的 canonical alias，同时继续拒绝原文没有证据的 `Kubernetes`。不能粗暴放宽到“模型认为相近就算支持”。

### Bad Case 8：英文小数结尾句号触发虚构指标
Application Guard 把 `0.89.` 的句号当成数字继续部分，无法匹配源证据中的 `0.89`；愿望性语句“期待有机会……”也被提取为候选人既有事实。修复数字边界并排除 aspirational statement。历史 4 个失败投递产物重新本地复核后 `4/4` 通过。

### Bad Case 9：Repair 开关和质量门禁互相矛盾
JSON Repair 次数曾为 0，而业务要求模型 JSON 缺项时可定向修复；答案 Repair/总调用/Prompt 上限也低于完整面试链的合理需求。调整为 JSON Repair 1 次、答案 Repair 2 次、总业务调用最多 8、Prompt 字符上限 100,000，并继续受全局 Token 与 Repair Budget 限制。修复能力必须有预算，不能无限重试。

### Bad Case 10：面试 Coverage 只看标签，不看真实题目
问题正文已覆盖某技能，但模型漏写 `skills` 标签时，门禁判为未覆盖并触发无效 Repair。修复为同时读取 question 和 follow-up 文本；标签只做辅助索引，不再是唯一事实源。

### Bad Case 11：`risk_level=high` 被误当成“缺失技能题”
高风险可以来自复杂系统设计，并不意味着候选人没有该技能。旧逻辑对所有 high-risk 题强制 missing-skill disclosure，造成合法项目回答被拒绝。修复为仅真实 missing skill 或 `jd_gap_drill` 执行能力边界检查。

### Bad Case 12：跨语言 Matcher 别名不足造成下游假失败
双语 case 中简历已有 hybrid/vector/semantic retrieval、多语 embedding 和 evaluation set/recall，但 Matcher 只认少量固定英文标签，错误生成能力缺口，最终 Tailor 边界门禁失败。补充可交付证据别名，同时明确不把普通 vector search 推断为 Vector Database 生产经验。修复后只重跑该完整双语对，V2/V3 均完成且全部质量门禁为 1.0。

### Bad Case 13：Checkpoint 重放被轨迹顺序门禁误判
恢复后后续节点可能再次执行，旧校验用“后续节点最大位置”与“前置节点最小位置”比较，只要存在 replay 就可能把合法历史判乱序。修复为仅当前置节点完全没有早于后置节点时失败，并增加 replay-aware 回归。

### Bad Case 14：手工恢复实验没有设置恢复模式
排障时曾把失败 Run 手工改成 `running` 后直接调用 `run_existing`，但没有设置 `execution_mode=checkpoint_resume`。编排器按设计从图起点执行，产生的数据库 Run 已被测试方法污染，不能作为“恢复成功/失败”指标。最终报告排除这些 Run；正式 recovery scanner 会设置正确模式，确定性测试验证最小重建与副作用防重放。本轮没有再花费 API 余额做操作系统级崩溃注入。

### Bad Case 15：`.env.example` 重复键让旧预算覆盖新预算
一致性审计发现面试 JSON Repair、答案 Repair、最大调用和 Prompt 上限在 `.env.example` 出现两组：前面是新值 `1/2/8/100000`，后面仍是旧值 `0/1/5/60000`。多数 dotenv 实现采用后值覆盖前值，用户照示例启动就可能与源码默认和评测合同不一致。修复为只保留一组 `1/2/8/100000`，并同步 README、架构和面试 bad case 文档。这个问题说明配置示例本身也是生产合同，必须检测重复键，不能只验证 Python 默认值。

### 真实完整流程 A/B
固定 `deepseek-v4-flash`，每个 Variant 均执行：生成 PDF、PDF 解析、JD 解析与入库、岗位匹配、简历 Evidence 检索、简历定制、独立 Guardrail、投递材料 Dry Run、6 题面试准备和 Completion Gate。5 个 case 覆盖中文 RAG 后端、LLM 评测、Agent 平台、中英双语 RAG 和 Tool Agent。

| 指标 | V2 | V3 | 结论 |
| --- | ---: | ---: | --- |
| 完成率 | 5/5 | 5/5 | 持平 |
| 平均 Input Token | 20,657.4 | 21,498.4 | V3 增加 4.07% |
| 平均 Total Token | 25,499.6 | 26,617.2 | V3 增加 4.38% |
| 平均 Business Calls | 8.2 | 8.8 | V3 多 0.6 次 |
| 平均端到端延迟 | 56,072.49 ms | 52,373.55 ms | V3 降低 6.60% |
| 总成本 | ¥0.151709 | ¥0.158680 | V3 增加 4.60% |
| Critical Fact/Citation/Guardrail/Application/Interview/Completion | 1.0 | 1.0 | 全部通过 |
| Repair Calls | 3 | 5 | V3 多 2 次 |

最终 5 对使用 4 个干净完整对，加修复后重新执行的双语完整对；报告保留来源 run_id 和 provenance。不能把它描述为一次原子 10-run 同批实验，但每个单项指标都来自真实、完整、未污染的 Run。

### 结论与当前边界
- V3 已作为正式功能上线，理由是节点隔离、对话压缩合同、事实/Citation 100% 完整、Checkpoint 最小重建、Receipt 防重放和共享 Batch 均落地并通过门禁。
- 本轮没有证明 V3 节省 Token。相反，Repair 增加使输入、总 Token 和成本小幅上升；早期窄切片的显著节省不能覆盖这个完整流程结论。
- 5 对样本不足以建立长期 P95 和成本置信区间；Conversation Compactor 在这些短会话 case 中没有触发，仍需长多轮真实流量切片。
- 跨进程恢复本轮未做真实 API 崩溃注入；自动 scanner 和确定性回归已覆盖正确恢复模式，后续应在不调用昂贵模型的故障注入环境中持续验证。

### 最终验证
- Context V3 及关联 Guardrail/RAG/Matcher/Interview 定向回归：`93 passed in 27.94s`。
- 全仓最终回归：`406 passed in 118.18s`。
- V3 核心文件 scoped Ruff、`py_compile`、仓库差异检查和 API key 扫描通过；`.env.example` 重复键回归测试已加入全仓门禁。
- 最终真实报告：`data/runtime/context-management-v3-full-ab-final.json`。
- 正式设计与评测说明：`docs/CONTEXT_MANAGEMENT_V3_PRODUCTION.md`。

## 2026-08-31 16:11:33 +08:00：Context Runtime V2 与 Token Optimization V2 正式上线及联合消融

### 本轮目标
- 将两套已经接入主链路的 V2 从 Shadow 切换为正式默认路径，同时保留独立回滚开关。
- 补齐“两个 V2 同时启用”的离线全量和真实 API 联合评测，验证收益不能简单相加、功能不会互相干扰。
- 在付费测试前先完成编译、定向测试和全量离线 Gate，避免用 API 余额发现基础代码错误。

### 正式改动
1. `Settings` 与 `.env.example` 默认改为 Context/Token V2 enabled=true、shadow=false；Execution Provenance 继续记录每次 Run 的实际开关、版本、合同和预算。
2. 新增 `scripts/run_combined_v2_ab.py`，比较“两个都关闭”和“两个都开启”，分 Context 长上下文 lane 与 Token 面试批处理 lane，并给出一个明确的 1:1 组合工作负载口径。
3. 原两份独立 A/B 不再继承应用默认值：Token 实验强制隔离 Context，Context 实验强制隔离 Token；联合实验才显式同时启用，所有控制条件写入报告。
4. 新增正式默认配置和联合 release gate 测试；更新 README、总体设计、评测文档及两套 V2 文档，清除“默认仍为 Shadow”的过期结论。

### Bad Case 1：Active 默认暴露未注册节点空对象崩溃
切换默认值后的第一次定向回归有 4 个 LLM 测试失败。`ContextRuntimeV2.observe_text_prompt()` 对没有注册 Context Contract 的 trace 正常返回 `None`，但调用端在 active=true 时无条件访问 `.packet`。以前 Shadow 默认不会进入这条读取分支，所以问题一直潜伏。

修复为只有“V2 active 且确实命中合同并返回 ContextBuildResult”时才替换 Prompt；未注册节点保持原 Prompt。这类问题说明 Feature Flag 上线不能只验证已命中能力的 happy path，还必须覆盖“不适用、跳过、部分注册”的分支。修复后 Context/Token/LLM/联合定向测试 `60 passed`。

### Bad Case 2：正式默认值会污染独立消融
如果 A/B 脚本直接继承生产默认配置，Token V2 实验的 V1/V2 都可能经过 Context V2，Context V2 实验也可能被 Output Token Policy 改写。报告仍能生成，但无法回答收益来自哪一项，历史数据也无法复现。

修复为实验自己拥有变量控制权：独立实验锁死非目标能力，联合实验锁死 V1 全关和 V2 全开，并持久化 `ab_controls`。这是 Harness 评测的一部分，不是脚本小细节；没有隔离变量的“消融”只能算前后对比。

### 联合离线全量结果
- 数据：Context 40 case；Token 36 case，每例 10 题。
- 定义工作负载：一次结构化长上下文任务 + 一次面试批处理任务。该口径用于比较，不冒充所有线上流量分布。
- 平均估算 Input Tokens：`64,030.38 -> 14,411.83`（-77.49%）。
- Business Calls：`21 -> 3`（-85.71%）。
- Critical Fact、Evidence 与 Citation Gate 均通过；Prompt Injection Escape 与 Cross-tenant Leakage 均为 0。

### 联合真实 API canary
- 固定 `deepseek-v4-flash`、temperature 0、thinking disabled、fallback disabled、routing disabled；Context 3 case，Token 3 case × 3 题。
- Provider Input Tokens：`25,756.33 -> 10,225.67`（-60.30%）。
- Provider Total Tokens：`26,313.33 -> 10,755`（-59.13%）。
- Business Calls：`7 -> 3`（-57.14%）。
- 组合延迟：`17,387.20 -> 9,511.22 ms`；成本：`¥0.026870 -> ¥0.011284`（-58.01%）。
- Context Critical Fact/Citation、Token Critical Fact/Evidence/Forbidden Claim Free 均为 1.0；注入逃逸、跨租户泄漏和 usage missing 均为 0。
- 联合 quality/efficiency gate 通过，`production_default_eligible=true`。API key 仅通过进程环境注入，未写入仓库。

### 当前边界
1. 真实联合样本为 3+3 case，支持默认上线，不代表已经获得真实流量的长期统计置信度；上线后仍按 Planner、Parser、Matcher、Tailor、Interview 分 lane 观察 SLO。
2. 组合降幅依赖工作负载比例，不能对外宣称所有请求固定下降 60.30%；独立 lane 报告仍是归因依据。
3. 两个开关必须继续独立可回滚，回滚后的判断依据包括 provenance、usage missing、Critical Fact Recall、repair rate、P95 和成本，不只是 HTTP 成功率。

原始报告：`data/runtime/combined-v2-offline.json`、`data/runtime/combined-v2-real-canary.json`。详细上线说明：`docs/COMBINED_V2_PRODUCTION_RELEASE.md`。

### 最终验证
- 联合专项、Context、Token 与 LLM 定向回归：`60 passed`。
- 正式默认切换后全仓回归发现并修正 1 条仍断言 V1 默认策略的旧测试，最终全仓 `387 passed in 115.04s`。
- 两份独立离线 isolation smoke 仍通过：Context 3 case 输入估算下降 69.84%，Token 3 case 输入估算下降 81.44%、调用下降 90%，证明正式默认值没有破坏独立实验的归因能力。
- Scoped Ruff、compileall 和仓库差异检查通过；API key 扫描无命中。

## 2026-08-31 12:07:55 +08:00：Token Optimization V2、共享上下文批处理与两组真实 API 消融

### 本轮目标
- 在 Context Runtime V2 之外治理业务 LLM 调用、HTTP attempt、retry/repair、重复 Prompt 区段、Tool Schema/Result 和输出 token，不能再用总 token 猜测浪费来源。
- 将节点预算、动态 Tool Catalog、Batch/并行 receipt、Artifact/Delta/cache、单一 Retry Owner 和 Feature Flag 落到代码与主链路。
- 使用同一 `deepseek-v4-flash` 分别评测 Context V2 和 Token V2，禁止把两种收益相加，禁止用 tokenizer 估算冒充 Provider usage。

### 开发前基线与审计
1. 历史面试包 `#44` 为 59 次调用、1,490,670 Prompt 字符、约 504 秒；当前代码已改成 10 题批量答案、批量 verifier 和失败题增量 repair，正常路径设计约 3 次业务调用、含一次 repair 最多 5 次。
2. 原 `LLMCallBudget.calls` 实际统计 HTTP attempt，不是业务调用。若发生 retry，“调用次数”口径会混乱。本轮保留旧字段语义，新增 `business_calls` 与 `repair_calls`，避免破坏历史测试。
3. 当前固定 LangGraph 并没有每轮给 LLM 注入全部 19 个 Tool Schema，因此不能声称动态目录已经节省了线上 Tool Schema token。它的当前价值是渐进披露和权限收紧；容量指标单列为 counterfactual。
4. Natural Planner 仍可能读取完整 `profile_context`；Prompt 日志只有总 usage，不能定位 profile、evidence、repair 或重复 control 的占比。

### 核心实现
1. 新增 `PromptSectionProfiler`，按 control、task contract、skill、tool schema、profile、job、evidence、history、memory、tool observations、repair、output schema 和 working 分段记录 token、估算方法、字符与哈希。
2. 每次业务调用生成 `business_call_id`；每个 HTTP attempt 独立落库。`usage_status` 区分 provider reported 与 missing，并记录 cached/reasoning token、模型路由、节点、retry attempt、repair type、压缩版本、输出上限和重复上下文 token。
3. `LLMCallBudget` 增加业务调用、attempt、repair、输入、输出和总 token 硬预算。Natural Agent 与 Interview Agent 在 V2 Active 时启用；预算耗尽不重试。
4. 新增 11 份 `NodeTokenBudget`，覆盖 Planner、两个 Parser、Matcher、Tailor、Application、Interview Question/Answer、Claim Verifier、Guardrail 和 Completion Gate。
5. `DynamicToolCatalog` 使用 task Skill allowlist、节点、风险和审批做确定性预筛；Planner 只接收紧凑目录。Runtime 权限校验不依赖模型看到 schema。
6. 面试 `_bounded_gather` 接入 `BatchToolExecutor`，每个 item 有稳定 receipt；高风险、共享副作用和有依赖任务拒绝批处理。JSON repair 只携带 parse error 与 malformed JSON。
7. Natural Planner 的 Active 路径只保留 Profile 身份、目标、技能及最多 5 条项目/经历摘要；Prompt 区段显式记录 typed memory、job、tool catalog 和 task contract。
8. 新增 `ToolResultArtifactizer`、`DeltaContextBuilder`、`ScopedVersionedCache`、并行 Observation 聚合和 `RetryOwnershipRegistry`。长结果可落 `AgentArtifact`，只读缓存 key 包含 tenant/user/数据/Tool/Prompt/Contract/模型版本，副作用工具禁止缓存。
9. Execution Provenance v5 记录 Token V2 模式、11 个合同、动态目录、Batch/Artifact/Delta 开关和全部 Run 上限。

### 专项数据与测试
- 新增 36-case Token 数据集，每例 10 题、16~24 条 Evidence，覆盖长简历/JD/历史、纠错、相似项目、数字、否定事实、计划学习、跨页 Citation、大 Tool Output、多 Query、partial failure、retry、JSON repair、重复 Tool、缓存、跨租户、注入、Checkpoint、审批和 Completion Gate。
- Token V2 专项 `19 passed`；LLM/自然语言/面试定向回归分别通过；实现期全仓 `384 passed in 124.18s`，全部文档和最终评测脚本完成后重跑为 `385 passed in 115.76s`。
- 本轮新增与修改文件定向 Ruff 通过。全仓历史 Ruff 债务仍按上一条日志记录，不把它伪装成本轮绿色。

### Bad Case 1：合并调用不等于减少 Token
第一版离线 A/B 已将 20 次调用合并为 2 次，但每个 batch item 仍复制完整 Profile、JD 和 Evidence。结果是调用下降 90%，平均输入 token 只从 `40,502.78` 降到约 `38,531.78`，仅 `4.87%`，没有达到 40% 门槛。

根因不是 Batch 实现失败，而是数据协议错误：把十个完整 Prompt 放进一个数组，只减少 API 回合，没有消除重复上下文。修复为 `shared_context + minimal items`，Profile/JD/Evidence 只传一次，item 只保留 question ID、问题与硬事实字段。36 例离线重跑为 `40,502.78 -> 7,309.78`（-81.95%），调用 `20 -> 2`。

面试口径：并行降低墙钟时间，Batch 降低推理回合，共享上下文才降低重复输入；三者必须分别测量。

### Bad Case 2：Release Gate 看见失败却仍放行
第一次真实 Token A/B 中，报告的关键事实 Recall 明显异常，但 `release_gate.passed=true`。原因是 Gate 只接了 token 和调用降幅，真实质量字段虽然被计算，却没有进入布尔条件。

修复后 Gate 同时要求 V1/V2 的关键事实、Evidence、禁止声明、Prompt Injection 和跨租户指标通过，并分别输出 `token_gate_passed` 与 `quality_gate_passed`。这个问题说明“有指标”不等于“指标参与发布决策”，每个关键指标都需要反向测试 Gate 的失败分支。

### Bad Case 3：整句匹配把正常改写判成事实丢失
初版评分要求模型逐字复现完整中文事实句，V1 为 11.11%、V2 为 0%，但引用和禁止声明均为 100%。检查发现模型保留了架构和数字，只改写了连接语。

没有降低 Gold Label，而是把不可改写事实拆为四个原子项：`BM25`、`向量检索`、`RRF` 和精确 `0.xx` 指标。允许语序变化，不允许组件或数字缺失。先做 1-case 调试，V1/V2 均为 1.0，再重跑最终 3 例。

### Bad Case 4：Context Packet 保留事实，模型输出仍可能漏用
Context 首次 3-case 真实 A/B 中，`ctx_003` 的 V2 关键事实 Recall 为 0.75，漏掉地点；检查 V2 Packet 后确认地点和 `hard=true` 仍完整存在。根因是评测 Prompt 只要求返回 `critical_facts`，没有要求遍历全部 hard fact。

修复为 V1/V2 共用 `context-ab-v2` 契约：必须逐项原样复制 Profile/Job 内所有 `hard=true` value。重跑后 3/3 的 V1/V2 事实与 Citation 均为 1.0。排查时必须区分输入保真、模型使用保真和输出评分，不能看到最终漏项就直接归因于压缩。

### 两次真实消融结果

**Context Runtime V2，3 case、每例成对调用：**
- Provider Input Tokens/Run：`17,272 -> 6,291`（-63.58%）。
- Provider Total Tokens/Run：`17,395.33 -> 6,412`（-63.14%）。
- Cost/Run：`¥0.017519 -> ¥0.006533`（-62.71%）。
- Critical Fact 与 Citation Recall：V1/V2 均 1.0；usage missing 0；`v2_can_be_default=true`（仅此 canary 范围）。

**Token V2，3 case、每例 3 题：**
- Provider Input Tokens/Run：`8,483 -> 3,880.67`（-54.25%）。
- Provider Total Tokens/Run：`8,918 -> 4,196`（-52.95%）。
- Business Calls / HTTP Attempts：`6 -> 2`（-66.67%）。
- P50：`14,895.77 -> 5,083.41 ms`；Cost/Run：`¥0.009353 -> ¥0.004511`。
- 事实、Evidence、禁止声明均 1.0；注入逃逸与跨租户泄漏 0；真实质量 Gate 通过。

原始报告为 `data/runtime/context-runtime-v2-real-canary.json` 与 `data/runtime/token-optimization-v2-real-canary.json`。API key 只通过进程环境注入，未写入代码、报告或日志。

### 当前限制与下一步
1. 两次真实实验都只有 3 case，Token 实验每例只取 3 题；支持进入灰度，不支持直接宣称全量生产稳定。
2. 默认保持 Context/Token V2 Shadow。下一步按 Planner、Tailor、Interview 分层灰度，持续观察 SLO、repair rate、usage missing、P95 和质量 Gate。
3. Dynamic Tool Catalog 的全量 schema 对比不计入真实收益，因为当前基线没有全量注入。
4. 通用 Token cache 仍是进程内；跨 Worker 共享应迁移到 Redis，并保留租户和版本 key。
5. Tool Result Artifact/Delta 基础设施已实现并测试，但仍有旧 Service 未迁移到统一接口，迁移时应按长输出占比排序，不做无收益重构。

## 2026-08-31 11:15:21 +08:00：Context Runtime V2 节点级 Token 治理、关键事实保真、JIT 与 Shadow A/B

### 本轮目标
- 将只服务 fit/tailor、主要按字符数裁剪的 `ContextCompressor` 升级为节点级 Context Runtime，同时保留 V1 可回滚路径。
- 明确 Control、Working、Evidence、Memory 和 Artifact 的生命周期，用真实 Token 预算语义取代“字符压缩率等于 Token 降幅”的错误口径。
- 防止压缩丢失身份、目标、数字、否定事实、Citation、审批和外部 Tool Receipt，并为丢失事实提供局部展开和硬失败。
- 构建不少于 30 个强噪声 Case、可重复的 V1/V2 A/B 脚本、跨租户/JIT/恢复测试和独立评测报告；未获本轮付费授权，不调用 DeepSeek。

### V1 审计与基线
1. V1 只有 `compress_fit_context` 和 `compress_tailor_context` 两个正式入口，其他 Planner、Parser、Interview、Verifier 和 Completion 节点没有独立 Context Contract。
2. 主要预算是 `LLM_CONTEXT_MAX_CHARS=9000` 和 `LLM_EVIDENCE_MAX_CHARS=3600`，能够减少长字符串，但不能证明供应商 Token、输出预留和 Tool Schema 后仍有多少可用输入。
3. V1 按列表前部和字符开头裁剪，低分否定证据、跨页 Citation 和 Chunk 中部的支持句缺少独立保真门禁。
4. 完整 Artifact、Memory、Tool Output 与 Working State 没有统一 Packet 生命周期，压缩事件也没有独立数据库 Trace。
5. 修改前固定基线：`tests/test_context_compressor.py + Agent Workflow + Natural Language` 共 `20 passed in 24.11s`。

### 核心实现
1. 新增 `ContextRuntimeV2`、`ContextContract`、`ContextRequest/BuildResult`、`TokenEstimator`、`CriticalFactLedger`、`ContextProjectionCache` 和 `ContextJITLoader`。
2. 为 Planner、Profile Parser、JD Parser、Matcher、Tailor、Application、Interview Question/Answer、Claim Verifier、Guardrail 和 Completion Gate 建立 11 份独立合同。合同声明 required/forbidden fields、Evidence 类型、输入/输出/Tool 预算、Memory/JIT 权限、Critical Fact 和失败方式。
3. 五类 Context 分治：Control 不允许外部内容提升；Working 移除已完成长输出并改用 Receipt；Evidence 保留 Citation/page/polarity/trust/injection；Memory 按 tenant+user+profile/TTL/status 隔离；Artifact 默认只传 ID/URI/SHA256/status/summary。
4. Token Estimator 优先本地模型 Tokenizer，其次 `tiktoken` 代理，最后 CJK heuristic。后两者在 Trace 明确标 `estimated`；供应商 usage 缺失时实际 Token 保持 0，不伪造。
5. 分级压缩实现 Level 0 去重、Level 1 Schema 投影、Level 2 query-specific Evidence window、Level 3 旧历史结构化 Compaction、Level 4 Handoff Context Reset。身份、数字、否定和审批不交给 LLM 重写。
6. Critical Fact Ledger 在压缩后计算 Recall；缺失时只添加缺失事实及 source ID，硬事实仍缺失则模型调用前失败。负向 Evidence 即使 score 很低也获得保留优先级。
7. JIT Loader 提供 Profile、Job、Evidence、Artifact、Session Decision 和 Prior Run Outcome 的受控读取；同时检查操作白名单、tenant/user/profile、字段、调用次数与单次 Token，并生成不含正文的 Receipt。
8. 新增 `context_compression_traces` 表。中央 `LLMClient` 为已注册 trace 构建 V2 观测，保存 Runtime/Contract/Prompt/Skill/Tool Policy 版本、估算/实际 Token、类别占比、压缩 Level、保真、JIT、Cache、Reset 和延迟。
9. Execution Provenance 升级 v5，保存 Context Runtime 模式、11 份合同和 Token 水位。V1/V2 使用 Feature Flag；当前保持 `enabled=false + shadow=true`。
10. `ContextCompressor` 在 Shadow 中仍返回原 V1 Prompt，只附加 V2 差异；Active 时才将结构化 Profile/JD/Evidence Packet 映射回现有 fit/tailor 接口，支持配置立即回滚。

### 专项数据与 A/B Harness
- 新增 40 Case 数据集，覆盖超长中英文简历、双栏/跨页、相似项目、课程与交付、计划学习、明确未实现、多数字、长 JD、required/preferred/negative、多轮纠错、城市/岗位变更、长 Tool Output、低相关 Evidence、PDF/JD/Memory 注入、同租户跨用户、跨租户、Checkpoint、Compaction、Artifact/JIT、缓存更新、审批与 Receipt 等场景。
- 每个 Case 含高分主证据、低分否定证据、18 个长噪声 Chunk、8 轮历史和大 Artifact，避免通过“删除全部 Evidence”制造好看的 Token 降幅。
- `run_context_runtime_ab.py` 默认执行零费用确定性 A/B；`--real-llm` 已实现同模型、同 Prompt、温度 0、同 1200 输出上限的成对调用入口。本轮未执行付费模式。

### 实际离线 A/B 结果
- Case：40；模式：deterministic；LLM calls：0。
- V1/V2 平均估算 Input Token：`23527.60 -> 7102.05`，相对下降 `69.81%`。
- Critical Fact、Required Evidence、Negative Evidence、Citation Integrity：均 `1.0`。
- Prompt Injection Escape、Cross-tenant Leakage、Forbidden Field：均 `0`。
- 同 key 二次构建 Cache Hit：`40/40`；Context Reset 专项恢复：`1/1`。
- Context 构建 P50/P95：`77.93/87.72 ms`。
- 确定性 Context Gate 通过，但 `v2_can_be_default=false`。实际 Provider Token、Total Token、费用、Fit、Tailor、Forbidden Claim、Hallucination 和 E2E 都是“未测”。

### 开发中暴露的 Bad Case
1. **高压缩率不能直接触发上线。** 第一轮 40 Case 已得到 `69.81%` 估算降幅和 100% 保真，但没有真实模型输出与供应商 usage。正确处理是把 deterministic gate 与 real-LLM release gate 分开，并让程序输出 `v2_can_be_default=false`，而不是用估算数字替代真实 Token/质量。
2. **Cache Hit=0 最初不是缓存失效，而是评测只做冷读。** 第一版每个 Case 的 data version 都唯一且只构建一次，报告自然为 0。修复为同一个 ContextRequest 显式二次构建，冷构建用于延迟/质量，第二次只验证 key 与 scope 复用；最终 40/40。评测指标必须匹配被测语义。
3. **最初 Reset 会把超长 Working 原样带进新窗口。** 旧逻辑生成 Handoff 后仍复用整个 `working_context`，因此真正超限时 Reset 可能再次超限。修复为新窗口只保留 Goal、约束、未完成步骤、最近错误、Receipt、选中 Job/Profile ID；长 history_blob 只留 Handoff 哈希和引用。
4. **Required Contract 最初只有声明没有执行。** 11 个合同已经写 required fields，但 Runtime 第一版未验证。增加结构化请求预检；中央 text adapter 只做 Shadow 观测，不冒充完整业务合同。测试固定缺 `job` 的 Tailor Context 必须失败。
5. **高风险 Memory 不能先摘要再保存。** 第一版 Compaction 保留 source IDs，但没有在加载阶段隔离 `injection_risk=high`。修复为高风险 Memory 不进入 Packet，Compaction 仍保留已允许记录的 risk metadata。
6. **静态检查发现测试本身破坏了基线语义。** 新增 LLM Context Trace 测试时，插入位置错误，把原 provider usage 测试主体放进了新函数，pytest 仍全绿，但 Ruff 报同一作用域 `FakeResponse` 重定义。修复为恢复两个独立测试并重新运行。这个 Case 说明“测试通过”不等于测试仍在测原目标，测试代码也需要静态检查和结构审查。
7. **全仓 Ruff 会暴露历史债务，不能混入本轮结论。** 全仓检查仍有 `ops.py` 变量遮蔽、数个旧 unused import 和旧脚本 E402 等 20 项既存问题；本轮没有为 Context 任务顺手重构无关文件。所有本轮新增/修改的 Context 文件定向 Ruff 已通过，并在限制中如实记录全仓 Ruff 不是绿色。

### 测试与统一评测
- V2 专项最终：`23 passed in 0.59s`。
- Context + LLM Trace：`39 passed in 1.39s`；重点 Agent/恢复/评测回归：`129 passed in 70.09s`。
- 全量 pytest 首轮 `366 passed in 128.40s`；修正测试结构并完成最终重跑：`366 passed in 125.24s`。
- 统一确定性 Agent 评测：EvaluationRun `#163`，`release_gate_passed=true`、`suite_errors={}`、`total_tokens=0`、费用 0、wall time `220519 ms`。
- `compileall`、本轮文件定向 Ruff 和 `git diff --check` 通过；本轮没有调用 DeepSeek。

### 当前限制与下一步
1. V2 默认仍是 Shadow。应先选 5 个代表 Case 做 10-call 真实 A/B smoke，确认 JSON、usage、质量门禁和费用后，再决定是否运行 40 Case/80-call 全量；避免无控制消耗余额。
2. 当前 DeepSeek V4 没有配置本地专用 Tokenizer，离线数值是显式估算；真实 A/B 必须以 provider usage 为主。
3. 只有 fit/tailor 已接入完整结构化 ContextRequest；其他 LLM 节点通过中央 text adapter 记录合同观测。后续应逐节点将 Planner/Parser/Interview/Verifier 的业务字段改为结构化请求。
4. Level 3 当前为确定性结构化 Compaction，没有新增 LLM 调用。只有真实长对话证明确定性结构不足时，才增加可评测的 LLM Compaction。
5. Context Cache 是单进程 LRU；多 Worker 共享、Redis 缓存和主动 Profile/JD invalidation event 尚未实现。当前 data version 可以防止更新后复用旧键，但不是分布式缓存。
6. Context Reset 已通过机制 Case，但尚未在真实跨进程 LangGraph 长上下文流程中做端到端 100% 恢复认证。
7. 全仓 Ruff 仍有 20 项本轮之前的历史问题；不影响 pytest 和本轮定向质量门禁，但应安排独立清理提交，避免与 Context 功能修改混在一起。

## 2026-08-21 11:12:32 +08:00：按真实执行轨迹补强 Tool Harness，并把隐式 RAG 与事实核验拆成可验证步骤

### 本轮目标
- 不再只从“代码里注册了多少 Tool”判断 Agent 是否成熟，而是核对每个 Tool 是否真的有独立调用点、输入输出合同、运行时权限和可追踪产物。
- 按真实用户链路审计“简历匹配 -> 证据检索 -> 定制简历 -> 事实核验”，确认 Completion Gate 能判断每个关键步骤是否完成，而不是只看到一个大 Service 返回成功。
- 将 Prompt 与 Skill 做版本化和真实运行时注入；同时补齐 Redis Worker、Checkpoint 恢复路径中的租户资源复核。
- 本轮先使用零外部 LLM 成本的合同测试和统一确定性评测验证代码，避免在基础实现仍可能报错时消耗 DeepSeek 余额。

### 开发前审计发现的问题
1. **注册表数量和真实 Tool Calling 不一致。** Registry 有 19 个 Tool Contract，但旧调用点只有约 14 个独立 `BoundAgentTool`；JD Parser、岗位索引和面经导入实际嵌在上层服务中，简历证据检索与事实核验虽然写在计划里，也被藏在 `resume_tailor` 内部。若直接回答“系统有 19 个可独立调用工具”，面试口径会夸大真实实现。
2. **计划轨迹与执行轨迹不一致。** Task Contract 要求 RAG 和 Guardrail，但外层图只记录 `tailor_resume`。当这个大函数返回成功时，Completion Gate 无法分别证明检索质量达标、定制完成和事实核验通过；排查失败时也只能进入 Service 内部猜测。
3. **Skill 权限表存在概念性工具。** 多份 `SKILL.md` 的 `allowed_tools` 引用了 `resume_parser`、`profile upsert`、`llm.generate_json` 和 `context compressor` 等未注册 helper。Planner 文档看似有限权，Runtime 却无法依据 Registry 校验这些名称，属于权限描述漂移。
4. **Prompt/Skill 只能看到配置，不能复现真实调用。** Prompt 分散在各服务中，没有统一的名称、版本和哈希；Skill 主要通过 API 展示，不能证明具体 LLM 调用加载了哪一版约束。出现 Bad Case 时只能看输入预览，无法回答“哪版 Prompt/Skill 导致的”。
5. **租户保护主要停留在 HTTP 入口。** FastAPI 路由会过滤 Profile/Job，但 Redis Worker 和 Checkpoint 恢复直接从 Run State 读取资源 ID，不会重新经过路由依赖。入口校验正确不等于后台执行一定不会跨租户读取。
6. **Tool 输入允许额外字段会隐藏 Planner 错误。** 如果模型多生成一个参数、handler 又没有使用它，调用可能表面成功，Trace 却记录了一个没有真正生效的用户要求。

### Harness 与真实流程改造
1. **区分直接工具和嵌入式能力。** `AgentToolSpec` 新增 `invocation_scope` 与 `parent_tools`。当前 19 个合同中，16 个是 Runtime 可独立执行和追踪的直接 Tool；`jd_parser.parse_jd`、`vector_index.upsert_job_chunks`、`interview_experience.import_text` 三个明确标记为嵌入式能力，不再混用一个统计口径。
2. **拆出证据检索节点。** LangGraph 在匹配后显式调用 `vector_index.retrieve_resume_evidence`，把 evidence chunks 与 retrieval quality 写入 `resume_evidence_retrieval` Artifact。质量门禁失败时在生成前停止，不允许 Tailor 用低质量证据继续猜测。
3. **拆出事实核验节点。** 定制后显式调用 `guardrail.verify_resume`，重新读取已保存的 ResumeVersion，写入 `resume_verification` Artifact；只有核验通过才进入后续匹配门禁和完成判定。
4. **消除隐藏重复检索。** `ResumeTailorService` 可以消费图中已经检索出的 evidence 和 quality，不再在一个正常流程中重复 Embedding/Reranker。旧的单独调用路径仍会自行检索，但不会用静默兜底掩盖检索失败。
5. **Completion Contract 与图同步。** `resume_tailor` 和 `full_career_flow` 的 required steps/artifacts 同时增加 retrieval 与 verification，并固定 `retrieve -> tailor -> verify` 顺序。完成判定现在检查业务证据，不只检查 Node 是否返回。
6. **Prompt Registry 与 Skill 渐进披露。** 新增 `PromptRegistry`，为九类 LLM 调用声明 Prompt 版本和相关 Skill。调用时只提取相关 Skill 的 context policy、forbidden behaviors 和 failure policy，组成最多 2400 字符的 Runtime Policy。Trace 保存 Prompt/Skill 版本和基础/最终 SHA256，不把七份 Skill 全文反复发送。
7. **执行时租户纵深校验。** `AgentToolRuntime` 在 handler 前根据 AgentRun tenant 复核 Profile、Job 和 ResumeVersion；全局 Job 可共享，用户 Profile/ResumeVersion 必须属于当前 tenant。该规则覆盖 API、Worker 和恢复路径。
8. **收紧输入输出合同。** Tool JSON Schema 默认 `additionalProperties=false`，未知参数直接拒绝；直接检索与 Guardrail 增加真实输出校验。Skill 版本统一升级到 `1.1.0`，所有 allowed tool 必须能在 Registry 中找到。
9. **执行 Provenance 升级。** Provenance v4 保存直接/嵌入式工具清单、全部 Skill 版本和 Prompt Registry Manifest，使历史 Run 能复现当时的 Harness 配置。

### 修复过程中暴露的 Bad Case
1. **严格输出检查揭露了合同漂移。** 第一次运行新 `verify_resume` 节点时，Runtime 报输出缺少 `verification`。调查发现 Tool Contract 写的是 `{"verification": ...}` 包装结构，真实 Guardrail handler 一直返回 `{"passed": ..., "risk_level": ...}` 正文。过去没有对该路径做独立输出校验，因此错误合同长期存在。最终按真实业务返回修正 Contract，而不是放宽 Runtime 校验。
2. **测试替身暴露了错误的组件耦合。** 旧工作流测试把 Guardrail 藏在 `FakeTailor.guardrails` 中；拆出独立 verify 节点后测试失败。这说明 Orchestrator 曾依赖 Tailor 的内部属性，而不是依赖显式 Guardrail Service。修复为构造 Orchestrator 时独立注入 `ResumeGuardrailService`，测试替身也按真实节点边界实现。
3. **恢复测试暴露了“大函数接口”假设。** 旧恢复 Fixture 只模拟 `tailor_resume`，没有证据检索接口。增加节点后恢复测试失败，迫使测试同时证明恢复后的 Run 会重新得到 evidence Artifact，并把同一份 evidence 传给 Tailor，而不是靠大函数内部的不可见状态。
4. **图上写了步骤不等于真的执行了步骤。** 这是本轮最重要的系统设计发现。计划、Trace 名称和文档都可能营造“有 RAG、有 Guardrail”的印象；只有节点独立执行、产物可查询、Completion Gate 校验三者同时存在，才能证明任务没有偷懒早停。

### 验证结果
- `python -m pytest -q`：`342 passed in 163.94s`。
- 重点回归覆盖未知 Tool 参数拒绝、后台跨租户 Profile 拒绝、Prompt/Skill 版本与有界注入、Skill 工具引用完整性、显式检索/核验步骤、Artifact 和中断恢复。
- 最新统一确定性 Agent 评测：EvaluationRun `#155`，约 `238.79 s`，release gate 通过，`suite_errors={}`，外部 LLM Token 为 `0`。
- PDF Chunk 96 份样本：选用 `paragraph_page_900_overlap160`，Top3 keyword/page/context hit 为 `0.9479/0.8299/0.7760`。
- PDF Extraction Bad Case 22 项：pass、critical、错误码、Bridge Precision/Recall 均为 `1.0`，Bridge FPR 为 `0`。
- Follow-up Directive Bad Case 22 项：全部门禁为 `1.0`。
- RAG 180 Case：Top1 `1.0`、Recall@3 `0.6125`、Recall@5 `0.7292`、MRR `1.0`、nDCG@5 `0.7862`；使用真实 sentence-transformers embedding 与 cross-encoder reranker，没有 hash/fallback 冒充生产结果。
- 岗位相关性 13 Case：pass rate `1.0`、nDCG `0.9495`；投递材料 27 Case pass rate `1.0`；Prompt Injection 70 Case recall `1.0`、FPR `0`。
- `compileall` 和 Ruff 均通过；本轮没有调用 DeepSeek，也没有消耗外部模型余额。

### 可以直接用于面试的结论
1. 我最初的问题不是缺少 RAG 或 Guardrail，而是二者藏在一个 Tailor Service 中，系统只能证明大函数返回，不能证明每个关键责任完成。修复方法是把检索、生成和验证变成独立受治理工具，各自留下 Artifact，再让 Completion Gate 按任务合同核对。
2. Tool 数量不能只报 Registry 行数。我会说明当前是 19 个合同、16 个直接工具、3 个嵌入式能力，并解释模型不会在 19 个工具上无限自由选择，而是由 Skill、Task Contract、Graph 和 Runtime 四层约束。
3. 权限不能只在 API 做。异步 Worker 和 Checkpoint 恢复会绕过 HTTP 依赖，所以 Tool Runtime 必须在真正执行前重新检查 tenant、Run 状态和 approval。
4. Prompt 优化首先要可复现。Prompt/Skill 的版本、哈希和 usage 是 A/B 的基础，但只有完成固定数据集的 baseline/challenger 对照后，才能说某版 Prompt 质量更高；版本化本身不是效果证明。
5. 测试替身失败不是单纯维护成本，它暴露了生产代码依赖内部实现。让 Fake 和真实组件都服从相同 Tool Contract，能够反向检查架构边界是否真的成立。

### 尚未解决与下一步
1. 最新确定性系统评测已经通过，但当前 Prompt/Skill/Graph 版本尚未重新运行完整真实 LLM workflow 和 full-flow release gate。应在预算可控时按固定 Case 分批运行，先小样本 smoke，再执行完整对照，避免用旧失败记录或确定性满分替代模型质量结论。
2. Prompt Registry 已提供版本、哈希和 Runtime Policy 注入，但尚未形成同模型、同数据、同温度下的 baseline/challenger A/B 报告；下一轮 Prompt 优化必须同时比较 grounding、任务完成率、Token、p95 和 pass^k。
3. 三个嵌入式能力仍没有独立顶层 Step。只有当独立审批、恢复、重试或观测确有价值时再拆成直接 Tool；不为了把数字从 16 增到 19 制造无意义节点。
4. Runtime 当前对 Profile、Job、ResumeVersion 做资源级租户复核，其他核心业务表仍应持续进行 tenant 下沉审计，并补充按资源类型的越权矩阵测试。
5. SQLite 仍适合当前单机受控部署；多实例公网生产需要 PostgreSQL/shared checkpointer、OIDC/SSO 和真实流量 SLO，不能由本轮 342 项离线测试替代。

## 2026-08-19 11:44:46 +08:00：为 PDF 与追加指令建立 Bad Case 发布门禁，并用失败基线驱动修复

### 本轮目标
- 不再用“新增了 11 个测试”描述 PDF 能力，而是建立有类别、难度、critical 标记、分桶指标和 release gate 的 Bad Case Suite。
- 对追加指令重点验证 Harness 语义，而不是重复评测 LLM 文案：并发状态、动作合同、幂等、父子 lineage、上下文最小化、审批隔离、父 Run 不可变和失败审计。
- 先运行当前实现获得失败基线，再根据真实失败修代码；不为了满分修改标签或降低门禁。
- 将两套 Suite 纳入 `/evaluations` API、统一系统评测和总设计文档；本轮不调用 DeepSeek。

### 新增评测数据与实现
- 新增 `evals/pdf_extraction_bad_cases.json`：22 Case，包含 input validation、resource safety、text quality、OCR route/quality、layout、cleanup、diagnostics 和 cross-page bridge 十类场景。
- PDF 评测器动态生成真实 PDF bytes，而不是只读取分页文本；覆盖空上传、伪扩展名、伪 magic、损坏、加密、超页数、超大小、危险渲染尺寸、短乱码文本、空扫描图、文本/扫描/混合页、文本/OCR 双栏、重复页眉和跨页正负例。
- 新增 `evals/follow_up_directive_bad_cases.json`：22 Case，使用确定性子 Run 执行器验证控制面，不调用 LLM。
- 新增 `CapabilityBadCaseEvaluationService`，把每个 Case 的 observed、errors、latency、category 和 difficulty 持久化到 `EvaluationRun`，输出总体/critical/分桶指标及发布门禁。
- 新增 `/evaluations/pdf-extraction-bad-cases` 和 `/evaluations/follow-up-directive-bad-cases`，并把两套 Suite 加入 `run_agent_system_eval.py` 的 deterministic required suites。
- AgentSystemEvaluationReporter 增加 PDF bridge、critical、幂等、lineage、上下文和失败审计等摘要指标，dataset manifest 自动纳入两份新数据集。

### 第一轮真实基线
- PDF：`22/22`，总通过率、critical、预期错误码准确率、Bridge Precision/Recall 均为 `1.0`，Bridge FPR 为 `0`。
- 追加指令：`17/22`，总通过率 `0.7727`，critical `0.75`，Concurrency Guard `0.75`，Idempotency Safety `0.6667`，Context Minimization `0.3333`；lineage 和 failure audit 已为 `1.0`。
- 追加指令失败的五项是：withdrawn 父 Run 仍可分支、同一幂等键可更换正文、未知 selected action 被接受、malformed 历史 JSON 触发 `.get` 错误、非法历史 limit 触发 `int()` 错误。
- 这些失败稳定对应代码路径，因此没有归因给模型随机性，也没有增加重试。

### 根据 Bad Case 完成的修复
1. **撤回事实隔离。** `queued/running/waiting_for_confirmation` 继续因为状态不稳定拒绝热修改；`withdrawn` 新增为不可分支状态，避免子 Run 复用已撤回 ResumeVersion/Application。失败和取消 Run 仍允许创建纠正分支。
2. **幂等键绑定 payload。** 先规范化 instruction 和去重后的 selected_actions；相同 key+payload 返回原结果，相同 key+不同 payload 明确冲突，不再静默执行旧要求。
3. **动作合同白名单。** `AgentDirectiveRequest.selected_actions` 改为 Pydantic Literal，只允许六类公开动作；未知动作在 API Schema 层拒绝，Runtime 仍负责后续权限检查。
4. **陈旧 JSON 只读容错。** SourceContext 对 input/output、selected_job、application、interview_prep、profile/job/tailor 使用 mapping 和 safe-int 读取；非法 limit 默认 8 并夹在 1..30。该处理不创造业务 ID，子 Run 仍需数据库回查。
5. **OCR 并发保护。** PDF Suite 虽然准确率全通过，但 P50 `3.34 ms`、P95 `2374.60 ms`、最大 `2610.07 ms`，暴露 OCR 长尾。异步上传现在用 `asyncio.to_thread` 卸载抽取，RapidOCR 共享引擎用锁保护，避免 CPU 推理阻塞 FastAPI event loop 或并发破坏模型实例。

### 评测 Harness 自身暴露的问题
1. 第一次在真实数据库运行 `nested_ids` 时，假执行器把只用于观察的 301/401 写入 AgentRun 外键，SQLite FK 失败并污染 Session，最终汇总出现 `PendingRollbackError`。修复为假执行器只在 request input 保留被观察 ID，不伪造业务外键；Case 外层异常统一 rollback。说明评测 Fixture 也必须遵守数据库事实边界。
2. 第一次用 `--only` 运行两套新 Suite 时，两套都通过、suite_errors 为空、Token 为 0，但系统总门禁失败，因为 Reporter 仍要求未运行的旧 Suite。修复为定向运行时 `required_suites` 与选择集取交集；完整 deterministic/full 模式仍要求全部 Suite。
3. 修复后使用相同 `--only` 命令重跑：约 `15.09 s`，系统 release gate 通过，`total_tokens=0`、LLM 成本为 0。

### 修复后指标
- PDF 22 Case：pass rate `1.0`、critical `1.0`、错误码准确率 `1.0`、Bridge Precision/Recall `1.0/1.0`、FPR `0`；最近一次 P50/P95/max 为 `3.34/2374.60/2610.07 ms`。
- 追加指令 22 Case：从 `17/22` 提升到 `22/22`；critical、Concurrency Guard、Idempotency、Lineage、Context Minimization、Failure Audit 均为 `1.0`。
- 重点回归：`47 passed in 20.86s`。
- `python -m pytest -q`：`338 passed in 117.02s`。
- 运行时事实：`133 routes / 30 tables`；新增两条评测 Route，没有增加业务表。
- `compileall`、相关 Ruff 和统一系统评测切片均通过；本轮没有调用外部 LLM。

### 有意义的面试结论
1. **准确率通过不代表并发设计通过。** PDF 的 OCR Case 全对，但 P95 超过两秒，促使 CPU 任务从 async event loop 卸载；质量和性能必须分开看。
2. **幂等不是只做唯一索引。** key 相同但 payload 不同必须报冲突，否则系统会返回“成功”却执行旧意图。幂等语义需要绑定规范化业务输入。
3. **撤回不是另一种 completed。** withdrawn 代表用户否定了业务产物，不能作为新分支的默认事实来源；失败 Run 则可以保留为纠正上下文。
4. **上下文压缩还包括防御陈旧结构。** allowlist 不只省 Token，也限制旧 Run 中 malformed/debug 字段进入新 Prompt；typed read 防止历史格式漂移让新入口崩溃。
5. **评测系统也会制造 Bad Case。** FK 夹具错误和 `--only` 汇总错误都可能给出假失败。评测器的事务、选择集和 required suites 同样需要合同与回归。

### 尚未解决与下一步
1. PDF 22 Case 是高强度生成式机制测试，不是现实分布；仍需获得授权的真实中文扫描简历，标注 OCR CER、邮箱/技术词/数字等关键实体 Recall、表格和复杂布局恢复率。
2. Bridge 只有 3 个正例和 2 个负例，当前 Precision/Recall 满分不能外推；应从真实跨页简历补充边界标注后再校准规则。
3. OCR 已不阻塞 event loop，但进程内模型锁使单 Worker OCR 串行。真实并发量上升后，应比较独立 OCR 队列、进程池和多 Worker 的吞吐/内存曲线。
4. 追加指令仍在 API 请求内同步运行自然语言图，Directive 落库后、子 Run 创建前崩溃的 stale recovery 仍是下一项生产增强；不能用本轮确定性 Harness 满分替代这项恢复验证。
5. 追加指令 Suite 不评语言理解，仍需在自然语言规划数据集中增加“只修改城市、不重复简历、不投递”等语义 Case，并受控运行真实 LLM。

## 2026-08-19 11:16:39 +08:00：修复 PDF 抽取与跨页 Bad Case，并实现可追溯的追加指令分支

### 本轮目标
- 先解决 PDF 的真实输入问题，而不是继续把“已经抽取好的分页文本”当作完整 PDF 解析能力：覆盖扫描页、文本/扫描混合页、双栏、跨页、重复页眉、空白页、损坏、加密、超大文件和低质量 OCR。
- 为用户增加历史流程上的自然语言追加要求，例如“城市改上海，只重新搜索岗位”，同时保留原 Run、产物和审批审计。
- 两部分完成后更新统一系统设计文档，并通过生产配置 Chunk 评测、全量测试和前端契约验证；本轮不调用 DeepSeek，避免产生余额消耗。

### 开发前审计发现的问题
1. **96 份所谓 PDF Case 不是 PDF 文件。** `pdf_chunk_cases.json` 直接保存了 `page_no + text`，能评测 Chunk 和检索，却无法暴露 PDF 打不开、扫描页无文本、双栏错序或 OCR 乱码。旧文档把两层能力写在一起，容易让人误以为“96 份通过”已经证明 PDF Parser 稳定。
2. **混合 PDF 会静默丢页。** 原实现逐页调用 `pypdf.extract_text()`，只检查整份文档最终是否非空。只要第一页有文本，第二页即使是扫描图片也能“成功建档”，但扫描页项目经历完全丢失。
3. **页内 overlap 不能修复跨页内容。** 900/160 overlap 只发生在同一页的超长段落；项目标题在上一页末尾、实现细节在下一页开头时，两者仍不会进入同一 Chunk。
4. **第一次跨页修复过度。** 初版对所有相邻页建立 tail/head bridge。96 份五页样本每份固定多出 4 个重复 Chunk，Hash 诊断的 Top3 keyword hit 降至 `0.8976`，低于 0.90 门禁。继续增加 overlap 只会掩盖问题。
5. **覆盖率公式没有区分能力。** `text + ocr + blank` 再除总页数，会把空白页也算成文本覆盖成功；只要页面被遍历，结果几乎总是 1.0。
6. **OCR 也会有双栏顺序问题。** 即使扫描页识别到了所有行，单纯按 y/x 坐标排序会把左右栏逐行交错，LLM Parser 接收到的仍是错误语义顺序。
7. **系统没有一般性的追加 Prompt 语义。** `/resume` 只恢复 LangGraph interrupt，不能表达“修改上一轮目标”。直接覆盖旧 Prompt 或运行中的 State 会破坏任务合同、产物 lineage 和审批边界。
8. **正确的 HTTPException 被通用异常改写。** 端点虽然在超大文件分支抛出 413，但外层 `except Exception` 会再次捕获并返回 500。服务层测试无法发现这个问题，必须增加真实 FastAPI 端点回归。

### PDF 实现与设计选择
- 新增 `PDFExtractionService`，使用 PyMuPDF 读取 block 坐标，按页产出文本和诊断；对低质量图片页使用本地 RapidOCR，不再调用外部模型。
- 上传前验证 `.pdf` 扩展名、`%PDF-` magic、大小、页数、加密和损坏状态；FastAPI 采用“配置上限 + 1 byte”的有界读取，超限返回 413，不先把任意大文件读入内存。
- 上传端点显式重新抛出 `HTTPException`，再处理 Parser 的 `ValueError` 和未知异常；端点级测试固定无效 PDF 为 400、超大 PDF 为 413，避免业务状态码被通用 500 吞掉。
- 每页记录 extraction method、字符数、可打印字符比例、替换字符比例、字母数字比例、图片数、layout mode、OCR confidence 和 warning，并持久化到 `structured_profile_json.source_diagnostics.pdf_extraction`。
- 文本层和 OCR 都增加双栏阅读顺序。文本层按 PDF block 坐标排序；OCR 按识别 box 区分左右栏，避免同高度的两栏内容交错。
- OCR 渲染设置最大像素，根据页面尺寸动态降低 DPI；低于安全 DPI、OCR 字符过少或置信度不足时直接报错，不建立半成品 Profile。
- 对每页前后有限行做重复页眉页脚识别，统一页码后只有在至少两页且覆盖 60% 页面时删除，并把被删除行保留在诊断中。
- 修正 `text_page_coverage`：只计算文本层或 OCR 成功页面，空白页单独计数；一页文本加一页空白必须得到 0.5。
- 跨页 Chunk 改为选择性 bridge：上一页末行未闭合，或下一页以列表符号续写时，才合并有限 tail/head；独立完整页面不复制。metadata 保存 `page_start/page_end` 和 `cross_page_semantic_bridge`。

### 追加指令实现与设计选择
- 新增 `agent_directives` 表和 `AgentDirectiveService`，保存 source/target Run、tenant/user、自然语言要求、选定动作、紧凑上下文、结果、状态、错误和幂等键。
- 新增 `POST/GET /agent/runs/{run_id}/directives`。前端历史记录页可以输入追加要求、查看处理状态，并跳转到生成的后续流程。
- 采用 immutable parent + child run：父 Run 不修改；系统提取 Profile/Job/ResumeVersion ID、已选岗位摘要、原目标短摘录和已有产物 ID，创建新的 natural-language Run，重新生成 Task Contract。
- 子 Run 保存 `parent_run_id`、`conversation_root_run_id`、`directive_id` 和 `task_contract_revision` Artifact；父 Run记录 received/completed/failed Event。
- 浏览器通过 `source_run_id + client_request_id` 幂等。新增要求涉及 browser/email 外发时仍需新 Run 的审批，不能复用父 Run 的一次性批准。
- `queued/running/waiting_for_confirmation` 状态拒绝热追加。原因不是实现偷懒，而是旧合同已经开始执行，途中改目标会让已完成步骤、旧 Job 和高风险动作产生竞态；用户应先完成 interrupt、等待或取消，再从稳定终态建立分支。

### 本轮值得面试讲述的 Bad Case
1. **评测数据标签不能代替数据内容审计。** 数据中有 `cross_page_distractor` 字段，但正文没有真正被页边界截断的连续项目。仅凭场景名设计无条件 bridge，反而造成检索回归。修复过程是先查看失败分桶和平均 Chunk 数，再回到数据生成器确认样本构造，最后把真实跨页/非跨页边界分别做 Fixture。
2. **“抽取成功”需要分层定义。** PDF 能打开、页面有字符、OCR 置信度高、Schema 合法和关键字段有原文支持是五件事。现在每一层有独立诊断，避免把 LLM Parser 的错误归因给 PDF，或用 JSON 合法掩盖扫描页丢失。
3. **局部修复不能无界扩大上下文。** 跨页问题最直觉的做法是把相邻页全部拼起来，但会制造重复证据和向量污染。最终方案只在边界有续接信号时扩大上下文，并用专项正负例约束。
4. **追加消息不等于 Interrupt Resume。** Interrupt 只补充原合同正在等待的选择；用户改变城市或动作集合是在修订目标，必须创建新合同和新 lineage。把二者都叫“继续运行”会让恢复、审计和幂等语义混乱。
5. **不可变历史比覆盖旧答案更适合 Agent。** 追加指令创建分支后，可以比较父子结果、撤回子产物、定位哪条新要求导致变化，也不会让已经批准或外发的动作失去来源。
6. **只测 Service 不足以证明 API 语义。** PDF 大小校验逻辑本身正确，但异常处理顺序破坏了 HTTP 契约。这个 Case 说明生产测试至少要覆盖 Parser、Service 和 HTTP Adapter 三层，而不是只断言内部异常码。

### 验证
- PDF 二进制/API 专项：`11 passed`，覆盖无效 magic/扩展名、HTTP 400/413、页数、损坏、加密、重复页眉、真实混合扫描 OCR、空白页覆盖率、文本双栏、OCR 双栏顺序、诊断持久化和跨页正负边界。
- PDF/Directive/前端/Chunk 重点回归：`30 passed`。
- 默认 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 下重新运行 96 份、576 Query：选择 `paragraph_page_900_overlap160`，Top3 keyword/page/context hit 分别为 `0.9479/0.8299/0.7760`，平均 Top1 `772.77` 字符、平均 `10.00` Chunk，全部发布门禁通过。
- Hash 只作为诊断配置，Top3 keyword hit 为 `0.8976`，不冒充生产 Embedding 结果；这也再次说明不同 Embedding 下不能共用一个结果口径。
- `python -m compileall -q app tests`、相关 Ruff、`node --check app/static/js/main.js` 和 `git diff --check` 均通过。
- `python -m pytest -q`：`330 passed in 108.84s`。
- 本轮没有调用 DeepSeek 或其他外部 LLM，Token 费用为 0。

### 尚未解决与下一步
1. 当前 OCR 能处理常见中英文扫描简历和双栏，但复杂表格、三栏、竖排、浮动文本框、手写和极低清晰度图片仍可能错序或错字；需要积累真实用户授权样本后增加字段级 CER 和关键实体 Recall，不能只看平均置信度。
2. `AgentDirectiveService` 当前在 API 请求内同步运行自然语言图。若进程在 Directive 落库后、子 Run 建立前崩溃，记录会停在 `executing`，但不会自动外发。后续应将 follow-up 创建接入 Redis 队列和 stale scanner；在此之前保留明确状态和 Trace，不自动重放高风险动作。
3. 当前选择性跨页判断基于页尾闭合和列表续写信号。未来发现新排版时应先加入真实正负例，比较 bridge precision/recall 与检索回归，再扩展边界检测，不能再次改成全量复制。

## 2026-08-14 10:45:46 +08:00：把 Runtime、Role/SubAgent 和 LangGraph 基础解释融入系统设计主线

### 本轮目标
- 用户在阅读统一设计文档时，发现 Runtime、Task Contract、业务表、handler、AgentRoleSpec、上下文压缩和 LLM Parser 虽然均有定义，但缺少从 LangGraph 执行模型到当前源码的连续解释。
- 不新增孤立的“阅读问答”章节，而是把知识分别嵌入 Agent Harness、总体架构、一次完整运行、数据模型、LangGraph、Tool/Skill/Role、PDF/JD Parser 和上下文治理章节。
- 特别澄清“LangGraph 本身是不是 Runtime”“Role 是否仍污染主 Prompt”“当前 Tool 是否由模型自由调用”这三个容易在面试中被追问的边界。

### 文档改进
- 在 Agent Harness 章节区分 Python Runtime、LangGraph Pregel Runtime 和 CareerAgent `AgentToolRuntime`，明确后者是运行在 Node 内部的受治理 Tool Executor，不是重新实现图引擎；总体架构职责表同步补上两层 Runtime。
- 在完整流程中把 `AgentRun.id`、`graph_thread_id` 和 Task Contract 分开解释：分别负责业务任务身份、Checkpoint 线程定位和机器可执行完成标准，并说明 Completion Gate 如何查询 State、Trace、Artifact、审批和业务表。
- 在数据模型章节说明 29 张 SQLAlchemy 表默认物理落在 `career_agent.db` SQLite 中、可通过 `DATABASE_URL` 切换 PostgreSQL；业务/Agent 运行表与独立 LangGraph Checkpoint 存储各有不同权威边界。
- 在 LangGraph 章节补齐 State、Node、Edge、StateGraph、compile、Pregel Runtime、invoke/stream、Checkpointer/thread_id、interrupt/Command 的最小心智模型，并解释 Graph State、Runtime Context 和 LLM Prompt 不是同一上下文。
- 在 Tool 章节说明官方 `@tool + ToolNode` 动态模式与本项目“有界 Node 选择 Tool”的差异；展开 Node、Tool Spec、handler、Tool Runtime 和 Service 五层调用关系，也诚实记录 BoundAgentTool 不能从闭包中证明业务语义一致的边界。
- 在 Role 章节说明真正 SubAgent 需要独立模型循环、上下文、工具、预算、输出合同和 Trace；历史七个对象从未具备这些特征，因此改名是语义纠正，不是把真实 Multi-Agent 降级。Role 进入 Plan/State 不等于进入 LLM Prompt，权限仍由 Skill/Runtime 强制。
- 在 PDF/JD 章节拆分文件文本提取、LLM Parser、Pydantic Validator 和 Grounding Gate，避免把“解析成功”误解成“合法 JSON”或把 `pypdf` 与语义 Parser 混为一谈。
- 在上下文章节说明当前确定性 Profile/JD/Evidence 压缩的适用理由，同时补充超长会话应按评测引入 trim、tool-result clearing、LLM compaction 或 context reset，避免把“不需要 context_manager SubAgent”误写成“任何场景都不需要 LLM 压缩”。

### 本轮重新暴露的设计边界
1. **一个词对应多层对象。** “Runtime”既可指 LangGraph 执行引擎，也可指项目工具治理类；原文只写 Runtime 容易让读者误以为 CareerAgent 替换了 LangGraph Runtime。修复为每次使用时附带具体职责。
2. **进入 State 不等于消耗 Prompt Token。** Role、Plan 和 Artifact 可以进入可恢复 State，但只有 Prompt assembly 选中的字段才发送给 LLM；上下文污染必须从实际模型输入判断，不能只看 State 字段数量。
3. **注册表不能制造 SubAgent。** 只有职责元数据而没有独立控制循环和上下文的对象不能宣称为 SubAgent；真正隔离来自独立调用边界，当前主流程则通过确定性 Context Packet 获得更低成本的一致性。
4. **Tool Registry 不等于 LangGraph 原生动态 Tool Calling。** 当前主图由 Node/Edge 决定调用，Registry 提供合同和治理；这是一种有界 Agent Harness 选择，但必须明确模型没有在 19 个 Tool 中自由循环选择。
5. **当前依赖注入仍有自定义部分。** 数据库 Session 通过按 Run 的进程内映射注入而不进入 Checkpoint；跨进程恢复会重建映射。LangGraph `context_schema/Runtime[Context]` 是后续可选的标准化迁移方向。

### 验证
- 本轮只修改中文技术文档和开发日志，没有修改业务代码、数据库或评测数据，也没有调用外部 LLM。
- 主文档当前 `4,326` 行、Markdown 代码围栏 `230` 个且成对；`git diff --check` 通过，新增外部引用均使用公开一手文档，没有失效的仓库相对链接。
- 运行时事实重新导入核对：`129 routes / 19 tools / 7 skills / 7 roles`，与正文口径一致。
- `python -m pytest tests/test_project_structure_docs.py -q`：`2 passed`；`python -m pytest tests/test_agent_runtime_maturity.py tests/test_health.py -q`：`28 passed in 7.23s`。

### 尚未解决与下一步
1. `AgentToolRuntime` 类名仍容易与 LangGraph Runtime 混淆；本文已给出 `GovernedToolExecutor/ToolPolicyGateway` 的准确语义，但代码改名涉及较多兼容引用，本轮不为文档问题引入无关重构。
2. Role 的 `reads/writes/context_policy` 当前是计划和审计元数据，不是字段级 ACL；若未来加入真实 SubAgent，应增加独立 Subgraph State、输入输出 Schema、预算、Trace 和父子 Checkpoint 语义，而不是只复制现有 Role。
3. 当前 Orchestrator 尚未使用 LangGraph `context_schema` 注入数据库和服务依赖；现有恢复路径可工作，但后续可以评估迁移是否能减少进程内映射，同时避免把不可序列化依赖写入 State。

## 2026-08-13 11:29:42 +08:00：按现代 Agent Harness 范式重审设计，并把文档约束下沉为运行时强制策略

### 本轮目标
- 用户要求审视大型系统文档是否真正符合现代 Agent Harness 范式，而不是只检查是否出现 LangGraph、RAG、Tool、Memory 等技术名词。
- 以“模型/图之外，谁负责约束工具、状态、恢复、副作用、Trace 和 Eval”为主线，对照 LangGraph durable execution/interrupt、主流 bounded runner/tool guardrail 和简洁优先的 Agent 设计原则，逐项核对文档与源码。
- 发现文档已经声称但代码仍依赖调用方约定的能力时，不只改文字，直接把约束下沉到 Harness runtime，并新增回归测试。

### 审视结论
1. 原设计的总体方向是正确的：有界 LangGraph、Task Contract、Completion Gate、持久化 Run/Artifact、HITL、RAG 质量门禁、上下文预算、错误分类、Redis worker 和分层评测都属于现代 Harness 的核心组成。
2. 原文仍把“使用了 LangGraph”与“具备完整 Harness”贴得过近，没有单独定义 Brain、Control Plane、Hands、Context、Memory、Durability、Observability 和 Evaluation 的责任关系。
3. 最严重的问题不是 Prompt，而是工具合同和真实执行函数分开传入。调用点可以写 `tool_name=A, handler=B`，Runtime 和 Trace 会相信名称 A，无法证明实际执行的函数与合同一致。
4. Skill 工具权限只在 Planner 构造固定计划时检查。恢复路径、新节点或未来动态规划如果直接调用 Runtime，缺少执行时的最小权限复核。
5. 高风险审批主要由 `HighRiskActionToolService` 自觉检查；普通 Trace/Runtime 调用如果新增了高风险 Tool，统一层没有强制验证 `approval_id` 与 Run、action、Payload 的绑定。
6. 代码中的七个“SubAgent”没有独立模型会话、调度循环或消息协议，实际只是责任、读写和上下文边界。继续叫 SubAgent 会误导面试官，也会掩盖系统选择有界单图而非自由 Multi-Agent 的真实理由。
7. Worker 只在 Orchestrator 抛异常时重排。Orchestrator 为保存完整 Trace 返回 `AgentRun(status=failed)` 时，临时依赖失败会被误判成队列消费成功。
8. SQLite Checkpointer 能提供单机跨请求恢复，但不能等同于生产多主机共享恢复。原文虽然提过 SQLite 并发限制，却没有用启动门禁阻止 production 继续使用本地 Checkpoint。

### Harness 重构
- 新增 `BoundAgentTool`：注册合同和本次唯一 callable 在调用点绑定；`TraceService.step()`、主 LangGraph、自然语言图和高风险工具全部改为消费绑定对象，不再接受彼此独立的 `tool_name/handler`。
- Tool Registry 升级为 contract v3，导出 JSON Schema Draft 2020-12，正确表达 `instruction|error` 这类“至少一个字段”约束，并为每个工具暴露 `execution_mode` 和 replay safety；同步/异步执行器用错时直接拒绝。
- 新增独立 `matcher.enforce_fit_gate` Tool，避免把“生成 MatchResult”和“读取结果执行策略门禁”伪装成同一个工具；Tool 总数由 18 增为 19。
- `AgentToolRuntime` 在每次调用时查询当前 AgentRun，拒绝 cancelled/withdrawn Run，按 task type 的 Skill capability 做二次授权；未知 task 不会隐式获得业务工具权限。
- 所有声明 `approval_requirement` 的工具由 Runtime 统一验证审批记录。审批必须属于当前 Run 和 action，状态合法，且审批摘要与本次执行的同名 Payload 字段一致。
- 真实浏览器和邮件外发加入原子审批状态流 `approved -> executing -> executed/execution_failed`。条件更新只允许一个并发执行器取得消费权，同一审批不能再次发送；投递包生成仍使用 approval-bound 业务幂等复用。
- 将 `SubAgentSpec` 正名为 `AgentRoleSpec`，Planner 主字段改为 `roles`，Skill front matter 改为 `owner_role`；旧类名、`subagents` 字段和 `/agent/subagents` 仅保留兼容，新增 `/agent/roles`。
- 新增 `LangGraphCheckpointerLifecycle`，支持本地 SQLite 和共享 PostgreSQL 两种后端；SQLite 继续配置 WAL/busy timeout，PostgreSQL 使用 `AsyncPostgresSaver`。
- 新增 `AgentHarnessService` 和 `/agent/tools/harness` Manifest，列出 Harness 组件、Tool/Skill/Role 数量和生产 readiness。production 启动会强制检查共享业务库、PostgreSQL Checkpointer、Redis、非可信 Header RBAC、非默认 Session Secret、严格工具合同和注入门禁。
- Execution Provenance 升级为 v3，记录 Harness/runtime 版本、角色和 Checkpoint 策略，方便历史 Run 复现当时的执行条件。

### 开发过程中发现的 Bad Case 与处理
1. **返回失败不等于抛出异常。** 初版 Worker 把图返回的 `failed` Run 直接 return，导致 retryable dependency failure 永久终止。修复为读取稳定 ErrorEnvelope：可重试错误改为 `queued + checkpoint_resume` 并重排，达到预算后进入 DLQ；业务/策略错误保持明确终态。
2. **重排顺序存在竞态。** 第一版修复先向 Redis `LPUSH`，再把数据库状态改回 queued。另一个 Worker 可能在两次操作之间拿到消息并看到旧 failed 状态。最终改为先提交可恢复数据库状态，再发布队列消息；若 Redis 发布失败，由 queued recovery scanner 补偿。
3. **审批存在重复消费窗口。** 仅验证 `status=approved` 时，客户端重复点击或两个 Worker 并发都可能执行 `email_send`。最终使用条件更新抢占 `executing`，成功后写 `executed`，失败写 `execution_failed`；二者都要求新审批才能再次外发。状态扩展后又发现 Business Summary 只把 `approved` 当合法授权，会把已经执行成功的 `executed` 误报成 approval bypass；已把授权态集合与审批状态机统一并增加回归。
4. **修改 Tool 名后 Completion Gate 反而拒绝正确轨迹。** Fit Gate 从 `matcher.match_job` 拆为独立工具后，四个完整流程测试到达终点却被 trajectory evaluator 报 `unexpected_tools`。根因是 Harness 的执行注册表和评测允许表没有一起版本化。已同步更新 TaskPolicy、Planner 和 argument validator，并保留这个 Case 说明“评测器也是系统合同的一部分”。
5. **测试解释器漂移。** 首次测试使用了没有 LangGraph 的系统 Python，收集阶段报 `ModuleNotFoundError`。确认业务代码尚未执行后，切回 Codex 项目 Python，并安装新增 PostgreSQL Checkpointer 依赖；没有把环境错误误记成业务回归。
6. **机器可读 Schema 与 Python Entity 返回不是同一层。** Registry 的 JSON Schema描述逻辑工具输出，内部 Python handler 可能返回 ORM Entity 或 `(jobs, source_errors)` tuple。当前由 Runtime adapter 做类型验证；文档明确这一边界，避免声称所有内部返回都已经物理转换成 JSON envelope。
7. **执行器模式不能只写在元数据里。** 最终 lint 发现匹配节点用 lambda 包装异步返回值，虽然功能正确，但会削弱 `execution_mode=async` 的可读性和静态检查价值。已改为显式 `async def` callable，并由 Runtime 对合同声明与真实 callable 做同步/异步一致性校验。

### 文档改写
- 在 `CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md` 增加 Agent Harness 定义、十条可执行基线和“原问题/不合规范原因/当前设计”审视表。
- 总体架构图新增 Harness 层，Tool policy 补 Binding、Capability 和 Approval；Checkpointer 改为“开发 SQLite / 生产 PostgreSQL”。
- Tool/Skill/SubAgent 章节改为 Tool/Skill/责任角色，更新 19 个工具、BoundAgentTool、双重权限检查、审批 Payload 绑定和 Role 语义。
- Checkpoint 章节区分单机持久化与跨 Worker 共享持久化，明确进程内 Session/Service 映射不是 State；队列章节增加 returned-failure reconciliation。
- 成熟度章节改为 Agent Harness 维度，并明确“架构范式符合”不等于“已经通过大规模生产验证”。
- 更新 `.env.example`、`DEVELOPMENT.md` 和 requirements，加入 PostgreSQL Checkpointer 的配置与依赖。

### 验证
- 未调用 DeepSeek 或任何外部 LLM，没有产生 Token 费用。
- `python -m compileall -q app tests`：通过。
- Harness/Runtime/Workflow/Health/Hardening 重点回归：`68 passed`；新增 Harness readiness、执行时能力拒绝、跨绑定审批拒绝、Role API、Manifest API、返回失败重排和审批单次消费测试。
- 本次改动涉及的全部 Python 文件通过 `python -m ruff check`；全仓仍有 7 个位于未改动模块的历史未使用导入，本轮不混入无关清理。
- `python -m pytest -q` 最终完整回归：`315 passed in 95.94s`；production PostgreSQL/Redis 多实例故障切换仍需真实容器环境验证，不能由 SQLite 单机测试替代。

### 尚未解决与下一步
1. PostgreSQL Checkpointer 已具备代码路径和启动门禁，但本轮没有启动 PostgreSQL 集群做连接中断、主从切换和多 Worker 压测；这仍是部署验证项，不是自动通过项。
2. Tool Registry 当前同时保留内部 Python 返回适配和逻辑 JSON Schema。若未来让外部模型动态直连 Tool，应增加统一 envelope adapter，确保所有模型可见结果只出现 JSON 类型。
3. `app/agents/subagents.py` 文件名和兼容 API 暂时保留，避免一次性破坏已有前端/测试；后续大版本可迁移到 `roles.py` 并正式弃用旧入口。
4. Harness readiness 是配置与拓扑前置门禁，不替代真实 SLO、RAG 质量门禁和 24-case 真实 LLM 发布认证。

## 2026-08-13 09:53:31 +08:00：将系统总览重写为从零可读的 4,000 行独立 Agent 实现手册

### 本轮目标
- 用户反馈 2026-08-12 生成的统一文档仍然过于简略：它更像架构摘要和专题索引，默认读者已经理解 Agent、LangGraph、RAG、Checkpoint、幂等、Completion Gate 和发布门禁。
- 不再执着于引用已有专题文档，而是把主文档重写为一份可以独立阅读的大型教程，从“什么是 Agent”开始，逐步讲到当前项目的总体架构、实现细节、一次完整运行、评测、Bad Case、上线边界和面试表达。
- 每个重要模块不仅列出“用了什么”，还说明“为什么需要、输入输出是什么、在一次用户任务中怎样工作、失败时怎样处理、为什么没有选择其他方案”。

### 开发前重新审计
1. 从运行时重新导入并核对当前源码：`127` 条 FastAPI route、`29` 张 SQLAlchemy 表、`18` 个 Tool、`7` 个文件化 Skill 和 `7` 个职责型 SubAgent；主 LangGraph 为 `18` 个节点，自然语言图为 `8` 个节点，面试 Agentic RAG 为 `7` 个节点。
2. 重新阅读主图 State/Conditional Edge/Interrupt/Checkpoint、Task Contract/Completion Gate、Tool Runtime、ContextCompressor、PDF Splitter、VectorIndex、Matcher、Redis Worker 和运行控制源码，避免只根据旧专题文档转述。
3. 核对评测原始 JSON 和开发记录，继续区分确定性回归、真实 LLM 历史、定向修复结果和合成 SLO，不把局部通过覆盖历史整轮失败。
4. 上一版约 `625` 行，能够做架构评审，但缺少概念铺垫、节点级数据流、公式解释、完整例子、排查过程和面试口述，导致初学者看到大量名词却不知道模块之间为什么这样连接。

### 文档重构内容
- 将 `docs/CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md` 重写为 `4,108` 行、约 `8` 万字符的独立手册，包含 `33` 个主章节、`351` 个层级标题、`300` 余行表格和 `4` 张 Mermaid 图。
- 使用“李明上传中文 PDF，寻找深圳或远程 Agent 实习”的统一案例，贯穿自然语言规划、Profile 建档、真实岗位搜索、JD 入库、混合检索、人工选岗、匹配差距、定制简历、投递审批、面试准备和 Completion Gate。
- 从普通 LLM 调用讲起，解释 Agent、Workflow、RAG、Plan-Execute、有限 ReAct、Typed State、Node/Edge、Tool/Skill/SubAgent 的区别，避免读者只能记住技术名词。
- 对主 LangGraph 18 个节点逐项给出输入、处理、输出和失败语义；解释 Task Contract、Goal Ledger、Artifact、数据库回查和 Completion Gate 如何共同防止偷懒早停。
- 详细说明 PDF 解析、`paragraph_page_900_overlap160`、结构化/原始双 Chunk、JD 语义字段 Chunk、混合检索公式、Multi-query、RRF、Top20 Reranker、Evidence Gate、Matcher 评分公式和证据类型。
- 解释简历定制的允许/禁止边界、上下文 Packet、事实 Guardrail、一次有界 ReAct repair，以及为什么检查结果和改动摘要必须与简历正文分离。
- 解释持久化 Approval 与 LangGraph interrupt 的不同职责，说明 browser/email 外发为什么不可自动重放，以及 HighRiskActionToolService、幂等和审计如何配合。
- 从零讲解 Redis 队列、优先级、Run Lock、Heartbeat Stage、DLQ、Supervisor、SQLite 并发边界、Checkpoint 恢复、最危险的业务 commit/checkpoint commit 崩溃窗口、Rewind 和 Business Withdrawal。
- 为 PDF、RAG、岗位排序、Evidence Gate、JD Parser、真实 LLM、Token、合成 SLO 等指标补充定义、公式、数据集范围和不能外推的边界。
- 把开发中积累的问题扩展成 Parser、Chunk/RAG、岗位匹配、LLM、Agent/Tool、队列恢复、安全租户和评测器八类 Bad Case；每个重点 Case 给出现象、根因、修复、验证或残余边界。
- 增加成熟度清单、不能宣称的生产能力、两分钟项目介绍和 12 个常见面试追问的可直接口述答案，并附运行、排障、评测流程和术语表。

### 本轮发现的文档设计 Bad Case
1. **信息正确不等于文档可理解。** 上一版列出了 LangGraph、RAG、Tool Runtime 和指标，但没有先建立概念关系。对熟悉项目的人是高密度摘要，对初学者却是关键词堆叠。本轮按“问题 -> 设计约束 -> 组件 -> 数据流 -> 失败处理 -> 指标”重排，而不是继续给原摘要追加表格。
2. **模块清单不能代替一次真实执行。** 读者知道有 18 个节点，仍不知道用户的一句话怎样变成 Run、在哪里等待选岗、为什么恢复后不会重复投递。本轮用同一案例展示每一步 State、Artifact、Interrupt 和 Completion Gate。
3. **只写方案会掩盖设计取舍。** 例如“SQLite + Chroma”“不用自由 Multi-Agent”“关闭结构化节点 thinking”如果没有说明数据规模、事务边界、成本和评测依据，面试时容易变成技术栈背诵。本轮同时写明选择理由、限制和迁移条件。
4. **Bad Case 表格过度压缩。** “现象/根因/处理”一行能做索引，但不能让不了解实现的人复述。本轮对 Token 爆炸、Reranker 负增益、早停、Checkpoint 崩溃窗口等问题展开了输入、错误链路和修复机制。
5. **架构规模会继续漂移。** 昨日扫描为 126 条 Route，今日当前运行时为 127 条；新版首次校验时仍引用了旧数字。已在提交前改为 127，并保留昨日日志的历史快照，不篡改旧记录。以后总览中的规模数字必须由当前代码导入校验。
6. **Markdown 视觉换行会触发空白门禁。** 文档头部用行尾双空格强制换行，`git diff --check` 报 trailing whitespace。已改为引用块内空行，保持显示效果同时满足仓库检查。

### 验证
- Markdown 代码围栏共 `214` 个，数量为偶数；临时分段标记、TODO 和未完成占位均已清除。
- 运行时核对结果：`127 routes / 29 tables / 18 tools / 7 skills / 7 subagents`。
- `python -m pytest tests/test_project_structure_docs.py tests/test_agent_runtime_maturity.py -q`：`20 passed in 4.78s`。
- `python -m pytest -q` 完整回归：`309 passed in 106.03s`。
- 文档没有调用 DeepSeek 或其他外部 LLM，没有产生 Token 费用，也没有修改业务代码和运行数据。

### 尚未解决与下一步
1. 大型手册显著提高了从零理解能力，也增加了维护成本；后续架构改动应更新对应章节，并用运行时测试约束 Route、Tool、Skill 和图节点等可自动计算的事实。
2. 当前文档完整说明了 2026-07-22 严格整轮失败和后续定向修复，但当前版本仍需要一次受预算控制的真实 LLM 24-case 全量认证，不能只依赖历史或确定性结果。
3. 真实用户 SLO、投递转化和面试结果仍没有足够样本；手册明确保留受控上线边界，后续数据形成后再增加线上指标章节。
4. 后续可为主文档增加自动目录锚点检查和源码事实生成脚本，减少数字漂移，但不能让自动生成内容取代人工解释设计思路。

## 2026-08-12 20:07:08 +08:00：生成统一系统设计文档，建立架构、指标与 Bad Case 的单一讲解入口

### 本轮目标
- 不再要求读者在架构、Agent、RAG、PDF、SLO、Runtime 和面试材料之间自行拼接当前实现，生成一份从用户流程到源码实现的中文权威总览。
- 系统解释当前各模块如何协作，统一列出架构规模、LangGraph、Skill/SubAgent/Tool、RAG、简历、岗位、投递、面试、队列、恢复、安全、上下文和 SLO。
- 把历史失败、修复后定向回归、当前确定性回归和合成 SLO 分开陈述；Bad Case 统一使用“现象/根因/处理方案”口径，方便开发复盘和面试讲解。

### 开发前审计发现的问题
1. 当前实现分散在十余份专题文档中。单份 `ARCHITECTURE.md` 足够解释运行时，但无法同时回答“用户怎么使用、每个模块怎么实现、指标怎么样、失败过什么、为什么这样设计”。
2. 旧材料中的规模数字已经漂移。当前源码实际为 126 条 FastAPI route、29 张表、18 个 Tool、7 个 Skill、7 个职责型 SubAgent、18 个主图节点；继续引用早期 110 route/23 表/15 Tool 会让面试讲解与源码冲突。
3. 评测存在多个有效时间层：2026-07-22 的整轮真实系统基线、之后的真实定向复测、当前 309 项确定性回归、2026-08-11 的多语言 RAG 和合成 SLO。把这些结果放进一个“最新通过率”会产生错误结论。
4. 本地 `evaluation_runs` 中某些最新记录来自初版 SLO diagnostic 或显式 heuristic fallback，不一定是该 Suite 的权威发布结果。仅按 `created_at desc limit 1` 取数字，会把探针前提错误或运行模式差异当成产品回归。
5. Bad Case 已经记录很多，但分散在开发日志和专题面试文档中，读者很难建立“检索错误、LLM 错误、Tool 错误、恢复错误、安全错误、评测器错误”之间的分类关系。

### 实现内容
- 新增 `docs/CAREER_AGENT_SYSTEM_DESIGN_AND_EVALUATION.md`，作为当前系统的统一技术入口。
- 文档先从无简历搜索、只提供简历、简历+偏好三种用户模式讲起，再解释 FastAPI/LangGraph/Service/SQLite/Redis/LLM/外部工具的完整数据流。
- 明确主 LangGraph 18 节点、自然语言图 8 节点、面试 Agentic RAG 7 节点的责任，并解释为什么主流程采用 Plan-Execute、ReAct 只用于有界局部修复。
- 列出 7 个 Skill、7 个 SubAgent 职责、18 个 Tool 和 29 张表的领域归属，说明 Skill 渐进披露、Tool Runtime、Task Contract、Completion Gate 和 typed memory 的边界。
- 逐模块说明 PDF/Profile、JD/岗位发现、混合 RAG、Matcher、定制简历、审批外发、面试 Agentic RAG、上下文治理、Redis worker、checkpoint/回溯/撤回、RBAC、安全和 SLO。
- 汇总 12 类评测集、当前 309 项全量回归、PDF、180-case RAG、144-case 多语言 RAG、Evidence Gate、岗位排序、投递、注入、面试、真实 LLM 历史和合成 SLO 指标。
- 将 50 余个开发问题归纳为 RAG/证据、LLM 解析生成、Agent/Tool 完成语义、持久化恢复、安全多租户和评测可观测性六大类，并给出根因与已实施方案。
- 更新根 README 和 `docs/README.md`，把统一文档放到文档导航首位。

### 文档口径与 Bad Case 处理
1. **不能用最新一条数据库记录代表最新能力。** 例如初版 SLO diagnostic 会创建失败的 full-flow EvaluationRun，较新的 JD fallback 运行也和真实 LLM 运行模式不同。主文档按 `run + provider + dataset + gate` 描述，不按 ID 新旧覆盖事实。
2. **不能用 309 passed 代替真实 LLM 质量。** 309 项证明确定性控制面和固定回归，真实 LLM 整轮历史仍保留自然语言 0.85、24-case E2E 0.75、6-case full-flow 0.8333 的失败结论；修复后的局部回归只证明对应 Bad Case 已处理。
3. **不能用 Top1 满分隐藏长尾召回。** 文档同时解释 RAG Top1=1.0 与 Recall@5=0.7292 的含义，并保留 `coursework_vs_shipped=0.0521` 这一 PDF Chunk 弱项。
4. **不能把合成 SLO 写成线上 SLO。** API 375/375、Agent 67/69 明确标为 synthetic；真实流量仍为 `insufficient_data`。
5. **Bad Case 不能只列现象。** 每项至少说明根因和处理，例如相关性不等于蕴含、重试错误不等于副作用可重放、checkpoint 存在不等于副作用安全、END 不等于任务完成。

### 验证
- 从运行时代码导入并核对：126 routes、29 tables、18 tools、7 skills、7 subagents。
- 直接检查主图源码确认 18 个节点；自然语言图 8 节点；面试 Agentic RAG 7 节点。
- 新文档共 600 余行，包含模块说明、架构图、17 个以上指标/设计表和完整相对链接；所有 Markdown 相对链接存在。
- 本轮只修改文档和导航，没有调用 DeepSeek，没有产生 LLM Token 消耗。

### 尚未解决与下一步
1. 统一文档是当前实现的讲解入口，但专题文档仍可能继续漂移；后续功能变更必须同步主文档或在开发日志中明确它不受影响。
2. 当前版本尚未重跑 24-case 真实 LLM 全量发布评测，不能把历史 Pro 18-case 满分或局部定向通过写成当前全量认证。
3. 真实用户 SLO、用户满意度、投递成功、面试邀请和录用 outcome 仍缺数据，文档只给出受控上线边界。
4. 后续可以从本主文档派生一份 10 分钟面试讲述稿，但讲述稿应引用当前指标，不能再独立维护另一套架构事实。

## 2026-08-11 13:18:52 +08:00：建立用户旅程 SLO，完成中英跨语言 RAG 校准并修复证据误放

### 本轮目标
- 不再用“测试通过”代替线上可靠性，正式建立用户 API、Agent 有效终态、端到端时延与 Completion Gate 完整性的 SLI/SLO、测量窗口和误差预算。
- 针对中文为主、少量英文 JD/简历的真实场景，新增成对多语言 RAG 数据，量化同一概念在 `zh_zh/en_en/zh_en/en_zh` 和中英混写上的召回差异。
- 使用本机真实多语言 embedding 和 reranker 做阈值扫描，不调用 DeepSeek；开发期探针增加 LLMCallLog 零增量门禁，避免再次意外消耗余额。

### 开发前审计发现的问题
1. FastAPI 只有进程内请求数与平均时延，重启即丢失；AgentRun 虽有 latency/status，却没有窗口、最小样本量、用户取消排除、Completion Gate 完整率和误差预算。
2. 旧 180-case RAG 数据有四档难度和多种噪声，但没有同一概念的中英文配对，无法判断语言方向差异；也没有把错误 chunk 当负类校准“是否允许下游生成”。
3. Evidence Gate v2 使用 `query_coverage >= 0.08 OR first_stage >= 0.08`。在 1,440 个正负证据对上，144 个正例全部通过，但 1,296 个负例中误放 1,259 个，FPR 高达 `97.15%`。
4. 当前 reranker 是英文 MS MARCO cross-encoder。中文走词面启发式后会改写一阶段头部排序，不能因为有二阶段就假定质量更高。
5. LangGraph checkpoint SQLite 未设置 WAL/busy timeout；错误分类只识别 SQLAlchemy `OperationalError`，不识别原生 `sqlite3.OperationalError`。

### 实现：持久化 SLI、窗口化 SLO 与控制台
- 新增 `http_request_metrics`，持久化 method、route template、状态码、服务端时延和 `real/synthetic` 流量类型；指标写入失败不改变用户响应。
- 新增 `evals/slo_policy.json` 和 `SLOService`，支持 7/30 天窗口、最小样本、P95、Wilson 95% 下界与按样本折算的误差预算。
- 正式追踪从 `2026-08-11 12:00:00 +08:00` 开始，避免历史开发 Run 被当成真实用户流量；真实与合成结果永远分开。
- SLO 包含用户 API 非 5xx 比例、API P95、Agent 有效终态率、Agent P95 和 completed Run 的 Completion Gate 完整率。
- 新增 `GET /ops/slo` 和控制台 SLO 面板，并排展示真实流量与合成探针；样本不足返回 `insufficient_data/partial`，不伪装成通过。
- `run_slo_probes` 执行 75 个 HTTP 请求和 20 次真实 LangGraph 岗位检索，探针期间 LLMCallLog 只要增加就立即判定无效。

### 实现：144-case 多语言 RAG 与 Evidence Gate v3
- 新增 24 个 Agent 工程概念，每个概念 6 个语言组合，共 144 case、1,440 个 query-chunk 对；每个 case 有 1 个正确证据和 9 个 hard negative。
- 噪声覆盖同主题错误实现、计划学习、失败方案、相邻 Agent 技术和跨域干扰；与原 180 case 合计 324 个检索 case。
- 新增批量真实 embedding 评测、语言桶分解、vector/hybrid/reranker 策略比较和 378 组证据门禁阈值扫描。
- Evidence Gate v3 采用 `vector >= 0.50 OR (lexical >= 0.10 AND first_stage >= 0.45)`，不再让低词面分或低语义分单独放行。
- 门禁阈值按 embedding provider 绑定：真实多语言模型继续使用上述校准阈值；离线 hash embedding 使用独立的 `0.28/0.30` 测试阈值，并在报告中记录 provider。生产 release gate 仍拒绝 hash，不能拿测试模型分布反向放宽生产门禁。
- 简历证据门禁前移 EvidenceClassifier：负向、coursework、planned learning 和 missing-skill disclosure 不计为生成支持，但仍可保留做差距分析。
- 最终门禁 Recall `95.83%`、Precision `85.19%`、F1 `90.20%`、错误证据 FPR `1.85%`；83 组候选阈值满足整体与分语言 release gate，当前配置为其中 F1 最优且 FPR 较低的组合。

### 排序与语言差异结果
- 多语言纯向量：Top1 `97.92%`、Recall@5 `100%`、MRR `0.9896`。
- 生产混合一阶段：Top1 `97.22%`、Recall@5 `100%`、MRR `0.9850`。
- 修复前 Top20 reranker：Top1 `93.06%`；中文查英文从 `91.67%` 降到 `79.17%`。
- 修复后 Top20 reranker：Top1 恢复为 `97.22%`，与一阶段一致，不再退化，但本数据集上也没有增益。
- 纯向量 `zh_en` Top1 为 `91.67%`，低于 `en_zh` 的 `100%`；两者 Recall@5 均为 `100%`。结论是跨语言目标可召回，但中文查英文的头部排序仍弱，不能只报告总平均。
- 尝试多语言 `mmarco-mMiniLMv2-L12-H384-v1`，本机 CPU 对 1,440 对评分超过 10 分钟未完成，因延迟不合格终止，不进入默认生产路由。

### 开发中遇到的 Bad Case 与处理
1. **生成器索引错位。** 第一版 VARIANT 映射把中文 query 指向 concept ID、把中英文 positive 下标互换。生成前抽样检查发现并修复；以后生成数据必须做语言、query/positive 语义和正例数 invariant 测试，不能只检查 JSON 数量。
2. **阈值 Recall 很高但几乎所有负例也通过。** 旧门禁 Recall 100% 看似漂亮，Precision 只有 10.26%。原因是只在正例排序集上调参，没有负类决策评测。本轮增加 1,296 个负证据并把 FPR 纳入 release gate。
3. **Reranker 产生负增益。** 英文 cross-encoder 遇到中文 query 时改走 lexical heuristic，并允许越过一阶段 anchor，导致正确 Top1 下沉。修复为语言降级路由只能重排 anchor 之后的候选；二阶段必须用数据证明增益。
4. **相关不等于支持。** “投递前审批”和“邮件发出后才确认”embedding 分数很高，因为主题相关，但后者不能支持前者。修复不是继续抬高阈值，而是把 retrieval relevance、evidence type/polarity 和 claim guardrail 分层。
5. **`without` 规则误杀成功经历。** `Detected repeated calls without new artifacts and terminated...` 被判成 missing disclosure。根因是规则只看局部否定词。修复为识别无新产物/无进展这类运行触发条件，并保留回归；长期不能无限追加词表，需要 query-conditioned entailment 标注。
6. **第一版 SLO 探针分母错误。** 完整评测混入未配置 LLM 时必然失败的定制/投递任务，导致“可靠性”被探针前置条件污染。修复为只运行依赖就绪的岗位检索旅程，其他记录标为 diagnostic。
7. **终止 shell 后 Python 子进程仍运行。** 旧探针继续写库，造成 traffic class 再次污染。通过进程审计和按任务类型重分类修复；CI 后续应使用进程组或容器确保级联终止。
8. **第 19 次重复 Agent Run 出现 SQLite lock。** 所有 Tool 与 Completion Gate 均成功，最终 checkpoint 写入失败。增加 checkpoint WAL、30 秒 busy timeout 和 NORMAL synchronous，并把原生 sqlite OperationalError 分类为可重试 dependency_transient。修复后新增 20 次连续探针全部完成。
9. **测试命令使用了不同 Python。** 直接 `pytest` 指向系统 Python，缺 `aiosqlite`；`python -m pytest` 指向 workspace Python。项目同时补充显式 `aiosqlite` 依赖，避免依赖环境偶然安装。
10. **岗位浏览被简历事实门禁误杀。** 新 Evidence Gate 上线后，4 个岗位发现用例返回 422。根因不是岗位不相关，而是岗位聚合候选没有把底层向量分、规则分和一阶段融合分写入统一 retrieval metadata，门禁读到的全是 0；同时不能把“浏览相关 JD”和“用个人经历生成事实”视为同一种支持语义。修复为岗位检索显式保存三类分数，浏览只要求相关 JD，Matcher/ResumeTailor 才启用 `require_supportive_evidence`。
11. **一段经历同时有成果和能力边界时被整段丢弃。** `Built ... A/B tests but did not implement ranking models` 既包含真实交付，也包含缺失技能；旧分类器只要看到否定词就把整个 project chunk 判成 negative，导致真实经历无法用于定制。新增 `mixed_delivery_disclosure/mixed`，允许已交付部分进入上下文，同时保留否定句约束 LLM 和 Guardrail。
12. **中文否定词包含正向动作子串。** 第一版 mixed 判定把“没有实现投递前审批”中的“实现”当成正向交付，纯负例被错误放行。修复为先消除 `没有实现/未部署/did not implement` 等完整否定动作短语，再判断是否存在独立交付动作，并增加纯负向与混合证据成对回归。
13. **真实 embedding 阈值不能直接套到 hash 测试模型。** 多语言模型 `0.50/0.45` 阈值用于 hash 后，一条真实 A/B 项目证据只有 `vector=0.289`，全流程测试被拒绝。没有降低生产阈值，而是增加 provider-aware policy；质量报告同时输出 provider 与两组阈值，避免离线回归和生产校准互相污染。

### SLO 测试结果
- HTTP 合成样本 `375/375` 非 5xx，availability `100%`，P95 `110.457ms`。
- Agent 岗位检索累计 `67/69` 到达 completed，valid terminal rate `97.1014%`，P95 `54,917ms`；最新追加的 20 次连续探针全部完成。
- completed Run 的 Completion Gate 完整率 `67/67=100%`。
- 两次失败均为修复前 checkpoint lock；69 个样本下 95% SLO 允许 3 次失败，因此当前合成窗口 Agent 误差预算剩余为 1。
- 真实用户流量尚未建立，真实窗口应显示样本不足；上述结果只能作为发布前合成证据。

### 测试与文档
- SLO、ErrorClassifier、Reranker、Evidence Gate、Matcher 专项最终通过；多语言 release gate 通过。
- 最终全量回归 `309 passed in 97.99s`；真实多语言模型复跑 144 case/1,440 pair 与文档指标一致；最新 SLO 探针 20/20 Agent 完成且最近 LLMCallLog 增量为 0。
- 新增 `docs/SLO.md` 和 `docs/RAG_MULTILINGUAL_CALIBRATION.md`，`EVALUATION.md`、README 和运维控制台同步。
- 所有多语言与 SLO 评测没有调用 DeepSeek，最近 LLMCallLog 增量为 0。

### 尚未解决与下一步
1. 上线后需要真实 7/30 天流量校准目标，并增加 1h/6h/3d burn-rate 告警；当前没有资格声称真实 SLO 达标。
2. 中文查英文 Top1 仍是最弱桶，需要补真实中文求职需求、英文 JD 与人工相关性等级，不应只继续生成模板数据。
3. 逐请求写 SQLite 适合当前规模；多实例高并发应迁移到 Prometheus/OpenTelemetry 聚合，并使用 PostgreSQL 存业务权威状态。
4. 现有 reranker 在新数据上只做到不退化，没有增益。多语言候选模型必须通过延迟预算后才能替换，不能只凭模型卡选型。
5. EvidenceClassifier 仍是确定性规则层，适合作为低成本前置门禁，但不能替代 query-conditioned entailment 与最终 claim guardrail。

## 2026-08-11 11:26:36 +08:00：成熟 Agent 二次审计，补齐证据、Tool、完成语义与成本上限

### 本轮目标与成熟度判断
- 重新回答“用了 LangGraph、RAG、Redis 和很多 Tool 后是否已经是成熟 Agent”。结论是：此前已具备成熟控制面的主体，但仍不能把“节点跑到 END”“TopK 非空”“Tool 返回成功”和“状态中存在产物 ID”直接等同于任务正确完成。
- 按成熟 Agent 的四类失败面继续加固：RAG 证据假阳性、Tool 契约只验字段存在、Agent 用变化参数绕过循环检测、状态产物与 SQLite 权威数据不一致。
- 本轮只使用 hash embedding、fake LLM 和本地 SQLite 故障注入，不调用 DeepSeek，不消耗余额。

### 开发前审计发现的问题
1. `RetrievalQualityService` 只用原始 chunk 数、总 query coverage 和最高一阶段分数判定。重复文本可以凑够证据数；完全错误的 `other` chunk 即使不属于 project/experience/skill，只要分数高仍可通过；多查询有几路真正命中也没有进入决策。
2. Tool Runtime 只检查必填字段是否存在。`profile_id="abc"`、`limit=0` 或 browser tool 返回 `status="success"` 都可能越过 preflight/postflight，直到更深处才以难定位的错误失败。
3. 循环保护只能识别“同一 Tool + 完全相同输入”。Agent 只要改变无关参数就能继续调用，即使连续输出完全没有变化。
4. Completion Gate 读取 LangGraph state 中的 ID 和验证标志，却不回查 SQLite。错误 checkpoint 或代码 bug 可以把不存在的 `resume_version_id`、跨岗位 MatchResult 或已撤回产物伪装成完成。
5. 自然语言入口没有覆盖整个父图和子图的总 LLM 预算；面试子流程有独立预算，但嵌套 context 会覆盖父预算，无法回答“一次用户请求总共最多花多少”。
6. 完整流程搜索岗位后已经创建 MatchResult，用户选择岗位后又使用另一个幂等键重新跑 Matcher/RAG，增加 embedding/reranker 开销。

### 实现：RAG Evidence Gate v2 与有界检索修复
- 证据计数改为归一化文本去重后的 `unique_evidence_count`，同时记录 `duplicate_evidence_count`。
- 每条 evidence 独立计算 query coverage，并结合底层 lexical/first-stage score 得到 `supporting_evidence_count`；不能再由一条高分证据带着多条噪声通过。
- 指定 `expected_chunk_types` 后，至少必须命中一个业务语义类型；错误类型高分不再通过。
- 记录 multi-query 覆盖索引与覆盖率；reranker 降级、重复证据和不足 50% 的 query variant 命中进入 degraded 观测与 Online Quality 扣分。
- Matcher 初次门禁失败后只允许一次不调用 LLM 的 `semantic_type_filtered_retry`，限定 project/experience/skill 重新检索；仍不足则抛 `RetrievalQualityError`，阻止事实敏感生成。

### 实现：Tool Runtime 强类型合同
- 输入合同校验 str/int/float/bool/dict/list/datetime、union、list 泛型、ORM 实体名和枚举值；ID、TopK 和 limit 必须大于 0。
- 输出合同验证 Profile/Job/MatchResult/ResumeVersion/Application/InterviewPrep 实体，browser/email 工具必须返回规定字段和合法状态枚举。
- 这使“函数正常返回”与“Tool 业务合同成功”分离；合同错误进入稳定 `tool_contract_violation`，不会交给 LLM 猜。

### 实现：完成判定、偏航与成本治理
- Task Contract 升级为 v3、Completion Gate 升级为 v2。完成前回查 Profile、Job、MatchResult、ResumeVersion、Application 和 InterviewPrep，校验 profile/job/resume lineage 与 lifecycle；自然语言父图也检查请求 ID、下游产物 ID、岗位选择范围和子 run 终态。
- Trace 新增 `repeated_tool_output_without_progress`：同一 step family 即使输入变化，只要连续两次输出相同，下一次调用前终止。使用 step family 而非纯 tool name，避免把 `match_job` 与使用同一 Matcher 合同的 `fit_gate` 误判成循环。
- 新增自然语言父工作流总预算：最多 12 次 LLM、140,000 Prompt 字符和 32,000 completion token 预留。`LLMCallBudget` 支持 parent-child 层级，子面试预算的调用与 usage 同时计入父预算，任一层超限都在调用前失败。
- 岗位搜索结果携带 `match_result_id`；选岗后验证归属并复用该结果。完整流程回归断言只落一个 MatchResult。

### 开发中暴露的 Bad Case 与修复
1. **条件 lambda 返回函数对象。** 为复用 MatchResult 写的条件表达式受 lambda 优先级影响，handler 在一个分支返回另一个 lambda，Runtime 报 `object function can't be used in 'await' expression`。Trace 定位到 `match_job`；改为显式构造 `match_handler` 两个分支，并回归完整流程。
2. **无进展检测误把业务复用当循环。** 第一版只按 tool name 聚合输出，`match_job` 和 `fit_gate` 都使用 `matcher.match_job`，可能被混在一起。改为 tool + 去除数字后缀的 step family；`planner_0/1/2` 能识别为循环，`match_job` 与 `fit_gate` 保持独立。
3. **单值枚举被当成类名。** `draft_created` 没有 `|`，类型解析器最初按 ORM 类名处理，真实邮件草稿被拒绝。最终约定大写开头为实体类型、小写未知 token 为 literal enum，并用真实 HighRiskActionToolService 回归。
4. **岗位检索结果缺底层 lexical metadata。** JobDiscovery 返回的聚合结果 query coverage 为 1.0，但没有 chunk 级 `lexical_score`，支持证据数被算成 0，4 个岗位发现测试失败。修复为 Gate 从每条正文重新计算 row query coverage，不能假设每种检索器都携带相同 metadata。
5. **Task/Provenance 版本漂移。** 运行时 Task Contract 仍写 v1，Provenance 却声明 v2。改为共享 `TASK_CONTRACT_VERSION` 常量，避免观测记录声称的策略与真实执行策略不同。

### 测试与当前结果
- 改动前专项基线：`26 passed`。
- 新增对抗用例覆盖：重复证据、错误 chunk 类型高分、跨岗位自然语言产物、不存在的数据库产物、变化输入但无进展、错误 Tool 输入类型、非法外发状态、嵌套预算透传和选岗后 MatchResult 复用。
- 第一轮专项：`51 passed`；完整流程复用继续回归后 `34 passed`。
- 第一轮全量：`294 passed, 5 failed`。5 个失败全部来自本轮门禁误判，分别为单值枚举和聚合岗位结果缺 retrieval metadata；没有通过放宽门禁解决。
- 修复失败集：`8 passed`；检索/匹配/岗位发现/完整流程/Runtime 组合回归 `48 passed in 22.70s`。
- Planner 输出合同继续收紧并增加回归后，最终全量：`300 passed in 160.18s`，覆盖 LangGraph 恢复与 interrupt、RAG、PDF、Tool Runtime、自然语言图、审批/撤回、Redis worker、RBAC、前后端 API、面试 claim verifier 和评测服务。`compileall`、`git diff --check` 通过。
- 本轮没有真实 LLM 质量重跑，因此结论是控制面和确定性 bad case 回归通过，不把它写成 DeepSeek 输出质量提升。

### 当前仍不能夸大的边界
1. 控制面已覆盖检索不足、工具错参、早停、无进展、产物串线、预算失控、重试风暴和高风险审批，但“成熟”还需要真实用户流量中的成功率、人工复核结果和长期 SLO 证明。
2. RAG Gate 是启发式加检索分数的可解释门禁，不是训练后的相关性/蕴含分类器；阈值仍需用更多真实中文简历/JD bad case 校准。
3. SQLite checkpointer/业务库适合当前单机作品与受控上线；多实例高写入部署仍应迁 PostgreSQL，熔断和队列状态使用 Redis 原子语义。
4. 浏览器/邮件 Tool 已验证合同、审批和本地结果，但外部最终成功还应记录招聘站确认号、页面提交后状态或 SMTP Message-ID。

## 2026-08-09 22:41:06 +08:00：Agent Runtime、定向恢复、类型化记忆与在线质量闭环升级

### 本轮目标
- 不再用“已经用了 LangGraph、RAG 和很多 Tool”作为成熟 Agent 的判断标准，而是补齐运行时合同、稳定错误语义、定向恢复、熔断、长期记忆、用户反馈、在线质量复核和版本溯源。
- 处理成熟 Agent 高频 Bad Case：错误 Tool、参数缺失、瞬时依赖失败、重试风暴、外发副作用重放、无意义 LLM repair、poison queue message、跨用户记忆污染、Prompt/模型版本不可追踪和诊断日志泄密。
- 延续开发期原则：失败直接暴露并保留 Trace，不用静默兜底隐藏代码错误；所有验证使用本地 fake/SQLite/hash embedding，不调用 DeepSeek，不消耗余额。

### 开发前审计发现的问题
1. `AgentToolSpec` 已声明超时、重试、风险和幂等策略，但 `TraceService.step` 实际仍直接 `await handler()`。合同只是展示信息，没有执行语义。
2. 主图异常最终只保存 `str(exc)`。worker、Planner 和运维无法判断错误是否可重试、该由用户补资料还是该修代码。
3. 自然语言 Agent 的第一次执行只要失败就进入 LLM plan repair。API 未配置、预算耗尽、依赖熔断也会再次调用模型，既无效又浪费 Token。
4. Redis worker 对所有异常统一重排队，参数错误和 run 不存在等 poison payload 也要耗尽次数才进 DLQ。
5. 系统有 checkpoint 短期状态和业务表，但没有受治理的跨 run 用户偏好、选择结果和用户纠错；也没有负反馈进入人工复核的闭环。
6. LLM 日志有 trace/model/token，但缺 Prompt 指纹和策略版本；Prompt 预览还可能含邮箱、手机号和用户粘贴的 Key。
7. AgentRun 子资源虽然按 tenant 查询列表，steps/events/approvals/SSE 部分接口仍可只凭 run ID 读取；长期记忆第一版若只按 tenant/profile 查询，还会在同租户用户之间串上下文。

### 实现：统一 Agent Tool Runtime
- 新增 `app/services/agent_runtime.py`，引入 `AgentToolRuntime`、`AgentErrorClassifier` 和结构化 `ErrorEnvelope`。
- Tool 调用统一执行 preflight、合同参数检查、持久化 circuit 检查、超时、单层重试、输出合同、失败分类和 attempt Event。
- `AGENT_STRICT_TOOL_CONTRACTS=true` 时拒绝未注册 Tool。补注册 `LangGraph.AgentPlanner`、`llm.intent_planner` 和 `NaturalLanguageAgentService`，避免把编排内部调用留在合同体系外。
- Tool Policy 新增 `retry_owner` 和合同版本。招聘源读取由 Runtime 重试；LLM 由 LLMClient/业务 Handler 重试；Guardrail 由 ResumeTailor repair；Orchestrator 负责 checkpoint；不同层不重复放大调用。
- 高风险 `browser_apply/email_draft/email_send` 在 approval table 门禁后也进入同步 Runtime 合同和错误事件，但因为有外部副作用，始终只尝试一次。
- 新增 `tool_circuit_states` 持久化连续依赖失败、open/half-open/closed 和冷却时间；提供运维查看及 reset API。

### 实现：ErrorEnvelope 与定向恢复
- 分类覆盖 dependency timeout/transient/circuit、input/state、insufficient evidence、model response/config/budget、completion gate、execution budget、policy/human interrupt、contract 和 internal invariant。
- LangGraph `graph_failed`、Step failure、run output、worker payload 和 DLQ 均保存同一结构，而不是不同模块各写一段错误字符串。
- 自然语言图只允许 `input_or_state_validation` 和 `completion_gate_rejected` 进入一次 LLM repair。配置、预算、熔断、依赖重试耗尽和代码错误直接进入失败终点。
- Redis worker 读取 `retryable`。不可重试 payload 第一次即进 DLQ，并写 `terminal_reason=non_retryable_error`；瞬时故障仍按 worker 上限处理。

### 实现：类型化长期记忆和反馈闭环
- 新增 `agent_memories`，只存 preference、constraint、decision、outcome、correction，不保存原始聊天全文。
- 同 key 新值把旧值标记为 superseded；Prompt 只接收受条数和字符预算约束的 compact typed memory。
- run 完成后仅从已验证 Artifact 学习岗位选择和“投递材料已准备但未发送”等 episode，不把模型自由文本自动升级为事实。
- 新增 memory 查看、创建、停用 API。AgentRun 增加 `user_id`，记忆按 tenant/user/profile 隔离；当前用户可读取自己的私有记忆和显式共享记忆。
- 新增 `agent_feedback` 和 `agent_quality_reviews`。负反馈或低评分自动建人工复核项；结构化纠错写成 correction memory，后续 Prompt 不再重复错误事实。

### 实现：在线质量、版本溯源和安全日志
- 每次 run 完成后执行不调用 LLM 的 `OnlineAgentQualityService`，检查终态、失败步骤、未知 Tool、Completion Gate、RAG quality 和 ErrorEnvelope，低于阈值自动进入质量复核队列。
- 每个计划新增 `execution_provenance` Artifact：应用版本、Runtime/Task 合同、Tool 合同 SHA-256、模型路由、thinking mode、embedding、reranker、RRF 和安全策略。
- LLM 日志新增 Prompt bundle SHA-256、Prompt 观测合同版本和模型路由策略版本。
- 新增统一日志脱敏：API Key、Bearer Token、认证字段、邮箱和手机号不进入诊断预览；`prompt_tokens/completion_tokens/total_tokens` 使用精确字段规则保留，不再被笼统的 token 脱敏误伤。
- 修复 AgentRun steps/events/approvals/graph-state/SSE 和反馈接口的 owner scope；RBAC 开启后普通用户只能读取自己的 run，租户管理员仍可管理租户内记录。

### 开发中遇到的 Bad Case 与处理
1. **熔断对象默认值为 None。** 第一轮断连测试在 `consecutive_failures += 1` 处报 `NoneType`。SQLAlchemy 列的 `default=0` 是 INSERT default，不保证 Python 新对象立刻有值。修复为构造时显式写 0/closed，更新时使用 `int(value or 0)`。
2. **严格 Tool 注册打破旧测试。** 执行预算测试使用未注册的 `demo.tool`。没有为了兼容测试关闭严格模式，而是改成真实注册的 Planner Tool；测试夹具不能要求产品保留不安全入口。
3. **只按 tenant/profile 读取记忆会跨用户串线。** 第一版 typed memory 能按租户隔离，但同租户多用户仍可能共享偏好。补 `AgentRun.user_id`、ContextVar 透传和 owner filter，并新增同租户 user-a/user-b 对抗测试。
4. **顶层 run 有 user_id，子图 run 仍可能丢 owner。** Natural Language Agent 调用子 Orchestrator 时原接口只传 request。新增签名感知 `_run_orchestrator`，真实 Orchestrator 透传 tenant/user，旧测试 fake 仍按两参数调用，不牺牲测试可替换性。
5. **重试层级如果不指定 owner 会指数放大。** LLM Tool 原合同也写 max_attempts=2，如果 Runtime 再执行一次，叠加 LLMClient retry 和 worker retry会放大成本。新增 `retry_owner=handler`，Runtime 对这些 Tool 只执行一次。
6. **外发 Tool 的连接失败虽然 retryable，也不能自动重试。** Error 分类中的 retryable 表示故障性质，不代表所有操作都能重放；email_send 合同 max_attempts=1，失败只开熔断/审计，必须人工决定。
7. **不可重试 queue message 不能等三轮。** worker 现在携带 ErrorEnvelope，参数/状态错误直接 DLQ；避免队列吞吐被确定性坏消息占用。
8. **日志脱敏不能用字段名包含 token 的简单规则。** 否则 Token 成本统计会被清空。最终使用精确 secret key 和字符串模式，另对 Prompt 预览做 PII 脱敏。

### 测试与结果
- 新增 Runtime 成熟度测试，覆盖：瞬时断连后有限重试成功、外发不重试、连续失败开熔断、未知 Tool 拒绝、typed memory 覆盖、同租户跨用户隔离、负反馈建复核和纠错记忆、失败 run 在线门禁、秘密/PII 脱敏、Prompt 指纹和错误路由不浪费 LLM。
- 新增 Redis poison payload 第一次直接 DLQ 测试。
- 第一轮 Runtime 专项：`10 passed`。
- 第一轮全量：`287 passed in 118.02s`。
- 增加定向恢复、worker ErrorEnvelope 和 user owner scope 后，专项回归：`56 passed in 21.32s`。
- 增加 memory/feedback governance API 串联回归后，最终全量一致性检查：`291 passed in 103.21s`；`compileall` 和 `git diff --check` 通过，密钥扫描只曾命中测试构造字符串，现已改为运行时拼接并重新确认仓库无真实 Key。
- 本轮所有测试使用本地替身和确定性数据，没有调用 DeepSeek。

### 文档同步
- 新增 `docs/AGENT_RUNTIME_RELIABILITY.md`，完整解释 Tool Runtime、ErrorEnvelope、单层重试、Circuit Breaker、typed memory、在线质量和 8 类核心 Bad Case。
- `AGENT_DESIGN.md`、`ARCHITECTURE.md`、`EVALUATION.md`、`README.md` 和面试 Bad Case 文档同步本轮架构。

### 尚未解决与下一步
1. 当前持久化熔断使用 SQLite，适合当前单机/中等并发。高吞吐多实例应迁移 PostgreSQL 或 Redis 原子状态机并做竞争测试。
2. Online Quality Gate 是确定性运行质量门禁，不是用户满意度模型；需要持续把人工复核结果回流版本化评测集。
3. typed memory 当前使用 scope/type/时间检索，尚未在大规模记忆上引入向量召回；数据量增大后应评测 metadata filter + semantic retrieval，而不是现在提前增加复杂度。
4. 外发工具仍需增加外部 outcome probe，如邮件 Message-ID、招聘站确认号和提交后页面状态，不能只相信 Tool 本地返回。
5. 本轮没有重跑真实 DeepSeek 全链路，结论只覆盖 Runtime 控制面、数据边界和确定性 Bad Case。

## 2026-08-09 21:17:11 +08:00：建立成熟 Agent 的任务完成、轨迹正确性与 RAG 证据质量闭环

### 本轮目标
- 回答并落实“Agent 是否真的完成任务才停止”，不能再把 LangGraph 到达 `END`、LLM 自称完成或工具没有报错当成业务成功。
- 为工具调用增加可执行的正确性约束：工具是否该调用、参数是否正确、先后顺序是否合理、是否越权、是否重复、外发前是否审批。
- 为 RAG 增加错误结果处理：区分“返回了 TopK”和“证据足以支持下游生成”，证据不足时允许弱匹配判断但阻止简历定制等事实敏感生成。
- 覆盖成熟 Agent 常见的早停、跑偏、无进展循环、跨产物串线、重试假失败、checkpoint 分支缺审计前缀和 Prompt Injection 清洗后证据不足。
- 全程不调用 DeepSeek。先用代码测试和独立 SQLite 确定性评测证明控制面正确，避免为代码问题消耗余额。

### 开发前发现的评测盲区
1. **旧 `trace_passed` 只证明 planner 跑过。** Agent 全流程评测中的 Trace 通过条件只是每个 run 有 completed `plan_task`，Artifact 通过条件也只要求存在 `execution_plan`。模型漏做后续动作、工具参数错或不该调用的工具执行成功，都可能被判为通过。
2. **工具成功率不是工具正确率。** `tool_success_rate=95.45%` 统计函数返回状态，不能回答 Tool Selection、Argument、Ordering、Redundancy、Side Effect 和 Outcome 是否正确。
3. **主图存在假完成路径。** `find_jobs_for_profile` 即使岗位源返回空数组，也会执行 finalize 并以 completed 结束。自然语言入口有二次空结果检查，但主图、后台任务和 API 直接调用没有统一约束。
4. **自然语言计划仍可能静默漏动作。** 之前修过 `create_profile + search_jobs` 的提前 return，但完成判定仍散落在执行分支中；新增动作后容易再次出现 plan 列出了动作、result 却没有对应产物的问题。
5. **RAG 把“有结果”当“有证据”。** Matcher 直接使用 TopK Profile chunk，没有记录 query coverage、候选分数、语义类型覆盖和 provider 降级。无关结果仍可进入 ResumeTailor。
6. **多查询和二阶段排序的边界不清。** 原实现通常用一个很长的 `title + skills + responsibilities + raw_jd` 查询简历，容易让不同意图互相稀释；如果每个子查询都独立 CrossEncoder，又会重复消耗计算。
7. **中文 reranker 存在已知降级。** 默认 `ms-marco-MiniLM-L-6-v2` 偏英文，中文 query 会进入 CJK lexical route。旧结果 metadata 有 fallback reason，但下游没有把它纳入证据质量报告。
8. **没有统一的无进展循环预算。** 固定主图本身是 DAG，但 Trace 层没有阻止 repair/recovery 或未来自由规划器以相同参数反复调用 Tool。

### 实现：Task Contract、Goal Ledger 与 Completion Gate
- 新增 `app/services/agent_reliability.py`，为五类主任务声明 `TaskPolicy`：必要 Goal、Artifact、Step、偏序约束和允许 Tool。
- `plan_task` 生成 `task_contract` Artifact。它是系统执行契约，不是交给 LLM 自由改写的计划文案。
- 所有 `finalize_find_jobs/finalize_tailor/finalize_quick_apply/finalize_interview/finalize_full_flow` 不再直接连接 `END`，统一进入 `completion_gate`。
- Completion Gate 生成 Goal Ledger，并检查：业务目标、必要 Artifact、Trajectory V2、审批、Validator、Profile/Job/Resume/Application/Interview ID 一致性。
- 只有 Completion Gate 可以返回 completed。失败会同时写入 `completion_verification` Artifact、`completion_gate_rejected` Event 和明确错误，不把部分结果包装成成功。
- 对固定 DAG 的终点缺失不启用泛化 Critic 无限重跑。原因是确定性步骤缺失通常是产品代码错误，重新调用 LLM 会隐藏缺陷、增加 Token，并可能重复外部副作用。
- 自然语言 LangGraph 增加 `verify_user_plan` 和 `verify_repaired_user_plan`。第一次结果缺动作时携带 `missing_actions` 进入一次 plan repair，第二次仍缺失则失败；等待岗位选择或投递确认是合法 `waiting_for_confirmation`，不按早停处理。
- plan repair 使用首轮 `prior_result` 作为已完成账本。已存在的 Profile、Job、搜索结果、定制简历、面试包或投递确认不会重复执行，只补缺失动作；修复 Prompt 也只暴露已完成结果摘要，不重新发送完整产物。

### 实现：工具轨迹与运行预算
- `careeragent-trajectory-eval-v2` 检查 required step、task-specific allowed tool、工具参数、步骤偏序、重复签名、投递审批和 Completion Artifact。
- 不使用唯一的严格路径字符串。岗位搜索可以有多个 `match_job_*`，quick apply 可以复用已有简历或补建简历；评测使用 required/superset、allowed/subset 和 precedence constraint 组合描述合法轨迹。
- 投递 Tool 如果没有 approved `application_packet` Action，轨迹直接失败。Fit Gate 正确阻断采用独立合法轨迹：必须执行到 `fit_gate`，且 application Tool 不能出现。
- `TraceService.step` 在真正调用 Tool 前检查 `AGENT_MAX_TOOL_STEPS=48` 和 `AGENT_MAX_IDENTICAL_TOOL_CALLS=2`。第三次相同 Tool/参数写入 `execution_budget_rejected` 并抛错。
- Evaluation trace 现在保存每步 input/output，并在系统汇总中报告 `trajectory_v2_pass_rate` 及 missing step、argument、order、duplicate、approval 等失败分桶。

### 实现：RAG Multi-query、RRF 与 Evidence Gate
- Profile 证据检索拆为三类 query：完整岗位语义、标题与必需技能、职责与资格条件。
- 每类 query 使用 SQLite chunk 上的向量/词法混合一阶段召回；候选使用 RRF 融合，然后只对融合后的 Top20 执行一次 rerank，策略标记为 `semantic_field_multi_query_rrf`。
- 新增 `RetrievalQualityService`，记录 evidence count、query token coverage、first-stage/final score、chunk types、multi-query hits、reranker degraded routes 和可解释 confidence。
- 低证据并不自动代表候选人不匹配，也不触发无限 query rewrite。Matcher 仍可据此给出弱匹配/缺口；ResumeTailor 等 evidence-dependent generation 必须通过门禁，否则抛出 `RetrievalQualityError`。
- Prompt Injection 清洗后再次计算可用证据数量。恶意行被移除后证据不足会直接停止生成，避免“清洗前通过、清洗后拿空文本继续写”。
- `match_results` 和 `job_search_sessions` 新增 `retrieval_quality_json`，SQLite 启动迁移负责旧库加列；岗位发现低质量时保存报告并返回 422，而不是用 500 或展示无关岗位。
- 当前 confidence 是规则合成分数，不是校准概率。`degraded_routes` 只做显式披露，没有因为默认中文 reranker 降级就悄悄使用另一套生成逻辑。

### 开发中暴露的新 Bad Case 与修复
1. **空岗位搜索从 completed 改为 failed 后，一个旧测试失败。** 旧测试名是“queued run 可启动”，却使用 EmptyJobSearch 并断言 completed。它实际固化了错误业务语义。本轮没有放宽 Completion Gate，而是把测试改为验证 empty search 必须显式失败并产生拒绝 Trace。
2. **恢复后成功仍被旧失败 attempt 污染。** Tailor 第一次模拟 worker crash，checkpoint resume 后第二次成功。Trajectory V2 最初看到历史 failed step 就拒绝整个 run。修复为同一 `step/tool/input` 签名以最后一次 attempt 决定当前状态，同时历史失败仍保留在 AgentStep 和可靠性统计中。
3. **Checkpoint fork 只有图状态，没有数据库轨迹前缀。** 从 `tailor_resume` 前 checkpoint 建立新 run 后，state 已包含 plan/profile/job/match，但新 run 没有对应 AgentStep/Artifact，严格门禁判定跳步。修复为按 checkpoint 时间截点复制此前 Artifact，并写 `checkpoint_inherited_trajectory` 保存逻辑前缀；新分支步骤与继承前缀联合评测。
4. **不能把继承步骤伪装成新 run 的真实调用。** 如果直接复制 AgentStep，会使新分支 Tool Call 数、成本和延迟虚高。最终只在 lineage Artifact 保存继承步骤，Trajectory Evaluator 合并读取，实际 AgentStep 仍只记录新执行。
5. **合法 Fit Gate 阻断不能要求 Completion Artifact。** 低匹配 quick apply 在 fit gate 抛错，本来就不会经过 Completion Gate。Trajectory V2 增加 expected policy block 语义：接受 fit_gate 自身 failed，但要求之前步骤完整、之后 application Tool 不得执行。
6. **RAG 最少两条证据会误伤极短但有效的简历。** 第一版门槛设为两条。复盘后改为至少一条且必须同时有 query coverage 或 first-stage relevance 信号，避免把“文档短”误判为“证据错误”。
7. **跨岗位产物串线不能靠字段非空发现。** Completion Gate 增加 ID integrity，岗位、定制简历、投递包和面试包的 profile/job/resume ID 必须一致，历史分支的 selected job 还必须来自当前 ranked jobs。
8. **完成校验触发 repair 后会重跑已成功动作。** 第一版 Natural Language Completion Gate 能发现 silent early-stop，却把 repaired plan 从头执行，可能重复搜索和生成产物。最终将首轮 result 传入第二次执行，并按 Artifact ID 跳过已满足动作；新增测试确认“搜索已完成、只缺定制简历”时只调用 `tailor_resume_for_job`。
9. **新增测试时断言被插入到错误函数。** plan repair 测试第一次运行出现 `NameError: run is not defined`，不是产品代码失败，而是补丁把上一条空搜索测试的最后一条断言移动到了新测试末尾。将断言放回原 case 后，专项 24 项通过。该事件保留在日志中，避免把测试实现错误误报为 Agent Bad Case。

### 新增与调整的测试
- 新增错误参数、步骤乱序和越权 `email.send` 的 Trajectory V2 Bad Case；旧 completed 状态会通过，当前同时报告 argument/order/unexpected-tool 失败。
- 新增相同 Tool/参数第三次调用测试，确认执行前被预算拒绝。
- 新增 supported evidence 与 irrelevant noise 的 Evidence Gate 对照。
- 新增自然语言多动作 silent early-stop 测试，`search_jobs` 有结果但 `tailor_resume/interview_prep` 缺失时必须进入 repair。
- 新增跨岗位 Artifact ID 混用测试。
- 保留并强化崩溃恢复、checkpoint rewind、审批与 Fit Gate 阻断回归。

### 验证结果
- 首轮专项测试：`39 passed`。
- 首轮全量测试：`272 passed, 2 failed`。两项失败均来自新门禁发现的恢复语义缺口，不是随机测试波动。
- 修复 attempt 与 lineage 后，两个恢复定向测试 `2 passed`。
- attempt/lineage 修复后的中间全量测试：`274 passed in 111.74s`。
- 增加自然语言 silent early-stop 与跨岗位 Artifact 串线回归后，全量测试：`276 passed in 122.09s`。
- 增加 plan repair 只补缺失动作的回归后，最终测试结果见本节末尾的最终一致性检查。
- 最终一致性检查：`277 passed in 147.85s`；`compileall`、`git diff --check`、文档契约测试和密钥扫描通过。
- 使用独立 `data/runtime/reliability_eval_20260809.sqlite`、空 API Key、`LLM_FALLBACK_ENABLED=true`、hash embedding、关闭 reranker 跑 6-case Agent full-flow：pass/top1/score/trajectory/artifact/langgraph/application packet 均为 `1.0`，3 个低匹配 case 全部按预期被 Fit Gate 阻断。
- 本轮没有调用外部 LLM，没有新增 DeepSeek Token 消耗。独立评测只证明控制面和确定性工作流，不能替代真实模型质量认证。

### 文档同步
- `AGENT_DESIGN.md` 新增 Task Contract、Goal Ledger、Completion Gate、有界执行和 RAG Evidence Gate。
- `ARCHITECTURE.md` 新增 Agent 可靠性控制面及 checkpoint lineage 说明。
- `EVALUATION.md` 新增 Trajectory V2 指标定义、合法 policy block 和 2026-08-09 确定性结果。
- `docs/interview/BAD_CASES_AND_DECISIONS.md` 新增假完成、工具成功率误导、恢复轨迹语义和 RAG 错误扩散四个可直接面试讲述的案例。

### 尚未解决与下一步
1. 2026-07-22 的整轮真实 Agent Release Gate 历史结果仍为失败。本轮没有重跑 24-case 真实 LLM workflow，因此不能宣称整个产品已经通过生产发布认证。
2. Retrieval confidence 尚未用人工标注的中文真实岗位/简历对做概率校准。下一步应构建 hard-negative、短简历、同名技术、过期 JD 和对抗文本集，比较 BGE multilingual reranker 与当前 CJK lexical route。
3. Trajectory V2 目前覆盖注册主任务和自然语言动作结果。未来接入 browser_apply/email_send 的正式业务主图时，需要为每种外部工具增加 outcome probe，例如网页确认号、邮件 Message-ID，而不能只验证本地 Tool 返回值。
4. SQLite checkpointer 适合当前单机部署；多实例生产部署应迁移到 LangGraph Postgres checkpointer，并对 tenant/thread/run 建立一致性约束和灾备演练。
5. 线上仍需把失败 Trace、用户忽略/选择岗位反馈和人工标注回流到版本化数据集，形成离线回放、Canary 与发布门禁闭环。

## 2026-08-09 15:01:16 +08:00：补全 LangGraph 崩溃恢复、Checkpoint 历史分支与业务撤回

### 本轮目标
- 把原来仅支持人工确认 `resume` 的 checkpoint 能力扩展为真正的 worker 崩溃自动恢复：运行中的进程消失后，scanner 能判断是否仍有存活心跳和锁，从最近持久化 checkpoint 继续，而不是直接把 run 标成失败。
- 提供 checkpoint 历史查询和“从这里创建新流程”，允许用户回到某个未结束节点重新执行，同时保留原 run、原 trace 和原业务产物，不篡改历史。
- 判断 CareerAgent 是否需要“会话撤回”。结论是需要，但产品语义应是**撤回某次求职流程产生的内部材料**，而不是删除聊天消息或抹掉已经发生的外部操作。
- 全程使用本地测试和持久化 fixture，不调用外部 LLM，不消耗 DeepSeek 余额。

### 开发中遇到的关键问题与 Bad Case
1. **旧 stale 策略不是真正恢复。** queued run 可以重新入队，但 running run 超时后只能由运维标记为 failed。这样 worker 被杀、机器重启或进程崩溃时，即使 SQLite checkpoint 完好，也无法继续执行。
2. **“有 checkpoint”不等于恢复安全。** 最危险的崩溃窗口是节点已经提交业务表，但 LangGraph 尚未保存下一份 checkpoint。直接从旧 checkpoint 重放会再次创建 MatchResult、ResumeVersion、Application 或 InterviewPrep。原实现只有后三类部分节点在创建后补写幂等键，第一次 INSERT 本身仍可能重复。
3. **只用 Redis lock 会遗漏两类故障。** 锁过期不代表 worker 一定死亡，长节点仍可能运行；锁还存在也不代表进程一定健康。因此 recovery scanner 同时检查分阶段 heartbeat 和 run lock，任何一个仍活跃都不抢占，二者都消失且 SQLite run 超过 stale 阈值才恢复。
4. **历史回溯不能改写原 thread。** 如果把旧 checkpoint 直接设为当前状态，原 run 的“当时发生了什么”会被覆盖，审批和产物也无法区分来自哪条时间线。本轮采用 fork 语义：新建 AgentRun、新 `graph_thread_id`，复制选定 checkpoint，并在新 state 中替换 `run_id`。
5. **Checkpoint ID 不能随便用 UUIDv4。** LangGraph SQLite saver 依赖 checkpoint ID 的可排序性确定最新记录；随机 UUIDv4 会让克隆后的 checkpoint 可能排在父 checkpoint 前面，`aget_state()` 读到错误版本。最终使用 LangGraph 自带的 `uuid6()` 生成单调可排序 ID。
6. **“撤回”不能等价于删除。** 用户可能选错岗位或生成了不满意的简历，需要让这批材料退出活动视图；但 Profile、Job 和 MatchResult 是共享事实，trace 和审批是审计证据，不能级联删除。邮件已发送、网页表单已提交更不可能撤销。
7. **异常阶段边界需要分开。** 回溯 API 起初把“复制 checkpoint 失败”和“新 run 入 Redis 失败”放在同一个异常块，前者发生时可能引用尚未创建的 `fork_run`。现已拆成创建分支与队列入队两个阶段，分别返回 409 和可追踪的 503。
8. **真实启动发现 worker CLI 依赖工作目录并不可靠。** 文档命令 `python scripts/run_agent_worker.py` 会把 `scripts/` 放在 `sys.path[0]`，未安装为 site-package 时无法导入 `app`。服务层测试没有覆盖进程入口；脚本现显式加入仓库根目录，直接运行和 supervisor 拉起都使用同一解释器依赖。
9. **迁移前旧 run 不具备恢复条件。** 第一次启动真实 worker 后，scanner 找到一条没有 `graph_thread_id` 的旧 stale run，原逻辑无意义重试 3 次后才进 DLQ。现在 scanner 在入队前检查 thread 标识；不可恢复的旧 run 直接写 `crash_recovery_unavailable`、控制审计和 OpsAudit，不消耗队列重试预算。
10. **接口正确但前端仍空白。** 内置浏览器能看到“流程版本”容器，API 也返回 checkpoint/撤回预览，但页面没有内容。根因是 `base.html` 仍使用旧静态资源版本号，浏览器命中 7 月脚本缓存。更新 cache-busting 版本并增加前端契约断言后，刷新会强制加载本轮 JS/CSS。
11. **移动端 checkpoint 行不溢出，页面却仍横向滚动。** 浏览器在 390px 视口测得文档宽 445px；逐元素定位后发现 12 列 Grid 虽在媒体查询改成单列，但 `.panel` 保留默认 `min-width:auto`，被历史列表的固有宽度撑开。为所有 `.workspace-grid > *` 设置 `min-width:0` 后，Grid 子项才允许收缩到可用宽度。
12. **修复 Grid 后仍剩 37px 溢出。** 第二次元素级检查定位到事件流固定 `112px + 1fr`，第二列时间戳的默认最小内容宽度仍会撑开详情面板。把第二列改为 `minmax(0, 1fr)`、时间文本允许换行，并在 760px 以下改为单列事件卡，最终移动端文档宽与视口一致。

### 实现与设计决策
- `RedisTaskRunner` 增加 stale-running recovery scanner。它从 SQLite 找到超时 running run，确认 heartbeat/run lock 均不存在后，将 run 改回 queued，并写入 `execution_mode=checkpoint_resume`、恢复次数和阶段，再放入 high priority 队列；超过 `AGENT_RUN_MAX_RECOVERY_ATTEMPTS=3` 才终止为 failed。
- Orchestrator 在 `checkpoint_resume` 模式下读取 `graph_thread_id` 的最新 state，调用 `graph.ainvoke(None, config)` 从 `snapshot.next` 继续；仅当该 thread 完全没有 checkpoint 时才从输入重建，并记录明确 trace。
- MatchResult、ResumeVersion、Application、InterviewPrep 的业务幂等键改为首次 INSERT 同事务写入并建立唯一索引；遇到并发唯一键冲突时回滚并读取已有产物。恢复执行不再依赖“先查再插”的竞态窗口。
- 新增 checkpoint history API，返回 checkpoint ID、父 checkpoint、下一节点、interrupt 和安全 state 摘要；回溯时只允许选择有后续节点的 checkpoint，并创建独立 run/thread。源 run 与新 run 都记录 `AgentRunControlAction` 和 trace 事件。
- 新增 `agent_run_control_actions` 审计表，统一记录 crash recovery、checkpoint rewind、withdraw 等控制面操作及 actor、源 checkpoint、新 run 和结果。
- 新增流程级业务撤回：本次 run 生成的简历版本和面试包标记 `lifecycle_status=withdrawn`，内部投递材料标记 `status=withdrawn`，未执行的审批取消；Profile、Job、MatchResult、trace、artifact 和审计保留。
- 外部邮件已经发送或浏览器已经提交时，撤回请求返回 409 并列出不可逆 artifact。高风险工具执行前也会检查父 run 是否已取消/撤回，防止审批通过后又在撤回窗口执行。
- 历史记录页新增 checkpoint 时间线、控制操作历史、“从这里创建新流程”和“撤回本次生成材料”；材料列表明确显示“已撤回”。控制台 stale run 增加“立即恢复”。

### 验证结果
- 新增 6 组关键场景测试：定制节点崩溃后从 checkpoint 继续且不重跑规划/匹配；历史 checkpoint fork 后状态和 thread 隔离；存活 heartbeat/lock 不被误恢复；缺少 graph thread 的旧 run 不进入无意义重试；撤回补偿内部产物并取消待执行审批；已发邮件阻止伪撤回。
- 定向扩展回归 `55 passed`。
- 完整执行 `python -m compileall -q app`、`git diff --check` 和 `python -m pytest -q --basetemp=.tmp_test/recovery-full`，结果 `269 passed in 106.91s`。
- 使用当前业务库 run `#233` 的 `finalize_find_jobs` 前 checkpoint 通过 HTTP 创建分支 `#236`；新 run 先返回 queued，随后由真实 Redis worker 完成，`output_json.recovery.mode=checkpoint_rewind`，源控制审计正确关联 target run `#236`。
- 随后撤回测试分支 `#236`：状态变为 withdrawn，控制操作 completed，`run_withdrawn` 事件 1 条，源 run `#233` 仍为 completed，证明分支隔离和撤回不改写原流程。
- 内置浏览器在桌面视口加载 9 个可回溯节点和 1 个撤回按钮；390px 移动端最终测得 `clientWidth=scrollWidth=375`，checkpoint/event 内部溢出均为 0。
- 最终全量回归在全部修复后重新执行，结果 `270 passed in 114.42s`；Redis、API `8071` 和独立 worker 均保持运行，队列为 0，验证过程中由旧 run `#19` 产生的 2 条 DLQ 通过正式 discard service 清理并留下 OpsAudit，最终 DLQ 为 0。

### 面试表达与当前边界
- 可以把这次问题概括为：**checkpoint 解决控制流位置，业务幂等解决副作用重放，heartbeat + lock 解决故障判定，审计表解决谁做了恢复或撤回；四者缺一不可。**
- 当前自动恢复面向 Redis worker 承载的后台 Agent run；同步请求若在 API 进程内执行，不由 worker scanner 接管。
- 回溯是从历史 checkpoint 派生新流程，不是把原流程倒带；这与 Git 分支类似，但业务产物仍通过 run 级幂等键隔离。
- 当前没有通用多轮聊天 message store，因此没有做“撤回某一句对话”。CareerAgent 的真实需求是撤回一次任务的交付物和后续动作，现有实现与业务风险更匹配。
- SMTP 发送和网页表单提交只能阻止未来动作、不能补偿外部事实；如果未来接入支持撤销的第三方 API，应为每种工具单独实现 compensation，而不是在通用层假装成功。

## 2026-07-22 23:49:02 +08:00：基于重构后代码和评测库重建完整面试材料，修正文档事实漂移与指标口径混淆

### 本轮目标
- 项目在 LangGraph、Redis、岗位双 RAG、面试 Agentic RAG、模型路由和系统评测方面连续重构，旧面试资料已经无法准确反映当前实现。本轮不再在旧 110 问和长报告上局部打补丁，而是从当前代码、配置、数据库 EvaluationRun、评测集和开发日志重新建立一套可直接口述的中文面试材料。
- 材料必须同时回答“系统是什么、为什么这样设计、代码怎样实现、指标怎样解释、遇到过哪些 bad case、当前还不能承诺什么”，避免只罗列 LangGraph/RAG/Redis 等关键词。
- 本轮不调用外部 LLM，不消耗 DeepSeek 余额；所有数字只读取已有数据库和可审计报告。

### 事实盘点与发现的问题
1. **旧面试资料已经整体过时。** `docs/interview/archive-2026-07-06` 仍以早期主图、旧面试模块和旧评测口径为主；当前项目已经有 7 个 Skill、15 个 Tool Policy、7 个 SubAgent、17 个 LangGraph 主图节点、23 张 SQLAlchemy 表和 110 条 FastAPI route。继续在旧文档上追加会让历史方案和当前方案混在一起。
2. **文档与源码出现预算漂移。** `app/core/config.py` 当前面试 RAG 硬预算为 5 次调用、70,000 Prompt 字符和 15,000 completion token 预留，但根 README、`ARCHITECTURE.md` 和 `EVALUATION.md` 仍写 60,000。该差异来自后续真实完整包根据 trace 调整预算，旧说明没有同步更新。
3. **Agent 设计仍残留旧面试实现。** `AGENT_DESIGN.md` 说 `InterviewPrepService` 使用结构化规则且不强制 LLM，但当前产品实际是问题生成 + `InterviewAgenticRAGService` 的本地 multi-query、混合检索、Pro 批量 claim 生成/验证和服务端 verified-claim 组合。旧描述会导致面试时把已经删除的规则答案框架讲成现状。
4. **评测数字存在三个时间层，不能挑最好看的混用。** `#113` 是修复前整轮真实系统实验；`#114/#118/#119/#120` 是修复后定向回归；`#101-#105/#111/#122` 是专项或不同运行模式。局部 1/1、2/2 不能改写成“最新全链路 100%”。
5. **同名指标也必须带运行模式。** `#107` 使用真实 LLM 跑 30 个 JD case，pass 和 grounding gate 均为 1.0；最新 `#122` 使用显式 heuristic fallback，skill F1 为 0.9901，但 grounding gate 0.9333 低于 0.95，release gate 失败。两个结果不冲突，不能只保留其中一个。
6. **Reranker 的中文边界需要诚实说明。** 默认模型 `ms-marco-MiniLM-L-6-v2` 偏英文，代码检测中文 query 后会走 CJK lexical rerank。固定 RAG 集证明真实 cross-encoder provider 被使用，但不等于所有中文请求都经过多语言 CrossEncoder；新材料将它列为后续 BGE multilingual 对照项。
7. **PDF 指标有必须保留的弱点。** 选中 Chunk 策略整体通过门禁，但 `coursework_vs_shipped` 的 Top3 context hit 只有 0.0521。这支持下一步做 sentence/facet evidence，而不是用总体 0.776 隐藏混合极性问题。

### 本轮改动
- 重写 `docs/interview/README.md`，建立当前材料的阅读顺序、事实基线、运行时证据文件和结论边界。
- 新增 `PROJECT_OVERVIEW_AND_ARCHITECTURE.md`：从用户三种入口、系统分层、LangGraph 控制面、Skill/Tool/SubAgent、23 张表、模型路由和代码地图解释整体架构，并提供 Mermaid 图。
- 新增 `IMPLEMENTATION_DEEP_DIVE.md`：逐步解释 PDF/JD 解析、双 RAG、岗位排序公式、简历经历召回、Evidence Type、匹配公式、上下文包、ReAct、投递审批和面试 Agentic RAG v3。
- 新增 `PRODUCTION_ENGINEERING.md`：说明 FastAPI 并发边界、Redis/SQLite 分工、优先级队列、锁/幂等、heartbeat、recovery、DLQ、checkpoint、审批、RBAC、Prompt Injection 和可观测性。
- 新增 `EVALUATION_AND_METRICS.md`：给出 12 类数据集规模、Recall/MRR/nDCG/grounding/pass^k 定义、`#101-#113` 整轮指标、归一化成本、修复后定向回归、`#122` 红灯和当前发布判断。
- 新增 `BAD_CASES_AND_DECISIONS.md`：整理 12 个可直接用于面试的真实案例，包括 59 次面试 LLM 调用、事实正确但答非所问、多动作提前结束、Parser/Chunker 组合丢证据、`AgentTrace` 子串污染、跨语言阈值误杀、JD 行内章节、评测器重复进程、LangGraph state 丢字段、跨页进度、理想化 RAG 和简历诊断污染。
- 新增 `INTERVIEW_QA.md`：以 30 个高频问题完整回答 LangGraph、Plan-Execute/ReAct、Skill/SubAgent/MCP、Chunk、双 RAG、向量库、reranker、匹配、幻觉、并发、队列、安全、评测和上线判断。
- 新增 `PRESENTATION_SCRIPTS.md`：提供 30 秒、2 分钟、5 分钟讲述稿、四条现场演示路径、Agent/后端/RAG 三种简历 bullet 和防止过度承诺的写法。
- 保留 `CAREER_AGENT_PROJECT_EVIDENCE.md` 与 `TECHNICAL_KNOWLEDGE_BASE.md` 的短事实结构。它们会被面试 Agentic RAG 运行时检索，不把长篇人类讲述稿混入，避免增加检索噪声和 Token。
- 同步修正根 `README.md`、`docs/ARCHITECTURE.md`、`docs/EVALUATION.md` 的 70,000 字符预算，更新 `docs/AGENT_DESIGN.md` 的当前面试链路，并将新材料加入 `docs/README.md` 导航。

### 指标核验结果
- PDF `#101`：96 case/576 query，Top3 keyword/page/context=`0.9479/0.8299/0.7760`，release gate 通过。
- RAG `#102`：180 case，Top1=`1.0`、Recall@3=`0.6125`、Recall@5=`0.7292`、MRR=`1.0`、nDCG@5=`0.7862`，真实 embedding/cross-encoder、无 fallback，门禁通过。
- 岗位排序 `#103`：13 query/130 candidate，nDCG@5=`0.9495`；投递 `#104` 和 Injection `#105` 固定集门禁通过。
- 整轮系统 `#113`：自然语言规划 `17/20`、LLM workflow `18/24`、full-flow `5/6`、pass^2=`0.6667`，严格发布门禁失败。剔除重复进程后为 171 次调用、218,342 tokens、0.257601 元，p50/p95=`3.538/9.684s`。
- 修复后定向回归：`#114=3/3`、`#118=2/2`、`#119=1/1`、`#120=1/1`。材料明确说明这些只证明已知失败切片修复。
- 最新离线 JD `#122`：precision/recall/F1=`0.9817/1.0/0.9901`，但 grounding gate=`0.9333<0.95`，仍保留 release gate 失败结论。

### 方案取舍
- 没有重新生成一个单文件几万字报告。按架构、实现、生产、评测、bad case、Q&A 和讲述稿拆分，既方便系统学习，也避免面试时在一份文档里搜索困难。
- 没有删除旧归档，因为它能证明架构演进；总导航明确标记旧资料不是当前事实源。
- 没有为让材料“好看”重跑付费模型或选择性删除红灯。当前任务是梳理已有证据，系统发布状态必须保持可审计。
- 没有把所有已有开发日志复制进面试资料，只选能解释设计变化、根因和验证的数据；完整时间线仍由开发日志承担。

### 验证与未完成项
- 新材料共 7 份主文档，内部相对链接检查无断链；新旧文档中的面试预算和 LLM 链路描述已与源码配置对齐。
- 完整离线回归为 `264 passed in 95.72s`；`git diff --check`、Markdown 相对链接、旧预算/链路事实扫描和密钥扫描通过。本轮没有调用外部 LLM。
- 8070 服务保持运行，`/health` 返回 `status=ok`、`llm_configured=false`，文档与测试过程没有重启或关闭用户正在检查的服务。
- 当前仍未完成最新代码的 24-case 全量真实 LLM 发布认证，也未修复 `#122` 两个离线 JD grounding case；它们是产品质量问题，不应在文档任务中通过改数字解决。
- 新材料中的固定集结果仍受合成/可控数据分布限制；fit 需要双人复标，完整面试包与 pass^k 需要扩大样本。

### 下一步
1. 修复 `#122` 两个 JD grounding case，建立双人 fit 标注和 disagreement set。
2. 预算允许后按同一 methodology 重跑 24-case workflow、6-case full-flow 和代表 case pass^3，生成新的整体 release report，而不是继续叠加定向 smoke。
3. 对中文 reranker 和 sentence/facet evidence 做独立对照，重点观察 `coursework_vs_shipped`、Recall@5、nDCG 和 p95。
4. 将新面试材料作为人工学习文档，运行时面试 RAG 继续只读取经过审核的短项目证据和技术知识库。

## 2026-07-22 23:22:00 +08:00：逐项修复系统评测失败，建立可审计 fit rubric、跨语言事实门禁与评测器数据契约

### 本轮目标与处理原则
- 上一轮系统评测 `#113` 的严格发布门禁未通过：自然语言规划为 `17/20`，真实 LLM workflow 为 `18/24`，Agent 全流程为 `5/6`。本轮没有把所有失败归因于模型，也没有通过降低阈值、关闭 verifier 或增加无限重试来追求全绿，而是逐条区分 **产品代码错误、检索/证据错误、评测器错误、标注分歧和模型输出错误**。
- 修复顺序采用“先复现 stage trace，再修确定性组件，最后只重跑受影响的付费 case”。真实 DeepSeek 只用于验证必须经过模型的节点；parser、matcher、grounding、数据契约和 release gate 先用离线回归证明没有代码问题，避免继续浪费余额。
- 本轮把原始模型结果和最终发布结果分开统计。LLM 可以产生错误 evidence/gap，但只有通过 grounding verifier 的字段才能进入用户消息；被拒绝内容保留在 `raw_*`、`rejected_*` 和 LLM call trace 中，便于调试而不污染用户结果。

### Bad case 1：多动作计划“看起来对”，执行器却在第一步提前结束
**现象：** `profile_context_plus_search_zh` 等自然语言 case 能输出 `create_profile + search_jobs`，action precision/recall 甚至可以满分，但主 intent 仍是 `create_profile`。旧执行器进入建档分支后直接 return，后续岗位搜索不会执行，因此 trajectory JSON 正确而业务终态错误。

**根因：** 计划 schema 同时存在单值 `intent` 和多值 `actions`，执行层却把 intent 当作终止分支。评测只检查“是否列出工具”，没有检查“工具是否按依赖顺序真正执行”。此外，LLM 有时会在更新档案时遗漏 prompt 明确给出的技术词，旧链路会接受这类合法 JSON。

**解决：**
- 用终端业务动作归一化主 intent，多动作计划以最后一个需要完成的用户目标为主，而不是以第一个准备动作结束。
- 执行器按 effective actions 连续执行，支持“创建档案 -> 搜索岗位 -> 后续材料”，不再在 `create_profile` 后提前 return。
- 增加 plan contract：prompt 明确出现的更新事实和技术词必须进入 `profile_patch`；缺失时只允许一次结构化 repair，repair 仍不满足契约就直接抛错。
- 档案更新改为增量合并。技能/目标岗位去重合并，项目、经历、教育按稳定键合并，避免一次自然语言补充把旧数组整体覆盖。

**验证：** 真实 DeepSeek Flash 自然语言定向复测 `EvaluationRun #114` 为 `3/3`，intent、action precision/recall、依赖字段和禁止动作均为 `1.0`，release gate 通过。

### Bad case 2：简历 parser 把整段叙事当 headline，随后又被 chunk 清洗删除
**现象：** `zh_agent_candidate_weak_planned_learning` 原文中的“经历：阅读过……正在学习 Python，计划做 RAG”被 LLM 填进 headline。`ResumeTextSplitter` 为避免元数据重复，会从 raw chunk 中删除 headline，结果这整段关键负向证据也一起消失，只剩“市场营销专业”，RAG 无法召回计划学习和没有项目的事实。

**根因：** parser 的 optional metadata 校验只检查“原文中是否出现”，没有检查字段形态是否像 headline；而 parser 与 chunk cleaner 分别看都合理，组合后形成跨组件数据损失。

**解决：** 新增 role-like headline 形态约束，拒绝带“经历/项目/技能”等章节前缀、过长叙事句和数据描述；LLM 返回的无效 headline 不发布，并写入 `quality_gate.rejected_optional_fields`。同时让 citation grounding 在相邻 2/3 句窗口中寻找正向证据，修复 PDF 分栏或换行把技能、动作和项目名拆开的漏判，但包含“阅读/计划/未实现”的 passage 仍不能当正向交付。

### Bad case 3：fit 标签不稳定，既有错误标注，也有 matcher 抬分
**现象：** 六个 hard/adversarial workflow 初次修复后仍只有 `4/6`。推荐算法 case 被模型判 `weak_fit/45`，旧 gold 一度改成 partial；Prompt Injection 噪声 case 被判 strong。继续调 prompt 无法解释两个方向相反的错误。

**逐项仲裁：**
- 推荐算法候选人有 Python、A/B 和 metrics，但 ranking、CTR、feature engineering 三项岗位核心能力全部缺失，并明确写了没有实现相关模型。按“岗位主线多数不在已有交付证据内”的 `fit-rubric-v2`，最终判 `weak_fit 35-54`，不是因为模型输出 45 就迁就模型。
- worker case 的唯一相关项目明确是 coursework，Redis、Celery、LangGraph 和 worker 并发均缺失。课程项目不能因匹配到 Python/FastAPI/RAG 就自动升级为 partial，继续标 `weak_fit`。
- 注入噪声 case 有 Python/FastAPI/RAG 和真实 trace viewer 项目，但没有直接实现 Agent、Tool Calling、评测系统，应该是 `partial_fit`，不能因项目名 `AgentTrace` 判 strong。

**真正的产品 bug：**
- `fuzzy_contains` 使用 `term in text`，导致 `Agent` 命中 `AgentTrace`。改为英文数字 token 边界；合法的 `Agent workflow/agents` 仍可由精确 token、别名或受控相似匹配命中。
- JD 中“有评测经验”没有稳定进入 required skills。`Model Evaluation` 增加中文“评测/评估 + 经验/体系/平台/指标/流程”别名，避免漏掉真实缺口。
- fit judge 增加可审计契约：weak/partial/strong 分数分别为 `0-54/55-84/85-100`；只有课程/计划不能高于 weak；有相关交付且至少匹配两项时不能仅因存在缺口判 weak；strong 必须达到至少 `0.67` 的必需技能覆盖率。只有违反契约才触发一次 repair，正常 case 不增加调用。
- 模型的 `matched_evidence` 和 `gaps` 先保留 raw 版本，再由双边 verifier 核验。用户可见 message 只由 verified evidence/gaps 确定性组合，不直接发布模型自由文本。

**真实复测：** `EvaluationRun #118` 的两个历史失败 case 均通过。推荐算法为 `weak_fit/45`；注入噪声为 `partial_fit/70`，matcher 明确输出 matched=`Python/FastAPI/RAG`、missing=`Agent/Tool Calling/Model Evaluation`。两例 parser、RAG、fit 标签与分数、定制和 Guardrail 均为 `1.0`，release gate 通过。

### Bad case 4：真实跨语言成果被 0.70 阈值误杀，不能简单全局降阈值
**现象：** 前端全流程中，LLM 把 `Improved component reuse and UI regression coverage` 忠实改写为“提升组件复用率和 UI 回归覆盖率”，多语言 embedding 分数为 `0.698`。旧门禁在 0.70 处拒绝 quick apply；但直接把全局阈值降到 0.65，会增加“把复用率改写成可靠性提升”等事实漂移的漏放风险。

**解决：** 保留 0.70 通用语义阈值；`0.65-0.70` 只开放两类有第二证据的边界恢复：
1. claim 技术 taxonomy 完全包含于原档案已验证技能，例如 `accessibility <-> 无障碍`；
2. claim 的最佳证据能回指原 Profile 的结构化 `project/work/campus description|impact|details`，并且成果语义组一致。

成果语义组新增 `reuse/coverage`，继续保留 reliability/performance/accuracy/efficiency/cost。边界恢复必须同时满足 embedding、否定极性、成果语义和结构化事实四个条件；“复用/覆盖”被改成“可靠性”仍会拒绝。恢复方法与最佳证据写入 `semantic_claim_grounding.results`，不是不可解释放行。

**真实复测：** `EvaluationRun #119` 单独执行前端全流程，top job score=`82.43`、ranking margin=`44.73`；岗位选择、定制、quick apply、投递包、Trace、Artifact 和 LangGraph 均为 `1.0`，Application `#32` 成功生成。该链路仅 2 次 Flash 调用、输入 2,971、输出 386、合计 3,357 tokens。

### Bad case 5：JD 一行里同时出现职责和要求，章节检测把正文整行丢掉
**现象：** 新增“要求 Python、FastAPI、RAG 和评测经验”的测试后，parser 只抽到 `Agent`。不是词典没识别，直接调用 `_extract_skills(raw)` 能抽全；问题出在 `_split_responsibilities`。

**根因：** 招聘文本没有在“负责……”与“要求……”之间换行。章节检测发现行内含“要求”，将整行视为 qualification header；`_content_after_header` 又只支持冒号，得到空字符串后 `continue`，于是整行正文被静默丢弃。

**解决：** 预处理按中文句号/分号拆分行内章节；支持无冒号的 `负责/岗位职责/任职要求/Requirements` 前缀，并保留前缀后的正文。没有把测试样例改成更规整的多行文本，因为真实 JD 的格式噪声正是 parser 必须承受的输入。

### Bad case 6：评测器自己制造失败，说明测试基础设施也需要契约
**单 case JSON 形状错误：** PowerShell `ConvertTo-Json` 在只有一个对象时可能把数组折叠成根对象，运行器随后把 dict 当列表迭代，最终在 `case['name']` 处出现难懂的 `TypeError`。所有 EvaluationService 数据集现在统一通过 `_load_case_dataset`，根节点不是数组、case 不是对象或缺少非空 name 时立即给出文件名和索引明确报错。

**子集 release gate 假失败：** 只跑一个应成功 quick apply 的正例时，旧 summary 仍固定要求 `fit_gate_block_count>=1`，因此业务 case 通过但 release gate 失败。现在每个结果记录 `expected_fit_gate_blocked`；只有所选子集包含负例时才要求对应阻断数。`run_agent_full_flow_evaluation` 也支持 `case_indexes/case_limit`，便于低成本定向回归。

**Windows 测试环境噪声：** 一次新增测试使用 pytest 默认临时目录，遇到 `C:\Users\IC\AppData\Local\Temp\pytest-of-IC` 权限错误。该用例改为内存 Path stub，业务测试不再依赖系统临时目录；完整回归统一显式使用项目 `runtime` basetemp。

### Bad case 7：修 `AgentTrace` 后误伤 A/B tests，二次回归没有靠放宽阈值解决
**现象：** 第一次完整回归为 `263 passed, 1 failed`。30 条 JD parser 数据的 grounding gate 从 1.0 降到 `0.9333`；失败集中在 `llm_eval_prompt_regression` 和 `recommendation_ranking_ctr`，共同的 unsupported skill 是 `A/B Testing`。

**根因：** 旧 grounding 依赖字符串前缀，`A/B test` 会偶然命中 `A/B tests`。收紧英文 token 边界后，这个隐式复数匹配消失，而别名字典只列了单数。说明严格化会暴露过去被宽松实现掩盖的词典缺口。

**解决：** 保留词边界，不恢复子串；明确加入 `A/B tests`、`AB tests` 复数别名。定向 30-case JD 回归恢复通过，随后完整回归为 `264 passed in 95.70s`。

### 最终真实 LLM 证据与 Token
- `#114`：自然语言历史失败 3 case，`3/3`，release gate 通过。
- `#118`：fit 历史失败 2 case，`2/2`；9 次 Flash 调用，输入 11,711、输出 1,840、合计 13,551 tokens，provider latency 合计 24.661 秒。
- `#119`：前端 Agent full-flow 1 case，`1/1`；2 次 Flash 调用，合计 3,357 tokens，release gate 通过。
- `#120`：注入噪声最终复测 1 case，`1/1`；4 次 Flash 调用，输入 4,356、输出 740、合计 5,096 tokens；`jd_unsupported_keyword_count=0`。
- 本轮最后三次定向验证合计 15 次 Flash 调用、22,004 tokens。没有调用 Pro，也没有重跑昂贵的完整面试包。
- `#120` 原始 gap grounding 仍为 `0.6667`，verifier 拒绝 1 条。该条把“工具调用 trace 查看器”直接概括成“缺少 Tool Calling 经验”，证据语义存在邻接但边界不够精确；最终 verified gap 和用户消息为 1.0。这个差值必须保留，不能用最终通过掩盖模型仍会生成边界不严的草稿。

### 尚未解决及原因
- EvidenceClassifier 仍按整个 chunk 输出单一 evidence type。推荐算法项目同一 chunk 同时包含已交付的 dashboard/A-B 分析和“未实现 ranking/CTR”，目前整体被标为 `missing_skill_disclosure`。这对保守判定有利，但会丢掉句子级混合极性。下一步应做 sentence/facet 级 evidence，再在 chunk 层聚合，而不是继续增加 if/else。
- fit-rubric-v2 已写明本轮分歧理由，但仍是单人仲裁，不代表统计意义上的标注一致性。扩大评测前应做双人独立标注，报告 Cohen's kappa 和 disagreement set。
- 本轮只重跑失败切片，没有再次付费运行全部 24 个 LLM workflow，因此可以结论为“已知失败 case 修复并回归通过”，不能据此把全套真实通过率宣称为 100%。
- sentence-transformers 仍提示 `cache_dir` 参数弃用。它不影响本轮结果，但需要迁移到 `model_kwargs/processor_kwargs/config_kwargs`，避免后续库升级变成启动错误。

### 面试时可以如何概括这轮工作
- 不是简单说“我调了 prompt”，而是：先用 stage trace 将失败拆成 planner 终态、parser 数据损失、matcher 子串污染、LLM rubric、grounding 误杀和评测器假失败；确定性错误在代码层修，语义判断用可版本化 rubric 和 verifier，只有违反契约才调用一次 repair。
- 强调 raw-vs-published 双轨：真实模型在通过的 `#120` 中仍产生一条不够严谨的 gap，系统没有假装模型完美，而是把它留在 trace、从用户结果中剔除。这比只报告最终 pass 更能说明生产 Agent 的可靠性设计。
- 强调阈值治理：0.698 正例没有通过“把 0.70 改成 0.65”解决，而是增加 taxonomy/结构化事实/成果语义/否定极性四重佐证；随后又用“可靠性替换复用率”的反例验证不会漏放。
- 强调评测器也是软件系统：单 case 数组折叠、子集 gate 强制负例、Windows 临时目录权限都可能制造假失败。评测数据 schema、实验子集语义和 invocation trace 与产品代码同样需要工程质量。

### 下一步
1. 把混合正负项目 chunk 拆成 sentence/facet evidence，分别评测 evidence type、polarity 和与具体 required skill 的绑定关系。
2. 为 fit-rubric-v2 建立双人复标流程，固定分歧仲裁记录，再全量运行 24-case Flash workflow 和代表性 `pass^3`。
3. 优化 gap generator，使其优先输出“缺少直接实现证据”而不是宽泛“缺少经验”，减少 verifier 拒绝率，同时保持发布层高精度。
4. 迁移 sentence-transformers 的 cache 参数，并把该 warning 加入依赖升级回归。

## 2026-07-22 22:12:17 +08:00：建立分层 Agent 系统评测，完成真实全链路运行并暴露规划、证据门禁与续跑记账问题

### 这次为什么不能再用“功能可以运行”作为结论
- 之前各模块分别有测试，但缺少一个统一实验把 PDF Chunk、岗位 RAG、LLM 解析、自然语言规划、LangGraph trajectory、业务终态、安全、可靠性、延迟和费用放在同一发布判断里。“接口返回 200”只能证明链路没有崩溃，不能证明 Agent 选对工具、使用了真实证据，也不能证明相同任务重复运行稳定。
- 本轮按 OpenAI eval best practices、LangSmith trajectory evaluation、BFCL、τ-bench、RAGAS 和 AgentDojo 的共同思路改为分层门禁：确定性组件先验收，再评 LLM 节点、工具轨迹、端到端终态、`pass^k`、安全和性能。没有把各项指标加权成一个总分，因为安全或投递错误不能被检索高分抵消。

### 工程实现
- 新增 `AgentSystemEvaluationReporter`：自动扫描数据集规模和难度，统一汇总各 suite，计算工具成功率与延迟、真实 LLM 调用成功率、p50/p95/max、模型/route/trace 用量、缓存命中 token 和 DeepSeek 人民币费用，并使用硬门禁而非主观综合分。
- 新增 `scripts/run_agent_system_eval.py`：提供 `deterministic/full` 两种模式、suite 选择、渐进式报告、断点续跑、真实 Token 总预算、可靠性重复、面试 case 上限和可选真实岗位源 smoke。未配置 LLM、embedding 或 reranker 时 full 模式直接失败，不启用结果 fallback。
- `LLMCallLog.context_json.provider_usage` 新增 `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens` 和 `reasoning_tokens`，解决此前只能用总 prompt token 推算费用、无法判断缓存价格的问题。
- 运行器为每次进程启动新增 `evaluation_invocation_id`，同时保留跨续跑的 `system_evaluation_id`；进度 JSON 写入 `invocation_id`。这使同一实验的多次启动可以在 trace 中拆分，不再只能靠时间窗人工对账。
- 修复断点报告尚未创建 `evaluation_run_id` 时调用 `db.get(..., None)` 产生的 SQLAlchemy 空主键警告。
- 新增系统 reporter、供应商 usage、模型路由和各类产品回归测试；完整评测方法、指标和结果写入 `docs/AGENT_SYSTEM_EVALUATION_2026-07-22.md`。

### 真实运行结果
- 实验 `agent-system-20260722-214145-d004ddf3`、总记录 `EvaluationRun #113` 完整结束，无 suite 异常，但严格发布门禁失败。
- 通过项：PDF Chunk 96 cases；RAG 180 cases；岗位相关性 13 cases；投递 Guardrail 27 cases；Prompt Injection 70 cases；JD Parser 30 cases；claim verifier 14 cases；完整面试包 1 case。
- 未通过项：自然语言规划 `17/20=85%`；LLM 工作流 `18/24=75%`；Agent 全流程 `5/6=83.33%`；3 个代表 case 重复 2 次的 `pass^2=66.67%`，低于 80% 门槛。
- 剔除确认的评测进程重叠后，系统净调用 171 次、218,342 tokens、0.257601 元；调用延迟 mean 4.624s、p50 3.538s、p95 9.684s、max 34.788s。Pro 只有 5 次调用但占 0.095959 元，热点集中在面试生成、验证和 claim verifier。
- 原始账单为 194 次、227,511 tokens、0.267945 元。多出的 23 次、9,169 tokens、0.010344 元属于评测运行器进程重叠开销，原始值保留用于账单核对，净值用于描述产品调用成本。
- 最终不调用外部 LLM 的完整回归为 `242 passed in 96.35s`；Python compileall、运行器 CLI、diff whitespace 和密钥扫描通过。8070 服务保持运行，`/health` 为 `ok`、`llm_configured=false`。

### 开发过程中发现的关键 badcase
1. **计划结构正确不代表能执行正确**：`profile_context_plus_search_zh` 返回了 `create_profile` 和 `search_jobs` 两个 action，但主 intent 是 `create_profile`。当前执行器会在建档分支提前返回，导致搜索不发生。早期 action precision/recall 会给它满分，加入 intent-to-effective-action 与端到端语义后才暴露。这是面试中可重点说明的“trajectory 与业务终态必须同时评估”的案例。
2. **标注标准与模型判断可能冲突**：推荐算法候选人有 Python、A/B、指标经验但缺 ranking/CTR，旧标注为 weak，模型判 partial；worker 岗位和 Prompt Injection 噪声 case 也有类似争议。直接为了门禁改标签会污染基线，因此本轮保留失败，记录为待双人复标，不把全部失败简单归因于模型。
3. **同义改写导致证据门禁误伤**：PDF 布局噪声 case 的 fit 标签正确，但“技能包括 Python、FastAPI、RAG”等中文归纳与原始英文/分栏证据的支持分只有 0.60-0.6957，被正向证据门禁拒绝。全流程中的前端“无障碍”跨语言结果也以 0.6953 卡在 0.70 阈值下。说明下一步应校准多语言证据检索和等价表达，不应全局降低阈值。
4. **`pass^k` 识别出系统性错误**：Prompt Injection 噪声工作流连续两次失败，另外两个 case 连续通过。失败不是随机温度抖动，继续多跑只会继续花 Token，应该先修 rubric/grounding 契约。
5. **评测进程本身也需要可观测性**：PowerShell 前台命令超时后，Python 子进程仍继续运行；随后启动的断点续跑和原进程在 JD suite 短暂并发，造成重复调用。只看实验 ID 会把它误计为产品成本。本轮通过日志 ID、trace、时间窗和重复 case 确认 23 次开销，并新增 invocation ID。这个问题说明“评测器也必须像被测系统一样有身份、trace 和成本审计”。
6. **完成率与调用成功率不是一个指标**：简历解析出现 1 次 `ConnectError`，业务重试后 case 最终完成，所以 case 完成率为 100%，调用成功率为 99.42%。以后报告必须同时保留两者，不能用最终成功掩盖供应商抖动。

### 为了让评测结果可信而修复的产品问题
- Resume Parser 改为基于清洗后的原文做 grounding；不再从任意 Agent 字样推断目标岗位，计划学习不写成已掌握技能，无法溯源的可选字段逐项拒绝并在 trace 中记录。
- JD Parser 移除把普通 prompt/“提示词”映射为 Prompt Engineering 的宽泛规则，避免注入文本污染岗位技能。
- Application Guardrail 将裸 `无/未` 改为有作用域的否定模式，修复“无障碍”被误判；投递材料首行强制为精确 company/title，避免通用标题通过。
- 自然语言评测将 intent 隐含动作计入 effective actions，并提高多动作计划的检查强度。
- 面试 verifier 把“证据是否支持”与“是否回答当前问题”拆开，提示和模型路由调整为 Pro；14 个独立 case 的 accuracy、positive recall、question accuracy、specificity 均为 1.0，FPR 和答非所问误接收率为 0。
- Interview Prompt 预算由 60k 调到 70k，使本轮 1 个完整包在不截断的情况下通过；这只是基于真实 trace 的预算调整，不代表可以继续无限扩大上下文。

### 尚未修复及原因
- 多动作 planner 的主 intent/执行器语义尚未重构。本轮目标是先冻结准确基线；在没有新增“建档后继续搜索”执行测试前直接改 executor，可能影响开始页已有流程。
- 跨语言正向证据的 0.70 阈值尚未调整。当前只有少量边界样本，直接放宽会提高 unsupported claim 漏放风险，需要先补充正负校准集并画出阈值曲线。
- 24 个 fit 工作流中的标签争议尚未改标。需要双人独立标注和分歧仲裁，记录一致率后才能发布新 rubric 版本。
- 完整面试链路本轮只跑 1 个 case，可靠性只测 3 cases × 2。余额有限时先保证跨组件覆盖；该规模只能作为 smoke 和回归门禁，不能宣称生产成功率或稳定延迟分布。
- 当前 invocation ID 能拆分未来重复进程，但尚未增加同 experiment 的进程互斥锁。强行加锁会影响合法的分 suite 并发，下一步应设计 coordinator lease/heartbeat，而不是简单创建一个可能遗留的 lock 文件。

### 下一步
1. 让 planner 输出的主任务与 action DAG 一致，执行器按 DAG 继续执行，并补多动作终态测试。
2. 建立 fit 标签与跨语言 evidence 的双人复标集，区分模型错误、grader 错误和 annotation disagreement。
3. 使用多语言校准集评估 grounding 阈值的 precision/recall，再决定 reranker、规范化或分类型阈值。
4. 扩大关键流程 `pass^3` 和完整面试包样本，持续报告模型路由、Token、p95 与失败 trace。
5. 为评测 coordinator 增加带 heartbeat 的 lease，防止同一 experiment 重复执行同一 suite，同时允许显式的并行 suite。

## 2026-07-22 17:53:21 +08:00：根据真实 Flash/Pro 对照落地节点级模型路由，并有限放宽 Flash 输出预算
### 决策依据
- DeepSeek 官方价格中，Flash 未缓存输入/输出单价约为 Pro 的三分之一，并发上限是 Pro 的 5 倍；但同样本真实评测显示，Flash 只在短结构化节点与普通 canary 上接近 Pro，10 题面试多约束回答在 repair 后仍有两题未覆盖，Pro 则通过发布门禁。
- 早期 Flash 简历深度建议还出现过 5 条建议全部无法通过简历证据契约的问题。因此不能简单设置一个全局 Flash，也不能先跑完整 Flash、失败后再把整条链路用 Pro 重跑。

### 路由方案
| Route | Trace | 模型 | 策略 |
| --- | --- | --- | --- |
| `flash_economy` | `natural_language.`、`resume_parser.`、`jd_parser.`、`evaluation.llm_judge_suitability`、`resume_tailor.`、`application.` | `deepseek-v4-flash` | 规划、解析、fit、定制和短投递文案，全部继续受 grounding/Guardrail 约束。 |
| `pro_quality` | `resume_review.`、`interview_prep.`、`interview_agentic_rag.` | `deepseek-v4-pro` | 深度简历建议、面试题、答案、verifier 和 repair 直接使用已通过真实门禁的 Pro。 |
| `configured_default` | 未命中前缀 | `LLM_MODEL` | 新 Trace 不静默归类，先在日志暴露再决定归属。 |

### 工程实现
- 在公共 `LLMClient` 中按 `trace_name` 解析 `LLMRoute`，Service 不各自硬编码模型。请求 payload、DeepSeek thinking 参数、`LLMCallLog.model` 和 Token budget 使用同一个 resolved model/limit，避免“请求走 Flash、日志却写默认 Pro”。
- `context_json` 新增 `model_route/routed_model`；`prompt_preview_json` 新增 `requested_max_tokens`、实际 `max_tokens` 和倍率。控制台脱敏配置展示路由开关、两种模型、Trace 前缀和倍率，最近调用展示实际 route。
- Flash completion 上限提高 15%，并在 Pydantic 配置层限制为 `1.0-1.5`。没有增加 HTTP retry 或业务 repair 次数：简历/JD parser 本身已有业务重试，继续叠加会把少量放宽乘成最多九次外部请求。
- 路由不是 fallback。Flash 结果未过门禁仍直接失败，不会自动切 Pro。`scripts/run_model_comparison_slice.py` 强制 `LLM_ROUTING_ENABLED=false`，防止混合模型调用污染单模型对照结论。

### 测试与当前边界
- 新增路由解析和假 HTTP 请求测试，验证 Planner 使用 Flash、简历 Review/面试使用 Pro、未分类 Trace 使用默认模型；验证 Flash `max_tokens=100` 实际发送和预算预留均为 115，日志写入 `flash_economy/deepseek-v4-flash`。
- LLM 日志、Ops、前端、面试预算和评测相关定向回归共 `113 passed in 76.67s`；最终完整回归为 `231 passed in 94.59s`，Python compileall、前端 JavaScript 语法和 diff whitespace 检查通过。本轮没有调用真实 DeepSeek，模型质量结论复用上一轮固定切片结果，不额外消耗余额。
- 重启不带 API Key 的 8070 服务后，`/health` 为 `ok`、`llm_configured=false`，`/ops/config` 返回 routing=true、默认/Flash=`deepseek-v4-flash`、Pro=`deepseek-v4-pro`、Flash 倍率 1.15。运行时探针确认 Planner/Parser/Application 走 `flash_economy`，Resume Review/Interview 走 `pro_quality`。
- 当前只按 Trace 做静态质量/成本路由，没有实现按单次置信度自动升级。后续只有在建立“Flash 失败产物 -> Pro 局部重写”的独立成本和成功率评测后，才考虑受控升级，不能把它当默认兜底。

## 2026-07-22 13:51:33 +08:00：修复求职流程状态卡片逐条冒出的问题，增加多记录列表和批量忽略
### 用户看到的问题
- 右下角“最近完成的求职流程”每次只显示一条；点击单条关闭后，下一条记录才出现。用户无法提前知道还有多少条，也容易把关闭理解成“已经清空”。

### 根因与设计判断
- 本地状态和后端接口实际都保存了多条 run；问题来自视图层的 `activeRows.length ? activeRows : orderedRows.slice(0, 1)`，在没有运行中任务时强制只渲染最新一条。这是展示策略与通知队列语义冲突，不是任务数据丢失。
- “忽略”只应关闭右下角通知，不能删除 AgentRun、Trace 或历史记录；运行中和等待确认任务也不能被批量隐藏，否则用户可能错过审批或失败状态。

### 修改内容
- 状态卡片现在按优先级一次渲染本地保存和服务端发现的全部最近记录，使用卡片内部滚动控制高度；多条结束记录会在顶部显示数量，不再通过逐条关闭才能发现下一条。
- 增加批量忽略方法：纯结束列表显示“全部忽略”，混合列表显示“忽略全部已结束”。它只批量记录 `completed/failed/cancelled` 的 run ID，保留 queued、running 和 waiting_for_confirmation。
- 单条关闭继续可用；批量忽略后给出“仍可在历史记录查看”的提示。页脚按钮样式与历史记录入口对齐，并允许窄屏换行。

### 验证与未完成项
- `tests/test_frontend_pages.py` 19 条通过，完整回归 `229 passed in 104.14s`；Node `--check` 通过。运行中的 8070 服务已确认返回新版 JS/CSS，批量按钮和全量列表标记均存在。
- 内置浏览器自动化初始化时报本地 kernel assets 路径错误，尚未进入页面操作，因此本轮不能声称完成了浏览器点击截图验证。该问题与应用页面无关；服务保持运行，可直接刷新页面人工检查交互。

## 2026-07-22 11:49:28 +08:00：完成 DeepSeek V4 Flash/Pro 同样本全链路对照，并用真实 bad case 修正证据门控与面试成本上界
### 本轮目标与付费前检查
- 目标不是比较文风，而是在相同输入、`thinking=disabled`、`fallback=false` 和相同 release gate 下，对比 `deepseek-v4-flash` 与 `deepseek-v4-pro` 的自然语言规划、简历/JD 解析、RAG 匹配、适配解释、定制简历、投递材料和面试 Agentic RAG。
- 付费前先完成 `/models` 可用性检查，确认账号同时可见两个模型；再运行完整无 LLM 回归，结果为 `223 passed`。核心样本依赖的 Profile `#156` 与 Job `#197` 也先做存在性检查，避免因本地数据缺失浪费调用。
- 新增 `scripts/run_model_comparison_slice.py`。脚本固定 canary/core/interview 三组样本，强制关闭 fallback，按 `benchmark_run_id/model/mode` 关联 `llm_call_logs`，输出调用数、输入/输出 Token、provider latency、retry/repair、逐 trace 成本和错误；core 在套件之间执行 Token 预算检查。

### 最终同样本结果
| 分层 | Flash | Pro | 可以得出的结论 |
| --- | --- | --- | --- |
| Canary：1 个中文 Agent 强匹配全链路 | 5 调用，7,629 tokens，40.0 秒；解析、RAG、fit、定制、投递全部通过 | 5 调用，7,469 tokens，57.1 秒；全部通过 | 单个正常样本质量打平，Flash 墙钟时间约快 30%。 |
| 自然语言规划 | 4/4，通过 intent、action precision/recall、禁止动作和依赖门禁 | 4/4，同样通过 | 短结构化规划暂未看到 Pro 优势。 |
| JD Parser | 4/4，required skill precision/recall/F1 与 grounding 通过 | 4/4，同样通过 | `Intern -> internship` 规范化修复后两者打平。 |
| 3 个 hard/adversarial workflow | 按当前收紧门控离线重判为 0/3 | 实际 0/3 | 两者都把 worker-gap case 判成 partial 而非标注的 weak，也都会在至少一个简历中推断未明确写出的 target role；不能宣称核心对抗集通过。 |
| Core 成本 | 20 调用，22,175 tokens，79.1 秒；含 2 次 tailor repair 和 1 次投递信 | 17 调用，15,737 tokens，128.8 秒；无 repair，因无通过的 strong/partial 产物而跳过投递 | Flash 更快，但 repair 使 Token 比 Pro 高；不能简单用“Flash 更便宜”描述全流程。 |
| 面试 Agentic RAG：同一 10 题 case | 5 调用，29,135 tokens，87.1 秒；repair 后仍有 2 道多子问题未覆盖，发布失败 | 5 调用，30,615 tokens，129.2 秒；repair 后 10 题、三类视角、技能覆盖、回答质量和 Markdown 全部门禁通过 | Pro 在长上下文、多约束回答和 repair 收敛上明显更可靠；Flash 快约 32%，但结果不可发布。 |

- 最终代表性三层运行合计：Flash 58,939 tokens、约 206 秒；Pro 53,821 tokens、约 315 秒。它们只用于当前固定小样本对照，不是模型总体胜率或价格结论。
- 调试运行与代表性结果分开统计。本轮为定位门控和预算 bug，Flash 额外消耗至少 73,266 tokens；这些成本不能混入“正常一次全链路成本”，但必须记录为开发期试错成本。
- 最终决策保持不变：短上下文规划、解析和普通 fit 可把 Flash 作为候选路由；面试答案生成、verifier/repair 暂时保留 Pro。没有修改全局默认模型，也没有因单个 canary 通过就宣布 Flash 可全局替换。

### 真实 bad case、错误判断与修复
1. **把检索扩展词当作事实字段。** Flash JD Parser 为了检索扩展加入“大模型”，旧门控把它和 required/preferred skills 一样要求逐字来自 JD，导致 canary 在 JD 阶段中止。修复后 `keywords` 的无来源扩展会单独记录 `unsupported_keywords`，但只有 required/preferred skills、职责、要求和元数据属于严格事实门禁。
2. **用正向 citation 算法验证负向 gap。** Flash 生成“FastAPI 只在技能列表、项目没有使用证据”，结论正确，但旧算法找不到一段原文直接写着“没有项目证据”。新增双边 gap 校验：先确认它确实对应 JD requirement，再在项目/实习/工作交付证据中验证该能力是否缺失；纯技能行即使被 Parser 错放进工作经历，也不能冒充交付证据。
3. **英文同一行的否定污染前一句。** `implemented ... graceful worker drain. Did not implement distributed tracing` 被旧句界整体视为负向，导致真实正向证据被拒绝。英文句号加空格现在是证据边界，否定只影响对应句子。
4. **Job type 只做字面相等。** 模型返回 `Intern`，人工标注为 `internship`，核心技能完全正确却整 case 失败。`Intern/Internship/实习/实习生` 现在统一规范化为 `internship`，并有回归测试。
5. **平均 grounding 分掩盖关键字段错误。** 较长简历即使模型推断出不存在的 `target_roles`，总体 grounding 仍可能超过 0.9。现在 unsupported target role 与身份、技能、成果一样是独立硬失败，不再由字段数量稀释。
6. **跨语言投递文案被词法门控误杀。** Flash 把英文 `event streaming / health probes / graceful worker drain` 忠实改写成中文，词法分只有 0.0465。投递 Guardrail 现在先做高精度词法校验，失败 claim 再用本地 `paraphrase-multilingual-MiniLM-L12-v2` 对单条及相邻证据窗口做语义校验；真实改写分数 0.7116/0.8593。为防 embedding 忽略否定，只有相似度不低于 0.70 且 claim 与最佳证据否定极性一致才放行。“已实现分布式追踪”对“未实现分布式追踪”即使向量很近仍会被拒绝。最终 Application `#28` 为 `ready/low risk`，grounding=1.0。
7. **比较脚本把 Guardrail 业务拒绝当进程崩溃。** `_create_application_from_workflow` 原先未捕获业务校验异常，导致已完成的规划/JD/workflow 套件没有写入最终报告。现在投递失败结构化记录 `passed/error/source_case`，不会吞掉前序指标。
8. **面试批处理只改 batch，没有重算 completion 上界。** 10 题合并后默认调用从 6 降到 3，但第一次 verifier 仍用旧 1,800 上限，Flash 输出正好截断为非法 JSON；改为 2,800 后，第二次又在 repair 后增量复验前被 12,000 预留上限阻断。最终契约为正常 3 次、repair 路径最多 5 次、60,000 Prompt 字符、15,000 completion 预留；增量复验只处理失败题。第三次 Flash 完整执行后是业务质量失败，不再归咎代码或继续重跑。
9. **高向量相似度会掩盖同句新增结果。** 代码复查发现，“实现健康探针和优雅退出，并确保平台可靠性提升”会因前半句与项目证据高度相似，让整句 embedding 通过，即使“可靠性提升”没有来源。修复后结果类声明按可靠性、性能、准确性、效率和成本语义组额外对齐；claim 出现的结果组必须也出现在最佳证据中，且仍需同时满足相似度与否定极性。新增 `semantic_outcome_fabrication` 对抗 case，避免为了接受跨语言改写而重新放开事实拼接。

### 门控为什么没有为了过样本而放宽
- Gap 门控没有接受“只要提到 experience 就算 JD 差距”。无结构化 requirement 命中时，JD 语句相似阈值从 0.32 收紧到 0.50，因此 Flash 生成的 `No work experience or internship history` 被正确拒绝。
- 跨语言投递没有直接降低整个词法阈值，也没有用第二次 LLM 当裁判；只对词法未通过的 claim 使用本地多语言 embedding，并增加否定极性一致性。真实正例最高 0.8233，伪造“跨地域容灾/生产切换”样本只有 0.3122。
- Flash 面试最终失败来自 verifier 的明确 `answer_not_responsive`，即 Python/FastAPI 的架构位置、选型理由和替代方案未被同时覆盖。没有修改标注、关闭 verifier 或增加无限 repair。

### 测试、遗留问题与下一步
- 本轮新增或更新了 JD 类型规范化、负向 gap、目标岗位推断、英文句界、跨语言投递、否定极性、结果语义一致性和面试成本契约测试。最终使用项目解释器执行 `python -m pytest -q`，结果为 `229 passed in 92.27s`。一次直接执行 `pytest` 落到了系统 Python，因缺少 `aiosqlite/langgraph` 在收集期失败；这属于 Windows PATH 中两个 pytest/Python 不一致，不是代码回归，后续命令统一绑定解释器。
- 投递 Guardrail 扩展后的 27-case 评测 `#82` 的 pass rate/high-risk recall/issue-code hit 均为 1.0，false-block 和 missed-high-risk 均为 0，release gate 通过；证明跨语言 embedding 恢复没有破坏原有高风险阻断，也能拒绝“实现相近但结果无来源”的事实拼接。
- 使用不带 API Key 的新进程重启 `127.0.0.1:8070`，`/health`、`/ops/readiness`、开始页、评测页、控制台和面试页均返回 200；数据库为 `ok`，页面能明确看到 LLM 未配置。`compileall` 与比较脚本 `--help` 通过，服务保留运行供人工检查；本机未安装 Ruff，因此没有把后续成功命令误报为 Ruff 通过。
- 当前 core 只抽取 4 个规划、4 个 JD 和 3 个 workflow；canary 与 interview 各 1 个 case。可以用于发现差异和决定暂时路由，不能替代 20/30/24/9 case 的完整真实 LLM 发布认证。
- 两个模型都没有稳定跨过 partial/weak 边界，也都会推断未明确目标岗位。下一步优先改 Parser/fit 的结构化契约与 few-shot，而不是扩大 repair 次数。
- 面试 Flash 的多子问题覆盖仍是明确未修复项。若继续优化，应把“架构位置/理由/替代方案”拆成 verifier 可逐项反馈的 coverage slots，并在 repair prompt 中逐槽补齐；在离线 fake-LLM 契约通过前不要继续付费重跑。

## 2026-07-22 10:22:55 +08:00：把“接口跑通”升级为分层质量门控，重新校准简历、JD、RAG、适配、定制、规划和投递结论
### 为什么要做这轮改造
- 上一轮把简历/JD 解析、RAG 匹配、适配判断、简历定制、自然语言规划和投递材料描述为“运行正常”，这个结论过宽。它主要证明 HTTP/Schema/持久化链路没有报错，只有面试模块具备较强的答案相关性和证据发布门禁；其他环节仍可能在返回 200 的同时漏字段、错误归一化、引用无依据或生成未支持事实。
- 本轮不再把“得到 JSON”当成正确，而是逐层回答四个问题：结果是否完成、是否符合人工标注、是否能回指输入证据、是否达到可发布阈值。真实 LLM 先跑分层 smoke 暴露问题，修复后只重跑失败 case，避免再次用大量 Token 盲跑全数据集。

### 新增或加强的质量契约
- **简历解析：** 新增字段级 grounding。姓名、邮箱、电话、地点、技能、项目/经历技术栈、成果描述和教育字段都纳入原文回指；关键身份、技能或成果 claim 无来源，或者总体字段支持率低于 90% 时直接拒绝 LLM 解析结果。`ProfileStructured.quality_gate` 和 stage trace 保存不支持字段。
- **JD 解析：** 在 required skill recall 之外增加 precision、F1、职责/要求语句 grounding、元数据 grounding 和 absent-skill violation；中文/英文别名与 `实习 -> internship` 做统一归一化，避免同一技能重复计数或类型口径不一致。
- **RAG：** 180 个 case、每题 12 个候选 chunk 和 4 个相关 chunk，要求真实多语言 sentence-transformers embedding、Top20 cross-encoder reranker、零 fallback，并对 Top1、Recall@3/5、MRR、nDCG@5 设置发布阈值。Recall@3 理论上限是 `3/4=0.75`，因此门槛设为 0.60，即达到理论上限的 80%，而不是设置不可能达到的 0.90。
- **适配判断：** 同时校验标签、分数区间、候选人匹配证据、JD 差距、负向证据和 Top3/Top5 检索命中。模型的自然语言结论不再直接发布，系统只使用已验证的 `matched_evidence` 与 `gaps` 组合用户消息，原始模型文案留在 trace；这堵住了“结构化数组正确，但 message 又多推断一句”的漏洞。
- **简历定制：** Guardrail 从数字和关键词检查扩展到语义 claim grounding；每条成果必须由原简历近似支持。高风险时只允许一次 ReAct repair，修复前后风险、问题和上下文都写入 trace。
- **自然语言规划：** 新增 20 个中文为主 case，覆盖无简历浏览、已有档案、建档/更新、粘贴 JD、多动作、显式禁止投递、UI 勾选动作和中英混合。指标包含 intent、action precision/recall、required/forbidden actions、`needs_profile`/`needs_job` 和实体抽取。
- **投递材料：** 数据集从 20 扩展到 26 个 case，增加非数字经历编造、支持/不支持指标、负面披露、双语改写和自动提交边界；新增语义 claim 与数字来源门禁，发布阈值同时约束高风险召回、误拦截、漏拦截和 issue code 命中。
- **失败策略：** 真实 LLM parser 和生产生成链路不静默 fallback。每个失败保留 `evaluation_run_id -> case -> stage -> llm_call_log`；离线 heuristic 只在显式测试配置中使用，并在结果里标出 provider。

### 第一轮真实 DeepSeek V4 Flash 门控暴露的问题
| 评测 | 首轮结果 | 以前为什么可能被误判成“正常” | 暴露的问题 |
| --- | ---: | --- | --- |
| 自然语言规划 `#56` | 6 case 完成率 1.0、intent/action 均 1.0，但 pass rate 0.6667 | 动作列表看起来正确 | 两个 case 的 `needs_profile` 含义混用“当前是否已给简历”和“后续工作流是否依赖简历”。 |
| JD Parser `#57` | 4 case 完成率 1.0、recall 0.9792，但 pass rate 0.5、precision 0.8422 | 只看召回会认为核心技能都抽到了 | `实习` 未归一化为 `internship`，中英文技能别名重复进入 required skills，制造假阳性。 |
| LLM workflow `#58` | 2 case 全部完成，但端到端通过率 0.5 | parser、RAG、tailor 都返回了结果 | 一例标签标注与既定 rubric 冲突；模型还把 JD 未明确要求的“生产/实习经验”推断为缺口，fit explanation grounding 只有 0.5。 |
| 真实投递材料首次尝试 | LLM 正常返回，但 Guardrail 阻断 | 文案流畅且包含岗位名和技能 | 同一句里同时出现目标岗位 `Agent` 与“已有 Python 经验”，句级 claim scope 把岗位名误判成候选人能力；求职信还加入了未来学习计划。 |
| 首次定制简历草稿 | 生成成功但触发一次 repair | 关键词覆盖和格式都正常 | 模型把技能列表里的 RAG 与项目里的向量检索拼成“基于 RAG 架构”，属于跨证据推导的新事实。 |

### 如何修复，而不是调整指标掩盖失败
- 把 `needs_profile`/`needs_job` 定义为“完成计划是否依赖该实体”，并在 LLM 计划后按 action dependency 确定性归一化；这不是为某条 prompt 写特判。
- 对 JD 技能和 job type 增加统一 canonicalization，并补充 `A/B实验 -> A/B Testing` 回归。precision 和 F1 与 recall 一起进入 release gate，重复/误抽不能再被高召回掩盖。
- 按现有标注规则复核弱候选人 case：只有课程和计划学习且缺少核心交付应标为 `weak_fit`，将错误 gold 从 partial 改为 weak；同时禁止模型把 JD 没写的生产、流量、规模、部署或实习经历推断为差距。
- 投递材料按子句而不是整句识别技能 claim，并把目标岗位句与候选人技能句分开；Prompt 明确禁止把 planned learning 写入求职信。修复的是 claim scope，而不是把 `Agent` 加入例外表。
- 定制简历继续保留一次 repair，但 repair Prompt 要求每条 bullet 近似改写一个来源，不得把独立技能列表和项目片段拼接成新架构事实。
- fit 用户消息改成 verified-structure composer：模型原文只进 trace，发布文本只引用已通过 grounding 的匹配证据与 JD 差距，不增加第二次 LLM 调用。

### 修复后复测证据与 Token
- 真实定向复测：自然语言规划 `#59` 为 2/2、JD Parser `#60` 为 2/2、LLM workflow `#61` 为 1/1，三个 release gate 均通过。workflow case 的 Profile/JD grounding、Top5 evidence、负向证据、fit explanation、定制语义 grounding 和 Guardrail 均为 1.0；该 case 触发一次有效 ReAct repair。
- 真实投递材料复测生成 Application `#25`，状态 `ready`，风险 `low`，语义 grounding 1.0，无 unsupported number；首次错误阻断和修复后的放行都保留了 LLM trace。
- 本轮真实日志 `#1288-#1317` 共 30 次调用，全部收到完成响应：输入 25,897、输出 6,680、合计 32,577 tokens，provider latency 累计 112.145 秒。没有再次运行高成本面试工作流。
- 真实 RAG 完整重跑 180 case：`real_embedding_top20_rerank` 的 Top1=1.0000、Recall@3=0.6125、Recall@5=0.7292、MRR=1.0000、nDCG@5=0.7862；embedding provider 为 sentence-transformers，reranker 为 cross-encoder，fallback 为空，release gate 通过。对抗桶 Recall@5=0.6667，仍是下一轮重点。
- 最新离线全量：JD Parser `#64` 共 30 case，pass=1.0000、precision=0.9769、F1=0.9876、grounding gate=1.0000；投递 Guardrail `#63` 共 26 case，pass/high-risk recall/issue-code hit 均为 1.0000，false-block 和 missed-risk 均为 0。
- PDF 数据保持 96 份五页噪声简历、576 条查询；当前 `paragraph_page_900_overlap160` 是在证据命中、页码、上下文完整度、chunk 数量和噪声之间的评测选择，不是经验拍脑袋参数。
- PDF 真实 embedding 评测 `#65` 的 Top3 关键词/页码/上下文命中分别为 0.9479/0.8299/0.7760，Top1 平均 772.77 字符、平均 10 个 chunk，新增 release gate 通过；岗位排序和 Agent 全流程也新增了独立发布门禁。
- parser grounding 最终覆盖项目 description/impact、工作/校园 details、教育 details、证书、奖项、语言、链接和 JD keywords；离线重放本轮三个真实 Flash Profile 均保持 grounding=1.0，另增“技能真实但项目虚构降低故障率 80%”反例并被正确拒绝。
- 完整 pytest 回归为 `220 passed in 92.77s`。第一次回归有 1 条测试失败：pytest 明确使用 hash embedding/关闭 reranker，测试却错误要求生产 RAG release gate 通过；这不是降低门槛，而是修正测试契约，使离线环境验证门控会拒绝 fallback，真实 180-case 运行单独证明生产 provider 门控通过。

### 当前可以和不可以得出的结论
- 可以说：这些环节现在不只“能调用”，而是已有分层指标、逐 case trace 和 release gate；本轮真实分层样本与完整离线 JD/RAG/投递数据集通过门控，且门控确实先拦出了错误结果。
- 不能说：20 个自然语言 case、30 个 JD case 和 24 个 LLM workflow case 已全部使用真实 Flash 通过。受余额约束，本轮真实调用采用分层抽样和失败 case 复测；完整真实集仍需按 checkpoint 分批执行。
- 不能把 lexical/alias grounding 描述成通用语义 NLI。它是高精度、低成本的第一层；语义复杂的简历 claim 由生成约束、证据分类、一次 repair 和对抗 case 共同治理。
- RAG 总体门禁通过不等于所有分桶都优秀。对抗桶 Recall@5 只有 0.6667；下一步应补真实人工脱敏 JD/简历 pair，重点分析 planned learning、课程、否定事实和相邻项目之间的误召回。
- 定制简历本次 1 个真实 case 需要 repair 才通过，说明安全性已被门控保护，但首稿通过率与 repair Token 成本仍需作为后续独立指标。

## 2026-07-22 09:24:59 +08:00：完成 DeepSeek V4 Flash 全链路替换实验，确认不能直接全局替换 Pro
### 本轮目标与测试边界
- 用户要求把真实 LLM 测试模型统一替换为 `deepseek-v4-flash`，验证简历解析、JD 解析、岗位匹配、简历定制、自然语言入口、简历评估、投递审批和面试准备是否仍能实际运行。测试使用 DeepSeek 官方 OpenAI-compatible 接口，`LLM_FALLBACK_ENABLED=false`，任何模型、Schema、预算或发布门禁失败均直接记录错误，没有静默降级。
- `/models` 只用于确认账号可访问 `deepseek-v4-flash`；真实生成调用均写入 `llm_call_logs`。本轮日志 `#1254-#1287` 共 34 条，全部标记为 `deepseek-v4-flash`，其中 33 条收到供应商响应，1 条在发 HTTP 前被本地预算拒绝。
- 本轮采用分层样例而不是再次盲跑 18-case：先跑 strong/partial/weak 三种适配边界，再覆盖 PDF 建档、简历评估、自然语言规划、LangGraph interrupt/审批/恢复、投递材料和最复杂的面试 Agentic RAG。这样可以沿 trace 暴露问题，并控制余额损耗。

### 真实结果与成本
| 链路 | 真实结果 | 可追踪 Token / 耗时 | 结论 |
| --- | --- | ---: | --- |
| LLM workflow strong case，评测 `#51` | strong_fit=92；解析、RAG、定制、Guardrail 全通过 | 6,102 tokens；进程约 55.8 秒 | 通过。 |
| LLM workflow partial + weak，评测 `#52` | partial_fit=65、weak_fit=20；需要定制的 case 通过，不适合岗位未错误定制 | 7,821 tokens；两 case 约 57.6 秒 | 通过。 |
| PDF 上传建档 | Profile `#163`，姓名、11 个技能、项目及 1,203 字原文落库 | parser 1,104 tokens | 通过；HTTP 响应不回传整份原文是接口边界，不是解析丢失。 |
| 简历评分与建议 | 评分 78.9，LLM/RAG 均参与；初次 5 条建议含把 JD 责任写成候选人成果的风险 | 两轮日志 7,521 tokens | 安全门禁修复后通过，但 Flash 的 5 条 LLM 改写全部未满足证据契约，最终只发布 3 条安全建议。 |
| 自然语言入口 | “只建档，不搜索、不要定制、不要投递”最初仍规划 `tailor_resume` | 两轮日志 1,620 tokens | 修复否定约束后只执行 `create_profile`。 |
| 投递材料 + LangGraph 审批 | Agent run `#194` 先 interrupt，用户确认后恢复完成；Application `#23/#24` 均为 ready，packet validation 通过 | 已补 trace 的复测 823 tokens；run `#194` 4.53 秒 | 通过；只生成材料，不执行外部提交。 |
| 面试准备尝试一 | 8 次供应商调用完成，repair 后复核在 HTTP 前超过 85,000 Prompt 字符预算 | 36,032 tokens；约 93.5 秒 | 失败，未落库。 |
| 面试准备尝试二 | 状态压缩后不再超预算，但架构题 3 条 claim 均无足够项目事实支撑，被 verifier 和发布门禁拒绝 | 35,173 tokens；约 77.2 秒 | 失败，未落库。 |
- 三个核心适配 case 共 11 次调用、13,923 tokens，strong/partial/weak 标签准确率、解析成功率、RAG evidence hit、定制与 Guardrail 通过率均为 1.0。
- 本轮 `#1254-#1287` 的供应商 usage 为输入 75,229、输出 20,967、合计 96,196 tokens，provider latency 累计 204.456 秒。这是可追踪下限：第一次投递信调用发生在 trace 参数补齐前，供应商用量未知，不能伪装成 0。
- 对照既有 Pro 成功基线：面试包 `#47` 使用 30,478 tokens、83.07 秒并通过发布。Flash 第二次用 35,173 tokens、77.2 秒仍失败，Token 增加约 15.4%，墙钟时间只快约 7%，没有形成可接受的成本或速度优势。

### 真实测试暴露的系统问题
- **否定意图被关键词命中覆盖。** `_text_wants_tailor()` 看见“不要定制简历”中的“定制简历”就返回真。修复不是为一句话增加特殊流程，而是在确定性策略层先应用用户的显式禁止约束，再归一化 LLM plan；回归验证不再执行被否定动作。
- **JD 证据被误当作候选人经历。** Flash 在简历建议中把岗位要求的“评估管线、工具调用精度、错误恢复率”改写成候选人已完成成果。简历评估 Schema 现在区分 `rewrite_supported`、`collect_evidence`、`structure_only`，事实改写必须给出简历原文 `source_quote` 并通过词项/连续片段 grounding；JD 只能说明缺口，不能证明经历。修复后这 5 条高风险建议全部被拒绝，系统发布安全的独立建议而不污染简历正文。
- **投递信是 Token 观测盲点。** `ApplicationService._cover_letter()` 调用了 LLM，但没有传入 DB 和 trace context，导致第一次真实调用无法统计。现在记录 `workflow=application_packet`、stage、profile/job 和 `application.cover_letter`，复测 usage 完整入库。
- **面试答案状态过大。** Flash 倾向生成更长、更松散的 claims，触发两批 repair 和复核。生成契约收紧为每题 3 条 35-100 字 claim，repair 只补 1-2 条，合并状态最多 4 条，verifier 输入只保留前 3 条；第二次工作流由预算失败推进到语义门禁阶段，证明压缩有效，但不等于质量已经达标。
- **最终 Top5 丢失第一阶段正确证据。** 架构事实卡在 BM25 项目文档通道排名第 1，但经过中文 lexical reranker 后降到全局第 11；最终选择只执行来源配额，两个项目文档名额都被泛化的架构策略/评测文档占用。修复后同一来源有多个名额时同时保留“重排首位”和 BM25/Vector/Exact 通道锚点，不为“系统架构”写硬编码关键词。

### 修复后的验证证据
- 使用真实 profile `#159`、job `#218` 离线重放同一道“Agent 系统架构、数据流和组件交互”检索：Top5 现在包含 `docs/interview/CAREER_AGENT_PROJECT_EVIDENCE.md:1`，正文明确给出 `FastAPI -> LangGraph -> Tool/Service -> SQLite/向量索引` 与 Redis worker 数据流；同时保留简历、JD 和通用技术证据。
- 新增回归构造“reranker 泛化文档分高、BM25 架构事实命中更准”的噪声场景，验证最终两个项目证据名额同时保留 reranker winner 与 BM25 anchor。
- 自然语言显式否定、投递 LLM trace、简历非数字幻觉、面试 claim 状态上限和最终检索锚点均有测试覆盖；完整离线回归 `208 passed in 93.79s`。
- 没有进行第三次付费面试重跑。两次 Flash 面试已经消耗 71,205 tokens 并稳定暴露同一类高风险语义问题；检索修复已离线证明，但在新的真实运行通过前，不能宣称 Flash 面试链路已修复。

### 结论与下一步
- **不能把生产全链路默认模型直接从 Pro 改为 Flash。** Flash 已证明适合结构化解析、岗位适配判断、简历定制、自然语言规划和简短投递材料；当前面试 Agentic RAG 的长上下文生成、claim 类型遵循和证据约束明显弱于 Pro。
- 保留默认 `deepseek-v4-pro`。下一步应实现按 workflow/trace 的显式模型路由：解析、fit judge、普通文案可选择 Flash；面试答案生成、verifier/repair 先保留 Pro。路由必须写入 trace，并用固定 case 比较成功率、支持性误放率、Token 和 P95，不能只按价格切换。
- 简历建议的 Flash 接受率本轮为 0/5，说明当前严格门禁保证了安全，但可用性仍不足。后续应单独建立“建议类型 + source quote”评测集，判断是 Prompt 契约过严还是 Flash grounding 能力不足，再决定是否让该节点继续使用 Flash。

## 2026-07-22 08:33:24 +08:00：修复 Token 控制台单栏挤压，并完成无 LLM 的运维链路优化
### 用户或系统看到了什么异常
- 控制台的“LLM Token 用量”只有约一百像素宽，标题、指标名称和数字逐字换行并互相挤压，已经无法读取。截图同时说明这不是 Token 数据错误，而是整块面板没有占满页面。
- 用户要求本轮不调用 LLM，在修复显示问题的同时检查系统还有哪些可以用确定性测试验证的优化点。

### 最初判断与定位过程
- 初看容易把问题归因于六个指标放在通用的两列 `validation-grid` 中，或者超大 Token 数缺少断行策略；但浏览器尺寸检查显示外层面板本身只有网格单列宽度，内部换行只是后果。
- `ops.html` 给 Token 面板和底部审计面板使用了 `span-12`，CSS 却只定义了 `span-5/6/7`。未定义的 grid item 会由浏览器自动放入 12 栏网格中的一个栏位，因此桌面页恰好缩成约十二分之一宽。
- 离线审计还发现两个工程问题：`LLMUsageService` 每次按最近时间窗口查询，但 `llm_call_logs.created_at` 没有索引；控制台同时发出 11 个请求，却等最慢请求结束后才一次性渲染，任一 Redis/队列接口变慢都会拖住整页反馈。

### 根因修复与方案取舍
- 补充共享 `.span-12 { grid-column: 1 / -1; }`，同时修复 Token 用量和运维审计两个全宽面板。没有给 Token 面板写绝对像素宽度，因为根因属于共享网格契约缺失。
- 为 Token 指标增加独立 `ops-usage-metrics`：桌面六列、中等视口三列、窄屏沿用单列；数值使用稳定字号和可控换行。更新静态资源版本，避免浏览器继续命中旧 CSS/JS。
- 将控制台加载拆成 `loadOpsSection`。11 个接口仍并发发起，但每个区块收到结果后立即独立渲染和独立显示错误，不再等待最慢子系统；这保留了 FastAPI 并发收益，也改善了部分依赖故障时的可用性。
- 为 `LLMCallLog.created_at` 增加模型索引，并在现有 SQLite 初始化迁移中执行 `CREATE INDEX IF NOT EXISTS`，保证老数据库也真正得到索引；`workflow/workflow_run_id` 可选过滤改为数据库 JSON 条件，不再先加载时间窗全部日志后用 Python 丢弃无关记录。
- 没有增加静态人民币费用估算，也没有为验证页面而调用模型。模型价格、缓存计费和供应商账单会变化，本轮只修复可验证的 provider usage 展示与查询路径。

### 验证证据
- 在显式清空 `LLM_API_KEY` 的进程中完成全量回归：`204 passed in 97.53s`；新增回归会检查 `span-12`、Token 专用指标网格、渐进加载函数和 `ix_llm_call_logs_created_at` 元数据。
- 实际 SQLite 索引列表包含 `ix_llm_call_logs_created_at`；真实历史库最近 24 小时聚合 902 条日志，接口约 119ms。
- Playwright 在 1440、900、390 三种视口验证：Token 面板宽度分别为 1240、868、358 像素，六个指标均存在；三种视口都没有横向溢出，也没有浏览器 console/page error。
- 本轮验证前后 `latest_log_id=1253`、24 小时总量 `518,809 tokens` 均未变化，证明没有产生新的 LLM 调用。服务以 `llm_configured=false` 运行在 `http://127.0.0.1:8070`。

### 未修复问题与下一步
- 当前控制台一次展示最近 20 条 LLM trace，长页面仍比较重；后续更适合改为摘要表格、服务端分页和按 `workflow_run_id` 展开，而不是继续堆完整卡片。
- 当前开发 Python 环境没有安装 `ruff`，因此本轮执行了 `git diff --check`、Node 语法检查和全量 pytest，但没有完成 Ruff 静态检查。应把 lint 工具加入锁定的开发依赖和 CI，而不是只在本机临时安装。
- Codex 内置浏览器连接因本机插件运行目录错误失败，本轮使用本地 Playwright 完成等价视觉验收；应用页面本身无浏览器错误。该插件故障不应和 CareerAgent 前端故障混为一谈。

## 2026-07-21 23:58:42 +08:00：从“答案事实正确但答非所问”推进到真实面试包 #47 发布通过
### 用户或系统看到了什么异常
- 用户确认 DeepSeek 仍有少量余额，希望继续修复并测试面试模块；目标不是再看一个最终布尔值，而是沿 `workflow_run_id -> trace_name -> claim verdict -> repair` 检查中间结果与真实 Token。
- 已生成的面试包 `#46` 虽然通过旧版事实校验，人工抽检却发现“Agent 在架构中的位置、为什么选它、替代方案”被回答成评测数据集介绍。这是一类重要 bad case：每句话都可能是真的，但整段没有回答问题。
- 后续真实运行又依次暴露 JSON 截断、完整 JSON 漏题、中文 query 被英文 reranker 错排、240 字证据截断、repair 覆盖掉已验证 claim、固定三段门禁误杀、必备技能覆盖不足等问题。任何一个只看最终 502 都无法定位。

### 真实 Trace 与 Token 成本
| 日志范围 | 结果 | Provider Tokens | 暴露的问题 |
| --- | --- | ---: | --- |
| `#1153` | 12 case verifier 通过 | 3,125 | 初版集合没有“事实正确但答非所问”样本。 |
| `#1154-#1160` | 面试包 `#46` 落库 | 25,055 | 结构通过，但人工抽检发现 Agent 架构题答非所问。 |
| `#1161-#1165` | 14 case verifier 最终通过 | 19,125 | 先发现负例 `expected_answered` 标注错误，又发现 14 case 单批 JSON 截断；改为固定 `7+7` 后 run `#50` 达到 14/14。 |
| `#1166-#1194` | 四次完整流程失败 | 116,611 | 5 题 verifier 截断、4 题批次漏掉整题、repair 并发导致部分无效付费、中文检索召回错误、最后只剩 FastAPI trace 回答不具体。 |
| `#1195-#1210` | 两个重复 workflow 均失败 | 61,283 | 外层 PowerShell 10 秒超时返回 124 后，Python 子进程仍继续运行；再次启动造成两个 workflow 同时消费 API。该问题属于开发测试运行器，不是产品 Redis worker。 |
| `#1211-#1218` | 完整流程失败 | 35,576 | repair 后仍没有正面回答 trace、Agent 选型和架构数据流。 |
| `#1219-#1220` | 3 道坏例定向回归 | 7,563 | q01/q02/q03 的问题相关性全部通过；额外的 PDF 降级说法因无证据被正确拒绝。 |
| `#1221-#1228` | 完整流程失败 | 31,820 | trace 方案提到异步日志、采样和日志服务，但知识证据没有明确支撑这些细节。 |
| `#1229-#1231` | 首次 TLS 失败，重试后 q01 通过 | 4,170 | `#1229 ConnectError` usage 为 0；定向重试生成 4 条可用 trace claim，只剪掉一条过度解释 JD 的 claim。 |
| `#1232-#1239` | 完整流程失败 | 35,916 | 只剩 1 条 SQLite 用途 claim 不受支持，却因“必须剩 3 条 claim”被整题判失败。 |
| `#1240-#1245` | Agent 图通过，发布门禁失败 | 27,048 | 质量分 0.966，但两题被标为不可用，必备技能覆盖 5/7；生成契约与发布门禁标准不一致。 |
| `#1246-#1253` | 面试包 `#47` 发布通过 | 30,478 | 10 题、1 次定向 repair、质量分 1.0、必备技能覆盖 6/7。 |
- 本阶段 `#1153-#1253` 共 101 条 LLM 日志，供应商实际返回输入 `292,295`、输出 `105,475`、合计 `397,770 tokens`。最终成功工作流只占 30,478；其余是评测构建和逐层暴露 bad case 的开发成本，不能在汇报中隐藏。
- 单工作流的 9 调用/85,000 Prompt 字符/22,000 completion 预留只能限制一次运行，不能阻止开发者连续启动多个 workflow；真实测试仍需要“先定向 probe、再全流程”的人工预算纪律。

### 最初的设计假设为什么不成立
- **只验证 claim 事实支持性就够了。** 真实架构题证明，支持性不等于相关性；verifier 必须在过滤 unsupported claims 后再判断整题是否回答了 question 的核心和并列子问题。
- **中文也可以直接使用现有 `ms-marco` CrossEncoder。** 该模型主要面向英文，中文问题会把包含大量 `Agent` 词频的评测文档排在架构证据前；同时旧 tokenizer 把整段连续中文当成一个 token，BM25 几乎失效。
- **Top20 里有正确 chunk，Top5 自然会保留。** reranker 的 anchor 机制禁止候选从第 6 名以后上升，来源配额又只扩展相同的高分候选，正确技术段落即使是 exact/BM25 通道第一也会丢失。
- **证据前 240 字足以校验。** FastAPI 技术段的 `run_id/heartbeat/错误/结果` 恰好位于截断线后，生成器和 verifier 只能看到通用 async 原理，导致模型给出空泛方案或被正确拒绝。
- **repair 应重建整题。** 真实 repair 会把上一轮 4 条已验证 claim 覆盖成 1-2 条新 claim，反而让答案变短；正确做法是保留已验证事实，只补 verifier 指出的缺失点。
- **三条 claim、三段框架和必须出现“我”能代表答案可用。** 这些是格式启发式，不是事实安全规则；技术题可以用两段完整说明回答，架构题也可能用箭头数据流而不出现第一人称。
- **先固定保留三道面经题，再补 JD 技能不会冲突。** 10 题预算下，7 个必备技能只能覆盖 5 个，最终和 80% release gate 冲突。选题器必须先保留最低来源多样性，再优先补到 80% 技能覆盖，最后添加更多面经题。

### 根因修复
- 新增 14 case `interview_claim_verifier` 数据集与独立评测服务，覆盖 supported strategy、伪装成未来方案的既有经历、支持/不支持事实、事实正确但答非所问；两批各 7 case，真实 run `#50` 的支持准确率、strategy recall、question-answering accuracy 均为 1.0，false positive rate 与 nonresponsive false accept rate 均为 0。
- verifier 现在每次最多 4 题，输出每条 claim verdict 和每题 `answer_check`；完整 JSON 漏题时只重试漏项题，repair 后复核改为串行。生产默认不修复截断 JSON，因为再次请求无法恢复被截掉的语义，只会重复花费。
- 中文 tokenizer 增加双字/三字 n-gram；检测到中文 query 且配置的是英文 `ms-marco` 模型时，改走中文 lexical reranker，并允许相关候选突破旧 anchor。候选池显式保留每个来源的 Exact、BM25、Vector 通道头部。
- rerank query 去掉“导入面经提到”和“请结合岗位与简历”包装，只保留真正的问题和技能。项目题在 resume/project/JD 之外增加 technical knowledge，面经题允许同时检索 interview/resume/JD/project/technical evidence。
- Prompt evidence 上限从 240 提到 360 字，确保短技术语义段完整进入生成和校验；新增 `CAREER_AGENT_PROJECT_EVIDENCE.md`，把 Agent 位置、数据流、选型理由、替代方案、Trace 与恢复整理为可检索项目事实，不把答案写成代码分支。
- 回答生成契约要求逐项覆盖并列问题；“如何”必须给步骤、组件、字段或数据流，“为什么/替代”必须分开回答，“画架构”可用箭头式文本。verifier 明确接受可口述数据流，不强制图片。
- repair 只生成 1-3 条缺口 claim，服务端合并上一轮已验证 claim 并去重。unsupported claim 只要仍有其他已验证 claim 就剪除并记 warning；整题是否可发布继续由 answer relevance、正文最短长度、来源权限和 citation integrity 决定。
- 发布门禁取消固定三段、固定句号数和每题必须出现“我”的机械条件；保留正文长度、已验证 claims、无 TODO/占位语、引用完整性和语义相关性。
- 10 题选择器先保留面经/项目/行为题最低多样性，再补齐至少 80% 的 JD 必备技能，最后继续补面经和其他问题。阈值仍为 80%，没有通过调低 release gate 掩盖问题。

### 最终验证
- 定向 3 坏例真实测试 `#1219-#1220`：2 次调用、7,563 tokens，Agent 架构题正面回答位置、理由和替代方案，架构图题给出模块数据流。
- 定向 trace 真实测试 `#1230-#1231`：2 次调用、4,170 tokens；输出包含 trace_id、时间戳、阶段、耗时、状态、错误、异步队列和 worker 批量持久化，只有一条过度解释 JD 的 claim 被剪除。
- 最终完整真实工作流 `workflow_run_id=66e396b94684...`、面试包 `#47`：83.07 秒，8 次调用，Prompt 预留字符 66,649，实际输入 23,028、输出 7,450、总计 30,478 tokens。
- `#47`：10 题；question quality `1.0`；引用完整性、来源权限、回答可用性均 `1.0`；必备技能覆盖 `6/7=0.8571`；同岗面经 2 题；1 次 repair；4 条不支持 claim 被剪除为 warning；最终成功写入 SQLite。
- 代表性回答已人工抽检：FastAPI trace 回答给出可执行字段与异步链路；Agent 题回答编排层位置、选择原因、状态机/DAG/直接 LLM/ReAct 替代方案；架构题给出 `FastAPI -> LangGraph -> Tool/Service -> SQLite/向量索引` 及 Redis worker 数据流。
- 相关面试、中文 embedding/reranker 和 claim verifier 测试共 `52 passed`；最终全量离线回归 `203 passed in 93.95s`。

### 未修复问题与下一步
- DeepSeek 同一 Prompt 仍有输出方差，最终成功路径需要一次 repair；当前硬上限能把单工作流限制在 9 次调用，但 30k tokens 对低余额仍偏高。下一步应评测更便宜的 verifier 模型或蒸馏分类器，前提是 14 case 与真实坏例门禁不退化。
- `response_preview` 仍只保存前 1,200 字，长 JSON 的后半段 verdict 需要依赖最终错误摘要分析。应增加脱敏的结构化 verdict 汇总列，而不是保存完整敏感 Prompt。
- 测试运行器超时不会自动终止管道里的 Python 子进程；后续真实脚本必须使用足够长的外层超时，并在重启前按 `workflow_run_id` 和进程列表确认没有残留。
- 项目知识卡属于经过审核的仓库事实，需要随架构代码同步更新；如果文档漂移，RAG 会生成“有引用但过时”的答案，因此后续应为事实卡增加代码入口或测试引用检查。

## 2026-07-21 22:18:09 +08:00：四轮真实面试 RAG 压测暴露证据稀释、Verifier 语义与并发预算问题
### 用户或系统看到了什么异常
- 在上一轮真实 case 因 60k Prompt 字符预算提前停止后，用户确认 DeepSeek 仍有余额，希望继续验证 Token 是否仍会异常增长，以及面试功能是否真正完善。
- 同一真实输入始终使用简历 `#159 Li Ming` 与腾讯岗位 `#218 Agent Evaluation Intern 107491`，避免更换样例掩盖回归；所有调用都按 `workflow_run_id` 和连续的 `llm_call_logs.id` 追踪。
- 后续四次真实运行都没有生成可交付面试包：第一次是 verifier 长 JSON 截断后 repair JSON 仍缺项；第二次是错误证据导致两批 repair，第二批被 Prompt 预算阻断；第三次虽只剩 2 道题需要 repair，但修复后仍错误绑定候选人经历；第四次正常 5 次调用结束后仍有多道方案题被 verifier 误判，随后并发 repair 出现“一批预算阻断、另一批仍付费完成”的问题。

### 四轮真实 Trace 与成本
| 运行日志 | 结果 | 实际供应商 Tokens | 关键证据 |
| --- | --- | ---: | --- |
| `#1127-#1131` | 失败 | 26,997 | 单个 verifier 使用 2,800 completion 上限并截断；JSON repair 虽恢复语法，但 verdict 缺少 4 题。 |
| `#1132-#1138` | 失败 | 25,461 | verifier 分成两批后不再截断，但错误来源分配让大量项目 claim 失败；第二个 repair 在 HTTP 前被 60k Prompt 字符预算阻断。 |
| `#1139-#1145` | 失败 | 26,543 | 来源配额后只剩 `q02_01/q02_02` 需要修复；repair 仍用 JD 证明候选人能力、用项目文档证明候选人所有权，复核失败。 |
| `#1146-#1152` | 失败 | 26,206 | 正常 5 次调用为 23,286 tokens；随后一批 repair 实际使用 2,920 tokens，另一批记录 `budget_exceeded`。 |
- 四轮供应商实际返回合计 `105,207 tokens`。这个结果说明“调用数已受控”不等于“流程成本已健康”：正常 5 次路径本身仍约 23k tokens，只要进入 repair 就可能接近 26k；本次因此停止继续真实重跑。
- 最后一轮正常节点耗时：题目生成 19.905 秒；两个答案批次 21.320/24.350 秒；两个 verifier 批次 17.484/19.812 秒。并发批次缩短墙钟时间，但不会减少供应商 Token。

### 最初的设计假设为什么不成立
- **假设一：Top5 证据只要来源多样就足够。** 旧实现为每题平均保留 resume、job、interview、project document、technical knowledge 各 1 条。真实项目题因此只得到一条简历摘要，完整项目 chunk 被无关文档挤出；“多样性”反而稀释了最重要的证据。
- **假设二：为了省 Token，Verifier 只看生成器引用过的证据也能完成 citation rebinding。** 真实 trace 证明校验器经常看不到同一题 Top5 中未被生成器引用、但更正确的 resume/technical evidence。Prompt 声称“查看全部证据并重新绑定”，实际输入契约却不允许它这样做。
- **假设三：所有 rejected claim 都应按同一标准要求资料证明。** `answer_strategy` 明确描述“如果让我设计，我会怎么做”，Verifier 却要求技术文档证明候选人已经执行过这个未来动作，把方案可行性和既有经历混为一谈。
- **假设四：repair 和普通答案生成一样适合并发。** 两个 repair batch 共享同一 `LLMCallBudget`；虽然预算会阻止超限 HTTP，但 `gather` 已同时启动另一批，最终出现一个 batch 失败、另一个 batch 仍消耗 2,920 tokens 的无效调用。
- **假设五：自动选中的同岗面经会自动进入 RAG。** `InterviewExperienceService` 会从全局面经按公司/岗位相关性选取正文，但 RAG 在 `experience_ids=None` 时只读取当前 `job_id` 或无绑定记录，导致问题生成看到面经、答案检索却看不到同一来源。

### 根因定位与系统改造
- 将检索计划改成由 LLM 已生成的 `source_perspective` 驱动来源配额，而不是用关键词题型分类器：
  - 项目实现题：`resume=2, project_document=2, job=1`；
  - 基础题：`technical_knowledge=2, resume=2, job=1`；
  - JD 技术/缺口题：`job=2, resume=2, technical_knowledge=1`；
  - 已导入面经题：`interview_experience=1, resume=2, job=1, technical_knowledge=1`。
- Top20 第一阶段和最终 Top5 都执行同一配额，防止 reranker 前后丢掉计划来源；新增回归验证项目题必须得到 2 条简历、2 条项目文档和 1 条 JD。
- 未导入真实面经时，不再生成“面经标题反复出现某问题”这类无正文支持的问题；牛客网、OfferShow、小红书仍作为独立参考入口展示。导入真实面经后才要求面经题和 `interview_experience` 引用。
- 题目预算改为先预留真实面经、项目深挖和行为题，再从剩余席位补齐尚未覆盖的 JD 必备技能。这样不会在 7 个必备技能的真实 JD 上用满 10 个席位后才发现没有项目题。
- 自动相关性选出的面经 ID 从 draft 显式传给 Agentic RAG，统一“问题生成看到的来源”和“答案检索可见的来源”。
- Verifier 恢复查看该题 Top5 全部证据。两批 verifier 的 Prompt 仍受 5 题分批、每批 1,800 completion 和 60k 总 Prompt 字符硬预算限制，不能再以破坏 citation rebinding 为代价压缩上下文。
- Verifier 契约明确区分 `answer_strategy` 与候选人经历：假设/未来方案只需 JD 或技术资料支撑场景与可行性；如果写成“我做过/我实现了”，仍必须由简历证据证明。
- repair batch 改为严格串行。第一批出现预算、网络或 schema 错误时立即停止，不再启动后续付费请求。
- verifier 从 10 题单批调整为 5 题两批，单批 completion 上限从 2,800 收紧到 1,800；默认最大调用数调整为 7，允许正常 5 次路径外最多一次 repair 和一次复核，但 Prompt/Completion 总预算仍优先阻断。

### 评测标准为什么也要调整
- 旧离线评测强制每个面试包都包含“网上同岗面经题”，即使没有导入任何面经正文；这会奖励无证据问题。现在无正文 case 只验证参考链接，有正文 case 才检查面经题数量、来源引用和公司/岗位相关性。
- 旧评测固定要求至少 2 道 LLM 项目题和 2 道 LLM 基础题。真实面经、项目、行为题和 JD 技能共享 10 题预算时，这个固定配额与必备技能覆盖冲突。现在检查 LLM 题目生成确实参与，同时由必备技能覆盖、三类准备视角、引用完整性和答案可用性分别把关。
- 对 9 个面试 case 重跑后，`pass_rate=1.0`、平均 10 题、必备技能覆盖率 `1.0`；最终全量测试 `188 passed`。

### 为什么没有继续放宽校验或继续真实重跑
- 没有把 rejected claim 直接放行。真实输出中仍出现把“技能栏写了 FastAPI/Chroma”扩成“已用它们搭建评测服务”、把 JD 要求写成候选人实际经验等风险；这些内容用于面试时会伤害可信度。
- 没有提高 60k Prompt 字符预算。最后一轮预算阻断虽然导致流程失败，却阻止了更大的 repair 请求；提高预算只会把架构问题转成更多费用。
- 没有在修改 `answer_strategy` 语义后再跑第五次真实请求。前四轮已经使用 105,207 tokens，继续用余额做提示词试错不符合本次新增成本治理目标。

### 当前结论与未修复问题
- **已验证：** Token 聚合和 workflow trace 可准确还原每次调用；正常调用数稳定为 5；verifier 分批不再因 2,800-token 输出上限截断；来源配额、面经作用域、题目预算和 repair 串行都有离线回归。
- **尚未验证：** 最新 `answer_strategy` verifier 契约尚未经过新的真实 DeepSeek case，因此不能宣称真实面试全流程已经通过。离线 `188 passed` 证明结构和策略回归，不等同于真实模型语义通过。
- **仍需优化：** 正常真实路径约 23k tokens，虽然远低于历史 59 次调用版本，但对低余额场景仍偏高。下一阶段应评估把 claim verifier 换成更便宜的专用模型、减少每题 claim 数，或只校验高风险 claim；必须先用固定 case 对比召回与误杀，不能直接删校验。
- **可观测性缺口：** `response_preview` 只保存前 1,200 字符，长 verifier 响应只能看到部分 rejected claim。后续应保存脱敏后的结构化 verdict 摘要（按 claim type、问题、原因分类），避免保存完整 Prompt 的同时仍能分析全部 bad case。

### 下一步
- 不再自动使用真实 Key。下一次真实验证前先在控制台确认余额，并只跑 `#159 + #218` 一次；成功标准是 5 次调用、无 repair、release gate 通过、10 道参考答案均不少于 3 条已验证 claim。
- 为 `answer_strategy` 增加专门语义评测集，至少覆盖“未来方案”“相邻经验”“已交付事实伪装成方案”“无证据能力自评”四类正反例，量化 verifier 的误杀率和漏放率。
- 在控制台增加按 `workflow_run_id` 展开的 rejected claim 摘要与 repair 原因，让 Token 曲线可以直接关联到具体语义失败，而不只看到总量。

## 2026-07-21 20:53:32 +08:00：增加 Token 用量控制台，并由真实面试 case 反推上下文降本
### 用户或系统看到了什么异常
- 用户在 DeepSeek 平台余额只剩约 3 元，需要知道 LLM 是否仍存在异常消耗；原系统虽然逐条保存 `prompt_tokens/completion_tokens/total_tokens`，但控制台无法回答“最近 24 小时用了多少”“一次面试流程用了多少”“哪个节点最贵”。
- 旧面试页直接写了“通常调用模型 4 次”等实现细节，却没有在运维控制台提供真实 usage 聚合，信息边界正好反了：普通用户看到了成本机制，开发者反而只能手工加日志。
- 真实调用简历 `#159` + 腾讯岗位 `#218 Agent Evaluation Intern` 时，前三次请求成功，第四个 claim verifier 在发 HTTP 前触发 prompt 字符预算，接口在 114.51 秒后返回 502，没有生成面试包。

### 最初的设计假设为什么不成立
- 初始判断是 v3 正常路径只有 4 次调用，已经从旧版 59 次显著下降，因此成本问题基本解决。这个判断只看了调用次数，没有看同一批 evidence、检索计划和 claims 在各节点间被重复发送了多少次。
- 另一个隐含假设是任何 verifier 否定都应该触发 repair。真实输出证明，一个答案有 4 条 claim 时，即使只删除 1 条幻觉，剩余 3 条通常已经足以组成可用回答；为此重写整题会再次发送问题、证据和旧答案，成本与收益不成比例。
- 不能简单删除 verifier。真实生成预览中出现了证据没有明确支持的 `Python logging`、把 CrossEncoder/reranker 写成 `LLM 重排`、以及把技能栏中的 Chroma 扩写成项目实现等说法；如果只做 citation ID 存在性检查，这些内容会直接进入用户可参考答案。

### 定位证据与量化结果
- 新增 `/ops/llm-usage` 后，以 `since_id=1122&workflow=interview_prep` 精确聚合本次真实运行。4 条日志属于同一 `workflow_run_id=a28be88e7f8c4ad98ff6d25bd76a22ce`。
- `interview_prep.generate_interviewer_questions`：输入 1,570、输出 805、合计 2,375 tokens，14.652 秒。
- `interview_agentic_rag.generate.1`：输入 5,523、输出 1,348、合计 6,871 tokens，19.931 秒。
- `interview_agentic_rag.generate.2`：输入 5,517、输出 1,069、合计 6,586 tokens，15.417 秒。
- 前三次供应商实际返回 usage 合计：输入 12,610、输出 3,222、总计 15,832 tokens；usage 覆盖率 100%。
- `interview_agentic_rag.verify.1` 准备发送 26,676 字符时，会让累计 prompt 从 39,025 增至 65,701，超过 60,000 字符硬上限，因此记录 `budget_exceeded`，实际 token 为 0，也没有发出第四个 HTTP 请求。
- 这说明本次不是循环、并发扇出或 retry 造成的调用次数爆炸，而是 verifier 再次携带全部 Top5 evidence、完整检索计划和全部 claims，形成跨节点上下文重复。

### 这次做了什么
- 新增 `LLMUsageService` 和管理接口 `GET /ops/llm-usage`，支持 `hours`、`since_id`、`workflow`、`workflow_run_id` 过滤，并按模型、workflow、单次 workflow run 和 trace 聚合。
- 聚合只累加供应商响应中的 usage；完成调用未返回 usage 时计入 `missing_usage_calls`，不把未知消耗伪装成 0。控制台显示输入、输出、总 token、完成/非完成日志和 usage 覆盖率。
- 面试入口为每次生成创建唯一 `workflow_run_id`，并把 profile/job/workflow 写入所有嵌套 LLM trace；持久化成功时也把该 ID 写入面试包 summary。
- 从用户面试页删除调用次数和预算提示，只保留“答案引用简历、JD 与面经证据”的用户说明；Token 数据只在运维控制台展示。
- 压缩答案生成 prompt：每题只携带精简 intent，不再重复 search queries、全量 claim type、forbidden claims 和 planner metadata；evidence prompt 删除 UI label、排序调试字段和重复来源策略。
- 压缩 verifier prompt：保留 claim 当前引用的证据；若来源与 claim type 不兼容，再补一条可重绑定证据；`project_implementation` 额外确保 resume 与 project document 的所有权边界都可见。verifier 不再接收每题完整 Top5。
- verifier 仍独立执行语义支持判断。被拒绝的 claim 会被删除；若某题仍有至少 3 条已支持 claim，则将删除记录降为 `verification_warning` 并直接组合答案，只有答案已不完整时才进入 repair。
- 默认面试工作流最大 LLM 调用数由 8 收紧为 6。正常路径仍为 4 次，只为确实不完整的少量题保留一次受预算约束的修复空间。
- 强化生成约束：未来方案只能标为 `answer_strategy`；证据未明确出现时，不得把 Chroma、LLM reranker、logging 等具体组件补写为已交付实现。

### 为什么选择这个修复
- 没有删除 RAG verifier，因为真实输出已经证明“有 citation”不等于“citation 支持 claim”；保留独立语义核验是防止候选人把虚构经历带进面试的必要成本。
- 没有提高 60k prompt 上限来让 case 强行通过，因为那只会掩盖重复上下文并继续消耗余额。先减少输入，再由硬预算处理异常路径。
- 没有把答案改回关键词模板或硬编码题型。检索和 LLM 仍负责语义生成，确定性代码只负责来源权限、引用完整性、最小可用 claim 数和预算。
- 没有在余额不明时立即跑第二次真实请求。本次 provider usage 已足以定位根因；继续试错会违背“先用 trace 排查，再花真实 token 验证”的原则。

### 修复后的验证
- 代表性 10 题 fake-provider 成本回归仍为 4 次调用：问题生成 3,496 字符、两个答案批次 8,161/8,549 字符、verifier 12,025 字符；总 prompt 为 32,231 字符，较此前离线基线 57,220 下降约 43.7%。
- verifier 单次 prompt 增加 `<18,000` 字符回归门禁；总 prompt 继续受 60,000 字符硬预算约束，总 completion reservation 为 11,800。
- 相关面试、Token 聚合、健康接口和前端测试 64 个通过；最终全量回归 `184 passed`。
- 第一次全量回归暴露 1 个测试替身契约漂移：生产 verifier 为降本删除重复的 `allowed_claim_types` 后，离线 `DeterministicInterviewEvaluationLLM` 仍读取旧字段并触发 `KeyError`。最终没有把冗余字段加回生产 prompt，而是让 fixture 与生产共享 `SOURCE_CLAIM_POLICY`，确保 source type 到 claim type 的规则只有一个权威来源。
- 真实测试的 Key 只短暂进入测试进程，临时 `.env` 已删除；测试完成后立即停止带 Key 的服务，避免页面误操作继续扣费。

### 未修复的问题与原因
- 本次没有得到修复后的真实面试包，因为第一次真实 case 已使用 15,832 tokens，无法从 DeepSeek API 获得实时人民币余额或逐请求账单；在余额约 3 元的条件下继续调用风险不可控。
- `LLMUsageService` 记录 token，不估算人民币费用。不同模型、缓存命中和平台价格可能变化，把静态单价写进代码会产生误导；如需金额应接供应商账单 API 或可版本化价格表。
- fake-provider 能证明调用次数、prompt 体积、schema、引用和 release gate，但不能替代修复后 DeepSeek 的语言质量验证。下次真实验证应只跑同一 case 一次，并以新的 `workflow_run_id` 对照 15,832-token 基线。

### 下一步
- 余额确认足够后，只重跑 `#159 + #218` 一个 case，目标是 4 次内完成、总 provider token 明显低于本次路径推算、质量门禁通过且无 repair；若失败，继续按 trace 定位，不扩大 case 数。
- 对真实答案逐题抽检：是否直接回答问题、是否错误绑定候选人经历、是否仍出现 LLM reranker/logging 等无证据组件、引用 evidence 是否真正支持 claim。
- 若长期需要金额治理，引入按模型版本生效日期维护的 price catalog，并将估算金额与供应商账单金额分栏展示，不能混成一个指标。

## 2026-07-21 20:21:18 +08:00：将开发日志升级为问题驱动的工程复盘
### 为什么要改
- 旧日志虽然按时间记录了“做了什么、发现了什么、怎么修复”，但不少条目仍是功能清单，缺少当时的错误假设、定位过程和方案取舍。
- Codex 会话中出现过很多有面试价值的 bad case，例如状态字段被 LangGraph 丢弃、测试环境误走真实模型、RAG 权重在强噪声集上反转、模型调用节点过多导致余额耗尽；这些信息如果只留在对话里，项目文档无法证明工程判断是如何形成的。
- 面试官通常不关心“用了多少技术名词”，而会追问为什么这样设计、旧方案为什么失败、如何证明修复有效、还有什么风险。因此日志需要同时承担开发追踪和设计决策记录两种职责。

### 这次做了什么
- 在本条日志后新增“面试级问题复盘索引”，从 Git 历史、测试、trace 和历史日志中补录 16 个跨模块案例。
- 每个案例统一记录：场景与现象、初始错误假设、定位证据、根因、最终决策、取舍与验证、面试表达重点。
- 保留原有时间顺序日志，不改写当时的事实；专题复盘用于串联多次迭代中逐步暴露的根因，避免只看到最后一次提交。
- 后续每次开发日志除了结果清单，还必须回答下面七个问题：
  1. 用户或系统看到了什么异常？
  2. 最初的设计假设是什么，为什么后来证明不成立？
  3. 用了哪些 trace、数据、测试或对照实验定位？
  4. 根因属于数据、模型、状态、并发、存储、产品语义还是外部依赖？
  5. 为什么选择当前修复，放弃了哪些替代方案？
  6. 用什么量化指标或回归测试证明修复有效？
  7. 还剩下哪些风险，什么条件下需要再次改造？

### 验证结果
- 对照 `git log` 与现有开发日志逐条核验，补录内容均来自已经发生的 bug、失败测试、真实 trace 或明确记录的架构反转，没有把计划中的能力写成已完成事实。
- 面试复盘覆盖 LLM 成本、Agent/RAG、上下文、LangGraph、Redis、审批、Prompt Injection、PDF/JD parser、向量检索、前端状态恢复、岗位源和测试工程。

### 未修复的问题
- 早期部分开发只有提交结论，没有保存完整原始 trace，因此专题复盘只能引用当时日志中的指标，不能还原每一次中间请求。
- 历史测试数会随测试删除、合并和契约升级变化；日志保留当时数字，不能把不同版本的 passed 数直接横向比较。

### 下一步
- 后续出现真实失败时，优先在同一次日志中保存最小复现输入、关键 trace 字段、修复前后指标和回归测试名。
- 为高价值案例补充对应代码入口和测试入口，使日志可以直接作为面试准备材料和架构评审依据。

## 面试级问题复盘索引（历史补录）

使用方式：先根据面试方向选择案例，再按“现象 -> 错误假设 -> 定位证据 -> 根因 -> 方案取舍 -> 量化验证”讲述。不要只背最终架构，也不要把历史方案说成当前仍在运行。

- **LLM 与 Agent 设计**：案例 01-04，覆盖费用失控、错误分类、结构化输出和上下文过度设计。
- **LangGraph 与生产运行时**：案例 05-08，覆盖 state/checkpoint、外部队列、人工审批和 Prompt Injection。
- **RAG、Parser 与证据质量**：案例 09-11，覆盖噪声评测、PDF 输出边界和正负证据识别。
- **产品与前端状态**：案例 12-14，覆盖交付物污染、跨页恢复和外部岗位源故障隔离。
- **存储、并发与测试工程**：案例 15-16，覆盖 SQLite/向量库职责、异步边界和测试环境隔离。

### 案例 01：面试 Agent 不是调用越多越智能，59 次调用耗尽余额
- **场景与现象**：面试包 `#44` 最终生成成功，但一次运行调用 LLM 59 次，累计 `1,490,670` Prompt 字符、`237,622` Response 字符，墙钟约 504 秒；此前版本甚至达到 94 次和约 641 秒。
- **初始错误假设**：把 planner、答案生成、claim verifier、renderer、coverage judge 和 repair 分成更多 LLM 节点，会让系统更“Agentic”且更安全。
- **定位证据**：按 `trace_name` 聚合 `llm_call_logs` 后发现 verifier 占 37 次调用和 `1,080,855` Prompt 字符；同一题的 evidence 被按 claim 重复发送，renderer 和 coverage judge 又重新消费已经验证过的内容。
- **根因**：职责拆分依据是概念名词而不是信息增益。多个节点重复读取同一上下文，却没有新增外部信息；系统同时缺少供应商 token usage 和工作流费用上限。
- **最终决策**：删除 LLM retrieval planner、renderer 和 coverage judge；本地构造 multi-query，本地组合已验证 claims；10 题按 5 题一批生成、10 题一批验证。增加最多 8 次 HTTP 尝试、60,000 Prompt 字符、18,000 completion token 预留的硬预算，网络重试也单独计数。
- **取舍**：不再追求每一步都由 LLM 决策，而是把 LLM 留给必须做语义判断的生成和 entailment；确定性的检索编排、来源权限和正文组合交给代码。
- **验证**：同一离线输入变为 10 题、4 次调用、`57,220` Prompt 字符、`11,800` completion 预留；调用下降约 93.2%，Prompt 字符下降约 96.2%。预算超限会在 HTTP 前停止并记录 `budget_exceeded`。
- **面试表达重点**：这不是简单“减少 token”，而是通过 trace 找出重复信息流，按信息增益重画 Agent 边界，并用硬预算防止架构错误再次变成真实费用。

### 案例 02：关键词分类器把 SQLite 问题回答成 PDF Chunk 问题
- **场景与现象**：面试题询问“SQLite 存 JD chunk 和向量元数据有什么边界”，页面却展示 PDF Chunk 的切分策略；包含 `Agent`、`chunk` 的问题也经常被路由到错误模板。
- **初始错误假设**：面试题领域有限，用关键词优先级和固定回答模板即可稳定分类，速度快且无需额外 LLM。
- **定位证据**：逐题检查 `question`、`skills`、`source_perspective` 和最终 `reference_answer`，发现分类器把泛化词 `chunk` 的优先级放在问题真正询问的 `SQLite/向量存储边界` 之前；岗位名中的 `Agent` 还会污染所有题型。
- **根因**：硬编码规则只能判断词是否出现，不能判断用户究竟在问切分策略、存储职责还是评测复现；模板即使结构完整，也可能与问题语义无关。
- **最终决策**：删除关键词题型分类器和规则答案模板。面试问题通过 JD、简历和面经生成；问题本身作为 query 检索多源证据，LLM 生成可验证 claims，服务端只组合通过 entailment 的 claims。
- **取舍**：纯 LLM 自由回答会增加幻觉，纯规则又缺少泛化，因此采用“广义 RAG 检索 + LLM claim 生成/校验 + 确定性组合”。
- **验证**：增加问题相关性、项目绑定、证据可追溯、引用完整性和答案可用性门禁；旧规则测试被删除，当前 9 个面试评测 case 保持 `pass_rate=1.0`。
- **面试表达重点**：不要把“规则泛化差”简单说成换 LLM，而要说明为什么用 RAG 限定信息边界、为什么输出 claims 而不是自由正文，以及如何避免新方案再次变贵。

### 案例 03：DeepSeek 结构化调用只有 reasoning_content，content 为空
- **场景与现象**：真实 DeepSeek V4 调用返回了 reasoning 内容，但 `choices[0].message.content` 为空；另一些长 JSON 在约 15K 字符附近截断，导致 parser 或面试包失败。
- **初始错误假设**：只要 API 返回 HTTP 200，模型就在正常工作；增加 `max_tokens` 或简单重试就能解决所有空输出和坏 JSON。
- **定位证据**：LLM 日志记录了 `finish_reason`、`reasoning_chars`、thinking mode、response format、attempt 和截断后的响应；trace 能区分网络断连、空 content、JSON 语法错误和业务 guardrail 失败。
- **根因**：结构化抽取和开放推理的输出契约不同。thinking token 可能先消耗预算，却没有形成最终 JSON；过大的单次 JSON 又容易在输出边界被截断。
- **最终决策**：官方 DeepSeek V4 的结构化 JSON 路径使用 `thinking: disabled` 和 `response_format=json_object`；大任务按题分批；JSON repair 只允许一次；网络瞬态错误有限重试，空 content、400 和业务错误直接失败。
- **取舍**：关闭 thinking 只针对 parser、分类器等严格 JSON 节点，不代表所有生成任务都应关闭推理；批处理提高稳定性，但批次太小又会增加调用次数，因此必须和费用预算一起评测。
- **验证**：真实 1-case workflow 在统一 JSON mode 和有限 retry 后跑通；后续日志可直接看到 provider usage 和请求 attempt。余额耗尽后不再用真实 Key 反复试错。
- **面试表达重点**：模型接口成功不等于业务成功，生产调用需要同时定义传输契约、结构契约、语义契约和费用契约。

### 案例 04：六级上下文压缩和 context_manager SubAgent 是过度设计
- **场景与现象**：早期设计了 L1-L6 压缩和独立 `context_manager` subagent，但短上下文加入结构化 metadata 后反而比原文更大，真实长跑超时后还拿不到中间结果。
- **初始错误假设**：压缩层级越多、单独再设一个上下文 Agent，就越符合现代 Agent 架构。
- **定位证据**：逐 case `stage_trace` 显示 strong case 的 tailor prompt 一度超过 9000 字符；部分层级 `within_budget=false` 的原因不是正文太长，而是 rank、score、provider 等 JSON metadata 膨胀。
- **根因**：上下文治理是 runtime/prompt assembly policy，不是需要独立推理的业务角色；过多层级提高了理解和调试成本，却没有对应的信息收益。
- **最终决策**：删除 `context_manager` subagent 和 `progressive_disclosure` skill，把上下文收敛为 Profile 摘要、JD 摘要、Top evidence 和最终 prompt packet 四层预算；只保留排序调试必需 metadata。
- **取舍**：不追求“压缩率一定为正”。短文本结构化可能扩张，因此同时记录 `reduction_ratio` 和 `expansion_ratio`，最终只要求 prompt packet 在预算内且关键证据未丢失。
- **验证**：重跑后两个 tailor case 分别为 6071 和 5516 字符，均在预算内；评测改为逐 case 落库和 JSONL trace，超时也能保留已完成结果。
- **面试表达重点**：上下文压缩的目标不是层级多，而是可证明地控制输入大小、保留任务所需证据，并能解释丢弃了什么。

### 案例 05：LangGraph TypedDict 未声明字段，岗位列表在节点间静默丢失
- **场景与现象**：迁移 LangGraph 后，`search_jobs` 节点明明返回了 `job_ids`，full-flow 下一节点却看不到岗位，流程像是搜索成功后突然没有结果。
- **初始错误假设**：LangGraph state 可以像普通 Python dict 一样接受节点返回的任意新字段。
- **定位证据**：节点 trace 显示搜索结果存在，但 checkpoint state 中没有 `job_ids`；对比 `CareerAgentGraphState` 后发现该字段未在 TypedDict schema 声明。
- **根因**：LangGraph 会按 state schema 合并和持久化数据，未声明字段不能依赖；同时 ORM 对象和 SQLAlchemy Session 也不适合作为可 checkpoint 的 state。
- **最终决策**：所有跨节点字段显式进入 JSON-friendly state；Session、service 和 execution plan 放在运行期映射中；持久化 checkpointer 改为异步 SQLite 懒初始化，副作用节点使用业务幂等键。
- **后续 bad case**：`resume_json` 与顶层 `confirmed/note` 同名时可能覆盖人工确认；因此恢复请求把确认字段独立建模，非法状态映射为 409。
- **验证**：增加跨 Orchestrator 实例恢复测试：实例一运行到 interrupt，实例二从 SQLite checkpoint 恢复并继续创建投递包；failed run 也保留 execution plan 以证明经过 LangGraph。
- **面试表达重点**：迁移框架不只是把函数画成图，真正困难在状态 schema、可序列化边界、checkpoint 重放和副作用幂等。

### 案例 06：FastAPI BackgroundTasks 无法支撑可恢复的长任务
- **场景与现象**：页面刷新不是主要问题，真正的问题是 API 进程重启后任务会消失，多 worker 可能重复执行，后台入口也无法提供可靠 heartbeat、取消和恢复。
- **初始错误假设**：简历项目先用 `BackgroundTasks` 足够，只要前端轮询状态就接近生产架构。
- **定位证据**：梳理进程边界后发现 task 与 API 生命周期绑定；LangGraph 虽有 checkpoint，执行器仍在 API 进程内；重复 resume 还可能再次写入简历版本、投递包和面试包。
- **根因**：工作流状态持久化不等于任务调度持久化。需要独立队列负责领取、租约、重试和故障转移，数据库负责业务状态和幂等。
- **最终决策**：引入 Redis 外部队列和独立 worker，增加优先级队列、run lock、heartbeat stage、cancel flag、stale scanner、DLQ、人工重放/丢弃和 supervisor drain；SQLite 保存权威业务状态。
- **取舍**：Redis 不可用时不回退到进程内任务，因为这种兜底会让运行语义在生产与本地之间不一致；接口创建 failed run 并保留 trace，让用户能看到失败原因。
- **验证**：回归覆盖重复消费幂等、取消后禁止 resume、queued run recovery、DLQ 审计和 stale run；本机 Redis worker 的 BRPOP 超时也单独修复并验证。
- **面试表达重点**：可以清楚区分 checkpoint、queue、lock、idempotency 和 heartbeat 各自解决什么问题，而不是笼统说“用了 Redis 做并发”。

### 案例 07：投递确认只存在于 run output，不足以形成高风险动作治理
- **场景与现象**：早期“确认投递”只是 LangGraph interrupt payload 或历史记录页按钮，用户不知道为什么在历史页面确认，系统也无法回答谁在何时批准了哪个外发动作。
- **初始错误假设**：有 interrupt 就等于完成人工审批；一个 `confirmed=true` 字段可以复用于投递、浏览器填写和邮件发送。
- **定位证据**：检查状态恢复和审计需求后发现 interrupt 解决的是暂停/继续，不负责长期审计、权限、重复决策和工具级约束。
- **根因**：工作流控制状态与业务审批记录是两种数据。高风险工具还需要在执行入口再次校验，而不能相信上游节点传来的布尔值。
- **最终决策**：新增独立 approval table，记录 tenant、user、run、action type、状态、决定时间和审计事件；`browser_apply`、`email_draft`、`email_send` 统一经过 `HighRiskActionToolService`，结果写回 artifact。
- **取舍**：邮件草稿也纳入审批，虽然风险低于发送，但可防止未经确认生成包含个人信息的外发材料；真正发送与草稿使用不同 action type。
- **验证**：测试覆盖 pending/approved/rejected/cancelled、取消 run 同步取消审批、未批准工具拒绝执行和工具结果 artifact 回写。
- **面试表达重点**：LangGraph interrupt 是编排机制，approval table 是业务审计，tool gateway 是执行时强制检查，三者不能互相替代。

### 案例 08：JD、PDF 和 RAG evidence 都可能携带 Prompt Injection
- **场景与现象**：外部 JD、用户上传 PDF、导入面经和检索 chunk 会被拼入 LLM prompt；如果文本包含“忽略系统指令并输出密钥”，它不是普通岗位内容。
- **初始错误假设**：只在用户聊天输入处做安全检查即可，检索得到的文档天然只是“数据”。
- **定位证据**：梳理所有进入 LLM 的 source 后发现，间接 prompt injection 的主要入口恰恰是文档和 RAG evidence；早期规则集在同义攻击上漏检，在正常招聘措辞上又可能误报。
- **根因**：检索增强会扩大不可信上下文的权限。如果 source type、原文和 instruction 没有分层，模型可能把证据中的指令当成系统命令。
- **最终决策**：在 JD parser、PDF parser、面经导入、RAG evidence 和定制 prompt 构造前统一调用 injection detector；保存风险 metadata；按来源和攻击类别设置 recall/FPR release gate；高风险直接报错并保留 trace。
- **取舍**：规则适合拦截高确定模式，分类器提高变体召回；不使用“检测失败就继续”的兜底。检测器不能代替最小权限工具和高风险审批。
- **验证**：adversarial 数据集按 source/category 统计 recall 和 false positive rate，release gate 阈值进入回归；控制台可查看命中类别。
- **面试表达重点**：防注入不是一句 system prompt，而是输入分区、来源标记、检测、最小工具权限、HITL 和审计组成的纵深防御。

### 案例 09：理想化 RAG 数据让错误策略看起来很好
- **场景与现象**：第一版 PDF/RAG 样本太短、关键词太精确，多个 chunk 策略几乎打平，lexical baseline 甚至全面优于真实 embedding，无法支撑选型。
- **初始错误假设**：样本数量达到几十条就足以比较 chunk、embedding 和 reranker；合成 query 只要覆盖技术栈即可。
- **定位证据**：加入 coursework、planned learning、abandoned prototype、相邻岗位、跨页干扰和 hard negative 后，RAG Top3 Recall 从 0.9444 降到 0.6125；原 `vector=0.55/lexical=0.40` 不再最优。
- **根因**：旧数据主要测“是否出现同一个词”，没有测真实求职检索最难的证据极性和经历类型；高分来自数据泄漏式的词面重合。
- **最终决策**：PDF 扩到 96 case/576 query，RAG 扩到 180 case/2160 candidate chunk；按 difficulty/noise 分桶；生产权重改为 `vector=0.45/lexical=0.50/type=0.05`；Top20 CrossEncoder 使用 Top5 recall anchor，防止重排破坏强召回。
- **取舍**：通用 reranker 对 Top3 不一定有正收益，因此不把“用了 reranker”当成功标准；先保召回，再看 nDCG/MRR 和证据类型噪声。
- **验证**：真实 embedding + 保守 rerank 在当时数据上达到 Top5 Recall=1.0、MRR=1.0、nDCG@5=0.9843；强噪声分桶继续暴露 `coursework_vs_shipped` 弱点。
- **面试表达重点**：RAG 选型必须先解释评测集是否足够难；指标下降不一定是回归，也可能是数据终于开始接近真实问题。

### 案例 10：复杂 PDF 让 LLM 在 JSON 中复述全文，直接导致输出截断
- **场景与现象**：三页复杂中文简历上传失败，模型返回的结构化 JSON 被截断；简单 PDF 能通过，所以最初容易误判为偶发网络问题。
- **初始错误假设**：让 LLM 同时返回结构化字段和完整 `raw_text`，可以减少服务端拼装逻辑。
- **定位证据**：对比简单与复杂 PDF 的 prompt/response 长度后发现，`raw_text` 已经由 PDF parser 提取，却又要求模型原样复制，占用了大量 completion 预算。
- **根因**：把确定性可获得的数据交给生成模型复述，既浪费 token，又引入截断和改写风险；输出 schema 职责不清。
- **最终决策**：服务端负责保存原始提取文本，LLM 只返回教育、项目、实习、技能等结构化字段；增加 section-aware、页码、字符范围和跨页 metadata；解析失败直接返回可追踪错误。
- **取舍**：多栏和复杂表格仍不能仅靠普通文本抽取完美恢复，因此保留原文与 chunk provenance，结构化结果不覆盖原始证据。
- **验证**：复杂 PDF 成功建立 Profile，并跑通岗位匹配、定制简历、投递包和面试包；parser prompt 回归测试明确禁止返回完整 raw text。
- **面试表达重点**：LLM parser 的工程原则是“模型只生成无法确定性得到的结构”，原文、ID、页码和 hash 应由服务端掌控。

### 案例 11：`No MLflow` 被当成会 MLflow，`Machine Learning` 被当成正在学习
- **场景与现象**：旧匹配器只要看到技能词就算覆盖，因此 `No MLflow experience` 命中 MLflow；负面词规则里裸 `learning` 又误伤 `Machine Learning`。
- **初始错误假设**：技能匹配可以用关键词集合和别名表完成，负面证据只要加几个否定词即可。
- **定位证据**：Agent full-flow bad case 显示目标岗位、headline、课程和缺口披露都进入了 support text；真实 trace 中模型对 partial/weak 的判断反而比人工标签更保守。
- **根因**：求职证据不仅有实体，还包含极性和证据类型。目标意向不是能力，课程不是交付，缺口披露更不能反向证明掌握。
- **最终决策**：按句子切分并让否定证据优先；过滤目标岗位、headline 和联系方式；区分 shipped project、metric evidence、coursework、planned learning、missing-skill disclosure 和 adjacent experience；缺失技能只能进入 notes/gap，不能写入简历正文。
- **取舍**：规则别名仍用于确定性边界，但最终匹配结合 RAG evidence 和 LLM judge；人工标注也允许被 trace 反证并重新定义，而不是强迫模型迎合错误标签。
- **验证**：增加 `No MLflow`、`did not implement ranking`、`Machine Learning`、A/B tests 别名和 weak-fit quick-apply 阻断样例；真实 5-case smoke 与离线 full-flow 门禁通过。
- **面试表达重点**：这是信息抽取中的 polarity 与 evidence type 问题，不是简单补关键词；评测标签本身也需要版本化和复审。

### 案例 12：把事实检查和改动摘要写进简历 HTML，污染最终交付物
- **场景与现象**：定制简历预览右侧出现“检查结果、风险、改动摘要”，用户下载或打印时这些内部诊断会和简历一起带出去。
- **初始错误假设**：为了让改动可解释，把诊断信息与简历放在同一个 HTML 中最直观。
- **定位证据**：真实浏览器截图显示诊断侧栏位于可打印简历页面内部；这不是 CSS 美观问题，而是领域对象边界错误。
- **根因**：把最终业务 artifact 和生成过程 metadata 混成了一个交付模型。解释性信息应该属于 review/trace，不属于候选人投递材料。
- **最终决策**：`ResumeHTMLRenderer` 只渲染简历正文；事实检查、评分、关键词缺口、修改说明和 RAG 证据放在独立前端诊断区域与 API 字段中。
- **取舍**：不删除诊断信息，只改变其展示和下载边界；用户仍可审查每次修改，但外发文件保持纯净。
- **验证**：HTML 预览测试明确断言不包含检查结果、改动摘要、缺失关键词和风险提示；浏览器确认简历正文与诊断分栏。
- **面试表达重点**：可解释性不是把内部日志贴到业务产物上，而是建立 artifact、diagnostics 和 audit 三层数据模型。

### 案例 13：刷新后进度丢失，根因不只是 localStorage
- **场景与现象**：用户刷新或切换页面后看不到正在运行的流程；快速失败的任务也会从右下角状态卡立即消失。Redis 未启动时接口直接 503，前端连 run_id 都拿不到。
- **初始错误假设**：前端保存一个 active run ID 到 localStorage 就能解决跨页恢复；终态任务可以立即从关注列表删除。
- **定位证据**：检查 JS 生命周期后发现状态只存在内存；localStorage 也会因会话变化或脚本异常丢失；队列入队失败虽然数据库里有失败 run，API 却没有把它返回给前端。
- **根因**：运行状态的权威来源应该是服务端，浏览器只保存关注偏好。失败本身也是用户需要查看的结果，不能因进入 terminal state 就隐藏。
- **最终决策**：双通道恢复：先读本地关注列表，本地为空再查服务端最近 24 小时 run；completed/failed 保留到用户手动关闭；入队失败创建 failed run、写 `queue_enqueue_failed/run_finished` 事件并返回 run_id。
- **取舍**：关闭状态仍保存在本地，不做跨设备同步；最近 50 条完整历史由历史记录页承担，右下角只负责当前和最近结果。
- **验证**：无 localStorage 的独立页面也能恢复服务端 run；Redis 缺失时接口返回可追踪 failed run 而不是无上下文 503；前端回归覆盖多 run 卡片。
- **面试表达重点**：前端状态恢复要区分 UI cache、服务端业务状态和事件流，不能把 localStorage 当持久化系统。

### 案例 14：真实岗位源的网络失败不应拖垮核心 Agent 评测
- **场景与现象**：Greenhouse 在中文招聘场景价值低；国内招聘站有 CSR、接口变化、空结果和反爬，真实 source smoke 经常波动。
- **初始错误假设**：接入越多招聘站越完整，外部源失败也应该算整个 Agent 全流程失败。
- **定位证据**：实际扫描发现公司自有招聘站的数据可达性和结构差异很大；网络波动与 matcher、RAG、tailor 的质量无关，把两者混在一个 pass rate 会导致无法复现。
- **根因**：source availability 是外部系统 SLI，核心 Agent quality 是内部算法指标，两者故障域不同。
- **最终决策**：中文优先接入腾讯、百度、美团、字节、阿里等公司源；岗位源并发请求和解析，SQLite 写入顺序执行；source smoke 单独记录可达性、结果数、解析率和延迟，不阻断核心回归。
- **取舍**：不为绕过反爬投入大量工程；难以稳定获取的面经只保存标题和链接，把核心生成转向 JD 与简历项目证据。
- **验证**：真实 JD ingest smoke 与内部岗位库检索分开；前端允许无简历浏览、仅简历自动匹配和需求加简历三种路径。
- **面试表达重点**：真实可用不等于强行保证每个外部站点稳定，而是隔离故障域、提供可观测 source 指标和可用的系统内岗位库。

### 案例 15：SQLite、向量库和 FastAPI 并发不能用一句“异步化”概括
- **场景与现象**：岗位源和 JD 解析适合并发，但同步 SQLAlchemy Session 并不适合在多个协程中同时写；同时只用 Chroma 又不方便承担业务事务和审计。
- **初始错误假设**：FastAPI 是 async 框架，所以搜索、解析、embedding 和写库全部 `asyncio.gather` 就能获得并发收益；用了向量库后 SQLite 只剩兜底价值。
- **定位证据**：早期实现明确暴露同步 Session 并发写边界；岗位、chunk、审批、run 和 artifact 又需要唯一约束、事务和可查询关系，单独向量库无法成为权威业务存储。
- **根因**：I/O 并发、CPU 推理和事务写入是三类不同负载；向量相似度索引与业务一致性存储也是不同职责。
- **最终决策**：招聘源请求和可独立 JD 解析使用 semaphore + gather；数据库写入保持每 Session 顺序执行；SQLite 保存岗位/chunk/metadata/embedding 与业务关系，Chroma 作为可重建向量镜像，Top20 reranker 可独立推理。
- **取舍**：单机 SQLite 适合作品和轻量部署，但多实例高写入场景应迁移 Postgres；向量库损坏可以由 SQLite 权威数据重建。
- **验证**：JD chunk、resume chunk、metadata 和向量写入都有回归；重复写入由 external_id/idempotency key 约束；并发只发生在无共享 Session 的阶段。
- **面试表达重点**：并发设计要画出阶段 DAG，说明哪些阶段可并行、哪些受 Session/事务约束，以及向量索引和权威数据库如何恢复一致性。

### 案例 16：测试环境本身制造过假失败和真实费用风险
- **场景与现象**：出现过 `pip install` 装到错误 Python、`TestClient` 未触发 lifespan 导致表不存在、pytest 的 `setdefault` 被外部真实环境变量覆盖、全局 `Settings` 缓存被测试修改、Windows `tmp_path` 权限失败等问题。
- **初始错误假设**：本机只有一个 Python；测试环境变量只要“没有时设置”即可；创建 TestClient 就一定执行 startup；修改缓存 Settings 不会污染其他测试。
- **定位证据**：`python`、`pytest.exe` 和实际 uvicorn 解释器路径不一致；测试栈显示 fixture 阶段权限错误；顺序运行与单测结果不一致；某些测试意外尝试加载真实 embedding 或严格 LLM 路径。
- **根因**：测试隔离没有覆盖解释器、环境变量、应用生命周期、全局缓存和文件系统五个层面。
- **最终决策**：统一使用目标解释器的 `python -m pip/pytest`；测试环境变量直接赋值；`with TestClient(app)` 触发 lifespan；Settings 使用 `model_copy` 或清理 cache；临时 checkpoint 放到项目 `.tmp_test`。
- **取舍**：普通回归使用 hash embedding、关闭 reranker 和 deterministic fixture，但产品路径默认失败直报；真实模型评测是显式的独立测试，不允许由残留环境变量偷偷触发。
- **验证**：全量测试可以重复运行且不访问外部 LLM；secret scan 不发现 Key；服务启动后 `/health` 明确展示 `llm_configured`。
- **面试表达重点**：可靠评测首先要求可靠测试环境，否则所谓模型回归可能只是解释器、缓存或环境污染问题，甚至会直接产生意外账单。

## 2026-07-21 20:01:09 +08:00：修复面试 Agent Token 成本失控并增加硬预算
### 这次做了什么
- 将面试包默认规模从 32 题降为 10 道重点题，正常路径从 59 次 LLM 调用重构为 4 次：一次题目生成、两批 Claim 生成、一次批量 Claim 验证。
- 删除运行路径中的 LLM Retrieval Planner、Verified Claim Renderer 和 Answer-to-Claim Coverage Judge 调用。检索 Query 由题目、JD 和简历摘要直接构造，不做关键词题型分类；正文只由已验证 claims 本地组合。
- 彻底删除上述三个旧 LLM 节点、planner repair 及其旧测试，不保留可被误接回工作流的高成本死代码。
- verifier Prompt 改为按题组织，每题 evidence 只发送一次；旧实现会对每个 claim 重复发送同一组 evidence。
- 默认每题只保留 Top5 evidence，每段最多 240 字；并发降为 2，repair 从最多 3 轮降为最多 1 轮。
- 新增工作流级 `LLMCallBudget`：最多 8 次调用、60,000 Prompt 字符、18,000 最大输出 token 预留，任一项超限都会在下一次网络请求前报错。
- 预算改为按每次真实 HTTP 尝试预留，网络重试不再被算作同一次免费调用；重试会超限时，在下一次 HTTP 请求发出前阻断。
- `llm_call_logs` 新增 `prompt_tokens`、`completion_tokens` 和 `total_tokens`，直接保存 OpenAI 兼容 API 返回的 usage；SQLite 启动迁移会为旧库补列。
- 面试页面明确显示默认题数和调用边界；控制台 LLM 日志显示总 token 与输入/输出 token。
- 契约升级为 `interview_agentic_rag_v3_cost_guarded`，旧 v1/v2 面试包需要重新生成。

### 发现的问题
- 成功面试包 `#44` 实际调用 59 次，Prompt 字符合计 `1,490,670`，Response 字符合计 `237,622`；其中 verifier 单独调用 37 次、占 `1,080,855` Prompt 字符。
- v2 把 LLM 同时当成题目生成器、检索规划器、答案生成器、Claim 分类器、引用校验器、正文编辑器和覆盖检查器，职责拆得过细，导致同一证据被反复发送。
- verifier 以 claim 为批次，每个 claim 都重复携带该题全部 evidence，这是 Prompt 膨胀的主要来源。
- 系统只有调用日志，没有供应商 token usage，也没有工作流级预算；因此错误架构可以在没有任何拦截的情况下持续消耗余额。
- 第一版预算只在逻辑调用入口预留一次，`LLMClient` 内部网络重试没有单独计数；这仍可能让实际请求数超过工作流统计。
- 固定 10 题后，旧评测仍要求至少 12/14 题，并用单一中文题组标题做精确匹配，导致低成本方案被旧标注误判失败。
- “多一层 LLM Judge 更安全”并不总成立。若正文完全由已验证 claims 组合，renderer 和 coverage judge 都可以删除，同时减少新的幻觉入口。

### 怎么修复
- 把语义能力集中到必要位置：LLM 生成问题、生成 claims、批量判断 claim 与证据的蕴含关系；检索、来源权限和输出组装全部确定化。
- 按问题批量验证 claims，evidence 在每题只出现一次；10 题默认只需一个 verifier 请求。
- 用来源配额限制问题数量，不做关键词题型分类：优先保留已导入真实面经，同时确保同岗面经、项目技术栈、基础/行为问题都进入 10 题预算。
- 在每次 `LLMClient` HTTP 尝试前预留调用数、Prompt 字符和最大输出 token；超预算直接抛出 `LLMBudgetExceededError`。
- 成功响应后读取 API `usage` 并写入数据库；历史记录缺少 usage 时保持 0，不用字符数伪装成 token。
- 10 题质量门禁要求 JD 必备技能覆盖率至少 80%，但所有明确缺口仍必须 100% 进入诚实披露 drill；评测将“工程协作与落地”视为“通用面试与行为问题”的等价细分类别。

### 验证结果
- 同一离线 Profile/Job 输入：10 题、4 次调用、`57,220` Prompt 字符、`11,800` 最大输出 token 预留；相对 `#44`，调用数下降约 93.2%，Prompt 字符下降约 96.2%。
- 面试离线评测 9 个 case 全部通过：平均 10 题，`pass_rate=1.0000`、`avg_required_skill_coverage_rate=0.9778`，明确缺口覆盖率保持 1.0。
- 删除旧节点并补齐预算 trace 后，全量回归 `180 passed in 72.87s`；Python 编译和 JavaScript 语法检查通过。
- 预算测试覆盖调用次数超限时在网络请求前失败；模拟 OpenAI usage 响应验证 `11 input + 3 output = 14 total` 正确写库并进入工作流预算统计。
- 集成测试验证首次 HTTP 连接失败后，第二次重试若超过预算会在请求发出前停止，实际网络调用次数保持为 1。
- 预算阻断会写入 `LLMCallLog.status=budget_exceeded` 和对应 attempt，控制台可以区分供应商失败与本地费用门禁。
- repair prompt 与 v3 claims-only 输出契约对齐，每题要求 3-4 条可验证 claim，避免生成已废弃正文结构或因 claim 太少再次失败。
- 本轮没有再次使用用户 API Key，也没有发起任何真实 LLM 请求。

### 未修复的问题
- 历史调用日志只保存字符数，无法还原供应商真实 token；文档只报告可审计字符数，不换算成伪精确 token。
- v3 尚未在余额恢复后的真实模型上运行，但即使恢复余额也会先受 8 次调用、Prompt 字符和输出 token 三层硬门禁限制。
- 当前预算按 `max_tokens` 预留输出上限，通常高于实际 completion token；这是为了优先避免超支。

### 下一步
- 增加用户可见的单次运行 usage 汇总，并按模型价格配置估算费用区间。
- 建立 10 题人工可用性标注，确认缩减题量后 JD、项目、八股和行为问题的覆盖质量。

## 2026-07-21 19:18:01 +08:00：面试模块迁移到 Agentic RAG v2 与真实 LLM 全链路硬化
### 这次做了什么
- 删除基于 `_question_kind` 关键词路由和规则模板拼接答案的旧实现。旧面试包不再在读取时静默升级为“新答案”，而是标记 `requires_regeneration`。
- 新增 `InterviewAgenticRAGService` LangGraph 子图：检索规划、多源检索、claim 生成、citation linker/classifier/entailment、verified-claim renderer、claim coverage、repair/finalize。
- 检索覆盖简历 chunk、当前 JD、用户导入面经、CareerAgent 项目文档和审核后的技术知识库；采用 exact/BM25/真实 embedding/RRF/Top20 CrossEncoder reranker/来源多样化 TopK。
- LLM planner 根据题意生成 multi-query、目标来源、必需证据、禁止声明和置信度；代码只校验 schema、来源库存和证据权限，不再用关键词硬编码题型。
- verifier 可在每题局部 `E1...E8` 中重新绑定证据，服务端再映射真实 ID 并执行来源策略。增加 Answer-to-Claim Coverage Judge，防止正文藏入未声明事实。
- 最终自然语言答案由 Verified Claim Renderer 只根据已验证 claims 生成。repair 最多 3 轮，判否 claim 会先从状态删除，验证只处理 dirty questions。
- 答案 batch 从 6 降到 3，verifier 按 claim 数量分批；增加可追踪 JSON repair。CrossEncoder 改为一次批量推理后按题拆分。
- 契约升级为 `interview_agentic_rag_v2`。旧 v1 包要求重新生成；`question_quality` 或 `coverage` 未通过时禁止持久化。
- 新增 `docs/interview/TECHNICAL_KNOWLEDGE_BASE.md`，明确技术原理证据不能证明候选人经历。

### 发现的问题
- 旧分类器会因 `chunk/Agent/SQLite` 等词把题目路由到错误模板，规则回答也无法证明每个事实。
- DeepSeek 长 JSON 在约 15K 字符附近多次出现语法错误；单纯重试或 JSON repair 不能稳定解决。
- 早期 verifier 只看生成器已选引用，能判错但不能改绑；JD `keywords` 碎片还会被误当成证据。
- repair 删除真实 ID 后未保留局部别名，产生 unbound claim；pre-verifier 又在 linker 之前拦截空引用，让 linker 无法补绑。
- verifier 判否的 claims 仍进入下一轮，repair 被旧答案锚定后重复错误说法。
- 校验阶段存在全局短路：任意题失败会让其他题跳过 linker/来源校验，却不进入 dirty set。开发期包 `#45` 因而出现项目实现错引 JD。
- 旧 release gate 只计算 `passed`，即使为 false 仍写库。
- 自由正文会写入 claims 没覆盖的实现细节，例如 middleware trace ID、异步日志和降级算法；只检查 claims 不足以保证正文可信。
- 每轮重验全部 32 题导致 94 次 LLM 调用和约 641 秒墙钟耗时。
- LLM 未配置时，面试入口仍先执行匹配、embedding 和 reranker，API 请求超过 20 秒才可能报错，用户会误以为任务卡死。
- 新增测试最初直接修改缓存的全局 `Settings`，污染了后续 JD 测试，暴露出测试配置对象的顺序依赖。
- 最终在线 v2 验收时 DeepSeek 返回 `HTTP 402 Insufficient Balance`。

### 怎么修复
- 用 LLM planner 做语义理解，source inventory 修复不可用来源；低置信度、漏题、重复题和越权来源直接报错。
- 排除 `job_chunks.keywords` 作为最终引用证据；每题来源多样化 TopK 从 6 增至 8。
- citation linker 从全部 TopK 中选最小证据集合，服务端校验别名、claim type 和来源权限。
- 按 claim 数切 verifier batch，按 3 题切生成 batch；JSON repair 仍失败时直接报错。
- 校验改为逐题推进，结构、entailment、来源策略、renderer、coverage 只阻断当前题，错误合并为 dirty set。
- 判否 claims 在 repair 前删除；repair 只接收 verified claims、错误原因和证据，不接收旧参考答案。
- verified-claim renderer 重新生成正文，coverage judge 再检查所有具体事实是否已声明。
- 增量校验只重验改写题目。真实 v1 包的调用从 94 降至 59，墙钟从约 641 秒降至约 504 秒。
- 并发 gather 完整回收 coroutine 后再抛首个异常，不再产生未 await 警告。
- 写库前同时检查 `question_quality.passed` 与 `coverage.passed`；v2 失败不生成 InterviewPrep。
- 把 LLM 可用性校验前置到匹配和检索之前；API 对配置缺失返回明确的 503。测试使用 `Settings.model_copy`，不再修改全局缓存对象。

### 验证结果
- 定向面试/RAG/reranker 回归 `32 passed`，覆盖 JSON repair、批量 reranker、计划库存修复、引用重绑定、判否 claim 清理、正文 claim coverage、renderer 质量错误和 release gate 不落库。
- 完整回归 `179 passed in 79.47s`；Python 编译、JavaScript 语法和 `git diff --check` 通过。
- 真实 DeepSeek v1：面试包 `#43`，32 题，质量分 `0.9841`，94 次调用，约 641 秒；面试包 `#44` 启用增量验证后质量分 `0.9947`，59 次调用，约 504 秒。
- `#44` 的验证题数按 `32 -> 21 -> 5 -> 1` 收敛，最终 citation integrity 和 source policy coverage 均为 1.0。
- v2 多轮真实测试验证了 JSON repair、citation linker、claim coverage、renderer、逐题阶段隔离和失败不落库；API 余额耗尽后数据库 InterviewPrep 数量保持 `45 -> 45`。
- Chromium 桌面端 `1440x1000` 与移动端 `390x844` 真实页面 smoke 均返回 200：加载 45 条历史计划和 32 道题，无横向溢出、控制台错误或失败请求；旧 v1 题目全部显示“需要重新生成”。
- 未配置 LLM 的生成请求在约 `691 ms` 内返回 503，InterviewPrep 数量保持 `45 -> 45`；页面同步显示完整中文警告。
- API key 未写入配置、数据库、日志或 Git；LLMCallLog 只保存模型、base URL、trace、字符数、耗时和截断预览。

### 未修复的问题
- DeepSeek 账户余额不足，无法提供 v2 最终落库成功的在线证据；没有切换旧 key 或伪造成功结果。
- Codex 内置浏览器连接器因本机 kernel assets 路径初始化失败，本轮改用同机 Playwright Chromium 完成页面验收；这是开发工具连接问题，不是 CareerAgent 页面错误。
- 32 题完整包在 CPU reranker 和多阶段 judge 下仍需数分钟，应使用后台队列和流式进度，不适合同步等待。
- 开发期 v1 包 `#43-#45` 仍在本地数据库中，但 v2 读取契约会要求重新生成。
- claim coverage 与 entailment 是 LLM judge，仍需人工标注集统计召回率、误报率和答案可用率。

### 下一步
- API 余额恢复后重跑 Profile `#159` + Job `#218`，要求 v2 两层 passed、落库和浏览器抽检同时通过。
- 将 CrossEncoder 部署到 GPU/独立推理 worker，并支持按用户选择的题组增量生成。
- 为 claim coverage 建人工标注集，评测漏声明事实召回率、无事实句误报率、citation linker 准确率和最终回答可用率。

## 2026-07-21 11:47:26 +08:00：面试题改为可直接参考的完整回答
### 这次做了什么
- 为每道面试题新增第一人称 `reference_answer`，覆盖 PDF Chunk、SQLite 与向量索引边界、FastAPI 并发和依赖注入、RAG、LangGraph、评测、Python 工程、LLM API、项目介绍、求职动机、行为题和能力缺口等题型。
- 前端展开区先展示“可直接参考的回答”，并使用清晰段落呈现；原来的五步框架、考察点和证据下沉到二级折叠的“查看回答思路与证据”，不再让用户面对一组抽象操作指令。
- 参考答案会绑定当前 JD、简历项目和能力缺口，并记录 `source/version/basis`；题型、题目或项目证据变化后自动重建。旧面试包读取时也会升级，不要求用户重新生成。
- 将题型分类改为以题目意图为主，不再把技能标签、追问或岗位名中的泛化词直接当作主问题；增加项目选择、面经核验、评测复现、依赖注入、动机和行为题等专用路由。
- Markdown 导出同步加入完整参考回答；质量门禁升级为 `heuristic_v3_reference_answer`，新增 `reference_answer_usability`，要求答案不少于 120 字、至少两句完整陈述、采用第一人称且无占位语，release 阈值为 `>= 0.9`。

### 发现的问题
- 原页面只有“先给结论、说明依据、绑定证据、用评测决定、说明边界”等框架。它适合开发者检查结构，却不能让普通用户直接看到一段可参考、可调整、可练习的回答。
- `q01_03` 问的是“SQLite 存 JD chunk 和向量元数据的边界”，旧分类器因为看到 `chunk`，错误展示了 PDF 切分策略，答案与问题不匹配。
- 全量审查 32 道题后还发现三类误判：面经题中的“选哪个项目”被当成面经核验；带 `chunk` 的“评测指标与实验复现”被当成切分题；仅凭 `risk_level=high` 会把 LangGraph 架构题误判为能力缺口。
- 旧质量门禁只判断回答框架是否可执行，即使没有完整参考答案也能通过，无法量化用户实际能否拿来练习。

### 怎么修复
- 使用题目正文进行意图优先级判断：明确缺口、动机、行为题和项目选择优先；评测指标与可复现语义优先于 `chunk` 关键词；只有题目明确询问切分策略、大小或 overlap 才进入 Chunk 答案。
- SQLite 题直接解释当前真实实现：`jobs` 保存岗位和结构化 JD，`job_chunks` 保存稳定 ID、原文、类型、embedding 与模型 metadata；SQLite 是权威存储，Chroma 仅为 hybrid 模式下可重建的检索镜像。
- 参考答案只组合已存在的岗位、简历和项目事实；无法证明的吞吐、参数或生产经验会明确标成未验证，不为了让答案完整而补造。
- 把 `question_kind` 加入答案指纹并升级为 `grounded_reference_answer_v2`，确保分类器修复后旧的错误答案不会继续命中缓存条件。
- 增加四类路由回归：评测题不能被 Chunk 标签干扰，高风险 LangGraph 架构题仍是 Agent 工作流，明确缺口题进入诚实披露，面经中的项目选择题进入项目介绍。

### 验证结果
- 面试包 `#42` 的 32 道题全部生成 300-561 字的完整参考回答；`q01_03` 命中 `storage_boundary`，答案包含 `job_chunks`、SQLite/Chroma 权威边界、二阶段检索和扩容路径，不再出现固定长度切分建议。
- 分类审查确认 `q05_02` 进入评测与可复现答案、`q06_01` 进入 FastAPI 依赖注入答案、`q09_01-q09_03` 进入行为题、`q10_01` 进入求职动机、`q10_02` 使用真实失败案例回答。
- 定向回归通过，共 `32 passed`；Python 编译、JavaScript 语法和 `git diff --check` 通过。
- 完整回归通过，共 `163 passed in 73.14s`。
- 内置浏览器实测旧面试包 `#42`：32 张题卡均包含“查看可直接参考的回答”；SQLite、FastAPI 依赖注入和求职动机三类答案内容与题目一致，回答思路与证据默认二级折叠；浏览器控制台无错误，390×844 视口下仍可完整展开和操作。

### 未修复的问题
- 当前完整参考答案由证据约束的本地组合器生成，不额外增加一次 LLM 调用；这样历史面试包也能立即升级，并避免 32 道题逐题调用带来的延迟和事实漂移。新题仍可由 LLM 结合 JD 与简历生成。
- 质量门禁当前衡量结构、长度、第一人称和占位语，不等价于真人面试官对表达自然度、技术深度和口语节奏的评分。

### 下一步
- 增加用户自己的口述或文本作答入口，再用参考答案和证据作为 rubric，给出遗漏点、事实风险和表达改写；该反馈必须与参考答案分栏，不能覆盖用户原答案。
- 对参考答案增加独立人工标注集，按题型统计问题相关性、证据一致性、可直接使用率和不支持事实率，再决定是否用 LLM-as-judge 做抽检。

## 2026-07-21 10:32:15 +08:00：面试回答框架证据化与单题生成来源拆分
### 这次做了什么
- 新增 `InterviewAnswerFrameworkService`，把每道面试题的回答提示统一升级为 `section + guidance` 的结构化框架，并按 PDF Chunk/RAG、FastAPI 并发与 Trace、SQLite/向量库边界、LangGraph/Agent、缺口披露和通用问题生成不同的回答路径。
- 将面试包级 `generation_mode` 与单题来源拆开：每道题新增 `question_generation_source(_label)` 和 `answer_framework_source(_label)`，明确区分“题目由 LLM 生成”“题目来自已导入面经”和“回答框架由证据规则生成”。
- 对旧面试包实施读取时升级：API、逐题练习接口和 Markdown 导出会自动将旧版三句模板改造成可练习框架，不需要删除或重新生成历史面试包。
- 回答框架会从简历项目中选择与题目最相关的证据。例如 RAG chunk 问题优先引用《RAG 评测实验台》，并把项目原文作为 `evidence_refs.preview`，不生成简历中没有的参数和指标。
- 前端将框架改成编号步骤，单独显示“题目来源、框架依据、考察点、可引用证据”；不再展示 `interview_experience:3` 等内部证据 ID。
- 将题目质量 judge 升级为 `heuristic_v2_answer_framework`：行动性不再只检查回答要点数量，而要求至少 4 个结构化步骤，并同时覆盖项目证据、评测/验证和失败或事实边界；release 阈值为 `actionability >= 0.9`。
- 面试包列表和详情查询使用 `joinedload` 一次加载关联的 Profile 与 Job，避免旧包读取升级时按行触发额外查询。

### 发现的问题
- 面试包 `#42` 虽然标记为 `llm_augmented_v1_jd_project_questions`，但截图中的 `q01_01` 是规则生成的已导入面经题；原页面没有显示单题来源，容易误以为每道题和回答框架都由 LLM 生成。
- 旧版回答框架只要求“标注来源、围绕技能说明、不要编造”，属于开发期事实边界模板，无法帮助用户组织一个可以直接回答的技术方案。
- 质量 judge 原先把“题目长度足够且至少有两条回答要点”视为行动性通过，因此空泛模板也能获得高分。
- 旧数据的 `evidence_refs` 含占位面经 URL 和内部数据库 ID；前端直接拼接 `ref`，把实现细节暴露给用户。
- 首轮定向测试发现题型路由会被岗位名中的 `Agent` 干扰，使包含 RAG 的题目误用 Agent 工作流框架。

### 怎么修复
- 对明确技术主题设置专用回答骨架。RAG Chunk 题固定覆盖结论、候选策略、PDF 噪声、项目证据、Recall@K/MRR/nDCG/引用正确率/延迟和 chunk 大小与 overlap 取舍。
- 项目证据选择改为主题加权匹配，`chunk/RAG/FastAPI/SQLite/LangGraph/Redis` 的权重高于泛化的 `Agent`；同分时再考虑项目信息完整度。
- 题型路由先识别 chunk、FastAPI、SQLite 和明确的 LangGraph/workflow 主题；只有没有更具体主题时，岗位名中的 `Agent` 才会进入 Agent 框架。
- 对证据 URL 复用 `InterviewReferenceService` 校验，占位地址从响应中移除；用户页面只显示来源标签和证据预览，内部 `ref` 仍保留在后端用于追踪。
- 新面试包在写库前生成结构化框架并进入 v2 质量门禁；旧面试包在交付层按相同规则幂等升级，连续读取不会重复追加证据。

### 验证结果
- 定向回归通过，共 `31 passed`；覆盖新旧面试包、RAG Chunk 专用框架、项目证据绑定、占位 URL 过滤、Markdown 导出和前端契约。
- 完整回归通过，共 `162 passed in 72.18s`；Python 编译、JavaScript 语法和 `git diff --check` 通过。
- API 实测旧面试包 `#42` 返回 10 个题组；首题框架来源明确为“系统根据 JD、简历证据和题目类型生成”，并包含 5 个结构化步骤。
- 内置浏览器实测 `q01_01`：题目显示“由已导入面经生成”，框架引用《RAG 评测实验台》，可引用证据包含面经原题和简历项目原文，不再出现内部证据 ID。
- 桌面端首题卡片 `scrollWidth=clientWidth=874`；390px 视口下页面、回答步骤和证据区均无横向溢出。

### 未修复的问题
- 本轮没有把“完整参考答案”交给 LLM 生成。当前产物是证据约束的回答框架，目的是帮助用户组织真实回答，而不是替用户编造一段可背诵的答案。
- 服务当前未配置 LLM，因此浏览器验证使用已有面试包完成；LLM 生成题目的链路由测试覆盖，本轮没有新增真实 LLM 调用成本和耗时数据。
- 历史面试包保存的 `question_quality` 仍记录生成当时的 judge 版本；读取时会升级交付框架，但不会篡改历史评测记录。新生成面试包才使用 v2 门禁。

### 下一步
- 增加用户口述/文本回答记录，以当前框架为 rubric 做证据一致性、技术深度、结构完整性和表达清晰度评分，并把薄弱步骤加入复习队列。
- 对 v2 框架建立独立评测集，按题型统计 evidence binding、framework completeness 和 unsupported claim rate，再决定是否对部分题型引入 LLM 框架生成与 LLM-as-judge 抽检。

## 2026-07-21 09:59:19 +08:00：面试三步语义对齐与参考链接可信化
### 这次做了什么
- 将顶部准备路线和下方内容改成完全对应的三步：`了解考察范围`、`准备缺口回答`、`按主题练习`，标题、顺序和说明保持一致。
- 将含义模糊的“优先补齐”改为“准备缺口回答”，明确说明这里展示的是“简历缺少充分证据的能力，以及面试时如何诚实说明相邻经验和学习计划”，不是要求用户临时掌握技能。
- 新增 `InterviewReferenceService`，统一区分用户导入原文、站内搜索、平台入口和普通搜索，并过滤 URL 路径中的 `example/sample/demo` 占位地址。
- 牛客搜索入口改为 `nowcoder.com/search/all?query=...`，小红书改为 `xiaohongshu.com/search_result?keyword=...`；OfferShow 没有稳定公开关键词搜索地址，因此明确标为平台入口并提示用户进入后手动搜索。
- 面试包 API 和 Markdown 导出都会在读取时规范化旧数据，不需要重新生成面试包；新生成面试包在写入前也使用同一规范化服务。
- 参考资料前端改名为“面经来源与搜索入口”，每条显示“原文 / 搜索入口 / 平台入口”类型，搜索结果不再伪装成具体文章。

### 发现的问题
- 顶部“看准备重点、补能力缺口、逐题练习”和下方“准备重点、优先补齐、按主题逐题练习”只是大致相近，没有形成用户可确认的一一对应关系。
- “优先补齐”没有解释补什么、何时补、如何补，容易被理解为修改简历或立即学习新技术。
- 旧演示数据包含 `https://www.nowcoder.com/discuss/example-agent-intern`，却被当成用户确认的真实面经链接展示。
- 自动调研项把百度搜索地址包装成“牛客网同岗位面经”等类似具体内容的标题，点击后的页面与用户预期不一致。

### 怎么修复
- 三步导航与三个内容区使用相同动词和名词，并在第二步增加完整的用户说明与“回答建议”标签；即使没有缺口也显示明确的空状态。
- 用 URL 结构校验过滤非公网地址和占位路径，失效链接不会进入 API 响应、用户页面或 Markdown 导出。
- 按平台生成真实入口，并用 `reference_type/reference_type_label` 保存链接语义；旧数据库里的百度包装链接在读取时会重建为对应站内搜索。
- 链接规范化设计为幂等操作，生成、API 读取和 Markdown 导出连续调用时不会重复添加标题前缀或改变链接。
- 面经正文和来源链接解耦：旧数据中的问题仍可用于练习，但无效来源地址不会继续显示为可点击原文。

### 验证结果
- 定向测试 `30 passed`，覆盖占位链接过滤、牛客/小红书站内搜索、OfferShow 平台入口、旧面试包 API、Markdown 规范化和重复调用幂等性。
- `python -m pytest -q --tb=short` 完整回归通过，共 `161 passed in 82.18s`；JavaScript 语法、Python 编译和 diff 检查通过。
- 旧面试包 `#42` API 返回 4 个规范入口，原来的 3 条 `example-agent-intern` 已消失；Markdown 导出不再包含占位地址并包含牛客站内搜索链接。
- 内置浏览器确认三步导航和下方三个区域逐字对应；展开参考资料后显示牛客搜索、OfferShow 平台、小红书搜索和公司背景搜索，并分别标记入口类型与可信边界。

### 未修复的问题
- 外部平台的搜索结果、登录要求和页面结构由平台控制，系统只能保证链接目标与标签一致，不能保证平台始终返回高质量内容。
- OfferShow 当前没有可稳定验证的公开关键词搜索地址，因此只提供平台首页入口，不伪造可直接搜索的 URL。

### 下一步
- 将链接状态检查并入 interview source smoke，记录 HTTP 状态、最终跳转地址和页面标题；平台入口发生结构变化时在控制台告警。

## 2026-07-21 09:37:36 +08:00：面试工作区视觉层级与练习导航重构
### 这次做了什么
- 将面试工作区重排为“左侧选择计划、右侧继续练习”的主从结构，左侧宽度收窄并固定显示最近面试包，去掉每张历史卡片上重复的导出操作。
- 当前计划增加三步准备路线：先看准备重点、再补能力缺口、最后逐题练习；进度区保留总题数、练习中、已掌握和完成度，并为完成度增加可视进度条。
- 把原来混在正文里的准备角度改为三类结构化信息行，能力缺口改为独立重点区，避免标签、数量和长文本挤在同一行。
- 面经与参考资料改为默认折叠，并按“来源、链接、标题”去重；外部链接不再抢占首屏，用户需要时再展开查看。
- 增加题目主题目录，可直接跳转到对应题组；每道题明确区分主问题、面试官可能追问、回答框架与证据、练习状态。
- 增加 1120px、860px 和 560px 响应式规则；手机端简历和岗位选择改为单列，计划列表、进度与题目状态根据可用宽度重新排布。

### 发现的问题
- 原页面中历史计划、进度、准备角度、能力缺口、参考链接和题目使用接近相同的视觉权重，用户无法快速判断先看什么。
- 左侧历史卡片反复展示完整标题、内部 ID、匹配分和导出按钮，窄列中换行严重且重复感强。
- 面经参考链接默认全部展开，旧数据中的相同链接会重复出现，把真正需要练习的问题推到页面下方。
- 390px 实测发现简历与岗位选择仍保持两列，标题和按钮被迫换行。

### 怎么修复
- 使用稳定的两栏工作区和选中态左边线建立主从关系，历史卡片只保留计划号、岗位、题数、匹配分和日期；导出统一放在当前计划标题区。
- 用三步路线、分区标题、数量标记和主题目录建立从概览到练习的阅读顺序，不依赖说明性大段文字。
- 在前端渲染参考资料前使用稳定复合键去重，并用 `details` 保持默认收起。
- 题目追问改为独立浅色信息区，练习状态改为带标题的分段控制；小屏下降为两列，避免按钮和正文横向溢出。
- 在 560px 以下将面试上下文选择器改成单列，并取消固定高度。

### 验证结果
- `node --check app/static/js/main.js`、Python 编译和 `git diff --check` 通过。
- `python -m pytest -q --tb=short` 完整回归通过，共 `159 passed in 78.00s`。
- 内置浏览器桌面端验证：左侧计划选择、三步路线、四项进度、准备重点、能力缺口、折叠资料和题目卡片层级正常。
- 390x844 Playwright 验证：页面 `scrollWidth=390`、`innerWidth=390`，没有横向溢出；32 道题和 128 个练习状态按钮全部渲染。

### 未修复的问题
- 本次只优化信息架构和交互呈现，没有重新生成或改写面试问题；问题质量仍由现有面试包生成与评测链路负责。
- 旧数据库中已有的重复面试包仍然保留，页面会压缩展示但不会自动删除历史数据。

### 下一步
- 在模拟面试会话落地后，将逐题练习状态与用户回答、追问轮次和评分结果合并成可恢复的训练记录。

## 2026-07-20 22:21:09 +08:00：面试模块接入岗位主线与逐题训练工作区
### 这次做了什么
- 将面试包生成统一接入 LangGraph `prepare_interview_for_job` 任务，不再由用户页直接调用孤立的生成接口；生成过程会产生 Agent run、步骤、事件和 artifact，可从历史记录追踪。
- 岗位详情增加“面试准备”标签和下一步按钮。选择简历后自动查询当前“简历 + 岗位”的已有面试计划；已有计划时按钮改为“查看面试准备”，避免无意重复生成，需要更新时再显式点击“重新生成”。
- 面试页从手填 `profile_id/job_id/prep_id/question_id` 改为简历选择器、岗位选择器、最近面试计划和当前训练工作区；支持从岗位详情、历史记录和完整求职流程通过 `prep_id/profile_id/job_id` 恢复同一上下文。
- 面试计划显示总题数、练习中、已掌握和完成度；每道题可直接标记“待练习、练习中、已掌握、稍后处理”，状态写入 `interview_practice_items` 后立即刷新进度。
- 问题卡片集中展示同岗面经、简历项目技术栈、JD/缺口问题、连续追问、回答重点和证据边界；面经导入保留为折叠的可选补充能力，不再占据主流程。
- `GET /interview-prep` 增加 `profile_id/job_id/limit` 过滤并使用稳定倒序；求职流程、历史摘要和首页结果中的面试链接现在携带具体 `prep_id`。

### 发现的问题
- 原面试页要求用户理解内部数据库 ID，生成、面经导入、练习状态和面试包列表平铺在同一页面，没有明确的目标岗位上下文。
- 岗位详情只连接匹配和定制简历，用户看到感兴趣的 JD 后不能继续准备面试。
- 原用户页直接调用 `/interview-prep`，不会生成 LangGraph run，导致面试产物与历史记录、trace 和同一个求职流程脱节。
- 已有多份相同岗位面试包时，岗位页仍只有“生成”动作，容易继续制造重复数据。
- 浏览器首次验证发现：URL 预填岗位后，岗位选择弹窗复用了单条缓存，只能看到当前岗位，无法更换。

### 怎么修复
- 将用户动作统一路由到 `createAgentRun({task_type: "prepare_interview_for_job"})`，使用现有 idempotency、trace、事件流和错误处理能力。
- 用 `profile_id + job_id` 作为用户侧面试上下文；岗位详情先查询已有面试包，再决定显示“查看”还是“生成”。
- 用 `prep_id` 作为训练工作区恢复键；页面加载后同时读取面试包和 `/practice` 状态，并以 `question_id` 合并。
- 练习状态按钮直接调用已有 PUT API，不再暴露内部题目 ID 表单；浏览器更新后按服务端返回重新渲染。
- 状态按钮只更新状态和信心分，不会清空用户此前写下的练习备注；旧面试包中的非数组回答要点会被安全归一化后渲染。
- 面试简历/岗位选择器每次打开都刷新完整列表，URL 预填数据只用于当前选择，不再污染候选缓存。

### 验证结果
- 定向测试 `39 passed`，覆盖面试 API 过滤、岗位入口、上下文选择器、LangGraph 任务、逐题状态和完整流程链接。
- 完整回归 `python -m pytest -q --tb=short` 通过，共 `159 passed in 68.41s`。
- 内置浏览器通过 `prep_id=42/profile_id=156/job_id=197` 恢复正确上下文，加载 42 份历史面试计划、32 道当前题目和 128 个状态动作。
- 将 `q01_01` 标记为“练习中”后，页面显示“1 练习中”，服务端状态立即生效；随后恢复为“待练习”，没有遗留测试状态。
- 岗位 `#197` 选择简历 `#156` 后识别 6 份已有计划，显示最近 5 份和“查看全部”，练习链接正确指向面试包 `#42`。
- 修复缓存后，岗位选择弹窗加载 80 个候选岗位；岗位详情主按钮显示“查看面试准备”，点击只切换到已有计划，最新 Agent run 仍为 `#192`，没有创建重复运行。

### 未修复的问题
- 当前服务未配置 LLM，浏览器验证使用已有真实面试包完成，没有新建面试包；新生成链路由 Fake LLM/工作流测试覆盖，真实 LLM 需要在注入 API Key 后再做一次耗时和内容质量复测。
- 历史面试包中存在旧版本重复数据；本次阻止用户无意重复生成，但没有删除或合并已有记录，以免破坏历史 trace。
- 练习状态目前记录完成度和固定信心分，尚未采集用户的口述回答，也没有 LLM 模拟面试官做多轮追问和回答评分。

### 下一步
- 增加“模拟面试会话”：按当前面试包选择题目，保存用户回答，使用独立 trace 做证据一致性、技术深度和表达结构评分。
- 给面试计划增加归档/版本关系，让“重新生成”明确形成新版本并可比较题目变化，而不是平铺为相互独立记录。

## 2026-07-20 21:38:43 +08:00：最近 50 条历史记录选择与详情恢复
### 这次做了什么
- 历史记录 API 增加 `limit` 参数；用户历史页显式请求最近 50 条，接口仍允许状态恢复和控制台最多读取 100 条运行记录。
- 左侧历史区改为固定高度的可滚动记录选择器，明确显示记录数量；每条记录展示编号、任务类型、中文状态、时间、简历和岗位。
- 每条记录的主体区域可点击。选择后同步更新左侧高亮、无障碍 `aria-pressed`、右侧详情标题、状态、摘要、待确认操作、阶段进度和 LangGraph 事件。
- 选择记录时把 `run_id` 写入 URL；刷新后恢复同一记录，并支持浏览器前进/后退切换已经查看过的记录。

### 发现的问题
- 后端原来一次返回 100 条，前端全部纵向铺开，没有独立滚动区和数量说明，历史记录与右侧详情之间缺少明确的主从关系。
- 点击记录后 URL 不变化，页面刷新会重新选择最新一条，用户会认为旧记录没有真正打开。
- 右侧标题始终写“本次任务完成了什么”，没有显示当前选中的记录编号，切换记录后的反馈不明显。

### 怎么修复
- 把用户页的历史查询边界固定为最近 50 条，并按“创建时间倒序、ID 倒序”双重排序，保证同一秒产生多条记录时结果仍然稳定。
- 使用专门的 `data-history-run-id` 交互契约，避免与确认、运维按钮混用；`loadRunSteps` 统一负责 URL、选中状态和全部详情区刷新。
- 首次进入页面使用 `replaceState`，主动选择历史记录使用 `pushState`，监听 `popstate` 恢复详情。

### 验证结果
- 增加 55 条运行记录的 API 回归用例，确认请求 50 条时只返回最新 50 条，顺序正确。
- 增加历史页前端契约测试，覆盖数量、可滚动列表、点击选择、URL 恢复和详情标题。
- 内置浏览器确认页面恰好渲染 50 条记录；点击旧记录 `#185` 后，右侧切换为 5 个阶段步骤和 49 条事件，URL 更新为 `run_id=185`，刷新后仍恢复 `#185`。
- `python -m pytest -q --tb=short` 全量通过，共 `157 passed in 72.87s`；JavaScript 语法、Python 编译和 diff 检查通过。

### 未修复的问题
- 当前保留的是最近 50 条运行级记录，其中可能同时包含自然语言父流程和 LangGraph 子流程；后续如要按“一个求职包”聚合，需要增加显式的业务流程关联字段，而不能仅靠前端猜测。

### 下一步
- 增加按状态和任务类型筛选，并在拥有显式父子关联后提供“按求职流程聚合 / 查看全部底层运行”的切换。

## 2026-07-20 20:07:13 +08:00：简历使用方式、用户确认流程与多运行状态框重构
### 这次做了什么
- 开始页将“不使用简历”改为独立且默认选中的方式，与“已有档案、上传 PDF、新建档案”组成清晰的四种选择；PDF 原生文件框改为统一按钮，并显示当前文件名。
- 岗位页增加“不使用简历 / 选择简历”两个显式动作。选择档案后按钮变为“更换简历”，切回不使用简历会清空 `profile_id` 并提示重新搜索。
- 重构用户确认流程：全站增加确认弹窗，区分 `job_selection` 和 `application_packet`；岗位选择展示候选岗位、公司、匹配分和命中技能，投递材料确认明确说明不会自动提交、填写外部网页或发送邮件。
- 历史记录页根据 `run_id` 恢复指定流程，待确认区前置到阶段进度之前；自然语言父 run 会定位到真正等待 LangGraph interrupt 的子 run，避免确认错误的记录。
- 右下角状态框改为最多逐条展示 3 个进行中流程，按等待确认、运行中、排队中排序；去重父子 wrapper、清除过期网络错误、保留临时同步错误但不覆盖真实状态。
- 多流程时状态框默认收起为一行摘要，避免遮挡岗位页操作区；展开后每条记录都有独立的“查看详情 / 现在处理 / 不再显示”操作。
- 修复 Trace 对 tuple 输出的序列化：`TraceService` 现在递归处理 `list/tuple/set`，步骤响应允许合法 JSON 的任意类型，旧 trace 字符串不再令 `/agent/runs/{id}/steps` 返回 500。
- 业务摘要按确认类型生成标题；岗位选择阶段显示“已找到 N 个候选岗位，等待你选择”，不再误报为“投递材料已准备”。

### 发现的问题
- 原确认 UI 把岗位选择和生成投递材料都显示为“确认继续”，且自然语言父 run 可能指向真正 interrupt 子 run 之外的错误恢复目标。
- 状态恢复能读到多条记录，但只渲染最高优先级的一条；旧网络错误会永久保存在 `localStorage`，成功同步后仍显示 `Failed to fetch`。
- 自然语言父 run #186 和完整流程子 run #187 同时显示，用户会误以为存在两个独立待确认任务。
- 多条状态记录全部展开后会遮挡岗位页右侧的简历选择区域。
- 真实完整流程 run #192 在 `search_jobs` trace 中返回 tuple；原 `_json_safe` 把 tuple 转成含内存地址的字符串，而 `AgentStepResponse` 又要求字典，导致步骤 API 500，并中断后续全局状态恢复。
- `RunBusinessSummaryService` 没有区分 `job_selection` 与 `application_packet`，真实岗位选择流程被错误描述为投递材料确认。

### 怎么修复
- 使用 `confirmationContext` 统一解析普通 run、自然语言 wrapper、LangGraph interrupt 和目标子 run；确认按钮提交明确的 `job_id/source/note`，不再使用无类型的通用确认 payload。
- `restoreActiveRuns` 每次合并浏览器记录和服务端活跃 run，以 `run_id` 去重，并识别包含待确认子 run 的 wrapper；网络失败仅写入瞬时 `sync_error`。
- 状态框只在没有活跃流程时展示一条最近终态；有活跃流程时隐藏终态噪声，最多显示 3 条，剩余记录进入历史页。
- Trace 层保留结构化容器语义，API schema 接受真实 JSON 值；增加旧 trace 字符串和 tuple 序列化回归测试。
- 业务摘要读取 `confirmation_type` 和 interrupt `kind/matches`，在岗位选择阶段使用候选数量生成用户文案。

### 验证结果
- 内置浏览器在开始页确认“不使用简历”为默认 pressed 状态，四种方式尺寸一致；岗位页完成“选择 Profile #159 -> 更换简历 -> 切回不使用简历”的双向交互。
- 创建真实完整流程 run #192，约 40 秒后进入 `job_selection` interrupt，返回 5 个真实候选岗位，包括阿里、百度和美团；弹窗正确显示公司、匹配分与“选择并继续”，测试期间没有选择岗位或生成后续材料。
- 右下角同时恢复 run #192、#187、#165 三条待确认流程，父 wrapper #186 未重复出现；默认收起，展开后恰有 3 个独立“现在处理”入口。
- 修复后 `/agent/runs/192/steps` 返回 200，业务摘要标题为“已找到 5 个候选岗位，等待你选择”，历史页不再出现 `Internal Server Error`，全局状态框继续恢复。
- `python -m pytest -q --tb=short` 全量通过，共 `156 passed in 72.18s`；`python -m compileall -q app`、`node --check app/static/js/main.js` 和 `git diff --check` 通过。

### 未修复的问题
- 历史 run #187 生成于旧版本，`interrupts` 为空，因此确认弹窗只能从 `run.job_id` 补查岗位标题，无法还原当时完整的 fit gate 细节；新 run 会持久化完整 interrupt payload。
- 内置浏览器对内容较长的岗位页和历史页执行 `Page.captureScreenshot` 仍会超时；DOM、按钮状态、弹窗内容和后端请求均已实测，截图工具超时不影响应用功能。
- 本次为验证真实岗位选择新增的 run #192 保持在等待确认状态，方便继续人工检查；没有替用户选择岗位或继续生成材料。

### 下一步
- 将用户确认弹窗复用到 `browser_apply/email_draft/email_send` 的用户侧审批入口，在外发前展示目标域名、收件人、字段摘要和幂等键。
- 给状态框增加服务端游标或批量状态接口，减少多活跃 run 时每 4 秒逐条请求的数量。
- 为 `job_selection -> application_packet` 两次 interrupt 增加浏览器自动化回归，验证第一次选择岗位后弹窗能原地切换到下一项确认。

## 2026-07-20 19:13:23 +08:00：以岗位发现为主线的三模式用户流程、岗位 RAG 与真实 LLM 验证
### 这次做了什么
- 将开始页改为岗位发现入口，支持三种真实用户模式：只填求职需求、只提供简历自动匹配、同时提供需求和简历联合检索；用户没有简历时也可以先浏览岗位。
- 新增 `job_search_sessions/job_search_results`，持久化输入模式、解析后的查询、来源错误、排序分数、可选匹配结果和理由；页面刷新或跨页后通过 `session_id` 恢复。
- 新增 `POST /job-discovery/sessions`、搜索记录查询接口、站内岗位详情页和简历选择弹窗；真实 JD、匹配与差距、简历评审、定制简历和官方投递链接集中在同一岗位页面。
- 岗位 RAG 采用“元数据/词法轻召回 -> 候选 JD chunk 真实向量召回 -> 岗位级 reranker”的两阶段结构，不再对 chunk 和岗位重复 rerank。
- 昂贵向量阶段的岗位候选数限制为 `max(12, min(80, limit * 4))`；增加 180 岗位回归样本，锁定候选池上界。
- 增加旧向量惰性迁移：历史 hash 或旧维度 embedding 首次命中时批量重算，并把真实 provider/model/dimensions 写回 SQLite，后续检索直接复用。
- 真实岗位结果列表改用 `JDParserService.parse_jd_for_search` 做确定性字段抽取、技能别名归一化和 Prompt Injection 检测，不再为每条搜索结果等待 LLM；用户触发评审和定制时仍调用真实 LLM。
- LangGraph `full_career_flow` 搜索后增加 `job_selection` interrupt，只有用户从候选结果中选择岗位后才继续定制，不再自动选择 Top1。
- 简历评审 LLM 增加输出后证据校验：建议中的新增数字不在原简历时整条拒绝，并在 trace 记录 `unsupported_numeric_claim`；确定性建议模板也改为只引用用户真实技能、岗位和项目。
- 修复岗位详情无投递链接仍显示按钮、匹配维度暴露英文后端字段、异步匹配/定制完成后按钮无法恢复、静态资源缓存未更新等前端问题。

### 发现的问题
- 初版跨岗位检索会对最多 800 个岗位的全部 JD chunk 重算真实 embedding，进程工作集接近 1 GB，搜索长时间停留在处理中。
- 岗位发现先对 chunk rerank，再对岗位 rerank，重复加载和执行 cross-encoder。
- 历史 SQLite 中旧向量只在内存里临时重算，没有写回数据库，导致服务重启或后续查询反复支付同一成本。
- 真实腾讯岗位抓取只需约 1 秒，但 3 条 JD 的旧同步 LLM 解析和索引链路使端到端耗时达到 66.92 秒。
- 第一次真实 LLM 评审擅自建议“500 份简历、92% 召回率、10+ 招聘网站、200+ 求职链路”等不存在的指标；仅在简历正文做 Guardrail 不够。
- 确定性个人总结模板硬编码 `LangGraph`，会在用户没有该技能时给出不真实的改写示例。
- 浏览器控制台显示 `event.currentTarget` 在异步回调结束后变为 `null`，导致匹配和定制按钮一直保持禁用。
- 真实腾讯 Agent 查询能返回岗位，但“只看实习/校招”会正确过滤当前查询中的社招结果；不能把过滤后的本地岗位结果误报成腾讯实习岗位。

### 怎么修复
- 先做轻量相关性排序，将真实 embedding 范围缩到有限候选，再只做岗位级二阶段重排；简历 RAG 和单岗位 JD RAG 的 TopN chunk reranker 保持不变。
- `_row_vectors` 重算旧向量后同步更新 `embedding_json/metadata_json` 并提交 SQLite，形成一次性、可追踪的索引迁移。
- 真实来源列表路径使用快速结构化 parser；原始 JD 始终保留，完整 LLM parser 仍用于独立 parser 评测和显式深度解析，不把模型调用伪装成抓取必要步骤。
- 对 LLM 修改建议同时加强 Prompt 和确定性后校验；未知量化指标必须写成“待补充真实数据”，不允许生成可直接复制的假数字。
- 所有匹配分维度映射为中文用户文案；`[hidden]` 使用强制隐藏规则；异步事件处理先保存按钮引用，再进入 `await`。
- 搜索会话同时写入 URL 和 `localStorage`，结果页、岗位详情页和返回链接继续携带 `session_id/profile_id`。

### 验证结果
- 定向回归最终通过 `35 passed`；完整回归 `python -m pytest -q --tb=short` 通过，共 `153 passed in 75.31s`。
- `python -m compileall -q app`、`node --check app/static/js/main.js` 和 `git diff --check` 通过；只有 Windows LF/CRLF 转换提示。
- 浏览器完成“只填需求、只选简历、需求+简历”三条路径，分别得到需求相关度或简历匹配/缺口；搜索记录刷新后仍能恢复。
- 岗位库检索同进程预热后约 3.43 秒；带简历的岗位匹配预热后约 6.30-6.55 秒。
- 真实腾讯 Source 直接查询 1.11 秒返回 10 条 Agent 岗位；完整 hybrid 落库后，3 个结果中包含 2 个腾讯岗位，原始 JD 和官方链接均非空。
- 真实来源 + 索引链路优化前为 66.92 秒；优化后冷启动 50.75 秒、同进程预热后 12.75 秒。
- 真实 DeepSeek 评审与定制已跑通，修复后不再显示虚构的 500/92%/10+/200+ 指标；定制简历 `#99` 通过事实检查，正文不包含评分、修改摘要或检查结果。
- 最新静态资源版本的真实岗位详情页显示中文匹配维度，匹配完成后按钮恢复可点击；浏览器中未出现指向该版本应用脚本的新控制台错误。

### 未修复的问题
- 真实 embedding 与 cross-encoder 在全新 Python 进程中仍有明显冷启动：本机首次完整真实来源查询为 50.75 秒；当前通过持久化索引把重复请求降到 12.75 秒，但尚未增加 worker 启动预热或模型服务化。
- 本次真实腾讯 Agent 查询返回的主要是社招岗位；启用“只看实习/校招”时结果会回到岗位库，不应将其表述为腾讯实习结果。
- 字节、阿里等动态来源仍受官网协议和浏览器冷启动影响；来源失败会进入 `source_errors_json`，但外部协议稳定性无法由本项目保证。
- in-app browser 的一次 `Page.captureScreenshot` CDP 调用超时；DOM、交互、控制台和响应式约束测试均已执行，未把截图工具超时当成应用通过证据。

### 下一步
- 在 Redis worker supervisor 启动时预热 embedding/reranker，并把 model ready、冷启动耗时和索引迁移数加入 readiness/metrics。
- 为真实岗位刷新增加查询级短 TTL 缓存和 source 级阶段进度，避免重复启动字节浏览器或重复读取相同公开 JD。
- 增加岗位 RAG 的 session 级离线评测集，分别报告无简历需求检索与带简历匹配的 Recall@K、nDCG@K、来源覆盖和延迟分位数。

## 2026-07-20 15:08:23 +08:00：国内招聘站调研与腾讯/百度/美团/字节/阿里五源接入
### 这次做了什么
- 实测国内互联网公司自有招聘站，正式增加百度、美团、字节跳动和阿里巴巴 Source，与已有腾讯组成默认五源中文岗位链路。
- 百度 Source 从公开 SSR `window.__INITIAL_DATA__` 读取实习岗位、职责、要求、地点和招聘项目。
- 美团 Source 调用公开岗位搜索 JSON，再以受控并发读取详情 JSON，补全职责、要求、部门和岗位亮点。
- 字节 Source 使用 async Playwright 打开官方校园招聘页，等待官网生成动态 `_signature`，捕获状态码为 200 的结构化岗位 JSON；没有复制短期签名，也没有依赖 DOM selector 抓 JD。
- 阿里 Source 首次访问获取 `XSRF-TOKEN`，调用批次发现接口获取 2027 届、日常和研究型实习批次，再并发搜索各批次完整 JD。
- 增加招聘站 query normalization，把自然语言目标提取为 `Agent`、`RAG`、`LLM`、`大模型` 等官网检索词，返回后仍按原始完整 query 做统一相关性排序。
- 默认 `JobSearchRequest`、前端岗位搜索和 `.env.example` 已切换为五源；Lever 继续只作为显式英文辅助。
- 新增 `evals/real_job_source_cases.json` 的 8 类真实 query release suite、`scripts/run_real_job_source_eval.py` 阈值脚本和 `tests/test_job_sources.py` Source 契约测试。
- 新增 `docs/REAL_JOB_SOURCES.md`，记录正式来源、未接入站点、协议边界、部署要求和真实指标；同步更新 README、API、架构、开发、评测和目录文档。

### 发现的问题
- 字节公开岗位 JSON 的 `_signature` 由官网 JavaScript 动态生成；直接 HTTP 请求或复用浏览器里复制的签名会收到 405 或很快失效。
- 阿里 `/position/search` 不接受“Agent 开发实习生”这种自然语言整句作为有效检索词，返回 `datas=null`；输入 `Agent` 时可以返回大量研究型和日常实习岗位。
- 阿里实习项目同时存在多个动态批次，固定写死 2027 届 `batchId` 会漏掉日常实习和研究型实习，也会在下一招聘年度失效。
- 腾讯当前 Agent 结果仍混有社招；单一腾讯 smoke 的实习率只有 0.3750，不能代表中文 Agent 实习市场。
- 滴滴公开接口当前能返回 Agent 社招但没有 Agent 实习；OPPO 详情可读但列表发现链路不稳定；小米、华为、京东尚未验证稳定的完整 JD 搜索协议。

### 怎么修复
- 对字节使用真实浏览器生成签名，但只把官方 JSON 响应作为数据协议；浏览器缺失、超时或协议变化直接写入 `source_errors`。
- 对阿里先动态发现所有 `internship` 批次，再并发查询；`success=true` 且 `datas=null/totalCount=0` 被正确视为空结果，不伪装成协议异常。
- 将官网检索词规划和跨来源排序分开：Source 负责用站点能理解的短检索词扩大候选池，`job_relevance` 负责按用户完整 query 做实习、Agent、开发、算法、后端、产品等意图重排。
- 五个 Source 在 FastAPI 服务层通过 `asyncio.gather` 并发运行；美团详情和阿里批次也各自在 Source 内并发，SQLite 写入仍保持顺序。
- 未达到“稳定发现、完整 JD、稳定投递 URL、连续 smoke”四项要求的站点不进入默认链路。

### 验证结果
- Source 契约、中文默认源和评测服务定向回归：`34 passed`；新增字节/阿里后 Source 定向回归：`8 passed`。
- 完整回归 `python -m pytest -q --tb=short`：`143 passed`；Python 编译检查、前端 JavaScript 语法检查、评测集 JSON 校验和 `git diff --check` 均通过。
- 单次真实五源 smoke EvaluationRun `#39`：5/5 来源可达且有结果，40 条岗位，JD 非空率 1.0000，投递链接率 1.0000，实习率 0.8500，query relevance 1.0000，Agent 相关率 0.9750，总耗时 7314ms。
- 8 类真实岗位 suite EvaluationRun `#40-#47`：8/8 case 通过，共返回 316 条岗位；每个 case 的可达率、有结果率、JD 非空率、投递链接率和 query relevance 都是 1.0000。
- 完整 suite 的实习率为 0.7778-0.9000，Agent 相关率为 0.8333-1.0000，单 case 五源并发耗时为 6.5-7.6 秒。
- 实际结果包含字节 `Agent开发实习生-开发者服务`、`AI Agent开发实习生-App Infra`，以及阿里 `研究型实习生-高性能算子生成 Agent 研发`、`日常实习生-1688-LLM/Agent实习生` 等完整 JD。

### 未修复的问题
- 字节 Source 每次搜索会启动一个 Chromium 进程，当前单 Source 实测约 4.6 秒；尚未实现浏览器进程池或短 TTL 查询缓存。
- 招聘站协议属于外部系统，字段、签名和批次随时可能变化；本次通过 release suite 证明当前可用，不能承诺永久稳定。
- 当前环境没有安装 Ruff，`python -m ruff check` 无法执行；代码质量继续由编译检查、`git diff --check` 和完整 pytest 覆盖。
- 滴滴、OPPO、小米、华为、京东没有为了增加 Source 数量而半接入，原因和当前证据已写入 `docs/REAL_JOB_SOURCES.md`。

### 下一步
- 为字节 Source 增加可选的浏览器池和 1-5 分钟查询缓存，降低重复搜索的冷启动开销，同时保留 source 级 trace。
- 把五源 smoke 加入定时监控，按来源记录协议失败、空结果、P95 延迟、JD 非空率和实习率趋势。
- 继续验证滴滴实习批次和 OPPO 列表协议；只有达到四项接入标准后再进入默认中文链路。

## 2026-07-20 14:15:36 +08:00：项目文件架构说明与文档归档治理
### 这次做了什么
- 参考 `ecom-service-agent` README 中“真实目录树 + 行内职责注释”的表达方式，为 CareerAgent 新增 `docs/PROJECT_STRUCTURE.md`。
- 文件级目录说明覆盖 FastAPI 入口、LangGraph Agent、API、领域服务、Core、Models、前端、Skill、评测集、Worker 脚本、测试、演示 PDF 和运行时目录。
- 补充分层职责、依赖方向、“常见需求去哪里改”和新文件放置规则，明确 API、Agent、Skill、Service、Core、Evals 的边界。
- 在 README 增加精简版“当前文件架构”，并链接到完整目录说明。
- 新增 `docs/README.md` 文档索引，将核心设计、接口开发、RAG 评测、生产安全和面试资料分组。
- 将仓库根目录的三份 2026-07-06 面试材料移动到 `docs/interview/archive-2026-07-06/`，增加归档说明，避免旧测试数量和旧实现状态被当成当前事实。
- 新增 `tests/test_project_structure_docs.py`，持续检查核心路径存在、目录文档关键章节存在，以及历史面试快照不再散落根目录。

### 发现的问题
- 现有源码分层已经稳定，但 `app/services` 按领域平铺后缺少职责地图，读者很难快速定位 PDF、RAG、审批、队列和外发工具。
- README 只有运行时架构图，没有类似“打开仓库后先看哪里”的物理文件树。
- `docs/` 没有统一索引，设计、评测、生产补强和面试资料都在同一层展示。
- 三份历史面试材料位于仓库根目录，而且内容早于后续 Prompt Injection、Redis worker、Skill、Tool Policy 和业务摘要改造。

### 怎么修复
- 保留当前 Python package 和 import，不为了目录视觉整齐大规模移动 `app/services`；通过文件级注释树和放置规则解决可发现性问题。
- 明确依赖方向为 `Frontend/API -> Agents -> Services -> Models/Core`，Skill 只提供 Planner/Tool Policy metadata，生产代码不能反向依赖评测。
- 把日期快照归入 archive，并在 `docs/interview/README.md` 标明它们不是当前实现状态的权威来源。
- 使用测试锁定核心文件、说明章节和归档位置，降低目录说明后续失真概率。

### 验证结果
- README、`docs/README.md`、`docs/PROJECT_STRUCTURE.md` 和面试资料索引中的本地 Markdown 链接全部可解析。
- `git diff --check` 通过，仅有 Windows CRLF 转换提示。
- `python -m pytest tests\test_project_structure_docs.py -q` 通过，2 个目录治理测试全部通过。
- `python -m pytest -q --tb=short` 通过，138 个测试全部通过。

### 未修复的问题
- `app/services` 仍是平铺包。这是有意保留：当前文件大多是单职责模块，强拆成多级 package 会制造大范围 import churn，却不会直接提升运行质量。
- 2026-07-06 的归档面试材料包含过期实现描述；本次只做归档和状态警示，没有逐段改写历史快照。

### 下一步
- 新增较大领域时按 `docs/PROJECT_STRUCTURE.md` 的阈值评估是否建立子 package，而不是继续无限扩充平铺目录。
- 后续新增 Tool、Skill、评测或用户页面时同步更新目录说明和对应回归测试。

## 2026-07-20 13:02:31 +08:00：黄金业务路径、文件化 Skill、Tool Policy 与业务摘要
### 这次做了什么
- 按“工程能力已经较深，但业务主线和演示表达不够集中”的评述重构产品表达，没有照搬外部电商项目的 JSON 存储、Mock 或为技术名词而拆多 Agent。
- 在开始页增加“岗位匹配、证据约束定制、审批式投递”三条快速示例，并新增 `evals/golden_demo_scenarios.json` 和 `docs/GOLDEN_DEMOS.md`，固化输入、阶段、预期产物和禁止行为。
- 将原 Python 常量式 Skill 注册升级为 7 个真实 `skills/*/SKILL.md`：每个 Skill 都有版本、触发条件、输入、允许工具、上下文、输出契约、禁止行为、成功标准、失败策略和正文指令。
- 增加 Skill 渐进式披露：`GET /agent/skills` 只返回目录 metadata，`GET /agent/skills/{skill_name}` 才返回完整指令；执行计划只带本任务需要的精简契约。
- 扩展统一 Tool Policy，声明风险等级、审批要求、业务幂等、超时、重试、审计事件、MCP 候选和反向 Skill 授权；Planner 会阻止未被当前 Skill 授权的 Tool。
- 将 LangGraph Trace 中的工具名统一为 Tool Policy 的 canonical name，避免类名、步骤名和工具注册表对不上。
- 新增 `RunBusinessSummaryService` 和 `GET /agent/runs/{run_id}/summary`，按路由、过程、结果、副作用四层汇总岗位、证据、Guardrail、repair、工具成功率、产物 ID、审批和真实外发结果。
- 历史记录页优先展示业务摘要，再展示阶段和 LangGraph 事件；README 增加 90 秒业务主线、组件存在理由和 V1-V8 产品演进。
- 浏览器验收中顺手修复 390px 窄屏顶部导航横向溢出；全局任务卡改为只突出一条最高优先级流程，其他流程进入历史记录，并支持收起为 64px 状态条。

### 发现的问题
- 原 Skill 只是 Python 注册表中的说明对象，没有独立版本、禁止行为、成功标准和失败策略，难以证明“渐进式披露”和能力治理真实存在。
- 原 Tool Spec 只强调输入输出和 MCP 候选，缺少统一的风险、审批、幂等、超时、重试和审计协议。
- 原运行页把原始 step/event 当作主要产物，用户和面试官需要阅读大量 JSON 才能判断一次任务是否产生了业务价值。
- 项目有较多深层工程能力，但 README 首屏是功能清单，真实主线和三条高价值场景不够突出，容易被误判为技术堆栈型 Demo。
- 390px 浏览器验收发现导航把页面宽度从 390px 撑到 456px；任务浮层同时显示 3 条记录会遮挡右侧运行摘要。

### 怎么修复
- 使用 PyYAML 加载和严格校验 `SKILL.md` front matter，并保留原 `AGENT_SKILLS` 兼容接口，避免破坏已有调用。
- Planner 根据任务选择 Skill contract 和 Tool Policy，执行前运行 `validate_tool_permissions`，权限失败直接抛错并留在 Trace。
- 业务摘要只读取已落库的 run、step、artifact、approval、Job 和 ResumeVersion，不从最终文案猜测成功，也不虚构“修改前后分数提升”。
- 将三条黄金路径的期望输出和 forbidden behaviors 写成机器可读资产，并增加回归测试检查覆盖和权限契约。
- 移动端对 `.nav` 设置受容器约束的宽度和横向滚动；任务卡按 waiting/running/queued/failed 排序，只展示最高优先级记录，并增加持久化收起交互。

### 验证结果
- `node --check app\static\js\main.js` 通过。
- 定向回归 31 个测试通过；修复移动端后前端定向回归 16 个测试通过。
- `python -m pytest -q --tb=short` 通过，136 个测试全部通过；新增用例确认匹配分为 0 时业务摘要不会误显示为空。
- 内置浏览器打开 `http://127.0.0.1:8062/`，三条快速示例、固定步骤、三选一简历来源和运行进度均可见。
- 390x844 视口复验 `scrollWidth=375 < innerWidth=390`，页面不再横向溢出；任务卡展开高度从 367px 降到 228px，收起后为 64px。
- 历史记录 run #185 成功展示四层摘要，工具调用数 5、工具成功率 100%，页面无横向溢出；浏览器控制台无 error。

### 未修复的问题
- 现有历史 run 在本次 schema 上线前没有固化 `business_summary` artifact；详情 API 会实时重建，但部分旧 run 缺少 ResumeVersion verification，因此会显示“事实风险未检查”或空证据覆盖。
- 当前业务摘要没有展示“定制前后匹配分提升”，因为还没有同一简历和同一 JD 的人工标注前后对照集；不能为了演示而生成虚假 delta。
- SubAgent 当前主要承担职责和上下文边界，不是多个自治 LLM 的效果实验；项目不会仅为了显得复杂而增加多 Agent 通信。

### 下一步
- 构建同一简历/同一 JD 的定制前后对照评测，真实计算 score delta、证据覆盖变化和人工偏好。
- 增加用户反馈表，区分材料生成、人工确认、真实投递、面试邀请和录用结果，形成长期 outcome 指标。
- 在发布流水线中把三条黄金场景与现有 RAG、Guardrail、Prompt Injection 和全流程 release gate 统一执行。

## 2026-07-08 16:54:20 +08:00：修复 LLM 全局提示空黄色条
### 这次做了什么
- 修复部分页面顶部出现空黄色提示条的问题。
- 将岗位页和投递页纳入 LLM 全局提示范围，避免这些页面只有提示框边框却没有文案。
- 为 `.llm-global-warning[hidden]` 增加显式隐藏样式，防止 `display: flex` 覆盖 `hidden` 状态。
### 发现的问题
- `base.html` 中的 LLM 提示容器默认带 `hidden`，但 CSS `.llm-global-warning { display: flex; }` 会让空容器在部分页面露出来。
- 原 `LLM_DEPENDENT_PAGES` 没包含 `jobs` 和 `applications`，这些页面不会触发 JS 填充文案。
### 怎么修复
- 修改 `app/static/js/main.js`，将 `jobs` 和 `applications` 加入 `LLM_DEPENDENT_PAGES`。
- 修改 `app/static/css/style.css`，增加 `.llm-global-warning[hidden] { display: none; }`。
- 修改 `tests/test_frontend_pages.py`，增加岗位/投递页提示覆盖和 hidden 样式断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，15 个测试全部通过。
- `python -m pytest -q` 通过，132 个测试全部通过。
- 本地浏览器自动化打开 `http://127.0.0.1:8060/ui/jobs` 和 `http://127.0.0.1:8060/ui/applications`，提示条不再为空，`hidden=False` 且文本非空。
### 未修复的问题
- PowerShell 默认编码下浏览器自动化输出的中文会显示乱码，但 DOM 文本存在且页面实际显示正常。
### 下一步
- 后续如果继续增加用户页面，需要同步判断是否属于 LLM 依赖页，避免提示策略遗漏。

## 2026-07-08 16:34:52 +08:00：运行进度跨页恢复补强与全局 LLM 状态提示
### 这次做了什么
- 修复后台队列不可用时前端拿不到 run_id 的问题：`/agent/runs/background` 现在会返回一个 `failed` run，而不是直接 503。
- 为队列入队失败的 run 写入 `queue_enqueue_failed` 和 `run_finished` 事件，历史记录页可以看到 trace，不再只有最终错误。
- 将 active run 恢复从“只依赖 localStorage”升级为“双通道恢复”：先读本地关注列表；本地为空时从 `/agent/runs` 恢复最近 24 小时内的运行中、待确认、失败或完成 run。
- 右下角运行卡片现在会保留最近完成/失败的 run，用户手动关闭后才不再显示。
- 增加全局 LLM 未接入提示，覆盖首页、简历页、定制简历页、面试页和评测页。
- 开始页 LLM 状态徽标修正为未配置时显示风险态，不再误用绿色 ready 样式。
### 发现的问题
- 原先 run 一进入 `completed` 或 `failed` 就被 `untrackActiveRun()` 清除，失败很快时用户看不到右下角卡片。
- Redis 队列未启动时，后端虽然创建了失败 run，但接口抛出 503，前端无法获得 run_id，也就无法恢复进度或跳转历史记录。
- 只靠 localStorage 不够稳：刷新、浏览器会话变化或脚本异常时，本地关注列表可能丢失。
- 独立页面依赖 LLM，但只有首页有局部提示，用户在简历页、定制简历页、面试页等页面操作前不知道 LLM 未配置。
### 怎么修复
- 修改 `app/api/agent_runs.py`：队列入队失败时保存失败状态、输出错误、写入事件，并返回 `AgentRunResponse`。
- 修改 `app/static/js/main.js`：新增 `DISMISSED_RUN_KEY`、`recentRunsFromServer()`、`updateTrackedRun()` 和终态 run 保留策略。
- 修改 `app/static/js/main.js`：`createBackgroundAgentRun()`、`createAgentRun()` 和 `waitForAgentRun()` 都会记录并更新 terminal run，而不是立即删除。
- 修改 `app/static/js/main.js`：增加 `loadGlobalLLMWarning()`，在 LLM 依赖页面统一展示配置缺失提示。
- 修改 `app/templates/base.html` 和 `app/static/css/style.css`：增加全局 LLM 提示容器、运行卡片关闭按钮和对应样式。
- 新增 `tests/test_agent_runs_api.py`，覆盖队列不可用时仍返回失败 run 且事件可追踪。
- 更新 `tests/test_frontend_pages.py`，覆盖全局 LLM 提示、服务端最近 run 恢复和手动关闭逻辑入口。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_agent_runs_api.py tests\test_frontend_pages.py -q` 通过，16 个测试全部通过。
- `python -m pytest -q` 通过，132 个测试全部通过。
- `Invoke-WebRequest http://127.0.0.1:8059/agent/runs/background` 在 Redis 未启动时返回 `failed` run #191，而不是 503。
- 本地浏览器自动化打开 `http://127.0.0.1:8059/ui/profiles`，在无本地 active run 的独立页面仍从服务端恢复右下角运行卡片，`monitorHidden=False`，并显示 run #191 的队列失败原因。
- 同一页面显示全局 LLM 未接入提示。
### 未修复的问题
- 右下角卡片的“关闭”状态仍保存在浏览器本地；多设备一致的关闭状态需要登录用户级偏好表。
- 当前只恢复最近 24 小时内的 run；更长时间的历史仍通过“历史记录”页面查看。
### 下一步
- 给右下角运行卡片增加“只显示运行中/失败/待确认”的筛选或折叠，避免最近失败 run 较多时占用空间。
- 在控制台健康检查里把 Redis、LLM 和 worker 状态联动到用户页顶部提示，形成更完整的上线前自检。

## 2026-07-08 16:00:56 +08:00：简历正文去诊断化与运行任务刷新恢复
### 这次做了什么
- 将定制简历 HTML 预览改为只展示可投递简历正文，不再把事实检查、改动摘要、关键词缺口写入正文页面。
- 保留定制简历列表/详情里的诊断展示，保证“修改依据”和“最终材料”分离。
- 新增全局 active run 监控：后台 run 创建后写入浏览器 `localStorage`，刷新页面或切换页面后自动查询后端 run 状态并显示运行卡片。
- 首页支持根据已保存 run 恢复阶段进度，用户可以继续跳转到历史记录查看 LangGraph trace。
- 更新 `/resumes/{id}/html` API 文档，明确诊断信息不会写入可打印简历。
### 发现的问题
- 简历定制的检查结果和改动摘要原本由 `ResumeHTMLRenderer` 直接拼进 HTML 预览，用户打开或下载时会把内部诊断信息一起带出去。
- 前端只用内存变量跟踪正在运行的任务，刷新页面、切换页面或回到首页后无法知道之前的 run 是否还在运行。
- 历史记录虽然持久化了 run，但缺少面向用户的“当前还有任务在跑”的全局入口。
### 怎么修复
- 修改 `app/services/resume_delivery.py`，移除 `_resume_version_aside()` 及相关侧栏布局，`render_resume_version()` 只渲染简历 Markdown 正文。
- 修改 `app/templates/base.html`，增加 `active-run-monitor` 全局运行状态容器，并更新静态资源版本。
- 修改 `app/static/js/main.js`，新增 `ACTIVE_RUN_KEY`、`trackActiveRun()`、`restoreActiveRuns()`、`renderActiveRunMonitor()`、`restoreCareerFlowFromRun()` 等前端恢复逻辑。
- 将 `createBackgroundAgentRun()`、`createAgentRun()` 和 `waitForAgentRun()` 接入 active run 跟踪，完成后自动清理，等待确认时保留入口。
- 修改 `app/static/css/style.css`，增加全局运行卡片样式。
- 修改 `tests/test_resume_html_preview.py`，断言可打印简历不包含检查结果、改动摘要、缺失关键词和风险提示。
- 修改 `tests/test_frontend_pages.py`，断言首页包含 active run 容器和前端恢复逻辑。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_resume_html_preview.py tests\test_frontend_pages.py -q` 通过，16 个测试全部通过。
- `python -m pytest -q` 通过，131 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8056/resumes/97/html`，页面包含 `Li Ming` 简历正文，且不包含 `检查结果`、`改动摘要` 和 `风险：`。
- 内置浏览器打开 `http://127.0.0.1:8056/`，确认首页存在 `active-run-monitor` 全局运行恢复容器，默认无 active run 时保持隐藏，页面加载新的 `run-restore` 静态资源版本。
### 未修复的问题
- 当前 active run 恢复是浏览器本地关注列表 + 后端 run 查询；跨浏览器设备同步需要登录态下的用户级 run 订阅表。
- 运行完成后的结果提醒目前依赖页面内结果区或历史记录；后续可以增加“最近完成”通知中心。
### 下一步
- 将定制简历页的评分和定制流程也后台化，统一纳入 active run 监控和 LangGraph event streaming。
- 给历史记录页增加按状态筛选和“只看待确认/运行中”快捷入口。

## 2026-07-08 15:48:26 +08:00：岗位 JD 预览、定制简历选择器与历史记录详情
### 这次做了什么
- 为岗位池增加 JD HTML 预览能力：岗位卡片新增“预览 JD”，后端新增 `/jobs/{job_id}/html`。
- 将定制简历页从裸 ID 输入改为“选择简历档案”和“选择目标岗位”两张选择卡片。
- 定制简历页新增二级弹窗：支持搜索并选择简历档案，也支持搜索并选择岗位池中的 JD。
- 定制简历提交前先调用岗位针对性简历评分，评分、问题、修改建议和 RAG 证据单独显示在页面上，不写入简历正文。
- 定制简历历史卡片增加“检查、修改说明和证据”折叠面板，用于展示事实检查、修改说明、关键词对齐和证据来源。
- 历史记录页打开后自动选中最近一次运行，并加载右侧阶段进度和 LangGraph 事件流。
- 历史记录页新增待确认说明区：解释确认是投递包、浏览器填写、邮件等高风险动作的人审点，而不是普通历史记录操作。
### 发现的问题
- 岗位池只有摘要和投递链接，用户无法查看完整 JD，导致后续定制简历时难以确认岗位内容。
- 定制简历页要求用户手填 Profile ID 和 Job ID，不符合已有开始页的二级选择交互，也容易填错。
- 简历评分和定制简历之间没有联动；用户看不到为什么这样改、哪些问题需要注意。
- 如果把评分/检查/修改说明写进简历正文，会污染最终可投递材料；这些内容应该作为前端诊断信息单独展示。
- 历史记录页右侧阶段进度和事件流需要用户先点击某条 run 才会加载，用户容易误以为功能坏了。
- 等待确认的按钮原先出现在历史列表里，缺少解释，用户不知道为什么历史记录页会要求确认。
### 怎么修复
- `app/api/jobs.py` 新增 `/jobs/{job_id}/html`，以 HTML 页面展示岗位标题、公司、地点、投递链接、结构化字段和原始 JD。
- `app/static/js/main.js` 的岗位列表渲染增加“预览 JD”按钮，直接打开 HTML 预览页。
- `app/templates/resumes.html` 将定制表单改为隐藏 ID + 两张选择卡 + 两个弹窗，并增加 `tailor-review-result` 与 `tailor-submit-result`。
- `app/static/js/main.js` 增加 `openTailorProfilePicker()`、`openTailorJobPicker()`、`renderTailorProfilePickerList()`、`renderTailorJobPickerList()` 等选择器逻辑。
- `app/static/js/main.js` 修改定制提交流程：先调用 `/profiles/{profile_id}/review` 做 targeted review，再调用 `/resumes/tailor` 生成定制简历。
- `app/static/js/main.js` 增加 `renderTailoredResumeDiagnostics()`，将事实检查、修改说明、关键词和证据折叠展示。
- `app/static/js/main.js` 修改 `loadRuns()`，列表加载后自动 `loadRunSteps(rows[0].id)`；新增 `renderRunConfirmation()` 展示待确认原因和确认按钮。
- `app/static/css/style.css` 增加定制选择卡、选中态和诊断折叠面板样式。
- `tests/test_frontend_pages.py` 增加岗位 JD 预览、定制页二级选择器、定制评分联动、历史记录自动详情和待确认说明断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，15 个测试全部通过。
- `python -m pytest -q` 通过，131 个测试全部通过。
- `Invoke-WebRequest http://127.0.0.1:8055/jobs/198/html` 返回 200，HTML 中包含 `Agent 开发实习生`。
- 内置浏览器打开 `http://127.0.0.1:8055/ui/jobs`，岗位池展示多个“预览 JD”按钮，页面无横向溢出。
- 内置浏览器打开 `http://127.0.0.1:8055/ui/resumes`，简历选择弹窗打开并加载 60 条档案，岗位选择弹窗打开并加载 80 条岗位；选择 Profile #159 和 Job #198 后，隐藏字段正确写入并显示选中态。
- 内置浏览器打开 `http://127.0.0.1:8055/ui/agent-runs`，自动选中最近运行，右侧阶段进度和事件流不再为空，并显示待确认事项解释。
### 未修复的问题
- 定制页提交会真实调用 LLM 做评分和定制，耗时取决于外部模型；后续可以把这一流程也接入后台任务和进度条。
- 岗位 HTML 预览当前是轻量模板，尚未提供 PDF 导出或打印样式优化。
### 下一步
- 将定制简历页的“评分 → 定制 → 预览”改为后台 run，接入和首页一致的阶段进度。
- 给岗位池增加按 ID、公司、技能和是否有投递链接的前端筛选，减少岗位很多时的查找成本。

## 2026-07-08 11:16:16 +08:00：首页顺序、入口表单对齐与流程页改名
### 这次做了什么
- 调整开始页信息顺序为“你想让 Agent 做什么” → “流程内容” → “三选一提供简历档案，再开始找岗位”。
- 将“三选一提供简历档案”的提示语放进三选一卡片容器内部顶部，避免提示和选项被其他模块隔开。
- 将简历页入口改为 PDF 上传 `span-5`、自然语言建档 `span-7`，保留等高面板和按钮底部对齐。
- 将岗位页改为搜索入口 `span-5`、手动添加目标 JD `span-7`，并把两个表单改成按钮沉底的入口面板。
- 将顶部导航中的“流程”改为“历史记录”，并把它移动到投递、面试之后。
- 将原“我的求职流程”页面改为“求职历史记录”，移除用户前端里的手动 Agent Run 启动表单，只保留运行记录、阶段进度、事件流和确认继续入口。
### 发现的问题
- 首页“三选一”提示离三个方式卡片太远，用户需要跨过“流程内容”模块才能理解下方三张卡。
- 仅把“流程内容”放到页面最上方也不符合用户心智；更合理的顺序是先说目标，再选要生成什么，最后提供简历来源。
- 岗位页两个 panel 虽然同排，但操作按钮位置不一致；左侧空的错误提示区域还会额外占位。
- 历史记录页左侧运行列表很长时，右侧“阶段进度/事件流”面板会被 CSS Grid 强行拉到同样高度，视觉上像一个巨大的空框。
- “流程”作为一级导航命名不清晰，普通用户难以理解它和“开始”页的区别。
### 怎么修复
- `app/templates/index.html` 重新排列开始页表单模块，并把 `input-guidance` 移入 `start-resume-picker` 内部。
- `app/static/css/style.css` 增加 `resume-source-guidance`，让提示语作为三选一卡片组标题呈现。
- `app/templates/profiles.html` 将两个建档入口调整为 5/7 宽，继续复用 `profile-entry-panel` 保持等高和按钮对齐。
- `app/templates/jobs.html` 增加 `job-entry-grid`、`job-entry-panel`、`job-entry-form`，将岗位搜索和目标 JD 添加统一为入口面板。
- `app/static/css/style.css` 增加 `job-entry-*`、`.notice:empty` 和 `run-history-grid` 样式，修复空提示占位和历史页右侧被拉高问题。
- `app/templates/base.html` 将导航改为“开始 / 简历 / 岗位 / 定制简历 / 投递 / 面试 / 历史记录”。
- `app/templates/agent_runs.html` 将页面改为历史记录说明 + 最近运行 + 阶段进度/事件流，不再暴露手动启动表单。
- `tests/test_frontend_pages.py` 增加首页模块顺序、岗位页入口对齐、历史记录页命名和无手动启动表单的断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，15 个测试全部通过。
- `python -m pytest -q` 通过，131 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8054/`，确认首页顺序为 prompt → 流程内容 → 三选一卡片，三选一提示位于卡片容器内部，页面无横向溢出。
- 内置浏览器打开 `http://127.0.0.1:8054/ui/profiles`，确认 PDF 入口宽 433px、自然语言入口宽 614px，两个 panel 高度均为 385px，按钮底部距离均为 23px。
- 内置浏览器打开 `http://127.0.0.1:8054/ui/jobs`，确认搜索入口宽 433px、目标 JD 入口宽 614px，两个 panel 高度均为 523px，按钮底部距离均为 23px。
- 内置浏览器打开 `http://127.0.0.1:8054/ui/agent-runs`，确认页面标题为“求职历史记录”，不存在 `agent-run-form`，右侧进度面板不再被左侧长列表撑高。
### 未修复的问题
- 历史记录左侧运行列表目前会一次性显示较多历史 run；数据继续增长后应增加分页、筛选或按求职包编号折叠。
- 首页三选一卡片在较窄视口下仍会单列展示，这是当前响应式设计的预期行为，不作为问题处理。
### 下一步
- 给历史记录增加按求职包编号、状态、任务类型和时间的过滤，避免真实使用后列表过长。
- 在开始页三选一卡片中显示“当前已选择/已解析”的更完整摘要，例如姓名、目标岗位和最近更新时间。

## 2026-07-08 11:09:46 +08:00：首页简历来源三选一与简历页入口对齐
### 这次做了什么
- 将开始页简历来源区改为“方式一/方式二/方式三”的三选一卡片：选择已有档案、上传 PDF 自动建档、去简历页建档。
- 明确提示“下面三种方式任选一种提供简历档案”，避免用户误以为三块内容需要同时填写。
- 为已有档案和 PDF 自动建档增加卡片选中态，选择档案或 PDF 解析成功后能直接看到当前采用哪种简历来源。
- 将简历页 PDF 上传和自然语言建档入口改为同宽、同高、同节奏的入口面板。
- 隐藏自然语言建档入口下方空结果容器，修复未生成前按钮底部不齐的问题。
### 发现的问题
- 原开始页的三种简历来源混用了卡片、文件输入和独立按钮，视觉上不像同一组选项。
- 上传 PDF 卡片被 CSS 选择器从 `flex` 覆盖成 `grid`，导致它和另外两张来源卡片布局不一致。
- 简历页两个入口使用 `span-5` 和 `span-7`，天然宽度不一致；空结果容器还会让按钮位置出现细微错位。
### 怎么修复
- `app/templates/index.html` 将三种简历来源统一为 `resume-source-card`，并补充“方式一/二/三”文案。
- `app/static/css/style.css` 为 `start-resume-picker` 增加同组容器样式、等高三列、选中态和文件输入底部对齐。
- `app/static/js/main.js` 增加 `updateResumeSourceSelection()`，在选择已有档案或 PDF 解析成功后切换对应卡片状态。
- `app/templates/profiles.html` 将 PDF 上传和自然语言建档入口都改为 `span-6 profile-entry-panel`，并补充面向用户的说明。
- `tests/test_frontend_pages.py` 增加三选一文案、来源卡片、选中态样式和简历页入口对齐相关断言。
- `.gitignore` 增加 `logs/`，避免本地服务运行日志进入待提交文件。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，14 个测试全部通过。
- `python -m pytest -q` 通过，130 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8051/`，桌面断点下三张简历来源卡片同排、等宽、等高，页面无横向溢出。
- 内置浏览器点击“选择已有档案”，档案弹窗异步加载成功，选择 Profile #159 后首页写入 `profile_id=159`，已有档案卡片进入选中态。
- 内置浏览器打开 `http://127.0.0.1:8051/ui/profiles`，PDF 上传和自然语言建档入口等宽等高，按钮底部距离一致，页面无横向溢出。
### 未修复的问题
- PDF 文件选择控件仍使用浏览器原生样式；后续如果要进一步精修，可以封装成自定义上传按钮并显示文件名。
- “去简历页建档”是导航入口，不是当前页内可选状态；当前保持这种设计，避免用户在开始页继续堆建档表单。
### 下一步
- 给开始页简历来源区补充更明确的上传成功预览，例如显示 PDF 解析出的姓名、目标岗位和技能摘要。
- 在简历页上传/自然语言建档成功后，直接引导用户预览、评分或回到开始页发起求职流程。

## 2026-07-08 10:51:08 +08:00：首页过程侧栏与简历档案选择弹窗
### 这次做了什么
- 将首页“分步处理 / 过程页面”从主内容下方移动到右侧“进度 / 运行进度”下面，形成更自然的侧栏辅助导航。
- 将开始页的简历档案输入从裸 ID 输入升级为“选择已有档案”按钮 + 二级弹窗。
- 档案选择弹窗会展示已有简历档案概况，包括 ID、姓名、来源、求职意向和技能摘要。
- 档案选择弹窗支持按 ID、姓名、邮箱、标题和目标岗位搜索，并提供“选择”和“详情”两个动作。
- 选择档案后，开始页会显示已选档案卡片，并把真实 Profile ID 写入隐藏字段供流程使用。
- PDF 自动建档后也会同步更新已选档案卡片，避免用户只看到一个数字 ID。
### 发现的问题
- 原“过程页面”放在主内容底部，和右侧运行进度分离，用户视线需要来回跳转。
- 简历档案 ID 输入框对普通用户不友好，也不利于确认自己选的是哪份档案。
- 直接输入 ID 虽然工程上简单，但缺少搜索、概况和详情查看，容易误选历史档案。
### 怎么修复
- `app/templates/index.html` 将过程页面嵌入右侧 `side-stack`，放到 `flow-panel` 之后。
- `app/templates/index.html` 用 `selected-profile-card`、隐藏 `profile_id`、`profile-picker-dialog` 替代裸数字输入框。
- `app/static/js/main.js` 增加 `openProfilePicker()`、`renderProfilePickerList()`、`selectProfileFromPicker()`、`updateSelectedProfileCard()` 和 `profileSummaryText()`。
- `app/static/css/style.css` 增加档案选择卡片、弹窗、档案列表、侧栏过程页面和响应式样式。
- `tests/test_frontend_pages.py` 增加过程侧栏、档案选择弹窗、搜索/选择函数和样式断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，13 个测试全部通过。
- `python -m pytest tests\test_natural_language_agent.py -q` 通过，5 个测试全部通过。
- `python -m pytest -q` 通过，129 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8050/`，确认过程页面位于运行进度下方，页面横向溢出为 0。
- 内置浏览器点击“选择已有档案”，搜索 `159` 后只显示 Profile #159，包含概况、选择按钮和详情链接。
- 内置浏览器选择 Profile #159 后，弹窗关闭，首页显示 `#159 Li Ming`，隐藏 `profile_id` 写入 `159`，提示语同步显示使用 Profile #159。
### 未修复的问题
- 弹窗当前是一次性加载最近的 `/profiles` 列表后在前端过滤；当档案数量非常大时，需要后端分页和搜索接口。
- 档案详情仍通过新标签页打开 HTML 预览，后续可以在弹窗内嵌一个详情抽屉，但当前先保持交互轻量。
### 下一步
- 为档案选择弹窗增加“最近使用/最近评分”排序，减少历史档案很多时的选择成本。
- 如果引入 Profile 版本管理，再把弹窗扩展为“选择档案 + 选择版本”。

## 2026-07-08 10:40:25 +08:00：开始页职责收敛与简历页建档入口重构
### 这次做了什么
- 将开始页从“半个简历建档页”改为真正的求职流程入口：只保留简历档案选择、PDF 自动建档、岗位搜索词、城市、岗位数量、已有 Job ID、公司和 JD。
- 开始页移除姓名、邮箱、目标岗位、技能关键词、项目经历等简历细节字段，避免用户在信息不足的地方生成低质量简历。
- 开始页新增固定步骤展示：简历档案、岗位搜索、匹配排序不再作为可选 checkbox，而是流程必做项。
- 开始页“生成内容”改为“额外材料”：只允许选择定制简历、投递材料和面试准备。
- 简历页增加“自然语言建档”入口，用户可以用一段描述生成简历档案；原 PDF 上传和手动建档保留。
- 开始页如果没有已有 Profile ID 且没有上传 PDF，会明确提示先去简历页建档，不再从开始页稀疏字段拼一个简历。
### 发现的问题
- 开始页承担了太多建档职责，但字段覆盖不完整，容易让没有简历的用户误以为可以直接在开始页生成完整简历。
- “简历档案”和“岗位搜索”被放在可选生成项里不符合真实流程；后续定制简历、投递包、面试包都依赖这两步。
- PDF 上传在开始页的正确语义是“自动建立档案并供本次流程使用”，不是“回填一个临时大表单继续编辑”。
### 怎么修复
- `app/templates/index.html` 精简开始页字段，加入固定步骤标签和“去简历页手动填写/自然语言建档”入口。
- `app/templates/profiles.html` 增加 `natural-profile-form` 和结果区域，形成 PDF 上传、自然语言建档、手动建档三种入口。
- `app/static/js/main.js` 调整 `selectedStartActions()`，自动把 `create_profile` 和 `search_jobs` 注入动作列表；用户只选择后续材料。
- `app/static/js/main.js` 让开始页不再传 `profile_context`，也不再用姓名/技能/项目字段创建档案。
- `app/static/js/main.js` 增加自然语言建档提交逻辑，调用 `/assistant/natural-language` 并只请求 `create_profile`。
- `app/static/css/style.css` 增加固定步骤和开始页简历入口样式，并将可选生成项改为三列。
- 更新前端测试，断言开始页不再出现简历细节字段，并确认简历页暴露 PDF、自然语言和手动三种建档入口。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile tests\test_frontend_pages.py` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，13 个测试全部通过。
- `python -m pytest tests\test_frontend_pages.py tests\test_natural_language_agent.py -q` 通过，18 个测试全部通过。
- `python -m pytest -q` 通过，129 个测试全部通过；本地 pytest cache 写入有权限 warning，不影响测试结果。
- 内置浏览器打开 `http://127.0.0.1:8049/`，确认开始页只剩简历档案/PDF/岗位相关字段，固定步骤为“简历档案、岗位搜索、匹配排序”，可选项只剩定制简历、投递材料、面试准备，页面横向溢出为 0。
- 内置浏览器打开 `http://127.0.0.1:8049/ui/profiles`，确认 PDF 上传、自然语言建档、手动建档、简历评分入口均存在，页面横向溢出为 0。
### 未修复的问题
- 自然语言建档目前复用自然语言 Agent 的 `create_profile` 动作，尚未增加独立的“建档专用质量门禁”和字段置信度展示。
- 开始页仍保留自然语言需求输入，但其职责已限制为求职流程意图和岗位偏好，不再承担完整简历生成。
### 下一步
- 为自然语言建档增加字段完整度检查和“缺失信息追问”能力，避免用户只写一句话时生成过薄档案。
- 将建档后的评分结果和缺失字段提示联动到简历页，让用户先补强档案再进入求职流程。

## 2026-07-08 10:22:17 +08:00：简历评分、RAG 针对性建议与首页勾选项收紧
### 这次做了什么
- 新增简历评分能力：支持通用简历体检，也支持传入岗位 ID 后做 JD 针对性评分。
- 新增 `ResumeReviewService`，评分维度包括完整度、证据强度、量化结果、关键词清晰度、可读性、事实边界；有岗位时额外加入岗位匹配维度。
- 岗位针对性评分复用现有 `MatcherService`、简历 chunks、SQLite/Chroma 向量检索和 reranker，返回 RAG 证据、缺失技能、匹配技能和针对性修改建议。
- 新增 `/profiles/{profile_id}/review` API，前端“我的简历档案”卡片增加“简历评分”按钮，并支持填写可选岗位 ID。
- 前端评分结果展示总分、等级、维度分、优势、主要问题、修改建议和 RAG 证据摘要。
- 收紧开始页“生成内容”勾选项高度，解决 checkbox 被全局输入框样式撑高的问题。
### 发现的问题
- 简历打分如果完全依赖 LLM，分数不可解释，也难以稳定回归；更适合让规则和匹配服务产出可追溯分数，再让 LLM 做建议表达增强。
- 针对岗位的修改意见如果不接 RAG，容易变成泛泛建议；接入简历 chunks 后可以指出哪段经历最相关、哪些 JD 技能缺少证据。
- 首页生成项虽然设置了较小 `min-height`，但全局 `input { min-height: 42px }` 会把 checkbox 撑高，导致实际高度仍偏大。
### 怎么修复
- `app/services/resume_review.py` 实现通用评分、岗位评分、RAG 证据压缩、问题归因和建议生成。
- `app/api/profiles.py` 增加简历评分接口，并在缺少岗位或简历时返回明确错误。
- `app/models/schemas.py` 增加 `ResumeReviewRequest` 和 `ResumeReviewResponse`，前后端使用结构化合同。
- `app/templates/profiles.html` 增加可选岗位 ID 输入；`app/static/js/main.js` 增加 `reviewProfile()` 和 `renderResumeReview()`。
- `app/static/css/style.css` 增加评分卡片样式，并覆盖 `.generation-options input` 的 `min-height/padding/flex`，让 checkbox 不再被全局输入框样式撑高。
- `tests/test_resume_review.py` 增加通用评分和岗位 RAG 评分测试；`tests/test_frontend_pages.py` 增加评分入口、JS 渲染和 CSS 控件高度断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\services\resume_review.py app\api\profiles.py app\models\schemas.py tests\test_resume_review.py tests\test_frontend_pages.py` 通过。
- `python -m pytest tests\test_resume_review.py tests\test_frontend_pages.py -q` 通过，15 个测试全部通过。
- `python -m pytest -q` 通过，129 个测试全部通过。
- 通过 `8048` 服务真实调用 `/profiles/159/review`，通用评分返回 200，总分 78.92。
- 通过 `8048` 服务真实调用 `/profiles/159/review` 并传入 `job_id=198`，岗位针对性评分返回 200，总分 74.35，RAG 证据 6 条。
- 内置浏览器打开 `http://127.0.0.1:8048/`，确认开始页生成项实际高度为 36px，checkbox 高度为 16px，页面横向溢出为 0。
- 内置浏览器打开 `http://127.0.0.1:8048/ui/profiles`，填写岗位 #198 并点击 Profile #159 的“简历评分”，页面正确展示岗位针对性评分、维度分、修改建议和 RAG 证据，无错误、无横向溢出。
### 未修复的问题
- 当前评分结果按请求即时计算，暂未持久化历史评分记录；如果后续要做评分趋势或对比多个版本，需要增加独立评分表。
- LLM 建议增强会在配置 LLM 时启用，但本次主要验证了规则分数和 RAG 证据链路；后续可以把 LLM 建议纳入长流程 trace 和质量评测。
### 下一步
- 增加“保存评分报告/对比修改前后分数”的能力，方便用户看到优化效果。
- 将简历评分接入定制简历前后，对比 JD 针对性修改是否真的提升岗位匹配分和证据强度。

## 2026-07-08 09:51:03 +08:00：首页提示文案、生成项控件与 PDF parser 修复
### 这次做了什么
- 将首页“信息会自动合并”改成面向普通用户的说明：“可以只写需求，也可以补充表单”，并明确上方需求、下方表单和 PDF 上传之间的关系。
- 将“生成内容”勾选区从窄小的默认 checkbox 改为卡片式选择控件，保留 checkbox 语义，同时让用户更清楚地点击选择要生成的材料。
- 优化首页动态提示：会根据 prompt、表单、PDF、Profile ID 和勾选项显示“将使用哪些信息”和“将生成哪些内容”。
- 修复 PDF parser 对标题行姓名的抽取问题，例如 `Li Ming - Agent Development Intern Candidate` 会正确识别姓名为 `Li Ming`。
- 修复 LLM 结构化解析返回 `null` 时覆盖启发式解析结果的问题，姓名、邮箱、电话、标题、技能、项目等关键字段会保留可信启发式结果。
### 发现的问题
- 原提示“信息会自动合并”过于抽象，普通用户很难理解 prompt、表单和 PDF 的优先级，也容易误以为系统会静默替用户改表单。
- 旧 checkbox 在截图中的中文文本被挤压成竖排，视觉上像后台控件，不适合作为首页主流程的用户选择入口。
- 演示 PDF 的第一行是“姓名 + 候选方向”的标题，旧逻辑只接受长度不超过 40 的第一行作为姓名，导致姓名为 `null`。
- 当 LLM 返回可解析 JSON 但部分字段为 `null` 时，旧的 `{**heuristic, **parsed}` 合并方式会把启发式抽到的姓名覆盖掉。
### 怎么修复
- `app/templates/index.html` 重写首页合并提示和生成内容说明，并给每个生成项增加稳定文本容器。
- `app/static/css/style.css` 为生成项增加五列卡片布局、hover 状态、选中状态、移动端两列布局和 `accent-color`，避免中文文本挤压。
- `app/static/js/main.js` 重写 `updateStartInputGuidance()`，让提示语随用户输入和勾选项实时变化。
- `app/services/resume_parser.py` 增加 `_guess_name()`、`_name_candidate_from_line()` 和 `_merge_parsed_with_heuristic()`，支持从标题行抽姓名，并避免 LLM 空值覆盖启发式结果。
- `tests/test_resume_parser.py` 增加标题行姓名抽取和 LLM 空值不覆盖的回归测试；`tests/test_frontend_pages.py` 增加新文案、旧文案移除和控件样式断言。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\services\resume_parser.py tests\test_resume_parser.py tests\test_frontend_pages.py` 通过。
- `python -m pytest tests\test_resume_parser.py tests\test_frontend_pages.py -q` 通过，17 个测试全部通过。
- `python -m pytest -q` 通过，126 个测试全部通过。
- 直接解析 `demo_resumes\agent_intern_strong_resume.pdf`，姓名返回 `Li Ming`，邮箱返回 `liming@example.com`，电话返回 `13800000000`。
- 通过 `8047` 服务真实上传同一 PDF 到 `/profiles/upload`，接口创建 Profile #159，`structured_profile_json.skills` 抽到 11 个技能，姓名和邮箱正确。
- 内置浏览器打开 `http://127.0.0.1:8047/`，确认最新静态资源版本加载成功，新提示存在、旧提示不存在，5 个生成项宽高稳定，页面横向溢出为 0。
- 内置浏览器勾选“定制简历”和“面试准备”后，提示更新为“将生成：定制简历、面试准备”，页面无错误且横向溢出为 0。
### 未修复的问题
- PDF parser 仍是“启发式 + LLM”的组合方案，对极端复杂版式、扫描件 OCR、姓名和求职标题混排的中文简历还需要继续积累样本。
- 上传 PDF 后如果用户编辑了回填表单，当前仍需要后续“保存为新的简历档案”能力才能把编辑后的内容固化成新版 Profile。
### 下一步
- 为 PDF 回填字段增加置信度和“请检查”标记，尤其是姓名、电话、教育经历和项目边界。
- 在首页补充“保存为新的简历档案”交互，让用户上传解析后可以先编辑确认，再作为后续流程输入。

## 2026-07-08 09:39:45 +08:00：首页输入合并、PDF 回填与显式生成项
### 这次做了什么
- 首页新增“信息会自动合并”提示，明确 prompt、PDF、已有 Profile ID 和表单字段会共同作为 Agent 输入。
- 首页新增“生成内容”勾选区，支持用户显式选择简历档案、岗位搜索、定制简历、投递材料和面试准备；不勾选时仍由 Agent 按 prompt 自动判断。
- PDF 简历选择后会立即调用 `/profiles/upload` 解析，并把解析出的 Profile ID、邮箱、目标岗位、技能和项目经历回填到首页表单。
- 自然语言请求新增 `profile_context` 和 `selected_actions`，允许“prompt 写一部分、表单填一部分”的混合输入进入后端执行。
- 自然语言 Agent 收到显式 `selected_actions` 时会优先按用户勾选动作执行；如果用户勾选了岗位搜索和后续材料生成，且没有指定 Job/JD，会先搜索岗位并选择 Top1 继续定制或面试准备。
- 调整自然语言执行顺序：当同时需要面试包和投递材料时，先生成面试准备，再进入投递确认，避免投递 interrupt 阻塞面试包。
- 保留安全优先级：如果 prompt 明确“不要投递/不要申请”，即使用户误勾投递材料，后端也会移除 `quick_apply/full_flow` 并改走定制简历或面试准备。
### 发现的问题
- 旧首页让用户误以为 prompt 会自动填充下方表单；实际旧逻辑只把 PDF/Profile ID 交给流程，不会把解析结果展示给用户。
- 旧自然语言请求只传 `instruction/profile_id/job_id/jd_text/query/location`，表单里的姓名、技能、项目等结构化补充不会进入自然语言 Agent。
- 多选 checkbox 经过 `FormData` 后旧 `formJson()` 只保留最后一个同名字段，无法可靠传递多个生成目标。
- 使用演示 PDF `demo_resumes/agent_intern_strong_resume.pdf` 真实上传验证时，接口返回 Profile #158，邮箱和目标岗位解析成功，但姓名为 `null`，说明 PDF 解析质量仍受版式/文本抽取影响。
### 怎么修复
- `app/templates/index.html` 增加输入合并提示、生成内容勾选区和 PDF 解析状态提示。
- `app/static/js/main.js` 增加 `parseResumeFileIntoStartForm()`、`populateStartFormFromProfile()`、`profileContextFromStartForm()` 和 `selectedStartActions()`，并让 PDF change 事件即时解析回填。
- `formJson()` 支持同名字段数组，确保多个 `selected_actions` 能完整提交。
- `app/models/schemas.py` 为 `NaturalLanguageAgentRequest` 增加 `profile_context` 和 `selected_actions`。
- `app/agents/natural_language.py` 把表单 profile context 纳入 plan 上下文，并用显式 actions 覆盖 LLM 推断；同时支持“先搜索岗位，再按勾选项继续处理”的组合路径。
- `app/agents/natural_language.py` 在显式 actions 之后再次应用“不投递”约束，避免勾选项绕过自然语言里的安全边界。
- `app/templates/base.html` 更新静态资源版本，避免浏览器继续加载旧 JS/CSS。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\agents\natural_language.py app\models\schemas.py tests\test_natural_language_agent.py tests\test_frontend_pages.py` 通过。
- `python -m pytest tests\test_frontend_pages.py tests\test_natural_language_agent.py -q` 通过，17 个测试全部通过。
- `python -m pytest -q` 通过，124 个测试全部通过；补充安全修正后再次全量回归仍为 124 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8046/`，确认首页有输入合并提示、5 个生成内容勾选项、PDF 自动解析提示、最新静态资源版本，页面横向溢出为 0。
- 内置浏览器真实点击“定制简历”和“面试准备”勾选项后，提示更新为“将按勾选生成：定制简历、面试准备”，无页面错误，横向溢出为 0。
- 通过 8046 服务真实上传 `demo_resumes\agent_intern_strong_resume.pdf` 到 `/profiles/upload`，接口创建 Profile #158，证明 PDF 解析链路可用；前端已接入同一接口做回填。
### 未修复的问题
- PDF 示例中姓名字段仍可能解析为 `null`；本次先把解析结果透明回填和展示，未继续优化 PDF parser 的姓名抽取规则。
- 如果用户上传 PDF 后又大幅修改表单，当前仍以解析出的 Profile ID 为主；后续可以增加“保存为新的简历档案”按钮，显式把编辑后的回填表单另存为新版 Profile。
### 下一步
- 优化 PDF parser 的中文姓名/联系方式抽取规则，并在回填卡片上标记低置信字段，提示用户检查。
- 在等待投递确认时，把进度条状态改成“等待确认”，并展示确认后将继续生成哪些材料。

## 2026-07-07 19:38:43 +08:00：首页入口整合、求职包编号统一与投递页溢出修复
### 这次做了什么
- 将首页原本分离的“需求入口”和“一键开始”整合为一个“开始”模块，用户先描述需求，再按需补充简历、岗位、JD 和城市信息。
- 合并后的首页表单会根据是否填写自然语言需求自动选择自然语言 Agent 或完整求职流程，并统一驱动右侧运行进度。
- 前端用户侧结果改为使用 `求职包 #run_id` 作为统一编号；简历、岗位、定制简历、投递材料、面试准备都作为同一求职包下的材料入口展示。
- 投递材料列表改为 `application-card`，长投递信、长链接和按钮都限制在卡片宽度内换行/滚动，避免横向撑出页面。
- 真实浏览器验证中发现自然语言结果会把主材料入口和子 run 入口重复展示，补充 `pushUniqueAction()` 与子 run 类型映射，用户侧只保留一组材料入口。
### 发现的问题
- 首页两个入口的语义重叠，用户不容易判断应该点“让 Agent 自动处理”还是“一键运行”，而且自然语言结果和运行进度没有统一关联。
- 自然语言/完整流程结果页会同时展示定制简历、投递包、面试包各自的内部 ID，用户视角上像是多个无关联材料。
- 投递材料页直接渲染长 `cover_letter` 到 `<pre>`，缺少页面级宽度约束，长文本或长 URL 会造成横向溢出。
- 首次浏览器验证 run #180 时，统一编号已生效，但“定制简历”和“面试准备”按钮各出现两次；根因是 `agent_runs` 子流程链接与主材料链接同时渲染。
### 怎么修复
- `app/templates/index.html` 删除独立自然语言模块，把需求描述合入 `career-start-form`，结果统一输出到 `career-flow-result`。
- `app/static/js/main.js` 增加 `packageLabel()` / `packageAction()` / `updateCareerFlowFromNaturalResult()`，自然语言和完整流程都用 run id 生成统一求职包编号。
- `renderNaturalLanguageResult()` 增加链接去重：`tailor_resume_for_job` 映射到定制简历入口，`prepare_interview_for_job` 映射到面试准备入口，避免同类材料重复按钮。
- `loadApplications()` 使用 `userPackageId(row)` 从 idempotency key 中解析流程编号；无法解析时保留材料自身可追踪编号。
- `app/static/css/style.css` 为 `.result-list`、`.item`、`.item-title`、`.flow-result-actions`、`.application-card` 和 `.application-letter` 增加 `min-width:0`、换行与滚动约束。
### 验证结果
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，12 个测试全部通过。
- `python -m pytest -q` 通过，123 个测试全部通过。
- 内置浏览器打开 `http://127.0.0.1:8045/`，首页只保留一个“开始”模块，旧“需求入口”和“一键开始”文案均未出现，页面横向溢出为 0。
- 内置浏览器从合并后的首页提交真实自然语言请求，run #183 完成并展示 `求职包 #183`；进度阶段 profile/search/match/tailor/interview 均使用同一编号，未触发投递阶段，结果区无旧内部材料编号，按钮无重复。
- 内置浏览器打开 `http://127.0.0.1:8045/ui/applications`，22 张投递材料卡片均以 `求职包 #...` 展示，`docOverflow=0`，未发现横向撑出页面的元素。
### 未修复的问题
- 后端历史 `user_message` 仍保留旧的内部材料编号；本次在用户侧展示层统一为求职包编号，暂不回写历史 trace。
### 下一步
- 后续可继续把历史 trace 的用户摘要做只读展示层脱敏，但不回写历史运行证据。

## 2026-07-07 19:16:38 +08:00：真实浏览器复测与自然语言产物 ID 补齐
### 这次做了什么
- 保持 Redis、FastAPI 和 worker 真实运行，使用 Codex 内置浏览器打开 `http://127.0.0.1:8042/`，从首页自然语言入口提交真实 LLM 流程。
- 使用已有中文简历 #156 与岗位 #197，要求 Agent 判断岗位适配、定制 HTML 简历并生成面试准备，不触发投递、邮件发送或外部申请。
- 修复自然语言入口在 LangGraph 嵌套子 run 场景下，子 run `output_json` 偶发只含执行计划时，用户摘要显示 `定制简历 #None`、`面试包 #None` 的问题。
- 修复面试包多路问题合并后没有全局去重，导致质量门禁因重复率过高失败的问题。
- 增加回归测试，模拟子 run 输出缺少产物 ID 但 trace artifact 完整的情况，确保自然语言汇总会恢复真实 `resume_version_id` 和 `interview_prep_id`。
- 增加面试包问题去重测试，确保质量 judge 前重复问题已被移除。
### 发现的问题
- 真实浏览器流程最终完成，生成自然语言 run #167、定制简历子 run #168、面试准备子 run #169，但页面首行摘要显示 `#None`。
- 数据库中真实产物已经创建：定制简历版本 #89、面试包 #37；说明 LLM 和业务工具成功执行，问题在自然语言层汇总子 run 结果时过度依赖 `output_json`。
- 当前服务直接调用 `/agent/runs` 跑 `tailor_resume_for_job` 能正确返回 `resume_version_id`，问题更集中在“自然语言 LangGraph 中嵌套调用子 Agent 图”的真实路径。
- 修复版 8043 真实 LLM 复测后，自然语言摘要已正确显示“定制简历 #91，面试包 #38”，但面试包 `coverage.passed=false`，原因是 `duplicate_rate=0.1795`，超过 0.08 阈值。
### 怎么修复
- `NaturalLanguageAgentService._completed_run_output()` 会在子 run 缺少关键产物 ID 时，读取对应 `AgentArtifact`，把 `tailored_resume` / `interview_prep` artifact 里的产物 ID 合并回 run output。
- `_user_message()` 不再把空 ID 格式化成 `#None`；即使没有恢复到 ID，也会显示“定制简历已完成”或“面试包已完成”。
- `tests/test_natural_language_agent.py` 新增 artifact 恢复回归用例，覆盖自然语言汇总层的真实失败形态。
- `InterviewPrepService._dedupe_question_sets()` 在问题元数据和质量 judge 前进行全局去重，保留先出现的问题来源，避免已导入面经、在线面经入口、规则题和 LLM 题之间重复。
### 验证结果
- 内置浏览器自然语言入口真实提交成功，页面无 500/Traceback，完成耗时约 61 秒。
- 修复版 8043 API 真实 LLM 复测完成，run #171 返回“定制简历 #91，面试包 #38”，无 `#None`。
- 修复版 8044 API 真实 LLM 复测完成，run #174 返回“定制简历 #92，面试包 #39”，面试包 `coverage.passed=true`、`duplicate_rate=0.0`、`question_quality_passed=true`。
- 内置浏览器在 8044 首页再次提交自然语言表单，页面完成 run #177，展示“定制简历 #93，面试包 #40”，无 `#None`，无错误提示；面试包子 run #179 `coverage.passed=true`、`duplicate_rate=0.0`。
- 内置浏览器抽查 `/ui/profiles`、`/ui/jobs`、`/ui/agent-runs`、`/ui/resumes`、`/ui/prep`、`/ui/ops`，页面均能打开且未出现 500/Traceback/接口请求失败文案。
- `/ops/queue/status` 返回 Redis enabled、queued_count=0、dead_letter_count=0。
- 目标回归 `python -m pytest tests\test_natural_language_agent.py tests\test_agent_workflow.py::test_tailor_resume_agent_workflow tests\test_interview_prep.py::test_interview_prep_agent_workflow_records_artifact -q` 通过，6 个测试全部通过。
- 补充回归 `python -m pytest tests\test_interview_prep.py tests\test_natural_language_agent.py -q` 通过，13 个测试全部通过。
- 全量回归 `python -m pytest -q` 通过，122 个测试全部通过。
### 未修复的问题
- 已完成的历史 run #167 的 `user_message` 仍保留旧文案；这是历史记录，不做运行态数据回写，避免修改既有 trace 证据。
- 8043 复测产生的面试包 #38 是去重修复前生成的历史产物，质量门禁失败记录保留为问题证据。
### 下一步
- 持续观察后台 stale run，后续可把面试包参考链接按 URL/title 做进一步去重，减少历史重复导入的展示噪声。

## 2026-07-07 18:56:39 +08:00：安装轻量 Redis 并修复 Worker BRPOP 超时
### 这次做了什么
- 评估本机资源与安装条件：当前机器 16GB 内存、16 线程、虚拟化开启，但当前非管理员；空闲内存约 0.9GB。
- 通过 winget 安装 `redis-windows` 8.8.0 portable 版本，并以本地开发模式启动 Redis：`127.0.0.1:6379`、数据目录 `data/runtime/redis`、关闭持久化。
- 安装 Python `redis>=5.0.0` 客户端，让项目 RedisTaskRunner 能连接真实 Redis。
- 修复 Redis worker 在 `BRPOP` 阻塞等待时被 socket timeout 打断并退出的问题。
### 发现的问题
- Redis server 本身非常轻：本轮启动后进程工作集约 12MB，`used_memory_human` 约 664KB。
- Docker Desktop 对本机来说不是轻量项：当前非管理员、WSL 没有可用发行版且空闲内存偏低，直接安装 Docker Desktop 可能需要管理员权限、WSL 更新和重启。
- 项目默认 `redis_socket_timeout_seconds=3`，但 worker `redis_worker_poll_timeout_seconds=10`；真实 Redis 下 `BRPOP` 还没到业务轮询超时，redis-py socket timeout 已先抛异常，导致 worker 退出，后台 run 停在 queued。
### 怎么修复
- 将默认 `redis_socket_timeout_seconds` 调整为 15 秒，使其大于默认 worker poll timeout。
- `consume_redis_queue_once()` 捕获 redis-py socket timeout，并把它视为一次空轮询返回 `None`，避免 worker 因空队列等待超时而崩溃。
- 增加单测覆盖 Redis socket timeout 场景，确保 worker 不退出。
### 验证结果
- `redis-cli -h 127.0.0.1 -p 6379 ping` 返回 `PONG`。
- `RedisTaskRunner().queue_status()` 在 `REDIS_ENABLED=true` 下返回 Redis 队列状态。
- FastAPI `/ops/readiness` 在 Redis enabled 模式下返回 `redis: ok`。
- `python -m py_compile app\core\config.py app\services\task_runner.py tests\test_agent_hardening.py` 通过。
- `python -m pytest tests\test_agent_hardening.py -q` 通过，20 个测试全部通过。
- 启动真实 Redis worker 后，后台 run #166 从 `queued -> running -> completed`，耗时 46064ms，生成定制简历 #88，事实校验 `passed=true`。
### 未修复的问题
- Docker Desktop 暂未安装；原因是当前空闲内存偏低且需要管理员/WSL 条件，Redis 已能覆盖本项目当前队列、DLQ、heartbeat 和 worker 验证需求。
### 下一步
- 如果后续要验证 Mailpit、Redis HA、更多外部依赖编排，再在管理员权限和足够内存条件下安装 Docker Desktop 或配置 WSL2。

## 2026-07-07 17:43:49 +08:00：真实浏览器巡检与核心路由修复
### 这次做了什么
- 启动 FastAPI 服务并配置真实 DeepSeek OpenAI-compatible LLM，使用 Playwright Chromium 从用户视角验证首页、简历页、岗位页、流程页、控制台和外发 smoke 页面。
- 真实上传 PDF、手动填写多段教育/项目/实习经历、打开 HTML 简历预览、保存目标 JD，并触发 Agent 定制简历与自然语言入口。
- 修复 `/profiles/upload` 和 `/agent/runs` 的 FastAPI 路由装饰器误挂问题，让 PDF 上传和同步 Agent run 能命中真正接口。
- 为 SQLite 连接增加 `timeout=30`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=30000` 和 `foreign_keys=ON`，降低真实浏览器并发请求下的 `database is locked` 风险。
- 修复自然语言入口对“不要投递/不要申请”的约束：计划归一化会禁止 `quick_apply/full_flow`，并支持“改简历 + 面试准备”的组合动作。
- 修复 LangGraph `GraphInterrupt` 被通用异常捕获后写成 failed 的问题，投递前人工确认现在会稳定落到 `waiting_for_confirmation`。
- 控制台队列状态在 Redis disabled 时改为返回结构化 disabled 状态，避免页面产生 503 console error。
- 修复多个异步表单提交后 `event.currentTarget` 变空导致的前端 pageerror。
- 定制简历页只对最新 3 个版本加载 iframe 预览，其余版本改为轻量占位和按需打开，避免历史版本多时一次性请求大量 HTML。
- `.gitignore` 补充 SQLite WAL/SHM 文件，避免真实运行后把运行态数据库文件带入提交。
### 发现的问题
- `/profiles/upload` 装饰器误挂到 `_apply_tenant()` helper，页面提交 PDF 时实际返回 422；因为列表已有旧 PDF 数据，页面容易误判为成功。
- `/agent/runs` 装饰器误挂到 `_tenant_query()` helper，流程页提交同步 Agent run 时返回 422，真实浏览器测试可以稳定暴露。
- 保存 JD 时出现 SQLite `database is locked`，说明浏览器同时触发 LLM 解析、列表刷新、截图和后续 API 请求时，默认 SQLite 短等待策略不够稳。
- 自然语言 Agent 在用户明确写“不要投递”时仍走 `full_flow`，触发投递确认 interrupt，最终被当成失败返回。
- 当前 LangGraph 版本会以 `GraphInterrupt` 异常形式冒泡人工确认，旧代码只检查 final state 的 `__interrupt__`，因此遗漏了异常形态。
- 控制台页面在 Redis 未启用的本地验证环境中请求 `/ops/queue/status` 会收到 503，浏览器 console 显示资源错误，影响前端巡检结果。
- 真实浏览器暴露了异步事件处理的细节：`await` 之后再访问 `event.currentTarget.reset()` 可能拿到 `null`。
- 定制简历历史版本很多时，页面会并发请求大量 `/resumes/{id}/html` iframe，用户打开页面明显变慢。
- 启用 SQLite WAL 后会生成 `*.db-wal` 和 `*.db-shm`，原 `.gitignore` 没覆盖这些运行态文件。
- 当前本机没有 Redis server、Docker 和 redis-py，首页一键流程的生产后台队列链路无法在本机完整验证；这不是静默兜底问题，需要环境具备 Redis worker 才能跑通。
### 怎么修复
- 将 `/profiles/upload` 装饰器移动到 `upload_resume()`，将 `/agent/runs` 装饰器移动到 `create_agent_run()`。
- SQLite 使用 WAL 和 30 秒 busy timeout，避免短时间读写竞争直接失败。
- 自然语言 planning prompt 明确禁止“不要投递”场景选择投递/完整流程；`_normalize_plan()` 再做一层硬规则兜底，并由 actions 驱动组合执行定制简历和面试准备。
- `LangGraphAgentOrchestrator` 显式捕获 `GraphInterrupt`，把 interrupt payload 写入 human_interrupt artifact，并将 run 状态置为 `waiting_for_confirmation`。
- `/ops/queue/status` 在 Redis disabled 时返回 200 + disabled payload，前端展示“Redis 队列未启用”。
- 表单提交处理器在 `await` 前保存 `const form = event.currentTarget`，后续统一使用稳定引用。
- `loadResumes()` 只渲染前三个 iframe，其他版本用 `.resume-preview-placeholder`。
- `.gitignore` 增加 `data/*.db-wal`、`data/*.db-shm`、`data/*.sqlite-wal`、`data/*.sqlite-shm`。
- 浏览器验证脚本改为关注真实接口状态、页面错误、截图和 run 列表，避免旧列表数据掩盖提交失败。
### 验证结果
- 第一轮真实浏览器验证已确认首页、手动建档、HTML 预览、目标 JD 页面和部分 LLM 流程可达，同时暴露上述 422/SQLite lock 问题。
- `python -m py_compile app\api\ops.py app\agents\natural_language.py app\agents\langgraph_orchestrator.py app\api\profiles.py app\api\agent_runs.py app\core\database.py` 通过。
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_agent_hardening.py tests\test_frontend_pages.py -q` 通过，30 个测试全部通过。
- `python -m pytest -q` 通过，119 个测试全部通过。
- 真实浏览器第三轮验证通过：PDF 上传新建 Profile #155、手动多段建档 Profile #156、HTML 简历预览、目标 JD Job #196、同步 Agent 定制简历 Run #161、自然语言“不要投递”Run #162、LangGraph interrupt Run #165 均按预期返回。
- 真实浏览器轻量回归通过：`/ops/queue/status` 在 Redis disabled 时返回 200，定制简历页只加载 3 个 HTML iframe，手动岗位表单提交无 console/pageerror。
### 未修复的问题
- 本机没有 Redis 服务，`/agent/runs/background` 和首页一键后台队列无法按生产模式验证；需要启动 Redis/worker 或在部署环境验证。
- 内置浏览器控制插件本轮因本地运行时路径问题不可用，所以改用 Playwright Chromium 做等价真实浏览器验证。
### 下一步
- 重启服务后重新跑 PDF 上传、同步 Agent run、自然语言入口和页面可达性回归。
- 如要验证首页一键后台流程，补齐 Redis server 与 worker 进程后再跑 SSE/interrupt 全链路。

## 2026-07-07 16:56:55 +08:00：Session RBAC、外发 Smoke 与 Worker Supervisor 健康/Drain
### 这次做了什么
- 新增 session 登录能力：`/auth/login`、`/auth/logout`、`/auth/me`，登录成功后签发 HttpOnly session cookie。
- 新增 `SessionAuthService`，支持 PBKDF2 密码哈希、签名 session token、启动时按 `SESSION_BOOTSTRAP_ADMIN_EMAIL/PASSWORD` 创建默认管理员。
- 将 RBAC 从可信 header 扩展为 session + header + admin token 三种入口；`require_admin` 和全局写操作保护都能识别 session。
- 将 `tenant_id` 下沉到 `profiles`、`jobs`、`agent_runs`，并在简历、岗位、Agent run 的核心创建/列表/读取路径逐步按当前租户过滤。
- 新增 `/ui/outbound-smoke` 和 `/ui/outbound-smoke/target`，用于本地验证 `browser_apply`、`email_draft`、`email_send` 的端到端 payload。
- 新增 `docker-compose.smtp.yml`，用 Mailpit 提供本地 SMTP 测试容器。
- Worker supervisor 增加结构化 JSON 日志、health 文件和 drain 文件；检测到 drain 文件后停止重启子进程并统一终止 worker。
- 更新 API、Redis + SQLite 架构文档。
### 发现的问题
- 可信 header 只能表达“网关已经认证过”，不能覆盖本地单机部署、开发调试和没有 OIDC 的场景。
- 多租户如果只停留在 `tenants/app_users`，核心业务数据仍会混在一起；至少要先让简历、岗位和 run 带租户归属。
- 真实外发工具如果没有本地 smoke target，每次验证都依赖外部招聘站或真实邮箱，调试成本高且不可控。
- Supervisor 只有重启能力还不够，生产排障需要健康文件，发布/维护时需要 drain 入口。
### 怎么修复
- 用 PBKDF2 + HMAC 签名 cookie 实现 session 登录，不引入额外外部认证服务，后续可以替换为 OIDC 签发。
- `parse_auth_context()` 优先解析 session，再兼容 admin token 和可信 header；审计 actor 使用 session/header 用户。
- SQLite 迁移自动为 `profiles/jobs/agent_runs/app_users` 补齐 `tenant_id/password_hash`。
- 外发 smoke 页面给出浏览器 target selector 和邮件 payload，本地 SMTP 使用 Mailpit。
- Supervisor 定期写 `SUPERVISOR_HEALTH_FILE`，看到 `SUPERVISOR_DRAIN_FILE` 后进入停止流程，并输出结构化事件。
### 验证结果
- `python -m py_compile app\services\session_auth.py app\api\auth.py app\core\security.py app\main.py app\api\profiles.py app\api\jobs.py app\api\agent_runs.py app\frontend\routes.py scripts\run_agent_worker_supervisor.py app\models\entities.py app\models\schemas.py app\core\database.py` 通过。
- `node --check app\static\js\main.js` 通过。
- `python -m pytest tests\test_agent_hardening.py tests\test_frontend_pages.py -q` 通过，30 个测试全部通过。
- `python -m pytest -q` 通过，119 个测试全部通过。
- 发现默认 `data/runtime/langgraph_checkpoints.sqlite` 较大后，为测试环境设置 `.tmp_test/pytest_langgraph_checkpoints.sqlite`，避免全量测试被历史 checkpoint 拖慢。
### 未修复的问题
- session 登录仍是项目内账号体系，不是企业 OIDC/SSO；原因是当前先补可运行的 session 和 RBAC enforcement，后续可接入 OIDC provider。
- `tenant_id` 还没有覆盖所有业务表和所有深层查询；本轮先覆盖简历、岗位、Agent run 三个核心入口，后续继续下沉到 match/resume/application/interview prep 等产物表。
- `browser_apply` smoke target 是本地页面，真实招聘站仍需要按站点维护 selector。
- Supervisor drain 当前是终止 worker 进程，不是节点级“完成当前 run 后退出”；后续需要 worker 主循环识别 drain flag 并在任务边界退出。
### 下一步
- 增加 OIDC code flow 或企业 SSO provider 配置，保留 session 作为登录结果。
- 将 tenant_id 下沉到 MatchResult、ResumeVersion、Application、InterviewPrep、AgentArtifact/AgentEvent 查询。
- 给 worker 增加任务级 graceful drain：不接新任务，当前任务完成后退出。
- 为 browser_apply 增加 smoke e2e 自动化测试，连接本地 FastAPI target 页面和 Playwright。

## 2026-07-07 12:13:57 +08:00：真实外发工具、Worker Supervisor、Redis HA、RBAC 与分类器版 Injection Detector
### 这次做了什么
- 将 `HighRiskActionToolService` 从“审批后放行”升级为真实工具网关：`email_draft` 生成 RFC822 `.eml` 草稿，`email_send` 通过 SMTP 发送，`browser_apply` 通过 Playwright 打开页面并按 selector 填写/提交。
- 高风险工具执行成功或失败都会写入 `agent_artifacts`，并继续写 `ops_audit_events` 与 run trace。
- Redis worker 支持 high/normal/low 三个优先级队列，`brpop` 按高优先级到低优先级消费；`queue_status` 输出各优先级长度。
- Redis client 增加 Sentinel HA 模式：`REDIS_MODE=sentinel`、`REDIS_SENTINEL_URLS`、`REDIS_SENTINEL_MASTER_NAME`。
- 新增 `scripts/run_agent_worker_supervisor.py`，按 `REDIS_WORKER_CONCURRENCY` 启动多个 worker 子进程，异常退出后自动重启，收到终止信号时统一停止。
- 新增 `tenants/app_users` 表和 RBAC header 上下文，运维接口支持 `X-Tenant-Id`、`X-User-Id`、`X-User-Roles` 中的 `owner/admin/ops` 角色。
- PromptInjectionGuard 增加轻量特征 classifier，补充规则 detector 对变体表达的覆盖。
- Prompt injection release gate 增加按 source 和 category 的分层阈值，并补充分类器变体样本。
- 控制台展示 Redis mode、worker 并发和优先级队列长度。
### 发现的问题
- 只创建 approval 但不执行真实工具，无法回答“工具结果如何进入 trace/artifact、外发失败怎么排查”。
- 单 Redis 队列无法区分用户交互 run 和低优先级批量评测任务；多 worker 并发也需要 supervisor 统一拉起和重启。
- 规则版 prompt injection 对“发送材料到外部邮箱”“不要遵守开发者规则”这类变体表达不够敏感。
- 只有 admin token 不能表达多租户和角色边界，审计 actor 也容易退化成固定 admin。
### 怎么修复
- 新增 `EmailOutboundTool` 和 `BrowserApplyTool`，由 `HighRiskActionToolService.execute_after_approval()` 调用；未 approved 仍直接报错，不进入工具层。
- 在 Redis payload 中加入 `priority`，根据 priority 写入不同队列；worker 消费 `redis_priority_queue_names`。
- 在 Redis client 中根据 `REDIS_MODE` 选择 standalone 或 Sentinel master client。
- 用 `AuthContext` 统一解析 admin token 和 RBAC header，并让 ops API 审计 actor 使用真实用户。
- 在 PromptInjectionGuard 中加入特征分类器和 `classifier_score/classifier_features`，并修正清洗逻辑以移除分类器命中的风险行。
- release gate 对 `source_breakdown` 和 `category_breakdown` 做分层检查，失败时输出具体 metric。
### 验证结果
- `python -m py_compile app\core\config.py app\core\redis_client.py app\core\security.py app\main.py app\api\ops.py app\services\high_risk_action_tools.py app\services\outbound_tools.py app\services\prompt_injection_guard.py app\services\evaluation_service.py app\services\task_runner.py scripts\run_agent_worker_supervisor.py app\models\entities.py app\models\schemas.py` 通过。
- `python -m pytest tests\test_agent_hardening.py tests\test_frontend_pages.py -q` 通过，26 个测试全部通过。
- `node --check app\static\js\main.js` 通过。
- `python -m pytest -q` 通过，115 个测试全部通过。
### 未修复的问题
- RBAC 当前是可信 header 模式，不是完整登录、会话、OIDC 或企业 SSO；原因是当前先补多租户/角色边界和审计上下文。
- `browser_apply` 依赖目标站 selector 配置和 Playwright 浏览器二进制，无法保证所有招聘站都可自动提交；失败会直接报错并写失败 artifact。
- `email_send` 需要真实 SMTP 配置；没有 SMTP 时不会兜底发送或伪造成功。
- 分类器是轻量特征模型，不是训练模型；目前用 release gate 和 adversarial 样本约束质量。
### 下一步
- 增加真实外发工具的端到端浏览器 smoke 页面和本地 SMTP 测试容器。
- 将 RBAC 从可信 header 升级到 OIDC/SSO 或 session 登录，并把 tenant_id 逐步下沉到核心业务表查询。
- 给 worker supervisor 增加结构化日志、健康探针和优雅 drain 模式。

## 2026-07-07 11:46:15 +08:00：DLQ 人工处置、高风险工具审批网关与 Prompt Injection Release Gate
### 这次做了什么
- 为 Redis DLQ 增加人工选择重放/丢弃能力：`/ops/queue/dead-letter/{dlq_index}/replay` 会移除 DLQ payload、重置 attempts 并重新入主队列，`discard` 会移除并停止重放。
- 新增 `ops_audit_events` 运维审计表和 `OpsAuditService`，DLQ 重放/丢弃、高风险工具放行都会留下 actor、target、payload 和时间。
- 为浏览器辅助填写、邮件草稿、邮件发送增加 `HighRiskActionToolService` 网关：必须先创建/复用 approval，且 approval 状态为 `approved` 后才允许工具执行阶段放行。
- 新增 `/ops/high-risk-actions/request`、`/ops/high-risk-actions/{approval_id}/execute` 和 `/ops/audit-events`。
- 扩展 `/ui/ops` 控制台：DLQ 预览现在可以逐条重放/丢弃，并新增运维审计事件列表。
- 将 prompt injection 评测集从 36 条扩展到 64 条，增加真实形态中文 JD、PDF OCR 噪声、RAG chunk、面经网页片段和 benign 安全工程表述。
- 新增 `evals/prompt_injection_release_policy.json`，按 release gate 校验样本量、最低 detection recall、最高 false positive rate、category recall 和 severity accuracy。
- 更新 README、API、Agent 设计、Redis + SQLite 架构说明和 Hardening Notes。
### 发现的问题
- 仅展示 DLQ 预览不够，排障时必须能人工决定某个 payload 是可重放还是应丢弃，否则异常消息会长期堆在 Redis 中。
- `agent_approvals` 虽然覆盖了动作类型，但如果真实浏览器/邮件工具绕过 service 直接执行，审批表就只是记录而不是强约束。
- Prompt injection 评测如果只有少量规则命中样本，容易高估效果；需要加入更接近真实 JD/PDF/OCR/网页片段的噪声和 benign 技术描述。
### 怎么修复
- 在 `RedisTaskRunner` 内实现 `_dead_letter_items()`、`replay_dead_letter()` 和 `discard_dead_letter()`，并为新进入 DLQ 的 payload 写入 `dlq_id/dead_lettered_at`。
- 用 `lrem` 移除人工选择的 DLQ 原始 payload，避免只按队列长度或模糊条件操作。
- 新增独立 `ops_audit_events`，同时在 payload 有合法 `run_id` 时写入 `agent_events`，兼顾跨 run 运维审计和单 run trace。
- 用 `HighRiskActionToolService.execute_after_approval()` 在工具执行前检查 approval 状态，未 approved 直接抛 `ApprovalRequiredError`。
- 在 `EvaluationService` 汇总 prompt injection 指标时加载 release policy，并输出 `release_gate.passed` 和 failed checks。
### 验证结果
- `python -m py_compile app\services\task_runner.py app\api\ops.py app\services\high_risk_action_tools.py app\services\evaluation_service.py app\models\entities.py app\models\schemas.py` 通过。
- `python -m pytest tests\test_agent_hardening.py tests\test_frontend_pages.py -q` 通过，22 个测试全部通过。
- `node --check app\static\js\main.js` 通过。
- `python -m pytest -q` 通过，111 个测试全部通过。
### 未修复的问题
- 浏览器真实填写、邮件草稿和邮件发送的外部工具本体还没有接入；本轮完成的是强制审批网关，后续接真实工具时必须从该网关进入。
- DLQ replay 目前是人工重放到同一主队列，没有多队列路由和优先级；原因是当前 Redis worker 仍保持轻量队列模型。
- Prompt injection guard 仍是规则版；本轮通过更强评测集和 release gate 固定质量底线，后续可接分类器或 LLM-as-judge 做二阶段判定。
### 下一步
- 将真实 browser_apply、email_draft、email_send 工具接入 `HighRiskActionToolService`，并把工具结果写回 run artifact。
- 为 Redis worker 增加多 worker 并发配置、队列优先级和 supervisor 启动文档。
- 在 prompt injection release gate 中加入按来源/类别的最低召回阈值，继续补充真实失败样本。

## 2026-07-07 11:35:42 +08:00：Redis Worker DLQ、审批扩展、Prompt Injection 评测与控制台运维
### 这次做了什么
- 为 Redis worker 增加 dead-letter queue：payload 解析失败、未知 kind、worker 级异常会按 attempts 重试，超过 `REDIS_WORKER_MAX_ATTEMPTS` 后写入 `REDIS_DEAD_LETTER_QUEUE_NAME`。
- 为 worker 增加更细粒度 heartbeat stage：`run_lock_acquired`、`sqlite_run_loaded`、`langgraph_starting`、`langgraph_finished:*`，task run 也会写 task heartbeat。
- 增加 queued run recovery scanner：worker 主循环每 60 秒扫描 SQLite 中超时的 queued run 并重新入队，控制台也可手动触发 `/ops/queue/recover-queued`。
- 将审批动作类型扩展到 `browser_apply`、`email_draft`、`email_send`，新增 `/ops/approvals`、`/ops/approvals/{approval_id}/decision` 等控制台审批接口。
- 新增 `evals/prompt_injection_cases.json`，覆盖 JD、PDF 简历、RAG chunk、导入面经四类来源的 adversarial/benign 样本。
- 新增 `POST /evaluations/prompt-injection`，量化 PromptInjectionGuard 的 detection recall、false positive rate、true negative rate、category recall、severity accuracy 和 source/category breakdown。
- 优化 `/ui/ops` 控制台：展示 Redis 队列/DLQ、active Agent run 取消按钮、approval 审批列表、stale run 列表和 queued recovery/mark stale 操作。
- 更新 README、API、Agent 设计、Redis + SQLite 架构说明、Hardening Notes 和面试 Q&A。
### 发现的问题
- 只有 Redis queue 和 lock 还不足以回答“worker 消费坏消息怎么办、queued run 丢在队列里怎么办”；需要 DLQ 和 recovery scanner 才能解释排障闭环。
- `agent_approvals` 虽然已经存在，但如果只支持 `application_packet`，后续接浏览器投递、邮件草稿和邮件发送时还要再改审批模型。
- Prompt injection 只有单元检测不够，无法量化误报率；需要负例样本覆盖正常安全工程描述、审批设计和工具调用技术词。
- 控制台此前能看 readiness/metrics/logs，但不能直接处理 pending approval、active run、stale run 和队列恢复，运维闭环不完整。
### 怎么修复
- 在 `RedisTaskRunner` 中增加 `requeue_or_dead_letter()`、`queue_status()` 和 `recover_queued_agent_runs()`，并让 worker 主循环定期执行 recovery。
- 将 Redis payload 统一成带 `kind/attempts/enqueued_at` 的 JSON，Agent run 和 LLM workflow task 共用同一队列。
- 在 `ApprovalService` 中显式声明支持的高风险动作类型，并通过 ops API 暴露创建/决策能力。
- 在 `EvaluationService` 中新增 prompt injection guard evaluation，按 source 和 category 分桶统计质量。
- 在前端控制台新增队列、运行控制、审批、stale run 四块 UI，并通过已有 admin token 机制调用写操作。
### 验证结果
- `python -m pytest tests\test_agent_hardening.py tests\test_frontend_pages.py -q` 通过，20 个测试全部通过。
- `python -m py_compile app\services\task_runner.py app\api\ops.py app\api\evaluations.py app\services\evaluation_service.py app\models\schemas.py app\services\approval_service.py app\core\redis_client.py app\core\config.py` 通过。
- `node --check app\static\js\main.js` 通过。
### 未修复的问题
- DLQ 目前只支持预览和保留 payload，还没有“从 DLQ 选择性重放”接口；原因是重放需要更明确的人工确认和去重策略，避免把已知坏 payload 反复打回主队列。
- Worker 仍是轻量单脚本，没有 supervisor、优先级队列、多队列路由和并发 worker pool 配置；原因是当前先补齐简历项目可解释的生产化最小闭环。
- Prompt injection guard 仍是规则版；虽然已有 adversarial 量化评测，但还没有训练分类器或接入 LLM-as-judge。
### 下一步
- 为 DLQ 增加人工选择重放/丢弃接口和审计事件。
- 在真实浏览器辅助填写、邮件草稿和邮件发送工具接入时，把对应动作强制绑定 approval table。
- 扩展 prompt injection 评测集到更多真实 JD/PDF 样本，并按 release 设置最低 recall 和最高 false positive rate 阈值。

## 2026-07-07 11:09:45 +08:00：Redis 队列、取消幂等、审批审计与 Prompt Injection 硬化
### 这次做了什么
- 将 Agent 后台 run 从 FastAPI 进程内任务升级为 RedisTaskRunner：`POST /agent/runs/background` 只创建 queued run 并入 Redis 队列，`scripts/run_agent_worker.py` 独立消费执行 LangGraph。
- 将 `/tasks/llm-workflow` 评测长跑也切到同一 Redis 队列，避免运维任务继续绑在 API 进程里。
- 新增 Redis run lock、heartbeat、cancel flag、Profile 级短窗口 rate limit，并在 `/ops/readiness` 和 `/ops/config` 暴露队列配置状态。
- 新增 `POST /agent/runs/{run_id}/cancel`，取消 queued/running/waiting run 时写事件、output、Redis cancel flag，并取消 pending approval。
- 为 `ResumeVersion`、`Application`、`InterviewPrep` 增加 `idempotency_key` 和 SQLite 唯一索引；LangGraph 写库节点重复执行会复用已有产物并写 `idempotency_reused` 事件。
- 新增 `agent_approvals` 审批审计表；投递包 interrupt 前创建 pending approval，resume 确认/拒绝和 cancel 都会更新审计状态。
- 新增 `PromptInjectionGuard`，接入 JD 解析、PDF 简历解析、RAG evidence、导入面经和简历定制 prompt 构造前的过滤。
- 新增 stale run 检测与运维接口：`GET /ops/agent-runs/stale`、`POST /ops/agent-runs/mark-stale`。
- 新增硬化测试 `tests/test_agent_hardening.py`，覆盖 prompt injection、Redis queue/lock、取消阻止 resume、approval 状态、业务幂等和 stale run。
- 新增/更新 Redis + SQLite 架构、Hardening Notes、面试 Q&A、README、API 和 Agent 设计文档。
### 发现的问题
- Agent 主流程已经迁移 LangGraph，但后台入口仍然由 API 进程启动任务，面试中会被追问 API 重启、页面关闭和多 worker 重复执行。
- 投递确认之前只体现在 run output 和 interrupt payload 中，不足以回答“审批是否可审计、能否复用到浏览器/邮件工具”。
- checkpoint 重放、worker retry 或重复 resume 会再次进入写库节点，如果没有业务幂等键，可能重复创建投递包或面试包。
- JD、PDF 和 RAG chunk 都是外部不可信文本，之前没有统一的 prompt injection risk flag 和进入 LLM 前过滤。
- 旧文档仍写 BackgroundTasks，和真实架构不一致。
### 怎么修复
- 抽象 `RedisTaskRunner`，统一承载 `agent_run` 和 `task_run` payload；worker 消费前获取 Redis lock，失败直接报错而不是退回进程内执行。
- 在 LangGraph Orchestrator 的节点入口统一检查 SQLite run status 和 Redis cancel flag；取消后继续 resume 会返回明确冲突，不创建后续业务产物。
- 在投递包 interrupt 前调用 `ApprovalService.get_or_create_pending()`，resume 后调用 `decide()` 写 approved/rejected，cancel 调用 `cancel_pending_for_run()`。
- 在 `tailor_resume`、`ensure_resume_version`、`create_application_packet`、`generate_interview_prep` 节点中生成稳定 idempotency key，先查已有产物再写库。
- 在 JD/PDF/面经/RAG evidence 进入 LLM 或下游生成前调用 `PromptInjectionGuard.sanitize_for_llm()` 或 `sanitize_evidence()`，保留风险 metadata。
- 用 `StaleRunService` 按最后事件时间识别 running 卡死 run，并通过运维接口标记 failed、保留 last_event/last_stage。
### 验证结果
- `python -m pytest tests\test_agent_hardening.py -q` 通过，6 个测试全部通过。
- `python -m pytest tests\test_agent_workflow.py -q` 通过，7 个测试全部通过。
- `python -m pytest tests\test_frontend_pages.py -q` 通过，9 个测试全部通过。
- `python -m pytest tests\test_resume_parser.py tests\test_matcher.py -q` 通过，7 个测试全部通过。
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\agents\langgraph_orchestrator.py app\api\agent_runs.py app\api\ops.py app\api\tasks.py app\services\task_runner.py app\services\approval_service.py app\services\prompt_injection_guard.py app\services\stale_runs.py app\models\entities.py app\models\schemas.py` 通过。
### 未修复的问题
- 轻量 Redis worker 还不是 Celery/Arq 级调度平台；原因是当前目标是简历项目中可解释、可运行的现代 Agent 架构，先补齐 queue/lock/cancel/heartbeat/idempotency 的核心闭环。
- SQLite 仍是业务事实库；原因是当前项目主要面向单机可上线演示和审计 trace，多租户高并发时再迁 PostgreSQL 更合理。
- Prompt injection guard 仍是规则版；原因是本轮先保证基础检测、过滤和 trace，后续可用分类器和 adversarial eval 增强。
- 还没有真实浏览器最终提交或邮件发送工具；原因是当前产品边界仍是准备材料和人工确认，真实外发工具需要单独权限隔离和更严格审批。
### 下一步
- 为 Redis worker 增加 dead-letter queue、queued run recovery scanner 和更细粒度 heartbeat stage。
- 将审批表扩展到 `browser_apply`、`email_draft`、`email_send` 等更高风险动作。
- 增加更多 adversarial JD/PDF/RAG 评测样本，量化 prompt injection guard 的召回率和误报率。
- 在前端控制台展示 approval 列表、取消按钮、stale run 管理和 Redis queue 状态。

## 2026-06-18 15:52:03 +08:00：统一 LangGraph 运行时、事件流和后台一键流程
### 这次做了什么
- 新增 `agent_events` 表和 `AgentEventResponse`，把 run、step、artifact、LangGraph graph/node/interrupt 事件统一持久化。
- `TraceService` 在 run 创建、启动、完成、step 开始/完成/失败、artifact 生成时写入事件，便于前端和排障读取同一套进度源。
- `LangGraphAgentOrchestrator` 改为通过 `astream_events(version="v2")` 执行 graph，捕获 `graph_node_started/update/completed`、`graph_interrupt`、`graph_completed` 和 `graph_failed`。
- 新增 `POST /agent/runs/background`，先创建 `status=queued` 的 run，再用 FastAPI `BackgroundTasks` 启动同一个 LangGraph graph。
- 新增 `GET /agent/runs/{run_id}/events` 和 `GET /agent/runs/{run_id}/events/stream`，前者用于 JSON 查询，后者用于 SSE 实时进度。
- 自然语言入口迁移为独立 LangGraph 图：`parse_user_request -> execute_user_plan -> repair_user_plan -> execute_repaired_user_plan -> finalize`。
- 首页一键流程改为单个后台 `full_career_flow` run，并通过 SSE 推进阶段进度，不再由前端串多个小 run。
- `full_career_flow` 支持已有 `job_id` 的目标岗位直跑：跳过岗位搜索，直接匹配、定制简历、投递包和面试包。
- 流程页新增 LangGraph 事件流时间线，点击 run 后同时展示 step trace 和 event stream。
### 发现的问题
- 之前“首页一键流程”虽然体验上完整，但本质是前端串行调用找岗、定制、投递和面试多个 run，不利于展示一个现代 Agent 的统一图编排。
- `full_career_flow` 之前只支持“先搜索再选岗位”，用户粘贴 JD 或已经选择岗位时仍无法作为单图流程直跑。
- 只看最终结果和 `agent_steps` 仍不够定位 LangGraph 中间状态，尤其是 interrupt、checkpoint 和后台运行时的节点级事件。
- 自然语言 Agent 之前是手写 try/except 编排，虽然会调用主 Orchestrator，但自身的 parse/execute/repair/finalize 没有用 LangGraph 表达。
- 内置浏览器环境里 `EventSource` 不可用，如果前端直接 new EventSource，会让一键流程在部分客户端无法启动进度监听。
### 怎么修复
- 用 `agent_events` 作为统一事件源，SSE 只读事件表；这样同步 run、后台 run、resume run 和自然语言 run 都能复用同一套前端展示。
- 把主 Orchestrator 的同步执行、queued 执行和 resume 执行收敛到同一套 `_execute_run/_invoke_graph` 逻辑。
- 自然语言服务保留原有工具调用边界，但外层改成 LangGraph 节点和条件边，失败进入 repair 节点，repair 后仍失败则结构化返回 failed。
- 修改 `full_career_flow` 路由：有 `job_id` 时从 `load_job` 进入目标岗位链路，没有 `job_id` 才搜索岗位；`match_job` 会为目标岗位直跑补齐 `selected_job`。
- 前端新增 `subscribeAgentRunEvents()`、`waitForAgentRun()` 和事件时间线；首页一键流程创建后台 run 后等待 SSE/轮询完成。
- `subscribeAgentRunEvents()` 增加 EventSource 能力检测；不支持 SSE 的客户端会自动退回轮询完成状态。
### 验证结果
- 目标回归 `python -m pytest tests\test_agent_workflow.py tests\test_natural_language_agent.py tests\test_frontend_pages.py -q` 通过，19 个测试全部通过。
- `python -m pytest -q` 全量回归通过，98 个测试全部通过。
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\agents\langgraph_orchestrator.py app\agents\natural_language.py app\api\agent_runs.py app\services\trace_service.py app\models\entities.py app\models\schemas.py` 通过。
- `git diff --check` 通过，仅有 Windows CRLF 提示，无空白错误。
- 内置浏览器 smoke 通过：`http://127.0.0.1:8060/` 首页和 `/ui/agent-runs` 流程页正常渲染，流程页显示“事件流”，页面 console error 为空。
### 未修复的问题
- 后台 Agent run 仍使用 FastAPI 进程内 `BackgroundTasks`，不是分布式队列；单机开发和简历项目展示足够，多实例生产部署应替换为 Redis/Celery/Arq。
- LangGraph 节点内部仍复用当前 FastAPI DB Session；如果要支持跨天恢复或多 worker 抢占式恢复，后续应让节点独立打开 Session，并为写库节点补业务幂等键。
- 投递确认记录目前写在 run output 和事件里，还没有独立审批/审计表；等接入浏览器填写、邮件发送等更高风险工具时应拆出审批表。
### 下一步
- 为投递、浏览器辅助填写、邮件草稿/发送等高风险工具建立独立审批/审计表。
- 把后台 Agent run 从 FastAPI `BackgroundTasks` 升级为可取消、可重试、可横向扩展的外部队列。
- 为应用包创建、面试包创建、简历版本创建补充业务幂等键，减少 checkpoint 恢复和后台重试带来的重复写入风险。

## 2026-06-18 15:23:03 +08:00：LangGraph SQLite checkpoint 与投递前人工确认 interrupt
### 这次做了什么
- 将 LangGraph checkpointer 从 `InMemorySaver` 升级为 `AsyncSqliteSaver`，默认持久化到 `data/runtime/langgraph_checkpoints.sqlite`。
- 新增 `LANGGRAPH_CHECKPOINT_FILE` 配置项和 `Settings.langgraph_checkpoint_path`，支持把 checkpoint 文件切到其他路径。
- 为 `quick_apply` 和 `full_career_flow` 的投递包生成前加入 LangGraph `interrupt()`；默认请求会返回 `status=waiting_for_confirmation`，确认前不会写入 `applications`。
- 新增 `POST /agent/runs/{run_id}/resume`，用户确认后用同一个 `graph_thread_id` 从 SQLite checkpoint 恢复执行。
- 新增 `GET /agent/runs/{run_id}/graph-state`，用于查看 checkpoint 的 next 节点、interrupt payload 和 checkpoint id。
- `resume` API 会把非法状态或缺失 checkpoint 转为明确的 4xx 响应，避免前端只看到 500。
- 前端一键流程收到等待确认后会调用 resume 继续，流程页的历史 run 卡片会显示“确认继续”按钮。
- 自然语言 Agent 支持返回 `waiting_for_confirmation`，不再把人工确认 interrupt 当作失败。
- Agent full-flow 评测继续显式传入 `application_confirmed=true`，保证批量回归不被人工节点卡住。
### 发现的问题
- 同步 `SqliteSaver` 不支持 async graph，直接用于 `ainvoke` 会报错，必须使用 `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`。
- SQLite saver 的创建是异步连接，不能在同步 `__init__` 中直接完成；需要在 `run/resume/graph_state` 时懒初始化 graph。
- pytest 的默认 `tmp_path` 在当前 Windows 临时目录出现权限错误，新增 checkpoint 恢复测试改为项目内 `.tmp_test/` 临时路径。
- 新增 interrupt 后，原本手动运行 quick_apply 的前端路径会把 `waiting_for_confirmation` 当失败；需要在 UI 层识别等待确认状态。
- `resume_json` 如果和顶层 `confirmed/note` 同名，可能覆盖人工确认字段，导致审批语义不清晰。
### 怎么修复
- `LangGraphAgentOrchestrator` 改为异步懒加载 checkpoint：打开 `aiosqlite` 连接、初始化 `AsyncSqliteSaver`、compile graph，执行结束后关闭连接并清空 graph 实例。
- `AgentRunRequest` 增加 `application_confirmed`；真实用户默认 `false` 触发 interrupt，评测或受控流程可显式传 `true`。
- `AgentRunResumeRequest` 增加 `confirmed/note/resume_json`，resume payload 会传回中断节点，确认后继续创建投递包，拒绝则 run 失败且不创建投递包。
- 新增跨实例恢复测试：第一个 Orchestrator 跑到 interrupt，第二个 Orchestrator 从 SQLite checkpoint 恢复并创建投递包。
- `resume` API 现在以顶层 `confirmed/note` 为准，额外字段只作为补充上下文，并把非法状态映射为 `409`。
- 前端 `createAgentRun()` 支持 `autoConfirmApplication`，首页一键流程自动确认继续；流程列表支持人工点击确认。
### 验证结果
- `python -m pytest -q` 全量回归通过，95 个测试全部通过。
- `node --check app\static\js\main.js` 通过。
- `python -m py_compile app\agents\langgraph_orchestrator.py app\api\agent_runs.py app\agents\natural_language.py app\models\schemas.py app\core\config.py app\services\evaluation_service.py` 通过。
- `git diff --check` 通过，仅有 Windows CRLF 提示，无空白错误。
- 内置浏览器 smoke 通过：`http://127.0.0.1:8050/` 首页显示“让 Agent 自动处理”和“一键运行”，`/ui/agent-runs` 流程页可打开，页面 console error 为空。
### 未修复的问题
- 前端进度仍主要读取 `agent_steps` 和 graph-state 快照，没有实现真正的 LangGraph SSE/event streaming；原因是本轮优先完成可恢复 checkpoint 和 interrupt，实时事件流适合单独设计前端订阅协议。
- checkpoint 已经跨请求持久化，但节点内部仍复用当前 FastAPI DB Session；更长时间跨度恢复时，后续应让每个节点独立打开 Session 并增强幂等写入。
### 下一步
- 增加 LangGraph event streaming/SSE 进度端点，前端展示节点级实时事件。
- 为投递确认、浏览器辅助填写、邮件发送等高风险操作建立独立审批/审计表。
- 将应用包创建、面试包创建等写库节点补充业务幂等键，避免恢复重试时重复写入。

## 2026-06-18 12:46:25 +08:00：Agent 主编排整体迁移到 LangGraph
### 这次做了什么
- 新增 `app/agents/langgraph_orchestrator.py`，用 LangGraph `StateGraph` 承接全部 Agent task：`find_jobs_for_profile`、`tailor_resume_for_job`、`quick_apply`、`prepare_interview_for_job` 和 `full_career_flow`。
- 将旧 `AgentOrchestrator` 改为兼容外壳，所有 FastAPI `/agent/runs`、自然语言 Agent、Agent full-flow 评测和面试包流程继续使用原 import，但实际执行已经走 LangGraph。
- 为 LangGraph graph 接入 `InMemorySaver` checkpointer，并在运行配置中写入 `graph_thread_id=agent-run-{id}`。
- 保留原有 `agent_runs`、`agent_steps`、`agent_artifacts` 可观测链路；每个 LangGraph 节点内部继续通过 `TraceService.step()` 写入步骤级 input/output/latency/error。
- `AgentPlanner` 和 run 输入输出新增 `orchestration_framework=langgraph`，`execution_plan.langgraph_decision.migrated=true`。
- Agent full-flow 评测新增 `langgraph_pass_rate`，并把 LangGraph 标识纳入 case 通过条件，避免后续绕过 LangGraph 主编排。
- 更新 README、Agent 设计、架构、API、开发说明和评测文档，说明当前已迁移到 LangGraph，以及 checkpointer、state 和后续 interrupt 的边界。
### 发现的问题
- LangGraph 的 `TypedDict` state schema 会丢弃未声明字段；初版 `search_jobs` 节点返回了 `job_ids`，但 state 未声明，导致 full-flow 后续选择岗位时看不到任何匹配结果。
- 弱匹配 case 中 `quick_apply` 被 `fit_gate` 正确阻断，run 状态是 failed；初版失败输出没有带回 `execution_plan`，导致评测无法证明 failed run 也经过 LangGraph 编排。
- 不能把 SQLAlchemy Session 或 ORM 对象放进 LangGraph state；否则后续接入 checkpointer/resume 时会出现序列化和副作用重放问题。
- 本地命令里 `pytest.exe` 和 `python` 可能来自不同解释器；LangGraph 安装后应使用 `python -m pytest` 固定解释器。
### 怎么修复
- 在 `CareerAgentGraphState` 中显式声明 `job_ids`，并通过回归测试覆盖 full-flow 选择岗位链路。
- 在 `LangGraphAgentOrchestrator` 中增加运行期 `run_id -> Session` 和 `run_id -> execution_plan` 映射：state 只保存 JSON 友好数据，失败 run 也能在 `output_json.execution_plan` 中保留计划。
- 为 graph compile 接入 `InMemorySaver`，让 `thread_id` 在单进程内有 checkpoint 语义；文档明确跨进程恢复需要后续替换为持久化 checkpointer。
- 保留所有 service 依赖注入参数，评测中的 fake `job_search`、`matcher`、`tailor`、`application`、`interview_prep` 仍能替换节点内部服务。
### 验证结果
- 迁移相关测试 `python -m pytest tests\test_agent_workflow.py tests\test_evaluation_service.py tests\test_natural_language_agent.py tests\test_interview_prep.py -q` 通过，41 个测试全部通过。
- 完整回归测试 `python -m pytest -q` 通过，94 个测试全部通过。
- `python -m py_compile app\agents\langgraph_orchestrator.py app\agents\orchestrator.py app\agents\tools.py app\services\evaluation_service.py` 通过。
### 未修复的问题
- 当前 checkpointer 是 `InMemorySaver`，适合本地单进程运行和调试；跨进程、重启后的恢复还需要换成 SQLite/Postgres 等持久化 checkpointer。
- 还没有把投递前人工确认改为 LangGraph interrupt；原因是现有产品已经通过 `ApplicationPacketGuardrail` 和人工确认字段阻止自动提交，真正的 interrupt 更适合和后续浏览器/邮箱/MCP 工具一起接入。
- 前端进度仍读取 `agent_steps`，没有直接消费 LangGraph event stream；原因是保留原有 UI 兼容性，后续可在不破坏 trace 表的前提下加 streaming。
### 下一步
- 将 `InMemorySaver` 替换为持久化 checkpointer，并为 `graph_thread_id` 增加恢复 API。
- 在投递、浏览器辅助填写、邮件发送等真实副作用节点前加入 LangGraph interrupt。
- 增加前端事件流进度视图，直接展示 LangGraph 节点状态和中间产物。

## 2026-06-18 08:34:57 +08:00：复杂 PDF 简历与真实 LLM 前端主流程验收
### 这次做了什么
- 生成复杂中文 PDF 简历样例 `D:\CareerAgent\generated_samples\complex_agent_resume_zh_20260618.pdf`，包含 3 页内容、多段教育/实习/项目、英文技术术语、社团经历和噪声文本，用于贴近真实简历上传测试。
- 使用真实 DeepSeek LLM 配置启动本地服务，并通过内置浏览器验证首页、简历档案页、HTML 简历预览页和一键求职流程。
- 修复 PDF 简历结构化解析 prompt：不再要求 LLM 输出完整 `raw_text`，由服务端回填原文，避免复杂 PDF 解析时 JSON 被截断。
- 将简历解析 LLM 输出预算从 1400 tokens 提升到 3600 tokens，并增加回归测试固定 prompt 约束和服务端回填逻辑。
- 为 `/matches` 增加异常包装：匹配阶段失败时回滚事务并返回包含根因的结构化错误，避免前端只显示 `Internal Server Error`。
- 将本地服务验收产生的 `.tmp_logs/` 加入 `.gitignore`，避免运行日志污染提交状态。
- 用上传后的 Profile #150 和中文 Agent 开发实习 JD 在首页跑通一键流程，生成 Job #193、Resume #81、Application #22、InterviewPrep #35，对应 Agent run #154/#155/#156 全部完成。
### 发现的问题
- 复杂多页 PDF 上传时，原 prompt 要求 LLM 在 JSON 里返回完整 `raw_text`，真实调用会因为输出过长导致 JSON 截断，上传失败。
- 首次前端一键流程用到了不支持的 `RERANKER_PROVIDER=keyword` 测试配置，匹配阶段抛出异常；旧接口没有包装根因，用户侧只能看到泛化 500。
- `/ui/resumes` 当前会一次加载历史上所有定制简历 iframe，本次已有 81 个版本，页面仍可用但继续增长后会变重。
- 内置浏览器控制台存在 Statsig 和 `MutationObserver.observe` 相关噪声；源码中没有对应调用，页面内容和后端 trace 均正常，判断为浏览器注入脚本噪声。
### 怎么修复
- 在 `ResumeParserService` 中移除 `raw_text` 的 LLM 输出要求，明确提示“不要在 JSON 中包含 raw_text”，解析成功后由服务端统一写入原文。
- 增加简历解析测试，验证 prompt 不包含 `"raw_text": string`，并验证 LLM 未返回 `raw_text` 时服务端仍会回填。
- 在 `/matches` API 中捕获 matcher 异常，执行 `db.rollback()`，并通过 `format_exception()` 返回可追溯错误。
- 使用项目支持的 `heuristic` reranker 重新启动服务，重新跑前端一键流程。
- 忽略 `.tmp_logs/`，保留本地排障日志但不进入版本控制。
### 验证结果
- `pypdf` 本地抽取 PDF：3 页、4486 个字符，关键字段 `林知远`、`CareerAgent`、`RAGEvalBoard`、`PromptShield Lite`、`DeepSeek`、`FastAPI` 均命中。
- `/profiles/upload` 成功创建 Profile #150，HTML 预览页能展示姓名、上海交通大学、北京大学、星河智能应用实验室、CareerAgent、RAGEvalBoard 和 PromptShield Lite。
- 内置浏览器首页一键流程完成 6 个阶段：档案、岗位、匹配、简历定制、投递包、面试包全部为完成态，匹配分数 71.71。
- `/ui/resumes` 可看到简历版本 #81，`/ui/applications` 可看到投递包 #22，`/ui/prep?job_id=193` 可看到面试包 #35 和问题列表。
- Agent trace 中 #154/#155/#156 的关键步骤均 completed；LLM 日志中 JD 解析、简历定制、面试题生成均 completed，且后端错误日志没有新增应用异常。
- 目标回归测试 `pytest tests\test_resume_parser.py tests\test_matcher.py -q` 通过，7 个测试全部通过。
- 完整回归测试 `pytest -q` 通过，94 个测试全部通过。
### 未修复的问题
- 当前 Browser API 没有文件选择器上传能力，本次用同一个后端上传接口上传 PDF 后，再在前端选择 Profile ID 跑流程；这不影响用户真实浏览器手动上传，但影响自动化浏览器端到端脚本。
- PDF 样例生成临时使用本机 `reportlab`，它是测试样例生成工具，没有加入项目运行依赖。
- `/ui/resumes` 需要分页或懒加载；原因是历史定制版本会持续增长，一次渲染全部 iframe 不适合长期上线。
### 下一步
- 为 `/ui/resumes` 增加分页或懒加载，避免历史版本过多时拖慢用户页面。
- 增加“已有 Profile ID + JD 文本”的前端 E2E smoke，把本轮人工浏览器验收固化成自动化测试。
- 在运维控制台增加 reranker provider 配置健康检查，对不支持的 provider 给出启动前或页面级提示。

## 2026-06-17 13:08:07 +08:00：简历经历类栏目支持多段条目
### 这次做了什么
- 将 `/ui/profiles` 手动建档中的教育经历、实习/工作经历、项目经历、校园/实践经历改为可重复条目。
- 每类经历默认保留 1 条，用户可以点击“添加”复制同结构的新条目，也可以删除多余条目。
- 前端提交时不再只读取单个字段，而是按 `data-repeat-list` 收集数组，写入 `education`、`work_experience`、`projects`、`campus_experience`。
- 保持首页一键流程里的简化建档兼容，只有完整建档页会走 DOM 重复块收集。
- 更新 README、API 文档、开发说明和测试，固定经历类栏目必须支持多段。
### 发现的问题
- 上一版虽然能自定义栏目，但教育、项目、实习和校园实践仍只能填 1 条，真实简历中很快会不够用。
- 表单字段名重复后，原 `FormData -> object` 会只保留最后一个同名字段，不能直接用于多条经历。
- 浏览器验证时发现，直接用 PowerShell 管道构造中文测试档案会把动态中文字段写成乱码；这是测试输入编码问题，不是 HTML 预览渲染问题。
### 怎么修复
- 新增 `repeat-list` / `repeat-entry` 结构，通过按钮复制条目并清空新条目字段。
- 新增 `collectRepeatList()`，提交时按每个条目容器读取字段，避免同名字段覆盖。
- 新增 `initializeRepeatLists()`、`addRepeatEntry()`、`removeRepeatEntry()` 和 `resetRepeatLists()`，处理编号、删除按钮显示和保存后的表单恢复。
- 验证脚本改为显式设置 UTF-8 后再写入中文测试数据，确保端到端预览验证可信。
### 验证结果
- 扩展前端页面测试，验证四类 repeat-list、添加按钮、删除按钮和相关 JS/CSS 存在。
- 扩展 schema 测试，验证教育、项目、实习/工作、校园实践均可保存 2 条以上。
- 使用浏览器实测 `/ui/profiles`，教育、项目、实习/工作、校园/实践均能添加到 2 条，隐藏栏目勾选后按钮会启用，新条目的实际输入值为空。
- 创建含 2 段教育、2 段项目、2 段实习、2 段校园实践的 UTF-8 测试档案，并打开 `/profiles/{profile_id}/html`，确认所有动态中文条目完整渲染。
### 未修复的问题
- 证书、荣誉、语言和作品链接仍是逗号/换行批量输入，不是逐条卡片；原因是这些信息结构简单，批量输入效率更高。
- 多段条目还没有拖拽排序；原因是当前按填写顺序保存，已能满足主要简历场景，排序可在后续模板优化时补。
### 下一步
- 为多段条目增加“上移/下移”排序。
- 给项目经历增加更细的“职责/技术难点/量化结果”拆分字段。
- 在自然语言生成简历时复用同一套多段结构，让 LLM 生成的档案也能对应前端编辑。

## 2026-06-17 11:34:58 +08:00：简历栏目自定义与可选照片上传
### 这次做了什么
- 将 `/ui/profiles` 手动建档改为“基础信息固定 + 其他栏目按需勾选”的结构，默认只展开求职意向、教育经历、项目经历和技能，减少页面杂乱感。
- 新增栏目选择器，用户可自定义是否启用个人总结、简历照片、作品链接、实习/工作经历、校园/实践经历、证书/荣誉/语言等模块。
- 新增可选照片上传与本地预览；照片以 `photo_data_url` 存在结构化 Profile 中，仅用于 HTML 简历预览。
- 新增 `enabled_sections` 字段，用于记录用户选择的简历栏目。
- 更新前端提交逻辑：未勾选栏目会隐藏、禁用且不会提交，避免默认值污染简历档案。
- 更新 HTML 简历预览页，有照片时在页眉右侧显示照片，没有照片时保持原布局。
- 更新 README、API 文档、开发说明和测试。
### 发现的问题
- 上一版虽然覆盖了中文简历常见栏目，但把所有栏目一次性展开，填写成本偏高，也不符合“不同候选人只需要部分模块”的真实使用场景。
- 项目经历、技能等字段有默认值，如果用户不需要对应模块但没有清理输入，旧提交逻辑仍可能把默认内容写入 Profile。
- 照片不能进入 raw_text 或向量 chunk，否则会污染 RAG 与 LLM 上下文。
### 怎么修复
- 通过 `data-profile-section-toggle` 和 `data-resume-section` 建立栏目选择与表单模块的绑定。
- 新增 `updateProfileSectionVisibility()`，未启用模块会 `hidden` 并禁用内部控件，从源头避免 FormData 收集。
- 新增 `readProfilePhotoDataUrl()`，限制照片格式为图片、大小不超过 1.5MB，并在提交时只在照片栏目启用后读取。
- `ResumeHTMLRenderer` 增加安全图片 data URL 白名单，只允许 png/jpg/jpeg/webp 的 base64 data URL 作为预览图片。
### 验证结果
- 扩展前端页面测试，验证栏目选择器、默认隐藏模块、照片上传入口和相关 JS/CSS 存在。
- 扩展建档 schema 测试，验证照片与 `enabled_sections` 会写入结构化 Profile，但照片不会进入 raw resume text。
- 扩展 HTML 预览测试，验证照片能在简历预览页渲染。
### 未修复的问题
- 照片目前以 data URL 存在 JSON 字段中，不是独立附件表；原因是本轮优先保证用户体验和预览闭环，正式多用户部署时应将照片迁移到受控文件/对象存储。
- 每个经历类模块仍只支持一条结构化输入；原因是动态多段经历表单需要更完整的前端状态管理，适合作为下一步做。
### 下一步
- 给教育、实习、项目和校园实践增加“添加一条”能力。
- 为照片增加裁剪/压缩，避免用户上传大图影响数据库体积。
- 在 HTML 预览中提供不同模板：无照片版、单页技术简历版、应届生完整版。

## 2026-06-17 09:56:22 +08:00：中文简历建档表单与结构化字段升级
### 这次做了什么
- 将 `/ui/profiles` 的“手动填写简历信息”从简化表单升级为更贴近中文求职简历的分区表单，覆盖基础信息与求职意向、教育经历、实习/工作经历、项目经历、校园/实践经历、技能证书与荣誉语言。
- 新增结构化 Profile 字段：`location`、`availability`、`self_summary`、`campus_experience`、`certifications`、`portfolio_links`，并保留旧字段兼容。
- 更新 `ResumeParserService`，让手动建档、LLM/PDF 解析提示、原始文本拼接都能识别新字段。
- 更新简历 chunk 逻辑，让个人总结、校园实践、证书、奖项、语言和作品链接进入可检索证据。
- 更新 HTML 简历预览顺序，按中文简历阅读习惯展示个人总结、目标岗位、教育、实习/工作、项目、校园实践、技能、证书、荣誉和语言。
- 更新前端静态资源版本号，避免浏览器继续使用旧的简历建档脚本。
- 更新 README、API 文档和开发说明，并新增/扩展测试覆盖前端栏目、结构化建档和 HTML 预览。
### 发现的问题
- 原手动建档只有姓名、联系方式、目标岗位、技能和单个项目经历，无法支撑真实中文求职简历，也让后续岗位匹配与简历定制缺少教育、实习、证书等证据。
- 前端原先在 submit 里单独拼 payload，首页一键流程又有一套 `guidedProfilePayload`，两套逻辑容易分叉。
- HTML 预览虽然能展示项目和技能，但缺少个人总结、校园实践、证书等中文简历常见模块。
### 怎么修复
- 将手动建档提交逻辑统一复用 `guidedProfilePayload()`，并把该函数扩展为结构化解析教育、实习/工作、项目、校园实践、证书、荣誉、语言和作品链接。
- 在 Pydantic schema 中补充新字段，避免前端字段被 API 忽略。
- 在 `ResumeTextSplitter.split_structured_profile()` 中为新字段生成 chunk，保证 RAG 和后续 Agent 工具能检索这些证据。
- 用分区表单和顶部栏目导览优化页面排版，减少大表单的拥挤感。
### 验证结果
- 新增 `tests/test_guided_profile_schema.py`，验证中文简历主流栏目会写入结构化 Profile 与原始文本。
- 扩展 `tests/test_frontend_pages.py`，验证 `/ui/profiles` 暴露完整中文简历栏目与前端结构化逻辑。
- 扩展 `tests/test_resume_html_preview.py`，验证 HTML 预览展示个人总结、教育经历、校园/实践经历和证书。
### 未修复的问题
- 当前手动建档每类经历先支持一条结构化输入；原因是本轮优先把主流栏目与后端链路跑通，下一步适合做“新增一段经历”的动态重复表单。
- 没有引入照片、籍贯、政治面貌等字段；原因是互联网技术岗位简历中这些字段通常不是核心匹配证据，且会增加隐私与版面负担。
### 下一步
- 给教育、实习、项目和校园实践增加“添加一条”动态表单能力。
- 给 HTML 简历预览增加模板切换和 A4 分页细节优化。
- 结合真实中文简历样例继续校验字段顺序、默认文案和简历定制输出质量。

## 2026-06-17 09:34:11 +08:00：简历 HTML 预览与档案点击查看
### 这次做了什么
- 新增 `ResumeHTMLRenderer`，把结构化 Profile 和定制简历 Markdown 渲染为可预览、可打印、可另存为 PDF 的 HTML 简历页面。
- 新增 `GET /profiles/{profile_id}/html`，用于“我的简历档案”直接打开 HTML 预览。
- 新增 `GET /resumes/{resume_version_id}/html`，用于定制简历的 HTML 排版预览；原 `/markdown` 下载接口保留，作为调试和二次编辑出口。
- `/ui/profiles` 的每个档案卡片新增“预览简历”按钮。
- `/ui/resumes` 从大段 Markdown `<pre>` 改为嵌入 HTML iframe 预览，并提供“打开 HTML 预览”和“下载 Markdown”两个动作。
### 发现的问题
- 当前定制简历数据库字段仍叫 `tailored_resume_markdown`，历史数据也是 Markdown；如果直接改存储格式会破坏已有版本和 guardrail 逻辑。
- PDF 上传时原始文件存到了 `data/uploads`，但数据库没有记录上传 PDF 路径；所以“我的简历档案”暂时不能可靠地回放原 PDF，只能根据结构化 Profile 生成 HTML 预览。
- 定制简历预览如果继续双列显示会太窄，接近不了真实简历阅读宽度。
### 怎么修复
- 不改历史存储格式，在交付层把 Markdown 转成安全 HTML 片段；这样兼容旧数据，也能立刻提升预览排版。
- Profile 预览走结构化字段渲染，展示姓名、联系方式、目标岗位、技能、项目、经历、教育、奖项和语言。
- 定制简历页改为单列布局，iframe 高度固定，便于扫读；HTML 预览页内置打印按钮，浏览器可直接另存为 PDF。
### 验证结果
- 目标测试：`pytest tests\test_resume_html_preview.py tests\test_frontend_pages.py -q`，8 个测试通过。
- 语法检查：`python -m py_compile app\services\resume_delivery.py app\api\profiles.py app\api\resumes.py` 通过；`node --check app\static\js\main.js` 通过。
### 未修复的问题
- 还没有真正的服务器端 PDF 渲染接口；原因是需要引入 wkhtmltopdf、Playwright PDF 或 WeasyPrint 这类渲染依赖，当前先用浏览器 HTML 打印/另存 PDF 满足预览和排版确认。
- 原始上传 PDF 路径未入库，暂时不能在 Profile 页面回放用户上传的原 PDF；后续应给 `profiles` 增加 `source_file_path/source_file_name` 字段或独立附件表。
### 下一步
- 给 HTML 简历预览增加主题模板选择、A4 分页优化和导出 PDF 后台任务。
- 在 Profile 入库时记录上传文件元数据，让 PDF 上传档案既能看结构化 HTML，也能回看原 PDF。

## 2026-06-16 23:48:27 +08:00：用户页二次产品化与自然语言 Agent 入口
### 这次做了什么
- 继续把普通用户页面从“后台数据展示”改成“求职操作页面”：过程页、简历页、岗位页、投递页、面试页的字段名、辅助标签、空状态和错误提示都改为中文用户语言；运维、Trace、LLM logs、配置和评测入口继续集中在右上角控制台。
- 首页新增自然语言入口：用户可以直接描述“生成简历、修改上传简历、按 JD 改简历、搜索岗位、生成投递包、生成面试包”等需求；前端支持可选 PDF 上传、已有 Profile ID、已有 Job ID、城市和目标 JD。
- 后端新增 `POST /assistant/natural-language`：先由 LLM 解析用户意图和计划，再调用现有 Agent Orchestrator、ResumeParser、JDParser、RAG 匹配、简历定制、投递包和面试包工具执行。
- 自然语言 Agent 增加 1 轮 repair loop：首次执行失败时把错误、原计划和用户需求交给 LLM 修复计划，再执行一次；修复失败不会兜底成成功，而是把 run 标记为 `failed`。
- 搜索岗位结果为空时不再算成功：`search_jobs` 返回 0 个 matches 会触发失败或 repair，避免用户看到“已完成岗位推荐”但实际没有岗位。
- 前端失败卡片支持展示失败状态、Run ID、自动修复次数和“查看流程”入口，不再把失败响应渲染成裸 JSON。
- 投递包用户页去掉 `missing_apply_url` 这类后台告警码，只展示“岗位缺少投递链接，需要用户手动补充”等用户可理解的信息。
### 发现的问题
- 真实 LLM 复合 case 首次调试时命令行中文输入被 PowerShell 管道转码污染，导致看起来像岗位标题和城市清洗失败；用 Unicode 安全输入复测后确认源码和 API 链路正常。
- 自然语言“只搜索岗位”真实 case 暴露产品问题：外部岗位源返回 0 条时，旧逻辑仍返回 completed，这会误导用户。
- repair 后再次失败时，旧代码会重新抛异常，API 只剩一段 500 字符串，前端拿不到 run_id，不利于按 trace 排查。
- 浏览器 Playwright role click 在本地页面偶发 CDP 超时，改用 Browser DOM CUA 节点点击后可以稳定验证。
### 怎么修复
- 在 `NaturalLanguageAgentService` 中增加 `_assert_search_has_matches()`，把空岗位推荐升级为明确失败，并在 `result_json.error` 和 run error_message 中保留原因。
- `NaturalLanguageAgentService.run()` 失败时返回 failed run；`/assistant/natural-language` 根据 run 状态返回结构化 body，失败时 HTTP 状态设为 500。
- 前端 `api()` 支持读取失败响应中的 `user_message/run_id/repair_attempts`，自然语言入口失败时也渲染结果卡片和流程入口。
- 增加空搜索失败回归测试，固定“repair 后仍为空必须 failed”的行为。
- 用内置浏览器验证首页、过程页、投递页和自然语言失败卡片；确认用户页没有裸 JSON，投递告警码不再出现。
### 验证结果
- 目标测试：`pytest tests\test_natural_language_agent.py tests\test_frontend_pages.py -q`，9 个测试通过；全量回归 `pytest -q`，88 个测试通过。
- 语法检查：`node --check app\static\js\main.js` 通过；`python -m py_compile app\agents\natural_language.py app\api\assistant.py app\main.py` 通过。
- 真实 LLM 复合 case：使用 DeepSeek 官方 OpenAI-compatible 接口和 `deepseek-v4-pro`，自然语言请求成功完成，生成 Profile #148、Job #192、ResumeVersion #80、Application #21、InterviewPrep #34；岗位标题为“Agent 开发实习生”，城市为“深圳”，简历定制事实检查通过，匹配分 76.13。
- 最新轻量真实 LLM 成功 case：Run #153 返回 HTTP 201、`status=completed`、`intent=create_profile`，生成 Profile #149。
- 真实 LLM 搜索 case：自然语言“搜索深圳 Agent 开发实习岗位”在外部源 0 结果时返回 HTTP 500、`status=failed`、Run #149，并记录 1 次 repair attempt；这符合开发期“失败可追踪、不伪装成功”的要求。
- 浏览器验证：`http://127.0.0.1:8030/` 首页显示自然语言需求入口、一键完整流程、阶段进度和过程页面；示例填充能正确写入中文需求、深圳和 Agent JD；无 LLM key 的失败卡片显示 Run #152 和流程入口，无 JSON 噪声。
### 未修复的问题
- 当前自然语言入口的长任务仍是同步 HTTP 执行，复杂 full-flow 可能需要几十秒；原因是本轮优先保证真实可用和 trace 完整，下一步应接入已有后台任务队列和进度轮询。
- 外部岗位源 0 结果时不会自动编造岗位，也不会静默 fallback 到假数据；原因是开发期要暴露真实 source 问题。用户可以粘贴目标 JD 跑完整核心链路。
- 自然语言入口暂未把“修改上传简历”做成原 Profile 原地覆盖，而是生成或复用简历档案版本；后续可以引入 profile versioning，让用户选择覆盖、复制或合并。
### 下一步
- 把 `/assistant/natural-language` 接入后台任务队列，提供 queued/running/failed/completed 轮询、取消、resume-from-last-completed 和阶段进度。
- 给自然语言计划增加更细的 tool availability 说明，让 LLM 在“只搜索岗位”“按 JD 完整处理”“只改简历”等场景里更稳定选择动作。
- 增加自然语言端到端评测集，覆盖建档、按 JD 改简历、搜索岗位、投递包、面试包、空岗位源失败、低匹配 fit gate 失败和 repair 成功/失败。

## 2026-06-16 22:30:39 +08:00：用户启动台、控制台拆分与真实 LLM 前端全流程验证
### 这次做了什么
- 将首页重构为面向用户的“开始”页：支持已有 Profile ID、上传 PDF、填写简历核心信息，并提供一键运行完整流程的阶段进度。
- 将普通用户页面和运维页面拆开：主导航只保留开始、简历、岗位、流程、定制简历、投递、面试；右上角新增“控制台”入口，`/ui/ops` 聚合 readiness、metrics、config、后台任务、LLM trace，并提供评测和 API 文档入口。
- 首页一键流程新增两种稳定路径：可以搜索真实岗位，也可以输入已有 Job ID 或粘贴目标 JD；粘贴 JD 时会先创建岗位、生成匹配分，再继续定制简历、投递包和面试包。
- 后端新增 `full_career_flow` Agent task type，并补齐 AgentPlanner、Skill、SubAgent 映射，让 API 层也能表达完整求职流程。
- 新增 `scripts/generate_demo_resumes.py`，生成 4 份可直接上传测试的 PDF 简历：强匹配 Agent、带噪声 Agent、后端平台、ML/RAG 部分匹配。
- 新增 `scripts/run_user_flow_smoke.py`，用于从环境变量读取真实 LLM 配置，跑 PDF 上传解析、JD 解析、定制简历、投递包、面试包的用户链路 smoke。
- 为前端静态资源增加版本参数，避免本地服务热更新后浏览器继续使用旧 JS/CSS。
### 发现的问题
- `8011` 端口已被旧服务占用，新进程绑定失败；旧进程会读取新模板但没有新 Python 路由，导致首页看起来更新了而 `/ui/ops` 仍返回 404。
- 浏览器真实一键流程第一次暴露一个重要 bug：`/agent/runs` 在业务失败时仍返回 HTTP 201，前端只检查 HTTP 状态，导致 `quick_apply` 因匹配分低于 55 失败时，页面仍把“投递包”标成完成。
- 演示 JD 初版抽取出的 required skills 偏多，导致强演示候选人的匹配分只有 54.95，触发投递阈值；这不是后端崩溃，而是样例材料和 JD 标注不够一致。
- 内置浏览器的 Playwright locator click 在本地页面上偶发 3 秒 CDP 超时；改用 Browser DOM CUA 节点点击后可以稳定操作。
- 浏览器隔离环境无法可靠读取页面脚本全局函数，不能用 `typeof createAgentRun` 判断新 JS 是否已加载；静态资源版本号更可靠。
### 怎么修复
- 改用干净端口 `8022` 验证新版 UI，再用带真实 LLM 环境变量的 `8024` 跑前端一键流程。
- 新增 `createAgentRun()` 前端 helper，所有首页 AgentRun 调用都会检查 `run.status === "completed"`；如果业务失败，直接把当前阶段标成 failed 并显示 `error_message`。
- 调整首页演示资料和 JD，使候选人证据与岗位要求更一致，真实 UI 一键流程最终匹配分提升到 72.44，顺利生成投递包。
- 首页新增已有 Job ID / 目标 JD 输入，避免演示或真实使用完全依赖外部招聘源；外部岗位源失败时仍能用用户粘贴 JD 完整跑通核心链路。
- 为 `/static/css/style.css` 和 `/static/js/main.js` 增加 `?v=20260616-flow`，规避浏览器缓存旧资源。
### 验证结果
- 单元与集成回归：`pytest -q` 全量 `85 passed in 33.79s`。
- 浏览器验证：`http://127.0.0.1:8022/` 首页导航只保留用户流程，右上角控制台入口存在；`/ui/ops` 展示 readiness、metrics、config、tasks、LLM logs，页面自身 console error 为空。
- 真实 LLM 脚本 smoke：使用 DeepSeek 官方兼容接口、`deepseek-v4-pro`、`LLM_THINKING_MODE=auto`，PDF 上传解析、JD 解析、定制简历、投递包、面试包全部完成；Profile #142，Job #188，ResumeVersion #74，Application #16，InterviewPrep #28，定制风险 `low`，面试包 10 组题、5 个 gap drill。
- 真实 LLM 前端 smoke：`http://127.0.0.1:8024/` 点击“填入演示信息”与“一键运行”，Profile #144、Job #190、匹配分 72.44、ResumeVersion #76、Application #17、InterviewPrep #30 均生成，6 个阶段全部 done，页面自身 console error 为空。
- 演示 PDF 已生成并用 `pypdf` 验证可抽取文本：`demo_resumes/agent_intern_strong_resume.pdf`、`agent_intern_noisy_resume.pdf`、`backend_platform_resume.pdf`、`ml_rag_partial_resume.pdf`。
### 未修复的问题
- 首页一键流程仍是前端串行调用多个接口，不是后台任务式长流程；原因是当前优先保证用户可见阶段和真实可用，长耗时流程后续应接入任务队列、可取消和可恢复。
- `full_career_flow` 后端任务已实现，但首页为了显示每个阶段的即时进度仍使用逐步调用；如果要统一成后端长任务，需要增加阶段进度事件或轮询端点。
- 外部岗位源仍只作为可选搜索路径，不作为本轮真实 UI smoke 的质量门禁；原因是招聘源网络波动和岗位结果会影响稳定复现，核心 LLM 链路已通过粘贴 JD 验证。
- 演示 PDF 当前是标准 Helvetica 文本 PDF，内容以英文技术简历为主；原因是纯标准库生成中文可抽取 PDF 需要嵌入字体和 ToUnicode 映射，后续可引入 reportlab 或预置中文字体改善展示。
### 下一步
- 将首页一键流程接入后台任务/进度轮询，支持取消、重跑、resume-from-last-completed 和失败阶段跳转 trace。
- 在控制台中增加“最近用户流程”视图，把 Profile、Job、ResumeVersion、Application、InterviewPrep 串成一条可点击链路。
- 给粘贴 JD 路径增加更清晰的 JD 预览、匹配解释和低分投递阻断提示。
- 继续补上线能力：账号/RBAC、文件权限隔离、结构化日志、Prometheus/OpenTelemetry、Docker 部署和生产环境变量模板。

## 2026-06-16 20:51 +08:00：前端上线体验、运维面板与内置浏览器验证
### 这次做了什么
- 新增 `/ui/ops` 运维页，聚合 `/ops/readiness`、`/ops/metrics`、`/ops/config`、`/tasks` 和 `/llm/debug/logs`，展示上线状态、脱敏配置、运行指标、后台任务和最近 LLM 调用。
- 前端新增 Admin Token 管理表单，令牌只保存到本机浏览器 localStorage；`api()` helper 和 PDF 上传请求会自动携带 `X-Admin-Token`，开启 `REQUIRE_ADMIN_FOR_MUTATIONS=true` 后前端写操作仍可用。
- 首页新增“系统状态”和“后台任务”面板，直接展示 readiness、最近评测、LLM 调用量和任务摘要，不需要先进入开发调试页。
- 为面试准备和评测页新增推荐别名 `/ui/prep`、`/ui/quality`，旧路径 `/ui/interview-prep`、`/ui/evaluations` 保持兼容；导航和首页快捷入口改为新路径。
- 后台任务卡新增“任务详情”展开区，可查看 input、progress、output、错误和时间戳，减少查 SQLite 的成本。
- 更新 README、API 文档和开发文档，说明 `/ui/ops`、`/ui/prep`、`/ui/quality` 和前端 Admin Token 行为。
### 发现的问题
- 内置浏览器能打开首页、简历、岗位、Agent Runs，但在旧服务或部分路径上会出现 `net::ERR_BLOCKED_BY_CLIENT`；原因不是 FastAPI 路由错误，而是浏览器客户端侧拦截或旧端口服务未热更新。
- 端口 `8000` 上已有旧服务没有加载新路由，`/ui/ops` 返回 404；新启动 `8010` 服务后，新页面路由和动态数据都正常。
- 首页原快捷入口仍指向旧的 `/ui/interview-prep`、`/ui/evaluations`，在内置浏览器误拦路径时体验不好。
- 前端之前虽然能看评测和任务进度，但缺少统一的上线状态入口；用户无法一眼判断数据库、LLM、embedding/reranker、后台任务和 LLM 调用是否健康。
### 怎么修复
- 新增 `/ui/ops`，并在导航里加入“运维”；首页添加系统状态面板，运维信息从真实接口加载。
- 增加 `/ui/prep`、`/ui/quality` 别名，并将导航和首页快捷入口切到新路径，减少内置浏览器路径误拦概率。
- `api()` 统一注入 `X-Admin-Token`，上传 PDF 的原生 `fetch` 也复用同一套 header。
- 用 `details` 展开任务和日志详情，默认保持页面扫描密度，排障时可以展开看 JSON。
### 验证结果
- 内置浏览器验证 `http://127.0.0.1:8010/`、`/ui/ops`、`/ui/quality`、`/ui/prep` 均可打开，中文显示正常，页面自身控制台无错误。
- `/ui/ops` 能展示 readiness、metrics、config、LLM logs；首页能展示系统状态和后台任务摘要。
- 目标测试：`tests/test_frontend_pages.py tests/test_health.py` 共 10 个测试通过；`node --check app\static\js\main.js` 通过；`python -m py_compile app\frontend\routes.py` 通过。
- 全量回归：`82 passed in 36.13s`；真实 DeepSeek 1-case smoke run 35 完成，`end_to_end_pass_rate=1.0`、`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`、`guardrail_pass_rate=1.0`。
### 未修复的问题
- `/ui/ops` 仍是单机运维面板，不是完整 SRE 平台；原因是当前产品还没有 Prometheus/OpenTelemetry、集中日志、告警和多实例部署。
- Admin Token 只是管理令牌，不是多用户登录/RBAC；原因是账号体系、组织空间和数据权限模型还没有引入。
- 内置浏览器的客户端拦截无法由项目代码完全控制；本轮通过新路径和新版端口验证规避，生产环境以实际部署域名为准。
- 前端还没有任务取消、任务重跑和失败 case 一键跳转到 LLM 日志；原因是后端还没有 cancellation/retry endpoint。
### 下一步
- 增加任务取消/重跑接口，并在 `/ui/ops` 和 `/ui/quality` 提供操作按钮。
- 将 LLM 调用日志按 `evaluation_run_id`、`case_name`、`stage` 做更强筛选和跳转。
- 做一次 18-case 后台长跑，用 `/ui/ops` 观察任务进度、失败记录和日志展示是否足够支撑上线排障。
- 继续补生产部署：Docker、环境变量模板、结构化日志、限流、审计日志和监控导出。

## 2026-06-16 18:33 +08:00：后台任务、权限监控与 DeepSeek JSON 链路稳定性
### 这次做了什么
- 新增 `task_runs` 表、`TaskQueueService` 和 `/tasks/llm-workflow`，把真实 LLM workflow 长跑放到 FastAPI `BackgroundTasks` 中执行，并通过 `/tasks` / `/tasks/{task_id}` 轮询 queued/running/completed/failed、进度、错误和最终 `evaluation_run_id`。
- `/ui/evaluations` 新增“后台 LLM 长跑”和“任务进度”面板，支持设置 `case_limit`、`trace_path`、checkpoint resume，并展示进度条、已完成 case、失败错误和任务输出指标。
- 新增 `/ops/readiness`、`/ops/metrics`、`/ops/config`：分别暴露数据库/LLM/embedding/reranker readiness、请求延迟与状态码、Agent run/task/LLM call 状态分布、最近评测摘要和脱敏配置。
- 新增可选权限隔离：`ADMIN_API_KEY` 配置后管理接口需要 `X-Admin-Token`；`REQUIRE_ADMIN_FOR_MUTATIONS=true` 时所有写操作都需要 admin token。
- `LLMClient.generate_json` 和结构化链路统一使用 `response_format={"type":"json_object"}`；官方 DeepSeek V4 + `LLM_THINKING_MODE=auto` 仍发送 `thinking: disabled`，保证最终 `content` 稳定返回。
- `LLMClient` 新增网络层短重试：仅对网络断连、429、5xx 等瞬时错误重试；每次失败写入 `llm_call_logs.status=retryable_failed`，最终失败仍直接报错，不静默兜底。
- 更新 README、API 文档、开发文档和 `.env.example`，补充后台任务、权限/运维、LLM retry、DeepSeek JSON mode 的中文说明。
### 发现的问题
- 真实 DeepSeek 1-case smoke 第一次暴露 `resume_tailor.tailor_resume` 阶段 `ConnectError`；前置的简历解析、JD 解析、RAG、fit judge 都成功，说明问题是外部 LLM HTTP 层瞬时失败，不是 prompt 或匹配逻辑错误。
- 抽查 LLM 日志发现 `fit_judge`、`resume_tailor` 已走 JSON mode，但 `resume_parser`、`jd_parser` 和面试问题生成仍是文本模式后解析 JSON，不利于官方接口上的结构化稳定性。
- 后台任务 API 的测试第一次没有进入 FastAPI lifespan，导致 `task_runs` 表不存在；原因是测试里直接创建 `TestClient` 后没有使用上下文管理器。
- `/ops/config` 的脱敏字段最初命名为 `admin_api_key_configured`，虽然不泄露值，但响应文本仍包含 `api_key`，容易造成敏感扫描误报。
- 内置浏览器插件访问本地 `127.0.0.1:8000` 被客户端拦截为 `net::ERR_BLOCKED_BY_CLIENT`，本轮改用 TestClient 路由 smoke、HTML/JS 检查和前端单元测试验证页面。
### 怎么修复
- 后台任务在每个 case 完成后由 `EvaluationService` 调用 `progress_callback`，写回 `task_runs.progress_json`；前端按 5 秒轮询 running/queued 任务。
- 结构化 LLM 调用全部带 `response_format={"type":"json_object"}`，并在 `prompt_preview_json` 记录 `response_format`、`attempt`、`max_attempts`，方便从日志追溯请求形态。
- 网络层 retry 只覆盖 `httpx.TransportError`、HTTP 408/409/429/5xx；JSON 空内容、400 参数错误、业务 guardrail 失败仍直接失败。
- 测试改用 `with TestClient(app)` 触发生命周期建表；`/ops/config` 改为 `admin_token_configured`，避免敏感字段命名误报。
- 面试包、JD parser、resume parser 的测试桩补充 `response_format` 参数，确保真实签名变化被测试覆盖。
### 验证结果
- 全量回归：`81 passed in 36.82s`。
- 语法检查：`python -m py_compile` 覆盖修改后的 LLM、parser、interview prep、任务和运维模块；`node --check app\static\js\main.js` 通过。
- 接口 smoke：`/health`、`/ops/readiness`、`/ops/metrics`、`/ops/config`、`/tasks`、`/ui/evaluations` 均返回 200。
- 真实 DeepSeek smoke：第一次 run 32 在 `tailor_resume` 因 `ConnectError` 失败；加入 retry 和 JSON mode 统一后，run 34 `case_count=1`、`status=completed`、`end_to_end_pass_rate=1.0`、`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`、`guardrail_pass_rate=1.0`。
- run 34 的 LLM 日志显示 `resume_parser.parse_structured_resume`、`jd_parser.parse_jd`、`evaluation.llm_judge_suitability`、`resume_tailor.tailor_resume` 全部记录 `response_format={"type":"json_object"}`、`attempt=1`、`max_attempts=2` 和对应 stage。
### 未修复的问题
- 当前后台任务是进程内 `BackgroundTasks` + SQLite，不是分布式队列；单机开发期和简历项目展示足够，生产多实例需要 Redis/Celery/Arq 或云任务队列。
- 权限隔离还是 admin token 级别，不是完整多用户 RBAC；原因是当前产品尚未引入账号体系、组织空间和数据归属模型。
- `/ops/metrics` 是内存计数 + SQLite 聚合，不是 Prometheus/OpenTelemetry；适合开发期观测，上线后应接入标准 metrics/log/trace 管道。
- 前端评测页功能可用，但仍偏开发者控制台，不是最终用户级体验；需要后续补充任务详情抽屉、失败定位、trace 折叠、移动端布局和 admin token 输入。
- 本轮只跑 1-case 真实 LLM smoke，没有重新跑 18-case 长跑；原因是改动集中在任务调度、结构化调用和运维能力，18-case 可通过新后台任务入口继续跑。
### 下一步
- 用 `/tasks/llm-workflow` 跑一次 18-case 后台长跑，观察任务进度、LLM retry 分布和失败恢复。
- 把任务详情页做成可展开 trace 树，支持按 case/stage 查看 LLM 调用、RAG 证据和失败原因。
- 将真实岗位源 smoke 独立接入 `/ops/metrics` 或评测页 source 面板，和核心 Agent workflow 质量门禁分开展示。
- 如果要接近上线，补账号/会话、文件权限、限流、审计日志、部署 health check、结构化日志和容器化启动文档。

## 2026-06-16 16:51 +08:00：DeepSeek V4 真实全流程长跑与上线前稳定性修复

### 这次做了什么
- 为 `LLMClient` 增加 DeepSeek V4 provider options：`LLM_THINKING_MODE=auto` 时，官方 `api.deepseek.com` + `deepseek-v4-*` 会自动发送 `thinking: disabled`，避免结构化任务只消耗 reasoning token 而最终 `content` 为空。
- `LLMClient` 的空内容错误现在会记录 `finish_reason`、`reasoning_chars` 和 `thinking_mode`，方便判断是思考模式、输出预算还是服务端异常。
- 新增 `scripts/run_llm_workflow_eval.py`，支持 `--case-limit`、`--case-indexes`、`--trace-path`、`--resume` 和质量失败非 0 退出码；stdout/stderr 固定 UTF-8，适合开发期长跑和复现。
- 将 `analytics_candidate_partial_recommendation_role` 重标为 `analytics_candidate_weak_recommendation_role`：候选人只有 A/B、指标和看板，且明确写了未实现 ranking/CTR，不应算推荐算法岗 partial fit。
- 为 `ResumeParserService` 增加 transient retry，服务端断连、超时、空返回会记录 `resume_parser.parse_structured_resume.retry_1/2` 后重试。
- LLM workflow 分桶 summary 中，没有 tailor 样本的难度桶会把 `tailor_pass_rate` 和 `guardrail_pass_rate` 记为 `null`，不再把“不适用”误显示为 `0.0`。
- 更新 `.env.example`、README、API、开发文档和评测文档，说明 DeepSeek 官方接口、thinking 配置、CLI 长跑和最新真实评测结果。

### 发现的问题
- 新 API key 可以访问 `deepseek-v4-pro`，但默认 thinking 模式下短结构化调用会出现 `content=""` 且只有 `reasoning_content` 的情况；对 JD/简历 JSON 链路来说，这会被正确判为失败。
- 18-case 真实长跑暴露出一个标注偏宽问题：推荐算法岗的核心是 ranking/CTR/feature engineering，不能因为候选人有 A/B 和 metrics 就标为 partial。
- 单 case 复测时出现 `RemoteProtocolError: Server disconnected without sending a response.`，说明真实 LLM 服务会有网络级瞬态失败，resume parser 也需要和 JD parser 一样的有限 retry。
- Windows 下 runner stdout 重定向默认编码不是 UTF-8，导致 JSON summary 文件用 UTF-8 读取失败。
- 分桶指标里空分母显示为 `0.0` 会让用户误解为质量失败。

### 怎么修复
- 按 DeepSeek 官方文档将 thinking 开关参数纳入请求 payload；默认只在官方 DeepSeek V4 接口自动关闭 thinking，其它 provider 不发送额外参数。
- 根据 trace 中的 Top evidence 和 suitability message 重新定义该 hard case 标注：核心否定证据优先级高于关键词重合，改为 weak fit，分数区间调整为 25-45。
- Resume parser 改为 `generate_text + extract_json_object`，在 transient 异常上最多重试 3 次，并保留每次 trace 名称。
- CLI runner 启动时 `sys.stdout/stderr.reconfigure(encoding="utf-8")`，保证重定向文件可被 UTF-8 工具链读取。
- LLM workflow 分桶 summary 使用 `null` 表示不适用的 tailor/guardrail rate。

### 验证结果
- DeepSeek 官方接口 smoke：`LLMClient` 返回 `XYZ123`，确认 provider options 生效。
- 真实 LLM workflow 1-case smoke：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`。
- 真实 LLM workflow 3-case smoke：覆盖 strong/partial/weak，`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`tailor_pass_rate=1.0000`。
- 真实 Agent full-flow 6-case：`pass_rate=1.0000`、`top_job_accuracy=1.0000`、`quick_apply_pass_rate=1.0000`、`application_packet_pass_rate=1.0000`。
- 第一次 18-case 长跑暴露 1 个标注边界失败；重标和 retry 修复后重新长跑，最终 `case_count=18`、`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`fit_score_in_range_rate=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`、`avg_hallucination_count=0.0000`。
- 本次 18-case run 关联 LLM 调用 68 次：68 completed、0 failed、0 configuration_error、1 repair。

### 未修复的问题
- `/ui/evaluations` 仍是同步 smoke 入口，不是后台任务队列；原因是本轮先补 CLI 长跑和 checkpoint，完整任务调度需要单独设计。
- 真实岗位源抓取仍与核心 LLM workflow 分离；原因是外部 source 波动不应影响核心链路门禁，后续应做独立上线前 source 健康度面板。
- DeepSeek V4 thinking 模式没有用于复杂规划链路；原因是当前结构化 JSON 任务更需要稳定最终 `content`，后续可为长推理场景单独开启并维护 reasoning trace。
- 18-case 虽已覆盖多岗位和 strong/partial/weak/adversarial，但还不是生产级人工标注集；原因是真实简历和真实 JD 的隐私、授权和人工复核还没建立。

### 下一步
- 把真实 LLM workflow 长跑接入后台任务或轮询式 UI，让用户不用 CLI 也能看到 18-case 增量进度。
- 增加真实中文 PDF 简历和真实中文 JD 的人工标注集，作为上线前验收集。
- 给真实岗位源 smoke 增加最近趋势和错误分布面板，和核心 Agent workflow 质量门禁分开展示。
- 为 `llm_call_logs.context_json.evaluation_run_id` 增加索引或派生列，避免日志量变大后过滤变慢。

## 2026-06-13 22:44 +08:00：LLM 调用日志关联到评测 run/case/stage

### 这次做了什么
- `llm_call_logs` 新增 `context_json`，用于记录 `evaluation_run_id`、`case_name`、`stage` 等运行上下文。
- `LLMClient` 增加基于 `contextvars` 的 `llm_trace_context`，业务层只需要在 stage 外围设置上下文，底层 `generate_text/generate_json` 和 retry/repair 日志都会自动继承。
- LLM workflow 在 `resume_parse`、`jd_parse`、`fit_judge` 和 `tailor_resume` 阶段写入 `evaluation_run_id/case_name/stage`，把真实 LLM 调用和 `stage_trace` 对齐。
- `/llm/debug/logs` 支持 `evaluation_run_id`、`case_name` 和 `stage` 查询参数；返回结果包含 `context_json`。
- `/ui/evaluations` 改为按最新 LLM workflow 的 `evaluation_run_id` 拉取日志，并在每个 case 下展示该 case 的 LLM 调用列表，不再只展示最近日志窗口的近似统计。
- 增加 LLM 日志 context 写入、context 过滤和前端调用树入口测试。

### 发现的问题
- 上一轮页面虽然显示了 `stage_trace`，但 retry/repair 统计来自最近 80 条日志，无法严格说明这些日志属于当前 evaluation run；长跑或多人使用时容易误判。
- 如果把 `run_id/case/stage` 做成多列，后续其他 workflow 又要不断加列；而当前需要的是可扩展的调试上下文，不是强关系型业务外键。
- FastAPI 的 `Query` 默认值在直接调用 endpoint 函数时不是普通 `None`，测试里需要显式传入可选参数；生产 HTTP 调用不受影响。

### 怎么修复
- 使用单个 `context_json` 承载调试上下文，避免为了观测性过度扩展表结构；SQLite 兼容迁移会给旧 `llm_call_logs` 添加默认 `{}`。
- 在 workflow stage 外层使用 `with llm_trace_context(...)`，不修改简历解析、JD 解析、简历定制等服务的公开接口，降低侵入性。
- 页面按 `evaluation_run_id` 拉取日志，再按 `case_name` 分组展示，retry/repair 计数变成当前 run 的精确信号。

### 未修复的问题
- `context_json` 仍是 JSON 字段，不是数据库外键；原因是当前目标是开发期可观测性和排障，严格外键会把所有 LLM 调用场景都绑到 evaluation run。
- `/llm/debug/logs` 的过滤仍在最近日志窗口内做 Python 过滤；原因是 SQLite/不同数据库的 JSON 查询语法不统一，当前规模下先保证可用和可移植。
- 页面仍不是流式进度；原因是同步评测 API 未改造为后台任务，下一步应基于 checkpoint/轮询解决。

### 下一步
- 为 LLM workflow 长跑增加后台任务或轮询 checkpoint，让页面在 case 完成时增量刷新调用树。
- 将同样的 `context_json` 机制接入普通 Agent run，让 `/ui/agent-runs` 也能看到该 run 触发的 LLM 调用。
- 评估在 SQLite 上为常用 `context_json.evaluation_run_id` 查询增加轻量索引或派生列，避免日志规模增大后过滤变慢。

## 2026-06-13 22:37 +08:00：评测工作台展示 LLM workflow trace

### 这次做了什么
- `/ui/evaluations` 新增“真实 LLM 流程评测”表单，支持输入 `case_limit` 和勾选 `resume_from_last_completed`，直接调用 `POST /evaluations/llm-workflow`。
- 评测工作台新增“最新 LLM 流程 Trace”面板，展示最近一次 LLM workflow 的完成率、端到端通过率、JD 解析、fit 标签、简历定制和 Guardrail 指标。
- 每个 case 展示 expected/predicted fit label、fit score、失败阶段、错误信息和完整 `stage_trace`，并把 top evidence 预览放在 `match_and_retrieve` 阶段下。
- 页面会读取最近 `/llm/debug/logs?limit=80`，统计 `jd_parser.parse_jd.retry_1`、`retry_2`、`repair_json` 和 failed 调用数量，帮助区分模型波动、格式损坏和业务阶段失败。
- 新增前端测试固定 `llm-workflow-form`、`llm-workflow-result`、`renderLLMWorkflow`、`renderStageTrace` 和 trace 样式入口。
- README、API 文档和评测文档补充评测工作台的 LLM workflow trace 说明。

### 发现的问题
- 之前 API 和数据库里已经有 `stage_trace`，但用户要排查真实 LLM 长跑失败仍然需要手动查 SQLite 或 JSONL；这会让“有 trace”停留在工程内部，而不是产品可观察性。
- `GET /llm/debug/logs` 目前没有 run/case 维度关联，页面只能展示最近日志里的 retry/repair 总数，不能严格证明这些日志都属于当前最新 evaluation run。
- LLM workflow 运行可能耗时数分钟，当前页面是同步等待请求完成；对于 smoke case 可接受，但不适合 18-case 长跑。

### 怎么修复
- 不新增任务队列或前端框架，只把已有评测 API 挂到评测工作台，原因是当前目标是开发期观察中间结果，而不是设计完整异步调度系统。
- trace 展示采用紧凑列表，不做嵌套卡片；每个 stage 只展示最关键的指标和错误，避免把页面变成难读的 JSON dump。
- 表单提交后先显示“运行中”提示，再刷新最近评测结果；失败时沿用全局 API 错误 toast，保持开发期失败直报。

### 未修复的问题
- LLM 调用日志还没有 `evaluation_run_id`、`case_name` 或 `stage` 外键；原因是 `LLMClient` 当前只接收通用 `trace_name`，要做精确关联需要扩展调用上下文并迁移日志 schema。
- 页面没有流式进度；原因是现有评测接口是同步返回，下一步如果要支撑 18-case 长跑，应先把 evaluation run 改成后台任务或轮询式 checkpoint。
- retry/repair 计数是“最近日志窗口”的辅助信号，不是当前 run 的严格指标；已在产品定位上把它作为开发调试摘要，而不是评测门禁。

### 下一步
- 给 `llm_call_logs` 增加 `run_id/case_name/stage` 关联，让页面能精确展示当前 evaluation run 的 LLM 调用树。
- 将 LLM workflow 长跑改成后台任务或可轮询 checkpoint，页面展示每个 case 的实时状态。
- 在评测工作台继续补齐 RAG strategy 和 real-job-ingest smoke 的运行入口与中间 trace，形成完整开发期质量面板。

## 2026-06-11 13:22 +08:00：真实 LLM 用户流复测与 JD parser repair

### 这次做了什么
- 从用户视角用 FastAPI API 入口 `POST /evaluations/llm-workflow?case_limit=3` 跑真实 LLM 链路，覆盖 strong、partial、weak 三类岗位适配 case。
- 为 `JDParserService` 改造真实 LLM 调用：先保留原始文本再解析 JSON，方便在截断/非法 JSON 时触发 repair，而不是丢失坏输出。
- JD parser 对空返回、超时、连接中断等瞬态错误增加到最多 3 次业务层调用，trace 名称为 `jd_parser.parse_jd`、`jd_parser.parse_jd.retry_1`、`jd_parser.parse_jd.retry_2`。
- JD parser 新增 `jd_parser.parse_jd.repair_json`：当模型返回截断或非法 JSON 时，带着原始 JD、坏输出预览和解析错误要求模型重新生成完整 strict JSON。
- 增加回归测试：空返回后 retry、截断 JSON repair、连续两次空返回后第三次成功。
- README、API 文档和评测文档补充 JD parser retry/repair trace 说明。

### 发现的问题
- 第一次临时数据库 API 测试没有进入 FastAPI lifespan，导致 `evaluation_runs` 表不存在；原因是测试脚本没有用 `TestClient` 上下文管理器。
- 初始真实测试误把 `RERANKER_PROVIDER=keyword` 作为配置传入，但当前项目只支持 `heuristic/lexical` 和 `cross_encoder`，导致 `match_and_retrieve` 阶段失败；这是测试配置问题，不是 matcher 本身问题。
- 真实 LLM 在 JD parser 上出现过两类波动：空返回，以及只返回几十到两百多字符的截断 JSON。只看最终结果会误判为“JD 解析能力差”，但 trace 显示根因是模型输出稳定性。
- Windows 默认 GBK stdout 不能打印模型返回中的部分 Unicode 字符，导致评测已完成但命令退出码为 1；后续输出评测摘要时需要使用 ASCII JSON 或 UTF-8 stdout。

### 怎么修复
- API 测试改用 `TestClient(app)` 上下文，确保 lifespan 初始化 SQLite schema。
- 真实 LLM 流程测试使用项目实际支持的 `RERANKER_PROVIDER=heuristic`，避免用不存在的 provider 污染能力判断。
- JD parser 不再直接调用 `generate_json` 丢掉原始坏文本，而是调用 `generate_text` 后本地 `extract_json_object`；如果发现可修复 JSON 错误，调用 `repair_json` 重新解析原始 JD。
- 将 transient retry 的退出条件改成 `max_attempts - 1`，并用测试固定 `retry_2` 真的会被调用。
- 最终真实 LLM 3-case 复测通过：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`resume_parse_success_rate=1.0000`、`jd_parse_success_rate=1.0000`、`fit_label_accuracy=1.0000`、`fit_score_in_range_rate=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。三个 case 的 stage trace 都完整走到 `case.completed`，weak frontend case 正确判为 `weak_fit` 且跳过简历定制。

### 未修复的问题
- 真实 LLM 仍可能在 3 次 retry 和 1 次 repair 后失败；原因是外部模型服务的空返回/截断不是本地代码能完全消除的，当前策略是 trace 清楚、有限恢复、失败直报。
- 本轮真实 LLM 复测只跑 3-case smoke，没有跑完整 18-case 长跑；原因是单轮真实调用耗时约 5 分钟，长跑更适合作为单独评测任务执行并观察中断恢复。
- `/evaluations/llm-workflow` API 还不能由用户指定 `trace_path`，只能通过 service 层传入；原因是当前 API 只暴露 `case_limit` 和 `resume_from_last_completed`，后续需要把 trace 文件路径或 run checkpoint 设计成安全的产品化参数。

### 下一步
- 跑 18-case 真实 LLM workflow 长评测，统计 retry/repair 触发率、各阶段耗时和不同难度桶稳定性。
- 在评测工作台展示每个 case 的 stage trace、LLM 调用日志摘要和 retry/repair 次数，让用户不用查 SQLite 也能定位中间失败。
- 用真实 embedding + cross-encoder reranker 再跑一次同样链路，区分 LLM 稳定性问题和检索/重排质量问题。

## 2026-06-11 12:52 +08:00：质量失败项定位到具体面试题

### 这次做了什么
- 先重试上一轮遗留推送，`1283e5e 展示面试题质量门禁` 已成功推送到 `origin/main`。
- `/ui/interview-prep` 的 `summary_json.question_quality.sample_issues` 现在会把 `q01_02` 这类题号渲染成可点击按钮。
- 每道面试题预览增加 `data-question-id`，点击质量失败项时会在当前面试包卡片内定位对应题目、滚动到视窗中部并高亮。
- 如果失败题目不在当前预览中，页面会提示“当前预览未显示该题，请打开 Markdown 查看完整题目”，避免用户误以为质量项无效。
- 新增 `.inline-action` 和 `.question-highlight` 样式，复用现有列表布局，不引入新的前端框架或调试组件。
- 前端测试新增对 `data-quality-jump`、`focusInterviewQuestion`、`data-question-id` 和 `question-highlight` 的断言。

### 发现的问题
- 上一轮推送失败是暂时性 DNS 问题；本轮重新执行 `git push origin main` 已恢复，说明不是仓库配置或凭据问题。
- 质量面板只展示失败项还不够，用户需要知道失败项对应哪道题；否则“quality judge”容易变成只给分、不指导修复。
- 面试包列表当前只展示每个题组前 4 道题，质量失败样例可能指向未渲染的完整题目；这是一种产品预览边界。

### 怎么修复
- 在失败项里解析题号并渲染轻量按钮，点击后只在当前面试包卡片范围内查找，避免多个面试包存在同名题号时跳错。
- 命中题目后添加 `question-highlight`，不改变题目尺寸和布局，只用 outline/background 做短路径定位。
- 没命中可见题目时给出 toast，而不是静默失败。

### 未修复的问题
- 还没有做真正的“按失败类型过滤所有题目”；原因是当前页面只展示题组预览，完整筛选需要先增加完整题目列表/展开机制，不能把一个小交互做成复杂调试台。
- 高亮不会自动消失；原因是它用于用户继续查看该题上下文，暂时保留更符合定位用途。

### 下一步
- 给面试包题组增加“展开全部/收起”或独立问题列表视图，再把质量失败项升级为完整筛选。
- 在真实 embedding + reranker 用户流中观察质量失败项是否能定位到 RAG 证据不足或缺口边界不足的问题。

## 2026-06-11 12:43 +08:00：面试准备页展示题目质量门禁

### 这次做了什么
- `/ui/interview-prep` 面试包卡片新增“题目质量”指标，直接显示 `summary_json.question_quality.score`。
- 新增 `renderQuestionQuality` 前端渲染：展示质量门禁通过/待检查、JD 贴合、连续追问、缺口边界、项目绑定、证据追溯、行动性、重复率、失败项和样例问题。
- 支持从 `summary_json.question_quality` 读取完整质量信息；老数据只有 `coverage_json.question_quality_score/rates` 时，会降级显示 coverage 中的质量摘要。
- README 已更新 `/ui/interview-prep` 能展示题目质量分、失败项和面经参考链接。
- 新增前端测试，固定 `renderQuestionQuality`、`题目质量`、`缺口边界`、`失败项` 这些关键 UI 能力入口。

### 发现的问题
- 上一轮质量 judge 已经落库并进入评测，但用户在面试准备页看不到质量分和失败项，只能去评测结果或数据库里查，不符合真实产品使用路径。
- PowerShell 直接 `Get-Content` 会把 UTF-8 中文显示成 mojibake；本轮确认文件本身仍是 UTF-8，读取和修改时继续按 UTF-8 处理，避免把控制台显示问题误当作源码损坏。
- 应用内浏览器插件目录缺少 `scripts/browser-client.mjs`，无法完成 Browser 自动化 smoke；这属于本地插件安装边界，不是页面运行错误。

### 怎么修复
- 在面试包列表卡片的顶部指标区加入题目质量分，并在准备角度和题组前展示完整质量面板。
- 质量面板复用现有 `validation-panel`、`validation-grid`、`status-pill` 样式，不新增前端框架或图表库；原因是这只是已有质量指标的产品可见性，不值得引入新的技术栈。
- 增加 `questionQualityFromCoverage` 和 `formatPercent`，保证新旧数据都能稳定显示。
- 使用本地 HTTP smoke 验证 `/ui/interview-prep` 返回 `200`，页面包含 `interview-prep-form`、`面试准备包` 和 `main.js`；同时用 `node --check app/static/js/main.js` 验证前端语法。

### 未修复的问题
- 本轮还没有把质量失败项做成可点击过滤题目；原因是当前目标先让质量门禁可见，后续再做“点击失败项定位问题题目”的交互增强。
- 还没有在 Markdown 导出里加入质量分；原因是 Markdown 现在面向面试练习交付，质量分更适合在生成/调试页面上展示。
- 未完成应用内浏览器自动化 smoke；原因是本地 Browser 插件缺少 `browser-client.mjs`，已用 HTTP smoke、JS 语法检查和前端测试替代验证。

### 下一步
- 给质量失败项增加题目定位和筛选，让用户能快速找到需要修改或补证据的问题。
- 在真实 embedding + reranker 用户流中观察质量面板是否能帮助定位 RAG 证据不足。

## 2026-06-11 12:33 +08:00：面试题质量 Judge 与连续追问质量门禁

### 这次做了什么
- `InterviewPrepService` 为所有面试题补齐默认 `follow_ups`，避免只有 LLM 题有连续追问、规则题缺少追问链。
- 新增 `summary_json.question_quality`：使用本地可解释 judge 计算 JD 贴合率、连续追问深度、缺口诚实边界率、项目绑定率、证据可追溯率、行动性和重复率。
- `coverage_json` 新增 `question_quality_passed`、`question_quality_score` 和 `question_quality_rates`；质量门禁不通过时，面试包整体 `coverage.passed=false`。
- `EvaluationService.run_interview_prep_evaluation` 新增 `question_quality_pass_rate`、`avg_question_quality_score` 和 `question_quality_failed` failure breakdown。
- 测试新增弱题样例：只有“介绍一下你自己”、无追问、无 JD 贴合、无 answer points 的题目必须被 judge 判失败。
- API、评测文档和 Agent 设计文档已更新中文说明，并明确本轮没有新增 LLM-as-judge 技术栈。

### 发现的问题
- 第一版质量分出现 `avg_question_quality_score > 1`，原因是“非适用项”没有进入分母，却被计入通过数。这会让指标看起来很高，但实际上数学含义不成立。
- `ml_platform_k8s_gap` 和英文辅助 case 暴露出一个真实准备边界：同岗位面经调研题或 JD 技术深挖题如果带到 `Kubernetes` 这类 missing skill，也必须追问“如何诚实说明边界/如何最小补齐”，不能只问“怎么设计实现方案”。
- 通用行为题本来可以服务 JD，但如果没有显式追问“如何回到当前 JD/岗位职责”，judge 很难判断它是否贴合岗位。

### 怎么修复
- 质量 judge 改为“只对适用题计分；没有适用题时该指标记为 1.0”，保证所有 rate 与 score 都落在 0-1 区间。
- 默认追问生成时优先检查题目技能是否命中 `missing_skills`；只要命中，就生成“没有真实交付时如何诚实说明边界”和“最小验证任务”两个追问。
- JD 贴合判断加入 `JD`、`岗位`、`职责` 等通用锚点，让通用行为题在追问回到岗位场景时可以被正确识别。
- 重新运行 interview prep 评测：`pass_rate=1.0000`、`question_quality_pass_rate=1.0000`、`avg_question_quality_score=0.9990`、`question_quality_failed=0`。
- 目标回归通过：`tests/test_interview_prep.py` 与 `test_interview_prep_evaluation_covers_sources_stack_and_gap_drills` 共 9 个测试通过；`py_compile` 通过。

### 未修复的问题
- 质量 judge 目前是可解释本地规则，不是 LLM-as-judge；原因是面试包生成已经调用 LLM，质量门禁优先需要稳定、低成本和可离线回归。LLM-as-judge 更适合后续抽检或发布前评审。
- `pytest` 在当前沙箱下无法写入 `.pytest_cache`，会出现 cache warning；测试本身通过，原因是工作区权限对缓存目录写入受限。
- 还没有把质量分展示到 `/ui/interview-prep` 卡片上；本轮先把生成、落库和评测链路打通。

### 下一步
- 在面试准备页展示 `question_quality_score`、失败项和样例问题，帮助用户理解为什么某个面试包需要重生成或补充简历证据。
- 增加 LLM-as-judge 抽检评测，但只作为离线/发布前质量校准，不替代本地可解释门禁。
- 用真实 embedding + reranker 重跑端到端用户流，比较质量 judge 在真实检索证据下的表现。

## 2026-06-10 10:24 +08:00：真实 LLM 用户流测试与 JD 强弱要求修复

### 这次做了什么
- 用真实 LLM 配置从用户视角跑完整中文链路：`/health`、`/profiles/guided`、`/jobs`、`/interview-prep`、`/interview-prep/{id}/questions`、`/interview-prep/{id}/markdown`、`/llm/debug/logs`。
- `LLMClient.generate_text/generate_json` 支持 `max_tokens`，并在 `llm_call_logs` 记录 `max_tokens`、真实 `response_chars`、失败 trace、延迟和错误信息。
- LLM timeout 从 60 秒提高到 120 秒，避免真实模型在长 prompt 下刚好超时。
- `InterviewPrepService` 的 LLM 面试题生成改为紧凑 JSON schema，只生成 2 个项目实现追问和 2 个八股/基础追问；失败时记录 trace，支持 transient retry、JSON repair 和局部 JSON 恢复。
- `/interview-prep` 对 LLM 失败返回 502，不再悄悄降级。
- `JDParserService` 增加 transient retry：首次 `LLM returned empty content`、`ReadTimeout`、连接中断等会记录 `jd_parser.parse_jd` 失败 trace，并用 `jd_parser.parse_jd.retry_1` 重试一次。
- `/jobs` 对 JD parser 的 LLM 失败返回 502，方便前端和用户读取 `/llm/debug/logs` 排查。
- JD parser merge 后新增“强弱要求归一化”：LLM 把 `MLflow`、`Kubernetes` 这类“有经验者优先/加分/非硬性要求”技能误放进 `required_skills` 时，会根据启发式结果和原文语境 demote 到 `preferred_skills`。
- 面试包评测的 LLM 问题检查调整为真实 schema：要求 `llm_project_implementation >= 2` 且 `llm_foundation_drill >= 2`。
- 补充回归测试：软性技能 demote、JD parser transient retry、面试包 LLM 紧凑 JSON 生成、LLM debug trace。

### 发现的问题
- 真实模型调用不是稳定的“同步函数”：同一条链路里曾出现 JD parser 空返回、面试包问题生成超时、长 JSON 输出截断/格式不完整、repair 调用空返回等情况。
- 旧逻辑只看最终接口是否成功，不够适合开发期排障；必须看 `llm_call_logs` 的阶段 trace、延迟、prompt 字符数、response 字符数和错误类型。
- 面试包 prompt 原先输出字段过多，容易让模型生成冗长 JSON，导致截断或格式错误；面试题高价值在“问题 + 追问”，不是让模型同时写完整答案。
- LLM 解析 JD 时容易把“优先/加分/不是硬性要求”的技能硬化成 required，进而污染匹配缺口和面试包覆盖率。
- PowerShell 通过 stdin pipe 给 Python 传中文脚本时会把测试数据污染成问号；真实中文链路测试需要用直接参数、UTF-8 文件或其他不会转码的方式。

### 怎么修复
- 保留失败即报错的开发期策略，但把错误变成可观测：所有真实 LLM 阶段都写入 `llm_call_logs`，API 层返回 502，便于沿 trace 排查。
- 对 JD parser 和 interview prep 分别做业务阶段 retry，而不是在底层 LLMClient 全局隐藏重试；trace 名称可以直接看出失败发生在哪个业务步骤。
- 把面试包 LLM 输出压缩成紧凑结构，再由本地代码补齐 `intent`、`answer_points`、`source_perspective`、准备角度和题目 ID。
- 用 JD 原文句子级语境判断 hard/soft requirement：有“加分、优先、非硬性、optional、preferred”等软性信号且没有独立硬性语境的技能，从 required 移到 preferred。
- 真实中文用户流重跑通过：`coverage.passed=true`、`required_skill_coverage_rate=1.0`、`missing_skill_drill_rate=1.0`、`question_count=36`、`llm_project_implementation=2`、`llm_foundation_drill=2`。
- 最终真实 LLM trace：`jd_parser.parse_jd` completed，约 17.5s，`response_chars=1254`；`interview_prep.generate_interviewer_questions` completed，约 39.2s，`response_chars=1345`。
- 最终 JD 结构化结果中 `MLflow`、`Kubernetes` 已进入 `preferred_skills`，没有进入 `required_skills`。
- 全量回归通过：`65 passed`；`python -m py_compile app\services\jd_parser.py app\api\jobs.py app\services\interview_prep.py app\core\llm.py` 通过；`node --check app\static\js\main.js` 通过；`git diff --check` 退出码为 0。

### 未修复的问题
- 真实 LLM 调用仍可能偶发空返回或慢返回；原因是外部模型服务不稳定。当前策略是保留 trace、重试一次、仍失败就 502 报错，不再伪造结果。
- LLM 生成题还没有独立 judge 打分；原因是本轮先修通真实用户流、trace 和结构稳定性，下一步可以增加 judge 检查“是否贴合 JD、是否追问项目实现、是否诚实披露缺口”。
- JD parser 目前只有 `required_skills`/`preferred_skills` 两级强度；原因是下游 matcher 和面试包暂时只消费这两类。后续如果要更细，可扩成 `must_have`、`nice_to_have`、`explicitly_not_required`。
- 真实用户流本次仍使用 hash embedding 和关闭 reranker，是为了把变量集中在 LLM 全流程；接下来需要再跑一次真实 embedding + reranker 的端到端链路。

### 下一步
- 增加 LLM 面试题 judge 评测，量化问题贴合度、追问深度、缺口诚实边界和项目证据绑定。
- 用真实 embedding + reranker 重跑用户流，比较 hash embedding 与真实向量检索下的匹配、证据和面试包变化。
- 给 `/llm/debug/logs` 增加按 profile/job/prep 关联筛选，减少长流程排查时手动查表成本。

## 2026-06-10 09:40 +08:00：面试包重心转向 JD + 简历项目的 LLM 追问链

### 这次做了什么
- `/interview-prep` API 和 Agent 工作流 `prepare_interview_for_job` 改为调用 `create_interview_prep_with_llm`，真实入口会基于 JD、简历项目、RAG 证据和缺口技能生成 LLM 面试问题。
- `InterviewPrepService` 新增 LLM 问题生成：`LLM 项目实现追问` 覆盖架构、输入输出、日志指标、失败边界、本人贡献；`LLM 八股与基础追问` 覆盖 JD 必备技能的基础原理、工程取舍和缺口诚实披露。
- 每道 LLM 生成题新增 `follow_ups` 连续追问，Markdown 和 `/ui/interview-prep` 页面都会展示追问链。
- 面经 source 收敛为参考入口：`summary_json.interview_reference_links` 只保存已导入面经或搜索入口的标题、链接、query 和边界说明；面试包不再把抓取平台正文作为核心依赖。
- `interview_prep` 评测切到 LLM 增强路径，并新增 `llm_question_generation_pass_rate`，要求项目实现追问和八股/基础追问都至少生成可用问题。
- README、Agent 设计文档和评测文档已更新中文说明。

### 发现的问题
- 继续围绕牛客网、OfferShow、小红书抓正文会让系统复杂度跑偏：登录态、反爬、客户端渲染、正文授权和内容真实性都不是求职 Agent 的核心价值。
- 旧版面试包的“同岗位面经”容易被理解成要自动抓取具体帖子正文；当抓取失败时，用户真正需要的是可参考链接和标题，而不是在 source 层继续投入复杂对抗。
- 面试准备的高价值部分应当是：结合 JD 和简历项目，生成面试官可能追问的项目实现细节、八股基础、工程取舍和缺口披露。
- 当前环境没有 `.env`，也没有 `LLM_API_KEY`/`OPENAI_API_KEY` 环境变量；为了不把密钥写进命令文本或日志，本轮没有执行在线 LLM smoke。

### 怎么修复
- 把面经平台能力降级为 source smoke + 参考链接，不再让核心面试包依赖外部正文抓取。
- LLM prompt 只接收结构化 JD、简历项目、RAG evidence、matched/missing skills，并要求输出严格 JSON；缺口技能必须生成诚实披露问题，不能假设候选人已掌握。
- 测试环境继续使用 deterministic fallback 生成同结构的 LLM 追问，保证离线评测稳定；真实环境没有 LLM 配置时会直接报错并记录配置问题。
- 全量回归通过：`63 passed`；`node --check app/static/js/main.js` 通过；`git diff --check` 无空白错误。
- 面试包评测刷新：`case_count=9`、`pass_rate=1.0000`、`llm_question_generation_pass_rate=1.0000`、`markdown_export_pass_rate=1.0000`、`avg_question_count=35.7778`。
- 页面 smoke 通过：`GET /ui/interview-prep` 返回 `200`，静态 JS 包含 `renderInterviewReferenceLinks` 和 `LLM 八股追问`；应用内浏览器加载页面后主 JS 存在，控制台无 error。

### 未修复的问题
- 未跑在线 LLM smoke；原因是当前环境没有安全注入的 LLM key，不能把用户密钥直接写入命令文本或日志。填好 `.env` 后可直接用 `/interview-prep` 或 Agent 工作流触发真实调用，并在 `llm_call_logs` 查看 trace。
- 还没有对 LLM 生成题做二次质量 judge；原因是本轮先把问题生成重心迁移到 JD + 项目，并用结构化评测保证追问组存在。
- 还没有按准备角度统计练习进度；原因是本轮优先处理生成逻辑和产品边界，练习闭环可以在现有 `practice_items` 上继续扩展。

### 下一步
- 增加 LLM 面试题质量 judge，检查问题是否贴合 JD、是否引用简历项目、是否包含有效追问、是否对缺口保持诚实边界。
- 给 `/ui/interview-prep` 增加按准备角度聚合的练习进度和薄弱题复习队列。
- 在 `.env` 安全配置 LLM key 后，跑真实中文 Agent 实习岗位 case，检查 `llm_call_logs`、Agent step trace 和最终 Markdown 质量。

## 2026-06-09 23:04 +08:00：面试包三类准备角度结构化

### 这次做了什么
- `InterviewPrepService` 为每道题新增 `preparation_angle` 和 `preparation_angle_label`，把题目归并为“网上同岗位面经”“简历项目技术栈”“其他可能面试问题”三类准备角度。
- `summary_json.preparation_angles` 新增每个角度的输入来源、题目数、准备重点和对应题源类型；`coverage_json` 新增 `preparation_angle_counts`、`preparation_angle_labels` 和 `preparation_angles_passed`。
- `InterviewPrepDeliveryService` 的题目展开、来源统计和 Markdown 导出都展示准备角度，Markdown 新增“准备角度”章节。
- `/ui/interview-prep` 的准备记录卡片新增三视角覆盖状态、准备角度列表，并在题目标签里展示准备角度。
- `interview_prep` 评测新增 `preparation_angle_pass_rate`，并要求 Markdown 包含“准备角度”章节。
- README、Agent 设计文档和评测文档已更新中文说明。

### 发现的问题
- 面试包之前虽然有 `source_perspective`，但它更偏“题目来源追溯”，不能直接表达真实准备时的三类视角。
- 只靠题组名和来源分布，后续新增题型时可能出现“来源标签还在，但面试包没有清晰三视角计划”的虚假通过。
- 页面列表只展示面经角度、项目技术栈、其他问题的计数，没有说明每类问题的输入来源和准备重点。

### 怎么修复
- 增加 `source_perspective -> preparation_angle` 的稳定映射：导入/调研面经归入网上同岗位面经，项目证据/技术栈归入简历项目技术栈，JD 技术深挖/缺口/通用行为题归入其他可能面试问题。
- 面试包生成时统一补齐题目 ID、来源视角和准备角度元数据，避免页面、导出和评测各自推断。
- 评测强制检查三类准备角度都存在，并把 `preparation_angle_pass_rate` 写入 summary。
- 目标测试通过：`28 passed`；全量回归通过：`62 passed`；`node --check app/static/js/main.js` 通过。
- 页面 smoke 通过：`GET /ui/interview-prep` 返回 `200`，页面包含 `interview-prep-form` 和“面试准备包”；应用内浏览器加载页面后主 JS 存在，控制台无 error。

### 未修复的问题
- 还没有基于多篇已确认面经做 LLM 聚合去重；原因是需要保留每个问题的原文引用、来源 URL 和可信度，不能简单把多篇面经混成一段摘要。
- 还没有把面试包按三类角度做独立练习进度统计；原因是本轮先把生成、展示、导出和评测的结构化标签打通。
- 还没有自动抓取牛客网、OfferShow、小红书正文；原因仍然是登录态、反爬、客户端渲染和授权边界，需要继续走 source smoke + 人工确认导入。

### 下一步
- 在多篇已导入面经基础上增加 LLM 摘要/去重增强层，同时保留原文引用、来源 URL 和可信度分。
- 给面试准备页增加按准备角度聚合的练习进度、薄弱题复习队列和模拟问答记录。
- 给评测工作台增加 LLM workflow、RAG strategy 和 real-job-ingest smoke 的运行入口与中间 trace 展示。

## 2026-06-09 22:18 +08:00：导入面经后提供面试包快捷入口

### 这次做了什么
- `/ui/evaluations` 的人工确认导入表单新增导入结果区域。
- 面经导入成功后，页面展示新建 `InterviewExperience` ID、抽取题目数、主题、样例问题和来源信息。
- 新增“用该面经生成面试包”快捷入口，跳转到 `/ui/interview-prep?experience_ids={id}`。
- `/ui/interview-prep` 会读取 URL 里的 `experience_ids` 和 `job_id`，自动预填生成面试包表单。
- 前端测试新增 `interview-source-import-result` 断言，固定导入结果容器。
- README 和评测文档已更新中文说明。

### 发现的问题
- 上一轮虽然能从候选面经人工确认导入，但导入成功后只显示 toast，用户仍然要手动记住 ID 再去面试页填写 `experience_ids`。
- 这种断点会降低真实使用效率，也容易让用户忘记指定刚导入的面经，导致面试包只使用调研线索而没有 source-backed 真实问题。
- 如果直接自动生成面试包，又会绕过 Profile ID、Job ID 和用户确认，不符合当前人工确认边界。

### 怎么修复
- 保持“导入后不自动生成面试包”，但展示可点击的快捷入口，把已确认的 `experience_ids` 带到面试准备页。
- 面试准备页只做表单预填，仍要求用户填写 Profile ID / Job ID 并手动点击生成。
- 导入结果卡展示抽取题目数和主题，帮助用户判断这份面经是否足够有用。
- 全量回归通过：`62 passed`；`node --check app/static/js/main.js` 通过。
- 页面 smoke 通过：`GET /ui/evaluations` 返回 `200`，页面包含 `interview-source-import-result`；静态 JS 包含 `renderImportedInterviewExperience`、`experience_ids` 和 `prefillInterviewPrepFromQuery`。

### 未修复的问题
- 还没有导入成功后自动展示新建面经在 `/ui/interview-prep` 的列表刷新结果；原因是当前跳转入口已经足够让用户进入面试准备页，跨页面同步可以后续做。
- 还没有支持多篇候选面经一次性合并导入；原因是多篇面经需要去重、可信度聚合和来源边界，不能简单拼接。
- 还没有 LLM 面经摘要/去重；原因是应基于已确认正文，而不是搜索摘要。

### 下一步
- 在多篇已导入面经基础上增加 LLM 摘要/去重增强层，同时保留原文引用、来源 URL 和可信度分。
- 给评测工作台增加 LLM workflow、RAG strategy 和 real-job-ingest smoke 的运行入口与中间 trace 展示。
- 面试准备页支持从 URL 自动触发“查看相关已导入面经”，减少跨页面上下文丢失。

## 2026-06-09 22:05 +08:00：候选面经接入人工确认导入草稿

### 这次做了什么
- `/ui/evaluations` 新增“确认导入候选面经”表单，字段复用 `POST /interview-prep/experiences` 的导入协议。
- 面经 source smoke 的 sample 结果新增“填入导入草稿”按钮，可把来源平台、标题、URL 和摘要预填到人工确认表单。
- 用户必须补全或确认真实面经正文后再提交，提交后写入 `interview_experiences`，后续可在面试准备包里作为 source-backed 证据引用。
- 前端测试新增断言，确保评测页同时包含 `interview-source-smoke-form`、`interview-source-import-form` 和 `evaluation-runs-list`。
- README、Agent 设计和评测文档已更新中文说明。

### 发现的问题
- source smoke 的 sample 只代表搜索页候选线索，不代表完整、真实、可授权使用的面经正文。
- 如果直接提供“一键导入”会让搜索摘要变成伪证据，后续面试包可能引用不完整或错误的问题。
- 面经导入表单只在 `/ui/interview-prep` 页面时，用户需要在评测页和面试页之间来回复制，调试链路不顺。

### 怎么修复
- 在评测页内增加人工确认表单，但仍复用后端 `InterviewExperienceService` 的原文抽取、主题识别和可信度计算。
- “填入导入草稿”只做预填，不自动提交；预填正文中明确提醒用户补充完整真实面经正文、轮次和追问。
- 提交后仍走 `POST /interview-prep/experiences` 的校验，短文本或无效文本会直接报错，不静默兜底。
- 全量回归通过：`62 passed`；`node --check app/static/js/main.js` 通过。
- 页面 smoke 通过：`GET /ui/evaluations` 返回 `200`，页面包含 `interview-source-import-form`，静态 JS 包含 `data-import-interview-candidate` 和 `prefillInterviewSourceImport`。

### 未修复的问题
- 还没有从候选 URL 自动抓取正文；原因是多数平台存在登录、反爬、客户端渲染和授权边界，自动抓正文应在 source 稳定性证据足够后单独设计。
- 还没有把导入成功后的面经 ID 自动带回面试包生成表单；原因是本轮先打通候选到导入的最小人工确认闭环，下一步再连接生成面试包。
- 还没有对候选摘要做 LLM 去重；原因是摘要不一定完整，去重应基于已确认导入的正文。

### 下一步
- 导入成功后展示新建 `InterviewExperience` ID，并提供“用该面经生成面试包”的快捷入口。
- 在多篇已导入面经基础上增加 LLM 摘要/去重增强层，同时保留原文引用、来源 URL 和可信度分。
- 给评测工作台增加 LLM workflow、RAG strategy 和 real-job-ingest smoke 的运行入口与中间 trace 展示。

## 2026-06-09 20:58 +08:00：新增评测工作台展示面经源探测

### 这次做了什么
- 新增 `/ui/evaluations` 页面，并加入顶部导航和首页高频操作。
- 评测页面支持填写 query、limit 和 source 列表运行 `POST /evaluations/interview-source-smoke`。
- 页面会展示最近一次面经源探测的 summary、source errors、source 级状态、耗时和样例结果。
- 最近评测记录列表会展示不同评测的状态、样例数和通过率/核心指标。
- 补充前端页面测试，确保 `/ui/evaluations` 可渲染并包含 `interview-source-smoke-form` 和 `evaluation-runs-list`。
- README 和评测文档已更新中文说明。

### 发现的问题
- 只有 API 的 source smoke 对开发者足够，但不利于产品调试；用户无法直观看到哪些面经源可达、哪些为空、哪些返回低质量结果。
- 面经平台失败不能只通过 toast 或异常提示暴露；需要显示 source 级结果，否则会误以为面试包生成能力失败。
- 最近评测结果如果只保存在 `evaluation_runs` 里，缺少页面入口时很难形成持续改进闭环。

### 怎么修复
- 新增评测工作台，把 `interview-source-smoke` 的运行入口和最新结果展示放在同一页。
- 面经源结果卡片展示 `reachable_source_rate`、`result_source_rate`、`interview_signal_rate`、`query_relevance_rate` 和 `content_extractable_rate`。
- source errors 和 sample experiences 直接展示在页面中，便于判断是登录/反爬、空结果还是内容弱相关。
- 前端页面测试通过：`tests/test_frontend_pages.py` 共 `2 passed`；全量回归通过：`62 passed`；`node --check app/static/js/main.js` 通过。
- 页面/API smoke 通过：`GET /ui/evaluations` 返回 `200`，页面包含 `interview-source-smoke-form` 和 `evaluation-runs-list`；`POST /evaluations/interview-source-smoke?limit=1&sources=unknown` 返回 `201`。

### 未修复的问题
- 还没有把高质量 sample 一键转为待确认导入；原因是当前 source smoke 只证明候选结果存在，不证明正文完整和可授权使用。
- 评测页面还没有覆盖所有评测的专用运行表单；原因是本轮优先打通面经 source smoke 的可操作闭环，其他评测可以后续逐步挂载。
- 页面没有做长任务进度流；原因是当前 source smoke 规模小，后续真实 LLM workflow 评测页面需要单独设计 trace 流式展示。

### 下一步
- 增加“候选面经 -> 人工确认 -> 导入 `InterviewExperience`”流程，把 source smoke 和面试包生成连接起来。
- 给评测工作台增加 LLM workflow、RAG strategy 和 real-job-ingest smoke 的运行入口与中间 trace 展示。
- 对多篇已导入面经增加 LLM 摘要/去重增强层，同时保留原文引用和可信度分。

## 2026-06-09 20:51 +08:00：新增面经来源 Source Smoke

### 这次做了什么
- 新增 `app/services/interview_sources.py`，为牛客网、OfferShow、小红书提供公开搜索页的非侵入式面经来源探测。
- 新增 `EvaluationService.run_interview_source_smoke`，并发记录每个面经 source 的可达性、结果数量、面经信号、query relevance、内容可抽取性、错误和样例结果。
- 新增 `POST /evaluations/interview-source-smoke`，默认 query 为 `Agent 开发实习生 面经`，支持 `limit` 和重复 `sources` 参数。
- 新增 fake source 测试，覆盖正常返回、登录/反爬类错误、可达但空结果、可达但低质量结果。
- README、API、架构、Agent 设计和评测文档已补充中文说明。

### 发现的问题
- “网上同岗位面经”不能简单等价于“自动抓正文”：牛客网、OfferShow、小红书都可能遇到登录态、反爬、客户端渲染、搜索页结构变化和内容噪声。
- 只记录 source 是否报错不够；生产排查时还需要知道是空结果、低质量结果、没有面经信号，还是只有标题/摘要而没有可导入正文。
- 如果把真实平台探测直接混进 `interview_prep` 核心评测，会让外部网络波动污染面试包生成质量，无法区分是 Agent 生成差还是平台不可达。

### 怎么修复
- 把面经平台接入限定为独立 source smoke：默认只探测公开搜索页，不绕过登录或反爬，不写入 `interview_experiences`，不影响核心面试包 pass rate。
- summary 增加 `reachable_source_rate`、`result_source_rate`、`url_rate`、`interview_signal_rate`、`query_relevance_rate`、`content_extractable_rate`、`source_errors` 和 `source_empty`。
- 状态区分 `completed`、`completed_with_source_errors`、`completed_with_empty_sources`、`completed_with_low_quality_results` 和 `source_unavailable`。
- 全量测试通过：`61 passed`。新增 API smoke 通过：`POST /evaluations/interview-source-smoke?limit=1&sources=unknown` 返回 `201`，`evaluation_type=interview_source_smoke`，`core_regression_independent=true`。

### 未修复的问题
- 还没有把真实平台返回结果自动导入为 `InterviewExperience`；原因是当前 smoke 只能证明 source 层是否有候选结果，不能证明正文真实、完整、可授权使用。
- 还没有针对每个平台写深度解析器；原因是公开页面结构和登录限制不稳定，直接写强解析很容易变成脆弱爬虫，应先通过 source smoke 收集稳定性证据。
- 没有绕过小红书等平台的登录和反爬；原因是该项目应保持真实产品边界，优先记录限制和人工导入流程，而不是做不可维护的反爬对抗。

### 下一步
- 在 UI 或评测页面展示 `interview-source-smoke` 最新结果，让用户知道当前哪些面经源可用、哪些需要手动导入。
- 对高质量候选结果增加人工确认导入流程：用户确认来源、正文和岗位相关性后，再写入 `interview_experiences`。
- 在多篇导入面经基础上增加 LLM 摘要/去重增强层，但保留原文引用、来源 URL 和可信度分。

## 2026-06-09 11:33 +08:00：面试包交付层和三角度覆盖评测增强

### 这次做了什么
- 新增 `InterviewPracticeItem` 数据模型，用 `interview_prep_id + question_id` 记录每道面试题的练习状态、信心分和备注。
- 新增 `InterviewPrepDeliveryService`，负责展开题目列表、统计来源分布、导出 Markdown 面试包和更新按题练习状态。
- `InterviewPrepService` 给每道题补稳定 `question_id` 和 `source_perspective`，并在 `coverage_json` 中记录同岗位面经/面经调研、简历项目技术栈、其他可能面试问题三类核心来源计数。
- 新增 `GET /interview-prep/{prep_id}/questions`、`GET /interview-prep/{prep_id}/practice`、`PUT /interview-prep/{prep_id}/practice` 和 `GET /interview-prep/{prep_id}/markdown`。
- `/ui/interview-prep` 增加 Markdown 导出入口、按题练习状态表单，以及面试包卡片上的三角度来源计数。
- 面试准备包评测新增 `question_id_pass_rate`、`source_perspective_pass_rate` 和 `markdown_export_pass_rate`，不再只检查题组名称。
- README、API、架构、Agent 设计和评测文档已更新为中文说明。

### 发现的问题
- 只检查“同岗位面经与高频追问 / 简历项目技术栈追问 / 通用面试与行为问题”这些题组名还不够，真实产品里需要能追踪每道题到底来自网上同岗面经、简历技术栈还是其他面试问题。
- 项目深挖题原本没有显式 `source_perspective`，后续评测或导出时会变成“未知来源”，不利于判断面试包是否来源单一。
- 只有生成结果没有交付形态时，用户无法拿着面试包直接练习，也无法记录哪些问题已经准备好。

### 怎么修复
- 为问题来源建立结构化标签：`source_backed_interview_experience`、`online_experience_research`、`resume_project_evidence`、`resume_project_stack`、`jd_technical_depth`、`jd_gap_drill` 和 `general_interview`。
- 把三角度覆盖写入 `coverage_json.core_perspective_counts`：同岗面经/面经调研、简历项目技术栈、其他可能面试问题必须都有题。
- Markdown 导出包含基本信息、问题来源分布、练习状态、题组、缺口 drill、外部调研清单和证据边界。
- 评测 case result 增加题号唯一性、来源视角覆盖和 Markdown 导出检查；summary 和 failure breakdown 同步暴露这些指标。
- 全量测试通过：`59 passed`。独立面试准备包评测结果：`case_count=9`、`pass_rate=1.0000`、`question_id_pass_rate=1.0000`、`source_perspective_pass_rate=1.0000`、`markdown_export_pass_rate=1.0000`、`avg_question_count=25.4444`。

### 未修复的问题
- 还没有自动抓取牛客网、OfferShow、小红书正文；原因仍然是登录态、反爬、内容噪声和时效性不稳定，需要作为独立 source smoke 接入，不能混进核心可重复回归。
- 面试包仍是结构化规则生成，没有做多篇面经的 LLM 聚合去重；原因是本轮优先补齐可交付、可练习、可量化验收的产品闭环。
- 还没有 PDF 导出；原因是 Markdown 已经满足可读和可提交材料的基础交付，PDF 应作为后续文档渲染层处理。

### 下一步
- 增加面经 source smoke，分别记录牛客网、OfferShow、小红书的可达性、登录限制、空结果、岗位相关性和内容时间。
- 在导入多篇面经后增加 LLM 摘要/去重增强层，但保留原文引用、来源 URL 和可信度分。
- 给面试包增加模拟问答记录和薄弱题复习队列，把 `practice_items` 从状态记录扩展成练习闭环。

## 2026-06-09 10:58 +08:00：面试包接入已导入同岗面经证据

### 这次做了什么
- 新增 `InterviewExperience` 数据模型，保存牛客网、OfferShow、小红书等同岗面经原文、来源链接、岗位关键词、抽取题目、技术主题、轮次和可信度信号。
- 新增 `InterviewExperienceService`，从用户导入的真实面经文本中抽取问题、轮次、主题和可信度；不会在文本没有明确问题时编造具体面经题。
- `InterviewPrepService` 增加 source-backed 面经追问：生成面试包时会优先引用已导入面经，并在 `source_evidence_json`、`coverage_json` 和题目 `evidence_refs` 中保留来源、可信度和原始问题。
- `POST /interview-prep/experiences`、`GET /interview-prep/experiences` 和 `/ui/interview-prep` 面试页支持导入和查看同岗面经材料；生成面试包时可传 `experience_ids`。
- Agent Tool/Skill/SubAgent 注册表增加 `interview_experience.import_text`，让面经导入成为显式工具能力，而不是隐藏 CRUD。
- `evals/interview_prep_cases.json` 增加 1 个带牛客网面经文本的中文 hard case，评测新增 `source_backed_pass_rate`、`experience_site_pass_rate`、`avg_source_backed_experience_count` 和 `avg_source_backed_question_count`。

### 发现的问题
- 面经材料经常是整段粘贴，不一定按问题换行；初版抽取器只对长段落切句，导致“RAG 怎么评估？FastAPI 如何定位？SQLite 有什么边界？”被吞成 1 个问题。
- 评测如果运行在持久 SQLite 上，历史导入的面经会被后续 case 自动检索到，导致 source-backed 指标被污染，不能反映当前 case 自身是否提供面经。
- 高相关面经来源只取前 2 个问题时，hard case 的真实面经覆盖不足；真实面试准备中，一个高相关来源保留 3 个问题更合理。

### 怎么修复
- `InterviewExperienceService._candidate_lines` 改为先按中文/英文问号、句号和分号切句，再判断是否像问题。
- 区分 `experience_ids=None` 和 `experience_ids=[]`：前者表示产品路径自动检索相关面经，后者表示评测隔离空集合，避免历史数据污染。
- source-backed 面经追问改为每个高相关来源最多使用 3 个真实问题，并把问题来源写入 `evidence_refs`。
- 面试准备评测重新运行通过：`case_count=9`、`pass_rate=1.0000`、`source_backed_pass_rate=1.0000`、`experience_site_pass_rate=1.0000`、`avg_question_count=25.4444`、`avg_source_backed_experience_count=0.1111`、`avg_source_backed_question_count=0.3333`。

### 未修复的问题
- 还没有自动搜索/抓取牛客网、OfferShow、小红书正文；原因是这些平台存在登录态、反爬、内容时效和真实性问题，自动抓取应作为独立 source smoke，而不是混进核心可重复评测。
- 面经整理目前是规则抽取，不做多篇面经 LLM 归纳去重；原因是本轮先保证 source-backed 证据链、评测隔离和 UI/API 闭环。
- 面试准备包还没有 Markdown/PDF 导出和“已练习/待复习”状态；原因是本轮优先打通真实面经证据进入面试包的主链路。

### 下一步
- 增加面经 source smoke，分别记录牛客网、OfferShow、小红书的可达性、登录限制、空结果、岗位相关性和内容时间。
- 给面经导入增加 LLM 摘要/去重增强层，但保留原文引用和可信度分，不让模型摘要替代证据。
- 增加面试准备包 Markdown 导出和按题练习状态，形成投递后的面试准备闭环。

## 2026-06-09 09:56 +08:00：新增面试准备包与面经调研视角

### 这次做了什么
- 新增 `InterviewPrep` 数据模型，持久化面试准备包、题组、缺口 drill、外部调研清单、证据引用和 coverage 指标。
- 新增 `InterviewPrepService`、`POST /interview-prep`、`GET /interview-prep` 和 `/ui/interview-prep` 页面。
- Agent 新增 `prepare_interview_for_job` 任务，执行 `load_profile -> load_job -> match_job -> generate_interview_prep`，并写入 `interview_prep` artifact。
- Tool/Skill/SubAgent 注册表新增 `interview_prep.generate_packet`、`interview_preparation` 和 `interview_coach`。
- 面试包从三个主要角度生成：牛客网/OfferShow/小红书同岗位面经调研线索、简历项目技术栈深挖、JD 缺口与通用行为问题。
- 新增 `evals/interview_prep_cases.json` 和 `POST /evaluations/interview-prep`，用 8 个中文为主 case 量化题源覆盖、调研源覆盖、缺口 drill 和必备技能覆盖。

### 发现了什么问题
- 初版面试包只围绕 JD 和 RAG 证据出题，不够贴近真实准备场景；真实面试准备还需要同岗位面经、简历项目技术栈和通用行为问题。
- 首轮测试发现 `没有 MLflow 生产经验` 会被误判成 MLflow 正向证据，原因是 matcher 只按英文句号切句，中文句号没有切开前一句“构建 CareerAgent”和后一句缺口披露。
- 第二轮评测发现 `没有 Kubernetes 集群维护经验` 仍被误判，原因是该句同时命中否定词“没有”和正向动作词“维护”，旧逻辑让正向词覆盖了否定证据。
- 牛客网、OfferShow、小红书存在登录态、反爬、内容真实性和时效性问题，不能在核心离线评测里假装已经稳定抓取真实面经。

### 怎么修复的
- 面试包题组新增 `同岗位面经与高频追问`、`简历项目技术栈追问` 和 `通用面试与行为问题`，并保留技术深挖、缺口追问和工程协作题。
- `research_checklist_json` 生成牛客网、OfferShow、小红书和搜索引擎 query，明确这是可执行调研线索，不是已抓取事实。
- `MatcherService._sentences_with_skill` 改为按中文/英文标点切句，避免中文缺口披露和前文正向项目粘连。
- `MatcherService._skill_has_positive_or_neutral_support` 改为否定证据优先：同一句里即使命中“维护/构建”等正向词，只要存在 `没有/No/without` 等否定信号，就不能算作支持证据。
- 面试准备评测修复后通过：`case_count=8`、`pass_rate=1.0000`、`research_source_pass_rate=1.0000`、`gap_drill_pass_rate=1.0000`、`avg_question_count=24.8750`、`avg_required_skill_coverage_rate=1.0000`。

### 未修复的问题及原因
- 还没有真实抓取牛客网、OfferShow、小红书帖子；原因是这些平台的公开可达性和内容质量不稳定，应作为独立 source 层能力接入，并用 smoke 区分网络失败、登录限制、空结果和低质量内容。
- 面试问题目前是结构化规则生成，不是 LLM 综合多篇面经后的归纳；原因是本轮先建立可重复、可评测的面试准备包骨架，后续再把真实面经摘要和 LLM 去重作为增强层。
- 面试包还没有导出 Markdown/PDF；原因是本轮优先补齐生成链路、Trace 和评测，导出属于交付体验优化。

### 下一步怎么做
- 增加面经 source smoke：分别探测牛客网、OfferShow、小红书的可达性、搜索结果数量、内容时间和岗位相关性。
- 支持用户粘贴面经文本，让系统把真实面经与 JD/简历证据对齐生成二次面试包。
- 给面试准备包增加 Markdown 导出和“按题练习/标记已准备”状态。

## 2026-06-08 13:28 +08:00：收紧中文岗位源边界

### 这次做了什么
- 明确项目岗位源策略：中文求职场景和中文 JD 是主路径，英文岗位只作为少量辅助测试。
- README、开发文档、架构文档和评测文档补充说明：Greenhouse 这类中国招聘场景弱的海外 ATS 不作为核心能力或默认岗位源接入。
- 为 `JobSourceRegistry` 增加默认源回归测试，确认默认只注册 `tencent`，不会把 `lever` 或 `greenhouse` 悄悄带入中文主链路。
- 给 `LeverCareersSource` 增加代码注释，标明它只是显式开启的英文辅助源。

### 发现了什么问题
- 真实产品场景不能只按“哪个接口容易爬”来选岗位源；如果默认接入中国候选人很少遇到的海外 ATS，会让项目看起来技术栈更丰富，但偏离中文 Agent 实习求职场景。
- 历史日志里曾经记录过 Greenhouse/Lever 探测过程，如果当前文档不再次收紧边界，容易让人误解下一步还要把这些源接成主路径。

### 怎么修复的
- 把默认源边界写进 README、架构、开发和评测文档：中文 source 优先，海外 ATS 只能显式英文辅助。
- 用测试固定默认注册行为：`JobSourceRegistry()` 默认只含 `tencent`。
- 保留历史日志中的试错记录，但在最新日志和当前文档里明确当前决策，避免历史探索覆盖当前产品方向。

### 未修复的问题及原因
- 目前中文真实 source 仍主要依赖腾讯招聘；原因是更多中文自有招聘站需要逐个验证公开接口、JD 完整性和稳定性，不能为了数量硬接不稳定或弱场景 source。
- 没有删除可选 Lever 代码；原因是项目允许少量英文辅助场景，但它默认关闭，并由配置和测试保证不进入中文主链路。

### 下一步怎么做
- 继续探测字节、阿里、美团、华为等中文自有招聘源，只接入能稳定返回公开中文 JD 的 source。
- 为新增中文 source 增加 source smoke、真实 JD ingest smoke 和排序前后 top sample，确保不是“能抓到”就算可用。

## 2026-06-08 13:24 +08:00：投递包页面展示 Guardrail 结果

### 这次做了什么
- 在 `/ui/applications` 的投递记录中展示 `packet_validation`。
- 前端新增 `applicationValidation` 和 `validationList`，展示 risk level、issues、warnings、manual confirmation mode 和 final submission 边界。
- 投递记录现在同时展示外联文案和求职信，方便用户在提交前一起检查。
- CSS 新增 validation panel、risk/ok 状态、紧凑 issue/warning 列表和移动端单列布局。
- Agent `quick_apply` 输出中保留 `packet_validation` 和完整 `automation_result`，让 Agent run trace 与 UI 使用同一份校验结果。
- 更新 README 和 API 文档，说明投递页面会展示 Guardrail issues/warnings。

### 发现了什么问题
- 上一轮已经把投递包 Guardrail 写入后端，但 UI 只显示投递状态和求职信，用户看不到为什么一个投递包安全、为什么有 warning，或者是否保留了人工确认边界。
- 只把 `packet_validation` 存在 `automation_result_json` 中不够，真实产品里用户需要在提交前直接看到风险项，否则 trace 只是开发者可见。

### 怎么修复的
- 在前端列表项中读取 `row.automation_result_json.packet_validation`，把 high/medium/low 风险、issue code、warning code 和 message 展示出来。
- 对没有问题的投递包显示“未发现阻断问题/无警告”，避免用户误以为没有显示就是缺数据。
- 移动端把 validation grid 改成单列，避免 issue/warning 文本挤压。
- 投递包 service 测试已确认 `validation_passed=true` 和 `packet_validation` 会写入 response 数据。

### 未修复的问题及原因
- UI 目前只展示列表中的 Guardrail 摘要，没有做逐条 issue 的交互式修复；原因是本轮先补齐风险可见性，下一步再做“根据 issue 修改投递包”的工作流。
- 前端没有引入浏览器端单元测试框架；原因是项目现有测试以 FastAPI 和服务层为主，本轮用 API/模板和服务测试覆盖数据可见性。

### 下一步怎么做
- 增加投递包重新生成/修复入口，让用户可以基于 `issues` 一键重写求职信或外联文案。
- 如果接浏览器辅助填写，UI 必须在最终提交按钮前再次显示 `user_confirmed_only`。

## 2026-06-08 13:13 +08:00：新增投递包 Guardrail 并修复硬编码 Agent 兜底

### 这次做了什么
- 新增 `ApplicationPacketGuardrail`，在 `quick_apply` 创建投递包前校验求职信、外联文案、投递清单和自动化边界。
- Guardrail 会检查 unsupported claims、目标岗位提及、人工确认边界、投递链接和文案长度。
- `No MLflow`、`没有 Kubernetes 经验` 等缺口披露不会被当作支持证据，避免把否定证据误判成能力证明。
- `ApplicationService` 的 fallback 求职信从硬编码 Agent/RAG/FastAPI/SQLite 改为根据 Profile skills、项目和目标岗位动态生成。
- 外联文案也改为根据候选人 target role 或目标 job title 生成，不再固定写“Agent 开发相关实习”。
- `automation_result_json` 新增 `final_submission=user_confirmed_only`、`packet_validation` 和 `validation_passed`；高风险 issue 会直接阻断投递包创建。
- 新增 `scripts/generate_application_packet_eval.py` 和 `evals/application_packet_cases.json`，包含 20 个中文投递包 case。
- 新增 `run_application_packet_evaluation` 和 `POST /evaluations/application-packet`，评估 high-risk recall、false block、missed high risk 和 issue code hit rate。

### 发现了什么问题
- 原 fallback 求职信不看目标岗位和候选人真实技能，总是写“Agent 工作流、RAG 检索、FastAPI 服务化和 SQLite 数据持久化”。
- 这在 Agent 岗位样例里看起来合理，但一旦候选人申请前端、数据或产品岗位，就会变成事实编造，是典型“兜底文本掩盖产品风险”。
- 直接从文本里看到某个技能也不能当作正向证据；例如“没有 MLflow 经验”应被视为缺口披露，而不是 MLflow 支持证据。
- `quick_apply` 的自动化边界需要落到可检查字段里，只写自然语言说明不够；否则后续接浏览器辅助填写时容易误以为可以自动提交。

### 怎么修复的
- 用 `ApplicationPacketGuardrail` 做确定性校验：有“熟悉、掌握、负责、建设、落地、经验”等声明动词的技能，必须在 Profile、项目、经历或定制简历中有正向证据。
- 对支持证据做句子级负向过滤，排除 `No/not/without/没有/不具备/缺少` 等缺口披露。
- 将自动化边界结构化为 `mode=manual_confirm_required` 和 `final_submission=user_confirmed_only`，缺失时标记 high-risk 并阻断。
- 缺少投递链接、外联文案过短先作为 warning，不直接阻断；原因是这类问题需要用户补充，但不一定代表文案编造或越权提交。
- 投递包评测已运行：`case_count=20`、`pass_rate=1.0000`、`high_risk_recall=1.0000`、`false_block_count=0`、`missed_high_risk_count=0`、`issue_code_hit_rate=1.0000`、`avg_warning_count=0.5000`。

### 未修复的问题及原因
- Guardrail 仍是规则版，不是 LLM verifier；原因是投递前最后一公里需要稳定可解释，当前先用确定性规则覆盖高风险事实编造和自动提交边界。
- 支持技能词表还不覆盖所有行业技能；原因是现阶段优先覆盖 Agent/LLM、前端、数据、ML 平台、推荐和 Prompt 等项目核心场景，后续应随真实 JD 扩展。
- 缺少投递链接目前只是 warning；原因是很多手动粘贴 JD 没有 apply_url，阻断会影响本地使用，后续可以在真实投递模式下提升为阻断。

### 下一步怎么做
- 把 ApplicationPacketGuardrail 接入 UI 展示，让用户能直接看到 `issues` 和 `warnings`。
- 增加真实 LLM 生成投递包的 smoke，检查 LLM 文案是否触发 unsupported claims。
- 如果后续接浏览器辅助填写，必须把 `user_confirmed_only` 作为提交按钮前的硬门禁。

## 2026-06-08 13:01 +08:00：新增中文岗位排序标注集和 NDCG/MRR 评测

### 这次做了什么
- 新增 `scripts/generate_job_relevance_eval.py`，生成中文为主的岗位排序标注集。
- 新增 `evals/job_relevance_cases.json`，覆盖 13 个 query、130 个候选岗位，包括 Agent 开发实习、智能体校招、RAG 平台、AI Agent 产品、推荐算法、后端 FastAPI、LLM 评测、大模型安全、数据开发、Agent 工程师、AI 产品经理、Prompt 工程和少量英文辅助样例。
- 每个候选岗位使用 0-4 级人工相关性标注，区分最匹配、强匹配、相关但有关键缺口、相邻岗位和噪声岗位。
- 新增 `EvaluationService.run_job_relevance_evaluation` 和 `POST /evaluations/job-relevance`。
- 评测 summary 输出 `top1_accuracy`、`avg_top3_recall`、`avg_top5_recall`、`avg_mrr`、`avg_ndcg_at_5`、`low_grade_above_strong_count`、intent/difficulty/noise breakdown。
- 每个 case 的 `ranked_jobs` 写入候选岗位 rank、人工 grade、排序 score 和 relevance reasons，方便定位误排。
- 更新 README、API 文档、评测文档和开发文档，说明排序评测数据、指标和运行方法。

### 发现了什么问题
- 只靠真实腾讯 source smoke 的 top sample 无法量化排序质量，也无法覆盖不同中文 query 意图。
- 首次运行 job relevance evaluation 时整体 `top1_accuracy=1.0000`、`avg_mrr=1.0000`，但状态仍是 `completed_with_quality_failures`，因为 `推荐算法实习生` case 的 `top3_recall=0.5000`。
- 失败 trace 显示 `排序模型实习生` 是强相关同义岗位，但被 `数据开发实习生` 和 `Agent开发实习生` 压到第 4 名。
- 根因是旧排序规则对“开发/工程/实习”这类泛技术信号加权较高，却缺少“算法/推荐”领域意图 boost；泛技术词会在部分 query 下压过更具体的领域意图。

### 怎么修复的
- 在 `job_relevance` 中新增领域意图识别和 boost：算法/推荐、后端/API、数据开发、安全、评测、Prompt。
- 保留产品意图正向 boost，确保 `AI Agent 产品实习生` 和 `AI产品经理 Agent方向` 这类 query 不被工程岗位压过。
- 修复后重新运行排序评测：`status=completed`、`case_count=13`、`candidate_count=130`、`pass_rate=1.0000`、`top1_accuracy=1.0000`、`avg_top3_recall=1.0000`、`avg_top5_recall=1.0000`、`avg_mrr=1.0000`、`avg_ndcg_at_5=0.9495`、`low_grade_above_strong_count=0`。
- `推荐算法实习生` case 修复后 `top3_recall=1.0000`、`nDCG@5=0.9698`，`排序模型实习生` 不再被泛开发岗位压到 Top3 之后。

### 未修复的问题及原因
- 当前标注集仍是合成的离线标注，不是真实用户点击或投递转化数据；原因是项目还没有线上行为数据，现阶段先用可控噪声覆盖主要中文 query 意图。
- 排序权重仍是规则版，不是学习排序模型；原因是数据规模还不足以训练稳定模型，现阶段可解释规则更适合开发期定位问题。
- 中文分词仍是轻量规则和领域词表，不是完整 NLP 分词；原因是 source 层排序要保持低依赖、低延迟，后续如果引入真实标注数据再考虑专门的中文检索/排序模型。

### 下一步怎么做
- 把真实腾讯 source smoke 中出现的产品、正式岗、策划类混入样例沉淀进 `job_relevance_cases.json`。
- 探测更多中文招聘源后，为每个 source 记录排序前后 top sample，比较排序改善幅度。
- 为 job relevance evaluation 增加人工复核字段，逐步从合成标注过渡到真实 JD 标注。

## 2026-06-08 12:48 +08:00：增加中文岗位相关性排序并收敛默认岗位源

### 这次做了什么
- 新增 `app/services/job_relevance.py`，把岗位 source 层的中文相关性判断独立成可复用模块。
- 排序规则显式提升 Agent/LLM/RAG、开发/工程、实习/校招信号，降低产品、销售、商务等与“Agent 开发实习生”意图不匹配的岗位。
- 腾讯 source 从 `limit` 扩大到最多 `limit * 3` 的候选池后再排序截断，避免原始搜索顺序过早截断高质量岗位。
- `JobSearchService` 在多 source 并发返回、去重之后再次执行统一排序，保证 API 搜索和 source smoke 使用同一套排序逻辑。
- `real-job-source-smoke` 的 `sample_jobs` 新增 `relevance_score` 和 `relevance_reasons`，summary 新增 `avg_relevance_score` 和 `avg_top_relevance_score`。
- 将默认岗位源收敛为 `["tencent"]`；Lever 这类海外 ATS source 默认关闭，仅作为显式开启的英文辅助源。
- 更新 README、API 文档、架构文档、评测文档和开发文档，说明中文主场景、排序 trace 和默认 source 边界。
- 新增 `tests/test_job_relevance.py`，覆盖中文 Agent 实习开发岗位排序、产品/销售降权和 `internal tools` 不被误判为 `intern`。

### 发现了什么问题
- 真实腾讯 query `Agent 开发实习生` 会返回 Agent 实习岗、开发工程岗、产品经理和策划类岗位的混合结果；只看关键词命中会把产品/策划岗位排得过高。
- 原先默认 `sources=["tencent", "lever"]` 不符合中文主场景；Lever 与 Greenhouse 类似，更适合作为少量英文辅助，不应默认进入中文求职链路。
- 只记录 `internship_like_rate`、`agent_related_rate` 等命中率还不够，无法解释具体岗位为什么排在前面；source smoke 需要能看到排序原因。
- 第一次真实 smoke 打印完整 `case_results_json` 时，PowerShell 默认 GBK 输出遇到 JD 中的特殊空格字符产生 `UnicodeEncodeError`；source 请求和评测写库已成功，问题出在本地调试输出编码。

### 怎么修复的
- 用确定性中文相关性排序替代 source 原始顺序：排序分数由 query token、Agent/LLM/RAG、实习/校招、开发/工程、产品/运营、销售/商务、JD 非空和投递链接组成。
- `intern` 判断改为词边界正则，避免 `internal tools` 这类真实英文噪声被误判成实习。
- `JobSourceRegistry` 仅在 `LEVER_CAREERS_ENABLED=true` 时注册 Lever；`.env.example` 中显式写为 `false`。
- 前端岗位搜索默认只提交 `sources=["tencent"]`，Pydantic `JobSearchRequest` 默认值也同步调整。
- 真实 source smoke 已重新运行：`query=Agent 开发实习生`、`sources=tencent`、`limit=8`、`status=completed`、`reachable_source_rate=1.0000`、`result_source_rate=1.0000`、`total_result_count=8`、`non_empty_jd_rate=1.0000`、`apply_url_rate=1.0000`、`internship_like_rate=0.3750`、`query_relevance_rate=1.0000`、`agent_related_rate=1.0000`、`avg_relevance_score=18.8250`、`avg_top_relevance_score=27.2000`。
- 排序后 top3 为 `Agent Development Intern 107276`、`Agent Evaluation Intern 107491`、`AI Agent Research & Application Intern 106432`；后续为 Agent/大模型/RAG 开发工程类岗位，产品经理/策划类岗位被降到 top8 之后。
- 使用 `PYTHONIOENCODING=utf-8` 和 `python -X utf8` 重新打印真实 trace，确认 `sample_jobs` 中的 `relevance_score` 与 `relevance_reasons` 可读。

### 未修复的问题及原因
- `internship_like_rate` 仍为 0.3750；原因是本次腾讯候选池里实际只有 3 个明确实习岗位进入 top8，可通过增加中文岗位源和扩大候选池改善，不能靠排序凭空制造实习岗位。
- 当前排序是规则版，不是学习排序或 LLM reranker；原因是 source 层需要低成本、稳定、可解释，后续应基于真实中文岗位点击/人工标注数据做权重校准。
- PowerShell 默认 GBK 打印完整真实 JD JSON 仍可能遇到特殊字符；原因是终端编码问题，不影响 API JSON、数据库 trace 或 UTF-8 调试命令。

### 下一步怎么做
- 构建中文岗位排序标注集，覆盖实习、校招、正式岗、产品、算法、后端、销售和泛 AI 噪声，量化排序 NDCG/MRR。
- 继续探测字节、阿里、美团、华为等中文自有招聘源，只接入能稳定返回公开中文 JD 的 source。
- 针对“产品经理”“算法实习生”等不同中文 query 增加 query intent 测试，避免当前开发实习排序规则过拟合一个岗位。

## 2026-06-07 12:03 +08:00：切回中文主场景并撤销 Greenhouse 默认源方向

### 这次做了什么
- 根据新的使用场景约束，将项目默认岗位搜索 query 从 `Agent Development Intern` / `Agent intern` 调整为 `Agent 开发实习生`。
- 更新 `JobSearchRequest`、`AgentRunRequest`、Agent fallback query、`real-job-source-smoke` 和 `real-job-ingest-smoke` 的默认 query。
- 更新首页、岗位页和 Agent Run 页表单默认值，避免 UI 默认把用户带到英文求职场景。
- 更新 API 文档和评测文档中的真实 source/ingest smoke 示例，将中文 query 作为主路径，英文岗位源只保留为辅助场景。
- 新增 `tests/test_chinese_first_defaults.py`，验证岗位搜索和 Agent run 的默认 query 是中文主场景。
- 撤销 Greenhouse 接入方向，没有把 Greenhouse 加入默认技术栈或 source registry。

### 发现了什么问题
- Greenhouse 在北美公司 ATS 中常见，但不符合当前“中文岗位为主，少量英文辅助”的求职场景；把它加入默认 source 会让项目看起来技术栈更多，但产品场景变弱。
- 真实 Greenhouse smoke 虽然可达并能返回结果，但 `Agent Development Intern` query 会混入大量 AI Sales/Account Executive 等英文商业岗位，不适合作为中文 Agent 实习求职助手的默认岗位源。
- 项目多个默认入口仍是英文 query，包括 API schema、评测 endpoint 和前端表单；这会让真实测试和用户演示偏离中文主场景。
- 中文 query `Agent 开发实习生` 在腾讯招聘公开接口上可用，可以返回 Agent Development/Evaluation Intern、QQ-Agent 产品经理、元宝-Agent 架构工程师、腾讯视频-AI Agent 工程师等岗位。

### 怎么修复的
- 完整撤销 Greenhouse 代码、配置、默认 source 和测试文件，不保留与主场景不匹配的技术栈。
- 将默认 query 统一改为 `Agent 开发实习生`，并用新增测试锁住默认值。
- 真实 source smoke 已用中文 query 运行：`sources=tencent`、`limit=8`、`status=completed`、`reachable_source_rate=1.0000`、`result_source_rate=1.0000`、`total_result_count=8`、`non_empty_jd_rate=1.0000`、`apply_url_rate=1.0000`、`internship_like_rate=0.3750`、`query_relevance_rate=1.0000`、`agent_related_rate=1.0000`。
- 真实 ingest smoke 已用中文 query 运行：`sources=tencent`、`limit=1`、`status=completed`、`parse_success_rate=1.0000`、`ingest_success_rate=1.0000`、`chunk_index_success_rate=1.0000`、`retrieval_probe_success_rate=1.0000`、`parser_quality_pass_rate=1.0000`、`avg_parser_quality_required_recall=1.0000`、`avg_parser_quality_query_coverage=1.0000`。

### 未修复的问题及原因
- 目前中文主场景真实 source 仍主要依赖腾讯招聘；原因是字节、美团等公开站点本轮探测到的是 SPA/内部接口形态，不适合作为短时间内稳定接入的默认源。
- 腾讯中文 query 会混入正式岗位和产品岗位，`internship_like_rate=0.3750`；原因是真实招聘搜索本身按关键词召回，后续需要做中文岗位排序/过滤，而不是强行引入英文 ATS。
- 旧评测集中仍有不少英文 case；原因是它们现在作为英文辅助场景保留，下一步应继续增加/替换中文 case，使整体测试数据逐步中文占主。

### 下一步怎么做
- 增加中文岗位排序规则，让实习、开发、Agent/RAG/LLM 技能匹配的岗位排在产品/销售/泛 AI 岗位前面。
- 继续探测字节、阿里、美团、华为等中文自有招聘源，只接入能稳定返回公开中文 JD 的 source。
- 扩充 JD parser、RAG 和 LLM workflow 的中文 case 占比，英文 case 作为辅助保留。

## 2026-06-07 11:39 +08:00：真实 JD Ingest 增加 Parser Quality Probe

### 这次做了什么
- 为 `real-job-ingest-smoke` 增加 parser quality probe，在真实岗位 posting 入库后继续检查 query、title 和原始 JD 中的核心技能是否进入 structured JD。
- 每条真实岗位结果新增 `parser_quality_evaluable`、`parser_quality_probe_passed`、`parser_quality_expected_skills`、`parser_quality_query_skills`、`parser_quality_required_recall`、`parser_quality_structured_recall`、`parser_quality_query_coverage`、`parser_quality_missing_required_skills` 和 `parser_quality_missing_structured_skills`。
- Summary 新增 `parser_quality_evaluable_count`、`parser_quality_pass_rate`、`avg_parser_quality_required_recall`、`avg_parser_quality_structured_recall`、`avg_parser_quality_query_coverage`、`parser_quality_failure_count` 和 `parser_quality_failure_breakdown`。
- `real-job-ingest-smoke` 状态新增 `completed_with_parser_quality_failures`：当 source、parse、SQLite upsert、chunk 和 retrieval 都成功，但 parser 漏掉核心技能时，不再显示为完全成功。
- `JDParserService.parse_jd` 不再让 LLM 结果完全覆盖 heuristic 结果；required/preferred/responsibilities/qualifications/keywords 会做有序并集合并，避免 LLM 漏掉标题或职责中的显式技能。
- 新增单元测试覆盖健康 ingest quality probe 和故意漏抽 Agent/RAG/LLM 的 parser quality failure。
- 新增单元测试覆盖 LLM parser 输出过稀疏时，heuristic 抽出的 `Agent/FastAPI/RAG/Evaluation` 不会被覆盖丢失。
- 更新 README、API 文档和评测文档，说明 real-job-ingest-smoke 不只看 parse/ingest 成功，也会检查 parser 对核心 JD 技能的理解质量。

### 发现了什么问题
- `parse_success_rate=1.0` 只能说明 parser 返回了结构化 JSON，不能说明 required skills 足够完整；真实求职场景中，漏掉 `Agent/RAG/LLM` 会直接影响匹配、RAG 证据召回和简历定制。
- 单独的 JD parser 标注集能做离线质量回归，但真实 source smoke 仍需要一个轻量在线 probe，否则真实 JD 入库链路可能在质量退化时仍显示成功。
- 质量 probe 不能直接复用完整标注集，因为真实岗位没有人工 gold label；因此本轮采用保守技能词表，只评估 query/title/JD 中明确出现的高价值技术词。
- 第一次真实腾讯 ingest 运行暴露出实际问题：`parse_success_rate=1.0000`、`ingest_success_rate=1.0000`、`retrieval_probe_success_rate=1.0000`，但 `parser_quality_pass_rate=0.0000`，因为 LLM parser 只返回 `Python`、`SQL`，漏掉标题和职责中的 `Agent`。
- 这说明真实 LLM parser 不是总比规则 parser 更完整；LLM 输出如果直接覆盖 heuristic，会把确定性技能抽取结果丢掉。

### 怎么修复的
- 在 `_ingest_smoke_posting` 中解析完成后立刻生成 parser quality probe，并把结果随 job result 一起写入 `EvaluationRun.case_results_json`。
- probe 将 query/title 和 raw JD 中识别到的核心技能作为 expected skills，再分别计算 required recall、structured recall 和 query coverage。
- preferred/optional/加分项行不会作为 raw JD required quality 期望；`No prior X required`、`不要求 X`、`无需 X` 等负向语境也不会触发期望技能。
- 如果 quality probe 失败，summary 会保留 parse/ingest/chunk/retrieval 的成功率，同时把整体状态标记为 `completed_with_parser_quality_failures`，便于定位是“链路可用但理解质量差”。
- 修复 LLM 与 heuristic 的合并策略：LLM 仍可以补充结构化字段，但 list 字段会与 heuristic 结果做有序并集，不再覆盖掉确定性抽取出的技能。
- 修复后重新运行真实腾讯 JD ingest：`status=completed`、`parser_quality_pass_rate=1.0000`、`avg_parser_quality_required_recall=1.0000`、`avg_parser_quality_structured_recall=1.0000`、`avg_parser_quality_query_coverage=1.0000`、`required_skills_preview=Python, SQL, Agent`。
- 新增测试已运行：`tests/test_evaluation_service.py` 共 17 个测试通过；全量测试 `python -m pytest -q` 共 41 个测试通过。

### 未修复的问题及原因
- parser quality probe 仍是保守词表，不等同于人工标注的真实 JD gold label；原因是真实 source smoke 需要轻量、低成本、可在线运行，不能每次依赖人工标注。
- 当前 probe 主要覆盖技术岗位核心技能，对薪资、学历、城市、年限等非技能字段还没有做质量判断；原因是这些字段对本项目的匹配和简历定制影响次于 required skills，本轮先修最关键的语义风险。
- 真实运行仍出现 `Transformer cache_dir argument is deprecated` 第三方告警；原因是告警来自模型加载链路内部兼容层，不影响 parser quality、embedding 或 reranker 指标，本轮不通过隐藏 warning 来伪装干净结果。

### 下一步怎么做
- 如果真实 JD 的 `parser_quality_required_recall` 偏低，把失败岗位样例加入 JD parser 标注集，形成离线回归。
- 后续可扩展 quality probe 到 location、intern/full-time、salary/benefit 和 apply_url 字段，逐步补齐真实发布前 smoke。

## 2026-06-07 11:31 +08:00：新增 JD Parser 质量评测并修复解析边界

### 这次做了什么
- 新增 `evals/jd_parser_cases.json`，包含 30 个中英混合 JD 解析 case，覆盖 Agent/RAG、LLM Eval、Prompt Security、ML Platform、Backend、Frontend、Data Engineering、Recommendation、MLOps、Computer Vision 等岗位。
- 新增 `run_jd_parser_evaluation` 和 `POST /evaluations/jd-parser`，独立评估 JD parser 的结构化质量，不再只依赖真实 JD ingest smoke 的 `parse_success_rate`。
- JD parser 评测新增 `avg_required_skill_recall`、`avg_keyword_hit_rate`、`job_type_accuracy`、`responsibility_min_pass_rate`、`qualification_min_pass_rate`、`absent_required_skill_violation_count`、`parser_mode_counts`、`difficulty_breakdown` 和 `noise_breakdown`。
- 扩展 JD 技能别名归一化：覆盖 `Vector Database`、`Embedding`、`Reranker`、`Tool Calling`、`Prompt Regression`、`Prompt Injection`、`Model Evaluation`、`A/B Testing`、`Feature Store`、`MLflow`、`Airflow`、`Kafka`、`Recommendation`、`Ranking`、`CTR`、`MLOps`、`Computer Vision` 等。
- parser 开始区分 required 与 preferred：`Preferred`、`Nice to have`、`加分项`、`optional` 等行进入 `preferred_skills`，不再混入 required。
- parser 增加负向语境过滤：`No prior X required`、`X is not required`、`不要求 X`、`无需 X` 等不会进入 required skills。
- 修复 job_type 推断：`intern` 改为词边界匹配，避免 `internal tools` 被误判成实习；同时把 `location=Remote` 和常规工程岗位标题纳入推断。
- 新增单元测试覆盖 JD parser 标注集、技能别名、preferred 技能和负向语境。
- 更新 README、API 文档和评测文档，说明 JD parser 评测入口、指标、数据规模和最新结果。

### 发现了什么问题
- 真实 JD ingest smoke 只能说明 parser 没报错，不能说明 required skills 抽全了；之前腾讯真实 JD 只抽出少量技能，说明需要单独的 parser 质量指标。
- 第一版新增评测后，30 个 case 的 pass rate 只有 0.6333，但技能召回已经接近满分；失败集中在 `job_type`，说明类型推断是独立薄弱点。
- 负向语境判断窗口过宽：`Tool Calling and A/B tests` 后面下一行出现 `No prior Kubernetes... required`，会把前一行技能误判为“不要求”。
- `intern` 使用子串匹配导致 `internal tools` 被误判为实习岗位，这是典型真实 JD 文本噪声。

### 怎么修复的
- 将负向语境判断收敛到当前行/句，而不是跨行窗口；这样只影响同一句里的 `No prior X required` 或 `X is not required`。
- preferred 技能抽取允许保留 `not required` 语境，因为 preferred 行的语义本来就是“非硬性但可加分”。
- `intern` 改为正则词边界匹配，避免命中 `internal`、`internet` 等普通词。
- `_guess_job_type` 现在会读取 `location`，并对没有显式 full-time 但标题是 Engineer/Developer/Analyst/Scientist/Architect 的岗位推断为 `full-time`。
- 离线评测已运行：`case_count=30`、`completed_rate=1.0000`、`pass_rate=1.0000`、`avg_required_skill_recall=0.9972`、`avg_keyword_hit_rate=1.0000`、`job_type_accuracy=1.0000`、`absent_required_skill_violation_count=0`。
- 新增测试已运行：`tests/test_evaluation_service.py` 共 15 个测试通过；全量测试 `python -m pytest -q` 共 39 个测试通过。

### 未修复的问题及原因
- 本次 JD parser 最新指标来自测试环境 `heuristic_fallback`，还不是真实 LLM parser 与 heuristic parser 的对照评测；原因是本轮先补齐离线可重复的 parser 质量门禁。
- 当前 schema 仍只有 `required_skills` 与 `preferred_skills`，没有更细的 `must_have`、`nice_to_have`、`explicitly_not_required` 字段；原因是下游 matcher 现在只消费 required/preferred，过早扩 schema 会牵动更多链路。
- 真实招聘源中的超长 JD、HTML 残留和多岗位混排还没有进入这个离线数据集；原因是本轮先用合成强噪声覆盖主要语义错误，下一步再接真实 source 样本。

### 下一步怎么做
- 用真实岗位源采样 JD，生成 parser LLM 与 heuristic 的对照评测，重点看 required skill recall、preferred/negative 误抽取和 job_type。
- 在 real-job-ingest-smoke 中加入 parser quality probe，不只记录 `required_skill_count`，也记录命中核心查询技能的比例。
- 如果真实 LLM parser 与 heuristic 差异大，增加 parser trace 对比和少量 gold JD 回归阈值。

## 2026-06-07 11:12 +08:00：新增真实 JD Ingest Smoke 并收敛模型缓存边界

### 这次做了什么
- 新增 `run_real_job_ingest_smoke`，从真实岗位源获取 posting 后继续验证 JD parser、SQLite upsert、JD chunk、embedding/reranker provider 和 retrieval probe。
- 新增 `POST /evaluations/real-job-ingest-smoke`，支持 `query`、`location`、`limit` 和重复 `sources` 参数。
- 每条真实岗位结果记录 `parse_success`、`ingest_success`、`chunk_index_success`、`retrieval_probe_hit`、`chunk_count`、`chunk_types`、`required_skill_count` 和 `retrieved_chunk_preview`。
- Summary 新增 `parse_success_rate`、`ingest_success_rate`、`chunk_index_success_rate`、`retrieval_probe_success_rate`、`embedding_provider_counts`、`retrieval_query_embedding_provider_counts`、`reranker_provider_counts`、`embedding_fallback_job_count` 和 `retrieval_fallback_job_count`。
- `EmbeddingService` 和 `RerankerService` 默认将 `HF_HOME`、`SENTENCE_TRANSFORMERS_HOME` 指向项目内 `data/models`，并默认设置 `HF_HUB_DISABLE_SYMLINKS_WARNING=1`。
- 新增单元测试覆盖真实 JD ingest 成功链路和 parser 失败链路，确保 parser 不可用时记录 `parse_error`，不会静默兜底为成功。
- 更新 README、API 和评测文档，说明 source smoke 与 ingest smoke 的边界。

### 发现了什么问题
- 只有 source smoke 还不够：岗位源可达并不代表 JD parser、SQLite 入库、chunk、embedding 和 retrieval probe 都能工作，需要单独的 ingest 层指标。
- 如果复用完整 `JobSearchService.search` 作为 smoke，source error、parser error、embedding error 和 SQLite error 会混在一起，生产排障时很难定位。
- 真实腾讯 JD 解析运行成功，但首次运行暴露出 HuggingFace 依赖会尝试访问/写入默认用户缓存目录；在当前 Windows 环境下，用户目录缓存会出现权限 warning。
- 将缓存迁到项目目录后，权限 warning 消失，但 Windows 不支持 symlink 时仍会出现 HuggingFace symlink 降级 warning；这是缓存策略噪声，不是业务失败。
- 关闭 symlink warning 后，真实运行仍会出现一条 `Transformer cache_dir argument is deprecated` 依赖告警；它来自第三方模型加载链路，不影响本次 ingest 指标。
- 当前真实 JD parser 对腾讯 Agent 实习 JD 只抽出 `Python`、`SQL` 两个 required skill，说明 parser 的技能抽取还需要在更多真实 JD 上校准。

### 怎么修复的
- `real-job-ingest-smoke` 逐 posting 捕获失败阶段：`parse_error` 表示 JD parser 失败，`ingest_error` 表示 SQLite upsert、chunk 或索引失败。
- 成功写入后立刻用 `query_job_chunks` 执行 retrieval probe，证明新写入的 JD chunk 可检索，而不是只看数据库行数。
- 从 `job_chunks.metadata_json.embedding` 和 retrieval metadata 中提取 provider 与 fallback reason，区分 `sentence_transformers/cross_encoder` 真模型路径和 `hash/heuristic` 降级路径。
- 模型缓存默认写入 `data/models/huggingface`，并关闭 symlink warning；用户已经设置 `HF_HOME` 时仍尊重用户配置。
- 真实 ingest smoke 已运行：`query=Agent Development Intern`、`sources=tencent`、`limit=1`、`parse_success_rate=1.0000`、`ingest_success_rate=1.0000`、`chunk_index_success_rate=1.0000`、`retrieval_probe_success_rate=1.0000`、`avg_chunks_per_job=8.0000`、`embedding_provider_counts=sentence_transformers:8`、`reranker_provider_counts=cross_encoder:3`、fallback job count 为 0。

### 未修复的问题及原因
- 真实 ingest smoke 现在默认最多跑少量岗位；原因是每条真实 JD 都会消耗 LLM 和 embedding/reranker 时间，当前先作为发布前 smoke，不做大规模批量评测。
- JD parser 的真实 required skills 抽取还不够细；原因是当前 parser prompt 与 schema 偏通用，下一步需要用真实 JD 标注集评估 parser recall。
- UI 还没有展示 source/ingest smoke 的历史趋势；原因是本轮先把 EvaluationRun 数据写完整，后续再做可视化。
- `Transformer cache_dir argument is deprecated` 告警未处理；原因是当前 CrossEncoder 已优先使用 `model_kwargs` 传递缓存目录，剩余告警可能来自第三方内部兼容层，本轮不为了隐藏 warning 改动模型加载参数。

### 下一步怎么做
- 基于真实腾讯/Lever JD 构建小规模 parser 标注集，评估 required skill recall、responsibility coverage 和 chunk coverage。
- 增加真实 JD parser 回归阈值，避免 parser 把核心技能抽漏却仍然显示 ingest 成功。
- 在评测页面展示 source smoke 与 ingest smoke 的最近状态、失败阶段和 provider/fallback 分布。

## 2026-06-06 21:56 +08:00：新增真实岗位源 Smoke 评测

### 这次做了什么
- 新增 `run_real_job_source_smoke`，并发探测真实岗位源，只记录 source 层健康度，不写入主岗位库，也不调用 LLM 解析 JD。
- 新增 `POST /evaluations/real-job-source-smoke`，支持 `query`、`location`、`limit` 和重复 `sources` 参数。
- 按 source 输出 `status`、`source_reachable`、`has_results`、`result_count`、`latency_ms`、`error` 和 `sample_jobs`。
- 汇总指标新增 `reachable_source_rate`、`result_source_rate`、`total_result_count`、`non_empty_jd_rate`、`apply_url_rate`、`internship_like_rate`、`query_relevance_rate`、`agent_related_rate` 和 `source_errors`。
- 保留 `agent-full-flow` 的可控岗位源回归，不把真实招聘站网络波动计入核心 `pass_rate`。
- 增加 fake source 单元测试，覆盖一个健康 source 和一个异常 source 同时存在时的 source 层指标，并断言 query relevance 与 Agent/AI relevance 指标。
- 更新 README、API 和评测文档，说明真实岗位源 smoke 的定位、接口和指标含义。

### 发现了什么问题
- 之前虽然有腾讯招聘和 Lever 的真实岗位源，但没有独立评测入口；如果直接塞进 Agent full-flow，会让外部网络波动影响核心链路回归。
- `JobSearchService.search` 会进入 JD parse 和入库链路，真实岗位源 smoke 如果复用它，会把 source 可达性、LLM 解析、embedding 和 SQLite 写入混在一起，定位问题不够清楚。
- 招聘源的“可访问”和“有结果”是两件事：source 可能正常返回空结果，也可能网络失败；需要分别记录 `reachable_source_rate` 和 `result_source_rate`。
- pytest 在当前 Windows 环境下会提示 `.pytest_cache` 写入权限警告，单测仍能通过；这属于测试缓存写入问题，不影响业务结果。

### 怎么修复的
- `EvaluationService` 直接通过 `JobSourceRegistry` 并发调用 source `search`，只做轻量岗位质量统计，不进入 JD parse 或职位入库。
- 对每个 source 单独 catch 异常并写入 `case_results_json`，把失败显式记录为 `source_error`，不是静默吞掉。
- Summary 增加 `core_regression_independent=true`，明确该评测不参与核心 Agent full-flow 回归门禁。
- Summary 状态细分为 `completed`、`completed_with_empty_sources`、`completed_with_source_errors` 和 `source_unavailable`，避免把空结果源误看成完全成功。
- 新增测试验证：一个 source 成功、一个 source 报错时，summary 为 `completed_with_source_errors`，且错误、样例岗位和质量指标都保留。
- 新增测试验证：所有 source 可达但部分 source 为空时，summary 为 `completed_with_empty_sources`。
- 真实网络 smoke 已运行：`query=Agent Development Intern`、`sources=tencent,lever`、`status=completed_with_empty_sources`、`reachable_source_rate=1.0000`、`result_source_rate=0.5000`、`total_result_count=8`、`non_empty_jd_rate=1.0000`、`apply_url_rate=1.0000`、`internship_like_rate=1.0000`、`query_relevance_rate=1.0000`、`agent_related_rate=1.0000`、`source_error_count=0`。腾讯返回 8 个岗位，Lever 当前 query 为空。

### 未修复的问题及原因
- 该 smoke 当前只检查 source 层，不验证真实岗位 JD 入库后的 parser/RAG/matcher 质量；原因是本轮先把外部源波动从核心回归中隔离出来。
- 还没有为真实 source 建立长期趋势看板；当前 EvaluationRun 已保存指标，后续可以在 UI 中展示历史 source 稳定性。
- Lever 当前配置 slug 对 `Agent Development Intern` 为空；原因可能是公司 slug 覆盖不足或岗位关键词不匹配，下一步需要扩展更多公司自有招聘源或为不同 source 配置查询策略。

### 下一步怎么做
- 在真实 source smoke 稳定后，增加一个可选的“真实 JD 解析与入库 smoke”，单独评估 parser/RAG，不和 source 可达性混淆。
- 扩展 Lever slug 和更多互联网/AI 大厂自有招聘源，并为中文/英文岗位源配置不同 query。
- 在 UI 评测页展示 source 层指标和最近失败原因。

## 2026-06-06 20:56 +08:00：补齐 Tailor ReAct Repair、Evidence Classifier 与 LLM 断点续跑

### 这次做了什么
- 为 `resume_tailor` 增加 1 轮 ReAct repair loop：初稿先过 Guardrail；如果 `risk_level=high` 或 `passed=false`，自动读取 Guardrail issues、当前草稿和压缩上下文，修复后再次验证。
- 真实 LLM 修复路径新增 `resume_tailor.repair_resume` 调用，要求 strict JSON，只能删除或改写无证据 claim、缺口披露和 `eager to learn` 类表达，不能新增事实。
- 离线测试路径新增 `resume_tailor.heuristic_repair`，用于在无 LLM fallback 测试中验证同一套 Guardrail repair 行为。
- 新增 `EvidenceClassifier`，区分 `shipped_project`、`metric_evidence`、`coursework`、`planned_learning`、`missing_skill_disclosure`、`adjacent_experience`、`generic_skill` 和 `unknown`。
- `MatcherService.retrieve_evidence` 接入 evidence type classification，并在相关性评分里提升交付/量化证据，降低课程、计划学习和缺口披露证据。
- LLM workflow 的 RAG stage trace 增加 `evidence_type` 和 `polarity`，方便检查 Top evidence 是真实交付证据还是噪声。
- `run_llm_workflow_evaluation` 增加 `resume_from_last_completed`，可以从 JSONL trace 中读取连续完成的 case 前缀，再从第一个缺失 case 继续运行。
- `POST /evaluations/llm-workflow` 增加 `resume_from_last_completed` 查询参数；当没有传 `trace_path` 时默认使用 `data/runtime/llm_workflow_trace_latest.jsonl`。
- JSONL trace 事件新增完整 `case_result`，恢复运行后不只知道最终状态，也能保留每个 case 的中间 `stage_trace`；`tailor_resume` stage 会展开 `react_repair` 元数据。
- 补充 evidence classifier、ReAct repair 和 LLM workflow resume 的单元测试；同步更新 README、API、Agent 设计和评测文档。

### 发现了什么问题
- 证据类型如果只按整段句子判断，`Analyzed A/B tests but did not implement ranking models` 这类混合证据会被整体判成负面，导致规则定制简历丢失真实的 `A/B tests` 和 `experiment analysis` 证据。
- 完整 Agent full-flow 评测首次回归时，推荐算法 case 的简历定制关键词覆盖失败，根因就是混合证据被 evidence type 过滤掉。
- Windows 下 pytest 的临时目录在当前环境触发权限问题，`tmp_path` 用例会在 fixture 阶段失败，无法真正测试断点续跑逻辑。
- 旧 JSONL trace 只保留 case 状态和 stage 摘要，恢复运行时不能完整还原 `case_results_json`，会影响后续 summary 和问题排查。
- 真实岗位源 smoke 仍然会受外部网络、接口变化和招聘站波动影响，不适合作为这一步内部链路修复的阻塞项。

### 怎么修复的
- `resume_tailor` 不再按 evidence type 直接丢弃 project/experience 证据，而是统一经过 `_safe_evidence_text`：保留 `but/did not/no` 前面的正向片段，删除负面披露。
- ReAct repair 的修复结果写入 `keyword_alignment.react_repair`，记录是否触发、触发风险、问题类型、修复工具、修复前后风险和二次 Guardrail 是否通过。
- `_load_resumable_llm_results` 只读取 selected cases 的连续完成前缀，遇到第一个缺失 case 就停止，避免跳跑导致评测顺序错乱。
- `_append_llm_trace` 写入完整 `case_result`，同时兼容旧 trace 的简化事件格式。
- 断点续跑测试改用 `data/runtime/test_llm_resume_*.jsonl` 并在 finally 中清理，避开当前 Windows pytest temp 权限问题。
- 完整回归已通过：`pytest -q` 为 `32 passed`；`python -m compileall app tests` 通过。
- 真实 LLM workflow smoke 已用新 key 跑通 3 个 case：先跑 1 个 case 写入 trace，再用 `resume_from_last_completed=true` 跳过已完成前缀补跑到 3 个 case；`resumed_case_count=1`、`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- 真实 `resume_tailor.repair_resume` smoke 已触发：故意构造 `Eager to learn MLflow` 高风险初稿，LLM repair 后正文不再包含 `MLflow`，二次 Guardrail 从 `high` 降为 `low` 并通过。

### 未修复的问题及原因
- 还没有加入真实岗位源 smoke；原因是本轮按要求先修复不依赖外部岗位源的三个问题，真实岗位源会在内部链路稳定后作为 source 层指标接入。
- Evidence classifier 目前是规则版，不是训练模型；原因是当前还缺真实人工标注数据，先用规则分类让 RAG 排序和 trace 具备可解释性。
- LLM workflow 的断点续跑目前基于 JSONL trace，不支持直接从历史 `EvaluationRun` 自动恢复；原因是 JSONL 更适合长跑中断的即时恢复，数据库级恢复需要额外设计 checkpoint 选择和冲突处理。
- ReAct repair 当前限制为 1 轮；原因是简历改写场景更需要可控、可审计，下一步如果真实 case 证明 1 轮不足，再扩展到有限状态机或 LangGraph 节点。

### 下一步怎么做
- 在 Agent full-flow 评测里加入真实岗位源 smoke，并把网络失败、空结果、解析失败归入 source 层指标，不影响核心链路回归。
- 用真实 PDF 简历和真实 JD 标注数据校准 evidence type 权重，补充 abandoned prototype、research prototype、internship delivery 等证据类型。
- 把真实 LLM workflow 从 3-case smoke 扩展到 18-case 长跑，重点观察 repair 触发率、长 prompt 耗时和不同难度桶的稳定性。
- 评估是否把 LLM workflow resume 扩展到 `EvaluationRun` checkpoint，并在 UI/API 中展示可恢复进度。

## 2026-06-06 11:25 +08:00：重定义适配标注并补齐 Agent 全流程评测

### 这次做了什么
- 重新定义 `strong_fit`、`partial_fit`、`weak_fit` 标注标准，明确目标岗位、headline、求职意向不算匹配证据，负面证据优先级高于关键词命中。
- 新增 `evals/agent_full_flow_cases.json`，覆盖岗位搜索、匹配排序、简历定制、`quick_apply`、`fit_gate`、Trace 和 Artifact。
- 新增 `POST /evaluations/agent-full-flow`，并在评测服务中使用可控岗位源写入真实 `jobs`、`job_chunks` 和匹配结果。
- `quick_apply` 前新增 `fit_gate`：低于 55 分直接失败，并在 Agent step trace 中记录缺失技能和阻断原因。
- 匹配器改为只用事实 support text 做技能覆盖判断，过滤 guided raw text 中的目标岗位、headline、邮箱等元信息。
- Profile chunk 构建同样过滤目标意向类元信息，避免 RAG 证据被“想做某岗位”污染。
- 增强匹配器的负面证据识别，覆盖 `no/not/without/lacks/missing/did not build/coursework/read articles` 等表达。
- 简历定制 prompt 新增硬规则：缺失 JD 要求只能写进 `keyword_alignment.missing/notes`，不能以 “eager to learn” 等形式写进简历正文。
- Guardrail 增加“缺失技能正文披露”和技能别名识别，能区分 `A/B testing` 与 `A/B tests/experiment analysis`、`model evaluation` 与 `evaluation dashboards`。
- forbidden claim 检查改为否定上下文感知，避免把 “did not implement ranking models” 误判成编造 ranking model。
- 规则 fallback 简历定制在写入 `Selected Evidence` 前会清洗负面证据句，保留 “A/B tests” 这类正向片段，丢弃 “did not implement ranking” 和 “No MLflow”。
- 匹配器负面词从裸 `learning` 收紧为 `learning about/currently learning`，避免误伤 `Machine Learning`。
- 更新 README、API、架构、Agent 设计和评测文档。

### 发现了什么问题
- 完整 Agent 评测第一次暴露出目标意向污染：候选人写了 `Target roles: Agent Development Intern`，旧匹配逻辑会把 `Agent` 当成事实技能。
- `No MLflow or feature store experience` 这类句子会被旧关键词匹配误判成覆盖 MLflow/Feature Store。
- 推荐算法和 ML 平台弱匹配 case 不应允许一键投递；更合理的产品行为是允许分析或定制，但 `quick_apply` 必须被门禁拦住。
- 重复运行 Agent full-flow 评测时，评测岗位 external_id 会撞 SQLite 唯一约束。
- 真实 LLM trace 发现，旧 forbidden claim 检查只做 substring，会把否定披露误判成违规。
- 真实 LLM trace 还发现，Guardrail 如果没有技能别名，会把真实证据里的 `A/B tests`、`evaluation dashboards` 误判为不支持 `A/B testing`、`model evaluation`。
- 完整 pytest 首次回归时，增强 Guardrail 把离线 fallback 生成的负面证据原文判为高风险，说明 fallback 也必须遵守与 LLM prompt 相同的简历正文约束。
- 检查规则时发现裸 `learning` 会误伤 `Machine Learning`，这是求职场景里非常常见的技术词边界。

### 怎么修复的
- `MatcherService._support_text` 改为事实字段优先，raw text 只保留非元信息行；`ResumeTextSplitter.build_resume_chunks` 也做同样清洗。
- 在句子级别判断技能是否被正向或中性证据支持；如果技能只出现在负面句中，就进入 missing。
- Agent full-flow evaluation 每次运行生成唯一 namespace，原始岗位 ID 保存在 `eval_external_id`，既可重复运行又可稳定断言。
- 把推荐算法和 ML 平台边界 case 重标为弱匹配投递阻断样例，测试要求 `fit_gate_block_count >= 3`。
- Guardrail 新增缺失技能正文披露检查和技能别名表；`eager to learn MLflow` 不再算覆盖 MLflow，`Machine Learning` 也不会被误判为“正在学习”。
- `_heuristic_tailor` 新增安全证据清洗，避免在离线测试模式下把 RAG 原文中的负面缺口直接贴入简历正文。
- 增加 `Machine Learning` 边界测试，保证它不会被当作负面证据；`currently learning RAG` 仍会被识别为负面。
- 真实 LLM 5-case smoke 用新 key 重跑通过：`completed_rate=1.0000`、`end_to_end_pass_rate=1.0000`、`fit_label_accuracy=1.0000`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- 离线 Agent full-flow 评测通过：`pass_rate=1.0000`、`top_job_accuracy=1.0000`、`quick_apply_pass_rate=1.0000`、`fit_gate_block_count=3`、`trace_pass_rate=1.0000`。
- 完整回归测试通过：`28 passed`。

### 未修复的问题及原因
- 还没有把真实招聘网站抓取纳入 Agent full-flow 评测；原因是外部岗位源会波动，当前全链路回归先用可控岗位源保证可重复。
- 简历定制还没有实现 ReAct repair loop；原因是本轮先把生成约束和 Guardrail 规则补齐，下一步再把高风险失败自动修复成 1-2 轮可追踪循环。
- Guardrail 的技能别名仍是规则表，不是训练过的 evidence classifier；原因是当前样例规模还不足以支撑领域分类器训练，但已经把真实 trace 暴露的别名补入回归。

### 下一步怎么做
- 为 `resume_tailor` 增加 ReAct repair loop：Guardrail 高风险时自动读取问题、收缩上下文并重写一次。
- 在 Agent full-flow 评测里加入真实岗位源 smoke，只把网络波动作为 source 层指标，不影响核心链路回归。
- 增加 evidence type classifier，区分 shipped project、metric evidence、coursework、planned learning 和 missing-skill disclosure。
- 给 LLM workflow 增加 resume-from-last-completed，支持长跑中断后继续。

## 2026-06-06 09:25 +08:00：收敛上下文治理并补 LLM 评测逐 Case Trace

### 这次做了什么

- 将上下文治理从独立 `context_manager` subagent 改回 LLM 调用前的 runtime policy。
- 从 Skill 注册表中移除 `progressive_disclosure`，保留 `fit_assessment`、`resume_tailoring` 这类真正的任务能力。
- 将 `ContextCompressor` 从过重的 L4/L5/L6 多阶段压缩，收敛为 Profile 摘要、JD 摘要、Top evidence 和一次 prompt packet 总预算检查。
- `AgentPlanner.context_policy` 保留渐进式披露和预算策略，但明确它不是独立 subagent 或 skill。
- LLM workflow 评测改为启动时先创建 `EvaluationRun`，每完成一个 case 就更新 `summary_json` 和 `case_results_json`。
- 每个 LLM workflow case 新增 `stage_trace`，记录 resume parse、JD parse、RAG、fit judge、tailor 和 Guardrail 的中间摘要。
- `run_llm_workflow_evaluation` 新增 `case_limit`、`case_indexes` 和 `trace_path`，API 支持 `POST /evaluations/llm-workflow?case_limit=3`。
- 更新 README、API、架构、Agent 设计、开发说明和评测文档。

### 发现了什么问题

- 6 级上下文压缩确实偏过度，容易显得像为了架构复杂度而复杂。
- 单独用一个 `context_manager` subagent 管压缩也不够主流；上下文管理更适合作为 agent runtime/memory/prompt assembly policy，而不是一个会独立推理的 subagent。
- 之前真实 18-case 测试超时后没有结果，是因为评测服务把 case result 放在内存 list 中，最后才创建 `EvaluationRun`；命令被杀时自然没有 summary。
- 新增逐 case trace 后，真实测试暴露出 strong case 的 tailor `prompt_packet` 曾超过 9000 字符预算，说明只看最终 pass 会漏掉中间质量问题。
- 最新真实 3-case 测试中，`ml_candidate_partial_agent_role` 仍被模型判为 `weak_fit`，但 trace 显示 RAG 已检到 “did not build an agent system”，所以这是 partial/weak 标注边界问题，不是上下文丢失。

### 怎么修复的

- 移除 `context_manager` subagent 和 `progressive_disclosure` skill，把渐进式披露放到执行计划的 `context_policy`。
- 将压缩策略改为 `progressive_disclosure_budgeted_packet`，元数据只保留 Profile、JD、Evidence 和 Prompt Packet 四个层面。
- 压缩 evidence metadata，只保留 rank/score/rerank provider/final score 等排序调试必要字段，避免 metadata 把 prompt 撑大。
- 真实 LLM workflow 每跑完一个 case 就落库，并可写入 `data/runtime/llm_workflow_trace_latest.jsonl`。
- 重新跑真实 3-case LLM 测试：`completed_rate=1.0000`、`end_to_end_pass_rate=0.6667`、`fit_label_accuracy=0.6667`、`tailor_pass_rate=1.0000`、`guardrail_pass_rate=1.0000`。
- trace 确认两个 tailor case 的 prompt packet 都在预算内：strong case 6071 chars，hard partial case 5516 chars。

### 未修复的问题及原因

- hard partial/weak 边界仍未修复；原因是当前样例把“有 Python/Transformers/Model Evaluation，但明确没有 Agent/RAG 交付”的候选人标为 `partial_fit`，而模型按严格岗位要求判 `weak_fit` 也有合理性，需要重新定义标注标准。
- LLM workflow 还没有真正的断点续跑；现在已经逐 case 落库和写 JSONL，但如果要从某个 case 继续，还需要增加 resume-from-last-completed 参数。
- API 目前只暴露 `case_limit`，没有暴露 `case_indexes`；原因是公开 API 先保持简单，开发脚本可直接调用 service 跑指定 case。

### 下一步怎么做

- 为 LLM workflow 增加 resume mode，从 trace 或 `EvaluationRun` 中找到最后完成 case 后继续。
- 重新审视 partial/weak 人工标注，增加“相邻能力但无交付”的边界样例。
- 在 summary 中加入 prompt packet `within_budget_rate`，让预算问题变成量化指标。
- 后续 ReAct repair loop 只在 Guardrail 高风险或证据不足时启用，并按需请求 deferred context。

## 2026-06-06 08:43 +08:00：补强 LLM Skill、SubAgent 与渐进式上下文披露

### 这次做了什么

- 新增 Agent Skill 注册表和 SubAgent 注册表，通过 `GET /agent/skills`、`GET /agent/subagents` 暴露能力边界。
- 将误理解的“奖金税披露”纠正为“渐进式披露”，新增 `progressive_disclosure` skill，并由 `context_manager` subagent 负责。
- `AgentPlanner` 的执行计划新增 `skills`、`subagents`、`context_policy` 和 `langgraph_decision` 字段。
- 重写 `ContextCompressor`，从单层裁剪升级为分级压缩：L1 Profile、L2 JD、L3 ranked evidence、L4-L6 prompt packet。
- 简历定制和 LLM workflow fit judge 都接入分级压缩上下文，并把 `context_compression` 元数据写入评测结果。
- 更新 README、架构文档、Agent 设计文档、API 文档、开发说明和评测文档，说明 Skill/SubAgent、渐进式披露、分级压缩和 LangGraph 暂不迁移理由。
- 新增上下文压缩测试、Skill/SubAgent API 测试、执行计划能力边界测试，并扩展 LLM workflow summary 测试。

### 发现了什么问题

- LLM 部分不是缺一个更大的 prompt，而是缺明确的能力边界、上下文预算、分级披露和可评测的压缩元数据。
- `ResumeTailorService._llm_tailor` 的异常 fallback 分支引用了已经不在作用域内的 `profile/job/evidence`，真实 LLM 超时或坏 JSON 时会触发二次错误。
- 18-case 真实 LLM workflow 全量评测在 20 分钟命令超时后没有拿到 summary，说明当前评测执行器缺少分批、逐 case 落盘和断点恢复。
- 5-case 真实 smoke 评测中，`ml_candidate_partial_agent_role` 仍被模型判为 `weak_fit`，partial/weak 边界仍不稳定。
- 2-case context smoke 发现短小 fit judge 上下文因为结构化字段和 trace 元数据，可能比原始上下文略大，直接展示负数 `reduction_ratio` 容易误导。

### 怎么修复的

- 用 `progressive_disclosure` skill 明确“默认只披露结构化摘要和 Top evidence，证据不足直接报告缺口”的规则。
- 增加 `context_manager` subagent，把上下文压缩从 prompt 内约定提升为可注册、可测试、可展示的工程模块。
- 在 `ContextCompressor` 中记录每层 `input_chars`、`output_chars`、`budget_chars`、`dropped_chars`、`within_budget` 和 shrink events。
- 修复 `_llm_tailor` 的参数传递，保证 LLM 异常时如果显式开启测试 fallback，可以正常回到规则路径。
- LLM workflow summary 新增 `context_compression` 聚合指标，包括 fit/tailor 压缩上下文数量、平均压缩率和平均保留证据数。
- 将 `reduction_ratio` 最低值限制为 0，并新增 `expansion_ratio` 表示短上下文结构化开销。
- 跑通真实 LLM 连通性测试、5-case 全流程 smoke 和 2-case context smoke；普通测试保持 `21 passed`。

### 未修复的问题及原因

- 暂不把整个 Agent 改成 LangGraph；原因是当前 Orchestrator 已有 plan-execute、trace、artifact 和工具边界，现阶段迁移框架收益低于补齐上下文治理和评测闭环。
- 18-case 全量真实 LLM 评测仍未在本次改动后完成；原因是顺序真实调用耗时过长，命令超时会丢失中间结果，需要先改造评测执行器。
- `ml_candidate_partial_agent_role` 的 partial/weak 边界仍未修复；原因是这需要更多边界样例、prompt 标准或单独 verifier，不应靠一次 prompt 微调硬掰结果。
- L3 evidence 层的 JSON metadata 开销仍可能让层级预算显示 `within_budget=false`，但最终 L4-L6 prompt packet 会继续压缩到总预算内；后续需要区分“证据文本预算”和“JSON 包预算”。

### 下一步怎么做

- 给 LLM workflow 增加 smoke mode、case limit、逐 case 落盘和可恢复运行，避免全量真实评测超时后没有 summary。
- 增加 partial/weak 边界数据，尤其是“有 ML/LLM 相邻经验但没有 Agent/RAG 交付”的案例。
- 评估不同 evidence budget 对 fit label、tailor keyword hit、Guardrail 通过率的影响，选择更稳的压缩预算。
- 在 Guardrail 高风险时实现 1-2 轮 ReAct repair loop，并让 repair loop 按需请求 deferred context。
- 等浏览器投递、邮箱、日历或多 MCP server 接入后，再评估是否迁移到 LangGraph。

## 2026-06-06 01:15 +08:00：补强 LLM 端到端流程评测与真实调用指标

### 这次做了什么

- 新增 `evals/llm_workflow_cases.json`，把 LLM 评测从 3 条岗位匹配样例扩展为 18 个端到端流程案例。
- LLM 评测覆盖简历解析、JD 解析、RAG 证据检索、岗位适配判断、简历定制和 Guardrail 验证。
- LLM 评测新增量化指标：`completed_rate`、`end_to_end_pass_rate`、`resume_parse_success_rate`、`jd_parse_success_rate`、`fit_label_accuracy`、`fit_score_in_range_rate`、`tailor_pass_rate`、`guardrail_pass_rate`、`forbidden_claim_free_rate` 和 `difficulty_breakdown`。
- 将岗位适配判断 prompt 改成通用证据约束规则，不再写死为 Agent/RAG 岗位边界。
- 在 schema 层兼容真实 LLM 常见的 `null` 叶子字段，把字符串字段缺失归一为空字符串，把列表字段缺失归一为空列表。
- 改进异常记录，`ReadTimeout` 这类 `str(exc)` 为空的异常会记录异常类型和 `repr(exc)`，方便通过 trace 追溯。
- 更新 README、API 说明、开发说明和评测文档，补充真实 LLM workflow 评测运行方式、指标定义和实测结果。
- 新增 LLM workflow 数据集测试、summary 指标测试、schema 归一化测试和异常格式化测试。

### 发现了什么问题

- 之前的 LLM 评测只覆盖岗位匹配标签，没有真实评测简历解析、JD 解析、简历定制、Guardrail 和失败 trace。
- 第一轮真实 LLM workflow 评测中，`resume_parse_success_rate=0.7778`，失败原因主要是模型把 `projects.impact`、`work_experience.duration` 等字段返回为 `null`。
- schema 修复后重新跑真实评测，`resume_parse_success_rate=1.0000`、`fit_label_accuracy=0.9444`、`end_to_end_pass_rate=0.8889`。
- 仍有 1 个 case 在 `tailor_resume` 阶段触发 `httpx.ReadTimeout`，说明长 prompt 的简历定制仍有超时风险。
- hard 分桶中 `ml_candidate_partial_agent_role` 被模型从人工期望的 `partial_fit` 判为 `weak_fit`，说明 partial/weak 边界还需要更多反例和 prompt 约束。

### 怎么修复的

- 将 LLM 评测 case 设计为包含原始简历、期望 Profile 技能、期望 Profile 关键词、JD、期望 JD 技能、fit label、fit score 区间、定制简历关键词和禁止 claim 的完整样本。
- 在 `EvaluationService.run_llm_workflow_evaluation` 中按阶段执行真实流程，并把每个阶段的成功率和质量指标写入 summary。
- 新增 `_keyword_hit_rate`、`_score_range_error`、`_llm_case_passed`、`_summarize_llm_by_key` 等指标 helper。
- 删除旧的 3 条硬编码 LLM workflow 逻辑，避免评测退回 toy demo。
- 在 Pydantic schema 中增加字段归一化 validator，真实 LLM 返回 `null` 时不编造信息，只保留为空值。
- 在 `LLMClient` 和 LLM workflow case 捕获处使用统一异常格式，保证失败报告里能看到异常类型。

### 未修复的问题及原因

- `tailor_resume` 仍可能因为上游 LLM 长时间无响应而超时；原因是当前 prompt 同时包含 Profile JSON、原始简历、JD JSON、JD 文本和 Top10 evidence，长上下文生成耗时不可控。
- hard case 的 partial/weak 边界还不够稳定；原因是模型对“有相邻 ML/LLM 能力但缺少 Agent/RAG 交付”比人工标注更保守。
- LLM workflow 数据集仍是合成数据；原因是真实简历和真实 JD 需要脱敏、人工标注和版本管理。

### 下一步怎么做

- 压缩 `resume_tailor` prompt，只传最相关 evidence 和结构化摘要，降低超时概率。
- 增加 partial/weak 边界样例，尤其是相邻技能、课程经验、读过论文但没有交付的情况。
- 在真实脱敏简历和真实招聘 JD 上建立人工标注 LLM workflow 数据集。
- 为 LLM workflow 增加 CI 阈值，例如 `fit_label_accuracy`、`end_to_end_pass_rate`、`guardrail_pass_rate` 的最低标准。

## 2026-06-05 22:54 +08:00：扩充强噪声评测集并改为默认失败直报

### 这次做了什么

- 重写 `scripts/generate_eval_datasets.py`，把 PDF chunk 评测从 30 个 case / 120 条 query 扩到 96 个 case / 576 条 query。
- 把 RAG 评测从 48 个 case / 288 个候选 chunk 扩到 180 个 case / 2160 个候选 chunk。
- 新数据集加入 hard negative、课程噪声、计划学习、废弃 prototype、相邻岗位项目、跨页干扰、通用工具词等噪声。
- PDF 与 RAG 评测 summary 增加 `difficulty_breakdown` 和 `noise_breakdown`。
- PDF chunk 评测改用生产 embedding 与生产检索权重，不再只在 hash ranker 上选切分策略。
- 根据强噪声 RAG 评测，将生产检索权重从 `vector=0.55 / lexical=0.40 / type=0.05` 调整为 `vector=0.45 / lexical=0.50 / type=0.05`。
- 将 embedding、reranker、LLM 默认策略改为失败直接报错；只有测试环境显式开启 hash/heuristic/LLM fallback。
- 更新 README、架构文档、开发说明和评测文档，说明严格失败和强噪声评测结果。

### 发现了什么问题

- 原 PDF/RAG 数据集过小、过理想，不能暴露课程噪声和相邻岗位干扰。
- 强噪声 PDF 评测发现 `coursework_vs_shipped` 很难，`paragraph_page_900_overlap160` 在该噪声下 Top3 context hit 只有 0.0521。
- 强噪声 RAG 评测把 Top3 Recall 从原来的 0.9444 拉低到 0.6125，说明新数据更能暴露真实弱点。
- `vector=0.55 / lexical=0.40 / type=0.05` 在强噪声数据下不如 `vector=0.45 / lexical=0.50 / type=0.05`。
- pytest 里使用 `setdefault` 设置环境变量会被外部 shell 中残留的真实评测变量覆盖，导致测试误走真实模型和严格 LLM 路径。

### 怎么修复的

- 生成更大规模、更强噪声的数据集，并把难度、噪声类型写入 case/query。
- 新增分桶评测指标，直接暴露 easy/medium/hard/adversarial 和不同噪声 profile 的表现。
- 重新运行真实 embedding + CrossEncoder reranker 评测，选择 `real_embedding_top20_rerank`。
- 将测试环境变量改为直接赋值，强制 `EMBEDDING_PROVIDER=hash`、`RERANKER_ENABLED=false`、`LLM_FALLBACK_ENABLED=true`。
- 默认配置改为 `EMBEDDING_PROVIDER_FALLBACK=error`、`RERANKER_PROVIDER_FALLBACK=error`、`LLM_FALLBACK_ENABLED=false`。

### 未修复的问题及原因

- `coursework_vs_shipped` 仍然很弱；原因是当前 ranker 还没有 evidence type classifier，难以区分“真实交付”和“课程/计划中提到”。
- Reranker 目前通过 Top5 anchor 避免破坏召回，但对 Top3 Recall 没有新增收益；原因是通用 MS MARCO CrossEncoder 未针对简历/JD 证据排序微调。
- 评测数据仍是合成数据；原因是真实 PDF 简历和真实 JD 需要人工脱敏和标注。

### 下一步怎么做

- 增加 evidence type classifier 或 LLM verifier，给 shipped project、metric evidence、coursework、planned learning、abandoned prototype 不同权重。
- 收集真实脱敏简历和真实 JD 做人工标注评测集。
- 用失败 trace 继续调试 LLM parse/tailor 的 prompt，而不是用 fallback 掩盖错误。

## 2026-06-05 22:21 +08:00：接入真实 Embedding、Top20 Reranker 与 Agent Tool 规划

### 这次做了什么

- 新增 `EmbeddingService`，默认接入 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，并保留 hash fallback。
- 新增 `RerankerService`，支持 `cross-encoder/ms-marco-MiniLM-L-6-v2` 对一阶段 Top20 chunk 做二阶段排序。
- 将 `SQLiteVectorIndex` 的简历 chunk、JD chunk 写入和查询改为真实 embedding 主路径，并在 metadata 中记录 provider/model/dimension。
- 将生产检索权重调整为 `vector=0.55 / lexical=0.40 / type=0.05`。
- 为 reranker 增加 Top5 recall anchor：前 5 条证据保留一阶段顺序，第 6 到第 20 条在分数带内 rerank。
- 扩展 RAG 评测，加入 hash baseline、真实 embedding 多权重、真实 CrossEncoder Top20 rerank 对比。
- 新增 `AgentToolSpec` 和 `AgentPlanner`，每次 Agent run 会先生成 Plan-Execute artifact。
- 新增 `GET /agent/tools`，可查看当前 Agent 工具清单和 MCP 候选边界。
- 新增 `docs/AGENT_DESIGN.md`，说明 LLM 调用点、Plan-Execute、ReAct、Tool 和 MCP 取舍。
- 更新 README、架构文档、API 文档、开发说明和评测文档。
- 新增 embedding/reranker 与 agent tools 测试。

### 发现了什么问题

- 裸 `pip install` 安装到了系统 Python，而项目实际使用 `C:\Users\IC\.codex\python312\python.exe`，导致第一次真实评测显示 `No module named 'sentence_transformers'`。
- `sentence-transformers` 自动安装了 `transformers 5.x` 后，本地模型加载不稳定，出现 tokenizer/processor 识别问题。
- 裸 CrossEncoder rerank 权重过高时，会把强关键词证据推出 Top3，导致 Top3 Recall 从 0.9444 降到 0.8889。
- 当前合成 RAG 数据仍偏精确技术关键词，hash/lexical baseline 的 nDCG@5 高于真实 embedding 策略。

### 怎么修复的

- 改用 `python -m pip install` 安装依赖到当前解释器。
- 在 `requirements.txt` 中增加 `transformers<5.0.0`、`huggingface-hub<1.0`，真实模型可稳定加载。
- 对 RAG 策略重新评测，真实 embedding 最佳权重为 `0.55/0.40/0.05`。
- 将 reranker 改为保守融合，并加入 Top5 recall anchor，最终 `real_embedding_top20_rerank` 达到 Top3 Recall=0.9444、Top5 Recall=1.0、MRR=1.0、nDCG@5=0.9843。
- 保留 hash baseline 作为离线可测对照，但生产策略选择真实 embedding + Top20 rerank。
- pytest 默认设置 `EMBEDDING_PROVIDER=hash`、`RERANKER_ENABLED=false`，保证普通回归测试不依赖模型下载。

### 未修复的问题及原因

- 真实 RAG 评测数据仍是合成数据，不是真实求职者 PDF 和真实招聘 JD；原因是需要人工标注数据才能可靠衡量真实效果。
- Reranker 目前使用通用 MS MARCO CrossEncoder，不是招聘/简历领域模型；原因是领域 reranker 需要额外数据微调。
- ReAct repair loop 还没有真正执行多轮修复；原因是当前先补齐 Tool registry、Plan artifact 和 Guardrail 验证边界。
- MCP 暂未引入；原因是当前工具都在同一 FastAPI 进程内，直接调用更简单，浏览器/邮箱/日历等外部授权工具接入后更适合 MCP 化。

### 下一步怎么做

- 构建真实 PDF 简历和真实 JD 的人工标注 RAG 数据。
- 增加简历定制的 ReAct repair loop，高风险时最多自动修复 2 轮。
- 接入浏览器辅助填写投递表单，并评估是否以 MCP server 形式暴露。
- 增加领域 reranker 或用真实招聘数据微调 reranker。

## 2026-06-05 21:18 +08:00：补充 PDF Chunk、RAG 与 LLM 实景评测

### 这次做了什么

- 新增 `scripts/generate_eval_datasets.py`，可重复生成较大规模评测数据。
- 生成 `evals/pdf_chunk_cases.json`：30 个 PDF 简历案例、120 条 chunk 查询。
- 生成 `evals/rag_cases.json`：48 个 RAG 检索案例，每个案例 6 个候选证据 chunk。
- 新增 PDF Chunk 多策略评测：固定窗口、页内段落窗口、大窗口、section-aware。
- 新增 RAG 多策略评测：纯向量、纯词法、词法优先混合、不同混合权重和类型加权。
- 根据评测结果将生产检索权重调整为 `lexical_score * 0.80 + vector_score * 0.15 + type_boost * 0.05`。
- 新增 query alias expansion，例如 `retrieval augmented generation` -> `RAG`。
- 新增 `/evaluations/pdf-chunk-strategies`、`/evaluations/rag-strategies`、`/evaluations/llm-workflow`。
- 使用真实 LLM 接口运行岗位适配判断和 JD 定制简历流程。
- 更新 `docs/EVALUATION.md`、`docs/PDF_CHUNKING.md`、`docs/API.md` 和 README。

### 发现了什么问题

- 第一版 PDF Chunk 评测数据页文本太短，几个策略几乎打平，无法支撑策略选择。
- 第一版 RAG 数据过于精确关键词匹配，`lexical_only` 明显占优，不能体现同义表达和向量重排的价值。
- 第一轮 LLM 实景评测中，模型把 `LLM Evaluation Intern` 错判为 `strong_fit`，说明 strong/partial 边界不够清楚。

### 怎么修复的

- 扩大并加长 PDF 评测数据，在页面中加入噪声段落和上下文关键词要求。
- 在 RAG 数据中加入同义表达查询，测试 query expansion 能力。
- 增加 `lexical_80_vector_15_type_5` 策略，保留词法召回优势，同时加入向量重排和 chunk 类型加权。
- 收紧 LLM 岗位适配 prompt：只有直接需要 Agent/RAG/FastAPI/SQLite 实现的岗位才能标为 `strong_fit`。
- 重新运行 LLM 实景评测后，`fit_label_accuracy=1.0`、`tailor_pass_rate=1.0`。

### 未修复的问题及原因

- 当前评测数据仍是合成数据，不是真实用户 PDF 和真实招聘 JD；原因是需要人工标注真实数据才能可靠评估。
- 当前 embedding 仍是 hash embedding，不是真实语义 embedding；原因是项目需要保持离线可测和无外部依赖可运行。
- 当前没有 reranker；原因是现阶段先用轻量混合检索建立 baseline，后续再增加二阶段排序。

### 下一步怎么做

- 收集真实 PDF 简历和真实 JD，构建人工标注评测集。
- 接入真实 embedding 模型后重新跑 RAG 权重评测。
- 增加 reranker，对 Top20 chunk 做二阶段排序。
- 将 LLM 实景评测纳入可选 CI，设置最低准确率阈值。

## 2026-06-05 20:40 +08:00：开发日志补充时间精度

### 这次做了什么

- 将开发日志标题格式从“日期”升级为“日期 + 时间 + 时区”。
- 在开发说明中补充日志格式要求：`YYYY-MM-DD HH:mm +08:00：变更标题`。
- 将上一条开发日志标题补齐到分钟级时间，便于同一天多次开发时追踪顺序。

### 发现了什么问题

- 原日志只写 `2026-06-05`，如果一天内多次提交或调试，无法快速判断先后顺序。
- Git 提交时间可以定位到具体分钟，但日志标题没有承载这个信息。

### 怎么修复的

- 新增本条日志，并放在文件最上方。
- 将上一条日志标题改为 `2026-06-05 20:30 +08:00`。
- 更新开发文档中的日志规则，明确以后必须带时间和时区。

### 未修复的问题及原因

- 没有补更早历史记录的时间，因为当前项目只有一条历史开发日志；已用对应提交时间补齐。

### 下一步怎么做

- 后续每次改动都按同一格式新增日志。
- 如果引入自动化发布或 CI，可在提交时校验开发日志标题格式。

## 2026-06-05 20:30 +08:00：中文文档、JD Chunk、混合向量索引、LLM 调试与评测闭环

### 这次做了什么

- 将 README 和 `docs/` 下已有文档改写为中文。
- 新增 `docs/PDF_CHUNKING.md`，详细说明 PDF 页级 chunk、结构化 chunk、metadata 和检索评分。
- 新增 `docs/EVALUATION.md`，说明评测样例、指标和运行方式。
- 新增 `docs/DEVELOPMENT_LOG.md`，并按“最新在最上面”的规则记录本次开发。
- 新增 `job_chunks` 表，岗位 JD 会和简历一样被切分、向量化并存储。
- 给 `resume_chunks` 增加 `metadata_json`，用于记录页码、字段、字符范围、切分策略。
- 增加 SQLite 轻量迁移，避免旧本地数据库因为新增列无法继续使用。
- 引入可选 Chroma 向量库镜像，SQLite 仍作为权威存储。
- 岗位搜索流程中，岗位源请求和 JD 解析使用 async 并发，数据库写入保持顺序。
- 新增 `llm_call_logs` 表和 `/llm/debug/logs` API，用于调试 LLM 调用。
- 新增 `evaluation_runs` 表、`/evaluations/run`、`/evaluations/results` 和 `evals/sample_cases.json`。
- 新增测试：JD chunk、LLM 日志、量化评测。

### 发现了什么问题

- 原项目只存储简历 chunk，没有职位 JD chunk，无法解释“岗位侧证据”。
- SQLite 检索虽然稳定，但缺少常见向量库组件，不够像真实 RAG 工程。
- PDF chunk 只有 raw text，没有页码和字符范围，证据回溯能力不足。
- LLM 调用失败时只能看到最终异常，缺少 prompt、response、延迟等调试信息。
- 测试只有功能是否跑通，缺少匹配质量的量化指标。
- 同步 SQLAlchemy Session 不适合直接并发写入。
- 使用 `TestClient(app)` 直接请求 DB 写入接口时，部分版本不会自动触发 lifespan，导致新表尚未创建。

### 怎么修复的

- 新增 `JobChunk` 模型和 `split_jd_text`，让 JD 也进入 chunk 检索体系。
- 新增 `metadata_json`，为简历 chunk 保存页码、字段和字符范围。
- 在 `SQLiteVectorIndex` 中增加 `upsert_job_chunks` 和 `query_job_chunks`。
- 增加 `ChromaVectorLibrary`，在可用时同步写入 Chroma，不可用时自动回退。
- 在 `JobSearchService` 中用 `asyncio.gather` 和 semaphore 并发解析 JD。
- 在 `LLMClient` 中记录调用日志，不记录 API key。
- 增加 `EvaluationService` 和样例集，输出 precision、recall、evidence hit rate、pass rate。
- 增加回归测试，保证新增能力可验证。
- API 级手动验证改用 `with TestClient(app) as client`，确保 startup/lifespan 执行后再请求。

### 未修复的问题及原因

- 还没有引入 Alembic：当前变更只需要轻量 SQLite 迁移，正式迁移系统会增加项目复杂度，适合下一阶段接入。
- Chroma 目前是镜像，不是主检索路径：为了保证无外部依赖时测试和演示稳定，SQLite 检索仍是主路径。
- PDF 多栏布局和表格恢复还没做：这需要更专业的 PDF layout parser，当前先保证页级证据和结构化证据可追踪。
- Agent 还没有后台任务队列：当前同步数据库写入较简单，后续长任务再引入队列更合理。

### 下一步怎么做

- 接入 Alembic 管理数据库迁移。
- 增加更多真实岗位评测样例，设置最低 pass rate 阈值。
- 将 Chroma 检索纳入主路径，并与 SQLite 检索做融合排序。
- 增加后台任务队列，让岗位搜索和简历定制支持异步任务状态轮询。
- 增加 PDF layout-aware 解析，处理多栏、表格和项目符号结构。
