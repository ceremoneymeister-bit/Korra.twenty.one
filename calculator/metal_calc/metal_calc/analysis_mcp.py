"""Three tools for the exact internal analysis attempt, never the operator chat."""
from __future__ import annotations

import json

from .analysis_recipe import LIMITS, proposal_schema, source_contents
from .errors import InvalidState, MetalCalcError


def build_mcp(store):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent
    import base64

    mcp = FastMCP("metal_calc", instructions=(
        "Inspect only the bound source. View every PDF page using analysis_page, "
        "then publish an unverified proposal with analysis_submit. No price or human approval."))

    def invoke(function, *args):
        try:
            return function(*args)
        except MetalCalcError as exc:
            return {"error": {"code": exc.code, "message": exc.public_message}}
        except Exception:
            return {"error": {"code": "AnalysisUnavailable", "message": "Данные разбора временно недоступны"}}

    def context(session_id):
        binding = store.for_session(session_id)
        source, observation = binding["source"], binding["observation"]
        contents = source_contents(source, observation)
        return {"source": {key: source.get(key) for key in
                            ("source_id", "sha256", "relative_path", "document_type", "page_count")},
                "contents": contents, "initial_answers": binding["plan"]["initial_answers"],
                "proposal_schema": proposal_schema(), "numeric_facts": "unverified",
                "human_approved": False, "use_for_calculation": False}

    def page_image(session_id, page):
        if type(page) is not int:
            raise InvalidState("Номер страницы должен быть целым")
        binding = store.for_session(session_id)
        source = binding["source"]
        if binding["observation"]["document_type"] != "pdf" or not 1 <= page <= source["page_count"]:
            raise InvalidState("Страница не принадлежит назначенному PDF")
        job_id = source["render_jobs"].get(str(page))
        if not job_id:
            raise InvalidState("Изображение страницы ещё не подготовлено")
        data, info = store.jobs.image(job_id, source["source_id"])
        if len(data) > LIMITS["max_model_image_bytes"] or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidState("Изображение не помещается в модельный вход; требуется уменьшенный рендер")
        store.record_view(session_id, page)
        receipt = {"source_id": source["source_id"], "source_sha256": source["sha256"],
                   "page": page, "image_sha256": info["sha256"], "numeric_facts": "unverified"}
        return [TextContent(type="text", text=json.dumps(receipt, ensure_ascii=False)),
                ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType="image/png")]

    @mcp.tool()
    def analysis_context(session_id: str | None = None) -> dict:
        """Read the assigned source, cached text/cells, and required proposal schema."""
        return invoke(context, session_id)

    @mcp.tool()
    def analysis_page(page: int, session_id: str | None = None):
        """View actual PNG pixels of one prepared page from the assigned PDF."""
        return invoke(page_image, session_id, page)

    @mcp.tool()
    def analysis_submit(proposal: dict, session_id: str | None = None) -> dict:
        """Publish source-local unverified positions/facts/relations/issues per proposal_schema."""
        return invoke(store.submit, session_id, proposal)

    return mcp
