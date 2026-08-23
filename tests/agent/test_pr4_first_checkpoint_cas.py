"""PR4 — FIRST_CHECKPOINT_CAS_GAP 修复验证。

基线缺陷：``SqlAlchemyCheckpointer.version_of`` 无行时返回 ``None``，首个发布走
``_save_legacy``（SELECT→+1→INSERT），两个竞争的首发者可能互相覆盖（stale A 在 lease
过期后仍以 version=1 覆盖 B 已发布的 version=1）。

修复目标语义：

* 无 checkpoint = accepted version 0；
* 首次发布 ``expected_version=0`` → 原子 ``INSERT … ON CONFLICT DO NOTHING RETURNING
  version``；第二个竞争首发者返回 0 行 → ``CheckpointVersionConflict``；
* 后续发布 ``expected_version=N`` → ``UPDATE WHERE version=N RETURNING version``，0 行冲突；
* ``expected_version=None`` 仅允许 legacy / 直接测试调用方，生产共享 lifecycle 不得传。

SQLite 快测在此文件内联 fixture；真实 PostgreSQL 竞争回归见 ``test_pg_*``
（@pytest.mark.postgres，CI 真实 PG 上零跳过运行）。
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from property_agent.agent.infrastructure.checkpointer import (
    CheckpointVersionConflict,
    LangGraphCheckpointCursor,
    SqlAlchemyCheckpointer,
)
from property_agent.agent.state import GraphState
from property_agent.agent.working_state import RepairWorkingState
from property_agent.platform.infrastructure.orm_models import Base

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


def _make_state(conversation_id: str = "conv-1") -> GraphState:
    return GraphState(
        conversation_id=conversation_id,
        actor_id=uuid4(),
        community_id=uuid4(),
        intent="REPAIR",
        domain=RepairWorkingState(description="x"),
        slots={"house_id": str(uuid4()), "description": "x"},
        messages=[],
    )


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


@pytest.fixture
def checkpointer(session_factory) -> SqlAlchemyCheckpointer:
    return SqlAlchemyCheckpointer(session_factory)


# ── SQLite 快测 ────────────────────────────────────────────


def test_version_of_no_row_is_accepted_zero(checkpointer):
    assert checkpointer.version_of("never") == 0


def test_first_publish_expected_zero_inserts_version_one(checkpointer):
    checkpointer.save("conv-1", _make_state("conv-1"), expected_version=0)
    assert checkpointer.version_of("conv-1") == 1


def test_two_stale_first_publishers_only_one_canonical(checkpointer):
    """第二个竞争首发者被 CAS 拒绝，先发布者保持 canonical。"""
    checkpointer.save("conv-1", _make_state("conv-1"), expected_version=0)
    assert checkpointer.version_of("conv-1") == 1

    with pytest.raises(CheckpointVersionConflict) as exc:
        checkpointer.save("conv-1", _make_state("conv-1"), expected_version=0)
    assert exc.value.expected == 0
    # B 的状态保持 canonical（version 仍为 1，未被 stale A 覆盖）
    assert checkpointer.version_of("conv-1") == 1


def test_subsequent_publish_cas_uses_expected_version(checkpointer):
    checkpointer.save("conv-1", _make_state("conv-1"), expected_version=0)
    checkpointer.save("conv-1", _make_state("conv-1"), expected_version=1)
    assert checkpointer.version_of("conv-1") == 2
    with pytest.raises(CheckpointVersionConflict):
        checkpointer.save("conv-1", _make_state("conv-1"), expected_version=1)


def test_publish_accepted_stores_runtime_cursor(checkpointer):
    cursor = LangGraphCheckpointCursor(
        thread_id="lg-conv-1", checkpoint_ns="", checkpoint_id="cp-abc"
    )
    checkpointer.publish_accepted(
        "conv-1", _make_state("conv-1"), expected_version=0, runtime_cursor=cursor.to_dict()
    )
    accepted = checkpointer.load_accepted("conv-1")
    assert accepted is not None
    assert accepted.runtime_cursor is not None
    assert accepted.runtime_cursor.thread_id == "lg-conv-1"
    assert accepted.runtime_cursor.checkpoint_id == "cp-abc"


def test_expected_none_is_legacy_only(checkpointer):
    """生产不得传 None；此处仅验证 legacy 兼容路径仍可写。"""
    checkpointer.save("conv-1", _make_state("conv-1"))
    assert checkpointer.version_of("conv-1") == 1


# ── 真实 PostgreSQL 回归 ───────────────────────────────────


@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="requires TEST_POSTGRES_URL and a dedicated PostgreSQL database"
)
def test_pg_first_checkpoint_cas_real_race():
    """A 读 expected 0，lease 过期；B 以更高 fence 发布 version 1；A 再尝试发布
    expected 0 → 冲突，B 状态保持 canonical。"""
    eng = create_engine(POSTGRES_URL, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        cp_a = SqlAlchemyCheckpointer(sf)
        cp_b = SqlAlchemyCheckpointer(sf)
        state = _make_state("pg-fc")
        exp_a = cp_a.version_of("pg-fc")  # 0
        cp_b.save("pg-fc", state, expected_version=0)
        assert cp_b.version_of("pg-fc") == 1
        with pytest.raises(CheckpointVersionConflict):
            cp_a.save("pg-fc", state, expected_version=exp_a)
        assert cp_b.version_of("pg-fc") == 1
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()
