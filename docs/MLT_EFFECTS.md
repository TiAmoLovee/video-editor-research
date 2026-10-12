# 保存 EDL 的竖屏与字幕 MLT 导出

2026-10-12，第五周后续实现。MLT 现在支持 EDL 0.1 横屏及 EDL 0.2 固定中心 9:16，也可接入同一 EDL 的已保存字幕版本。原横屏 profile、片段顺序、包含末帧的范围及 API 约定保留；重复使用含音轨来源时调整 XML 解码器结构以修复实际音频丢失。代码仍在本地工作区，未上传或部署；React 已完成本地 [网页导出与历史恢复](WEB_EXPORT.md)，交付范围见 [第五周交付核对](WEEK5_DELIVERY.md)。

## 共用事实来源

`export_mlt(edl, source_root, require_media=True, *, subtitle_plan=None, analyses=None, font_name='Noto Sans CJK SC')` 先核验 EDL。字幕必须同时提供完整的原分析，调用 `validate_subtitles()` 重建核对计划；不能拿任意 SRT、ASS、改动的词或其他版本字幕直接插入工程。提供分析但没有字幕计划也会被拒绝。

所有选段继续使用 `[source_in_frame, source_out_frame)`，MLT entry 为 `in=source_in_frame`、`out=source_out_frame-1`。素材引用解析为本机普通文件，并核对字节 SHA；源文件不重新转写或评分。无声重复来源仍可共享 chain；含音轨来源被重复取段时为每个片段使用独立 avformat 解码器，避免共享解码状态使后续片段丢失 PCM。这一修复同时适用于 EDL 0.1／0.2，不改变时间线顺序或流编号，也不复制媒体文件。

竖屏 profile 来自 EDL 0.2，native source 描述保持原宽高。每个 chain 的 crop 仅使用保存并重新验证的 `framing.sources`：左、上为 `x/y`，右、下为原尺寸减去位置及窗口尺寸。`use_profile=0` 保留原素材像素单位，crop 在加载器归一化／缩放前生效；不会把 profile 换成 1080×1920 就当作已执行裁切。当前仅固定中心裁切，没有主体跟踪。

## 字幕表示与差异

字幕和全片“AI辅助剪辑”标识作为最终 tractor 上的过滤器，使用成片时间线，不挂在源 chain 上。每条 cue 的 MLT `in` 等于保存的 `timeline_in_frame`，`out` 等于 `timeline_out_frame-1`，全片标识覆盖 `0..duration_frames-1`。这样重复、重排来源时不会套错源时间。

新导出使用纯文字 `qtext`，通过 `shotcut:filter=dynamicText` 选取 Shotcut 的“文本：简易”编辑控件；实际渲染服务仍为 `qtext`，不会展开 `#timecode#` 或把尖括号当 HTML。原词和显式换行直接保存于 `argument`，字体、字号、白色和对齐独立保存。这样替换整段文字不会继承富文本编辑器的默认字号和段落样式。

在已验证的 Shotcut 26.8.1／96 DPI 环境中，MLT 字号从共享布局的请求值向下量化为 4px 的整数倍，并明确保存 `shotcut:pointSize` 和 `shotcut:usePointSize=1`，避免打开控件时再次舍入。小于 4px 的拟合和超过 256 个 UTF-16 单位的初始文字明确拒绝导出，不修改原计划、不截断；emoji 按两个 UTF-16 单位计算。字幕框预留政策规定的两行并底部对齐，品牌为一行顶部右对齐。实际行距由字体决定，背景按文字外框绘制，长行仍可能整体缩小；用户在 Shotcut 新增超过控件限制的文字仍可能被编辑器截断。没有承诺任意 DPI、任意编辑长度或与 ASS 逐像素一致。

