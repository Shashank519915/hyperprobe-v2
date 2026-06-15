from pathlib import Path

from agent.breakpoints import normalize_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_normalize_path_resolves_relative_to_absolute():
    rel = REPO_ROOT / "target" / "engines" / "addition.py"
    assert normalize_path("target/engines/addition.py") == normalize_path(rel)


def test_normalize_path_string_and_path_equivalent():
    target_file = REPO_ROOT / "agent" / "models.py"
    assert normalize_path(str(target_file)) == normalize_path(target_file)


def test_normalize_path_collapses_dot_segments():
    messy = REPO_ROOT / "target" / ".." / "target" / "engines" / "addition.py"
    clean = REPO_ROOT / "target" / "engines" / "addition.py"
    assert normalize_path(messy) == normalize_path(clean)


def test_normalize_path_same_file_twice_is_stable():
    target_file = REPO_ROOT / "target" / "handlers.py"
    first = normalize_path(target_file)
    second = normalize_path(target_file)
    assert first == second
