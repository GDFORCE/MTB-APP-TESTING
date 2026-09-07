import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, View } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CalendarDays } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, spacing } from "@/src/theme/tokens";
import {
  cancelScheduleImpact, confirmScheduleImpact, getPatientAnchorStatuses,
  getPatientConditions, getPatientDeviations, getPatientSchedule,
  getPatientScheduleProjection, getScheduleProjection, getUniversalSchedule,
  previewConditionImpact,
} from "./api";
import type {
  AnchorStatusResponse, DeviationReportResponse, PatientProjection, ScheduleImpact,
} from "./api";
import {
  summariseImpact, toAnchorStatusRows, toConditionalRequirementRows,
  toConfinementRows, toImpactRows, toPatientScheduleRows, toProtocolScheduleRows,
  toRepeatRuleRows, toTimelineEntries, toVersionContext,
} from "./presentation";
import type { ConditionalRequirementRow } from "./presentation";
import {
  AnchorStatusTable, ConditionalRequirementsTable, ConfinementTable, DeviationTable,
  ImpactPreviewTable, PatientTimeline, RepeatRuleFooter, ScheduleTable,
  VersionContextHeader,
} from "./ScheduleTable";
import type {
  PatientConditionsResponse, PatientScheduleResponse, ScheduleProjection,
  UniversalSchedule,
} from "./types";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;

