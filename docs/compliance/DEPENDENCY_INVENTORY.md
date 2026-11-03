# 第八周依赖清单启动记录

日期：2026-11-04。来源基线：`6b507a6fb735d3dbd4f733fc42cae0e0d540ca57`。

已生成 [机器可读依赖清单](dependency-inventory.json) 与 [Python 安装元数据快照](python-installed-metadata.json)，作为 SBOM 审计的第一批证据。这是有来源和版本边界的盘点，不是已经完成的许可审计，也不宣称符合某个标准 SBOM JSON Schema。

**全出口水印后续更新：** 本轮为 worker 添加 `fonts-noto-cjk` 和明确字体配置，已另存 [当前工作树盘点](dependency-inventory-visible-marking.json) 与 [来源及差异记录](dependency-inventory-visible-marking.provenance.json)。原清单和安装元数据快照保持逐字节不变。新清单的 `source_baseline_commit` 仍指上游基线，**不表示当前文件与该提交完全相同**；实际采集文件以新清单 `input_sha256` 为准。与原清单相比，已登记输入中仅 `deploy/Dockerfile.worker` 的哈希变化，266 条 Python/npm 记录保持不变。

字体 apt 包已出现在 Dockerfile，尚未构建并记录该镜像的具体包版本、许可文件和 digest；因此没有把它当作已核验的 Python/npm 组件加进计数。

## 当前覆盖

| 范围 | 本次结果 | 解释 |
| --- | ---: | --- |
| 前端锁文件 | 201 条安装位置记录 | 保留不同嵌套版本、开发依赖、可选平台包；不等于实际打进浏览器成品的包数 |
| Python 显式固定版本 | 33 个名称/版本 | 三份 requirements 去重，保留每一处来源；冻结清单不能直接证明业务直接依赖关系 |
| Python 其余可达包 | 32 个名称/版本 | 仅沿本机安装元数据的有效依赖边、平台条件和 extras 发现；版本是环境观察值，不是仓库锁定承诺 |
| 合计 | 266 条记录 | 不是完整容器镜像、模型、素材或二进制发行物 SBOM |

本次 33 个显式固定版本均找到相同版本的安装元数据，可达依赖无缺失；所有 266 条都有某种许可标签。**有标签不等于许可已核验**：锁文件和 Python METADATA 可能漏掉捆绑代码、附带二进制的条款及版权声明，广义 BSD 分类器也不能替代精确许可证文本。

## 证据与分类规则

- npm 记录来自 `frontend/package-lock.json`，保留安装路径、版本、来源地址、integrity、许可原标签、平台限制及 direct/transitive。包含所有锁定平台条目，未声称本机都安装了这些包。
- Python 根记录来自三份 `backend/requirements*.txt`；`-r` 引用单独记录，同名同版本去重保留声明位置。条件和 extras 使用采集环境解析，环境信息保留在 JSON。
- Python 许可依次保留 `License-Expression`、简短 `License` 字段、原许可分类器。完整长 `License` 字段用字数和 SHA256 标识，未把整篇许可文本压缩成猜测的 SPDX ID。
- 若安装版本不同，工具不会把安装版本的许可借给固定版本；两个版本分别记录。仅采集从清单可达的包，不把整个虚拟环境列为项目依赖。
- `license_review_signal` 只是根据原标签的文本分流。`contains-weak-copyleft-family-label-review` 或 `compound-or-multiple-labels-review` 表示需人工检查，**不是对项目整体“传染性”、链接方式或分发义务的法律结论**。其他标签也保持待审。
- 本次应优先复核 `certifi` 的 `MPL-2.0`、`tqdm` 的 `MPL-2.0 AND MIT`、`packaging` 的 `Apache-2.0 OR BSD-2-Clause`，以及 `lightningcss` 和对应可选平台包的 `MPL-2.0`；是否随成品分发须按实际构建核对。

## 可复现命令

在仓库根目录运行。工具依赖 Python 环境中的 `packaging`；原采集版本已写入快照。本次复用该快照，未重新读取环境包；三份 requirements 的哈希与快照必须一致，变动后工具拒绝沿用旧快照。

```powershell
python tools/week8/build_dependency_inventory.py --metadata-snapshot docs/compliance/python-installed-metadata.json --output dependency-inventory-visible-marking-reproduced.json --baseline 6b507a6fb735d3dbd4f733fc42cae0e0d540ca57
python -m unittest discover -s tools/week8 -p 'test_*.py' -v
```

复算当前文件会得到新盘点，不会得到 Dockerfile 更新前的历史清单。重现原清单时须恢复原 `input_sha256` 对应文件及生成器版本，不能在当前工作树覆盖历史证据。需要采集新环境时另选快照与输出文件名，例如：

```powershell
python tools/week8/build_dependency_inventory.py --capture-installed --metadata-snapshot python-installed-metadata-new.json --output dependency-inventory-new.json --baseline 6b507a6fb735d3dbd4f733fc42cae0e0d540ca57
```

原轮次离线复算与其初次输出逐字节一致；本次新盘点再次离线复算也逐字节一致，记录见来源文件。五项解析/证据隔离回归及 Ruff 检查重新通过。测试覆盖版本不符时不借用许可、重复声明去重、平台与 extras 条件、npm scoped/nested 包、复合许可不被简化。

## 仍需完成的审计

基线根目录尚无项目 `LICENSE`、`THIRD_PARTY_NOTICES.md` 和 `datasets/LICENSE.md`。它们分别需要作者确认项目授权、核查实际引用代码及登记素材来源；当前不替作者选择许可，也不因素材已用于实验而推断拥有公开分发授权。

1. 按真正交付的安装包/构建成品复核许可全文、版权声明、捆绑库与分发条件；形成逐项结论和必要的 `THIRD_PARTY_NOTICES.md`。目前没有据此选定项目自身许可证。
2. 构建最终 Linux 镜像后，记录不可变镜像 digest、基础镜像和 OS 包清单；当前 Dockerfile 的 apt 包与 Python 传递依赖并未全部锁为精确工件。`redis:7.4.11-alpine` 须查该版本原始许可，不可照搬旧 Redis 的许可印象。
3. 单列 FFmpeg 实际 `-version/-buildconf/-L` 证据、Shotcut/MLT 的实际发行版与调用方式；不能因 Python `av` 包标签是 BSD 就推定其附带媒体库均为 BSD。
4. 单列固定 base/small 模型权重、Noto 字体及任何其他分发资源的许可；代码库许可与模型权重许可分别核对。
5. 确认评测/演示素材来源、授权和人物同意记录。源码依赖清单不提供这些授权，也不替代素材登记。
6. 根据最终发行包边界生成标准格式 SBOM，并验证格式及覆盖；该步骤完成前不把本清单称为完整发行物 SBOM。

本次工具不访问网络、不安装软件、不上传元数据、不更改现有项目许可，未替用户作出许可证选择。
