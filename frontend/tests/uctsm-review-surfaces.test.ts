import assert from "node:assert/strict";
import test from "node:test";

import {
  DEPENDENCY_MODE_CHOICES, describeReadiness, displayConfinementStatus,
  summariseImpact, toApplicabilityReviewRows, toConfinementRows,
  toCompletionSummary, toDependencyReviewRows, toImpactRows, toParityRows,
  toTimelineEntries, toVersionContext,
} from "../src/features/uctsm/presentation.ts";
import type { PatientScheduleRow } from "../src/features/uctsm/presentation.ts";
import type { UniversalSchedule } from "../src/features/uctsm/types.ts";

/**
 * The review surfaces the UI specification asks for (s4.4, s4.5, s4.7, s4.10),
 * plus the two reviewer screens the requirement documents need: the
 * dependency-mode choice that unblocks approval (doc 4 s7) and the applicability
 * review (doc 8 s33).
 *
 * These are the screens a person reads before agreeing to something, so what
 * they emphasise matters: a consequential change buried twentieth in a list gets
 * skimmed, and a patient on an older schedule version looks like a bug unless
 * the screen says why.
 */

// --- UI s4.5: the confirm-before-apply table ---------------------------------

const IMPACT_EVENTS = [
  { logical_key: "WEEK_4:0", change: "UNCHANGED",
    before: { date: "2026-09-29" }, after: { date: "2026-09-29" } },
  { logical_key: "C1D1:0", change: "PROTECTED",
    before: { date: "2026-09-01", status: "COMPLETED" },
    after: { date: "2026-09-01", status: "COMPLETED" }, protected_history: true },
  { logical_key: "WEEK_8:0", change: "MOVED",
    before: { date: "2026-10-27" }, after: { date: "2026-11-03" } },
];

test("consequential changes sort ahead of unchanged and protected rows", () => {
  const rows = toImpactRows(IMPACT_EVENTS);

  assert.equal(rows[0].id, "WEEK_8:0");
  assert.equal(rows[0].consequential, true);
  assert.deepEqual(rows.slice(1).map((row) => row.consequential), [false, false]);
});

test("a completed visit is never counted as a change", () => {
  const rows = toImpactRows(IMPACT_EVENTS);
  const completed = rows.find((row) => row.id === "C1D1:0")!;

  assert.equal(completed.protectedHistory, true);
  assert.equal(completed.consequential, false);
  assert.equal(completed.change, "Already happened — unchanged");
});

test("the change vocabulary reads clinically, not as an enum", () => {
  const rows = toImpactRows([
    { logical_key: "A:0", change: "ADDED" },
    { logical_key: "B:0", change: "CANCELLED" },
  ]);

  assert.deepEqual(rows.map((row) => row.change).sort(),
    ["Newly required", "No longer required"]);
});

test("the summary states the size of the change and what is protected", () => {
  const summary = summariseImpact(toImpactRows(IMPACT_EVENTS));

  assert.match(summary, /1 visit would change/);
  assert.match(summary, /1 completed visit is protected/);
});

test("an impact that changes nothing says so plainly", () => {
  assert.equal(
    summariseImpact(toImpactRows([])),
    "Nothing in this patient's schedule would change.",
  );
  assert.match(
    summariseImpact(toImpactRows([{ logical_key: "A:0", change: "UNCHANGED" }])),
    /^No visit would change/,
  );
});

// --- UI s4.10 / doc 9 s12-13: which version produced these dates ---------------

test("a patient on an older version is told why, not warned", () => {
  const context = toVersionContext(
    { name: "Schedule of Assessments", version_number: 1, approved_at: "2026-08-01" },
    { newestVersionNumber: 3 },
  )!;

  assert.equal(context.label, "Schedule of Assessments v1");
  assert.equal(context.amended, true);
  assert.match(context.detail, /newer version \(v3\) exists/);
  assert.match(context.detail, /remains on the version they were enrolled under/);
});

