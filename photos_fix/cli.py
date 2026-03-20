"""
CLI principal: photos-fix scan | fix | icloud | health | export

Uso:
  photos-fix scan    [--library PATH] [--filter-size WxH] [--output DIR] [--format csv|json|both]
  photos-fix fix     [--library PATH] [--input FILE] [--backup-dir DIR] [--dry-run]
  photos-fix icloud  [--library PATH] [--output DIR] [--format csv|json|both]
  photos-fix health  [--library PATH] [--output DIR] [--format csv|json|both]
  photos-fix export  [--library PATH] [--output DIR] [--only-not-uploaded] [--skip-existing]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

from photos_fix import PHOTOS_DB, PHOTOS_ORIGINALS
from photos_fix.db import (
    check_photos_running,
    get_all_assets,
    get_icloud_status,
    open_db,
)
from photos_fix.export import export_batch
from photos_fix.fixer import FixStatus, fix_batch
from photos_fix.health import run_health_check
from photos_fix.icloud import get_not_uploaded
from photos_fix.log import configure_logging, get_console, get_logger
from photos_fix.report import (
    write_export_report,
    write_fix_report,
    write_health_report,
    write_icloud_report,
    write_scan_report,
)
from photos_fix.scanner import ScanResult, Status, scan_library

log = get_logger(__name__)


def _make_progress() -> Progress:
    """Barra de progreso rich con la configuración estándar del proyecto."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=get_console(),
    )


