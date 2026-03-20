"""
Configuración de structlog + rich para photos-fix.

Copiado y adaptado de stlog-dconf (workspace MutuaTFE 2.0).
Autónomo — sin dependencia del paquete interno stlog-dconf.

Uso:
    from photos_fix.log import configure_logging, get_logger, get_console

    configure_logging()                    # RichHandler con colores
    configure_logging(enable_json=True)    # JSONRenderer (para pipes / CI)
    configure_logging(verbose=True)        # incluye módulo y línea de código

    log = get_logger(__name__)
    log.info("Escaneando biblioteca", total=51432)
    log.warning("Fotos con problemas", swap_confirmed=127)
    log.error("Sin acceso a la base de datos", path=str(db_path))

La instancia de Console compartida (get_console()) debe usarse también en
rich.progress.Progress para evitar conflictos de pintado en el terminal:

    from rich.progress import Progress
    from photos_fix.log import get_console

    with Progress(console=get_console()) as progress:
        ...
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from rich.console import Console
from rich.logging import RichHandler

_console = Console()


def get_console() -> Console:
    """Devuelve la instancia de Console compartida con el logging handler."""
    return _console


def _plain_renderer(logger: Any, method: str, event_dict: dict) -> str:
    """
    Renderer minimalista para RichHandler.

    Extrae el evento como mensaje y formatea el resto de claves como k=v.
    Elimina las claves que RichHandler ya muestra (timestamp, level, logger).
    """
    event = event_dict.pop("event", "")
    for key in ("timestamp", "level", "logger"):
        event_dict.pop(key, None)
    if event_dict:
        extras = "  " + "  ".join(f"{k}={v}" for k, v in event_dict.items())
    else:
        extras = ""
    return f"{event}{extras}"


def configure_logging(
    level: int = logging.INFO,
    enable_json: bool = False,
    verbose: bool = False,
    force: bool = False,
) -> None:
    """
    Configura structlog + stdlib logging.

    Idempotente: no hace nada si ya está configurado (salvo force=True).

    Args:
        level:       Nivel de log raíz (default: INFO).
        enable_json: Usar JSONRenderer en vez de RichHandler.
        verbose:     Incluir módulo, función y línea en cada mensaje.
        force:       Resetear y reconfigurar (útil para tests).
    """
    if structlog.is_configured() and not force:
        return

    if force:
        structlog.reset_defaults()

    callsite_params = (
        {
            structlog.processors.CallsiteParameter.MODULE,
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        }
        if verbose
        else set()
    )

    shared_processors: list = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if callsite_params:
        shared_processors.append(
            structlog.processors.CallsiteParameterAdder(callsite_params)
        )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    if enable_json:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            keep_stack_info=True,
        )
        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(formatter)
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                _plain_renderer,
            ],
            keep_stack_info=True,
        )
        handler = RichHandler(
            console=_console,
            rich_tracebacks=True,
            show_time=True,
            show_level=True,
            show_path=verbose,
        )
        handler.setFormatter(formatter)

    handler.setLevel(logging.NOTSET)
    root.addHandler(handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Devuelve un logger structlog vinculado al nombre dado."""
    return structlog.get_logger(name)
