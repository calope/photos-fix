"""Generación de informes en CSV y JSON."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from photos_fix.scanner import ScanResult
from photos_fix.fixer import FixResult
from photos_fix.icloud import ICloudResult


def _now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_scan_report(
    results: list[ScanResult],
    output_dir: Path,
    fmt: str = "both",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = _now()
    written = []

    rows = [
        {
            "uuid": r.uuid,
            "filename": r.filename,
            "path": r.path,
            "status": r.status.value,
            "w_real": r.w_real,
            "h_real": r.h_real,
            "w_exif": r.w_exif,
            "h_exif": r.h_exif,
            "w_db": r.w_db,
            "h_db": r.h_db,
            "error": r.error,
        }
        for r in results
    ]

    if fmt in ("csv", "both"):
        path = output_dir / f"scan_{ts}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)

    if fmt in ("json", "both"):
        from collections import Counter
        counts = Counter(r.status.value for r in results)
        data = {
            "generated_at": datetime.now().isoformat(),
            "total_scanned": len(results),
            **counts,
            "assets": rows,
        }
        path = output_dir / f"scan_{ts}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(path)

    return written


def write_fix_report(
    results: list[FixResult],
    output_dir: Path,
    fmt: str = "both",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = _now()
    written = []

    rows = [
        {
            "uuid": r.uuid,
            "filename": r.filename,
            "path": r.path,
            "fix_status": r.fix_status.value,
            "error": r.error,
        }
        for r in results
    ]

    if fmt in ("csv", "both"):
        path = output_dir / f"fix_{ts}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)

    if fmt in ("json", "both"):
        from collections import Counter
        counts = Counter(r.fix_status.value for r in results)
        data = {
            "generated_at": datetime.now().isoformat(),
            "total": len(results),
            **counts,
            "fixes": rows,
        }
        path = output_dir / f"fix_{ts}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(path)

    return written


def write_icloud_report(
    results: list[ICloudResult],
    output_dir: Path,
    fmt: str = "both",
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = _now()
    written = []

    rows = [
        {
            "uuid": r.uuid,
            "filename": r.filename,
            "path": r.path,
            "local_availability": r.local_availability,
            "remote_availability": r.remote_availability,
            "remote_label": r.remote_label,
            "cloud_local_state": r.cloud_local_state,
            "state_label": r.state_label,
        }
        for r in results
    ]

    if fmt in ("csv", "both"):
        path = output_dir / f"icloud_{ts}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)

    if fmt in ("json", "both"):
        data = {
            "generated_at": datetime.now().isoformat(),
            "total_not_uploaded": len(results),
            "assets": rows,
        }
        path = output_dir / f"icloud_{ts}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(path)

    return written
