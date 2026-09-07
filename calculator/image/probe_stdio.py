"""Exercise the new engine's MCP client against the isolated V9 server."""
import asyncio
import json
import os
from pathlib import Path
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(block.text for block in result.content if block.type == "text"))


async def main():
    with tempfile.TemporaryDirectory(prefix="calc21-wire-") as directory:
        root = Path(directory)
        env = dict(os.environ)
        for name in ("cache", "orders", "delivery"):
            (root / name).mkdir()
        env.update({"METAL_CALC_CACHE_ROOT": str(root / "cache"),
                    "METAL_CALC_ORDERS_ROOT": str(root / "orders"),
                    "METAL_CALC_DELIVERY_ROOT": str(root / "delivery"),
                    "METAL_CALC_ROLE": "front", "METAL_CALC_PROFILE": "default"})
        counts = {}
        params = StdioServerParameters(command="/opt/metal-calc/bin/metal-calc-mcp", env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "qa_verdict" not in names and "order_upsert" in names
                counts["front"] = len(names)
                catalog = payload(await session.call_tool("rates_catalog", {}))
                assert "error" not in catalog, catalog
                created = payload(await session.call_tool("order_upsert", {
                    "order_id": "wire-probe", "expected_revision": 0,
                    "patch": {"customer": {"name": "Synthetic wire probe"}}}))
                assert "error" not in created, created
                loaded = payload(await session.call_tool("order_get", {"order_id": "wire-probe"}))
                assert "error" not in loaded, loaded
        env.update({"METAL_CALC_ROLE": "tech", "METAL_CALC_PROFILE": "raschet-route"})
        params = StdioServerParameters(command="/opt/metal-calc/bin/metal-calc-mcp", env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "order_upsert" not in names and "bom_upsert" in names
                counts["tech"] = len(names)
                denied = payload(await session.call_tool("order_get", {"order_id": "wire-probe"}))
                assert denied.get("error", {}).get("code") == "OrderScopeDenied", denied
        print(json.dumps({"mcp_wire": "PASS", "tools": counts,
                          "front_order_get": "PASS", "unscoped_tech": "REJECTED",
                          "model_calls": 0}))


if __name__ == "__main__":
    asyncio.run(main())
