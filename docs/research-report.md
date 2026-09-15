# 视频剪辑项目调研报告 v1

当前状态：框架已建立，实际调研待完成。

## 1. 调研目的与范围

本次调研涵盖 OpenCut Classic、AutoClip 和 Shotcut/MLT。

通过本地运行、源码阅读和官方文档查阅，
分析三个项目的实现方式，为后续系统需求与设计提供依据。

## 2. 调研环境与版本

- 操作系统：Windows，具体版本待记录
- OpenCut Classic 版本或提交编号：待确认
- AutoClip 版本或提交编号：待确认
### Shotcut 实验与源码版本

- 本地实验软件：Shotcut 26.8.1。
- 源码仓库：mltframework/shotcut。
- 本次固定的源码提交编号：8cd39efcdf8ab80390ee4736db2f52577fc82cdc。
- 主窗口源码永久链接：https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp

版本说明：本地安装的软件版本与本次查阅的源码提交分别记录，尚未确认该提交对应 Shotcut 26.8.1。

## 3. OpenCut Classic

### 3.1 本地运行与基础流程
待记录：启动步骤、测试操作、运行结果及截图。

### 3.2 主要模块与职责
待调研。

### 3.3 模块依赖图
待根据源码绘制。

### 3.4 关键技术发现与证据
待记录：技术结论、源码路径或官方文档链接。

## 4. AutoClip

### 4.1 本地运行与基础流程
待记录：启动步骤、测试操作、运行结果及截图。

### 4.2 后端任务编排
待调研：任务如何创建、执行和返回结果。

### 4.3 LLM 调用层
待调研：调用位置、输入输出及后续处理。

### 4.4 模块依赖图
待根据源码绘制。

### 4.5 关键技术发现与证据
待记录：技术结论、源码路径或官方文档链接。

## 5. Shotcut / MLT

### 5.1 本地运行与基础流程
使用 Shotcut 26.8.1 导入素材 Sucai1.mp4，进行分割并移除中间部分，保留两个片段。将结果保存为 Week1Work.mlt，关闭后重新打开，确认工程可以恢复剪辑结果。
工程分辨率为 960 × 600，帧率为 30 fps。

### 5.2 工程文件读写
查看 Week1Work.mlt 后，发现工程使用 XML 格式记录素材引用和剪辑安排：
resource 属性记录原始素材的文件路径。
两个素材引用 chain0 和 chain1 均指向 Sucai1.mp4。
playlist0 对应名为 V1 的视频轨道。
轨道中的两条 entry 按顺序播放，分别选取原素材的 00:00:00.000—00:00:20.000 和 00:00:30.600—00:00:41.233。
in、out 表示原素材中的起止位置，out 包含结束帧。
初步理解：工程通过“素材引用＋片段范围＋排列顺序”保存剪辑结果，重新打开时仍需能够找到原素材。
依据：
本次实验文件：Week1Work.mlt 中的 resource、playlist0 和 entry。
MLT XML 官方文档：https://www.mltframework.org/docs/mltxml/
MLT 起止点与时长说明：https://www.mltframework.org/docs/mvcp/
#### 保存工程的源码初步调查

在 Shotcut 官方仓库 master 分支的 `src/mltcontroller.cpp` 中，定位到 `Controller::saveXML(...)` 函数。

该函数使用 MLT 的 XML Consumer 生成工程 XML，并设置时间表示、工程标题等属性。在普通文件保存分支中，生成的 XML 经处理和校验后，通过 `QTextStream` 写入文件，最后调用 `QSaveFile::commit()` 完成保存。

其中，`time_format` 设置为 `clock`，与本次 Week1Work.mlt 样本中观察到的时钟格式时间值一致。

源码依据：https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp

版本说明：本次查看的是 master 分支，尚未记录具体提交编号；本地实验使用 Shotcut 26.8.1，两者尚未进行版本对应核对。

当前进度：已定位保存工程函数，并初步理解普通文件保存流程；界面如何调用该函数、工程读取流程尚待继续调查。
#### 打开工程的源码初步调查

在 `src/mltcontroller.cpp` 中定位到 `Controller::open(...)` 函数。

