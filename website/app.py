from flask import Flask, render_template, redirect, url_for, request, send_file, session, flash
import mysql.connector
import os
import time
import pandas as pd 
import io
import datetime
import json
import socket
import requests
from functools import wraps 

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=False)

# ==========================================
# AUTO SETUP DATABASE SAAT STARTUP
# ==========================================
def init_db():
    try:
        conn = mysql.connector.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASS', ''),
            database=os.getenv('DB_NAME', 'db_polda_kalsel'),
            use_pure=True,
        )
        cursor = conn.cursor()

        # Tabel Arsip
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `arsip_log_kendaraan` (
              `id` int(11) NOT NULL AUTO_INCREMENT,
              `waktu` timestamp NOT NULL DEFAULT current_timestamp(),
              `arah` varchar(10) NOT NULL DEFAULT 'MASUK',
              `jenis_kendaraan` varchar(50) NOT NULL,
              `kategori` varchar(50) NOT NULL,
              `foto_bukti` varchar(255) NOT NULL,
              PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """)

        # Tabel Tamu (Plat dihapus)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `data_tamu` (
              `id` int(11) NOT NULL AUTO_INCREMENT,
              `nama_tamu` varchar(100) NOT NULL,
              `instansi` varchar(100) DEFAULT NULL,
              `tujuan` varchar(255) NOT NULL,
              `no_id_tamu` varchar(50) DEFAULT NULL,
              `status` varchar(20) DEFAULT 'AKTIF',
              `waktu_datang` timestamp NOT NULL DEFAULT current_timestamp(),
              PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """)

        # Tabel Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `log_kendaraan` (
              `id` int(11) NOT NULL AUTO_INCREMENT,
              `waktu` timestamp NOT NULL DEFAULT current_timestamp(),
              `arah` varchar(10) NOT NULL DEFAULT 'MASUK',
              `jenis_kendaraan` varchar(50) NOT NULL,
              `kategori` varchar(50) NOT NULL,
              `foto_bukti` varchar(255) NOT NULL,
              PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `pengguna` (
              `id` int(11) NOT NULL AUTO_INCREMENT,
              `username` varchar(50) NOT NULL,
              `password` varchar(255) NOT NULL,
              `nama_lengkap` varchar(100) NOT NULL,
              `role` varchar(20) NOT NULL DEFAULT 'Admin',
              `terakhir_login` timestamp NULL DEFAULT NULL,
              PRIMARY KEY (`id`),
              UNIQUE KEY `username` (`username`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """)

        cursor.execute("SELECT COUNT(*) FROM pengguna")
        (total,) = cursor.fetchone()
        if total == 0:
            cursor.execute("""
                INSERT INTO pengguna (username, password, nama_lengkap, role) VALUES
                ('admin', 'scrypt:32768:8:1$21sJLOLBlKqzMhqO$a73527aec7943593acc4410e2c75e17f07610e4af822fcd98070efcba67b1d707e68971cb31985791c81eb64f183f8e1c47c0da4b302a256d0fc24ed1774aa81', 'Bid Tik', 'Super Admin')
            """)

        # AUTO-CLEANUP: Buang kolom plat_nomor jika database lama masih menyimpannya
        try: cursor.execute("ALTER TABLE log_kendaraan DROP COLUMN plat_nomor")
        except: pass
        try: cursor.execute("ALTER TABLE arsip_log_kendaraan DROP COLUMN plat_nomor")
        except: pass
        try: cursor.execute("ALTER TABLE data_tamu DROP COLUMN plat_nomor")
        except: pass

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Database OK: Struktur bersih tanpa Plat Nomor.")
    except Exception as e:
        print(f"⚠️ init_db error: {e}")

init_db()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(32))
csrf = CSRFProtect(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day"],
    storage_uri="memory://"
)

# Variabel Global untuk Heartbeat AI
last_heartbeat = {"cpu": 0, "ram": 0, "time": 0}

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'loggedin' not in session:
            flash('Akses ditolak! Silakan login terlebih dahulu.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST') or os.getenv('MYSQLHOST', 'localhost'),
        port=int(os.getenv('DB_PORT') or os.getenv('MYSQLPORT') or 3306),
        user=os.getenv('DB_USER') or os.getenv('MYSQLUSER', 'root'),
        password=os.getenv('DB_PASS') or os.getenv('MYSQLPASSWORD', ''),
        database=os.getenv('DB_NAME') or os.getenv('MYSQLDATABASE', 'railway'),
        use_pure=True,
    )

def get_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    default = {"rtsp_cam1": "0", "ngrok_url": ""}
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = json.load(f)
                if 'ngrok_url' not in data: data['ngrok_url'] = ""
                return data
    except: pass
    return default

def save_config(data):
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=4)

def get_tanggal_aktif():
    tanggal = request.args.get('tanggal')
    if not tanggal: tanggal = datetime.datetime.now().strftime('%Y-%m-%d')
    return tanggal

# ==========================================
# AUTH
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db_connection()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM pengguna WHERE username = %s", (username,))
        akun = cursor.fetchone()
        if akun:
            sandi_cocok = False
            if check_password_hash(akun['password'], password): sandi_cocok = True
            elif akun['password'] == password:
                sandi_cocok = True
                new_hash = generate_password_hash(password)
                cursor.execute("UPDATE pengguna SET password = %s WHERE id = %s", (new_hash, akun['id']))
                db.commit()
            if sandi_cocok:
                session['loggedin'] = True
                session['id'] = akun['id']
                session['username'] = akun['username']
                session['nama_lengkap'] = akun['nama_lengkap']
                session['role'] = akun['role']
                cursor.execute("UPDATE pengguna SET terakhir_login = CURRENT_TIMESTAMP WHERE id = %s", (akun['id'],))
                db.commit()
                cursor.close(); db.close()
                return redirect(url_for('index'))
        cursor.close(); db.close()
        flash('Username atau Password salah!', 'danger')
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah berhasil keluar dari sistem.', 'success')
    return redirect(url_for('login'))

# ==========================================
# DASHBOARD
# ==========================================
@app.route('/')
@login_required
def index():
    if session.get('auto_policy_hari') and session.get('auto_policy_hari') != 'off':
        try:
            hari_auto = int(session.get('auto_policy_hari'))
            foto_dir = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan')
            if os.path.exists(foto_dir):
                batas_waktu = time.time() - (hari_auto * 86400)
                for nama_file in os.listdir(foto_dir):
                    path_file = os.path.join(foto_dir, nama_file)
                    if os.path.isfile(path_file) and os.path.getmtime(path_file) < batas_waktu: os.remove(path_file)
        except: pass

    tanggal_aktif = get_tanggal_aktif()
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    
    # Ambil data tanpa plat nomor
    cursor.execute("SELECT id, waktu, arah, jenis_kendaraan, kategori, foto_bukti FROM log_kendaraan WHERE DATE(waktu) = %s ORDER BY waktu DESC LIMIT 5", (tanggal_aktif,))
    data_kendaraan = cursor.fetchall()
    
    cursor.execute("SELECT kategori, jenis_kendaraan, arah FROM log_kendaraan WHERE DATE(waktu) = %s", (tanggal_aktif,))
    semua_data = cursor.fetchall()
    
    stats = {'total': len(semua_data), 'dinas': 0, 'sipil': 0, 'mobil': 0, 'motor': 0, 'besar': 0, 'keluar': 0, 'mobil_out': 0, 'motor_out': 0, 'besar_out': 0}
    for row in semua_data:
        # Polisi -> Dinas, Sisanya -> Sipil
        if row['kategori'] == 'Polisi' or row['kategori'] == 'Dinas': stats['dinas'] += 1
        else: stats['sipil'] += 1
        
        if row['arah'] == 'MASUK':
            if row['jenis_kendaraan'] == 'Mobil': stats['mobil'] += 1
            elif row['jenis_kendaraan'] == 'Motor': stats['motor'] += 1
            elif row['jenis_kendaraan'] in ['Truk', 'Bus']: stats['besar'] += 1
        elif row['arah'] == 'KELUAR':
            stats['keluar'] += 1
            if row['jenis_kendaraan'] == 'Mobil': stats['mobil_out'] += 1
            elif row['jenis_kendaraan'] == 'Motor': stats['motor_out'] += 1
            elif row['jenis_kendaraan'] in ['Truk', 'Bus']: stats['besar_out'] += 1
            
    cursor.execute("SELECT HOUR(waktu) as jam, kategori FROM log_kendaraan WHERE DATE(waktu) = %s", (tanggal_aktif,))
    raw_grafik = cursor.fetchall()
    hitung_dinas, hitung_sipil = [0]*6, [0]*6
    for row in raw_grafik:
        jam = row['jam']
        slot = 0
        if 6 <= jam < 9: slot = 0
        elif 9 <= jam < 12: slot = 1
        elif 12 <= jam < 15: slot = 2
        elif 15 <= jam < 18: slot = 3
        elif 18 <= jam < 21: slot = 4
        else: slot = 5
        
        if row['kategori'] == "Polisi" or row['kategori'] == "Dinas": hitung_dinas[slot] += 1
        else: hitung_sipil[slot] += 1
        
    data_grafik_final = {"dinas": hitung_dinas, "sipil": hitung_sipil}
    cursor.close(); db.close()
    return render_template('index.html', data_kendaraan=data_kendaraan, stats=stats, data_grafik=json.dumps(data_grafik_final), halaman='dashboard', tanggal_aktif=tanggal_aktif)

@app.route('/log_harian')
@login_required
def log_harian():
    tanggal_aktif = get_tanggal_aktif()
    cari = request.args.get('cari', '')
    arah_filter = request.args.get('arah', 'Semua')
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    query = "SELECT id, waktu, arah, jenis_kendaraan, kategori, foto_bukti FROM log_kendaraan WHERE DATE(waktu) = %s"
    params = [tanggal_aktif]
    if arah_filter != 'Semua': query += " AND arah = %s"; params.append(arah_filter)
    if cari: query += " AND (jenis_kendaraan LIKE %s OR waktu LIKE %s)"; params.extend([f'%{cari}%', f'%{cari}%'])
    query += " ORDER BY waktu DESC"
    cursor.execute(query, tuple(params))
    data_kendaraan = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('index.html', data_kendaraan=data_kendaraan, stats={}, halaman='log_semua', tanggal_aktif=tanggal_aktif, cari=cari, arah_aktif=arah_filter)

@app.route('/polisi')
@login_required
def polisi():
    tanggal_aktif = get_tanggal_aktif()
    cari = request.args.get('cari', '')
    arah_filter = request.args.get('arah', 'Semua')
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    query = "SELECT id, waktu, arah, jenis_kendaraan, kategori, foto_bukti FROM log_kendaraan WHERE (kategori = 'Polisi' OR kategori = 'Dinas') AND DATE(waktu) = %s"
    params = [tanggal_aktif]
    if arah_filter != 'Semua': query += " AND arah = %s"; params.append(arah_filter)
    if cari: query += " AND (jenis_kendaraan LIKE %s OR waktu LIKE %s)"; params.extend([f'%{cari}%', f'%{cari}%'])
    query += " ORDER BY waktu DESC"
    cursor.execute(query, tuple(params))
    data_kendaraan = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('index.html', data_kendaraan=data_kendaraan, stats={}, halaman='dinas', tanggal_aktif=tanggal_aktif, cari=cari, arah_aktif=arah_filter)

@app.route('/umum')
@login_required
def umum():
    tanggal_aktif = get_tanggal_aktif()
    cari = request.args.get('cari', '')
    arah_filter = request.args.get('arah', 'Semua')
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    query = "SELECT id, waktu, arah, jenis_kendaraan, kategori, foto_bukti FROM log_kendaraan WHERE (kategori = 'Umum' OR kategori = 'Sipil') AND DATE(waktu) = %s"
    params = [tanggal_aktif]
    if arah_filter != 'Semua': query += " AND arah = %s"; params.append(arah_filter)
    if cari: query += " AND (jenis_kendaraan LIKE %s OR waktu LIKE %s)"; params.extend([f'%{cari}%', f'%{cari}%'])
    query += " ORDER BY waktu DESC"
    cursor.execute(query, tuple(params))
    data_kendaraan = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('index.html', data_kendaraan=data_kendaraan, stats={}, halaman='sipil', tanggal_aktif=tanggal_aktif, cari=cari, arah_aktif=arah_filter)

@app.route('/hapus/<int:id_kendaraan>')
@login_required
def hapus_data(id_kendaraan):
    if session.get('role') != 'Super Admin':
        flash('PELANGGARAN: Anda tidak memiliki akses menghapus data.', 'danger')
        return redirect(request.referrer or url_for('index'))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("SELECT foto_bukti FROM log_kendaraan WHERE id = %s", (id_kendaraan,))
    hasil = cursor.fetchone()
    if hasil:
        nama_file = secure_filename(hasil[0])
        if nama_file:
            path_foto = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan', nama_file)
            if os.path.exists(path_foto): os.remove(path_foto)
        cursor.execute("DELETE FROM log_kendaraan WHERE id = %s", (id_kendaraan,))
        db.commit()
    cursor.close(); db.close()
    flash('Data berhasil dihapus permanen.', 'success')
    return redirect(request.referrer or url_for('index'))

# ==========================================
# API: HEARTBEAT (TERIMA DATA LAPTOP)
# ==========================================
@app.route('/api/heartbeat', methods=['POST'])
@csrf.exempt
def heartbeat():
    global last_heartbeat
    try:
        data = request.json
        if data:
            last_heartbeat['cpu'] = data.get('cpu', 0)
            last_heartbeat['ram'] = data.get('ram', 0)
            last_heartbeat['time'] = time.time()
        return {"status": "ok"}, 200
    except:
        return {"status": "error"}, 400

# ==========================================
# API: SYSTEM HEALTH BARU
# ==========================================
@app.route('/api/system_health')
@login_required
def system_health():
    global last_heartbeat
    
    # 1. Cek Koneksi DB MySQL
    db_status = "DISCONNECTED"
    try:
        temp_db = get_db_connection()
        if temp_db.is_connected():
            db_status = "CONNECTED"
        temp_db.close()
    except: pass

    # 2. Cek Koneksi AI
    jeda_waktu = time.time() - last_heartbeat['time']
    ai_running = jeda_waktu < 15 # Toleransi delay 15 detik
    status_ai = "RUNNING" if ai_running else "OFFLINE"
    
    cpu_load = last_heartbeat['cpu'] if ai_running else 0
    ram_usage = last_heartbeat['ram'] if ai_running else 0

    return {
        "cpu": cpu_load, 
        "ram": ram_usage, 
        "db_status": db_status, 
        "ai_status": status_ai
    }

# ==========================================
# STATISTIK
# ==========================================
@app.route('/statistik')
@login_required
def statistik():
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("""
        SELECT COUNT(*) as total,
            SUM(CASE WHEN kategori = 'Polisi' OR kategori = 'Dinas' THEN 1 ELSE 0 END) as dinas,
            SUM(CASE WHEN kategori = 'Sipil' OR kategori = 'Umum' THEN 1 ELSE 0 END) as sipil
        FROM log_kendaraan WHERE waktu >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
    """)
    row_stats = cursor.fetchone()
    stats = {'total': row_stats[0] if row_stats[0] else 0, 'dinas': row_stats[1] if row_stats[1] else 0, 'sipil': row_stats[2] if row_stats[2] else 0}
    cursor.execute("""
        SELECT CONCAT(LPAD(jam, 2, '0'), ':00 - ', LPAD(jam+1, 2, '0'), ':00')
        FROM (
            SELECT HOUR(waktu) AS jam, COUNT(*) AS total
            FROM log_kendaraan WHERE waktu >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY HOUR(waktu) ORDER BY total DESC LIMIT 1
        ) AS sub
    """)
    row_jam = cursor.fetchone()
    jam_sibuk = row_jam[0] if row_jam else "00:00 - 00:00"
    dinas_array, sipil_array = [0]*7, [0]*7
    cursor.execute("""
        SELECT WEEKDAY(waktu), kategori, COUNT(*) FROM log_kendaraan
        WHERE waktu >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY WEEKDAY(waktu), kategori
    """)
    for row in cursor.fetchall():
        hari_index, kategori, jumlah = row[0], row[1], row[2]
        if hari_index is not None:
            if kategori == 'Polisi' or kategori == 'Dinas': dinas_array[hari_index] += jumlah
            else: sipil_array[hari_index] += jumlah
    data_grafik = json.dumps({'dinas': dinas_array, 'sipil': sipil_array})
    cursor.close(); db.close()
    return render_template('statistik.html', stats=stats, jam_sibuk=jam_sibuk, data_grafik=data_grafik, halaman='statistik')

# ==========================================
# TAMU
# ==========================================
@app.route('/tamu')
@login_required
def tamu():
    tanggal_aktif = request.args.get('tanggal', datetime.datetime.now().strftime('%Y-%m-%d'))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, nama_tamu, instansi, tujuan, no_id_tamu, waktu_datang FROM data_tamu WHERE status = 'AKTIF' ORDER BY waktu_datang DESC")
    tamu_aktif = cursor.fetchall()
    cursor.execute("SELECT id, nama_tamu, instansi, tujuan, no_id_tamu, waktu_datang, status FROM data_tamu WHERE status = 'SELESAI' AND DATE(waktu_datang) = %s ORDER BY waktu_datang DESC", (tanggal_aktif,))
    tamu_selesai = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('tamu.html', tamu_aktif=tamu_aktif, tamu_selesai=tamu_selesai, halaman='tamu', tanggal_aktif=tanggal_aktif)

@app.route('/tambah_tamu', methods=['POST'])
@login_required
def tambah_tamu():
    nama = request.form['nama_tamu']
    instansi = request.form['instansi']
    tujuan = request.form['tujuan']
    no_id = request.form['no_id_tamu']
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("INSERT INTO data_tamu (nama_tamu, instansi, tujuan, no_id_tamu) VALUES (%s, %s, %s, %s)", (nama, instansi, tujuan, no_id))
    db.commit()
    cursor.close(); db.close()
    flash('Tamu berhasil diregistrasi!', 'success')
    return redirect(url_for('tamu'))

@app.route('/selesai_tamu/<int:id_tamu>')
@login_required
def selesai_tamu(id_tamu):
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("UPDATE data_tamu SET status = 'SELESAI' WHERE id = %s", (id_tamu,))
    db.commit()
    cursor.close(); db.close()
    flash('Status tamu diubah menjadi selesai/keluar.', 'success')
    return redirect(url_for('tamu'))

@app.route('/hapus_tamu/<int:id_tamu>')
@login_required
def hapus_tamu(id_tamu):
    if session.get('role') != 'Super Admin': return redirect(url_for('tamu'))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM data_tamu WHERE id = %s", (id_tamu,))
    db.commit()
    cursor.close(); db.close()
    flash('Riwayat tamu dihapus secara permanen.', 'success')
    return redirect(url_for('tamu'))

@app.route('/tamu/hapus_harian', methods=['POST'])
@login_required
def hapus_tamu_harian():
    if session.get('role') != 'Super Admin': return redirect(url_for('tamu'))
    tanggal = request.form.get('tanggal')
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM data_tamu WHERE DATE(waktu_datang) = %s AND status = 'SELESAI'", (tanggal,))
    db.commit()
    cursor.close(); db.close()
    flash(f"Seluruh riwayat tamu (SELESAI) pada tanggal {tanggal} telah dibersihkan.", 'warning')
    return redirect(url_for('tamu', tanggal=tanggal))

# ==========================================
# EXPORT
# ==========================================
@app.route('/export')
@login_required
def export_data():
    tipe_laporan = request.args.get('tipe_laporan', 'kendaraan')
    tanggal_awal = request.args.get('tanggal_awal', get_tanggal_aktif())
    tanggal_akhir = request.args.get('tanggal_akhir', get_tanggal_aktif())
    db = get_db_connection()
    if tipe_laporan == 'tamu':
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT waktu_datang, nama_tamu, instansi, tujuan, no_id_tamu, status FROM data_tamu WHERE DATE(waktu_datang) BETWEEN %s AND %s ORDER BY waktu_datang ASC", (tanggal_awal, tanggal_akhir))
        data = cursor.fetchall()
        cursor.close(); db.close()
        if not data:
            flash(f'Tidak ada catatan tamu dari {tanggal_awal} s.d {tanggal_akhir} untuk diekspor.', 'warning')
            return redirect(request.referrer or url_for('index'))
        df = pd.DataFrame(data)
        df.columns = ['Waktu', 'Nama Tamu', 'Instansi / Asal', 'Tujuan', 'No. Badge', 'Status']
        sheet_name = 'Buku_Tamu'
        nama_file = f"Laporan_Tamu_{tanggal_awal}_sd_{tanggal_akhir}.xlsx"
    else:
        kategori_export = request.args.get('kategori_export', 'Semua')
        jenis_export = request.args.get('jenis_export', 'Semua')
        cursor = db.cursor()
        query = "SELECT waktu, jenis_kendaraan, kategori FROM log_kendaraan WHERE DATE(waktu) BETWEEN %s AND %s"
        params = [tanggal_awal, tanggal_akhir]
        if kategori_export != 'Semua': query += " AND kategori = %s"; params.append(kategori_export)
        if jenis_export != 'Semua': query += " AND jenis_kendaraan = %s"; params.append(jenis_export)
        query += " ORDER BY waktu DESC"
        cursor.execute(query, tuple(params))
        data_kendaraan = cursor.fetchall()
        cursor.close(); db.close()
        if not data_kendaraan:
            flash(f'Tidak ada log kendaraan dari {tanggal_awal} s.d {tanggal_akhir} untuk diekspor.', 'warning')
            return redirect(request.referrer or url_for('index'))
        df = pd.DataFrame(data_kendaraan, columns=['Waktu Terekam', 'Jenis Kendaraan', 'Kategori'])
        sheet_name = 'Log_Kendaraan'
        nama_file = f"Laporan_Mako_{tanggal_awal}_sd_{tanggal_akhir}.xlsx"
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        worksheet = writer.sheets[sheet_name]
        for idx, col in enumerate(df.columns):
            worksheet.column_dimensions[chr(65 + idx)].width = 22
    output.seek(0)
    return send_file(output, download_name=nama_file, as_attachment=True)

# ==========================================
# ARSIP & MAINTENANCE
# ==========================================
@app.route('/arsip')
@login_required
def arsip():
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True, buffered=True)
    cursor.execute("SELECT COUNT(*) as total FROM log_kendaraan")
    hasil_count = cursor.fetchone()
    total_log = hasil_count['total'] if hasil_count else 0
    cursor.execute("SHOW TABLES LIKE 'arsip_log_kendaraan'")
    cek_tabel = cursor.fetchall()
    if cek_tabel:
        try:
            cursor.execute("SELECT id, waktu, jenis_kendaraan, kategori FROM arsip_log_kendaraan ORDER BY waktu DESC LIMIT 500")
            data_arsip = cursor.fetchall()
        except mysql.connector.Error:
            cursor.execute("SELECT * FROM arsip_log_kendaraan ORDER BY waktu DESC LIMIT 500")
            data_arsip = cursor.fetchall()
    else:
        data_arsip = []
    cursor.close(); db.close()
    foto_dir = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan')
    total_foto = len(os.listdir(foto_dir)) if os.path.exists(foto_dir) else 0
    return render_template('arsip.html', halaman='arsip', total_log=total_log, total_foto=total_foto, data_arsip=data_arsip)

@app.route('/arsip/pindahkan', methods=['POST'])
@login_required
def pindahkan_log():
    batas_hari = int(request.form['batas_hari'])
    hapus_foto = 'setuju_hapus_foto' in request.form
    tgl_batas = (datetime.datetime.now() - datetime.timedelta(days=batas_hari)).strftime('%Y-%m-%d')
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    try:
        if hapus_foto:
            cursor.execute("SELECT foto_bukti FROM log_kendaraan WHERE DATE(waktu) <= %s", (tgl_batas,))
            for f in cursor.fetchall():
                safe_name = secure_filename(f['foto_bukti'])
                if safe_name:
                    path = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan', safe_name)
                    if os.path.exists(path): os.remove(path)
        cursor.execute("CREATE TABLE IF NOT EXISTS arsip_log_kendaraan LIKE log_kendaraan")
        cursor.execute("INSERT INTO arsip_log_kendaraan SELECT * FROM log_kendaraan WHERE DATE(waktu) <= %s", (tgl_batas,))
        row_count = cursor.rowcount
        cursor.execute("DELETE FROM log_kendaraan WHERE DATE(waktu) <= %s", (tgl_batas,))
        db.commit()
        msg = f"Berhasil mengarsipkan {row_count} data."
        if hapus_foto: msg += " File foto fisik juga telah dibersihkan."
        flash(msg, 'success')
    except Exception as e: flash(f"Error: {e}", 'danger')
    finally: cursor.close(); db.close()
    return redirect(url_for('arsip'))

@app.route('/arsip/hapus_massal', methods=['POST'])
@login_required
def hapus_arsip_massal():
    tgl_awal = request.form['tgl_awal']
    tgl_akhir = request.form['tgl_akhir']
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM arsip_log_kendaraan WHERE DATE(waktu) BETWEEN %s AND %s", (tgl_awal, tgl_akhir))
    db.commit()
    flash(f"Pemusnahan massal berhasil! {cursor.rowcount} data arsip dihapus permanen.", 'warning')
    cursor.close(); db.close()
    return redirect(url_for('arsip'))

@app.route('/arsip/hapus_satuan/<int:id_arsip>')
@login_required
def hapus_arsip_satuan(id_arsip):
    if session.get('role') != 'Super Admin': return redirect(url_for('arsip'))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM arsip_log_kendaraan WHERE id = %s", (id_arsip,))
    db.commit()
    cursor.close(); db.close()
    flash('Data arsip berhasil dihapus secara satuan.', 'success')
    return redirect(url_for('arsip'))

@app.route('/arsip/hapus_semua', methods=['POST'])
@login_required
def hapus_semua_arsip():
    if session.get('role') != 'Super Admin': return redirect(url_for('arsip'))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("TRUNCATE TABLE arsip_log_kendaraan")
    db.commit()
    cursor.close(); db.close()
    flash("SELURUH DATA ARSIP TELAH DIMUSNAHKAN SECARA PERMANEN!", 'danger')
    return redirect(url_for('arsip'))

@app.route('/arsip/clean_foto', methods=['POST'])
@login_required
def clean_foto_manual():
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    hari = int(request.form['hari_foto'])
    foto_dir = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan')
    if not os.path.exists(foto_dir):
        flash("Folder foto tidak ditemukan di server.", 'danger')
        return redirect(url_for('arsip'))
    batas_waktu_detik = time.time() - (hari * 86400)
    jumlah_dihapus = 0
    for nama_file in os.listdir(foto_dir):
        path_file = os.path.join(foto_dir, nama_file)
        if os.path.isfile(path_file) and os.path.getmtime(path_file) < batas_waktu_detik:
            try: os.remove(path_file); jumlah_dihapus += 1
            except: pass
    flash(f"Storage Sweeper Berhasil! {jumlah_dihapus} file foto yang lebih tua dari {hari} hari telah dihapus.", 'success')
    return redirect(url_for('arsip'))

@app.route('/arsip/auto_policy', methods=['POST'])
@login_required
def set_auto_policy():
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    hari_auto = request.form['hari_auto']
    session['auto_policy_hari'] = hari_auto
    if hari_auto == 'off': flash("Sistem Hapus Foto Otomatis telah DIMATIKAN (OFF).", 'warning')
    else: flash(f"Auto-Policy AKTIF! Sistem akan otomatis membuang foto yang usianya lebih dari {hari_auto} hari.", 'success')
    return redirect(url_for('arsip'))

@app.route('/arsip/backup_full')
@login_required
def backup_full():
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    output = io.BytesIO()
    ALLOWED_TABLES = ['log_kendaraan', 'data_tamu', 'pengguna', 'arsip_log_kendaraan']
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for tabel in ALLOWED_TABLES:
            try:
                cursor.execute(f"SELECT * FROM {tabel}")
                data = cursor.fetchall()
                if data: pd.DataFrame(data).to_excel(writer, index=False, sheet_name=tabel.upper())
            except: pass
    cursor.close(); db.close()
    output.seek(0)
    tgl = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    return send_file(output, download_name=f"BACKUP_DB_MAKO_{tgl}.xlsx", as_attachment=True)

# ==========================================
# USERS
# ==========================================
@app.route('/users')
@login_required
def users():
    if session.get('role') != 'Super Admin':
        flash('Akses ditolak!', 'danger')
        return redirect(url_for('index'))
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT id, username, nama_lengkap, role, terakhir_login FROM pengguna ORDER BY role ASC")
    daftar_user = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('users.html', daftar_user=daftar_user, halaman='users')

@app.route('/tambah_user', methods=['POST'])
@login_required
def tambah_user():
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    username = request.form['username']
    password = request.form['password']
    nama_lengkap = request.form['nama_lengkap']
    role = request.form['role']
    hashed_password = generate_password_hash(password)
    db = get_db_connection()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO pengguna (username, password, nama_lengkap, role) VALUES (%s, %s, %s, %s)", (username, hashed_password, nama_lengkap, role))
        db.commit()
        flash('Personil berhasil didaftarkan dengan sandi terenkripsi.', 'success')
    except: flash('Gagal! Username sudah ada.', 'danger')
    finally: cursor.close(); db.close()
    return redirect(url_for('users'))

@app.route('/hapus_user/<int:id_user>')
@login_required
def hapus_user(id_user):
    if session.get('role') != 'Super Admin': return redirect(url_for('index'))
    if session.get('id') == id_user:
        flash('Tidak bisa menghapus diri sendiri!', 'danger')
        return redirect(url_for('users'))
    db = get_db_connection()
    cursor = db.cursor()
    cursor.execute("DELETE FROM pengguna WHERE id = %s", (id_user,))
    db.commit()
    cursor.close(); db.close()
    flash('Akses dicabut.', 'success')
    return redirect(url_for('users'))

# ==========================================
# PENGATURAN
# ==========================================
@app.route('/pengaturan', methods=['GET', 'POST'])
@login_required
def pengaturan():
    if session.get('role') != 'Super Admin':
        flash('Akses Ditolak!', 'danger')
        return redirect(url_for('index'))

    config_data = get_config()
    if request.method == 'POST':
        rtsp_1 = request.form.get('rtsp_cam1')
        ngrok_url = request.form.get('ngrok_url', '').strip().rstrip('/')
        if rtsp_1 is not None: config_data['rtsp_cam1'] = rtsp_1
        config_data['ngrok_url'] = ngrok_url
        save_config(config_data)
        flash('Konfigurasi berhasil disimpan!', 'success')
        return redirect(url_for('pengaturan'))
    return render_template('pengaturan.html', halaman='pengaturan', config=config_data)

@app.route('/edit_profil', methods=['POST'])
@login_required
def edit_profil():
    nama_lengkap = request.form.get('nama_lengkap')
    username = request.form.get('username')
    password_baru = request.form.get('password_baru')
    db = get_db_connection()
    cursor = db.cursor()
    try:
        if password_baru:
            hashed_pw = generate_password_hash(password_baru)
            cursor.execute("UPDATE pengguna SET nama_lengkap = %s, username = %s, password = %s WHERE id = %s", (nama_lengkap, username, hashed_pw, session['id']))
        else:
            cursor.execute("UPDATE pengguna SET nama_lengkap = %s, username = %s WHERE id = %s", (nama_lengkap, username, session['id']))
        db.commit()
        session['nama_lengkap'] = nama_lengkap
        session['username'] = username
        flash('Profil berhasil diperbarui!', 'success')
    except: flash('Gagal! Username mungkin sudah digunakan orang lain.', 'danger')
    finally: cursor.close(); db.close()
    return redirect(request.referrer or url_for('index'))

# ==========================================
# LIVE STREAM
# ==========================================
@app.route('/live_stream')
def live_stream():
    if 'loggedin' not in session: return redirect(url_for('login'))
    cfg = get_config()
    ngrok_url = cfg.get('ngrok_url', '').strip().rstrip('/')
    return render_template('live_stream.html', halaman='live_stream', ngrok_url=ngrok_url)

@app.route('/toggle_theme', methods=['POST'])
def toggle_theme():
    session['theme'] = 'dark' if session.get('theme') == 'light' else 'light'
    return redirect(request.referrer or url_for('index'))

@app.route('/api/upload_foto', methods=['POST'])
@csrf.exempt
def upload_foto():
    if 'foto' not in request.files: return {"status": "gagal", "pesan": "Tidak ada file"}, 400
    file = request.files['foto']
    nama_file = request.form.get('nama_file')
    if file and nama_file:
        foto_dir = os.path.join(os.path.dirname(__file__), 'static', 'foto_kendaraan')
        os.makedirs(foto_dir, exist_ok=True)
        file.save(os.path.join(foto_dir, nama_file))
        return {"status": "sukses", "pesan": "Foto diamankan"}, 200
    return {"status": "gagal", "pesan": "Data tidak lengkap"}, 400

@app.route('/api/get_config')
def get_config_api():
    cfg = get_config()
    return {"rtsp_cam1": cfg.get("rtsp_cam1", "0"), "ngrok_url": cfg.get("ngrok_url", "")}

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    port = int(os.getenv('PORT', 5005))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)