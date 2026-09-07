import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  PanResponder,
  Platform,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  FileStack,
  GripVertical,
  Download,
  Edit3,
  MessageSquareText,
  Plus,
  Sparkles,
  Stethoscope,
  Trash2,
  X,
} from "lucide-react-native";
import * as DocumentPicker from "expo-document-picker";
import { api } from "@/src/api/client";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Small } from "@/src/components/ui";
import {
  formatScheduleDay,
  formatProtocolProcedure,
  formatVisitTiming,
  formatVisitWindow,
  normalizeProtocolConstraints,
  normalizeProtocolProcedures,
  parseOptionalDayOffset,
  ProtocolProcedure,
} from "@/src/lib/visit-timing";
import { colors, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

type Row = {
  id?: string;
  visit_number?: number;
  name: string;
  day_offset: string;
  source_day_label: string;
  day_end: number | null;
  calendar_offset_value: number | null;
  calendar_offset_unit: "month" | "year" | null;
  hour_offset: number | null;
  hour_end: number | null;
  hour_offset_basis: "absolute" | "within_day" | null;
  window_before: number | null;
  window_after: number | null;
  relative_to: string | null;
  relative_offset_days: number | null;
  arm_label: string | null;
  period: string | null;
  // Which independent Schedule of Assessments (substudy) this visit belongs
  // to, when a protocol prints more than one. Null for every ordinary,
  // single-schedule trial.
  substudy_label: string | null;
  visit_type: string | null;
  anchor_study_day: 0 | 1 | null;
  includes_day_zero: boolean | null;
  window_days: string;
  activities: string;
  procedures: ProtocolProcedure[];
  operational_constraints: string;
  clinical_tasks: string;
  admin_tasks: string;
  comments: string;
  extraction_warning: boolean;
  review_status: "pending" | "ok";
  extracted_from_protocol: boolean;
  field_evidence: { field: string; evidence_ids: string[] }[];
};

type ExtractedVisit = {
  name: string;
  day_offset: number | null;
  source_day_label?: string | null;
  source_timing_label?: string | null;
  day_end?: number | null;
  calendar_offset_value?: number | null;
  calendar_offset_unit?: "month" | "year" | null;
  hour_offset?: number | null;
  hour_end?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
  window_before?: number | null;
  window_after?: number | null;
  relative_to?: string | null;
  relative_offset_days?: number | null;
  arm?: string | null;
  arm_label?: string | null;
  period?: string | null;
  visit_type?: string | null;
  anchor_study_day?: 0 | 1 | null;
  includes_day_zero?: boolean | null;
  window_days: number | null;
  activities: string[];
  procedures?: unknown;
  operational_constraints?: string[] | string | null;
  clinical_tasks?: string[];
  admin_tasks?: string[];
  comments?: string;
  extraction_warning?: boolean;
  review_status?: "pending" | "ok";
  field_evidence?: { field: string; evidence_ids: string[] }[];
};

type ExtractionVerification = {
  status: "verified" | "needs_review" | "not_run";
  confidence?: number | null;
  refinement_count?: number;
  issues?: string[];
  accuracy?: Record<string, number | null>;
};

type ScheduleNumbering = {
  anchor_study_day?: 0 | 1 | null;
  includes_day_zero?: boolean | null;
};

// One independently-extracted Schedule of Assessments (e.g. one substudy of
// a seamless Phase 2/3 protocol). A single-schedule protocol never produces
// more than one of these, and the whole screen renders exactly as it did
// before this concept existed.
type ScheduleVariant = {
  optionId: string;
  label: string;
  description?: string;
  scheduleDefinitionId: string | null;
  rows: Row[];
  // The rows already confirmed for this substudy, last time the trial's
  // saved schedule was loaded/saved — i.e. this variant's own "original",
  // scoped so switching between substudies never confuses one's diff/delete
  // logic with another's.
  savedRows: Row[];
  verification: ExtractionVerification | null;
  // Notes the extractor recorded about what it had to assume to produce
  // these rows (an open-ended cycle tail it capped, an anchor it could not
  // resolve). Not field-level issues — statements about the schedule as a
  // whole, which the reviewer has to confirm before saving.
  assumptions: string[];
};

const hasAbsoluteHourTiming = (timing: {
  hour_offset?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
}) => timing.hour_offset_basis === "absolute"
  && typeof timing.hour_offset === "number"
  && Number.isFinite(timing.hour_offset);

const blankRow = (name = "New Visit"): Row => ({
  name,
  day_offset: "0",
  source_day_label: "",
  day_end: null,
  calendar_offset_value: null,
  calendar_offset_unit: null,
  hour_offset: null,
  hour_end: null,
  hour_offset_basis: null,
  window_before: null,
  window_after: null,
  relative_to: null,
  relative_offset_days: null,
  arm_label: null,
  period: null,
  substudy_label: null,
  visit_type: null,
  anchor_study_day: null,
  includes_day_zero: null,
  window_days: "",
  activities: "",
  procedures: [],
  operational_constraints: "",
  clinical_tasks: "",
  admin_tasks: "",
  comments: "",
  extraction_warning: false,
  review_status: "ok",
  extracted_from_protocol: false,
  field_evidence: [],
});

const templateToRow = (template: any): Row => {
  const hasOffset = typeof template.day_offset === "number"
    && Number.isInteger(template.day_offset);
  return {
    id: template.id,
    visit_number: template.visit_number,
    name: template.name ?? "",
    day_offset: hasOffset ? String(template.day_offset) : "",
    source_day_label: template.source_day_label?.trim()
      || template.source_timing_label?.trim()
      || "",
    day_end: template.day_end ?? null,
    calendar_offset_value: template.calendar_offset_value ?? null,
    calendar_offset_unit: template.calendar_offset_unit ?? null,
    hour_offset: template.hour_offset ?? null,
    hour_end: template.hour_end ?? null,
    hour_offset_basis: template.hour_offset_basis ?? null,
    window_before: template.window_before ?? null,
    window_after: template.window_after ?? null,
    relative_to: template.relative_to ?? null,
    relative_offset_days: template.relative_offset_days ?? null,
    arm_label: template.arm_label ?? template.arm ?? null,
    period: template.period ?? null,
    substudy_label: template.substudy_label ?? null,
    visit_type: template.visit_type ?? null,
    anchor_study_day: template.anchor_study_day ?? null,
    includes_day_zero: template.includes_day_zero ?? null,
    window_days: template.window_days == null ? "" : String(template.window_days),
    activities: (template.activities ?? []).join(", "),
    procedures: normalizeProtocolProcedures(template.procedures),
    operational_constraints: normalizeProtocolConstraints(template.operational_constraints).join("\n"),
    clinical_tasks: (template.clinical_tasks ?? []).join(", "),
    admin_tasks: (template.admin_tasks ?? []).join(", "),
    comments: template.comments ?? "",
    extraction_warning: Boolean(template.extraction_warning),
    review_status: template.review_status === "pending" ? "pending" : "ok",
    extracted_from_protocol: Boolean(template.extracted_from_protocol),
    field_evidence: Array.isArray(template.field_evidence) ? template.field_evidence : [],
  };
};

const extractedVisitsToRows = (
  visits: ExtractedVisit[],
  numbering: ScheduleNumbering = {},
): Row[] => visits.map((visit) => {
  const hasDayOffset = typeof visit.day_offset === "number"
    && Number.isInteger(visit.day_offset);
  const missingComputableTiming = !hasDayOffset && !hasAbsoluteHourTiming(visit);
  return {
    name: visit.name ?? "",
    day_offset: hasDayOffset ? String(visit.day_offset) : "",
    source_day_label: visit.source_day_label?.trim()
      || visit.source_timing_label?.trim()
      || "",
    day_end: visit.day_end ?? null,
    calendar_offset_value: visit.calendar_offset_value ?? null,
    calendar_offset_unit: visit.calendar_offset_unit ?? null,
    hour_offset: visit.hour_offset ?? null,
    hour_end: visit.hour_end ?? null,
    hour_offset_basis: visit.hour_offset_basis ?? null,
    window_before: visit.window_before ?? null,
    window_after: visit.window_after ?? null,
    relative_to: visit.relative_to ?? null,
    relative_offset_days: visit.relative_offset_days ?? null,
    arm_label: visit.arm_label ?? visit.arm ?? null,
    period: visit.period ?? null,
    // Stamped by the caller for a multi-substudy extraction (see
    // runAutofill); a single-schedule extraction leaves every row untagged.
    substudy_label: null,
    visit_type: visit.visit_type ?? null,
    anchor_study_day: visit.anchor_study_day ?? numbering.anchor_study_day ?? null,
    includes_day_zero: visit.includes_day_zero ?? numbering.includes_day_zero ?? null,
    window_days: visit.window_days == null ? "" : String(visit.window_days),
    activities: (visit.activities ?? []).join(", "),
    procedures: normalizeProtocolProcedures(visit.procedures),
    operational_constraints: normalizeProtocolConstraints(visit.operational_constraints).join("\n"),
    clinical_tasks: (visit.clinical_tasks ?? visit.activities ?? []).join(", "),
    admin_tasks: (visit.admin_tasks ?? []).join(", "),
    comments: visit.comments ?? "",
    extraction_warning: Boolean(visit.extraction_warning) || missingComputableTiming,
    review_status: visit.review_status === "pending" || missingComputableTiming ? "pending" : "ok",
    extracted_from_protocol: true,
    field_evidence: Array.isArray(visit.field_evidence) ? visit.field_evidence : [],
  };
});

type RawScheduleVariant = {
  option_id?: string;
  option_label?: string;
  option_description?: string;
  schedule_definition_id?: string | null;
  visits?: ExtractedVisit[];
  anchor_study_day?: 0 | 1 | null;
  includes_day_zero?: boolean | null;
  verification?: ExtractionVerification | null;
  assumptions?: string[];
};

// Shared by every path that produces a ScheduleVariant from a freshly
// extracted/consumed schedule — a fan-out extraction (runAutofill) and a
// prepared-at-Add-Trial consume (load()) return the same payload shape.
const buildScheduleVariant = (
  variant: RawScheduleVariant,
  existingVariants: ScheduleVariant[],
): ScheduleVariant => {
  // A re-extraction after edits already exist: keep whatever was already
  // confirmed/saved for a substudy with the same label, so re-running
  // autofill doesn't make save() think every row is brand new.
  const existing = existingVariants.find((item) => item.label === variant.option_label);
  const extractedRows = extractedVisitsToRows(variant.visits ?? [], {
    anchor_study_day: variant.anchor_study_day ?? null,
    includes_day_zero: variant.includes_day_zero ?? null,
  }).map((row) => ({ ...row, substudy_label: variant.option_label || null }));
  return {
    optionId: variant.option_id || "",
    label: variant.option_label || "",
    description: variant.option_description,
    scheduleDefinitionId: variant.schedule_definition_id ?? null,
    rows: extractedRows,
    savedRows: existing ? existing.savedRows : [],
    verification: variant.verification ?? null,
    assumptions: variant.assumptions ?? [],
  };
};

const sameRow = (left: Row, right: Row) =>
  left.name.trim() === right.name.trim()
  && parseOptionalDayOffset(left.day_offset) === parseOptionalDayOffset(right.day_offset)
  && left.source_day_label.trim() === right.source_day_label.trim()
  && left.day_end === right.day_end
  && left.calendar_offset_value === right.calendar_offset_value
  && left.calendar_offset_unit === right.calendar_offset_unit
  && left.hour_offset === right.hour_offset
  && left.hour_end === right.hour_end
  && left.hour_offset_basis === right.hour_offset_basis
  && left.window_before === right.window_before
  && left.window_after === right.window_after
  && left.relative_to === right.relative_to
  && left.relative_offset_days === right.relative_offset_days
  && left.arm_label === right.arm_label
  && left.period === right.period
  && left.substudy_label === right.substudy_label
  && left.visit_type === right.visit_type
  && left.anchor_study_day === right.anchor_study_day
  && left.includes_day_zero === right.includes_day_zero
  && (left.window_days.trim() === "" ? null : Number(left.window_days))
    === (right.window_days.trim() === "" ? null : Number(right.window_days))
  && left.activities.trim() === right.activities.trim()
  && JSON.stringify(left.procedures) === JSON.stringify(right.procedures)
  && left.operational_constraints.trim() === right.operational_constraints.trim()
  && left.clinical_tasks.trim() === right.clinical_tasks.trim()
  && left.admin_tasks.trim() === right.admin_tasks.trim()
  && left.comments.trim() === right.comments.trim()
  && left.extraction_warning === right.extraction_warning
  && left.review_status === right.review_status
  && left.extracted_from_protocol === right.extracted_from_protocol
  && JSON.stringify(left.field_evidence) === JSON.stringify(right.field_evidence);

const dayForRow = (row: Row) => formatScheduleDay({
  day_offset: parseOptionalDayOffset(row.day_offset),
  day_end: row.day_end,
  source_day_label: row.source_day_label,
});

const canonicalTimingForRow = (row: Row) => formatVisitTiming({
  day_offset: parseOptionalDayOffset(row.day_offset),
  day_end: row.day_end,
  hour_offset: row.hour_offset,
  hour_end: row.hour_end,
  hour_offset_basis: row.hour_offset_basis,
  relative_to: row.relative_to,
  relative_offset_days: row.relative_offset_days,
}, "-");

const rowNeedsReview = (row: Row) => (
  row.extraction_warning
  || row.review_status === "pending"
);

const csvCell = (value: string | number | boolean) => `"${String(value).replace(/"/g, "\"\"")}"`;

export default function VisitScheduleEditor() {
  const router = useRouter();
  const { id, extractionId, extractionIds } = useLocalSearchParams<{
    id: string;
    extractionId?: string;
    extractionIds?: string;
  }>();
  const [rows, setRows] = useState<Row[]>([]);
  const [original, setOriginal] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractErr, setExtractErr] = useState("");
  const [verification, setVerification] = useState<ExtractionVerification | null>(null);
  const [assumptions, setAssumptions] = useState<string[]>([]);
  // Populated only when the protocol prints more than one independent
  // Schedule of Assessments. `rows`/`original`/`verification` above always
  // describe whichever one is `activeVariantId` — the one currently loaded
  // into the interactive editor below.
  const [variants, setVariants] = useState<ScheduleVariant[]>([]);
  const [expandedVariantIds, setExpandedVariantIds] = useState<Set<string>>(new Set());
  const [activeVariantId, setActiveVariantId] = useState<string | null>(null);
  const [savedVariantIds, setSavedVariantIds] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState(false);
  const [filter, setFilter] = useState<"all" | "pending" | "ok">("all");
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [confirmSave, setConfirmSave] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showReviewNotes, setShowReviewNotes] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr("");
    try {
      const response = await api.get(`/trials/${id}/visits`);
      const templates: any[] = response.data ?? [];
      let loaded = templates.map(templateToRow);
      const pendingIds = [
        ...(extractionIds ? extractionIds.split(",") : []),
        ...(extractionId ? [extractionId] : []),
      ].map((item) => item.trim()).filter(Boolean);
      const uniquePendingIds = [...new Set(pendingIds)];
      if (!loaded.length && uniquePendingIds.length > 1) {
        // The protocol printed more than one independent Schedule of
        // Assessments — Add Trial already extracted every one of them, so
        // consume each prepared draft and show them as their own cards,
        // exactly like a fresh multi-substudy autofill would (see
        // buildScheduleVariant).
        const consumed = await Promise.allSettled(uniquePendingIds.map((pendingId) =>
          api.post(`/trials/${id}/protocol-extractions/${pendingId}/consume`)));
        const built: ScheduleVariant[] = [];
        let failures = 0;
        for (const outcome of consumed) {
          if (outcome.status === "fulfilled") {
            built.push(buildScheduleVariant(outcome.value.data, []));
          } else {
            failures += 1;
          }
        }
        if (built.length) {
          setVariants(built);
          setActiveVariantId(built[0].optionId);
          setExpandedVariantIds(new Set([built[0].optionId]));
          setRows(built[0].rows);
          setOriginal(built[0].savedRows);
          setVerification(built[0].verification);
          setAssumptions(built[0].assumptions);
          setShowReviewNotes(false);
          setNotice(`${built.length} independent schedule${built.length === 1 ? "" : "s"} `
            + "prepared from your protocol upload are ready for review "
            + "(shown below as separate cards). No second AI extraction was used."
            + (failures ? ` ${failures} could not be loaded and were skipped.` : ""));
          setEditing(false);
          return;
        }
        setExtractErr("The prepared schedules could not be loaded. You can upload the PDF again or build it manually.");
      } else if (!loaded.length && uniquePendingIds.length === 1) {
        try {
          const prepared = await api.post(
            `/trials/${id}/protocol-extractions/${uniquePendingIds[0]}/consume`,
          );
          const visits: ExtractedVisit[] = prepared.data?.visits ?? [];
          const preparedVerification: ExtractionVerification | null =
            prepared.data?.verification ?? null;
          loaded = extractedVisitsToRows(visits, {
            anchor_study_day: prepared.data?.anchor_study_day ?? null,
            includes_day_zero: prepared.data?.includes_day_zero ?? null,
          });
          setVerification(preparedVerification);
          setAssumptions(prepared.data?.assumptions ?? []);
          setShowReviewNotes(false);
          if (loaded.length) {
            setNotice(preparedVerification?.status === "verified"
              ? "The schedule prepared from your first protocol upload is ready for review. No second AI extraction was used."
              : "The schedule from your first upload is ready, with items that need review. No second AI extraction was used.");
          }
        } catch (preparedError: any) {
          setExtractErr(
            preparedError?.response?.data?.detail
            || "The prepared schedule could not be loaded. You can upload the PDF again or build it manually.",
          );
        }
      }
      // A trial with visits saved from more than one substudy (see save())
      // is re-grouped the same way on reload, so reopening the screen shows
      // the same collapsible cards as right after extraction.
      const groups = new Map<string, Row[]>();
      for (const row of loaded) {
        const key = row.substudy_label?.trim() || "";
        const list = groups.get(key) ?? [];
        list.push(row);
        groups.set(key, list);
      }
      const namedGroups = [...groups.entries()].filter(([key]) => key);
      if (namedGroups.length > 1) {
        const built: ScheduleVariant[] = namedGroups.map(([label, groupRows]) => ({
          optionId: label,
          label,
          scheduleDefinitionId: null,
          rows: groupRows,
          savedRows: groupRows,
          verification: null,
          assumptions: [],
        }));
        setVariants(built);
        setActiveVariantId(built[0].optionId);
        setExpandedVariantIds(new Set([built[0].optionId]));
        setRows(built[0].rows);
        setOriginal(built[0].savedRows);
      } else {
        setVariants([]);
        setActiveVariantId(null);
        setExpandedVariantIds(new Set());
        setRows(loaded);
        setOriginal(loaded);
      }
      setEditing(loaded.length === 0);
    } catch (error: any) {
      setLoadErr(error?.response?.data?.detail || "Couldn't load the existing schedule.");
    } finally {
      setLoading(false);
    }
  }, [id, extractionId, extractionIds]);

  useEffect(() => {
    load();
  }, [load]);

  const updateRow = <K extends keyof Row>(index: number, key: K, value: Row[K]) => {
    setRows((current) => current.map((row, rowIndex) => {
      if (rowIndex !== index) return row;
      const next = { ...row, [key]: value };
      // Clearing a previously fixed date is a meaningful scheduling change.
      // Flag it by default, but let the reviewer acknowledge a legitimate
      // Unscheduled/ET row afterwards without inventing an offset.
      if (
        key === "day_offset"
        && typeof value === "string"
        && !value.trim()
        && !hasAbsoluteHourTiming(next)
      ) {
        next.extraction_warning = true;
        next.review_status = "pending";
      }
      return next;
    }));
  };

  const add = () => {
    setRows((current) => {
      const next = [...current, blankRow(`Visit ${current.length + 1}`)];
      setSelectedIndex(next.length - 1);
      return next;
    });
    setEditing(true);
  };

  const remove = (index: number) => {
    setRows((current) => current.filter((_, rowIndex) => rowIndex !== index));
    setSelectedIndex(null);
  };

  const move = (from: number, to: number) => {
    setRows((current) => {
      if (to < 0 || to >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    });
  };

  // ── Drag-to-reorder ──────────────────────────────────────────────────────
  // Rows have variable height (a long visit name wraps), so the drop target is
  // found by measuring each row rather than assuming a fixed row height.
  // PanResponder is used instead of a gesture library because this screen sits
  // in a plain ScrollView and the app has no GestureHandlerRootView.
  const dragFromRef = useRef<number | null>(null);
  const dragOverRef = useRef<number | null>(null);
  const rowLayoutsRef = useRef<{ y: number; height: number }[]>([]);
  const visibleOrderRef = useRef<number[]>([]);
  const [dragPosition, setDragPosition] = useState<number | null>(null);
  const [dropPosition, setDropPosition] = useState<number | null>(null);
  const [dragTranslate, setDragTranslate] = useState(0);

  // Held in a ref so the single PanResponder instance always calls the latest
  // closure over rows/filters without being rebuilt on every render.
  const endDragRef = useRef<() => void>(() => {});
  endDragRef.current = () => {
    const from = dragFromRef.current;
    const to = dragOverRef.current;
    dragFromRef.current = null;
    dragOverRef.current = null;
    setDragPosition(null);
    setDropPosition(null);
    setDragTranslate(0);
    if (from === null || to === null || from === to) return;
    // Positions are indices into the *visible* list; translate them back to the
    // underlying rows so reordering stays correct under an active filter.
    const order = visibleOrderRef.current;
    const fromIndex = order[from];
    const toIndex = order[to];
    if (fromIndex === undefined || toIndex === undefined) return;
    move(fromIndex, toIndex);
  };

  const dragResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => dragFromRef.current !== null,
      onMoveShouldSetPanResponder: () => dragFromRef.current !== null,
      onPanResponderGrant: () => {
        dragOverRef.current = dragFromRef.current;
        setDragPosition(dragFromRef.current);
        setDropPosition(dragFromRef.current);
        setDragTranslate(0);
      },
      onPanResponderMove: (_event, gesture) => {
        const from = dragFromRef.current;
        if (from === null) return;
        setDragTranslate(gesture.dy);
        const layouts = rowLayoutsRef.current;
        const source = layouts[from];
        if (!source) return;
        const centre = source.y + source.height / 2 + gesture.dy;
        let target = from;
        for (let position = 0; position < layouts.length; position += 1) {
          const layout = layouts[position];
          if (!layout) continue;
          if (centre >= layout.y && centre <= layout.y + layout.height) {
            target = position;
            break;
          }
          // Past the last row, or above the first: clamp to that end.
          if (centre < layout.y && position === 0) target = 0;
          if (centre > layout.y + layout.height && position === layouts.length - 1) {
            target = position;
          }
        }
        if (target !== dragOverRef.current) {
          dragOverRef.current = target;
          setDropPosition(target);
        }
      },
      onPanResponderRelease: () => endDragRef.current(),
      onPanResponderTerminate: () => endDragRef.current(),
    }),
  ).current;

  const acknowledge = (index: number) => {
    setRows((current) => current.map((row, rowIndex) => (
      rowIndex === index
        ? { ...row, extraction_warning: false, review_status: "ok" }
        : row
    )));
  };

  // Has the live editor (rows) diverged from what's actually saved for the
  // currently active variant (original)? Mirrors save()'s own per-row
  // comparison, so "nothing to lose" here means save() would issue zero
  // requests either.
  const isRowsDirty = () => {
    if (rows.length !== original.length) return true;
    return rows.some((row, index) => {
      const baseline = original[index];
      if (!baseline) return true;
      if ((row.id || null) !== (baseline.id || null)) return true;
      return !sameRow(baseline, row);
    });
  };

  const toggleVariant = (optionId: string) => {
    setExpandedVariantIds((current) => {
      const next = new Set(current);
      if (next.has(optionId)) next.delete(optionId); else next.add(optionId);
      return next;
    });
  };

  const switchActiveVariant = (optionId: string) => {
    if (optionId === activeVariantId) return;
    const target = variants.find((variant) => variant.optionId === optionId);
    if (!target) return;
    const commit = () => {
      // Carry the outgoing variant's live edits along, so switching back to
      // it later (without saving first) doesn't lose them.
      setVariants((current) => current.map((variant) => (
        variant.optionId === activeVariantId ? { ...variant, rows } : variant
      )));
      setRows(target.rows);
      setOriginal(target.savedRows);
      setVerification(target.verification);
      setAssumptions(target.assumptions);
      setActiveVariantId(optionId);
      setSelectedIndex(null);
      setShowReviewNotes(false);
      setEditing(false);
      setErr("");
      setExpandedVariantIds((current) => new Set(current).add(optionId));
    };
    if (isRowsDirty()) {
      Alert.alert(
        "Discard unsaved changes?",
        "Switching to another schedule will discard your unsaved edits to this one. Save first if you want to keep them.",
        [
          { text: "Cancel", style: "cancel" },
          { text: "Discard", style: "destructive", onPress: commit },
        ],
      );
      return;
    }
    commit();
  };

  const runAutofill = async (asset: DocumentPicker.DocumentPickerAsset) => {
    const form = new FormData();
    if (Platform.OS === "web") {
      const file = (asset as any).file as File | undefined;
      if (!file) {
        setExtractErr("Couldn't read the selected file.");
        return;
      }
      form.append("file", file, asset.name || "protocol.pdf");
    } else {
      form.append("file", {
        uri: asset.uri,
        name: asset.name || "protocol.pdf",
        type: asset.mimeType || "application/pdf",
      } as any);
    }
    setExtracting(true);
    try {
      const response = await api.post(`/trials/${id}/extract-schedule`, form, {
        // Do not set Content-Type manually: React Native/Axios must add its
        // multipart boundary. Protocol analysis can take longer than a normal
        // API request — now potentially several substudies' worth of full
        // extraction pipelines, bounded to a few running at once server-side.
        timeout: 1800000,
      });
      const rawVariants: any[] = Array.isArray(response.data?.schedule_variants)
        ? response.data.schedule_variants : [];
      if (rawVariants.length > 1) {
        // This protocol prints more than one independent Schedule of
        // Assessments (e.g. separate substudies) — every one of them was
        // already extracted server-side. Show them all as their own cards
        // instead of a single merged table.
        const built: ScheduleVariant[] = rawVariants.map(
          (variant) => buildScheduleVariant(variant, variants));
        setVariants(built);
        setShowReviewNotes(false);
        if (built.length) {
          setActiveVariantId(built[0].optionId);
          setRows(built[0].rows);
          setOriginal(built[0].savedRows);
          setVerification(built[0].verification);
          setAssumptions(built[0].assumptions);
          setExpandedVariantIds(new Set([built[0].optionId]));
        }
        setSelectedIndex(null);
        setEditing(false);
        setNotice(`Extracted ${built.length} independent schedules from this protocol `
          + "(shown below as separate cards). Review and save each one you want to keep.");
        return;
      }
      setVariants([]);
      setActiveVariantId(null);
      setExpandedVariantIds(new Set());
      const visits: ExtractedVisit[] = response.data?.visits ?? [];
      const agentVerification: ExtractionVerification | null = response.data?.verification ?? null;
      setVerification(agentVerification);
      setAssumptions(response.data?.assumptions ?? []);
      setShowReviewNotes(false);
      if (!visits.length) {
        setExtractErr("No visit schedule was found in that PDF. You can still build it manually.");
        return;
      }
      setRows(extractedVisitsToRows(visits, {
        anchor_study_day: response.data?.anchor_study_day ?? null,
        includes_day_zero: response.data?.includes_day_zero ?? null,
      }));
      setSelectedIndex(null);
      setEditing(false);
      setNotice(agentVerification?.status === "verified"
        ? `Agent extracted and verified the draft${agentVerification.refinement_count
          ? ` after ${agentVerification.refinement_count} correction pass${agentVerification.refinement_count === 1 ? "" : "es"}`
          : ""}. Human review is still required before saving.`
        : "Agent extracted the draft but found unresolved items. Review every flagged row before saving.");
    } catch (error: any) {
      const status = error?.response?.status;
      const timedOut = error?.code === "ECONNABORTED" || /timeout/i.test(String(error?.message || ""));
      setExtractErr(timedOut
        ? "Protocol analysis is taking longer than expected. Try a smaller PDF containing the Schedule of Assessments pages."
        : error?.response?.data?.detail
        || (status === 503
          ? "AI extraction isn't configured on the server yet."
          : "Couldn't extract the schedule. Try again, or build it manually."));
    } finally {
      setExtracting(false);
    }
  };

  const autofill = async () => {
    setExtractErr("");
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: "application/pdf",
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled || !result.assets?.length) return;
      asset = result.assets[0];
    } catch {
      setExtractErr("Couldn't open the file picker.");
      return;
    }
    await runAutofill(asset);
  };

  const validate = () => {
    if (!rows.length) return "Add at least one visit before saving.";
    const invalidIndex = rows.findIndex((row) => (
      !row.name.trim()
      || (row.day_offset.trim() !== "" && parseOptionalDayOffset(row.day_offset) === null)
      || (row.window_days.trim() !== "" && (
        !Number.isInteger(Number(row.window_days)) || Number(row.window_days) < 0
      ))
    ));
    if (invalidIndex >= 0) {
      return `Check the name, whole-day baseline offset, and window for Visit ${invalidIndex + 1}.`;
    }
    const invalidAdvancedIndex = rows.findIndex((row) => {
      const finiteOrNull = (value: number | null) => value === null || Number.isFinite(value);
      return !finiteOrNull(row.day_end)
        || !finiteOrNull(row.hour_offset)
        || !finiteOrNull(row.hour_end)
        || !finiteOrNull(row.window_before)
        || !finiteOrNull(row.window_after)
        || !finiteOrNull(row.relative_offset_days)
        || (row.window_before !== null && row.window_before < 0)
        || (row.window_after !== null && row.window_after < 0);
    });
    if (invalidAdvancedIndex >= 0) {
      return `The advanced timing values for Visit ${invalidAdvancedIndex + 1} are invalid.`;
    }
    return "";
  };

  const save = async () => {
    setConfirmSave(false);
    setSaving(true);
    setErr("");
    try {
      // `original` is already scoped to the active variant's own previously
      // saved rows (see load()/runAutofill()/switchActiveVariant()), so this
      // delete pass only ever removes rows that belonged to THIS substudy —
      // other substudies already saved on the same trial are untouched.
      const keptIds = new Set(rows.filter((row) => row.id).map((row) => row.id));
      for (const previous of original) {
        if (previous.id && !keptIds.has(previous.id)) {
          await api.delete(`/visits/${previous.id}`);
        }
      }
      const nextRows = [...rows];
      for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index];
        const visitNumber = index + 1;
        const dayOffset = parseOptionalDayOffset(row.day_offset);
        const payload = {
          name: row.name.trim(),
          visit_number: visitNumber,
          day_offset: dayOffset,
          source_day_label: row.source_day_label.trim() || null,
          day_end: row.day_end,
          calendar_offset_value: row.calendar_offset_value,
          calendar_offset_unit: row.calendar_offset_unit,
          hour_offset: row.hour_offset,
          hour_end: row.hour_end,
          hour_offset_basis: row.hour_offset_basis,
          window_before: row.window_before,
          window_after: row.window_after,
          relative_to: row.relative_to,
          relative_offset_days: row.relative_offset_days,
          arm_label: row.arm_label,
          period: row.period,
          substudy_label: row.substudy_label || null,
          visit_type: row.visit_type,
          anchor_study_day: row.anchor_study_day,
          includes_day_zero: row.includes_day_zero,
          window_days: row.window_days.trim() === "" ? null : Number(row.window_days),
          activities: row.activities.split(",").map((value) => value.trim()).filter(Boolean),
          procedures: row.procedures,
          operational_constraints: normalizeProtocolConstraints(row.operational_constraints),
          clinical_tasks: row.clinical_tasks.split(",").map((value) => value.trim()).filter(Boolean),
          admin_tasks: row.admin_tasks.split(",").map((value) => value.trim()).filter(Boolean),
          comments: row.comments.trim(),
          extraction_warning: row.extraction_warning,
          review_status: row.review_status,
          extracted_from_protocol: row.extracted_from_protocol,
          field_evidence: row.field_evidence,
        };
        if (row.id) {
          const previous = original.find((item) => item.id === row.id);
          const numberChanged = !previous || previous.visit_number !== visitNumber;
          if (!previous || !sameRow(previous, row) || numberChanged) {
            await api.put(`/visits/${row.id}`, payload);
          }
          nextRows[index] = { ...row, visit_number: visitNumber };
        } else {
          const created = await api.post("/visits", { trial_id: id, ...payload });
          // A freshly-extracted row has no id yet; capture the one Mongo just
          // assigned so a second Save (possible now that saving a variant no
          // longer force-navigates away) issues a PUT, not a duplicate POST.
          const newId = created?.data?.id;
          nextRows[index] = newId
            ? { ...row, id: newId, visit_number: visitNumber }
            : { ...row, visit_number: visitNumber };
        }
      }
      setRows(nextRows);
      setOriginal(nextRows);
      if (activeVariantId) {
        setVariants((current) => current.map((variant) => (
          variant.optionId === activeVariantId
            ? { ...variant, rows: nextRows, savedRows: nextRows }
            : variant
        )));
        setSavedVariantIds((current) => new Set(current).add(activeVariantId));
      }
      setSaved(true);
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "The schedule couldn't be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    const message = validate();
    if (message) {
      setErr(message);
      return;
    }
    setErr("");
    if (pendingCount > 0) {
      setConfirmSave(true);
      return;
    }
    save();
  };

  const download = async () => {
    const hasMultipleSchedules = variants.length > 1;
    // Each group's rows reflect the live editor for the currently active
    // variant, and whatever was last committed (extracted/edited/saved) for
    // every other one \u2014 "what you'd see if you expanded that card."
    const exportGroups = hasMultipleSchedules
      ? variants.map((variant) => ({
          label: variant.label,
          rows: variant.optionId === activeVariantId ? rows : variant.rows,
        }))
      : [{ label: "", rows }];
    if (!exportGroups.some((group) => group.rows.length)) {
      setErr("Add at least one visit before downloading.");
      return;
    }
    const header = [
      ...(hasMultipleSchedules ? ["Substudy"] : []),
      "Visit number", "Visit name", "Protocol timing label", "Offset from baseline (days)",
      "Offset end (days)", "Hour offset", "Hour end", "Hour offset basis",
      "Window days", "Window before", "Window after", "Relative to", "Relative offset days",
      "Arm", "Period", "Visit type", "Anchor study day", "Includes Day 0", "Activities",
      "Structured procedures", "Operational constraints", "Clinical tasks",
      "Administrative tasks", "Comments", "Review status",
    ];
    const lines = exportGroups.flatMap((group) => group.rows.map((row, index) => [
      ...(hasMultipleSchedules ? [group.label] : []),
      index + 1,
      row.name,
      row.source_day_label,
      row.day_offset,
      row.day_end ?? "",
      row.hour_offset ?? "",
      row.hour_end ?? "",
      row.hour_offset_basis ?? "",
      row.window_days,
      row.window_before ?? "",
      row.window_after ?? "",
      row.relative_to ?? "",
      row.relative_offset_days ?? "",
      row.arm_label ?? "",
      row.period ?? "",
      row.visit_type ?? "",
      row.anchor_study_day ?? "",
      row.includes_day_zero ?? "",
      row.activities,
      row.procedures.map(formatProtocolProcedure).join(" | "),
      normalizeProtocolConstraints(row.operational_constraints).join(" | "),
      row.clinical_tasks,
      row.admin_tasks,
      row.comments,
      row.extraction_warning || row.review_status === "pending" ? "Needs review" : "OK",
    ].map(csvCell).join(",")));
    const csv = `\uFEFF${header.map(csvCell).join(",")}\r\n${lines.join("\r\n")}`;
    const filename = `visit-schedule-${id || "trial"}.csv`;
    try {
      if (Platform.OS === "web") {
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
      } else {
        const uri = `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
        await Share.share({
          title: filename,
          message: csv,
          url: uri,
        });
      }
      setNotice(`Schedule exported as ${filename}`);
      setErr("");
    } catch (error: any) {
      setErr(error?.message || "Couldn't export the schedule.");
    }
  };

  const goToTrial = () => {
    setSaved(false);
    router.replace({ pathname: "/(app)/clinical/trial-summary", params: { id } });
  };

  const pendingCount = rows.filter(rowNeedsReview).length;
  // One line naming both kinds of note, so neither is hidden behind a label
  // that only mentions the other.
  const issueCount = verification?.issues?.length ?? 0;
  const reviewNotesLabel = [
    assumptions.length
      ? `${assumptions.length} extraction assumption${assumptions.length === 1 ? "" : "s"}`
      : "",
    issueCount
      ? `why ${issueCount} field${issueCount === 1 ? "" : "s"} ${issueCount === 1 ? "was" : "were"} flagged`
      : "",
  ].filter(Boolean).join(" and ");
  const visibleRows = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => (
      filter === "all"
      || (filter === "pending"
        ? rowNeedsReview(row)
        : !rowNeedsReview(row))
    ));
  // Keep the drag gesture's view of the list in step with what is rendered:
  // visible position -> underlying row index, and drop stale row measurements.
  visibleOrderRef.current = visibleRows.map(({ index }) => index);
  rowLayoutsRef.current.length = visibleRows.length;
  const selectedRow = selectedIndex === null ? null : rows[selectedIndex];
  const isProtocolExtract = rows.some((row) => row.extracted_from_protocol);
  const activeVariant = variants.find((variant) => variant.optionId === activeVariantId) ?? null;

  // The summary/filters/editable table/add-visit block — identical whether
  // this trial has one schedule (rendered directly) or several (rendered
  // inside whichever variant card is currently active). `rows` always
  // describes the active variant, so nothing here needs to know which case
  // it's in.
  const renderEditorBody = () => (
    <>
      <View style={styles.summary}>
        <View style={styles.summaryLeft}>
          <View style={styles.sourcePill}>
            <Sparkles size={12} color={colors.primary} />
            <Small color={colors.primary} weight="700">
              {isProtocolExtract ? "AI Extracted" : "Visit Template"}
            </Small>
          </View>
          <Small>{rows.length} visits</Small>
          {verification?.status === "verified" && (
            <View style={styles.verifiedPill}>
              <CheckCircle2 size={13} color={colors.success} />
              <Small color={colors.success} weight="700">Agent verified</Small>
            </View>
          )}
        </View>
        {pendingCount > 0 && (
          <View style={styles.pendingPill}>
            <AlertTriangle size={13} color={colors.warning} />
            <Small color={colors.warning} weight="700">{pendingCount} need review</Small>
          </View>
        )}
      </View>

      {(!!assumptions.length || !!verification?.issues?.length) && (
        <View style={styles.reviewNotesBlock}>
          <Pressable
            testID="review-notes-toggle"
            onPress={() => setShowReviewNotes((value) => !value)}
            style={styles.reviewNotesToggle}
          >
            {showReviewNotes
              ? <ChevronUp size={14} color={assumptions.length ? colors.warning : colors.mutedFg} />
              : <ChevronDown size={14} color={assumptions.length ? colors.warning : colors.mutedFg} />}
            <Small color={assumptions.length ? colors.warning : colors.mutedFg} weight="700">
              {showReviewNotes ? "Hide" : "Show"} {reviewNotesLabel}
            </Small>
          </Pressable>
          {showReviewNotes && (
            <View style={styles.reviewNotesList}>
              {/* What the extractor had to assume to produce these rows — an
                  open-ended cycle tail it capped, an anchor it could not
                  resolve. Schedule-level, and written for the reviewer to
                  confirm BEFORE saving, so it leads. */}
              {!!assumptions.length && (
                <>
                  <Small color={colors.warning} weight="700" style={styles.reviewNoteHeading}>
                    ASSUMPTIONS MADE DURING EXTRACTION
                  </Small>
                  {assumptions.map((assumption, assumptionIndex) => (
                    <Small
                      key={`assumption-${assumptionIndex}`}
                      testID={`extraction-assumption-${assumptionIndex}`}
                      color={colors.warning}
                      style={styles.reviewNoteItem}
                    >
                      {"• "}{assumption}
                    </Small>
                  ))}
                </>
              )}
              {!!verification?.issues?.length && (
                <>
                  {!!assumptions.length && (
                    <Small color={colors.mutedFg} weight="700" style={styles.reviewNoteHeading}>
                      FIELDS FLAGGED FOR REVIEW
                    </Small>
                  )}
                  {verification.issues.map((issue, issueIndex) => (
                    <Small key={issueIndex} color={colors.mutedFg} style={styles.reviewNoteItem}>
                      {"• "}{issue}
                    </Small>
                  ))}
                </>
              )}
            </View>
          )}
        </View>
      )}

      <View style={styles.filters}>
        {(["all", "pending", "ok"] as const).map((value) => {
          const count = value === "all"
            ? rows.length
            : value === "pending" ? pendingCount : rows.length - pendingCount;
          const active = filter === value;
          return (
            <Pressable
              key={value}
              testID={`schedule-filter-${value}`}
              onPress={() => setFilter(value)}
              style={[styles.filterChip, active && styles.filterChipActive]}
            >
              {active && <Check size={12} color={colors.primaryFg} />}
              <Small color={active ? colors.primaryFg : colors.mutedFg} weight="700">
                {value === "all" ? "All" : value === "pending" ? "Pending" : "OK"}
              </Small>
              <View style={[styles.filterCount, active && styles.filterCountActive]}>
                <Small color={active ? colors.primaryFg : colors.mutedFg} weight="700">{count}</Small>
              </View>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.table}>
        <View style={[styles.tableGrid, styles.tableHeader, editing && styles.tableGridEditing]}>
          {editing && <View style={styles.orderCell} />}
          <Small weight="700" style={styles.numberCell}>#</Small>
          <Small weight="700" style={styles.nameCell}>Visit name</Small>
          <Small weight="700" style={styles.dayCell}>Day</Small>
          <Small weight="700" style={styles.windowCell}>Window</Small>
          {editing && <View style={styles.deleteCell} />}
        </View>

        {visibleRows.map(({ row, index }, position) => {
          const warning = rowNeedsReview(row);
          const dragging = dragPosition === position;
          const isDropTarget = dropPosition === position && dragPosition !== position;
          return (
            <Pressable
              key={row.id ?? `new-${index}`}
              testID={`visit-row-${index}`}
              onPress={() => setSelectedIndex(index)}
              onLayout={(event) => {
                const { y, height } = event.nativeEvent.layout;
                rowLayoutsRef.current[position] = { y, height };
              }}
              style={[
                styles.tableGrid,
                styles.tableRow,
                editing && styles.tableGridEditing,
                warning && styles.warningRow,
                isDropTarget && styles.dropTargetRow,
                dragging && styles.draggingRow,
                dragging && { transform: [{ translateY: dragTranslate }] },
              ]}
            >
              {editing && (
                <View
                  testID={`drag-visit-${index}`}
                  style={styles.orderCell}
                  onTouchStart={() => { dragFromRef.current = position; }}
                  {...dragResponder.panHandlers}
                >
                  <GripVertical
                    size={16}
                    color={dragging ? colors.primary : colors.mutedFg}
                  />
                </View>
              )}
              <View style={styles.numberCell}>
                {warning
                  ? <AlertTriangle size={15} color={colors.warning} />
                  : <Small weight="700">{index + 1}</Small>}
              </View>
              <View style={styles.nameCell}>
                <Body weight="700" numberOfLines={1}>{row.name || "Untitled visit"}</Body>
                {!!row.comments && <MessageSquareText size={11} color={colors.mutedFg} />}
              </View>
              <Small numberOfLines={1} style={styles.dayCell}>{dayForRow(row)}</Small>
              <Small style={styles.windowCell}>
                {formatVisitWindow({
                  window_days: row.window_days.trim() === "" ? null : Number(row.window_days),
                  window_before: row.window_before,
                  window_after: row.window_after,
                }, true)}
              </Small>
              {editing && (
                <View style={styles.deleteCell}>
                  <Pressable
                    testID={`remove-visit-${index}`}
                    onPress={(event) => {
                      event.stopPropagation();
                      remove(index);
                    }}
                    style={styles.deleteButton}
                  >
                    <Trash2 size={15} color={colors.destructive} />
                  </Pressable>
                </View>
              )}
            </Pressable>
          );
        })}

        {visibleRows.length === 0 && (
          <View style={styles.emptyRows}>
            <Small color={colors.mutedFg}>
              {rows.length ? "No visits in this filter." : "No visits yet. Add one or extract from a protocol."}
            </Small>
          </View>
        )}
      </View>

      <Pressable testID="add-visit-row" onPress={add} style={styles.addButton}>
        <Plus size={17} color={colors.primary} />
        <Small color={colors.primary} weight="700">Add Visit</Small>
      </Pressable>

      {!!notice && (
        <Pressable onPress={() => setNotice("")} style={styles.notice}>
          <CheckCircle2 size={15} color={colors.success} />
          <Small color={colors.success} weight="700" style={styles.flex}>{notice}</Small>
          <X size={13} color={colors.success} />
        </Pressable>
      )}
      {!!err && <Small color={colors.destructive} style={styles.inlineMessage}>{err}</Small>}
    </>
  );

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Build schedule" title="Visit Schedule" />
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (loadErr) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Build schedule" title="Visit Schedule" />
        <View style={styles.center}>
          <Small color={colors.destructive} style={styles.centerText}>{loadErr}</Small>
          <Button testID="retry-load" variant="secondary" onPress={load}>Try again</Button>
        </View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="Build schedule"
        title="Visit Schedule"
        right={(
          <View style={styles.headerActions}>
            <Pressable
              accessibilityLabel="Download visit schedule"
              testID="download-visit-schedule"
              onPress={download}
              style={styles.headerIcon}
            >
              <Download size={17} color={colors.primaryFg} />
            </Pressable>
            <Pressable
              accessibilityLabel={editing ? "Finish editing schedule" : "Edit schedule"}
              testID="toggle-schedule-edit"
              onPress={() => setEditing((value) => !value)}
              style={[styles.headerIcon, editing && styles.headerIconActive]}
            >
              {editing
                ? <Check size={17} color={colors.primaryFg} />
                : <Edit3 size={17} color={colors.primaryFg} />}
            </Pressable>
          </View>
        )}
      />

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.flex}
      >
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
          // The list must not scroll out from under a row being dragged.
          scrollEnabled={dragPosition === null}
        >
          {!!extractErr && (
            <Small color={colors.destructive} style={styles.inlineMessage}>{extractErr}</Small>
          )}

          {variants.length > 1 ? (
            <View style={styles.variantList}>
              {variants.map((variant) => {
                const isActive = variant.optionId === activeVariantId;
                const isOpen = expandedVariantIds.has(variant.optionId);
                const displayRows = isActive ? rows : variant.rows;
                const isSaved = savedVariantIds.has(variant.optionId);
                return (
                  <View key={variant.optionId} testID={`variant-card-${variant.optionId}`} style={styles.variantCard}>
                    <Pressable
                      testID={`variant-header-${variant.optionId}`}
                      onPress={() => toggleVariant(variant.optionId)}
                      style={styles.variantHeader}
                    >
                      <View style={styles.variantHeaderLeft}>
                        <View style={styles.variantOrb}><FileStack size={16} color={colors.primary} /></View>
                        <View style={styles.flex}>
                          <Body weight="700" numberOfLines={1}>{variant.label}</Body>
                          <Small color={colors.mutedFg}>
                            {displayRows.length} visit{displayRows.length === 1 ? "" : "s"}
                            {isActive ? " · editing" : ""}
                          </Small>
                        </View>
                      </View>
                      <View style={styles.variantHeaderRight}>
                        {isSaved && (
                          <View style={styles.verifiedPill}>
                            <CheckCircle2 size={12} color={colors.success} />
                            <Small color={colors.success} weight="700">Saved</Small>
                          </View>
                        )}
                        {isOpen
                          ? <ChevronUp size={16} color={colors.mutedFg} />
                          : <ChevronDown size={16} color={colors.mutedFg} />}
                      </View>
                    </Pressable>

                    {isOpen && isActive && (
                      <View style={styles.variantBody}>
                        {renderEditorBody()}
                      </View>
                    )}

                    {isOpen && !isActive && (
                      <View style={styles.variantBody}>
                        <View style={styles.table}>
                          <View style={[styles.tableGrid, styles.tableHeader]}>
                            <Small weight="700" style={styles.numberCell}>#</Small>
                            <Small weight="700" style={styles.nameCell}>Visit name</Small>
                            <Small weight="700" style={styles.dayCell}>Day</Small>
                            <Small weight="700" style={styles.windowCell}>Window</Small>
                          </View>
                          {displayRows.map((row, rowIndex) => (
                            <View key={row.id ?? `preview-${rowIndex}`} style={[styles.tableGrid, styles.tableRow]}>
                              <View style={styles.numberCell}><Small weight="700">{rowIndex + 1}</Small></View>
                              <View style={styles.nameCell}>
                                <Body weight="700" numberOfLines={1}>{row.name || "Untitled visit"}</Body>
                              </View>
                              <Small numberOfLines={1} style={styles.dayCell}>{dayForRow(row)}</Small>
                              <Small style={styles.windowCell}>
                                {formatVisitWindow({
                                  window_days: row.window_days.trim() === "" ? null : Number(row.window_days),
                                  window_before: row.window_before,
                                  window_after: row.window_after,
                                }, true)}
                              </Small>
                            </View>
                          ))}
                          {displayRows.length === 0 && (
                            <View style={styles.emptyRows}>
                              <Small color={colors.mutedFg}>No visits extracted for this schedule.</Small>
                            </View>
                          )}
                        </View>
                        <Pressable
                          testID={`edit-variant-${variant.optionId}`}
                          onPress={() => switchActiveVariant(variant.optionId)}
                          style={styles.variantEditButton}
                        >
                          <ArrowRight size={16} color={colors.primary} />
                          <Small color={colors.primary} weight="700">Edit this schedule</Small>
                        </Pressable>
                      </View>
                    )}
                  </View>
                );
              })}
            </View>
          ) : renderEditorBody()}
        </ScrollView>

        <View style={styles.saveBar}>
          <Button testID="save-schedule" onPress={requestSave} loading={saving}>
            <View style={styles.buttonContent}>
              <Check size={14} color={colors.primaryFg} />
              <Small color={colors.primaryFg} weight="700">Save Template</Small>
            </View>
          </Button>
        </View>
      </KeyboardAvoidingView>

      <VisitDetailSheet
        row={selectedRow}
        index={selectedIndex}
        // Tapping a visit opens the editor itself, so it is always editable.
        // The list's own edit toggle still gates the drag handles and the
        // inline delete column; nothing here is persisted until Save Template.
        editing
        onClose={() => setSelectedIndex(null)}
        onChange={updateRow}
        onAcknowledge={acknowledge}
        onDelete={remove}
      />

      <Modal
        transparent
        visible={confirmSave}
        animationType="fade"
        onRequestClose={() => setConfirmSave(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.confirmCard}>
            <View style={styles.warningOrb}><AlertTriangle size={24} color={colors.warning} /></View>
            <Body weight="700" style={styles.confirmTitle}>Save with pending review?</Body>
            <Small style={styles.confirmCopy}>
              {pendingCount} {pendingCount === 1 ? "visit still needs" : "visits still need"} manual review.
              You can save the draft now, but it should be acknowledged before sharing.
            </Small>
            <View style={styles.confirmActions}>
              <Pressable onPress={() => setConfirmSave(false)} style={styles.secondaryAction}>
                <Small color={colors.primary} weight="700">Review visits</Small>
              </Pressable>
              <Pressable testID="confirm-save-schedule" onPress={save} style={styles.primaryAction}>
                <Small color={colors.primaryFg} weight="700">Save draft</Small>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal
        transparent
        visible={saved}
        animationType="fade"
        onRequestClose={variants.length > 1 ? () => setSaved(false) : goToTrial}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.successCard}>
            <View style={styles.successOrb}><CheckCircle2 size={31} color={colors.success} /></View>
            <Body weight="700" style={styles.confirmTitle}>
              {variants.length > 1
                ? `"${activeVariant?.label ?? "This"}" schedule saved`
                : "Visit template saved"}
            </Body>
            <Small style={styles.confirmCopy}>
              {rows.length} {rows.length === 1 ? "visit is" : "visits are"} now attached to this trial.{" "}
              {variants.length > 1
                ? "The other schedules below are unaffected — review or save them next."
                : "The schedule can be reviewed or shared from the trial workspace."}
            </Small>
            {variants.length > 1 ? (
              <View style={styles.confirmActions}>
                <Pressable testID="keep-reviewing-schedules" onPress={() => setSaved(false)} style={styles.secondaryAction}>
                  <Small color={colors.primary} weight="700">Keep reviewing</Small>
                </Pressable>
                <Pressable testID="return-to-trial" onPress={goToTrial} style={styles.primaryAction}>
                  <Small color={colors.primaryFg} weight="700">Done — go to trial</Small>
                </Pressable>
              </View>
            ) : (
              <Pressable testID="return-to-trial" onPress={goToTrial} style={styles.successAction}>
                <Small color={colors.primaryFg} weight="700">Return to trial</Small>
              </Pressable>
            )}
          </View>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

function VisitDetailSheet({
  row,
  index,
  editing,
  onClose,
  onChange,
  onAcknowledge,
  onDelete,
}: {
  row: Row | null;
  index: number | null;
  editing: boolean;
  onClose: () => void;
  onChange: <K extends keyof Row>(index: number, key: K, value: Row[K]) => void;
  onAcknowledge: (index: number) => void;
  onDelete: (index: number) => void;
}) {
  if (!row || index === null) return null;
  const missingTiming = parseOptionalDayOffset(row.day_offset) === null
    && !hasAbsoluteHourTiming(row);
  const warning = rowNeedsReview(row);
  return (
    <Modal transparent visible animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.sheetKeyboard}
        >
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <View style={styles.sheetHeader}>
              <View style={[styles.visitBadge, warning && styles.visitBadgeWarning]}>
                {warning
                  ? <AlertTriangle size={18} color={colors.warning} />
                  : <Body color={colors.primary} weight="700">{index + 1}</Body>}
              </View>
              <View style={styles.flex}>
                {editing ? (
                  <TextInput
                    testID={`vname-${index}`}
                    value={row.name}
                    onChangeText={(value) => onChange(index, "name", value)}
                    placeholder="Visit name"
                    placeholderTextColor={colors.mutedFg}
                    style={[styles.input, styles.nameInput]}
                  />
                ) : (
                  <>
                    <Body weight="700">Visit {index + 1} · {row.name}</Body>
                    <View style={styles.metaLine}>
                      <CalendarDays size={12} color={colors.mutedFg} />
                      <Small>{dayForRow(row)}</Small>
                      <Small>·</Small>
                      <Small>
                        Visit tolerance {formatVisitWindow({
                          window_days: row.window_days.trim() === "" ? null : Number(row.window_days),
                          window_before: row.window_before,
                          window_after: row.window_after,
                        })}
                      </Small>
                    </View>
                  </>
                )}
              </View>
              <Pressable accessibilityLabel="Close visit details" onPress={onClose} style={styles.closeButton}>
                <X size={17} color={colors.mutedFg} />
              </Pressable>
            </View>

            <ScrollView
              contentContainerStyle={styles.sheetContent}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              {warning && (
                <View style={styles.warningBanner}>
                  <AlertTriangle size={15} color={colors.warning} />
                  <Small color={colors.warning} style={styles.flex}>
                    This protocol-extracted visit needs manual review.
                  </Small>
                </View>
              )}


              {editing && (
                <>
                  <View style={styles.twoColumns}>
                  <Field label="Day">
                    <TextInput
                      testID={`vday-${index}`}
                      value={row.day_offset}
                      onChangeText={(value) => onChange(index, "day_offset", value)}
                      placeholder="Leave blank if undated"
                      placeholderTextColor={colors.mutedFg}
                      keyboardType="numbers-and-punctuation"
                      style={styles.input}
                    />
                  </Field>
                  <Field label="Window">
                    <TextInput
                      testID={`vwin-${index}`}
                      value={row.window_days}
                      onChangeText={(value) => onChange(index, "window_days", value)}
                      keyboardType="number-pad"
                      style={styles.input}
                    />
                  </Field>
                  </View>
                  <Small color={colors.mutedFg} style={styles.offsetHelp}>
                    0 is the baseline date. Leave visit tolerance blank when the protocol does not state one.
                  </Small>
                </>
              )}


              <TaskField
                key={`clinical-${index}`}
                icon={<Stethoscope size={16} color={colors.primary} />}
                title="Clinical Tasks"
                value={row.clinical_tasks}
                fallback={row.activities}
                editing={editing}
                placeholder="Vitals, ECG, blood draw"
                onChange={(value) => onChange(index, "clinical_tasks", value)}
              />
              <TaskField
                key={`admin-${index}`}
                icon={<ClipboardList size={16} color={colors.success} />}
                title="Admin Tasks"
                value={row.admin_tasks}
                editing={editing}
                placeholder="eCRF, consent, drug accountability"
                onChange={(value) => onChange(index, "admin_tasks", value)}
              />
              <TaskField
                key={`other-${index}`}
                icon={<CheckCircle2 size={16} color={colors.info} />}
                title="Other Activities"
                value={row.activities}
                editing={editing}
                placeholder="Additional protocol activities"
                onChange={(value) => onChange(index, "activities", value)}
              />

              <View>
                <View style={styles.sectionTitle}>
                  <MessageSquareText size={16} color={colors.mutedFg} />
                  <Body weight="700">Comments</Body>
                </View>
                {editing ? (
                  <TextInput
                    value={row.comments}
                    onChangeText={(value) => onChange(index, "comments", value)}
                    multiline
                    textAlignVertical="top"
                    placeholder="Add visit-specific comments..."
                    placeholderTextColor={colors.mutedFg}
                    style={[styles.input, styles.commentInput]}
                  />
                ) : (
                  <Small style={styles.readComment}>
                    {row.comments.trim() || "No comments added."}
                  </Small>
                )}
              </View>

              <View style={styles.detailDivider} />
              <View style={styles.timingSummary}>
                <View style={styles.timingSummaryBlock}>
                  <Small color={colors.mutedFg} weight="700">PROTOCOL TIMING (SOURCE)</Small>
                  <Body weight="700" style={styles.timingSummaryValue}>
                    {row.source_day_label.trim() || "-"}
                  </Body>
                  <Small color={colors.mutedFg}>
                    Computed placement: {canonicalTimingForRow(row)}
                  </Small>
                </View>
                <View style={styles.timingSummaryBlock}>
                  <Small color={colors.mutedFg} weight="700">VISIT TOLERANCE</Small>
                  <Body weight="700" style={styles.timingSummaryValue}>
                    {formatVisitWindow({
                      window_days: row.window_days.trim() === "" ? null : Number(row.window_days),
                      window_before: row.window_before,
                      window_after: row.window_after,
                    })}
                  </Body>
                  <Small color={colors.mutedFg}>
                    How early or late the whole visit may occur. Procedure tolerances are separate.
                  </Small>
                </View>
              </View>

              {editing && (
                  <Field label="Protocol timing (exact source text)">
                    <TextInput
                      testID={`vsource-day-label-${index}`}
                      value={row.source_day_label}
                      onChangeText={(value) => onChange(index, "source_day_label", value)}
                      placeholder="For example: Day 0, Week 4, Unscheduled"
                      placeholderTextColor={colors.mutedFg}
                      style={styles.input}
                    />
                  </Field>
              )}

              <ProcedureField
                procedures={row.procedures}
                editing={editing}
                onChange={(value) => onChange(index, "procedures", value)}
              />
              <ConstraintField
                value={row.operational_constraints}
                editing={editing}
                onChange={(value) => onChange(index, "operational_constraints", value)}
              />

              {warning && (
                <Pressable
                  testID={`ack-warning-${index}`}
                  onPress={() => onAcknowledge(index)}
                  style={styles.acknowledgeButton}
                >
                  <Check size={15} color={colors.success} />
                  <Small color={colors.success} weight="700">Acknowledge warning</Small>
                </Pressable>
              )}
              {missingTiming && (
                <View style={styles.undatedNote}>
                  <AlertTriangle size={15} color={colors.warning} />
                  <Small color={colors.warning} style={styles.flex}>
                    {warning
                      ? "This visit has no computable baseline offset. Acknowledge it only when the protocol intentionally leaves the visit undated."
                      : "This protocol visit intentionally has no fixed baseline date."}
                  </Small>
                </View>
              )}
            </ScrollView>

            <View style={styles.sheetFooter}>
              {editing && (
                <Pressable
                  testID={`sheet-delete-visit-${index}`}
                  onPress={() => onDelete(index)}
                  style={styles.sheetDelete}
                >
                  <Trash2 size={16} color={colors.destructive} />
                  <Small color={colors.destructive} weight="700">Delete</Small>
                </Pressable>
              )}
              <Pressable onPress={onClose} style={styles.sheetDone}>
                <Small color={colors.primaryFg} weight="700">{editing ? "Done" : "Close"}</Small>
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Small color={colors.foreground} weight="700" style={styles.fieldLabel}>{label}</Small>
      {children}
    </View>
  );
}

function TaskField({
  icon,
  title,
  value,
  fallback = "",
  editing,
  placeholder,
  onChange,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
  fallback?: string;
  editing: boolean;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const source = value || fallback;
  const tasks = source.split(",").map((item) => item.trim()).filter(Boolean);
  // One row per task, edited in place. The parent still stores a comma-joined
  // string, so the row list is held locally: a blank row the user just added
  // would otherwise vanish on the next render before it has been typed into.
  const [rows, setRows] = useState<string[]>(tasks);
  const commit = (next: string[]) => {
    setRows(next);
    onChange(next.map((item) => item.trim()).filter(Boolean).join(", "));
  };
  if (editing) {
    return (
      <View>
        <View style={styles.sectionTitle}>
          {icon}
          <Body weight="700">{title}</Body>
        </View>
        <View style={styles.taskList}>
          {rows.map((task, taskIndex) => (
            <View key={taskIndex} style={styles.taskEditRow}>
              <TextInput
                value={task}
                onChangeText={(text) => commit(
                  rows.map((item, position) => (position === taskIndex ? text : item)))}
                placeholder={placeholder}
                placeholderTextColor={colors.mutedFg}
                style={[styles.input, styles.flex]}
              />
              <Pressable
                accessibilityLabel={`Remove ${task || "task"}`}
                onPress={() => commit(rows.filter((_, position) => position !== taskIndex))}
                style={styles.taskRemove}
              >
                <X size={15} color={colors.destructive} />
              </Pressable>
            </View>
          ))}
        </View>
        <Pressable
          accessibilityLabel={`Add ${title.toLowerCase()} task`}
          onPress={() => commit([...rows, ""])}
          style={styles.addTaskButton}
        >
          <Plus size={14} color={colors.primary} />
          <Small color={colors.primary} weight="700">Add task</Small>
        </Pressable>
      </View>
    );
  }
  return (
    <View>
      <View style={styles.sectionTitle}>
        {icon}
        <Body weight="700">{title}</Body>
      </View>
      {tasks.length ? (
        <View style={styles.taskList}>
          {tasks.map((task, taskIndex) => (
            <View key={`${task}-${taskIndex}`} style={styles.taskRow}>
              <View style={styles.taskDot} />
              <Small style={styles.flex}>{task}</Small>
            </View>
          ))}
        </View>
      ) : (
        <Small color={colors.mutedFg} style={styles.emptyTask}>No tasks specified.</Small>
      )}
    </View>
  );
}

function ProcedureField({
  procedures,
  editing,
  onChange,
}: {
  procedures: ProtocolProcedure[];
  editing: boolean;
  onChange: (value: ProtocolProcedure[]) => void;
}) {
  const update = (index: number, field: keyof ProtocolProcedure, value: string) => {
    onChange(procedures.map((procedure, procedureIndex) => (
      procedureIndex === index ? { ...procedure, [field]: value } : procedure
    )));
  };
  return (
    <View>
      <View style={styles.sectionTitle}>
        <Stethoscope size={16} color={colors.primary} />
        <Body weight="700">Procedure timing & tolerances</Body>
      </View>
      {procedures.length ? (
        <View style={styles.procedureList}>
          {procedures.map((procedure, procedureIndex) => (
            <View key={procedure.id || `${procedure.name}-${procedureIndex}`} style={styles.procedureCard}>
              {editing ? (
                <>
                  <View style={styles.procedureEditHeader}>
                    <TextInput
                      value={procedure.name}
                      onChangeText={(value) => update(procedureIndex, "name", value)}
                      placeholder="Procedure name"
                      placeholderTextColor={colors.mutedFg}
                      style={[styles.input, styles.flex]}
                    />
                    <Pressable
                      accessibilityLabel={`Remove ${procedure.name || "procedure"}`}
                      onPress={() => onChange(procedures.filter((_, index) => index !== procedureIndex))}
                      style={styles.deleteButton}
                    >
                      <Trash2 size={15} color={colors.destructive} />
                    </Pressable>
                  </View>
                  <TextInput
                    value={procedure.timing || ""}
                    onChangeText={(value) => update(procedureIndex, "timing", value)}
                    placeholder="Procedure timing, e.g. 0.5 h post-dose"
                    placeholderTextColor={colors.mutedFg}
                    style={styles.input}
                  />
                  <TextInput
                    value={procedure.window || ""}
                    onChangeText={(value) => update(procedureIndex, "window", value)}
                    placeholder="Procedure tolerance, e.g. ±2 minutes"
                    placeholderTextColor={colors.mutedFg}
                    style={styles.input}
                  />
                  <TextInput
                    value={procedure.condition || procedure.description || ""}
                    onChangeText={(value) => update(procedureIndex, "condition", value)}
                    placeholder="Condition or instruction"
                    placeholderTextColor={colors.mutedFg}
                    style={styles.input}
                  />
                </>
              ) : (
                <>
                  <Body weight="700">{procedure.name}</Body>
                  {!!procedure.timing && <ProcedureLine label="Timing" value={procedure.timing} />}
                  {!!procedure.window && <ProcedureLine label="Procedure tolerance" value={procedure.window} />}
                  {!!procedure.condition && <ProcedureLine label="Condition" value={procedure.condition} />}
                  {!!procedure.description && !procedure.condition && (
                    <ProcedureLine label="Details" value={procedure.description} />
                  )}
                </>
              )}
            </View>
          ))}
        </View>
      ) : (
        <Small color={colors.mutedFg} style={styles.emptyTask}>
          No procedure-level timing or tolerance was extracted.
        </Small>
      )}
      {editing && (
        <Pressable
          onPress={() => onChange([...procedures, { name: "New procedure" }])}
          style={styles.addProcedureButton}
        >
          <Plus size={14} color={colors.primary} />
          <Small color={colors.primary} weight="700">Add procedure</Small>
        </Pressable>
      )}
    </View>
  );
}

function ProcedureLine({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.procedureLine}>
      <Small color={colors.mutedFg} weight="700">{label}:</Small>
      <Small style={styles.flex}>{value}</Small>
    </View>
  );
}

function ConstraintField({
  value,
  editing,
  onChange,
}: {
  value: string;
  editing: boolean;
  onChange: (value: string) => void;
}) {
  const constraints = normalizeProtocolConstraints(value);
  return (
    <View>
      <View style={styles.sectionTitle}>
        <ClipboardList size={16} color={colors.warning} />
        <Body weight="700">Operational constraints</Body>
      </View>
      <Small color={colors.mutedFg} style={styles.constraintHelp}>
        Housing, infusion, washout, sampling, and conditional rules. These are not visit windows.
      </Small>
      {editing ? (
        <TextInput
          value={value}
          onChangeText={onChange}
          placeholder="One protocol constraint per line"
          placeholderTextColor={colors.mutedFg}
          multiline
          textAlignVertical="top"
          style={[styles.input, styles.constraintInput]}
        />
      ) : constraints.length ? (
        <View style={styles.taskList}>
          {constraints.map((constraint, index) => (
            <View key={`${constraint}-${index}`} style={styles.taskRow}>
              <View style={[styles.taskDot, styles.constraintDot]} />
              <Small style={styles.flex}>{constraint}</Small>
            </View>
          ))}
        </View>
      ) : (
        <Small color={colors.mutedFg} style={styles.emptyTask}>No operational constraints specified.</Small>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  centerText: { textAlign: "center" },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  headerActions: { flexDirection: "row", alignItems: "center", gap: 5 },
  headerIcon: { width: 34, height: 34, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  headerIconActive: { backgroundColor: "rgba(255,255,255,0.16)" },
  disabled: { opacity: 0.65 },
  summary: { marginTop: 13, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  summaryLeft: { flexDirection: "row", alignItems: "center", gap: 8 },
  sourcePill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.primary + "12" },
  pendingPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.warning + "14" },
  verifiedPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.success + "12" },
  reviewNotesBlock: { marginTop: 8 },
  reviewNotesToggle: { flexDirection: "row", alignItems: "center", gap: 5, paddingVertical: 4 },
  reviewNotesList: { marginTop: 4, gap: 6, paddingHorizontal: 2 },
  reviewNoteItem: { lineHeight: 18 },
  reviewNoteHeading: { marginTop: 2, fontSize: 10, letterSpacing: 0.5 },
  filters: { marginTop: 12, flexDirection: "row", gap: 7 },
  filterChip: { minHeight: 34, paddingHorizontal: 10, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, flexDirection: "row", alignItems: "center", gap: 5 },
  filterChipActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  filterCount: { minWidth: 19, height: 19, borderRadius: 10, paddingHorizontal: 4, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  filterCountActive: { backgroundColor: "rgba(255,255,255,0.16)" },
  table: { marginTop: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: "hidden", ...shadows.sm },
  tableGrid: { minHeight: 50, paddingHorizontal: 10, display: "flex", flexDirection: "row", alignItems: "center", gap: 5 },
  tableGridEditing: {},
  tableHeader: { minHeight: 37, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  tableRow: { borderBottomWidth: 1, borderBottomColor: colors.border },
  warningRow: { backgroundColor: colors.warning + "09" },
  // A comfortable touch target for dragging, not just the icon's own size.
  orderCell: { width: 30, minHeight: 40, alignItems: "center", justifyContent: "center" },
  draggingRow: {
    backgroundColor: colors.card,
    borderRadius: radii.md,
    zIndex: 20,
    elevation: 6,
    shadowColor: "#000",
    shadowOpacity: 0.18,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 4 },
  },
  dropTargetRow: { backgroundColor: colors.primary + "12" },
  numberCell: { width: 26, alignItems: "center", justifyContent: "center", textAlign: "center" },
  nameCell: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 5 },
  dayCell: { width: 48, textAlign: "center", lineHeight: 15 },
  windowCell: { width: 80, textAlign: "center" },
  deleteCell: { width: 29, alignItems: "center", justifyContent: "center" },
  deleteButton: { width: 29, height: 29, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive + "0C" },
  emptyRows: { paddingHorizontal: 16, paddingVertical: 30, alignItems: "center" },
  addButton: { marginTop: 11, minHeight: 46, borderRadius: radii.lg, borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.primary + "55", backgroundColor: colors.primary + "08", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  notice: { marginTop: 12, minHeight: 42, paddingHorizontal: 11, borderRadius: radii.md, borderWidth: 1, borderColor: colors.success + "40", backgroundColor: colors.success + "10", flexDirection: "row", alignItems: "center", gap: 7 },
  inlineMessage: { marginTop: 9 },
  saveBar: { paddingHorizontal: spacing.md, paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  buttonContent: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  modalBackdrop: { flex: 1, padding: 24, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(46,27,51,0.52)" },
  confirmCard: { width: "100%", maxWidth: 380, padding: 20, borderRadius: radii.xl, alignItems: "center", backgroundColor: colors.card, ...shadows.md },
  successCard: { width: "100%", maxWidth: 380, padding: 24, borderRadius: radii.xl, alignItems: "center", backgroundColor: colors.card, ...shadows.md },
  warningOrb: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center", backgroundColor: colors.warning + "16" },
  successOrb: { width: 64, height: 64, borderRadius: 32, alignItems: "center", justifyContent: "center", backgroundColor: colors.success + "16" },
  confirmTitle: { marginTop: 13, textAlign: "center" },
  confirmCopy: { marginTop: 7, textAlign: "center", lineHeight: 18 },
  confirmActions: { alignSelf: "stretch", marginTop: 18, flexDirection: "row", gap: 9 },
  secondaryAction: { flex: 1, minHeight: 44, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "45", alignItems: "center", justifyContent: "center" },
  primaryAction: { flex: 1, minHeight: 44, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  successAction: { alignSelf: "stretch", marginTop: 20, minHeight: 46, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  // Auto-extracted multi-substudy schedules: one collapsible card per
  // independent Schedule of Assessments, stacked vertically.
  variantList: { marginTop: 12, gap: 10 },
  variantCard: { borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: "hidden", ...shadows.sm },
  variantHeader: { minHeight: 58, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  variantHeaderLeft: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 10 },
  variantHeaderRight: { flexDirection: "row", alignItems: "center", gap: 7 },
  variantOrb: { width: 34, height: 34, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "12" },
  variantBody: { paddingHorizontal: 13, paddingBottom: 13, borderTopWidth: 1, borderTopColor: colors.border },
  variantEditButton: { marginTop: 11, minHeight: 42, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "45", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 },
  sheetBackdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(46,27,51,0.48)" },
  sheetKeyboard: { maxHeight: "89%" },
  sheet: { maxHeight: "100%", borderTopLeftRadius: 26, borderTopRightRadius: 26, backgroundColor: colors.card, overflow: "hidden", ...shadows.md },
  sheetHandle: { alignSelf: "center", width: 40, height: 5, marginTop: 9, borderRadius: 3, backgroundColor: colors.border },
  sheetHeader: { paddingHorizontal: 17, paddingTop: 10, paddingBottom: 13, borderBottomWidth: 1, borderBottomColor: colors.border, flexDirection: "row", alignItems: "center", gap: 10 },
  visitBadge: { width: 42, height: 42, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "12" },
  visitBadgeWarning: { backgroundColor: colors.warning + "16" },
  nameInput: { fontFamily: fonts.semibold, fontSize: 15 },
  metaLine: { marginTop: 4, flexDirection: "row", alignItems: "center", gap: 5 },
  timingSummary: { gap: 8 },
  timingSummaryBlock: { padding: 11, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  timingSummaryValue: { marginTop: 3, marginBottom: 3 },
  closeButton: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  sheetContent: { padding: 17, paddingBottom: 22, gap: 18 },
  warningBanner: { padding: 10, borderRadius: radii.md, borderWidth: 1, borderColor: colors.warning + "35", backgroundColor: colors.warning + "10", flexDirection: "row", alignItems: "flex-start", gap: 7 },
  twoColumns: { flexDirection: "row", gap: 10 },
  offsetHelp: { marginTop: -12, lineHeight: 17 },
  field: { flex: 1 },
  fieldLabel: { marginBottom: 5 },
  input: { minHeight: 42, paddingHorizontal: 11, paddingVertical: 9, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, color: colors.foreground, fontFamily: fonts.regular, fontSize: 13 },
  sectionTitle: { marginBottom: 8, flexDirection: "row", alignItems: "center", gap: 7 },
  taskInput: { minHeight: 67 },
  taskList: { gap: 7 },
  procedureList: { gap: 8 },
  procedureCard: { gap: 7, padding: 11, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  procedureLine: { flexDirection: "row", alignItems: "flex-start", gap: 5 },
  procedureEditHeader: { flexDirection: "row", alignItems: "center", gap: 7 },
  addProcedureButton: { marginTop: 8, minHeight: 36, borderRadius: radii.md, borderWidth: 1, borderStyle: "dashed", borderColor: colors.primary + "55", alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 6 },
  constraintHelp: { marginTop: -5, marginBottom: 8, lineHeight: 17 },
  constraintInput: { minHeight: 88 },
  constraintDot: { backgroundColor: colors.warning },
  taskRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  taskDot: { width: 6, height: 6, marginTop: 6, borderRadius: 3, backgroundColor: colors.primary },
  emptyTask: { fontStyle: "italic" },
  commentInput: { minHeight: 72 },
  readComment: { padding: 11, borderRadius: radii.md, backgroundColor: colors.surface, lineHeight: 18 },
  acknowledgeButton: { minHeight: 42, borderRadius: radii.md, borderWidth: 1, borderColor: colors.success + "45", backgroundColor: colors.success + "10", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  undatedNote: { padding: 10, borderRadius: radii.md, borderWidth: 1, borderColor: colors.warning + "35", backgroundColor: colors.warning + "10", flexDirection: "row", alignItems: "flex-start", gap: 7 },
  sheetFooter: { paddingHorizontal: 17, paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.border, flexDirection: "row", gap: 10 },
  sheetDelete: { flex: 1, minHeight: 44, borderRadius: radii.md, borderWidth: 1, borderColor: colors.destructive + "35", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  taskEditRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  taskRemove: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  addTaskButton: { flexDirection: "row", alignItems: "center", gap: spacing.xs, paddingVertical: spacing.xs },
  detailDivider: { height: 1, backgroundColor: colors.border, marginVertical: spacing.sm },
  sheetDone: { flex: 1, minHeight: 44, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
});
