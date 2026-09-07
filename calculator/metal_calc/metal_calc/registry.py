from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import Conflict, NotFound
from .util import canonical_json, utcnow, validate_id

Mutator = Callable[[dict[str, Any]], None]


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise RuntimeError("registry.db must not be a symlink")
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def _migrate(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_json BLOB NOT NULL
                ) STRICT
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_proposals (
                    fact_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES orders(order_id),
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'superseded')),
                    proposal_json BLOB NOT NULL,
                    approved_fact_json BLOB,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    superseded_at TEXT
                ) STRICT
                """
            )

    def create(self, order_id: str, state: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        validate_id(order_id, field="order_id")
        now = utcnow()
        state = deepcopy(state)
        state["order_id"] = order_id
        state.setdefault("timestamps", {"created_at": now, "updated_at": now})
        state["timestamps"]["created_at"] = now
        state["timestamps"]["updated_at"] = now
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    "INSERT INTO orders(order_id, revision, status, created_at, updated_at, state_json) "
                    "VALUES(?, 1, ?, ?, ?, ?)",
                    (order_id, state["status"], now, now, canonical_json(state)),
                )
                con.commit()
        except sqlite3.IntegrityError as exc:
            raise Conflict("Order already exists") from exc
        return 1, state

    def get(self, order_id: str) -> tuple[int, dict[str, Any]]:
        validate_id(order_id, field="order_id")
        with self._connect() as con:
            row = con.execute(
                "SELECT revision, state_json FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        if row is None:
            raise NotFound("Order not found")
        return int(row["revision"]), json.loads(row["state_json"])

    def mutate(
        self,
        order_id: str,
        mutator: Mutator,
        *,
        expected_revision: int | None = None,
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        validate_id(order_id, field="order_id")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT revision, state_json FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if row is None:
                con.rollback()
                raise NotFound("Order not found")
            current = int(row["revision"])
            if expected_revision is not None and expected_revision != current:
                con.rollback()
                raise Conflict("Order revision conflict")
            state = json.loads(row["state_json"])
            mutator(state)
            if validator is not None:
                validator(state)
            now = utcnow()
            state.setdefault("timestamps", {})["updated_at"] = now
            new_revision = current + 1
            changed = con.execute(
                "UPDATE orders SET revision=?, status=?, updated_at=?, state_json=? "
                "WHERE order_id=? AND revision=?",
                (new_revision, state["status"], now, canonical_json(state), order_id, current),
            ).rowcount
            if changed != 1:
                con.rollback()
                raise Conflict("Order revision conflict")
            con.commit()
            return new_revision, state

    def list(
        self,
        *,
        statuses: list[str] | None,
        limit: int,
        offset: int,
        sort: str,
    ) -> tuple[list[dict[str, Any]], int]:
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError("Invalid pagination")
        order_sql = {"updated_desc": "updated_at DESC", "created_asc": "created_at ASC"}.get(sort)
        if order_sql is None:
            raise ValueError("Invalid sort")
        where = ""
        args: list[Any] = []
        if statuses:
            marks = ",".join("?" for _ in statuses)
            where = f" WHERE status IN ({marks})"
            args.extend(statuses)
        with self._connect() as con:
            total = int(con.execute(f"SELECT count(*) FROM orders{where}", args).fetchone()[0])
            rows = con.execute(
                f"SELECT revision, state_json FROM orders{where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            state = json.loads(row["state_json"])
            items.append(
                {
                    "order_id": state["order_id"],
                    "revision": int(row["revision"]),
                    "status": state["status"],
                    "customer": state["customer"],
                    "updated_at": state["timestamps"]["updated_at"],
                    "price_total_rub": (state.get("price") or {}).get("total_rub"),
                }
            )
        return items, total

    def all_states(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT state_json FROM orders").fetchall()
        return [json.loads(row[0]) for row in rows]

    def propose_facts(
        self,
        order_id: str,
        expected_revision: int,
        proposals: list[dict[str, Any]],
    ) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
        validate_id(order_id, field="order_id")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT revision, state_json FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if row is None:
                con.rollback()
                raise NotFound("Order not found")
            current = int(row["revision"])
            if current != expected_revision:
                con.rollback()
                raise Conflict("Order revision conflict")
            for proposal in proposals:
                existing = con.execute(
                    "SELECT order_id, proposal_json FROM fact_proposals WHERE fact_id=?",
                    (proposal["fact_id"],),
                ).fetchone()
                if existing is not None:
                    saved = json.loads(existing["proposal_json"])
                    if (
                        existing["order_id"] != order_id
                        or saved.get("proposal_sha256") != proposal.get("proposal_sha256")
                    ):
                        con.rollback()
                        raise Conflict("Fact proposal conflict")
                    continue
                con.execute(
                    "INSERT INTO fact_proposals(fact_id,order_id,status,proposal_json,created_at) "
                    "VALUES(?,?,'pending',?,?)",
                    (proposal["fact_id"], order_id, canonical_json(proposal), proposal["proposed_at"]),
                )
            state = json.loads(row["state_json"])
            now = utcnow()
            state.setdefault("timestamps", {})["updated_at"] = now
            new_revision = current + 1
            con.execute(
                "UPDATE orders SET revision=?, updated_at=?, state_json=? WHERE order_id=? AND revision=?",
                (new_revision, now, canonical_json(state), order_id, current),
            )
            con.commit()
            return new_revision, state, proposals

    def fact_status(self, order_id: str, fact_id: str) -> tuple[str, dict[str, Any] | None]:
        with self._connect() as con:
            row = con.execute(
                "SELECT status, approved_fact_json FROM fact_proposals WHERE order_id=? AND fact_id=?",
                (order_id, fact_id),
            ).fetchone()
        if row is None:
            raise NotFound("Fact proposal not found")
        approved = json.loads(row["approved_fact_json"]) if row["approved_fact_json"] else None
        return str(row["status"]), approved

    def list_fact_proposals(self, order_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status,proposal_json,approved_at,superseded_at FROM fact_proposals "
                "WHERE order_id=? ORDER BY created_at,fact_id",
                (order_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = json.loads(row["proposal_json"])
            item["status"] = row["status"]
            if row["approved_at"]:
                item["approved_at"] = row["approved_at"]
            if row["superseded_at"]:
                item["superseded_at"] = row["superseded_at"]
            result.append(item)
        return result

    def approve_fact(
        self,
        order_id: str,
        fact_id: str,
        approved_by: str,
        approved_at: str,
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, Any]]:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status, proposal_json, approved_fact_json FROM fact_proposals "
                "WHERE order_id=? AND fact_id=?",
                (order_id, fact_id),
            ).fetchone()
            if row is None:
                con.rollback()
                raise NotFound("Fact proposal not found")
            if row["status"] == "approved":
                fact = json.loads(row["approved_fact_json"])
                order_row = con.execute(
                    "SELECT revision,state_json FROM orders WHERE order_id=?", (order_id,)
                ).fetchone()
                con.commit()
                return int(order_row["revision"]), json.loads(order_row["state_json"]), fact
            if row["status"] != "pending":
                con.rollback()
                raise Conflict("Only a pending fact proposal can be approved")
            proposal = json.loads(row["proposal_json"])
            fact = {
                "fact_id": fact_id,
                "code": f"part_quantity:{proposal['target_source_file_id']}",
                "value": proposal["value"],
                "unit": "pcs",
                "source_ref": proposal["evidence_ref"],
                "source_sha256": proposal["proposal_sha256"],
                "approved_by": approved_by,
                "approved_at": approved_at,
            }
            order_row = con.execute(
                "SELECT revision,state_json FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if order_row is None:
                con.rollback()
                raise NotFound("Order not found")
            state = json.loads(order_row["state_json"])
            if state["status"] in {"calculated", "quoted", "won", "lost", "cancelled"}:
                con.rollback()
                raise Conflict("Cannot approve facts after calculation or closure")
            now = utcnow()
            approved_rows = con.execute(
                "SELECT fact_id,proposal_json FROM fact_proposals "
                "WHERE order_id=? AND status='approved'",
                (order_id,),
            ).fetchall()
            superseded_ids: set[str] = set()
            for approved_row in approved_rows:
                prior = json.loads(approved_row["proposal_json"])
                if prior.get("target_source_file_id") == proposal["target_source_file_id"]:
                    superseded_ids.add(str(approved_row["fact_id"]))
            if superseded_ids:
                marks = ",".join("?" for _ in superseded_ids)
                con.execute(
                    f"UPDATE fact_proposals SET status='superseded',superseded_at=? "
                    f"WHERE fact_id IN ({marks}) AND status='approved'",
                    [now, *sorted(superseded_ids)],
                )
                state["manual_facts"] = [
                    saved
                    for saved in state.get("manual_facts", [])
                    if saved.get("fact_id") not in superseded_ids
                ]
            state.setdefault("manual_facts", []).append(fact)
            if validator is not None:
                validator(state)
            state["timestamps"]["updated_at"] = now
            revision = int(order_row["revision"]) + 1
            con.execute(
                "UPDATE fact_proposals SET status='approved',approved_fact_json=?,approved_at=? "
                "WHERE fact_id=? AND status='pending'",
                (canonical_json(fact), approved_at, fact_id),
            )
            con.execute(
                "UPDATE orders SET revision=?,updated_at=?,state_json=? WHERE order_id=?",
                (revision, now, canonical_json(state), order_id),
            )
            con.commit()
            return revision, state, fact
