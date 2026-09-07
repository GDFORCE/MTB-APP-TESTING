import React, { useCallback, useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable, Modal, Text, TextInput, ActivityIndicator, Linking } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Phone, MessageCircle, Calendar as CalIcon, CalendarPlus, Check, CheckCircle2, ChevronDown, Clock3, FileText, Plus, Send, Trash2, X } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { animateNextLayout } from "@/src/lib/motion";
import { formatIsoCalendarDate, formatVisitTiming } from "@/src/lib/visit-timing";

type VisitTask = {
  id: string;
  label: string;
  completed: boolean;
  completed_by?: string | null;
  completed_by_name?: string | null;
  completed_at?: string | null;
};

type VisitComment = {
  id: string;
  text: string;
  created_by: string;
  created_by_name?: string;
  created_at: string;
};

type Instance = {
  id: string;
  name: string;
  seq: number;
  visit_number?: number;
  scheduled_date: string;
  day_offset?: number | null;
  day_end?: number | null;
  hour_offset?: number | null;
  hour_end?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
  source_day_label?: string | null;
  relative_to?: string | null;
  relative_offset_days?: number | null;
  status: string;
  note?: string;
  clinical_tasks?: VisitTask[];
  admin_tasks?: VisitTask[];
  comments?: VisitComment[];
  completed_by_name?: string | null;
  completed_at?: string | null;
  visit_type?: string;
  window_days?: number | null;
  // Set when the protocol timing could not be resolved to a date for this
  // patient (an undated Early Termination / Unscheduled visit, an anchor the
  // schedule never pins down). The visit is still created — dateless.
  manual_review_reason?: string | null;
  // An extra visit added for THIS patient only (an unplanned safety check, a
  // repeat lab, a re-consent) rather than one of the protocol's own visits.
  unscheduled?: boolean;
  unscheduled_reason?: string | null;
};

// Statuses the PATCH /visit-instances/{id} endpoint accepts; surfaced as chips.
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "screen_fail", label: "Screen Fail" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "dropout", label: "Drop Out" },
  { value: "completed", label: "Completed" },
];

function fmtInstantDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

const fmtScheduleDate = (iso?: string | null) => formatIsoCalendarDate(iso, "—");

// The server returns a patient's visits in calendar order (undated ones last).
// An added visit is applied optimistically, so the same order is reproduced
// here — otherwise a visit added mid-schedule would flash at the bottom of the
// list before the reload put it back where its date belongs.
function orderVisits(list: Instance[]): Instance[] {
  const key = (visit: Instance) => {
    const time = Date.parse(visit.scheduled_date || "");
    const seq = Number(visit.seq ?? visit.visit_number ?? 0);
    return Number.isNaN(time)
      ? { dated: 1, time: Number.MAX_SAFE_INTEGER, seq }
      : { dated: 0, time, seq: Number.isNaN(seq) ? 0 : seq };
  };
  return [...list].sort((left, right) => {
    const a = key(left), b = key(right);
    return a.dated - b.dated || a.time - b.time || a.seq - b.seq;
  });
}

// What the VISIT column reads for a row. A protocol visit keeps the number the
// site knows it by; an added visit has no protocol number, so it says what it
// is instead of borrowing (and shifting) its neighbours' numbering.
const visitLabel = (visit: Instance) =>
  visit.unscheduled ? "Extra" : `Visit ${visit.seq ?? visit.visit_number ?? "—"}`;

const todayISO = () => new Date().toISOString().slice(0, 10);

function pillFor(status: string): { label: string; bg: string; fg: string } {
  switch (status) {
    case "completed": return { label: "Done", bg: colors.success + "22", fg: colors.success };
    case "missed": return { label: "Missed", bg: colors.mutedFg + "22", fg: colors.mutedFg };
    case "overdue": return { label: "Overdue", bg: colors.destructive + "22", fg: colors.destructive };
    case "screen_pass": return { label: "Screen Pass", bg: colors.success + "22", fg: colors.success };
    case "screen_fail": return { label: "Screen Fail", bg: colors.destructive + "22", fg: colors.destructive };
    case "withdrawn": return { label: "Withdrawn", bg: colors.mutedFg + "22", fg: colors.mutedFg };
    case "dropout": return { label: "Drop Out", bg: colors.mutedFg + "22", fg: colors.mutedFg };
    default: return { label: "Upcoming", bg: colors.warning + "22", fg: colors.warning };
  }
}