该函数先调用 `checkFile()` 检查文件；检查未报告错误后，关闭当前内容。对于 `.mlt` 文件，它会处理路径编码，然后通过 `Mlt::Producer` 创建读取对象，并检查对象是否有效。

读取成功后，函数根据条件更新工程和预览参数，并读取工程中保存的音频声道数、处理模式。本次 Week1Work.mlt 样本包含相应的 `shotcut:projectAudioChannels` 和 `shotcut:processingMode` 属性，可与这一读取流程对照。

该函数返回整数错误码：正常流程返回 0；读取对象无效时设为 1；文件检查失败时返回检查得到的错误码。

初步结论：这段代码通过 MLT 读取工程，而不是自行逐项解析 XML。MLT 内部解析以及界面时间轴恢复流程尚待进一步调查。

源码依据：[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)
#### 主窗口与保存控制器的调用关系

在 `src/mainwindow.cpp` 中定位到 `MainWindow::saveXML(...)` 函数。该函数根据当前工程状态选择保存对象，再调用 `MLT.saveXML(...)` 执行保存。

当时间轴模型中存在轨道时，函数传入 `multitrack()`，保存多轨时间轴。本次样本包含 V1 轨道及两个片段，对应这一分支。其他分支分别处理播放列表、其他有效内容对象和空工程。

初步确认的分工为：

- `MainWindow::saveXML`：选择保存内容，并传递文件名、路径选项和工程备注。
- `Controller::saveXML`：生成工程 XML，在普通文件保存流程中将其写入文件。

源码依据：
[https://github.com/mltframework/shotcut/blob/master/src/mainwindow.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp
](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)
当前已确认这两个函数之间的调用关系，保存按钮到主窗口保存函数的调用路径尚待调查。
#### 保存操作入口与调用流程

在 `src/mainwindow.cpp` 中定位到 `MainWindow::on_actionSave_triggered()`。

该函数先停止时间轴录制。如果当前工程没有保存路径，则进入“另存为”流程；如果已有路径，则在通过可写检查后调用定期备份逻辑，再执行 `saveXML(m_currentFile)`。

已确认的已有路径保存调用链为：

`MainWindow::on_actionSave_triggered()` → `MainWindow::saveXML(...)` → `Controller::saveXML(...)`

三个函数分别承担保存操作处理、保存对象选择、XML 生成与文件写入的职责。

保存后，入口函数重新建立自动保存对象、更新窗口和撤销历史状态，并根据 `success` 显示成功消息或错误提示。

观察到的细节：已有路径分支末尾返回固定值 `true`，而不是 `success`，因此不能仅凭该入口函数返回 `true` 判断文件保存成功。

源码依据：
[https://github.com/mltframework/shotcut/blob/master/src/mainwindow.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
[https://github.com/mltframework/shotcut/blob/master/src/mltcontroller.cpp](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)

当前范围：已追踪保存操作处理函数到文件写入的主要路径；尚未核对界面动作绑定，“另存为”内部流程也未展开调查。
### 5.3 模块依赖图
待根据源码绘制。

### 5.4 工程样本
已生成 Week1Work.mlt，并在本机验证可以重新打开。
存放位置：docs/samples/Week1Work.mlt。
原始素材：Sucai1.mp4。当前工程通过本机绝对路径引用素材，在其他电脑上打开时需重新定位素材。

### 5.5 关键技术发现与证据
待记录：技术结论、源码路径或官方文档链接。

## 6. 六维对比

| 维度 | OpenCut Classic | AutoClip | Shotcut/MLT |
|---|---|---|---|
| 架构模式 | 待调研 | 待调研 | 待调研 |
| 核心算法 | 待调研 | 待调研 | 待调研 |
| 数据模型 | 待调研 | 待调研 | 待调研 |
| 部署方式 | 待调研 | 待调研 | 待调研 |
| 许可证 | 待核对 | 待核对 | 待核对 |
| 可借鉴点与不可借鉴点 | 待分析 | 待分析 | 待分析 |

## 7. Descript / Opus Clip 体验记录

待记录：体验日期、可用免费功能、操作步骤及交互设计要点。
未能体验的部分说明原因。

## 8. 调研结论与后续工作

待总结：适合本项目参考的设计、限制和待验证问题。

## 9. 参考资料

待补充官方文档和源码链接。
各项技术结论应同时在对应正文位置标注证据。
