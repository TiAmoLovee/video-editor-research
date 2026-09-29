# 第三周第二步：PySceneDetect 镜头检测

日期：2026-09-28。基线提交：a31f90b。该基线的 [远程 CI](https://github.com/TiAmoLovee/video-editor-research/actions/runs/36369764883) 已成功；本次新增代码尚未推送，不能沿用该结果。

## 本次接入

现有流程增加镜头阶段：上传 -> 归一化 -> 镜头检测 -> 固定切片 -> 下载。

- `backend/clipforge/analysis/shots.py` 封装 PySceneDetect ContentDetector。
- 仅对归一化的 H.264 / CFR 30 fps 视频进行检测，逐帧解码，不把整段视频加载进内存。
- `analyzing_shots` 阶段进度为 45%；成功后才继续原有切片与打包。
- `shots.json` 同时提供独立下载和 ZIP 内副本，页面有“镜头分析结果”入口。
- 旧任务没有镜头结果时隐藏新入口，仍保留原下载；原固定 30 秒切片逻辑不变。
- 没有镜头切换时返回全片一个镜头，单帧视频也有一段；视频帧位置与检测器实际处理计数交叉检查，漏帧、异常尺寸或不完整解码不得伪装为完整成功。
- 分析失败时任务在对应阶段标记 FAILED，不继续切片，不发布结果下载。

## 输出如何理解

`shots.json` 的 `artifact_kind` 是 `shot_analysis`，目前不作为完整 `analysis.json`。
VAD 和 ASR 尚未执行，不能填空数组声称它们成功。后续可将这里的 `shots` 和 `analyzer` 分别合入 analysis 的 shots 与 analyzers.shots。

| 字段 | 含义 |
| --- | --- |
| `media.normalized_sha256` | 实际参与分析的视频内容哈希 |
| `media.total_frames` / `fps` | 完整帧数及固定帧率 30 |
| `media.duration_seconds` | 按视频帧数除以帧率计算；不使用可能受 AAC 尾部影响的容器总时长 |
| `media.time_reference` | 所有时间均相对归一化视频的 0 秒 |
| `cut_frames` | 镜头切换帧，0 基索引；不把视频开头和结尾算作切点 |
| `shots` | 按时间排列的 `[start, end)` 秒区间，完整覆盖视频，无空隙或重叠 |
| `analyzer` | 检测器、版本、阈值、最小镜头间隔及其他实际参数 |
| `decoder` | 解码后端版本和失败重试设置；本版不跳过坏帧继续成功 |
| `elapsed_seconds` | 本次镜头模块耗时，包含读取媒体信息和计算哈希，不等于整个任务耗时 |

例如 `cut_frames: [60, 120]` 在 30 fps 下表示第 2 秒和第 4 秒切换画面。
当前默认 threshold=27、min_scene_len=15 帧（0.5 秒）、downscale=1、frame_skip=0。
阈值不是概率；调参时应结合人工镜头边界记录实际变化。

## 依赖选择

固定 PySceneDetect 0.6.7.1、opencv-python-headless 4.12.0.88、NumPy 2.2.6，及配置文件列出的辅助依赖。
使用官方提供的 opencv-headless extra，适合没有图形桌面的 worker；不同时安装两种 OpenCV 包。
该 PySceneDetect 版本要求 Click <8.3，因此把原来的 Click 8.5.0 调整为 8.2.1，并在 API 与 worker 依赖中保持一致。
本次本地依赖兼容性检查通过；用户已完成 Docker 重建；正式服务的任务与下载验证见下方补充记录，未独立读取容器内完整依赖清单。

实现参考官方 [0.6.7.1 API](https://www.scenedetect.com/docs/0.6.7/api.html) 和 [ContentDetector](https://www.scenedetect.com/docs/0.6.7/api/detectors.html)。本项目固定版本，不假设未来升级无需修改。

## 已验证的范围

- 84 项后端测试通过，包括真实 MJPG 解码、已知切点、无切点、单帧、中文路径、异常帧、参数校验、失败状态、ZIP 与独立下载。
- 10 项前端交互测试、TypeScript 检查、Vite 生产构建通过。包括镜头阶段显示、新结果入口及旧任务兼容；没有完成本轮浏览器目视验收。
- Ruff、当前本地环境的 pip check、Compose 配置解析通过。
- 原生 Windows/Python 3.11.9，使用实际 FFmpeg 和 OpenCV 跑通两次媒体流程，详见 [机器可读记录](samples/shot_detection_verification.json)。

| 素材 | 结果 | 证据边界 |
| --- | --- | --- |
| 31 秒人工合成黑/白/黑视频 | 检测到第 60、120 帧，分成 3 个镜头；成品仍为 900+30 帧两段 | 已知切点的功能验证，不计入 ≥10 条真实评测集 |
| 第二周使用过的 Sucai1.mp4 | 1238 帧，检测到 271、587、607 帧，共 4 个镜头；原切片仍为 900+338 帧 | 无人工镜头标注，仅确认流程与输出，不据此计算 F1 |

这两次验证使用真实上传/查询/下载接口的 TestClient 和实际媒体处理函数，但将队列投递替换为测试替身，直接调用流水线。
它不等于 Docker/Celery 联调通过。所有数据都写入隔离临时目录，未替换正式服务的数据卷。
本次没有验证长视频新增分析阶段的耗时、峰值内存、处理中断恢复、缓存或 F1。
镜头模块暂未增加独立的强制超时；后续长视频工程加固需补充超时与恢复实验。

## 本地复现

在项目根目录执行（依赖安装必须同时包含开发与 worker 清单）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt -r backend\requirements-worker.txt
.\.venv\Scripts\python.exe -m pip check
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m ruff check --no-cache .
```

若终端找不到 FFmpeg/FFprobe，按 README 设置 FFMPEG、FFPROBE 环境变量。
自建合成视频并验证实际媒体流程：

```powershell
.\.venv\Scripts\python.exe backend\tests\integration_shots.py --output downloads\shot-verification.json
```

可加 `--video` 指定自备视频；输出文件必须不存在，重复实验要换一个记录名。
已有归一化文件可单独检测：

```powershell
.\.venv\Scripts\python.exe -m clipforge.analysis.shots normalized.mp4 --output downloads\shots.json
```

可选参数 `--threshold`、`--min-scene-len`（帧）、`--downscale` 用于后续对照实验；后台目前使用默认值。

## Docker 复现步骤

导入代码、完成本地检查，并确认没有任务正在处理后：

```powershell
docker compose config --quiet
docker compose up -d --build api worker
docker compose ps
```

打开 http://127.0.0.1:8200/，强制刷新后提交一个短视频。
检查“检测镜头边界”阶段、原有切片预览、镜头结果独立下载、ZIP 内 shots.json，以及旧任务仍可下载。
任务短时可能来不及在轮询页面看到镜头阶段，可通过 worker 日志和产物确认。
重建通过后才能把本次 Docker 验收写为已完成；不要删除数据卷。

## 下一步

准备至少 10 条自有或获授权视频，访谈、课程、Vlog 每类至少 3 条，人工标注镜头切点。
随后完成 F1 评测脚本、首轮结果和失败案例；VAD 与 ASR 仍按第三周计划继续接入。


## 正式服务验证补充（2026-09-28）

用户提供的构建输出显示镜像构建完成，API、worker 为 Running，Redis 为 Healthy。
随后对本机 8200 的实际任务进行只读 HTTP 检查，归档至
[正式服务验证记录](samples/shot_detection_deployed_verification.json)。

- 新任务 `28194bd5-46e0-4344-ae2d-d28e06e601e7` 为 SUCCEEDED，素材 Sucai1.mp4，清单为 1238 帧、900+338 帧两段。
- 检出第 271、587、607 帧，共 4 个镜头；区间无空隙、无重叠，覆盖全部 41.2667 秒。
- 用户下载文件、接口直接返回的 shots.json 与 ZIP 中的同名文件三者完全相同。
- 新 ZIP 的 CRC 与完整文件清单检查通过。
- 第二周旧任务 `0ab4374c-f874-4926-a1dc-e081e5fe51bb` 仍可查询、下载，旧 ZIP 的 CRC 与文件清单检查通过。
- 输出记录镜头模块耗时 4.545894 秒；这是输出中的单次模块耗时，不能用与本机实验的差异推断缓存命中或性能提升。

本次确认实际服务的新任务与新旧下载可用。没有在此轮重新逐帧解码切片、目视播放、采集处理中阶段变化、直接访问 Docker 管道或读取 worker 日志。
没有人工镜头标签，因此不宣称 F1 达标。新代码尚未提交/推送，其远程 CI 尚待单独核验。
本次只补记录，无需为这些文档再次重建 Docker。
