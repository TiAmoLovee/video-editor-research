# 从保存的 EDL 精确渲染 MP4

2026-10-12，第五周第三步。渲染器直接读取已保存的 EDL，候选排名、文本和评分不参与重新选段。实现位于 `clipforge.editing.render`，任务保存与下载位于 `clipforge.services.rendering`。

## 帧与音频的处理约定

- 每段视频使用 EDL 的整数 `[source_in_frame, source_out_frame)` 裁剪，依次拼接，再以项目有理数帧率建立连续时间戳。结束帧不包含，继续沿用第二周约定。
- 所有素材先按 EDL hash 与实测媒体描述核对；渲染期间持有来源读锁。同一物理文件只核验一次，重复引用不会重新分析或转写。
- 视频和音频分别拼接，避免较长音轨推动视频切点。音频保留源时间戳，先对齐 48 kHz 时钟，再将源帧边界换算为样本位置；每段长度取全局时间线样本边界的差值，避免有理数帧率累计舍入误差。
- 迟起或短音轨按对应时间补齐；有声/无声混合时无声段保留等长静音。全部选段无音轨时不虚构音轨。
- MP4 为 H.264、yuv420p、BT.709；有音轨时为 AAC、48 kHz、立体声。当前要求偶数画幅、素材和项目 profile 一致，最多 128 个片段。重排、重复来源、单帧和素材末帧均可处理。
- AAC 有编码延迟和尾部填充。报告区分精确的编码前样本数与实测 AAC track duration，允许最多一个 1024 样本编码包的封装差异；这不等同于音画偏差，后者用同步标记另测。

成品再次核验逐帧 PTS、真实帧数、画幅、编码、音轨和 ClipForge 工具元数据。先在临时文件完成转码与核验，再独占创建最终文件；不覆盖同名文件，失败清理此次半成品。源核验、转码、输出核验及发布共用超时预算。

## 已保存任务的渲染与下载

按 README 配置 backend、`CLIPFORGE_DATA_DIR`、`FFMPEG` 和 `FFPROBE`；工具继续使用显式参数 → 环境变量 → PATH 的既有优先级。

```powershell
python -m clipforge.services.rendering '<task_uuid>' '<revision_uuid>'
```

独立 EDL 也可渲染；素材引用以指定的本机目录解析：

```powershell
python -m clipforge.editing.render project.edl.json --source-root 'your-absolute-media-directory' --output result.mp4
```

新增 HTTP 接口：

- `POST /tasks/{task_id}/edl/{revision_id}/renders`：无需新候选、排名或渲染参数；只使用指定版本的 EDL。同步处理默认共享 90 秒预算，成功返回 201、render_id 和 Location。
- `GET /tasks/{task_id}/edl/{revision_id}/renders/{render_id}`：返回成片记录和实测核验报告。
- `GET /tasks/{task_id}/edl/{revision_id}/renders/{render_id}/files/{filename}`：无字幕成片的白名单文件为 `output.mp4`、`verification.json`、`source.edl.json`；字幕成片另有 `subtitles.json`、`captions.srt`、`captions.ass`。核验和传输使用同一读句柄，完成或中断后释放。

结果追加在 `jobs/<task_id>/edits/<revision_id>/renders/<render_id>/`。`render.json` 最后写入，记录原 EDL 文件 hash、内容 hash、MP4 hash/大小/profile/帧数和来源快照。失败结果不会成为可下载的成功成片；已有 EDL、SQLite 任务、历史 ZIP 和缓存结果不改写。

## 独立媒体核验

合成素材的预期序列由静态源帧范围定义，不从渲染报告读取。独立解码 MP4 内的二进制帧编号，并解码实际 AAC 双音调及同步标记：

- 9 个案例、420 个输出视频帧逐帧与 EDL 的来源及顺序一致，切点帧误差为 0；视频 PTS/帧数正确。
- 覆盖单帧、间断取段、重排、重复、素材末帧、无音轨、多来源、30000/1001、有声/静音混合及 44.1 → 48 kHz。
- 358 个音频标记顺序正确，最大测得音画偏差 0.0034375 帧，低于 1 帧。这个结果适用于这些带标记的合成检查，不作为任意真实素材的同步保证。

真实 Test3 从前一步保存的 EDL 生成两段拼接 MP4，共 2180 帧。四个片段首尾的输出帧 0、1638、1639、2179，分别匹配源帧 43、1681、112、652；全帧缩略图与预期源序列的最大平均灰阶绝对误差为 0.21224。H.264 有损缩略图不能区分完全相同的相邻帧，因此精确帧结论同时依赖上述独立帧编号检查。

真实任务记录、保存的原 EDL 和 16 个已有文件均未改变；没有模型调用或新增人工质量标签。工作区 `outputs/week5-mp4-render/` 保存脚本、实际 MP4、独立核验报告及测试记录。

本轮相关回归共 113 项：112 通过，1 项 POSIX 专用检查在 Windows 跳过；Ruff 和 diff 检查通过。渲染核心的 stdlib 执行行覆盖试测为 254/270 行、94.07%，排除 CLI，使用现有 12 项渲染单测；不等同于分支覆盖、coverage.py 或第五周完整 CI 验收。

## 本轮边界

本地代码尚未推送或部署；生产网页按钮、完整 HTTP 和 Docker 验收沿用 [任务 EDL](TASK_EDL.md) 中的待验证状态。后续已补 [字幕生成、重映射与烧录](SUBTITLES.md)及 [EDL 0.2 固定中心 9:16](PORTRAIT_RENDER.md)，字幕包含画面“AI辅助剪辑”标识，默认无字幕成片只带工具元数据。竖屏直接读取原归一化素材，原切点和音轨算法保留；完整 Shotcut 界面及字幕/竖屏 MLT 互操作仍待完成。

下一步接入 MLT 的字幕和竖屏效果。片段是否完整表达事件、是否吸引人属于候选算法及人工编辑评测，不由导出成功替代。

滤镜边界语义依据 [FFmpeg trim/atrim/concat 文档](https://ffmpeg.org/ffmpeg-filters.html)；音频迟起对齐依据 [FFmpeg 重采样 first_pts 文档](https://ffmpeg.org/ffmpeg-resampler.html)。
