from local_worker.documents.jsonutil import InvalidModelJSON, parse_json_object
from local_worker.documents.reader import load_text_file
from local_worker.llm.base import Unavailable
from tests.fakes import FakeAdapter, make_service


def test_txt_csv_json_code_and_log(tmp_path):
    (tmp_path / "note.txt").write_text("hello worker", encoding="utf-8")
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "obj.json").write_text('{"n": 3}', encoding="utf-8")
    (tmp_path / "strategy.py").write_text("def stop():\n    return 2\n", encoding="utf-8")
    (tmp_path / "run.log").write_text("ERROR boom at line\n", encoding="utf-8")

    service = make_service(tmp_path)
    txt = service.delegate_file(str(tmp_path / "note.txt"), "summarize")
    assert txt["status"] == "success"
    csv = service.delegate_file(str(tmp_path / "data.csv"), "extract numbers")
    assert csv["status"] == "success"
    js = service.delegate_file(str(tmp_path / "obj.json"), "extract n")
    assert js["status"] == "success"
    code = service.delegate_file(str(tmp_path / "strategy.py"), "find stop")
    assert code["status"] == "success"
    log = service.delegate_file(str(tmp_path / "run.log"), "find errors")
    assert log["status"] == "success"

    _, pages, kind = load_text_file(tmp_path / "strategy.py")
    assert kind == "code"
    assert "1|" in pages[0].text


def test_synthesis_and_invalid_json(tmp_path):
    adapter = FakeAdapter(responses=["not json at all"])
    service = make_service(tmp_path, adapter)
    (tmp_path / "a.txt").write_text("x" * 40, encoding="utf-8")
    result = service.delegate_file(str(tmp_path / "a.txt"), "analyze")
    assert result["status"] == "error"
    assert result["fallback_recommended"] is True

    parsed = parse_json_object('```json\n{"summary": "ok"}\n```')
    assert parsed["summary"] == "ok"
    try:
        parse_json_object("nope")
        assert False
    except InvalidModelJSON:
        pass


def test_missing_and_unsupported_and_large_file(tmp_path):
    service = make_service(tmp_path)
    missing = service.delegate_file(str(tmp_path / "nope.txt"), "x")
    assert missing["status"] == "error"
    assert "not found" in missing["reason"]

    binary = tmp_path / "pic.bin"
    binary.write_bytes(b"\x00\x01")
    bad = service.delegate_file(str(binary), "x")
    assert bad["status"] == "error"
    assert "unsupported" in bad["reason"]

    huge = tmp_path / "huge.txt"
    huge.write_text("word " * 50_000, encoding="utf-8")
    # still supported; pipeline should chunk instead of exploding
    adapter = FakeAdapter()
    service = make_service(tmp_path, adapter)
    result = service.delegate_file(str(huge), "summarize")
    assert result["status"] in {"success", "partial"}
    assert adapter.calls >= 1


def test_task_unavailable_fallback(tmp_path):
    service = make_service(tmp_path, FakeAdapter(fail_with=Unavailable("Local LLM endpoint unreachable")))
    result = service.delegate_task("do work", context="lots of text " * 20)
    assert result["status"] == "unavailable"
    assert result["fallback_recommended"] is True
    assert "unreachable" in result["reason"]
