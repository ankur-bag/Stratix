import assert from "node:assert/strict";
import test from "node:test";
import {
  strategyStageLabel,
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

