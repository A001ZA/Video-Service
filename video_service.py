import os
import uuid
import threading
import logging
import tempfile
import traceback
import urllib.request

import requests
from flask import Flask, request, send_file, jsonify

import moviepy.config as mpconfig
mpconfig.IMAGEMAGICK_BINARY = ""

from moviepy import (
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    ColorClip,
    TextClip,
    concatenate_videoclips,
)
from moviepy.video import fx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "Sarabun-Regular.ttf")
FONT_URL  = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf"

VIDEO_W        = 720
VIDEO_H        = 1280
SUBTITLE_Y     = 1000
SUBTITLE_BOX_W = VIDEO_W - 80

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def ensure_font() -> bool:
    if os.path.exists(FONT_PATH):
        logger.info(f"พบฟอนต์: {FONT_PATH}")
        return True
    try:
        logger.info("ดาวน์โหลดฟอนต์จาก Google Fonts...")
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        logger.info(f"ดาวน์โหลดฟอนต์สำเร็จ: {FONT_PATH}")
        return True
    except Exception as e:
        logger.error(f"ดาวน์โหลดฟอนต์ไม่สำเร็จ: {e}")
        return False


def download_file(url: str, dest: str, label: str = "ไฟล์") -> bool:
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        logger.info(f"ดาวน์โหลด{label}สำเร็จ: {dest}")
        return True
    except Exception as e:
        logger.warning(f"ดาวน์โหลด{label}ไม่สำเร็จ: {e}")
        return False


def resize_and_crop(clip):
    if (clip.w / clip.h) > (VIDEO_W / VIDEO_H):
        clip = clip.resized(height=VIDEO_H)
    else:
        clip = clip.resized(width=VIDEO_W)
    clip = clip.cropped(
        x_center=clip.w / 2, y_center=clip.h / 2,
        width=VIDEO_W, height=VIDEO_H
    )
    return clip


def make_background(bg_video_urls: list, duration: float):
    loaded_clips = []
    for i, url in enumerate(bg_video_urls):
        if not url:
            continue
        dest = os.path.join(tempfile.gettempdir(), f"bg_{i}.mp4")
        if download_file(url, dest, f"วิดีโอพื้นหลัง[{i}]"):
            try:
                clip = VideoFileClip(dest)
                clip = resize_and_crop(clip)
                loaded_clips.append(clip)
                logger.info(f"โหลดคลิป[{i}] สำเร็จ ความยาว {clip.duration:.1f}s")
            except Exception as e:
                logger.warning(f"ประมวลผลคลิป[{i}] ไม่สำเร็จ: {e}")

    if not loaded_clips:
        logger.warning("ไม่มีคลิปพื้นหลัง → ใช้สีดำ")
        return ColorClip(size=(VIDEO_W, VIDEO_H), color=(0, 0, 0), duration=duration)

    total = sum(c.duration for c in loaded_clips)
    if total < duration:
        repeated = []
        accumulated = 0.0
        while accumulated < duration:
            for c in loaded_clips:
                repeated.append(c)
                accumulated += c.duration
                if accumulated >= duration:
                    break
        loaded_clips = repeated

    bg = concatenate_videoclips(loaded_clips, method="compose")
    bg = bg.subclipped(0, duration)
    return bg


