import { describe, expect, it } from "vitest";
import {
	describeStep,
	formatCost,
	formatDuration,
	isRunActive,
	mergeStep,
	pendingCount,
	replaceAction,
} from "./run-state";
import type { Action, Step } from "./types";

const step = (index: number, extra: Partial<Step> = {}): Step => ({
	index,
	kind: "tool",
	tool_name: "search_kb",
	input: {},
	output: {},
	is_error: false,
	duration_ms: 5,
	...extra,
});

const action = (id: number, status: Action["status"] = "pending"): Action => ({
	id,
	type: "escalate",
	payload: { reason: "x" },
	status,
	edited: false,
	decision_note: null,
	decided_at: null,
});

describe("mergeStep", () => {
	it("keeps steps ordered when they arrive out of order", () => {
		expect(mergeStep([step(0), step(2)], step(1)).map((s) => s.index)).toEqual([0, 1, 2]);
	});

	it("ignores a step that was already received", () => {
		const steps = [step(0), step(1)];
		expect(mergeStep(steps, step(1, { tool_name: "other" }))).toEqual(steps);
	});
});

describe("actions", () => {
	it("counts only pending proposals", () => {
		expect(pendingCount([action(1), action(2, "approved"), action(3)])).toBe(2);
	});

	it("replaces an action by id without touching the others", () => {
		const updated = replaceAction([action(1), action(2)], action(2, "rejected"));
		expect(updated.map((a) => a.status)).toEqual(["pending", "rejected"]);
	});
});

describe("formatting", () => {
	it.each([
		["0", "$0"],
		["0.004", "<$0.01"],
		["0.029", "$0.029"],
		["1.5", "$1.50"],
	])("formats cost %s as %s", (input, expected) => {
		expect(formatCost(input)).toBe(expected);
	});

	it.each([
		[420, "420 ms"],
		[12_340, "12.3 s"],
		[95_000, "1 min 35 s"],
	])("formats %d ms as %s", (input, expected) => {
		expect(formatDuration(input)).toBe(expected);
	});

	it("treats queued and running as active", () => {
		expect(isRunActive("running")).toBe(true);
		expect(isRunActive("awaiting_review")).toBe(false);
		expect(isRunActive(undefined)).toBe(false);
	});
});

describe("describeStep", () => {
	it("names the tools the model chose", () => {
		const s = step(0, { kind: "model", output: { tool_calls: ["get_customer", "search_kb"] } });
		expect(describeStep(s)).toBe("Model decided to call get_customer, search_kb");
	});

	it("surfaces guardrail notes", () => {
		const s = step(0, { kind: "guardrail", output: { note: "Automatic escalation: turn limit" } });
		expect(describeStep(s)).toBe("Automatic escalation: turn limit");
	});

	it("flags tool errors", () => {
		expect(describeStep(step(0, { is_error: true }))).toBe("search_kb returned an error");
	});
});
