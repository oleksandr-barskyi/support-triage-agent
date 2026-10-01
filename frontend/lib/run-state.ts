import type { Action, RunStatus, Step, TicketStatus } from "./types";

export const ACTIVE_RUN: ReadonlySet<RunStatus> = new Set(["queued", "running"]);

export function isRunActive(status: RunStatus | undefined): boolean {
	return status !== undefined && ACTIVE_RUN.has(status);
}

export function mergeStep(steps: readonly Step[], step: Step): Step[] {
	if (steps.some((s) => s.index === step.index)) return [...steps];
	return [...steps, step].sort((a, b) => a.index - b.index);
}

export function pendingCount(actions: readonly Action[]): number {
	return actions.filter((a) => a.status === "pending").length;
}

export function replaceAction(actions: readonly Action[], updated: Action): Action[] {
	return actions.map((a) => (a.id === updated.id ? updated : a));
}

export function formatCost(usd: string | number): string {
	const value = Number(usd);
	if (!Number.isFinite(value) || value === 0) return "$0";
	if (value < 0.01) return "<$0.01";
	return `$${value.toFixed(value < 1 ? 3 : 2)}`;
}

export function formatDuration(ms: number): string {
	if (ms < 1000) return `${ms} ms`;
	const seconds = ms / 1000;
	if (seconds < 60) return `${seconds.toFixed(1)} s`;
	return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

type Tone = "neutral" | "info" | "warn" | "danger" | "success";

const TICKET_STATUS: Record<TicketStatus, { label: string; tone: Tone }> = {
	new: { label: "New", tone: "neutral" },
	triaging: { label: "Agent working", tone: "info" },
	awaiting_review: { label: "Needs review", tone: "warn" },
	escalated: { label: "Escalated", tone: "danger" },
	answered: { label: "Answered", tone: "success" },
	failed: { label: "Failed", tone: "danger" },
};

export function ticketStatus(status: TicketStatus): { label: string; tone: Tone } {
	return TICKET_STATUS[status];
}

export function describeStep(step: Step): string {
	if (step.kind === "guardrail") {
		const note = (step.output as { note?: string } | null)?.note;
		return note ?? "Guardrail";
	}
	if (step.kind === "model") {
		const out = step.output as { tool_calls?: string[]; stop_reason?: string } | null;
		const calls = out?.tool_calls ?? [];
		return calls.length > 0 ? `Model decided to call ${calls.join(", ")}` : "Model finished";
	}
	return step.is_error ? `${step.tool_name} returned an error` : `${step.tool_name}`;
}
