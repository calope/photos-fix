"""
Acceso de solo lectura a la base de datos SQLite de macOS Photos.

Requisito: Full Disk Access concedido a Terminal.app en:
  Ajustes del Sistema → Privacidad y Seguridad → Acceso completo al disco
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

from photos_fix import PHOTOS_DB
from photos_fix.log import get_logger

log = get_logger(__name__)


def check_photos_running() -> None:
    result = subprocess.run(["pgrep", "-x", "Photos"], capture_output=True)
    if result.returncode == 0:
        log.error("Photos está abierto — ciérralo antes de ejecutar")
        sys.exit(1)


def open_db(db_path: Path = PHOTOS_DB) -> sqlite3.Connection:
    if not db_path.exists():
        log.error(
            "No se encuentra la base de datos",
            path=str(db_path),
            hint="Asegúrate de que la biblioteca de Fotos está en ~/Pictures/",
        )
        sys.exit(1)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as e:
        if "authorization" in str(e).lower() or "unable to open" in str(e).lower():
            log.error(
                "Sin acceso a la base de datos de Fotos",
                hint="Ajustes del Sistema → Privacidad → Acceso completo al disco",
            )
            sys.exit(1)
        raise


def get_all_assets(
    conn: sqlite3.Connection, filter_size: tuple[int, int] | None = None
) -> list[sqlite3.Row]:
    """
    Devuelve todas las fotos (no vídeos, no en papelera).
    filter_size: (width, height) para filtrar por tamaño en DB.
    """
    query = """
        SELECT
            a.Z_PK,
            a.ZUUID,
            a.ZFILENAME,
            a.ZDIRECTORY,
            a.ZWIDTH,
            a.ZHEIGHT,
            a.ZKIND
        FROM ZASSET a
        WHERE a.ZKIND = 0
          AND a.ZTRASHEDSTATE = 0
    """
    params: list = []

    if filter_size:
        w, h = filter_size
        query += " AND ((a.ZWIDTH = ? AND a.ZHEIGHT = ?) OR (a.ZWIDTH = ? AND a.ZHEIGHT = ?))"
        params = [w, h, h, w]

    query += " ORDER BY a.Z_PK"

    cursor = conn.execute(query, params)
    return cursor.fetchall()


def get_icloud_status(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Fotos disponibles localmente pero no subidas a iCloud."""
    query = """
        SELECT
            a.ZUUID,
            a.ZFILENAME,
            a.ZDIRECTORY,
            r.ZLOCALAVAILABILITY,
            r.ZREMOTEAVAILABILITY,
            r.ZCLOUDLOCALSTATE,
            r.ZRESOURCETYPE
        FROM ZASSET a
        JOIN ZINTERNALRESOURCE r ON r.ZASSET = a.Z_PK
        WHERE a.ZKIND = 0
          AND a.ZTRASHEDSTATE = 0
          AND r.ZRESOURCETYPE = 0
          AND r.ZLOCALAVAILABILITY = 1
          AND r.ZREMOTEAVAILABILITY != 1
        ORDER BY a.Z_PK
    """
    return conn.execute(query).fetchall()
