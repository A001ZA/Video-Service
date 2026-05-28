import os
import logging
import tempfile
import traceback

import requests
from flask import Flask, request, send_file

# ตั้งค่า MoviePy ให้ใช้ Pillow ไม่ต้องใช้ ImageMagick
import moviepy.config as mpconfig
mpconfig.IMAGEMAGICK_BINARY = ""   # ว่างเพื่อบังคับให้ใช้ Pillow

from moviepy import (
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    ColorClip,
    TextClip,
)

# ========== ตั้งค่า logging ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ========== เตรียมฟอนต์ไทย ==========
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/static/Sarabun-Regular.ttf"
FONT_PATH = "/tmp/Sarabun-Regular.ttf"

def download_thai_font():
    if os.path.exists(FONT_PATH):
        logger.info("Font already exists")
        return True
    try:
        logger.info(f"Downloading font from {FONT_URL}")
        r = requests.get(FONT_URL, timeout=10)
        r.raise_for_status()
        with open(FONT_PATH, 'wb') as f:
            f.write(r.content)
        logger.info("Font downloaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to download font: {e}")
        return False

# ดาวน์โหลดฟอนต์ตอน start (ถ้าไม่สำเร็จจะใช้ฟอนต์ fallback)
font_ready = download_thai_font()
if not font_ready:
    logger.warning("Font download failed, will try system font 'DejaVu Sans'")
    FONT_PATH = "DejaVu-Sans"  # fallback สำหรับกรณีไม่มีฟอนต์ไทย (จะได้สี่เหลี่ยม)

# ========== API ประกอบวิดีโอ ==========
@app.route('/assemble', methods=['POST'])
def assemble():
    try:
        data = request.json
        audio_url = data['audio_url']
        subtitles = data['subtitles']
        bg_video_url = data.get('bg_video_url')

        # --- ดาวน์โหลดไฟล์เสียง ---
        audio_path = os.path.join(tempfile.gettempdir(), 'audio.mp3')
        with open(audio_path, 'wb') as f:
            f.write(requests.get(audio_url).content)
        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration

        # --- วิดีโอพื้นหลัง ---
        if bg_video_url:
            bg_path = os.path.join(tempfile.gettempdir(), 'bg.mp4')
            with open(bg_path, 'wb') as f:
                f.write(requests.get(bg_video_url).content)
            video_clip = VideoFileClip(bg_path).loop(duration=duration).resize(width=1080, height=1920)
        else:
            video_clip = ColorClip(size=(1080, 1920), color=(0, 0, 0), duration=duration)

        # --- ซับไตเติล ---
        txt_clips = []
        for sub in subtitles:
            try:
                # ใช้ method='label' เพื่อทำงานผ่าน Pillow
                txt = TextClip(
                    text=sub['text'],
                    font=FONT_PATH,
                    font_size=60,
                    color='white',
                    stroke_color='black',
                    stroke_width=2,
                    method='label'
                )
                txt = txt.with_position(('center', 'center')) \
                         .with_start(sub['start']) \
                         .with_duration(sub['end'] - sub['start'])
                txt_clips.append(txt)
            except Exception as e:
                logger.warning(f"Could not create subtitle for {sub['text']}: {e}")

        # --- ประกอบคลิป ---
        final = CompositeVideoClip([video_clip] + txt_clips).with_audio(audio_clip)

        out_path = os.path.join(tempfile.gettempdir(), 'final.mp4')
        final.write_videofile(
            out_path,
            codec='libx264',
            audio_codec='aac',
            fps=24,
            preset='ultrafast',
            threads=2
        )

        return send_file(out_path, mimetype='video/mp4', as_attachment=True, download_name='clip.mp4')

    except Exception as e:
        logger.error(f"Error in assembly: {traceback.format_exc()}")
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
