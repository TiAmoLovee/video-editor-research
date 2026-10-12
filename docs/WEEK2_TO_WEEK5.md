# 第二周整改与第五周的衔接

2026-10-12 核对了第二周原聊天、当前代码历史，以及 GitHub [PR #4](https://github.com/TiAmoLovee/video-editor-research/pull/4) 的实际页面。该 PR 于 2026-09-24 合并到 main，合并提交为 `a244ac82308a1fc439b7cfbb874cb7335ef4affe`，页面显示 8 项检查通过。两个提交分别为工程整改 `4eae101` 和 Docker 回归记录 `0888118`；均已在当前代码历史中。

## 当时老师要求改什么

老师先认可了“上传 → 归一化 → 切片 → 状态查询 → 下载”的完整流程，再指出三项工程问题：

1. 前端按任务书使用 React 18、TypeScript 5、Vite、Zustand 4、Ant Design 5，不能继续依赖无构建流程的单文件页面。
2. 代码从仓库根目录平铺改为分目录组织。
3. 文档去掉个人电脑的 FFmpeg 路径，使用环境变量或说明配置方式。

原聊天的措辞是“在接下来的任务中很可能出问题”，没有明确写“第五周”。这些整改确实是第五周继续开发的基础；完整 EDL、MLT 导出来自任务书的第五周要求，不能转述成第二周已经完成。

整改依据见 [ENGINEERING_REFACTOR.md](ENGINEERING_REFACTOR.md)。[WEEK2_REVIEW.md](WEEK2_REVIEW.md) 和 [WEEK2_DELIVERY.md](WEEK2_DELIVERY.md) 记录整改前的阶段成果，其旧目录和前端形式以整改说明为准。

第二周还留下了直接衔接 EDL 的媒体约定：[SPLITTING.md](SPLITTING.md) 从初次实现 `51ddfe3` 起就说明固定切片清单是初版中间表示，统一 EDL 仍需扩展并定义 Schema。其整数帧、结束帧不包含、真实帧数核验与最后一帧保留约定继续有效；工程整改没有重写这套取帧逻辑。

## 第五周沿用哪些约定

| 第二周基础 | 第五周处理及核对状态 |
| --- | --- |
| React/TypeScript 前端及构建流程 | 当前 `frontend/` 保留原选型。未来采纳、排序、导出入口接入现有组件、store 与 API 层；离线验证页面不是产品前端替代品。 |
| 后端包与前端、测试、部署分目录 | EDL/MLT 位于 `backend/clipforge/editing/`，测试在 `backend/tests/`，契约在 `schemas/`。已延续。 |
| 媒体工具可配置 | 复用 `clipforge.config.media_tool()`：显式参数 → `FFMPEG`/`FFPROBE` → 兼容的 `*_PATH` → PATH。后续探测和渲染模块不能引入个人盘符。 |
| HTTP、Celery、SQLite、任务目录和数据卷兼容 | 原型未修改这些协议。整合时在现有 `jobs/<task_id>/` 保存 EDL/MLT；旧任务及下载继续可读，不另建并行任务协议。部署保留 `clipforge` 项目及 `media_data`/`redis_data` 数据卷。 |
| 归一化视频以 CFR 30 fps 处理 | 实际任务从归一化素材建立 EDL，使用 `30/1`；从真实视频读取帧数。不能从容器时长或平均帧率推断精确帧数/CFR。 |
| 固定切片的 `[start_frame, end_frame)` 约定 | EDL 同样使用不包含结束帧的范围；MLT 包含最后帧，必须转换为 `out_frame - 1`。已有单帧、末帧及重排验证覆盖这一差异。 |
| Schema 资源随镜像交付 | API/worker 原本复制整个 `schemas/`。此次给 API 构建时校验名单补 EDL Schema，并增加隔离资源目录下的候选 → EDL → MLT 回归。 |
| Windows/Linux 的既有 CI | 新后端测试沿用原 unittest discover 与 Ruff，不另起一套检查流程。历史 CI 通过不代替本轮完整回归。 |

## 必须继续补的两处衔接

**真实媒体描述：本轮已补。** 第二周的 `media_meta.json` 不足以直接形成 EDL source。新增 manifest builder 从实文件核验 hash、帧数、CFR、SAR、progressive、rotation 和 colorspace，并复用原工具配置与帧数计数；没有盲填缺失属性。真实任务的明确候选选择可以保存为 EDL 版本，见 [任务 EDL](TASK_EDL.md)。

**生产流程以 EDL 为共同依据：继续推进。** 第二周固定切片和第四周边界预览仍保留；新增 [MP4 渲染](MP4_RENDER.md) 只读取保存的 EDL。[字幕导出和烧录](SUBTITLES.md) 从同版本分析快照按 EDL 映射，并绑定 SHA；额外字体和工具仍走显式参数及环境配置。[固定中心竖屏](PORTRAIT_RENDER.md) 另存 EDL 0.2，保持源描述、切点和原分析。[MLT 裁切／字幕及导出版本服务](MLT_EFFECTS.md) 已接入同一保存依据，继续使用原任务目录和下载持锁方式，不另起任务协议或复制视频。新效果五例共 87 帧、13 条 cue 的原生核验通过，含音单帧限制明确拒绝而不改清单；真实完整渲染、界面打开与保存读回另行验收。本轮 API 尚未部署，也未把旧固定切片流程冒充 EDL 流程。

第五周继续顺序：核验真实媒体描述 → 保存实际任务的 EDL → 精确 MP4 与 MLT 对照 → 字幕及固定中心 9:16 → Shotcut 打开与音画同步验收。计划及当前原型边界见 [WEEK5_PLAN.md](WEEK5_PLAN.md) 和 [EDL_MLT.md](EDL_MLT.md)。这些待办明确保留，不以第二周或第四周成功记录替代第五周验收。

## 本次衔接检查结果

新增的打包资源测试在隔离目录中完成候选 → EDL → MLT，并核对顺序、帧范围和媒体引用；删除隔离目录的 EDL Schema 后，导入与导出都必须失败，防止误用工作区资源掩盖打包遗漏。EDL/MLT 原型及打包资源共 20 项相关测试通过，Ruff 与 Git diff 检查通过。

本次修改仍在本地工作区；完整后端回归及真实部署的限制沿用 [EDL_MLT.md](EDL_MLT.md) 的记录。
