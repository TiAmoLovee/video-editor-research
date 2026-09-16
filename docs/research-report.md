# 视频剪辑项目调研报告 v1

当前状态：已完成 Shotcut 和 AutoClip 的基础流程验证，并完成部分源码调查；整体调研仍在进行。

- **Shotcut / MLT**：已完成素材导入、剪辑、工程保存与重新打开、MP4 导出和播放验证；已克隆源码、固定提交，调查部分工程读写与保存调用关系。完整模块图、滤镜或渲染机制及源码编译仍待补充。
- **AutoClip**：已完成 Docker 本地部署，以及自动转写、模型分析、切片生成、预览、下载和本地播放验证；已初步调查任务编排、LLM 调用层、主要数据模型和项目许可证，绘制文件导入与任务启动的局部关系图，并填写六维对比中的初步结论。
- **OpenCut Classic**：本报告尚未记录实际运行和源码调查结果。
- **综合部分**：仍需完善跨项目对比、产品体验记录、调研结论及参考资料。

AutoClip 当前仍待补充：

- 用运行证据确认本次实验实际经过的任务启动路径。
- 扩充完整模块依赖图，核对前端进度获取和结果存储流程。
- 检查字幕准确性、切点自然度及标题与内容的一致性。
- 补充处理耗时、资源占用和模型调用用量。
- 修复或明确记录已发现的上传入口、健康检查、安装接口及密钥日志问题。

当前完成的是基础实验与初步源码调查，尚未完成第一周全部交付内容。

当前完成的是部分项目的基础实验与初步调查，尚未完成第一周全部交付内容。

## 1. 调研目的与范围

本次调研涵盖 OpenCut Classic、AutoClip 和 Shotcut/MLT。

通过本地运行、源码阅读和官方文档查阅，
分析三个项目的实现方式，为后续系统需求与设计提供依据。

## 2. 调研环境与版本

- 操作系统：Windows，具体版本待记录
- OpenCut Classic 版本或提交编号：待确认
- 操作系统：Windows 10，系统版本 10.0.19045.6456
- AutoClip 克隆后记录的提交编号：aaf863bbd7bba99c64bc53284d41c0ed19034387
### AutoClip 实验环境与源码版本

- 源码仓库：https://github.com/zhouxiaoka/autoclip
- 本地源码目录：`D:\CodeResearch\autoclip`。
- WSL：2.7.14.0。
- Docker Desktop：4.91.0。
- Docker Engine：29.8.0，Linux/amd64。
- Docker Compose：5.5.1。
- 本地构建镜像：`autoclip:local`。
- 内容分析服务：阿里云 DashScope，模型 `qwen-plus`。
- 本地语音转写组件：faster-whisper 1.2.1，模型 `base`。
- 访问地址：`http://localhost:8000/`。

本次部署额外添加了前端启动覆盖配置，并在共享数据目录安装语音转写组件和模型。具体过程及验证结果见第 4.1、4.5 节。

版本说明：提交编号来自克隆后的 `git rev-parse HEAD` 输出。基础镜像使用标签，完整依赖版本尚未全部锁定，后续重新构建可能存在差异。
### Shotcut 实验与源码版本

- 本地实验软件：Shotcut 26.8.1。
- 源码仓库：mltframework/shotcut。
- 本次固定的源码提交编号：8cd39efcdf8ab80390ee4736db2f52577fc82cdc。
- 主窗口源码永久链接：https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp

版本说明：本地安装的软件版本与本次查阅的源码提交分别记录，尚未确认该提交对应 Shotcut 26.8.1。

## 3. OpenCut Classic

### 3.1 本地运行与基础流程
待记录：启动步骤、测试操作、运行结果及截图。

### 3.2 主要模块与职责
待调研。

### 3.3 模块依赖图
待根据源码绘制。

### 3.4 关键技术发现与证据
待记录：技术结论、源码路径或官方文档链接。

## 4. AutoClip

### 4.1 本地运行与基础流程

#### 实验环境与版本

本次在 Windows 10 环境中，使用 WSL 2 和 Docker Desktop 部署 AutoClip。

- 本地源码目录：`D:\CodeResearch\autoclip`。
- 克隆后记录的提交编号：`aaf863bbd7bba99c64bc53284d41c0ed19034387`。
- Docker Desktop：4.91.0。
- Docker Compose：5.5.1。
- 本地应用镜像：`autoclip:local`。
- 页面访问地址：`http://localhost:8000/`。
- 内容分析：阿里云 DashScope，模型为 `qwen-plus`。
- 语音转写：本地 faster-whisper 1.2.1，使用 base 模型。

#### 部署与准备

完成源码克隆、镜像构建和容器启动。部署过程中通过代理配置解决基础镜像下载失败的问题，并增加前端静态文件挂载配置，使浏览器能够访问操作页面。

模型配置通过环境变量加载。在应用容器中直接调用 DashScope，返回 HTTP 200，确认模型服务能够正常响应。

由于测试素材没有独立字幕文件，另行安装 faster-whisper，并在后台任务容器中完成 base 模型下载与加载检查。

