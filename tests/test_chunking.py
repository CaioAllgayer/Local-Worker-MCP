from local_worker.documents.chunking import Page, chunk_pages, estimate_tokens, with_line_numbers


def test_chunking_preserves_pages():
    pages = [
        Page(number=1, text="alpha " * 20),
        Page(number=2, text="beta " * 20),
        Page(number=3, text="gamma " * 20),
    ]
    chunks = chunk_pages(pages, max_tokens=40, overlap_tokens=5)
    assert chunks
    asserted = set()
    for chunk in chunks:
        asserted.update(chunk.pages)
        assert "[page " in chunk.text
    assert {1, 2, 3}.issubset(asserted)


def test_large_page_splits_without_losing_page_number():
    page = Page(number=14, text=("stop de 2 ATR. " * 400))
    chunks = chunk_pages([page], max_tokens=50, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(chunk.pages == [14] for chunk in chunks)


def test_line_numbers_and_token_estimate():
    numbered = with_line_numbers("a\nb\nc", start=10)
    assert "10| a" in numbered
    assert "12| c" in numbered
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
