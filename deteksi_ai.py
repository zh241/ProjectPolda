import cv2
import time
import datetime
import os
import threading
import queue
import json
import mysql.connector
import numpy as np
import requests
import psutil  # <-- TAMBAHAN UNTUK HEARTBEAT CPU & RAM
from collections import deque, defaultdict
from ultralytics import YOLO
from flask import Flask, Response

from dotenv import load_dotenv

# Load variabel rahasia dari file .env
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
WEBSITE_DIR = os.path.join(BASE_DIR, "website")
load_dotenv(os.path.join(WEBSITE_DIR, ".env"), override=True)

os.environ["OPENCV_LOG_LEVEL"]              = "SILENT"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

# ==========================================
# RAILWAY API — BACA CONFIG DARI SERVER
# ==========================================
RAILWAY_URL = "https://digital-gate-poldakalsel.up.railway.app"

def baca_config_dari_railway():
    """Fetch config terbaru dari Railway. Fallback ke file lokal kalau gagal."""
    try:
        respon = requests.get(f"{RAILWAY_URL}/api/get_config", timeout=3)
        if respon.status_code == 200:
            return respon.json()
    except:
        pass
    # Fallback ke config.json lokal
    try:
        with open(os.path.join(WEBSITE_DIR, "config.json"), 'r') as f:
            return json.load(f)
    except:
        return {"rtsp_cam1": "0", "ngrok_url": ""}

# ==========================================
# THREAD PENGIRIM HEARTBEAT (CPU & RAM)
# ==========================================
def pengirim_heartbeat():
    """Mengirim status CPU & RAM ke Web Server diam-diam"""
    while True:
        try:
            cpu_sekarang = psutil.cpu_percent(interval=1)
            ram_sekarang = psutil.virtual_memory().percent
            requests.post(
                f"{RAILWAY_URL}/api/heartbeat", 
                json={"cpu": cpu_sekarang, "ram": ram_sekarang}, 
                timeout=3
            )
        except Exception:
            pass
        time.sleep(3) # Kirim detak jantung setiap 3 detik

# Mulai thread heartbeat
threading.Thread(target=pengirim_heartbeat, daemon=True).start()

# ==========================================
# DATABASE & WORKER THREAD
# ==========================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")
DB_NAME = os.getenv("DB_NAME", "db_polda_kalsel")

print(f"🔌 Konek ke DB: {DB_HOST}:{DB_PORT} / {DB_NAME}")

try:
    db = mysql.connector.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cursor = db.cursor()
    print("✅ Database Terhubung!")
except Exception as e:
    print(f"❌ DB gagal: {e}")
    db = cursor = None

def insert_db(waktu, jenis, foto):
    global db, cursor
    if not db: return False
    
    # ========================================================
    # 🌟 LOGIKA KATEGORI DINAMIS (TANPA PLAT NOMOR)
    # ========================================================
    if jenis == "Kendaraan Polisi": # <--- NAMA CLASS DISESUAIKAN
        kategori_db = "Dinas"
    else:
        kategori_db = "Umum"

    try:
        if not db.is_connected():
            db.reconnect(attempts=5, delay=1)
            cursor = db.cursor()
            
        # SQL diupdate: Dihapus plat_nomor, ditambah kategori dinamis
        cursor.execute(
            "INSERT INTO log_kendaraan "
            "(waktu, jenis_kendaraan, kategori, foto_bukti) "
            "VALUES (%s,%s,%s,%s)",
            (waktu.strftime("%Y-%m-%d %H:%M:%S"), jenis, kategori_db, foto)
        )
        db.commit()
        return True
    except Exception as e:
        print(f"⚠️ DB: {e}")
        try:
            db.reconnect(attempts=3, delay=2)
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO log_kendaraan "
                "(waktu, jenis_kendaraan, kategori, foto_bukti) "
                "VALUES (%s,%s,%s,%s)",
                (waktu.strftime("%Y-%m-%d %H:%M:%S"), jenis, kategori_db, foto)
            )
            db.commit()
            return True
        except Exception as e2:
            print(f"❌ DB retry gagal: {e2}")
            return False

q_tugas = queue.Queue()

