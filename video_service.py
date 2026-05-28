import os, requests, tempfile
from flask import Flask, request, send_file
from moviepy import (
    VideoFileClip, AudioFileClip, CompositeVideoClip, ColorClip,
    TextClip
)
import moviepy.config as mpconfig

app = Flask(__name__)

# ตั้งค่าให้ MoviePy ใช้ Pillow (ไม่ใช้ ImageMagick)
mpconfig.change_settings({"IMAGEMAGICK_BINARY": None})

# ดาวน์โหลดฟอนต์ไทย (Sarabun) มาไว้ใน /tmp หากยังไม่มี
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sarabun/Sarabun-Regular.ttf"
FONT_PATH = "/tmp/Sarabun-Regular.ttf"

def download_thai_font():
    if not os.path.exists(FONT_PATH):
        r = requests.get(FONT_URL)
        with open(FONT_PATH, 'wb') as f:
            f.write(r.content)

download_thai_font()

@app.route('/assemble', methods=['POST'])
def assemble():
    data = request.json
    audio_url = data['audio_url']
    subtitles = data['subtitles']  # [{"start": 0.5, "end": 2.0, "text": "..."}]
    bg_video_url = data.get('bg_video_url')

    # ดาวน์โหลดไฟล์เสียง
    audio_path = os.path.join(tempfile.gettempdir(), 'audio.mp3')
    with open(audio_path, 'wb') as f:
        f.write(requests.get(audio_url).content)
    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration

    # วิดีโอพื้นหลัง (หรือใช้จอดำ)
    if bg_video_url:
        bg_path = os.path.join(tempfile.gettempdir(), 'bg.mp4')
        with open(bg_path, 'wb') as f:
            f.write(requests.get(bg_video_url).content)
        video_clip = VideoFileClip(bg_path).loop(duration=duration).resized((1080, 1920))
    else:
        video_clip = ColorClip(size=(1080, 1920), color=(0,0,0), duration=duration)

    # สร้าง TextClip ด้วย Pillow (method='label') เพราะ method='caption' ต้องใช้ ImageMagick
    txt_clips = []
    for sub in subtitles:
        txt_clip = TextClip(
            text=sub['text'],
            font=FONT_PATH,      # ใช้ฟอนต์ไทยที่ดาวน์โหลดไว้
            font_size=60,
            color='white',
            stroke_color='black',
            stroke_width=2,
            method='label'        # ใช้ Pillow ไม่ต้องใช้ ImageMagick
        )
        txt_clip = txt_clip.with_position(('center', 'center')) \
                           .with_start(sub['start']) \
                           .with_duration(sub['end'] - sub['start'])
        txt_clips.append(txt_clip)

    # ประกอบคลิป
    final = CompositeVideoClip([video_clip] + txt_clips).with_audio(audio_clip)

    out_path = os.path.join(tempfile.gettempdir(), 'final.mp4')
    final.write_videofile(out_path, codec='libx264', audio_codec='aac', fps=24, preset='ultrafast')

    return send_file(out_path, mimetype='video/mp4', as_attachment=True, download_name='clip.mp4')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)