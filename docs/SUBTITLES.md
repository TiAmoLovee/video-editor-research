# 保存 EDL 的字幕导出与烧录

字幕从同一保存版本的 `analysis.json` 词时间生成，所有片段范围和顺序由 `project.edl.json` 决定。没有新 ASR、百炼请求、候选评分或人工质量标签。EDL 0.1.0 保持兼容；`subtitles.schema.json` 定义派生的字幕计划，并绑定 EDL 内容 SHA、源媒体 SHA、原分析内容 SHA。改变片段顺序或范围后必须基于新 EDL 生成新字幕版本，不能复用旧时轴。

## 时间与文字

每个词先与片段的半开源区间求交，再把源帧位置映射到 EDL 的成片帧位置。重复、重排和多源在核心转换器中逐片段处理，不能把原片秒数直接当成成片秒数。跨切点词及极短词有明确证据；字幕不能越过片段尾进入下一个场景。

使用原词序列分组，按句子、停顿、长度和持续时间拆行。同一句、同停顿组、同片段内的短尾可并回前组至最多 4.5 秒，或借末尾词重新分组，并保留例外证据；不改词时间。原 ASR 识别错误不在导出时猜测修正。超长词、重叠词等特殊情况在计划内记录；无法用 ASS 的百分之一秒时钟表示、或量化后没有任何视频帧能显示的极短事件明确失败，不静默丢词。SRT 导出毫秒，ASS 导出百分之一秒，起点向后、终点向前取整。ASS 另有全片右上角“AI辅助剪辑”标识；字幕正文在画面下方。转写中的 ASS 样式控制符及 SRT 尖括号作为字面字形处理，原词证据不变。

自动检查证明字幕遵守保存的词时间和 EDL，不等于证明原 ASR 时间与实际说话音频的偏差 ≤0.3 秒，也不等于证明文字识别正确。此项人声准确度验收尚未新增，保留原任务书要求。

## 保存与服务

版本路径：`jobs/<task>/edits/<revision>/subtitles/<subtitle-id>/`。包含字幕计划 `subtitles.json`、SRT、ASS、EDL 快照；`subtitle-version.json` 最后写入，记录全部文件 SHA 和原分析文件 SHA。旧 EDL、分析、ZIP、任务状态及历史人工记录不修改。

| 接口 | 作用 |
|---|---|
| `POST /tasks/{task}/edl/{revision}/subtitles` | 从此版本的保存转写生成字幕 |
| `GET /tasks/{task}/edl/{revision}/subtitles/{subtitle}` | 读取版本与词时间映射 |
| `GET .../subtitles/{subtitle}/files/{filename}` | 下载 SRT、ASS、计划、EDL 快照 |
| `POST .../subtitles/{subtitle}/renders` | 按同一 EDL 烧录保存字幕并生成新的 MP4 |
| `POST .../subtitles/{subtitle}/exports/mlt` | 导出同版 EDL 的保存字幕，使用整数 cue 范围及原字体名称 |

烧录使用现有渲染服务，仍从原归一化素材按 EDL 整数帧剪辑；不会再次切割前一轮已编码 MP4。新成片另保存实际烧录的 ASS、SRT、字幕计划和核验报告，并绑定字幕版本。下载先核对 SHA，并持续持有同一文件句柄。旧无字幕渲染接口与文件格式继续可用。

已有任务服务当前只接入单个源分析快照；核心转换器支持多个源。API 为同步原型，共享 90 秒处理预算；较长素材可用 CLI 明确指定更长预算。React 页面已在本地接入新接口及历史恢复，见 [网页导出](WEB_EXPORT.md)；真实 HTTP 和生产 Docker 尚未核验。

## 工具配置

沿用第二周的 `FFMPEG`、`FFPROBE` 及 `media_tool()` 配置优先级。烧录所用 FFmpeg 必须支持 `ass` / libass，字体必须覆盖所需中文字符。额外配置 `SUBTITLE_FONT_FILE` 和 `SUBTITLE_FONT_NAME`，CLI 显式参数优先。缺工具、缺字体、缺字、字体来源不匹配均停止发布，不能静默生成无字幕成片。

