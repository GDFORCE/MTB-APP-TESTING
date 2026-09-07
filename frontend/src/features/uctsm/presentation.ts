import type {
  AnchorStatusView, ConditionalDefinition, EnrolmentDimension, PatientActivity,
  PatientScheduleEvent, PatientScheduleResponse, Qualifier, RepeatRuleSummary,
  ScheduleActivity, ScheduleEvent, ScheduleProjection, TypedScheduleChange,
  UniversalSchedule,
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
  appliesTo: string;
  status: string;
  evidenceRefs: string[];
  requiresReview: boolean;
  /** Short indicator such as "Footnote a"; details open on expansion. */
  qualifier: string;
  qualifierDetails: QualifierDetail[];
  /** Conditional visits render in their own table, never in the dated schedule. */
  isConditional: boolean;
  /** Doc 10 s31-s32: every mode a hybrid visit permits, so none is invented. */
  allowedVisitModes: string[];
  /**
   * UI spec s4.4. Present when this visit is a repeating RULE rather than one
   * occasion, so the row reads "every 21 days, continues" instead of looking
   * like a single visit that happens once.
   */
  repeatRule: string | null;
  /**
   * Case 6 at protocol-review level: each activity's timing rule and the anchor
   * it is measured from. Absent on a patient row, where the resolved day-wise
   * table carries actual times instead.
   */
  activityRows?: ProtocolActivityRow[];
};

export type QualifierDetail = {
  id: string;
  marker: string;
  text: string;
  appliesTo: string;
  category: string;
  resolved: boolean;
};

export type ConditionalRequirementRow = {
  id: string;
  condition: string;
  trigger: string;
  action: string;
  timing: string;
  status: string;
  requiresReview: boolean;
  evidenceRefs: string[];
};

export type DayScheduleRow = {
  id: string;
  activity: string;
  timingRule: string;
  plannedTime: string;
  actualTime: string;
  status: string;
  requiredness: string;
};

export type PatientScheduleRow = ProtocolScheduleRow & {
  patientEventId: string;
  expectedDate: string;
  actualDate: string;
  allowedWindow: string;
  status: string;
  dayScheduleRows: DayScheduleRow[];
  /** Doc 5 s26: how much of this visit is actually done. */
  completion: CompletionSummary;
  /** Doc 10 s21-s23: how the patient attends, in plain words. */
  visitMode: string;
  /** Doc 10 s14: why a site created this unscheduled visit. */
  unscheduledReason: string | null;
  /** Doc 10 s13-s15: a definition that is available but carries no date. */
  isOnDemand: boolean;
  /**
   * The machine dates behind expectedDate / actualDate. Display strings are
   * localised and do not sort, so anything ordering by time needs these.
   */
  expectedIso: string | null;
  actualIso: string | null;
};

/**
 * A footer row for a repeat that continues past the visits on screen
 * (requirement doc 3 s4 and s36). Without it, the last materialized row reads as
 * the end of the protocol.
 */
export type RepeatRuleRow = {
  id: string;
  eventCode: string;
  cadence: string;
  continuation: string;
  nextDate: string;
};

const EMPTY = "—";

// Codes such as PK_2H or ANC_LOW must read as clinical language, not as enums.
const titleCase = (value: string) => value
  .toLowerCase()
  .replace(/(^|[\s/_-])([a-z0-9])/g, (_match, prefix, letter) => `${prefix === "_" ? " " : prefix}${letter.toUpperCase()}`)
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
    WAITING_FOR_ANCHOR: "Awaiting Anchor",
    WAITING_FOR_CONDITION: "Inactive",
    // Doc 10 s13-s15: available, never due. Deliberately not "Scheduled" and
    // not "Not applicable" - the patient may well have one.
    AVAILABLE_ON_DEMAND: "Available if needed",
    NOT_APPLICABLE: "Not applicable",
    HUMAN_REVIEW_REQUIRED: "Review required",
    UNRESOLVED: "Unresolved",
    BLOCKED: "Awaiting Anchor",
    PAUSED: "Paused",
    CANCELLED: "Cancelled",
    COMPLETED: "Completed",
    MISSED: "Missed",
    NOT_DONE: "Not done",
    DUE: "Due",
    UPCOMING: "Upcoming",
    ACTIVE: "Scheduled",
  };
  return labels[value] || titleCase(value);
}

/** Name the event a visit is waiting for, so the row reads "Awaiting Surgery Date". */
export function displayWaitingReason(event: PatientScheduleEvent): string | null {
  const explanation = (event.explanation || {}) as Record<string, unknown>;
  const waitingFor = explanation.waiting_for_activity;
  if (typeof waitingFor === "string") return `Awaiting ${titleCase(waitingFor)} Time`;
  const dependency = explanation.reconciliation ? null : explanation.reason;
  if (event.status === "AVAILABLE_ON_DEMAND") {
    return "Occurs only when clinically indicated";
  }
  if (event.status === "WAITING_FOR_CONDITION") return "Awaiting protocol condition";
  if (event.status !== "WAITING_FOR_ANCHOR" && event.status !== "BLOCKED") return null;
  const anchor = anchorCodeFrom(event);
  return anchor
    ? `Awaiting ${titleCase(anchor)} Date`
    : (typeof dependency === "string" ? dependency : "Awaiting required date");
}

