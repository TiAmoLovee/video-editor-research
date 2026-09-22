# GitHub 自动检查

## 本次改动

将 `.github/workflows/ci.yml` 的 lint / test 占位任务替换为 `ClipForge checks`。推送、拉取请求及手动触发时，分别在 Ubuntu 和 Windows 上使用 Python 3.11 执行：

1. 安装开发与 worker Python 依赖；Ruff 固定为 0.16.8。
2. `pip check` 检查依赖兼容性。
3. Ruff 检查 Python 语法、部分控制流错误及未定义名称，规则为 E9、F63、F7、F82。
4. `unittest` 发现并运行 `tests/test_*.py`，目前为 43 项。

检查不会自动修改代码；本次未要求全仓库统一格式或启用全部风格规则。workflow 只读仓库，不部署服务、不访问开发机的任务和视频。同一分支新提交会取消该分支尚未完成的旧 CI，不影响本机 Docker。

## 本地执行相同检查

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-worker.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check --no-cache .
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

首次安装新增的 Ruff 需要联网；遇到下载失败时保留错误信息排查，不通过跳过检查来获取绿色结果。

## 如何查看 GitHub 结果

1. 提交并推送本次配置到 `week2/mvp0`。
2. 打开仓库的 Actions 页面，选择本次提交对应的 **ClipForge checks**。
3. 确认 `Python 3.11 / ubuntu-latest` 与 `Python 3.11 / windows-latest` 两个任务都成功。
4. 展开 Run unit tests，确认日志显示实际发现的测试数和 OK；记录提交编号及本次运行链接。

黄色表示等待或执行中；红色时查看第一个失败步骤。若依赖安装失败，测试并未运行，不能记录为测试通过。旧的 CI placeholders 绿色结果仍不代表真实测试。

首次运行前，本次工作只记为“配置已完成，本地预验收通过，GitHub 运行待确认”。手动触发入口的可见性受工作流是否在默认分支影响，首次以推送自动触发为准。

## 验证范围

接口单元测试使用队列与媒体处理替身，数据库使用临时目录，不需要启动 Redis、Docker 或 FFmpeg。`integration_*.py` 未纳入本工作流，真实媒体、容器联调与 60 分钟素材继续单独验收。

本地已检查 Ruff 及 43 项测试；在 GitHub 执行之前，不能宣称 Ubuntu 或远程检查通过。本次未采集测试覆盖率，也不声明满足需求中的覆盖率目标。

配置参考：[Ruff 官方 GitHub Actions 集成](https://docs.astral.sh/ruff/integrations/)、[setup-python](https://github.com/actions/setup-python)、[checkout](https://github.com/actions/checkout)。
