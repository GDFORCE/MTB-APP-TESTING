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

export type Qualifier = {
  id: string;
  marker?: string;
  text: string;
  scope: string;
  category: string;
  target_codes: string[];
  resolved: boolean;
  evidence_refs: string[];
};

export type ScheduleActivity = {
  id: string;
  code?: string;
  display_name: string;
  activity_type: string;
  requiredness: string;
  sequence_number?: number;
  timing?: Record<string, unknown> & { type?: string };
  applicability?: unknown[];
  qualifiers?: Qualifier[];
};

export type ConditionalDefinition = {
  condition_code: string;
  display_name: string;
  protocol_label: string;
  state: "NOT_OCCURRED" | "PENDING_CONFIRMATION" | "ACTIVE" | "RESOLVED"
    | "NOT_APPLICABLE" | "CANCELLED";
  occurrence_date?: string;
  resolution_date?: string;
  requires_review: boolean;
  interpretation_status: InterpretationStatus;
  actions: Array<{ action_type: string; target_code: string }>;
  evidence_refs: string[];
};

export type PatientConditionsResponse = {
  patient_id: string;
  schedule_version_id?: string;
  conditions: ConditionalDefinition[];
};

export type PatientActivity = {
  id: string;
  activity_definition_id: string;
  activity_code?: string;
  status: string;
  requiredness: string;
  sequence_number?: number;
  planned_time?: string;
  earliest_time?: string;
  latest_time?: string;
  actual_time?: string;
  explanation?: Record<string, unknown>;
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
  dependency_mode?: "NOMINAL" | "ACTUAL_PREVIOUS_EVENT" | "MANUAL" | "UNCLEAR";
  /** Doc 10 s2-s3. Null when the protocol never stated a mode. */
  visit_mode?: string | null;
  /** Doc 10 s31-s32. Every mode a hybrid visit permits. */
  allowed_visit_modes?: string[];
  /** Doc 10 s13-s15. ON_DEMAND visits exist but are never due. */
  activation?: "SCHEDULED" | "ON_DEMAND";
  recurrence?: Record<string, unknown>;
  conditional_actions?: unknown[];
  qualifiers?: Qualifier[];
  confinement?: Record<string, unknown>;
  activities: ScheduleActivity[];
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
  actual_date?: string;
  timing_resolution?: Record<string, unknown>;
  activities?: PatientActivity[];
  explanation?: Record<string, unknown>;
  /** Doc 10 s31-s32. Absent when the protocol never stated one. */
  visit_mode?: string | null;
  /** Doc 10 s14. Present only on a visit a site created on demand. */
  unscheduled_reason?: string | null;
};

/**
 * A repeat that continues past the occurrences that were materialized
 * (requirement doc 3 s4 and s36). Without this the last row on screen reads as
 * the end of the protocol, which it is not.
 */
/**
 * The per-patient status of one anchor (requirement doc 1 s22). PLANNED and
 * ACTUAL are deliberately separate: an expected date is not evidence the event
 * happened.
 */
export type AnchorStatusView = {
  anchor_code: string;
  display_name: string;
  status:
    | "NOT_REQUIRED" | "AWAITING_EVENT" | "PLANNED"
    | "ACTUAL" | "CONFIRMED" | "CORRECTED";
  value?: string | null;
  reason: string;
  awaiting_event_code?: string | null;
  confirmed: boolean;
};

/**
 * One selectable enrolment dimension (requirement doc 8 s15-s18). ``blocked``
 * means a parent choice is still outstanding, so nothing here can be picked yet.
 */
export type EnrolmentDimension = {
  dimension_type: string;
  display_name: string;
  options: {
    code: string;
    display_name: string;
    description?: string | null;
    parent_dimension_type?: string | null;
    parent_code?: string | null;
  }[];
  depends_on?: string | null;
  blocked: boolean;
  reason?: string | null;
};

/**
 * One classified difference between schedule versions (requirement doc 9 s10).
 * CLINICAL means it can affect a patient already on the study.
 */
export type TypedScheduleChange = {
  change_type: string;
  significance: "CLINICAL" | "ADMINISTRATIVE";
  entity_type: string;
  entity_code: string;
  summary: string;
  detail?: string | null;
};

export type ScheduleDiffResponse = {
  added_events: string[];
  removed_events: string[];
  typed_changes?: TypedScheduleChange[];
  clinically_significant_count?: number;
};

export type RepeatRuleSummary = {
  event_code: string;
  block_code?: string | null;
  cadence: string;
  termination: string;
  materialized_count: number;
  continues: boolean;
  next_unmaterialized?: string | null;
};

export type PatientScheduleResponse = {
  patient_id: string;
  status: string;
  schedule_version_id?: string;
  evaluation_id?: string;
  events: PatientScheduleEvent[];
  repeat_rules?: RepeatRuleSummary[];
};
