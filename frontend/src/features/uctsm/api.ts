import { api } from "@/src/api/client";
import type {
  AnchorStatusView, ApprovedScheduleSummary, EnrolmentDimension,
  PatientConditionsResponse, PatientScheduleResponse, ScheduleDiffResponse,
  ScheduleEvent, ScheduleProjection, UniversalSchedule, ValidationIssue,
} from "./types";

const root = "/uctsm";

export type DemoWorkspace = {
  trial_id: string;
  patient_id: string;
  schedule_version_id: string;
};

export async function seedDemoWorkspace() {
  const response = await api.post<DemoWorkspace>(`${root}/demo/seed`);
  return response.data;
}

export async function getUniversalSchedule(scheduleVersionId: string) {
  const response = await api.get<UniversalSchedule>(`${root}/schedule-versions/${scheduleVersionId}`);
  return response.data;
}

export async function getScheduleProjection(scheduleVersionId: string) {
  const response = await api.get<ScheduleProjection[]>(`${root}/schedule-versions/${scheduleVersionId}/projection`);
  return response.data;
}

export async function getApprovedSchedules(trialId: string) {
  const response = await api.get<ApprovedScheduleSummary[]>(`${root}/trials/${trialId}/approved-schedules`);
  return response.data;
}

export async function validateSchedule(scheduleVersionId: string) {
  const response = await api.post<{
    status: string; blocking_issues: number; warnings: number; issues: ValidationIssue[];
  }>(`${root}/schedule-versions/${scheduleVersionId}/validate`);
  return response.data;
}

export async function recordFieldDecision(
  scheduleVersionId: string,
  input: {
    decision: "APPROVE" | "CONFIRM" | "CORRECT" | "REJECT";
    entity_type: string;
    entity_id: string;
    field_path: string;
    previous_value?: unknown;
    new_value?: unknown;
    reason?: string;
    comment?: string;
  },
) {
  const response = await api.post(`${root}/schedule-versions/${scheduleVersionId}/review-decisions`, input);
  return response.data;
}

/**
 * Confirm every required field with one action - and record it as one bulk
 * action, never as N individual field reviews (doc s14). ``reason`` is
 * mandatory: the audit trail has to say why the fast path was used, the same
 * way any other schedule-changing decision does.
 */
export async function bulkConfirmSchedule(scheduleVersionId: string, reason: string) {
  const response = await api.post<{ fields_confirmed: number; action: string }>(
    `${root}/schedule-versions/${scheduleVersionId}/bulk-confirm`,
    { reason },
  );
  return response.data;
}

export async function correctScheduleEvent(
  scheduleVersionId: string,
  event: ScheduleEvent,
  reason: string,
) {
  const response = await api.put(
    `${root}/schedule-versions/${scheduleVersionId}/events/${event.id}`,
    event,
    { headers: { "X-Review-Reason": reason } },
  );
  return response.data;
}

/** One footnote as a reviewer needs to judge it: marker, wording, target, evidence. */
export type QualifierRow = {
  id: string;
  marker: string | null;
  text: string;
  scope: string;
  category: string;
  resolved: boolean;
  target_codes: string[];
  owner_type: "EVENT" | "ACTIVITY";
  owner_code: string;
  owner_name: string;
  event_code: string;
  evidence: {
    id: string;
    page_number: number | null;
    section_title: string | null;
    source_text: string | null;
  }[];
};

export async function getScheduleQualifiers(scheduleVersionId: string) {
  const response = await api.get<{
    schedule_version_id: string; qualifiers: QualifierRow[]; unresolved: number;
  }>(`${root}/schedule-versions/${scheduleVersionId}/qualifiers`);
  return response.data;
}

/**
 * Record what a footnote MEANS so it stops blocking approval.
 *
 * Extraction never interprets a table marker - it carries the marker and its
 * evidence and leaves it unresolved. This is the reviewer's half of that
 * contract, and `reason` is required on every outcome because the audit trail
 * has to answer "why was this decided" for a rule that changes patient dates.
 */
export async function resolveScheduleQualifier(
  scheduleVersionId: string,
  qualifierId: string,
  input: {
    decision: "ACCEPT" | "EDIT" | "NOT_APPLICABLE" | "ESCALATE";
    reason: string;
    category?: string;
    text?: string;
    scope?: string;
    target_codes?: string[];
  },
) {
  const response = await api.post<{
    qualifier_id: string; decision: string; blocking_issues: number;
    unresolved_qualifiers: number;
  }>(
    `${root}/schedule-versions/${scheduleVersionId}/qualifiers/${qualifierId}/resolve`,
    input,
  );
  return response.data;
}

