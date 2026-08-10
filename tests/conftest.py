import sys
import time
import cv2
import threading
import types

class StopImportException(BaseException):
    pass

raise_on_sleep = False
saved_module = None

original_sleep = time.sleep
def mock_sleep(seconds):
    global saved_module
    # Hanya lempar exception jika dipanggil di Main Thread saat flag raise_on_sleep aktif
    if raise_on_sleep and threading.current_thread() == threading.main_thread():
        # Ambil frame pemanggil (konteks modul deteksi_ai)
        frame = sys._getframe(1)
        # Ambil semua class & variabel yang sudah terbentuk, amankan ke dummy module
        mock_module = types.ModuleType('deteksi_ai')
        mock_module.__dict__.update(frame.f_globals)
        saved_module = mock_module
        
        raise StopImportException("Safe import stop")
    original_sleep(seconds)

time.sleep = mock_sleep

# 1. Mock cv2.VideoCapture
class DummyVideoCapture:
    def __init__(self, *args, **kwargs): pass
    def isOpened(self): return True
    def read(self): return False, None
    def set(self, *args, **kwargs): pass
    def release(self): pass

cv2.VideoCapture = DummyVideoCapture

# 2. Mock mysql.connector
import mysql.connector
class DummyCursor:
    def execute(self, *args, **kwargs): pass
    def fetchone(self): return (1,)
    def close(self): pass
class DummyDB:
    def cursor(self, *args, **kwargs): return DummyCursor()
    def commit(self): pass
    def close(self): pass
    def is_connected(self): return True

mysql.connector.connect = lambda *args, **kwargs: DummyDB()
