"""
Diagnóstico integral de la biblioteca de Photos.

Combina todas las comprobaciones en un solo informe:
  - Dimensiones EXIF incorrectas (SWAP_CONFIRMED, SUSPECT)
  - Archivos de 0 bytes (ZERO_BYTE)
  - Archivos corruptos / ilegibles (UNREADABLE)
  - Sin EXIF (NO_EXIF)
  - Originales no disponibles localmente (LOCAL_MISSING)
  - Archivos físicos sin entrada en la DB (FILE_NO_DB — huérfanos)
  - Fotos no subidas a iCloud (NOT_UPLOADED)

Solo lectura. No modifica ningún archivo.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from photos_fix import PHOTOS_ORIGINALS
from photos_fix.icloud import ICloudResult, get_not_uploaded
from photos_fix.scanner import ScanResult, Status, scan_library


@dataclass
class OrphanResult:
    """Archivo físico en originals/ que no tiene entrada en la base de datos de Photos."""

    path: str
    size_bytes: int


@dataclass
class ZeroByteResult:
    """Archivo de 0 bytes — importación fallida o corrupción total."""

    uuid: str
    filename: str
    path: str


@dataclass
class HealthReport:
    scan_results: list[ScanResult] = field(default_factory=list)
    icloud_results: list[ICloudResult] = field(default_factory=list)
    orphans: list[OrphanResult] = field(default_factory=list)
    zero_bytes: list[ZeroByteResult] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        from collections import Counter

        scan_counts = Counter(r.status.value for r in self.scan_results)
        return {
            "total_fotos": len(self.scan_results),
            "swap_confirmed": scan_counts.get(Status.SWAP_CONFIRMED.value, 0),
            "iphoto_rotated": scan_counts.get(Status.IPHOTO_ROTATED.value, 0),
            "deformed": scan_counts.get(Status.DEFORMED.value, 0),
            "rotated": scan_counts.get(Status.ROTATED.value, 0),
            "suspect": scan_counts.get(Status.SUSPECT.value, 0),
            "ok": scan_counts.get(Status.OK.value, 0),
            "no_exif": scan_counts.get(Status.NO_EXIF.value, 0),
            "local_missing": scan_counts.get(Status.LOCAL_MISSING.value, 0),
            "unreadable": scan_counts.get(Status.UNREADABLE.value, 0),
            "zero_byte": len(self.zero_bytes),
            "not_uploaded": len(self.icloud_results),
            "orphans": len(self.orphans),
        }

    def has_issues(self) -> bool:
        s = self.summary()
        return any(
            s[k] > 0
            for k in (
                "swap_confirmed",
                "iphoto_rotated",
                "deformed",
                "rotated",
                "suspect",
                "local_missing",
                "unreadable",
                "zero_byte",
                "not_uploaded",
                "orphans",
            )
        )


def _find_zero_bytes(
    assets: list[sqlite3.Row],
    originals_dir: Path,
) -> list[ZeroByteResult]:
    results = []
    for row in assets:
        uuid = row["ZUUID"]
        filename = row["ZFILENAME"]
        directory = row["ZDIRECTORY"] or ""

        path = originals_dir / directory / uuid / filename
        if not path.exists():
            path = originals_dir / directory / filename

        if path.exists() and path.stat().st_size == 0:
            results.append(ZeroByteResult(uuid=uuid, filename=filename, path=str(path)))

    return results


def _find_orphans(
    assets: list[sqlite3.Row],
    originals_dir: Path,
) -> list[OrphanResult]:
    """
    Busca archivos físicos en originals/ que no estén referenciados en la DB.

    Solo revisa extensiones de imagen comunes para evitar falsos positivos
    con archivos de sistema o metadatos que Photos guarda junto a los originales.
    """
    IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".heic",
        ".heif",
        ".png",
        ".gif",
        ".tif",
        ".tiff",
        ".bmp",
        ".raw",
        ".cr2",
        ".cr3",
        ".nef",
        ".arw",
        ".dng",
        ".mov",
        ".mp4",
        ".m4v",
    }

    # Construir conjunto de rutas y UUIDs conocidos por la DB
    known_paths: set[str] = set()
    known_uuids: set[str] = set()
    for row in assets:
        uuid = row["ZUUID"]
        filename = row["ZFILENAME"]
        directory = row["ZDIRECTORY"] or ""

        p1 = originals_dir / directory / uuid / filename
        p2 = originals_dir / directory / filename
        known_paths.add(str(p1))
        known_paths.add(str(p2))
        known_uuids.add(uuid)

    orphans = []
    if not originals_dir.exists():
        return orphans

    for f in originals_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if str(f) in known_paths:
            continue

        # Descartar derivados internos de Photos: UUID_3.mov (Live Photo video),
        # UUID_O.heic (original antes de edición), etc.
        # Si el UUID base existe en ZASSET, Photos gestiona este archivo
        # a través de ZINTERNALRESOURCE — no es un huérfano real.
        stem = f.stem  # ej: "F9BBAB17-09F9-481E-8A4C-1E19F3A2E437_3"
        if "_" in stem:
            base_uuid = stem.rsplit("_", 1)[0]
            if base_uuid in known_uuids:
                continue

        orphans.append(OrphanResult(path=str(f), size_bytes=f.stat().st_size))

    return orphans


def run_health_check(
    assets: list[sqlite3.Row],
    icloud_rows: list[sqlite3.Row],
    originals_dir: Path = PHOTOS_ORIGINALS,
    progress_callback=None,
    detect_rotation: bool = False,
) -> HealthReport:
    report = HealthReport()

    # 1. Scan completo (dimensiones EXIF, archivos ilegibles, LOCAL_MISSING)
    report.scan_results = scan_library(
        assets,
        originals_dir,
        progress_callback=progress_callback,
        detect_rotation=detect_rotation,
    )

    # 2. Archivos de 0 bytes
    report.zero_bytes = _find_zero_bytes(assets, originals_dir)

    # 3. Fotos no subidas a iCloud
    report.icloud_results = get_not_uploaded(icloud_rows, originals_dir)

    # 4. Huérfanos (archivos sin entrada en DB)
    report.orphans = _find_orphans(assets, originals_dir)

    return report
