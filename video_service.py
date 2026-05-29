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
from moviepy.video import fx  # นำเข้าโมดูล fx สำหรับ MoviePy v2.0+

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

# ดาวน์โหลดฟอนต์ตอน start
font_ready = download_thai_font()
if not font_ready:
    logger.warning("Font download failed, will try system font 'DejaVu Sans'")
    FONT_PATH = "DejaVu-Sans"

# ========== API ประกอบวิดีโอ ==========
@app.route('/assemble', methods=['POST'])
def assemble():
    try:
        data = request.json
        audio_url = data.get('audio_url', '').strip()
        subtitles = data.get('subtitles', [])
        bg_video_url = data.get('bg_video_url', '').strip()

        if not audio_url:
            return {"error": "ไม่พบพารามิเตอร์ audio_url ส่งมาจาก n8n"}, 400

        # --- ดาวน์โหลดไฟล์เสียง + ตรวจสอบความถูกต้อง ---
        audio_path = os.path.join(tempfile.gettempdir(), 'audio.mp3')
        try:
            logger.info(f"กำลังดาวน์โหลดไฟล์เสียงจาก: {audio_url}")
            r_audio = requests.get(audio_url, timeout=30)
            r_audio.raise_for_status()
            
            # เช็คว่าสิ่งที่ดาวน์โหลดมา ไม่ใช่หน้าเว็บพัง (HTML) หรือข้อความ Error
            content_peek = r_audio.content[:200]
            if b"<!DOCTYPE" in content_peek or b"<html" in content_peek.lower():
                return {
                    "error": "ไฟล์เสียงพัง! ลิงก์ที่ n8n ส่งมาไม่ใช่ไฟล์เสียง MP3 แต่เป็นหน้าเว็บ HTML (อาจเกิดจากโหนด TTS หรือ Catbox พัง/หมดอายุ)",
                    "debug_url": audio_url
                }, 400
                
            with open(audio_path, 'wb') as f:
                f.write(r_audio.content)
        except Exception as e:
            return {"error": f"ดาวน์โหลดไฟล์เสียงจากต้นทางไม่สำเร็จ ลิงก์อาจผิดพลาด: {str(e)}", "debug_url": audio_url}, 400

        # โหลดไฟล์เสียงเข้า MoviePy
        audio_clip = AudioFileClip(audio_path)
        duration = audio_clip.duration

        # --- วิดีโอพื้นหลัง (ปรับขนาดลงเป็น 720x1280 เพื่อลดการใช้ CPU บน Free Tier) ---
        if bg_video_url:
            bg_path = os.path.join(tempfile.gettempdir(), 'bg.mp4')
            try:
                logger.info(f"กำลังดาวน์โหลดวิดีโอพื้นหลังจาก: {bg_video_url}")
                r_bg = requests.get(bg_video_url, timeout=30)
                r_bg.raise_for_status()
                
                content_peek_bg = r_bg.content[:200]
                if b"<!DOCTYPE" in content_peek_bg or b"<html" in content_peek_bg.lower():
                    return {
                        "error": "ไฟล์วิดีโอพัง! ลิงก์จาก Pexels ส่งกลับมาเป็นหน้าเว็บ HTML ไม่ใช่ไฟล์วิดีโอจริง",
                        "debug_url": bg_video_url
                    }, 400
                    
                with open(bg_path, 'wb') as f:
                    f.write(r_bg.content)
                
                # เปลี่ยนจากขนาด 1080x1920 เป็น 720x1280 เพื่อเซฟแรงเครื่องประมวลผล
                video_clip = VideoFileClip(bg_path).with_effects([fx.Loop(duration=duration)]).resized(width=720, height=1280)
            except Exception as e:
                logger.warning(f"โหลดวิดีโอพื้นหลังไม่สำเร็จ เปลี่ยนไปใช้พื้นหลังดำ: {e}")
                video_clip = ColorClip(size=(720, 1280), color=(0, 0, 0), duration=duration)
        else:
            video_clip = ColorClip(size=(720, 1280), color=(0, 0, 0), duration=duration)

        # --- ซับไตเติล (ปรับขนาดฟอนต์เหลือ 40 ให้พอดีกับสัดส่วนจอ 720p) ---
        txt_clips = []
        for sub in subtitles:
            try:
                txt = TextClip(
                    text=sub['text'],
                    font=FONT_PATH,
                    font_size=40,
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
            threads=1  # บังคับใช้ 1 เทรด ป้องกันระบบ Render ทำการ Throttling บีบความเร็วเราลง
        )

        return send_file(out_path, mimetype='video/mp4', as_attachment=True, download_name='clip.mp4')

    except Exception as e:
        error_msg = f"Error in assembly: {traceback.format_exc()}"
        logger.error(error_msg)
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
