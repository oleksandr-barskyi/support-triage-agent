import type { ReactNode } from "react";

const TONES = {
	neutral: "bg-zinc-500/10 text-zinc-700 dark:text-zinc-300",
	info: "bg-blue-500/10 text-blue-700 dark:text-blue-300",
	warn: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
	danger: "bg-red-500/10 text-red-700 dark:text-red-300",
	success: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
} as const;

export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
	return (
		<span
			className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
		>
			{children}
		</span>
	);
}
