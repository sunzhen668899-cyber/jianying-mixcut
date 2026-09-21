# -*- coding: utf-8 -*-
"""剪映混剪流水线统一 CLI。所有子命令都需要 --work <任务目录>。

任务目录结构:
  sources/            源视频(用户放入, 文件名建议 ASCII)
  config.json         probe 生成骨架, Kimi/用户补全(见 SKILL.md)
  plan.json           Kimi 根据逐字稿写出的精剪计划
  work/               全部中间产物与最终成片

子命令:
  probe       探测源视频参数+抽帧, 生成 config.json 骨架
  assets      生成幕布/水印/结尾logo页/BGM(可换用户音乐)
  prep        源片烧录字幕带模糊 -> work/src_clean/
  transcribe  faster-whisper 逐字稿 -> work/transcripts/
  tts         plan.json 文案 -> edge-tts 配音
  timeline    plan.json + 配音时长 -> work/timeline.json
  render      渲染最终成片 work/final.mp4(封面+字幕+水印+结尾logo)
  draft       生成剪映草稿, --install 自动拷入剪映草稿目录
  qc          成片质检(时长/响度/抽帧)
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT_RENDER = r"C\:/Windows/Fonts/simhei.ttf"   # ffmpeg 滤镜内: 引号+反斜杠转义冒号
FONT_PIL = "C:/Windows/Fonts/msyhbd.ttc"
CANVAS_W, CANVAS_H, FPS = 1080, 1920, 30
JY_DRAFTS = Path.home() / r"AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"

DEFAULTS = {
    "product_name": "产品名",
    "product_model": "型号",
    "watermark_text": "叁哥",
    "brand_cn": "九牧", "brand_en": "JOMOO", "brand_slogan": "专 注 高 端 卫 浴",
    "voice": "zh-CN-YunxiNeural", "tts_rate": "+15%",
    "max_dur": 45.0, "cover_dur": 1.3, "end_dur": 1.5,
    "bgm_file": "",            # 空=合成垫音; 否则为用户音乐文件路径
    "draft_name": "",
    "sources": {},             # "1.mp4": {"band": [0.75, 0.90], "top": 0.18(可选,顶部花字上裁线)} 或 null
}


def esc(path):
    """ffmpeg 滤镜内路径: 转义盘符冒号"""
    return Path(path).as_posix().replace(":", r"\:")


def run(cmd, err_msg):
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stderr[-3000:])
        raise SystemExit(err_msg)
    return proc


def probe_media(path):
    """返回 (时长秒, 宽, 高)"""
    proc = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    v = re.search(r"Video:.*? (\d{2,5})x(\d{2,5})", proc.stderr)
    return dur, (int(v.group(1)) if v else 0), (int(v.group(2)) if v else 0)


def load_cfg(work):
    cfg = dict(DEFAULTS)
    cfg.update(json.loads((work / "config.json").read_text(encoding="utf-8")))
    return cfg


BGM_LIB = Path(__file__).resolve().parent / "assets" / "bgm_library"


def resolve_bgm(work, plan):
    """BGM 优先级: plan.bgm_file > work/assets/bgm.wav(含 config.bgm_file 转换产物)。
    plan.bgm_file 只写文件名时, 从 skill 内置曲库 BGM_LIB 里找。"""
    f = (plan or {}).get("bgm_file") or ""
    if f:
        p = Path(f)
        if not p.is_absolute():
            p = BGM_LIB / f if (BGM_LIB / f).exists() else work / f
        return p
    return work / "work" / "assets" / "bgm.wav"


# ---------------------------------------------------------------- probe
def cmd_probe(work, args):
    src_dir = work / "sources"
    videos = sorted(p for p in src_dir.iterdir() if p.suffix.lower() in (".mp4", ".mov", ".mkv"))
    if not videos:
        raise SystemExit(f"{src_dir} 里没有视频文件")
    out_dir = work / "work" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if (work / "config.json").exists():
        cfg.update(json.loads((work / "config.json").read_text(encoding="utf-8")))
    for v in videos:
        dur, w, h = probe_media(v)
        cfg["sources"].setdefault(v.name, {"band": [0.75, 0.90]})
        cfg["sources"][v.name].update({"duration": round(dur, 2), "size": [w, h]})
        for pct in (0.1, 0.5, 0.8):
            run([FFMPEG, "-y", "-ss", f"{dur * pct:.1f}", "-i", str(v),
                 "-frames:v", "1", "-q:v", "3", str(out_dir / f"{v.stem}_{int(pct*100)}.jpg")],
                f"抽帧失败: {v.name}")
        print(f"{v.name}: {dur:.1f}s {w}x{h}, 抽帧 3 张 -> {out_dir}")
    (work / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"config.json 骨架已写入, 请查看抽帧确定每个源片的 band(烧录字幕纵向区间, null=无需模糊)")


# ---------------------------------------------------------------- assets
def cmd_assets(work, args):
    cfg = load_cfg(work)
    assets = work / "work" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)

    # 幕布: 稀疏斜纹+微粒, 上层 98% 不透明度叠加(去重特征)
    veil = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(veil)
    for x in range(-CANVAS_H, CANVAS_W, 24):
        d.line([(x, 0), (x + CANVAS_H, CANVAS_H)], fill=(255, 255, 255, 6), width=1)
    for _ in range(4000):
        x, y = rng.integers(0, CANVAS_W), rng.integers(0, CANVAS_H)
        a, r = int(rng.integers(4, 11)), int(rng.integers(1, 3))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, a))
    veil.save(assets / "veil.png")

    # 移动水印
    font = ImageFont.truetype(FONT_PIL, 72)
    wm = Image.new("RGBA", (280, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)
    d.text((20, 12), cfg["watermark_text"], font=font, fill=(255, 255, 255, 140),
           stroke_width=3, stroke_fill=(0, 0, 0, 80))
    wm.save(assets / "watermark.png")

    # 结尾 logo 页: 黑底 + 蓝色横带 + 品牌三行字
    card = Image.new("RGB", (CANVAS_W, CANVAS_H), (0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rectangle([0, 520, CANVAS_W, 1400], fill=(27, 79, 160))
    d.text((CANVAS_W / 2, 760), cfg["brand_cn"], font=ImageFont.truetype(FONT_PIL, 150),
           fill=(255, 255, 255), anchor="mm")
    d.text((CANVAS_W / 2, 1000), cfg["brand_en"], font=ImageFont.truetype(FONT_PIL, 200),
           fill=(255, 255, 255), anchor="mm")
    d.text((CANVAS_W / 2, 1230), cfg["brand_slogan"], font=ImageFont.truetype(FONT_PIL, 56),
           fill=(255, 255, 255), anchor="mm")
    card.save(assets / "endcard.png")

    # BGM: 用户音乐转 44.1k 单声道 wav; 否则合成垫音
    if cfg["bgm_file"]:
        run([FFMPEG, "-y", "-i", cfg["bgm_file"], "-ar", "44100", "-ac", "1",
             str(assets / "bgm.wav")], "BGM 转换失败")
        print(f"BGM 使用用户音乐: {cfg['bgm_file']}")
    else:
        sr, dur = 44100, 150.0
        t = np.arange(int(sr * dur)) / sr
        pad = (np.sin(2 * np.pi * 65.41 * t) * 0.30
               + np.sin(2 * np.pi * 98.00 * t) * 0.26
               + np.sin(2 * np.pi * 130.81 * t) * 0.22
               + np.sin(2 * np.pi * 164.81 * t) * 0.14)
        lfo = 0.75 + 0.25 * np.sin(2 * np.pi * 0.08 * t)
        notes = [261.63, 293.66, 329.63, 392.00, 440.00]
        arp = np.zeros_like(t)
        for i, start in enumerate(np.arange(0, dur, 0.5)):
            idx = (t >= start) & (t < start + 0.5)
            arp[idx] += np.sin(2 * np.pi * notes[i % 5] * (t[idx] - start)) * np.exp(-6 * (t[idx] - start)) * 0.08
        music = pad * lfo + arp
        fade = int(sr * 2)
        music[:fade] *= np.linspace(0, 1, fade)
        music[-fade:] *= np.linspace(1, 0, fade)
        music /= np.max(np.abs(music))
        pcm = (music * 0.5 * 32767).astype(np.int16)
        with wave.open(str(assets / "bgm.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        print("BGM 使用合成垫音(建议成片后在剪映换乐库曲子, 或 config.bgm_file 指定音乐)")
    print(f"素材已生成: {assets}")


# ---------------------------------------------------------------- prep
def cmd_prep(work, args):
    cfg = load_cfg(work)
    clean = work / "work" / "src_clean"
    clean.mkdir(parents=True, exist_ok=True)
    for name, sc in cfg["sources"].items():
        src = work / "sources" / name
        band = sc.get("band")
        dst = clean / name
        if not band:
            shutil.copy2(src, dst)
            print(f"{name}: 无需模糊, 已复制")
            continue
        _, w, h = probe_media(src)
        # 烧录字幕是像素级的, 模糊遮盖会像一层膜; 直接裁掉字幕区以下画面,
        # render 端会等比放大铺满画布(损失左右少量边缘, 主体居中不受影响)
        # top(可选): 源片顶部有烧录花字时, 从 top 比例处开始保留(双裁)
        top = sc.get("top") or 0
        y0 = int(h * top) // 2 * 2
        ch = int(h * (band[0] - top)) // 2 * 2
        fc = f"[0:v]crop={w}:{ch}:0:{y0}"
        run([FFMPEG, "-y", "-i", str(src), "-filter_complex", fc,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-c:a", "copy", "-pix_fmt", "yuv420p", str(dst)], f"预处理失败: {name}")
        print(f"{name}: 字幕区 {band} 以下已裁除(上裁 top={top}) -> {dst}")


# ---------------------------------------------------------------- transcribe
def cmd_transcribe(work, args):
    from faster_whisper import WhisperModel
    cfg = load_cfg(work)
    out_dir = work / "work" / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    import os
    want_cpu = os.environ.get("FW_CPU") == "1"
    state = {"model": None, "device": None}

    def get_model(force_cpu=False):
        if state["model"] is None or force_cpu:
            device = "cpu" if (want_cpu or force_cpu) else "cuda"
            try:
                state["model"] = WhisperModel(
                    args.model, device=device,
                    compute_type="int8" if device == "cpu" else "float16")
            except Exception as e:
                print("CUDA 加载失败, 退回 CPU int8:", e)
                state["model"] = WhisperModel(args.model, device="cpu", compute_type="int8")
                device = "cpu"
            state["device"] = device
        return state["model"]

    prompt = f"{cfg['product_name']} {cfg['product_model']} {cfg['brand_cn']} 产品介绍"
    for v in sorted((work / "work" / "src_clean").iterdir()):
        if v.suffix.lower() not in (".mp4", ".mov", ".mkv"):
            continue
        try:
            segments, info = get_model().transcribe(
                str(v), language="zh", vad_filter=True,
                word_timestamps=True, initial_prompt=prompt)
            segments = list(segments)  # 生成器: 此处才真正推理, CUDA 缺dll会在这抛
        except RuntimeError as e:
            if state["device"] == "cpu":
                raise
            print(f"CUDA 推理失败({e}), 退回 CPU int8 重试")
            segments, info = get_model(force_cpu=True).transcribe(
                str(v), language="zh", vad_filter=True,
                word_timestamps=True, initial_prompt=prompt)
            segments = list(segments)
        result = {"source": v.name, "duration": info.duration,
                  "segments": [{"start": round(s.start, 2), "end": round(s.end, 2),
                                "text": s.text.strip()} for s in segments]}
        out = out_dir / f"{v.stem}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{v.name}: {len(result['segments'])} 段 -> {out}")


# ---------------------------------------------------------------- tts
def cmd_tts(work, args):
    import asyncio
    import edge_tts
    cfg = load_cfg(work)
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    tts_dir = work / "work" / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    async def gen():
        durations = {}
        for seg in plan["segments"]:
            order = seg["order"]
            path = tts_dir / f"seg{order:02d}.mp3"
            # 连续数字串按位分开读(36656 -> "3 6 6 5 6"逐位念), 仅影响配音, 字幕仍显示原文
            spoken = re.sub(r"\d{3,}", lambda m: " ".join(m.group()), seg["text"])
            await edge_tts.Communicate(spoken, cfg["voice"], rate=cfg["tts_rate"]).save(str(path))
            dur, _, _ = probe_media(path)
            durations[str(order)] = {"path": str(path), "dur": round(dur, 3)}
            print(f"seg{order:02d}: {dur:.2f}s  {seg['text'][:18]}")
        (tts_dir / "durations.json").write_text(
            json.dumps(durations, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"配音总时长: {sum(v['dur'] for v in durations.values()):.2f}s")

    asyncio.run(gen())


# ---------------------------------------------------------------- timeline
def cmd_timeline(work, args):
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    durs = json.loads((work / "work" / "tts" / "durations.json").read_text(encoding="utf-8"))
    segs, cursor = [], 0.0
    for s in plan["segments"]:
        order = s["order"]
        src_dur = s["end"] - s["start"]
        tts_dur = durs[str(order)]["dur"]
        speed = min(max(src_dur / tts_dur, 0.80), 1.30)
        slot = src_dur / speed
        segs.append({
            "order": order, "video": s["video"],
            "src_path": str(work / plan["sources"][s["video"]]),
            "src_start": round(s["start"], 3), "src_dur": round(src_dur, 3),
            "speed": round(speed, 4),
            "target_start": round(cursor, 3), "target_dur": round(slot, 3),
            "tts_path": durs[str(order)]["path"], "tts_dur": tts_dur,
            "text": s["text"],
        })
        cursor += slot
        print(f"seg{order:02d} {s['video']} speed={speed:.3f} slot={slot:5.2f}s tts={tts_dur:5.2f}s")
    tl = {"total_dur": round(cursor, 3), "canvas": plan.get("canvas", [CANVAS_W, CANVAS_H]), "segments": segs}
    (work / "work" / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"正片总时长: {cursor:.2f}s")


# ---------------------------------------------------------------- render
def wrap_text(text):
    if len(text) <= 16:
        return [text]
    for sep in ("，", "？"):
        if sep in text:
            head, tail = text.split(sep, 1)
            head += sep
            if len(head) >= 6 and len(tail) >= 4:
                return [head, tail]
    mid = len(text) // 2
    return [text[:mid], text[mid:]]


def cmd_render(work, args):
    cfg = load_cfg(work)
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    tl = json.loads((work / "work" / "timeline.json").read_text(encoding="utf-8"))
    segs = tl["segments"]
    body_dur = tl["total_dur"]
    cover_dur, end_dur = cfg["cover_dur"], cfg["end_dur"]
    total = body_dur + end_dur
    assets = work / "work" / "assets"
    subs = work / "work" / "subs"
    subs.mkdir(exist_ok=True)
    bgm_src = resolve_bgm(work, plan)
    bgm_start = float(plan.get("bgm_start", 0))
    bgm_vol = float(plan.get("bgm_volume", 0.75))

    inputs = []
    for s in segs:
        inputs += ["-i", s["src_path"]]
    for s in segs:
        inputs += ["-i", s["tts_path"]]
    inputs += ["-i", str(assets / "veil.png"), "-i", str(assets / "watermark.png"),
               "-i", str(bgm_src),
               "-loop", "1", "-t", str(end_dur), "-i", str(assets / "endcard.png")]
    n = len(segs)
    veil_i, wm_i, bgm_i, end_i = 2 * n, 2 * n + 1, 2 * n + 2, 2 * n + 3

    fc = []
    for i, s in enumerate(segs):
        fc.append(
            f"[{i}:v]trim=start={s['src_start']}:end={s['src_start'] + s['src_dur']},"
            f"setpts=(PTS-STARTPTS)/{s['speed']},fps=30,format=yuv420p,setsar=1,split[bg{i}][fg{i}]")
        fc.append(f"[bg{i}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,boxblur=15:2[bgi{i}]")
        fc.append(f"[fg{i}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[fgi{i}]")
        fc.append(f"[bgi{i}][fgi{i}]overlay=(W-w)/2:(H-h)/2[v{i}]")
    fc.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
    fc.append("[cat]scale=1112:1978,crop=1080:1920,setsar=1[base]")   # 1.03 放大裁边(去重)
    fc.append(f"[{veil_i}:v]format=rgba,colorchannelmixer=aa=0.98[veil]")
    fc.append("[base][veil]overlay=0:0:eval=frame[b1]")
    fc.append(f"[{end_i}:v]fps=30,format=yuv420p,setsar=1[endc]")
    fc.append("[b1][endc]concat=n=2:v=1:a=0[full]")

    # 移动水印: 正片正弦漂移, 结尾固定顶部居中
    wm_x = f"if(gt(t,{body_dur}),(W-w)/2,(W-w)/2*(1+0.62*sin(2*PI*t/11)))"
    wm_y = f"if(gt(t,{body_dur}),180,(H-h)/2*(1+0.62*cos(2*PI*t/17)))"
    fc.append(f"[{wm_i}:v]format=rgba[wm]")
    fc.append(f"[full][wm]overlay=x='{wm_x}':y='{wm_y}':eval=frame[vwm]")

    # 封面标题(前 cover_dur 秒, 粉字白边, 首帧即封面)
    for ci, (text, fs, y) in enumerate(((cfg["product_name"], 100, 700), (cfg["product_model"], 96, 850))):
        p = subs / f"cover_{ci}.txt"
        p.write_text(text, encoding="utf-8")
        fc.append(
            f"[{'vwm' if ci == 0 else 'cov0'}]drawtext=fontfile='{FONT_RENDER}':textfile='{esc(p)}':"
            f"fontsize={fs}:fontcolor=0xFF4D94:borderw=8:bordercolor=white:"
            f"x=(w-text_w)/2:y={y}:enable='lt(t,{cover_dur})'[cov{ci}]")

    # 烧录字幕
    prev, k = "cov1", 0
    for s in segs:
        lines = wrap_text(s["text"])
        fs = 56 if len(lines) == 1 else 52
        y0 = 1512 if len(lines) == 1 else 1476
        t0, t1 = s["target_start"], s["target_start"] + s["target_dur"]
        for li, line in enumerate(lines):
            txt = subs / f"line{k:02d}.txt"
            txt.write_text(line, encoding="utf-8")
            fc.append(
                f"[{prev}]drawtext=fontfile='{FONT_RENDER}':textfile='{esc(txt)}':"
                f"fontsize={fs}:fontcolor=white:borderw=4:bordercolor=black:"
                f"x=(w-text_w)/2:y={y0 + li * 76}:enable='between(t,{t0:.2f},{t1:.2f})'[sub{k}]")
            prev, k = f"sub{k}", k + 1
    fc.append(f"[{prev}]null[vout]")

    for j, s in enumerate(segs):
        fc.append(f"[{n + j}:a]aresample=44100,apad,atrim=0:{s['target_dur']}[a{j}]")
    fc.append("".join(f"[a{j}]" for j in range(n)) + f"concat=n={n}:v=0:a=1,apad,atrim=0:{total}[voice]")
    fc.append(f"[{bgm_i}:a]aresample=44100,atrim=start={bgm_start},asetpts=PTS-STARTPTS,"
              f"aloop=loop=-1:size=44100*60,atrim=0:{total},loudnorm=I=-24:TP=-4:LRA=11,"
              f"volume={bgm_vol}[bgm]")
    # amix 默认 normalize=1 会把人声和 BGM 都减半, 关掉后用 alimiter 防削波
    fc.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.9[aout]")

    fc_path = work / "work" / "filter_complex.txt"
    fc_path.write_text(";\n".join(fc), encoding="utf-8")
    out = work / "work" / "final.mp4"
    run([FFMPEG, "-y", *inputs, "-filter_complex_script", str(fc_path),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-t", str(total), str(out)], "渲染失败")
    print(f"最终成片: {out} ({out.stat().st_size / 1048576:.1f} MB, {total:.2f}s)")


# ---------------------------------------------------------------- draft
def cmd_draft(work, args):
    import pyJianYingDraft as draft
    from pyJianYingDraft import ClipSettings, TextBorder, TextStyle, trange

    def us(sec):
        return int(round(sec * 1_000_000))

    cfg = load_cfg(work)
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    tl = json.loads((work / "work" / "timeline.json").read_text(encoding="utf-8"))
    segs = tl["segments"]
    assets = work / "work" / "assets"
    name = cfg["draft_name"] or f"{cfg['product_model']}混剪"
    end_dur, cover_dur = cfg["end_dur"], cfg["cover_dur"]
    bgm_src = resolve_bgm(work, plan)
    bgm_start = float(plan.get("bgm_start", 0))
    bgm_vol = float(plan.get("bgm_volume", 0.75))

    (work / "drafts").mkdir(parents=True, exist_ok=True)
    folder = draft.DraftFolder(str(work / "drafts"))
    script = folder.create_draft(name, CANVAS_W, CANVAS_H, fps=FPS,
                                 allow_replace=True, maintrack_adsorb=False)
    t_main = script.append_track(draft.TrackSpec(draft.TrackType.video, name="主视频"))
    t_veil = script.append_track(draft.TrackSpec(draft.TrackType.video, name="幕布"))
    t_wm = script.append_track(draft.TrackSpec(draft.TrackType.video, name="水印"))
    t_cover1 = script.append_track(draft.TrackSpec(draft.TrackType.text, name="封面1"))
    t_cover2 = script.append_track(draft.TrackSpec(draft.TrackType.text, name="封面2"))
    t_txt = script.append_track(draft.TrackSpec(draft.TrackType.text, name="字幕"))
    t_voice = script.append_track(draft.TrackSpec(draft.TrackType.audio, name="配音"))
    t_bgm = script.append_track(draft.TrackSpec(draft.TrackType.audio, name="背景音乐"))

    slots, cursor = [], 0
    for s in segs:
        dur = round(us(s["src_dur"]) / s["speed"])   # 与 VideoSegment 内部同算法
        slots.append((s, cursor, dur))
        cursor += dur
    body_us = cursor
    total_us = body_us + us(end_dur)

    for s, start, dur in slots:
        script.add_segment(
            draft.VideoSegment(s["src_path"], trange(start, dur),
                               source_timerange=trange(us(s["src_start"]), us(s["src_dur"])),
                               speed=s["speed"], volume=0.0,
                               clip_settings=ClipSettings(scale_x=1.03, scale_y=1.03)),
            track=t_main)
    # 结尾 logo 页
    script.add_segment(
        draft.VideoSegment(str(assets / "endcard.png"), trange(body_us, us(end_dur)),
                           source_timerange=trange(0, us(end_dur))),
        track=t_main)
    script.add_segment(
        draft.VideoSegment(str(assets / "veil.png"), trange(0, total_us),
                           source_timerange=trange(0, total_us),
                           clip_settings=ClipSettings(alpha=0.98)),
        track=t_veil)

    wm = draft.VideoSegment(str(assets / "watermark.png"), trange(0, total_us),
                            source_timerange=trange(0, total_us))
    t = 0
    while t <= body_us:  # 正片: 正弦漂移 (草稿 y 向上为正, 预览向下, 取负)
        sec = t / 1e6
        wm.add_keyframe(draft.KeyframeProperty.position_x, t, 0.62 * math.sin(2 * math.pi * sec / 11))
        wm.add_keyframe(draft.KeyframeProperty.position_y, t, -0.62 * math.cos(2 * math.pi * sec / 17))
        t += 3_000_000
    for t in (body_us, total_us):  # 结尾: 固定顶部居中
        wm.add_keyframe(draft.KeyframeProperty.position_x, t, 0.0)
        wm.add_keyframe(draft.KeyframeProperty.position_y, t, 0.81)
    script.add_segment(wm, track=t_wm)

    # 封面标题(粉字白边, 居中偏上)
    cov_style = TextStyle(size=15, bold=True, color=(1.0, 0.30, 0.58))
    cov_border = TextBorder(color=(1.0, 1.0, 1.0), width=40, alpha=1.0)
    for text, ty, tr in ((cfg["product_name"], 0.22, t_cover1), (cfg["product_model"], 0.08, t_cover2)):
        script.add_segment(
            draft.TextSegment(text, trange(0, us(cover_dur)), style=cov_style,
                              border=cov_border, clip_settings=ClipSettings(transform_y=ty)),
            track=tr)

    txt_style = TextStyle(size=11, bold=True, color=(1.0, 1.0, 1.0))
    txt_border = TextBorder(color=(0.0, 0.0, 0.0), width=32, alpha=0.9)
    for s, start, dur in slots:
        script.add_segment(
            draft.TextSegment(s["text"], trange(start, dur), style=txt_style,
                              border=txt_border, clip_settings=ClipSettings(transform_y=-0.78)),
            track=t_txt)
        mat = draft.AudioMaterial(s["tts_path"])
        script.add_segment(
            draft.AudioSegment(mat, trange(start, min(us(s["tts_dur"]), dur, mat.duration))),
            track=t_voice)
    script.add_segment(
        draft.AudioSegment(str(bgm_src), trange(0, total_us),
                           source_timerange=trange(us(bgm_start), us(bgm_start) + total_us),
                           volume=round(0.5 * bgm_vol, 3)),
        track=t_bgm)
    script.save()
    out = work / "drafts" / name
    print(f"草稿已生成: {out}")

    if args.install:
        if not JY_DRAFTS.exists():
            raise SystemExit(f"未找到剪映草稿目录: {JY_DRAFTS}")
        dst = JY_DRAFTS / name
        shutil.copytree(out, dst, dirs_exist_ok=True)
        print(f"已拷入剪映草稿目录: {dst} (重启剪映可见)")


# ---------------------------------------------------------------- qc
def cmd_qc(work, args):
    out = work / "work" / "final.mp4"
    dur, w, h = probe_media(out)
    proc = subprocess.run([FFMPEG, "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    mean = re.search(r"mean_volume: ([-\d.]+ dB)", proc.stderr)
    peak = re.search(r"max_volume: ([-\d.]+ dB)", proc.stderr)
    qc_dir = work / "work" / "qc"
    qc_dir.mkdir(exist_ok=True)
    for i, tsec in enumerate((0.5, dur * 0.25, dur * 0.5, dur * 0.75, dur - 0.3)):
        run([FFMPEG, "-y", "-ss", f"{tsec:.1f}", "-i", str(out),
             "-frames:v", "1", "-q:v", "3", str(qc_dir / f"f{i}.jpg")], "QC 抽帧失败")
    print(f"时长 {dur:.2f}s | {w}x{h} | 响度 mean={mean and mean.group(1)} max={peak and peak.group(1)}")
    print(f"抽帧 5 张 -> {qc_dir} (检查: 封面型号/字幕/水印/源片字幕模糊/结尾logo)")


def main():
    ap = argparse.ArgumentParser(description="剪映混剪流水线")
    ap.add_argument("cmd", choices=["probe", "assets", "prep", "transcribe", "tts",
                                    "timeline", "render", "draft", "qc"])
    ap.add_argument("--work", required=True, help="任务目录")
    ap.add_argument("--model", default="medium", help="whisper 模型(transcribe)")
    ap.add_argument("--install", action="store_true", help="draft: 拷入剪映草稿目录")
    args = ap.parse_args()
    work = Path(args.work).resolve()
    {"probe": cmd_probe, "assets": cmd_assets, "prep": cmd_prep,
     "transcribe": cmd_transcribe, "tts": cmd_tts, "timeline": cmd_timeline,
     "render": cmd_render, "draft": cmd_draft, "qc": cmd_qc}[args.cmd](work, args)


if __name__ == "__main__":
    main()