MLT 的白字置于半透明深色面板，ASS 使用白字及字形描边；这是两种表示的样式差异，不承诺逐像素相同。MLT cue 直接使用整数帧，ASS 的词字幕起点向后、终点向前量化至厘秒；边缘出现／消失帧可能不同。MLT 将 `out=0` 解释为未设置结束点，因此首帧单帧 cue `[0,1)` 及单帧全片标识另有受控 opacity 门控：帧 0 为 1、帧 1 为 0，保留声明的整数范围。此门控已通过单独原生验证。两种字幕均绑定同一保存词时间，但都不能据此证明 ASR 与真实语音达到 ≤0.3 秒。

MLT 只保存字体家族名称，采用所选字幕版本的 `font_name`，不会因当前环境变量改变旧版本。Shotcut 所在机器必须安装该字体并覆盖所需字形；系统可能回退到其他字体，生成 XML 本身不能验证字体实际可用性。没有复制或上传个人字体。FFmpeg 烧录使用的字体文件验证继续沿用 [字幕服务](SUBTITLES.md)。

## 保存、读取与下载

实现位于 `clipforge.services.mlt_exporting`。每次创建独立的 `jobs/<task>/edits/<revision>/exports/mlt/<export-id>/`，不会覆盖原 EDL、分析、历史成片、ZIP、任务记录或已导出的工程。

| 文件 | 内容 |
| --- | --- |
| `project.mlt` | 由保存版本生成的 XML |
| `source.edl.json` | 原保存 EDL 的完整原始字节 |
| `subtitles.json`、`captions.srt`、`captions.ass` | 仅带字幕导出包含；完整复制所选字幕版本的原始字节 |
| `mlt-export.json` | 完成记录，最后写入；包含任务／版本／导出身份、源媒体 SHA、EDL 文件及内容 SHA、profile、帧数、片段数和全部导出文件 SHA；竖屏另记 framing，字幕另记版本、计划 SHA、字体及 cue 数 |

创建期间持续持有经过核验的 EDL、源媒体及字幕快照句柄，完成记录写入前再次核对源 hash。读取重新核对所有文件、记录身份、保存的父版本及字幕来源，并重新生成 XML 比对；不能通过只修改记录中的文件 hash 让错误切点或字幕变成有效工程。`subtitle_id` 若出现，必须是有效的规范 UUID，null 或无效值不能跳过字幕来源验证。失败清理本次新目录，编号冲突不会删除已有版本。

| API | 作用 |
| --- | --- |
| `POST /tasks/{task}/edl/{revision}/exports/mlt` | 导出保存的 EDL，包含保存的竖屏裁切（若有） |
| `POST /tasks/{task}/edl/{revision}/subtitles/{subtitle}/exports/mlt` | 导出同版 EDL 和指定的保存字幕 |
| `GET /tasks/{task}/edl/{revision}/exports/mlt/{export}` | 核验并读取导出记录 |
| `GET .../exports/mlt/{export}/files/{filename}` | 按白名单下载经 SHA 核对的工程或快照 |

创建不接受客户端字幕正文、字体、裁切坐标或新候选列表；成功返回 201 和新导出版本 Location。下载核验与传输持有同一文件句柄，结束或断开后释放；`project.mlt` 使用 `application/xml`，所有接口返回 `Cache-Control: no-store`。原任务 ZIP 的下载白名单不变。接口和 CLI 都不会调用 MLT 渲染器或模型。

当前 MLT 引擎对含音轨的单帧片段存在已确认音频缺失。核心导出在读取源文件或写 XML 前拒绝这类 EDL；任务服务返回 409：`MLT 暂不支持含音轨的单帧片段，请使用同一 EDL 导出 MP4。` 不会移走片段、补静音或仅改变 EDL 来隐藏问题。无声单帧片段仍支持；一帧字幕位于至少两帧的有音片段内也支持。

## 本地使用

先按 README 安装 backend，设置 `CLIPFORGE_DATA_DIR` 为已有任务数据目录。仓库根目录运行，替换下面的示例编号：

```powershell
$env:PYTHONPATH = 'backend'
python -m clipforge.services.mlt_exporting 'task-uuid' 'revision-uuid'
python -m clipforge.services.mlt_exporting 'task-uuid' 'revision-uuid' --subtitle-id 'subtitle-uuid'
```

