import { api } from "@/src/api/client";
import type {
  ApprovedScheduleSummary, PatientScheduleResponse, ScheduleEvent,
  ScheduleProjection, UniversalSchedule, ValidationIssue,
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
    status: "CONFIRMED",
    source_type: "DEMO_UI",
  });
  return response.data;
}

export async function recordPatientState(
  patientId: string,
  stateCode: string,
  stateValue: unknown,
) {
  const response = await api.post(`${root}/patients/${patientId}/states`, {
    state_code: stateCode,
    state_value: stateValue,
    effective_at: new Date().toISOString(),
    source_reference: { source: "DEMO_UI" },
  });
  return response.data;
}

export async function getPatientSchedule(patientId: string) {
  const response = await api.get<PatientScheduleResponse>(`${root}/patients/${patientId}/schedule`);
  return response.data;
}
