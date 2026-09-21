# jianying-mixcut（剪映混剪流水线）

把同一产品的多个视频号/抖音介绍视频，混剪成一条 ≤45s 的可过查重成片，同时产出剪映草稿供微调。

面向 AI agent（Kimi Code / Codex / Claude Code 等）设计的 skill：agent 读 `SKILL.md` 获得完整工作流，调用 `mixcut.py` 执行各环节；其中「语义去重 + 口播文案改写」由 AI 完成，`plan.json` 是人与 AI 的确认检查点。

## 流水线能力

- **素材收集**：collector.py 从抖音按账号（免登录）或关键词（扫码登录）批量收集好物分享素材，自动下载无水印源、抽帧质检、人脸初筛，素材表 CSV 状态机管理
- **AI 语义去重**：faster-whisper 转写全部源片逐字稿，AI 做语义级去重选段，剔除重复/冗长介绍
- **统一配音**：改写口播文案后用 edge-tts 单音色配音（默认云希），全片一个声音
- **烧录字幕**：字幕直接烧进画面，剪映草稿里是独立文字轨
- **去重处理**：源片字幕模糊带 + 幕布 98% 不透明度 + 移动水印 + 1.03 放大 + 横屏模糊填充
- **固定模板**：封面粉字产品名+型号（前 1.3s）、结尾品牌 logo 页（1.5s）
- **双产物**：`final.mp4` 直发版 + 剪映草稿（自动装进剪映草稿目录，重启可见，各元素独立轨道可微调）

## 安装

```bash
# 1. 克隆本仓库为 skill 目录（以 Kimi Code 为例）
git clone https://github.com/sunzhen668899-cyber/jianying-mixcut.git ~/.kimi-code/skills/jianying-mixcut
# Claude Code: ~/.claude/skills/jianying-mixcut
# Codex: ~/.codex/skills/jianying-mixcut

# 2. 安装 Python 依赖（建议独立 venv，Python 3.10+）
pip install -r requirements.txt
playwright install chromium   # 素材收集器需要

# 3. 安装剪映专业版（无需会员）
```

运行时依赖：联网（edge-tts）、ffmpeg（由 imageio-ffmpeg 提供）。

## 使用

对 agent 直接说：「用 jianying-mixcut 剪 XX 产品，素材在 <文件夹>」。agent 会按 SKILL.md 执行：

```
mixcut.py probe      --work <任务目录>   # 探测素材、抽帧定位源片字幕区
mixcut.py assets     --work <任务目录>   # 生成幕布/水印/结尾页/BGM
mixcut.py prep       --work <任务目录>   # 源片字幕带模糊
mixcut.py transcribe --work <任务目录>   # whisper 转写逐字稿
# --- AI 读逐字稿，写 plan.json（分段+改写文案），给用户确认 ---
mixcut.py tts        --work <任务目录>   # edge-tts 逐段配音
mixcut.py timeline   --work <任务目录>   # 计算时间线（变速 0.8~1.3）
mixcut.py render     --work <任务目录>   # ffmpeg 合成成片
mixcut.py qc         --work <任务目录>   # 抽 5 帧质检
mixcut.py draft      --work <任务目录> --install   # 生成剪映草稿并安装
```

详见 [SKILL.md](SKILL.md)。

### 素材收集（可选前置）

```
collector.py --lib <素材库> author --url <作者主页> --model <型号>   # 按账号收（免登录）
collector.py --lib <素材库> login                                    # 扫码登录（搜索前跑一次）
collector.py --lib <素材库> search --keyword <词> --model <型号>     # 按关键词搜
collector.py --lib <素材库> download [--model <型号>]                # 下载"待下载"行（无水印源）
collector.py --lib <素材库> qc [--model <型号>]                      # 时长/分辨率/抽帧/人脸初筛
```

素材表 `<素材库>/素材表.csv` 状态机：候选 → 待下载 → 已下载 → 待审核 → 可用/弃用 → 已用。

## 可配置项（config.json）

产品名/型号、水印名（默认「叁哥」）、音色、时长上限（默认 45s）、自备 BGM 文件、封面/结尾时长等。

## 文件结构

- `SKILL.md` — agent 工作流 playbook（触发词、流程、检查点、已知坑）
- `mixcut.py` — 混剪统一 CLI，无第三方 UI 依赖，全子命令 `--work <任务目录>`
- `collector.py` — 抖音素材收集器（author/search/download/qc/login）
- `assets/face_detection_yunet_2023mar.onnx` — qc 人脸初筛模型（OpenCV Zoo，MIT）
- `requirements.txt` — Python 依赖

## 说明

- 平台查重无 100% 保证，建议首发观察流量再批量
- 默认 BGM 为合成垫音，建议在剪映乐库换热门免费曲（草稿里 BGM 独立一轨）
