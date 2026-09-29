# 第三周：镜头人工标注与 F1 评测

这一阶段建立可复跑的镜头评测，不更改线上流水线、ASR 模型或历史结果。任务书要求自建至少 10 条真实视频，访谈、课程、Vlog 各至少 3 条，人工标注镜头边界并报告 F1；目标为 F1 ≥0.75。低于目标应报告失败案例和调参记录。

## 目前有什么、还缺什么

`evaluation/inventory.json` 登记了素材目录中的四个原片文件及 SHA256、时长和视频属性：Sucai1（41.27 秒）、Test1（345.50 秒）、Test2（33.40 秒）、Test3（72.60 秒）。Sucai1 的两个归一化版本不另计素材。哈希只能识别字节相同的文件，不能自动识别重新编码、裁剪后的同源视频；这部分需人工排重。

四条暂标 `category=unknown`，等待用户确认，保守记为 development，不冒充独立留出集。已有五段人工转写属于语音参考，不是镜头切点标签。当前没有可计算真实镜头 F1 的人工标签；`samples/evaluation_readiness.json` 的 F1 为 null，不是 0，也不是已经达标。至少还缺六条不同视频；如果现有素材不属于三类，需要补得更多。

素材可继续放到项目外的自有目录。不要把原片、模型或大视频提交进 Git。同一素材重新命名、重新编码或裁成几段，不能借此增加独立视频数量。每个文件在清单中用一个 id，并登记原片 SHA256。

## 第一步：准备一条素材

在项目根目录的 PowerShell 执行，解释器使用现有项目虚拟环境，无需新增依赖：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
python -m clipforge.evaluation.shots prepare --source 'C:\Users\asus\Desktop\Sucai\Test3.mp4' --output-dir data/evaluation/Test3 --ffmpeg 'D:\Shotcut\ffmpeg.exe' --ffprobe 'D:\Shotcut\ffprobe.exe'
```

输出目录必须不存在，避免覆盖人工工作。若处理中断，保留原目录以便排查，重试用新目录。准备成功后包含：

- `normalized.mp4`：与标注、预测绑定的 30 fps 视频，人工观看这一份。
- `annotation.json`：空白人工参考，status 为 draft、cut_frames 为 null。
- `prediction.json`：固定现有检测器产生的独立预测；不用 ASR，不需要重新推理 small。

本地新归一化文件的字节哈希可能与 Docker 旧任务不同，不能直接混用旧任务的切点或片段时间。本工具对归一化媒体哈希、帧数、时长、时间基准执行一致性校验。若使用别的归一化文件，重新标注或逐帧确认之后建立新的参考。

## 第二步：人工标注

1. 用播放器或剪辑软件打开 normalized.mp4，完整观看并逐帧定位镜头切换。建议先完成标注再查看 prediction.json，减少被算法结果引导。
2. 标记每个新镜头的第一帧，帧号从 0 开始。不记录片头 0 和片尾 total_frames，只记录片内切换。30 fps 下若软件显示的是“时:分:秒:帧”，00:00:10:12 对应 312 帧；不要把末段帧数当毫秒。优先直接读帧号。
3. 摇镜、变焦、人物移动或字幕变化本身不自动算切换；依据画面是否发生剪辑到另一镜头判断。渐变转场统一记过渡区间中点最近的整数帧，并把疑难点写入 notes。这是本项目标注约定。
4. 将切点按升序填入 cut_frames。示例 `[90, 240]` 只是格式演示，不能当 Test3 标签。整片确实无切换时填 `[]`；还没标注必须保留 `null`。
5. 核对完整后填 reviewer（自己的名字或代号）、reviewed_at（日期）、status 改为 verified。不要手动修改媒体哈希和帧数。

工具只能检查标签是否声明人工核对和结构是否有效，不能替代真正的人工观看。不要把算法预测复制成标签后当作人工准确率。

## 第三步：单条计分

```powershell
python -m clipforge.evaluation.shots score --annotation data/evaluation/Test3/annotation.json --prediction data/evaluation/Test3/prediction.json --output data/evaluation/Test3/score-v1.json
```

输出必须是新文件，防止覆盖以前的实验。结果包含容差 F1、严格逐帧 F1、命中对、漏检帧和误检帧，以及原视频/归一化视频身份和检测器参数。

本项目预先约定主指标容差为 ±3 帧（30 fps 下 ±0.1 秒）。任务书没有指定镜头匹配容差；该数值不是 ASR 的 ≤0.3 秒要求。命令可用 `--tolerance-frames` 显式改变，必须记录，不得只挑最好看的容差。清单评测统一读取清单 tolerance_frames。

每个人工切点最多匹配一个预测点，反之亦然。按帧序最早可行匹配最大化 TP；不宣称匹配方案最小化时间偏差。TP 是命中数，FP 是多检数，FN 是漏检数：F1 = 2TP / (2TP + FP + FN)。只把片内切点用于评分，不把固定分段点或片头片尾算作正确预测。

人工和预测都无切点时记录 correct_no_cuts=true，单条 F1=null；不会人为塞入 F1=1 来抬高平均值。只要有误检或漏检而没有命中，F1=0。

## 第四步：扩充与汇总

修改 `evaluation/dataset.json`：分类只能是 interview（访谈）、course（课程）、vlog、other、unknown。添加不同原片时登记 SHA256、annotation / prediction 路径。路径相对清单文件所在目录解析。split 区分 development / evaluation；先用于调参的素材不能重新标为独立留出素材。

```powershell
python -m clipforge.evaluation.shots dataset --manifest docs/evaluation/dataset.json --output data/evaluation/report-v1.json
```

当前提供的清单已引用 data/evaluation 下的预定目录；请与实际准备目录保持一致。未分类、未准备或 draft 的项只列为 pending，不产生分数。错误的媒体身份、重复素材、混用检测器版本或参数会明确报错。不同试验配置应另存预测和清单，保留原人工标签，不把每条素材各自最优参数混在一轮总体评测里。

汇总采用 micro F1：先加总 TP/FP/FN 再计算，同时报告类别和 development/evaluation 子集。只有至少 10 条有效标签、三类各至少 3 条、清单无待完成项且总体 F1≥0.75，shot_f1_target_met 才为 true。这里的 true 也只表示镜头指标，week3_complete 始终为 false；缓存、逐词时间和契约冻结需另外验收。

完成一轮后把人工 annotation.json、prediction.json 和计分报告归档到 docs/evaluation 下的专用结果目录，更新清单引用，再随文档提交；原片保持项目外，归一化视频保持 data 目录内。若低于 0.75，按报告漏检/误检帧回看片段，记录案例、修改的参数以及同一批素材上的前后结果。

## 本轮验证范围

新工具有独立的匹配/身份/草稿/去重/覆盖检查测试，并用已知切点的合成视频验证实际准备链路。合成夹具的正确切点是程序构造的，不能充当真实视频人工标注或首轮真实 F1 成绩。另为 Test3 准备人工标注材料，模板未填标签。

后端完整测试、代码检查及具体运行记录见 `samples/shot_evaluation_tool_verification.json`。本轮未改变服务代码、数据库、依赖或前端，不需要为此重建 Docker。
