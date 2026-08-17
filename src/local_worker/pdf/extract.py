"""Local PDF text extraction that keeps page numbers."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..documents.chunking import Page


class PdfExtractError(Exception):
    def __init__(self, reason: str, *, pages: list[Page] | None = None, incomplete: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.pages = pages or []
        self.incomplete = incomplete


def extract_pdf(path: Path) -> tuple[list[Page], dict]:
    try:
        reader = PdfReader(str(path), strict=False)
    except PdfReadError as exc:
        raise PdfExtractError(f"PDF is corrupted or unreadable: {exc}") from exc
    except Exception as exc:
        raise PdfExtractError(f"failed to open PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:
            raise PdfExtractError("PDF is encrypted") from exc

    pages: list[Page] = []
    failed: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            failed.append(index)
            continue
        pages.append(Page(number=index, text=text.strip(), source=str(path)))

    nonempty = [page for page in pages if page.text]
    meta = {
        "page_count": len(reader.pages),
        "extracted_pages": len(pages),
        "nonempty_pages": len(nonempty),
        "failed_pages": failed,
    }
    if not nonempty:
        raise PdfExtractError("PDF has no extractable text (possibly scanned)", pages=pages)
    if failed:
        raise PdfExtractError(
            f"PDF partially unreadable; failed pages: {failed}",
            pages=nonempty,
            incomplete=True,
        )
    return nonempty, meta
