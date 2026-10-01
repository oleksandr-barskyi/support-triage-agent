import type { Action, Run, RunStatus, Step, Ticket, TicketCreate, TicketListItem } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
	/\/$/,
	"",
);

export class ApiError extends Error {
	constructor(
		readonly status: number,
		message: string,
	) {
		super(message);
	}
}

function detailOf(body: unknown, fallback: string): string {
	if (body && typeof body === "object" && "detail" in body) {
		const detail = (body as { detail: unknown }).detail;
		if (typeof detail === "string") return detail;
		if (Array.isArray(detail)) {
			return detail
				.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : ""))
				.filter(Boolean)
				.join("; ");
		}
	}
	return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const response = await fetch(`${API_URL}${path}`, {
		...init,
		headers: { "Content-Type": "application/json", ...init?.headers },
		cache: "no-store",
	});
	const body: unknown = await response.json().catch(() => null);
	if (!response.ok) {
		throw new ApiError(response.status, detailOf(body, response.statusText));
	}
	return body as T;
}

export const api = {
	health: () => request<{ status: string }>("/health"),
	listTickets: () => request<TicketListItem[]>("/tickets"),
	getTicket: (id: number) => request<Ticket>(`/tickets/${id}`),
	createTicket: (data: TicketCreate) =>
		request<Ticket>("/tickets", { method: "POST", body: JSON.stringify(data) }),
	rerun: (id: number) => request<Ticket>(`/tickets/${id}/rerun`, { method: "POST" }),
	approve: (id: number, payload?: Action["payload"], note?: string) =>
		request<Action>(`/actions/${id}/approve`, {
			method: "POST",
			body: JSON.stringify({ payload: payload ?? null, note: note ?? null }),
		}),
	reject: (id: number, note?: string) =>
		request<Action>(`/actions/${id}/reject`, {
			method: "POST",
			body: JSON.stringify({ note: note ?? null }),
		}),
};

export interface RunStreamHandlers {
	onStep: (step: Step) => void;
	onStatus: (status: RunStatus) => void;
	onDone: (run: Run) => void;
	onError: () => void;
}

export function streamRun(runId: number, handlers: RunStreamHandlers): () => void {
	const source = new EventSource(`${API_URL}/runs/${runId}/stream`);
	source.addEventListener("step", (e) => handlers.onStep(JSON.parse(e.data)));
	source.addEventListener("status", (e) => handlers.onStatus(JSON.parse(e.data).status));
	source.addEventListener("done", (e) => {
		handlers.onDone(JSON.parse(e.data));
		source.close();
	});
	source.onerror = () => {
		source.close();
		handlers.onError();
	};
	return () => source.close();
}