独立文件入口允许为每个 source 指定完整的原分析，字幕仍必须是由该 EDL 派生的 canonical `subtitles.json`：

```powershell
python -m clipforge.editing.mlt project.edl.json --source-root 'D:\your-local-media' --output project.mlt
python -m clipforge.editing.mlt project.edl.json --source-root 'D:\your-local-media' --output project-with-subtitles.mlt --subtitles subtitles.json --analysis 'source-id=analysis.json' --font-name 'Noto Sans CJK SC'
```

`--analysis` 可以重复，编号必须与 EDL source 一一对应。输出新建且不覆盖已有文件。`--source-root` 必须是本机绝对目录，禁止 URL、UNC、逃离根目录的相对引用及 Windows alternate data stream。

MLT 引用原归一化视频，不打包或复制媒体。服务器生成的引用指向服务器本机路径，下载一个 MLT 不代表另一台电脑可以直接打开；应一并携带经核验的素材，并用独立文件入口按目标机器的引用重新导出。现阶段没有自动打包素材、重新定位 API 或跨机器字体安装。

## 验证及未完成范围

新增服务及直接路由检查覆盖原字节保存、所选字体、裁切绑定、重排／字幕整数范围衔接、带重新 hash 的 XML／SRT 篡改拒绝、null 字幕身份、编号冲突、失败清理、完成记录最后写入、读锁、下载白名单、UUID 及无客户端效果参数。历史横屏原型的五个样例及 226 帧 MLT 引擎结果继续保留为原步骤记录。

本次五个有界合成案例已由原生 MLT 实际渲染并独立核验，共 87 个输出帧、13 条 cue；输入五个源的 1200 帧编号另行核对。结果保存在工作区 `outputs/week5-mlt-effects/verification/verification.json` 与 `metrics.json`，绑定实际源码、工具、工程、素材及成片 SHA。

| 案例 | 输出帧 | cue 数 | 内容 |
| --- | ---: | ---: | --- |
| 横屏重排／重复 | 26 | 5 | 原切点、重复含音来源、首帧单帧 cue |
| 竖屏单帧／末帧 | 9 | 2 | 无声单帧、素材末尾取段、重复含音来源 |
| 有理数帧率 | 16 | 3 | 30000/1001、分数词时间、整数 cue |
| 原生无音轨 | 12 | 0 | 空字幕及全片 AI 标识 |
| 竖屏混合来源 | 24 | 3 | 多来源、有声／无声、保存裁切 |

87 帧源编号及顺序、整数 cue 显示／消失、全片 AI 标识均通过；竖屏帧裁切参照唯一优于错误窗口，最小错误窗口与正确窗口 MAE 差为 4.63015。未覆盖区域最大 RGB MAE 为 1.33104。四个有音案例的 PCM 编号及顺序均正确，最大同步误差 0.001124 帧、长度误差 0.000250 帧。封装 PTS 相对 CFR 网格最大误差 0.0005 秒。27 项已解码数组变更负控制全部拒绝，EDL 0.1／0.2 含音单帧约束另各一次明确拒绝。

这些结果适用于绑定的合成样例。字幕显示状态及字面 HTML 已核对，没有以 OCR 证明所有字形或 ASR 正确；Qt offscreen 原生渲染不能替代 Shotcut GUI。真实任务的三类保存工程及完整真实 MLT 渲染已完成，结果见下文。服务／直接路由测试也不能替代完整 HTTP 或 Docker 检查。

原生核验保留了重复含音来源共享解码器、首帧单帧 `out=0` 及含音单帧片段的失败证据，记录在 `known-native-issues.json`。前两项已分别以独立解码器和 opacity 门控修正；含音单帧问题经源帧 0、eof、静音及 mix 隔离探针仍未消除，因此明确拒绝导出。相同九帧 EDL 的 FFmpeg 对照保留全部九个 PCM 脉冲，最大误差 ≤0.00094 帧；只证明该控制样例和 MP4 路径，不宣称 MLT 的已知限制已解决。

