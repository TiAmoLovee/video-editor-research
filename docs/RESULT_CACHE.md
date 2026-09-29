# 第三周：持久化分析与成品结果缓存

日期：2026-09-29。实现及本地真实模型验收完成，Docker/Celery 部署验收待执行。前置版本是已安装的镜头框选试用版。本次保留 6 帧设置，不包含未推广的 5 帧候选。

## 行为

- 同内容、同配置再次上传，复用成功任务的镜头、VAD、ASR、汇总及切片，跳过归一化、三路分析和切片，重新打包本次 ZIP。
- 文件名改变仍可复用。视频内容、扩展名、框选/检测模式、ASR 模型与固定文件哈希、语言、源码、Schema、相关依赖、FFmpeg/ffprobe 版本变化会失效。
- 模型文件在构建缓存键时仍校验。无法建立键时回退正常处理，由原处理步骤报告实际错误，不通过缓存掩盖错误。
- 只索引完整成功且通过汇总校验的任务。命中时检查各文件大小和 SHA256、汇总契约及切片清单；缺失或损坏时重新处理并修复索引。
- 同键使用进程间锁，进程退出自动释放；索引临时写入后原子替换。等待超过 15 分钟则绕过缓存独立处理，不共用可写输出。
- 新任务独立复制文件，不使用硬链接；修改新任务不会污染缓存源任务。
- 索引位于共享数据目录 result-cache-v1，引用已有成功任务输出，不另存全量缓存视频。源任务输出被删除后会重算；当前没有 TTL 或自动清理。
- 命中任务不恢复 normalized.mp4 等中间文件。下载 JSON 和切片完整保留；分析 JSON 中耗时是原始计算耗时，不是这次命中耗时。

## 真实 Test3 实验

完整 Test3.mp4，small CPU int8 模型。每轮 worker 在新 Python 进程执行，第三轮同时改显示文件名。

|轮次|命中|实际转写调用|后台总耗时|
|---|---|---:|---:|
|首次|否|1|252.530 秒|
|第二次，新进程|是|0|1.333 秒|
|第三次，新进程并改名|是|0|1.291 秒|

两次命中的耗时中位数相对首次下降 **99.48%**，达到 80%：**是**。

计时包括 worker 内缓存键计算、模型及文件校验、分析/复用、复制、ZIP、索引发布；不包括上传、排队和浏览器轮询。这是一个真实素材的本地实验，不保证所有素材都达到相同加速。

三次 analysis.json 和全部切片哈希相同，HTTP 下载适配器及 ZIP CRC/JSON 一致性通过。HTTP 使用 TestClient，实际处理在本地子进程，未启动真实 Celery/Docker。本地性能通过不能冒充部署验收。

## 验证

- 172 项后端测试通过，包括 10 项缓存测试：成功复用、参数变化、改名、损坏/缺文件、失败任务、不缓存半成品、并发、终止后解锁、关闭缓存及路径检查。
- 前端 17 项测试、类型检查及生产构建通过，新增“本次处理与复用记录”下载入口。
- 项目 Ruff 检查、Compose 与 small 覆盖配置解析通过。
- 缓存特性测试显式启用缓存；原流水线模拟测试关闭缓存，真实模型实验启用缓存。模拟输出不作为媒体质量证据。

## 安装与验收

安装器先核对已知文件版本、备份后应用；未知修改会导致预检停止，不自动提交或推送。安装器和对应 ZIP 放在同一个 outputs 目录。以下命令不依赖 PowerShell 当前目录。

先运行：

~~~powershell
& "D:\CodeResearch\video-editor-research\.venv\Scripts\python.exe" "C:\Users\asus\Documents\Codex\2026-09-28\w\outputs\Install-ClipForge-result-cache.py" --repo "D:\CodeResearch\video-editor-research"
~~~

确认出现 APPLIED 或 CHECK OK: 0 后，再运行：

~~~powershell
docker compose --project-directory "D:\CodeResearch\video-editor-research" -f "D:\CodeResearch\video-editor-research\compose.yaml" -f "D:\CodeResearch\video-editor-research\compose.asr-small.yaml" up -d --build api worker
~~~

默认启用。在项目 .env 设置 CLIPFORGE_RESULT_CACHE=0，再更新容器配置可关闭；改回 1 恢复。本地 Python 使用同名环境变量。旧任务下载兼容，但只由新版成功任务建立索引，不自动信任旧任务补建缓存。

1. 重建成功后刷新网页，上传 Test3，固定同一试用开关和框选设置（可都不勾选），等到成功。
2. 同一文件、相同设置再次上传。新任务有自己的任务编号和下载链接。
3. 在“处理记录与参数文件”下载“本次处理与复用记录” cache.json。首次应 hit=false、status=miss；第二次应 hit=true、transcription_executed=false。
4. 同时核对分析 JSON 和切片。cache.json 的 worker_seconds_before_packaging 不含 ZIP/索引发布，不能充当完整后台总耗时；正式部署计时需记录 worker 首尾时间。

缓存不会纠正分析质量问题，模型/参数/代码更新会换键重算。本地实验与后续部署记录采用不同计时范围，见以下补充及 WEEK3_ACCEPTANCE.md。

## 2026-09-29 部署复用核对

用户完成重建并顺序上传相同视频：首次任务 8cfdcbc8-34e4-4bfd-a3ba-11ba4328fb48，第二次 ee8face6-b5e4-4fd4-b5f0-7d96c83fe2bf。两次键相同，后者 hit=true、transcription_executed=false，并明确引用首次任务。

worker_seconds_before_packaging 从 187.525713 降至 3.604821 秒，下降 98.0777%，按此处理口径超过 80%；不包含上传、队列、ZIP、缓存发布，不称作部署端到端总耗时。原始记录与统计保存在 evaluation/week3-closeout/cache/。两份记录没有输出文件哈希，不能替代全量输出一致性检查。
