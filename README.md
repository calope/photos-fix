# photos-fix

Diagnóstico y corrección de metadatos EXIF en macOS Photos.

Detecta fotos cuyas dimensiones EXIF (PixelXDimension/PixelYDimension) tienen el
ancho y alto intercambiados, y las corrige **sin tocar el pixel data** — solo
reescribe el bloque EXIF del archivo JPEG.

## Requisitos

- macOS (probado en Monterey 12.7)
- Python 3.11+
- **Full Disk Access** concedido a Terminal.app

### Conceder Full Disk Access

Ajustes del Sistema → Privacidad y Seguridad → Acceso completo al disco → añadir Terminal.app

## Instalación

```bash
git clone https://github.com/tuusuario/photos-fix
cd photos-fix
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Uso

**Cerrar Photos antes de ejecutar cualquier comando.**

### 1. Scan (solo lectura — seguro)

```bash
# Scan completo (~10-20 min para 50k fotos)
photos-fix scan

# Filtrar por tamaño concreto (más rápido si recuerdas el tamaño afectado)
photos-fix scan --filter-size 3264x2448

# Solo JSON
photos-fix scan --format json
```

Genera `reports/scan_YYYYMMDD_HHMMSS.csv` y `.json`.

### 2. Diagnóstico iCloud

```bash
photos-fix icloud
```

Genera `reports/icloud_YYYYMMDD_HHMMSS.csv` con fotos disponibles localmente
pero no subidas a iCloud.

### 3. Revisar resultados

Abre el CSV en Numbers o Excel. Las columnas clave:

| Columna | Descripción |
|---------|-------------|
| `status` | Ver tabla de estados |
| `w_real` / `h_real` | Dimensiones reales del pixel data |
| `w_exif` / `h_exif` | Dimensiones en metadatos EXIF |
| `w_db` / `h_db` | Dimensiones que Photos tiene en su base de datos |

### Estados

| Estado | Significado |
|--------|-------------|
| `SWAP_CONFIRMED` | Dimensiones EXIF exactamente intercambiadas → se corrige |
| `SUSPECT` | Orientación opuesta entre pixel data y DB, sin EXIF de dimensiones |
| `OK` | Sin problemas detectados |
| `NO_EXIF` | Sin bloque EXIF (fotos muy antiguas o procesadas) |
| `LOCAL_MISSING` | Original no descargado de iCloud |
| `UNREADABLE` | Archivo corrupto o formato no soportado (HEIC sin pillow-heif) |

### 4. Dry-run (simula sin modificar)

```bash
photos-fix fix --dry-run
```

### 5. Corrección real

```bash
photos-fix fix --backup-dir ./backups/
```

El script pide escribir `CONFIRMAR` antes de modificar cualquier archivo.
Los originales se copian a `backups/` antes de cada modificación.

Si el fix falla en alguna foto, se restaura el backup automáticamente.

### Opciones avanzadas

```bash
# Biblioteca en ruta no estándar
photos-fix scan --library /Volumes/Disco/MiBiblioteca.photoslibrary

# Usar un CSV específico para el fix
photos-fix fix --input reports/scan_20240115_103000.csv
```

## Nota sobre HEIC

Las fotos HEIC (iPhone reciente) requieren el paquete adicional `pillow-heif`.
Sin él, se marcan como `UNREADABLE` y se saltan. Las fotos afectadas por el
bug descrito son JPEG (hace 10-15 años), por lo que normalmente no es necesario.

```bash
pip install pillow-heif
```

## Recuperación de emergencia

Si algo falla, los backups están en `backups/` con el nombre `{UUID}_{filename}`.
Para restaurar manualmente:

```bash
cp backups/XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX_foto.jpg \
   ~/Pictures/Photos\ Library.photoslibrary/originals/X/UUID/foto.jpg
```
