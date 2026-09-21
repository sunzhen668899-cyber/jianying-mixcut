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
3. 可选：水印名（默认「叁哥」）、音色（默认云希 `zh-CN-YunxiNeural`）、时长上限（默认 45s）、BGM（默认从 skill 内置曲库 `assets/bgm_library/` 选，每个版本配不同曲子；用户也可在音乐文件路径上自备）

## 工作流（严格按序）

任务目录新建在纯 ASCII 路径下，如 `D:/vibe coding/security-lab/jianying-mixcut/jobs/<产品型号>/`。

### 1. probe — 探测素材
```
PY mixcut.py probe --work <任务目录>
```
读 `work/probe/` 里每个源片的 3 张抽帧，**肉眼定位烧录字幕的上边界**，写回 `config.json` 的 `sources.<文件>.band`（如 `[0.78, 0.93]`；源片无烧录字幕则 `null`）。band[0] 是裁剪线：要比字幕最上沿再高 1~2%，否则字幕上沿会残留在画面里。同时把产品名/型号等填入 config。

### 2. assets + prep + transcribe — 预处理（可连续跑）
```
PY mixcut.py assets --work <任务目录>
PY mixcut.py prep --work <任务目录>
PY mixcut.py transcribe --work <任务目录>
```
prep 完必须抽一张 src_clean 的帧确认字幕区已裁除干净（prep 是直接裁掉 band[0] 以下画面再放大铺满，不用模糊——模糊会像一层透明膜，用户明确不要）；有残留就调 band[0] 重跑。

### 3. 精剪决策（Kimi 做，不可跳过）
读 `work/transcripts/*.json` 全部逐字稿，做语义级去重：
- 同一卖点在不同源片的重复介绍，只保留讲得最好的一段；剔除寒暄、冗长、弱信息段
- 为每个保留段**改写口播文案**（不是照抄原文），修正 ASR 热词错字（型号、品牌、专业词），文案宁短勿长（时长靠文案控制，不靠硬变速；变速只允许 0.8~1.3）
- 结构：痛点钩子 → 核心卖点 3~5 个 → 信任背书 → 行动号召
写出 `plan.json`：
```json
{"canvas": [1080, 1920],
 "sources": {"v1": "work/src_clean/1.mp4", "v2": "work/src_clean/2.mp4"},
 "bgm_file": "Monkeys Spinning Monkeys.mp3",
 "bgm_start": 0,
 "bgm_volume": 0.75,
 "segments": [{"order": 1, "video": "v1", "start": 12.3, "end": 18.5, "text": "改写后的口播文案"}]}
```
`bgm_file` 只写文件名时从 skill 内置曲库 `assets/bgm_library/` 取（也可写绝对路径用用户自备音乐）；`bgm_start` 是曲子起始偏移秒数；`bgm_volume` 是 BGM 相对音量（默认 0.75，经用户验收的听感平衡点；想「再小一点」就往下调，想突出 BGM 就往 1.0 靠）。不写则用 `work/assets/bgm.wav`（合成垫音，仅兜底——合成垫音听感像电流声，正式出片不要用）。
**检查点**：把分段清单（每段来源+时间区间+文案）和预估总时长发给用户确认后再继续。

### 4. tts + timeline — 配音与时间线
```
PY mixcut.py tts --work <任务目录>
PY mixcut.py timeline --work <任务目录>
```
正片总时长 > max_dur 时：回第 3 步砍段或截短文案，重跑这两步（不要靠提速硬压）。

注意：tts 会自动把文案里 ≥3 位的连续数字串按位分开再配音（`36656` 读作「三 六 六 五 六」，否则 TTS 会读成「三万六千六十五」），**plan 文案里型号保持连写**，字幕显示不受影响。

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

### 7. 三版本备选（默认流程，每次成品必做）

