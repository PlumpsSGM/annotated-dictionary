#!/usr/bin/env python3
"""Validate the structure and internal links of the LaTeX dictionary sources."""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path


ANCHOR = "anchor"  # defines a hyperlink target
TARGET = "target"  # links to a hyperlink target
TEXT = "text"  # typeset text, walked for nested macros

# Every macro the validator reads, with the role of each braced argument.
SIGNATURES = {
    "HT": (ANCHOR, TEXT),
    "HL": (TARGET, TEXT),
    "Headword": (ANCHOR, TEXT, TEXT),
    "Redirect": (TEXT, TARGET, TEXT),
    "Derivatives": (TEXT,),
    "Derivative": (TARGET, TEXT, TEXT),
    "textsc": (TEXT,),
}

# Their items carry their own word type and examples, and have no headword.
HAND_WRITTEN_DERIVATIVES = {"kintrr", "vospxI"}

MACRO_PATTERN = re.compile(r"\\(?:(?P<name>[A-Za-z]+)|.)", re.DOTALL)
FINAL_PERIOD_PATTERN = re.compile(r"\.\}*$")


@dataclass(frozen=True)
class Reference:
    command: str
    target: str
    defines: bool
    path: Path
    line: int


@dataclass(frozen=True, eq=False)
class Argument:
    role: str
    text: str
    start: int
    end: int


@dataclass(frozen=True, eq=False)
class Macro:
    name: str
    arguments: tuple[Argument, ...]
    source: str
    start: int
    end: int
    path: Path
    line: int
    parent: Macro | None
    container: Argument | None

    @property
    def complete(self) -> bool:
        return len(self.arguments) == len(SIGNATURES[self.name])


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


def walk(
    source: str,
    path: Path,
    start: int,
    end: int,
    parent: Macro | None = None,
    container: Argument | None = None,
) -> Iterator[Macro]:
    """Yield each macro in SIGNATURES, recursing into its text arguments."""
    position = start
    while match := MACRO_PATTERN.search(source, position, end):
        position = match.end()
        roles = SIGNATURES.get(match.group("name"))
        if roles is None:
            continue

        arguments: list[Argument] = []
        for role in roles:
            parsed = braced_argument(source, position)
            if parsed is None:
                break
            text, position = parsed
            arguments.append(
                Argument(role, text, position - 1 - len(text), position - 1)
            )

        macro = Macro(
            name=match.group("name"),
            arguments=tuple(arguments),
            source=source,
            start=match.start(),
            end=position,
            path=path,
            line=source.count("\n", 0, match.start()) + 1,
            parent=parent,
            container=container,
        )
        yield macro
        for argument in macro.arguments:
            if argument.role == TEXT:
                yield from walk(
                    source, path, argument.start, argument.end, macro, argument
                )


def find_macros(path: Path, repository: Path) -> list[Macro]:
    source = strip_comments(path.read_text(encoding="utf-8"))
    return list(walk(source, path.relative_to(repository), 0, len(source)))


def find_references(macros: Iterable[Macro]) -> Iterator[Reference]:
    for macro in macros:
        for argument in macro.arguments:
            if argument.role in (ANCHOR, TARGET):
                yield Reference(
                    command=macro.name,
                    target=argument.text.strip(),
                    defines=argument.role == ANCHOR,
                    path=macro.path,
                    line=macro.line,
                )


def enclosing_headword(macro: Macro) -> Macro | None:
    parent = macro.parent
    while parent is not None and parent.name != "Headword":
        parent = parent.parent
    return parent


def check_derivatives_list(
    macro: Macro, children: list[Macro]
) -> Iterator[tuple[Macro, str]]:
    entry = macro.parent
    if (
        entry is None
        or entry.name != "Headword"
        or macro.container is not entry.arguments[2]
    ):
        yield macro, "\\Derivatives must be inside a \\Headword body"
    elif macro.source[macro.end : entry.arguments[2].end].strip():
        yield macro, "\\Derivatives must be last in its entry"

    argument = macro.arguments[0]
    items = [child for child in children if child.name == "Derivative"]
    remainder = ""
    cursor = argument.start
    for item in items:
        remainder += macro.source[cursor : item.start]
        cursor = item.end
    remainder += macro.source[cursor : argument.end]
    if remainder.strip():
        yield macro, "\\Derivatives may hold only \\Derivative items"
    if not items:
        yield macro, "\\Derivatives is empty"


