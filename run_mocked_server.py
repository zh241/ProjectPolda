import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# 1. Mock DB Connection
import mysql.connector

class DummyCursor:
    def __init__(self):
        self.last_query = ""

    def execute(self, query, *args, **kwargs):
        self.last_query = query.strip().lower()

    def fetchone(self):
        # Jika query adalah menghitung jumlah user (untuk init_db)
        if "count(*)" in self.last_query:
            return (1,)  # Kembalikan tuple untuk di-unpack (total,)
        
        # Jika query untuk mengambil data user saat login
        return {
            'id': 1,
            'username': 'admin',
            'password': 'scrypt:32768:8:1$21sJLOLBlKqzMhqO$a73527aec7943593acc4410e2c75e17f07610e4af822fcd98070efcba67b1d707e68971cb31985791c81eb64f183f8e1c47c0da4b302a256d0fc24ed1774aa81',
            'nama_lengkap': 'Mock Admin',
            'role': 'Super Admin'
        }
    def close(self): pass

class DummyDB:
    def cursor(self, *args, **kwargs): return DummyCursor()
    def commit(self): pass
    def close(self): pass

# Patch koneksi mysql
mysql.connector.connect = lambda *args, **kwargs: DummyDB()

# 2. Hindari error encoding emoji di Windows CMD
import builtins
original_print = builtins.print
def safe_print(*args, **kwargs):
    new_args = []
    for arg in args:
        s = str(arg)
        s = s.replace("✅", "[OK]").replace("⚠️", "[WARN]").replace("❌", "[ERROR]")
        new_args.append(s.encode('ascii', 'ignore').decode('ascii'))
    original_print(*new_args, **kwargs)
builtins.print = safe_print

# 3. Jalankan aplikasi Flask asli & Konfigurasi Filter Limiter
from website.app import app, limiter
from flask import request

# Exempt GET requests dari rate limiting agar fetch CSRF tidak dihitung
@limiter.request_filter
def exempt_get_and_test_admin():
    # 1. Bebaskan semua request GET
    if request.method == 'GET':
        return True
    # 2. Bebaskan username 'admin' (tes Wrong Credentials) agar tidak memakan jatah limit bruteforce
    if request.form.get('username') == 'admin':
        return True
    return False

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0') == '1'
    port = int(os.getenv('PORT', 5005))
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
