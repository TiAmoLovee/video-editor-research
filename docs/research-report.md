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

#### 运行方式与完成范围

本次在 Windows 10 环境中使用已安装的 Shotcut 26.8.1，完成素材导入、片段剪辑、工程保存和重新打开。

已使用 Git 克隆 Shotcut 官方源码仓库，本地位置为 `D:\CodeResearch\shotcut`。

已将本地源码切换到提交 `8cd39efcdf8ab80390ee4736db2f52577fc82cdc`，并通过 `git rev-parse HEAD` 核对，输出与报告记录一致。

当前已完成软件基础使用实验、源码克隆和部分源码阅读。尚未从源码编译运行 Shotcut，也未单独部署 MLT。本地安装的软件与所查源码提交是否对应同一发布版本，尚未核对。

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
本图为工程保存部分的模块关系草稿，仅覆盖目前已阅读的源码。

```mermaid
flowchart TD
    A["主窗口 MainWindow"]
    B["时间轴面板 TimelineDock"]
    C["多轨数据模型 MultitrackModel"]
    D["MLT 多轨对象"]
    E["保存控制器 Controller"]
    F["MLT XML 输出组件"]
    G[".mlt 工程文件"]

    A -->|"通过 model() 取得数据模型"| B
    B -->|"返回 m_model"| C
    C -->|"tractor() 返回 m_tractor"| D
    A -->|"将取得的多轨对象交给 saveXML()"| E
    E -->|"连接保存对象，生成 XML"| F
    F -->|"返回 XML 文本"| E
    E -->|"写入文件"| G
```
### 时间轴对象的获取关系

`MainWindow::multitrack()` 调用 `m_timelineDock->model()->tractor()` 获取时间轴对象。

其中：

- `TimelineDock::model()` 返回面板持有的 `MultitrackModel` 对象地址。
- `MultitrackModel::tractor()` 返回模型持有的 `m_tractor`，类型为 `Mlt::Tractor*`。

这条路径说明，主窗口保存时间轴时，从时间轴面板的数据模型取得已有的 MLT 多轨对象，再交给保存控制器处理。

补充源码依据：

- [时间轴面板](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/docks/timelinedock.h)
- [多轨数据模型](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/models/multitrackmodel.h)

本图仍为时间轴对象获取与工程保存部分的关系图，尚未覆盖完整系统。
### 源码对应关系

- 主窗口：`MainWindow::on_actionSave_triggered()` 处理保存操作，再调用 `MainWindow::saveXML()` 选择保存内容。
- 控制器：`Controller::saveXML()` 调用 MLT 生成 XML，在普通文件保存分支中完成文件写入。
- MLT XML 输出组件：由控制器创建并连接工程对象，用于生成 XML。

### 源码依据

- [主窗口源码](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mainwindow.cpp)
- [控制器源码](https://github.com/mltframework/shotcut/blob/8cd39efcdf8ab80390ee4736db2f52577fc82cdc/src/mltcontroller.cpp)

### 当前范围

已整理工程保存部分的模块关系。时间轴编辑、播放预览、滤镜和视频导出等模块尚未纳入，本图后续继续补充。

### 5.4 工程样本
已生成 Week1Work.mlt，并在本机验证可以重新打开。
存放位置：docs/samples/Week1Work.mlt。
原始素材：Sucai1.mp4。当前工程通过本机绝对路径引用素材，在其他电脑上打开时需重新定位素材。

### 5.5 关键技术发现与证据
#### 可借鉴点：将剪辑安排与视频素材分开记录

本次实验中，Week1Work.mlt 通过素材路径、片段起止位置和排列顺序保存剪辑安排，原始视频仍作为独立文件存在。

对 ClipForge 的启发：可以先用统一的剪辑清单记录选中的片段，再根据清单生成视频或 MLT 工程。人工调整时更新清单，使不同导出方式使用一致的剪辑安排。

这是结合本次调研与任务书提出的设计思路，不表示 Shotcut 内部使用了 ClipForge 所规划的 EDL JSON。

依据：本次工程样本，以及第 5.2、5.3 节记录的源码与官方文档。
#### 架构分工的初步观察

在已阅读的代码中，主窗口、时间轴数据模型和保存控制器承担不同职责：

- 主窗口处理保存操作，并选择保存内容。
- 时间轴面板通过数据模型提供已有的 MLT 多轨对象。
- 保存控制器调用 MLT 生成 XML，并负责文件写入。

可借鉴之处：ClipForge 也可以分别组织用户操作、剪辑数据和导出处理，避免把所有逻辑集中在界面代码中。

同时，已调查的保存流程直接使用 MLT 对象，说明这部分实现与 MLT 的接口存在依赖。若替换底层媒体框架，相关代码需要调整。这个判断仅针对已查看的流程，不代表已完成整个 Shotcut 架构的分析。

源码依据见第 5.2、5.3 节。
#### 许可证初步调查

Shotcut 官方 FAQ 标明软件采用 GPLv3。

MLT 官方版权政策说明，框架核心采用 LGPLv2.1；模块和附带程序的许可证可能不同。因此，需要区分 Shotcut 应用程序、MLT 框架核心及具体模块，不能只用一个许可证名称概括全部组件。

本次完成的是项目级许可证信息调查，尚未逐项核对本机安装包所包含的依赖和模块。

官方依据：

- [Shotcut FAQ](https://shotcut.org/FAQ/)
- [MLT 版权政策](https://www.mltframework.org/docs/copyrightpolicy/)
## 6. 六维对比

| 维度 | OpenCut Classic | AutoClip | Shotcut/MLT |
|---|---|---|---|
| 架构模式 | 待调研 | 待调研 | 桌面应用。已调查的工程保存路径中，主窗口处理保存操作，时间轴面板及模型提供多轨对象，控制器调用 MLT 生成 XML 并写入文件。依据见第 5.2、5.3 节。 |
| 核心算法 | 待调研 | 待调研 | 待调研 |
| 数据模型 | 待调研 | 待调研 | 工程使用 MLT XML 保存：resource 记录素材引用，playlist 中的 entry 记录片段范围与排列顺序；时间轴模型通过 tractor() 返回 MLT 多轨对象。依据见第 5.2、5.3 节。 |
| 部署方式 | 待调研 | 待调研 | 使用已安装的 Shotcut 26.8.1 完成本地实验；已克隆官方源码并切换到报告记录的固定提交。尚未从源码编译运行，也未单独部署 MLT。依据见第 5.1 节。 |
| 许可证 | 待核对 | 待核对 | Shotcut：GPLv3；MLT 框架核心：LGPLv2.1。MLT 模块及附带程序可能采用不同许可证，使用时需分别核对。依据见第 5.5 节。 |
| 可借鉴点与不可借鉴点 | 待分析 | 待分析 | 待分析 |

## 7. Descript / Opus Clip 体验记录

待记录：体验日期、可用免费功能、操作步骤及交互设计要点。
未能体验的部分说明原因。

## 8. 调研结论与后续工作

待总结：适合本项目参考的设计、限制和待验证问题。

## 9. 参考资料

待补充官方文档和源码链接。
各项技术结论应同时在对应正文位置标注证据。