export async function submitScheduleReview(scheduleVersionId: string) {
  const response = await api.post(`${root}/schedule-versions/${scheduleVersionId}/submit-review`);
  return response.data;
}

export async function decideSchedule(
  scheduleVersionId: string,
  decision: "APPROVE" | "REJECT",
  comment: string,
) {
  const response = await api.post(`${root}/schedule-versions/${scheduleVersionId}/review`, {
    decision, comment, reason: decision === "REJECT" ? comment : undefined,
  });
  return response.data;
}

export async function evaluatePatientSchedule(patientId: string, horizon: string, idempotencyKey: string) {
  const response = await api.post(
    `${root}/patients/${patientId}/schedule/evaluate`,
    { horizon },
    { headers: { "Idempotency-Key": idempotencyKey } },
  );
  return response.data;
}

export async function recordPatientAnchor(
  patientId: string,
  anchorDefinitionId: string,
  valueDate: string,
) {
  const response = await api.post(`${root}/patients/${patientId}/anchors`, {
    anchor_definition_id: anchorDefinitionId,
    value_date: valueDate,
    status: "PENDING_CONFIRMATION",
    source_type: "DEMO_UI",
  });
  return response.data;
}

export async function previewAnchorImpact(
  patientId: string,
  candidateId: string,
  horizon: string,
  reason: string,
) {
  const response = await api.post<ScheduleImpact>(
    `${root}/patients/${patientId}/anchors/${candidateId}/impact-preview`,
    { horizon, reason },
  );
  return response.data;
}

export async function previewPatientStateImpact(
  patientId: string,
  stateCode: string,
  stateValue: unknown,
  horizon: string,
  reason: string,
) {
  const response = await api.post<ScheduleImpact>(
    `${root}/patients/${patientId}/states/impact-preview`,
    {
      state: {
        state_code: stateCode,
        state_value: stateValue,
        effective_at: new Date().toISOString(),
        source_reference: { source: "UI" },
      },
      horizon,
      reason,
    },
  );
  return response.data;
}

export async function getPatientSchedule(patientId: string) {
  const response = await api.get<PatientScheduleResponse>(`${root}/patients/${patientId}/schedule`);
  return response.data;
}

export async function getPatientConditions(patientId: string) {
  const response = await api.get<PatientConditionsResponse>(
    `${root}/patients/${patientId}/conditions`);
  return response.data;
}

export type ScheduleImpact = {
  id: string;
  status: string;
  expires_at: string;
  impact: {
    summary: Record<string, number>;
    events: Array<{
      logical_key: string;
      change: "UNCHANGED" | "ADDED" | "MOVED" | "STATUS_CHANGED" | "CANCELLED" | "PROTECTED";
      before?: { date?: string; status?: string } | null;
      after?: { date?: string; status?: string } | null;
      protected_history: boolean;
    }>;
    condition_change?: Record<string, unknown>;
  };
};

/**
 * Ask what a condition change would do WITHOUT applying it. Nothing in the
 * patient schedule moves until confirmScheduleImpact is called with this id.
 */
export async function previewConditionImpact(
  patientId: string,
  condition: {
    condition_code: string;
    state: string;
    occurrence_date?: string;
    resolution_date?: string;
  },
  horizon: string,
  reason: string,
) {
  const response = await api.post<ScheduleImpact>(
    `${root}/patients/${patientId}/conditions/impact-preview`,
    { condition, horizon, reason });
  return response.data;
}

export async function confirmScheduleImpact(proposalId: string, reason?: string) {
  const response = await api.post(
    `${root}/schedule-impact-proposals/${proposalId}/confirm`, { reason });
  return response.data;
}

export async function cancelScheduleImpact(proposalId: string, reason: string) {
  const response = await api.post(
    `${root}/schedule-impact-proposals/${proposalId}/cancel`, { reason });
  return response.data;
}

export async function recordPatientActivity(
  patientId: string,
  body: {
    event_code: string;
    occurrence_index?: number;
    activity_code: string;
    status: "COMPLETED" | "NOT_DONE" | "PENDING";
    actual_time?: string;
    reason?: string;
  },
) {
  const response = await api.post(`${root}/patients/${patientId}/activities`, body);
  return response.data;
}

