# ClipForge 智剪工坊：视频剪辑系统调研与设计

本仓库记录 ClipForge 的技术调研、产品体验、需求定义和系统设计，并用于后续开发。

项目目标是构建“导入长视频 → 自动分析与候选推荐 → 人工复核 → 保存剪辑安排 → 导出 MP4 / MLT”的半自动剪辑流程。现阶段以成熟组件集成和已有交互流程的适配为起点，具体借鉴关系见需求规格说明书。

## 当前阶段

第一周：技术调研与需求定义。已完成三个参考项目的基础流程实验及部分源码调查，形成商业产品体验记录、需求规格说明书、用例图和主流程时序图。调研报告仍在补充和核对。

| 工作项 | 当前状态 |
| --- | --- |
| OpenCut Classic | 已完成本地运行、导入、分割与删除、MP4 导出、播放及刷新恢复；已补充编辑、保存、预览、音频与导出的主要模块图和源码依据 |
| AutoClip | 已完成本地部署、转写、模型分析、切片生成及播放；已补充六步处理、结果同步与进度查询模块图；实际任务启动路径及异常分支仍需运行证据核对 |
| Shotcut / MLT | 已完成手工剪辑、MLT 保存与重新打开、MP4 导出与播放；已有工程样本，并补充时间轴、播放、滤镜挂接和后台导出模块图及源码依据 |
| OpusClip / Descript | 已整理两款产品的体验与截图；OpusClip 已验证 MP4 下载，Descript 最终输出及文本删改联动尚未确认 |
| 需求规格说明书 | 已形成 8 条功能需求、5 条非功能需求，逐条附验收判据，并补充借鉴说明和分阶段实现范围 |
| 用例图与主流程时序图 | 已形成图片和说明，与需求编号对应 |
| 仓库与 CI | 已配置 `.gitignore` 和 GitHub Actions 的 lint＋test 占位任务；本次核对的运行成功 |

需求文档和设计图描述后续开发目标，不表示 ClipForge 功能已实现或通过测试。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [调研报告](docs/research-report.md) | 三个参考项目的实验与源码调查、六维对比、两款商业产品体验、参考资料 |
| [需求规格说明书](docs/requirements.md) | 8＋5 需求、验收判据、逐条借鉴关系和分阶段实现 |
| [用例图与主流程时序图说明](docs/design-diagrams.md) | 两张图、读图说明、需求映射与异常处理 |
| [用例图](docs/clipforge-use-case.png) | 用户、系统边界及 8 项功能用例 |
| [主流程时序图](docs/clipforge-main-sequence.png) | 上传、分析、人工修改、保存和导出的配合过程 |
| [Shotcut 工程样本说明](docs/samples/README.md) | 样本信息、打开方法、验证范围及本人录制的素材来源记录 |
| [Week1Work.mlt](docs/samples/Week1Work.mlt) | 手工生成的工程格式样本，原视频不包含在工程文件内 |

## 仓库结构

```text
video-editor-research/
├── README.md
├── .gitignore
├── .github/workflows/ci.yml
└── docs/
    ├── research-report.md
    ├── requirements.md
    ├── design-diagrams.md
    ├── clipforge-use-case.png
    ├── clipforge-main-sequence.png
    ├── 产品体验与实验截图
    └── samples/
        ├── README.md
        └── Week1Work.mlt
```

图中的“产品体验与实验截图”表示 `docs` 内现有的 PNG 文件，不是一个实际目录名。

## 查看与运行说明

当前仓库主要交付调研和设计材料，尚未提供 ClipForge 应用程序的启动命令。可直接打开上述 Markdown 文档及图片查看成果。

OpenCut Classic、AutoClip 和 Shotcut 的本地实验环境、版本及调整记录分别见调研报告第 3、4、5 节。

查看 MLT 样本时，需安装 Shotcut 并具备原始素材 `Sucai1.mp4`。样本当前记录本机素材路径，在其他机器上需要重新定位素材；详见样本说明。

## 持续集成

[CI 配置](.github/workflows/ci.yml) 在推送、拉取请求和手动触发时运行，包含两个明确标记为占位的任务：

- **Lint placeholder**：输出尚未配置代码规范检查的提示。
- **Test placeholder**：输出尚未配置自动化测试的提示。

当前成功结果说明占位工作流可以运行，不代表实际代码质量检查或功能测试已经通过。后续应用开发时接入真实检查和测试。

核对记录：2026-09-17，提交 `39861a61879dfa5c901ec3c790e4bb9e3ed00432` 的 [CI 运行成功](https://github.com/TiAmoLovee/video-editor-research/actions/runs/35171077991)。后续状态请查看 [Actions 页面](https://github.com/TiAmoLovee/video-editor-research/actions)。

## 接下来的工作

1. 检查本轮新增模块图在 GitHub 的显示；继续复核 AutoClip 任务入口、空结果状态和结果同步等异常路径。
2. 核对已补充的工程样本来源说明、报告引用和图片显示，并排版导出以确认最终页数。
3. 在后续开发阶段先打通上传、后台处理、最小剪辑清单和导出流程，再逐步接入语音转写、评分及人工修改。

性能、识别准确率、精确切点、异常恢复和跨设备恢复等尚未验证的内容，按报告中的限制如实记录。
