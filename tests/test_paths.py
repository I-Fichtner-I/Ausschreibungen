from __future__ import annotations

from pathlib import Path

import pytest

from tender_ai.sources.base import safe_document_path


@pytest.mark.parametrize(
    "source_id",
    ["../../evil", "..\\..\\evil", "/etc/passwd", "a/b/c", "", ".", "..", "x" * 500],
)
def test_path_never_escapes_base(tmp_path: Path, source_id: str):
    base = tmp_path / "docs"
    target = safe_document_path(base, "ted", source_id, ".pdf")
    assert base.resolve() in target.parents
    assert target.parent == base.resolve() / "ted"
    assert target.suffix == ".pdf"
    assert len(target.name) <= 140


def test_clean_ids_stay_readable(tmp_path: Path):
    target = safe_document_path(tmp_path, "ted", "00123456-2026", ".pdf")
    assert target.name == "00123456-2026.pdf"


def test_different_unsafe_ids_do_not_collide(tmp_path: Path):
    left = safe_document_path(tmp_path, "src", "a/b", ".xml")
    right = safe_document_path(tmp_path, "src", "a\\b", ".xml")
    assert left != right
    assert left.name.startswith("a_b")


def test_suffix_is_sanitised(tmp_path: Path):
    target = safe_document_path(tmp_path, "src", "id", "../.pdf")
    assert target.name == "id..pdf" or target.name.endswith(".pdf")
    assert tmp_path.resolve() in target.parents
