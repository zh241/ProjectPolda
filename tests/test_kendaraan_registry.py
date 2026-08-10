import sys
import os
import datetime
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import conftest
conftest.raise_on_sleep = True
try:
    import deteksi_ai
except conftest.StopImportException:
    # Gunakan module hasil kloningan dari conftest yang dijamin aman dan terdefinisi
    deteksi_ai = conftest.saved_module
finally:
    conftest.raise_on_sleep = False

class DummyFrame:
    def copy(self):
        return self

@pytest.fixture
def mock_dependencies(monkeypatch):
    monkeypatch.setattr(deteksi_ai.cv2, "rectangle", lambda *args, **kwargs: None)
    monkeypatch.setattr(deteksi_ai.cv2, "putText", lambda *args, **kwargs: None)
    monkeypatch.setattr(deteksi_ai.cv2, "imwrite", lambda *args, **kwargs: None)
    
    put_calls = []
    def mock_put(item):
        put_calls.append(item)
    monkeypatch.setattr(deteksi_ai.q_tugas, "put", mock_put)
    return put_calls

def test_cek_dan_catat_belum_terkunci(mock_dependencies):
    reg = deteksi_ai.KendaraanRegistry()
    reg.update("OBJ_1", "Mobil", 100, 0, 0, 100, 100, 1.0, 200) 
    res = reg.cek_dan_catat("OBJ_1", 200, 30, 1.1, DummyFrame(), 1.0, 1.0)
    assert res is False

def test_cek_dan_catat_sukses_melintas(mock_dependencies):
    reg = deteksi_ai.KendaraanRegistry()
    garis_y = 200
    buffer = 30
    
    reg.update("OBJ_2", "Motor", 50, 0, 0, 50, 50, 1.0, garis_y)
    reg.update("OBJ_2", "Motor", 150, 0, 0, 50, 50, 2.0, garis_y)
    res1 = reg.cek_dan_catat("OBJ_2", garis_y, buffer, 2.1, DummyFrame(), 1.0, 1.0)
    assert res1 is False
    
    reg.update("OBJ_2", "Motor", 210, 0, 0, 50, 50, 3.0, garis_y)
    res2 = reg.cek_dan_catat("OBJ_2", garis_y, buffer, 3.1, DummyFrame(), 1.0, 1.0)
    assert res2 is True
    assert len(mock_dependencies) == 1

def test_cek_dan_catat_cegah_duplikasi(mock_dependencies):
    reg = deteksi_ai.KendaraanRegistry()
    garis_y = 200
    buffer = 30
    
    reg.update("OBJ_3", "Truk", 150, 0, 0, 100, 100, 1.0, garis_y)
    reg.update("OBJ_3", "Truk", 210, 0, 0, 100, 100, 2.0, garis_y)
    
    res1 = reg.cek_dan_catat("OBJ_3", garis_y, buffer, 2.1, DummyFrame(), 1.0, 1.0)
    assert res1 is True
    
    res2 = reg.cek_dan_catat("OBJ_3", garis_y, buffer, 2.2, DummyFrame(), 1.0, 1.0)
    assert res2 is False

def test_cek_dan_catat_belum_mencapai_garis(mock_dependencies):
    reg = deteksi_ai.KendaraanRegistry()
    garis_y = 200
    buffer = 30
    
    reg.update("OBJ_4", "Bus", 150, 0, 0, 100, 100, 1.0, garis_y)
    reg.update("OBJ_4", "Bus", 160, 0, 0, 100, 100, 2.0, garis_y)
    
    res = reg.cek_dan_catat("OBJ_4", garis_y, buffer, 2.1, DummyFrame(), 1.0, 1.0)
    assert res is False

def test_cek_dan_catat_muncul_di_bawah_garis(mock_dependencies):
    reg = deteksi_ai.KendaraanRegistry()
    garis_y = 200
    buffer = 30
    
    reg.update("OBJ_5", "Mobil", 180, 0, 0, 100, 100, 1.0, garis_y) 
    res1 = reg.cek_dan_catat("OBJ_5", garis_y, buffer, 1.1, DummyFrame(), 1.0, 1.0)
    assert res1 is False
    
    reg.update("OBJ_5", "Mobil", 210, 0, 0, 100, 100, 2.0, garis_y)
    res2 = reg.cek_dan_catat("OBJ_5", garis_y, buffer, 2.1, DummyFrame(), 1.0, 1.0)
    assert res2 is True

def test_pelacak_objek():
    pelacak = deteksi_ai.PelacakObjek(max_dist=150, max_age=25)
    deteksi1 = [(100, 100, 80, 80, 120, 120, "Mobil")]
    hasil1 = pelacak.update(deteksi1)
    
    assert len(hasil1) == 1
    id1 = hasil1[0][0]
    
    deteksi2 = [(110, 110, 90, 90, 130, 130, "Mobil")]
    hasil2 = pelacak.update(deteksi2)
    assert hasil2[0][0] == id1
