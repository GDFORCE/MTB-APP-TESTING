export type VisitTiming = {
  day_offset?: number | null;
  day_end?: number | null;
  hour_offset?: number | null;
  hour_end?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
  relative_to?: string | null;
  relative_offset_days?: number | null;
  source_day_label?: string | null;
  // Compatibility with early extraction payloads that used this name. New
  // writes use source_day_label, which is the backend's canonical field.
  source_timing_label?: string | null;
};

export type VisitWindow = {
  window_days?: number | null;
  window_before?: number | null;
  window_after?: number | null;
};

/**
 * Procedure-level protocol detail. These values must stay separate from the
 * visit window: for example a PK draw may have a +/- 2 minute tolerance even
 * when the enclosing visit has no early/late allowance at all.
 */
export type ProtocolProcedure = {
  id?: string;
  name: string;
  timing?: string;
  window?: string;
  condition?: string;
  description?: string;
  evidence_ids?: string[];
};

const cleanLabel = (value?: string | null) => value?.trim() || "";

const textValue = (value: unknown): string => typeof value === "string" ? value.trim() : "";

/**
 * Accept both the canonical extraction shape (`name`, timing/window/condition)
 * and historical patient-safe rows (`label`, description). Invalid entries
 * are ignored instead of leaking `[object Object]` into the editor.
 */
export function normalizeProtocolProcedures(value: unknown): ProtocolProcedure[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry): ProtocolProcedure[] => {
    if (typeof entry === "string") {
      const name = entry.trim();
      return name ? [{ name }] : [];
    }
    if (!entry || typeof entry !== "object") return [];
    const item = entry as Record<string, unknown>;
    const name = textValue(item.name) || textValue(item.label);
    if (!name) return [];
    const evidenceIds = Array.isArray(item.evidence_ids)
      ? item.evidence_ids.map(textValue).filter(Boolean)
      : [];
    return [{
      id: textValue(item.id) || undefined,
      name,
      timing: textValue(item.timing) || undefined,
      window: textValue(item.window) || undefined,
      condition: textValue(item.condition) || undefined,
      description: textValue(item.description) || undefined,
      evidence_ids: evidenceIds.length ? evidenceIds : undefined,
    }];
  });
}

/** Normalise newline/array API values without merging distinct constraints. */
export function normalizeProtocolConstraints(value: unknown): string[] {
  const values = Array.isArray(value) ? value : typeof value === "string" ? value.split(/\r?\n/) : [];
  return values.map(textValue).filter(Boolean);
}

/** Concise export/read-only description without reclassifying any window. */
export function formatProtocolProcedure(procedure: ProtocolProcedure): string {
  const details = [
    procedure.timing ? `Timing: ${procedure.timing}` : "",
    procedure.window ? `Procedure tolerance: ${procedure.window}` : "",
    procedure.condition ? `Condition: ${procedure.condition}` : "",
    procedure.description || "",
  ].filter(Boolean);
  return details.length ? `${procedure.name} (${details.join("; ")})` : procedure.name;
}

const SIMPLE_STUDY_DAY = /^day\s*([+-]?\d+)$/i;
const SIMPLE_STUDY_DAY_RANGE = /^day\s*([+-]?\d+)\s*(?:-|–|—|to)\s*(?:day\s*)?([+-]?\d+)$/i;

const formatSignedDay = (value: number) => value >= 0 ? `+${value}` : String(value);

/**
 * Compact value for the schedule editor's Day column.
 *
 * A protocol's printed Day 1 is display evidence, while day_offset=0 is the
 * calendar arithmetic used to place that visit on the baseline date. Prefer
 * an exact protocol Day label for display, then fall back to the canonical
 * baseline offset. Free-form timing prose never belongs in this narrow column.
 */
export function formatScheduleDay(
  timing: Pick<VisitTiming, "day_offset" | "day_end" | "source_day_label" | "source_timing_label">,
  unspecifiedLabel = "-",
): string {
  const sourceLabel = cleanLabel(timing.source_day_label)
    || cleanLabel(timing.source_timing_label);
  const sourceRange = sourceLabel.match(SIMPLE_STUDY_DAY_RANGE);
  if (sourceRange) {
    return `${formatSignedDay(Number(sourceRange[1]))} to ${formatSignedDay(Number(sourceRange[2]))}`;
  }
  const sourceDay = sourceLabel.match(SIMPLE_STUDY_DAY);
  if (sourceDay) return formatSignedDay(Number(sourceDay[1]));

  const start = timing.day_offset;
  if (typeof start !== "number" || !Number.isFinite(start)) return unspecifiedLabel;
  if (
    typeof timing.day_end === "number"
    && Number.isFinite(timing.day_end)
    && timing.day_end !== start
  ) {
    return `${formatSignedDay(start)} to ${formatSignedDay(timing.day_end)}`;
  }
  return formatSignedDay(start);
}

