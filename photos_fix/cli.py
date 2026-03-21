"""
CLI principal: photos-fix scan | fix | icloud | health | export | album

Uso:
  photos-fix scan    [--library PATH] [--filter-size WxH] [--output DIR] [--format csv|json|both]
  photos-fix fix     [--library PATH] [--input FILE] [--backup-dir DIR] [--dry-run]
  photos-fix icloud  [--library PATH] [--output DIR] [--format csv|json|both]
  photos-fix health  [--library PATH] [--output DIR] [--format csv|json|both]
  photos-fix export  [--library PATH] [--output DIR] [--only-not-uploaded] [--skip-existing]
  photos-fix album   [--input FILE] [--filter ESTADOS] [--name NOMBRE] [--output FILE] [--run]
  photos-fix quarantine [--input FILE] [--dest DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import shutil
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
    candidates = [
        r
        for r in scan_results
        if r.status
        in (Status.SWAP_CONFIRMED, Status.IPHOTO_ROTATED, Status.DEFORMED)
    ]

    if not candidates:
        log.warning("No hay fotos corregibles — nada que corregir")
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
            detect_rotation=args.detect_rotation,
        )

    summary = report.summary()
    print("\n── Resumen de salud de la biblioteca ──────────────────────")
    print(f"  Total fotos escaneadas : {summary['total_fotos']}")
    print(f"  OK                     : {summary['ok']}")
    print()
    print(
        f"  ⚠  SWAP_CONFIRMED      : {summary['swap_confirmed']}  ← corregibles con 'fix'"
    )
    print(
        f"  ⚠  IPHOTO_ROTATED      : {summary['iphoto_rotated']}  ← iPhoto 9 rotó píxeles"
    )
    print(
        f"  ⚠  DEFORMED            : {summary['deformed']}  ← deformación por gradient ratio"
    )
    print(
        f"  ⚠  ROTATED             : {summary['rotated']}  ← rotación incorrecta (face detection)"
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
# album
# ---------------------------------------------------------------------------


def cmd_album(args: argparse.Namespace) -> None:
    filters = {f.strip().upper() for f in args.filter.split(",")}
    input_path = Path(args.input) if args.input else None

    if not input_path:
        reports_dir = Path("reports")
        # Si el filtro incluye estados de health, buscar health_scan_*.csv
        health_states = {"SUSPECT", "SWAP_CONFIRMED", "IPHOTO_ROTATED", "DEFORMED", "NO_EXIF", "UNREADABLE", "ZERO_BYTE"}
        use_health = bool(filters & health_states)
        glob_pattern = "health_scan_*.csv" if use_health else "fix_*.csv"
        csvs = (
            sorted(reports_dir.glob(glob_pattern), reverse=True)
            if reports_dir.exists()
            else []
        )
        if not csvs:
            log.error(
                "No se encontró informe",
                hint="Ejecuta primero: photos-fix health o photos-fix fix",
            )
            sys.exit(1)
        input_path = csvs[0]
        log.info("Usando informe", path=str(input_path))

    uuids = []
    with open(input_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # Soportar tanto CSVs de fix (fix_status) como de health (status)
            status = row.get("fix_status") or row.get("status", "")
            if status.upper() in filters:
                uuids.append(row["uuid"])

    if not uuids:
        log.warning("No hay fotos con estados %s en el informe", filters)
        return

    log.info("Generando AppleScript", total=len(uuids))

    album_name = args.name
    items = ", ".join(f'(media item id "{u}")' for u in uuids)
    script = (
        'tell application "Photos"\n'
        "    activate\n"
        f'    set albumName to "{album_name}"\n'
        "    set matchingAlbums to (every album whose name = albumName)\n"
        "    if (count of matchingAlbums) > 0 then\n"
        "        set fixedAlbum to item 1 of matchingAlbums\n"
        "    else\n"
        "        set fixedAlbum to make new album named albumName\n"
        "    end if\n"
        f"    set theItems to {{{items}}}\n"
        "    add theItems to fixedAlbum\n"
        "end tell\n"
    )

    script_path = Path(args.output)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    log.info("Script guardado", path=str(script_path))

    if args.run:
        import subprocess

        log.info("Ejecutando en Photos.app...")
        result = subprocess.run(
            ["osascript", str(script_path)], capture_output=True, text=True
        )
        if result.returncode == 0:
            log.info("Álbum creado", name=album_name)
        else:
            log.error("Error de AppleScript", detalle=result.stderr.strip())
            sys.exit(1)
    else:
        log.info("Para crear el álbum ejecuta", cmd=f"osascript {script_path}")


# ---------------------------------------------------------------------------
# quarantine
# ---------------------------------------------------------------------------


def cmd_quarantine(args: argparse.Namespace) -> None:
    input_path = Path(args.input) if args.input else None
    if not input_path:
        reports_dir = Path("reports")
        csvs = (
            sorted(reports_dir.glob("health_orphans_*.csv"), reverse=True)
            if reports_dir.exists()
            else []
        )
        if not csvs:
            log.error(
                "No se encontró informe de orphans",
                hint="Ejecuta primero: photos-fix health",
            )
            sys.exit(1)
        input_path = csvs[0]
        log.info("Usando informe", path=str(input_path))

    dest = Path(args.dest)
    dry_run = args.dry_run

    orphans = []
    with open(input_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            orphans.append(row)

    if not orphans:
        log.warning("No hay archivos orphan en el informe")
        return

    total_size = sum(int(r.get("size_bytes", 0)) for r in orphans)
    log.info(
        "Orphans a mover",
        total=len(orphans),
        size_gb=f"{total_size / 1024 / 1024 / 1024:.1f}",
    )

    if dry_run:
        log.info("Modo dry-run — no se moverá ningún archivo")
        for r in orphans[:5]:
            log.info("  Movería", path=r["path"])
        if len(orphans) > 5:
            log.info(f"  ... y {len(orphans) - 5} más")
        return

    confirm = input('\nEscribe "CONFIRMAR" para continuar: ')
    if confirm.strip() != "CONFIRMAR":
        print("Cancelado.")
        return

    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    errors = 0

    for r in orphans:
        src = Path(r["path"])
        if not src.exists():
            errors += 1
            continue
        # Preservar estructura de directorios relativa a originals/
        try:
            rel = src.relative_to(
                src.parents[1]
            )  # directorio_hex/filename
        except ValueError:
            rel = Path(src.name)
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:
            log.error("Error moviendo", path=str(src), error=str(e))
            errors += 1

    log.info("Cuarentena completada", movidos=moved, errores=errors)
    log.info("Archivos en", dest=str(dest))


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
    p_health.add_argument(
        "--detect-rotation",
        action="store_true",
        help="Detectar rotación incorrecta via face detection (lento)",
    )

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

    # --- album ---
    p_album = sub.add_parser(
        "album",
        help="Crear álbum en Photos.app con las fotos corregidas",
    )
    p_album.add_argument(
        "--input", help="CSV generado por fix o health (default: más reciente en reports/)"
    )
    p_album.add_argument(
        "--filter",
        default="FIXED",
        help='Estados a incluir, separados por coma (default: "FIXED"). '
        "Valores: FIXED, SUSPECT, SWAP_CONFIRMED, IPHOTO_ROTATED, NO_EXIF, OK, etc.",
    )
    p_album.add_argument(
        "--name",
        default="Fotos corregidas EXIF",
        help='Nombre del álbum (default: "Fotos corregidas EXIF")',
    )
    p_album.add_argument(
        "--output",
        default="reports/create_album.applescript",
        help="Ruta del script generado (default: reports/create_album.applescript)",
    )
    p_album.add_argument(
        "--run",
        action="store_true",
        help="Ejecutar el script en Photos.app inmediatamente",
    )

    # --- quarantine ---
    p_quarantine = sub.add_parser(
        "quarantine",
        help="Mover archivos orphan a cuarentena",
    )
    p_quarantine.add_argument(
        "--input",
        help="CSV de orphans (default: más reciente en reports/)",
    )
    p_quarantine.add_argument(
        "--dest",
        default="quarantine",
        help='Carpeta destino (default: "quarantine/")',
    )
    p_quarantine.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo mostrar qué se movería",
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
    elif args.command == "album":
        cmd_album(args)
    elif args.command == "quarantine":
        cmd_quarantine(args)


if __name__ == "__main__":
    main()
