# 后台队列与 HTTP 演示

## 当前阶段

已在用户本机通过 `queue_demo.py` 验证 Redis、Linux Celery worker 和结果查询，2 + 3 返回 5。
本次新增 HTTP 提交和查询接口。它们仍是加法演示，不处理视频。

后续新增的视频上传与处理接口使用 `/tasks`，运行方法、SQLite 状态和下载说明见 [VIDEO_TASKS.md](VIDEO_TASKS.md)。本页只记录 `/demo/tasks` 的加法实验。

结构：浏览器或测试脚本 → FastAPI 容器 → Redis 队列 → worker → Redis 结果 → 查询接口。
API 和 worker 通过 Docker 内网访问 `redis:6379`，无需向 Windows 开放 Redis 端口。
API 仅映射到本机 `127.0.0.1:8200`。容器内监听 `0.0.0.0`，不等于向局域网开放宿主机端口。

## 启动

保持 Docker Desktop 运行。ClipForge 使用宿主机 8200 端口，容器内仍使用 8000，映射为 `127.0.0.1:8200:8000`。不需要停止使用其他端口的项目。

```powershell
docker compose config --quiet
docker compose up -d --build api worker
docker compose ps
```

此后使用 Compose 启动 API，不再同时运行原来的 Windows Uvicorn 命令。
重新启动已有容器可以使用 `docker compose up -d`；修改打包进镜像的 Python 文件后需重新 `--build`。
关闭 PowerShell 窗口不会停止以 `-d` 方式启动的容器；本项目可用 `docker compose stop` 停止服务。

## 接口实验

打开 http://127.0.0.1:8200/docs 。

1. 展开 `POST /demo/tasks`，点击 Try it out，输入 `{"left": 2, "right": 3}`，点击 Execute。
2. 返回 HTTP 202、`task_id` 和 `status_url`。202 只表示提交已获接受，不表示任务成功。
3. 将 task_id 填入 `GET /demo/tasks/{task_id}` 并执行。可能先看到 PENDING 或 STARTED，稍后得到 SUCCESS 和 result=5。

POST 不等待加法完成；GET 只读取一次状态，不阻塞等待最终结果。
参数限定为 -1000000 到 1000000 的整数；非法参数或非 UUID 任务编号返回 422。
Redis 连接失败时返回 503。查询到 FAILURE 时返回通用说明，详细异常留在 worker 日志。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\integration_queue_api.py
docker compose logs --tail 40 api worker
```

单元测试用队列替身检查接口逻辑，不连接 Redis。集成脚本通过真实 HTTP 提交 2 + 3，轮询结果，并检查参数校验和未知编号行为；本地连接不走系统代理。
命令行队列实验仍可运行：`docker compose exec worker python queue_demo.py`。

## 状态语义和限制

- PENDING 表示 Celery 没有状态记录：可能尚未执行，也可能编号不存在或结果已经过期。接口提供 note 说明，不能据此宣称任务存在。
- 结果保留一天。Redis AOF 与持久化数据卷不等于已完成任务数据库、任务历史和重启恢复验收。
- 加法演示接口不处理视频；视频接口初版另见 VIDEO_TASKS.md。前端页面尚未实现。当前没有用户认证，接口仅供本机学习验证。
- `/health` 仅表示 API 进程正常响应，不证明 worker 或 Redis 可用。
- 提交失败不能保证消息一定未投递；当前没有幂等提交机制，不应直接沿用为生产视频任务接口。

## 依赖与验证记录

API 镜像安装 `requirements.txt` 与 `requirements-worker.txt`；worker 保持独立依赖。
`celery[redis]==5.6.3` 固定直接依赖，间接依赖尚未完整锁定；Python 基础镜像尚未固定摘要。
Windows `.venv` 用于本地媒体开发与测试；不需要在 Windows 运行 Celery worker。

2026-09-21 验证记录：29 项单元测试及 Compose 配置检查通过；本机真实 HTTP → Redis → worker → 查询结果实验通过。观察到 PENDING → STARTED → SUCCESS，result=5；参数校验和未知编号语义检查通过，集成脚本输出 PASS。该实验只验证加法任务，不代表视频处理闭环已完成。

测试脚本默认地址已统一为 `http://127.0.0.1:8200`，也可使用 `--base-url` 指定其他地址。

参考：
- https://docs.celeryq.dev/en/stable/userguide/tasks.html#pending
- https://fastapi.tiangolo.com/tutorial/response-status-code/
