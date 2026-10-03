# 第四周：规则评分接入工作台

2026-10-03。基于 `429135b` 的 RuleScorer，接入后台任务、缓存、下载和现有工作台。本节是本机验证记录，Docker 部署待执行。

## 运行行为

分析完成后，任务进入 `scoring_candidates` 阶段，先生成完整句子窗口，再提取来源匹配的真实音量并规则评分。成功任务新增 `candidate_windows.json` 和 `candidates.json`，单独下载和 ZIP 都包含这两个文件。评分失败时任务失败，不发布部分下载结果。

`GET /tasks/{task_id}/candidates?limit=20&offset=0` 提供分页展示，limit 为 1–100。响应包含 items、total、limit、offset、has_more、scorer、audio_status；每条提供排名、时间、文字、分数、原因及句子引用。API 不重算评分。未完成任务返回 409，旧任务没有候选文件返回 404。

工作台每页显示 10 条候选，支持展开评分原因、翻页、重试及下载评分文件。切换任务取消旧请求。旧任务和原切片下载继续可用；候选是推荐时间范围，当前切片仍沿用原切片策略，并不对应候选范围。尚未实现 LLM、NMS 或主题聚类。

## 缓存与部署

缓存存储格式升为 2，使用 `result-cache-v2`。评分配置和两份候选 Schema 纳入指纹；生成窗口及评分文件成为必需产物，缺失或损坏自动重算。评分使用计算指纹时的配置快照，避免运行中配置修改造成键和结果不一致。命中记录增加 scoring_candidates。`cache.json` 下载记录格式仍为 1，与内部缓存存储格式不同。

旧缓存目录不删除，旧任务仍能查看。升级后第一次处理会建立新缓存；再次提交相同内容及配置才能命中。worker 镜像新增复制 `config/scoring`，需要重建镜像。

## 验证

- 207 项后端测试通过；随后补充 scoring_candidates 失败分支并单独重跑对应测试通过。Ruff 和 diff 检查通过。
- 24 项前端测试、类型检查、生产构建通过。构建仍提示现有主包超过 500 kB，不影响构建。
- Test3 真实 small 模型：首次 275.942 秒；两次新进程复用 1.302 / 1.237 秒，转写调用次数为 1 / 0 / 0。第三次改文件名仍命中。
- 三次均 19 个候选，音量 measured，评分文件字节一致；HTTP 测试适配器分页、下载及 ZIP 检查通过。热运行中位数相对首次下降 99.54%。计时包含 worker 指纹、校验、复制、ZIP、缓存发布，不含上传、排队和浏览器。
- 浏览器使用本次生产构建和上述真实结果，核对 1–10 / 19、11–19 / 19、第一页五项原因、分页边界和评分下载入口。
- Compose 配置解析通过。Docker 引擎当时未启动，未完成 Docker/Celery 部署验收；本机预览未提供 Celery 上传处理。

证据摘要见 `samples/week4_workbench_verification.json`。19 是本次本机输入对应的结果；此前部署分析 6 句生成 14 个窗口，本机分析 7 句生成 19 个窗口，不混用来源，也不保证另一环境候选数相同。规则得分不代表人工内容质量或语义完整率。

## 部署后验收

1. 导入提交并启动 Docker Desktop，沿用 small 覆盖配置执行 `docker compose -f compose.yaml -f compose.asr-small.yaml up -d --build`。
2. 刷新工作台，重新上传 Test3。旧任务不会自动补算候选。
3. 新任务成功后查看候选时间、文字、分数、原因和分页；下载 candidates.json，确认 ZIP 内含两份候选文件，原切片仍可播放下载。
4. 再次提交相同原片及配置，确认 cache.json 的 hit=true、transcription_executed=false，reused_stages 包含 scoring_candidates。
5. 保存部署证据后进入 LLMScorer、降级可靠性、去重与两种评分对比。整周人工完整率 ≥85% 验收仍未完成。
