"""
test_diff_parser.py — Tests for the unified diff parser.

Focuses on the correctness of line-number mapping (critical for posting
inline comments to the right position) and chunk splitting behaviour.
"""

from __future__ import annotations

import pytest

from app.services.diff_parser import (
    build_prompt_for_chunk,
    extract_chunks_from_files,
    parse_file_patch,
    split_chunk,
)


# ── parse_file_patch ──────────────────────────────────────────────────────────

def test_parse_identifies_additions(sample_patch):
    chunk = parse_file_patch("app/users.py", sample_patch)
    additions = [l for l in chunk.lines if l.is_addition]
    assert len(additions) > 0


def test_parse_file_path_preserved(sample_patch):
    chunk = parse_file_patch("src/auth.py", sample_patch)
    assert chunk.file_path == "src/auth.py"


def test_parse_patch_stored(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    assert chunk.patch == sample_patch


def test_diff_position_map_populated(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    # Every diff line should have an entry in the position map.
    for line in chunk.lines:
        assert line.number in chunk.diff_position_map


def test_diff_position_map_values_increase(sample_patch):
    """GitHub position must be monotonically increasing."""
    chunk = parse_file_patch("f.py", sample_patch)
    positions = [chunk.diff_position_map[l.number] for l in chunk.lines]
    assert positions == sorted(positions)


def test_addition_starts_with_plus(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    for line in chunk.lines:
        if line.is_addition:
            assert line.content.startswith("+")


def test_empty_patch_returns_empty_lines():
    chunk = parse_file_patch("f.py", "")
    assert chunk.lines == []


def test_context_lines_not_additions():
    patch = "@@ -1,3 +1,3 @@\n context line\n+added line\n-removed line"
    chunk = parse_file_patch("f.py", patch)
    context = [l for l in chunk.lines if l.content.startswith(" ")]
    assert all(not l.is_addition for l in context)


# ── split_chunk ───────────────────────────────────────────────────────────────

def test_split_no_split_when_small(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    result = split_chunk(chunk, max_lines=1000)
    assert len(result) == 1
    assert result[0] is chunk


def test_split_produces_multiple_chunks(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    if len(chunk.lines) < 2:
        pytest.skip("Patch too small to split")
    result = split_chunk(chunk, max_lines=2)
    assert len(result) > 1


def test_split_preserves_file_path(sample_patch):
    chunk = parse_file_patch("src/auth.py", sample_patch)
    for sub in split_chunk(chunk, max_lines=2):
        assert sub.file_path == "src/auth.py"


def test_split_no_lines_lost(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    total_before = len(chunk.lines)
    sub_chunks = split_chunk(chunk, max_lines=3)
    total_after = sum(len(c.lines) for c in sub_chunks)
    assert total_before == total_after


# ── extract_chunks_from_files ─────────────────────────────────────────────────

def test_extract_skips_files_without_patch(sample_pr_file):
    from unittest.mock import MagicMock
    no_patch = MagicMock()
    no_patch.patch = None
    no_patch.filename = "image.png"

    result = extract_chunks_from_files([no_patch, sample_pr_file])
    # Only the file with a real patch should appear.
    assert all(c.file_path == sample_pr_file.filename for c in result)


def test_extract_skips_deletion_only_files():
    from unittest.mock import MagicMock
    deletion_only = MagicMock()
    deletion_only.filename = "old.py"
    deletion_only.patch = "@@ -1,3 +1,0 @@\n-line1\n-line2\n-line3"

    result = extract_chunks_from_files([deletion_only])
    assert result == []


def test_extract_returns_chunks_for_additions(sample_pr_files):
    result = extract_chunks_from_files(sample_pr_files)
    assert len(result) >= 1


def test_extract_respects_max_lines(sample_pr_files, sample_patch):
    from unittest.mock import MagicMock
    # Build a large fake patch with many addition lines.
    big_patch = "@@ -1,1 +1,100 @@\n" + "\n".join(f"+line{i}" for i in range(100))
    big_file = MagicMock()
    big_file.filename = "big.py"
    big_file.patch = big_patch

    result = extract_chunks_from_files([big_file], max_lines_per_chunk=10)
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk.lines) <= 10


# ── build_prompt_for_chunk ────────────────────────────────────────────────────

def test_prompt_contains_file_path(sample_patch):
    chunk = parse_file_patch("app/users.py", sample_patch)
    prompt = build_prompt_for_chunk(chunk)
    assert "app/users.py" in prompt


def test_prompt_contains_diff_content(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    prompt = build_prompt_for_chunk(chunk)
    # At least one addition line's content should appear in the prompt.
    additions = [l.content for l in chunk.lines if l.is_addition]
    assert any(a.lstrip("+").strip() in prompt for a in additions)


def test_prompt_contains_json_instruction(sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    prompt = build_prompt_for_chunk(chunk)
    assert "JSON" in prompt
