#!/usr/bin/env python3
"""Validate internal hyperlink targets in the LaTeX dictionary sources."""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


COMMAND_PATTERN = re.compile(r"\\(?P<command>HL|HT)\s*\{(?P<target>[^{}]*)\}")
REDIRECT_PATTERN = re.compile(r"\\Redirect\s*")


@dataclass(frozen=True)
class Reference:
    command: str
    target: str
    path: Path
    line: int


def strip_comments(source: str) -> str:
    """Remove unescaped LaTeX comments while preserving line numbers."""
    cleaned_lines: list[str] = []
    for line in source.splitlines(keepends=True):
        comment_at: int | None = None
        for index, character in enumerate(line):
            if character != "%":
                continue
            preceding_backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                preceding_backslashes += 1
                cursor -= 1
            if preceding_backslashes % 2 == 0:
                comment_at = index
                break
        if comment_at is None:
            cleaned_lines.append(line)
        else:
            newline = "\n" if line.endswith("\n") else ""
            cleaned_lines.append(line[:comment_at] + newline)
    return "".join(cleaned_lines)


def braced_argument(source: str, position: int) -> tuple[str, int] | None:
    while position < len(source) and source[position].isspace():
        position += 1
    if position >= len(source) or source[position] != "{":
        return None

    start = position + 1
    depth = 1
    position += 1
    while position < len(source):
        character = source[position]
        preceding_backslashes = 0
        cursor = position - 1
        while cursor >= 0 and source[cursor] == "\\":
            preceding_backslashes += 1
            cursor -= 1
        if preceding_backslashes % 2 == 0:
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return source[start:position], position + 1
        position += 1
    return None


def find_references(path: Path, repository: Path) -> list[Reference]:
    source = strip_comments(path.read_text(encoding="utf-8"))
    references = [
        Reference(
            command=match.group("command"),
            target=match.group("target").strip(),
            path=path.relative_to(repository),
            line=source.count("\n", 0, match.start()) + 1,
        )
        for match in COMMAND_PATTERN.finditer(source)
    ]

    for match in REDIRECT_PATTERN.finditer(source):
        display_argument = braced_argument(source, match.end())
        if display_argument is None:
            continue
        target_argument = braced_argument(source, display_argument[1])
        if target_argument is None:
            continue
        references.append(
            Reference(
                command="HL",
                target=target_argument[0].strip(),
                path=path.relative_to(repository),
                line=source.count("\n", 0, match.start()) + 1,
            )
        )

    return references


def annotation_escape(value: str, *, property_value: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def report_error(reference: Reference, message: str) -> None:
    relative_path = reference.path.as_posix()
    print(f"{relative_path}:{reference.line}: error: {message}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        file_property = annotation_escape(relative_path, property_value=True)
        annotation = annotation_escape(message)
        print(f"::error file={file_property},line={reference.line}::{annotation}")


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    source_files = sorted(repository.glob("*.tex"))
    references = [
        reference
        for path in source_files
        for reference in find_references(path, repository)
    ]

    targets: dict[str, list[Reference]] = defaultdict(list)
    links: list[Reference] = []
    errors = 0

    for reference in references:
        if not reference.target:
            report_error(reference, f"\\{reference.command} has an empty target")
            errors += 1
        elif reference.command == "HT":
            targets[reference.target].append(reference)
        else:
            links.append(reference)

    for target, definitions in sorted(targets.items()):
        if len(definitions) <= 1:
            continue
        locations = ", ".join(
            f"{definition.path.as_posix()}:{definition.line}"
            for definition in definitions
        )
        for definition in definitions:
            report_error(
                definition,
                f"duplicate \\HT target {target!r}; definitions: {locations}",
            )
            errors += 1

    for link in links:
        if link.target not in targets:
            report_error(link, f"\\HL target {link.target!r} has no matching \\HT")
            errors += 1

    if errors:
        print(
            f"Dictionary validation failed with {errors} error(s): "
            f"{len(targets)} targets and {len(links)} links checked."
        )
        return 1

    print(
        f"Dictionary validation passed: {len(targets)} targets and "
        f"{len(links)} links checked across {len(source_files)} files."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
