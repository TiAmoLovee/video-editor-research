# 第四周：可选模型评分与整批降级

2026-10-03，本机实现及离线验证。默认 `rule`；用户选择复用阿里云百炼 `qwen-plus`。新增百炼请求配置；本节记录的离线测试不代表真实请求或服务部署已成功。实际连接结果单独记录。

## 行为与契约

Scorer 注册表现在有 rule 和 llm 两个实现。worker 仍先生成并校验规则基线；启用 llm 后，对完整候选逐条请求文本评分。只有整批成功才采用模型排名，任一候选失败就整批回退规则分，避免不同标准混排。超过候选上限时也整批回退，不暗中挑选一部分。视频、音轨不发送；请求仅包含候选文字、时间、ID 和句子引用。

`prompts/scoring/llm-v1.txt` 独立版本管理，按语义完整性、信息清晰度、吸引力、少重复四个维度评审，明确转写错误与不可见画面的限制。原文被视为数据，不启用工具。响应只接受 score 和 reasons，拒绝非有限分数、越界、空理由、额外字段、重复键、Markdown 包装、拒答、截断及异常响应。

默认规则产物保留 draft.2。启用模型后使用 draft.3，保留每条 `rule_score`、`rule_reasons`、`rule_rank` 和原规则 features；模型仅能改变排序、score 和 reasons，不能改写时间、文字或来源句子。`scoring.scorer/version` 表示实际采用的评分器，`scoring.llm` 记录请求模式、配置、提示词哈希、降级原因和原始调用统计。analysis 0.1.0 不变。

页面区分“模型分”和“规则基线”，降级时解释整批使用规则。`scoring_usage.json` 随任务提供单独下载和 ZIP，区分本次调用和缓存源任务的调用记录。

## 调用限制与成本

- 默认每个候选最多 2 次尝试；仅网络/超时、429 和 5xx 重试，间隔 1 秒（配置三次时第二次退避 2 秒）。无效密钥、其他 4xx、无效响应不反复请求。
- 请求 socket 超时默认 15 秒，任务模型阶段时间预算 120 秒；发起下一请求和重试前检查截止时间。等待限速计入预算。底层 DNS/连接/响应头读取受系统与 socket 行为影响，不承诺严格实时取消。
- 共享数据目录 SQLite 协调 worker，默认 30 请求/分钟；不同数据目录不共享配额。服务商额度还可能更严格。
- 默认最多 30 个候选，每次最多 512 个输出 token，整任务本地预留 token 上限 60000；每次重试独立计入预留。预留输入按 UTF-8 字节数加协议余量估算，不是服务商 tokenizer 的准确值。
- 可配置输入/输出每百万 token 单价及 `max_task_cost`，三者须使用同一种货币单位。启用费用上限必须同时填两个单价。上限依据本地预留控制是否继续发起请求，不是服务商账单硬上限；超时或无 usage 的请求仍可能计费。真实账户额度应同时在服务商侧设置。
- 实际 usage 存在时累加 token 与按配置单价估算的费用；没有单价或存在未知 usage 时，完整成本记为未知，不能伪装成 0。无请求和缓存复用的本次成本为 0。
- 不保存密钥、原始 HTTP 错误正文或未校验的模型回复。不自动跟随重定向。外部地址必须 HTTPS，HTTP 仅允许 loopback 测试。

## 缓存行为

评分模式、模型配置、版本化提示词及哈希进入完整结果缓存指纹。成功模型结果可复用，`scoring_usage.json` 在新任务重新生成，明确本次 requests=0、cost=0，源调用记录另存。降级产物不会发布到缓存，恢复密钥或网络后会再次尝试；若旧索引异常指向降级产物，校验会拒绝。完整视频缓存模式/代码变化会重算分析，这是现有缓存粒度的限制。

## 配置和启用

1. 选择请求配置：默认 `request_profile=json-schema` 需要 Chat Completions 严格 JSON Schema 和 `max_completion_tokens` 支持。百炼 `qwen-plus` 使用 `request_profile=qwen-json`，发送 `response_format=json_object`、`enable_thinking=false`、`max_tokens`。两种配置均严格执行本地字段、数值和理由校验；JSON Object 本身不保证结构正确，失败仍整批回退。
2. 复制 `config/scoring/llm-v1.json` 为 `config/scoring/llm.local.json`，填写完整 endpoint（以 `/chat/completions` 结尾）、model、限制和费用单价。local.json 已排除 Git 与镜像构建上下文；作为只读配置挂载。
3. 密钥只设置在本机 `CLIPFORGE_LLM_API_KEY` 环境变量，不放进配置、前端、截图或聊天。Docker 仅将它传入 worker。
4. 原生 Python 使用 `CLIPFORGE_SCORER=llm` 和 `CLIPFORGE_LLM_CONFIG`。Docker 显式添加 `compose.llm.yaml`，并将 `CLIPFORGE_LLM_CONFIG_FILE` 指向本地配置。基础 compose 继续运行 rule，不会因导入代码自动开始收费。

