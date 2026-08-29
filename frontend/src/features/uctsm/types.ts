export type InterpretationStatus =
  | "EXTRACTED" | "INFERRED" | "CONFIRMED" | "AMBIGUOUS"
  | "CONFLICTING" | "UNRESOLVED" | "REJECTED";

export type ScheduleStatus =
  | "DRAFT" | "EXTRACTED" | "VALIDATION_REQUIRED" | "IN_REVIEW"
  | "APPROVED" | "REJECTED" | "SUPERSEDED" | "ARCHIVED";

export type Evidence = {
  id: string;
  evidence_type: string;
  page_number?: number;
  section_title?: string;
  table_title?: string;
  row_identifier?: string;
  column_identifier?: string;
  source_text?: string;
  source_locator: Record<string, unknown>;
};

export type ValidationIssue = {
  id: string;
  entity_type?: string;
  entity_id?: string;
  issue_code: string;
  severity: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  message: string;
  blocking: boolean;
  status: "OPEN" | "RESOLVED" | "ACCEPTED" | "DISMISSED";
  details: Record<string, unknown>;
};

export type ScheduleEvent = {
  id: string;
  code: string;
  protocol_label: string;
  display_name: string;
  event_type: string;
  timing: Record<string, unknown> & { type: string };
  applicability: unknown[];
  conditions: unknown[];
  dependencies: unknown[];
  recurrence?: Record<string, unknown>;
  activities: Array<{ id: string; display_name: string; activity_type: string; requiredness: string }>;
  evidence_refs: string[];
  interpretation_status: InterpretationStatus;
  requires_review: boolean;
};

export type UniversalSchedule = {
  schema_version: "uctsm.v1";
  schedule_version_id: string;
  schedule_metadata: {
    name: string;
    description?: string;
    schedule_type: string;
    version_number: number;
    status: ScheduleStatus;
  };
  anchors: Array<{ id: string; code: string; display_name: string; status: string }>;
  events: ScheduleEvent[];
  evidence: Evidence[];
  validation_issues: ValidationIssue[];
};

export type ScheduleProjection = {
  event_id: string;
  event_code: string;
  title: string;
  timing_display: string;
  window_display?: string;
  event_type_display: string;
  activities_display: string[];
  condition_display?: string;
  status: InterpretationStatus;
  requires_review: boolean;
  evidence_refs: string[];
};

export type ApprovedScheduleSummary = {
  schedule_definition_id: string;
  schedule_version_id: string;
  name: string;
  schedule_type: string;
  version_number: number;
  approved_at?: string;
};

export type PatientScheduleEvent = {
  id: string;
  event_definition_id: string;
  occurrence_index: number;
  status: string;
  nominal_start_date?: string;
  nominal_end_date?: string;
  earliest_date?: string;
  latest_date?: string;
  timing_resolution?: Record<string, unknown>;
  explanation?: Record<string, unknown>;
};

export type PatientScheduleResponse = {
  patient_id: string;
  status: string;
  schedule_version_id?: string;
  evaluation_id?: string;
  events: PatientScheduleEvent[];
};