export type PatientProjection = {
  patient_id: string;
  reminders: Array<{
    key: string; logical_key: string; event_code: string; message: string;
    nominal_date: string; policy_state: "UPCOMING" | "DUE_WINDOW" | "OVERDUE";
    requires_attendance: boolean;
  }>;
  calendar: Array<{
    key: string; kind: "EVENT" | "CONFINEMENT"; title: string;
    start_date: string; end_date: string;
  }>;
  suppressed: Array<{ logical_key: string; reason: string }>;
  /** Doc 6 s17/s31: one stay per entry, with its study days. */
  confinements?: Array<{
    episode_code: string;
    display_name: string;
    status: string;
    planned_admission?: string | null;
    actual_admission?: string | null;
    planned_discharge?: string | null;
    actual_discharge?: string | null;
    days?: Array<{
      day_label: string; relative_day: number;
      scheduled_date?: string | null; activity_codes?: string[];
    }>;
  }>;
};

export async function getPatientScheduleProjection(patientId: string) {
  const response = await api.get<PatientProjection>(
    `${root}/patients/${patientId}/schedule/projection`);
  return response.data;
}

// --- capabilities the engine gained but no screen consumed yet ------------------

export type AnchorStatusResponse = {
  patient_id: string;
  anchors: AnchorStatusView[];
  /** Anchor codes a site still has to supply or confirm. */
  outstanding: string[];
};

export async function getPatientAnchorStatuses(patientId: string) {
  const response = await api.get<AnchorStatusResponse>(
    `${root}/patients/${patientId}/anchors`);
  return response.data;
}

export type VisitDeviation = {
  logical_key: string;
  event_code: string;
  display_name: string;
  deviation_type: string;
  planned_date?: string | null;
  actual_date?: string | null;
  earliest_date?: string | null;
  latest_date?: string | null;
  delta_from_planned_days?: number | null;
  outside_window_days?: number | null;
  reason?: string | null;
};

export type DeviationReportResponse = {
  patient_id: string;
  schedule_version_id: string;
  assessed_on: string;
  visits: VisitDeviation[];
  activities: unknown[];
  confinements: unknown[];
  summary: { deviations: number; visits_assessed: number; activity_deviations: number };
};

export async function getPatientDeviations(patientId: string, today?: string) {
  const response = await api.get<DeviationReportResponse>(
    `${root}/patients/${patientId}/deviations`,
    { params: today ? { today } : undefined });
  return response.data;
}

export type DashboardAction = {
  category: string;
  priority: number;
  patient_id: string;
  patient_code?: string | null;
  title: string;
  detail?: string | null;
  due_date?: string | null;
  reference?: string | null;
};

export async function getDashboardActions(trialId?: string, today?: string) {
  const response = await api.get<{ actions: DashboardAction[] }>(
    `${root}/dashboard/actions`,
    { params: { ...(trialId ? { trial_id: trialId } : {}), ...(today ? { today } : {}) } });
  return response.data;
}

/**
 * Doc 10 s14. Creates an occurrence of a protocol-defined unscheduled visit for
 * ONE patient. It never changes the protocol, and a reason is mandatory.
 */
export async function createUnscheduledVisit(
  patientId: string,
  body: { event_code: string; occurred_on: string; reason: string; visit_mode?: string },
) {
  const response = await api.post(
    `${root}/patients/${patientId}/unscheduled-visits`, body);
  return response.data;
}

export async function getEnrolmentOptions(
  scheduleVersionId: string,
  selected: Record<string, string | undefined> = {},
) {
  const response = await api.get<{ dimensions: EnrolmentDimension[] }>(
    `${root}/schedule-versions/${scheduleVersionId}/enrolment-options`,
    { params: selected });
  return response.data;
}

/** Doc 8 s22. Shows what moving a patient between groups would do; applies nothing. */
export async function previewAssignmentImpact(
  patientId: string,
  assignment: Record<string, string | null>,
  horizon: string,
  reason: string,
) {
  const response = await api.post<ScheduleImpact>(
    `${root}/patients/${patientId}/assignment/impact-preview`,
    { assignment, horizon, reason });
  return response.data;
}

