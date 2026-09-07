import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { AlertCircle, ChevronDown, ChevronRight, FileText } from "lucide-react-native";

import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import type {
  ConditionalRequirementRow, DayScheduleRow, PatientScheduleRow, ProtocolActivityRow,
  ProtocolScheduleRow, AnchorStatusRow, ConfinementRow, ImpactRow, QualifierDetail,
  RepeatRuleRow, TimelineEntry, VersionContext,
} from "./presentation";

type Props = {
  rows: Array<ProtocolScheduleRow | PatientScheduleRow>;
  patient?: boolean;
  onEvidence?: (row: ProtocolScheduleRow) => void;
};

function hasDetail(row: ProtocolScheduleRow | PatientScheduleRow): boolean {
  const day = (row as PatientScheduleRow).dayScheduleRows;
  return Boolean(
    row.qualifierDetails?.length || day?.length || row.activityRows?.length);
}

const widths = {
  visit: 74,
  name: 190,
  timing: 250,
  window: 130,
  type: 130,
  activities: 240,
  appliesTo: 190,
  expected: 150,
  actual: 150,
  status: 190,
  qualifier: 170,
};

export function ScheduleTable({ rows, patient = false, onEvidence }: Props) {
  const { width } = useWindowDimensions();
  // Complex protocol logic stays hidden until the user asks for it: the main
  // table is a familiar schedule, details live one tap away (UI spec section 5).
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  const toggle = (id: string) => setExpanded((current) => ({ ...current, [id]: !current[id] }));
  if (!rows.length) return <View style={styles.empty}><Text style={styles.emptyTitle}>No schedule events available</Text><Text style={styles.emptyText}>This schedule does not contain any displayable events.</Text></View>;
  if (width < 760) {
    return <View style={styles.mobileList}>{rows.map((row) => <MobileRow key={row.id} row={row} patient={patient} onEvidence={onEvidence} expanded={!!expanded[row.id]} onToggle={() => toggle(row.id)} />)}</View>;
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
          {!patient && <Header label="Applies To" width={widths.appliesTo} />}
          {patient && <Header label="Actual Date" width={widths.actual} />}
          <Header label="Status" width={widths.status} />
          <Header label="Notes" width={widths.qualifier} />
        </View>
        {rows.map((row, index) => (
          <React.Fragment key={row.id}>
          <View style={[styles.dataRow, index % 2 === 1 && styles.altRow]}>
            <Cell width={widths.visit}>
              {hasDetail(row)
                ? <Pressable
                    onPress={() => toggle(row.id)}
                    accessibilityRole="button"
                    accessibilityLabel={`${expanded[row.id] ? "Collapse" : "Expand"} ${row.visit} details`}
                    style={styles.expandToggle}>
                    {expanded[row.id]
                      ? <ChevronDown size={14} color={colors.primary} />
                      : <ChevronRight size={14} color={colors.primary} />}
                    <Text style={styles.visit}>{row.visit}</Text>
                  </Pressable>
                : <Text style={styles.visit}>{row.visit}</Text>}
            </Cell>
            <Cell width={widths.name}>
              <Text style={styles.name}>{row.visitName}</Text>
              {row.requiresReview && <View style={styles.reviewLine}><AlertCircle size={12} color={colors.warning} /><Text style={styles.reviewText}>Review required</Text></View>}
              {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
            </Cell>
            <Cell width={patient ? widths.expected : widths.timing}><Text style={styles.value}>{patient ? (row as PatientScheduleRow).expectedDate : row.timing}</Text></Cell>
            <Cell width={widths.window}><Text style={styles.value}>{patient ? (row as PatientScheduleRow).allowedWindow : row.window}</Text></Cell>
            <Cell width={widths.type}><Text style={styles.value}>{row.type}</Text></Cell>
            <Cell width={widths.activities}>{row.activities.length ? row.activities.map((activity) => <View key={activity} style={styles.bulletLine}><Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text></View>) : <Text style={styles.muted}>—</Text>}</Cell>
            {!patient && <Cell width={widths.appliesTo}><Text style={styles.value}>{row.appliesTo}</Text></Cell>}
            {patient && <Cell width={widths.actual}><Text style={styles.value}>{(row as PatientScheduleRow).actualDate}</Text></Cell>}
            <Cell width={widths.status}><Status value={row.status} /></Cell>
            <Cell width={widths.qualifier}><Text style={row.qualifier === "—" ? styles.muted : styles.value}>{row.qualifier}</Text></Cell>
          </View>
          {expanded[row.id] && <DetailPanel row={row} />}
          </React.Fragment>
        ))}
      </View>
    </ScrollView>
  );
}

/** Child tables for one visit: the day-wise activity schedule and source notes. */
function DetailPanel({ row }: { row: ProtocolScheduleRow | PatientScheduleRow }) {
  const patientRow = row as PatientScheduleRow;
  const day = patientRow.dayScheduleRows || [];
  return (
    <View style={styles.detailPanel}>
      {/* Doc 10 s21-s23: how the patient attends belongs with the visit, and an
          unstated mode must read as unstated rather than as a clinic visit. */}
      {!!patientRow.visitMode && (
        <MobileField label="How this visit happens" value={patientRow.visitMode} />
      )}
      {!!patientRow.unscheduledReason && (
        <MobileField label="Reason recorded" value={patientRow.unscheduledReason} />
      )}
      {/* UI spec s4.4: a repeat is a RULE. Saying so on the row stops the
          materialized occurrences from reading as the whole protocol. */}
      {!!row.repeatRule && (
        <MobileField label="Repeating rule" value={row.repeatRule} />
      )}
      {/* Doc 5 s26: "7 of 8 complete", against the REQUIRED assessments only. */}
      {!!patientRow.completion?.required && (
        <MobileField label="Progress" value={patientRow.completion.label} />
      )}
      {!!day.length && <DayScheduleTable rows={day} />}
      {/* Case 6 at protocol-review level. Shown only when there are no patient
          times to show instead: the rule is what a reviewer checks, the clock
          times are what a site reads. */}
      {!day.length && !!row.activityRows?.length && (
        <ProtocolActivityTable rows={row.activityRows} />
      )}
      {!!row.qualifierDetails?.length && <QualifierPanel details={row.qualifierDetails} />}
    </View>
  );
}

/**
 * The activities of one visit at protocol-review level: what each one is timed
 * against and by how much, with no dates or clock times, because none exist
 * until a patient does.
 */
export function ProtocolActivityTable({ rows }: { rows: ProtocolActivityRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Activities</Text>
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Activity</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Timing rule</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Anchor</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Window</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Review</Text>
      </View>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>
            {item.activity}{item.qualifier ? " *" : ""}
          </Text>
          <Text style={[styles.muted, styles.childRule]}>{item.timingRule}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.anchor}</Text>
          <Text style={[styles.muted, styles.childTime]}>{item.window}</Text>
          <View style={styles.childTime}>
            <Status value={item.review} compact />
          </View>
        </View>
      ))}
    </View>
  );
}

