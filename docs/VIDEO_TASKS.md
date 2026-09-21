# 视频后台处理接口初版

## 这一阶段要验证什么

上传视频 → 保存文件和任务记录 → Celery 队列 → FFprobe 读取参数 → FFmpeg 归一化 → 固定切片 → ZIP → 查询和下载。

接口继续使用 http://127.0.0.1:8200/docs 。原有 `/health` 和 `/demo/tasks` 保留。

## 文件分工

| 文件 | 作用 |
| --- | --- |
| video_api.py | 上传文件、查询视频任务、下载已完成结果 |
| job_store.py | SQLite 状态记录与按 UUID 分配存储目录 |
| video_pipeline.py | 按顺序调用已有 probe、normalize、split 模块并打包 |
| tasks.py | 注册 Celery 视频任务，只传递任务编号 |
| compose.yaml | API 和 worker 共用 media_data 卷，Redis 保持原数据卷 |
| tests/integration_video_api.py | 通过真实 HTTP 上传、查询、下载并核对 ZIP 清单 |

## 安装、检查和启动

在仓库根目录执行，保持 Docker Desktop 与已经验证可用的代理运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
docker compose config --quiet
docker compose up -d --build api worker
docker compose ps
docker compose exec worker ffmpeg -version
```

当前共 39 项单元测试。开发依赖中的 httpx 仅用于 HTTP 接口测试，不安装到生产容器；现有 Starlette 若提示 httpx 弃用，是兼容提醒，当前测试仍可运行。

worker 首次切换到 Python 3.11 的 Trixie 基础镜像并新增 FFmpeg 系统包，构建需要联网下载。API 仍复用原有 Bookworm 基础镜像。出现拉取、apt 或 pip 错误时保留最后的日志定位，不需要删除 Redis 数据或重置 Docker。

### FFmpeg 版本边界

worker 使用 `python:3.11-slim-trixie`，从 Debian 官方软件源安装 FFmpeg 7.1 系列，与任务书的 7.x 版本范围对应。原 Bookworm 软件源提供 5.1 系列，因此本次只调整需要 FFmpeg 的 worker 基础镜像。Windows Shotcut 仍为 8.1 系列，Windows 验证不能替代容器验证。构建后应记录 `ffmpeg -version` 并完成真实视频集成验证。软件源包和基础镜像摘要尚未锁定。

参考：https://packages.debian.org/trixie/ffmpeg

## 第一次用真实素材验证

```powershell
.\.venv\Scripts\python.exe tests\integration_video_api.py "C:\Users\asus\Desktop\Sucai\Sucai1.mp4" --expected-clips 2
```

默认连接 8200；默认等待最多 600 秒。脚本分块上传，不把整个视频读进内存；下载到 `downloads/<任务编号>-result.zip`，该目录已加入 `.gitignore`。
预期输出 `PASS: upload -> worker -> 2 clips -> ZIP verified`，两个片段预期分别为 900、338 帧。
脚本检查 HTTP 状态、任务完成、片段数量、ZIP CRC、归档文件与 clip_plan 的对应关系、总帧数求和。它不替代人工播放检查或精确音画同步测量。

也可以在 `/docs` 手动操作：

1. `POST /tasks` → Try it out → 选择视频 → Execute，得到 HTTP 202 与 task_id。
2. `GET /tasks/{task_id}` 输入编号，间隔数秒查询；完成状态为 SUCCEEDED。
3. 返回的 `result.downloads` 包含 JSON、各段 MP4 和 result.zip 的相对地址，将地址附在 `http://127.0.0.1:8200` 后即可下载。

注意：新的视频任务使用 `SUCCEEDED`；旧加法演示仍使用 Celery 的 `SUCCESS`。
只打开 `/docs` 不算视频实验通过，必须取得完成结果和下载产物。

## 状态与阶段

| 状态 | 含义 |
| --- | --- |
| QUEUED | 文件和数据库记录已保存，等待后台领取 |
| RUNNING | 后台正在执行 |
| SUCCEEDED | 全部步骤完成，下载结果可用 |
| FAILED | 处理失败，返回失败阶段，详细原因查看 worker 日志 |
| SUBMISSION_UNKNOWN | 队列提交没有收到确认，可能仍会被 worker 执行；保留编号继续查询 |

