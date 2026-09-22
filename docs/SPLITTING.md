# 固定 30 秒切片：第一版

## 输入、清单和输出

输入使用 `normalize.py` 生成的 H.264 / CFR 30 fps MP4；有音轨时要求为 AAC。程序检查编码和平均帧率，但没有逐帧鉴定任意输入是否为 CFR，所以不能把任意平均 30 fps 的视频视为已归一化素材。

每段 900 帧。切片清单由整数帧数生成，包含起始帧、结束帧（不包含）、帧数和对应秒数，然后渲染器依照清单重新编码。这样避免按容器时长的小数误差多切一个空尾片。

- 1238 帧：900 + 338，共两段。
- 9000 帧：10 段，每段 900 帧。
- 901 帧：900 + 1，真实存在的最后一帧会保留。

输出为新的目录，含 `clip_001.mp4` 等文件和 `clip_plan.json`。清单是当前固定切片的初版中间表示，后续统一 EDL 仍需扩展与定义 Schema。

## Windows 运行示例

在仓库根目录运行，按本机情况替换路径；输出目录必须尚不存在。

```powershell
.\.venv\Scripts\python.exe .\split.py "C:\Users\asus\Desktop\Sucai\Sucai1_python_normalized.mp4" "C:\Users\asus\Desktop\Sucai\clipforge_auto_segments" --ffmpeg "D:\Shotcut\ffmpeg.exe" --ffprobe "D:\Shotcut\ffprobe.exe"
```

每段完成后检查视频编码、平均帧率、预期音轨，并实际解码统计视频帧数。全部片段通过后保存清单，再发布最终结果目录。失败时清理本次临时目录，不覆盖已有输出。当前程序面向本次 Windows 环境验证。

默认每次工具调用的超时为 3600 秒，可用 `--timeout` 调整；这不是整个批次的总超时。终端显示当前第几段，没有 Web 进度页面。切片必须重新编码，有耗时和再次有损压缩的成本。

## 自动化检查

不调用 FFmpeg 的单元测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

独立媒体集成实验：自动生成低分辨率素材、运行真实切片并清理临时文件，需要 FFmpeg / FFprobe。

```powershell
.\.venv\Scripts\python.exe tests\integration_split.py --ffmpeg "D:\Shotcut\ffmpeg.exe" --ffprobe "D:\Shotcut\ffprobe.exe"
```

## 当前验证记录

2026-09-21，助手在本机 Python 3.11.9 / Shotcut 自带 FFmpeg 环境完成：

- 新旧共 22 项单元测试通过，其中固定切片新增 8 项。
- 真实 Sucai1_python_normalized.mp4 自动切为两段，分别经解码计数确认 900 帧与 338 帧。
- 合成 5 分钟、160×90、有音轨的 9000 帧视频，生成 10 个 MP4，每段经解码确认 900 帧。
- 合成 901 帧无音轨素材，实际生成 900 帧与 1 帧的两段。
- FFmpeg 程序不存在时明确失败，不保留最终结果目录或临时目录。

用户已验证前一步手动 FFmpeg 两段切片的播放；本版 Python 自动切片产物的用户播放检查完成。

## 验证范围

这是本地媒体模块的集成实验，不是任务书中“通过系统提交 5 分钟视频并得到 10 段”的 API 集成测试。尚未完成 FastAPI / Celery / Redis、上传下载、Web 进度、任务持久化和 60 分钟 1080p 压力测试。

帧数与清单的边界检查不等于逐帧画面内容比对；尚未自动测量切点处音频衔接误差、音画同步误差或检测所有画面重复/丢失情况。
