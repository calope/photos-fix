from pathlib import Path

__version__ = "0.1.0"

PHOTOS_LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"
PHOTOS_DB = PHOTOS_LIBRARY / "database" / "Photos.sqlite"
PHOTOS_ORIGINALS = PHOTOS_LIBRARY / "originals"
