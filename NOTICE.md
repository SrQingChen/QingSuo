# 附加条款与第三方说明 / Additional Terms & Third-Party Notices

青梭 QingSuo（下称"本项目"）以 **GNU General Public License v3.0** 发布，全文见 [LICENSE](LICENSE)。
本文件包含两类内容：**GPLv3 第 7 条允许的附加条款**，以及第三方组件说明。

> **本文件不缩小 GPLv3 授予你的任何权利。**
> 本文件与 GPLv3 抵触时，以 GPLv3 为准。依照 GPLv3 第 7 条末段，若你认为本文件中存在
> "进一步限制"（further restriction），**你有权移除相应条款**——这是 GPLv3 明文给你的权利，
> 我们在此明确承认它。

---

## 一、附加条款（依据 GPLv3 第 7 条）

### 1. 保留署名与来源（§7(b)）

分发本项目的副本或修改版本时，须保留本文件、`LICENSE`、源代码文件头部的版权声明与作者署名，
以及指向本仓库的来源链接。不得移除或篡改这些声明。

### 2. 不得虚假标示来源（§7(c)）

不得声称你的修改版本是官方版本，或以本项目名义发布。修改版本必须以显著方式标明**"已修改"**
及修改日期。

### 3. 名称与标识不随许可授权（§7(e)）

"青梭""QingSuo"名称与 [docs/logo.svg](docs/logo.svg) 标识**不在 GPLv3 的授权范围内**，
GPLv3 亦不授予任何商标权。修改版本可以自由使用本项目的全部源代码（GPLv3 已授予该权利），
但不得使用上述名称与标识暗示官方关联或背书。

### 4. 免责声明（§7(a)）

在 GPLv3 第 15、16 条之外重申：本项目按**"现状"**提供，不附任何明示或默示担保。作者不对
剪辑结果是否符合预期、素材损坏、数据丢失或任何间接损失承担责任。请务必保留原始素材副本。

---

## 二、并行授权：GPLv3 + 商业授权

本项目采用**双授权（dual licensing）**模式：

| 授权路径 | 适用对象 | 条件 |
|---|---|---|
| **GPLv3** | 所有人，默认路径 | 遵循 GPLv3 全部条款（含 copyleft：分发修改版须以 GPLv3 开源） |
| **商业授权** | 希望在不承担 GPL 义务的前提下使用（如闭源产品集成、内部专有分发） | 需另行取得作者的书面授权 |

这部分之所以**合法且成立**：作者是本项目全部代码的著作权人，可以对自己拥有版权的作品
**同时**提供多套许可，由使用者择一。这与你无法对他人施加额外限制是两回事——前者是授权，
后者是限制。有商业授权需求请通过仓库 Issue 联系。

> 若本项目接受了外部贡献，作者若希望这些贡献也能被纳入商业授权，需与贡献者另行签署
> CLA（贡献者许可协议）。在此之前，外部贡献部分仍受 GPLv3 约束。

---

## 三、关于"最终解释权"

需要如实说明一个容易踩的坑：**单方保留"最终解释权"的条款，不能作为许可条件约束下游使用者。**

- **在 GPLv3 下**：这类条款属于第 10 条所禁止的"进一步限制"（further restrictions）。
  依第 7 条末段，下游使用者**有权将其移除**；因此它既无法约束他人，反而会让分发本身
  处于不合规状态。
- **在一般格式条款下**：市场监管总局《合同行政监督管理办法》（2023 年施行）沿袭了原
  《合同违法行为监督处理办法》第十一条的立场，禁止经营者以格式条款排除消费者
  "请求就格式条款作出解释的权利"。"本公司拥有最终解释权"式条款在中国的司法与执法实践中
  通常不被支持。（以上为一般性说明，不构成法律意见。）

本项目因此**不采用"最终解释权"作为许可限制**，而采用不冲突的替代安排：

1. **许可范围完全以 GPLv3 为准，不作任何限缩。** 这一点本身就是对使用者的最大善意——
   你不必担心某天被一句"解释权归作者"收回已经获得的自由。
2. **项目文档对本项目的设计意图与用法具有权威性。** `README.md` / 本文件对本项目
   *是什么、怎么用、参数含义* 的说明是官方口径；如果你的使用方式与文档描述相悖，
   官方不予背书，但这不影响 GPLv3 已授予你的权利。
3. **名称与标识**（见上文 §一.3）是真正被保留的部分，且是 GPLv3 第 7 条明确允许保留的。

简言之：**代码自由，名称保留，解释以文档为准但不得凌驾于 GPLv3 之上。**

---

## 四、第三方组件

本项目在运行与分发中涉及以下第三方组件，其权利归各自所有者，遵循各自的许可。
GPLv3 与下列许可均兼容（Apache-2.0 可单向并入 GPLv3）：

| 组件 | 用途 | 许可 |
|---|---|---|
| [PP-OCRv5](https://github.com/PaddlePaddle/PaddleOCR) ONNX 权重（`models/`） | 文字检测与识别模型 | Apache-2.0 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) / `rapidocr-onnxruntime` | OCR 推理封装与字典 | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 推理引擎（CPU） | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) · [Uvicorn](https://github.com/encode/uvicorn) | HTTP 服务 | MIT / BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | 请求校验 | MIT |
| [OpenCV](https://github.com/opencv/opencv-python) | 帧相似度签名 | Apache-2.0 |
| [NumPy](https://github.com/numpy/numpy) | 数组运算 | BSD-3-Clause |
| [psutil](https://github.com/giampaolo/psutil) | 性能采样 | BSD-3-Clause |
| [Pillow](https://github.com/python-pillow/Pillow) | 图像处理 | MIT-CMU (HPND) |
| [FFmpeg](https://ffmpeg.org/)（`ffmpeg` / `ffprobe` 可执行文件） | 抽帧与剪辑 | **外部依赖，本项目不随附分发**；其许可以具体构建为准（LGPL-2.1+ 或 GPL） |

`models/` 目录下随仓库分发的轻量档权重为 Apache-2.0 授权的原始文件，未作修改；
高精度档权重请用 `python scripts/fetch_models.py` 自行获取。

---

## 五、贡献

除非你在提交时明确声明另有约定，你向本项目提交的贡献（Pull Request、补丁、Issue 中的代码片段）
将按 **GPLv3** 授权，且你确认有权作出该授权。

---

## English Summary

QingSuo is released under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)).
This file adds only those additional terms expressly permitted by **GPLv3 §7**: preservation of
attribution (§7b), no misrepresentation of origin (§7c), reservation of the "青梭 / QingSuo" name and
logo under trademark law (§7e), and a restated warranty disclaimer (§7a).

**Nothing here restricts the rights GPLv3 grants you.** Where this file conflicts with GPLv3, GPLv3
governs, and under GPLv3 §7 you may remove any term you consider a further restriction.

The project is **dual-licensed**: GPLv3 for the public, plus an optional commercial license available
from the copyright holder for use cases that cannot comply with GPLv3.

A unilateral "final interpretation right" clause is deliberately **not** used as a license condition:
under GPLv3 §10 it would be an unenforceable further restriction that downstream users may remove.
See section 三 above for details. This document is not legal advice.
