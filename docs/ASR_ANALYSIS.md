# 第三周第四步：词级转写、中文分句与完整分析汇总

日期：2026-09-28。ASR 基线 `d490222` 已提交并通过[远程检查](https://github.com/TiAmoLovee/video-editor-research/actions/runs/36397131288)。随后根据用户人工试听，新增简体输出及转写前语音过滤修复；修复后的用户 Docker 验收待执行。下文旧实验数字均对应各自版本。

## 本次交付

归一化 → 镜头 → WebRTC VAD → faster-whisper 转写 → 完整分析校验 → 固定帧数切片 → 下载。新增 `transcribing`（60%）、`combining_analysis`（65%），切片阶段调整为 70%。页面用“分析”步骤归纳镜头、语音检测、转写及校验，当前动作仍显示具体名称。

新增下载：

| 文件 | 内容 |
| --- | --- |
| `asr.json` | 原始模型分段、词条、句子、语言判断、模型身份、参数及时间处理记录 |
| `analysis.json` | 原文件与归一化文件哈希、三路分析器信息、镜头、语音/非语音、词和句子 |

两份文件同时进入 ZIP。旧任务没有新结果时不出现新下载入口。成品继续按最多 900 帧切片，尚未使用语义选段。模型识别、时间和文字均须人工核对，页面为新转写任务显示这一提示。

## 模型与依赖

- `faster-whisper==1.2.1`，CTranslate2 4.6.0，CPU int8，4 个线程。采用较小的多语言 **base 基线模型**，不要求显卡。
- 模型为 `Systran/faster-whisper-base`，固定 revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`。四个必要文件的 SHA-256 写在 model.py，加载前校验。模型包附上 Whisper MIT 许可。
- 依赖固定包含 PyAV、tokenizers、ONNX Runtime、huggingface-hub；jsonschema 从开发期校验扩展到 worker 发布前校验。
- 本机默认模型目录为仓库的 `models/faster-whisper-base`。Docker worker 只读挂载 `./models:/models:ro`；模型不放入 Git，也不进入 Docker 构建上下文。
- 模型在任务运行时只从本地加载，不自动联网。模型缺失或校验失败会在转写阶段失败；无音轨、数字全零音频不需要启动推理。
- 单进程复用模型对象，减少重复初始化。这不是分析结果缓存，不代表“缓存加速 80%”已经实现。

## 时间、文本及分句规则

- 沿用 VAD 的音频提取逻辑：16 kHz 单声道 PCM，保留音视频相对偏移，补齐/裁切到归一化视频时间轴。ASR 启用 Silero 语音过滤；词级时间由 faster-whisper 还原到完整视频时间轴。`vad.json` 仍为独立的 WebRTC 检测结果，两种检测器可能不同。
- 默认自动判断语言；已知语言可设置 `CLIPFORGE_ASR_LANGUAGE=zh` 或 `en`。使用转写而非翻译模式。中文的公开词条、句子通过 OpenCC t2s 转为简体；原始识别文本保存在 `raw_segments`。
- 启用 `word_timestamps=True`，明确消费完整结果生成器。词条是模型输出的单位，中文可能为单字或组合词，并不保证语言学分词。
- 当前采用分句规则 v2：同时保护中文词语与模型词条边界，优先使用标点和停顿，50 字符/15 秒改为软目标并允许有限前瞻。每个词仍恰好被一个句子引用，词条内容与时间不改；详见 [SENTENCE_RULES.md](SENTENCE_RULES.md)。早期实验记录的句数对应旧规则。
- 零时长词条并入相邻有时间的词条，保留文字，合并后的概率设为 null；原始结果和调整数量留在 asr.json。只有零时长文字却没有可用时间锚点时明确失败。不凭空创造独立词时长。
- 允许最多 50 ms 的边缘越界被截到视频边界，并记录次数；更大越界、无效概率、NaN/Inf、时间倒序、缺失词时间戳均失败。
- 无音轨时 ASR 为 `no_audio`，词句为空；有音轨但 PCM 采样全部为零时为 `ok`，跳过推理并记录 `digital_silence_skipped=true`。非零音频再运行 Silero：未检测到语音时不加载 Whisper，返回空词句并记录 `speech_gate.no_speech_skipped=true`。没有按音量或重复词黑名单硬删文字；模型仍可能误判轻声、音乐或歌声。

完整汇总会核对三路的归一化文件哈希、时间基准、帧数、时长、音轨状态，再检查 Schema 和跨字段规则。失败任务不会发布下载文件。当前保留 `0.1.0` 草案；正式服务验收后再决定冻结版本，不改写旧分析文件。

## 本地验证与已知质量问题

- 114 项后端测试、14 项前端测试、类型检查、生产构建、Ruff、pip check、Compose 配置解析通过。Vite 保留既有的大于 500 kB 主包提示。
- 真实模型与媒体实验覆盖无音轨、静音、Test1 前 30 秒及公开中文语音样例。均经过上传、处理、独立下载、ZIP 与完整契约校验；队列投递为替身。
- Test1 前 30 秒：自动语言为英语，35 个词条、6 句。只检查了该切片，未把完整 345.5 秒视频重新转写。
- 公开中文样例封装为 7.33 秒视频，自动语言为中文，12 个词条、1 句。输出含疑似错字，没有人工逐词校准，不能作为准确率达标证据。音频来源与哈希写入实验记录，不将其计入实习要求的真实视频评测集。
- Sucai1 实验虽通过结构和时间范围检查，但输出存在大量重复文本，91 个零时长词被合并；它作为质量问题记录保留，不能当作合格转写。
- 首次纯静音实验也生成了文字，已用数字全零检查修复并补上回归测试。普通噪声或音乐的幻觉问题仍需评测与后续模型/过滤策略改进。

证据见 [asr_analysis_verification.json](samples/asr_analysis_verification.json)。本轮没有新版 Docker/Celery、浏览器视觉、人工词级误差 ≤0.3 秒、镜头 F1、至少 10 条真实评测集或缓存加速验收。不能以“测试通过”替代上述验收。

## 导入后操作

代码包与模型包都从项目根目录解压。模型包内路径是 `models/faster-whisper-base/`，不会覆盖代码。使用现有 Python 3.11 虚拟环境：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m clipforge.analysis.model --check
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

模型包已下载好时，检查模型不会联网。没有模型包时可执行 `python -m clipforge.analysis.model` 从官方源下载，约 148 MB；必要时使用自己的有效 HTTPS 代理，不修改系统代理设置。已有目录会先校验，不自动覆盖损坏文件。

本机完整媒体实验需设置 FFmpeg/FFprobe，可执行：

```powershell
.\.venv\Scripts\python.exe backend/tests/integration_asr.py --video "你的语音视频.mp4" --output artifacts/asr-local-check.json
```

输出须为新文件；`--video` 可重复。该脚本使用真实模型，含语音样例未产出词条会提示检查素材；不计算准确率。

前端在 frontend 下按既有命令执行 typecheck/test/build。通过后，等待现有任务完成，再 `docker compose up -d --build`。重建时会安装新依赖，耗时可能增加；模型通过目录挂载，不在镜像构建时下载。

Docker 内确认：

```powershell
docker compose exec worker python -m clipforge.analysis.model --check
```

刷新工作台，重新上传一条有清晰讲话的素材，等待任务成功，下载“词级转写与分句”和“完整分析结果”。旧任务不自动补算。再取无音轨/静音素材及历史任务进行回归。需要指定语言时，Docker Compose 在仓库 `.env` 中设置 `CLIPFORGE_ASR_LANGUAGE=zh` 或 `en` 后重新创建 worker；本机 Python 则在终端设置同名环境变量。

## 参考

- [faster-whisper 官方说明](https://github.com/SYSTRAN/faster-whisper)：词级时间、生成器、CPU int8 和本地模型加载。
- [base 模型与固定版本](https://huggingface.co/Systran/faster-whisper-base/tree/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66)。
- [公开中文示例的来源](https://huggingface.co/FunAudioLLM/SenseVoiceSmall/tree/main/example)：仅借用中文音频做 smoke test，本项目未接入 SenseVoice 模型。


## 实际服务验收补充（2026-09-28）

前文实验记录对应开发阶段；以下为用户项目及实际服务的后续证据。

- 用户环境：依赖安装成功、pip check 无冲突、模型四个文件校验通过、114 项后端测试和 Ruff 通过。
- 用户提供的构建输出显示 API/worker 镜像构建并启动成功、Redis healthy；容器内模型校验输出 PASS: /models/faster-whisper-base。
- 任务 96231ccc-97d6-41ed-b132-8ff83be6d98e，素材 Test3.mp4，状态 SUCCEEDED，72.6 秒、2178 帧，切片清单为 900/900/378 帧。
- 实际结果为 4 个镜头、49 个语音区间、50 个非语音区间、217 个模型词条、9 个句子；自动语言判断为中文。ASR 记录耗时 16.466007 秒，该值不是整条任务耗时，也不是缓存加速结果。
- analysis.json 通过 JSON Schema 与跨字段校验；按照当前分句规则重算结果一致；词条引用完整，时间边界有效。
- shots/vad/asr 的媒体标识与完整汇总一致，各组件内容和分析器信息与汇总相等。用户提供的 asr.json、analysis.json 与 HTTP 下载、ZIP 内容完全一致；ZIP CRC 和文件清单通过。
- 旧 VAD 任务 ee2cb03c-9298-499e-9d4f-f16cc7811b32 仍可查询和下载 vad.json，没有自动补算 analysis.json；本次未重新下载旧 ZIP。

质量边界：文本仍有疑似错字和重复，未通过原音频人工复核。9 个零时长词条被合并、0 个边界截断，原始结果与调整记录可追溯。没有词级人工时间参考，不能声明 ≤0.3 秒达标。没有重新逐段解码或目视播放，也未独立检查 Docker 内部版本/队列日志；容器模型校验来自用户输出。原文件与归一化视频哈希仅核对各分析文件中的一致性，未重新读取原始媒体计算。

本次结论：实际服务的分析、汇总和下载流程验收通过，识别质量与第三周完整指标继续评测。Schema 仍为 0.1.0 草案。证据见 [asr_deployed_verification.json](samples/asr_deployed_verification.json)。