阶段依次为 queued、probing、normalizing、splitting、packaging、done。
`progress` 是阶段估计值 0、10、25、60、90、100，`progress_kind=stage_estimate`；不是实际处理帧数的精确百分比，阶段内可能长时间不变。

未知视频任务返回 404；非法 UUID 返回 422；未完成时下载返回 409；未列入该任务成品清单的文件返回 404。
支持 MP4、MOV、MKV、WebM、M4V、AVI 扩展名，单文件上限 1 GiB；真实媒体有效性在 worker 中由 FFprobe 检查。损坏或伪装的视频会得到 FAILED，不会开放半成品下载。

## 数据与重启

`media_data` 为项目独立命名卷，API 和 worker 内部均挂载到 `/data`：

```text
/data/tasks.sqlite3
/data/jobs/<UUID>/source.<扩展名>
/data/jobs/<UUID>/media_meta.json
/data/jobs/<UUID>/normalized.mp4
/data/jobs/<UUID>/normalized_media_meta.json
/data/jobs/<UUID>/clips/clip_plan.json
/data/jobs/<UUID>/clips/clip_001.mp4 ...
/data/jobs/<UUID>/result.zip
```

SQLite 才是视频任务查询来源，不依赖 Celery 结果的一天过期时间。每次操作使用独立短连接，数据库事务不跨越媒体处理。
API 和 worker 均以 UID 10001 运行，镜像预先创建属于该用户的 `/data`，首次初始化新卷时继承该目录权限。

完成一个任务后，可以保留编号，执行 `docker compose restart api worker`，再查询该编号并下载，验证容器重启后记录与文件仍可使用。
不要为了普通重启删除卷。`docker compose stop` 不删除数据；`docker compose down -v` 会删除项目数据卷，不能用于本阶段的重启验证。

此版本不保证处理中任务被强制中断后的自动恢复：worker 中断可能留下 RUNNING；尚无超时巡视、恢复队列、任务取消或重试接口。重复投递只会被原子状态抢占拦住，不会覆盖原成品。
数据库落盘与消息投递不是分布式事务：若 API 在两者之间中断，任务可能停留在 QUEUED。当前需要人工检查，没有自动补投机制。

## 验证记录与剩余工作

- 单元测试：39 项通过，覆盖上传校验、隔离存储、提交异常、未知编号、失败状态、下载清单和重复任务保护。
- 本机媒体验证：使用隔离目录和 Windows FFmpeg 对真实 Sucai1.mp4 运行上传接口、媒体处理函数及 ZIP 下载检查，得到 900、338 帧。另通过临时本地 HTTP 服务验证了集成脚本的真实上传、查询和 ZIP 下载。两个实验都替换了 Celery 投递，不能代替真实 Docker/Celery 视频联调。
- Compose 配置检查和容器构建通过；真实 `Test1.mp4` 视频通过 HTTP 提交给 Docker worker，完成 12 段切片，状态查询和 ZIP 下载已验证，说明本例共享卷读写正常。实际容器 FFmpeg 版本输出待归档。
- 尚未实现前端页面、精确进度、自动恢复、清理策略、60 分钟 1080p 验证。原视频、中间文件与成品目前保留在卷中，会占用磁盘。
- 服务仅绑定本机，没有用户认证；上传限制在 multipart 解析后与文件复制时检查，不是面向公网的流量限制方案。

参考：https://fastapi.tiangolo.com/tutorial/request-files/


## Test1.mp4 实际验证记录（2026-09-21）

- 任务编号：`8e9ae99c-68f4-441a-b635-b9947dedf537`。
- 原视频 345.5 秒；归一化容器时长约 345.511 秒；计划及成品视频总帧数为 10,365。
- 前 11 段各 900 帧，第 12 段 465 帧。30 fps 下分别为 30 秒与 15.5 秒。
- 查询状态 SUCCEEDED，ZIP CRC、文件清单、全部切片解码帧数、H.264 / AAC 与平均帧率检查通过。
- 原上传脚本错误指定了 `--expected-clips 2`，因此没有执行到 ZIP 检查；上述检查针对已有任务单独完成，不能改写原脚本为 PASS。
- 人工播放检查：待补充确认（用户已确认结果下载基本正常）。
- 结果记录见 [Test1_video_task_verification.json](samples/Test1_video_task_verification.json)。视频和 ZIP 不纳入版本管理。

换新素材且不确定段数时，可以省略 `--expected-clips`；该参数只是测试预期，不控制视频生成几段。
