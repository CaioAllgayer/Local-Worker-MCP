from local_worker.server.mcp import create_mcp
from tests.fakes import make_service


def test_mcp_registers_expected_tools(tmp_path):
    service = make_service(tmp_path)
    mcp = create_mcp(service)
    tools = getattr(mcp, "_tool_manager").list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "local_status",
        "delegate_task",
        "delegate_batch",
        "delegate_file",
        "delegate_pdf",
        "cache_stats",
        "cache_cleanup",
        "cache_clear",
    }
