# 媒体归一化：第一版

> 工程整改后请先按 [README](../README.md) 安装 `backend` 包并设置 `FFMPEG` / `FFPROBE`。示例素材放在自行准备的 `downloads/fixtures/` 中；旧验收结果保留原时间，不表示本次重新运行。

## 功能

`backend/clipforge/media/normalize.py` 复用 `backend/clipforge/media/probe.py` 读取素材，再调用 FFmpeg 转码，并检查成品基本参数。

- 视频：H.264、恒定 30 fps、yuv420p，CRF 20、fast 预设。
- 音频：有音轨时选第一条并转成 AAC，目标码率 128 kbps；无音轨时保持无音轨，不生成静音轨。
- 视频流：选择第一条非封面视频流。奇数宽高向上补齐到偶数，以适配 yuv420p；最多增加一列或一行黑色像素。
- 不覆盖原素材或已有成品，输出必须为新的 MP4 文件。
- 先写临时文件，验证后再复制到目标文件；失败时清理临时文件。复制期间目标文件可能已存在，因此未来后台服务应只在函数成功返回后发布下载入口。
- 通过命令参数指定工具位置，不把本机安装路径写入程序。

## Windows 本地运行示例

在仓库根目录执行，前提是已经创建 `.venv`，并安装支持 libx264 和 AAC 的 FFmpeg。

```powershell
.\.venv\Scripts\python.exe -m clipforge.media.normalize "downloads\fixtures\Sucai1.mp4" "downloads\fixtures\Sucai1_python_normalized.mp4" --ffmpeg $env:FFMPEG --ffprobe $env:FFPROBE
```

示例素材需自行准备，工具路径由环境变量提供。重复运行请换一个输出文件名。程序把元数据打印到终端；保存 JSON 仍使用 `python -m clipforge.media.probe 输入视频 --output 输出.json`。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

默认转码超时为 3600 秒，可通过 `--timeout` 调整。成功退出码为 0，失败为 1。当前只有开始和结束提示，尚未实现百分比进度。

## 函数分工

1. `probe_video()` 调用 FFprobe。
2. `normalize_metadata()` 整理元数据。
3. `normalize_video()` 安排转码和文件保存。
4. `validate_output()` 检查 H.264、平均 30 fps、有效时长及预期音轨。

注意：元数据格式整理与视频转码是两种不同操作。函数的基本参数检查不等同于逐帧验证 CFR，也不证明人工播放质量。

## 当前验证记录

2026-09-21，在 Windows、Python 3.11.9、Shotcut 自带 FFmpeg n8.1.2-34-g9b6c8969e0 环境验证：

- 14 项单元测试通过：7 项元数据转换测试和 7 项转码约束/文件保护测试。单元测试不运行 FFmpeg，不代表端到端转码测试。
- 助手另外运行真实 Sucai1.mp4 转码：输出 H.264 / AAC、960×600、时长约 41.266016 秒。
- 对本次 Python 转码产物另行读取全部 1238 帧的时间戳，相邻帧间隔均精确为 1/30 秒。
- 另用 1 秒、25 fps 的无音轨生成素材执行真实转码，确认成品仍无音轨。
- 指向不存在的 FFmpeg 程序时，返回失败，未留下最终成品或临时目录。
- 用户已确认此前手工 FFmpeg 命令生成的成品播放正常；Python 版成品的用户播放检查完成。

## 尚未完成

- 长视频压力测试、异常中断恢复和多任务并发验证。
- 逐帧 CFR 检查尚未纳入常规程序或自动化测试。
- 自动切片、任务队列、进度推送及 Web 页面。
- 本节早期实验使用 Windows FFmpeg 8.1 系列；后续 Docker worker 已使用 FFmpeg 7.1.5-0+deb13u1 完成视频流程验收，见[交付版本记录](WEEK2_DELIVERY.md)。这不表示其他任意版本均已验证兼容。
