"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/badge";
import { ProposalCard } from "@/components/proposal-card";
import { Timeline } from "@/components/timeline";
import { ApiError, api, streamRun } from "@/lib/api";
import {
	formatCost,
	formatDuration,
	isRunActive,
	mergeStep,
	replaceAction,
	ticketStatus,
} from "@/lib/run-state";
import type { Action, Run, RunStatus, Step, Ticket } from "@/lib/types";

export default function TicketPage({ params }: { params: Promise<{ id: string }> }) {
	const ticketId = Number(use(params).id);
	const [ticket, setTicket] = useState<Ticket | null>(null);
	const [run, setRun] = useState<Run | null>(null);
	const [steps, setSteps] = useState<Step[]>([]);
	const [liveStatus, setLiveStatus] = useState<RunStatus | undefined>();
	const [error, setError] = useState<string | null>(null);
	const [starting, setStarting] = useState(false);

	const load = useCallback(async () => {
		try {
			const data = await api.getTicket(ticketId);
			setTicket(data);
			setRun(data.latest_run);
			setSteps(data.latest_run?.steps ?? []);
			setLiveStatus(data.latest_run?.status);
		} catch (e) {
			setError(e instanceof ApiError ? e.message : "Could not load the ticket.");
		}
	}, [ticketId]);

	useEffect(() => {
		load();
	}, [load]);

	const runId = run?.id;
	const active = isRunActive(liveStatus);

	useEffect(() => {
		if (runId === undefined || !active) return;
		return streamRun(runId, {
			onStep: (step) => setSteps((prev) => mergeStep(prev, step)),
			onStatus: setLiveStatus,
			onDone: () => load(),
			onError: () => setTimeout(load, 2000),
		});
	}, [runId, active, load]);

	const startRun = async () => {
		setStarting(true);
		setError(null);
		try {
			const data = await api.rerun(ticketId);
			setTicket(data);
			setRun(data.latest_run);
			setSteps(data.latest_run?.steps ?? []);
			setLiveStatus(data.latest_run?.status);
		} catch (e) {
			setError(e instanceof ApiError ? e.message : "Could not start the agent.");
		} finally {
			setStarting(false);
		}
	};

	const onActionChange = (updated: Action) => {
		setRun((prev) => (prev ? { ...prev, actions: replaceAction(prev.actions, updated) } : prev));
		api.getTicket(ticketId).then((data) => setTicket(data));
	};

	if (error && !ticket) {
		return <p className="text-red-600 dark:text-red-400">{error}</p>;
	}
	if (!ticket) {
		return <p className="text-muted">Loading ticket</p>;
	}

	const status = ticketStatus(ticket.status);

	return (
		<div className="space-y-6">
			<Link href="/" className="text-sm text-muted hover:underline">
				Back to inbox
			</Link>

			<section className="space-y-3 rounded-lg border border-border bg-surface p-5">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="space-y-1">
						<h1 className="text-xl font-semibold">{ticket.subject}</h1>
						<p className="text-sm text-muted">From {ticket.customer_email}</p>
					</div>
					<div className="flex flex-wrap gap-2">
						{ticket.category && <Badge>{ticket.category.replace("_", " ")}</Badge>}
						{ticket.priority && <Badge>{ticket.priority}</Badge>}
						<Badge tone={status.tone}>{status.label}</Badge>
					</div>
				</div>
				<p className="whitespace-pre-wrap">{ticket.body}</p>
			</section>

			{error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

			<div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
				<section className="space-y-3">
					<div className="flex flex-wrap items-center justify-between gap-2">
						<h2 className="font-semibold">Agent trace</h2>
						<button
							type="button"
							disabled={active || starting}
							onClick={startRun}
							className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50"
						>
							{run ? "Run the agent again" : "Run the agent"}
						</button>
					</div>
					{run ? (
						<>
							<p className="font-mono text-xs text-muted">
								{run.model} · {run.input_tokens + run.output_tokens} tokens ·{" "}
								{formatCost(run.cost_usd)} · {formatDuration(run.duration_ms)}
								{run.error ? ` · ${run.error}` : ""}
							</p>
							<Timeline steps={steps} live={active} />
						</>
					) : (
						<p className="text-sm text-muted">
							The agent has not looked at this ticket yet. Run it to watch it investigate.
						</p>
					)}
				</section>

				<section className="space-y-3">
					<h2 className="font-semibold">Proposals for review</h2>
					{run && !active && run.actions.length === 0 && (
						<p className="text-sm text-muted">No proposals in this run.</p>
					)}
					{active && (
						<p className="text-sm text-muted">Proposals appear when the agent finishes.</p>
					)}
					{run &&
						!active &&
						run.actions.map((action) => (
							<ProposalCard key={action.id} action={action} onChange={onActionChange} />
						))}
				</section>
			</div>
		</div>
	);
}
