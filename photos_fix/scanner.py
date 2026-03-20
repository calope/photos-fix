"""
Detección de fotos con dimensiones EXIF incorrectas (ancho y alto intercambiados).

Lógica:
  - PIL lee las dimensiones reales del pixel data (fuente de verdad)
  - piexif lee PixelXDimension / PixelYDimension del EXIF
  - Si w_real == h_exif AND h_real == w_exif → SWAP_CONFIRMED
  - Si no hay EXIF de dimensiones, compara con ZASSET.ZWIDTH / ZASSET.ZHEIGHT
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import piexif
from PIL import Image, UnidentifiedImageError

from photos_fix import PHOTOS_ORIGINALS


class Status(str, Enum):
    SWAP_CONFIRMED = "SWAP_CONFIRMED"  # dimensiones EXIF exactamente intercambiadas
    SUSPECT = "SUSPECT"  # orientación opuesta entre PIL y DB, sin EXIF dims
    OK = "OK"
    LOCAL_MISSING = "LOCAL_MISSING"  # archivo no disponible localmente (en iCloud)
    UNREADABLE = "UNREADABLE"  # archivo corrupto o formato no soportado
    NO_EXIF = "NO_EXIF"  # sin bloque EXIF (foto muy antigua o procesada)


@dataclass
class ScanResult:
    uuid: str
    filename: str
    path: str
    status: Status
    w_real: int | None = None
    h_real: int | None = None
    w_exif: int | None = None
    h_exif: int | None = None
    w_db: int | None = None
    h_db: int | None = None
    error: str | None = None


def _asset_path(originals_dir: Path, directory: str, uuid: str, filename: str) -> Path:
    """Construye la ruta al original: originals/{ZDIRECTORY}/{UUID}/{filename}"""
    # En Photos el directorio es una sola letra hex (A-F, 0-9)
    # y dentro hay subcarpetas con UUID
    candidate = originals_dir / directory / uuid / filename
    if candidate.exists():
        return candidate

    # Algunas versiones antiguas no tienen subcarpeta UUID
    candidate2 = originals_dir / directory / filename
    if candidate2.exists():
        return candidate2

    return (
        candidate  # devolvemos el esperado aunque no exista (FileNotFoundError luego)
    )


def scan_asset(
    row: sqlite3.Row,
    originals_dir: Path = PHOTOS_ORIGINALS,
) -> ScanResult:
    uuid = row["ZUUID"]
    filename = row["ZFILENAME"]
    directory = row["ZDIRECTORY"] or ""
    w_db = row["ZWIDTH"]
    h_db = row["ZHEIGHT"]

    path = _asset_path(originals_dir, directory, uuid, filename)

    result = ScanResult(
        uuid=uuid,
        filename=filename,
        path=str(path),
        status=Status.OK,
        w_db=w_db,
        h_db=h_db,
    )

    if not path.exists():
        result.status = Status.LOCAL_MISSING
        return result

    # Leer dimensiones reales del pixel data
    try:
        with Image.open(path) as img:
            w_real, h_real = img.size
            exif_raw = img.info.get("exif", b"")
    except (UnidentifiedImageError, OSError) as e:
        result.status = Status.UNREADABLE
        result.error = str(e)
        return result

    result.w_real = w_real
    result.h_real = h_real

    # Leer dimensiones EXIF
    w_exif = h_exif = None
    if exif_raw:
        try:
            exif_dict = piexif.load(exif_raw)
            w_exif = exif_dict["Exif"].get(piexif.ExifIFD.PixelXDimension)
            h_exif = exif_dict["Exif"].get(piexif.ExifIFD.PixelYDimension)
        except Exception:
            pass

    result.w_exif = w_exif
    result.h_exif = h_exif

    # Detección: comparar PIL vs EXIF
    if w_exif and h_exif:
        if w_real == h_exif and h_real == w_exif and w_real != h_real:
            result.status = Status.SWAP_CONFIRMED
            return result
    elif not exif_raw:
        result.status = Status.NO_EXIF

    # Fallback: comparar PIL vs DB (solo si no hay EXIF de dimensiones).
    # Si EXIF está presente y coincide con PIL, la foto es correcta aunque la DB
    # tenga valores distintos (caché desactualizada). No marcar como SUSPECT.
    if result.status in (Status.OK, Status.NO_EXIF) and not (w_exif and h_exif):
        if w_db and h_db and w_real == h_db and h_real == w_db and w_real != h_real:
            result.status = Status.SUSPECT

    return result


def scan_library(
    assets: list[sqlite3.Row],
    originals_dir: Path = PHOTOS_ORIGINALS,
    progress_callback=None,
) -> list[ScanResult]:
    results = []
    total = len(assets)

    for i, row in enumerate(assets):
        result = scan_asset(row, originals_dir)
        results.append(result)

        if progress_callback:
            progress_callback(i + 1, total, result)

    return results
