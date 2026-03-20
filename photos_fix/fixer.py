"""
Corrección de metadatos EXIF: intercambia PixelXDimension ↔ PixelYDimension.

NO modifica el pixel data. Usa piexif.insert() para reemplazar solo el bloque
EXIF del archivo JPEG, sin recomprimir ni degradar la imagen.

Pipeline de seguridad:
  1. hash SHA-256 del original
  2. copia a backup_dir
  3. verificar hash del backup
  4. [dry-run: parar aquí]
  5. piexif.insert() — reescribe solo el bloque EXIF
  6. verificar que PIL puede abrir el resultado
  7. si falla cualquier paso: restaurar backup automáticamente
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import piexif
from PIL import Image

from photos_fix.scanner import ScanResult, Status


class FixStatus(str, Enum):
    FIXED = "FIXED"
    DRY_RUN = "DRY_RUN"
    SKIPPED = "SKIPPED"           # no era SWAP_CONFIRMED
    NO_EXIF_DIMS = "NO_EXIF_DIMS" # sin PixelXDimension en EXIF
    BACKUP_FAILED = "BACKUP_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"
    RESTORED = "RESTORED"         # falló el fix, se restauró el backup
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

    # Fix: intercambiar PixelXDimension ↔ PixelYDimension
    try:
        exif_dict["Exif"][piexif.ExifIFD.PixelXDimension] = h
        exif_dict["Exif"][piexif.ExifIFD.PixelYDimension] = w

        new_exif_bytes = piexif.dump(exif_dict)
        piexif.insert(new_exif_bytes, str(path))
    except Exception as e:
        # Restaurar backup
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
        # Restaurar backup
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
