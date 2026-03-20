"""Tests del fixer usando imágenes generadas en memoria."""

import io
from pathlib import Path

import piexif
import pytest
from PIL import Image

from photos_fix.fixer import fix_asset, FixStatus
from photos_fix.scanner import ScanResult, Status


def _make_jpeg_with_swapped_exif(tmp_path: Path) -> Path:
    """Crea un JPEG con pixel data 200×100 pero EXIF dice 100×200."""
    img = Image.new("RGB", (200, 100), color=(100, 150, 200))
    exif_dict = {
        "0th": {},
        "Exif": {
            piexif.ExifIFD.PixelXDimension: 100,
            piexif.ExifIFD.PixelYDimension: 200,
        },
        "GPS": {},
        "1st": {},
    }
    path = tmp_path / "test.jpg"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=piexif.dump(exif_dict))
    path.write_bytes(buf.getvalue())
    return path


def _make_scan_result(path: Path) -> ScanResult:
    return ScanResult(
        uuid="TEST-UUID",
        filename=path.name,
        path=str(path),
        status=Status.SWAP_CONFIRMED,
        w_real=200, h_real=100,
        w_exif=100, h_exif=200,
        w_db=100, h_db=200,
    )


def test_dry_run(tmp_path):
    path = _make_jpeg_with_swapped_exif(tmp_path)
    original_bytes = path.read_bytes()
    scan = _make_scan_result(path)

    result = fix_asset(scan, backup_dir=tmp_path / "backups", dry_run=True)

    assert result.fix_status == FixStatus.DRY_RUN
    assert path.read_bytes() == original_bytes  # sin cambios


def test_fix_swaps_exif(tmp_path):
    path = _make_jpeg_with_swapped_exif(tmp_path)
    scan = _make_scan_result(path)

    result = fix_asset(scan, backup_dir=tmp_path / "backups", dry_run=False)

    assert result.fix_status == FixStatus.FIXED

    # Verificar que el EXIF fue corregido
    with Image.open(path) as img:
        exif_dict = piexif.load(img.info["exif"])
        w = exif_dict["Exif"][piexif.ExifIFD.PixelXDimension]
        h = exif_dict["Exif"][piexif.ExifIFD.PixelYDimension]

    # Ahora debe coincidir con el pixel data real (200×100)
    assert w == 200
    assert h == 100


def test_backup_created(tmp_path):
    path = _make_jpeg_with_swapped_exif(tmp_path)
    backup_dir = tmp_path / "backups"
    scan = _make_scan_result(path)

    fix_asset(scan, backup_dir=backup_dir, dry_run=False)

    backups = list(backup_dir.glob("*.jpg"))
    assert len(backups) == 1


def test_skipped_if_not_confirmed(tmp_path):
    path = _make_jpeg_with_swapped_exif(tmp_path)
    scan = _make_scan_result(path)
    scan.status = Status.OK

    result = fix_asset(scan, backup_dir=tmp_path / "backups", dry_run=False)
    assert result.fix_status == FixStatus.SKIPPED
