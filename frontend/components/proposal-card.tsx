"use client";

import { useState } from "react";
import { Badge, type Tone } from "@/components/badge";
import { ApiError, api } from "@/lib/api";
import type { Action } from "@/lib/types";

const TITLE: Record<Action["type"], string> = {
	triage: "Triage",
	reply: "Reply to customer",
	refund: "Refund",
	escalate: "Escalate to a senior agent",
};

const STATUS_TONE: Record<Action["status"], Tone> = {
	pending: "warn",
	approved: "success",
	rejected: "neutral",
};

const CATEGORIES = ["billing", "bug", "account", "how_to", "cancellation", "other"];
const PRIORITIES = ["low", "normal", "high", "urgent"];

const input = "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm";

function Editor({
	action,
	draft,
	setDraft,
	disabled,
}: {
	action: Action;
	draft: Action["payload"];
	setDraft: (p: Action["payload"]) => void;
	disabled: boolean;
}) {
	switch (action.type) {
		case "triage": {
			const p = draft as typeof action.payload;
			return (
				<div className="space-y-2">
					<div className="flex flex-wrap gap-2">
						<select
							disabled={disabled}
							value={p.category}
							onChange={(e) => setDraft({ ...p, category: e.target.value })}
							className={`${input} w-auto`}
						>
							{CATEGORIES.map((c) => (
								<option key={c}>{c}</option>
							))}
						</select>
						<select
							disabled={disabled}
							value={p.priority}
							onChange={(e) => setDraft({ ...p, priority: e.target.value })}
							className={`${input} w-auto`}
						>
							{PRIORITIES.map((c) => (
								<option key={c}>{c}</option>
							))}
						</select>
					</div>
					<p className="text-sm text-muted">{p.reason}</p>
				</div>
			);
		}
		case "reply": {
			const p = draft as typeof action.payload;
			return (
				<div className="space-y-2">
					<textarea
						disabled={disabled}
						rows={8}
						value={p.body}
						onChange={(e) => setDraft({ ...p, body: e.target.value })}
						className={input}
					/>
					{p.cited_article_ids.length > 0 && (
						<p className="text-xs text-muted">
							Grounded in help center articles{" "}
							{p.cited_article_ids.map((id) => `#${id}`).join(", ")}
						</p>
					)}
				</div>
			);
		}
		case "refund": {
			const p = draft as typeof action.payload;
			return (
				<div className="space-y-2 text-sm">
					<div className="flex items-center gap-2">
						<span>Order #{p.order_id}, amount USD</span>
						<input
							disabled={disabled}
							type="number"
							min={0.01}
							step={0.01}
							value={p.amount}
							onChange={(e) => setDraft({ ...p, amount: e.target.value })}
							className={`${input} w-28`}
						/>
					</div>
					<p className="text-muted">{p.reason}</p>
				</div>
			);
		}
		case "escalate":
			return <p className="text-sm">{action.payload.reason}</p>;
	}
}

export function ProposalCard({
	action,
	onChange,
}: {
	action: Action;
	onChange: (updated: Action) => void;
}) {
	const [draft, setDraft] = useState<Action["payload"]>(action.payload);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const pending = action.status === "pending";
	const edited = JSON.stringify(draft) !== JSON.stringify(action.payload);

	const decide = async (approve: boolean) => {
		setBusy(true);
		setError(null);
		try {
			onChange(
				approve
					? await api.approve(action.id, edited ? draft : undefined)
					: await api.reject(action.id),
			);
		} catch (e) {
			setError(e instanceof ApiError ? e.message : "Request failed");
		} finally {
			setBusy(false);
		}
	};

	return (
		<article className="space-y-3 rounded-lg border border-border bg-surface p-4">
			<header className="flex items-center justify-between gap-2">
				<h3 className="font-medium">{TITLE[action.type]}</h3>
				<div className="flex gap-1">
					{action.edited && <Badge tone="info">edited</Badge>}
					<Badge tone={STATUS_TONE[action.status]}>{action.status}</Badge>
				</div>
			</header>
			<Editor action={action} draft={draft} setDraft={setDraft} disabled={!pending || busy} />
			{error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
			{pending && (
				<div className="flex gap-2">
					<button
						type="button"
						disabled={busy}
						onClick={() => decide(true)}
						className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
					>
						{edited ? "Approve with edits" : "Approve"}
					</button>
					<button
						type="button"
						disabled={busy}
						onClick={() => decide(false)}
						className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-60"
					>
						Reject
					</button>
				</div>
			)}
		</article>
	);
}