def _db_and_originals(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.library:
        lib = Path(args.library)
        return lib / "database" / "Photos.sqlite", lib / "originals"
    return PHOTOS_DB, PHOTOS_ORIGINALS


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> None:
    db_path, originals_dir = _db_and_originals(args)
    output_dir = Path(args.output)

    filter_size = None
    if args.filter_size:
        try:
            w, h = args.filter_size.lower().split("x")
            filter_size = (int(w), int(h))
        except ValueError:
            log.error(
                "--filter-size debe tener formato WxH",
                ejemplo="3264x2448",
                valor=args.filter_size,
            )
            sys.exit(1)

    check_photos_running()
    conn = open_db(db_path)
    assets = get_all_assets(conn, filter_size=filter_size)
    conn.close()

    log.info("Escaneando biblioteca", total=len(assets))
    if filter_size:
        log.info("Filtro activo", width=filter_size[0], height=filter_size[1])

    with _make_progress() as progress:
        task = progress.add_task("Escaneando...", total=len(assets))

        def on_progress(current, total, result):
            progress.update(task, completed=current)

        results = scan_library(assets, originals_dir, progress_callback=on_progress)

    counts = Counter(r.status.value for r in results)
    print("\nResultados:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    if not results:
        log.warning("No se encontraron fotos")
        return

    paths = write_scan_report(results, output_dir, fmt=args.format)
    for p in paths:
        log.info("Informe guardado", path=str(p))


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


def cmd_fix(args: argparse.Namespace) -> None:
    backup_dir = Path(args.backup_dir)
    dry_run: bool = args.dry_run

    input_path = Path(args.input) if args.input else None
    if not input_path:
        reports_dir = Path("reports")
        csvs = (
            sorted(reports_dir.glob("scan_*.csv"), reverse=True)
            if reports_dir.exists()
            else []
        )
        if not csvs:
            log.error(
                "No se encontró informe de scan",
                hint="Ejecuta primero: photos-fix scan",
            )
            sys.exit(1)
        input_path = csvs[0]
        log.info("Usando informe", path=str(input_path))

    scan_results = _load_scan_csv(input_path)
    candidates = [r for r in scan_results if r.status == Status.SWAP_CONFIRMED]

    if not candidates:
        log.warning("No hay fotos con SWAP_CONFIRMED — nada que corregir")
        return

    log.info("Fotos a corregir", total=len(candidates))

    if dry_run:
        log.info("Modo dry-run — no se modificará ningún archivo")
    else:
        log.info("Backup en", dir=str(backup_dir))
        confirm = input('\nEscribe "CONFIRMAR" para continuar: ')
        if confirm.strip() != "CONFIRMAR":
            print("Cancelado.")
            sys.exit(0)

    check_photos_running()

    with _make_progress() as progress:
        task = progress.add_task("Corrigiendo...", total=len(candidates))

        def on_progress(current, total, result):
            progress.update(task, completed=current)

        results = fix_batch(
            candidates, backup_dir, dry_run=dry_run, progress_callback=on_progress
        )

    counts = Counter(r.fix_status.value for r in results)
    print("\nResultados:")
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
    for p in paths:
        log.info("Informe guardado", path=str(p))


# ---------------------------------------------------------------------------
# icloud
# ---------------------------------------------------------------------------


def cmd_icloud(args: argparse.Namespace) -> None:
    db_path, originals_dir = _db_and_originals(args)
    output_dir = Path(args.output)

    check_photos_running()
    conn = open_db(db_path)
    rows = get_icloud_status(conn)
    conn.close()

    results = get_not_uploaded(rows, originals_dir)
    log.info("Fotos no subidas a iCloud", total=len(results))

    if not results:
        return

    paths = write_icloud_report(results, output_dir, fmt=args.format)
    for p in paths:
        log.info("Informe guardado", path=str(p))


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> None:
    db_path, originals_dir = _db_and_originals(args)
    output_dir = Path(args.output)

    check_photos_running()
    conn = open_db(db_path)
    assets = get_all_assets(conn)
    icloud_rows = get_icloud_status(conn)
    conn.close()

    log.info("Analizando biblioteca", total=len(assets), aviso="puede tardar 10-20 min")

    with _make_progress() as progress:
        task = progress.add_task("Analizando...", total=len(assets))

        def on_progress(current, total, result):
            progress.update(task, completed=current)

        report = run_health_check(
            assets,
            icloud_rows,
            originals_dir=originals_dir,
            progress_callback=on_progress,
        )

    summary = report.summary()
    print("\n── Resumen de salud de la biblioteca ──────────────────────")
    print(f"  Total fotos escaneadas : {summary['total_fotos']}")
    print(f"  OK                     : {summary['ok']}")
    print()
    print(
        f"  ⚠  SWAP_CONFIRMED      : {summary['swap_confirmed']}  ← corregibles con 'fix'"
    )
    print(f"  ⚠  SUSPECT             : {summary['suspect']}")
    print(f"  ⚠  LOCAL_MISSING       : {summary['local_missing']}  ← solo en iCloud")
    print(f"  ⚠  UNREADABLE          : {summary['unreadable']}")
    print(f"  ⚠  ZERO_BYTE           : {summary['zero_byte']}  ← archivos vacíos")
    print(f"  ⚠  NO_EXIF             : {summary['no_exif']}")
    print(
        f"  ⚠  NOT_UPLOADED        : {summary['not_uploaded']}  ← no subidas a iCloud"
    )
    print(
        f"  ⚠  ORPHANS             : {summary['orphans']}  ← archivos sin entrada en DB"
    )
    print("────────────────────────────────────────────────────────────")

    if not report.has_issues():
        log.info("Sin problemas detectados")
    else:
        log.warning("Se encontraron problemas — revisa los informes generados")

    paths = write_health_report(report, output_dir, fmt=args.format)
    for p in paths:
        log.info("Informe guardado", path=str(p))


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> None:
    db_path, originals_dir = _db_and_originals(args)
    output_dir = Path(args.output)
    only_not_uploaded: bool = args.only_not_uploaded
    skip_existing: bool = not args.no_skip_existing
    report_dir = Path(args.report_dir)

    check_photos_running()
    conn = open_db(db_path)
    assets = get_all_assets(conn)
    not_uploaded_uuids: set[str] | None = None

    if only_not_uploaded:
        icloud_rows = get_icloud_status(conn)
        icloud_results = get_not_uploaded(icloud_rows, originals_dir)
        not_uploaded_uuids = {r.uuid for r in icloud_results}
        log.info(
            "Exportando solo fotos no subidas a iCloud",
            total=len(not_uploaded_uuids),
        )
    else:
        log.info("Exportando fotos", total=len(assets), destino=str(output_dir))

    conn.close()

    confirm = input('\nEscribe "CONFIRMAR" para iniciar la copia: ')
    if confirm.strip() != "CONFIRMAR":
        print("Cancelado.")
        sys.exit(0)

    export_total = (
        len(not_uploaded_uuids)
        if only_not_uploaded and not_uploaded_uuids
        else len(assets)
    )
    with _make_progress() as progress:
        task = progress.add_task("Exportando...", total=export_total)

        def on_progress(current, total, result):
            progress.update(task, completed=current)

        results = export_batch(
            assets,
            output_dir,
            originals_dir=originals_dir,
            only_not_uploaded=only_not_uploaded,
            not_uploaded_uuids=not_uploaded_uuids,
            skip_existing=skip_existing,
            progress_callback=on_progress,
        )

    counts = Counter(r.status.value for r in results)
    print("\nResultados:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    errors = [r for r in results if r.status.value == "ERROR"]
    if errors:
        print(f"\nErrores ({len(errors)}):")
        for r in errors[:10]:
            print(f"  {r.filename}: {r.error}")

    paths = write_export_report(results, report_dir, fmt="both")
    for p in paths:
        log.info("Informe guardado", path=str(p))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_scan_csv(path: Path) -> list[ScanResult]:
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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="photos-fix",
        description="Diagnóstico y corrección de metadatos EXIF en macOS Photos",
    )
    parser.add_argument("--version", action="version", version="photos-fix 0.1.0")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Incluir módulo, función y línea en los logs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_logs",
        help="Logs en formato JSON (para pipes / CI)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- scan ---
    p_scan = sub.add_parser(
        "scan", help="Detectar fotos con dimensiones EXIF incorrectas"
    )
    p_scan.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_scan.add_argument(
        "--filter-size", metavar="WxH", help="Filtrar por tamaño (ej: 3264x2448)"
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

    # --- health ---
    p_health = sub.add_parser("health", help="Diagnóstico integral de la biblioteca")
    p_health.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_health.add_argument(
        "--output", default="reports", help="Directorio de salida (default: reports/)"
    )
    p_health.add_argument("--format", choices=["csv", "json", "both"], default="both")

    # --- export ---
    p_export = sub.add_parser(
        "export", help="Exportar originales a un directorio plano"
    )
    p_export.add_argument("--library", help="Ruta a la biblioteca .photoslibrary")
    p_export.add_argument(
        "--output", required=True, help="Directorio destino de la exportación"
    )
    p_export.add_argument(
        "--only-not-uploaded",
        action="store_true",
        help="Exportar solo fotos no subidas a iCloud",
    )
    p_export.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Sobreescribir archivos que ya existen en destino",
    )
    p_export.add_argument(
        "--report-dir",
        default="reports",
        help="Directorio donde guardar el informe (default: reports/)",
    )

    args = parser.parse_args()
    configure_logging(verbose=args.verbose, enable_json=args.json_logs)

    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "fix":
        cmd_fix(args)
    elif args.command == "icloud":
        cmd_icloud(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
