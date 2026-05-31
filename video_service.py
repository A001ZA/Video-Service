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
)
from moviepy.video import fx

# ========== Logging ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========== Font ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "Sarabun-Regular.ttf")
FONT_URL  = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf"

# ========== Video config ==========
VIDEO_W        = 720
VIDEO_H        = 1280
SUBTITLE_Y     = 1000
SUBTITLE_BOX_W = VIDEO_W - 80

# ========== Job store (in-memory) ==========
# jobs[job_id] = {"status": "pending"|"processing"|"done"|"error", "error": str, "path": str}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


# ---------- helpers ----------

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


def make_background(bg_video_url: str, duration: float):
    if bg_video_url:
        bg_path = os.path.join(tempfile.gettempdir(), "bg.mp4")
        if download_file(bg_video_url, bg_path, "วิดีโอพื้นหลัง"):
            try:
                clip = VideoFileClip(bg_path)
                clip = clip.with_effects([fx.Loop(duration=duration)])
                if (clip.w / clip.h) > (VIDEO_W / VIDEO_H):
                    clip = clip.resized(height=VIDEO_H)
                else:
                    clip = clip.resized(width=VIDEO_W)
                clip = clip.cropped(
                    x_center=clip.w / 2, y_center=clip.h / 2,
                    width=VIDEO_W, height=VIDEO_H
                )
                return clip.with_duration(duration)
            except Exception as e:
                logger.warning(f"ประมวลผลพื้นหลังไม่สำเร็จ: {e}")
    return ColorClip(size=(VIDEO_W, VIDEO_H), color=(0, 0, 0), duration=duration)


def make_subtitle_clips(subtitles: list, font_available: bool) -> list:
    clips = []
    font_arg = FONT_PATH if font_available else None
    for i, sub in enumerate(subtitles):
        text = sub.get("text", "").strip()
        start = sub.get("start", 0)
        end   = sub.get("end", start + 1)
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


# ---------- background worker ----------

def render_job(job_id: str, audio_url: str, subtitles: list, bg_video_url: str):
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

        video_clip  = make_background(bg_video_url, duration)
        txt_clips   = make_subtitle_clips(subtitles, font_ok)
        logger.info(f"[{job_id}] ซับไตเติล {len(txt_clips)}/{len(subtitles)} บรรทัด")

        final = CompositeVideoClip([video_clip] + txt_clips).with_audio(audio_clip)

        out_path = os.path.join(tempfile.gettempdir(), f"final_{job_id}.mp4")
        final.write_videofile(
            out_path,
            codec="libx264", audio_codec="aac",
            fps=15, preset="ultrafast", threads=2, logger=None,
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


# ========== Routes ==========

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "font_ready": os.path.exists(FONT_PATH)}), 200


@app.route("/assemble", methods=["POST"])
def assemble():
    """รับงานแล้วตอบ job_id ทันที ไม่รอ render"""
    data = request.json
    if not data:
        return jsonify({"error": "ไม่พบ JSON body"}), 400

    audio_url    = data.get("audio_url", "").strip()
    subtitles    = data.get("subtitles", [])
    bg_video_url = data.get("bg_video_url", "").strip()

    if not audio_url:
        return jsonify({"error": "ไม่พบ audio_url"}), 400

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending", "path": None, "error": None}

    t = threading.Thread(
        target=render_job,
        args=(job_id, audio_url, subtitles, bg_video_url),
        daemon=True,
    )
    t.start()

    logger.info(f"สร้าง job: {job_id}")
    return jsonify({"job_id": job_id}), 202


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    """เช็คสถานะ job — n8n จะ poll endpoint นี้"""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "ไม่พบ job"}), 404
    return jsonify({
        "job_id":  job_id,
        "status":  job["status"],   # pending | processing | done | error
        "error":   job.get("error"),
    }), 200


@app.route("/result/<job_id>", methods=["GET"])
def result(job_id: str):
    """ดาวน์โหลดไฟล์วิดีโอเมื่อ status == done"""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "ไม่พบ job"}), 404
    if job["status"] != "done":
        return jsonify({"error": f"ยังไม่เสร็จ status={job['status']}"}), 425
    return send_file(
        job["path"],
        mimetype="video/mp4",final.write_videofile(
        as_attachment=True,
        download_name="clip.mp4",
    )


# ========== Start ==========
if __name__ == "__main__":
    ensure_font()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