test("a patient on the current version gets no amendment note", () => {
  const context = toVersionContext(
    { name: "Primary", version_number: 3, approved_at: "2026-08-01" },
    { newestVersionNumber: 3 },
  )!;

  assert.equal(context.amended, false);
  assert.doesNotMatch(context.detail, /newer version/);
});

test("an effective date is shown separately from approval", () => {
  const context = toVersionContext({
    name: "Primary", version_number: 2,
    approved_at: "2026-08-01", effective_from: "2026-10-01",
  })!;

  assert.match(context.detail, /Approved/);
  assert.match(context.detail, /In force from/);
});

test("no version means no header rather than an empty one", () => {
  assert.equal(toVersionContext(undefined), null);
});

// --- UI s4.7 / doc 6: one stay, its days underneath ---------------------------

test("a confinement is one row with its study days nested", () => {
  const rows = toConfinementRows([{
    episode_code: "PK_STAY", display_name: "PK confinement",
    status: "IN_CONFINEMENT",
    actual_admission: "2026-09-01", planned_discharge: "2026-09-04",
    days: [
      { day_label: "Day -1", relative_day: -1, scheduled_date: "2026-08-31",
        activity_codes: ["ADMISSION"] },
      { day_label: "Day 1", relative_day: 1, scheduled_date: "2026-09-01",
        activity_codes: ["DOSE", "PK_2H"] },
    ],
  }]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "Currently admitted");
  assert.equal(rows[0].days.length, 2);
  assert.equal(rows[0].days[1].activities, "Dose, Pk 2h");
});

test("a stay with no admission date says so instead of showing a blank range", () => {
  const rows = toConfinementRows([{
    episode_code: "PK_STAY", display_name: "PK confinement",
    status: "WAITING_FOR_ANCHOR", days: [],
  }]);

  assert.equal(rows[0].stay, "Awaiting admission date");
  assert.equal(rows[0].status, "Awaiting dates");
});

test("an ongoing stay is not given a fabricated discharge date", () => {
  const rows = toConfinementRows([{
    episode_code: "S", display_name: "Stay", status: "IN_CONFINEMENT",
    actual_admission: "2026-09-01", days: [],
  }]);

  assert.match(rows[0].stay, /ongoing$/);
});

test("confinement statuses read clinically", () => {
  assert.equal(displayConfinementStatus("DISCHARGED"), "Discharged");
  assert.equal(displayConfinementStatus("EXTENDED"), "Stay extended");
  assert.equal(displayConfinementStatus("SOMETHING_NEW"), "Something New");
});

// --- doc 4 s7: the choice that unblocks approval -------------------------------

const SCHEDULE = {
  schema_version: "uctsm.v1",
  schedule_version_id: "s1",
  schedule_metadata: { name: "Primary", schedule_type: "PRIMARY", version_number: 1, status: "DRAFT" },
  anchors: [], evidence: [], validation_issues: [],
  events: [
    {
      id: "e1", code: "SURGERY_FU", protocol_label: "V2", display_name: "Post-op follow-up",
      event_type: "SITE_VISIT", timing: { type: "OFFSET" },
      applicability: [{ dimension: "ARM", operator: "IN", values: ["SURGICAL"] }],
      conditions: [], dependencies: [{ source_event_code: "SURGERY", dependency_type: "TEMPORAL" }],
      dependency_mode: "UNCLEAR",
      activities: [], evidence_refs: [], interpretation_status: "EXTRACTED",
      requires_review: false,
    },
    {
      id: "e2", code: "WEEK_4", protocol_label: "V3", display_name: "Week 4",
      event_type: "SITE_VISIT", timing: { type: "PROTOCOL_DAY" },
      applicability: [], conditions: [], dependencies: [],
      activities: [], evidence_refs: [], interpretation_status: "CONFIRMED",
      requires_review: false,
    },
  ],
} satisfies UniversalSchedule;

test("only visits whose dependency rule is unclear need a decision", () => {
  const rows = toDependencyReviewRows(SCHEDULE);

  assert.deepEqual(rows.map((row) => row.visit), ["Post-op follow-up"]);
  assert.equal(rows[0].dependsOn, "Surgery");
  assert.equal(rows[0].current, "The protocol does not say");
});

