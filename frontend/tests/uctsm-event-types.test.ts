import assert from "node:assert/strict";
import test from "node:test";

import {
  displayPatientState, displayVisitMode, toActionBoardGroups, toAnchorStatusRows,
  toEnrolmentFields, toPatientScheduleRows, toProtocolScheduleRows,
  toRepeatRuleRows, toScheduleChangeRows,
} from "../src/features/uctsm/presentation.ts";
import type {
  PatientScheduleResponse, ScheduleProjection, UniversalSchedule,
} from "../src/features/uctsm/types.ts";

/**
 * Event types, visit mode, and unscheduled visits in the UI
 * (MTB requirement doc 10, sections 13-15, 18, 21-23, 31-33; doc 3 s4 and s36).
 *
 * The failures these guard against are all "the screen said something the
 * protocol did not": a clinic visit the protocol never specified, an unscheduled
 * definition shown as due, and a repeating schedule that appears to have ended
 * at the last row on screen.
 */

const schedule = {
  schema_version: "uctsm.v1",
  schedule_version_id: "schedule-1",
  schedule_metadata: {
    name: "Protocol Schedule", schedule_type: "PRIMARY", version_number: 1,
    status: "APPROVED",
  },
  anchors: [],
  evidence: [],
  validation_issues: [],
  events: [
    {
      id: "event-unscheduled",
      code: "UNSCHEDULED_VISIT",
      protocol_label: "Unscheduled Visit",
      display_name: "Unscheduled Visit",
      event_type: "UNSCHEDULED",
      activation: "ON_DEMAND",
      allowed_visit_modes: ["CLINIC", "TELEPHONE"],
      timing: { type: "PROTOCOL_DEFINED" },
      applicability: [],
      conditions: [],
      dependencies: [],
      activities: [],
      evidence_refs: [],
      interpretation_status: "CONFIRMED",
      requires_review: false,
    },
    {
      id: "event-week4",
      code: "WEEK_4",
      protocol_label: "Visit 3",
      display_name: "Week 4",
      event_type: "SITE_VISIT",
      visit_mode: "TELEPHONE",
      timing: { type: "PROTOCOL_DAY" },
      applicability: [],
      conditions: [],
      dependencies: [],
      activities: [],
      evidence_refs: [],
      interpretation_status: "CONFIRMED",
      requires_review: false,
    },
  ],
} satisfies UniversalSchedule;

const projections: ScheduleProjection[] = [];

function patientSchedule(
  events: PatientScheduleResponse["events"],
  repeatRules: PatientScheduleResponse["repeat_rules"] = [],
): PatientScheduleResponse {
  return {
    patient_id: "patient-1", status: "ACTIVE",
    schedule_version_id: "schedule-1", evaluation_id: "evaluation-1",
    events, repeat_rules: repeatRules,
  };
}

// --- doc 10 s18 and s33: never imply a mode the protocol did not state --------

test("an unstated visit mode says so rather than showing a clinic visit", () => {
  assert.equal(displayVisitMode(null, []), "Not stated in the protocol");
  assert.equal(displayVisitMode(undefined, undefined), "Not stated in the protocol");
});

test("a stated mode reads as clinical language, not as an enum", () => {
  assert.equal(displayVisitMode("PHONE_CONTACT"), "By telephone");
  assert.equal(displayVisitMode("CLINIC"), "In clinic");
  assert.equal(displayVisitMode("HOME_NURSE"), "Home nurse visit");
});

test("a hybrid visit shows both options and marks the choice as outstanding", () => {
  assert.equal(
    displayVisitMode(null, ["CLINIC", "TELEPHONE"]),
    "In clinic or By telephone — to be confirmed",
  );
});

test("a single allowed mode needs no confirmation prompt", () => {
  assert.equal(displayVisitMode(null, ["CLINIC"]), "In clinic");
});

// --- doc 10 s13-s15: available is not due ------------------------------------

test("an on-demand visit reads as available, not scheduled and not excluded", () => {
  const label = displayPatientState("AVAILABLE_ON_DEMAND");

  assert.equal(label, "Available if needed");
  assert.notEqual(label, displayPatientState("RESOLVED"));
  assert.notEqual(label, displayPatientState("NOT_APPLICABLE"));
});

