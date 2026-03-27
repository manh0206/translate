from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
import json
import tempfile
import subprocess
import traceback

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

from faster_whisper import WhisperModel
import requests

# =========================================
# CONFIG
# =========================================
from collections import defaultdict

VALID_KEYS = []
DEAD_KEYS = set()
KEY_USAGE = defaultdict(int)

MODEL_NAME = "tiny"

FFMPEG_PATH = "ffmpeg"  # Đường dẫn đến ffmpeg

API_FILE = "apis.txt"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
PORT = int(os.environ.get("PORT", 5000))

# =========================================
# INIT
# =========================================
app = Flask(__name__)
CORS(app)

print("=== Loading whisper model ===")
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
print("=== Whisper loaded ===")

# Load API keys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_FILE = os.path.join(BASE_DIR, "apis.txt")

with open(API_FILE, "r", encoding="utf-8") as f:
    API_KEYS = [x.strip() for x in f if x.strip()]


# =========================================
# MIME DETECTION HELPERS
# =========================================
AUDIO_TYPES = [
    "audio/wav", "audio/mpeg", "audio/mp3",
    "audio/ogg", "audio/webm", "audio/mp4",
    "audio/x-wav"
]

IMAGE_TYPES = [
    "image/jpeg", "image/png",
    "image/webp", "image/bmp", "image/gif"
]

def is_image_bytes(data: bytes):
    return data.startswith(b'\xff\xd8') or \
           data.startswith(b'\x89PNG') or \
           data.startswith(b'GIF') or \
           data.startswith(b'RIFF')


# =========================================
# AUDIO → WAV
# =========================================
def convert_to_wav(audio_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".raw") as inp:
        inp.write(audio_bytes)
        inp.flush()
        in_path = inp.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as out:
        out_path = out.name

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", in_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        out_path
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        print("FFMPEG ERROR:", proc.stderr.decode("utf-8"))
        raise Exception("FFmpeg failed to convert audio.")

    data = open(out_path, "rb").read()
    os.remove(in_path)
    os.remove(out_path)
    return data


# =========================================
# STT (IMAGE / AUDIO HANDLING)
# =========================================
from io import BytesIO