function anchorCodeFrom(event: PatientScheduleEvent): string | null {
  const explanation = (event.explanation || {}) as Record<string, unknown>;
  const dependency = explanation.dependency_result as Record<string, unknown> | undefined;
  const waiting = (dependency?.waiting_for_actual || dependency?.waiting_for_manual_date) as
    string[] | undefined;
  if (Array.isArray(waiting) && waiting.length) return waiting[0];
  const timing = explanation.timing as Record<string, unknown> | undefined;
  const reference = timing?.reference as Record<string, unknown> | undefined;
  const code = reference?.code || reference?.event_code;
  return typeof code === "string" ? code : null;
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

const visitModeLabels: Record<string, string> = {
  CLINIC: "In clinic",
  SITE_VISIT: "In clinic",
  TELEPHONE: "By telephone",
  PHONE_CONTACT: "By telephone",
  VIDEO: "By video call",
  HOME: "At home",
  HOME_NURSE: "Home nurse visit",
  REMOTE: "Remote",
  IMAGING_CENTRE: "At an imaging centre",
  LABORATORY: "At a laboratory",
  PHARMACY: "At the pharmacy",
};

/**
 * Doc 10 s18 and s33: when the protocol did not state a mode, say so rather
 * than showing a clinic visit. Telling a patient to travel on a guess is the
 * failure this requirement exists to prevent.
 */
export function displayVisitMode(
  mode?: string | null, allowed?: string[] | null,
): string {
  if (mode) return visitModeLabels[mode.toUpperCase()] || titleCase(mode);
  const options = (allowed || []).filter(Boolean);
  if (options.length > 1) {
    return `${options
      .map((item) => visitModeLabels[item.toUpperCase()] || titleCase(item))
      .join(" or ")} — to be confirmed`;
  }
  if (options.length === 1) {
    return visitModeLabels[options[0].toUpperCase()] || titleCase(options[0]);
  }
  return "Not stated in the protocol";
}

/**
 * UI spec s4.5. The table a reviewer reads before confirming a schedule change.
 *
 * Sorted so the consequential rows come first - a change nobody looked at is the
 * failure this screen exists to prevent - and protected history is called out
 * explicitly, because "this visit already happened and will not move" is the
 * reassurance that makes the rest of the list safe to accept.
 */
export type ImpactRow = {
  id: string;
  visit: string;
  change: string;
  before: string;
  after: string;
  protectedHistory: boolean;
  consequential: boolean;
};

const impactChangeLabels: Record<string, string> = {
  UNCHANGED: "No change",
  ADDED: "Newly required",
  MOVED: "Date moves",
  STATUS_CHANGED: "Status changes",
  CANCELLED: "No longer required",
  PROTECTED: "Already happened — unchanged",
};

/** Changes that alter what a site must do. UNCHANGED and PROTECTED do not. */
const CONSEQUENTIAL = new Set(["ADDED", "MOVED", "STATUS_CHANGED", "CANCELLED"]);

export function toImpactRows(
  events: {
    logical_key: string;
    change: string;
    before?: { date?: string; status?: string } | null;
    after?: { date?: string; status?: string } | null;
    protected_history?: boolean;
  }[] | undefined,
): ImpactRow[] {
  return (events || [])
    .map((item) => ({
      id: item.logical_key,
      visit: titleCase(item.logical_key.split(/[:#]/)[0] || item.logical_key),
      change: impactChangeLabels[item.change] || titleCase(item.change),
      before: describeImpactSide(item.before),
      after: describeImpactSide(item.after),
      protectedHistory: Boolean(item.protected_history),
      consequential: CONSEQUENTIAL.has(item.change) && !item.protected_history,
    }))
    .sort((left, right) => Number(right.consequential) - Number(left.consequential));
}

function describeImpactSide(side?: { date?: string; status?: string } | null): string {
  if (!side) return EMPTY;
  const parts = [displayDate(side.date)];
  if (side.status) parts.push(displayPatientState(side.status));
  return parts.filter((part) => part && part !== EMPTY).join(" · ") || EMPTY;
}

/** A one-line summary so a reviewer knows the size of the change before reading it. */
export function summariseImpact(rows: ImpactRow[]): string {
  const consequential = rows.filter((row) => row.consequential).length;
  const protectedCount = rows.filter((row) => row.protectedHistory).length;
  if (!rows.length) return "Nothing in this patient's schedule would change.";
  const parts = [
    consequential === 0
      ? "No visit would change"
      : `${consequential} visit${consequential === 1 ? "" : "s"} would change`,
  ];
  if (protectedCount) {
    parts.push(
      `${protectedCount} completed visit${protectedCount === 1 ? " is" : "s are"} protected and will not move`,
    );
  }
  return `${parts.join("; ")}.`;
}

/**
 * UI spec s4.10 and doc 9 s12-13. Which schedule version this patient is on.
 *
 * Doc 9's whole point is that patients stay pinned to the version they were
 * enrolled under. A screen that shows visit dates without saying which version
 * produced them invites someone to compare two patients and conclude the system
 * is inconsistent, when it is behaving exactly as the protocol requires.
 */
export type VersionContext = {
  label: string;
  detail: string;
  amended: boolean;
};

export function toVersionContext(
  version: {
    name?: string; version_number?: number; status?: string;
    approved_at?: string | null; effective_from?: string | null;
  } | undefined,
  options: { newestVersionNumber?: number } = {},
): VersionContext | null {
  if (!version) return null;
  const number = version.version_number;
  const amended = Boolean(
    options.newestVersionNumber && number && options.newestVersionNumber > number,
  );
  const detail: string[] = [];
  if (version.approved_at) detail.push(`Approved ${displayDate(version.approved_at)}`);
  if (version.effective_from) {
    detail.push(`In force from ${displayDate(version.effective_from)}`);
  }
  if (amended) {
    // Not a warning: staying on the enrolled version is correct behaviour.
    detail.push(
      `A newer version (v${options.newestVersionNumber}) exists; this patient remains on the version they were enrolled under`,
    );
  }
  return {
    label: `${version.name || "Schedule"} v${number ?? "?"}`,
    detail: detail.join(" · "),
    amended,
  };
}

/**
 * UI spec s4.7 and doc 6 s17/s31. One inpatient stay, with its study days
 * underneath it - never one row per day, which would read as several visits.
 */
export type ConfinementRow = {
  id: string;
  episode: string;
  status: string;
  stay: string;
  days: { id: string; label: string; date: string; activities: string }[];
};

export function toConfinementRows(
  episodes: {
    episode_code: string; display_name: string; status: string;
    planned_admission?: string | null; actual_admission?: string | null;
    planned_discharge?: string | null; actual_discharge?: string | null;
    days?: { day_label: string; relative_day: number; scheduled_date?: string | null; activity_codes?: string[] }[];
    explanation?: Record<string, unknown>;
  }[] | undefined,
): ConfinementRow[] {
  return (episodes || []).map((item) => {
    const start = item.actual_admission || item.planned_admission;
    const end = item.actual_discharge || item.planned_discharge;
    return {
      id: item.episode_code,
      episode: item.display_name,
      status: displayConfinementStatus(item.status),
      stay: start
        ? `${displayDate(start)} → ${end ? displayDate(end) : "ongoing"}`
        : "Awaiting admission date",
      days: (item.days || []).map((day) => ({
        id: `${item.episode_code}:${day.relative_day}`,
        label: day.day_label,
        date: displayDate(day.scheduled_date || undefined),
        activities: (day.activity_codes || []).map(titleCase).join(", ") || EMPTY,
      })),
    };
  });
}

const confinementStatusLabels: Record<string, string> = {
  UPCOMING: "Upcoming",
  IN_CONFINEMENT: "Currently admitted",
  EXTENDED: "Stay extended",
  DISCHARGED: "Discharged",
  CANCELLED: "Cancelled",
  NOT_APPLICABLE: "Not applicable",
  WAITING_FOR_ANCHOR: "Awaiting dates",
};

export function displayConfinementStatus(value: string): string {
  return confinementStatusLabels[value] || titleCase(value);
}

/**
 * Doc 4 s7. When the protocol does not say whether a dependent visit counts from
 * the planned or the actual previous date, approval is blocked. These are the
 * three answers a reviewer can give, with what each one DOES - because the
 * difference is real visit dates for real patients, not a preference.
 */
export type DependencyModeChoice = {
  value: "NOMINAL" | "ACTUAL_PREVIOUS_EVENT" | "MANUAL";
  label: string;
  effect: string;
};

export const DEPENDENCY_MODE_CHOICES: DependencyModeChoice[] = [
  {
    value: "NOMINAL",
    label: "Keep the original schedule",
    effect: "Later visits stay on their planned dates even when this one runs late.",
  },
  {
    value: "ACTUAL_PREVIOUS_EVENT",
    label: "Shift from the actual date",
    effect: "Later visits move by however late this one actually happened.",
  },
  {
    value: "MANUAL",
    label: "Decide each time",
    effect: "No date is calculated; a person sets each following visit.",
  },
];

export type DependencyReviewRow = {
  id: string;
  visit: string;
  dependsOn: string;
  current: string;
};

/** The visits whose dependency rule is blocking approval. */
export function toDependencyReviewRows(
  schedule: UniversalSchedule | undefined,
): DependencyReviewRow[] {
  return (schedule?.events || [])
    .filter((event) => {
      const dependencies = (event.dependencies || []) as { source_event_code?: string }[];
      return dependencies.length > 0
        && (!event.dependency_mode || event.dependency_mode === "UNCLEAR");
    })
    .map((event) => ({
      id: event.id,
      visit: event.display_name,
      dependsOn: ((event.dependencies || []) as { source_event_code?: string }[])
        .map((item) => titleCase(item.source_event_code || ""))
        .filter(Boolean)
        .join(", ") || EMPTY,
      current: event.dependency_mode === "UNCLEAR"
        ? "The protocol does not say"
        : "Not yet set",
    }));
}

/**
 * Doc 8 s33. Who each visit applies to, so a reviewer can check the arm and
 * cohort rules without reading the raw applicability expressions.
 */
export type ApplicabilityReviewRow = {
  id: string;
  visit: string;
  appliesTo: string;
  restricted: boolean;
};

export function toApplicabilityReviewRows(
  schedule: UniversalSchedule | undefined,
): ApplicabilityReviewRow[] {
  return (schedule?.events || []).map((event) => {
    const rules = (event.applicability || []) as {
      dimension?: string; operator?: string; values?: string[];
    }[];
    return {
      id: event.id,
      visit: event.display_name,
      appliesTo: rules.length
        ? rules.map((rule) => {
            const dimension = titleCase(rule.dimension || "Group");
            const values = (rule.values || []).join(", ") || "requires review";
            return `${dimension}: ${rule.operator === "NOT_IN" ? "excluding " : ""}${values}`;
          }).join(" · ")
        : "All enrolled patients",
      restricted: rules.length > 0,
    };
  });
}

/**
 * Doc 1 s27. The patient's visits in the order they happen, with the undated
 * ones kept at the end rather than dropped - a visit waiting on an anchor is
 * still part of the patient's plan.
 */
export type TimelineEntry = {
  id: string;
  date: string;
  visit: string;
  status: string;
  detail: string | null;
  past: boolean;
  undated: boolean;
};

export function toTimelineEntries(
  rows: PatientScheduleRow[],
  today: string = new Date().toISOString().slice(0, 10),
): TimelineEntry[] {
  const entries = rows.map((row) => {
    const iso = isoFromRow(row);
    return {
      id: row.patientEventId,
      date: row.actualDate !== EMPTY ? row.actualDate : row.expectedDate,
      visit: row.visitName,
      status: row.status,
      detail: row.unscheduledReason || (row.visitMode || null),
      past: Boolean(iso && iso <= today),
      undated: !iso,
      _sort: iso || "",
    };
  });
  entries.sort((left, right) => {
    if (!left._sort && !right._sort) return 0;
    if (!left._sort) return 1;
    if (!right._sort) return -1;
    return left._sort < right._sort ? -1 : 1;
  });
  return entries.map(({ _sort, ...entry }) => entry);
}

/** The row's own machine date, when it has one. Display strings do not sort. */
function isoFromRow(row: PatientScheduleRow): string | null {
  const source = row.actualIso || row.expectedIso;
  return source || null;
}

export type ActionBoardItem = {
  id: string;
  patientId: string;
  patient: string;
  title: string;
  detail: string | null;
  due: string | null;
};

export type ActionBoardGroup = {
  id: string;
  label: string;
  items: ActionBoardItem[];
};

const actionCategoryLabels: Record<string, string> = {
  IMPACT_CONFIRMATION: "Waiting for your confirmation",
  ANCHOR_CONFIRMATION: "Reference dates to confirm",
  CONDITION_CONFIRMATION: "Clinical conditions to confirm",
  VISIT_OVERDUE: "Overdue visits",
  DEVIATION: "Protocol deviations",
  VISIT_DUE: "Visits due soon",
  ACTIVITY_INCOMPLETE: "Assessments not recorded",
  CONDITION_ACTIVE: "Active conditions",
};

/**
 * Group the action board by category, keeping the engine's priority order
 * (requirement doc 2 s13, doc 3 s28, doc 10 s24). The engine already decided
 * WHAT belongs here and bounded it; this only decides how it reads.
 */
export function toActionBoardGroups(
  actions: {
    category: string; priority: number; patient_id: string;
    patient_code?: string | null; title: string;
    detail?: string | null; due_date?: string | null; reference?: string | null;
  }[] | undefined,
): ActionBoardGroup[] {
  const ordered = [...(actions || [])].sort((left, right) => left.priority - right.priority);
  const groups = new Map<string, ActionBoardGroup>();
  ordered.forEach((item, index) => {
    const label = actionCategoryLabels[item.category] || titleCase(item.category);
    if (!groups.has(item.category)) {
      groups.set(item.category, { id: item.category, label, items: [] });
    }
    groups.get(item.category)!.items.push({
      id: `${item.category}:${item.reference || item.patient_id}:${index}`,
      patientId: item.patient_id,
      patient: item.patient_code || "Patient",
      title: item.title,
      detail: item.detail || null,
      due: item.due_date ? displayDate(item.due_date) : null,
    });
  });
  return [...groups.values()];
}

export type CompletionSummary = {
  done: number;
  required: number;
  label: string;
  complete: boolean;
  outstanding: string[];
};

/**
 * Doc 5 s26. "7 of 8 complete" against the REQUIRED assessments.
 *
 * Optional assessments are excluded from the denominator on purpose: counting
 * them would make a visit where every required assessment was done read as
 * incomplete, and a CRC chasing a false gap stops trusting the number.
 */
export function toCompletionSummary(
  activities: PatientActivity[] | undefined,
): CompletionSummary {
  const required = (activities || []).filter(
    (item) => (item.requiredness || "REQUIRED") === "REQUIRED");
  const done = required.filter((item) => item.status === "COMPLETED");
  const outstanding = required
    .filter((item) => item.status !== "COMPLETED")
    .map((item) => titleCase(item.activity_code || "assessment"));
  if (!required.length) {
    return {
      done: 0, required: 0, complete: false,
      label: "No required assessments recorded",
      outstanding: [],
    };
  }
  return {
    done: done.length,
    required: required.length,
    complete: done.length === required.length,
    label: `${done.length} of ${required.length} required assessments complete`,
    outstanding,
  };
}

export type ParityRow = {
  id: string;
  visit: string;
  kind: string;
  operational: string;
  engine: string;
  detail: string;
};

const parityKindLabels: Record<string, string> = {
  MISSING_IN_ENGINE: "Only in the current system",
  MISSING_IN_LEGACY: "Only in the new engine",
  DATE_DIFFERS: "Different date",
  WINDOW_DIFFERS: "Different window",
  ENGINE_UNDATED: "The engine will not date this",
  LEGACY_UNDATED: "Currently undated",
  AMBIGUOUS_MATCH: "Cannot be matched",
};

/**
 * The differences a person signs off before a trial's visit reads move to the
 * engine. Every row is a reason NOT to cut over yet, so none of them is styled
 * as informational.
 */
export function toParityRows(
  differences: {
    kind: string; key: string; name: string;
    legacy?: string | null; engine?: string | null; detail: string;
  }[] | undefined,
): ParityRow[] {
  return (differences || []).map((item, index) => ({
    id: `${item.key}:${item.kind}:${index}`,
    visit: item.name || titleCase(item.key),
    kind: parityKindLabels[item.kind] || titleCase(item.kind),
    operational: item.legacy || EMPTY,
    engine: item.engine || EMPTY,
    detail: item.detail,
  }));
}

/** One line a person can act on: is this trial safe to move, and if not why. */
export function describeReadiness(status: {
  read_mode: string; ready: boolean; patients: number; checked: number;
  reasons: string[];
} | undefined): string {
  if (!status) return "";
  const where = status.read_mode === "ENGINE"
    ? "Visit dates come from the schedule engine."
    : "Visit dates come from the current system.";
  if (status.ready) {
    return `${where} All ${status.patients} patient${status.patients === 1 ? "" : "s"} match; this trial can move to the engine.`;
  }
  return `${where} ${status.reasons.join(" ")}`;
}

export type ScheduleChangeRow = {
  id: string;
  category: string;
  target: string;
  summary: string;
  detail: string | null;
  clinical: boolean;
};

const changeTypeLabels: Record<string, string> = {
  VISIT_ADDED: "Visit added",
  VISIT_REMOVED: "Visit removed",
  VISIT_RENAMED: "Renamed",
  TIMING_CHANGED: "Timing changed",
  WINDOW_WIDENED: "Window widened",
  WINDOW_NARROWED: "Window narrowed",
  ACTIVITY_ADDED: "Assessment added",
  ACTIVITY_REMOVED: "Assessment removed",
  ACTIVITY_CHANGED: "Assessment changed",
  APPLICABILITY_CHANGED: "Applies to different patients",
  CONDITION_CHANGED: "Trigger changed",
  DEPENDENCY_CHANGED: "Dependency changed",
  RECURRENCE_CHANGED: "Repeat changed",
  VISIT_MODE_CHANGED: "Attendance changed",
  ANCHOR_ADDED: "Anchor added",
  ANCHOR_REMOVED: "Anchor removed",
};

/**
 * Doc 9 s10. Clinically significant changes sort first, because that is the
 * question a reviewer is actually answering: does this amendment affect anyone
 * already enrolled?
 */
export function toScheduleChangeRows(
  changes: TypedScheduleChange[] | undefined,
): ScheduleChangeRow[] {
  return (changes || [])
    .map((item, index) => ({
      id: `${item.entity_code}:${item.change_type}:${index}`,
      category: changeTypeLabels[item.change_type] || titleCase(item.change_type),
      target: titleCase(item.entity_code),
      summary: item.summary,
      detail: item.detail || null,
      clinical: item.significance === "CLINICAL",
    }))
    .sort((left, right) => Number(right.clinical) - Number(left.clinical));
}

export type EnrolmentField = {
  id: string;
  label: string;
  options: { value: string; label: string }[];
  /** Disabled until the dimension named in `hint` has been chosen. */
  disabled: boolean;
  hint: string | null;
};

/**
 * Doc 8 s15-s18. A dimension nested inside an unchosen parent is DISABLED with
 * the reason, rather than listing every value in the protocol: offering a cohort
 * from the wrong part lets someone record an assignment that cannot exist.
 */
export function toEnrolmentFields(
  dimensions: EnrolmentDimension[] | undefined,
): EnrolmentField[] {
  return (dimensions || []).map((item) => ({
    id: item.dimension_type,
    label: item.display_name,
    options: item.options.map((option) => ({
      value: option.code, label: option.display_name,
    })),
    disabled: item.blocked || item.options.length === 0,
    hint: item.reason ? sentenceCase(item.reason) : null,
  }));
}

const sentenceCase = (value: string) =>
  value.charAt(0).toUpperCase() + value.slice(1);

export type AnchorStatusRow = {
  id: string;
  anchor: string;
  status: string;
  date: string;
  detail: string;
  needsAttention: boolean;
};

const anchorStatusLabels: Record<string, string> = {
  NOT_REQUIRED: "Not required",
  AWAITING_EVENT: "Awaiting event",
  // "Planned" must never read as though the event happened (doc 1 s22).
  PLANNED: "Planned date",
  ACTUAL: "Actual date",
  CONFIRMED: "Confirmed",
  CORRECTED: "Corrected",
};

/**
 * Doc 1 s22. A site reading this needs to know at a glance which anchors still
 * need something from them, so NOT_REQUIRED and CONFIRMED are the only two
 * statuses that are finished.
 */
export function toAnchorStatusRows(
  anchors: AnchorStatusView[] | undefined,
): AnchorStatusRow[] {
  return (anchors || []).map((item) => ({
    id: item.anchor_code,
    anchor: item.display_name,
    status: anchorStatusLabels[item.status] || titleCase(item.status),
    date: displayDate(item.value || undefined),
    detail: item.awaiting_event_code
      ? `Awaiting ${titleCase(item.awaiting_event_code)}`
      : item.reason,
    needsAttention:
      item.status !== "NOT_REQUIRED" && item.status !== "CONFIRMED"
      && item.status !== "CORRECTED",
  }));
}

/** Doc 3 s4 and s36: never let the last visible row imply the protocol ended. */
export function toRepeatRuleRows(
  summaries: RepeatRuleSummary[] | undefined,
): RepeatRuleRow[] {
  return (summaries || []).filter((item) => item.continues).map((item) => ({
    id: item.event_code,
    eventCode: titleCase(item.event_code),
    cadence: titleCase(item.cadence),
    continuation: `Continues — ${item.termination}`,
    nextDate: displayDate(item.next_unmaterialized || undefined),
  }));
}

const scopeLabels: Record<string, string> = {
  CELL: "this visit only",
  ROW: "every occurrence of this activity",
  COLUMN: "every activity at this visit",
  VISIT: "this visit",
  ACTIVITY: "this activity",
  BLOCK: "this schedule block",
  ARM: "one treatment arm",
  COHORT: "one cohort",
  SUBSTUDY: "one substudy",
  PERIOD: "one study period",
  GLOBAL: "the whole schedule",
};

export function toQualifierDetails(qualifiers: Qualifier[] | undefined): QualifierDetail[] {
  return (qualifiers || []).map((qualifier) => ({
    id: qualifier.id,
    marker: qualifier.marker ? `Footnote ${qualifier.marker}` : "Protocol note",
    text: qualifier.text,
    appliesTo: scopeLabels[qualifier.scope] || titleCase(qualifier.scope),
    category: titleCase(qualifier.category),
    resolved: qualifier.resolved,
  }));
}

function qualifierIndicator(details: QualifierDetail[]): string {
  if (!details.length) return EMPTY;
  const unresolved = details.filter((item) => !item.resolved).length;
  if (unresolved) return `${details.length} note${details.length > 1 ? "s" : ""} · needs review`;
  return details.map((item) => item.marker).join(", ");
}

function visitLabel(event: ScheduleEvent, index: number): string {
  const candidate = event.protocol_label?.trim();
  if (candidate && /^(visit\s*)?v?\d+[a-z]?\+?$/i.test(candidate)) {
    const normalized = candidate.replace(/^visit\s*/i, "V").replace(/^([0-9])/, "V$1");
    return event.recurrence && !normalized.endsWith("+") ? `${normalized}+` : normalized;
  }
  return `V${index + 1}${event.recurrence ? "+" : ""}`;
}

function applicabilityText(event: ScheduleEvent): string {
  if (!event.applicability.length) return "All enrolled patients";
  return event.applicability.map((item) => {
    const rule = item as { dimension?: string; operator?: string; values?: string[] };
    const dimension = titleCase(rule.dimension || "Protocol group");
    const values = rule.values?.join(", ") || "requires review";
    return `${dimension}: ${rule.operator === "NOT_IN" ? "excluding " : ""}${values}`;
  }).join(" · ");
}

export function toProtocolScheduleRows(
  schedule: UniversalSchedule,
  projections: ScheduleProjection[],
  conditionalEventCodes: string[] = [],
): ProtocolScheduleRow[] {
  const byId = new Map(projections.map((projection) => [projection.event_id, projection]));
  const conditional = new Set(conditionalEventCodes);
  return schedule.events.map((event, index) => {
    const projection = byId.get(event.id);
    const recurring = recurrenceText(event.recurrence);
    const isConditional = conditional.has(event.code) || event.conditions.length > 0;
    const conditionPrefix = isConditional ? "If required by protocol" : null;
    const activityQualifiers = event.activities.flatMap(
      (activity) => toQualifierDetails(activity.qualifiers));
    const qualifierDetails = [...toQualifierDetails(event.qualifiers), ...activityQualifiers];
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
      appliesTo: applicabilityText(event),
      status: titleCase(projection?.status || event.interpretation_status),
      evidenceRefs: projection?.evidence_refs || event.evidence_refs,
      requiresReview: projection?.requires_review ?? event.requires_review,
      qualifier: qualifierIndicator(qualifierDetails),
      qualifierDetails,
      isConditional,
      allowedVisitModes: event.allowed_visit_modes || [],
      repeatRule: recurring,
      activityRows: toProtocolActivityRows(event.activities),
    };
  });
}

/**
 * Conditional requirements render in their own compact table below the main
 * schedule (UI spec 4.3). They must never appear as dated rows before a PI or CRC
 * confirms that the condition occurred for that patient.
 */
export function toConditionalRequirementRows(
  conditions: ConditionalDefinition[],
): ConditionalRequirementRow[] {
  const actionText: Record<string, string> = {
    ADD_EVENT: "Activate",
    REPEAT_EVENT: "Repeat",
    CANCEL_EVENT: "Cancel",
    STOP_BLOCK: "Stop",
    PAUSE_BLOCK: "Pause",
    RESUME_BLOCK: "Resume",
    EXTEND_CONFINEMENT: "Extend stay",
    MANUAL_REVIEW: "Flag for review of",
  };
  return conditions.map((condition) => ({
    id: condition.condition_code,
    condition: condition.display_name || condition.protocol_label,
    trigger: "PI/CRC confirms",
    action: condition.actions
      .map((action) => `${actionText[action.action_type] || titleCase(action.action_type)} `
        + titleCase(action.target_code))
      .join(" · ") || "No schedule change",
    timing: condition.occurrence_date
      ? `Occurred ${displayDate(condition.occurrence_date)}`
      : "On confirmation",
    status: displayPatientState(condition.state),
    requiresReview: condition.requires_review
      || condition.interpretation_status === "UNRESOLVED",
    evidenceRefs: condition.evidence_refs,
  }));
}

/**
 * The day-wise activity schedule shown when a visit row is expanded (UI spec 4.6).
 * An activity whose anchor time has not been recorded shows why it is waiting
 * rather than a guessed clock time.
 */
export function toDayScheduleRows(activities: PatientActivity[] = []): DayScheduleRow[] {
  return [...activities]
    .sort((left, right) => (left.sequence_number ?? 0) - (right.sequence_number ?? 0))
    .map((activity) => {
      const explanation = (activity.explanation || {}) as Record<string, unknown>;
      const waitingFor = explanation.waiting_for_activity;
      return {
        id: activity.id,
        activity: titleCase(activity.activity_code || "Activity"),
        timingRule: typeof waitingFor === "string"
          ? `Relative to ${titleCase(waitingFor)}`
          : (typeof explanation.reason === "string" ? explanation.reason : "As scheduled"),
        plannedTime: displayTime(activity.planned_time),
        actualTime: displayTime(activity.actual_time),
        status: activity.status === "WAITING_FOR_ANCHOR" && typeof waitingFor === "string"
          ? `Awaiting ${titleCase(waitingFor)} Time`
          : displayPatientState(activity.status),
        requiredness: titleCase(activity.requiredness),
      };
    });
}

function displayTime(value?: string): string {
  if (!value) return EMPTY;
  const match = /T(\d{2}):(\d{2})/.exec(value);
  return match ? `${match[1]}:${match[2]}` : displayDate(value);
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
      appliesTo: "All enrolled patients",
      status: "Unresolved",
      evidenceRefs: [],
      requiresReview: false,
      qualifier: EMPTY,
      qualifierDetails: [],
      isConditional: false,
      allowedVisitModes: [],
      repeatRule: null,
      activityRows: [],
    };
    const row = definition || fallback;
    const earliest = displayDate(event.earliest_date);
    const latest = displayDate(event.latest_date);
    const waiting = displayWaitingReason(event);
    return {
      ...row,
      id: event.id,
      patientEventId: event.id,
      visit: event.occurrence_index > 0 ? `${row.visit}.${event.occurrence_index + 1}` : row.visit,
      dayScheduleRows: toDayScheduleRows(event.activities),
      completion: toCompletionSummary(event.activities),
      // An undated visit shows the event it waits on, never an invented date.
      expectedDate: event.nominal_start_date
        ? displayDate(event.nominal_start_date)
        : (waiting || EMPTY),
      actualDate: displayDate(event.actual_date),
      allowedWindow: earliest === EMPTY && latest === EMPTY
        ? EMPTY
        : `${earliest} – ${latest}`,
      status: displayPatientState(event.status),
      visitMode: displayVisitMode(event.visit_mode, definition?.allowedVisitModes),
      unscheduledReason: event.unscheduled_reason || null,
      isOnDemand: event.status === "AVAILABLE_ON_DEMAND",
      expectedIso: event.nominal_start_date || null,
      actualIso: event.actual_date || null,
    };
  });
}

