"""
CLI principal: photos-fix scan | fix | icloud

Uso:
  photos-fix scan [--library PATH] [--filter-size WxH] [--output DIR] [--format csv|json|both]
  photos-fix fix  [--library PATH] [--input FILE] [--backup-dir DIR] [--dry-run]
  photos-fix icloud [--library PATH] [--output DIR] [--format csv|json|both]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from photos_fix import PHOTOS_DB, PHOTOS_ORIGINALS
from photos_fix.db import (
    check_photos_running,
    get_all_assets,
    get_icloud_status,
    open_db,
)
from photos_fix.fixer import FixStatus, fix_batch
from photos_fix.icloud import get_not_uploaded
from photos_fix.report import write_fix_report, write_icloud_report, write_scan_report
from photos_fix.scanner import ScanResult, Status, scan_library


def _progress_bar(current: int, total: int, width: int = 40) -> str:
    filled = int(width * current / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"\r[{bar}] {current}/{total}"


def cmd_scan(args: argparse.Namespace) -> None:
    db_path = (
        Path(args.library) / "database" / "Photos.sqlite" if args.library else PHOTOS_DB
    )
    originals_dir = (
        Path(args.library) / "originals" if args.library else PHOTOS_ORIGINALS
    )
    output_dir = Path(args.output)

    filter_size = None
    if args.filter_size:
        try:
            w, h = args.filter_size.lower().split("x")
            filter_size = (int(w), int(h))
        except ValueError:
            print(
                "ERROR: --filter-size debe tener formato WxH (ej: 3264x2448)",
                file=sys.stderr,
            )
            sys.exit(1)

    check_photos_running()
    conn = open_db(db_path)
    assets = get_all_assets(conn, filter_size=filter_size)
    conn.close()

    print(f"Escaneando {len(assets)} fotos...")
    if filter_size:
        print(f"Filtro activo: {filter_size[0]}x{filter_size[1]}")

    results: list[ScanResult] = []

    def on_progress(current, total, result):
        print(_progress_bar(current, total), end="", flush=True)

    results = scan_library(assets, originals_dir, progress_callback=on_progress)
    print()  # nueva línea tras barra de progreso

    # Resumen
    from collections import Counter

    counts = Counter(r.status.value for r in results)
    print(f"\nResultados:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    if not results:
        print("No se encontraron fotos.")
        return

    paths = write_scan_report(results, output_dir, fmt=args.format)
    print(f"\nInforme guardado en:")
    for p in paths:
        print(f"  {p}")


def cmd_fix(args: argparse.Namespace) -> None:
    backup_dir = Path(args.backup_dir)
    dry_run: bool = args.dry_run

    # Cargar resultados del scan desde CSV
    input_path = Path(args.input) if args.input else None
    if not input_path:
        # Buscar el CSV más reciente en reports/
        reports_dir = Path("reports")
        csvs = (
            sorted(reports_dir.glob("scan_*.csv"), reverse=True)
            if reports_dir.exists()
            else []
        )
        if not csvs:
            print(
                "ERROR: No se encontró informe de scan. Ejecuta primero: photos-fix scan",
                file=sys.stderr,
            )
            sys.exit(1)
        input_path = csvs[0]
        print(f"Usando informe: {input_path}")

    scan_results = _load_scan_csv(input_path)
    candidates = [r for r in scan_results if r.status == Status.SWAP_CONFIRMED]

    if not candidates:
        print("No hay fotos con SWAP_CONFIRMED. Nada que corregir.")
        return

    print(f"Fotos a corregir: {len(candidates)}")

    if dry_run:
        print("MODO DRY-RUN: no se modificará ningún archivo.\n")
    else:
        print(f"Backup en: {backup_dir}")
        confirm = input(f'\nEscribe "CONFIRMAR" para continuar: ')
        if confirm.strip() != "CONFIRMAR":
            print("Cancelado.")
            sys.exit(0)

    check_photos_running()

    results = []

    def on_progress(current, total, result):
        print(_progress_bar(current, total), end="", flush=True)

    results = fix_batch(
        candidates, backup_dir, dry_run=dry_run, progress_callback=on_progress
    )
    print()

    from collections import Counter

    counts = Counter(r.fix_status.value for r in results)
    print(f"\nResultados:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    errors = [
        r
        for r in results
        if r.fix_status not in (FixStatus.FIXED, FixStatus.DRY_RUN, FixStatus.SKIPPED)
    ]
    if errors:
        print(f"\nErrores ({len(errors)}):")
        for r in errors[:10]:
            print(f"  {r.filename}: {r.fix_status.value} — {r.error}")

    paths = write_fix_report(
        results,
        Path(args.output) if hasattr(args, "output") else Path("reports"),
        fmt="both",
    )
    print(f"\nInforme guardado en:")
    for p in paths:
        print(f"  {p}")


def cmd_icloud(args: argparse.Namespace) -> None:
    db_path = (
        Path(args.library) / "database" / "Photos.sqlite" if args.library else PHOTOS_DB
    )
    originals_dir = (
        Path(args.library) / "originals" if args.library else PHOTOS_ORIGINALS
    )
    output_dir = Path(args.output)

    check_photos_running()
    conn = open_db(db_path)
    rows = get_icloud_status(conn)
    conn.close()

    results = get_not_uploaded(rows, originals_dir)
    print(f"Fotos no subidas a iCloud: {len(results)}")

    if not results:
        return

    paths = write_icloud_report(results, output_dir, fmt=args.format)
    print(f"\nInforme guardado en:")
    for p in paths:
        print(f"  {p}")


def _load_scan_csv(path: Path) -> list[ScanResult]:
    from photos_fix.scanner import Status

    results = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            r = ScanResult(
                uuid=row["uuid"],
                filename=row["filename"],
                path=row["path"],
                status=Status(row["status"]),
                w_real=int(row["w_real"]) if row["w_real"] else None,
                h_real=int(row["h_real"]) if row["h_real"] else None,
                w_exif=int(row["w_exif"]) if row["w_exif"] else None,
                h_exif=int(row["h_exif"]) if row["h_exif"] else None,
                w_db=int(row["w_db"]) if row["w_db"] else None,
                h_db=int(row["h_db"]) if row["h_db"] else None,
                error=row["error"] or None,
            )
            results.append(r)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="photos-fix",
        description="Diagnóstico y corrección de metadatos EXIF en macOS Photos",
    )
    parser.add_argument("--version", action="version", version="photos-fix 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- scan ---
    p_scan = sub.add_parser(
        "scan", help="Detectar fotos con dimensiones EXIF incorrectas"
    )
    p_scan.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_scan.add_argument(
        "--filter-size", metavar="WxH", help="Filtrar por tamaño en DB (ej: 3264x2448)"
    )
    p_scan.add_argument(
        "--output", default="reports", help="Directorio de salida (default: reports/)"
    )
    p_scan.add_argument("--format", choices=["csv", "json", "both"], default="both")

    # --- fix ---
    p_fix = sub.add_parser(
        "fix", help="Corregir dimensiones EXIF (intercambiar ancho↔alto)"
    )
    p_fix.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_fix.add_argument(
        "--input", help="CSV generado por scan (default: más reciente en reports/)"
    )
    p_fix.add_argument(
        "--backup-dir",
        default="backups",
        help="Directorio de backups (default: backups/)",
    )
    p_fix.add_argument(
        "--output", default="reports", help="Directorio de informes (default: reports/)"
    )
    p_fix.add_argument(
        "--dry-run", action="store_true", help="Simula sin modificar nada"
    )

    # --- icloud ---
    p_icloud = sub.add_parser("icloud", help="Diagnóstico de fotos no subidas a iCloud")
    p_icloud.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_icloud.add_argument(
        "--output", default="reports", help="Directorio de salida (default: reports/)"
    )
    p_icloud.add_argument("--format", choices=["csv", "json", "both"], default="both")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "fix":
        cmd_fix(args)
    elif args.command == "icloud":
        cmd_icloud(args)


if __name__ == "__main__":
    main()