test("an on-demand row shows why it has no date instead of an invented one", () => {
  const rows = toPatientScheduleRows(
    patientSchedule([{
      id: "pe-1", event_definition_id: "event-unscheduled", occurrence_index: 0,
      status: "AVAILABLE_ON_DEMAND",
    }]),
    toProtocolScheduleRows(schedule, projections),
  );

  assert.equal(rows[0].status, "Available if needed");
  assert.equal(rows[0].expectedDate, "Occurs only when clinically indicated");
  assert.equal(rows[0].isOnDemand, true);
  assert.equal(rows[0].visitMode, "In clinic or By telephone — to be confirmed");
});

test("a created unscheduled visit shows its date, mode, and recorded reason", () => {
  const rows = toPatientScheduleRows(
    patientSchedule([{
      id: "pe-2", event_definition_id: "event-unscheduled", occurrence_index: 0,
      status: "RESOLVED", nominal_start_date: "2026-10-14",
      visit_mode: "TELEPHONE", unscheduled_reason: "Grade 3 rash",
    }]),
    toProtocolScheduleRows(schedule, projections),
  );

  assert.equal(rows[0].status, "Scheduled");
  assert.equal(rows[0].visitMode, "By telephone");
  assert.equal(rows[0].unscheduledReason, "Grade 3 rash");
  assert.equal(rows[0].isOnDemand, false);
});

test("a scheduled visit takes the mode from its own definition", () => {
  const rows = toPatientScheduleRows(
    patientSchedule([{
      id: "pe-3", event_definition_id: "event-week4", occurrence_index: 0,
      status: "RESOLVED", nominal_start_date: "2026-09-29",
      visit_mode: "TELEPHONE",
    }]),
    toProtocolScheduleRows(schedule, projections),
  );

  assert.equal(rows[0].visitMode, "By telephone");
});

// --- doc 3 s4 and s36: the last row is not the end of the protocol -----------

test("a continuing repeat is stated, so the visible rows do not read as the end", () => {
  const rows = toRepeatRuleRows([{
    event_code: "CYCLE",
    cadence: "every 21 days",
    termination: "no stated maximum; the protocol continues while the patient remains on study",
    materialized_count: 4,
    continues: true,
    next_unmaterialized: "2027-01-05",
  }]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].cadence, "Every 21 Days");
  assert.match(rows[0].continuation, /^Continues — no stated maximum/);
  assert.notEqual(rows[0].nextDate, "—");
});

test("a repeat that genuinely ended contributes no continuation row", () => {
  assert.deepEqual(
    toRepeatRuleRows([{
      event_code: "CYCLE", cadence: "every 21 days",
      termination: "a protocol maximum of 6 occurrences",
      materialized_count: 6, continues: false,
    }]),
    [],
  );
  assert.deepEqual(toRepeatRuleRows(undefined), []);
});

// --- doc 1 s22: the anchor status vocabulary ---------------------------------

test("a planned anchor date never reads as an actual one", () => {
  const rows = toAnchorStatusRows([
    {
      anchor_code: "SURGERY", display_name: "Surgery Date", status: "PLANNED",
      value: "2026-10-01", reason: "an expected date", confirmed: false,
    },
    {
      anchor_code: "LAST_DOSE", display_name: "Last Dose", status: "ACTUAL",
      value: "2026-10-03", reason: "recorded", confirmed: false,
    },
  ]);

  assert.equal(rows[0].status, "Planned date");
  assert.equal(rows[1].status, "Actual date");
  assert.notEqual(rows[0].status, rows[1].status);
});

test("an anchor waiting on an event names that event", () => {
  const rows = toAnchorStatusRows([{
    anchor_code: "SURGERY", display_name: "Surgery Date", status: "AWAITING_EVENT",
    reason: "waiting", awaiting_event_code: "SURGERY_PROCEDURE", confirmed: false,
  }]);

  assert.equal(rows[0].detail, "Awaiting Surgery Procedure");
  assert.equal(rows[0].date, "—");
  assert.equal(rows[0].needsAttention, true);
});

test("only unfinished anchors are flagged for attention", () => {
  const rows = toAnchorStatusRows([
    { anchor_code: "A", display_name: "A", status: "NOT_REQUIRED", reason: "n/a", confirmed: false },
    { anchor_code: "B", display_name: "B", status: "CONFIRMED", reason: "ok", confirmed: true },
    { anchor_code: "C", display_name: "C", status: "CORRECTED", reason: "changed", confirmed: true },
    { anchor_code: "D", display_name: "D", status: "PLANNED", reason: "expected", confirmed: false },
  ]);

  assert.deepEqual(
    rows.filter((row) => row.needsAttention).map((row) => row.id),
    ["D"],
  );
});

// --- doc 8 s15-s18: dependent enrolment fields -------------------------------

