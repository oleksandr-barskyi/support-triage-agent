"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/badge";
import { api } from "@/lib/api";
import { formatCost, ticketStatus } from "@/lib/run-state";
import type { TicketListItem } from "@/lib/types";

const PRIORITY_TONE = {
	urgent: "danger",
	high: "warn",
	normal: "neutral",
	low: "neutral",
} as const;

export default function InboxPage() {
	const [tickets, setTickets] = useState<TicketListItem[] | null>(null);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(async () => {
		try {
			setTickets(await api.listTickets());
			setError(null);
		} catch {
			setError("Could not load tickets. The backend may still be waking up.");
		}
	}, []);

	useEffect(() => {
		load();
		const timer = setInterval(load, 8000);
		return () => clearInterval(timer);
	}, [load]);

	return (
		<div className="space-y-6">
			<section className="space-y-2">
				<h1 className="text-2xl font-semibold">Support inbox</h1>
				<p className="max-w-3xl text-muted">
					Open a ticket and the agent investigates it with tools: customer, plan, charges, help
					center, similar tickets. It proposes a triage, a reply, refunds or an escalation. Nothing
					happens until you approve it.
				</p>
			</section>

			{error && (
				<p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">{error}</p>
			)}

			{tickets === null && !error && <p className="text-muted">Loading tickets</p>}

			{tickets && (
				<ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface">
					{tickets.map((t) => {
						const status = ticketStatus(t.status);
						return (
							<li key={t.id}>
								<Link
									href={`/tickets/${t.id}`}
									className="flex flex-col gap-2 p-4 hover:bg-zinc-500/5 sm:flex-row sm:items-center sm:justify-between"
								>
									<div className="min-w-0 space-y-1">
										<p className="truncate font-medium">{t.subject}</p>
										<p className="truncate text-sm text-muted">{t.customer_email}</p>
									</div>
									<div className="flex shrink-0 flex-wrap items-center gap-2 text-sm">
										{t.category && <Badge>{t.category.replace("_", " ")}</Badge>}
										{t.priority && (
											<Badge
												tone={PRIORITY_TONE[t.priority as keyof typeof PRIORITY_TONE] ?? "neutral"}
											>
												{t.priority}
											</Badge>
										)}
										<Badge tone={status.tone}>{status.label}</Badge>
										{t.latest_run && (
											<span className="font-mono text-xs text-muted">
												{formatCost(t.latest_run.cost_usd)}
											</span>
										)}
									</div>
								</Link>
							</li>
						);
					})}
				</ul>
			)}
		</div>
	);
}