2026-10-12 已收到用户用 Shotcut 26.8.1 实际打开并另存的真实竖屏带字幕工程 `shotcut-check.mlt`。按终端 timeline 的引用关系核对，两个片段仍为源帧 `[43,1682)`、`[112,653)`，共 2180 帧，切点仍为第 1639 帧；1080×1920／30 fps、21 条字幕、AI 标识、字体及 HTML、原素材像素裁切 594×1056@(662,12) 均未变。整数帧改写为毫秒时间串后往返不改帧，最大表示误差为 0.01 帧。

保存后的原始 XML 直接交给 MLT，有限渲染首条字幕、中段、切点前后及尾部，共 37 帧。与原先完整原生 H.264 成片对照，最大整图灰度 MAE 1.3529/255，字幕区域 0.7664、品牌区域 0.4956；关键画面可读，新增默认 qtext 属性未覆盖显式 HTML 字体。切点两侧 PCM 均为 0 样本偏移，相关系数分别为 0.999900、0.998565。保存工程、原始工程、原始成片及素材 SHA 保持不变。综合记录在工作区 `outputs/week5-shotcut-roundtrip/verification.json`，静态结构与原生样本记录分别独立保存；没有再完整编码 2180 帧。

上述真实单例证明的是当时的打开、另存及结构保持，没有证明那次修改了 RichText 或 crop 控件。其历史记录及 37 帧原生抽样结果保留。

后续已完成上表五组合成案例的 Shotcut 26.8.1 实际 GUI 打开和另存，每例保留 `shotcut-opened.jpg` 及 `shotcut-check.mlt`。工作区 `outputs/week5-delivery/gui-roundtrip.json` 为 5/5 通过、0 待验、0 失败：片段数量、顺序、源范围、整数切点、裁切及字幕有效显示区间均与原工程一致。整数帧改写为毫秒时间串的最大表示残差约 0.014985 帧，精确值 <0.014986 帧；整数切点仍完全一致。首帧 cue 的 XML 声明结束位置可被 Shotcut 改写，但 opacity 门控保留其 `[0,1)` 有效显示区间，不能声称 XML 字节或所有声明范围未变。此轮只做实际 GUI 操作与保存结构回读，没有新增原生渲染，不以该结果替代画面、声音或真实人声字幕抽检。

crop 控件实验另在第五例首个片段上进行，保存为 `gui-cases/05-portrait-mixed-native/shotcut-crop-controls.mlt`，不覆盖五例验收副本。GUI 将 `use_profile=0` 的原素材像素坐标转为 `use_profile=1` 的项目像素坐标，转换与整数舍入单独记录；随后在 Left 控件输入 71、按 Tab 提交并 Ctrl+S 保存。独立报告 `outputs/week5-delivery/crop-control-audit.json` 状态为 `passed_profile_unit_crop_edit_with_quantization`，实际回读为 Left=71、Top=43、Right=70、Bottom=50、`use_profile=1`。Left 相对初次转换后的 70 增加 1 个项目像素；反投影仅是坐标比较，不证明渲染画面像素相同。除有意修改的裁切值外，时间线、字幕与品牌、音轨及其他语义未变，五例原副本和截图 SHA 保持不变。该 crop 实验未新增渲染；不能把独立编辑的单位变化当作五例未编辑工程往返不一致。

RichText 控件也已实际编辑并保存为独立 `gui-cases/05-portrait-mixed-native/shotcut-text-controls.mlt`。第一条 cue 的 HTML 字面文本由“重复原片段”改为“字幕编辑验收”，有效范围保持 `[3,5)`，文字提交与保存通过；`outputs/week5-delivery/text-control-audit.json` 独立审计字面 HTML 和样式。Qt 保存的 HTML 从 Microsoft YaHei 11px、白字、居中、17px 行高变为 Microsoft YaHei UI 9pt 与默认段落，原显式白字、居中和行高声明消失。`argument` 属性仍保留旧文本“重复原片段”，不能用它代替实际 HTML 文本核对；geometry 前四坐标保持不变、额外增加末尾 `1`。这些实际改写保留为旧策略失败证据；当时只通过文字保存，未新增渲染。后续新版修复与实际渲染见下文。

