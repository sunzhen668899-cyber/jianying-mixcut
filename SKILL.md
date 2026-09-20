# 剪映混剪流水线（jianying-mixcut）

把同一产品的多个视频号/抖音介绍视频，混剪成一条 ≤45s 的可过查重成片，同时产出剪映草稿供微调。

## 何时使用

用户给一批同产品的素材视频（一个文件夹），要做「混剪去重 + AI 配音 + 统一字幕 + 封面/结尾模板」的短视频，目标平台抖音/视频号。触发词：混剪、九牧混剪、视频号混剪、剪映自动化、去重剪辑。

## 环境（首次使用需安装）

- Python 3.10+，安装依赖：`pip install -r requirements.txt`（建议装在独立 venv；下文所有 `PY` 均指该环境的 python）
- 本机已就绪的 venv：`D:/vibe coding/security-lab/jianying-mixcut/.venv/Scripts/python.exe`
- 脚本：`<skill目录>/mixcut.py`（本机位于 `C:/Users/Administrator/.kimi-code/skills/jianying-mixcut/`）
- 剪映专业版已安装（无需会员），草稿目录 `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`
- 联网：edge-tts 需要

## 输入（向用户确认）

1. 素材文件夹路径（放 2~N 个同产品视频，拷入 `<任务目录>/sources/`）
2. 产品名 + 型号（封面用，如「九牧智能毛巾架 9340024-HC2」）
3. 可选：水印名（默认「叁哥」）、音色（默认云希 `zh-CN-YunxiNeural`）、时长上限（默认 45s）、BGM 音乐文件（默认合成垫音，建议用户自备或在剪映换乐库）

## 工作流（严格按序）

任务目录新建在纯 ASCII 路径下，如 `D:/vibe coding/security-lab/jianying-mixcut/jobs/<产品型号>/`。

### 1. probe — 探测素材
```
PY mixcut.py probe --work <任务目录>
```
读 `work/probe/` 里每个源片的 3 张抽帧，**肉眼定位烧录字幕的纵向区间**，写回 `config.json` 的 `sources.<文件>.band`（如 `[0.66, 0.84]`；源片无烧录字幕则 `null`）。同时把产品名/型号等填入 config。

### 2. assets + prep + transcribe — 预处理（可连续跑）
```
PY mixcut.py assets --work <任务目录>
PY mixcut.py prep --work <任务目录>
PY mixcut.py transcribe --work <任务目录>
```
prep 完必须抽一张 src_clean 的帧确认字幕带被模糊覆盖；没盖住就调 band 重跑。

### 3. 精剪决策（Kimi 做，不可跳过）
读 `work/transcripts/*.json` 全部逐字稿，做语义级去重：
- 同一卖点在不同源片的重复介绍，只保留讲得最好的一段；剔除寒暄、冗长、弱信息段
- 为每个保留段**改写口播文案**（不是照抄原文），修正 ASR 热词错字（型号、品牌、专业词），文案宁短勿长（时长靠文案控制，不靠硬变速；变速只允许 0.8~1.3）
- 结构：痛点钩子 → 核心卖点 3~5 个 → 信任背书 → 行动号召
写出 `plan.json`：
```json
{"canvas": [1080, 1920],
 "sources": {"v1": "work/src_clean/1.mp4", "v2": "work/src_clean/2.mp4"},
 "segments": [{"order": 1, "video": "v1", "start": 12.3, "end": 18.5, "text": "改写后的口播文案"}]}
```
**检查点**：把分段清单（每段来源+时间区间+文案）和预估总时长发给用户确认后再继续。

### 4. tts + timeline — 配音与时间线
```
PY mixcut.py tts --work <任务目录>
PY mixcut.py timeline --work <任务目录>
```
正片总时长 > max_dur 时：回第 3 步砍段或截短文案，重跑这两步（不要靠提速硬压）。

### 5. render + qc — 成片与质检
```
PY mixcut.py render --work <任务目录>
PY mixcut.py qc --work <任务目录>
```
看 `work/qc/` 5 张抽帧逐项核对：封面型号字、字幕、移动水印、源片字幕已模糊、结尾 logo 页。`work/final.mp4` 即直发版。

### 6. draft — 剪映草稿（微调版）
```
PY mixcut.py draft --work <任务目录> --install
```
自动拷入剪映草稿目录，提示用户**重启剪映**打开。草稿里字幕/水印/幕布/BGM 都是独立轨道可改。

## 交付话术要点

- 给用户两个产物：`work/final.mp4`（直发）+ 剪映草稿（微调导出）
- BGM 是合成垫音时，明确建议在剪映乐库换热门免费曲（BGM 独立一轨）
- 平台查重无 100% 保证，建议首发观察流量再批量
- 去重手段说明：源片字幕模糊 + 新文案新配音 + 幕布 98% + 移动水印 + 1.03 放大 + 横屏模糊填充

## 已知坑（勿重复踩）

- ffmpeg 滤镜脚本里 Windows 路径：盘符冒号要在引号内写成 `C\:/...`（脚本已处理，手写滤镜时注意）
- pyJianYingDraft 的时间单位是**微秒**；`VideoSegment` 同时给 source_timerange+speed 时槽位时长按 `round(src_us/speed)` 算（脚本已对齐）
- Git Bash 会篡改 `C:/...` 形式的 ffmpeg 参数（MSYS 路径转换），ffmpeg 一律通过 Python subprocess 调用
- 横屏源片的模糊填充背景会带回源片亮色文字的重影，属正常现象
