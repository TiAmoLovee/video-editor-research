# 分析结果数据契约（第三周冻结基线）

冻结日期：2026-09-29。数据版本：`0.1.0`，状态：**已冻结**。对应需求 FR-03。

镜头、VAD、词级转写及中文分句已接入流水线。最终 analysis.json 发布前同时进行 JSON Schema 与跨字段校验。冻结现有字段和时间语义，不将已部署的 0.1.0 输出改写为 1.0.0。

冻结凭据：[schema-freeze.json](evaluation/week3-closeout/schema-freeze.json)。Schema 历史 title 中的 draft 字样保留；当前状态以本文与冻结凭据为准，避免仅修改说明就改变已部署缓存身份。语义哈希按解析后 JSON 规范排序计算，不受 Git 换行转换影响。

## 兼容与变更规则

- 0.1.0 字段、类型、必填项、枚举、区间定义及跨字段规则冻结；未来修改需新版本、迁移说明和新旧数据校验。
- Schema 为封闭对象的地方禁止擅自增加字段；可扩展参数仅限当前 Schema 已允许的区域。
- 模型、参数、规则变化记录在 analyzers 中；结构兼容不表示预测内容、准确率或耗时一致。
- 冻结范围为 analysis.json；shots.json、vad.json、asr.json 是组件输出，cache.json 使用独立 format_version，不冒称全部由本 Schema 校验。
- 所有旧下载与现有示例保留；示例 result_kind=synthetic_example 不充当真实测量。

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

三路中任一路执行失败时，不能用空数组伪装成功。本冻结版本只描述成功或无音轨跳过的最终结果，
失败原因由现有任务状态记录；未来如增加部分成功协议，必须另行版本化。

## 流水线与缓存

实际流程：归一化 → 镜头/VAD/ASR → 分句及汇总校验 → 固定时长切片。参数按实际生效值记录。失败任务不发布伪成功分析。

结果缓存按内容、选项、代码、Schema、模型与依赖身份区分，详见 [RESULT_CACHE.md](RESULT_CACHE.md)。缓存身份变化后重算；冻结数据结构不冻结模型。

## 校验与验收

从项目根目录设置 PYTHONPATH=backend，执行：

```powershell
.\.venv\Scripts\python.exe -m clipforge.analysis.validation docs\samples\analysis.example.json --schema schemas\analysis.schema.json
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p 'test_*.py'
```

本地检查与部署证据、限制见 [第三周验收报告](WEEK3_ACCEPTANCE.md)。远程 CI 必须由对应提交的运行结果确认。