// Clinical (PI/CRC) view of a patient — demographics + a live visit timeline
// backed by per-patient visit instances, with working mutations.
export default function ClinicalVisitDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [patient, setPatient] = useState<any | null>(null);
  const [trial, setTrial] = useState<any | null>(null);
  const [visits, setVisits] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Update-visit bottom sheet state.
  const [editing, setEditing] = useState<Instance | null>(null);
  const [form, setForm] = useState<{ dateISO: string; status: string; note: string; visitType: string }>({ dateISO: "", status: "", note: "", visitType: "Hospital" });
  const [visitTypeOpen, setVisitTypeOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [taskSaving, setTaskSaving] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [commentSaving, setCommentSaving] = useState(false);
  const [sheetFeedback, setSheetFeedback] = useState<string | null>(null);
  const [scheduleOpen, setScheduleOpen] = useState(true);
  const [expandedScheduleVisit, setExpandedScheduleVisit] = useState<string | null>(null);
  // Error surfaced INSIDE the update sheet (the full-screen modal covers the
  // main-scroll error card, so a save failure must render within the sheet).
  const [sheetError, setSheetError] = useState<string | null>(null);
  // Add-visit sheet: one extra dated visit for this patient only.
  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState({ name: "", dateISO: "", windowDays: "0", reason: "" });
  const [addSaving, setAddSaving] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true); setError(null);
    try {
      const [detail, timeline] = await Promise.all([
        api.get(`/patients/${id}`),
        api.get(`/patients/${id}/visits`),
      ]);
      setPatient(detail.data);
      setTrial(detail.data?.trial || null);
      setVisits(orderVisits(timeline.data || []));
    } catch {
      setError("Couldn't load this patient. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Re-ordered on every write: re-dating a visit moves it to where that date
  // now falls in the schedule, the same way the server orders it on reload.
  const applyLocal = (instId: string, patch: Partial<Instance>) => {
    setVisits(list => orderVisits(list.map(v => (v.id === instId ? { ...v, ...patch } : v))));
    setEditing(current => current?.id === instId ? { ...current, ...patch } : current);
  };

  const applyServerInstance = (updated: Instance) => {
    setVisits(list => orderVisits(list.map(v => (v.id === updated.id ? updated : v))));
    setEditing(current => current?.id === updated.id ? updated : current);
  };

  // Optimistic PATCH: apply immediately, reconcile with server, revert on error.
  const markComplete = async (inst: Instance) => {
    const prev = visits;
    applyLocal(inst.id, { status: "completed" });
    try {
      const r = await api.patch(`/visit-instances/${inst.id}`, { status: "completed" });
      applyLocal(inst.id, r.data);
    } catch {
      setVisits(prev);
      setError("Couldn't mark the visit complete. Please try again.");
    }
  };

  const openSheet = (inst: Instance) => {
    setError(null);
    setSheetError(null);
    setSheetFeedback(null);
    setComment("");
    setForm({ dateISO: (inst.scheduled_date || "").slice(0, 10), status: inst.status, note: inst.note || "", visitType: inst.visit_type || "Hospital" });
    setEditing(inst);
  };

  const saveUpdate = async () => {
    if (!editing) return;
    const patch: Record<string, string> = {};
    if (form.status && form.status !== editing.status) patch.status = form.status;
    if (form.note !== (editing.note || "")) patch.note = form.note;
    if (form.visitType !== (editing.visit_type || "Hospital")) patch.visit_type = form.visitType;
    if (form.dateISO && form.dateISO !== (editing.scheduled_date || "").slice(0, 10)) patch.scheduled_date = form.dateISO;
    if (Object.keys(patch).length === 0) { setEditing(null); return; }

    setSaving(true);
    setSheetError(null);
    const prev = visits;
    // Optimistic local apply (server returns canonical window/date on success).
    applyLocal(editing.id, {
      ...(patch.status ? { status: patch.status } : {}),
      ...(patch.note !== undefined ? { note: patch.note } : {}),
      ...(patch.visit_type ? { visit_type: patch.visit_type } : {}),
      ...(patch.scheduled_date ? { scheduled_date: `${patch.scheduled_date}T00:00:00Z` } : {}),
    });
    try {
      const r = await api.patch(`/visit-instances/${editing.id}`, patch);
      applyServerInstance(r.data);
      setEditing(null);
    } catch {
      // Revert the optimistic change and surface the error inside the sheet so
      // the user can correct (e.g. a bad date) and retry without losing input.
      setVisits(prev);
      setSheetError("Couldn't save the visit update. Check the date (YYYY-MM-DD) and try again.");
    } finally {
      setSaving(false);
    }
  };

  const openAddSheet = () => {
    setAddError(null);
    setAddForm({ name: "", dateISO: todayISO(), windowDays: "0", reason: "" });
    setAddOpen(true);
  };

  // Add one extra visit to THIS patient's schedule. The server places it by
  // its date — between the two protocol visits it falls between — and returns
  // the saved row, which is merged back in the same calendar order.
  const addVisit = async () => {
    if (addSaving) return;
    const name = addForm.name.trim();
    const dateISO = addForm.dateISO.trim();
    if (!name) { setAddError("Give the visit a name."); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateISO) || Number.isNaN(Date.parse(dateISO))) {
      setAddError("Enter the visit date as YYYY-MM-DD."); return;
    }
    const windowDays = Number(addForm.windowDays || 0);
    if (!Number.isInteger(windowDays) || windowDays < 0 || windowDays > 365) {
      setAddError("The window must be a whole number of days (0–365)."); return;
    }
    setAddSaving(true);
    setAddError(null);
    try {
      const response = await api.post(`/patients/${id}/visits`, {
        name,
        scheduled_date: dateISO,
        window_days: windowDays,
        reason: addForm.reason.trim() || undefined,
      });
      animateNextLayout();
      setVisits(list => orderVisits([...list, response.data]));
      setScheduleOpen(true);
      setExpandedScheduleVisit(response.data?.id ?? null);
      setAddOpen(false);
    } catch {
      setAddError("Couldn't add this visit. Check the date and try again.");
    } finally {
      setAddSaving(false);
    }
  };

  // Only ever an added visit — a protocol visit is cancelled via its status.
  const removeVisit = async (inst: Instance) => {
    if (removing) return;
    const previous = visits;
    setRemoving(inst.id);
    animateNextLayout();
    setVisits(list => list.filter(v => v.id !== inst.id));
    try {
      await api.delete(`/patients/${id}/visits/${inst.id}`);
    } catch {
      setVisits(previous);
      setError("Couldn't remove that visit. Please try again.");
    } finally {
      setRemoving(null);
    }
  };

  const toggleTask = async (kind: "clinical_tasks" | "admin_tasks", task: VisitTask) => {
    if (!editing || taskSaving) return;
    const previousVisits = visits;
    const previousEditing = editing;
    const nextCompleted = !task.completed;
    const optimistic = (editing[kind] || []).map(row =>
      row.id === task.id ? {
        ...row,
        completed: nextCompleted,
        completed_at: nextCompleted ? new Date().toISOString() : null,
      } : row);
    setTaskSaving(task.id);
    setSheetError(null);
    setSheetFeedback(null);
    animateNextLayout();
    applyLocal(editing.id, { [kind]: optimistic });
    try {
      const response = await api.patch(
        `/visit-instances/${editing.id}/tasks/${task.id}`,
        { completed: nextCompleted },
      );
      applyServerInstance(response.data);
      setSheetFeedback(nextCompleted ? "Task marked complete." : "Task reopened.");
    } catch {
      setVisits(previousVisits);
      setEditing(previousEditing);
      setSheetError("Couldn't update this task. The previous state was restored.");
    } finally {
      setTaskSaving(null);
    }
  };

  const addComment = async () => {
    if (!editing || commentSaving || !comment.trim()) return;
    const previousVisits = visits;
    const previousEditing = editing;
    const text = comment.trim();
    const optimisticComment: VisitComment = {
      id: `pending-${Date.now()}`,
      text,
      created_by: "",
      created_by_name: "Saving…",
      created_at: new Date().toISOString(),
    };
    setCommentSaving(true);
    setSheetError(null);
    setSheetFeedback(null);
    animateNextLayout();
    applyLocal(editing.id, { comments: [...(editing.comments || []), optimisticComment] });
    setComment("");
    try {
      const response = await api.post(`/visit-instances/${editing.id}/comments`, { text });
      applyServerInstance(response.data);
      setSheetFeedback("Comment added.");
    } catch {
      setVisits(previousVisits);
      setEditing(previousEditing);
      setComment(text);
      setSheetError("Couldn't add the comment. The unsaved comment was restored.");
    } finally {
      setCommentSaving(false);
    }
  };

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Patient record" title="Loading…" />
        <View style={{ padding: spacing.xxl, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }
  if (!patient) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Patient record" title="Not found" />
        <View style={{ padding: spacing.md }}>
          <Small color={colors.destructive}>{error || "This patient could not be loaded."}</Small>
          <Button testID="retry" variant="secondary" style={{ marginTop: spacing.md }} onPress={load}><Small weight="700" color={colors.primary}>Retry</Small></Button>
        </View>
      </ScreenContainer>
    );
  }

  const currentVisit = visits.find(v => v.status === "overdue") || visits.find(v => v.status !== "completed") || visits[visits.length - 1];
  const completedVisits = visits.filter(v => v.status === "completed").slice().reverse();
  const windowLabel = (date?: string) => {
    if (!date) return "—";
    const match = date.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|[T\s])/);
    if (!match) return "—";
    const base = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
    if (Number.isNaN(base.getTime())) return "—";
    const start = new Date(base); start.setUTCDate(start.getUTCDate() - 3);
    const end = new Date(base); end.setUTCDate(end.getUTCDate() + 3);
    const fmt = (value: Date) => formatIsoCalendarDate(value.toISOString(), "—");
    return `${fmt(start)} – ${fmt(end)}`;
  };
  const recordStatus = pillFor(currentVisit?.status || "completed");

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="PATIENT" title="Patient Record" />
      <ScrollView contentContainerStyle={s.recordContent}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.finalHero}>
          <View style={s.finalHeroTop}><View style={s.finalIdentity}><View style={s.finalAvatar}><Body weight="700" color={colors.primaryFg}>{patient.avatar_initials || (patient.full_name || "?").slice(0, 2).toUpperCase()}</Body></View><View><Body weight="700" color={colors.primaryFg}>{patient.avatar_initials || "Participant"}</Body><Small color={colors.overlay25}>{patient.subject_id || `SUBJ-${String(patient.id).slice(-3)}`}{patient.age ? ` · Age ${patient.age}` : ""}</Small></View></View><View style={s.finalStatus}><Small color={colors.primaryFg} weight="700">{recordStatus.label}</Small></View></View>
          <View style={s.finalGrid}>{[
            { label: "PROTOCOL", value: trial?.protocol_id || "—" }, { label: "SITE", value: trial?.site_names?.[0] || patient.site_name || "—" },
            { label: "CURRENT VISIT", value: currentVisit ? visitLabel(currentVisit) : "—" }, { label: "VISIT DATE", value: fmtScheduleDate(currentVisit?.scheduled_date) },
            { label: "VISIT COMPLETED", value: currentVisit?.status === "completed" ? "Yes" : "No" }, { label: "LAST UPDATED", value: currentVisit?.completed_at ? fmtInstantDate(currentVisit.completed_at) : "—" },
          ].map(field => <View key={field.label} style={s.finalField}><Eyebrow color={colors.overlay25} style={s.finalLabel}>{field.label}</Eyebrow><Small color={colors.primaryFg} weight="700" numberOfLines={1} style={s.finalValue}>{field.value}</Small></View>)}</View>
        </LinearGradient>

        <Card style={s.finalRemarks}><Eyebrow style={{ marginBottom: 7 }}>REMARKS</Eyebrow><Small color={colors.foreground}>{currentVisit?.note?.trim() || "No remarks recorded yet."}</Small></Card>

        {/* The canonical schedule this patient is actually scheduled from:
            day-wise activity timing, conditional requirements, confinement
            episodes and the schedule version they were enrolled under. Shown
            only once the patient is linked, because before that there is
            nothing canonical to open. */}
        {!!patient.uctsm_patient_id && (
          <Pressable
            testID="open-canonical-patient-schedule"
            accessibilityRole="button"
            accessibilityLabel="Open the protocol schedule for this patient"
            onPress={() => router.push({
              pathname: "/(app)/clinical/patient-schedule",
              params: {
                patientId: patient.uctsm_patient_id,
                patientName: patient.subject_id || patient.full_name || "",
              },
            })}
          >
            <Card style={s.canonicalLink}>
              <View style={{ flex: 1 }}>
                <Eyebrow style={{ marginBottom: 4 }}>PROTOCOL SCHEDULE</Eyebrow>
                <Small color={colors.foreground}>
                  Activities, conditional requirements, confinement and anchor status
                </Small>
              </View>
              <ChevronDown
                size={16}
                color={colors.primary}
                style={{ transform: [{ rotate: "-90deg" }] }}
              />
            </Card>
          </Pressable>
        )}

        <View>
          <View style={s.finalScheduleHead}>
            <Pressable onPress={() => setScheduleOpen(open => !open)} style={s.scheduleHeadToggle}><Eyebrow>VISIT SCHEDULE</Eyebrow><Small>{visits.length} visits</Small><ChevronDown size={15} color={colors.primary} style={{ transform: [{ rotate: scheduleOpen ? "180deg" : "0deg" }] }} /></Pressable>
            <Pressable testID="add-patient-visit" onPress={openAddSheet} hitSlop={8} style={s.addVisitButton}><Plus size={13} color={colors.primary} /><Small color={colors.primary} weight="700">Add visit</Small></Pressable>
          </View>
          {scheduleOpen && <View style={s.scheduleTable}>
            <View style={s.scheduleColumns}><Text style={s.scheduleColumnText}>VISIT</Text><Text style={[s.scheduleColumnText, { flex: 1.15 }]}>VISIT NAME</Text><Text style={[s.scheduleColumnText, { flex: 1, textAlign: "right" }]}>WINDOW PERIOD</Text></View>
            {visits.map((visit, index) => {
              const expanded = expandedScheduleVisit === visit.id;
              const done = visit.status === "completed";
              const overdue = visit.status === "overdue";
              const tasks = [...(visit.clinical_tasks || []), ...(visit.admin_tasks || [])];
              const added = !!visit.unscheduled;
              // No calculable date: the visit is real and still on the
              // schedule, it just cannot be placed on the calendar yet. It
              // sorts to the end of the timeline, so the row has to say why
              // it is down there rather than showing a bare dash.
              const undated = !visit.scheduled_date;
              return <View key={visit.id} style={index ? s.scheduleDivider : undefined}><Pressable onPress={() => setExpandedScheduleVisit(expanded ? null : visit.id)} style={[s.scheduleRow, expanded && s.scheduleRowOpen, added && s.scheduleRowAdded]}><View style={s.scheduleVisitCell}>{done ? <CheckCircle2 size={14} color={colors.success} /> : added ? <CalendarPlus size={14} color={colors.primary} /> : overdue ? <Clock3 size={14} color={colors.destructive} /> : <CalIcon size={13} color={colors.mutedFg} />}<Small weight="700" color={added ? colors.primary : undefined}>{visitLabel(visit)}</Small></View><View style={{ flex: 1.15 }}><Small numberOfLines={1}>{visit.name || "Visit"}</Small><Small color={colors.mutedFg} numberOfLines={1}>{added ? (visit.unscheduled_reason || "Added for this patient") : formatVisitTiming(visit)}</Small></View><View style={s.scheduleWindow}><Small numberOfLines={1} color={undated ? colors.warning : undefined}>{undated ? "Date pending" : windowLabel(visit.scheduled_date)}</Small><ChevronDown size={13} color={colors.mutedFg} style={{ transform: [{ rotate: expanded ? "180deg" : "0deg" }] }} /></View></Pressable>{expanded && <View style={s.activities}><Eyebrow style={{ marginBottom: 6 }}>{done ? "ACTIVITIES COMPLETED" : "PLANNED ACTIVITIES"}</Eyebrow>{tasks.length ? <View style={s.activitiesGrid}>{tasks.map(task => <View key={task.id} style={s.activity}><CheckCircle2 size={12} color={task.completed ? colors.success : colors.mutedFg} /><Small numberOfLines={1}>{task.label}</Small></View>)}</View> : <Small>No activities listed.</Small>}{undated && <View testID={`manual-review-${visit.id}`} style={s.manualReview}><Clock3 size={13} color={colors.warning} /><Small color={colors.warning} style={{ flex: 1 }}>{visit.manual_review_reason?.trim() || "This visit has no date yet. Add the date once the protocol timing for it is known."}</Small></View>}{added && <Pressable testID={`remove-visit-${visit.id}`} disabled={removing === visit.id} onPress={() => removeVisit(visit)} style={s.removeVisit}><Trash2 size={13} color={colors.destructive} /><Small color={colors.destructive} weight="700">{removing === visit.id ? "Removing…" : "Remove this visit"}</Small></Pressable>}</View>}</View>;
            })}
          </View>}
        </View>

        <View><View style={s.finalScheduleHead}><Eyebrow>VISIT HISTORY</Eyebrow><Small>{completedVisits.length} completed</Small></View><Card style={s.historyCard}>{completedVisits.length ? <View style={s.historyLine}>{completedVisits.map(visit => <View key={visit.id} style={s.historyItem}><View style={s.historyIcon}><CheckCircle2 size={14} color={colors.success} /></View><View style={{ flex: 1 }}><View style={s.historyTop}><Small weight="700">{visitLabel(visit)}</Small><View style={s.historyDone}><Small color={colors.success} weight="700">Completed</Small></View></View><Small>{visit.name || "Visit"}</Small><Small>{fmtScheduleDate(visit.scheduled_date)}</Small>{visit.note ? <Small style={s.historyNote}>“{visit.note}”</Small> : null}</View></View>)}</View> : <Small>No visits recorded yet.</Small>}</Card></View>

        <Button testID="patient-record-update" variant="dawn" disabled={!currentVisit} onPress={() => currentVisit && openSheet(currentVisit)}><Small color={colors.primaryFg} weight="700">Update Visit</Small></Button>

        {false && <>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.avatar}><Body weight="700" color={colors.primary} style={{ fontSize: 22 }}>{patient.avatar_initials || (patient.full_name || "?").slice(0, 2).toUpperCase()}</Body></View>
            <View style={{ flex: 1 }}>
              <H1 color={colors.primaryFg} style={{ fontSize: 18 }}>{patient.full_name}</H1>
              <Small color={colors.overlay25}>{trial?.protocol_id || "—"} · Enrolled {fmtScheduleDate(patient.enrolled_date)}</Small>
            </View>
          </View>
          <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.md }}>
            <Pressable
              testID="contact-call"
              style={[s.heroBtn, !patient.phone && { opacity: 0.45 }]}
              disabled={!patient.phone}
              onPress={() => Linking.openURL(`tel:${String(patient.phone).replace(/[^\d+]/g, "")}`)}
            ><Phone size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Call</Small></Pressable>
            <Pressable
              testID="contact-chat"
              style={[s.heroBtn, !patient.user_id && { opacity: 0.45 }]}
              disabled={!patient.user_id}
              onPress={() => router.push({
                pathname: "/(app)/chat",
                params: { participantId: patient.user_id },
              })}
            ><MessageCircle size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Chat</Small></Pressable>
          </View>
        </LinearGradient>

        <Card style={s.recordSummary}>
          <View style={s.recordSummaryTop}>
            <View style={s.recordAvatar}><Body weight="700" color={colors.primary}>{patient.avatar_initials || (patient.full_name || "?").slice(0, 2).toUpperCase()}</Body></View>
            <View style={{ flex: 1 }}><Body weight="700">{patient.avatar_initials || patient.full_name || "Participant"}</Body><Small>{patient.subject_id || patient.patient_code || `SUBJ-${String(patient.id || "").slice(-3)}`} {patient.age ? `· Age ${patient.age}` : ""}</Small></View>
            <View style={[s.recordStatus, { backgroundColor: visits.some(v => v.status === "overdue") ? colors.destructive + "20" : colors.success + "20" }]}><Small weight="700" color={visits.some(v => v.status === "overdue") ? colors.destructive : colors.success}>{visits.some(v => v.status === "overdue") ? "Overdue" : "On Track"}</Small></View>
          </View>
          <View style={s.recordGrid}>{[
            { label: "PROTOCOL", value: trial?.protocol_id || "—" }, { label: "SITE", value: trial?.site_names?.[0] || patient.site_name || "—" },
            { label: "CURRENT VISIT", value: (() => { const v = visits.find(x => x.status === "overdue") || visits.find(x => x.status !== "completed"); return `Visit ${v?.seq ?? v?.visit_number ?? "—"}`; })() },
            { label: "VISIT DATE", value: fmtScheduleDate((visits.find(x => x.status === "overdue") || visits.find(x => x.status !== "completed"))?.scheduled_date) },
            { label: "VISIT COMPLETED", value: visits.some(v => v.status !== "completed") ? "No" : "Yes" }, { label: "LAST UPDATED", value: patient.updated_at ? fmtInstantDate(patient.updated_at) : fmtScheduleDate(patient.enrolled_date) },
          ].map(field => <View key={field.label} style={{ width: "50%", paddingRight: 8 }}><Eyebrow style={s.recordLabel}>{field.label}</Eyebrow><Small numberOfLines={1} weight="700" style={s.recordValue}>{field.value}</Small></View>)}</View>
        </Card>

        <Card style={s.remarksCard}><Eyebrow style={{ marginBottom: 7 }}>REMARKS</Eyebrow><Small color={colors.foreground}>{visits.find(v => v.note?.trim())?.note || "No remarks recorded yet."}</Small></Card>

        {error && (
          <Card style={{ borderColor: colors.destructive + "55" }}>
            <Small color={colors.destructive} weight="700">{error}</Small>
          </Card>
        )}

        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>CONTACT INFO</Eyebrow>
          <Row label="Email" value={patient.email || "—"} />
          <Row label="Phone" value={patient.phone || "—"} />
          <Row label="Enrolled" value={fmtScheduleDate(patient.enrolled_date)} last />
        </Card>

        <View>
          <View style={s.scheduleHeading}><Eyebrow>VISIT SCHEDULE</Eyebrow><Small>{visits.length} visits</Small></View>
          {visits.length === 0 && (
            <Card><Small>No visits scheduled for this patient yet.</Small></Card>
          )}
          {visits.map((v) => {
            const done = v.status === "completed";
            const pill = pillFor(v.status);
            const seq = v.seq ?? v.visit_number ?? 0;
            const tasks = [...(v.clinical_tasks || []), ...(v.admin_tasks || [])];
            const completedTasks = tasks.filter(task => task.completed).length;
            return (
              <Card key={v.id} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={[s.node, done && { backgroundColor: colors.accent, borderColor: colors.accent }]}>
                    {done ? <Check size={14} color={colors.primaryFg} /> : <Small weight="700">{seq}</Small>}
                  </View>
                  <View style={{ flex: 1 }}>
                    <Body weight="700">Visit {seq} · {v.name}</Body>
                    <View style={{ flexDirection: "row", gap: 4, alignItems: "center", marginTop: 2 }}>
                      <CalIcon size={11} color={colors.mutedFg} /><Small>{fmtScheduleDate(v.scheduled_date)} · {formatVisitTiming(v)}</Small>
                    </View>
                  </View>
                  <View style={[s.pill, { backgroundColor: pill.bg }]}>
                    <Small weight="700" color={pill.fg}>{pill.label}</Small>
                  </View>
                </View>
                {tasks.length > 0 && (
                  <View style={s.taskSummary}>
                    <Small weight="700">Visit tasks</Small>
                    <Small color={completedTasks === tasks.length ? colors.success : colors.mutedFg}>
                      {completedTasks}/{tasks.length} completed
                    </Small>
                  </View>
                )}
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border }}>
                  {!done && (
                    <Button testID={`mark-${v.id}-done`} variant="primary" style={{ flex: 1, paddingVertical: 10 }} onPress={() => markComplete(v)}><Small weight="700" color={colors.primaryFg}>Mark complete</Small></Button>
                  )}
                  <Button testID={`update-${v.id}`} variant="secondary" style={{ flex: 1, paddingVertical: 10 }} onPress={() => openSheet(v)}><Small weight="700" color={colors.primary}>Update visit</Small></Button>
                </View>
              </Card>
            );
          })}
        </View>

        <Button
          testID="view-records"
          variant="secondary"
          disabled={!trial?.id}
          onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } })}
        ><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><FileText size={14} color={colors.primary} /><Small weight="700" color={colors.primary}>View clinical records</Small></View></Button>
        </>}
      </ScrollView>

      {/* ── Update Visit bottom sheet ─────────────────────────── */}
      <Modal visible={!!editing} transparent animationType="slide" onRequestClose={() => setEditing(null)}>
        <Pressable style={s.backdrop} onPress={() => (saving ? null : setEditing(null))} />
        <View style={s.sheet}>
          <View style={s.grabber} />
          <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", marginBottom: spacing.md }}>
            <View>
              <H1 style={{ fontSize: 18 }}>Update Visit</H1>
              <Small>{editing ? `${visitLabel(editing)} · ${editing.name}` : ""}</Small>
            </View>
            <Pressable testID="sheet-close" onPress={() => setEditing(null)} hitSlop={10}><X size={20} color={colors.mutedFg} /></Pressable>
          </View>

          <ScrollView contentContainerStyle={{ gap: spacing.md, paddingBottom: spacing.md }} keyboardShouldPersistTaps="handled">
            {/* Inline save error — rendered inside the sheet so it stays visible
                above the modal while the user fixes their input and retries. */}
            {sheetError && (
              <View testID="sheet-error" style={s.sheetError}>
                <Small color={colors.destructive} weight="700">{sheetError}</Small>
              </View>
            )}
            {sheetFeedback && (
              <View accessibilityLiveRegion="polite" style={s.sheetFeedback}>
                <Check size={14} color={colors.success} />
                <Small color={colors.success} weight="700">{sheetFeedback}</Small>
              </View>
            )}

            {/* Trial context (read-only) */}
            <View style={s.context}>
              {[
                { label: "Protocol ID", val: trial?.protocol_id || "—" },
                { label: "Phase", val: trial?.phase || "—" },
                { label: "Indication", val: trial?.condition || "—" },
              ].map((f) => (
                <View key={f.label} style={{ flex: 1 }}>
                  <Eyebrow style={{ fontSize: 9 }}>{f.label}</Eyebrow>
                  <Small weight="700" color={colors.foreground} style={{ marginTop: 2 }}>{f.val}</Small>
                </View>
              ))}
            </View>

            <View style={s.editPair}><View style={{ flex: 1 }}><Small weight="700" style={{ marginBottom: 6 }}>Visit</Small><View style={s.readOnly}><Small weight="700">{editing ? visitLabel(editing) : "—"}</Small></View></View><View style={{ flex: 1 }}><Small weight="700" style={{ marginBottom: 6 }}>Visit Date</Small><TextInput testID="sheet-date" value={form.dateISO} onChangeText={(t) => setForm(f => ({ ...f, dateISO: t }))} placeholder="YYYY-MM-DD" placeholderTextColor={colors.mutedFg} style={s.input} /></View></View>

            <View style={s.editPair}><View style={{ flex: 1 }}><Small weight="700" style={{ marginBottom: 6 }}>Visit Name</Small><View style={s.readOnly}><Small numberOfLines={1}>{editing?.name || "Visit"}</Small></View></View><View style={{ flex: 1 }}><Small weight="700" style={{ marginBottom: 6 }}>Visit Type</Small><Pressable testID="visit-type-dropdown" onPress={() => setVisitTypeOpen(true)} style={s.selectBox}><Small>{form.visitType}</Small><ChevronDown size={16} color={colors.foreground} /></Pressable></View></View>

            {/* Status */}
            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Status</Small>
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {STATUS_OPTIONS.map((o) => {
                  const on = form.status === o.value;
                  return (
                    <Pressable key={o.value} testID={`status-${o.value}`} onPress={() => setForm(f => ({ ...f, status: o.value }))} style={[s.statusChip, on ? { backgroundColor: colors.primary, borderColor: colors.primary } : { borderColor: colors.border }]}>
                      <Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{o.label}</Small>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            {false && <>
            <VisitTaskGroup
              title="Clinical tasks"
              tasks={editing?.clinical_tasks || []}
              savingId={taskSaving}
              onToggle={(task) => toggleTask("clinical_tasks", task)}
            />
            <VisitTaskGroup
              title="Administrative tasks"
              tasks={editing?.admin_tasks || []}
              savingId={taskSaving}
              onToggle={(task) => toggleTask("admin_tasks", task)}
            />

            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Visit comments</Small>
              {(editing?.comments || []).length === 0 ? (
                <View style={s.emptyTasks}><Small>No comments added yet.</Small></View>
              ) : (
                <View style={{ gap: 8 }}>
                  {(editing?.comments || []).map(row => (
                    <View key={row.id} style={s.commentCard}>
                      <Small color={colors.foreground}>{row.text}</Small>
                      <Small style={{ marginTop: 5 }}>
                        {row.created_by_name || "Study team"} · {fmtInstantDate(row.created_at)}
                      </Small>
                    </View>
                  ))}
                </View>
              )}
              <View style={s.commentComposer}>
                <TextInput
                  testID="visit-comment"
                  value={comment}
                  onChangeText={setComment}
                  placeholder="Add a visit comment…"
                  placeholderTextColor={colors.mutedFg}
                  multiline
                  maxLength={2000}
                  style={[s.input, s.commentInput]}
                />
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Add visit comment"
                  testID="add-visit-comment"
                  disabled={commentSaving || !comment.trim()}
                  onPress={addComment}
                  style={[s.sendButton, (commentSaving || !comment.trim()) && { opacity: 0.45 }]}
                >
                  {commentSaving
                    ? <ActivityIndicator size="small" color={colors.primaryFg} />
                    : <Send size={16} color={colors.primaryFg} />}
                </Pressable>
              </View>
            </View>
            </>}

            {/* Remarks */}
            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Remarks</Small>
              <TextInput
                testID="sheet-note"
                value={form.note}
                onChangeText={(t) => setForm(f => ({ ...f, note: t }))}
                placeholder="Add any notes about this visit…"
                placeholderTextColor={colors.mutedFg}
                multiline
                style={[s.input, { height: 84, textAlignVertical: "top" }]}
              />
            </View>

            <View style={{ flexDirection: "row", gap: 8 }}>
              <Button testID="sheet-cancel" variant="secondary" style={{ flex: 1 }} onPress={() => setEditing(null)}><Small weight="700" color={colors.primary}>Cancel</Small></Button>
              <Button testID="sheet-save" variant="dawn" style={{ flex: 1 }} loading={saving} onPress={saveUpdate}><Small weight="700" color={colors.primaryFg}>Save Update</Small></Button>
            </View>
          </ScrollView>
        </View>
      </Modal>
      {/* ── Add Visit bottom sheet ─────────────────────── */}
      <Modal visible={addOpen} transparent animationType="slide" onRequestClose={() => setAddOpen(false)}>
        <Pressable style={s.backdrop} onPress={() => (addSaving ? null : setAddOpen(false))} />
        <View style={s.sheet}>
          <View style={s.grabber} />
          <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", marginBottom: spacing.md }}>
            <View>
              <H1 style={{ fontSize: 18 }}>Add Visit</H1>
              <Small>Added for this patient only · placed by its date</Small>
            </View>
            <Pressable testID="add-sheet-close" onPress={() => setAddOpen(false)} hitSlop={10}><X size={20} color={colors.mutedFg} /></Pressable>
          </View>

          <ScrollView contentContainerStyle={{ gap: spacing.md, paddingBottom: spacing.md }} keyboardShouldPersistTaps="handled">
            {addError && (
              <View testID="add-sheet-error" style={s.sheetError}>
                <Small color={colors.destructive} weight="700">{addError}</Small>
              </View>
            )}

            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Visit name</Small>
              <TextInput
                testID="add-visit-name"
                value={addForm.name}
                onChangeText={(t) => setAddForm(f => ({ ...f, name: t }))}
                placeholder="e.g. Unscheduled safety review"
                placeholderTextColor={colors.mutedFg}
                style={s.input}
              />
            </View>

            <View style={s.editPair}>
              <View style={{ flex: 1 }}>
                <Small weight="700" style={{ marginBottom: 6 }}>Visit date</Small>
                <TextInput
                  testID="add-visit-date"
                  value={addForm.dateISO}
                  onChangeText={(t) => setAddForm(f => ({ ...f, dateISO: t }))}
                  placeholder="YYYY-MM-DD"
                  placeholderTextColor={colors.mutedFg}
                  style={s.input}
                />
              </View>
              <View style={{ flex: 1 }}>
                <Small weight="700" style={{ marginBottom: 6 }}>Window (± days)</Small>
                <TextInput
                  testID="add-visit-window"
                  value={addForm.windowDays}
                  onChangeText={(t) => setAddForm(f => ({ ...f, windowDays: t.replace(/[^0-9]/g, "") }))}
                  placeholder="0"
                  placeholderTextColor={colors.mutedFg}
                  keyboardType="number-pad"
                  style={s.input}
                />
              </View>
            </View>

            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Reason (optional)</Small>
              <TextInput
                testID="add-visit-reason"
                value={addForm.reason}
                onChangeText={(t) => setAddForm(f => ({ ...f, reason: t }))}
                placeholder="Why this extra visit was added"
                placeholderTextColor={colors.mutedFg}
                multiline
                style={[s.input, { height: 72, textAlignVertical: "top" }]}
              />
            </View>

            <View style={s.addHint}>
              <CalendarPlus size={14} color={colors.primary} />
              <Small color={colors.mutedFg} style={{ flex: 1 }}>
                This visit is added to this patient&apos;s schedule only. It takes its
                place in the timeline by date — in between the protocol visits it
                falls between — and the trial&apos;s other patients are unaffected.
              </Small>
            </View>

            <View style={{ flexDirection: "row", gap: 8 }}>
              <Button testID="add-visit-cancel" variant="secondary" style={{ flex: 1 }} onPress={() => setAddOpen(false)}><Small weight="700" color={colors.primary}>Cancel</Small></Button>
              <Button testID="add-visit-save" variant="dawn" style={{ flex: 1 }} loading={addSaving} onPress={addVisit}><Small weight="700" color={colors.primaryFg}>Add Visit</Small></Button>
            </View>
          </ScrollView>
        </View>
      </Modal>
      <Modal visible={visitTypeOpen} transparent animationType="fade" onRequestClose={() => setVisitTypeOpen(false)}>
        <Pressable style={s.selectBackdrop} onPress={() => setVisitTypeOpen(false)} />
        <View style={s.selectMenu}>{["Hospital", "Phone", "Remote", "Home"].map(option => <Pressable key={option} testID={`visit-type-${option.toLowerCase()}`} onPress={() => { setForm(current => ({ ...current, visitType: option })); setVisitTypeOpen(false); }} style={[s.selectOption, form.visitType === option && s.selectOptionActive]}><Small color={form.visitType === option ? colors.primaryFg : colors.foreground} weight={form.visitType === option ? "700" : "400"}>{option}</Small></Pressable>)}</View>
      </Modal>
    </ScreenContainer>
  );
}

function VisitTaskGroup({
  title,
  tasks,
  savingId,
  onToggle,
}: {
  title: string;
  tasks: VisitTask[];
  savingId: string | null;
  onToggle: (task: VisitTask) => void;
}) {
  return (
    <View>
      <View style={s.sectionHeading}>
        <Small weight="700">{title}</Small>
        {tasks.length > 0 && (
          <Small>{tasks.filter(task => task.completed).length}/{tasks.length}</Small>
        )}
      </View>
      {tasks.length === 0 ? (
        <View style={s.emptyTasks}><Small>No {title.toLowerCase()} configured for this visit.</Small></View>
      ) : (
        <View style={s.taskList}>
          {tasks.map(task => {
            const saving = savingId === task.id;
            return (
              <Pressable
                key={task.id}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: task.completed, disabled: !!savingId }}
                accessibilityLabel={`${task.label}. ${task.completed ? "Completed" : "Not completed"}`}
                testID={`visit-task-${task.id}`}
                disabled={!!savingId}
                onPress={() => onToggle(task)}
                style={[s.taskRow, saving && { opacity: 0.55 }]}
              >
                <View style={[s.checkbox, task.completed && s.checkboxDone]}>
                  {saving
                    ? <ActivityIndicator size="small" color={task.completed ? colors.primaryFg : colors.primary} />
                    : task.completed && <Check size={13} color={colors.primaryFg} />}
                </View>
                <View style={{ flex: 1 }}>
                  <Small weight="700" color={task.completed ? colors.mutedFg : colors.foreground}>
                    {task.label}
                  </Small>
                  {task.completed && (
                    <Small style={{ marginTop: 2 }}>
                      Completed{task.completed_by_name ? ` by ${task.completed_by_name}` : ""}
                      {task.completed_at ? ` · ${fmtInstantDate(task.completed_at)}` : ""}
                    </Small>
                  )}
                </View>
              </Pressable>
            );
          })}
        </View>
      )}
    </View>
  );
}

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[{ flexDirection: "row", justifyContent: "space-between", paddingVertical: 8 }, !last && { borderBottomWidth: 1, borderBottomColor: colors.border }]}>
      <Small>{label}</Small>
      <Small weight="700" color={colors.foreground}>{value}</Small>
    </View>
  );
}

