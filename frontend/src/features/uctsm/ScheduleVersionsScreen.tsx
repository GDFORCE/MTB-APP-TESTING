import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, ChevronRight } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { buildCanonicalSchedule, getCanonicalSchedules } from "./api";
import type { CanonicalScheduleIndex } from "./api";
import { toEnrolmentReadiness, toScheduleVersionRows } from "./presentation";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;
const errorText = (error: any) =>
  error?.response?.data?.detail || error?.message
  || "The schedule versions could not be loaded.";

/**
 * Case 10 / doc 9 - the trial-level protocol and schedule version table.
 *
 * Every version is listed, superseded ones included, because a patient enrolled
 * under v1 stays on v1: hiding it would make the patients still being scheduled
 * from it unexplainable. The row that matters most - the one new enrollments
 * receive - is marked rather than merely being first.
 */
export default function ScheduleVersionsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string; trialId?: string; trialName?: string }>();
  const trialId = first(params.id) || first(params.trialId) || "";
  const [index, setIndex] = useState<CanonicalScheduleIndex | null>(null);
  const [busy, setBusy] = useState("load");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!trialId) { setError("No trial was selected."); setBusy(""); return; }
    setBusy("load"); setError("");
    try {
      setIndex(await getCanonicalSchedules(trialId));
    } catch (nextError) {
      setError(errorText(nextError));
    } finally { setBusy(""); }
  }, [trialId]);

  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(
    () => toScheduleVersionRows(index?.schedules, index?.approved_schedule_version_id),
    [index],
  );
  const readiness = useMemo(() => toEnrolmentReadiness(index?.schedules), [index]);

  const build = async () => {
    setBusy("build"); setError("");
    try {
      await buildCanonicalSchedule(trialId);
      await load();
    } catch (nextError) {
      setError(errorText(nextError));
    } finally { setBusy(""); }
  };

  const open = (versionId: string) => router.push({
    pathname: "/(app)/sponsor/protocol-schedule",
    params: { scheduleVersionId: versionId, id: trialId },
  });

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow={first(params.trialName) || "Trial"}
        title="Protocol & Schedule Versions"
      />
      {busy === "load" ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
          <Small style={{ marginTop: 10 }}>Loading schedule versions…</Small>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {!!error && (
            <Card style={styles.notice}>
              <AlertTriangle size={20} color={colors.warning} />
              <Small style={styles.noticeText}>{error}</Small>
            </Card>
          )}

          <Card>
            <Small color={colors.mutedFg} weight="700">ENROLMENT</Small>
            <Body style={{ marginTop: 4 }}>{readiness.message}</Body>
          </Card>

          {!rows.length ? (
            <Card style={styles.empty}>
              <Body weight="700">No canonical schedule yet</Body>
              <Small style={styles.emptyText}>
                Build the schedule from this trial's extracted protocol. It is
                created as a draft for review — nothing reaches a patient until
                it is approved.
              </Small>
              <Button
                testID="build-canonical-schedule"
                onPress={build}
                disabled={busy === "build"}
              >
                {busy === "build" ? "Building…" : "Build schedule from protocol"}
              </Button>
            </Card>
          ) : (
            <View style={styles.table}>
              <View style={styles.headerRow}>
                <Text style={[styles.headerText, styles.colName]}>Schedule</Text>
                <Text style={[styles.headerText, styles.colVersion]}>Protocol</Text>
                <Text style={[styles.headerText, styles.colVersion]}>Version</Text>
                <Text style={[styles.headerText, styles.colEffective]}>Effective For</Text>
                <Text style={[styles.headerText, styles.colStatus]}>Status</Text>
                <Text style={[styles.headerText, styles.colPatients]}>Patients</Text>
              </View>
              {rows.map((row, position) => (
                <Pressable
                  key={row.id}
                  testID={`schedule-version-${row.id}`}
                  accessibilityRole="button"
                  accessibilityLabel={`Open ${row.name} ${row.version}`}
                  onPress={() => open(row.id)}
                  style={[
                    styles.dataRow,
                    position % 2 === 1 && styles.altRow,
                    row.current && styles.currentRow,
                  ]}
                >
                  <Text style={[styles.value, styles.name, styles.colName]}>{row.name}</Text>
                  <Text style={[styles.value, styles.colVersion]}>{row.protocolVersion}</Text>
                  <Text style={[styles.value, styles.colVersion]}>{row.version}</Text>
                  <Text style={[styles.value, styles.colEffective]}>{row.effectiveFor}</Text>
                  <Text style={[styles.value, styles.colStatus]}>{row.status}</Text>
                  <View style={[styles.patientsCell, styles.colPatients]}>
                    <Text style={styles.value}>{row.patients}</Text>
                    <ChevronRight size={15} color={colors.mutedFg} />
                  </View>
                </Pressable>
              ))}
            </View>
          )}

          {!!rows.length && (
            <Button
              testID="rebuild-canonical-schedule"
              variant="secondary"
              onPress={build}
              disabled={busy === "build"}
            >
              {busy === "build" ? "Checking…" : "Import newly extracted schedules"}
            </Button>
          )}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 14 },
  notice: { flexDirection: "row", alignItems: "center", gap: 10 },
  noticeText: { flex: 1 },
  empty: { alignItems: "flex-start", gap: 10 },
  emptyText: { marginBottom: 4 },
  table: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg,
    overflow: "hidden", backgroundColor: colors.card,
  },
  headerRow: {
    flexDirection: "row", backgroundColor: colors.primaryDeep,
    paddingHorizontal: 10, paddingVertical: 11, gap: 8,
  },
  headerText: {
    color: colors.white, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.2,
  },
  dataRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 10, paddingVertical: 13,
    borderTopWidth: 1, borderTopColor: colors.border,
  },
  altRow: { backgroundColor: "#FBF7F1" },
  currentRow: { borderLeftWidth: 3, borderLeftColor: colors.primary },
  value: { fontFamily: fonts.regular, fontSize: 12, color: colors.foreground },
  name: { fontFamily: fonts.semibold },
  patientsCell: { flexDirection: "row", alignItems: "center", gap: 4 },
  colName: { flex: 1.6 },
  colVersion: { flex: 1 },
  colEffective: { flex: 1.7 },
  colStatus: { flex: 1.1 },
  colPatients: { flex: 0.9 },
});