/**
 * Doc 3 s4 and s36. Rendered under the schedule so the last materialized visit
 * is never read as the end of the protocol.
 */
export function RepeatRuleFooter({ rows }: { rows: RepeatRuleRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Continuing protocol requirements</Text>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.eventCode}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.cadence}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.continuation}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.nextDate}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Doc 1 s3 and s22. A site reading this needs to know which reference dates are
 * still outstanding, so an undated visit reads as "waiting on surgery" rather
 * than as a broken row.
 */
export function AnchorStatusTable({ rows }: { rows: AnchorStatusRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.anchor}</Text>
          <View style={styles.childRule}>
            <Status value={item.status} compact />
          </View>
          <Text style={[styles.value, styles.childTime]}>{item.date}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.detail}</Text>
        </View>
      ))}
    </View>
  );
}

type DeviationRow = {
  logical_key: string;
  display_name: string;
  deviation_type: string;
  planned_date?: string | null;
  actual_date?: string | null;
  delta_from_planned_days?: number | null;
  outside_window_days?: number | null;
  reason?: string | null;
};

/**
 * Doc 1 s18 and s26 use both magnitudes, so both are shown: how far the visit
 * moved from its expected date, and how far outside the allowed window it fell.
 * A visit can be days early and still perfectly in window.
 */
export function DeviationTable({ rows }: { rows: DeviationRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Visit</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Expected</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Actual</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Deviation</Text>
      </View>
      {rows.map((item) => (
        <View key={item.logical_key} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.display_name}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.planned_date || "—"}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.actual_date || "—"}</Text>
          <Text style={[styles.muted, styles.childRule]}>
            {describeDeviation(item)}
          </Text>
        </View>
      ))}
    </View>
  );
}

function describeDeviation(item: DeviationRow): string {
  if (item.reason) return item.reason;
  const parts: string[] = [];
  const delta = item.delta_from_planned_days;
  if (typeof delta === "number" && delta !== 0) {
    parts.push(`${Math.abs(delta)} day${Math.abs(delta) === 1 ? "" : "s"} ${delta < 0 ? "early" : "late"}`);
  }
  const outside = item.outside_window_days;
  if (typeof outside === "number" && outside !== 0) {
    parts.push(`${Math.abs(outside)} outside the window`);
  }
  return parts.join(" · ") || item.deviation_type;
}

