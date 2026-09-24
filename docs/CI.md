# GitHub 自动检查

工作流为 `.github/workflows/ci.yml`，在推送、PR 和手动触发时运行。Ubuntu 与 Windows 各执行一套后端和前端检查，共四个任务。

## 后端

使用 Python 3.11，安装 `backend/requirements-dev.txt` 与 `backend/requirements-worker.txt`，运行依赖兼容性检查、Ruff 和 `backend/tests/test_*.py`。当前本地发现并通过 51 项测试。

在根目录完成 README 中的本地安装后执行：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check --no-cache backend
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

CI 通过 `PYTHONPATH=backend` 定位包，本地开发通过 `pip install -e backend` 安装。接口单元测试使用临时数据库及队列替身，不需要 Docker、Redis 或 FFmpeg。

## 前端

使用 Node.js 24。`package.json` 固定直接依赖版本，`package-lock.json` 锁定依赖树；CI 使用 `npm ci`。在 `frontend` 目录执行：

```powershell
npm ci
npm run typecheck
npm test
npm run build
```

当前本地 7 项交互与校验测试通过，覆盖上传输入、下载地址、长列表搜索、按需预览、失败任务下载保护和任务选择。构建执行 TypeScript 检查并生成 `dist/`。Vite 使用原生配置加载器，要求 Node.js 24。

## 查看远程结果

推送整改分支后，在 Actions 中打开对应提交的 **ClipForge checks**，确认以下四个任务成功：

- Python 3.11 / ubuntu-latest
- Python 3.11 / windows-latest
- Frontend / ubuntu-latest
- Frontend / windows-latest

绿色只代表该次工作流运行的检查通过。安装依赖失败时，后续测试并没有执行。旧提交的通过记录不能证明本次整改通过。本次本地检查已完成，整改提交的远程 CI 尚待推送后确认。

## 验证边界

本工作流不部署服务，不包含真实 Docker/Celery 联调或长视频性能测试，也没有采集覆盖率。`integration_*.py` 是单独运行的媒体/API 实验。旧版第二周验收和本次迁移验证分别记录在 WEEK2_DELIVERY.md 与 ENGINEERING_REFACTOR.md。
