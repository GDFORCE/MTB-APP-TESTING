import type {
  PatientScheduleResponse, ScheduleEvent, ScheduleProjection, UniversalSchedule,
} from "./types";

export type ProtocolScheduleRow = {
  id: string;
  eventDefinitionId: string;
  visit: string;
  visitName: string;
  timing: string;
  window: string;
  type: string;
  activities: string[];
  evidenceRefs: string[];
  requiresReview: boolean;
};

export type PatientScheduleRow = ProtocolScheduleRow & {
  patientEventId: string;
  expectedDate: string;
  allowedWindow: string;
  status: string;
};

const EMPTY = "—";

const titleCase = (value: string) => value
  .toLowerCase()
  .replace(/(^|[\s/_-])([a-z])/g, (_match, prefix, letter) => `${prefix === "_" ? " " : prefix}${letter.toUpperCase()}`)
  .trim();

const typeLabels: Record<string, string> = {
  ONSITE: "Site",
  SITE: "Site",
  SITE_VISIT: "Site",
  TELEPHONE: "Telephone",
  PHONE: "Telephone",
  VIDEO: "Video",
  HOME: "Home",
  REMOTE: "Remote",
  LAB_ONLY: "Lab",
  IMAGING_ONLY: "Imaging",
  SAFETY_ASSESSMENT: "Safety assessment",
  ASSESSMENT: "Assessment",
};

export function displayVisitType(value?: string): string {
  if (!value) return EMPTY;
  return value
    .split(/\s*[/,|+]\s*/)
    .filter(Boolean)
    .map((part) => typeLabels[part.toUpperCase()] || titleCase(part))
    .join(" / ");
}

export function displayPatientState(value: string): string {
  const labels: Record<string, string> = {
    RESOLVED: "Scheduled",
    WAITING_FOR_ANCHOR: "Waiting for required information",
    WAITING_FOR_CONDITION: "Waiting for condition",
    NOT_APPLICABLE: "Not applicable",
    HUMAN_REVIEW_REQUIRED: "Review required",
    COMPLETED: "Completed",
    UPCOMING: "Upcoming",
    ACTIVE: "Scheduled",
  };
  return labels[value] || titleCase(value);
}

function recurrenceText(recurrence?: Record<string, unknown>): string | null {
  if (!recurrence) return null;
  const interval = recurrence.interval as { value?: number; unit?: string } | undefined;
  if (!interval?.value || !interval.unit) return "Repeats as specified by protocol";
  const unit = titleCase(interval.unit).toLowerCase();
  const plural = interval.value === 1 ? unit : `${unit}s`;
  const termination = recurrence.termination as { type?: string; event_code?: string; count?: number } | undefined;
  let ending = "";
  if (termination?.type === "EVENT" && termination.event_code) ending = ` until ${titleCase(termination.event_code)}`;
  else if (termination?.type === "COUNT" && termination.count) ending = ` for ${termination.count} occurrences`;
  else if (termination?.type === "HORIZON") ending = " while the patient remains on study";
  return `Every ${interval.value} ${plural}${ending}`;
}

function visitLabel(event: ScheduleEvent, index: number): string {
  const candidate = event.protocol_label?.trim();
  if (candidate && /^(visit\s*)?v?\d+[a-z]?\+?$/i.test(candidate)) {
    return candidate.replace(/^visit\s*/i, "V").replace(/^([0-9])/, "V$1");
  }
  return `V${index + 1}${event.recurrence ? "+" : ""}`;
}

export function toProtocolScheduleRows(
  schedule: UniversalSchedule,
  projections: ScheduleProjection[],
): ProtocolScheduleRow[] {
  const byId = new Map(projections.map((projection) => [projection.event_id, projection]));
  return schedule.events.map((event, index) => {
    const projection = byId.get(event.id);
    const recurring = recurrenceText(event.recurrence);
    const conditionPrefix = event.conditions.length ? "If required by protocol" : null;
    const baseTiming = recurring || projection?.timing_display || "Timing requires review";
    const timing = conditionPrefix ? `${conditionPrefix} · ${baseTiming}` : baseTiming;
    return {
      id: event.id,
      eventDefinitionId: event.id,
      visit: visitLabel(event, index),
      visitName: projection?.title || event.display_name || event.protocol_label,
      timing,
      window: projection?.window_display || EMPTY,
      type: displayVisitType(projection?.event_type_display || event.event_type),
      activities: projection?.activities_display?.filter(Boolean) || event.activities.map((item) => item.display_name),
      evidenceRefs: projection?.evidence_refs || event.evidence_refs,
      requiresReview: projection?.requires_review ?? event.requires_review,
    };
  });
}

function displayDate(value?: string): string {
  if (!value) return EMPTY;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(match[3])} ${months[Number(match[2]) - 1]} ${match[1]}`;
}

export function toPatientScheduleRows(
  patientSchedule: PatientScheduleResponse,
  protocolRows: ProtocolScheduleRow[],
): PatientScheduleRow[] {
  const definitions = new Map(protocolRows.map((row) => [row.eventDefinitionId, row]));
  return patientSchedule.events.map((event, index) => {
    const definition = definitions.get(event.event_definition_id);
    const fallback: ProtocolScheduleRow = {
      id: event.event_definition_id,
      eventDefinitionId: event.event_definition_id,
      visit: `V${index + 1}`,
      visitName: "Protocol visit",
      timing: EMPTY,
      window: EMPTY,
      type: EMPTY,
      activities: [],
      evidenceRefs: [],
      requiresReview: false,
    };
    const row = definition || fallback;
    const earliest = displayDate(event.earliest_date);
    const latest = displayDate(event.latest_date);
    return {
      ...row,
      id: event.id,
      patientEventId: event.id,
      visit: event.occurrence_index > 0 ? `${row.visit}.${event.occurrence_index + 1}` : row.visit,
      expectedDate: displayDate(event.nominal_start_date),
      allowedWindow: earliest === EMPTY && latest === EMPTY
        ? EMPTY
        : `${earliest} – ${latest}`,
      status: displayPatientState(event.status),
    };
  });
}
