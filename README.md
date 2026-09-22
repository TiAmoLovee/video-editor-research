# ClipForge 智剪工坊

ClipForge 是一个正在开发的视频剪辑系统。第二周已实现本地媒体处理最小流程：**上传视频 → 后台转码 → 按最多 30 秒切片 → 查看进度 → 下载结果**。

完整项目还计划加入镜头检测、语音分析、候选推荐、人工复核和 MP4 / MLT 导出；这些设计目标不表示当前均已实现。当前按固定帧数切片，不进行语义选段。

## 当前进度

第二周成果在 **`week2/mvp0` 分支**。查看 GitHub 时请切换到该分支；`main` 未合并前可能仍显示第一周内容。

- 已实现：FFprobe 元数据读取、H.264 / AAC 与 CFR 30 fps 归一化、每段最多 900 帧切片、FastAPI 接口、Celery / Redis 后台处理、SQLite 任务记录、浏览器上传与历史任务下载。
- 已验证：43 项本地自动化测试；真实短视频 2 段、345.5 秒视频 12 段；页面刷新恢复所选任务；用户重启操作后的已完成任务记录与 ZIP 内容对比一致。
- 长视频流程通过：60 分钟 1080p 低复杂度合成素材生成 120 段，逐段解码共 108,000 帧，ZIP 核验通过；任务记录耗时约 50 分 18 秒。峰值内存与完整端到端性能仍未测量，见[长视频验收](docs/LONG_VIDEO_TEST.md)。
- 异常验收通过：无效扩展名、空上传、损坏 MP4、失败任务下载保护，以及失败后继续处理正常视频，见[异常验收](docs/FAILURE_TESTS.md)。
- 待完成：资源故障等更多异常场景、完整性能记录与处理中断恢复。详见[每周工作记录](docs/WEEKLY.md)。

**老师查看入口：[第二周交付摘要](docs/WEEK2_DELIVERY.md)**；[详细验收说明](docs/WEEK2_REVIEW.md)列出演示步骤、验证记录和已知限制。容器已实测 FFmpeg 7.1.5-0+deb13u1，版本输出随交付归档。

## 本机启动

前提：安装 Git、Docker Desktop，并启用 Linux 容器。首次构建需要联网获取基础镜像及依赖。容器内自带 Python 和 FFmpeg；仅通过网页使用时，无需另装 Windows Python 或 Shotcut。

首次获取项目，在 PowerShell 中执行：

```powershell
git clone --branch week2/mvp0 https://github.com/TiAmoLovee/video-editor-research.git
cd video-editor-research
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

服务启动后，打开 [视频工作台](http://127.0.0.1:8200/)；[健康检查](http://127.0.0.1:8200/health)与[接口文档](http://127.0.0.1:8200/docs)用于开发检查。上述地址指向访问者自己的电脑，GitHub 不会运行这些服务。

以后在项目目录执行 `docker compose up -d` 启动已有服务。只有修改需打入镜像的代码后才重新构建。演示或长视频任务执行期间，请保持 Docker 运行和电脑唤醒，不要重建或重启 worker。

若 Docker Hub 访问超时，先确认自己的网络和 Docker Desktop 代理设置。原开发机使用本地代理；其他机器不能直接照搬该机器的代理端口。排查记录见[周报](docs/WEEKLY.md)。

## 页面操作

1. 选择本地视频，点击“上传并开始处理”。支持 MP4、MOV、MKV、WebM、M4V、AVI，单文件上限 1 GiB。
2. 上传完成后获取任务编号，后台执行媒体处理；页面每 3 秒查询一次。
3. 完成后点击“下载全部切片 · ZIP”，也可单独下载某一段。
4. 历史任务可重新选择。地址栏包含所选任务编号，刷新后可继续查询。

25%、60% 等数值表示处理阶段，不是按已处理帧数计算的实时百分比。视频最后不足 30 秒的部分会保留；无音轨视频不会强行生成音轨。

浏览器下载的位置由浏览器设置决定，可在下载记录中找到。命令行集成脚本另将 ZIP 保存到仓库的 `downloads` 目录。完整说明见[页面使用说明](docs/WEB_UI.md)。

## 结构与数据存储

```text
浏览器 → FastAPI → SQLite 保存任务 / Redis 队列 → Celery worker
                     ↑                              ↓
               查询状态与下载 ← 元数据、归一化、切片、ZIP
