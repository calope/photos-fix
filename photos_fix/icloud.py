"""
Diagnóstico de fotos disponibles localmente pero no subidas a iCloud.
Solo lectura — no modifica nada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from photos_fix import PHOTOS_ORIGINALS


REMOTE_AVAILABILITY = {
    1: "Disponible en iCloud",
    0: "No disponible en iCloud",
    -1: "Estado desconocido",
}

CLOUD_LOCAL_STATE = {
    0: "No está en iCloud",
    1: "Subiendo",
    2: "Subida completa",
    3: "Error de subida",
}


@dataclass
class ICloudResult:
    uuid: str
    filename: str
    path: str
    local_availability: int
    remote_availability: int
    cloud_local_state: int

    @property
    def remote_label(self) -> str:
        return REMOTE_AVAILABILITY.get(self.remote_availability, str(self.remote_availability))

    @property
    def state_label(self) -> str:
        return CLOUD_LOCAL_STATE.get(self.cloud_local_state, str(self.cloud_local_state))


def get_not_uploaded(
    rows: list[sqlite3.Row],
    originals_dir: Path = PHOTOS_ORIGINALS,
) -> list[ICloudResult]:
    results = []
    for row in rows:
        uuid = row["ZUUID"]
        filename = row["ZFILENAME"]
        directory = row["ZDIRECTORY"] or ""

        path = originals_dir / directory / uuid / filename
        if not path.exists():
            path = originals_dir / directory / filename

        results.append(ICloudResult(
            uuid=uuid,
            filename=filename,
            path=str(path),
            local_availability=row["ZLOCALAVAILABILITY"],
            remote_availability=row["ZREMOTEAVAILABILITY"],
            cloud_local_state=row["ZCLOUDLOCALSTATE"],
        ))

    return results
