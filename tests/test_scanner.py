"""Tests del scanner usando imágenes de prueba generadas en memoria."""

import io
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import piexif
import pytest
from PIL import Image

from photos_fix.scanner import Status, scan_asset


def _make_jpeg_with_exif(w: int, h: int, exif_w: int, exif_h: int) -> bytes:
    """Genera un JPEG en memoria con dimensiones reales w×h y EXIF que dice exif_w×exif_h."""
    img = Image.new("RGB", (w, h), color=(128, 128, 128))

    exif_dict = {
        "0th": {},
        "Exif": {
            piexif.ExifIFD.PixelXDimension: exif_w,
            piexif.ExifIFD.PixelYDimension: exif_h,
        },
        "GPS": {},
        "1st": {},
    }
    exif_bytes = piexif.dump(exif_dict)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_bytes)
    return buf.getvalue()


def _mock_row(uuid="TEST-UUID", filename="test.jpg", directory="A", w_db=100, h_db=200):
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "ZUUID": uuid,
        "ZFILENAME": filename,
        "ZDIRECTORY": directory,
        "ZWIDTH": w_db,
        "ZHEIGHT": h_db,
    }[key]
    return row


def test_swap_confirmed(tmp_path):
    """Foto con pixel data 200×100 pero EXIF dice 100×200 → SWAP_CONFIRMED."""
    jpeg = _make_jpeg_with_exif(w=200, h=100, exif_w=100, exif_h=200)
    path = tmp_path / "A" / "TEST-UUID" / "test.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(jpeg)

    row = _mock_row(w_db=100, h_db=200)
    result = scan_asset(row, originals_dir=tmp_path)

    assert result.status == Status.SWAP_CONFIRMED
    assert result.w_real == 200
    assert result.h_real == 100
    assert result.w_exif == 100
    assert result.h_exif == 200


def test_ok(tmp_path):
    """Foto con dimensiones correctas → OK."""
    jpeg = _make_jpeg_with_exif(w=200, h=100, exif_w=200, exif_h=100)
    path = tmp_path / "A" / "TEST-UUID" / "test.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(jpeg)

    row = _mock_row(w_db=200, h_db=100)
    result = scan_asset(row, originals_dir=tmp_path)

    assert result.status == Status.OK


def test_square_not_flagged(tmp_path):
    """Foto cuadrada con dimensiones intercambiadas → no se marca (w==h)."""
    jpeg = _make_jpeg_with_exif(w=100, h=100, exif_w=100, exif_h=100)
    path = tmp_path / "A" / "TEST-UUID" / "test.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(jpeg)

    row = _mock_row(w_db=100, h_db=100)
    result = scan_asset(row, originals_dir=tmp_path)

    assert result.status == Status.OK


def test_local_missing(tmp_path):
    """Archivo no existente → LOCAL_MISSING."""
    row = _mock_row()
    result = scan_asset(row, originals_dir=tmp_path)
    assert result.status == Status.LOCAL_MISSING
