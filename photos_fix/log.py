"""
Configuración de structlog para photos-fix.

Copiado y adaptado de stlog-dconf (workspace MutuaTFE 2.0).
Autónomo — sin dependencia del paquete interno stlog-dconf.

Uso:
    from photos_fix.log import configure_logging, get_logger

    configure_logging()                    # ConsoleRenderer con colores
    configure_logging(enable_json=True)    # JSONRenderer (para pipes / CI)
    configure_logging(verbose=True)        # incluye módulo y línea de código

    log = get_logger(__name__)
    log.info("Escaneando biblioteca", total=51432)
    log.warning("Fotos con problemas", swap_confirmed=127)
    log.error("Sin acceso a la base de datos", path=str(db_path))
"""

from __future__ import annotations

import logging

import structlog


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
        enable_json: Usar JSONRenderer en vez de ConsoleRenderer.
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

    if enable_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(
            exception_formatter=structlog.dev.better_traceback
        )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        keep_stack_info=True,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(logging.NOTSET)
    root.addHandler(handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Devuelve un logger structlog vinculado al nombre dado."""
    return structlog.get_logger(name)
