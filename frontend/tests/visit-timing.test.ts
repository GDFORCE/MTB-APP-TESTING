import assert from "node:assert/strict";
import test from "node:test";

import {
  formatProtocolProcedure,
  formatScheduleDay,
  formatVisitWindow,
  normalizeProtocolConstraints,
  normalizeProtocolProcedures,
} from "../src/lib/visit-timing.ts";

test("an unstated visit window stays unstated beside a procedure tolerance", () => {
  assert.equal(formatVisitWindow({}, true), "-");
  const [pkDraw] = normalizeProtocolProcedures([{
    name: "PK blood draw",
    timing: "0.083-48 hours post-dose",
    window: "±2 minutes",
  }]);
  assert.equal(pkDraw.window, "±2 minutes");
  assert.match(formatProtocolProcedure(pkDraw), /Procedure tolerance: ±2 minutes/);
});

test("canonical and historical procedure payloads are normalised safely", () => {
  assert.deepEqual(normalizeProtocolProcedures([
    { id: "a1", name: "Infusion", timing: "Day 1", condition: "30 minutes" },
    { label: "Vitals", description: "Before dosing" },
    "ECG",
    null,
    {},
  ]), [
    { id: "a1", name: "Infusion", timing: "Day 1", condition: "30 minutes", description: undefined, window: undefined, evidence_ids: undefined },
    { id: undefined, name: "Vitals", description: "Before dosing", timing: undefined, window: undefined, condition: undefined, evidence_ids: undefined },
    { name: "ECG" },
  ]);
});

test("operational constraints remain distinct list items", () => {
  assert.deepEqual(
    normalizeProtocolConstraints("At least 12 hours housing\n\nAt least 21 days washout"),
    ["At least 12 hours housing", "At least 21 days washout"],
  );
});

test("the compact Day column does not display free-form timing prose", () => {
  assert.equal(formatScheduleDay({ source_day_label: "Day 1", day_offset: 0 }), "+1");
  assert.equal(formatScheduleDay({ source_day_label: "30 days after last dose", day_offset: null }), "-");
});