第五周发布准备：三处人声字幕定性抽检已获用户确认，≤0.3 秒保留为未量化限制，代码待授权发布；无需重做旧五例 GUI 打开另存。现有 React 页面已完成本地 [导出接入](WEB_EXPORT.md)，一次真实网页选择、排序、保存、字幕及 MP4／MLT 导出、历史恢复、HTTP 范围读取和播放器拖动已核验；该结果不等同于全量接口或容器回归。完整事件候选和人工修改量的后续任务仍按 [第五周计划](WEEK5_PLAN.md) 保留，导出完成不替代叙事质量验证。

## 真实工程与相关回归

同一保存的 Test3 EDL 已生成横屏带字幕、竖屏、竖屏带字幕三个独立 MLT 导出。最终竖屏带字幕工程经 MLT 实际渲染为 1080×1920、30 fps、2180 帧、21 条字幕；全序列中央 ROI 及四个关键位置核验通过，七个音频窗口（含切点两侧）与 FFmpeg 对照均为 0 样本偏移，最低相关系数 0.999218。79 个此前文件、任务记录和源视频保持原状。仅最终工程完整编码一次；先前图的中断实验单独保留，没有冒充完成结果。

相关回归共 210 项，209 通过、1 项因平台跳过；Ruff 通过。stdlib trace 对三个导出器核心函数及辅助函数的执行行试测：EDL 210/213（98.59%）、MLT 141/143（98.60%）、MP4 453/506（89.53%）。分母排除模块级声明、异常类方法及 CLI；工具实现不计入，单元测试中包含工具 mock。这是明确范围的执行行试测，不是分支覆盖率、完整 HTTP／容器 CI 或 Shotcut GUI 验收。

工作区 `outputs/week5-mlt-effects/index.html` 提供 MLT 与 FFmpeg 成片同步对照及工程下载；完整结果见同目录 `result.json`、`related-tests-and-core-coverage.json` 和 `real-task/native-final/verification.json`。三份导出保留本机素材引用和指定字体，尚未推送或部署；真实样例的 Shotcut 保存回读独立保存在 `outputs/week5-shotcut-roundtrip`。

## 2026-10-12 字幕编辑修复验收

当前 qtext 纯文字 v2 已通过实际 Shotcut 全文替换和一行改两行、另存及保存工程原生 24 帧核验；去文字同图控制另 24 帧。第 3、4 帧的两条白色文字行带为 [281,288)、[291,298)，在 180×320 图内及 [9,269,162,32] 文本框内，字体、字号、颜色、对齐和范围读回保持。额外五个新版工程共 87 帧，双行中文、长行和字面标记三例共 36 帧原生通过。历史 101 份 JSON 未变。

新导出完成记录为 format_version=2、text_style_version=2；旧 format_version=1 且未声明策略的记录按精确旧 v1 生成器核验。未知、布尔、浮点或不匹配版本拒绝，完整源事实及文件 SHA 继续校验。真实横屏 21 条字幕另生成 v2 工程，并确认 v1 与冻结旧工程逐字节相同。此兼容不自动修改旧工程样式。

最新限定后端回归 219 项中 218 通过、1 项平台跳过；EDL 98.59%、MLT 98.83%、MP4 88.93% 为当前 SHA 绑定的可执行行试测。此前本页 210 项及旧覆盖率为历史记录。当前汇总见 docs/evaluation/week5-export/current-manifest.json 和 current-native-refresh.json；隔离 Linux 合成 EDL 的 MP4/MLT/字幕导出和四次 HTTP Range 检查已通过，见 [当前交付](WEEK5_DELIVERY.md)。用户确认三处人声字幕无明显同步问题；≤0.3 秒未量化记录。