@app.route("/stt", methods=["POST"])
def stt():
    try:
        data = request.get_json()
        audio_url = data.get("url")

        if not audio_url:
            return jsonify({"error": "No URL provided"}), 400

        r = requests.get(audio_url, timeout=20)

        print("URL:", audio_url)
        print("Status:", r.status_code)
        print("Content-Type:", r.headers.get("Content-Type"))
        print("Size:", len(r.content))

        if r.status_code != 200:
            return jsonify({"error": "Cannot download file"}), 400

        content_type = r.headers.get("Content-Type", "").lower()

        # =========================================
        # IMAGE DETECTION
        # =========================================
        if (
            content_type in IMAGE_TYPES
            or content_type.startswith("image")
            or is_image_bytes(r.content)
        ):
            print("IMAGE DETECTED → RETURN URL")
            return jsonify({
                "type": "image",
                "url": audio_url
            })

        # =========================================
        # AUDIO CHECK
        # =========================================
        if not any(t in content_type for t in AUDIO_TYPES):
            print("NOT AUDIO → SKIP")
            return jsonify({
                "type": "unknown",
                "text": ""
            })

        if len(r.content) < 5000:
            print("FILE TOO SMALL → SKIP")
            return jsonify({
                "type": "audio",
                "text": ""
            })

        # =========================================
        # TRANSCRIBE
        # =========================================
        audio_buffer = BytesIO(r.content)

        try:
            segments, _ = model.transcribe(
                audio_buffer,
                beam_size=1,
                vad_filter=True
            )
        except Exception as e:
            print("TRANSCRIBE CRASH:", e)
            return jsonify({
                "type": "audio",
                "text": ""
            })

        text = "".join(seg.text for seg in segments).strip()

        return jsonify({
            "type": "audio",
            "text": text
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
# =========================================
# GEMINI
# =========================================
def test_gemini_key(key):
    try:
        url = f"{GEMINI_URL}?key={key}"
        payload = {
            "contents": [{"parts": [{"text": "ping"}]}]
        }
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except:
        return False
print("=== Checking Gemini API keys ===")
for key in API_KEYS:
    if test_gemini_key(key):
        VALID_KEYS.append(key)
    else:
        print(f"[DEAD INIT] {key[:8]}...")

API_KEYS = VALID_KEYS
print(f"=== Gemini keys OK: {len(API_KEYS)} ===")
def call_gemini_single(key, prompt):
    if key in DEAD_KEYS:
        return None

    try:
        url = f"{GEMINI_URL}?key={key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }

        r = requests.post(url, json=payload, timeout=500)

        # ❌ HẾT QUOTA → loại key
        if r.status_code == 429:
            print(f"[QUOTA] {key[:8]}...")
            DEAD_KEYS.add(key)
            return None

        # ⏳ MODEL OVERLOAD → KHÔNG loại key
        if r.status_code == 503:
            print(f"[OVERLOAD] {key[:8]}...")
            return None

        if r.status_code != 200:
            print(f"[ERROR] {key[:8]}:", r.text)
            return None

        data = r.json()
        if not data.get("candidates"):
            return None

        KEY_USAGE[key] += 1
        return data["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as e:
        print(f"[EXCEPTION] {key[:8]}:", e)
        return None

executor = ThreadPoolExecutor(max_workers=10)

import random
from concurrent.futures import as_completed

def ask_gemini(prompt, max_retries=2):
    keys = API_KEYS.copy()
    random.shuffle(keys)

    for attempt in range(max_retries):
        futures = {
            executor.submit(call_gemini_single, key, prompt): key
            for key in keys
        }

        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()

                if result and result.strip():
                    return result

            except Exception:
                continue

        # retry vòng mới nếu tất cả fail
        random.shuffle(keys)

    return "⚠️ Tất cả API đều lỗi sau nhiều lần thử."
def attach_index(questions):
    return list(enumerate(questions))
def split_into_4(items):
    k, m = divmod(len(items), 4)
    return [
        items[i*k + min(i, m):(i+1)*k + min(i+1, m)]
        for i in range(4)
    ]
def build_prompt(batch):
    """
    batch: [(index, question), ...]
    """
    lines = []
    for idx, question in batch:
        lines.append(f"{idx}. {question}")
    guide = """HƯỚNG DẪN TRẢ LỜI (BẮT BUỘC):
    BẮT BUỘC FORMAT:
- Chỉ trả về đáp án theo format chính xác, KHÔNG thêm lời giải hay chú thích.
- Dạng trắc nghiệm: trả về ABCD
- Dạng True/False: trả về True hoặc False vd : True
- Dạng điền chữ (một từ) và thường là câu hỏi có chứa dấu *: trả về ví dụ: w#hell ( nếu đã có o ) nhớ là viết đủ hết các trống * và không được thừa hay thiếu! ( Vd như Wh*** bạn nghĩ là What hợp lí nhưng What* chứng tỏ rằng còn xót 1 * , cần xem lại) và nhớ rằng hãy xem 1 cách thận trọng , dùng từ cần thêm ko phải đầy đủ!
+ Nếu một số ký tự đã có sẵn (được hiển thị trên đề), chỉ đưa ra các ký tự *còn thiếu* theo thứ tự, vẫn dùng dấu gạch ngang và câu hỏi mà viết vd như he*** thì mỗi * là một kí tự còn thiếu , nhớ là viết đủ hết các trống * và không được thừa hay thiếu!
- Dạng nhiều từ cần điền: tách các từ bằng dấu -, trong mỗi từ dùng dấu gạch ngang cho ký tự, ví dụ: hello-orld
- Dạng ghép hết các từ cho sẵn: trả về dạng ví dụ: Hello world-m name is-ane.
- Cuối cùng: Nhớ rằng hãy áp dụng kĩ tất cả luật trên để tránh hiểu nhầm !
-- KẾT THÚC HƯỚNG DẪN --
"""

    prompt = guide + "\n\n"
    prompt += "Trả lời từng câu theo đúng số thứ tự.\n"
    prompt += "Mỗi dòng một câu trả lời, giữ nguyên số.\n\n"
    prompt += "Chỉ trả về đáp án theo format chính xác, KHÔNG thêm lời giải hay chú thích hay nhắc lại câu hỏi. Nếu không thì formart có thể gãy !\n"
    prompt += "\n".join(lines)

    return prompt
def parse_answer(batch, result_text):
    """
    Trả về dict: {index: answer}
    """
    result = {}

    lines = result_text.strip().split("\n")

    for (idx, _), line in zip(batch, lines):
        result[idx] = line.strip()

    return result
from concurrent.futures import wait, ALL_COMPLETED
def ask_gemini_large(questions):

    indexed = attach_index(questions)

    # Nếu dưới 200 thì xử lý bình thường
    # Nếu dưới 200 thì vẫn parse như bình thường
    if len(indexed) < 200:
     prompt = build_prompt(indexed)
     result_text = ask_gemini(prompt)
     parsed = parse_answer(indexed, result_text)
     ordered = [parsed[i] for i in sorted(parsed.keys())]
     return ordered

    # Nếu >=200 thì chia 4
    parts = split_into_4(indexed)

    def process_batch(batch):
        prompt = build_prompt(batch)
        result_text = ask_gemini(prompt)
        return parse_answer(batch, result_text)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(process_batch, part) for part in parts]

    # CHỜ TẤT CẢ hoàn thành
    done, not_done = wait(futures)

    if not_done:
        print("WARNING: Có batch chưa hoàn tất")

    results = []
    for f in futures:
        try:
            results.append(f.result())
        except Exception as e:
            print("Batch error:", e)
            results.append({})

    # Ghép lại
    merged = {}
    for partial in results:
        merged.update(partial)

    ordered = [merged[i] for i in sorted(merged.keys())]
    return ordered

@app.route("/gemini", methods=["POST"])
def gemini():
    data = request.json

    # Nếu gửi dạng nhiều câu
    if "questions" in data:
        questions = data["questions"]
        answer = ask_gemini_large(questions)
        return jsonify({"answer": answer})

    # Nếu vẫn gửi 1 prompt bình thường
    prompt = data.get("prompt", "")
    answer = ask_gemini(prompt)
    return jsonify({"answer": answer})
@app.route("/gemini/status", methods=["GET"])
def gemini_status():
    return jsonify({
        "total_keys": len(API_KEYS),
        "dead_keys": len(DEAD_KEYS),
        "usage": dict(KEY_USAGE)
    })


# =========================================
# RUN FLASK
# =========================================
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 5000))
    print(f"Server Flask chạy tại http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)
