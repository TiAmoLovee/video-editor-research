# 第三周第一步：统一分析结果

日期：2026-09-28。版本：`0.1.0` 草案。对应需求 FR-03。

阶段说明：本文保留第一步的交付范围。后续镜头模块已接入并单独输出 shots.json，见 [镜头检测说明](SHOT_DETECTION.md)；VAD 已接入并独立输出 vad.json，见 [语音活动检测说明](VAD_DETECTION.md)。三路已在本机接通并在发布前校验，见 [ASR 与汇总说明](ASR_ANALYSIS.md)。本次仍待正式服务验收。

本次交付正式 JSON Schema 文件、手工示例、开发与运行时校验器和测试。
第一步交付时三路真实分析尚未接入。现已完成本机三路冒烟验证，当前保留 0.1.0 草案；完成正式服务验收后再决定冻结为 1.0.0。
示例不代表模型输出、精度结果或缓存验证结果。

## 文件怎么读

| 文件 | 用途 |
| --- | --- |
| `schemas/analysis.schema.json` | 字段、类型、必填项、枚举和静态取值范围 |
| `docs/samples/analysis.example.json` | 人工编写的 10 秒示例，`result_kind` 为 `synthetic_example` |
| `backend/clipforge/analysis/validation.py` | 检查 Schema 和跨字段规则；也可在终端独立运行 |
| `backend/tests/test_analysis_contract.py` | 验证合法样本及错误时间、引用、无音轨等情况 |

## 字段说明

| 字段 | 表示什么 |
| --- | --- |
| `schema_version` | 数据格式版本，本版固定为 0.1.0 |
| `result_kind` | `measured` 表示真实运行结果；`synthetic_example` 表示人工示例 |
| `media.source_sha256` | 原始上传文件的内容哈希，不依赖文件名；示例中的全零值只是占位 |
| `media.normalized_sha256` | 实际参与分析的归一化视频哈希；示例中的全一值只是占位 |
| `media.duration_seconds` | 归一化视频时长，单位秒 |
| `media.time_reference` | 固定为 `normalized_video`，所有结果使用同一个时间原点 |
| `media.has_audio` | 归一化媒体是否存在音轨；不能凭 VAD 没检测到语音就写 false |
| `analyzers` | shots、vad、asr 各自的状态、工具名、版本、模型和参数 |
| `shots` | 连续镜头区间，完整覆盖视频 |
| `speech` | VAD 判定为有语音的区间 |
| `silence` | VAD 未判定为语音的区间，可能含音乐或环境声，不等同于物理上完全无声 |
| `words` | ASR 词条的 ID、原文本、起止时间和可空的概率 |
| `sentences` | 中文分句文本、起止时间，以及对应的 `word_ids` |

中文 ASR 的“词”是模型实际返回的时间戳单元，不承诺等于一个汉字。
分句可以补充标点，但必须保留与原始词条的关联，不能凭空插入带时间戳的“识别词”。
`probability: null` 表示没有该值，不能填造出来的置信度。

## 时间与一致性规则

- 所有时间均从归一化视频的 0 秒起算，区间为 `[start, end)`，结束点不属于该区间。
- 所有区间要求 `0 <= start < end <= duration_seconds`，按 start 升序保存。
- shots 不可有空隙或重叠，应完整覆盖视频；没有切镜头时，输出覆盖全片的一个镜头。
- 有音轨时，speech 与 silence 合起来完整覆盖音轨对应的视频时间范围，二者不重叠。
  后续提取音频时必须对齐视频时间原点，并将短音轨的尾部补齐至视频时长。
- 无音轨时，VAD 与 ASR 状态为 `no_audio`，speech、silence、words、sentences 全部为空。
  这与“存在音轨，但全程没有语音”不同；后者状态为 ok，silence 覆盖全片。
- 词 ID、句子 ID 各自唯一；所有词必须按原顺序、恰好一次地分配给句子。
- 句子时间范围等于其引用词条的最小开始、最大结束时间。句子不得引用不存在的词。
- 保留 ASR 的重叠词时间；VAD 和 ASR 相互独立，不强制词时间落在 VAD 的 speech 中。
  两路不一致时，应作为后续人工复核和评测的线索。
- NaN、Infinity 在任何层级均不可接受。分区边界允许最多 1 微秒的浮点表示误差。

JSON Schema 检查数据结构；标准 Schema 不直接比较任意两个字段的数值。
所以仅通过 Schema 不代表时间合法，必须同时运行 `validate_analysis` 的跨字段校验。

三路中任一路执行失败时，不能用空数组伪装成功。本草案只描述成功或无音轨跳过的最终结果，
失败原因由现有任务状态记录；实际接入时再决定是否增加部分成功协议。

## 模块边界和后续缓存设计

计划在现有 `归一化 -> 固定切片` 之间加入分析阶段，输出 `analysis.json`。
本次没有修改任务流水线、下载接口、Docker 服务或前端。

模型和参数应记录实际采用的配置，包括默认参数，不能只保存用户修改过的值。
ASR 后续还需记录中文分句实现版本，以便区分同一词表经过不同分句逻辑产生的结果。

缓存尚未实现。计划使用内容哈希、归一化配置、分析工具/模型版本、全部有效参数、分句版本、Schema 版本
共同确定缓存身份，不能仅凭同名文件或任务编号命中。
需验证同一视频二次提交不执行转写，并分别记录分析阶段与端到端耗时；本次不声称达到 80% 降耗目标。

## 本地检查

从仓库根目录执行，先安装新增的开发依赖，再运行示例校验和全部后端测试：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\.venv\Scripts\python.exe -m clipforge.analysis.validation docs\samples\analysis.example.json --schema schemas\analysis.schema.json
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

成功时校验器输出 `VALID` 并注明 synthetic_example；错误数据输出 `INVALID`，退出码为 1。
`jsonschema` 当前只加入开发依赖，生产任务没有调用此校验器；后续接入 worker 时需补齐运行依赖及 Schema 的部署路径。
现有 CI 会自动发现新测试。远程 CI 必须推送后另行查看，不能由本地结果替代。

## 本周下一步

1. 接入 PySceneDetect，先对一个真实短视频输出 shots，再对比人工切点。
2. 准备至少 10 条自有或已获授权素材，访谈、课程、Vlog 每类至少 3 条，并开始人工镜头标注。
3. 后续接入 VAD、faster-whisper 和中文分句，再完成缓存及首轮评测。
