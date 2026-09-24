# ClipForge 智剪工坊

ClipForge 已实现第二周媒体处理最小流程：**网页上传 → 后台归一化 → 每段最多 30 秒切片 → 查询阶段进度 → 预览与下载**。当前采用固定帧数切片，不进行语义选段。

根据老师的工程反馈，本分支将单文件前端迁移为 **React 18 + TypeScript 5 + Vite + Zustand 4 + Ant Design 5**，后端、测试和部署文件分目录，并统一 FFmpeg 环境配置。第二周原有验收记录是历史证据，迁移后的验证范围单独记录。

**老师查看入口：[工程整改说明](docs/ENGINEERING_REFACTOR.md)** · [第二周交付摘要](docs/WEEK2_DELIVERY.md) · [周报](docs/WEEKLY.md)

## 项目结构

```text
frontend/                     React 前端与构建配置
  src/components/             上传、任务历史、详情、切片结果
  src/api/                    HTTP 请求、上传及下载地址校验
  src/store/                  Zustand 任务、分页、选中项与连接状态
  src/hooks/                  可取消的任务轮询
  src/types/                  TypeScript 接口类型
  src/test/                   前端交互测试
  package.json                开发、类型检查、测试、构建命令
  package-lock.json           可复现安装的依赖锁文件
backend/
  clipforge/api.py            FastAPI 入口与前端构建产物提供
  clipforge/routes/           HTTP 上传、查询、下载
  clipforge/media/            FFprobe、归一化、切片
  clipforge/services/         视频处理流程
  clipforge/storage/          SQLite 与任务目录
  clipforge/queue/            Celery 任务、客户端及队列演示
  clipforge/config.py         FFmpeg / FFprobe 配置解析
  tests/                     Python 单元与集成实验
  requirements*.txt           Python 依赖
deploy/                      API / worker Dockerfile
docs/                        说明、周报与历史验收记录
schemas/                     数据格式定义
compose.yaml                 本地容器编排
.env.example                 本机环境变量模板
```

## Docker 启动（用于完整流程）

安装 Git、Docker Desktop，启用 Linux 容器。在项目根目录执行：

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