def check_derivative_item(
    macro: Macro, headwords: set[str], seen: set[str]
) -> Iterator[tuple[Macro, str]]:
    anchor = macro.arguments[0].text.strip()
    display = macro.arguments[1].text.strip()
    gloss = macro.arguments[2].text.strip()

    if not display:
        yield macro, f"\\Derivative {anchor!r} has an empty display text"
    if not gloss:
        yield macro, f"\\Derivative {anchor!r} has an empty gloss"

    entry = enclosing_headword(macro)
    if anchor and anchor not in headwords:
        yield macro, f"\\Derivative target {anchor!r} has no \\Headword"
    if entry is not None and anchor == entry.arguments[0].text.strip():
        yield macro, f"\\Derivative target {anchor!r} is its own entry"
    if anchor in seen:
        yield macro, f"\\Derivative target {anchor!r} repeats within the list"
    seen.add(anchor)

    if FINAL_PERIOD_PATTERN.search(gloss):
        yield macro, f"\\Derivative {anchor!r} gloss must not end in a period"
    if "\\\\" in gloss:
        yield macro, f"\\Derivative {anchor!r} gloss must not contain \\\\"
    if "\\slash" in display or "\\slash" in gloss:
        yield macro, f"\\Derivative {anchor!r} must use / instead of \\slash"


def check_derivatives(
    macros: list[Macro], headwords: set[str]
) -> Iterator[tuple[Macro, str]]:
    children: dict[Macro, list[Macro]] = defaultdict(list)
    for macro in macros:
        if macro.parent is not None:
            children[macro.parent].append(macro)

    for macro in macros:
        if macro.name == "Derivatives":
            yield from check_derivatives_list(macro, children[macro])
            seen: set[str] = set()
            for item in children[macro]:
                if item.name == "Derivative":
                    yield from check_derivative_item(item, headwords, seen)
        elif macro.name == "Derivative":
            if macro.parent is None or macro.parent.name != "Derivatives":
                yield macro, "\\Derivative must be inside \\Derivatives"
        elif macro.name == "textsc":
            if macro.arguments[0].text.startswith("Deriv"):
                entry = enclosing_headword(macro)
                anchor = entry and entry.arguments[0].text.strip()
                if anchor not in HAND_WRITTEN_DERIVATIVES:
                    yield macro, "derivative list must use \\Derivatives"


def annotation_escape(value: str, *, property_value: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def report_error(reference: Reference | Macro, message: str) -> None:
    relative_path = reference.path.as_posix()
    print(f"{relative_path}:{reference.line}: error: {message}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        file_property = annotation_escape(relative_path, property_value=True)
        annotation = annotation_escape(message)
        print(f"::error file={file_property},line={reference.line}::{annotation}")


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    source_files = sorted(repository.glob("*.tex"))
    found = [
        macro
        for path in source_files
        for macro in find_macros(path, repository)
    ]
    macros = [macro for macro in found if macro.complete]

    targets: dict[str, list[Reference]] = defaultdict(list)
    links: list[Reference] = []
    errors = 0

    for macro in found:
        if not macro.complete:
            report_error(macro, f"\\{macro.name} is missing an argument")
            errors += 1

    for reference in find_references(macros):
        if not reference.target:
            report_error(reference, f"\\{reference.command} has an empty target")
            errors += 1
        elif reference.defines:
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

    headwords = {
        macro.arguments[0].text.strip()
        for macro in macros
        if macro.name == "Headword"
    }
    for macro, message in check_derivatives(macros, headwords):
        report_error(macro, message)
        errors += 1

    derivatives = sum(macro.name == "Derivative" for macro in macros)
    checked = f"{len(targets)} targets, {len(links)} links and {derivatives} derivatives"
    if errors:
        print(f"Dictionary validation failed with {errors} error(s): {checked} checked.")
        return 1

    print(f"Dictionary validation passed: {checked} checked across {len(source_files)} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