export type ScheduleVersionRow = {
  id: string;
  scheduleDefinitionId: string;
  name: string;
  protocolVersion: string;
  version: string;
  status: string;
  effectiveFor: string;
  patients: string;
  openable: boolean;
  current: boolean;
};

const scheduleVersionStatus: Record<string, string> = {
  DRAFT: "Draft",
  EXTRACTED: "Extracted",
  VALIDATION_REQUIRED: "Needs Review",
  IN_REVIEW: "In Review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  SUPERSEDED: "Superseded",
  ARCHIVED: "Archived",
};

export type CanonicalScheduleVersion = {
  schedule_definition_id: string;
  schedule_version_id: string;
  name: string;
  schedule_type?: string;
  version_number: number;
  status: string;
  protocol_version_label?: string;
  effective_from?: string | null;
  approved_at?: string | null;
  is_current_protocol_version?: boolean;
  patients?: number;
};

/**
 * Case 10 / doc 9. The trial-level version table.
 *
 * "Effective For" is the column that answers the question a site actually asks -
 * which schedule a patient enrolling today receives, and which one the patients
 * already on study stay on. A version with patients on it is never presented as
 * retired, because those patients are still being scheduled from it.
 */
export function toScheduleVersionRows(
  versions: CanonicalScheduleVersion[] | undefined,
  approvedVersionId?: string | null,
): ScheduleVersionRow[] {
  const rows = versions || [];
  const inForce = (item: CanonicalScheduleVersion) =>
    item.status === "APPROVED"
    && (!item.effective_from || item.effective_from <= new Date().toISOString().slice(0, 10));
  const currentByDefinition = new Map<string, string>();
  for (const item of rows) {
    if (!inForce(item)) continue;
    const seen = rows.find((other) =>
      other.schedule_version_id === currentByDefinition.get(item.schedule_definition_id));
    if (!seen || seen.version_number < item.version_number) {
      currentByDefinition.set(item.schedule_definition_id, item.schedule_version_id);
    }
  }
  return rows.map((item) => {
    const current = currentByDefinition.get(item.schedule_definition_id) === item.schedule_version_id
      || (!!approvedVersionId && approvedVersionId === item.schedule_version_id);
    const patients = item.patients || 0;
    let effectiveFor: string;
    if (current) {
      effectiveFor = "Current new enrollments";
    } else if (item.status === "APPROVED" && item.effective_from) {
      effectiveFor = patients ? "Earlier enrollments" : `Takes effect ${item.effective_from}`;
    } else if (item.status === "APPROVED") {
      effectiveFor = patients ? "Earlier enrollments" : "Approved, not in force";
    } else if (patients) {
      // Approval was withdrawn or superseded, but a patient is still on it.
      effectiveFor = "Earlier enrollments";
    } else {
      effectiveFor = "Not in use";
    }
    return {
      id: item.schedule_version_id,
      scheduleDefinitionId: item.schedule_definition_id,
      name: item.name,
      protocolVersion: item.protocol_version_label || EMPTY,
      version: `Schedule v${item.version_number}`,
      status: scheduleVersionStatus[item.status] || titleCase(item.status),
      effectiveFor,
      patients: String(patients),
      openable: true,
      current,
    };
  });
}