export async function compareScheduleVersions(leftId: string, rightId: string) {
  const response = await api.get<ScheduleDiffResponse>(
    `${root}/schedule-versions/${leftId}/diff/${rightId}`);
  return response.data;
}

export async function queueScheduleExtraction(
  protocolId: string, protocolVersionId: string, idempotencyKey: string,
) {
  const response = await api.post<{
    extraction_run_id: string; status: string; provider_configured: boolean;
  }>(
    `${root}/protocols/${protocolId}/versions/${protocolVersionId}/extract-schedule`,
    undefined,
    { headers: { "Idempotency-Key": idempotencyKey } },
  );
  return response.data;
}

export async function getExtractionRun(runId: string) {
  const response = await api.get<{
    id: string; status: string; error_details?: Record<string, unknown> | null;
    schedule_version_id?: string | null;
  }>(`${root}/extraction-runs/${runId}`);
  return response.data;
}

// --- the cutover gate ------------------------------------------------------------

export type ParityDifference = {
  kind: string;
  key: string;
  name: string;
  legacy?: string | null;
  engine?: string | null;
  detail: string;
};

export type ParityCheckResponse = {
  verdict: "MATCH" | "DIFFERENCES_FOUND" | "NOT_COMPARABLE";
  compared: number;
  matched: number;
  differences: ParityDifference[];
  note: string;
  passed: boolean;
  parity_run_id?: string | null;
};

/**
 * Compare one patient's engine schedule against the operational one. The caller
 * supplies the operational visits because the canonical API cannot reach the
 * operational store. Records the result whatever it is.
 */
export async function runParityCheck(
  patientId: string,
  legacyVisits: Record<string, unknown>[],
  horizon?: string,
) {
  const response = await api.post<ParityCheckResponse>(
    `${root}/patients/${patientId}/parity-check`,
    { legacy_visits: legacyVisits, horizon });
  return response.data;
}

export type ReadModeStatus = {
  trial_id: string;
  read_mode: "LEGACY" | "ENGINE";
  ready: boolean;
  patients: number;
  checked: number;
  unchecked: string[];
  with_differences: string[];
  reasons: string[];
};

export async function getReadMode(trialId: string) {
  const response = await api.get<ReadModeStatus>(`${root}/trials/${trialId}/read-mode`);
  return response.data;
}

/**
 * Move a trial's visit reads between the operational store and the engine.
 * Moving TO the engine requires a matching parity run for every patient; moving
 * back needs no gate, because reversing must never be harder than cutting over.
 */
export async function setReadMode(
  trialId: string, mode: "LEGACY" | "ENGINE", reason: string,
) {
  const response = await api.post<{ trial_id: string; read_mode: string; previous: string }>(
    `${root}/trials/${trialId}/read-mode`, { mode, reason });
  return response.data;
}

/**
 * The Add Trial bridge (operational API, not /uctsm): these two endpoints live on
 * the trial the sponsor just created, because the trial id they take is the
 * operational one the rest of the app already holds.
 */
export type CanonicalScheduleRow = {
  schedule_definition_id: string;
  schedule_version_id: string;
  name: string;
  schedule_type: string;
  version_number: number;
  status: string;
  protocol_version_label: string;
  effective_from?: string | null;
  approved_at?: string | null;
  is_current_protocol_version: boolean;
  patients: number;
};

export type CanonicalScheduleBuild = {
  trial_id: string;
  uctsm_trial_id: string;
  schedules: {
    external_schedule_definition_id: string;
    schedule_definition_id: string;
    schedule_version_id: string;
    version_number: number;
    status: string;
    name: string;
    created: boolean;
  }[];
};

/** Build the canonical draft schedule(s) from this trial's extracted protocol. */
export async function buildCanonicalSchedule(trialId: string) {
  const response = await api.post<CanonicalScheduleBuild>(`/trials/${trialId}/uctsm-schedule`);
  return response.data;
}

export type CanonicalScheduleIndex = {
  trial_id: string;
  uctsm_trial_id?: string | null;
  approved_schedule_version_id?: string | null;
  draft_schedule_version_id?: string | null;
  schedules: CanonicalScheduleRow[];
};

/** Every protocol/schedule version this trial has, superseded ones included. */
export async function getCanonicalSchedules(trialId: string) {
  const response = await api.get<CanonicalScheduleIndex>(`/trials/${trialId}/uctsm-schedule`);
  return response.data;
}