#### 测试操作与结果

通过“文件导入”上传 `Test1.mp4`，页面显示文件大小为 19.30 MB，视频分类选择“娱乐”，未提供独立的 `.srt` 字幕文件。

点击“开始导入并处理”后，系统完成自动语音转写、内容分析和切片生成。项目页面展示了 4 个切片，各自包含标题、简介、评分和时间区间。

| 原视频时间区间 | 切片时长 | 系统评分 |
| --- | --- | --- |
| 00:02–01:00 | 58 秒 | 85 |
| 01:00–02:41 | 1 分 41 秒 | 91 |
| 02:41–03:53 | 1 分 12 秒 | 94 |
| 03:53–05:42 | 1 分 49 秒 | 96 |

随后进行页面预览、文件下载和本地播放检查，结果正常。

本次实验已完成“导入视频 → 自动转写 → AI 分析 → 生成切片 → 预览 → 下载并播放”的基础流程。

#### 验证范围

本次结论基于单个视频样本和人工播放检查。尚未系统评估字幕准确率、切点自然度、处理耗时、资源占用及多任务并发能力。

上述评分为系统生成的评分，不是人工质量评价。四个切片连续覆盖原视频的一个时间区间，尚不能据此证明系统实现了高压缩率的精彩片段筛选。

### 4.2 后端任务编排

#### 调查范围

本次调查文件导入入口、字幕准备和视频处理任务的提交关系，依据本地提交 `aaf863bbd7bba99c64bc53284d41c0ed19034387` 的源码。

以下为源码调用关系调查，尚未通过运行日志逐项核对本次实验实际经过的全部分支。

#### 1. 前端提交视频与项目信息

`frontend/src/components/FileUpload.tsx` 调用 `projectApi.uploadFiles()`。

该方法在 `frontend/src/services/api.ts` 中定义，将视频、可选字幕、项目名称和视频分类组成表单，提交到 `/projects/upload` 接口。

前端收到项目响应后，将项目加入列表并提示后台正在处理。这个提示本身不能证明后台任务已经成功执行。

#### 2. 上传接口创建项目并准备文件

`backend/api/v1/projects.py` 中的 `upload_files()` 负责检查文件扩展名、创建项目记录，并将视频保存到项目原始素材目录。

如果用户提供字幕，也会保存字幕文件。接口还会同步尝试生成缩略图，因此上传请求并非只负责提交后台任务。

源码中随后安排调用 `process_import_task.delay()`，将项目编号、视频路径和可选字幕路径交给 Celery。

#### 3. 导入任务准备字幕

`backend/tasks/import_processing.py` 中的 `process_import_task()` 负责检查缩略图，并在缺少字幕时调用语音识别功能生成字幕。

字幕准备完成后，该任务调用 `submit_video_pipeline_task()`，提交后续视频处理任务。

这里的“导入处理完成”表示字幕等准备工作完成、后续处理已提交，不等同于最终切片已经生成。

#### 4. Redis 与 Celery 分配后台任务

`backend/core/celery_app.py` 配置 Redis 作为 Celery 的消息代理和结果后端，将导入任务及视频处理任务路由到 `processing` 队列。

可以将其理解为：

- 应用提交任务消息。
- Redis 保存和传递队列消息。
- Celery worker 接收任务并执行具体处理。

`backend/utils/task_submission_utils.py` 在非 Desktop 模式下，通过 `celery_app.send_task()` 提交 `backend.tasks.processing.process_video_pipeline`。

该工具另有 Desktop 模式的本地线程执行分支，不能将两种部署方式混为一谈。

#### 5. 视频处理任务执行并保存结果状态

`backend/tasks/processing.py` 中的 `process_video_pipeline()` 创建数据库任务记录，并调用 `simple_pipeline_adapter` 执行后续处理。

处理结束后，根据返回结果更新任务和项目状态。成功分支将任务进度设置为 100，并记录项目完成时间；失败分支保存错误信息。

当前尚未完整调查前端如何获取进度，以及处理适配器内部各步骤的实现。

#### 待复核的入口问题

当前源码的 `upload_files()` 在提交导入任务前使用了 `db.query(...)`，但该函数内未发现 `db` 的定义或注入；此前访问数据库使用的是 `project_service.db`。

这一处存在触发未定义变量异常的风险。该异常会被任务提交部分的异常处理捕获，而接口仍可能返回项目创建成功。

因此，需要进一步比对运行容器中的代码和任务记录，确认实际实验的执行路径。不能仅凭页面“项目创建成功”的提示，认定后台提交一定成功；本次实验最终生成并播放切片，是另一个独立的运行结果证据。
#### 上传入口问题与另一条启动路径

已检查运行容器中的 `backend/api/v1/projects.py`，第 126 行同样使用 `db.query(...)`，与本地源码一致。本地上传函数中未发现该变量的定义或注入，因此存在任务提交前触发未定义变量异常的问题。

