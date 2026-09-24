# 第二周异常输入与失败后继续处理验收

> 工程整改后请先按 [README](../README.md) 安装 `backend` 包并设置 `FFMPEG` / `FFPROBE`。示例素材放在自行准备的 `downloads/fixtures/` 中；旧验收结果保留原时间，不表示本次重新运行。

日期：2026-09-22。环境：实际 Docker API、Redis、Celery worker，页面与接口均访问本机 8200。

## 本次结果

| 场景 | 实际结果 |
| --- | --- |
| 文本扩展名上传 | HTTP 415，拒绝不支持的类型 |
| 空 MP4 上传 | HTTP 422，拒绝空文件 |
| 非空但无效的 MP4 | 页面提交后，后台在 probing 阶段记录 FAILED，result 为 null |
| 失败任务页面 | 显示“处理失败”及失败阶段，刷新后同一任务仍可查看，没有结果下载链接 |
| 直接请求失败任务 ZIP / MP4 | 两个请求均为 HTTP 409，没有开放下载 |
| 随后提交正常 1 秒视频 | SUCCEEDED，1 个切片，清单为 30 帧；ZIP CRC 与文件清单核验通过 |
| 正常任务页面 | 显示已完成、1 个切片、1.00 秒与下载入口 |
| 服务状态 | 健康检查仍返回 ok，原失败任务仍可查询 |

失败任务：`3b5543b5-a835-4468-afd2-f5eda310aa19`。正常后续任务：`a846c99a-4f6f-4d34-b7a8-30eb3f210ac5`。

`intentional_corrupt_sample.mp4` 是刻意制作的无效视频验收样本，历史列表中的这条失败记录是预期结果。测试没有损坏或删除原视频，也没有重启服务。`recovery_sample.mp4` 为合成的 320×180、30 fps、1 秒无音轨视频。

## 复现方法

新增 [integration_failure_api.py](../backend/tests/integration_failure_api.py)，用真实 HTTP 调用检查拒绝上传、失败状态、下载保护和失败后正常处理。它不属于 `test_*.py` 单元测试集合，不会被每次推送的 CI 自动提交到本机服务。

先启动本地服务，在仓库根目录用自己的 FFmpeg 路径生成一个小视频：

```powershell
New-Item -ItemType Directory -Force downloads\fixtures
& $env:FFMPEG -hide_banner -n -f lavfi -i "color=c=green:s=320x180:r=30:d=1" -c:v libx264 -preset ultrafast -pix_fmt yuv420p "downloads\fixtures\recovery_sample.mp4"
.\.venv\Scripts\python.exe backend\tests\integration_failure_api.py --recovery-video "downloads\fixtures\recovery_sample.mp4"
```

`$env:FFMPEG` 是开发机路径，其他机器需替换为实际位置。若样本文件已生成，可直接执行最后一行，无需覆盖旧文件。

默认运行会新增一个故意失败的任务和一个正常任务，报告保存到 `downloads/failure_verification.json`。测试已有失败任务时可加 `--failed-task-id <UUID>`，避免再提交损坏样本；仍会提交一个正常任务以验证后台继续处理。脚本最多接受 10 MiB 的正常测试素材，适用于短小验收样本。

本次先通过实际网页提交损坏样本，然后使用 `--failed-task-id` 运行接口检查，再回到网页核对刷新后的失败状态与后续成功任务。浏览器检查另行记录在[验收 JSON](samples/failure_verification.json)中，不由脚本自动声称完成。

## 范围与限制

这次证明输入处理失败后能记录错误、保护下载，并继续处理一个正常任务；不等于强制中断 worker 后的自动恢复。没有测试磁盘不足、资源耗尽、超 1 GiB 实际上传或编码途中失败，也不测量音画同步。

错误提示目前给出失败阶段和查看 worker 日志的说明，尚未将所有媒体错误细分为面向用户的具体原因。短视频脚本检查 ZIP 和清单帧数，本次没有单独解码这个 1 秒结果；60 分钟任务的逐段解码证据见[长视频验收](LONG_VIDEO_TEST.md)。

## 本轮交付检查

长视频文档提交 `a30d8ec` 的 [GitHub 检查](https://github.com/TiAmoLovee/video-editor-research/actions/runs/35681686427)已确认 Ubuntu / Windows 两个任务成功。本次新增脚本通过同一 Ruff 规则检查，实际接口实验通过；本轮新提交的远程结果应在推送后另行查看。