打开 [工作台](http://127.0.0.1:8200/)；[接口文档](http://127.0.0.1:8200/docs)用于开发检查。地址指向访问者自己的电脑，GitHub 不运行这些服务。

API 镜像先使用 Node.js 24 构建 React 页面，再把静态产物复制到 Python 镜像。worker 自带 FFmpeg。只用 Docker 运行时无需在宿主机额外安装 Node、Python 或 FFmpeg。首次构建需要网络，可能需要配置 Docker Desktop 代理。

**从旧版迁移时，等待正在处理的任务完成，再同时重新构建 API 和 worker**。本次 Python 模块路径发生变化，不能只更新页面。服务名、8200 端口、任务 API、Celery 任务名、SQLite 表结构及数据卷名称保持不变。不要删除数据卷；历史任务仍由现有数据卷读取，容器迁移后的实际回归需按整改说明执行。

以后可用 `docker compose up -d` 启动，用 `docker compose stop` 停止。处理任务时保持 Docker 运行和电脑唤醒。`docker compose down -v` 会删除数据卷，不用于普通关闭。

## 本地开发

本地开发使用 Python 3.11、Node.js 24 和 npm。先在仓库根目录创建 Python 环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
.\.venv\Scripts\python.exe -m pip install -e backend
```

编辑前端时，先启动上述 Docker 服务作为 API，再在另一窗口执行：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173/`。Vite 将 `/tasks` 等请求代理到 `http://127.0.0.1:8200`，上传和下载仍使用同一后端。如 API 地址不同，启动 Vite 前设置 `$env:CLIPFORGE_API_URL`。

构建及测试（在 `frontend` 目录）：

```powershell
npm run typecheck
npm test
npm run build
```

产物为 `frontend/dist/`，不提交 Git。源码、package.json 和锁文件提交 Git。开发页面使用 Vite；部署页面由 FastAPI 提供构建产物，不把源码目录直接作为网站发布。

仅开发 API 时可在仓库根目录运行 `.\.venv\Scripts\python.exe -m uvicorn clipforge.api:app --host 127.0.0.1 --port 8201`，并设置 Vite 的 API 目标。该方式需要另外配置可连接的 Redis 和共用数据目录；推荐初学阶段使用 Docker 完整后台，避免误连另一套任务存储。

## 本机 FFmpeg 配置

Docker 已配置容器内工具；以下仅用于 Windows 本机媒体命令。优先使用系统 PATH 中的可执行文件：

```powershell
$env:FFMPEG = (Get-Command ffmpeg -ErrorAction Stop).Source
$env:FFPROBE = (Get-Command ffprobe -ErrorAction Stop).Source
& $env:FFMPEG -version
& $env:FFPROBE -version
```

如果工具不在 PATH 中，改用下面方式输入本机实际路径（输入时不用额外加引号）：

```powershell
$env:FFMPEG = Read-Host "请输入 ffmpeg 可执行文件的完整路径"
$env:FFPROBE = Read-Host "请输入 ffprobe 可执行文件的完整路径"
& $env:FFMPEG -version
& $env:FFPROBE -version
```

配置优先级：命令行显式参数 > `FFMPEG` / `FFPROBE` > 兼容旧变量 `FFMPEG_PATH` / `FFPROBE_PATH` > PATH 中的命令。变量在每次调用时读取。`.env.example` 只是模板，Python 不会自动读取 `.env`；PowerShell 的 `$env:` 设置作用于当前窗口及其子进程。

准备自己的输入视频后，在仓库根目录执行（输出必须是新路径）：

```powershell
$video = Read-Host "请输入要处理的视频完整路径"
.\.venv\Scripts\python.exe -m clipforge.media.probe "$video"
.\.venv\Scripts\python.exe -m clipforge.media.normalize "$video" "downloads/normalized.mp4"
.\.venv\Scripts\python.exe -m clipforge.media.split "downloads/normalized.mp4" "downloads/clips"
```

## 验证与 CI

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
.\.venv\Scripts\python.exe -m ruff check --no-cache backend
.\.venv\Scripts\python.exe backend/tests/integration_split.py
.\.venv\Scripts\python.exe backend/tests/integration_video_api.py "$video" --timeout 7200
```

最后一条需要服务运行并先设置 `$video`；会创建一个真实任务。`--expected-clips` 设置测试预期，不控制切片数量，不确定时省略。等待超时不代表后台停止，应先查询已有任务，避免重复上传。

[GitHub Actions](.github/workflows/ci.yml) 分别在 Ubuntu / Windows 检查 Python 与前端：Python 依赖检查、Ruff、单元测试；前端锁文件安装、TypeScript 检查、交互测试和 Vite 构建。新提交的远程结果以其 Actions 记录为准。

## 已完成的历史验收与当前限制

- 第二周原型已验证真实短视频 2 段、345.5 秒素材 12 段，记录见 [页面验收](docs/samples/web_ui_video_task_verification.json) 和 [Test1 验收](docs/samples/Test1_video_task_verification.json)。
- 60 分钟 1080p 低复杂度合成素材生成 120 段，逐段解码共 108,000 帧；任务记录耗时约 50 分 18 秒，见 [长视频验收](docs/LONG_VIDEO_TEST.md)。这是旧版已归档的媒体实验，不代表本次重新转码或真实复杂素材性能。
- 已归档的 [数据保留记录](docs/samples/restart_persistence_verification.json) 和 [异常验收](docs/FAILURE_TESTS.md)保留原测试时间与范围。
- 仍未实现处理中断自动恢复、取消/重试、多用户隔离与自动清理；峰值内存、精确音画同步和完整端到端性能未测量。阶段百分比不是逐帧进度。

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
