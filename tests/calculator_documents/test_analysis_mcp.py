import base64
import json

import pytest

from test_intake_analysis import ready
from test_intake_analysis_worker import prepare_model
from test_analysis_proposals import proposal
from metal_calc.analysis_mcp import build_mcp


def payload(result):
    if isinstance(result, tuple):
        return result[1]
    return json.loads(next(block.text for block in result if block.type == "text"))


@pytest.mark.asyncio
async def test_actual_mcp_pixels_scope_and_typed_submission(ready):
    store, record, native, worker = prepare_model(ready)
    claim = store.claim("inspect-test")
    source = store.sources(claim)[0]
    session = source["attempt"]["session_id"]
    store.release(claim)
    mcp = build_mcp(store)
    assert {tool.name for tool in await mcp.list_tools()} == {"analysis_context", "analysis_page", "analysis_submit"}
    metadata = payload(await mcp.call_tool("analysis_context", {"session_id": session}))
    assert metadata["source"]["source_id"] == source["source_id"]
    assert metadata["human_approved"] is False
    result = proposal()
    result["evidence"][0].update(source_id=source["source_id"], source_sha256=source["sha256"])
    denied = payload(await mcp.call_tool("analysis_submit", {"session_id": session, "proposal": result}))
    assert "error" in denied  # Pixels have not been requested yet.
    for page in (1, 2):
        blocks = await mcp.call_tool("analysis_page", {"session_id": session, "page": page})
        # Image-bearing tools return content blocks, not structured dict wrappers.
        if isinstance(blocks, tuple):
            blocks = blocks[0]
        image = next(block for block in blocks if block.type == "image")
        pixels, _ = store.jobs.image(source["render_jobs"][str(page)], source["source_id"])
        assert base64.b64decode(image.data) == pixels
        assert image.mimeType == "image/png"
    saved = payload(await mcp.call_tool("analysis_submit", {"session_id": session, "proposal": result}))
    assert saved["status"] == "proposal_saved" and saved["human_approved"] is False
    foreign = payload(await mcp.call_tool("analysis_context", {"session_id": record["session_id"]}))
    assert "error" in foreign
    invalid = await mcp.call_tool("analysis_page", {"session_id": session, "page": 999})
    assert "error" in str(invalid)
