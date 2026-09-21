# -*- coding: utf-8 -*-
"""
抖音素材收集器（jianying-mixcut 配套）
用法：
  python collector.py author --url <作者主页链接> --model <型号> [--max 50]
  python collector.py login                                  # 扫码登录，存 auth.json（search 前置）
  python collector.py search --keyword <关键词> --model <型号> [--max 30] [--min-likes 30]
  python collector.py download --model <型号>                 # 下载素材表中"待下载"的记录
  python collector.py qc --model <型号>                       # 已下载记录抽帧质检，状态->待审核
素材表：<素材库>/素材表.csv（utf-8-sig，Excel 可直接打开）
状态机：候选 -> 待下载 -> 已下载 -> 待审核 -> 可用/弃用 -> 已用
"""
import argparse, csv, json, re, subprocess, sys, time, urllib.request
from pathlib import Path

LIB_DEFAULT = Path(__file__).resolve().parent / "素材库"
PROFILE_DIR = Path(__file__).resolve().parent / "browser_profile"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

FIELDS = ["id", "型号", "平台", "作者", "标题", "点赞", "链接", "文件名",
          "时长s", "分辨率", "人脸", "水印贴纸", "字幕带", "状态"]


def lib_path(args):
    return Path(args.lib) if getattr(args, "lib", None) else LIB_DEFAULT


def table_file(lib):
    return lib / "素材表.csv"


