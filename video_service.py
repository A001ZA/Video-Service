import os
import logging
import tempfile
import traceback
import urllib.request

import requests
from flask import Flask, request, send_file, jsonify

# ตั้งค่า MoviePy ให้ใช้ Pillow แทน ImageMagick
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

# ========== ตั้งค่า logging ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========== ตั้งค่าพาธฟอนต์ ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "Sarabun-Regular.ttf")
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf"

# ขนาดวิดีโอ (9:16 สำหรับ Reels/Shorts)
VIDEO_W = 720
VIDEO_H = 1280

# ตำแหน่งซับไตเติล (จากด้านบน)
SUBTITLE_Y = 1000

# ขนาดกล่องซับไตเติล (ความกว้างหน้าจอ - margin)
SUBTITLE_BOX_W = VIDEO_W - 80  # เว้น margin ซ้าย-ขวา 40px


def ensure_font():
    """ดาวน์โหลดฟอนต์ภาษาไทยอัตโนมัติถ้ายังไม่มีในเครื่อง"""
    if os.path.exists(FONT_PATH):
        logger.info(f"พบฟอนต์แล้วที่: {FONT_PATH}")
        return True
    try:
        logger.info(f"ไม่พบฟอนต์ กำลังดาวน์โหลดจาก Google Fonts...")
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        logger.info(f"ดาวน์โหลดฟอนต์สำเร็จ: {FONT_PATH}")
        return True
    except Exception as e:
        logger.error(f"ดาวน์โหลดฟอนต์ไม่สำเร็จ: {e}")
        return False


def download_file(url: str, dest_path: str, label: str = "ไฟล์") -> bool:
    """ดาวน์โหลดไฟล์จาก URL ไปยัง dest_path พร้อม error handling"""
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"ดาวน์โหลด{label}สำเร็จ: {dest_path}")
        return True
    except Exception as e:
        logger.warning(f"ดาวน์โหลด{label}ไม่สำเร็จ ({url}): {e}")
        return False


def make_background(bg_video_url: str, duration: float):
    """
    สร้างคลิปพื้นหลัง:
    - ถ้ามี bg_video_url → โหลดวิดีโอแล้ว loop/crop ให้ได้ขนาด 720x1280
    - ถ้าไม่มี หรือโหลดไม่ได้ → ใช้พื้นหลังดำ
    """
    if bg_video_url:
        bg_path = os.path.join(tempfile.gettempdir(), 'bg.mp4')
        if download_file(bg_video_url, bg_path, "วิดีโอพื้นหลัง"):
            try:
                clip = VideoFileClip(bg_path)

                # Loop ให้ยาวพอก่อน แล้วค่อย trim
                clip = clip.with_effects([fx.Loop(duration=duration)])

                # Resize ให้ครอบคลุม 720x1280 (scale จาก dimension ที่เล็กกว่า)
                clip_ratio = clip.w / clip.h
                target_ratio = VIDEO_W / VIDEO_H
                if clip_ratio > target_ratio:
                    # วิดีโอกว้างกว่า → scale ตามความสูง
                    clip = clip.resized(height=VIDEO_H)
                else:
                    # วิดีโอสูงกว่า → scale ตามความกว้าง
                    clip = clip.resized(width=VIDEO_W)

                # Crop ตรงกลาง
                x_center = clip.w / 2
                y_center = clip.h / 2
                clip = clip.cropped(
                    x_center=x_center,
                    y_center=y_center,
                    width=VIDEO_W,
                    height=VIDEO_H
                )

                return clip.with_duration(duration)
            except Exception as e:
                logger.warning(f"ประมวลผลวิดีโอพื้นหลังไม่สำเร็จ ใช้สีดำแทน: {e}")

    logger.info("ใช้พื้นหลังสีดำ")
    return ColorClip(size=(VIDEO_W, VIDEO_H), color=(0, 0, 0), duration=duration)