/**
 * UI spec s4.5. What a schedule change would do, before it is applied.
 *
 * Consequential rows sort first and protected history is labelled, because the
 * question a reviewer is answering is "is anything here wrong", and a list where
 * the important row is twentieth gets skimmed.
 */
export function ImpactPreviewTable({
  rows, summary,
}: { rows: ImpactRow[]; summary?: string }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      {!!summary && <Text style={[styles.childTitle]}>{summary}</Text>}
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Visit</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Change</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Now</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Would be</Text>
      </View>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.visit}</Text>
          <Text
            style={[item.consequential ? styles.value : styles.muted, styles.childRule]}>
            {item.change}
          </Text>
          <Text style={[styles.muted, styles.childTime]}>{item.before}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.after}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * UI spec s4.10 and doc 9 s12-13. Which version produced these dates.
 *
 * Without it, two patients on the same trial showing different visit dates looks
 * like a bug rather than the protocol working as written.
 */
export function VersionContextHeader({ context }: { context: VersionContext | null }) {
  if (!context) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>{context.label}</Text>
      {!!context.detail && <Text style={styles.muted}>{context.detail}</Text>}
    </View>
  );
}

/**
 * UI spec s4.7 and doc 6 s17/s31. One stay, expandable to its study days.
 * Rendering a row per day would read as several separate visits, which is the
 * misreading the confinement model exists to prevent.
 */
