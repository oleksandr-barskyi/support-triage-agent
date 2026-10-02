from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.evals.cases import EvalCase

CHECKS = (
    "completed",
    "category",
    "escalation",
    "refund",
    "grounded_reply",
    "no_leak",
    "used_tools",
)


@dataclass
class CaseResult:
    subject: str
    checks: dict[str, bool] = field(default_factory=dict)
    status: str = ""
    tokens: int = 0
    duration_ms: int = 0
    model: str = ""

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _payloads(actions: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [payload for action_type, payload in actions if action_type == kind]


def _amount(payload: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str(payload.get("amount")))
    except (InvalidOperation, ValueError):
        return None


def score_case(
    case: EvalCase,
    status: str,
    actions: list[tuple[str, dict[str, Any]]],
    tools_called: Sequence[str] = (),
) -> dict[str, bool]:
    checks = {"completed": status != "failed"}
    if case.required_tools:
        checks["used_tools"] = set(case.required_tools) <= set(tools_called)
    triage = _payloads(actions, "triage")
    checks["category"] = bool(triage) and triage[0].get("category") in case.categories
    escalated = bool(_payloads(actions, "escalate"))
    if case.must_escalate:
        checks["escalation"] = escalated
    refunds = _payloads(actions, "refund")
    if case.refund_expected:
        checks["refund"] = any(
            case.refund_amount is None or _amount(r) == case.refund_amount for r in refunds
        )
    else:
        checks["refund"] = not refunds
    replies = _payloads(actions, "reply")
    if replies:
        checks["grounded_reply"] = all(r.get("cited_article_ids") for r in replies)
    if case.forbidden_reply_terms:
        bodies = " ".join(str(r.get("body", "")) for r in replies).lower()
        checks["no_leak"] = not any(t.lower() in bodies for t in case.forbidden_reply_terms)
    return checks


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    per_check: dict[str, dict[str, int]] = {}
    for name in CHECKS:
        applicable = [r.checks[name] for r in results if name in r.checks]
        per_check[name] = {"passed": sum(applicable), "total": len(applicable)}
    runs = len(results) or 1
    return {
        "cases": len(results),
        "fully_passed": sum(r.passed for r in results),
        "checks": per_check,
        "avg_tokens": round(sum(r.tokens for r in results) / runs),
        "avg_duration_ms": round(sum(r.duration_ms for r in results) / runs),
    }


def render_markdown(results: list[CaseResult], summary: dict[str, Any], model: str) -> str:
    lines = [
        f"# Eval report: {model}",
        "",
        f"Cases fully passed: {summary['fully_passed']} / {summary['cases']}. "
        f"Average {summary['avg_tokens']} tokens and {summary['avg_duration_ms']} ms per run.",
        "",
        "| Check | Passed |",
        "| --- | --- |",
    ]
    for name, c in summary["checks"].items():
        if c["total"]:
            lines.append(f"| {name} | {c['passed']} / {c['total']} |")
    lines += ["", "| Ticket | Status | Failed checks |", "| --- | --- | --- |"]
    for r in results:
        failed = ", ".join(k for k, ok in r.checks.items() if not ok) or "none"
        lines.append(f"| {r.subject} | {r.status} | {failed} |")
    return "\n".join(lines) + "\n"
