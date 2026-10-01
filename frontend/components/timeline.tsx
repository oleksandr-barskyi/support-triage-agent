import { describeStep, formatDuration } from "@/lib/run-state";
import type { Step } from "@/lib/types";

const KIND_STYLE: Record<Step["kind"], string> = {
	model: "bg-accent",
	tool: "bg-zinc-400",
	guardrail: "bg-amber-500",
};

function Json({ value }: { value: unknown }) {
	return (
		<pre className="max-h-64 overflow-auto rounded-md bg-zinc-500/5 p-2 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words">
			{JSON.stringify(value, null, 2)}
		</pre>
	);
}

export function Timeline({ steps, live }: { steps: Step[]; live: boolean }) {
	if (steps.length === 0) {
		return (
			<p className="text-sm text-muted">{live ? "Agent is starting" : "No steps recorded."}</p>
		);
	}
	return (
		<ol className="space-y-2">
			{steps.map((step) => {
				const modelText =
					step.kind === "model" ? (step.output as { text?: string } | null)?.text : undefined;
				return (
					<li key={step.index} className="flex gap-3">
						<span className={`mt-1.5 size-2 shrink-0 rounded-full ${KIND_STYLE[step.kind]}`} />
						<details className="min-w-0 flex-1 rounded-md border border-border bg-surface">
							<summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2 text-sm">
								<span className={step.is_error ? "text-red-600 dark:text-red-400" : ""}>
									{describeStep(step)}
								</span>
								<span className="shrink-0 font-mono text-xs text-muted">
									{step.duration_ms > 0 ? formatDuration(step.duration_ms) : step.kind}
								</span>
							</summary>
							<div className="space-y-2 border-t border-border p-3">
								{modelText && <p className="text-sm whitespace-pre-wrap">{modelText}</p>}
								{step.kind === "tool" && (
									<>
										<p className="text-xs font-medium text-muted">Input</p>
										<Json value={step.input} />
									</>
								)}
								<p className="text-xs font-medium text-muted">Output</p>
								<Json value={step.output} />
							</div>
						</details>
					</li>
				);
			})}
			{live && (
				<li className="flex items-center gap-3 text-sm text-muted">
					<span className="size-2 animate-pulse rounded-full bg-accent" />
					Working
				</li>
			)}
		</ol>
	);
}
