"""Eval for Ask the agent (docs/SPEC.md → Ask the agent → Eval).

``python -m app.eval_ask --model gpt-6.1-sol`` runs eval/ask_v1.json
against a fresh fixture database and reports per-question and aggregate
property results plus cost. Requires the provider key in the
environment or secrets store; tests never run this (it calls a real
model — the harness is only used manually with ``make eval-ask``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.data.cache import _naive_utc

FIXED_NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def _fixture_db() -> Path:
    """Build a fixture db: the warm test template when available, else a
    migrated empty db. Lives in a temp file the caller may delete."""
    import os
    import shutil

    from alembic import command
    from alembic.config import Config as AlembicConfig

    path = Path(tempfile.mkdtemp()) / "eval-ask.db"
    template = Path(tempfile.gettempdir()) / "stock-agent-test-template"
    candidates = sorted(template.glob("template-*.db")) if template.exists() else []
    if candidates:
        shutil.copyfile(candidates[-1], path)
        return path
    os.environ["SA_DB_PATH"] = str(path)
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    command.upgrade(AlembicConfig(str(ini)), "head")
    return path


async def _run_question(
    question: dict[str, Any], deps: Any, client: Any, session_factory: Any
) -> dict[str, Any]:
    from app.ask.service import answer
    from app.db.models import AskMessage, AskThread

    with session_factory() as s:
        t = AskThread(
            title="eval",
            context=question.get("context"),
            created_at=_naive_utc(FIXED_NOW),
            updated_at=_naive_utc(FIXED_NOW),
        )
        s.add(t)
        s.commit()
        m = AskMessage(
            thread_id=t.id,
            role="assistant",
            text="",
            status="ok",
            created_at=_naive_utc(FIXED_NOW),
        )
        s.add(m)
        s.commit()
        tid, mid = t.id, m.id

    text = ""
    done: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    async for ev in answer(
        client=client,
        deps=deps,
        session_factory=session_factory,
        thread_id=tid,
        message_id=mid,
        question=question["q"],
        context=question.get("context"),
    ):
        if ev["event"] == "delta":
            text += ev["data"]["text"]
        elif ev["event"] == "step":
            steps.append(ev["data"])
        elif ev["event"] == "done":
            done = ev["data"]

    full = done.get("text") or text
    blob = json.dumps(done, default=str) + full
    checks = []
    for fact in question.get("facts", []):
        needle = str(fact).lower()
        checks.append(needle in blob.lower())
    props = question.get("properties", [])
    prop_results: dict[str, bool] = {}
    for prop in props:
        if prop == "uses_tools":
            prop_results[prop] = bool(steps)
        elif prop == "cites_refs":
            prop_results[prop] = bool(done.get("references"))
        elif prop == "no_unverified":
            prop_results[prop] = not done.get("unverified_numbers")
        elif prop == "concise":
            prop_results[prop] = len(str(full)) < 1200
        elif prop == "read_only_refusal":
            prop_results[prop] = any(
                w in str(full).lower()
                for w in ("can't", "cannot", "never change", "read-only", "won't")
            )
        else:
            prop_results[prop] = True
    return {
        "q": question["q"],
        "answer": str(full)[:400],
        "facts": checks,
        "properties": prop_results,
        "steps": [s.get("label") for s in steps],
        "cost": str(done.get("cost", "0")),
        "passed": all(checks) and all(prop_results.values()),
    }


async def _main(model: str, file: Path) -> int:
    import os

    from app.ask.tools import AskDeps
    from app.config import AppConfig
    from app.db.base import get_session_maker
    from app.market.clock import FakeClock

    db = _fixture_db()
    os.environ["SA_DB_PATH"] = str(db)
    config = AppConfig()
    # The eval model overrides ask.model for this run only (in place —
    # _active shares the yaml dict when no settings table exists).
    config.yaml.setdefault("ask", {})["model"] = model
    from app.llm.clients import ProviderClients
    from app.secrets_service import read_env_keys

    env_keys = read_env_keys()
    try:
        clients = ProviderClients(
            config,
            key_for=lambda name: env_keys.get(name),
            default_model=model,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"cannot create client for {model}: {exc}")
        return 2
    deps = AskDeps(
        config=config,
        clock=FakeClock(FIXED_NOW),
        session_factory=get_session_maker(),
    )
    questions = json.loads(file.read_text())["questions"]
    results = []
    total_cost = Decimal(0)
    for q in questions:
        try:
            r = await _run_question(q, deps, clients, get_session_maker())
        except Exception as exc:  # noqa: BLE001  # noqa: BLE001 - eval keeps going
            r = {"q": q["q"], "error": str(exc), "passed": False}
        results.append(r)
        total_cost += Decimal(r.get("cost") or 0)
        mark = "PASS" if r.get("passed") else "FAIL"
        print(f"[{mark}] {q['q'][:70]}")
        if r.get("error"):
            print(f"      error: {r['error'][:200]}")
        if r.get("facts") is not None and not all(r["facts"]):
            print(f"      missing facts: {r['answer'][:160]}")
    passed = sum(1 for r in results if r.get("passed"))
    total_checks = sum(len(r.get("facts") or [True]) for r in results)
    ok_checks = sum(sum(r.get("facts") or [True]) for r in results)
    print(
        f"\n{passed}/{len(results)} questions, {ok_checks}/{total_checks} facts "
        f"({ok_checks / max(total_checks, 1):.0%}), ${total_cost:.4f} total "
        f"(${total_cost / max(len(results), 1):.4f}/question), model {model}"
    )
    return 0 if passed / max(len(results), 1) >= 0.9 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--file",
        default=str(Path(__file__).resolve().parents[1] / "eval" / "ask_v1.json"),
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.model, Path(args.file)))


if __name__ == "__main__":
    sys.exit(main())
