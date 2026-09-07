import assert from "node:assert/strict";
import test from "node:test";

import {
  toEnrolmentReadiness, toProtocolActivityRows, toScheduleVersionRows,
} from "../src/features/uctsm/presentation.ts";
import type { CanonicalScheduleVersion } from "../src/features/uctsm/presentation.ts";
import type { ScheduleActivity } from "../src/features/uctsm/types.ts";

/**
 * Case 10 / doc 9: the trial-level protocol and schedule version table, and the
 * question it exists to answer - which schedule a patient enrolling today gets,
 * and which one the patients already on study stay on.
 *
 * The failure this guards against is silent: a screen that shows only the newest
 * version makes twelve patients on v1 look like they are on v2, and nothing in
 * the UI would contradict that.
 */

const APPROVED_V1: CanonicalScheduleVersion = {
  schedule_definition_id: "d1", schedule_version_id: "v1", name: "Primary",
  version_number: 1, status: "SUPERSEDED", protocol_version_label: "v1.0",
  patients: 12,
};
const APPROVED_V2: CanonicalScheduleVersion = {
  schedule_definition_id: "d1", schedule_version_id: "v2", name: "Primary",
  version_number: 2, status: "APPROVED", protocol_version_label: "v2.0",
  patients: 5,
};

test("the in-force version is the one new enrollments receive", () => {
  const rows = toScheduleVersionRows([APPROVED_V1, APPROVED_V2]);

  const current = rows.find((row) => row.id === "v2");
  assert.equal(current?.effectiveFor, "Current new enrollments");
  assert.equal(current?.current, true);
  assert.equal(current?.version, "Schedule v2");
  assert.equal(current?.protocolVersion, "v2.0");
});

test("a superseded version with patients stays listed as still serving them", () => {
  const rows = toScheduleVersionRows([APPROVED_V1, APPROVED_V2]);

  const historical = rows.find((row) => row.id === "v1");
  assert.equal(historical?.effectiveFor, "Earlier enrollments");
  assert.equal(historical?.patients, "12");
  assert.equal(historical?.current, false);
});

test("a draft is shown as needing review, not as the schedule in use", () => {
  const rows = toScheduleVersionRows([{
    schedule_definition_id: "d1", schedule_version_id: "v1", name: "Primary",
    version_number: 1, status: "VALIDATION_REQUIRED", protocol_version_label: "v1.0",
  }]);

  assert.equal(rows[0].status, "Needs Review");
  assert.equal(rows[0].effectiveFor, "Not in use");
  assert.equal(rows[0].current, false);
});

test("a version approved for a future date is not presented as current", () => {
  const rows = toScheduleVersionRows([{
    ...APPROVED_V2, effective_from: "2999-01-01", patients: 0,
  }]);

  assert.equal(rows[0].current, false);
  assert.equal(rows[0].effectiveFor, "Takes effect 2999-01-01");
});

test("each substudy schedule keeps its own current version", () => {
  const rows = toScheduleVersionRows([
    APPROVED_V2,
    { ...APPROVED_V2, schedule_definition_id: "d2", schedule_version_id: "v3",
      name: "Substudy B", version_number: 1 },
  ]);

  assert.deepEqual(rows.filter((row) => row.current).map((row) => row.id), ["v2", "v3"]);
});

// --- enrolment readiness -----------------------------------------------------

test("enrolment is ready only against an approved, in-force version", () => {
  const ready = toEnrolmentReadiness([APPROVED_V1, APPROVED_V2]);

  assert.equal(ready.ready, true);
  assert.equal(ready.scheduleVersionId, "v2");
  assert.match(ready.message, /Schedule v2/);
});

test("a schedule still in review blocks enrolment with the reason said plainly", () => {
  const ready = toEnrolmentReadiness([{
    schedule_definition_id: "d1", schedule_version_id: "v1", name: "Primary",
    version_number: 1, status: "VALIDATION_REQUIRED",
  }]);

  assert.equal(ready.ready, false);
  assert.equal(ready.scheduleVersionId, null);
  assert.match(ready.message, /still in review/);
});