API 镜像安装 `fonts-noto-cjk`，配置 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` 与 `Noto Sans CJK SC`；本地 Windows 验证显式使用电脑现有字体，不把个人路径写进产品代码，也不向仓库上传 Windows 字体。原生代码不会自动读取 `.env` 文件。

字幕渲染在系统临时目录内使用固定的相对文件名，完成核验后持锁复制发布。实际对照证实旧 Windows FFmpeg/libass 无法读取超长工程目录下的 ASS；短临时路径已修复此问题。读取字体或 ASS 的失败 warning 同样拒绝发布，不允许依赖同名字体回退产生表面成功。内部日志仅保留有界诊断尾部，API 不暴露本机路径和工具输出。

```powershell
$env:PYTHONPATH = 'backend'
python -m clipforge.services.subtitling <task-id> <revision-id> --font-name 'Noto Sans CJK SC'
python -m clipforge.services.rendering <task-id> <revision-id> --subtitle-id <subtitle-id> --font-file <font-file> --font-name 'Noto Sans CJK SC' --timeout 3600
```

核心函数：`build_subtitles(edl, analyses)`、`validate_subtitles(plan, edl, analyses)`、`to_srt(plan)`、`to_ass(plan, font_name=...)`，以及 `render_edl(..., subtitle_plan=plan, analyses=analyses, font_file=..., font_name=...)`。完整重建核对字幕计划，不能只检查时间值合法而接受错源或被改动的词。

## 验证记录

本轮记录位于工作区 `outputs/week5-subtitles/`，实际结果以该目录 JSON 为准。独立测试核对字幕出现/消失帧、源帧顺序、重排、临界切点、空白、理数帧率及篡改拒绝；真实 Test3 用此前已保存 EDL 与转写生成带字幕成片。保留原文件和任务状态的前后 SHA 核对。不重新要求用户评价同一批旧片。

3 组合成案例共 301 帧源编号与 20 条字幕显示均通过，全部帧保留 AI 标识，10 项负控制拒绝；最大 SRT/ASS 序列化误差 0.0099 秒。该检查记录的是临时路径修复前的实际 renderer SHA，不改写为新版核验。路径修复另有同一真实源的单帧对照及最终 Test3 补证，视频和音频取段图谱经独立检查未改。

最终 Test3 带字幕成片共 2180 帧、21 条字幕，使用保存的两个技术对照片段；不是新增的叙事质量样本。首尾及切点两侧 4 个关键帧用遮住字幕/标识的原分辨率 RGB 独立比较，在 ±3 帧窗口均唯一对应源帧 43、1681、112、652。最初 64×36 中部缩略图丢失静态画面的细微差异而失败，保留该诊断，并以绑定源/成品 SHA 的更大视域核验补证，没有重编码或放宽最近帧判据。全部 2180 帧仍通过原缩略图顺序一致性检查。SRT 最大导出时标误差 0.000667 秒，ASS 为 0.006667 秒；这两个值不代表与真实语音的误差。成片截图确认中文字形和 AI 标识可见，原转写错字保留。

导出时标相对整数字幕网格的误差、相对原始分数词时间的误差和实际视频帧采样显示误差分开报告。理数例子的实际显示相对整数 cue 可迟 1 帧、相对原词时间可迟 1.5 帧；因此不把小于 1 帧的序列化误差宣称为原语音同步误差。

相关回归 150 项：149 通过、1 项 POSIX 检查在 Windows 跳过；长路径修复后 21 项受影响检查通过，其中包含新增的有界诊断回归。新 renderer 核心执行行试测 489/544、89.89%，排除 CLI、媒体工具使用 mock；不等同于分支覆盖或第五周完整 CI。原 EDL/MLT 核心行试测及默认渲染行为保留，完整 HTTP/容器限制也保留。

后续已补 [9:16 裁剪及统一配置](PORTRAIT_RENDER.md)，竖屏生成新的字幕布局，原横屏字幕字节保持兼容。[MLT 字幕导出](MLT_EFFECTS.md) 接入保存计划和字体名称，并完整复制原计划、SRT 与 ASS 文件；使用精确整数 cue 的 RichText／深色面板，与 ASS 厘秒及字形描边的表示不同。首帧单帧 cue 另有通过原生验证的 opacity 门控，避免 MLT 把 `out=0` 当作未结束；字幕本身一帧可以表示，但含音单帧选段因引擎音频限制明确拒绝，改用同一 EDL 的 MP4。字体家族需在 Shotcut 本机安装，不能据 XML 推断字形可用。新 MLT 效果五例原生核验共 87 帧、13 条 cue 已通过。真实完整 MLT 渲染和一个真实 Shotcut 打开／另存回读已完成；剩余五例 GUI、效果编辑控件、真实语音字幕同步、完整 HTTP／容器回归及授权发布。网页已在本地接入导出和历史恢复。

依据：[FFmpeg 的 ASS 过滤器文档](https://ffmpeg.org/ffmpeg-filters.html#ass)、[Debian 的字体包文件清单](https://packages.debian.org/bookworm/all/fonts-noto-cjk/filelist)。