def pekerja_background():
    while True:
        tugas = q_tugas.get()
        if tugas is None: break
        foto, nama_foto, foto_dir, waktu_dt, label, id_str = tugas
        try:
            # 1. Simpan foto ke hardisk laptop lokal (backup)
            path_lokal = os.path.join(foto_dir, nama_foto)
            cv2.imwrite(path_lokal, foto)

            # 2. Masukkan data teks ke Database Railway
            ok_db = insert_db(waktu_dt, label, nama_foto)

            # 3. Kirim file foto fisik ke server Railway
            try:
                with open(path_lokal, 'rb') as f:
                    respon = requests.post(
                        f"{RAILWAY_URL}/api/upload_foto",
                        files={'foto': f},
                        data={'nama_file': nama_foto},
                        timeout=5
                    )
                status_kirim = "🚀 Terkirim ke Cloud" if respon.status_code == 200 else f"⚠️ Gagal Kirim: {respon.status_code}"
            except Exception as e_kirim:
                status_kirim = f"⚠️ Timeout/Error: {e_kirim}"

            print(f"{'✅' if ok_db else '📸'} [{waktu_dt.strftime('%H:%M:%S')}] {label} {id_str} -> {nama_foto} | {status_kirim}")

        except Exception as e:
            print(f"❌ Gagal simpan/kirim: {e}")

        q_tugas.task_done()

# Start background worker
threading.Thread(target=pekerja_background, daemon=True).start()

# ==========================================
# CONFIG & ZONA BLOKIR
# ==========================================
FOTO_DIR = os.path.join(WEBSITE_DIR, "static", "foto_kendaraan")
os.makedirs(FOTO_DIR, exist_ok=True)

WARNA = defaultdict(lambda: (200, 200, 200))
WARNA.update({
    "Mobil":  (255, 100,   0),
    "Motor":  (  0, 220,   0),
    "Bus":    (  0,  50, 220),
    "Truk":   (  0, 200, 255),
    "Kendaraan Polisi": (255,  50,  50) # <--- NAMA CLASS DISESUAIKAN
})

ROI_X1, ROI_Y1 = 160,  60
ROI_X2, ROI_Y2 = 640, 360
GARIS_PERSEN   = 0.75
BUFFER_PX      = 30

ZONA_BLOKIR_KIRI = np.array([
    [0, 0], [150, 0], [40, 360], [0, 360]
], np.int32)

ZONA_BLOKIR_KANAN = np.array([
    [480, 360], [550, 140], [640, 160], [640, 360]
], np.int32)

def adalah_motor(x1, y1, x2, y2):
    cx, cy = (x1+x2)/2, (y1+y2)/2
    if not (ROI_X1 <= cx <= ROI_X2 and ROI_Y1 <= cy <= ROI_Y2): return False
    lebar, tinggi = x2-x1, y2-y1
    area = lebar * tinggi
    if area < 80 or area > 9000: return False
    if tinggi / max(lebar, 1) > 3.0: return False
    return True

# ==========================================
# PELACAK UNIVERSAL HYBRID
# ==========================================
class PelacakObjek:
    def __init__(self, max_dist=150, max_age=25):
        self.tracks, self.next_id = {}, 1
        self.max_dist, self.max_age = max_dist, max_age

    def update(self, detections):
        for tr in self.tracks.values(): tr["updated"] = False
        hasil = []
        for (cx, cy, x1, y1, x2, y2, label) in detections:
            best_id, best_score = None, 99999
            for tid, tr in self.tracks.items():
                if tr["updated"]: continue
                is_kend_besar_lama = "motor" not in tr["label"].lower()
                is_kend_besar_baru = "motor" not in label.lower()
                label_cocok = (tr["label"] == label) or (is_kend_besar_lama and is_kend_besar_baru)
                if not label_cocok: continue
                xA, yA = max(x1, tr["x1"]), max(y1, tr["y1"])
                xB, yB = min(x2, tr["x2"]), min(y2, tr["y2"])
                inter  = max(0, xB-xA) * max(0, yB-yA)
                area1  = (x2-x1) * (y2-y1)
                area2  = (tr["x2"]-tr["x1"]) * (tr["y2"]-tr["y1"])
                iou    = inter / float(area1 + area2 - inter + 1e-5)
                dist   = abs(tr["cx"]-cx) + abs(tr["cy"]-cy)
                if dist < self.max_dist or iou > 0.25:
                    if (cy - tr["cy"] < -40) and iou < 0.2: continue
                    score = dist - (iou * 200)
                    if score < best_score:
                        best_score = score
                        best_id = tid
            if best_id is not None:
                self.tracks[best_id].update({
                    "cx":cx,"cy":cy,"x1":x1,"y1":y1,"x2":x2,"y2":y2,
                    "label":label,"age":0,"updated":True
                })
                hasil.append((best_id, cx, cy, x1, y1, x2, y2, label))
            else:
                self.tracks[self.next_id] = {
                    "label":label,"cx":cx,"cy":cy,"x1":x1,"y1":y1,"x2":x2,"y2":y2,
                    "age":0,"updated":True
                }
                hasil.append((self.next_id, cx, cy, x1, y1, x2, y2, label))
                self.next_id += 1
        hapus = []
        for tid, tr in self.tracks.items():
            if not tr["updated"]:
                tr["age"] += 1
                if tr["age"] > self.max_age: hapus.append(tid)
        for tid in hapus: del self.tracks[tid]
        return hasil