test("an approval that has not taken effect names the date instead of enrolling", () => {
  const ready = toEnrolmentReadiness([{ ...APPROVED_V2, effective_from: "2999-01-01" }]);

  assert.equal(ready.ready, false);
  assert.match(ready.message, /2999-01-01/);
});

test("a trial with no canonical schedule says so rather than failing on submit", () => {
  assert.equal(toEnrolmentReadiness([]).ready, false);
  assert.match(toEnrolmentReadiness(undefined).message, /no canonical schedule/);
});

// --- Case 6 at protocol-review level -----------------------------------------

const activity = (changes: Partial<ScheduleActivity>): ScheduleActivity => ({
  id: changes.id || "a1", display_name: changes.display_name || "PK +1h",
  activity_type: "PROTOCOL_ACTIVITY", requiredness: "REQUIRED", ...changes,
});

test("an activity states what it is timed against, not a clock time", () => {
  const rows = toProtocolActivityRows([activity({
    display_name: "PK +1h",
    timing: {
      type: "NOMINAL_WITH_WINDOW",
      nominal: {
        type: "OFFSET", offset: { value: 1, unit: "HOUR" },
        reference: { kind: "ACTIVITY", activity_code: "DOSE" },
      },
      window: { before: { value: 5, unit: "MINUTE" }, after: { value: 5, unit: "MINUTE" } },
    },
  })]);

  assert.equal(rows[0].timingRule, "+1 hour");
  // The anchor is the DOSE's actual recorded time, and the row has to say so.
  assert.equal(rows[0].anchor, "Dose (actual time)");
  assert.equal(rows[0].window, "±5 minutes");
  assert.equal(rows[0].review, "OK");
});

test("a pre-dose activity reads as a negative offset from its anchor", () => {
  const rows = toProtocolActivityRows([activity({
    display_name: "Pre-dose ECG",
    timing: {
      type: "OFFSET", offset: { value: -30, unit: "MINUTE" },
      reference: { kind: "ACTIVITY", activity_code: "DOSE" },
    },
  })]);

  assert.equal(rows[0].timingRule, "-30 minutes");
  assert.equal(rows[0].anchor, "Dose (actual time)");
});

test("an activity with no stated tolerance shows no window", () => {
  const rows = toProtocolActivityRows([activity({
    timing: {
      type: "OFFSET", offset: { value: 2, unit: "HOUR" },
      reference: { kind: "ANCHOR", code: "BASELINE" },
    },
  })]);

  assert.equal(rows[0].window, "—");
  assert.equal(rows[0].anchor, "Baseline");
});

test("an activity with no timing at all is shown without inventing one", () => {
  const rows = toProtocolActivityRows([activity({ display_name: "Labs" })]);

  assert.equal(rows[0].activity, "Labs");
  assert.equal(rows[0].timingRule, "—");
  assert.equal(rows[0].anchor, "—");
});

test("an unresolved activity timing carries its reason into the review column", () => {
  const rows = toProtocolActivityRows([activity({
    timing: { type: "UNRESOLVED", reason: "Protocol says 'periodically'" },
  })]);

  assert.equal(rows[0].timingRule, "Protocol says 'periodically'");
  assert.equal(rows[0].review, "Unresolved");
});

test("an unresolved footnote on an activity marks the row for review", () => {
  const rows = toProtocolActivityRows([activity({
    qualifiers: [{
      id: "q1", marker: "a", text: "Only if clinically indicated", scope: "ACTIVITY",
      category: "CONDITION", target_codes: ["ECG"], resolved: false,
    }] as any,
  })]);

  assert.equal(rows[0].review, "Needs review");
  assert.notEqual(rows[0].qualifier, "");
});
