from __future__ import annotations

from testing.real_memory_embedding_closure import execute


def test_real_memory_embedding_closure_only_stops_for_missing_external_key(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MEMORY_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    report = execute()

    assert report == {
        "schema_version": "real-memory-embedding-closure-v1",
        "status": "NOT_RUN",
        "external_embedding_gate": "NOT_RUN",
        "reason": "MEMORY_EMBEDDING_API_KEY unavailable",
    }
