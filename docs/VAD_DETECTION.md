# 第三周第三步：语音活动检测

日期：2026-09-28。基于镜头模块提交 `3549fc4`；该提交的 [GitHub Actions](https://github.com/TiAmoLovee/video-editor-research/actions/runs/36380624114) 已通过。本次 VAD 代码尚待导入、提交和远程检查。

## 当前行为

流程：归一化 → 镜头检测 → **语音活动检测** → 固定 900 帧切片 → 打包。新阶段 `analyzing_speech` 的进度为 55%。新增独立下载 `vad.json`，ZIP 同时包含它。旧任务没有该文件时，前端不显示对应链接。

本步产出 VAD 的语音/非语音区间，不生成转写文字，也不按语音区间改变切片。完整 `analysis.json` 仍需接入 ASR 后汇总，草案 Schema 暂不冻结。

## 算法与时间轴

- 固定依赖 `webrtcvad-wheels==2.0.14`，提供 Windows / Linux Python 3.11 的二进制包；导入名为 `webrtcvad`。无需额外下载模型。
- 默认 mode=2、30 ms 帧。FFmpeg 将第一条音轨转换为 16 kHz、单声道、16-bit PCM；逐帧读取临时 WAV，处理后清理，不将整段音频载入内存。
- 视频区间为 `[0, 总视频帧数 / 30)`，与镜头分析一致。读取视频流原始起始时间；`-copyts` 保留原始时间戳，再减去视频起点，使用 `aresample` 的 `first_pts=0` 对齐。音轨晚开始时补前部，提前结束时补尾部，超出视频的音频截去。
- 用整数采样数推进；最后不足一帧时补零供检测，输出结束时间严格截在视频末端。只合并相邻同类帧，不添加额外平滑、最小语音时长或跨静音合并规则。
- `speech` 表示检测器判为语音；`silence` 表示未判为语音，可能含音乐、噪声，不代表物理上完全无声。
- 有音轨时，两组区间合并后无空隙、无重叠，覆盖全片。无音轨时 `has_audio=false`、状态 `no_audio`，两组均为空；有音轨但全静音时状态 `ok`、`speech=[]`、`silence` 覆盖全片。
- 提取失败、超时、采样数不符、PCM 被截断或检测器异常都会使任务失败，不发布空结果作为成功。

输出为 `schema_version=0.1.0`、`artifact_kind=vad_analysis`、`result_kind=measured`，包含视频哈希、帧数、时间基准、音轨存在状态、工具版本、参数、区间和耗时。此独立文件并非完整 analysis Schema 的实例。

## 本地验证

- 95 项后端测试通过，包含 11 项新增测试；覆盖真实 WebRTC 静音判断、10/20/30 ms 帧、尾帧、区间合并、无音轨、异常传播以及流水线失败阶段。
- 12 项前端测试、TypeScript 类型检查、Vite 生产构建、Ruff、pip check 通过。Vite 仍有既存的主包大于 500 kB 提示；本轮未做浏览器视觉验收。
- 真实 FFmpeg/WebRTC 实验：无音轨与静音视频各跑一次完整本机流水线；音轨延迟 1 秒并提前结束的夹具，分别验证视频起点为 0 秒和 5 秒的对齐。正弦波夹具只检查时间位置，不用作人声准确率标签。
- `Sucai1.mp4`：1238 帧、41.2666667 秒，VAD 判出 16 个语音区间和 15 个非语音区间；维持 900+338 帧两段切片。区间覆盖、镜头/VAD 哈希和时长一致性、独立下载与 ZIP 中 JSON 一致性均通过。
- 完整实验记录见 [vad_verification.json](samples/vad_verification.json)。使用本机 Python、FFmpeg 和 FastAPI TestClient，队列投递为替身；不代表 Docker/Celery 新版部署已验证。

这些结果证明流程与边界处理通过检查，不证明 VAD 准确率达标。真实素材仍缺人工语音标签。ASR、中文分句、缓存及至少 10 条真实素材的镜头 F1 评测仍待完成。

## 在项目环境复测

从项目根目录执行，使用已有虚拟环境：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py" -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

前端在 `frontend` 下执行 `npm ci`、`npm run typecheck`、`npm test`、`npm run build`。首次安装依赖需联网。

本机媒体实验需 FFmpeg/FFprobe 在 PATH 中，或设置 `FFMPEG`/`FFPROBE` 为实际程序路径：

```powershell
.\.venv\Scripts\python.exe backend/tests/integration_vad.py --output artifacts/vad-local-check.json
```

可加 `--video "你的素材路径.mp4"`；记录文件必须是新文件。独立处理已归一化视频：

```powershell
.\.venv\Scripts\python.exe -m clipforge.analysis.vad normalized.mp4 --output artifacts/vad.json
```

## Docker 页面验收

等待当前任务结束，在项目根目录运行 `docker compose up -d --build`，然后 `docker compose ps`。刷新工作台，上传一条新视频；旧任务不会自动补算 VAD。

任务成功后展开“处理记录与参数文件”，应有“语音活动分析结果”；下载该 JSON 和 ZIP，检查两份 `vad.json` 一致。另取无音轨和静音素材，检查上述两类状态。确认历史任务仍可查询、下载。全部通过后再记录部署证据、提交并推送。

## 参考实现与文档

- [WebRTC VAD Python 接口](https://github.com/wiseman/py-webrtcvad)：支持的 PCM 格式、帧长和模式。
- [webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels)：二进制分发。
- [FFmpeg aresample](https://ffmpeg.org/ffmpeg-filters.html#aresample) 与 [重采样选项](https://ffmpeg.org/ffmpeg-resampler.html#Resampler-Options)：时间戳对齐与首采样位置。


## 实际服务验收补充（2026-09-28）

上述“尚待部署”为本地开发完成时的状态。本次通过现有 localhost:8200 服务读取新任务并校验下载，补充证据如下：

- 任务 ee2cb03c-9298-499e-9d4f-f16cc7811b32，源文件 Test1.mp4，状态 SUCCEEDED。
- 10365 帧、345.5 秒；切片清单为 11 段 900 帧加 1 段 465 帧，共 12 段。
- VAD 文件报告 WebRTC VAD 2.0.14、mode=2、30 ms 帧；406 个语音区间，共 131.79 秒，407 个非语音区间，共 213.71 秒。区间连续覆盖全片，无空隙或重叠。
- 用户提供文件、HTTP 独立下载和 ZIP 内 vad.json 完全一致；shots.json 独立下载与 ZIP 一致，镜头/VAD 的视频哈希、帧数和时长一致；ZIP CRC 和文件清单通过。
- 上一步旧任务 28194bd5-46e0-4344-ae2d-d28e06e601e7 仍可查询并下载 shots.json，没有被补算 vad.json。本次未重新下载旧 ZIP。
- 用户项目环境 pip check、95 项后端测试和 Ruff 已通过；本次代码尚待提交推送，不能沿用镜头模块的 CI 结果作为 VAD 的远程检查结果。

这是流程和数据一致性验收，尚无人声人工标签，不能判断 406 个语音区间均识别正确。未重新解码或目视播放切片，也未直接检查容器版本、队列日志。本次证据见 [vad_deployed_verification.json](samples/vad_deployed_verification.json)。