def calc_timestamps(subtitles: list, duration: float) -> list:
    """
    คำนวณ timestamp จากสัดส่วนจำนวนคำของแต่ละประโยค
    เทียบกับ duration จริงของเสียง — ซับจะตรงเสียงเสมอ
    """
    if not subtitles:
        return subtitles

    # นับจำนวนคำแต่ละบรรทัด (ภาษาไทยไม่มี space แบ่งคำ ใช้ความยาวตัวอักษรแทน)
    lengths = []
    for sub in subtitles:
        text = sub.get("text", "")
        # ถ้ามี space (มีคำภาษาอังกฤษปน) นับ word ปกติ ถ้าไม่มีใช้ความยาวตัวอักษร
        words = text.split()
        lengths.append(max(len(words), len(text) // 4, 1))

    total = sum(lengths)
    elapsed = 0.0
    for i, sub in enumerate(subtitles):
        ratio        = lengths[i] / total
        sub_duration = round(ratio * duration, 2)
        sub["start"] = round(elapsed, 2)
        sub["end"]   = round(elapsed + sub_duration, 2)
        elapsed     += sub_duration

    logger.info(f"คำนวณ timestamp {len(subtitles)} บรรทัด รวม {elapsed:.2f}s / {duration:.2f}s")
    return subtitles


def make_subtitle_clips(subtitles: list, font_available: bool) -> list:
    clips = []
    font_arg = FONT_PATH if font_available else None
    for i, sub in enumerate(subtitles):
        text  = sub.get("text", "").strip()
        start = float(sub.get("start", 0))
        end   = float(sub.get("end", start + 1))
        if not text:
            continue
        clip = None
        try:
            clip = TextClip(
                text=text, font=font_arg, font_size=40,
                color="white", stroke_color="black", stroke_width=2,
                method="caption", size=(SUBTITLE_BOX_W, None), text_align="center",
            )
        except Exception as e1:
            logger.warning(f"[sub {i}] caption ล้มเหลว ({e1}) → ลอง label")
            try:
                clip = TextClip(
                    text=text, font=font_arg, font_size=36,
                    color="white", stroke_color="black", stroke_width=2,
                    method="label",
                )
            except Exception as e2:
                logger.warning(f"[sub {i}] label ล้มเหลวด้วย ({e2}) → ข้าม")
                continue
        clip = (
            clip
            .with_position(("center", SUBTITLE_Y))
            .with_start(start)
            .with_duration(max(end - start, 0.1))
        )
        clips.append(clip)
    return clips


def render_job(job_id: str, audio_url: str, subtitles: list, bg_video_urls: list):
    with jobs_lock:
        jobs[job_id]["status"] = "processing"

    try:
        font_ok = ensure_font()

        audio_path = os.path.join(tempfile.gettempdir(), f"audio_{job_id}.mp3")
        if not download_file(audio_url, audio_path, "เสียง"):
            raise RuntimeError("ดาวน์โหลดเสียงไม่สำเร็จ")

        audio_clip = AudioFileClip(audio_path)
        duration   = audio_clip.duration
        logger.info(f"[{job_id}] ความยาวเสียง: {duration:.2f}s")

        video_clip = make_background(bg_video_urls, duration)

        # คำนวณ timestamp จากสัดส่วนจำนวนคำ ตรงกับ duration เสียงจริงเสมอ
        subtitles = calc_timestamps(subtitles, duration)

        txt_clips = make_subtitle_clips(subtitles, font_ok)
        logger.info(f"[{job_id}] ซับไตเติล {len(txt_clips)}/{len(subtitles)} บรรทัด")

        final = CompositeVideoClip([video_clip] + txt_clips).with_audio(audio_clip)

        out_path = os.path.join(tempfile.gettempdir(), f"final_{job_id}.mp4")
        final.write_videofile(
            out_path,
            codec="libx264",
            audio_codec="aac",
            fps=15,
            preset="ultrafast",
            threads=2,
            logger=None,
            ffmpeg_params=["-b:v", "800k", "-b:a", "96k", "-maxrate", "800k", "-bufsize", "1600k"]
        )

        logger.info(f"[{job_id}] Render สำเร็จ")
        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["path"]   = out_path

    except Exception:
        err = traceback.format_exc()
        logger.error(f"[{job_id}] Error:\n{err}")
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"]  = err


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "font_ready": os.path.exists(FONT_PATH)}), 200


@app.route("/assemble", methods=["POST"])
def assemble():
    data = request.json
    if not data:
        return jsonify({"error": "ไม่พบ JSON body"}), 400

    audio_url     = data.get("audio_url", "").strip()
    subtitles     = data.get("subtitles", [])
    bg_video_urls = data.get("bg_video_urls", [])
    if not bg_video_urls:
        single = data.get("bg_video_url", "").strip()
        if single:
            bg_video_urls = [single]

    if not audio_url:
        return jsonify({"error": "ไม่พบ audio_url"}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending", "path": None, "error": None}

    t = threading.Thread(
        target=render_job,
        args=(job_id, audio_url, subtitles, bg_video_urls),
        daemon=True,
    )
    t.start()

    logger.info(f"สร้าง job: {job_id}")
    return jsonify({"job_id": job_id}), 202


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "ไม่พบ job"}), 404
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "error":  job.get("error"),
    }), 200


@app.route("/result/<job_id>", methods=["GET"])
def result(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "ไม่พบ job"}), 404
    if job["status"] != "done":
        return jsonify({"error": f"ยังไม่เสร็จ status={job['status']}"}), 425
    return send_file(
        job["path"],
        mimetype="video/mp4",
        as_attachment=True,
        download_name="clip.mp4",
    )


if __name__ == "__main__":
    ensure_font()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
