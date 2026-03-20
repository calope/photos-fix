# photos-fix

Diagnóstico y corrección de metadatos EXIF en bibliotecas de macOS Photos.

Detecta fotos cuyas dimensiones EXIF (`PixelXDimension`/`PixelYDimension`) tienen el
ancho y alto intercambiados y las corrige **sin tocar el pixel data** — solo
reescribe el bloque EXIF del archivo JPEG. También diagnostica fotos no subidas a iCloud.

---

## Índice

- [Requisitos previos](#requisitos-previos)
- [Instalación](#instalación)
- [Configuración inicial](#configuración-inicial)
- [Uso](#uso)
  - [scan — detectar problemas](#scan--detectar-problemas)
  - [fix — corregir metadatos](#fix--corregir-metadatos)
  - [icloud — diagnóstico de subida](#icloud--diagnóstico-de-subida)
- [Referencia de estados](#referencia-de-estados)
- [Referencia de columnas CSV](#referencia-de-columnas-csv)
- [Flujo completo recomendado](#flujo-completo-recomendado)
- [Recuperación de emergencia](#recuperación-de-emergencia)
- [Desarrollo](#desarrollo)

---

## Requisitos previos

- macOS Monterey 12.7+ (probado en 12.7.6)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — gestor de dependencias
- **Full Disk Access** concedido a Terminal.app (obligatorio para leer la DB de Photos)

### Instalar uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Conceder Full Disk Access a Terminal.app

> **Sin este paso, el proyecto no puede leer la base de datos de Photos y fallará.**

1. Abrir **Ajustes del Sistema**
2. Ir a **Privacidad y Seguridad → Acceso completo al disco**
3. Hacer clic en **+** y añadir **Terminal.app** (en `/Applications/Utilities/`)
4. Cerrar y reabrir Terminal

---

## Instalación

```bash
git clone https://github.com/calope/photos-fix
cd photos-fix
uv sync
```

Esto crea `.venv/` e instala todas las dependencias (incluidas las de desarrollo).

Para instalar solo las dependencias de producción (sin herramientas dev):

```bash
uv sync --no-dev
```

Verificar que la instalación es correcta:

```bash
uv run photos-fix --version
# photos-fix 0.1.0
```

---

## Configuración inicial

### Pre-commit hooks

Instala los hooks para que black, isort, commitizen y gitleaks se ejecuten
automáticamente en cada commit:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

Verificar que los hooks están activos:

```bash
uv run pre-commit run --all-files
```

### Ruta de la biblioteca

Por defecto el proyecto usa `~/Pictures/Photos Library.photoslibrary`.
Si tu biblioteca está en otra ubicación, usa `--library` en todos los comandos:

```bash
uv run photos-fix scan --library "/Volumes/Externo/Mi Biblioteca.photoslibrary"
```

---

## Uso

> **Importante: cierra la app Photos antes de ejecutar cualquier comando.**
>
> El script detecta si Photos está abierto y sale con error si es así.

### scan — detectar problemas

Analiza la biblioteca en **modo solo lectura**. No modifica ningún archivo.

```bash
# Scan completo de todas las fotos (~10-20 min para 50k fotos)
uv run photos-fix scan

# Filtrar por un tamaño concreto (útil si recuerdas el tamaño de las fotos afectadas)
uv run photos-fix scan --filter-size 3264x2448

# Generar solo JSON (sin CSV)
uv run photos-fix scan --format json

# Guardar informes en un directorio específico
uv run photos-fix scan --output /tmp/mis-informes
```

**Salida:**

```
Escaneando 51432 fotos...
[████████████████████████████████████████] 51432/51432

Resultados:
  LOCAL_MISSING: 312
  NO_EXIF: 48
  OK: 50891
  SWAP_CONFIRMED: 127
  SUSPECT: 14
  UNREADABLE: 40

Informe guardado en:
  reports/scan_20240315_142301.csv
  reports/scan_20240315_142301.json
```

**Opciones:**

| Opción | Default | Descripción |
|--------|---------|-------------|
| `--library PATH` | `~/Pictures/Photos Library.photoslibrary` | Ruta a la biblioteca |
| `--filter-size WxH` | — | Filtrar por tamaño en DB (ej: `3264x2448`) |
| `--output DIR` | `reports/` | Directorio donde guardar los informes |
| `--format` | `both` | Formato: `csv`, `json` o `both` |

---

### fix — corregir metadatos

Corrige las fotos con estado `SWAP_CONFIRMED` intercambiando `PixelXDimension` ↔
`PixelYDimension` en el EXIF. **El pixel data no se toca.**

#### Paso 1 — Dry-run obligatorio (simula sin modificar)

```bash
uv run photos-fix fix --dry-run
```

Muestra cuántas fotos se corregirían y verifica que los backups serían viables,
sin modificar ningún archivo.

#### Paso 2 — Corrección real

```bash
uv run photos-fix fix --backup-dir ./backups/
```

El script:
1. Muestra cuántas fotos va a modificar
2. Pide escribir `CONFIRMAR` para continuar
3. Para cada foto: hace backup → corrige EXIF → verifica integridad
4. Si algo falla, restaura el backup automáticamente

```
Fotos a corregir: 127
Backup en: backups/

Escribe "CONFIRMAR" para continuar: CONFIRMAR

[████████████████████████████████████████] 127/127

Resultados:
  FIXED: 125
  NO_EXIF_DIMS: 2

Informe guardado en:
  reports/fix_20240315_143012.csv
  reports/fix_20240315_143012.json
```

**Opciones:**

| Opción | Default | Descripción |
|--------|---------|-------------|
| `--library PATH` | `~/Pictures/Photos Library.photoslibrary` | Ruta a la biblioteca |
| `--input FILE` | CSV más reciente en `reports/` | CSV generado por `scan` |
| `--backup-dir DIR` | `backups/` | Directorio donde guardar los originales antes de modificar |
| `--output DIR` | `reports/` | Directorio donde guardar el informe del fix |
| `--dry-run` | — | Simula sin modificar nada |

---

### icloud — diagnóstico de subida

Identifica fotos disponibles localmente pero **no subidas a iCloud**.
Solo lectura — no modifica nada.

```bash
uv run photos-fix icloud
```

```
Fotos no subidas a iCloud: 23

Informe guardado en:
  reports/icloud_20240315_144500.csv
  reports/icloud_20240315_144500.json
```

**Opciones:**

| Opción | Default | Descripción |
|--------|---------|-------------|
| `--library PATH` | `~/Pictures/Photos Library.photoslibrary` | Ruta a la biblioteca |
| `--output DIR` | `reports/` | Directorio donde guardar los informes |
| `--format` | `both` | Formato: `csv`, `json` o `both` |

---

## Referencia de estados

### Estados del scan

| Estado | Descripción | ¿Se corrige? |
|--------|-------------|--------------|
| `SWAP_CONFIRMED` | Dimensiones EXIF exactamente intercambiadas respecto al pixel data | ✅ Sí |
| `SUSPECT` | Orientación opuesta entre pixel data y DB, sin EXIF de dimensiones | ⚠️ Revisar manualmente |
| `OK` | Sin problemas detectados | — |
| `NO_EXIF` | Sin bloque EXIF (fotos muy antiguas o procesadas con herramientas que lo eliminan) | — |
| `LOCAL_MISSING` | Original no descargado de iCloud (solo miniatura en local) | — |
| `UNREADABLE` | Archivo corrupto, formato no soportado o HEIC sin `pillow-heif` | — |

### Estados del fix

| Estado | Descripción |
|--------|-------------|
| `FIXED` | Corrección aplicada correctamente |
| `DRY_RUN` | Simulación: se habría corregido |
| `SKIPPED` | No era `SWAP_CONFIRMED`, se omitió |
| `NO_EXIF_DIMS` | No tiene `PixelXDimension`/`PixelYDimension` en EXIF |
| `BACKUP_FAILED` | Error al crear el backup (no se modificó el original) |
| `RESTORED` | El fix falló, se restauró el backup automáticamente |
| `ERROR` | Error inesperado |

### Estados iCloud

| `remote_label` | Descripción |
|----------------|-------------|
| `Disponible en iCloud` | Subida completa |
| `No disponible en iCloud` | Nunca se subió o falló la subida |
| `Estado desconocido` | Estado de sincronización indefinido |

---

## Referencia de columnas CSV

### scan_*.csv

| Columna | Descripción |
|---------|-------------|
| `uuid` | Identificador único del asset en Photos |
| `filename` | Nombre del archivo original |
| `path` | Ruta absoluta al archivo original |
| `status` | Estado detectado (ver tabla de estados) |
| `w_real` / `h_real` | Dimensiones reales leídas del pixel data (Pillow) |
| `w_exif` / `h_exif` | Dimensiones en los metadatos EXIF del archivo |
| `w_db` / `h_db` | Dimensiones almacenadas en la base de datos de Photos |
| `error` | Descripción del error si `status` es `UNREADABLE` o similar |

### fix_*.csv

| Columna | Descripción |
|---------|-------------|
| `uuid` | Identificador único del asset |
| `filename` | Nombre del archivo |
| `path` | Ruta absoluta al archivo modificado |
| `fix_status` | Resultado del fix (ver tabla de estados) |
| `error` | Descripción del error si el fix falló |

### icloud_*.csv

| Columna | Descripción |
|---------|-------------|
| `uuid` | Identificador único del asset |
| `filename` | Nombre del archivo |
| `path` | Ruta absoluta al original |
| `local_availability` | `1` = disponible localmente |
| `remote_availability` | `1` = disponible en iCloud |
| `remote_label` | Descripción legible del estado remoto |
| `cloud_local_state` | Código numérico del estado de sincronización |
| `state_label` | Descripción legible: `Subida completa`, `Error de subida`, etc. |

---

## Flujo completo recomendado

```bash
# 0. Prerrequisitos
#    - Full Disk Access concedido a Terminal.app
#    - Photos cerrado (Cmd+Q)

# 1. Clonar e instalar
git clone https://github.com/calope/photos-fix
cd photos-fix
uv sync

# 2. Análisis completo (solo lectura, ~10-20 min)
uv run photos-fix scan

# 2b. Si recuerdas el tamaño de las fotos afectadas (más rápido)
uv run photos-fix scan --filter-size 3264x2448

# 3. Diagnóstico de subidas a iCloud
uv run photos-fix icloud

# 4. Revisar los informes antes de tocar nada
open reports/   # abre en Finder
# Abrir el CSV en Numbers y revisar las SWAP_CONFIRMED

# 5. Dry-run: ver qué se modificaría sin tocar nada
uv run photos-fix fix --dry-run

# 6. Corrección real (pide escribir CONFIRMAR)
uv run photos-fix fix --backup-dir ./backups/

# 7. Verificar resultado
uv run photos-fix scan --filter-size 3264x2448
# SWAP_CONFIRMED debería ser 0

# 8. Reabrir Photos y verificar visualmente
open -a Photos
```

---

## Recuperación de emergencia

Los backups se guardan en `backups/` con el nombre `{UUID}_{filename}`.

Para restaurar un archivo manualmente:

```bash
# Encontrar el backup
ls backups/ | grep nombre-foto.jpg

# Restaurar (ajusta la ruta según tu biblioteca)
cp backups/XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX_foto.jpg \
   ~/Pictures/Photos\ Library.photoslibrary/originals/X/UUID/foto.jpg
```

Para restaurar todos los backups a la vez (revertir el fix completo):

```bash
# PRECAUCIÓN: esto sobreescribe los archivos corregidos con los originales
for f in backups/*.jpg; do
  uuid=$(basename "$f" | cut -d_ -f1)
  filename=$(basename "$f" | cut -d_ -f2-)
  dir_letter=${uuid:0:1}
  dest=~/Pictures/Photos\ Library.photoslibrary/originals/${dir_letter^^}/${uuid}/${filename}
  [ -f "$dest" ] && cp "$f" "$dest" && echo "Restaurado: $filename"
done
```

---

## Desarrollo

### Herramientas

| Herramienta | Versión | Uso |
|-------------|---------|-----|
| [uv](https://docs.astral.sh/uv/) | ≥0.5 | Gestión de dependencias y entorno virtual |
| [black](https://black.readthedocs.io/) | ≥24.0 | Formateo de código |
| [isort](https://pycqa.github.io/isort/) | ≥5.13 | Ordenación de imports |
| [commitizen](https://commitizen-tools.github.io/commitizen/) | ≥4.0 | Commits convencionales y versionado |
| [pre-commit](https://pre-commit.com/) | ≥4.0 | Hooks automáticos en cada commit |
| [pytest](https://pytest.org/) | ≥8.0 | Tests |
| [gitleaks](https://github.com/gitleaks/gitleaks) | ≥8.30 | Detección de secretos en commits |

### Instalar entorno de desarrollo

```bash
uv sync                                              # instala todas las dependencias (incluyendo dev)
uv run pre-commit install                            # hook pre-commit (black, isort, gitleaks)
uv run pre-commit install --hook-type commit-msg     # hook commit-msg (commitizen)
```

### Comandos habituales

```bash
# Formatear código
uv run black .
uv run isort .

# Verificar formato sin modificar
uv run black --check .
uv run isort --check-only .

# Ejecutar tests
uv run pytest

# Ejecutar tests con detalle
uv run pytest -v

# Bump de versión (patch/minor/major)
uv run cz bump --increment PATCH
uv run cz bump --increment MINOR
```

### Conventional Commits

El proyecto usa [Conventional Commits](https://www.conventionalcommits.org/).
commitizen valida el formato del mensaje en cada commit.

```bash
# Commit interactivo guiado
uv run cz commit

# O manual con el formato correcto
git commit -m "fix(scanner): corregir detección de HEIC sin pillow-heif"
git commit -m "feat(cli): añadir subcomando health para diagnóstico completo"
git commit -m "chore(deps): actualizar Pillow a 11.0"
```

**Formato:** `tipo(scope): descripción en imperativo`

| Tipo | Cuándo usarlo |
|------|---------------|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de bug |
| `docs` | Solo documentación |
| `refactor` | Refactor sin cambio funcional |
| `test` | Añadir o modificar tests |
| `chore` | Mantenimiento, dependencias, CI |
| `perf` | Mejora de rendimiento |

### Pre-commit hooks

Al hacer `git commit`, se ejecutan automáticamente:

1. **black** — formatea el código
2. **isort** — ordena los imports
3. **commitizen** — valida el mensaje de commit
4. **gitleaks** — detecta secretos o credenciales hardcodeadas

Si algún hook falla, el commit se cancela. Corrígelo y vuelve a hacer commit.

### Estructura del proyecto

```
photos-fix/
├── photos_fix/
│   ├── __init__.py       # constantes de ruta, __version__
│   ├── cli.py            # interfaz de línea de comandos (argparse)
│   ├── db.py             # acceso SQLite read-only a Photos.sqlite
│   ├── scanner.py        # detección de dimensiones EXIF incorrectas
│   ├── fixer.py          # corrección EXIF con backup y verificación
│   ├── icloud.py         # diagnóstico de fotos no subidas
│   └── report.py         # generación de informes CSV y JSON
├── tests/
│   ├── test_scanner.py   # tests con imágenes JPEG generadas en memoria
│   └── test_fixer.py     # tests del pipeline de corrección
├── .pre-commit-config.yaml
├── pyproject.toml
├── uv.lock
└── README.md
```

### Nota sobre HEIC

Las fotos HEIC (iPhone reciente) necesitan `pillow-heif` para ser leídas.
Sin él, se marcan como `UNREADABLE` y se saltan.
Las fotos afectadas por el bug de dimensiones son JPEG (hace 10-15 años),
por lo que normalmente no es necesario instalarlo.

```bash
uv add pillow-heif
```
