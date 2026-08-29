import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { AlertCircle, FileText } from "lucide-react-native";

import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import type { PatientScheduleRow, ProtocolScheduleRow } from "./presentation";

type Props = {
  rows: Array<ProtocolScheduleRow | PatientScheduleRow>;
  patient?: boolean;
  onEvidence?: (row: ProtocolScheduleRow) => void;
};

const widths = {
  visit: 74,
  name: 190,
  timing: 250,
  window: 130,
  type: 130,
  activities: 240,
  expected: 150,
  status: 190,
};

export function ScheduleTable({ rows, patient = false, onEvidence }: Props) {
  const { width } = useWindowDimensions();
  if (!rows.length) return <View style={styles.empty}><Text style={styles.emptyTitle}>No schedule events available</Text><Text style={styles.emptyText}>This schedule does not contain any displayable events.</Text></View>;
  if (width < 760) {
    return <View style={styles.mobileList}>{rows.map((row) => <MobileRow key={row.id} row={row} patient={patient} onEvidence={onEvidence} />)}</View>;
  }
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator accessibilityLabel="Protocol schedule table">
      <View style={styles.table}>
        <View style={styles.headerRow}>
          <Header label="Visit" width={widths.visit} />
          <Header label="Visit Name" width={widths.name} />
          {patient ? <Header label="Expected Date" width={widths.expected} /> : <Header label="Timing" width={widths.timing} />}
          <Header label={patient ? "Allowed Window" : "Window"} width={widths.window} />
          <Header label="Type" width={widths.type} />
          <Header label="Activities" width={widths.activities} />
          {patient && <Header label="Status" width={widths.status} />}
        </View>
        {rows.map((row, index) => (
          <View key={row.id} style={[styles.dataRow, index % 2 === 1 && styles.altRow]}>
            <Cell width={widths.visit}><Text style={styles.visit}>{row.visit}</Text></Cell>
            <Cell width={widths.name}>
              <Text style={styles.name}>{row.visitName}</Text>
              {row.requiresReview && <View style={styles.reviewLine}><AlertCircle size={12} color={colors.warning} /><Text style={styles.reviewText}>Review required</Text></View>}
              {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
            </Cell>
            <Cell width={patient ? widths.expected : widths.timing}><Text style={styles.value}>{patient ? (row as PatientScheduleRow).expectedDate : row.timing}</Text></Cell>
            <Cell width={widths.window}><Text style={styles.value}>{patient ? (row as PatientScheduleRow).allowedWindow : row.window}</Text></Cell>
            <Cell width={widths.type}><Text style={styles.value}>{row.type}</Text></Cell>
            <Cell width={widths.activities}>{row.activities.length ? row.activities.map((activity) => <View key={activity} style={styles.bulletLine}><Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text></View>) : <Text style={styles.muted}>—</Text>}</Cell>
            {patient && <Cell width={widths.status}><Status value={(row as PatientScheduleRow).status} /></Cell>}
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function MobileRow({ row, patient, onEvidence }: { row: ProtocolScheduleRow | PatientScheduleRow; patient: boolean; onEvidence?: (row: ProtocolScheduleRow) => void }) {
  const patientRow = row as PatientScheduleRow;
  return (
    <View style={styles.mobileCard} accessibilityLabel={`${row.visit} ${row.visitName}`}>
      <View style={styles.mobileHead}>
        <View style={styles.visitBadge}><Text style={styles.visitBadgeText}>{row.visit}</Text></View>
        <View style={{ flex: 1 }}><Text style={styles.mobileName}>{row.visitName}</Text>{row.requiresReview && <Text style={styles.reviewText}>Review required</Text>}</View>
        {patient && <Status value={patientRow.status} compact />}
      </View>
      <View style={styles.mobileGrid}>
        <MobileField label={patient ? "Expected date" : "Timing"} value={patient ? patientRow.expectedDate : row.timing} />
        <MobileField label={patient ? "Allowed window" : "Window"} value={patient ? patientRow.allowedWindow : row.window} />
        <MobileField label="Type" value={row.type} />
      </View>
      <Text style={styles.mobileLabel}>Activities</Text>
      {row.activities.length ? row.activities.map((activity) => <View key={activity} style={styles.bulletLine}><Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text></View>) : <Text style={styles.muted}>No activities listed</Text>}
      {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
    </View>
  );
}

function Header({ label, width }: { label: string; width: number }) { return <View style={[styles.headerCell, { width }]}><Text style={styles.headerText}>{label}</Text></View>; }
function Cell({ width, children }: { width: number; children: React.ReactNode }) { return <View style={[styles.cell, { width }]}>{children}</View>; }
function MobileField({ label, value }: { label: string; value: string }) { return <View style={styles.mobileField}><Text style={styles.mobileLabel}>{label}</Text><Text style={styles.value}>{value}</Text></View>; }
function EvidenceButton({ onPress }: { onPress: () => void }) { return <Pressable onPress={onPress} style={styles.evidence}><FileText size={13} color={colors.primary} /><Text style={styles.evidenceText}>View source</Text></Pressable>; }
function Status({ value, compact = false }: { value: string; compact?: boolean }) {
  const pending = value.startsWith("Waiting") || value === "Review required";
  const inactive = value === "Not applicable";
  return <View style={[styles.status, compact && styles.statusCompact, pending && styles.statusPending, inactive && styles.statusInactive]}><Text style={[styles.statusText, pending && styles.statusPendingText, inactive && styles.statusInactiveText]}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  table: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden", backgroundColor: colors.card },
  headerRow: { flexDirection: "row", backgroundColor: colors.primaryDeep },
  headerCell: { minHeight: 48, paddingHorizontal: 12, justifyContent: "center", borderRightWidth: 1, borderRightColor: "rgba(255,255,255,0.12)" },
  headerText: { color: colors.white, fontFamily: fonts.semibold, fontSize: 12, letterSpacing: 0.2 },
  dataRow: { flexDirection: "row", minHeight: 78, backgroundColor: colors.card, borderTopWidth: 1, borderTopColor: colors.border },
  altRow: { backgroundColor: "#FBF7F1" },
  cell: { paddingHorizontal: 12, paddingVertical: 13, borderRightWidth: 1, borderRightColor: colors.border },
  visit: { fontFamily: fonts.bold, fontSize: 14, color: colors.primary },
  name: { fontFamily: fonts.semibold, fontSize: 14, color: colors.foreground },
  value: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: colors.foreground, flexShrink: 1 },
  muted: { fontFamily: fonts.regular, fontSize: 13, color: colors.mutedFg },
  bulletLine: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  bullet: { color: colors.primary, fontSize: 13, lineHeight: 19 },
  evidence: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 8, alignSelf: "flex-start", minHeight: 28 },
  evidenceText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  reviewLine: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 5 },
  reviewText: { color: colors.warning, fontFamily: fonts.medium, fontSize: 11 },
  status: { alignSelf: "flex-start", paddingHorizontal: 9, paddingVertical: 6, borderRadius: radii.pill, backgroundColor: "#E7F4EA" },
  statusCompact: { maxWidth: 120 },
  statusText: { color: "#326A43", fontFamily: fonts.semibold, fontSize: 11 },
  statusPending: { backgroundColor: "#FFF0D6" },
  statusPendingText: { color: "#855416" },
  statusInactive: { backgroundColor: colors.surface },
  statusInactiveText: { color: colors.mutedFg },
  mobileList: { gap: 12 },
  mobileCard: { padding: spacing.md, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card },
  mobileHead: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  visitBadge: { minWidth: 42, height: 34, paddingHorizontal: 8, borderRadius: radii.sm, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  visitBadgeText: { color: colors.primary, fontFamily: fonts.bold, fontSize: 13 },
  mobileName: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16, lineHeight: 21 },
  mobileGrid: { marginTop: 14, flexDirection: "row", flexWrap: "wrap", gap: 10 },
  mobileField: { minWidth: "45%", flexGrow: 1, flexBasis: 130, padding: 10, borderRadius: radii.sm, backgroundColor: colors.background },
  mobileLabel: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, marginBottom: 4, marginTop: 10, textTransform: "uppercase", letterSpacing: 0.4 },
  empty: { padding: 28, alignItems: "center", borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card },
  emptyTitle: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16 },
  emptyText: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 13, textAlign: "center", marginTop: 5 },
});