def load_rows(lib):
    f = table_file(lib)
    if not f.exists():
        return []
    with open(f, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def save_rows(lib, rows):
    lib.mkdir(parents=True, exist_ok=True)
    with open(table_file(lib), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r.pop(None, None)
            w.writerow(r)


def video_id(url):
    m = re.search(r"/video/(\d+)", url)
    return m.group(1) if m else None


def new_row(vid, model, author_name="", title="", likes=-1, link=""):
    return {"id": vid, "型号": model, "平台": "抖音", "作者": author_name,
            "标题": title, "点赞": likes, "链接": link or f"https://www.douyin.com/video/{vid}",
            "文件名": f"dy_{vid}.mp4", "时长s": "", "分辨率": "", "人脸": "",
            "水印贴纸": "", "字幕带": "", "状态": "候选"}


# ---------- 浏览器抓取 ----------

JS_CARDS = r"""
() => {
  const out = new Map();
  // 作者页作品列表：有 author-card-user-video-like 点赞标的卡片才是作者本人的作品
  for (const sp of document.querySelectorAll('span.author-card-user-video-like')) {
    const li = sp.closest('li');
    if (!li) continue;
    const a = li.querySelector('a[href*="/video/"]');
    if (!a) continue;
    const m = a.href.match(/\/video\/(\d+)/);
    if (!m) continue;
    let likes = -1;
    const lm = sp.textContent.trim().match(/(\d+(?:\.\d+)?)(万?)/);
    if (lm) likes = Math.round(parseFloat(lm[1]) * (lm[2] ? 10000 : 1));
    const img = li.querySelector('img[alt]');
    let title = ((img && img.alt) || a.innerText || '').replace(/\s+/g, ' ').trim();
    title = title.replace(/^[^：:]{1,30}[：:]/, '').trim();  // 去「作者名：」前缀
    out.set(m[1], {id: m[1], title: title.slice(0, 80), likes});
  }
  if (out.size) return [...out.values()];
  // 回退（搜索页等）：通用卡片，点赞记 -1 待人工判断
  for (const a of document.querySelectorAll('a[href*="/video/"]')) {
    const m = a.href.match(/\/video\/(\d+)/);
    if (!m) continue;
    const card = a.closest('li') || a.parentElement;
    const txt = ((card && card.innerText) || '').replace(/\s+/g, ' ').trim();
    out.set(m[1], {id: m[1], title: txt.slice(0, 80), likes: -1});
  }
  return [...out.values()];
}
"""


def scrape_list(page, url, max_items, timeout_s=120):
    """滚动页面收集视频卡片，返回 [{id,title,likes}]"""
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    if "登录后即可搜索" in page.inner_text("body"):
        raise SystemExit("搜索页需要登录态，请先运行: python collector.py login")
    items, stagnant = {}, 0
    t0 = time.time()
    while len(items) < max_items and stagnant < 5 and time.time() - t0 < timeout_s:
        for it in page.evaluate(JS_CARDS):
            items[it["id"]] = it
        before = len(items)
        page.mouse.wheel(0, 3000)
        time.sleep(1.8)
        for it in page.evaluate(JS_CARDS):
            items[it["id"]] = it
        stagnant = stagnant + 1 if len(items) == before else 0
    return list(items.values())[:max_items]


def add_candidates(rows, items, model, author_name, min_likes):
    have = {r["id"] for r in rows}
    added = 0
    for it in items:
        if it["id"] in have:
            continue
        if min_likes > 0 and 0 <= it["likes"] < min_likes:
            continue
        rows.append(new_row(it["id"], model, author_name, it["title"], it["likes"]))
        added += 1
    return added


def open_browser(p):
    """持久化浏览器 profile：cookies 累积可过风控，login 一次后 search 免登"""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ctx = p.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=False, user_agent=UA,
        viewport={"width": 1280, "height": 900})
    return ctx, ctx.pages[0] if ctx.pages else ctx.new_page()


def cmd_author(args):
    from playwright.sync_api import sync_playwright
    lib, rows = lib_path(args), load_rows(lib_path(args))
    with sync_playwright() as p:
        ctx, page = open_browser(p)
        author_name = ""
        try:
            items = scrape_list(page, args.url, args.max)
            m = re.search(r"^(.*?)的抖音", page.title() or "")
            author_name = m.group(1) if m else ""
        finally:
            ctx.close()
    added = add_candidates(rows, items, args.model, author_name, args.min_likes)
    save_rows(lib, rows)
    print(f"作者「{author_name}」抓到 {len(items)} 条，新增候选 {added} 条（门槛 点赞>={args.min_likes}）")


def cmd_login(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx, page = open_browser(p)
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
        print("浏览器窗口已打开抖音，请用抖音 App 扫码登录（检测到登录态会自动关闭窗口）...")
        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            if any(c["name"] == "sessionid" for c in ctx.cookies()):
                ok = True
                break
            time.sleep(3)
        time.sleep(2)  # 等 cookie 落盘
        ctx.close()
    if ok:
        print(f"登录成功，登录态已保存到浏览器 profile: {PROFILE_DIR}")
    else:
        raise SystemExit("5 分钟内未检测到登录态，请重跑 login")


def cmd_search(args):
    from playwright.sync_api import sync_playwright
    lib, rows = lib_path(args), load_rows(lib_path(args))
    from urllib.parse import quote
    url = f"https://www.douyin.com/search/{quote(args.keyword)}?type=video"
    with sync_playwright() as p:
        ctx, page = open_browser(p)
        try:
            items = scrape_list(page, url, args.max)
        finally:
            ctx.close()
    added = add_candidates(rows, items, args.model, "", args.min_likes)
    save_rows(lib, rows)
    print(f"关键词「{args.keyword}」抓到 {len(items)} 条，新增候选 {added} 条（门槛 点赞>={args.min_likes}）")


# ---------- 下载 ----------

def get_play_url(page, link):
    page.goto(link, wait_until="domcontentloaded")
    time.sleep(3)
    return page.evaluate(
        "() => { const v = document.querySelector('video'); if (!v) return ''; "
        "v.muted = true; v.play().catch(()=>{}); return v.src || v.currentSrc; }")


def cmd_download(args):
    from playwright.sync_api import sync_playwright
    lib = lib_path(args)
    rows = load_rows(lib)
    pending = [r for r in rows
               if r["状态"] in ("待下载",) and (not args.model or r["型号"] == args.model)]
    if not pending:
        print("没有待下载记录（先把候选在素材表里改为 待下载）")
        return
    out_dir = lib / (args.model or "未分类")
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    with sync_playwright() as p:
        ctx, page = open_browser(p)
        try:
            for r in pending:
                dst = out_dir / r["文件名"]
                if dst.exists() and dst.stat().st_size > 100_000:
                    r["状态"] = "已下载"
                    ok += 1
                    continue
                try:
                    src = get_play_url(page, r["链接"])
                    if not src:
                        raise RuntimeError("未取到播放地址")
                    req = urllib.request.Request(
                        src, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"})
                    with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as fh:
                        while True:
                            chunk = resp.read(1 << 20)
                            if not chunk:
                                break
                            fh.write(chunk)
                    if dst.stat().st_size < 300_000:
                        dst.unlink()
                        raise RuntimeError("文件过小，疑似风控预览流（重试或先跑 login 过验证）")
                    r["状态"] = "已下载"
                    ok += 1
                    print(f"[ok] {r['文件名']} {dst.stat().st_size//1024}KB")
                except Exception as e:
                    fail += 1
                    print(f"[fail] {r['链接']}: {e}")
                save_rows(lib, rows)
        finally:
            ctx.close()
    print(f"下载完成: 成功 {ok} 失败 {fail}")


# ---------- 质检 ----------

def ffprobe(path):
    from imageio_ffmpeg import get_ffmpeg_exe
    proc = subprocess.run([get_ffmpeg_exe(), "-i", str(path)], capture_output=True)
    err = proc.stderr.decode("utf-8", "ignore")
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    res = re.search(r"(\d{3,4})x(\d{3,4})", err)
    d = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 0
    return round(d, 1), (f"{res.group(1)}x{res.group(2)}" if res else "")


def face_detect(img_path):
    """YuNet 初筛，有检出返回 True（需人工复核，可能有误报）"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    model = Path(__file__).parent / "assets" / "face_detection_yunet_2023mar.onnx"
    if not model.exists():
        return None
    img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    h, w = img.shape[:2]
    det = cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.6)
    _, faces = det.detect(img)
    return faces is not None and len(faces) > 0


def cmd_qc(args):
    from imageio_ffmpeg import get_ffmpeg_exe
    lib = lib_path(args)
    rows = load_rows(lib)
    targets = [r for r in rows
               if r["状态"] == "已下载" and (not args.model or r["型号"] == args.model)]
    if not targets:
        print("没有已下载待质检的记录")
        return
    ff = get_ffmpeg_exe()
    for r in targets:
        src = lib / r["型号"] / r["文件名"]
        if not src.exists():
            continue
        qc_dir = lib / r["型号"] / "qc" / src.stem
        qc_dir.mkdir(parents=True, exist_ok=True)
        d, res = ffprobe(src)
        r["时长s"], r["分辨率"] = d, res
        ts = [0.4, max(d / 2, 0.5), max(d - 1.0, 0.6)]   # 按时间戳取帧，不依赖帧率
        sel = "+".join(f"between(t\\,{max(t - 0.2, 0):.2f}\\,{t + 0.2:.2f})" for t in ts)
        proc = subprocess.run([ff, "-y", "-i", str(src), "-vf",
                               f"select='{sel}',scale=540:-1",
                               "-vsync", "vfr", "-frames:v", "3", str(qc_dir / "f%d.jpg")],
                              capture_output=True)
        jpgs = sorted(qc_dir.glob("f*.jpg"))
        if len(jpgs) < 3:
            tail = proc.stderr.decode("utf-8", "ignore")[-300:]
            print(f"[qc][warn] 只抽到 {len(jpgs)}/3 帧 rc={proc.returncode} {src.name}: {tail}")
        faces = [face_detect(f) for f in jpgs]
        r["人脸"] = ("疑似" if any(faces) else "无") if any(f is not None for f in faces) else ""
        r["状态"] = "待审核"
        print(f"[qc] {r['文件名']} {d}s {res} 人脸:{r['人脸']} -> {qc_dir}")
    save_rows(lib, rows)
    print("质检完成：请人工查看各 qc 目录抽帧，回填 水印贴纸/字幕带/状态(可用|弃用)")


def main():
    ap = argparse.ArgumentParser(description="抖音素材收集器")
    ap.add_argument("--lib", default=str(LIB_DEFAULT), help="素材库目录")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("author", "search"):
        sp = sub.add_parser(name)
        sp.add_argument("--model", required=True, help="产品型号，如 36656")
        sp.add_argument("--max", type=int, default=50)
        sp.add_argument("--min-likes", type=int, default=30)
    sub.choices["author"].add_argument("--url", required=True)
    sub.choices["search"].add_argument("--keyword", required=True)
    sub.add_parser("login")
    for name in ("download", "qc"):
        sp = sub.add_parser(name)
        sp.add_argument("--model", default="")
    args = ap.parse_args()
    {"author": cmd_author, "search": cmd_search, "login": cmd_login,
     "download": cmd_download, "qc": cmd_qc}[args.cmd](args)


if __name__ == "__main__":
    main()