test("each choice states what it does to real visit dates", () => {
  assert.deepEqual(
    DEPENDENCY_MODE_CHOICES.map((choice) => choice.value),
    ["NOMINAL", "ACTUAL_PREVIOUS_EVENT", "MANUAL"],
  );
  DEPENDENCY_MODE_CHOICES.forEach((choice) => {
    assert.ok(choice.effect.length > 20, `${choice.value} needs a real explanation`);
  });
  assert.match(
    DEPENDENCY_MODE_CHOICES[1].effect, /move by however late this one actually happened/);
});

// --- doc 8 s33: who each visit applies to --------------------------------------

test("a restricted visit names the group it is limited to", () => {
  const rows = toApplicabilityReviewRows(SCHEDULE);
  const restricted = rows.find((row) => row.visit === "Post-op follow-up")!;

  assert.equal(restricted.restricted, true);
  assert.equal(restricted.appliesTo, "Arm: SURGICAL");
});

test("an unrestricted visit says everyone rather than showing nothing", () => {
  const rows = toApplicabilityReviewRows(SCHEDULE);
  const open = rows.find((row) => row.visit === "Week 4")!;

  assert.equal(open.restricted, false);
  assert.equal(open.appliesTo, "All enrolled patients");
});

test("an exclusion reads as an exclusion", () => {
  const rows = toApplicabilityReviewRows({
    ...SCHEDULE,
    events: [{
      ...SCHEDULE.events[0],
      applicability: [{ dimension: "COHORT", operator: "NOT_IN", values: ["C3"] }],
    }],
  } as UniversalSchedule);

  assert.equal(rows[0].appliesTo, "Cohort: excluding C3");
});

// --- doc 1 s27: the patient timeline ------------------------------------------

function row(changes: Partial<PatientScheduleRow>): PatientScheduleRow {
  return {
    id: "r", eventDefinitionId: "d", visit: "V1", visitName: "Visit",
    timing: "—", window: "—", type: "—", activities: [],
    appliesTo: "All enrolled patients", status: "Scheduled", evidenceRefs: [],
    requiresReview: false, qualifier: "—", qualifierDetails: [],
    isConditional: false, allowedVisitModes: [], repeatRule: null,
    patientEventId: "pe", expectedDate: "—", actualDate: "—", allowedWindow: "—",
    dayScheduleRows: [], completion: toCompletionSummary([]),
    visitMode: "", unscheduledReason: null, isOnDemand: false,
    expectedIso: null, actualIso: null,
    ...changes,
  };
}

test("the timeline runs in date order, not in schedule order", () => {
  const entries = toTimelineEntries([
    row({ patientEventId: "b", visitName: "Week 8", expectedIso: "2026-10-27" }),
    row({ patientEventId: "a", visitName: "Week 4", expectedIso: "2026-09-29" }),
  ], "2026-10-01");

  assert.deepEqual(entries.map((entry) => entry.visit), ["Week 4", "Week 8"]);
});

test("an undated visit stays on the timeline rather than disappearing", () => {
  const entries = toTimelineEntries([
    row({ patientEventId: "u", visitName: "Post-op", expectedDate: "Awaiting Surgery Date" }),
    row({ patientEventId: "a", visitName: "Week 4", expectedIso: "2026-09-29" }),
  ], "2026-10-01");

  assert.deepEqual(entries.map((entry) => entry.visit), ["Week 4", "Post-op"]);
  assert.equal(entries[1].undated, true);
  assert.equal(entries[1].date, "Awaiting Surgery Date");
});

test("a visit that has happened is marked past so the timeline reads at a glance", () => {
  const entries = toTimelineEntries([
    row({ patientEventId: "a", visitName: "Week 4", expectedIso: "2026-09-29" }),
    row({ patientEventId: "b", visitName: "Week 8", expectedIso: "2026-10-27" }),
  ], "2026-10-01");

  assert.deepEqual(entries.map((entry) => entry.past), [true, false]);
});

