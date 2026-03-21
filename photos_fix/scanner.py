"""
Detección de fotos con dimensiones EXIF incorrectas y deformaciones por iPhoto.

Lógica:
  - PIL lee las dimensiones reales del pixel data (fuente de verdad)
  - piexif lee PixelXDimension / PixelYDimension del EXIF
  - Si w_real == h_exif AND h_real == w_exif → SWAP_CONFIRMED
  - Si Software=iPhoto 9 + Orientation=1 → IPHOTO_ROTATED
  - Si no hay EXIF header + portrait + gradient ratio alto → DEFORMED
  - Si no hay EXIF de dimensiones, compara con ZASSET.ZWIDTH / ZASSET.ZHEIGHT
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
import piexif
from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener

try:
    import cv2

    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

register_heif_opener()  # habilita PIL.Image.open() para HEIC/HEIF

from photos_fix import PHOTOS_ORIGINALS


GRADIENT_THRESHOLD = 1.4  # ratio H/V por encima del cual se considera deformada (F1=0.950)


class Status(str, Enum):
    SWAP_CONFIRMED = "SWAP_CONFIRMED"  # dimensiones EXIF exactamente intercambiadas
    IPHOTO_ROTATED = "IPHOTO_ROTATED"  # iPhoto 9 rotó píxeles y dejó Orientation=1
    DEFORMED = "DEFORMED"  # deformación detectada por gradient ratio (sin EXIF)
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


def _gradient_ratio(path: Path) -> float | None:
    """Ratio de energía de gradientes horizontales vs verticales.

    Fotos deformadas (aplastadas por iPhoto) tienen ratio alto (>1.5)
    porque los gradientes horizontales dominan sobre los verticales.
    Requiere OpenCV instalado.
    """
    if not _HAS_CV2:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    gx = cv2.Sobel(img, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_64F, 0, 1, ksize=3)
    energy_v = np.sum(gy**2)
    if energy_v == 0:
        return None
    return float(np.sum(gx**2) / energy_v)


def _has_exif_header(path: Path) -> bool:
    """Comprueba si el archivo JPEG tiene cabecera EXIF (primeros 100 bytes)."""
    with open(path, "rb") as f:
        return b"Exif" in f.read(100)


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
    orientation = None
    software = None
    if exif_raw:
        try:
            exif_dict = piexif.load(exif_raw)
            w_exif = exif_dict["Exif"].get(piexif.ExifIFD.PixelXDimension)
            h_exif = exif_dict["Exif"].get(piexif.ExifIFD.PixelYDimension)
            orientation = exif_dict["0th"].get(piexif.ImageIFD.Orientation)
            sw_raw = exif_dict["0th"].get(piexif.ImageIFD.Software, b"")
            if isinstance(sw_raw, bytes):
                software = sw_raw.decode("utf-8", errors="replace").strip()
            else:
                software = str(sw_raw).strip()
        except Exception:
            pass

    result.w_exif = w_exif
    result.h_exif = h_exif

    # Detección 1: comparar PIL vs EXIF (swap clásico)
    if w_exif and h_exif:
        if w_real == h_exif and h_real == w_exif and w_real != h_real:
            result.status = Status.SWAP_CONFIRMED
            return result
    elif not exif_raw:
        result.status = Status.NO_EXIF

    # Detección 2: iPhoto 9.x rotó los píxeles y dejó Orientation=1.
    # PIL=EXIF=DB coinciden, pero la foto se ve girada en Photos.
    # Patrón: Software=iPhoto 9.*, Orientation=1, dimensiones no cuadradas.
    if (
        result.status == Status.OK
        and software
        and software.startswith("iPhoto 9")
        and orientation == 1
        and w_real != h_real
    ):
        result.status = Status.IPHOTO_ROTATED
        return result

    # Detección 3: deformación por gradient ratio.
    # Fotos sin EXIF header, portrait, con gradient ratio alto = deformadas por iPhoto.
    # iPhoto borró el EXIF además de deformar los píxeles.
    if (
        result.status == Status.NO_EXIF
        and _HAS_CV2
        and h_real > w_real
        and not _has_exif_header(path)
    ):
        gr = _gradient_ratio(path)
        if gr is not None and gr > GRADIENT_THRESHOLD:
            result.status = Status.DEFORMED
            return result

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