上传接口捕获该异常后，仍可能返回项目创建成功。因此，“项目创建成功”与“后台任务提交成功”需要分别判断。

进一步检查本地前端源码，发现另一条启动路径：

1. 首页上传成功后刷新项目列表。
2. `ProjectCard.tsx` 检测到项目状态为 `pending`、未处于下载中，且尚未自动尝试启动时，调用 `handleRetry({ silent: true })`。
3. 对于 `pending` 项目，该函数调用 `projectApi.startProcessing()`，请求 `/projects/{project_id}/process`。
4. 后端 `start_processing()` 直接提交 `process_video_pipeline` 任务。
5. `simple_pipeline_adapter.py` 在没有可用字幕时，也会尝试自动生成字幕，然后继续处理。

这条路径可以解释为何上传接口存在问题，但项目仍有机会完成处理。

需要区分证据范围：容器中的问题语句已经核对；另一条启动路径已通过本地源码确认。本次实验是否实际由该路径启动，尚未通过请求或任务日志逐项验证，当前属于基于源码的解释。

相关源码：

- `frontend/src/pages/HomePage.tsx`
- `frontend/src/components/ProjectCard.tsx`
- `frontend/src/services/api.ts`
- `backend/api/v1/projects.py`
- `backend/services/simple_pipeline_adapter.py`

#### 源码依据

- `frontend/src/components/FileUpload.tsx`
- `frontend/src/services/api.ts`
- `backend/api/v1/projects.py`
- `backend/tasks/import_processing.py`
- `backend/core/celery_app.py`
- `backend/utils/task_submission_utils.py`
- `backend/tasks/processing.py`

### 4.3 LLM 调用层

#### 调查范围

本次依据本地提交 `aaf863bbd7bba99c64bc53284d41c0ed19034387`，调查字幕进入分析流程后，业务步骤如何调用模型服务及处理返回结果。

本次实验配置的提供商为 DashScope，模型为 `qwen-plus`。以下调用结构来自源码检查，尚未逐次核对实验中的模型请求和原始响应。

#### 1. 分阶段分析字幕与片段信息

`simple_pipeline_adapter.py` 组织大纲提取、时间定位、内容评分、标题生成、主题聚类和视频生成等步骤。

| 阶段 | 主要输入 | 模型参与的工作 |
| --- | --- | --- |
| 大纲提取 | 字幕解析后得到的文本块 | 提取话题大纲 |
| 时间定位 | 话题大纲及对应字幕块 | 确定话题对应的时间范围和内容 |
| 内容评分 | 片段大纲、内容和起止时间 | 生成评分及推荐信息 |
| 标题生成 | 片段编号、大纲、内容及推荐理由 | 生成切片标题等信息 |
| 主题聚类 | 切片标题、摘要和评分等 | 组织主题合集 |

大纲提取阶段先解析 SRT，再根据素材时长进行分块，并逐块调用模型。因此，不能仅凭设置页面显示的“5000 字符”，认定当前流程固定按 5000 字符分块。

各阶段采用不同的提示词和输入数据，并非一次模型调用直接完成全部剪辑。

#### 2. 模型调用的分层关系

已确认的主要调用关系为：

`业务步骤 → LLMClient → LLMManager → 具体提供商 → 模型服务`

各层职责：

- **业务步骤**：准备提示词、字幕或片段数据，并处理阶段结果。
- **LLMClient**：为业务步骤提供统一调用入口，以及 JSON 响应解析方法。
- **LLMManager**：加载配置、选择提供商、转发调用，并处理部分调用异常与重试。
- **DashScopeProvider**：拼接提示词与输入数据，发送请求并提取模型返回文本。

这些层分别位于：

- `backend/pipeline/step1_outline.py` 至 `step5_clustering.py`
- `backend/utils/llm_client.py`
- `backend/core/llm_manager.py`
- `backend/core/llm_providers.py`

#### 3. DashScope 请求与返回

`DashScopeProvider` 支持原生 SDK 和 OpenAI 兼容接口两种调用方式，代码默认使用原生 SDK。

在原生方式中，它将提示词与输入数据组合后，通过 `Generation.call()` 传入模型名称、API Key 和提示内容，再从响应中提取文本。

本次部署曾直接调用该 SDK，并获得 HTTP 200。这证明测试请求成功，但不能代替对整条流程中每次模型调用的验证。

#### 4. 响应解析与后续处理

模型首先返回文本，各业务步骤再进行结构化解析、字段对齐或其他处理，供后续步骤使用。

例如：

- 大纲提取结果附加文本块编号，随后进行合并。
- 评分步骤解析响应后，将返回评分与原片段对齐。
- 标题步骤根据片段编号将生成结果关联回片段。
- 部分步骤保存原始响应及中间 JSON 文件，便于检查。

模型返回内容并不保证始终格式正确。代码中存在重试、解析失败处理及部分兜底逻辑，但尚未系统验证这些机制的效果。

`LLMManager.call_with_retry()` 默认最多尝试 3 次调用；遇到 `ValueError` 时直接抛出，其他异常按条件等待后重试。这里是最多 3 次尝试，不是首次调用之外再重试 3 次。