export function ConfinementTable({ rows }: { rows: ConfinementRow[] }) {
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Inpatient stays</Text>
      {rows.map((item) => (
        <View key={item.id}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${open[item.id] ? "Hide" : "Show"} study days for ${item.episode}`}
            onPress={() => setOpen((prior) => ({ ...prior, [item.id]: !prior[item.id] }))}
            style={styles.childRow}>
            {open[item.id]
              ? <ChevronDown size={13} color={colors.primary} />
              : <ChevronRight size={13} color={colors.primary} />}
            <Text style={[styles.value, styles.childActivity]}>{item.episode}</Text>
            <View style={styles.childRule}><Status value={item.status} compact /></View>
            <Text style={[styles.muted, styles.childRule]}>{item.stay}</Text>
          </Pressable>
          {open[item.id] && item.days.map((day) => (
            <View key={day.id} style={[styles.childRow, styles.confinementDay]}>
              <Text style={[styles.value, styles.childActivity]}>{day.label}</Text>
              <Text style={[styles.value, styles.childTime]}>{day.date}</Text>
              <Text style={[styles.muted, styles.childRule]}>{day.activities}</Text>
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

/** Doc 1 s27. The patient's visits in the order they happen. */
export function PatientTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return null;
  return (
    <View style={styles.childBlock}>
      {entries.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <View style={[styles.timelineDot, item.past && styles.timelineDotPast]} />
          <Text style={[styles.value, styles.childTime]}>{item.date}</Text>
          <Text style={[styles.value, styles.childActivity]}>{item.visit}</Text>
          <View style={styles.childRule}><Status value={item.status} compact /></View>
          {!!item.detail && <Text style={[styles.muted, styles.childRule]}>{item.detail}</Text>}
        </View>
      ))}
    </View>
  );
}

export function DayScheduleTable({ rows }: { rows: DayScheduleRow[] }) {
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Day-wise schedule</Text>
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Activity</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Timing rule</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Planned</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Actual</Text>
        <Text style={[styles.childHeaderText, styles.childStatus]}>Status</Text>
      </View>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.activity}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.timingRule}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.plannedTime}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.actualTime}</Text>
          <View style={styles.childStatus}><Status value={item.status} compact /></View>
        </View>
      ))}
    </View>
  );
}

export function QualifierPanel({ details }: { details: QualifierDetail[] }) {
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Protocol notes</Text>
      {details.map((detail) => (
        <View key={detail.id} style={styles.qualifierItem}>
          <View style={styles.qualifierHead}>
            <Text style={styles.qualifierMarker}>{detail.marker}</Text>
            <Text style={styles.qualifierScope}>Applies to {detail.appliesTo}</Text>
            {!detail.resolved && <Text style={styles.reviewText}>Needs review</Text>}
          </View>
          <Text style={styles.value}>{detail.text}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Conditional requirements live in their own compact table (UI spec 4.3). They are
 * deliberately NOT rows in the dated schedule: a condition that never occurs for
 * this patient must never look like a missed visit.
 */
export function ConditionalRequirementsTable({
  rows, onConfirm,
}: {
  rows: ConditionalRequirementRow[];
  onConfirm?: (row: ConditionalRequirementRow) => void;
}) {
  if (!rows.length) return null;
  return (
    <View style={styles.conditionalBlock}>
      <Text style={styles.conditionalTitle}>Conditional Requirements</Text>
      <Text style={styles.conditionalSubtitle}>
        These apply only if the condition occurs for this patient. Confirming one shows
        its schedule impact before anything changes.
      </Text>
      {rows.map((row) => (
        <View key={row.id} style={styles.conditionalRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{row.condition}</Text>
            <Text style={styles.muted}>If this occurs: {row.action}</Text>
            <Text style={styles.muted}>{row.timing}</Text>
            {row.requiresReview && (
              <View style={styles.reviewLine}>
                <AlertCircle size={12} color={colors.warning} />
                <Text style={styles.reviewText}>Needs reviewer confirmation</Text>
              </View>
            )}
          </View>
          <View style={styles.conditionalActions}>
            <Status value={row.status} compact />
            {onConfirm && (
              <Pressable
                onPress={() => onConfirm(row)}
                accessibilityRole="button"
                accessibilityLabel={`Record that ${row.condition} occurred`}
                style={styles.conditionalButton}>
                <Text style={styles.conditionalButtonText}>Condition occurred</Text>
              </Pressable>
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

function MobileRow({ row, patient, onEvidence, expanded, onToggle }: { row: ProtocolScheduleRow | PatientScheduleRow; patient: boolean; onEvidence?: (row: ProtocolScheduleRow) => void; expanded: boolean; onToggle: () => void }) {
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
        {!patient && <MobileField label="Applies to" value={row.appliesTo} />}
        {patient && <MobileField label="Actual date" value={patientRow.actualDate} />}
        {patient && !!patientRow.visitMode && (
          <MobileField label="How it happens" value={patientRow.visitMode} />
        )}
      </View>
      <Text style={styles.mobileLabel}>Activities</Text>
      {row.activities.length ? row.activities.map((activity) => <View key={activity} style={styles.bulletLine}><Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text></View>) : <Text style={styles.muted}>No activities listed</Text>}
      {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
      {hasDetail(row) && (
        <Pressable
          onPress={onToggle}
          accessibilityRole="button"
          accessibilityLabel={`${expanded ? "Hide" : "Show"} details for ${row.visit}`}
          style={styles.evidence}>
          {expanded ? <ChevronDown size={13} color={colors.primary} /> : <ChevronRight size={13} color={colors.primary} />}
          <Text style={styles.evidenceText}>{expanded ? "Hide details" : "Show details"}</Text>
        </Pressable>
      )}
      {expanded && <DetailPanel row={row} />}
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
  confinementDay: { paddingLeft: 22 },
  timelineDot: {
    width: 8, height: 8, borderRadius: 4, marginRight: 8,
    backgroundColor: colors.border,
  },
  timelineDotPast: { backgroundColor: colors.primary },
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
  expandToggle: { flexDirection: "row", alignItems: "center", gap: 4, minHeight: 30 },
  detailPanel: { paddingHorizontal: 14, paddingVertical: 12, backgroundColor: colors.background, borderTopWidth: 1, borderTopColor: colors.border, gap: 14 },
  childBlock: { gap: 6 },
  childTitle: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 },
  childHeader: { flexDirection: "row", gap: 10, paddingBottom: 5, borderBottomWidth: 1, borderBottomColor: colors.border },
  childHeaderText: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11 },
  childRow: { flexDirection: "row", gap: 10, alignItems: "center", paddingVertical: 7 },
  childActivity: { width: 170 },
  childRule: { width: 210 },
  childTime: { width: 82 },
  childStatus: { width: 170 },
  qualifierItem: { paddingVertical: 6, gap: 3 },
  qualifierHead: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  qualifierMarker: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  qualifierScope: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12 },
  conditionalBlock: { marginTop: spacing.lg, padding: spacing.md, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card, gap: 4 },
  conditionalTitle: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16 },
  conditionalSubtitle: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, lineHeight: 18, marginBottom: 8 },
  conditionalRow: { flexDirection: "row", gap: 12, alignItems: "flex-start", paddingVertical: 11, borderTopWidth: 1, borderTopColor: colors.border },
  conditionalActions: { alignItems: "flex-end", gap: 8 },
  conditionalButton: { paddingHorizontal: 11, paddingVertical: 8, borderRadius: radii.sm, backgroundColor: colors.secondary, minHeight: 34, justifyContent: "center" },
  conditionalButtonText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
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