export type EnrolmentReadiness = {
  ready: boolean;
  scheduleVersionId: string | null;
  message: string;
};

/**
 * Whether a patient can be enrolled onto this trial's canonical schedule yet.
 *
 * Enrolment needs an APPROVED version that is in force today. Saying so plainly
 * - rather than letting the enrolment form fail on submit - is the difference
 * between a site knowing the schedule still needs review and a site thinking the
 * product is broken.
 */
export function toEnrolmentReadiness(
  versions: CanonicalScheduleVersion[] | undefined,
): EnrolmentReadiness {
  const rows = versions || [];
  const today = new Date().toISOString().slice(0, 10);
  const inForce = rows
    .filter((item) => item.status === "APPROVED"
      && (!item.effective_from || item.effective_from <= today))
    .sort((left, right) => right.version_number - left.version_number);
  if (inForce.length) {
    return {
      ready: true,
      scheduleVersionId: inForce[0].schedule_version_id,
      message: `${inForce[0].name} · Schedule v${inForce[0].version_number}`,
    };
  }
  const pending = rows.filter((item) => item.status === "APPROVED");
  if (pending.length) {
    const soonest = pending
      .map((item) => item.effective_from)
      .filter(Boolean)
      .sort()[0];
    return {
      ready: false, scheduleVersionId: null,
      message: `The approved schedule takes effect on ${soonest}. No version is in force today.`,
    };
  }
  if (rows.length) {
    return {
      ready: false, scheduleVersionId: null,
      message: "The protocol schedule is still in review. Approve it before enrolling patients.",
    };
  }
  return {
    ready: false, scheduleVersionId: null,
    message: "This trial has no canonical schedule yet.",
  };
}