const s = StyleSheet.create({
  recordContent: { padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md },
  finalHero: { borderRadius: radii.xl, padding: spacing.md },
  finalHeroTop: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 10, marginBottom: 13 },
  finalIdentity: { flexDirection: "row", alignItems: "center", gap: 10, flex: 1 },
  finalAvatar: { width: 42, height: 42, borderRadius: 21, alignItems: "center", justifyContent: "center", backgroundColor: colors.overlay20 },
  finalStatus: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: radii.pill, backgroundColor: colors.overlay20 },
  finalGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: 11 },
  finalField: { width: "50%", paddingRight: 8 },
  finalLabel: { fontSize: 9 }, finalValue: { marginTop: 3, fontSize: 11 },
  canonicalLink: { flexDirection: "row", alignItems: "center", gap: 10 },
  finalRemarks: { paddingVertical: 13 },
  finalScheduleHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 7 },
  scheduleHeadToggle: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8 },
  addVisitButton: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: colors.primary, backgroundColor: colors.card },
  scheduleRowAdded: { backgroundColor: colors.primary + "0D" },
  removeVisit: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 10, alignSelf: "flex-start", minHeight: 30 },
  manualReview: { flexDirection: "row", alignItems: "flex-start", gap: 7, marginTop: 10, padding: spacing.sm, borderRadius: radii.md, borderWidth: 1, borderColor: colors.warning + "55", backgroundColor: colors.warning + "12" },
  addHint: { flexDirection: "row", gap: 8, padding: spacing.sm, borderRadius: radii.md, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
  scheduleTable: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden", backgroundColor: colors.card },
  scheduleColumns: { flexDirection: "row", paddingHorizontal: 10, paddingVertical: 8, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  scheduleColumnText: { flex: 0.95, color: colors.mutedFg, opacity: 0.75, fontSize: 8, fontWeight: "700", letterSpacing: 0.7 },
  scheduleDivider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
  scheduleRow: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, paddingVertical: 9 },
  scheduleRowOpen: { backgroundColor: colors.surface },
  scheduleVisitCell: { flex: 0.95, flexDirection: "row", alignItems: "center", gap: 4 },
  scheduleWindow: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 2 },
  activities: { paddingHorizontal: 10, paddingBottom: 10, backgroundColor: colors.surface },
  activitiesGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: 6 },
  activity: { width: "50%", flexDirection: "row", alignItems: "center", gap: 4, paddingRight: 5 },
  historyCard: { padding: spacing.md }, historyLine: { gap: 13, paddingLeft: 20, borderLeftWidth: 1, borderLeftColor: colors.border },
  historyItem: { position: "relative" }, historyIcon: { position: "absolute", left: -28, top: 1, width: 15, height: 15, backgroundColor: colors.card },
  historyTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" }, historyDone: { paddingHorizontal: 7, paddingVertical: 2, borderRadius: radii.pill, backgroundColor: colors.success + "20" }, historyNote: { marginTop: 2, fontStyle: "italic" },
  hero: { borderRadius: radii.xl, padding: spacing.md },
  recordSummary: { padding: spacing.md, borderColor: colors.border },
  recordSummaryTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  recordAvatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  recordStatus: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: radii.pill },
  recordGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: 12, paddingTop: 13, marginTop: 13, borderTopWidth: 1, borderTopColor: colors.border },
  recordLabel: { color: colors.mutedFg, opacity: 0.72, fontSize: 9 },
  recordValue: { fontSize: 11, marginTop: 3 },
  remarksCard: { paddingVertical: 13 },
  scheduleHeading: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  heroBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.overlay20 },
  node: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: { backgroundColor: colors.card, borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, padding: spacing.md, maxHeight: "88%" },
  grabber: { alignSelf: "center", width: 40, height: 4, borderRadius: 999, backgroundColor: colors.border, marginBottom: spacing.md },
  context: { flexDirection: "row", gap: spacing.sm, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, padding: spacing.sm },
  editPair: { flexDirection: "row", gap: spacing.sm },
  readOnly: { minHeight: 43, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.background, paddingHorizontal: 12, justifyContent: "center" },
  selectBox: { minHeight: 43, borderWidth: 1, borderColor: colors.primary, borderRadius: radii.md, backgroundColor: colors.card, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  selectBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.24)" },
  selectMenu: { position: "absolute", left: spacing.md, right: spacing.md, bottom: "34%", borderRadius: radii.lg, overflow: "hidden", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, elevation: 12, shadowColor: "#2E1B33", shadowOpacity: 0.22, shadowRadius: 12 },
  selectOption: { paddingHorizontal: 16, paddingVertical: 13, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  selectOptionActive: { backgroundColor: colors.primary },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.background, paddingHorizontal: 12, paddingVertical: 10, color: colors.foreground, fontSize: 15 },
  statusChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, borderWidth: 1, backgroundColor: colors.card },
  sheetError: { backgroundColor: colors.destructive + "14", borderWidth: 1, borderColor: colors.destructive + "55", borderRadius: radii.md, padding: spacing.sm },
  sheetFeedback: { flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: colors.success + "14", borderWidth: 1, borderColor: colors.success + "55", borderRadius: radii.md, padding: spacing.sm },
  taskSummary: { flexDirection: "row", justifyContent: "space-between", marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border },
  sectionHeading: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 },
  emptyTasks: { padding: spacing.sm, borderRadius: radii.md, backgroundColor: colors.surface },
  taskList: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden" },
  taskRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  checkbox: { width: 24, height: 24, borderRadius: 7, borderWidth: 1.5, borderColor: colors.primary, alignItems: "center", justifyContent: "center", backgroundColor: colors.card },
  checkboxDone: { backgroundColor: colors.primary, borderColor: colors.primary },
  commentCard: { padding: spacing.sm, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  commentComposer: { flexDirection: "row", alignItems: "flex-end", gap: 8, marginTop: spacing.sm },
  commentInput: { flex: 1, minHeight: 44, maxHeight: 96, textAlignVertical: "top" },
  sendButton: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary },
});
