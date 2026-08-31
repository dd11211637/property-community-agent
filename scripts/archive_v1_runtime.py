"""Archive and remove retired V1 runtime state from the local demo database.

This tool never migrates runtime state in place. ``archive`` writes an auditable JSON
export and manifest. ``delete`` verifies that manifest and then removes only runtime
rows in one transaction. Business-domain and audit tables are left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

RUNTIME_TABLES = (
    ("agent_action_approvals", "conversation_id"),
    ("agent_run_leases", "thread_id"),
    ("agent_checkpoints", "thread_id"),
    ("agent_messages", "conversation_id"),
    ("agent_conversations", "conversation_id"),
)


def _connect(url: str):
    database_url = url.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(database_url, row_factory=dict_row)


def _assert_local_demo(url: str, connection) -> None:
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    database = connection.execute("SELECT current_database() AS name").fetchone()["name"]
    if parsed.hostname not in {"127.0.0.1", "localhost"} or database != "property_agent_demo":
        raise RuntimeError("refusing to mutate anything except local property_agent_demo")


def _v1_ids(connection) -> list[str]:
    rows = connection.execute(
        "SELECT conversation_id FROM agent_conversations "
        "WHERE runtime_version = 'v1' ORDER BY conversation_id"
    ).fetchall()
    return [row["conversation_id"] for row in rows]


def _rows(connection, table: str, column: str, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    return connection.execute(
        f"SELECT * FROM {table} WHERE {column} = ANY(%s) ORDER BY {column}", (ids,)
    ).fetchall()


def _canonical_bytes(value: Any) -> bytes:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return content.encode("utf-8")


def archive(url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    with _connect(url) as connection:
        _assert_local_demo(url, connection)
        ids = _v1_ids(connection)
        snapshot = connection.execute("SELECT txid_current_snapshot() AS value").fetchone()["value"]
        payload = {
            "schema_version": "v1-runtime-archive-v1",
            "database": "property_agent_demo",
            "snapshot": snapshot,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tables": {
                table: _rows(connection, table, column, ids) for table, column in RUNTIME_TABLES
            },
        }
    export_path = output_dir / "v1-runtime-records.json"
    export_path.write_bytes(_canonical_bytes(payload))
    digest = hashlib.sha256(export_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "v1-runtime-archive-manifest-v1",
        "database": "property_agent_demo",
        "snapshot": payload["snapshot"],
        "v1_conversation_count": len(ids),
        "row_counts": {name: len(rows) for name, rows in payload["tables"].items()},
        "export_file": export_path.name,
        "export_sha256": digest,
        "full_dump_file": "property_agent_demo.dump",
        "restore_verified": False,
        "generated_at": payload["generated_at"],
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_bytes(_canonical_bytes(manifest))
    return manifest_path


def mark_restore_verified(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["restore_verified"] = True
    manifest["restore_verified_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_bytes(_canonical_bytes(manifest))


def delete(url: str, manifest_path: Path) -> dict[str, int]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    export_path = manifest_path.parent / manifest["export_file"]
    if not manifest.get("restore_verified"):
        raise RuntimeError("archive restore has not been verified")
    if hashlib.sha256(export_path.read_bytes()).hexdigest() != manifest["export_sha256"]:
        raise RuntimeError("archive digest mismatch")
    with _connect(url) as connection:
        _assert_local_demo(url, connection)
        ids = _v1_ids(connection)
        if len(ids) != manifest["v1_conversation_count"]:
            raise RuntimeError("live V1 inventory no longer matches the verified archive")
        deleted: dict[str, int] = {}
        with connection.transaction():
            for table, column in RUNTIME_TABLES:
                result = connection.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (ids,))
                deleted[table] = result.rowcount
            remaining = connection.execute(
                "SELECT count(*) AS count FROM agent_conversations WHERE runtime_version = 'v1'"
            ).fetchone()["count"]
            if remaining:
                raise RuntimeError("V1 rows remain; rolling back")
        return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("archive", "mark-restore-verified", "delete"))
    parser.add_argument("--database-url")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.mode == "archive":
        if not args.database_url or not args.output_dir:
            parser.error("archive requires --database-url and --output-dir")
        print(archive(args.database_url, args.output_dir))
    elif args.mode == "mark-restore-verified":
        if not args.manifest:
            parser.error("mark-restore-verified requires --manifest")
        mark_restore_verified(args.manifest)
    else:
        if not args.database_url or not args.manifest:
            parser.error("delete requires --database-url and --manifest")
        print(json.dumps(delete(args.database_url, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()
