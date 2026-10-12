# 实测媒体描述与任务 EDL

2026-10-12，第五周第二步。新增已有任务到 EDL 的服务及 HTTP 接口，沿用 [第二周工程及媒体约定](WEEK2_TO_WEEK5.md)。代码尚未推送或部署；本地 React 页面已接入 [剪辑与导出工作台](WEB_EXPORT.md)，真实 HTTP／容器仍待核验。

## 实测媒体描述

`clipforge.editing.manifest.build_media_manifest()` 从归一化视频实文件生成 EDL 0.1.0 所需的 `profile` 和 `source`，不把旧 `media_meta.json` 缺失字段补成默认值。

- 默认 30/1 fps。逐帧核对整数 PTS 与 time_base，要求首帧 PTS 为零、时间戳连续并符合 CFR 网格。整数步长精确匹配，非整数步长最多容许一个 tick 的量化误差。
- 检查实际画幅、方形像素、逐行扫描、BT.709 space/transfer/primaries，以及容器和逐帧旋转信息。未知或不支持的属性拒绝进入当前 EDL。
- 流式扫描计帧后，复用第二周 `read_frame_count()` 独立解码计帧并核对；平均帧率和容器时长不代替计帧。
- 保存实际流编号和字节 SHA-256。无音轨为 null，不生成虚构音轨。
- Windows 核验全程持有禁止写入、删除的只读句柄，并从同一句柄计算 hash；POSIX 使用协作锁及文件身份检查，不支持外部程序绕过锁修改素材。

FFprobe 沿用显式参数 → `FFPROBE` → 兼容的 `FFPROBE_PATH` → PATH。探测、计帧和分块哈希共享一个截止时间。配置应直接指向 FFprobe 可执行程序；超时停止读取并终止直接工具进程，不承诺强制清理任意脚本包装器的所有后代。

历史媒体和旧归一化函数没有被改写。未知颜色、VFR 或旋转素材需用明确参数生成合规的新素材、重新分析并核验；不能静默替换旧 `normalized.mp4` 后继续套用原候选。

## 任务保存与兼容性

`clipforge.services.editing.create_task_edl()` 只接收成功任务及明确选择的候选 ID；列表顺序就是 EDL 顺序。服务深度校验已有候选及其分析来源，不重新评分、转写或调用模型。保存初始秒数、帧范围、来源 hash、选择序号和句子引用，供后续人工修改量统计使用。

缓存命中任务可能没有自己的归一化视频。服务沿 `cache.json` 原任务链定位成功任务，拒绝循环、缺失和原片替代，最终用实文件 hash 核对当前分析与候选；不复制大视频。

每次保存产生新的 `jobs/<task_id>/edits/<revision_id>/`：

| 文件 | 用途 |
| --- | --- |
| `media-profile.json` | 实测媒体描述 |
| `project.edl.json` | 统一剪辑清单 |
| `candidates.json`、`analysis.json` | 完整来源快照 |
| `revision.json` | 任务/版本/来源身份、创建时间、产物 hash；最后写入的完成记录 |

读取必须通过完成记录、文件 hash 及 EDL 校验；失败保存清理此次目录，旧版本不覆盖。读回时使用保存的 EDL，不从当前排名重建。原 SQLite、任务状态、`result.files`、ZIP 和下载协议保持不变。

## 使用接口

按 README 安装 backend，设置 `CLIPFORGE_DATA_DIR` 为已有任务数据目录，并配置 FFPROBE。在仓库根目录运行，候选编号需替换为该任务实际编号：

```powershell
python -m clipforge.editing.manifest 'your-media/normalized.mp4' --ref normalized.mp4 --output media-profile.json
python -m clipforge.services.editing '<task_uuid>' --selected-id '<candidate_2>' --selected-id '<candidate_1>'
```

- `POST /tasks/{task_id}/edl`：JSON `{"selected_ids":["candidate_2","candidate_1"]}`；1–100 个严格字符串、不重复编号，顺序即输出顺序。成功返回 201、版本记录和 Location；同步媒体核验共享 90 秒预算。
- `GET /tasks/{task_id}/edl/{revision_id}`：读回 EDL、媒体描述及版本记录。
- `GET /tasks/{task_id}/edl/{revision_id}/files/{filename}`：下载白名单中的 EDL、媒体描述或来源快照。
- `GET /tasks/{task_id}/edl/{revision_id}/source`：下载与快照 hash 一致的 `normalized.mp4`。核验及分块传输使用同一锁定读句柄，完成或中断后释放。

API 镜像增加 FFmpeg 包以提供 FFprobe，继续使用非 root 用户。完整 Docker 重建尚未验证。代码新增文件会改变现有缓存的代码指纹，后续上传不承诺命中旧键；已有任务和下载仍保留。

EDL 不打包视频。跨机器需另携带或下载 `normalized.mp4`，再按 [MLT 导出命令](MLT_EFFECTS.md) 指定本机 `--source-root`。后续已新增 `POST .../edl/{revision}/exports/mlt` 和 `POST .../edl/{revision}/subtitles/{subtitle}/exports/mlt`，生成新的、经过核验的 MLT 版本；对应 GET 记录及文件下载见 [接口说明](MLT_EFFECTS.md)。工程引用服务器本机素材路径，下载 XML 后需按目标电脑的本地引用重新导出；不是自动跨机器媒体包。

## 验证范围

真实 Test3 已完成实测媒体描述、候选到 EDL、保存读回及本机 MLT 转换：2178 个 CFR 30 fps 源帧，1920×1080，实文件 hash 与原候选一致。技术对照选择两条候选并核对 MLT 起止帧，原任务记录和 16 个原有文件均未改变；没有新增模型调用、大视频副本或人工质量标签。

自动化检查包括未知属性、伪 CFR、丢帧、重复时间戳、无音轨、非零流编号、素材替换、工具超时、帧级 SEI 旋转、缓存来源、失败保存、版本完整性及接口输入。可复跑脚本与记录在工作区 `outputs/week5-media-edl/`。

本轮定向回归共 86 项：85 通过、1 项 POSIX 专用测试在 Windows 跳过，Windows 对应锁测试通过。直接工具进程清理修正后又单独复跑媒体描述 15 项，无 ResourceWarning；Ruff 和 diff 检查通过。这不是全量后端或跨平台 CI 通过结论。

当前受限 Windows 环境不能完成完整 HTTP 验证：独立对照连既有 `/health` 也未进入业务代码，诊断停在 asyncio 创建 socketpair 的 accept。服务、直接路由及请求模型分别测试；完整 HTTP、Docker、Shotcut 界面和音画同步仍待对应环境核验，不声明生产部署通过。

已新增 [保存的 EDL → 精确 MP4](MP4_RENDER.md) 渲染、保存及下载，原 EDL 版本保持不变；[字幕重映射、SRT/ASS 与烧录](SUBTITLES.md) 使用同版分析快照。[固定中心 9:16](PORTRAIT_RENDER.md) 另存 EDL 0.2 和竖屏字幕布局，保留源媒体描述、父版本和原分析 SHA。[MLT 对应裁切／字幕效果及导出版本](MLT_EFFECTS.md) 已实现，新效果五例共 87 帧、13 条 cue 的原生核验通过；含音单帧片段的 MLT 引擎限制明确拒绝并提示导出 MP4，真实完整渲染及一个真实 Shotcut 打开／另存回读已通过。现有 React 页面已接入选择、排序、保存版本及导出，一次真实浏览器导出流程与 HTTP 范围读取已通过，详见 [网页核验](WEB_EXPORT.md)。边界编辑、五例 GUI 和容器回归仍待对应阶段核验，尚未部署。
