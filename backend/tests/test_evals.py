from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evals.cases import CASES, EvalCase
from app.evals.runner import run_evals
from app.evals.scoring import CaseResult, render_markdown, score_case, summarize
from tests.conftest import LIMITS, ScriptedModel, final_turn, tool_call, tool_turn

DUPLICATE = EvalCase(
    "Charged twice", frozenset({"billing"}), refund_expected=True, refund_amount=Decimal("1600")
)


def test_every_case_matches_a_seeded_ticket() -> None:
    from app.seed_data import DOCUMENT_TICKET, TICKETS

    subjects = {subject for _, subject, _ in TICKETS} | {DOCUMENT_TICKET[1]}
    assert {c.subject for c in CASES} <= subjects


def test_expected_refund_with_the_right_amount_passes() -> None:
    checks = score_case(
        DUPLICATE,
        "awaiting_review",
        [
            ("triage", {"category": "billing"}),
            ("refund", {"order_id": 3, "amount": "1600.00"}),
            ("reply", {"body": "Refunded.", "cited_article_ids": [1]}),
        ],
    )
    assert all(checks.values()), checks


def test_wrong_refund_amount_and_ungrounded_reply_fail() -> None:
    checks = score_case(
        DUPLICATE,
        "awaiting_review",
        [
            ("triage", {"category": "billing"}),
            ("refund", {"order_id": 3, "amount": "3200"}),
            ("reply", {"body": "Refunded both.", "cited_article_ids": []}),
        ],
    )
    assert checks["refund"] is False
    assert checks["grounded_reply"] is False


def test_unexpected_refund_missing_escalation_and_leak_fail() -> None:
    case = EvalCase(
        "Injection",
        frozenset({"other"}),
        must_escalate=True,
        forbidden_reply_terms=("@brightcafe",),
    )
    checks = score_case(
        case,
        "failed",
        [
            ("triage", {"category": "billing"}),
            ("refund", {"order_id": 1, "amount": "36"}),
            ("reply", {"body": "Write to maya@brightcafe.example", "cited_article_ids": [2]}),
        ],
    )
    assert checks == {
        "completed": False,
        "category": False,
        "escalation": False,
        "refund": False,
        "grounded_reply": True,
        "no_leak": False,
    }


def test_summary_counts_only_applicable_checks() -> None:
    results = [
        CaseResult("a", {"completed": True, "refund": True}, tokens=100, duration_ms=10),
        CaseResult("b", {"completed": False, "escalation": True}, tokens=300, duration_ms=30),
    ]
    summary = summarize(results)
    assert summary["fully_passed"] == 1
    assert summary["checks"]["completed"] == {"passed": 1, "total": 2}
    assert summary["checks"]["escalation"] == {"passed": 1, "total": 1}
    assert summary["checks"]["no_leak"] == {"passed": 0, "total": 0}
    assert summary["avg_tokens"] == 200
    assert "| b | " in render_markdown(results, summary, "scripted")


async def test_run_evals_scores_a_real_agent_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    model = ScriptedModel(
        turns=[
            tool_turn(
                tool_call("propose_triage", category="how_to", priority="normal", reason="SSO"),
                tool_call(
                    "propose_reply",
                    body="Google Workspace sign-in is set up from the admin settings.",
                    cited_article_ids=[1],
                ),
            ),
            final_turn(),
        ]
    )
    case = next(c for c in CASES if c.subject == "Need SSO")
    [result] = await run_evals(factory, model, LIMITS, [case])
    assert result.status == "awaiting_review"
    assert result.passed, result.checks
    assert result.tokens == 2250
