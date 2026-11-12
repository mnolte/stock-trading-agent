"""The review-story eval (docs/SPEC.md, Reviews — Stories, item 079).

``make eval-reviews PROMPTS=briefing.v3,weekly.v3 MODEL=...`` sends each
fixture case's facts through the named prompt versions on the default
review models and runs the same gates production runs — the per-version
contract, the number check, the v3 format check and the word cap —
then reports the pass rate per prompt. A case passes when the model's
answer would be stored as ``source="model"`` and it uses at least one
glossary term from the facts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.decide.prompt import cached_prompt
from app.llm.models import review_model
from app.reviews.client import SCHEMAS
from app.reviews.service import ReviewService

EVAL_FILE = Path(__file__).resolve().parents[1] / "eval" / "review_stories.json"


def _story_text(content: dict[str, Any], kind: str) -> str:
    key = "story" if kind == "weekly" else "text"
    value = content.get(key)
    return value if isinstance(value, str) else ""


def _uses_glossary_term(text: str, glossary: dict[str, Any]) -> bool:
    """At least one glossary term appears as a whole word, any case."""
    for term in glossary:
        if re.search(
            rf"(?<![A-Za-z]){re.escape(str(term))}(?![A-Za-z])",
            text,
            re.IGNORECASE,
        ):
            return True
    return False


async def run_eval(
    *,
    prompts: list[str],
    model: str | None,
    eval_path: Path = EVAL_FILE,
    client: Any = None,
    config: AppConfig | None = None,
) -> int:
    config = config or AppConfig()
    doc = json.loads(eval_path.read_text())
    cases: list[dict[str, Any]] = doc["cases"]

    if client is None:
        from app.llm.clients import make_model_clients

        if config.env.mode == "simulated":
            client = make_model_clients(config)
        else:
            from app.market.clock import RealClock
            from app.secrets_service import SecretsStore

            client = make_model_clients(
                config, SecretsStore(config, RealClock())
            )

    results: dict[str, list[bool]] = {p: [] for p in prompts}
    detail_rows: list[str] = []
    for case in cases:
        kind = str(case["kind"])
        facts = case["facts"]
        versions = [
            p for p in prompts if p.split(".", 1)[0] == kind or "." not in p
        ]
        for version in versions:
            prompt = cached_prompt(version)
            use_model = model or review_model(config, kind)
            response = await client.complete(
                system=prompt.text,
                user=json.dumps({"kind": kind, "facts": facts}, indent=2),
                schema=SCHEMAS.get(version, SCHEMAS[kind]),
                model=use_model,
                temperature=float(
                    config.get("reviews", "temperature", default=0.2)
                ),
            )
            try:
                parsed: object = (
                    json.loads(response.raw) if response.raw else None
                )
            except json.JSONDecodeError:
                parsed = None
            content = ReviewService._validate(
                kind, version, parsed, config
            )
            if content is None:
                results[version].append(False)
                detail_rows.append(f"{case['name']} {version}: invalid output")
                continue
            screened, reason, _detail = ReviewService._screen_content(
                kind, version, content, facts, config
            )
            if screened is None:
                results[version].append(False)
                detail_rows.append(f"{case['name']} {version}: {reason}")
                continue
            text = _story_text(screened, kind)
            glossary = facts.get("glossary") or {}
            ok = _uses_glossary_term(text, glossary)
            results[version].append(ok)
            detail_rows.append(
                f"{case['name']} {version}: "
                + ("pass" if ok else "no glossary term used")
            )

    print(f"eval-reviews: {len(cases)} case(s), model {model or 'default'}")
    all_ok = True
    for version in prompts:
        runs = results.get(version) or []
        if not runs:
            print(f"  {version}: no cases")
            continue
        rate = sum(runs) / len(runs)
        all_ok = all_ok and rate >= 0.9
        print(f"  {version}: {sum(runs)}/{len(runs)} pass ({rate:.0%})")
    for row in detail_rows:
        print(f"    {row}")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompts",
        default="briefing.v3,weekly.v3",
        help="comma-separated prompt versions to evaluate",
    )
    parser.add_argument("--model", default=None, help="override the review model")
    parser.add_argument("--eval", dest="eval_path", default=str(EVAL_FILE))
    args = parser.parse_args()
    return asyncio.run(
        run_eval(
            prompts=[p.strip() for p in args.prompts.split(",") if p.strip()],
            model=args.model,
            eval_path=Path(args.eval_path),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