```

- `api.py` / `video_api.py`：页面、上传、历史列表、状态查询与下载。
- `job_store.py`：SQLite 任务状态与任务目录。
- `tasks.py` / `video_pipeline.py`：后台任务及处理顺序。
- `probe.py` / `normalize.py` / `split.py`：读取参数、转码、切片。
- `index.html`：无需前端构建工具的操作页面。
- `compose.yaml` / `Dockerfile.*`：本地部署。
- `schemas/` / `tests/` / `docs/`：数据格式、测试与证据。

API 和 worker 共用 `media_data` 数据卷，保存上传素材、SQLite、转码中间文件和成品。Redis 使用 `redis_data` 卷。任务状态以 SQLite 为准，不依赖 Celery 临时结果是否过期。

任务都结束后可用 `docker compose stop` 停止服务并保留数据。`docker compose down -v` 会删除项目数据卷，不用于普通关闭或重启。原视频与成品当前没有自动清理策略。

## 本地开发与验证

本地测试使用 Python 3.11。首次创建环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-worker.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

2026-09-22 本地运行 43 项测试全部通过。接口单元测试使用替身隔离队列或媒体处理，因此真实 Docker、FFmpeg 与下载需另外验收。

服务运行时，可用自己的视频运行完整接口测试；将下面路径替换为实际文件：

```powershell
.\.venv\Scripts\python.exe tests\integration_video_api.py "C:\Videos\sample.mp4" --timeout 7200
```

脚本会提交一个新任务，等待结果、下载 ZIP 并检查 CRC、文件清单和清单帧数求和。`--expected-clips` 只设置测试预期，不控制生成数量；不确定时可省略。等待超时不表示后台已停止，应保留任务编号继续查询，避免重复上传。

本地切片边界集成实验需要 FFmpeg / FFprobe；具体命令见[切片说明](docs/SPLITTING.md)。视频和 ZIP 不提交到 Git，`.venv`、`data`、`downloads` 与日志已忽略。

## 验证证据

| 场景 | 已记录结果 | 证据 |
| --- | --- | --- |
| 41.27 秒真实视频 | 2 段，900 / 338 帧；实际页面上传与下载通过 | [页面验收记录](docs/samples/web_ui_video_task_verification.json) |
| 345.5 秒真实视频 | 12 段，总计 10,365 帧；ZIP 与逐段解码计数通过 | [Test1 验收记录](docs/samples/Test1_video_task_verification.json) |
| 重启操作后的已完成任务 | 两个任务记录与 ZIP SHA-256 均与之前一致 | [数据保留记录](docs/samples/restart_persistence_verification.json) |
| 5 分钟合成素材与边界素材 | 10 段；901 帧保留 1 帧尾片；编码失败清理结果 | [切片实验说明](docs/SPLITTING.md) |
| 60 分钟 1080p 合成素材 | 120 段均为 900 帧，ZIP 及逐段解码核验通过；资源指标待补充 | [长视频验收](docs/LONG_VIDEO_TEST.md) |
| 异常输入与后续任务 | 损坏输入记录失败并拒绝下载，后续正常任务完成 | [异常验收](docs/FAILURE_TESTS.md) |

重启记录包含用户操作后的页面反馈和前后接口、文件对比，未独立归档 Docker 重启命令输出。它不验证处理中任务的自动恢复。

## 当前限制与持续集成

- 服务只绑定本机 `127.0.0.1:8200`，没有用户账户及多用户隔离。
- worker 被强制中断可能留下 RUNNING 状态；自动恢复、取消、重试与队列投递补偿尚未实现。
- JSON Schema 自动校验、常规逐帧 CFR 检查及精确音画同步测量尚未接入。
- 长视频使用低复杂度的蓝色画面与测试音，只用于该素材条件下的长时长流程检查，不能代表复杂实拍视频性能。
- [GitHub Actions 配置](.github/workflows/ci.yml) 已替换为真实检查：Ubuntu / Windows、Python 3.11、依赖兼容性、Ruff 和单元测试。提交 `d70eb43` 的[首次远程运行](https://github.com/TiAmoLovee/video-editor-research/actions/runs/35679910293)已确认两套系统的检查全部成功；旧占位任务的绿色状态不算真实测试。详见 [CI 说明](docs/CI.md)，查看 [Actions](https://github.com/TiAmoLovee/video-editor-research/actions)。

## 第一周调研与设计

第一周完成基础流程实验、商业产品体验记录、需求规格与设计图。以下保留原调研阶段状态；需求文档描述后续目标，不表示当前功能全部交付。

| 工作项 | 当前状态 |
| --- | --- |
| OpenCut Classic | 已完成本地运行、导入、分割与删除、MP4 导出、播放及刷新恢复；已补充编辑、保存、预览、音频与导出的主要模块图和源码依据 |
| AutoClip | 已完成本地部署、转写、模型分析、切片生成及播放；已补充六步处理、结果同步与进度查询模块图；实际任务启动路径及异常分支仍需运行证据核对 |
| Shotcut / MLT | 已完成手工剪辑、MLT 保存与重新打开、MP4 导出与播放；已有工程样本，并补充时间轴、播放、滤镜挂接和后台导出模块图及源码依据 |
| OpusClip / Descript | 已整理两款产品的体验与截图；OpusClip 已验证 MP4 下载，Descript 最终输出及文本删改联动尚未确认 |
| 需求规格说明书 | 已形成 8 条功能需求、5 条非功能需求，逐条附验收判据，并补充借鉴说明和分阶段实现范围 |
| 用例图与主流程时序图 | 已形成图片和说明，与需求编号对应 |
| 仓库与 CI | 已配置 `.gitignore` 和 GitHub Actions 的 lint＋test 占位任务；本次核对的运行成功 |

| 文档 | 内容 |
| --- | --- |
| [调研报告](docs/research-report.md) | 参考项目实验、源码调查、产品对比与资料 |
| [需求规格说明书](docs/requirements.md) | 功能、非功能需求与验收判据 |
| [设计图说明](docs/design-diagrams.md) | 用例与主流程时序、需求映射 |
| [用例图](docs/clipforge-use-case.png) / [时序图](docs/clipforge-main-sequence.png) | 设计图片 |
| [Shotcut 工程样本说明](docs/samples/README.md) | 素材来源、路径调整与验证范围 |
| [Week1Work.mlt](docs/samples/Week1Work.mlt) | 手工工程样本，需 Shotcut 和对应原素材 |

第二周进一步说明：[归一化](docs/NORMALIZATION.md)、[切片](docs/SPLITTING.md)、[队列](docs/QUEUE.md)、[视频接口](docs/VIDEO_TASKS.md)、[网页操作](docs/WEB_UI.md)、[周报](docs/WEEKLY.md)。
