"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type State = "waking" | "up" | "down";

const LABEL: Record<State, string> = {
	waking: "Waking the backend",
	up: "Backend online",
	down: "Backend unreachable",
};

const DOT: Record<State, string> = {
	waking: "bg-amber-500 animate-pulse",
	up: "bg-emerald-500",
	down: "bg-red-500",
};

export function BackendStatus() {
	const [state, setState] = useState<State>("waking");

	useEffect(() => {
		let cancelled = false;
		const check = async (attempt: number) => {
			try {
				await api.health();
				if (!cancelled) setState("up");
			} catch {
				if (cancelled) return;
				if (attempt < 12) setTimeout(() => check(attempt + 1), 5000);
				else setState("down");
			}
		};
		check(0);
		return () => {
			cancelled = true;
		};
	}, []);

	return (
		<span className="flex items-center gap-2 text-muted" title={LABEL[state]}>
			<span className={`size-2 rounded-full ${DOT[state]}`} />
			<span className="hidden md:inline">{LABEL[state]}</span>
		</span>
	);
}