export default function PatientScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ patientId?: string; patient_id?: string; id?: string; patientName?: string }>();
  const [patientSchedule, setPatientSchedule] = useState<PatientScheduleResponse | null>(null);
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
  const [anchors, setAnchors] = useState<AnchorStatusResponse | null>(null);
  const [deviations, setDeviations] = useState<DeviationReportResponse | null>(null);
  const [projection, setProjection] = useState<PatientProjection | null>(null);
  const [newestVersion, setNewestVersion] = useState<number | undefined>();
  // Doc 2 / Case 3: a condition never changes a patient's schedule directly. The
  // PI or CRC records that it occurred, sees exactly what would move, and only
  // then confirms.
  const [conditions, setConditions] = useState<PatientConditionsResponse | null>(null);
  const [proposal, setProposal] = useState<ScheduleImpact | null>(null);
  const [pendingCondition, setPendingCondition] = useState<ConditionalRequirementRow | null>(null);
  const [conditionBusy, setConditionBusy] = useState(false);
  const [view, setView] = useState<"table" | "timeline">("table");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true); setError("");
    try {
      let patientId = first(params.patientId) || first(params.patient_id) || first(params.id);
      if (!patientId && __DEV__) patientId = await AsyncStorage.getItem("uctsm:last_patient_id") || undefined;
      if (!patientId) throw new Error("Select a patient to view their visit schedule.");
      const nextPatientSchedule = await getPatientSchedule(patientId);
      setPatientSchedule(nextPatientSchedule);
      if (nextPatientSchedule.schedule_version_id) {
        const [nextSchedule, nextProjections] = await Promise.all([
          getUniversalSchedule(nextPatientSchedule.schedule_version_id),
          getScheduleProjection(nextPatientSchedule.schedule_version_id),
        ]);
        setSchedule(nextSchedule); setProjections(nextProjections);
      }
      // Anchor status and deviations are derived views. A failure in either must
      // not hide the schedule itself, which is what the site actually needs.
      const [nextAnchors, nextDeviations, nextProjection, nextConditions] =
        await Promise.allSettled([
          getPatientAnchorStatuses(patientId),
          getPatientDeviations(patientId),
          getPatientScheduleProjection(patientId),
          getPatientConditions(patientId),
        ]);
      setAnchors(nextAnchors.status === "fulfilled" ? nextAnchors.value : null);
      setDeviations(nextDeviations.status === "fulfilled" ? nextDeviations.value : null);
      setProjection(nextProjection.status === "fulfilled" ? nextProjection.value : null);
      setConditions(nextConditions.status === "fulfilled" ? nextConditions.value : null);
    } catch (nextError: any) {
      setError(nextError?.response?.data?.detail || nextError?.message || "The patient schedule could not be loaded.");
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [params.patientId, params.patient_id, params.id]);

  const rows = useMemo(() => {
    if (!patientSchedule || !schedule) return [];
    return toPatientScheduleRows(patientSchedule, toProtocolScheduleRows(schedule, projections));
  }, [patientSchedule, schedule, projections]);

  const repeatRules = useMemo(
    () => toRepeatRuleRows(patientSchedule?.repeat_rules), [patientSchedule]);
  const anchorRows = useMemo(
    () => toAnchorStatusRows(anchors?.anchors), [anchors]);
  const reportedDeviations = useMemo(
    () => (deviations?.visits || []).filter(
      (item) => item.deviation_type !== "NONE" && item.deviation_type !== "NOT_ASSESSABLE"),
    [deviations],
  );
  const versionContext = useMemo(
    () => toVersionContext(
      schedule ? {
        name: schedule.schedule_metadata?.name,
        version_number: schedule.schedule_metadata?.version_number,
        status: schedule.schedule_metadata?.status,
        effective_from: (schedule.schedule_metadata as { effective_from?: string })
          ?.effective_from,
      } : undefined,
      { newestVersionNumber: newestVersion },
    ),
    [schedule, newestVersion],
  );
  const timeline = useMemo(() => toTimelineEntries(rows), [rows]);
  const confinements = useMemo(
    () => toConfinementRows(projection?.confinements), [projection]);
  const conditionRows = useMemo(
    () => toConditionalRequirementRows(conditions?.conditions || []), [conditions]);
  const impactRows = useMemo(
    () => toImpactRows(proposal?.impact?.events || []), [proposal]);

  const horizon = () => {
    const next = new Date();
    next.setFullYear(next.getFullYear() + 1);
    return next.toISOString().slice(0, 10);
  };

  /** Ask what would change. Applies nothing - that needs a separate confirmation. */
  const previewCondition = async (row: ConditionalRequirementRow) => {
    const patientId = patientSchedule?.patient_id;
    if (!patientId || conditionBusy) return;
    setConditionBusy(true); setError("");
    try {
      setProposal(await previewConditionImpact(
        patientId,
        {
          condition_code: row.id,
          state: "ACTIVE",
          occurrence_date: new Date().toISOString().slice(0, 10),
        },
        horizon(),
        `${row.condition} was recorded as having occurred`,
      ));
      setPendingCondition(row);
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail || nextError?.message
        || "The schedule impact of this condition could not be calculated.");
    } finally { setConditionBusy(false); }
  };

  const applyProposal = async () => {
    if (!proposal || conditionBusy) return;
    setConditionBusy(true); setError("");
    try {
      await confirmScheduleImpact(
        proposal.id, `${pendingCondition?.condition || "Condition"} confirmed by reviewer`);
      setProposal(null); setPendingCondition(null);
      await load();
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail || nextError?.message
        || "The schedule change could not be applied.");
    } finally { setConditionBusy(false); }
  };

  const discardProposal = async () => {
    if (!proposal || conditionBusy) return;
    setConditionBusy(true);
    try {
      await cancelScheduleImpact(proposal.id, "Reviewer cancelled before applying");
    } catch {
      // The proposal expires on its own; failing to cancel must not leave the
      // reviewer stuck looking at a change they already decided against.
    } finally {
      setProposal(null); setPendingCondition(null); setConditionBusy(false);
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Clinical" title="Patient Visit Schedule" />
      {loading ? <View style={styles.center}><ActivityIndicator color={colors.primary} /><Small style={{ marginTop: 10 }}>Loading patient visits…</Small></View> : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {error ? (
            <Card style={styles.empty}>
              <AlertTriangle size={30} color={colors.warning} />
              <Body weight="700" style={{ marginTop: 10 }}>Schedule unavailable</Body>
              <Small style={styles.message}>{error}</Small>
              <Button onPress={() => router.push("/(app)/clinical/patients" as never)} variant="secondary" style={styles.button}>Choose patient</Button>
            </Card>
          ) : patientSchedule?.status === "NOT_EVALUATED" ? (
            <Card style={styles.empty}>
              <CalendarDays size={30} color={colors.primary} />
              <Body weight="700" style={{ marginTop: 10 }}>Schedule not evaluated yet</Body>
              <Small style={styles.message}>Required clinical information must be recorded before the approved schedule can produce dated visits.</Small>
            </Card>
          ) : (
            <>
              <Card>
                <Small>PATIENT</Small>
                <Body weight="700" style={styles.title}>{first(params.patientName) || "Patient visit plan"}</Body>
                <Small>{rows.length} scheduled, pending or non-applicable events · Dates are supplied by the UCTSM evaluator.</Small>
              </Card>

              {/* Doc 1 s3 and s22: an undated visit is explained by the anchor it
                  waits on, so the schedule never just looks broken. */}
              {!!anchorRows.length && (
                <>
                  <View>
                    <Body weight="700" style={styles.sectionTitle}>Reference Dates</Body>
                    <Small>What each protocol anchor is waiting on for this patient</Small>
                  </View>
                  <AnchorStatusTable rows={anchorRows} />
                </>
              )}

              {/* Doc 9 s12-13: which version produced these dates. Two patients
                  on one trial showing different dates is the protocol working as
                  written, and this is what says so. */}
              <VersionContextHeader context={versionContext} />

              <View style={styles.sectionRow}>
                <View style={{ flex: 1 }}>
                  <Body weight="700" style={styles.sectionTitle}>
                    {view === "table" ? "Visit Schedule" : "Patient Timeline"}
                  </Body>
                  <Small>
                    {view === "table"
                      ? "Expected dates and allowed windows"
                      : "Visits in the order they happen"}
                  </Small>
                </View>
                <Button
                  variant="secondary"
                  onPress={() => setView(view === "table" ? "timeline" : "table")}>
                  {view === "table" ? "Timeline" : "Table"}
                </Button>
              </View>

              {view === "table" ? (
                <>
                  <ScheduleTable rows={rows} patient />
                  <RepeatRuleFooter rows={repeatRules} />
                </>
              ) : (
                <PatientTimeline entries={timeline} />
              )}

              {/* Doc 6 s17/s31: one stay, expandable to its study days. A row per
                  day would read as several separate visits. */}
              <ConfinementTable rows={confinements} />

              {/* Case 3. Inactive conditions carry no date, no reminder and no
                  overdue state - they are requirements, not visits. */}
              <ConditionalRequirementsTable
                rows={conditionRows}
                onConfirm={(row) => void previewCondition(row)}
              />
              {!!proposal && (
                <Card>
                  <Body weight="700" style={styles.sectionTitle}>Schedule Impact</Body>
                  <Small>
                    {pendingCondition
                      ? `If ${pendingCondition.condition} is confirmed: `
                      : ""}
                    {summariseImpact(impactRows)}
                  </Small>
                  <ImpactPreviewTable rows={impactRows} />
                  <View style={styles.proposalActions}>
                    <Button
                      variant="secondary"
                      disabled={conditionBusy}
                      onPress={() => void discardProposal()}>
                      Cancel
                    </Button>
                    <Button
                      testID="confirm-condition-impact"
                      disabled={conditionBusy}
                      onPress={() => void applyProposal()}>
                      {conditionBusy ? "Applying…" : "Confirm change"}
                    </Button>
                  </View>
                </Card>
              )}

              {/* Doc 1 s18 and s26: deviation is computed from the CURRENT expected
                  date, so a corrected anchor changes what is reported here. */}
              {!!reportedDeviations.length && (
                <>
                  <View>
                    <Body weight="700" style={styles.sectionTitle}>Protocol Deviations</Body>
                    <Small>Recalculated against this patient's pinned schedule version</Small>
                  </View>
                  <DeviationTable rows={reportedDeviations} />
                </>
              )}
            </>
          )}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 15 },
  empty: { alignItems: "center", paddingVertical: 30 },
  message: { textAlign: "center", marginTop: 6, maxWidth: 420 },
  button: { width: "100%", marginTop: 18 },
  title: { fontSize: 18, marginVertical: 4 },
  sectionTitle: { fontSize: 20 },
  sectionRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  proposalActions: { flexDirection: "row", gap: 10, marginTop: 12 },
});