#### 5. 文本分析与视频切割的职责

在本次调查的路径中，Whisper 负责语音转写，LLM 负责基于文本生成大纲、时间范围、评分、标题和主题信息。

最终视频生成由 `step6_video.py` 调用 `VideoProcessor` 完成，底层使用 FFmpeg。模型输出需要经过程序处理，才能成为实际视频文件。

因此，字幕准确性、模型输出质量、时间范围处理和视频切割实现都会影响最终成片。

#### 当前边界与待补事项

- 尚未逐项分析各类提示词及其对输出的影响。
- 尚未量化模型调用次数、Token 用量、费用和耗时。
- 尚未系统验证 JSON 解析失败、空响应和重试后的处理结果。
- 尚未核对本次 4 个切片对应的全部中间文件。
- 模型评分不等同于人工质量评价，生成的标题与摘要仍需核实。

补充源码依据：

- `backend/services/simple_pipeline_adapter.py`
- `backend/pipeline/step6_video.py`
- `backend/utils/video_processor.py`

### 4.4 模块依赖图

#### 文件导入与任务启动的局部关系图

本图依据本地源码绘制，覆盖文件上传、两条任务启动路径及视频处理入口，尚未覆盖完整系统。

```mermaid
flowchart TD
    A["文件导入组件 FileUpload"]
    B["上传接口 upload_files"]
    C["创建项目并保存素材"]
    D["提交导入任务前的检查"]
    E["导入任务 process_import_task"]
    F["准备字幕并提交视频处理任务"]
    G["首页刷新项目列表"]
    H["项目卡片 ProjectCard"]
    I["启动处理接口 start_processing"]
    J["Redis：processing 队列"]
    K["Celery worker"]
    L["视频处理任务 process_video_pipeline"]
    M["处理适配器 simple_pipeline_adapter"]
    N["字幕准备与后续视频处理"]
    O["更新任务和项目状态"]

    A --> B
    B --> C
    C --> D
    D -.->|"预期提交；存在未定义 db 问题"| E
    E --> F
    F --> J

    B -->|"返回项目信息"| G
    G --> H
    H -->|"pending 且满足自动启动条件"| I
    I --> J

    J --> K
    K --> L
    L --> M
    M --> N
    N --> O
```

#### 图示说明

- 上传接口负责创建项目、保存文件，并尝试提交导入任务。
- 图中虚线表示存在已发现问题的预期调用：任务提交前的 `db.query(...)` 可能触发异常，不能视为已经成功执行。
- 导入任务本身也是由 Celery 经 `processing` 队列交给 worker 执行；图中为便于阅读，未重复展开这一段队列关系。
- 项目卡片另有自动启动逻辑，可以调用独立的处理接口，直接提交视频处理任务。
- 视频处理适配器在缺少字幕时，也会尝试自动生成字幕。
- 两条启动路径来自源码调查；尚未通过日志确认本次实验实际经过的完整路径。

#### 调查边界与依据

当前图是文件导入与任务启动的局部关系图，不是完整模块依赖图。LLM 调用、具体切片步骤、结果存储和前端进度获取关系仍待展开。

源码依据：

- `frontend/src/components/FileUpload.tsx`
- `frontend/src/pages/HomePage.tsx`
- `frontend/src/components/ProjectCard.tsx`
- `frontend/src/services/api.ts`
- `backend/api/v1/projects.py`
- `backend/core/celery_app.py`
- `backend/tasks/import_processing.py`
- `backend/utils/task_submission_utils.py`
- `backend/tasks/processing.py`
- `backend/services/simple_pipeline_adapter.py`

### 4.5 关键技术发现与证据

#### 部署问题与处理结果

| 问题 | 排查与处理 | 当前结果 |
| --- | --- | --- |
| 基础镜像下载失败 | 构建时访问 Docker Hub 认证接口超时；配置 Docker Desktop 代理，分别拉取 Node 和 Python 基础镜像后重新构建。 | 本次构建成功。 |
| 原启动方式未提供前端页面 | 镜像中已有前端构建文件，通过额外的 Compose 配置，在 FastAPI 中挂载前端静态文件目录。 | 可以访问首页及项目页面。 |
| 网页模型连接测试返回 HTTP 400 | 检查发现设置接口限制在 Desktop 模式使用；改用环境变量配置，并在容器中直接调用 DashScope 验证。 | 实际请求返回 HTTP 200；网页接口限制尚未修复。 |
| 缺少语音转写组件 | 在共享数据目录安装 faster-whisper，下载 base 模型，并在后台任务容器中检查模型加载。 | 检查通过，随后完成无外部字幕视频的处理。 |
| Celery worker、beat 显示不健康 | 它们沿用了面向 HTTP 服务的健康检查，该检查不适合直接判断这些后台进程的状态。 | 健康检查待修正；本次视频任务已成功处理。 |
| Flower 反复重启 | 启动命令提示不存在 flower 子命令，当前镜像缺少相应组件。 | 可选监控组件待处理，本次基础流程已完成。 |

