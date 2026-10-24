"""The llm_calls backfill (docs/SPEC.md, LLM call history — Backfill).

The migration fills llm_calls from decisions (model output or a
``model_error`` no-call), shadow_decisions (model output or a failed
call, never missed work) and reviews/verdicts (model text, or the plain
template only when a call ran and failed a check). Rows are marked
``backfilled``; fields old tables never recorded — HTTP status, request
id — stay null. Calls that were never sent get no row.
"""

import json
from pathlib import Path
from typing import Any

import app.config as config_module
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
PRIOR_REVISION = "b6c7d8e9f0a1"


def _upgrade(db: Path, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SA_DB_PATH", str(db))
    monkeypatch.setattr(config_module, "_config", None)
    command.upgrade(Config(str(ALEMBIC_INI)), revision)


def _fixture(db: Path) -> None:
    """Rows at the pre-PR18 schema: one of each backfill outcome."""
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO cycles (id, kind, scheduled_time, status) VALUES "
            "(1, 'cycle', '2026-10-22 15:46:00', 'done'), "
            "(2, 'cycle', '2026-10-22 15:56:00', 'done')"
        ))
        conn.execute(text(
            "INSERT INTO strategies (id, name, kind, status, created_at) VALUES "
            "(1, 'balanced', 'llm', 'active', '2026-10-20 00:00:00')"
        ))

        decisions = [
            # (id, model_output, validation, final_action, no_call_reason,
            #  latency, snapshot, cached)
            (1, '{"a":1}', '{"valid": true, "model_action": "enter"}',
             "enter", None, 800, "{}", 0),
            (2, '{"a":1}', '{"valid": true, "model_action": "enter"}',
             "blocked", None, 900, "{}", 0),
            (3, None, '{"valid": false}',
             "no_call", "model_error", None, "{}", 0),
            (4, '{"a":1}', '{"valid": false, "model_action": null}',
             "skip", None, 700, "{}", 0),
            (5, '{"a":1}', '{"valid": true, "model_action": "skip"}',
             "skip", None, 600, '{"price": "unavailable"}', 0),
            (6, '{"a":1}', '{"valid": true, "model_action": "skip"}',
             "skip", None, 20000, "{}", 0),
            # Never sent: a cached replay and no_call rows.
            (7, '{"a":1}', '{"valid": true, "model_action": "skip"}',
             "skip", None, 500, "{}", 1),
            (8, None, None, "no_call", "call_cap", None, "{}", 0),
            (9, None, None, "no_call", "rules", None, "{}", 0),
        ]
        for d in decisions:
            conn.execute(text(
                "INSERT INTO decisions (id, cycle_id, symbol, context,"
                " input_snapshot, validation_result, model_output,"
                " final_action, no_call_reason, prompt_version, prompt_hash,"
                " model, tokens_in, tokens_out, cost, latency_ms,"
                " raw_response, cached) VALUES"
                " (:id, 1, :symbol, 'candidate', :snapshot, :validation,"
                " :output, :action, :ncr, 'decision.v1', 'hash',"
                " 'fake-decision', 100, 20, '0.00001', :latency,"
                " :raw, :cached)"
            ), {
                "id": d[0], "symbol": f"SYM{d[0]}", "snapshot": d[6],
                "validation": d[2], "output": d[1], "action": d[3],
                "ncr": d[4], "latency": d[5],
                "raw": d[1], "cached": d[7],
            })

        shadows = [
            # (id, model_output, validation, final_action, no_call_reason,
            #  missed, live_id)
            (1, '{"a":1}', '{"valid": true, "model_action": "hold"}',
             "hold", None, 0, 1),
            (2, None, '{"valid": false}',
             "no_call", "model_error", 0, 2),
            (3, '{"a":1}', '{"valid": true}',
             "no_call", None, 1, 3),  # missed — never sent
            (4, None, None, "no_call", "call_cap", 0, 4),  # never sent
        ]
        for s in shadows:
            conn.execute(text(
                "INSERT INTO shadow_decisions (id, strategy_id, cycle_id,"
                " symbol, context, live_decision_id, model_output,"
                " validation_result, final_action, no_call_reason,"
                " prompt_version, model, missed, created_at) VALUES"
                " (:id, 1, 1, :symbol, 'held', :live, :output, :validation,"
                " :action, :ncr, 'decision.v1', 'fake-decision', :missed,"
                " '2026-10-22 15:50:00')"
            ), {
                "id": s[0], "symbol": f"SHD{s[0]}", "live": s[6],
                "output": s[1],
                "validation": s[2], "action": s[3], "ncr": s[4],
                "missed": s[5],
            })

        reviews = [
            # (id, kind, source, reason)
            (1, "weekly", "model", None),
            (2, "briefing", "template", "model call failed"),
            (3, "briefing", "template", "invalid model output"),
            (4, "weekly", "template", "number check failed"),
            (5, "briefing", "template", "fact check failed"),
            # Never sent: no key and a reached cap.
            (6, "briefing", "template", "no OpenAI key"),
            (7, "weekly", "template", "cost cap reached"),
        ]
        for r in reviews:
            conn.execute(text(
                "INSERT INTO reviews (id, kind, period, text, source,"
                " reason, model, prompt_version, due_at, created_at) VALUES"
                " (:id, :kind, :period, 'text', :source, :reason,"
                " 'fake-review', 'weekly.v1', '2026-10-24 14:00:00',"
                " '2026-10-24 14:00:00')"
            ), {
                "id": r[0], "kind": r[1], "period": f"p{r[0]}",
                "source": r[2], "reason": r[3],
            })

        # Verdicts on decisions 1-4; the template ones need a
        # review_fallback event to prove the call was sent.
        conn.execute(text(
            "INSERT INTO verdicts (id, decision_id, verdict, source,"
            " draft_source, created_at, updated_at) VALUES"
            " (1, 1, 'good_call', 'draft', 'model', '2026-10-29 20:00:00',"
            "  '2026-10-29 20:00:00'),"
            " (2, 2, 'bad_call', 'draft', 'template', '2026-10-29 20:00:00',"
            "  '2026-10-29 20:00:00'),"
            " (3, 3, 'unclear', 'draft', 'template', '2026-10-29 20:00:00',"
            "  '2026-10-29 20:00:00'),"
            " (4, 4, 'unclear', 'draft', 'template', '2026-10-29 20:00:00',"
            "  '2026-10-29 20:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO events (time, level, event_type, details) VALUES"
            " ('2026-10-29 20:00:00', 'warning', 'review_fallback',"
            "  '{\"kind\": \"verdict\", \"ref\": \"decision 2 (SYM2)\","
            "    \"reason\": \"number check failed\"}'),"
            " ('2026-10-29 20:00:00', 'warning', 'review_fallback',"
            "  '{\"kind\": \"verdict\", \"ref\": \"decision 3 (SYM3)\","
            "    \"reason\": \"no OpenAI key\"}')"
        ))
    engine.dispose()


