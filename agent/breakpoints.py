from pathlib import Path


def normalize_path(path: str | Path) -> str:
    """Canonical absolute path for file_line matching (ARCHITECTURE_V2 §5.6)."""
    return str(Path(path).resolve())
