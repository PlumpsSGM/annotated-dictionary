#!/usr/bin/env python3
"""Write the temporary LaTeX wrapper used by CI and release builds."""

from __future__ import annotations

import argparse
from pathlib import Path


LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "_": r"\_",
    "%": r"\%",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: str) -> str:
    return "".join(LATEX_ESCAPES.get(character, character) for character in value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if any(character in args.url for character in "\r\n{}\\%#"):
        raise ValueError("Build URL contains a character that is unsafe in LaTeX")

    legacy_metadata_support = r"""
% Supply release metadata to dictionary revisions from before these hooks existed.
\makeatletter
\newif\ifDictionaryLegacyMetadata
\AddToHook{cmd/maketitle/before}{%
  \date{\DictionaryReleaseDate}%
  \ifcsname DictionaryBuildFooter\endcsname
  \else
    \global\DictionaryLegacyMetadatatrue
    \definecolor{DictionaryBuildMetadata}{gray}{0.45}%
    \newcommand{\DictionaryBuildFooter}{%
      \hfil{\scriptsize\color{DictionaryBuildMetadata}%
      \ifx\DictionaryBuildUrl\empty
        \texttt{\DictionaryBuildIdentifier}%
      \else
        \begingroup\hypersetup{pdfborder={0 0 0}}%
        \href{\DictionaryBuildUrl}{\texttt{\DictionaryBuildIdentifier}}%
        \endgroup
      \fi
      }\hfil
    }%
    \let\DictionaryLegacyEmptyPageStyle\ps@empty
    \def\ps@empty{%
      \let\@mkboth\@gobbletwo
      \let\@oddhead\@empty
      \let\@evenhead\@empty
      \def\@oddfoot{\DictionaryBuildFooter}%
      \let\@evenfoot\@oddfoot
    }%
  \fi
}
\AddToHook{cmd/maketitle/after}{%
  \ifDictionaryLegacyMetadata
    \let\ps@empty\DictionaryLegacyEmptyPageStyle
  \fi
}
\makeatother
""".strip()

    wrapper = "\n".join(
        (
            rf"\def\DictionaryReleaseDate{{{latex_escape(args.date)}}}",
            rf"\def\DictionaryBuildIdentifier{{{latex_escape(args.identifier)}}}",
            rf"\def\DictionaryBuildUrl{{{args.url}}}",
            legacy_metadata_support,
            r"\input{00_dict.tex}",
            "",
        )
    )
    args.output.write_text(wrapper, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
