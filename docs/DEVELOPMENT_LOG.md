# 开发日志

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
