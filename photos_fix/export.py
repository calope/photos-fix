"""
Exportación de originales a un directorio plano.

Copia todos los archivos originales de la biblioteca a un directorio destino
preservando el nombre de archivo original. Útil para:
  - Backup independiente de la app Photos antes de migrar
  - Extraer fotos no subidas a iCloud
  - Migración a otro gestor de fotos

No modifica la biblioteca. Solo copia.

En caso de nombre duplicado, añade el UUID al nombre para evitar colisiones:
  foto.jpg → foto_XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX.jpg
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from photos_fix import PHOTOS_ORIGINALS


class ExportStatus(str, Enum):
    COPIED = "COPIED"
    SKIPPED_EXISTS = "SKIPPED_EXISTS"  # ya existe en destino con mismo nombre y tamaño
    LOCAL_MISSING = "LOCAL_MISSING"  # original no disponible localmente
    ERROR = "ERROR"


@dataclass
class ExportResult:
    uuid: str
    filename: str
    src_path: str
    dst_path: str
    status: ExportStatus
    error: str | None = None


def export_asset(
    row: sqlite3.Row,
    output_dir: Path,
    originals_dir: Path = PHOTOS_ORIGINALS,
    skip_existing: bool = True,
) -> ExportResult:
    uuid = row["ZUUID"]
    filename = row["ZFILENAME"]
    directory = row["ZDIRECTORY"] or ""

    src = originals_dir / directory / uuid / filename
    if not src.exists():
        src = originals_dir / directory / filename

    result = ExportResult(
        uuid=uuid,
        filename=filename,
        src_path=str(src),
        dst_path="",
        status=ExportStatus.LOCAL_MISSING,
    )

    if not src.exists():
        return result

    # Resolver colisiones de nombre
    dst = output_dir / filename
    if dst.exists():
        if skip_existing and dst.stat().st_size == src.stat().st_size:
            result.dst_path = str(dst)
            result.status = ExportStatus.SKIPPED_EXISTS
            return result
        # Nombre diferente: añadir UUID para evitar sobreescritura
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        dst = output_dir / f"{stem}_{uuid}{suffix}"

    try:
        shutil.copy2(src, dst)
        result.dst_path = str(dst)
        result.status = ExportStatus.COPIED
    except Exception as e:
        result.dst_path = str(dst)
        result.status = ExportStatus.ERROR
        result.error = str(e)

    return result


def export_batch(
    assets: list[sqlite3.Row],
    output_dir: Path,
    originals_dir: Path = PHOTOS_ORIGINALS,
    only_not_uploaded: bool = False,
    not_uploaded_uuids: set[str] | None = None,
    skip_existing: bool = True,
    progress_callback=None,
) -> list[ExportResult]:
    """
    Exporta originales al directorio destino.

    only_not_uploaded: si True, exporta solo las fotos que no están en iCloud.
    not_uploaded_uuids: conjunto de UUIDs de fotos no subidas (requerido si only_not_uploaded=True).
    skip_existing: si True, omite archivos que ya existen en destino con el mismo tamaño.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if only_not_uploaded and not_uploaded_uuids:
        assets = [row for row in assets if row["ZUUID"] in not_uploaded_uuids]

    results = []
    total = len(assets)

    for i, row in enumerate(assets):
        result = export_asset(
            row, output_dir, originals_dir, skip_existing=skip_existing
        )
        results.append(result)

        if progress_callback:
            progress_callback(i + 1, total, result)

    return results
