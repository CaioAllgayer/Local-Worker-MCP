"""Token-window chunking that preserves page and line provenance."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..results import estimate_tokens


@dataclass
class Page:
    number: int
    text: str
    source: str = ""
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class Chunk:
    index: int
    text: str
    pages: list[int] = field(default_factory=list)
    source: str = ""
    line_start: int | None = None
    line_end: int | None = None

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.text)


def chunk_pages(pages: list[Page], *, max_tokens: int = 3500, overlap_tokens: int = 200) -> list[Chunk]:
    if not pages:
        return []
    units: list[Page] = []
    for page in pages:
        if estimate_tokens(page.text) <= max_tokens:
            units.append(page)
            continue
        units.extend(_split_page(page, max_tokens))

    chunks: list[Chunk] = []
    current: list[Page] = []
    current_tokens = 0
    overlap: list[Page] = []

    def flush() -> None:
        nonlocal current, current_tokens, overlap
        if not current:
            return
        text = "\n\n".join(_render(page) for page in current)
        pages_nums = sorted({page.number for page in current})
        line_start = next((p.line_start for p in current if p.line_start is not None), None)
        line_end = next((p.line_end for p in reversed(current) if p.line_end is not None), None)
        chunks.append(
            Chunk(
                index=len(chunks),
                text=text,
                pages=pages_nums,
                source=current[0].source,
                line_start=line_start,
                line_end=line_end,
            )
        )
        if overlap_tokens > 0:
            overlap = _tail_for_overlap(current, overlap_tokens)
        else:
            overlap = []
        current = list(overlap)
        current_tokens = sum(estimate_tokens(p.text) for p in current)

    for unit in units:
        unit_tokens = estimate_tokens(unit.text)
        if current and current_tokens + unit_tokens > max_tokens:
            flush()
        current.append(unit)
        current_tokens += unit_tokens
    flush()
    return chunks


def with_line_numbers(text: str, start: int = 1) -> str:
    lines = text.splitlines()
    width = len(str(start + len(lines) - 1)) if lines else 1
    return "\n".join(f"{idx:>{width}}| {line}" for idx, line in enumerate(lines, start=start))


def _render(page: Page) -> str:
    header = f"[page {page.number}]"
    if page.line_start is not None and page.line_end is not None:
        header = f"[page {page.number} lines {page.line_start}-{page.line_end}]"
    return f"{header}\n{page.text}"


def _split_page(page: Page, max_tokens: int) -> list[Page]:
    paragraphs = page.text.split("\n\n")
    pieces: list[Page] = []
    buf: list[str] = []
    offset = page.line_start or 1
    buf_start = offset
    consumed_lines = 0

    def emit() -> None:
        nonlocal buf, buf_start
        if not buf:
            return
        text = "\n\n".join(buf)
        line_count = text.count("\n") + 1
        pieces.append(
            Page(
                number=page.number,
                text=text,
                source=page.source,
                line_start=buf_start,
                line_end=buf_start + line_count - 1,
            )
        )
        buf = []

    for paragraph in paragraphs:
        if estimate_tokens(paragraph) > max_tokens:
            emit()
            for line_group in _split_lines(paragraph, max_tokens):
                line_count = line_group.count("\n") + 1
                pieces.append(
                    Page(
                        number=page.number,
                        text=line_group,
                        source=page.source,
                        line_start=offset + consumed_lines,
                        line_end=offset + consumed_lines + line_count - 1,
                    )
                )
                consumed_lines += line_count
            buf_start = offset + consumed_lines
            continue
        candidate = "\n\n".join(buf + [paragraph]) if buf else paragraph
        if buf and estimate_tokens(candidate) > max_tokens:
            emit()
            buf_start = offset + consumed_lines
        buf.append(paragraph)
        consumed_lines += paragraph.count("\n") + 1 + (1 if buf[:-1] else 0)
    emit()
    return pieces or [page]


def _split_lines(text: str, max_tokens: int) -> list[str]:
    lines = text.splitlines() or [text]
    groups: list[str] = []
    buf: list[str] = []
    for line in lines:
        if estimate_tokens(line) > max_tokens:
            if buf:
                groups.append("\n".join(buf))
                buf = []
            groups.extend(_split_chars(line, max_tokens))
            continue
        candidate = "\n".join(buf + [line])
        if buf and estimate_tokens(candidate) > max_tokens:
            groups.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        groups.append("\n".join(buf))
    return groups


def _split_chars(text: str, max_tokens: int) -> list[str]:
    width = max(32, max_tokens * 4)
    return [text[i : i + width] for i in range(0, len(text), width)] or [text]


def _tail_for_overlap(pages: list[Page], overlap_tokens: int) -> list[Page]:
    tail: list[Page] = []
    tokens = 0
    for page in reversed(pages):
        tail.insert(0, page)
        tokens += estimate_tokens(page.text)
        if tokens >= overlap_tokens:
            break
    return tail