#### 发现的实现限制

1. 设置接口存在 Desktop 模式限制，Docker 部署不能直接依赖网页完成全部配置。
2. 语音运行时采用跨平台的 faster-whisper，但安装接口仍存在 macOS 平台限制。本次通过命令完成安装，接口本身尚未修复。
3. DashScope 调用代码存在将完整 API Key 写入日志的语句，需删除或脱敏。实验报告及共享日志不应包含真实密钥。

#### 对 ClipForge 的启发

- 应分别检查模型服务、转写组件、模型文件和后台任务是否可用，再允许用户开始处理。
- 前端应显示具体失败原因，避免只展示 HTTP 状态码，让用户误判为密钥错误。
- Web 服务与后台任务应使用各自适用的健康检查。
- 部署说明应明确区分桌面模式和 Docker 模式，并列出额外下载的组件及模型。
- 日志应记录排查所需的信息，同时避免输出完整密钥。

以上为结合本次实验提出的设计建议，不表示 AutoClip 已实现这些改进。

#### 证据与源码位置

实验依据包括镜像构建输出、容器状态、DashScope 请求返回的 HTTP 200、转写组件安装结果、切片结果截图及人工播放检查。

本次检查涉及以下源码文件：

- `docker-compose.yml`：服务编排、共享目录及健康检查。
- `Dockerfile`：镜像构建、依赖安装与前端构建产物。
- `backend/api/v1/settings.py`：设置接口的运行模式限制。
- `backend/api/v1/speech_recognition.py`：语音组件安装接口的平台判断。
- `backend/services/whisper_runtime.py`：转写组件安装目录及模型缓存管理。
- `backend/utils/speech_recognizer.py`：本地 Whisper 模型加载与字幕生成。
- `backend/core/llm_providers.py`：模型调用及日志输出。

项目源码来源：https://github.com/zhouxiaoka/autoclip

本次额外添加的前端启动配置为 `docker-compose.frontend.yml`，属于本地部署补充配置。
#### 数据模型的初步调查

源码通过 SQLAlchemy 定义项目、任务和切片等数据库模型。本次重点检查三个模型：

| 模型 | 主要记录内容 | 关联方式 |
| --- | --- | --- |
| Project（项目） | 名称、状态、视频路径、字幕路径、处理配置及完成时间 | 关联多个任务和切片 |
| Task（任务） | 任务类型、状态、进度、当前步骤、Celery 任务编号、错误信息及处理结果 | 通过 project_id 关联项目 |
| Clip（切片） | 标题、描述、起止时间、时长、评分、推荐理由及输出视频路径 | 通过 project_id 关联项目 |

这些对象分别回答三个问题：

- 项目：正在处理哪份素材，总体状态是什么？
- 任务：后台正在做什么，执行到哪一步，是否失败？
- 切片：选中了哪段内容，生成的文件在哪里？

模型中的 video_path、subtitle_path 等字段记录文件路径。结合前面调查的文件写入流程，可以看到系统同时使用数据库与文件系统：数据库保存业务记录和关联，文件系统保存视频、字幕及部分中间结果。

需要注意，Clip 模型将起止时间和时长声明为以秒为单位的 Integer 字段。当前只确认了模型声明，尚未核对写入时的小数处理及最终切点精度，不能据此声称支持逐帧精度。

当前调查仅覆盖主要字段与关联，尚未完整检查数据库迁移、文件与数据库的一致性、删除清理及异常恢复。

源码依据：

- `backend/models/project.py`
- `backend/models/task.py`
- `backend/models/clip.py`
- `backend/api/v1/projects.py`
#### 许可证初步调查

本次核对提交 `aaf863bbd7bba99c64bc53284d41c0ed19034387`，仓库根目录的 `LICENSE` 为 MIT License，版权声明为 `Copyright (c) 2024 AutoClip Team`。

`pyproject.toml` 和 README 也将项目许可证标为 MIT，与许可证文件一致。

许可证文本允许使用、修改和分发等行为，同时要求在软件副本或实质性部分中保留版权声明及许可声明，并包含无担保条款。

本次核对的是 AutoClip 项目自身的许可证。依赖库、模型文件及外部模型服务的许可或使用条款，需要分别核对，不能统一视为 MIT。

源码依据：