同一批素材、同一次收集，固定产出 **3 个不同版本**，供用户当天一个账号分时段发布（早中晚）。做法：
- 在第 3 步精剪决策时直接写 3 份 plan（不同切入角度，如：奶油风角度 / 双色功能角度 / 快节奏清单），分段组合、顺序、文案都要错开，避免平台判重；**每版 `bgm_file` 配不同的曲子**（曲库不同曲目或不同 `bgm_start` 偏移）
- 每版独立走 tts → timeline → render → qc → draft，每版前把 `config.json` 的 `draft_name` 改成 `<型号>A/B/C`。**每版切换 plan 后必须重跑 tts + timeline，哪怕只改了 BGM 字段**——timeline.json 和 work/tts/ 是磁盘上的共享状态，属于最后跑的那版；只重跑 render 会让所有版本都产出同一版的内容（已踩过两次）
- 产物归档：每版渲染完立刻 `cp work/final.mp4 work/final_<版本>.mp4` 并把 `work/qc/` 改名 `work/qc_<版本>/`，防止被下一版覆盖；plan 备份为 `plan_<版本>.json`
- 三版全部 QC 通过、草稿装好后再一次性汇总交付
- 素材尾段越界坑：draft 对 `end` 越界敏感（哪怕超 3ms 也报错），plan 里每段 `end` 要比源片实际时长至少短 0.03s

## 素材收集（collector.py，混剪的可选前置）

从抖音收集同产品好物分享素材，产出带质检标注的素材库，供混剪流水线挑选。素材表：`<素材库>/素材表.csv`（utf-8-sig，Excel 可直接打开），状态机：`候选 → 待下载 → 已下载 → 待审核 → 可用/弃用 → 已用`。收集门槛默认点赞≥30（`--min-likes` 可调）；只收无人脸、无作者名/抖音号烧录水印的好物分享类，无作者信息的固定角标放行。**调用一律 `PY collector.py --lib <素材库目录> <子命令>`（--lib 必传）**。

### author — 按账号批量收（免登录）
```
PY collector.py --lib <素材库> author --url <作者主页链接> --model <型号> [--max 50 --min-likes 30]
```
抓作者全部作品卡片（点赞取 `span.author-card-user-video-like`，排除推荐区噪声），达标的写素材表为「候选」。人工/AI 把要下载的行状态改为「待下载」。注意：按账号收是全店混合产品，qc 审核时按目标型号筛掉非目标产品（标弃用）。

### login + search — 按关键词搜（需登录）
```
PY collector.py --lib <素材库> login     # 扫码，cookies 存 browser_profile/
PY collector.py --lib <素材库> search --keyword <词> --model <型号>
```
抖音网页版视频页/作者页免登录，但搜索页强制登录；扫码一次后 profile 长效。

### download — 下载「待下载」行
浏览器打开视频页取 web 播放流（v26-web.douyinvod.com，即无水印源），curl 带 UA + `Referer: https://www.douyin.com/` 下载；<300KB 判定为风控占位流，删文件保持「待下载」，重试即可。

### qc — 质检「已下载」行
ffprobe 测时长/分辨率 + 抽帧到 `<型号>/qc/<id>/` + YuNet 人脸初筛，状态置「待审核」。然后 AI/人工看抽帧回填：人脸、水印贴纸、字幕带（如 `[0.82,0.95]`）、状态（可用/弃用）。

**抽帧必须至少 5 点（5/25/50/75/95%）或每 2~3s 一帧**：主持人出镜和烧字标题常集中在片头 0~12s 或片中某段，只抽 3 点（如 50/80%）会整段漏掉。plan 分段前同样要先确认各源片的「人脸/烧字分布图」，主持人出镜段整段弃用，不要只换封面帧。

### 收集器已知坑（勿重复踩）

- 全新 chromium 会被风控喂 2.6s/194KB 占位预览流；必须 `launch_persistent_context(browser_profile/)` 养 cookies，下载失败重试或先跑一次 login
- cv2（5.x）在 Windows 读不了中文路径图片：用 `np.fromfile + imdecode`；cv2 5.0 移除了 `CascadeClassifier`，人脸检测用 YuNet（`assets/face_detection_yunet_2023mar.onnx`，已随仓库）
- playwright 装完还要 `playwright install chromium`
- 素材表字幕带字段值含逗号，手写 CSV 行必须带引号

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