test("a cohort nested in an unchosen part is disabled with the reason", () => {
  const fields = toEnrolmentFields([{
    dimension_type: "COHORT", display_name: "Cohort", options: [],
    depends_on: "PART", blocked: true, reason: "choose a study part first",
  }]);

  assert.equal(fields[0].disabled, true);
  assert.equal(fields[0].hint, "Choose a study part first");
  assert.deepEqual(fields[0].options, []);
});

test("once the part is chosen only its own cohorts are offered", () => {
  const fields = toEnrolmentFields([{
    dimension_type: "COHORT", display_name: "Cohort",
    options: [
      { code: "C1", display_name: "Cohort 1", parent_dimension_type: "PART", parent_code: "A" },
      { code: "C2", display_name: "Cohort 2", parent_dimension_type: "PART", parent_code: "A" },
    ],
    depends_on: "PART", blocked: false,
  }]);

  assert.equal(fields[0].disabled, false);
  assert.deepEqual(fields[0].options.map((o) => o.value), ["C1", "C2"]);
  assert.equal(fields[0].hint, null);
});

test("a parent with no children is disabled rather than looking broken", () => {
  const fields = toEnrolmentFields([{
    dimension_type: "COHORT", display_name: "Cohort", options: [],
    depends_on: "PART", blocked: false,
    reason: "no cohort exists for the selection above",
  }]);

  assert.equal(fields[0].disabled, true);
  assert.equal(fields[0].hint, "No cohort exists for the selection above");
});

// --- doc 9 s10: typed schedule comparison ------------------------------------

test("clinically significant changes sort ahead of administrative ones", () => {
  const rows = toScheduleChangeRows([
    {
      change_type: "VISIT_RENAMED", significance: "ADMINISTRATIVE",
      entity_type: "EVENT", entity_code: "WEEK_4", summary: "renamed",
    },
    {
      change_type: "WINDOW_NARROWED", significance: "CLINICAL",
      entity_type: "EVENT", entity_code: "WEEK_8",
      summary: "the visit window narrowed from -7/+7 to -3/+3 days",
      detail: "patients already booked may now fall outside their window",
    },
  ]);

  assert.deepEqual(rows.map((row) => row.clinical), [true, false]);
  assert.equal(rows[0].category, "Window narrowed");
  assert.equal(rows[0].target, "Week 8");
  assert.equal(rows[1].category, "Renamed");
});

test("an unmapped change type still reads as language, not as an enum", () => {
  const rows = toScheduleChangeRows([{
    change_type: "SOMETHING_NEW", significance: "CLINICAL",
    entity_type: "EVENT", entity_code: "WEEK_4", summary: "changed",
  }]);

  assert.equal(rows[0].category, "Something New");
  assert.equal(rows[0].clinical, true);
});

test("no differences produce no rows", () => {
  assert.deepEqual(toScheduleChangeRows([]), []);
  assert.deepEqual(toScheduleChangeRows(undefined), []);
});

// --- doc 2 s13 / doc 10 s24: the action board --------------------------------

test("actions group by category and keep the engine's priority order", () => {
  const groups = toActionBoardGroups([
    {
      category: "VISIT_DUE", priority: 5, patient_id: "p2", patient_code: "P002",
      title: "Week 4 is due", due_date: "2026-10-01",
    },
    {
      category: "IMPACT_CONFIRMATION", priority: 0, patient_id: "p1",
      patient_code: "P001", title: "Confirm a schedule change",
      detail: "3 visits would move",
    },
  ]);

  assert.deepEqual(groups.map((g) => g.id), ["IMPACT_CONFIRMATION", "VISIT_DUE"]);
  assert.equal(groups[0].label, "Waiting for your confirmation");
  assert.equal(groups[0].items[0].patient, "P001");
  assert.equal(groups[0].items[0].detail, "3 visits would move");
});

test("an unmapped category still reads as language", () => {
  const groups = toActionBoardGroups([{
    category: "SOMETHING_NEW", priority: 9, patient_id: "p1", title: "Look at this",
  }]);

  assert.equal(groups[0].label, "Something New");
});

test("an empty board produces no groups", () => {
  assert.deepEqual(toActionBoardGroups([]), []);
  assert.deepEqual(toActionBoardGroups(undefined), []);
});

test("every action links to the patient it concerns", () => {
  const groups = toActionBoardGroups([{
    category: "VISIT_OVERDUE", priority: 3, patient_id: "patient-42",
    patient_code: "P042", title: "Week 8 is overdue",
  }]);

  assert.equal(groups[0].items[0].patientId, "patient-42");
});
