from pypdf import PdfWriter

from local_worker.documents.chunking import Page
from local_worker.documents.pipeline import DocumentPipeline
from local_worker.pdf.extract import PdfExtractError, extract_pdf
from local_worker.llm.factory import LLMGateway
from local_worker.llm.circuit import CircuitBreaker
from tests.fakes import FakeAdapter, make_service, make_settings


def test_pdf_without_text(tmp_path):
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(str(path))
    try:
        extract_pdf(path)
        assert False, "expected empty text error"
    except PdfExtractError as exc:
        assert "no extractable text" in exc.reason


def test_pdf_page_preservation_and_no_invented_pages(tmp_path):
    pages = [
        Page(number=14, text="A estratégia utiliza stop de 2 ATR em todos os trades."),
        Page(number=15, text="Limitação: amostra pequena."),
    ]
    adapter = FakeAdapter(
        responses=[
            {
                "summary": "stop 2 ATR",
                "findings": [
                    {"text": "A estratégia utiliza stop de 2 ATR.", "page": 14, "excerpt": "stop de 2 ATR"}
                ],
                "numbers": [{"label": "stop", "value": "2 ATR", "page": 14, "excerpt": "stop de 2 ATR"}],
                "evidence": [{"page": 99, "text": "invented"}],
                "uncertainties": [],
                "confidence": 0.91,
            }
        ]
    )
    settings = make_settings(tmp_path)
    pipeline = DocumentPipeline(
        LLMGateway(settings, adapter=adapter, breaker=CircuitBreaker()), chunk_tokens=2000
    )
    merged = pipeline.run(pages, "extraia a estratégia")
    pages_cited = {item.get("page") for item in merged["findings"]}
    assert 14 in pages_cited
    assert 99 not in {item.get("page") for item in merged["evidence"]}
    assert any("invented page" in item for item in merged["uncertainties"])


def test_delegate_pdf_uses_extractor(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(str(path))

    def fake_extract(p):
        return [Page(number=17, text="A estratégia utiliza stop de 2 ATR.", source=str(p))], {"page_count": 1}

    monkeypatch.setattr("local_worker.workers.service.extract_pdf", fake_extract)
    service = make_service(tmp_path)
    result = service.delegate_pdf(str(path), "estratégia e parâmetros")
    assert result["status"] == "success"
    assert result["evidence"]
    assert result["compression"]["estimated_original_tokens"] > 0
