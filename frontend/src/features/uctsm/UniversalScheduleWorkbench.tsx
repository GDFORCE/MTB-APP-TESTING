import React, { useMemo, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { ArrowLeft, CalendarDays, CheckCircle2, FlaskConical, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { Body, Small } from "@/src/components/ui";
import { colors, radii, shadows, spacing } from "@/src/theme/tokens";
import {
  decideSchedule, evaluatePatientSchedule, getScheduleProjection, getUniversalSchedule,
  recordFieldDecision, recordPatientAnchor, recordPatientState, seedDemoWorkspace,
  submitScheduleReview, validateSchedule,
} from "./api";
import { ScheduleReviewCard } from "./ScheduleReviewCard";
import type { Evidence, ScheduleProjection, UniversalSchedule } from "./types";

type PatientResult = {
  evaluation_id: string;
  events: Array<{
    id: string; status: string; nominal_start_date?: string;
    earliest_date?: string; latest_date?: string; explanation?: Record<string, unknown>;
  }>;
};

const errorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback;

export default function UniversalScheduleWorkbench() {
  const router = useRouter();
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
  const [patientId, setPatientId] = useState("");
  const [lastDose, setLastDose] = useState("2026-12-15");
  const [progression, setProgression] = useState("");
  const [patientResult, setPatientResult] = useState<PatientResult | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});

  const evidence = useMemo(
    () => new Map((schedule?.evidence || []).map((item) => [item.id, item])),
    [schedule?.evidence],
  );
  const blocking = schedule?.validation_issues.filter(
    (item) => item.blocking && item.status === "OPEN",
  ).length || 0;

  const refresh = async (versionId: string) => {
    const [nextSchedule, nextProjection] = await Promise.all([
      getUniversalSchedule(versionId), getScheduleProjection(versionId),
    ]);
    setSchedule(nextSchedule);
    setProjections(nextProjection);
  };

  const run = async (label: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(label);
    setError("");
    setNotice("");
    try { await action(); }
    catch (nextError: any) { setError(errorMessage(nextError, "The test action failed.")); }
    finally { setBusy(""); }
  };

  const loadDemo = () => run("seed", async () => {
    const workspace = await seedDemoWorkspace();
    setPatientId(workspace.patient_id);
    await refresh(workspace.schedule_version_id);
    setNotice("Test workspace loaded. Start with deterministic validation.");
  });

  const validate = () => schedule && run("validate", async () => {
    const result = await validateSchedule(schedule.schedule_version_id);
    await refresh(schedule.schedule_version_id);
    setNotice(result.blocking_issues
      ? `Validation found ${result.blocking_issues} blocking issue(s).`
      : "Validation passed with no blocking issues.");
  });

  const submit = () => schedule && run("submit", async () => {
    await submitScheduleReview(schedule.schedule_version_id);
    await refresh(schedule.schedule_version_id);
    setNotice("Schedule is now in human review.");
  });

  const confirmField = (eventId: string, fieldPath: string) => schedule && run(`${eventId}:${fieldPath}`, async () => {
    await recordFieldDecision(schedule.schedule_version_id, {
      decision: "CONFIRM", entity_type: "EVENT", entity_id: eventId,
      field_path: fieldPath, comment: "Confirmed in UCTSM interactive test.",
    });
    setConfirmed((current) => ({ ...current, [`${eventId}:${fieldPath}`]: true }));
    setNotice(`${fieldPath.replace(/_/g, " ")} confirmed.`);
  });

  const confirmAll = () => schedule && run("confirm-all", async () => {
    for (const event of schedule.events) {
      const fields = ["display_name", "timing"];
      if (event.conditions.length) fields.push("conditions");
      if (event.applicability.length) fields.push("applicability");
      if (event.recurrence) fields.push("recurrence");
      if (event.activities.length) fields.push("activities");
      for (const fieldPath of fields) {
        await recordFieldDecision(schedule.schedule_version_id, {
          decision: "CONFIRM", entity_type: "EVENT", entity_id: event.id,
          field_path: fieldPath, comment: "Confirmed in UCTSM interactive test.",
        });
        setConfirmed((current) => ({ ...current, [`${event.id}:${fieldPath}`]: true }));
      }
    }
    setNotice("All required event fields are confirmed. You can approve the version.");
  });

  const approve = () => schedule && run("approve", async () => {
    await decideSchedule(schedule.schedule_version_id, "APPROVE", "Approved through the UCTSM interactive test workflow.");
    await refresh(schedule.schedule_version_id);
    setNotice("Approved and frozen. Patient scheduling is now enabled.");
  });

  const evaluate = () => schedule && run("evaluate", async () => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(lastDose)) throw new Error("Enter Last Dose as YYYY-MM-DD.");
    const lastDoseAnchor = schedule.anchors.find((item) => item.code === "LAST_DOSE");
    const progressionAnchor = schedule.anchors.find((item) => item.code === "PROGRESSION");
    if (!lastDoseAnchor) throw new Error("The test schedule has no LAST_DOSE anchor.");
    await recordPatientAnchor(patientId, lastDoseAnchor.id, lastDose);
    if (progression) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(progression)) throw new Error("Enter Progression as YYYY-MM-DD or leave it blank.");
      if (!progressionAnchor) throw new Error("The test schedule has no PROGRESSION anchor.");
      await recordPatientAnchor(patientId, progressionAnchor.id, progression);
      await recordPatientState(patientId, "progression", true);
    }
    const result = await evaluatePatientSchedule(patientId, "2028-12-31", `uctsm-ui-${Date.now()}`);
    setPatientResult(result as PatientResult);
    setNotice("Patient schedule evaluated deterministically and saved as a new evaluation.");
  });

  const openEvidence = (item: Evidence) => Alert.alert(
    `Protocol evidence · Page ${item.page_number || "?"}`,
    [item.section_title, item.source_text].filter(Boolean).join("\n\n") || "No source text available.",
  );

  return (
    <View style={styles.page}>
      <SafeAreaView edges={["top"]} style={styles.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}><ArrowLeft size={21} color={colors.white} /></Pressable>
        <View style={{ flex: 1 }}>
          <Body color={colors.white} weight="700">Universal Schedule Test</Body>
          <Small color="rgba(255,255,255,0.72)">Evidence → review → patient dates</Small>
        </View>
        <FlaskConical size={22} color="#F5C7D2" />
      </SafeAreaView>

      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.intro}>
          <View style={styles.introIcon}><ShieldCheck size={24} color={colors.primary} /></View>
          <View style={{ flex: 1 }}>
            <Body weight="700">Safe interactive test data</Body>
            <Small style={{ marginTop: 3 }}>This workspace is isolated from existing trials. Test evidence review, immutable approval, anchors, conditions, windows, and patient evaluation.</Small>
          </View>
        </View>

        {!schedule ? (
          <ActionButton label="Load UCTSM test workspace" loading={busy === "seed"} icon={<FlaskConical size={17} color={colors.white} />} onPress={loadDemo} />
        ) : (
          <>
            <View style={styles.statusCard}>
              <View style={styles.statusRow}>
                <View style={{ flex: 1 }}>
                  <Small>Schedule version</Small>
                  <Body weight="700">{schedule.schedule_metadata.name} · v{schedule.schedule_metadata.version_number}</Body>
                </View>
                <View style={[styles.pill, schedule.schedule_metadata.status === "APPROVED" && styles.approvedPill]}>
                  <Small weight="700" color={schedule.schedule_metadata.status === "APPROVED" ? colors.success : colors.warning}>{schedule.schedule_metadata.status.replace(/_/g, " ")}</Small>
                </View>
              </View>
              <Small style={{ marginTop: 8 }}>{schedule.events.length} events · {schedule.evidence.length} evidence source · {blocking} blocking issues</Small>
            </View>

            {!!error && <Message tone="error" text={error} />}
            {!!notice && <Message tone="success" text={notice} />}

            {schedule.schedule_metadata.status !== "APPROVED" && (
              <View style={styles.workflowCard}>
                <Body weight="700">Review workflow</Body>
                <View style={styles.actionGrid}>
                  <MiniAction label="1. Validate" active={busy === "validate"} onPress={validate} />
                  <MiniAction label="2. Submit review" active={busy === "submit"} onPress={submit} />
                  <MiniAction label="3. Confirm fields" active={busy === "confirm-all"} onPress={confirmAll} />
                  <MiniAction label="4. Approve & freeze" active={busy === "approve"} onPress={approve} />
                </View>
                <Small style={{ marginTop: 8 }}>Follow the numbered order. Approval fails safely if validation or field review is incomplete.</Small>
              </View>
            )}

            <View style={styles.sectionHead}><Body weight="700">Canonical events</Body><Small>Tap evidence to inspect source</Small></View>
            <View style={styles.eventList}>
              {schedule.events.map((event) => {
                const projection = projections.find((item) => item.event_id === event.id);
                if (!projection) return null;
                return (
                  <View key={event.id}>
                    <ScheduleReviewCard
                      event={event} projection={projection}
                      evidence={event.evidence_refs.map((id) => evidence.get(id)).filter(Boolean) as Evidence[]}
                      issues={schedule.validation_issues} onOpenEvidence={openEvidence}
                      onReviewField={(field) => confirmField(event.id, field)}
                    />
                    {(confirmed[`${event.id}:display_name`] || confirmed[`${event.id}:timing`]) && (
                      <View style={styles.confirmedLine}><CheckCircle2 size={13} color={colors.success} /><Small color={colors.success}>Field decision recorded</Small></View>
                    )}
                  </View>
                );
              })}
            </View>

            {schedule.schedule_metadata.status === "APPROVED" && (
              <View style={styles.patientCard}>
                <View style={styles.sectionTitleRow}><CalendarDays size={19} color={colors.primary} /><Body weight="700">Patient schedule test · DEMO-P001</Body></View>
                <Small style={{ marginTop: 4 }}>Last Dose drives safety follow-up. Leave Progression blank to test a pending condition.</Small>
                <Small weight="700" style={styles.fieldLabel}>Last Dose (YYYY-MM-DD)</Small>
                <TextInput value={lastDose} onChangeText={setLastDose} style={styles.input} placeholder="2026-12-15" />
                <Small weight="700" style={styles.fieldLabel}>Progression date — optional</Small>
                <TextInput value={progression} onChangeText={setProgression} style={styles.input} placeholder="Leave blank to test waiting state" />
                <ActionButton label="Evaluate patient schedule" loading={busy === "evaluate"} icon={<RefreshCw size={17} color={colors.white} />} onPress={evaluate} />
              </View>
            )}

            {!!patientResult && (
              <View style={styles.resultsCard}>
                <Body weight="700">Patient evaluation result</Body>
                <Small style={{ marginTop: 3 }}>Evaluation {patientResult.evaluation_id.slice(0, 8)}…</Small>
                {patientResult.events.map((event) => (
                  <View key={event.id} style={styles.resultRow}>
                    <View style={styles.resultTop}>
                      <Body weight="700">{String(event.explanation?.rule || "Patient event")}</Body>
                      <Small weight="700" color={event.status === "RESOLVED" ? colors.success : colors.warning}>{event.status.replace(/_/g, " ")}</Small>
                    </View>
                    {event.nominal_start_date && <Small>Expected: {event.nominal_start_date}</Small>}
                    {(event.earliest_date || event.latest_date) && <Small>Allowed: {event.earliest_date || "—"} to {event.latest_date || "—"}</Small>}
                    {!event.nominal_start_date && !event.earliest_date && <Small>No date invented; required patient information is pending.</Small>}
                  </View>
                ))}
              </View>
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

function ActionButton({ label, loading, icon, onPress }: { label: string; loading: boolean; icon: React.ReactNode; onPress: () => void }) {
  return <Pressable disabled={loading} onPress={onPress} style={[styles.primaryButton, loading && styles.disabled]}>{loading ? <ActivityIndicator color={colors.white} /> : icon}<Body color={colors.white} weight="700">{label}</Body></Pressable>;
}

function MiniAction({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return <Pressable disabled={active} onPress={onPress} style={styles.miniButton}>{active ? <ActivityIndicator size="small" color={colors.primary} /> : <Small weight="700" color={colors.primary}>{label}</Small>}</Pressable>;
}

function Message({ tone, text }: { tone: "error" | "success"; text: string }) {
  const color = tone === "error" ? colors.destructive : colors.success;
  return <View style={[styles.message, { borderColor: `${color}55`, backgroundColor: `${color}10` }]}>{tone === "error" ? <TriangleAlert size={16} color={color} /> : <CheckCircle2 size={16} color={color} />}<Small color={color} style={{ flex: 1 }}>{text}</Small></View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  header: { minHeight: 78, paddingHorizontal: spacing.md, paddingBottom: 13, backgroundColor: colors.primary, flexDirection: "row", alignItems: "center", gap: 12 },
  content: { padding: spacing.md, paddingBottom: 50, gap: 13 },
  intro: { padding: 15, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "25", backgroundColor: colors.card, flexDirection: "row", gap: 12, ...shadows.sm },
  introIcon: { width: 44, height: 44, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "12" },
  primaryButton: { minHeight: 48, borderRadius: radii.md, backgroundColor: colors.primary, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 },
  disabled: { opacity: 0.6 },
  statusCard: { padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  statusRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  pill: { paddingHorizontal: 9, paddingVertical: 6, borderRadius: radii.pill, backgroundColor: colors.warning + "14" },
  approvedPill: { backgroundColor: colors.success + "12" },
  workflowCard: { padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  actionGrid: { marginTop: 10, flexDirection: "row", flexWrap: "wrap", gap: 8 },
  miniButton: { minHeight: 40, minWidth: "46%", flexGrow: 1, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "45", backgroundColor: colors.primary + "08", alignItems: "center", justifyContent: "center", paddingHorizontal: 8 },
  sectionHead: { marginTop: 4, flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", gap: 8 },
  eventList: { gap: 12 },
  confirmedLine: { marginTop: 5, marginLeft: 8, flexDirection: "row", alignItems: "center", gap: 5 },
  message: { padding: 11, borderRadius: radii.md, borderWidth: 1, flexDirection: "row", alignItems: "flex-start", gap: 8 },
  patientCard: { padding: 15, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "30", backgroundColor: colors.card, gap: 8 },
  sectionTitleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  fieldLabel: { marginTop: 5 },
  input: { minHeight: 44, paddingHorizontal: 12, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, color: colors.foreground },
  resultsCard: { padding: 15, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.success + "35", backgroundColor: colors.card },
  resultRow: { marginTop: 10, padding: 11, borderRadius: radii.md, backgroundColor: colors.surface, gap: 3 },
  resultTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
});

