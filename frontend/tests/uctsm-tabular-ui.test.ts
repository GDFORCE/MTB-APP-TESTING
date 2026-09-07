/**
 * Tabular Schedule UI Specification conformance.
 *
 * Sources: UI spec sections 4.2 (anchor-dependent rows), 4.3 (conditional
 * requirements in their own table), 4.6 (day-wise activity child table),
 * 4.8 (footnote / qualifier indicators), 6 (status vocabulary) and 7 (technical
 * fields must not surface in the main table).
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  displayPatientState, displayWaitingReason, toConditionalRequirementRows,
  toDayScheduleRows, toPatientScheduleRows, toProtocolScheduleRows, toQualifierDetails,
} from "../src/features/uctsm/presentation.ts";
import type {
  ConditionalDefinition, PatientActivity, PatientScheduleEvent, ScheduleProjection,
  UniversalSchedule,
} from "../src/features/uctsm/types.ts";

function schedule(overrides: Partial<UniversalSchedule["events"][number]> = {}): UniversalSchedule {
  return {
    schema_version: "uctsm.v1",
    schedule_version_id: "schedule-1",
    schedule_metadata: {
      name: "Protocol Schedule", schedule_type: "PRIMARY", version_number: 1, status: "APPROVED",
    },
    anchors: [],
    evidence: [],
    validation_issues: [],
    events: [{
      id: "event-1",
      code: "C1D1",
      protocol_label: "C1D1",
      display_name: "Cycle 1 Day 1",
      event_type: "SITE_VISIT",
      timing: { type: "PROTOCOL_DAY" },
      applicability: [],
      conditions: [],
      dependencies: [],
      activities: [],
      evidence_refs: ["evidence-1"],
      interpretation_status: "CONFIRMED",
      requires_review: false,
      ...overrides,
    }],
  } satisfies UniversalSchedule;
}

const projections: ScheduleProjection[] = [{
  event_id: "event-1", event_code: "C1D1", title: "Cycle 1 Day 1",
  timing_display: "Day 1 relative to Baseline", window_display: "-1 day/+1 day",
  event_type_display: "Site Visit", activities_display: ["Labs", "Dose"],
  status: "CONFIRMED", requires_review: false, evidence_refs: ["evidence-1"],
}];

test("a resolved footnote surfaces as a short indicator, with detail on expansion", () => {
  const rows = toProtocolScheduleRows(schedule({
    qualifiers: [{
      id: "q1", marker: "a", text: "ECG should be performed pre-dose.",
      scope: "CELL", category: "TIMING", target_codes: ["C1D1"], resolved: true,
      evidence_refs: ["evidence-1"],
    }],
  }), projections);

  assert.equal(rows[0].qualifier, "Footnote a");
  const detail = rows[0].qualifierDetails[0];
  assert.equal(detail.text, "ECG should be performed pre-dose.");
  assert.equal(detail.appliesTo, "this visit only");
  assert.equal(detail.category, "Timing");
  // The raw scope enum never reaches the main table (UI spec section 7).
  assert.ok(!rows[0].qualifier.includes("CELL"));
});

test("an unresolved footnote asks for review instead of claiming a meaning", () => {
  const rows = toProtocolScheduleRows(schedule({
    qualifiers: [{
      id: "q1", marker: "b", text: "Pre-dose only.", scope: "ROW",
      category: "UNRESOLVED", target_codes: [], resolved: false, evidence_refs: [],
    }],
  }), projections);
  assert.match(rows[0].qualifier, /needs review/);
});

test("conditional visits are classified so they can be split into their own table", () => {
  const rows = toProtocolScheduleRows(schedule(), projections, ["C1D1"]);
  assert.equal(rows[0].isConditional, true);
  assert.match(rows[0].timing, /^If required by protocol/);

  const normal = toProtocolScheduleRows(schedule(), projections);
  assert.equal(normal[0].isConditional, false);
});

test("conditional requirements render as their own compact table", () => {
  const conditions: ConditionalDefinition[] = [
    {
      condition_code: "ANC_LOW", display_name: "ANC <1000/mm3", protocol_label: "ANC <1000",
      state: "NOT_OCCURRED", requires_review: false, interpretation_status: "CONFIRMED",
      actions: [
        { action_type: "PAUSE_BLOCK", target_code: "TREATMENT_BLOCK" },
        { action_type: "REPEAT_EVENT", target_code: "REPEAT_CBC" },
      ],
      evidence_refs: ["evidence-1"],
    },
    {
      condition_code: "DISEASE_PROGRESSION", display_name: "Disease progression confirmed",
      protocol_label: "Progression", state: "ACTIVE", occurrence_date: "2026-11-14",
      requires_review: false, interpretation_status: "CONFIRMED",
      actions: [{ action_type: "STOP_BLOCK", target_code: "TREATMENT_BLOCK" }],
      evidence_refs: [],
    },
  ];
  const rows = toConditionalRequirementRows(conditions);

  assert.equal(rows[0].condition, "ANC <1000/mm3");
  assert.equal(rows[0].trigger, "PI/CRC confirms");
  assert.equal(rows[0].action, "Pause Treatment Block · Repeat Repeat Cbc");
  assert.equal(rows[0].status, "Not Occurred");
  assert.equal(rows[0].timing, "On confirmation");
  // An activated condition shows when it occurred.
  assert.equal(rows[1].status, "Scheduled");
  assert.equal(rows[1].timing, "Occurred 14 Nov 2026");
});

test("an unreviewed conditional requirement is flagged for the reviewer", () => {
  const rows = toConditionalRequirementRows([{
    condition_code: "CLINICAL", display_name: "As clinically indicated",
    protocol_label: "As clinically indicated", state: "NOT_OCCURRED",
    requires_review: false, interpretation_status: "UNRESOLVED",
    actions: [{ action_type: "MANUAL_REVIEW", target_code: "EXTRA_ASSESSMENT" }],
    evidence_refs: [],
  }]);
  assert.equal(rows[0].requiresReview, true);
  assert.equal(rows[0].action, "Flag for review of Extra Assessment");
});

test("the day-wise child table shows what an activity is waiting for, not a guessed time", () => {
  const activities: PatientActivity[] = [
    {
      id: "a1", activity_definition_id: "d1", activity_code: "DOSE", status: "COMPLETED",
      requiredness: "REQUIRED", sequence_number: 1,
      planned_time: "2026-09-01T09:30:00+00:00", actual_time: "2026-09-01T09:32:00+00:00",
    },
    {
      id: "a2", activity_definition_id: "d2", activity_code: "PK_2H",
      status: "WAITING_FOR_ANCHOR", requiredness: "REQUIRED", sequence_number: 3,
      explanation: { waiting_for_activity: "DOSE", reason: "waiting for the actual time of DOSE" },
    },
    {
      id: "a3", activity_definition_id: "d3", activity_code: "PK_1H", status: "RESOLVED",
      requiredness: "REQUIRED", sequence_number: 2, planned_time: "2026-09-01T10:32:00+00:00",
    },
  ];
  const rows = toDayScheduleRows(activities);

  // Ordered by protocol sequence, not by arrival in the payload.
  assert.deepEqual(rows.map((row) => row.activity), ["Dose", "Pk 1h", "Pk 2h"]);
  assert.equal(rows[0].plannedTime, "09:30");
  assert.equal(rows[0].actualTime, "09:32");
  assert.equal(rows[1].actualTime, "—");
  assert.equal(rows[2].plannedTime, "—");
  assert.equal(rows[2].status, "Awaiting Dose Time");
  assert.equal(rows[2].timingRule, "Relative to Dose");
});

test("an anchor-dependent patient row names the event it waits on", () => {
  const protocolRows = toProtocolScheduleRows(schedule({
    code: "SAFETY_FOLLOWUP", display_name: "Safety follow-up",
  }), []);
  const event: PatientScheduleEvent = {
    id: "pe-1", event_definition_id: "event-1", occurrence_index: 0,
    status: "WAITING_FOR_ANCHOR",
    explanation: { timing: { reference: { code: "LAST_DOSE" } } },
  };
  const [row] = toPatientScheduleRows(
    { patient_id: "p1", status: "ACTIVE", events: [event] }, protocolRows);

  assert.equal(row.status, "Awaiting Anchor");
  // No invented date, and no blank cell either: the reason is the content.
  assert.equal(row.expectedDate, "Awaiting Last Dose Date");
  assert.equal(row.actualDate, "—");
});

test("a blocked actual-previous-event row names the visit it depends on", () => {
  const event: PatientScheduleEvent = {
    id: "pe-2", event_definition_id: "event-1", occurrence_index: 0, status: "BLOCKED",
    explanation: { dependency_result: { waiting_for_actual: ["C2D1"] } },
  };
  assert.equal(displayWaitingReason(event), "Awaiting C2d1 Date");
  assert.equal(displayPatientState(event.status), "Awaiting Anchor");
});

test("patient rows carry their day-wise activities for expansion", () => {
  const protocolRows = toProtocolScheduleRows(schedule(), projections);
  const [row] = toPatientScheduleRows({
    patient_id: "p1", status: "ACTIVE",
    events: [{
      id: "pe-1", event_definition_id: "event-1", occurrence_index: 0, status: "RESOLVED",
      nominal_start_date: "2026-09-01",
      activities: [{
        id: "a1", activity_definition_id: "d1", activity_code: "DOSE", status: "RESOLVED",
        requiredness: "REQUIRED", sequence_number: 1, planned_time: "2026-09-01T09:30:00+00:00",
      }],
    }],
  }, protocolRows);

  assert.equal(row.expectedDate, "1 Sep 2026");
  assert.equal(row.dayScheduleRows.length, 1);
  assert.equal(row.dayScheduleRows[0].plannedTime, "09:30");
});

test("qualifier scopes translate into clinical language", () => {
  const details = toQualifierDetails([
    {
      id: "q1", marker: "c", text: "Applicable only to Arm B.", scope: "ARM",
      category: "APPLICABILITY", target_codes: ["ARM_B"], resolved: true, evidence_refs: ["e1"],
    },
  ]);
  assert.equal(details[0].appliesTo, "one treatment arm");
  assert.equal(details[0].category, "Applicability");
});
