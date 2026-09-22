# ClipForge 第二周交付摘要

整理日期：2026-09-22。交付分支：`week2/mvp0`。本次已核验代码提交：`9ad78fc5c2bfc0164551ac61cb6c94629f8db160`；本文件属于随后补充的交付文档，提交后需查看新提交自己的 CI。

## 交付结论

已完成第二周媒体处理原型的可演示最小流程：**网页上传 → 后台执行 → 查询阶段 → 固定切片 → 下载 ZIP / 单段视频**。已保留本地 Git 提交、远程同步、真实自动检查和验收证据。

本次按“核心流程及已列明场景通过，未完成指标明确记录”交付，不宣称任务书所有性能、恢复和覆盖率指标均已达标。今天未启动第三周功能开发。

## 老师查看入口

| 查看内容 | 入口 |
| --- | --- |
| 第二周分支 | [GitHub week2/mvp0](https://github.com/TiAmoLovee/video-editor-research/tree/week2/mvp0) |
| 启动、使用和仓库结构 | [README](../README.md) |
| 详细演示与验收矩阵 | [WEEK2_REVIEW.md](WEEK2_REVIEW.md) |
| 工作过程、障碍及剩余项 | [WEEKLY.md](WEEKLY.md) |
| 最新已核验的自动检查 | [9ad78fc 的 Windows / Ubuntu CI](https://github.com/TiAmoLovee/video-editor-research/actions/runs/35682603364) |
| 工程习惯 | [本分支提交历史](https://github.com/TiAmoLovee/video-editor-research/commits/week2/mvp0) |

尚未合并到 main 时，应查看上述第二周分支；GitHub 首页的 main 可能仍是第一周内容。合并请求拟从 `week2/mvp0` 指向 `main`，当前材料不声明它已创建或合并。

## 已完成与证据

| 内容 | 本次记录 |
| --- | --- |
| 媒体处理 | FFprobe 读取参数、统一元数据 Schema、H.264 / AAC、CFR 30 fps、按每段最多 900 帧切片 |
| 网页与后台 | FastAPI、Celery / Redis、SQLite、上传、历史任务、阶段进度、结果下载 |
| 真实素材 | 约 41.27 秒生成 2 段；345.5 秒生成 12 段，逐段帧数及 ZIP 核验通过 |
| 60 分钟 1080p | 低复杂度合成素材生成 120 段，每段实际解码 900 帧；共 108,000 帧；ZIP 核验通过 |
| 任务耗时 | 长视频任务记录创建至完成 3017.892 秒，约 50 分 18 秒；不含上传与下载，非完整端到端性能值 |
| 异常处理 | 无效扩展名、空文件、损坏 MP4、失败任务下载保护、失败后正常新任务处理均已验收 |
| 数据保留 | 用户重启步骤反馈后，两个已完成任务与 ZIP 校验值与之前一致；未独立归档重启命令输出 |
| 自动检查 | 本地 43 项测试通过；指定提交的两平台依赖检查、Ruff 和单元测试步骤全部成功 |

证据：[真实视频](samples/Test1_video_task_verification.json)、[页面流程](samples/web_ui_video_task_verification.json)、[长视频](samples/long_video_verification.json)、[异常输入](samples/failure_verification.json)、[数据保留](samples/restart_persistence_verification.json)。

## 运行环境记录

| 项目 | 版本及证据口径 |
| --- | --- |
| Windows 本地解释器 | Python 3.11.9，本次终端读取确认 |
| 容器 FFmpeg | **7.1.5-0+deb13u1**，用户在运行中的 worker 执行命令后提供输出 |
| FFmpeg 编译器 / 架构 | GCC 14，Debian 14.2.0-19，amd64，来自版本输出 |
| API / worker 基础镜像 | 配置为 Python 3.11 的 Bookworm / Trixie slim 标签，未将容器 Python 补丁号写成已实测 |
| Redis | Compose 配置 `redis:7.4.11-alpine`，本次未单独采集运行版本 |
| 应用依赖 | FastAPI 0.141.1、Celery 5.6.3、Ruff 0.16.8 等由 requirements 文件记录；配置版本与运行时逐项核验区分 |

容器 FFmpeg 的[原始输出记录](samples/worker_ffmpeg_version.txt)已归档。基础镜像摘要和 apt 包未固定，未来重建可能取得不同补丁版本，应重新记录。Windows Shotcut 8.1 系列用于本机媒体检查，不替代容器版本证据。

## 现场演示顺序

1. 按 README 启动服务，打开本机 `http://127.0.0.1:8200/`。
2. 选择 Sucai1，展示两段及尾片；选择 Test1，展示 12 段并下载。
3. 展示长视频历史任务与 120 段结果，结合 JSON 说明逐段核验和耗时口径。
4. 选择 `intentional_corrupt_sample.mp4`，说明这是故意制造的失败样本，没有下载入口；再选择 `recovery_sample.mp4` 展示后续成功任务。
5. 刷新页面展示任务保留；最后展示 Git 提交历史、最新 CI 与周报。

这些历史任务位于开发机数据卷，不包含在 Git 仓库内；新电脑需用自己的素材生成任务。现场演示无需再跑一次一小时素材。人工播放与音画同步判断不能由文件校验结果代替。

## 已知缺口与交付边界

- 峰值内存、完整端到端耗时、复杂实拍视频的性能及资源故障场景仍需补测。
- 处理中断后的自动恢复、取消、重试、队列投递补偿和自动清理未实现；阶段进度不是逐帧进度。
- 覆盖率、JSON Schema 自动校验、常规逐帧 CFR 与精确音画同步测量尚未完成。
- 当前只有本机使用流程，没有多用户认证与隔离；镜头检测、语音活动检测、转写和完整 EDL 编辑属于后续工作。

## 提交与评审

本轮仅补交付文档，不改运行代码，不需要重建 Docker。提交这些材料并确认 CI 后，在 GitHub 创建 `main ← week2/mvp0` 的合并请求，正文应保留上述已知限制；创建 PR 不等于已经合并。