- [固定提交的 LICENSE](https://github.com/zhouxiaoka/autoclip/blob/aaf863bbd7bba99c64bc53284d41c0ed19034387/LICENSE)
- [固定提交的 pyproject.toml](https://github.com/zhouxiaoka/autoclip/blob/aaf863bbd7bba99c64bc53284d41c0ed19034387/pyproject.toml)

## 5. Shotcut / MLT

### 5.1 本地运行与基础流程
使用 Shotcut 26.8.1 导入素材 Sucai1.mp4，进行分割并移除中间部分，保留两个片段。将结果保存为 Week1Work.mlt，关闭后重新打开，确认工程可以恢复剪辑结果。
工程分辨率为 960 × 600，帧率为 30 fps。

#### 运行方式与完成范围

本次在 Windows 10 环境中使用已安装的 Shotcut 26.8.1，完成素材导入、片段剪辑、工程保存和重新打开。

已使用 Git 克隆 Shotcut 官方源码仓库，本地位置为 `D:\CodeResearch\shotcut`。

已将本地源码切换到提交 `8cd39efcdf8ab80390ee4736db2f52577fc82cdc`，并通过 `git rev-parse HEAD` 核对，输出与报告记录一致。

当前已完成软件基础使用实验、源码克隆和部分源码阅读。尚未从源码编译运行 Shotcut，也未单独部署 MLT。本地安装的软件与所查源码提交是否对应同一发布版本，尚未核对。
#### MP4 导出与播放验证

将 Week1Work.mlt 的时间轴导出为 MP4，并使用视频播放器打开检查。

人工检查结果：
- 视频可以正常播放，画面和声音正常。
- 被删除的中间部分未出现在成品中。
- 两个保留片段按预期顺序连接。

本次验证表明，该样本已完成“导入素材 → 剪辑 → 保存并重新打开工程 → 导出视频 → 播放检查”的基础流程。

检查范围仅限本次样本的人工播放观察，尚未进行逐帧精度或音画同步误差测量。

### 5.2 工程文件读写
查看 Week1Work.mlt 后，发现工程使用 XML 格式记录素材引用和剪辑安排：
resource 属性记录原始素材的文件路径。
两个素材引用 chain0 和 chain1 均指向 Sucai1.mp4。
playlist0 对应名为 V1 的视频轨道。
轨道中的两条 entry 按顺序播放，分别选取原素材的 00:00:00.000—00:00:20.000 和 00:00:30.600—00:00:41.233。
in、out 表示原素材中的起止位置，out 包含结束帧。
初步理解：工程通过“素材引用＋片段范围＋排列顺序”保存剪辑结果，重新打开时仍需能够找到原素材。
依据：
本次实验文件：Week1Work.mlt 中的 resource、playlist0 和 entry。
MLT XML 官方文档：https://www.mltframework.org/docs/mltxml/
MLT 起止点与时长说明：https://www.mltframework.org/docs/mvcp/
#### 保存工程的源码初步调查

在 Shotcut 官方仓库 master 分支的 `src/mltcontroller.cpp` 中，定位到 `Controller::saveXML(...)` 函数。

该函数使用 MLT 的 XML Consumer 生成工程 XML，并设置时间表示、工程标题等属性。在普通文件保存分支中，生成的 XML 经处理和校验后，通过 `QTextStream` 写入文件，最后调用 `QSaveFile::commit()` 完成保存。

其中，`time_format` 设置为 `clock`，与本次 Week1Work.mlt 样本中观察到的时钟格式时间值一致。

源码依据：https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp

版本说明：本次查看的是 master 分支，尚未记录具体提交编号；本地实验使用 Shotcut 26.8.1，两者尚未进行版本对应核对。

当前进度：已定位保存工程函数，并初步理解普通文件保存流程；界面如何调用该函数、工程读取流程尚待继续调查。
#### 打开工程的源码初步调查

在 `src/mltcontroller.cpp` 中定位到 `Controller::open(...)` 函数。

该函数先调用 `checkFile()` 检查文件；检查未报告错误后，关闭当前内容。对于 `.mlt` 文件，它会处理路径编码，然后通过 `Mlt::Producer` 创建读取对象，并检查对象是否有效。

读取成功后，函数根据条件更新工程和预览参数，并读取工程中保存的音频声道数、处理模式。本次 Week1Work.mlt 样本包含相应的 `shotcut:projectAudioChannels` 和 `shotcut:processingMode` 属性，可与这一读取流程对照。

该函数返回整数错误码：正常流程返回 0；读取对象无效时设为 1；文件检查失败时返回检查得到的错误码。

初步结论：这段代码通过 MLT 读取工程，而不是自行逐项解析 XML。MLT 内部解析以及界面时间轴恢复流程尚待进一步调查。

源码依据：[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)
#### 主窗口与保存控制器的调用关系

在 `src/mainwindow.cpp` 中定位到 `MainWindow::saveXML(...)` 函数。该函数根据当前工程状态选择保存对象，再调用 `MLT.saveXML(...)` 执行保存。

当时间轴模型中存在轨道时，函数传入 `multitrack()`，保存多轨时间轴。本次样本包含 V1 轨道及两个片段，对应这一分支。其他分支分别处理播放列表、其他有效内容对象和空工程。

初步确认的分工为：

- `MainWindow::saveXML`：选择保存内容，并传递文件名、路径选项和工程备注。
- `Controller::saveXML`：生成工程 XML，在普通文件保存流程中将其写入文件。

源码依据：
[https://github.com/mltframework/shotcut/blob/master/src/mainwindow.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp
](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)
当前已确认这两个函数之间的调用关系，保存按钮到主窗口保存函数的调用路径尚待调查。
#### 保存操作入口与调用流程

在 `src/mainwindow.cpp` 中定位到 `MainWindow::on_actionSave_triggered()`。

该函数先停止时间轴录制。如果当前工程没有保存路径，则进入“另存为”流程；如果已有路径，则在通过可写检查后调用定期备份逻辑，再执行 `saveXML(m_currentFile)`。

已确认的已有路径保存调用链为：

`MainWindow::on_actionSave_triggered()` → `MainWindow::saveXML(...)` → `Controller::saveXML(...)`

三个函数分别承担保存操作处理、保存对象选择、XML 生成与文件写入的职责。

保存后，入口函数重新建立自动保存对象、更新窗口和撤销历史状态，并根据 `success` 显示成功消息或错误提示。

观察到的细节：已有路径分支末尾返回固定值 `true`，而不是 `success`，因此不能仅凭该入口函数返回 `true` 判断文件保存成功。

源码依据：
[https://github.com/mltframework/shotcut/blob/master/src/mainwindow.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)

当前范围：已追踪保存操作处理函数到文件写入的主要路径；尚未核对界面动作绑定，“另存为”内部流程也未展开调查。
### 5.3 模块依赖图
本图为工程保存部分的模块关系草稿，仅覆盖目前已阅读的源码。

```mermaid
flowchart TD
    A["主窗口 MainWindow"]
    B["时间轴面板 TimelineDock"]
    C["多轨数据模型 MultitrackModel"]
    D["MLT 多轨对象"]
    E["保存控制器 Controller"]
    F["MLT XML 输出组件"]
    G[".mlt 工程文件"]

    A -->|"通过 model() 取得数据模型"| B
    B -->|"返回 m_model"| C
    C -->|"tractor() 返回 m_tractor"| D
    A -->|"将取得的多轨对象交给 saveXML()"| E
    E -->|"连接保存对象，生成 XML"| F
    F -->|"返回 XML 文本"| E
    E -->|"写入文件"| G
```
### 时间轴对象的获取关系

`MainWindow::multitrack()` 调用 `m_timelineDock->model()->tractor()` 获取时间轴对象。

其中：

- `TimelineDock::model()` 返回面板持有的 `MultitrackModel` 对象地址。
- `MultitrackModel::tractor()` 返回模型持有的 `m_tractor`，类型为 `Mlt::Tractor*`。

这条路径说明，主窗口保存时间轴时，从时间轴面板的数据模型取得已有的 MLT 多轨对象，再交给保存控制器处理。

补充源码依据：

- [时间轴面板](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/docks/timelinedock.h)
- [多轨数据模型](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/models/multitrackmodel.h)

本图仍为时间轴对象获取与工程保存部分的关系图，尚未覆盖完整系统。
### 源码对应关系

- 主窗口：`MainWindow::on_actionSave_triggered()` 处理保存操作，再调用 `MainWindow::saveXML()` 选择保存内容。
- 控制器：`Controller::saveXML()` 调用 MLT 生成 XML，在普通文件保存分支中完成文件写入。
- MLT XML 输出组件：由控制器创建并连接工程对象，用于生成 XML。

### 源码依据

- [主窗口源码](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
- [控制器源码](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)

### 当前范围

已整理工程保存部分的模块关系。时间轴编辑、播放预览、滤镜和视频导出等模块尚未纳入，本图后续继续补充。

### 5.4 工程样本
已生成 Week1Work.mlt，并在本机验证可以重新打开。
存放位置：docs/samples/Week1Work.mlt。
原始素材：Sucai1.mp4。当前工程通过本机绝对路径引用素材，在其他电脑上打开时需重新定位素材。

### 5.5 关键技术发现与证据
#### 可借鉴点：将剪辑安排与视频素材分开记录

本次实验中，Week1Work.mlt 通过素材路径、片段起止位置和排列顺序保存剪辑安排，原始视频仍作为独立文件存在。

对 ClipForge 的启发：可以先用统一的剪辑清单记录选中的片段，再根据清单生成视频或 MLT 工程。人工调整时更新清单，使不同导出方式使用一致的剪辑安排。

这是结合本次调研与任务书提出的设计思路，不表示 Shotcut 内部使用了 ClipForge 所规划的 EDL JSON。

依据：本次工程样本，以及第 5.2、5.3 节记录的源码与官方文档。
#### 架构分工的初步观察

在已阅读的代码中，主窗口、时间轴数据模型和保存控制器承担不同职责：

- 主窗口处理保存操作，并选择保存内容。
- 时间轴面板通过数据模型提供已有的 MLT 多轨对象。
- 保存控制器调用 MLT 生成 XML，并负责文件写入。

可借鉴之处：ClipForge 也可以分别组织用户操作、剪辑数据和导出处理，避免把所有逻辑集中在界面代码中。

同时，已调查的保存流程直接使用 MLT 对象，说明这部分实现与 MLT 的接口存在依赖。若替换底层媒体框架，相关代码需要调整。这个判断仅针对已查看的流程，不代表已完成整个 Shotcut 架构的分析。

源码依据见第 5.2、5.3 节。
#### 许可证初步调查

Shotcut 官方 FAQ 标明软件采用 GPLv3。

MLT 官方版权政策说明，框架核心采用 LGPLv2.1；模块和附带程序的许可证可能不同。因此，需要区分 Shotcut 应用程序、MLT 框架核心及具体模块，不能只用一个许可证名称概括全部组件。

本次完成的是项目级许可证信息调查，尚未逐项核对本机安装包所包含的依赖和模块。

官方依据：

- [Shotcut FAQ](https://shotcut.org/FAQ/)
- [MLT 版权政策](https://www.mltframework.org/docs/copyrightpolicy/)
#### 当前调研边界与待完成事项

已完成：
- 使用 Shotcut 制作剪辑工程，并保存、重新打开。
- 观察 MLT XML 中的素材引用、片段范围和排列顺序。
- 追踪工程保存、打开以及多轨对象获取的部分源码。
- 克隆官方源码，固定并核对提交编号。
- 整理工程保存部分的模块关系图。
- 已完成一次 MP4 导出与人工播放检查，结果正常。

待完成：
- 继续调查滤镜或渲染流程，补充核心处理机制。
- 扩充模块关系图，目前仅覆盖时间轴对象获取和工程保存。
- 源码编译运行尚未进行。
## 6. 六维对比

| 维度 | OpenCut Classic | AutoClip | Shotcut/MLT |
|---|---|---|---|
| 架构模式 | 待调研 | Web 前端通过 API 创建项目和启动处理；Docker 模式使用 Redis 与 Celery 执行后台任务，处理适配器组织字幕分析及视频生成。模型调用经过 LLMClient、LLMManager 和具体提供商分层封装。当前仅调查了相关局部流程，依据见第 4.2—4.4 节。 | 桌面应用。已调查的工程保存路径中，主窗口处理保存操作，时间轴面板及模型提供多轨对象，控制器调用 MLT 生成 XML 并写入文件。依据见第 5.2、5.3 节。 |
| 核心算法 | 待调研 | 已调查基于字幕文本的处理流程：Whisper 语音转写后，分阶段调用 LLM 提取大纲、定位时间范围、评分、生成标题和组织主题合集，再使用 FFmpeg 生成视频。当前记录的是处理机制，尚未深入分析语音模型内部算法，也未系统验证选段质量。依据见第 4.3 节。 | 当前已调查工程对象获取、XML 生成及文件读写流程，属于工程机制；尚未深入分析具体视频处理算法。后续需结合滤镜或渲染实现补充。 |
| 数据模型 | 待调研 | 使用 SQLAlchemy 定义 Project、Task、Clip 等模型；任务和切片通过 project_id 关联项目。数据库记录状态、进度、片段时间、评分及文件路径，视频、字幕和部分中间结果保存在文件系统。当前仅核对主要字段与关联，依据见第 4.5 节。 | 工程使用 MLT XML 保存：resource 记录素材引用，playlist 中的 entry 记录片段范围与排列顺序；时间轴模型通过 tractor() 返回 MLT 多轨对象。依据见第 5.2、5.3 节。 |
| 部署方式 | 待调研 | 在 Windows 10 的 WSL 2 环境下使用 Docker Compose 构建并运行，包含应用、Redis 和 Celery 后台任务。额外添加前端静态文件挂载配置，通过环境变量配置千问服务，并在共享数据目录安装 faster-whisper 与 base 模型。已完成单视频导入、自动转写、切片生成及下载播放验证。依据见第 4.1、4.5 节。 | 使用已安装的 Shotcut 26.8.1 完成本地实验；已克隆官方源码并切换到报告记录的固定提交。尚未从源码编译运行，也未单独部署 MLT。依据见第 5.1 节。 |
| 许可证 | 待核对 | 项目自身采用 MIT，已核对固定提交中的 LICENSE、pyproject.toml 和 README。依赖库、模型及外部服务条款仍需分别核对。依据见第 4.5 节。 | Shotcut：GPLv3；MLT 框架核心：LGPLv2.1。MLT 模块及附带程序可能采用不同许可证，使用时需分别核对。依据见第 5.5 节。 |
| 可借鉴点与不可借鉴点 | 待分析 | 可借鉴：将视频导入、自动转写、模型分析与切片输出串成完整流程，并展示标题、评分和时间区间，供用户预览后下载。不宜直接照搬：当前部署中的前端入口缺失、设置接口模式限制、不适配的后台健康检查和明文密钥日志。系统评分也不能直接作为人工质量评价。依据见第 4.1、4.5 节。 | 待分析 |

## 7. Descript / Opus Clip 体验记录

待记录：体验日期、可用免费功能、操作步骤及交互设计要点。
未能体验的部分说明原因。

## 8. 调研结论与后续工作

待总结：适合本项目参考的设计、限制和待验证问题。

## 9. 参考资料

待补充官方文档和源码链接。
各项技术结论应同时在对应正文位置标注证据。
