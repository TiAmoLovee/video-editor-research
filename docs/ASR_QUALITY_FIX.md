# 简体输出与音乐误转写修复

日期：2026-09-28。基线：d490222；该基线的远程检查已通过。此修复基于用户的 Test3 人工核对，不代表第三周精度指标全部达标。

## 问题和处理

用户要求第二段使用简体中文，确认旧结果第八段（60.40–70.62 秒）和第九段（71.78–72.58 秒）实际为音乐、没有人声。此前流水线虽然保存 WebRTC VAD 结果，但没有对 ASR 输入做语音过滤，Whisper 仍识别完整音轨。

- ASR 增加 Silero VAD 前置检测，使用 faster-whisper 1.2.1 自带的本地 `silero_vad_v6.onnx`，无需另下模型。全部非语音时直接输出空词句，不加载 Whisper。
- 有语音时使用 faster-whisper 内置过滤及时间还原。固定 threshold=0.5、neg_threshold=0.35、min_speech_duration_ms=0、min_silence_duration_ms=500、speech_pad_ms=200。预检测与转写使用相同参数；没有按 Test3 时间点或重复词定制删除规则。
- `asr.json` 记录过滤器模型哈希、参数、保留区间及是否跳过；所有公开词级时间仍以完整归一化视频为基准。
- 中文输出通过固定依赖 `opencc-python-reimplemented==0.1.7` 的 t2s 字典转换。按完整词条序列处理，兼顾跨词条词组，随后映射回原 ID/时间；转换若改变字数则明确失败，避免错误映射。`raw_segments` 保留原始文字，公开 `words`、`sentences` 和 `analysis.json` 使用简体。
- `vad.json` 的 WebRTC 算法本轮未替换，不能用 ASR 过滤结果宣称 WebRTC 对音乐也已识别准确。Schema 仍为 0.1.0 草案。

## 验证与边界

自动测试覆盖非零但无语音时跳过 Whisper、过滤器失败传播、中文词组转换、原始文字保留及时间映射。真实音频验证和计数见 `samples/asr_quality_verification.json`。

可重复运行 `backend/tests/integration_asr_quality.py --no-speech-video 音乐视频.mp4 --speech-video 讲话视频.mp4 --output artifacts/asr-quality.json`。两类视频参数均可重复；类别应经人工试听确认，输出必须为新文件。按既有配置设置 FFmpeg、FFprobe 和本地模型目录。整条流水线的结构与下载验证继续使用 `integration_asr.py`。

Test3 测试输入由旧任务下载包的三个切片按顺序重新拼接。重编码、音频切片边界可能影响结果；它不是原始 Test3 的逐字节副本。后续必须在用户 Docker 服务中重新上传原始 Test3 复验。

VAD 可以减少无讲话时的误转写，但并不保证所有音乐都不会被误判；歌声、多人重叠和轻声仍需评测。音量降低样例保留文字只能证明该样例未全部丢失，不能证明所有轻声或每个词均被保留。简体转换不能纠正模型原有的错字。新转写使用不同音频上下文，前段文字也可能变化，需要复听。未完成文字准确率、人工词级 ≤0.3 秒、镜头 F1 或缓存加速验收。

## 导入与复验

1. 在项目根目录导入修复 ZIP，安装 worker 依赖新增的 OpenCC；可以用随交付提供的官方 wheel 离线安装。
2. 运行后端单元测试、Ruff 和 pip check。
3. 等待正在执行的视频任务完成，再 `docker compose up -d --build`；现有 base 模型继续使用，无需重新下载。
4. 重新上传原始 Test3。旧任务结果不会自动改写。
5. 下载新任务的 asr.json、analysis.json。检查公开词句为简体；旧第八、九段对应的音乐时间范围内应无转写文字；复听前面的讲话是否被遗漏。
6. 新句子的分组和编号可能改变，按时间范围比较，不要求仍有九段。

参考：[faster-whisper VAD 官方说明](https://github.com/SYSTRAN/faster-whisper#vad-filter)、[OpenCC Python 项目](https://github.com/yichen0831/opencc-python)。