export type ProtocolActivityRow = {
  id: string;
  activity: string;
  timingRule: string;
  anchor: string;
  window: string;
  requiredness: string;
  qualifier: string;
  evidenceRefs: string[];
  review: string;
};

const requirednessLabels: Record<string, string> = {
  REQUIRED: "Required",
  OPTIONAL: "Optional",
  CONDITIONAL: "Conditional",
  RECOMMENDED: "Recommended",
  NOT_APPLICABLE: "Not applicable",
  UNRESOLVED: "Needs review",
};

const amountText = (raw: unknown): string | null => {
  const amount = raw as { value?: number; unit?: string } | undefined;
  if (!amount || typeof amount.value !== "number" || !amount.unit) return null;
  const unit = amount.unit.toLowerCase();
  const magnitude = Math.abs(amount.value);
  return `${magnitude} ${unit}${magnitude === 1 ? "" : "s"}`;
};

/** The thing an activity's time is measured from, named the way a protocol names it. */
function anchorText(reference: unknown): string {
  const item = reference as {
    kind?: string; code?: string; event_code?: string; activity_code?: string;
  } | undefined;
  if (!item) return EMPTY;
  if (item.kind === "ANCHOR" && item.code) return titleCase(item.code);
  if (item.kind === "EVENT" && item.event_code) return titleCase(item.event_code);
  // Doc 6: an intra-day anchor is another activity's ACTUAL recorded time, which
  // is why saying which activity matters - a planned time is never substituted.
  if (item.kind === "ACTIVITY" && item.activity_code) {
    return `${titleCase(item.activity_code)} (actual time)`;
  }
  return EMPTY;
}