test("an actual date replaces the expected one on the timeline", () => {
  const entries = toTimelineEntries([row({
    patientEventId: "a", visitName: "Week 4",
    expectedDate: "29 Sep 2026", expectedIso: "2026-09-29",
    actualDate: "03 Oct 2026", actualIso: "2026-10-03",
  })], "2026-11-01");

  assert.equal(entries[0].date, "03 Oct 2026");
});

// --- the cutover gate ---------------------------------------------------------

test("every parity difference reads as a reason not to cut over", () => {
  const rows = toParityRows([
    { kind: "DATE_DIFFERS", key: "week_4", name: "Week 4",
      legacy: "2026-09-29", engine: "2026-09-30",
      detail: "the two systems disagree by 1 day" },
    { kind: "MISSING_IN_ENGINE", key: "week_8", name: "Week 8",
      legacy: "2026-10-27", engine: null,
      detail: "the operational schedule has this visit and the engine does not" },
  ]);

  assert.equal(rows[0].kind, "Different date");
  assert.equal(rows[1].kind, "Only in the current system");
  assert.equal(rows[1].engine, "—");
  assert.equal(rows.length, 2);
});

test("an unmapped difference kind still reads as language", () => {
  const rows = toParityRows([
    { kind: "SOMETHING_NEW", key: "x", name: "X", detail: "explain" },
  ]);

  assert.equal(rows[0].kind, "Something New");
});

test("a ready trial says so plainly", () => {
  assert.match(
    describeReadiness({
      read_mode: "LEGACY", ready: true, patients: 4, checked: 4, reasons: [],
    }),
    /All 4 patients match; this trial can move to the engine\./,
  );
});

test("a trial that cannot move says why, not just that it cannot", () => {
  const line = describeReadiness({
    read_mode: "LEGACY", ready: false, patients: 4, checked: 3,
    reasons: ["1 patient(s) have not been compared against the version they are pinned to: P004"],
  });

  assert.match(line, /have not been compared/);
  assert.match(line, /P004/);
});

test("a trial already on the engine says where its dates come from", () => {
  assert.match(
    describeReadiness({
      read_mode: "ENGINE", ready: true, patients: 2, checked: 2, reasons: [],
    }),
    /^Visit dates come from the schedule engine\./,
  );
});

test("no status produces no claim", () => {
  assert.equal(describeReadiness(undefined), "");
  assert.deepEqual(toParityRows(undefined), []);
});

// --- doc 5 s26: how much of a visit is actually done --------------------------

function activity(code: string, status: string, requiredness = "REQUIRED") {
  return {
    id: code, activity_definition_id: code, activity_code: code,
    status, requiredness,
  };
}

test("completion counts required assessments, not every row", () => {
  const summary = toCompletionSummary([
    activity("VITALS", "COMPLETED"),
    activity("ECG", "COMPLETED"),
    activity("PK", "PENDING"),
    activity("OPTIONAL_BIOPSY", "PENDING", "OPTIONAL"),
  ] as never);

  assert.equal(summary.label, "2 of 3 required assessments complete");
  assert.equal(summary.complete, false);
  assert.deepEqual(summary.outstanding, ["Pk"]);
});

test("an optional assessment left undone does not make a visit incomplete", () => {
  const summary = toCompletionSummary([
    activity("VITALS", "COMPLETED"),
    activity("OPTIONAL_BIOPSY", "PENDING", "OPTIONAL"),
  ] as never);

  assert.equal(summary.complete, true);
  assert.equal(summary.label, "1 of 1 required assessments complete");
});

test("a visit with no required assessments says so rather than claiming 0 of 0", () => {
  const summary = toCompletionSummary([]);

  assert.equal(summary.label, "No required assessments recorded");
  assert.equal(summary.complete, false);
});

test("a not-done assessment counts as outstanding, not as complete", () => {
  const summary = toCompletionSummary([
    activity("VITALS", "NOT_DONE"),
    activity("ECG", "COMPLETED"),
  ] as never);

  assert.equal(summary.done, 1);
  assert.deepEqual(summary.outstanding, ["Vitals"]);
});