def make_subtitle_clips(subtitles: list, font_available: bool) -> list:
    """
    สร้าง TextClip สำหรับซับไตเติลแต่ละบรรทัด
    - ใช้ method='caption' พร้อม size เพื่อ wrap ข้อความอัตโนมัติ
    - Fallback เป็น method='label' ถ้า caption ไม่ได้
    """
    txt_clips = []
    font_arg = FONT_PATH if font_available else None

    for i, sub in enumerate(subtitles):
        text = sub.get('text', '').strip()
        start = sub.get('start', 0)
        end = sub.get('end', start + 1)
        duration = max(end - start, 0.1)

        if not text:
            continue

        clip = None

        # วิธีที่ 1: caption (รองรับ word wrap อัตโนมัติ)
        try:
            clip = TextClip(
                text=text,
                font=font_arg,
                font_size=40,
                color='white',
                stroke_color='black',
                stroke_width=2,
                method='caption',
                size=(SUBTITLE_BOX_W, None),  # ✅ ต้องระบุ size เมื่อใช้ method='caption'
                text_align='center',
            )
        except Exception as e1:
            logger.warning(f"[sub {i}] caption ไม่ได้ ({e1}) → ลอง label แทน")

            # วิธีที่ 2: label (ไม่ wrap แต่ไม่ต้องการ size)
            try:
                clip = TextClip(
                    text=text,
                    font=font_arg,
                    font_size=36,
                    color='white',
                    stroke_color='black',
                    stroke_width=2,
                    method='label',
                )
            except Exception as e2:
                logger.warning(f"[sub {i}] label ไม่ได้เช่นกัน ({e2}) → ข้ามซับนี้")
                continue

        clip = (
            clip
            .with_position(('center', SUBTITLE_Y))
            .with_start(start)
            .with_duration(duration)
        )
        txt_clips.append(clip)

    return txt_clips


# ========== Health Check (ใช้ ping ปลุก Render) ==========
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "font_ready": os.path.exists(FONT_PATH)}), 200


# ========== API หลัก: ประกอบวิดีโอ ==========
@app.route('/assemble', methods=['POST'])
def assemble():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "ไม่พบ JSON body"}), 400

        audio_url   = data.get('audio_url', '').strip()
        subtitles   = data.get('subtitles', [])
        bg_video_url = data.get('bg_video_url', '').strip()

        if not audio_url:
            return jsonify({"error": "ไม่พบพารามิเตอร์ audio_url"}), 400

        # ตรวจสอบฟอนต์ (ดาวน์โหลดถ้าไม่มี)
        font_available = ensure_font()
        if not font_available:
            logger.warning("ดำเนินการต่อโดยไม่มีฟอนต์ภาษาไทย — ซับไตเติลอาจแสดงไม่ถูกต้อง")

        # ดาวน์โหลดเสียง
        audio_path = os.path.join(tempfile.gettempdir(), 'audio.mp3')
        if not download_file(audio_url, audio_path, "เสียง"):
            return jsonify({"error": "ดาวน์โหลดไฟล์เสียงไม่สำเร็จ"}), 502

        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration
        logger.info(f"ความยาวเสียง: {duration:.2f} วินาที")

        # สร้างพื้นหลัง
        video_clip = make_background(bg_video_url, duration)

        # สร้างซับไตเติล
        txt_clips = make_subtitle_clips(subtitles, font_available)
        logger.info(f"สร้างซับไตเติลได้ {len(txt_clips)}/{len(subtitles)} บรรทัด")

        # ประกอบคลิปทั้งหมด
        all_clips = [video_clip] + txt_clips
        final = CompositeVideoClip(all_clips).with_audio(audio_clip)

        # Render วิดีโอ
        out_path = os.path.join(tempfile.gettempdir(), 'final.mp4')
        final.write_videofile(
            out_path,
            codec='libx264',
            audio_codec='aac',
            fps=15,
            preset='ultrafast',
            threads=2,
            logger=None,  # ปิด progress bar ใน log (ลด noise)
        )

        logger.info(f"Render สำเร็จ: {out_path}")
        return send_file(
            out_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name='clip.mp4'
        )

    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาด:\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


# ========== เริ่มต้นแอป ==========
if __name__ == '__main__':
    # ดาวน์โหลดฟอนต์ตอน startup เลย (ไม่ต้องรอ request แรก)
    ensure_font()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