/**
 * Case 6, protocol-review level: an activity's timing RULE and the anchor it is
 * measured from, before any patient exists.
 *
 * The protocol-review table deliberately carries no planned or actual times. A
 * "PK +1h" row is not a time until a dose is given; showing a clock time here
 * would be a number nobody wrote and no patient is bound by.
 */
export function toProtocolActivityRows(
  activities: ScheduleActivity[] | undefined,
): ProtocolActivityRow[] {
  return (activities || []).map((activity, index) => {
    const timing = activity.timing as
      | (Record<string, unknown> & { type?: string }) | undefined;
    const nominal = (timing?.type === "NOMINAL_WITH_WINDOW"
      ? timing.nominal as Record<string, unknown> | undefined
      : timing) as (Record<string, unknown> & { type?: string }) | undefined;
    const window = timing?.type === "NOMINAL_WITH_WINDOW"
      ? timing.window as { before?: unknown; after?: unknown } | undefined
      : undefined;
    const qualifierDetails = toQualifierDetails(activity.qualifiers);

    let timingRule = EMPTY;
    if (nominal?.type === "OFFSET") {
      const offset = nominal.offset as { value?: number } | undefined;
      const text = amountText(nominal.offset);
      if (text) {
        timingRule = (offset?.value ?? 0) === 0
          ? "Same time as anchor"
          : `${(offset?.value ?? 0) < 0 ? "-" : "+"}${text}`;
      }
    } else if (nominal?.type === "WITHIN") {
      const text = amountText(nominal.duration);
      const direction = String(nominal.direction || "AFTER").toLowerCase();
      if (text) timingRule = `Within ${text} ${direction}`;
    } else if (nominal?.type === "NO_LATER_THAN") {
      const text = amountText(nominal.duration);
      if (text) timingRule = `No later than ${text}`;
    } else if (nominal?.type === "RANGE") {
      const start = amountText(nominal.start);
      const end = amountText(nominal.end);
      if (start && end) timingRule = `${start} to ${end}`;
    } else if (nominal?.type === "UNRESOLVED") {
      timingRule = String(nominal.reason || "Timing requires review");
    } else if (nominal?.type === "PROTOCOL_DEFINED") {
      timingRule = "Protocol-defined";
    }

    const before = amountText(window?.before);
    const after = amountText(window?.after);

    return {
      id: activity.id || `${activity.display_name}-${index}`,
      activity: activity.display_name,
      timingRule,
      anchor: anchorText(nominal?.reference),
      // Never invent a window: an activity with no stated tolerance shows none.
      window: before && after
        ? (before === after ? `±${after}` : `-${before} / +${after}`)
        : EMPTY,
      requiredness: requirednessLabels[activity.requiredness] || titleCase(activity.requiredness),
      qualifier: qualifierIndicator(qualifierDetails),
      evidenceRefs: [],
      review: qualifierDetails.some((item) => !item.resolved)
        ? "Needs review"
        : (nominal?.type === "UNRESOLVED" ? "Unresolved" : "OK"),
    };
  });
}
