import assert from "node:assert/strict";
import test from "node:test";

import {
  displayPatientState, displayVisitType, toPatientScheduleRows, toProtocolScheduleRows,
} from "../src/features/uctsm/presentation.ts";
import type { ScheduleProjection, UniversalSchedule } from "../src/features/uctsm/types.ts";

const schedule = {
  schema_version: "uctsm.v1",
  schedule_version_id: "schedule-1",
  schedule_metadata: { name: "Protocol Schedule", schedule_type: "PRIMARY", version_number: 1, status: "APPROVED" },
  anchors: [],
  evidence: [],
  validation_issues: [],
  events: [{
    id: "event-1",
    code: "FOLLOW_UP",
    protocol_label: "Visit 6",
    display_name: "Follow-up",
    event_type: "TELEPHONE",
    timing: { type: "OFFSET" },
    applicability: [],
    conditions: [{}],
    dependencies: [],
    recurrence: {
      type: "INTERVAL",
      interval: { value: 3, unit: "MONTH" },
      start_reference: { code: "RANDOMIZATION" },
      termination: { type: "EVENT", event_code: "FINAL_VISIT" },
    },
    activities: [{ id: "activity-1", display_name: "Safety Assessment", activity_type: "ASSESSMENT", requiredness: "REQUIRED" }],
    evidence_refs: ["evidence-1"],
    interpretation_status: "CONFIRMED",
    requires_review: false,
  }],
} satisfies UniversalSchedule;

const projections: ScheduleProjection[] = [{
  event_id: "event-1",
  event_code: "FOLLOW_UP",
  title: "Follow-up",
  timing_display: "3 months after Randomization",
  window_display: "-2 weeks/+2 weeks",
  event_type_display: "Telephone",
  activities_display: ["Safety Assessment"],
  condition_display: "Conditional",
  status: "CONFIRMED",
  requires_review: false,
  evidence_refs: ["evidence-1"],
}];

test("protocol adapter renders readable schedule rows without visit-gap fields", () => {
  const [row] = toProtocolScheduleRows(schedule, projections);
  assert.equal(row.visit, "V6+");
  assert.equal(row.timing, "If required by protocol · Every 3 months until Final Visit");
  assert.equal(row.window, "-2 weeks/+2 weeks");
  assert.equal(row.type, "Telephone");
  assert.deepEqual(row.activities, ["Safety Assessment"]);
  assert.equal("visit_gap_days" in row, false);
});

test("patient adapter displays backend dates and friendly states", () => {
  const protocolRows = toProtocolScheduleRows(schedule, projections);
  const [row] = toPatientScheduleRows({
    patient_id: "patient-1",
    status: "ACTIVE",
    schedule_version_id: "schedule-1",
    events: [{
      id: "patient-event-1",
      event_definition_id: "event-1",
      occurrence_index: 0,
      status: "WAITING_FOR_CONDITION",
      nominal_start_date: "2026-09-01",
      earliest_date: "2026-08-30",
      latest_date: "2026-09-03",
    }],
  }, protocolRows);
  assert.equal(row.expectedDate, "1 Sep 2026");
  assert.equal(row.allowedWindow, "30 Aug 2026 – 3 Sep 2026");
  assert.equal(row.status, "Waiting for condition");
});

test("enum labels are presentation-only and human readable", () => {
  assert.equal(displayVisitType("ONSITE/TELEPHONE"), "Site / Telephone");
  assert.equal(displayPatientState("HUMAN_REVIEW_REQUIRED"), "Review required");
});
