import assert from "node:assert/strict";
import test from "node:test";
import {
  shouldPollStrategyRun,
  strategyStageLabel,
  toggleStrategyCompetitor,
  toUtcBoundary,
  validateStrategyInput,
} from "./strategy.ts";

test("compare validation requires two competitors and never adds tenant fields", () => {
  const error = validateStrategyInput({
    mode: "compare",
    query: "Compare pricing",
    competitor_ids: ["one"],
    date_from: "2026-05-01T00:00:00.000Z",
    date_to: "2026-07-31T23:59:59.999Z",
  });
  assert.equal(error, "Select at least two competitors to compare.");
});

test("strategy progress maps user-safe stages", () => {
  assert.equal(strategyStageLabel("validating_citations"), "Validating evidence citations");
  assert.equal(strategyStageLabel("awaiting_retry"), "Waiting to retry");
});

test("date inputs become explicit UTC boundaries", () => {
  assert.equal(toUtcBoundary("2026-07-31", true), "2026-07-31T23:59:59.999Z");
});

test("strategy polling pauses in hidden tabs and stops for terminal runs", () => {
  assert.equal(shouldPollStrategyRun("analyzing", false), true);
  assert.equal(shouldPollStrategyRun("analyzing", true), false);
  assert.equal(shouldPollStrategyRun("succeeded", false), false);
  assert.equal(shouldPollStrategyRun("cancelled", false), false);
});

test("competitor selection is bounded and supports deselection", () => {
  const selected = Array.from({ length: 10 }, (_, index) => `competitor-${index}`);
  assert.equal(toggleStrategyCompetitor(selected, "competitor-extra"), selected);
  assert.deepEqual(toggleStrategyCompetitor(selected, "competitor-3"), [
    "competitor-0",
    "competitor-1",
    "competitor-2",
    "competitor-4",
    "competitor-5",
    "competitor-6",
    "competitor-7",
    "competitor-8",
    "competitor-9",
  ]);
});
