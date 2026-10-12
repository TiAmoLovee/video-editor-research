# EDL 与 MLT 离线原型

2026-10-12，第五周第一步。对应 [第五周计划](WEEK5_PLAN.md)。第二步已新增 [实测媒体描述与任务 EDL](TASK_EDL.md) 的服务及接口；代码仍在本地，生产页面尚未接入。

## 数据契约

[edl.schema.json](../schemas/edl.schema.json) 版本 `0.1.0` 是原型契约。包含明确的项目 profile、素材描述、片段数组及总帧数。源范围和时间线均为 `[in_frame, out_frame)`，用整数帧避免秒数误差累积；MLT 的最后一帧为 `source_out_frame - 1`。

`candidates_to_edl` 接收候选文档、明确选择的候选 ID 列表和已核验媒体描述。选择列表就是输出顺序，不根据分数自动采纳；相同来源可在多个片段中重复出现。重复候选 ID、错误媒体身份、零帧片段、越界、错配帧率及不连续的时间线会被拒绝。

每个片段保存原始来源 ID/hash、候选文档 hash、候选 ID、句子引用、最初的秒数、帧范围及选择序号。边界和顺序以后可以调整，基线不能借另一段视频冒用。同一内容的素材重新定位允许保留 ID/hash、更改文件引用。实际操作人、操作时间、增删记录和修改量汇总尚待后续编辑流程接入。

## 离线运行

先按项目 README 安装 backend；以下命令在仓库根目录运行，参数为本机自备文件，不会调用模型。

```powershell
python -m clipforge.editing.edl candidates.json --media-profile media-profile.json --selected-id c_first --selected-id c_second --output project.edl.json
python -m clipforge.editing.mlt project.edl.json --source-root 'D:\your-local-media' --output project.mlt
```

`media-profile.json` 必须只有 `profile` 和 `source` 两个对象；字段定义见 Schema。不能将旧 `media_meta.json` 直接当作该描述。源描述要求：ID、本地文件引用、SHA-256、已核验的视频总帧数、CFR 有理数帧率、尺寸、progressive、方形像素、旋转为 0、BT.709、视频和音频流索引。无音轨时音频索引为 null。首版支持 1–120 fps，源尺寸和帧率必须匹配项目 profile。

MLT 导出默认检查文件存在和字节 SHA-256；新增 manifest builder 核验 CFR、帧数、尺寸及流索引，MLT 模块本身不重新探测。手工提供描述的离线入口仍由调用者负责核验。来源仅支持明确的本地普通文件，网络 URL/UNC 不支持；相对路径不能逃离 `source-root`。输出采用新建模式，不覆盖已有文件。

生成的工程引用原素材，不能单独传一个 MLT 就保证另一台机器能打开。跨机器需携带素材，并保持引用可解析或重新定位。

## 本轮验证

- 新增 18 项单元测试，覆盖候选明确选择和重排、有理数帧率、帧量化、非法范围、身份绑定、素材替换检测、缺失素材、中文/XML 特殊字符路径、单帧、多个来源和无音轨。
- 生成五个 EDL/MLT 样例：单帧、不连续取段、重排、重复素材范围和素材最后一帧。
- 使用 Shotcut 安装目录中的 MLT 7.41.0 实际渲染，再独立解码视频中的二进制帧编号；五例共 226 帧的源位置和排列顺序全部精确一致，并保留音轨。输入是合成验证素材，没有使用人评标签。
- stdlib trace 对原型执行行的试测为 EDL 89.09%、MLT 83.81%。CLI 子进程未计覆盖；这不是分支覆盖率，也不能代替第五周三个导出器的完整覆盖率验收。

第一步的样例与记录在工作区 `outputs/week5-edl-mlt/`，只证明当时的 MLT 引擎按清单取帧。后续已补 [EDL→MP4 渲染](MP4_RENDER.md)、真实任务对照及合成素材音画同步测量，以及 [字幕生成和烧录](SUBTITLES.md)。现在另已实现 [MLT 竖屏与字幕及版本服务](MLT_EFFECTS.md)；新效果的原生核验和 Shotcut 界面／保存读回与历史结果分别报告。

本轮没有取得全量后端回归通过结论：第一次全量运行超时；详细复跑发现既有 acceptance 测试的 `os.link` 在当前受限环境报 `WinError 5`，在工作区独立创建硬链接也同样被拒绝，随后既有 boundary media 接口测试未结束。新增原型的 18 项测试单独全部通过；全量回归仍需在允许这些操作的验证环境复跑。没有修改既有测试、绕过媒体完整性检查或据此声称旧功能通过。

## 后续

保持 EDL 为共同依据，[精确 MP4 渲染与对照验证](MP4_RENDER.md)、[字幕时间映射](SUBTITLES.md)和 [固定中心竖屏](PORTRAIT_RENDER.md) 已实现。[MLT_EFFECTS.md](MLT_EFFECTS.md) 补齐 EDL 0.2 原素材坐标裁切、同版保存字幕及任务导出／读取／下载；不再因版本为竖屏而拒绝。实际核验修复了重复含音来源的共享解码状态及首帧单帧字幕 `out=0` 的持续显示，原 profile 和取帧约定不变。含音轨的单帧片段存在确认的引擎音频缺失，当前明确拒绝 MLT 导出并提示改用同一 EDL 的 MP4；无声单帧支持。新效果五例原生核验共 87 帧、13 条 cue 通过，最大合成音画误差 0.001124 帧；真实完整渲染、GUI 开启和编辑后保存仍待核对。候选生成改为话题段的实验另行推进，不能把可导出、句子不断或原型测试通过写成完整事件质量已解决。

MLT 结构依据：[MLT XML 官方文档](https://www.mltframework.org/docs/mltxml/)及已归档的 [Shotcut 工程](samples/Week1Work.mlt)。
