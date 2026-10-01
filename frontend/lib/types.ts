export type TicketStatus =
	| "new"
	| "triaging"
	| "awaiting_review"
	| "escalated"
	| "answered"
	| "failed";

export type RunStatus = "queued" | "running" | "awaiting_review" | "escalated" | "failed" | "done";

export type ActionType = "triage" | "reply" | "refund" | "escalate";
export type ActionStatus = "pending" | "approved" | "rejected";

export interface Step {
	index: number;
	kind: "model" | "tool" | "guardrail";
	tool_name: string | null;
	input: unknown;
	output: unknown;
	is_error: boolean;
	duration_ms: number;
}

export interface TriagePayload {
	category: string;
	priority: string;
	reason: string;
}

export interface ReplyPayload {
	body: string;
	cited_article_ids: number[];
}

export interface RefundPayload {
	order_id: number;
	amount: string | number;
	reason: string;
}

export interface EscalatePayload {
	reason: string;
}

interface ActionBase<T extends ActionType, P> {
	id: number;
	type: T;
	payload: P;
	status: ActionStatus;
	edited: boolean;
	decision_note: string | null;
	decided_at: string | null;
}

export type Action =
	| ActionBase<"triage", TriagePayload>
	| ActionBase<"reply", ReplyPayload>
	| ActionBase<"refund", RefundPayload>
	| ActionBase<"escalate", EscalatePayload>;

export interface RunSummary {
	id: number;
	status: RunStatus;
	model: string;
	input_tokens: number;
	output_tokens: number;
	cost_usd: string;
	duration_ms: number;
	error: string | null;
	created_at: string;
}

export interface Run extends RunSummary {
	ticket_id: number;
	steps: Step[];
	actions: Action[];
}

export interface TicketBase {
	id: number;
	customer_email: string;
	subject: string;
	status: TicketStatus;
	category: string | null;
	priority: string | null;
	created_at: string;
}

export interface TicketListItem extends TicketBase {
	latest_run: RunSummary | null;
}

export interface Ticket extends TicketBase {
	body: string;
	latest_run: Run | null;
}

export interface TicketCreate {
	customer_email: string;
	subject: string;
	body: string;
}
