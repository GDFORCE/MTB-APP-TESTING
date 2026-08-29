import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CheckCircle2, ClipboardCheck, FlaskConical, RefreshCw, ShieldCheck } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  decideSchedule, getApprovedSchedules, getScheduleProjection, getUniversalSchedule,
  recordFieldDecision, submitScheduleReview, validateSchedule,
} from "./api";
import { toProtocolScheduleRows, type ProtocolScheduleRow } from "./presentation";
import { ScheduleTable } from "./ScheduleTable";
import type { Evidence, ScheduleProjection, UniversalSchedule } from "./types";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
const errorText = (error: any) => error?.response?.data?.detail || error?.message || "The schedule could not be loaded.";

export default function ProtocolScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    id?: string; scheduleVersionId?: string; schedule_version_id?: string;
    trialName?: string; protocolVersion?: string;
  }>();
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
  const [busy, setBusy] = useState("load");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadVersion = async (versionId: string) => {
    const [nextSchedule, nextProjections] = await Promise.all([
      getUniversalSchedule(versionId), getScheduleProjection(versionId),
    ]);
    setSchedule(nextSchedule);
    setProjections(nextProjections);
  };

  const resolveVersion = async () => {
    setBusy("load");
    setError("");
    try {
      let versionId = first(params.scheduleVersionId) || first(params.schedule_version_id);
      const trialId = first(params.id);
      if (!versionId && trialId) {
        try {
          const approved = await getApprovedSchedules(trialId);
          versionId = approved[0]?.schedule_version_id;
        } catch {
          // A legacy trial identifier has no UCTSM schedule mapping yet.
        }
      }
      if (!versionId && __DEV__) versionId = await AsyncStorage.getItem("uctsm:last_schedule_version_id") || undefined;
      if (!versionId) throw new Error("No UCTSM schedule version is linked to this trial yet.");
      await loadVersion(versionId);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setBusy("");
    }
  };

  useEffect(() => { void resolveVersion(); }, [params.id, params.scheduleVersionId, params.schedule_version_id]);

  const rows = useMemo(
    () => schedule ? toProtocolScheduleRows(schedule, projections) : [],
    [schedule, projections],
  );
  const openIssues = schedule?.validation_issues.filter((issue) => issue.status === "OPEN") || [];
  const blocking = openIssues.filter((issue) => issue.blocking).length;

  const run = async (label: string, action: () => Promise<void>) => {
    if (!schedule || busy) return;
    setBusy(label); setError(""); setMessage("");
    try { await action(); await loadVersion(schedule.schedule_version_id); }
    catch (nextError) { setError(errorText(nextError)); }
    finally { setBusy(""); }
  };

  const validate = () => run("validate", async () => {
    const result = await validateSchedule(schedule!.schedule_version_id);
    setMessage(result.blocking_issues ? `${result.blocking_issues} item(s) require correction.` : "Schedule structure is valid.");
  });
  const submit = () => run("submit", async () => {
    await submitScheduleReview(schedule!.schedule_version_id);
    setMessage("Schedule submitted for clinical review.");
  });
  const confirmFields = () => run("confirm", async () => {
    for (const event of schedule!.events) {
      const fields = ["display_name", "timing"];
      if (event.conditions.length) fields.push("conditions");
      if (event.applicability.length) fields.push("applicability");
      if (event.recurrence) fields.push("recurrence");
      if (event.activities.length) fields.push("activities");
      for (const field_path of fields) {
        await recordFieldDecision(schedule!.schedule_version_id, {
          decision: "CONFIRM", entity_type: "EVENT", entity_id: event.id, field_path,
          comment: "Confirmed from the human-readable protocol schedule.",
        });
      }
    }
    setMessage("All required schedule fields have been confirmed.");
  });
  const approve = () => run("approve", async () => {
    await decideSchedule(schedule!.schedule_version_id, "APPROVE", "Approved from protocol schedule review.");
    setMessage("Schedule approved and frozen as an immutable version.");
  });

  const openEvidence = (row: ProtocolScheduleRow) => {
    const evidence = schedule?.evidence.filter((item) => row.evidenceRefs.includes(item.id)) || [];
    if (!evidence.length) return Alert.alert("Protocol evidence", "No linked evidence is available for this event.");
    const text = evidence.map(formatEvidence).join("\n\n──────────\n\n");
    Alert.alert(`${row.visit} · ${row.visitName}`, text);
  };

  if (busy === "load") return <ScreenContainer><ScreenHeader eyebrow="Protocol" title="Schedule" /><View style={styles.center}><ActivityIndicator color={colors.primary} /><Small style={{ marginTop: 10 }}>Loading approved schedule…</Small></View></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Protocol schedule" title="Schedule Review" />
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {error && !schedule ? (
          <Card style={styles.emptyCard}>
            <AlertTriangle size={30} color={colors.warning} />
            <Body weight="700" style={styles.emptyTitle}>Schedule not available</Body>
            <Small style={styles.centerText}>{error}</Small>
            <Button onPress={resolveVersion} variant="secondary" style={styles.wideButton}>Try again</Button>
            {__DEV__ && <Pressable onPress={() => router.push("/(app)/dev/uctsm-workbench" as never)} style={styles.devLink}><FlaskConical size={14} color={colors.primary} /><Text style={styles.devText}>Open development workbench</Text></Pressable>}
          </Card>
        ) : schedule ? (
          <>
            <View style={styles.summaryRow}>
              <Card style={styles.summaryMain}>
                <Small>TRIAL / PROTOCOL</Small>
                <Body weight="700" style={styles.summaryTitle}>{first(params.trialName) || schedule.schedule_metadata.name}</Body>
                <Small>{first(params.protocolVersion) || `Protocol schedule version ${schedule.schedule_metadata.version_number}`}</Small>
              </Card>
              <Card style={styles.statusCard}>
                <Small>STATUS</Small>
                <View style={[styles.statusPill, schedule.schedule_metadata.status === "APPROVED" && styles.approvedPill]}>
                  {schedule.schedule_metadata.status === "APPROVED" ? <ShieldCheck size={14} color={colors.success} /> : <RefreshCw size={14} color={colors.warning} />}
                  <Text style={[styles.statusText, schedule.schedule_metadata.status === "APPROVED" && styles.approvedText]}>{schedule.schedule_metadata.status.replace(/_/g, " ")}</Text>
                </View>
              </Card>
            </View>

            {!!error && <Notice error text={error} />}
            {!!message && <Notice text={message} />}

            <Card style={styles.validationCard}>
              <View style={styles.validationHead}>
                {blocking ? <AlertTriangle size={21} color={colors.warning} /> : <CheckCircle2 size={21} color={colors.success} />}
                <View style={{ flex: 1 }}>
                  <Body weight="700">Validation</Body>
                  <Small>{openIssues.length ? `${openIssues.length} item(s) require attention; ${blocking} block approval.` : "No open validation issues."}</Small>
                </View>
              </View>
              {openIssues.slice(0, 5).map((issue) => <View key={issue.id} style={styles.issue}><Text style={styles.issueText}>{issue.message}</Text></View>)}
            </Card>

            <View style={styles.sectionHead}>
              <View><Body weight="700" style={styles.sectionTitle}>Protocol Schedule</Body><Small>{rows.length} visits and protocol events</Small></View>
              {schedule.schedule_metadata.status !== "APPROVED" && <Button onPress={validate} loading={busy === "validate"} variant="secondary" style={styles.smallButton}>Validate</Button>}
            </View>
            <ScheduleTable rows={rows} onEvidence={openEvidence} />

            {schedule.schedule_metadata.status !== "APPROVED" && (
              <Card style={styles.reviewCard}>
                <View style={styles.reviewTitle}><ClipboardCheck size={20} color={colors.primary} /><View style={{ flex: 1 }}><Body weight="700">Review and approval</Body><Small>Review the table and evidence before approving this version.</Small></View></View>
                <View style={styles.actions}>
                  {["EXTRACTED", "VALIDATION_REQUIRED"].includes(schedule.schedule_metadata.status) && <Button onPress={submit} loading={busy === "submit"} style={styles.actionButton}>Submit for review</Button>}
                  {schedule.schedule_metadata.status === "IN_REVIEW" && <Button onPress={confirmFields} loading={busy === "confirm"} variant="secondary" style={styles.actionButton}>Confirm reviewed fields</Button>}
                  {schedule.schedule_metadata.status === "IN_REVIEW" && <Button onPress={approve} loading={busy === "approve"} disabled={blocking > 0} style={styles.actionButton}>Approve schedule</Button>}
                </View>
              </Card>
            )}
          </>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

function formatEvidence(item: Evidence) {
  return [
    item.page_number ? `Page ${item.page_number}` : null,
    item.section_title ? `Section: ${item.section_title}` : null,
    item.table_title ? `Table: ${item.table_title}` : null,
    item.source_text || null,
  ].filter(Boolean).join("\n");
}
function Notice({ text, error = false }: { text: string; error?: boolean }) { return <View style={[styles.notice, error && styles.errorNotice]}>{error ? <AlertTriangle size={16} color={colors.destructive} /> : <CheckCircle2 size={16} color={colors.success} />}<Small color={error ? colors.destructive : colors.success} style={{ flex: 1 }}>{text}</Small></View>; }

const styles = StyleSheet.create({
  content: { padding: spacing.md, paddingBottom: 50, gap: 14 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  centerText: { textAlign: "center", marginTop: 6 },
  emptyCard: { alignItems: "center", paddingVertical: 30 },
  emptyTitle: { marginTop: 10 },
  wideButton: { width: "100%", marginTop: 18 },
  devLink: { flexDirection: "row", gap: 6, alignItems: "center", marginTop: 18 },
  devText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  summaryRow: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  summaryMain: { flex: 1, minWidth: 230 },
  summaryTitle: { marginVertical: 4, fontSize: 18 },
  statusCard: { minWidth: 175 },
  statusPill: { marginTop: 8, paddingHorizontal: 10, paddingVertical: 7, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "#FFF0D6", alignSelf: "flex-start" },
  approvedPill: { backgroundColor: "#E7F4EA" },
  statusText: { color: "#855416", fontFamily: fonts.semibold, fontSize: 11 },
  approvedText: { color: "#326A43" },
  validationCard: { gap: 9 },
  validationHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  issue: { padding: 9, borderRadius: radii.sm, backgroundColor: "#FFF7E8" },
  issueText: { color: "#754A16", fontFamily: fonts.regular, fontSize: 12 },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10, marginTop: 5 },
  sectionTitle: { fontSize: 20 },
  smallButton: { minWidth: 110, paddingHorizontal: 12 },
  reviewCard: { gap: 13 },
  reviewTitle: { flexDirection: "row", alignItems: "center", gap: 10 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  actionButton: { minWidth: 180, flexGrow: 1 },
  notice: { flexDirection: "row", alignItems: "center", gap: 8, padding: 11, borderWidth: 1, borderColor: "#BFDCC6", backgroundColor: "#EFF8F1", borderRadius: radii.md },
  errorNotice: { borderColor: "#EBC0BB", backgroundColor: "#FFF1EF" },
});
