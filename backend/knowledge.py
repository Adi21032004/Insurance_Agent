"""Search and retrieve claim-handling source sections."""

import re
from typing import Literal

from backend.money import convert_currency_text

KnowledgeSource = Literal["policy_wording", "claims_manual"]
HEADING_PATTERN = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
VERSION_PATTERN = re.compile(r"\bVersion\s+([0-9]+(?:\.[0-9]+)*)")
WORD_PATTERN = re.compile(r"[a-z0-9]+")
MAX_EXCERPT_LENGTH = 280
MAX_SEARCH_RESULTS = 5


def split_sections(source: KnowledgeSource, document: str) -> list[dict[str, str]]:
    """Split one markdown source into numbered sections with version metadata."""
    version_match = VERSION_PATTERN.search(document)
    version = version_match.group(1) if version_match is not None else "unknown"
    sections: list[dict[str, str]] = []
    current_heading = ""
    current_number = ""
    current_lines: list[str] = []
    for line in document.splitlines():
        heading = HEADING_PATTERN.match(line)
        if heading is not None:
            if current_heading:
                sections.append(
                    make_section(
                        source, version, current_number, current_heading, current_lines
                    )
                )
            current_number, current_heading = heading.groups()
            current_lines = []
        elif current_heading:
            current_lines.append(line)
    if current_heading:
        sections.append(
            make_section(
                source, version, current_number, current_heading, current_lines
            )
        )
    return sections


def make_section(
    source: KnowledgeSource,
    version: str,
    number: str,
    heading: str,
    lines: list[str],
) -> dict[str, str]:
    """Build one stable section record from its source lines."""
    return {
        "source": source,
        "section_id": f"{source}:{number.zfill(2)}",
        "heading": heading,
        "version": version,
        "content": "\n".join(lines).strip(),
    }


def search_sections(
    source: KnowledgeSource, document: str, query: str, rate: float
) -> list[dict[str, str]]:
    """Return the most relevant section excerpts for a claim query."""
    query_words = set(WORD_PATTERN.findall(query.casefold()))
    if not query_words:
        return []
    results: list[tuple[int, int, dict[str, str]]] = []
    for section in split_sections(source, document):
        searchable = f"{section['heading']} {section['content']}".casefold()
        matches = query_words.intersection(WORD_PATTERN.findall(searchable))
        if matches:
            excerpt = matching_excerpt(section["content"], matches)
            results.append(
                (
                    len(matches),
                    int(section["section_id"].rsplit(":", maxsplit=1)[1]),
                    {
                        **{
                            key: section[key]
                            for key in ("source", "section_id", "heading", "version")
                        },
                        "excerpt": convert_currency_text(excerpt, rate),
                    },
                )
            )
    results.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in results[:MAX_SEARCH_RESULTS]]


def matching_excerpt(content: str, terms: set[str]) -> str:
    """Choose a short paragraph that contains the strongest query match."""
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()
    ]
    if not paragraphs:
        return ""
    paragraph = max(
        paragraphs,
        key=lambda text: len(terms.intersection(WORD_PATTERN.findall(text.casefold()))),
    )
    if len(paragraph) <= MAX_EXCERPT_LENGTH:
        return paragraph
    return f"{paragraph[: MAX_EXCERPT_LENGTH - 1].rstrip()}…"


def read_section(
    source: KnowledgeSource, document: str, section_id: str, rate: float
) -> dict[str, str] | None:
    """Return one requested section with all source amounts converted to INR."""
    section = next(
        (
            item
            for item in split_sections(source, document)
            if item["section_id"] == section_id
        ),
        None,
    )
    if section is None:
        return None
    return {**section, "content": convert_currency_text(section["content"], rate)}
