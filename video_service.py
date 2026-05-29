import os
import logging
import tempfile
import traceback

import requests
from flask import Flask, request, send_file

# ตั้งค่า MoviePy ให้ใช้ Pillow
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

# กำหนดพาธฟอนต์ที่อัปโหลดไว้ในโฟลเดอร์เดียวกับไฟล์นี้
FONT_FILENAME = "Sarabun-Regular.ttf"

# ========== API ประกอบวิดีโอ ==========
@app.route('/assemble', methods=['POST'])
def assemble():
    try:
        data = request.json
        audio_url = data.get('audio_url', '').strip()
        subtitles = data.get('subtitles', [])
        bg_video_url = data.get('bg_video_url', '').strip()

        if not audio_url:
            return {"error": "ไม่พบพารามิเตอร์ audio_url"}, 400

        # --- ดาวน์โหลดไฟล์เสียง ---
        audio_path = os.path.join(tempfile.gettempdir(), 'audio.mp3')
        r_audio = requests.get(audio_url, timeout=30)
        r_audio.raise_for_status()
        with open(audio_path, 'wb') as f:
            f.write(r_audio.content)

        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration

        # --- วิดีโอพื้นหลัง ---
        if bg_video_url:
            bg_path = os.path.join(tempfile.gettempdir(), 'bg.mp4')
            try:
                r_bg = requests.get(bg_video_url, timeout=30)
                r_bg.raise_for_status()
                with open(bg_path, 'wb') as f:
                    f.write(r_bg.content)
                video_clip = VideoFileClip(bg_path).with_effects([fx.Loop(duration=duration)]).resized(width=720, height=1280)
            except Exception as e:
                logger.warning(f"โหลดวิดีโอไม่ได้ ใช้พื้นหลังดำแทน: {e}")
                video_clip = ColorClip(size=(720, 1280), color=(0, 0, 0), duration=duration)
        else:
            video_clip = ColorClip(size=(720, 1280), color=(0, 0, 0), duration=duration)

        # --- ซับไตเติล ---
        txt_clips = []
        for sub in subtitles:
            try:
                # ตรวจสอบว่ามีไฟล์ฟอนต์จริงไหม
                font_to_use = FONT_FILENAME if os.path.exists(FONT_FILENAME) else None
                
                txt = TextClip(
                    text=sub['text'],
                    font=font_to_use, # ใช้ไฟล์ที่อัปโหลด
                    font_size=40,
                    color='white',
                    stroke_color='black',
                    stroke_width=2,
                    method='caption'
                )
                txt = txt.with_position(('center', 1000)) \
                         .with_start(sub['start']) \
                         .with_duration(sub['end'] - sub['start'])
                txt_clips.append(txt)
            except Exception as e:
                logger.warning(f"สร้างซับไตเติลไม่ได้: {e}")

        # --- ประกอบคลิป ---
        final = CompositeVideoClip([video_clip] + txt_clips).with_audio(audio_clip)

        out_path = os.path.join(tempfile.gettempdir(), 'final.mp4')
        final.write_videofile(
            out_path,
            codec='libx264',
            audio_codec='aac',
            fps=15,
            preset='ultrafast',
            threads=1
        )

        return send_file(out_path, mimetype='video/mp4', as_attachment=True, download_name='clip.mp4')

    except Exception as e:
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
