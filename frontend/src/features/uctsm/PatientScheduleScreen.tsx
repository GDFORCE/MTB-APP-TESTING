import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, View } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CalendarDays } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, spacing } from "@/src/theme/tokens";
import { getPatientSchedule, getScheduleProjection, getUniversalSchedule } from "./api";
import { toPatientScheduleRows, toProtocolScheduleRows } from "./presentation";
import { ScheduleTable } from "./ScheduleTable";
import type { PatientScheduleResponse, ScheduleProjection, UniversalSchedule } from "./types";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;

export default function PatientScheduleScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ patientId?: string; patient_id?: string; id?: string; patientName?: string }>();
  const [patientSchedule, setPatientSchedule] = useState<PatientScheduleResponse | null>(null);
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [projections, setProjections] = useState<ScheduleProjection[]>([]);
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
    } catch (nextError: any) {
      setError(nextError?.response?.data?.detail || nextError?.message || "The patient schedule could not be loaded.");
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [params.patientId, params.patient_id, params.id]);

  const rows = useMemo(() => {
    if (!patientSchedule || !schedule) return [];
    return toPatientScheduleRows(patientSchedule, toProtocolScheduleRows(schedule, projections));
  }, [patientSchedule, schedule, projections]);

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
              <View><Body weight="700" style={styles.sectionTitle}>Visit Schedule</Body><Small>Expected dates and allowed windows</Small></View>
              <ScheduleTable rows={rows} patient />
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
});
