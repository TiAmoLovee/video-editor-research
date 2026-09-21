# 后台队列最小实验

## 目标和结构

本阶段用加法任务验证“提交消息 → Celery worker 执行 → 查询结果”。任务故意等待 3 秒，不处理视频。

- Redis：Docker 网络内的 `redis:6379`，0 号逻辑数据库保存队列消息，1 号保存任务结果。
- worker：Linux 容器里的独立工作进程，单个并发槽。
- queue_demo.py：由 `docker compose exec` 启动的另一个客户端进程，投递 2 + 3，并最多等待 30 秒获取结果。
- Windows 上的 FastAPI 当前仍只有 `/health`，没有与此队列连接。

## 启动和验证

在仓库根目录，保持 Docker Desktop 已启动：

```powershell
docker compose config --quiet
docker compose up -d --build worker
docker compose ps
docker compose logs --tail 40 worker
docker compose exec worker python queue_demo.py
```

首次构建会下载 Python 基础镜像和 Python 包。成功时 worker 日志显示 `ready`；演示脚本输出任务编号、`final_state: SUCCESS`、`result: 5` 和 `PASS`。初始状态受执行时机影响，可能为 PENDING 或 STARTED。

配置了 Redis AOF 与数据卷，但这不等于已完成任务状态数据库。Celery 结果默认在本项目中保留一天；PENDING 也可能表示编号未知，不能直接用作未来用户任务查询的存在性判断。

`job.get()` 仅用于本次命令行实验等待结果，未来 HTTP 提交接口应立即返回任务编号，不应在请求中等待耗时任务完成。

## 版本记录

首次构建固定直接依赖 `celery[redis]==5.6.3`，其间接依赖由 pip 解析。实验通过后，在 PowerShell 中记录完整解析版本，再重建核对：

```powershell
docker compose exec -T worker python -m pip freeze | Out-File -Encoding ascii requirements-worker.txt
docker compose up -d --build worker
docker compose exec worker python queue_demo.py
```

当前 Python 基础镜像为 `python:3.11-slim-bookworm`，尚未固定镜像摘要，不能视为位级一致的构建。用户本地 `.venv` 与 worker 的 Linux 环境分别管理依赖。

## 当前范围

本阶段未实现视频后台处理、任务数据库、HTTP 任务接口、进度推送或下载。停止容器不会删除已有 Redis 数据卷；处理其他项目时不要混用 Compose 配置。

此更新包只做了 Python 语法与 Compose 配置检查；真实镜像构建、Redis 连接及任务执行需在用户 Docker 环境完成并记录结果。