```powershell
$env:CLIPFORGE_LLM_CONFIG_FILE = './config/scoring/llm.local.json'
# 先在本机私密设置 CLIPFORGE_LLM_API_KEY，再启用：
docker compose -f compose.yaml -f compose.asr-small.yaml -f compose.llm.yaml up -d --build
```

回到默认规则：使用原来的两个 compose 文件重新 `up -d`，worker 环境随服务配置恢复。无需删数据卷。

### 百炼预设

`config/scoring/llm-bailian.json` 不含密钥，固定使用北京地域的 `qwen-plus` 与仍受支持的 DashScope 域名；不自动换模型，以免切换到没有免费额度的模型。启用时将 `CLIPFORGE_LLM_CONFIG_FILE` 指向该文件。

预设每个候选最多一次请求，最多 512 输出 Token，30 秒请求超时、300 秒评分阶段预算、20 请求/分钟、30 个候选上限和 60000 本地预留 Token。非思考模式下 `max_tokens` 控制回答长度。单价暂为 null，不把免费额度推断为已核实的实际费用 0；是否抵扣免费额度以百炼账单为准。

使用前在百炼确认对应模型免费额度有效，并保持“免费额度用完即停”开启。HTTP 403 中的固定错误码 `AllocationQuota.FreeTierOnly` 记录为 `free_quota_exhausted`，不重试；其他 403 记录为 `access_denied`，不会一概误报为密钥无效。原始错误正文不会进入产物。

2026-10-03 核对的接口依据：[百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[百炼 Chat Completions 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[免费额度规则](https://help.aliyun.com/zh/model-studio/new-free-quota)。

## 离线重放

已下载同一任务的 analysis.json 和 draft.2 candidates.json 时，无需重新上传、转写或渲染。

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
python -m clipforge.decision.llm_cli PATH_TO_ANALYSIS.json PATH_TO_RULE_CANDIDATES.json --output NEW_OUTPUT.json
```

默认不读取密钥；空默认配置得到 missing_configuration 降级，配置模型但不启用远端得到 missing_key 降级。仅在目标与预算确认后，添加 `--config LOCAL_CONFIG.json --enable-remote` 才真实调用。不覆盖已有输入/输出。

## 验证范围

- 224 项后端测试通过；此后补充深层异常 JSON 保护，对应 14 项 LLM 测试再跑通过。26 项前端测试、类型检查、构建、Ruff 和 Compose 三文件配置解析通过。主包大小警告仍存在。
- 本机 HTTP 测试覆盖真实请求收发、401/403、429、503、重定向拒绝、异常 JSON、响应大小限制、socket 超时及关闭端口。其他单测注入部分成功后失败、重试、限速、token/费用耗尽、缺少配置/密钥、拒答和截断、缺少 usage、结果篡改与整批降级。模拟响应不是模型质量评测。
- pipeline 测试覆盖模型成功后缓存复用 0 次新调用、ZIP 用量文件一致、提示词变化失效、失败不发布缓存、修复密钥恢复成功。
- 使用已部署 Test3 的真实 analysis 与 14 个规则候选离线重放：0 次外部调用，全部候选文字、时间、来源、分数、理由保持不变。浏览器核对降级提示与原规则第一名 47.32 分通过；预览只验证候选，未重新生成视频。
- 上一阶段实际部署缓存验收已通过：207.551349 → 4.325448 秒（打包前 worker），命中同一缓存、跳过 ASR、候选文件逐字节相等；该证据不等同于本轮模型版本已部署。
- 真实模型连通性、真实 usage/费用、模型与规则质量对比、NMS/主题聚类、人工完整率 ≥85% 仍待完成。

### 2026-10-03 百炼单候选连接测试补充

用户明确允许向百炼发送 Test3 的 1.43–27.84 秒候选文字、编号和时间，进行一次请求、最多 512 输出 Token 的连接测试。实际使用与部署规则基线相同的候选，qwen-plus 返回 45 分（规则分 47.321075），本地响应结构校验通过；1 次请求、0 次重试、输入 449 与输出 329 Token，共 778 Token，约 8.047 秒。

这覆盖了上文尚未验证的真实连通性及该次 usage；实际账单/免费额度抵扣尚未核对。模型理由含对原词的猜测和未听音频却声称“语速急促”等证据不足的表述，因此不认定通过语义质量验收。规则与模型采用不同评分机制，也不能用 45 与 47.321075 的差值衡量提升。完整 14 候选的模型重放、部署、模型缓存及人工评测仍待后续完成；没有继续发起其他请求。

接口依据：[OpenAI Docs：Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)，2026-10-03 核对 `response_format`、`max_completion_tokens`。这里只声明实现的协议格式，不代选模型或承诺第三方兼容性。
