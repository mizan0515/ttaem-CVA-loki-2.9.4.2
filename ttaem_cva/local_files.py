"""Reject link/reparse redirection for private files and export destinations."""
from pathlib import Path
import stat

def plain_path(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked/reparse data paths are not supported')
    return path

def read_json(path, maximum=16_000_000):
    import json
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Invalid or oversized local JSON')
    return json.loads(path.read_text(encoding='utf-8'))