# ==========================================
# KENDARAAN REGISTRY
# ==========================================
class KendaraanRegistry:
    def __init__(self):
        self.data = {}

    def update(self, id_str, label, y_bottom, x1, y1, x2, y2, now, garis_y):
        if id_str not in self.data:
            self.data[id_str] = {
                "label": label, "y_now": float(y_bottom),
                "y_awal": None, "y_awal_terkunci": False,
                "velocity": 0.0, "t_update": now, "t_advance": now,
                "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                "tercatat": False, "cooldown_t": 0.0,
            }
            return
        d = self.data[id_str]
        dt = max(now - d["t_update"], 0.001)
        dy = y_bottom - d["y_now"]
        if dt < 2.0:
            d["velocity"] = 0.7 * d["velocity"] + 0.3 * (dy / dt)
        d["label"] = label
        d["y_now"], d["t_update"], d["t_advance"] = float(y_bottom), now, now
        d["x1"], d["y1"], d["x2"], d["y2"] = x1, y1, x2, y2
        zona_kunci = garis_y - 80
        if not d["y_awal_terkunci"]:
            if y_bottom < zona_kunci:
                d["y_awal"] = float(y_bottom)
            else:
                d["y_awal"]          = float(y_bottom)
                d["y_awal_terkunci"] = True

    def advance(self, now):
        for d in self.data.values():
            dt = now - d.get("t_advance", now)
            if 0 < dt < 0.3 and abs(d["velocity"]) > 0:
                if now - d["t_update"] > 0.02:
                    d["y_now"] = max(0, min(d["y_now"] + (d["velocity"] * dt), 400))
            d["t_advance"] = now

    def cek_dan_catat(self, id_str, garis_y, buffer, now, frame_hd, ratio_x, ratio_y):
        if id_str not in self.data: return False
        d = self.data[id_str]
        if d["tercatat"]: return False
        if not d["y_awal_terkunci"] or d["y_awal"] is None: return False
        batas_atas = garis_y - buffer
        if d["y_awal"] >= batas_atas:
            if d["y_now"] < garis_y: return False
        else:
            if not (d["y_awal"] < batas_atas and d["y_now"] >= batas_atas): return False
        d["tercatat"]   = True
        d["cooldown_t"] = now
        label    = d["label"]
        waktu_dt = datetime.datetime.now()
        nama_foto = f"{label.replace(' ','_')}_{id_str.replace('_','')}_{waktu_dt.strftime('%Y%m%d_%H%M%S')}.jpg"
        hd_x1, hd_y1 = int(d["x1"]*ratio_x), int(d["y1"]*ratio_y)
        hd_x2, hd_y2 = int(d["x2"]*ratio_x), int(d["y2"]*ratio_y)
        foto  = frame_hd.copy()
        color = WARNA[label]
        cv2.rectangle(foto, (hd_x1,hd_y1), (hd_x2,hd_y2), color, 4)
        cv2.putText(foto, f"{label} {waktu_dt.strftime('%H:%M:%S')}",
                    (hd_x1, hd_y1-10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, color, 3)
        q_tugas.put((foto, nama_foto, FOTO_DIR, waktu_dt, label, id_str))
        return True

    def bersihkan(self, now, max_usia=15):
        hapus = [k for k, d in self.data.items() if now - d["t_update"] > max_usia]
        for k in hapus: del self.data[k]

# ==========================================
# INFERENCE THREAD
# ==========================================
class InferenceThread:
    def __init__(self, model):
        self.model           = model
        self.frame_input     = None
        self.frame_hd_input  = None
        self.frame_hd_output = None
        self.hasil_output    = []
        self.frame_id        = 0
        self.last_id         = -1
        self.ada_hasil_baru  = False
        self.lock_input      = threading.Lock()
        self.lock_output     = threading.Lock()
        self.running         = True
        threading.Thread(target=self._run, daemon=True).start()

    def kirim_frame(self, frame, frame_hd):
        with self.lock_input:
            self.frame_input    = frame.copy()
            self.frame_hd_input = frame_hd
            self.frame_id      += 1

    def ambil_hasil(self):
        with self.lock_output:
            if self.ada_hasil_baru:
                self.ada_hasil_baru = False
                return True, list(self.hasil_output), self.frame_hd_output
            return False, [], None

    def _run(self):
        while self.running:
            with self.lock_input:
                frame    = self.frame_input
                frame_hd = self.frame_hd_input
                frame_id = self.frame_id
            if frame is None or frame_id == self.last_id:
                time.sleep(0.005)
                continue
            self.last_id = frame_id
            try:
                hasil = self.model.predict(frame, imgsz=320, conf=0.25, verbose=False)
                kandidat_awal = []
                if hasil[0].boxes is not None and len(hasil[0].boxes) > 0:
                    for box in hasil[0].boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cx, cy = (x1+x2)//2, (y1+y2)//2
                        if cv2.pointPolygonTest(ZONA_BLOKIR_KIRI,  (cx,cy), False) >= 0: continue
                        if cv2.pointPolygonTest(ZONA_BLOKIR_KANAN, (cx,cy), False) >= 0: continue
                        cls        = int(box.cls[0])
                        conf       = float(box.conf[0])
                        nama_label = self.model.names[cls]
                        if "motor" in nama_label.lower():
                            if adalah_motor(x1, y1, x2, y2):
                                kandidat_awal.append({'cx':cx,'cy':cy,'x1':x1,'y1':y1,'x2':x2,'y2':y2,'label':nama_label,'conf':conf})
                        else:
                            kandidat_awal.append({'cx':cx,'cy':cy,'x1':x1,'y1':y1,'x2':x2,'y2':y2,'label':nama_label,'conf':conf})

                posisi_motor = [(d['cx'],d['cy'],d['y2']) for d in kandidat_awal if "motor" in d['label'].lower()]
                kandidat_setelah_cross_nms = []
                for d in kandidat_awal:
                    if "motor" not in d['label'].lower():
                        tumpang = any(abs(d['cx']-mx)<80 and abs(d['cy']-my)<80 and abs(d['y2']-my2)<60 for mx,my,my2 in posisi_motor)
                        if tumpang: continue
                    kandidat_setelah_cross_nms.append(d)

                deteksi_final = []
                for k in kandidat_setelah_cross_nms:
                    is_double = False
                    for d_fin in deteksi_final:
                        is_k_besar    = "motor" not in k['label'].lower()
                        is_dfin_besar = "motor" not in d_fin['label'].lower()
                        if is_k_besar and is_dfin_besar:
                            if abs(k['y2']-d_fin['y2']) < 70:
                                xA = max(k['x1'],d_fin['x1']); yA = max(k['y1'],d_fin['y1'])
                                xB = min(k['x2'],d_fin['x2']); yB = min(k['y2'],d_fin['y2'])
                                interArea = max(0,xB-xA)*max(0,yB-yA)
                                iom = interArea / float(min((k['x2']-k['x1'])*(k['y2']-k['y1']),(d_fin['x2']-d_fin['x1'])*(d_fin['y2']-d_fin['y1']))+1e-5)
                                if iom > 0.50:
                                    is_double = True
                                    if k['conf'] > d_fin['conf']: d_fin.update(k)
                                    break
                        elif "motor" in k['label'].lower() and "motor" in d_fin['label'].lower():
                            if abs(k['cx']-d_fin['cx'])<60 and abs(k['cy']-d_fin['cy'])<60:
                                is_double = True
                                if k['conf'] > d_fin['conf']: d_fin.update(k)
                                break
                    if not is_double: deteksi_final.append(k)

                deteksi = [(d['cx'],d['cy'],d['x1'],d['y1'],d['x2'],d['y2'],d['label']) for d in deteksi_final]
                with self.lock_output:
                    self.hasil_output    = deteksi
                    self.frame_hd_output = frame_hd
                    self.ada_hasil_baru  = True
            except Exception:
                time.sleep(0.01)

    def stop(self):
        self.running = False

# ==========================================
# FLASK WEB STREAMING
# ==========================================
app_flask = Flask(__name__)
frame_stream_terbaru = None
lock_stream = threading.Lock()

def gen_frames():
    global frame_stream_terbaru
    while True:
        frame_copy = None
        with lock_stream:
            if frame_stream_terbaru is not None:
                frame_copy = frame_stream_terbaru.copy()
        if frame_copy is None:
            time.sleep(0.01)
            continue
        ret, buffer = cv2.imencode('.jpg', frame_copy)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app_flask.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

print("🌐 Memulai Server Web Stream di port 5050...")
threading.Thread(target=lambda: app_flask.run(host='0.0.0.0', port=5050, debug=False, use_reloader=False), daemon=True).start()

# ==========================================
# INIT MODEL & KAMERA
# ==========================================
model         = YOLO("best.pt")
inference     = InferenceThread(model)
registry      = KendaraanRegistry()
pelacak_objek = PelacakObjek()

stat = defaultdict(int)
fps_list       = deque(maxlen=30)
prev_t         = time.time()
boxes_display  = []
last_bersih    = time.time()
frame_hd_cache = None

def connect_camera():
    rtsp_target = 0
    try:
        # Fetch config dari Railway (bukan baca file lokal)
        cfg = baca_config_dari_railway()
        url = cfg.get('rtsp_cam1', '0')
        rtsp_target = int(url) if str(url).isdigit() else url
    except Exception as e:
        print(f"⚠️ Gagal baca config Railway, pakai Webcam (0). Error: {e}")

    print(f"📡 Menghubungkan ke Kamera: {rtsp_target}")
    if str(rtsp_target) == '0':
        cap = cv2.VideoCapture(0)
    else:
        cap = cv2.VideoCapture(rtsp_target, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap

cap = connect_camera()
print("🚀 V8.16 TIME MACHINE (STABIL TANPA OCR + HEARTBEAT CPU) AKTIF!")

# ==========================================
# MAIN LOOP
# ==========================================
waktu_cek_config = time.time()
rtsp_saat_ini = None
try:
    cfg_awal = baca_config_dari_railway()
    rtsp_saat_ini = cfg_awal.get('rtsp_cam1', '0')
except:
    pass

while True:
    try: ret, frame_hd = cap.read()
    except Exception: ret = False

    now = time.time()

    # Cek config dari Railway setiap 30 detik
    if now - waktu_cek_config > 30:
        waktu_cek_config = now
        try:
            cfg_baru  = baca_config_dari_railway()
            rtsp_baru = cfg_baru.get('rtsp_cam1', '0')
            if str(rtsp_baru) != str(rtsp_saat_ini):
                print(f"🔄 PERINTAH WEB DITERIMA! Pindah ke {rtsp_baru}")
                rtsp_saat_ini = rtsp_baru
                cap.release()
                time.sleep(1)
                cap = connect_camera()
                continue
        except:
            pass

    if not ret or frame_hd is None:
        print("⚠️ Reconnecting...")
        try: cap.release()
        except: pass
        time.sleep(2)
        cap = connect_camera()
        continue

    h_hd, w_hd       = frame_hd.shape[:2]
    ratio_x, ratio_y = w_hd/640, h_hd/360

    fps_list.append(1.0 / max(now - prev_t, 0.001))
    prev_t  = now
    fps_avg = int(sum(fps_list) / len(fps_list))

    frame   = cv2.resize(frame_hd, (640, 360))
    h, w    = frame.shape[:2]
    garis_y = int(h * GARIS_PERSEN)

    inference.kirim_frame(frame, frame_hd)
    ada_baru, deteksi_terbaru, frame_hd_sinkron = inference.ambil_hasil()

    if ada_baru:
        frame_hd_cache = frame_hd_sinkron
        objek_terlacak = pelacak_objek.update(deteksi_terbaru)
        boxes_display  = []
        for (tid, cx, cy, x1, y1, x2, y2, label) in objek_terlacak:
            id_str = f"OBJ_{tid}"
            registry.update(id_str, label, y2, x1, y1, x2, y2, now, garis_y)
            boxes_display.append((x1,y1,x2,y2,label,WARNA[label]))

    registry.advance(now)
    baru_tercatat = False
    if frame_hd_cache is not None:
        for id_str in list(registry.data.keys()):
            if registry.cek_dan_catat(id_str, garis_y, BUFFER_PX, now, frame_hd_cache, ratio_x, ratio_y):
                stat[registry.data[id_str]["label"]] += 1
                baru_tercatat = True

    for (x1,y1,x2,y2,label,color) in boxes_display:
        cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
        cv2.putText(frame,label,(x1,y1-5),cv2.FONT_HERSHEY_SIMPLEX,0.5,color,2)

    if baru_tercatat:
        cv2.rectangle(frame,(0,0),(w,h),(0,220,255),4)

    cv2.polylines(frame,[ZONA_BLOKIR_KIRI], isClosed=True,color=(0,255,0),thickness=2)
    cv2.putText(frame,"BLOKIR",(20,40),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),1)
    cv2.polylines(frame,[ZONA_BLOKIR_KANAN],isClosed=True,color=(0,255,0),thickness=2)
    cv2.putText(frame,"POS",(560,160),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),1)

    cv2.line(frame,(0,garis_y-BUFFER_PX),(w,garis_y-BUFFER_PX),(0,180,180),1)
    cv2.line(frame,(0,garis_y),(w,garis_y),(0,220,255),2)
    cv2.line(frame,(0,garis_y+BUFFER_PX),(w,garis_y+BUFFER_PX),(0,180,180),1)
    cv2.putText(frame,"SENSOR",(5,garis_y-BUFFER_PX-4),cv2.FONT_HERSHEY_SIMPLEX,0.38,(0,220,255),1)

    cv2.rectangle(frame,(5,5),(135,36),(0,0,0),-1)
    cv2.putText(frame,f"FPS:{fps_avg}",(10,28),cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,255,100),2)

    px, py = w-180, 5
    jumlah_baris = max(len(stat), 1)
    cv2.rectangle(frame,(px,py),(w-2,py+40+(jumlah_baris*18)),(0,0,0),-1)
    cv2.putText(frame,"TOTAL MASUK",(px+5,py+18),cv2.FONT_HERSHEY_SIMPLEX,0.42,(180,180,180),1)
    yo = py+36
    for lbl, jumlah in stat.items():
        cv2.putText(frame,f"{lbl[:10]:<10}: {jumlah:>3}",(px+5,yo),cv2.FONT_HERSHEY_SIMPLEX,0.42,WARNA[lbl],1)
        yo += 18
    cv2.putText(frame,f"Total      : {sum(stat.values()):>3}",(px+5,yo+2),cv2.FONT_HERSHEY_SIMPLEX,0.44,(255,255,255),1)

    with lock_stream:
        frame_stream_terbaru = frame.copy()

    cv2.imshow("MONITOR CCTV POLDA", frame)

    if now - last_bersih > 30:
        registry.bersihkan(now)
        last_bersih = now

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'): break
    elif key == ord('r'):
        stat.clear()
        registry.data.clear()
        pelacak_objek.tracks.clear()
        boxes_display = []
        print("🔄 Reset!")

inference.stop()
q_tugas.put(None)
cap.release()
if db: db.close()
cv2.destroyAllWindows()
print(f"\n📊 Rekap: {dict(stat)} | Total: {sum(stat.values())}")