def _calls(db: Path) -> list[dict[str, Any]]:
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT source, kind, decision_id, shadow_decision_id, review_id,"
            " verdict_id, strategy_id, symbol, final_action, outcome, reasons,"
            " error_type,"
            " session_date, latency_ms, http_status, request_id, backfilled"
            " FROM llm_calls"
        )).mappings().all()
    engine.dispose()
    out = []
    for r in rows:
        row = dict(r)
        row["reasons"] = json.loads(row["reasons"])
        row["session_date"] = str(row["session_date"])
        out.append(row)
    return out


@pytest.fixture()
def migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    db = tmp_path / "backfill.db"
    _upgrade(db, PRIOR_REVISION, monkeypatch)
    _fixture(db)
    _upgrade(db, "head", monkeypatch)
    return _calls(db)


def _by_link(rows: list[dict[str, Any]], key: str, value: int) -> dict[str, Any]:
    (row,) = [r for r in rows if r[key] == value]
    return row


def test_every_call_gets_the_spec_outcome(migrated: list[dict[str, Any]]) -> None:
    live = {r["decision_id"]: r for r in migrated if r["source"] == "live"}
    assert live[1]["outcome"] == "success" and live[1]["reasons"] == []
    assert live[2]["outcome"] == "warning"
    assert live[2]["reasons"] == ["changed_by_code"]
    assert live[3]["outcome"] == "error" and live[3]["error_type"] == "api_error"
    assert live[4]["outcome"] == "error"
    assert live[4]["error_type"] == "invalid_output"
    assert live[5]["outcome"] == "warning"
    assert live[5]["reasons"] == ["input_incomplete"]
    assert live[6]["outcome"] == "warning" and live[6]["reasons"] == ["slow"]
    # Cached answers and no_calls never sent a request: no row.
    assert 7 not in live and 8 not in live and 9 not in live


def test_shadow_backfill(migrated: list[dict[str, Any]]) -> None:
    shadow = {
        r["shadow_decision_id"]: r
        for r in migrated if r["source"] == "shadow"
    }
    assert set(shadow) == {1, 2}
    assert shadow[1]["outcome"] == "success"
    assert shadow[1]["strategy_id"] == 1
    assert shadow[2]["outcome"] == "error" and shadow[2]["error_type"] == "api_error"


def test_review_backfill(migrated: list[dict[str, Any]]) -> None:
    review = {r["review_id"]: r for r in migrated if r["source"] == "review"
              and r["review_id"] is not None}
    assert set(review) == {1, 2, 3, 4, 5}
    assert review[1]["outcome"] == "success" and review[1]["kind"] == "weekly"
    assert review[2]["outcome"] == "error" and review[2]["error_type"] == "api_error"
    assert review[3]["error_type"] == "invalid_output"
    assert review[4]["outcome"] == "warning"
    assert review[4]["reasons"] == ["number_check_failed"]
    assert review[5]["reasons"] == ["number_check_failed"]


def test_verdict_backfill(migrated: list[dict[str, Any]]) -> None:
    verdict = {r["verdict_id"]: r for r in migrated
               if r["verdict_id"] is not None}
    assert set(verdict) == {1, 2}
    assert verdict[1]["outcome"] == "success" and verdict[1]["kind"] == "verdict"
    assert verdict[2]["outcome"] == "warning"
    assert verdict[2]["reasons"] == ["number_check_failed"]
    # "no OpenAI key" and eventless templates: no proof a call was sent.
    assert verdict[1]["symbol"] == "SYM1"
    assert verdict[1]["decision_id"] == 1


def test_backfilled_rows_are_marked_and_sparse(migrated: list[dict[str, Any]]) -> None:
    assert migrated, "the fixture must produce rows"
    for row in migrated:
        assert row["backfilled"] == 1
        # Never recorded before PR 18.
        assert row["http_status"] is None
        assert row["request_id"] is None
    live1 = _by_link(
        [r for r in migrated if r["source"] == "live"], "decision_id", 1
    )
    assert live1["session_date"] == "2026-10-22"
    assert live1["latency_ms"] == 800
    review1 = _by_link(migrated, "review_id", 1)
    # Saturday 2026-10-24 10:00 New York — its own New York date.
    assert review1["session_date"] == "2026-10-24"
