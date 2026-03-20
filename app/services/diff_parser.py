"""
diff_parser.py — Parse GitHub unified diff patches into structured DiffChunks.

Design decisions:
* We parse the raw patch string that PyGithub exposes on each PullRequestFile,
  rather than calling the GitHub Compare API separately.  This saves an API
  round-trip per file.
* The GitHub Pull Request review comment API requires a "position" integer —
  the 1-indexed position *within the diff* (counting both context and changed
  lines, and hunk headers).  We compute this mapping here so the GitHub
  service only needs to look up a pre-computed value.
* Chunks are split at max_lines to prevent sending enormous diffs to Claude in
  a single request, which would blow the context window and produce worse
  results than shorter, focused prompts.
* Files with no additions are skipped — there's nothing for Claude to review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.schemas import DiffChunk, DiffLine

# Hunk header pattern: @@ -a,b +c,d @@ (optional trailing context text)
_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_file_patch(file_path: str, patch: str) -> DiffChunk:
    """
    Convert a single file's raw unified-diff patch into a DiffChunk.

    The 'position' used by GitHub's review comment API starts at 1 for the
    first hunk header line and increments for every subsequent line (including
    hunk headers and context lines).
    """
    lines: list[DiffLine] = []
    diff_position_map: dict[int, int] = {}  # diff_line_number → github_position

    current_new_line: int = 0
    diff_line_number: int = 0  # 1-indexed within this file's diff
    github_position: int = 0   # as required by the GitHub API

    for raw_line in patch.splitlines():
        github_position += 1

        # Hunk header — reset the line counter for the new hunk.
        m = _HUNK_HEADER_RE.match(raw_line)
        if m:
            current_new_line = int(m.group(2)) - 1  # -1 because we'll +1 below
            continue  # Hunk headers don't count as diff_line_numbers for us.

        diff_line_number += 1
        is_addition = raw_line.startswith("+")
        is_deletion = raw_line.startswith("-")

        if not is_deletion:
            current_new_line += 1

        new_line_num = current_new_line if not is_deletion else None

        dl = DiffLine(
            number=diff_line_number,
            content=raw_line,
            is_addition=is_addition,
            new_line=new_line_num,
        )
        lines.append(dl)
        diff_position_map[diff_line_number] = github_position

    return DiffChunk(
        file_path=file_path,
        patch=patch,
        lines=lines,
        diff_position_map=diff_position_map,
    )


def split_chunk(chunk: DiffChunk, max_lines: int) -> list[DiffChunk]:
    """
    Split a DiffChunk whose patch is too large into smaller pieces.

    Each sub-chunk keeps the same file_path but carries only a slice of the
    diff lines.  The diff_position_map is preserved for each slice so comment
    posting still works.
    """
    if len(chunk.lines) <= max_lines:
        return [chunk]

    sub_chunks: list[DiffChunk] = []
    patch_lines = chunk.patch.splitlines()

    for start in range(0, len(chunk.lines), max_lines):
        end = start + max_lines
        sub_lines = chunk.lines[start:end]
        # Rebuild a patch fragment from the line content.
        sub_patch = "\n".join(l.content for l in sub_lines)
        sub_position_map = {
            dl.number: chunk.diff_position_map[dl.number]
            for dl in sub_lines
            if dl.number in chunk.diff_position_map
        }
        sub_chunks.append(
            DiffChunk(
                file_path=chunk.file_path,
                patch=sub_patch,
                lines=sub_lines,
                diff_position_map=sub_position_map,
            )
        )

    return sub_chunks


def extract_chunks_from_files(
    pr_files: list, max_lines_per_chunk: int = 300
) -> list[DiffChunk]:
    """
    Top-level entry point.  Accepts a list of PyGithub PullRequestFile objects
    and returns a flat list of DiffChunks ready for the AI service.

    Files without a patch (e.g. binary files, renamed-only) are skipped.
    Files with zero additions are also skipped — Claude has nothing to review.
    """
    chunks: list[DiffChunk] = []
    for pr_file in pr_files:
        patch = getattr(pr_file, "patch", None)
        if not patch:
            continue
        # Skip if no additions exist in this file's diff.
        if not any(line.startswith("+") for line in patch.splitlines()):
            continue

        chunk = parse_file_patch(pr_file.filename, patch)
        chunks.extend(split_chunk(chunk, max_lines_per_chunk))

    return chunks


def build_prompt_for_chunk(chunk: DiffChunk) -> str:
    """
    Format a DiffChunk into a user message suitable for Claude.

    We include the file path so Claude can populate `file_path` in its
    findings, and we wrap the patch in a code block so it's unambiguous.
    """
    return (
        f"File: {chunk.file_path}\n\n"
        f"```diff\n{chunk.patch}\n```\n\n"
        "Return your findings as a JSON array per the instructions.  "
        "Use diff_line_number values relative to this file's diff (as shown above)."
    )
