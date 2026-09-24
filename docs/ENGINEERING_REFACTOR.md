# 第二周工程整改说明

日期：2026-09-24。基线：`main` 的 `a91e0b3`。整改分支：`refactor/week2-engineering`。

老师认可媒体处理链路，同时指出前端选型、目录结构与运行文档的工程问题。本次保留原有功能与视觉风格，补齐这些要求；不增加第三周分析功能。

## 反馈与落实

| 反馈 | 本次修改 | 检查位置 |
| --- | --- | --- |
| 前端需按任务书选型 | React 18、TypeScript 5、Vite、Zustand 4、Ant Design 5 实际参与页面、类型、状态和组件构建 | `frontend/package.json`、`frontend/src/` |
| 根目录平铺 | 后端按 HTTP、媒体、处理流程、存储、队列拆分；前端、测试与部署独立目录 | `backend/clipforge/`、`backend/tests/`、`deploy/` |
| 个人工具路径硬编码 | 支持环境变量及 PATH，保留命令行覆盖；运行文档使用通用配置与自备素材 | `backend/clipforge/config.py`、README、`.env.example` |

前端依赖主版本按老师要求固定，精确版本由 package.json 与锁文件记录。Zustand 管理选中任务、任务列表、分页与连接状态；React 组件负责上传、历史、详情和切片结果；Ant Design 提供上传、按钮、进度、步骤、状态与输入组件。HTTP 请求和可取消轮询独立封装。

Vite 开发服务默认 5173，代理到本机 API 8200。部署时，API 的 Dockerfile 先运行 Node 构建，再让 FastAPI 提供 `dist/`；不需要在生产服务中启动 Vite。

## 兼容性

- HTTP 路径、任务编号和下载地址保持原协议。
- Celery 注册任务名 `clipforge.process_video` 与 `clipforge.add` 保持不变，Python 导入路径变为 `clipforge.queue.tasks`。
- SQLite 仍读取 `tasks.sqlite3`，表结构与 `jobs/<任务编号>/` 文件布局保持不变。
- Compose 项目名 `clipforge`、8200 端口、`media_data` / `redis_data` 数据卷保持不变。
- 已加入旧数据库读取回归测试；运行中任务须先完成再重建服务，不能把本次迁移当作中断恢复功能。

## 本地已验证

- 51 项 Python 单元测试、Ruff 和 Compose 配置解析通过。
- TypeScript 检查、7 项前端测试、Vite 生产构建通过。
- 隔离预览读取旧数据副本，原有成功与失败任务显示正常。
- React 页面上传 Sucai1.mp4，真实媒体函数生成两段，页面显示 30.00 秒与 11.27 秒。
- 刷新保留所选任务；浏览器预览播放与 ZIP 下载触发成功，ZIP CRC、文件清单及两段实际解码帧数 900 / 338 均通过，[机器可读记录](samples/engineering_refactor_verification.json)已归档。
- 1440 像素桌面与 390 像素窄屏没有横向页面溢出；检查期间未捕获浏览器控制台错误。

预览使用独立数据副本、本机 FFmpeg 与线程调度，未改动现有 Docker 数据。它验证页面、HTTP 和媒体函数的组合，不代表迁移后 Docker/Celery 验收。旧版 60 分钟实验没有在本次重新执行。

## Docker 部署回归（2026-09-24）

用户提供的 Compose 输出显示 API 和 worker 重新创建并启动，Redis healthy。随后用户确认旧任务与旧 ZIP 可用，新上传短视频处理、预览、下载及刷新恢复均正常。

助手另外读取正式服务 8200 的任务 `0ab4374c-f874-4926-a1dc-e081e5fe51bb`：Sucai1.mp4 为 SUCCEEDED，生成 2 段；下载 ZIP 后 CRC 检查通过，清单帧数为 900 / 338。[此次容器回归记录](samples/engineering_docker_verification.json)区分了用户确认和独立核验的项目。未再次逐段解码，也未取得重建后的 worker 版本输出。

## 后续复现步骤

确认原任务全部结束后，在项目根目录执行：

```powershell
docker compose config --quiet
docker compose up -d --build api worker
docker compose ps
docker compose exec -T worker ffmpeg -version
```

打开 http://127.0.0.1:8200/ 并刷新页面，依次检查：

1. 原历史任务仍可查看，原 ZIP 可下载。
2. 上传一份短视频，等待完成，确认切片和 ZIP 可下载。
3. 刷新页面后仍选择同一任务；切片预览与搜索可用。
4. 推送整改分支，查看本次提交在 GitHub 的四项 CI 任务。

不要执行删除数据卷的命令。此次 API 镜像增加 Node 构建阶段，首次需要下载 Node 基础镜像与 npm 依赖；网络失败需按具体输出排查。旧分支仍保留，可切回后重建 API 与 worker；没有数据库升级需要回滚。

## 尚未声明完成的部分

整改后的 Docker 短视频流程已按上述范围验收，远程 CI 仍待推送后确认。本地构建提示 JavaScript 主包超过 Vite 默认 500 kB 提醒阈值（压缩前），构建通过；后续可按性能测量决定进一步拆包。没有新增覆盖率、资源峰值或音画同步测量结果。
