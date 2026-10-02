import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.agent.loop import Limits
from app.agent.model import build_model
from app.core.settings import get_settings
from app.evals.cases import CASES
from app.evals.runner import run_evals
from app.evals.scoring import render_markdown, summarize

REPORTS = Path(__file__).resolve().parents[2] / "evals" / "reports"


async def main(limit: int | None, only: str | None) -> None:
    from app.db.session import SessionFactory, engine

    settings = get_settings()
    model = build_model(settings)
    cases = [c for c in CASES if only is None or only.lower() in c.subject.lower()][:limit]
    results = await run_evals(SessionFactory, model, Limits.from_settings(settings), cases)
    await engine.dispose()

    summary = summarize(results)
    models = sorted({r.model for r in results if r.model}) or [model.name]
    report = render_markdown(results, summary, ", ".join(models))
    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{stamp}.md").write_text(report, encoding="utf-8")
    (REPORTS / f"{stamp}.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "results": [
                    {
                        "subject": r.subject,
                        "status": r.status,
                        "model": r.model,
                        "checks": r.checks,
                        "tokens": r.tokens,
                        "duration_ms": r.duration_ms,
                    }
                    for r in results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the triage agent against labelled tickets.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None, help="substring of a ticket subject")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.only))
