"""Synthetic migration replay. Run identical inputs in old and new images."""
import argparse
import json
from pathlib import Path
import tempfile

from metal_calc import registry as registry_module, service3, util
from metal_calc.packs2 import PipelinePackStore
from metal_calc.registry import Registry
from metal_calc.securefs import SecureRoot
from metal_calc.service3 import WorkflowService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", type=Path,
                        default=Path(__file__).parent / "fixtures/synthetic_pack.json")
    args = parser.parse_args()
    # Fix metadata time for byte comparisons; this is an isolated synthetic run.
    for module in (registry_module, service3, util):
        module.utcnow = lambda: "2026-09-07T00:00:00Z"
    with tempfile.TemporaryDirectory(prefix="calc21-parity-") as directory:
        root = Path(directory)
        rates = root / "rates"
        rates.mkdir()
        data = args.pack.read_bytes()
        pack = json.loads(data)
        (rates / (pack["revision"] + ".json")).write_bytes(data)
        (rates / "_active.json").write_text(json.dumps({
            "revision": pack["revision"], "sha256": util.sha256_bytes(data)}))
        store = SecureRoot(rates, writable=False)
        svc = WorkflowService(Registry(root / "orders/registry.db"), PipelinePackStore(store))
        oid = "synthetic-migration-1"
        source = {"source_file_id": "src_" + "a" * 24, "name": "synthetic.pdf",
                  "sha256": "a" * 64, "format": "pdf", "bytes": 100,
                  "received_at": "2026-09-07T00:00:00Z"}
        rev, _ = svc.registry.create(oid, {"order_id": oid, "customer": {"name": "Synthetic"},
            "source_files": [source], "status": "draft", "warnings": [], "timestamps": {},
            "provenance": {"created_by": "migration-probe"}})
        manifest = [{k: source[k] for k in ("source_file_id", "name", "sha256")}]
        rev = svc.input_freeze(oid, rev, 10, "R1", manifest, "synthetic input", actor_role="front")["revision"]
        rev = svc.bom_upsert(oid, rev, [{"bom_node_id": "part-1", "parent_bom_id": None,
            "node_type": "MANUFACTURED_PART", "make_or_buy": "MAKE", "quantity": 1,
            "note": "synthetic shaft"}], actor_role="tech")["revision"]
        proposed = svc.route_variants_propose(oid, rev, [
            {"seq": 1, "process_code": "BLANK.CUTOFF", "execution_mode": "in_house", "note": "cut"},
            {"seq": 2, "process_code": "MACHINING.TURN.CNC", "execution_mode": "in_house", "note": "turn"},
            {"seq": 3, "process_code": "FINISH.FITTER", "execution_mode": "in_house", "note": "finish"},
        ], actor_role="tech")
        rev = svc.route_freeze(oid, proposed["revision"], proposed["variant_id"], actor_role="tech")["revision"]
        rev = svc.blank_drivers_set(oid, rev, "steel-09g2s", "2.5", "per_piece", "synthetic mass",
            [{"route_seq": 1, "cuts": 10, "note": "one cut per part"}], actor_role="supply")["revision"]
        rev = svc.time_norms_set(oid, rev, [{"route_seq": 2, "batch": 5,
            "params": {"diameter_mm": "60", "length_mm": "120", "stock_mm": "3"}}],
            [{"route_seq": 3, "hours": "0.5", "quantity_basis": "order_total", "note": "finish"}],
            actor_role="norm")["revision"]
        rev = svc.book_assemble(oid, rev, actor_role="book_machine")["revision"]
        svc.qa_run_mechanical(oid, rev, actor_role="book_machine")
        _, state = svc.registry.get(oid)
        report = svc.report_get(oid, "client", actor_role="front")
        assert report["document_status"] == "PRELIMINARY"
        rendered = svc.render_book_xlsx(oid, state["book"]["digest"], actor_role="front")
        print(json.dumps({"case": "synthetic", "model_calls": 0,
            "book_digest": state["book"]["digest"], "cost": state["book"]["cost"],
            "price": report["price"], "xlsx_sha256": rendered["sha256"],
            "document_status": report["document_status"]}, ensure_ascii=False, sort_keys=True))
        store.close()


if __name__ == "__main__":
    main()
