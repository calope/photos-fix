"""
Corrección de metadatos EXIF: intercambia PixelXDimension ↔ PixelYDimension.

NO modifica el pixel data.

JPEG: piexif.insert() reemplaza solo el bloque EXIF sin recomprimir.
HEIC: exiftool modifica solo los metadatos en el contenedor HEIF sin re-encodificar.

Pipeline de seguridad:
  1. hash SHA-256 del original
  2. copia a backup_dir
  3. verificar hash del backup
  4. [dry-run: parar aquí]
  5. fix según formato (piexif para JPEG, exiftool para HEIC)
  6. verificar que PIL puede abrir el resultado
  7. si falla cualquier paso: restaurar backup automáticamente
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import piexif
from PIL import Image

from photos_fix.scanner import ScanResult, Status

_HEIC_EXTENSIONS = {".heic", ".heif", ".heics", ".heifs"}


class FixStatus(str, Enum):
    FIXED = "FIXED"
    DRY_RUN = "DRY_RUN"
    SKIPPED = "SKIPPED"  # no era SWAP_CONFIRMED
    NO_EXIF_DIMS = "NO_EXIF_DIMS"  # sin PixelXDimension en EXIF
    HEIC_NO_EXIFTOOL = "HEIC_NO_EXIFTOOL"  # exiftool no instalado
    BACKUP_FAILED = "BACKUP_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"
    RESTORED = "RESTORED"  # falló el fix, se restauró el backup
    ERROR = "ERROR"


@dataclass
class FixResult:
    uuid: str
    filename: str
    path: str
    fix_status: FixStatus
    error: str | None = None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _exiftool_available() -> bool:
    try:
        subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _fix_jpeg(path: Path, exif_dict: dict, w: int, h: int) -> None:
    """Intercambia dimensiones EXIF en JPEG usando piexif.insert() (sin recomprimir)."""
    exif_dict["Exif"][piexif.ExifIFD.PixelXDimension] = h
    exif_dict["Exif"][piexif.ExifIFD.PixelYDimension] = w
    new_exif_bytes = piexif.dump(exif_dict)
    piexif.insert(new_exif_bytes, str(path))


def _fix_heic(path: Path, w: int, h: int) -> None:
    """Intercambia dimensiones EXIF en HEIC usando exiftool (sin re-encodificar)."""
    subprocess.run(
        [
            "exiftool",
            f"-PixelXDimension={h}",
            f"-PixelYDimension={w}",
            "-overwrite_original",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def fix_asset(
    scan_result: ScanResult,
    backup_dir: Path,
    dry_run: bool = False,
) -> FixResult:
    result = FixResult(
        uuid=scan_result.uuid,
        filename=scan_result.filename,
        path=scan_result.path,
        fix_status=FixStatus.SKIPPED,
    )

    if scan_result.status != Status.SWAP_CONFIRMED:
        return result

    path = Path(scan_result.path)
    is_heic = path.suffix.lower() in _HEIC_EXTENSIONS

    # Leer EXIF
    try:
        with Image.open(path) as img:
            exif_raw = img.info.get("exif", b"")
    except Exception as e:
        result.fix_status = FixStatus.ERROR
        result.error = str(e)
        return result

    if not exif_raw:
        result.fix_status = FixStatus.NO_EXIF_DIMS
        return result

    try:
        exif_dict = piexif.load(exif_raw)
        w = exif_dict["Exif"].get(piexif.ExifIFD.PixelXDimension)
        h = exif_dict["Exif"].get(piexif.ExifIFD.PixelYDimension)
    except Exception as e:
        result.fix_status = FixStatus.ERROR
        result.error = f"piexif.load: {e}"
        return result

    if not w or not h:
        result.fix_status = FixStatus.NO_EXIF_DIMS
        return result

    if is_heic and not _exiftool_available():
        result.fix_status = FixStatus.HEIC_NO_EXIFTOOL
        result.error = "Instala exiftool: brew install exiftool"
        return result

    if dry_run:
        result.fix_status = FixStatus.DRY_RUN
        return result

    # Backup
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{scan_result.uuid}_{path.name}"

    try:
        hash_before = _sha256(path)
        shutil.copy2(path, backup_path)
        hash_backup = _sha256(backup_path)

        if hash_before != hash_backup:
            result.fix_status = FixStatus.BACKUP_FAILED
            result.error = "Hash mismatch tras copia de backup"
            return result
    except Exception as e:
        result.fix_status = FixStatus.BACKUP_FAILED
        result.error = str(e)
        return result

    # Fix según formato
    try:
        if is_heic:
            _fix_heic(path, w, h)
        else:
            _fix_jpeg(path, exif_dict, w, h)
    except Exception as e:
        try:
            shutil.copy2(backup_path, path)
            result.fix_status = FixStatus.RESTORED
        except Exception:
            result.fix_status = FixStatus.ERROR
        result.error = str(e)
        return result

    # Verificar que el archivo sigue siendo legible
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        try:
            shutil.copy2(backup_path, path)
            result.fix_status = FixStatus.RESTORED
        except Exception:
            result.fix_status = FixStatus.ERROR
        result.error = f"verify falló tras fix: {e}"
        return result

    result.fix_status = FixStatus.FIXED
    return result


def fix_batch(
    scan_results: list[ScanResult],
    backup_dir: Path,
    dry_run: bool = False,
    progress_callback=None,
) -> list[FixResult]:
    candidates = [r for r in scan_results if r.status == Status.SWAP_CONFIRMED]
    results = []
    total = len(candidates)

    for i, scan_result in enumerate(candidates):
        fix_result = fix_asset(scan_result, backup_dir, dry_run=dry_run)
        results.append(fix_result)

        if progress_callback:
            progress_callback(i + 1, total, fix_result)

    return results