/**
 * Human-readable protocol timing. The stored offset is calendar arithmetic,
 * not a study-day label: offset 0 means the baseline date and may represent
 * protocol Day 0 or Day 1. Prefer the protocol's exact source label whenever
 * it is available; otherwise use an explicit baseline-relative description.
 */
export function formatVisitTiming(
  timing: VisitTiming,
  unspecifiedLabel = "Timing not specified",
): string {
  const sourceLabel = cleanLabel(timing.source_day_label)
    || cleanLabel(timing.source_timing_label);
  if (sourceLabel) return sourceLabel;

  const hourOffset = timing.hour_offset;
  const hasHourTiming = typeof hourOffset === "number"
    && Number.isFinite(hourOffset)
    && (
      hourOffset !== 0
      || typeof timing.hour_end === "number"
      || timing.hour_offset_basis != null
    );
  if (hasHourTiming) {
    if (timing.hour_offset_basis === "absolute") {
      return formatAbsoluteHourRange(hourOffset, timing.hour_end);
    }
    if (typeof timing.day_offset !== "number" || !Number.isFinite(timing.day_offset)) {
      return unspecifiedLabel;
    }
    const day = formatBaselineOffset(timing.day_offset, unspecifiedLabel);
    return `${day} ${formatHourRange(hourOffset, timing.hour_end)}`;
  }

  const relativeTo = cleanLabel(timing.relative_to);
  if (
    relativeTo
    && (typeof timing.day_offset !== "number" || !Number.isFinite(timing.day_offset))
  ) {
    const gap = timing.relative_offset_days;
    if (typeof gap !== "number" || !Number.isFinite(gap)) return `Relative to ${relativeTo}`;
    if (gap === 0) return `At ${relativeTo}`;
    return gap > 0
      ? `${gap} ${gap === 1 ? "day" : "days"} after ${relativeTo}`
      : `${Math.abs(gap)} ${gap === -1 ? "day" : "days"} before ${relativeTo}`;
  }

  const start = formatBaselineOffset(timing.day_offset, unspecifiedLabel);
  if (
    typeof timing.day_offset === "number"
    && typeof timing.day_end === "number"
    && Number.isFinite(timing.day_end)
    && timing.day_end !== timing.day_offset
  ) {
    return `${start} to ${formatBaselineOffset(timing.day_end, unspecifiedLabel)}`;
  }
  return start;
}

const signedNumber = (value: number) => value >= 0 ? `+${value}` : String(value);

const hourUnit = (value: number) => Math.abs(value) === 1 ? "hour" : "hours";

function formatHourRange(start: number, end?: number | null): string {
  if (typeof end === "number" && Number.isFinite(end) && end !== start) {
    return `${signedNumber(start)} to ${signedNumber(end)} hours`;
  }
  return `${signedNumber(start)} ${hourUnit(start)}`;
}

function formatAbsoluteHourRange(start: number, end?: number | null): string {
  if (typeof end === "number" && Number.isFinite(end) && end !== start) {
    return `Baseline ${signedNumber(start)} to ${signedNumber(end)} hours`;
  }
  return `Baseline ${signedNumber(start)} ${hourUnit(start)}`;
}

export function formatBaselineOffset(
  offset?: number | null,
  unspecifiedLabel = "Timing not specified",
): string {
  if (typeof offset !== "number" || !Number.isFinite(offset)) return unspecifiedLabel;
  if (offset === 0) return "Baseline";
  return offset > 0
    ? `Baseline +${offset} ${offset === 1 ? "day" : "days"}`
    : `Baseline ${offset} ${offset === -1 ? "day" : "days"}`;
}

export function parseOptionalDayOffset(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isInteger(parsed) ? parsed : null;
}

export function formatVisitWindow(window: VisitWindow, compact = false): string {
  const hasSymmetricWindow = typeof window.window_days === "number"
    && Number.isFinite(window.window_days);
  const symmetric = hasSymmetricWindow ? Math.max(0, window.window_days as number) : 0;
  const hasAsymmetricWindow = typeof window.window_before === "number"
    || typeof window.window_after === "number";
  if (hasAsymmetricWindow) {
    const before = typeof window.window_before === "number" ? window.window_before : symmetric;
    const after = typeof window.window_after === "number" ? window.window_after : symmetric;
    return `-${before}${compact ? "d" : " days"} / +${after}${compact ? "d" : " days"}`;
  }
  if (!hasSymmetricWindow) return "-";
  return `±${symmetric}${compact ? "d" : ` ${symmetric === 1 ? "day" : "days"}`}`;
}

/**
 * Format the calendar date encoded at the start of an ISO value without
 * allowing the device timezone to move it to the previous/next day.
 */
export function formatIsoCalendarDate(
  value?: string | null,
  fallback = "Date not available",
): string {
  const match = value?.trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])/);
  if (!match) return fallback;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return fallback;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
