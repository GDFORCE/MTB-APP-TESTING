import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, CheckCircle2 } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Card, Small } from "@/src/components/ui";
import { colors, radii, spacing } from "@/src/theme/tokens";
import {
  correctScheduleEvent, getScheduleQualifiers, getUniversalSchedule,
  resolveScheduleQualifier, validateSchedule,
} from "./api";
import type { QualifierRow } from "./api";
import {
  DEPENDENCY_MODE_CHOICES, toApplicabilityReviewRows, toDependencyReviewRows,
  toVersionContext,
} from "./presentation";
import { VersionContextHeader } from "./ScheduleTable";
import type { ScheduleEvent, UniversalSchedule } from "./types";

/**
 * The meanings a footnote can carry, in a reviewer's words rather than the
 * engine's enum spelling.
 */
const QUALIFIER_CATEGORIES = [
  { value: "TIMING", label: "Timing" },
  { value: "WINDOW", label: "Window" },
  { value: "CONDITION", label: "Condition" },
  { value: "APPLICABILITY", label: "Who it applies to" },
  { value: "POPULATION_RESTRICTION", label: "Population" },
  { value: "OPTIONALITY", label: "Optional" },
  { value: "EXCEPTION", label: "Exception" },
  { value: "REPEAT", label: "Repeat rule" },
  { value: "STOP", label: "Stop rule" },
  { value: "DETAIL", label: "Procedure detail" },
  { value: "OTHER", label: "Other" },
] as const;

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;

/**
 * The two review decisions a schedule version can be blocked on.
 *
 * Doc 4 s7 - when the protocol does not say whether a dependent visit counts
 * from the PLANNED or the ACTUAL previous date, approval is correctly blocked.
 * Until now a reviewer could only unblock it through the API. The two answers
 * produce different real visit dates for real patients, which is why each choice
 * states what it does rather than just naming itself.
 *
 * Doc 8 s33 - who each visit applies to, so the arm and cohort restrictions can
 * be checked without reading raw applicability expressions.
 */
export default function ProtocolReviewScreen() {
  const params = useLocalSearchParams<{
    scheduleVersionId?: string; schedule_version_id?: string; id?: string;
  }>();
  const [schedule, setSchedule] = useState<UniversalSchedule | null>(null);
  const [qualifiers, setQualifiers] = useState<QualifierRow[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [categories, setCategories] = useState<Record<string, string>>({});
  const [blocking, setBlocking] = useState<number | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const versionId = first(params.scheduleVersionId)
    || first(params.schedule_version_id) || first(params.id);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      if (!versionId) throw new Error("Select a schedule version to review.");
      const next = await getUniversalSchedule(versionId);
      setSchedule(next);
      try {
        setQualifiers((await getScheduleQualifiers(versionId)).qualifiers);
      } catch {
        // The footnote list is a review aid; the sections below still work.
        setQualifiers([]);
      }
      try {
        setBlocking((await validateSchedule(versionId)).blocking_issues);
      } catch {
        // Validation is informational here; the review lists below still work.
        setBlocking(null);
      }
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail
        || nextError?.message
        || "The schedule version could not be loaded.",
      );
    } finally { setLoading(false); }
  }, [versionId]);

  useEffect(() => { void load(); }, [load]);

  const dependencyRows = useMemo(() => toDependencyReviewRows(schedule ?? undefined), [schedule]);
  const applicabilityRows = useMemo(
    () => toApplicabilityReviewRows(schedule ?? undefined), [schedule]);
  const unresolvedQualifiers = useMemo(
    () => qualifiers.filter((item) => !item.resolved), [qualifiers]);
  const versionContext = useMemo(
    () => toVersionContext(schedule ? {
      name: schedule.schedule_metadata?.name,
      version_number: schedule.schedule_metadata?.version_number,
      status: schedule.schedule_metadata?.status,
    } : undefined),
    [schedule],
  );

  const chooseMode = async (eventId: string, mode: string, label: string) => {
    if (!schedule || !versionId) return;
    const event = schedule.events.find((item) => item.id === eventId);
    if (!event) return;
    setSaving(eventId); setNotice(""); setError("");
    try {
      await correctScheduleEvent(
        versionId,
        { ...event, dependency_mode: mode } as ScheduleEvent,
        `Reviewer set the dependency rule to ${label}`,
      );
      await load();
      setNotice(`${event.display_name}: ${label}. The schedule was re-validated.`);
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail
        || nextError?.message
        || "The dependency rule could not be saved.",
      );
    } finally { setSaving(null); }
  };

  const resolve = async (
    row: QualifierRow,
    decision: "ACCEPT" | "NOT_APPLICABLE" | "ESCALATE",
  ) => {
    if (!versionId) return;
    const reason = (reasons[row.id] || "").trim();
    if (!reason) {
      setError("Say why you are making this decision before saving it.");
      return;
    }
    const category = categories[row.id]
      || (row.category === "UNRESOLVED" ? "" : row.category);
    if (decision === "ACCEPT" && !category) {
      setError("Choose what kind of rule this footnote is before accepting it.");
      return;
    }
    setSaving(row.id); setNotice(""); setError("");
    try {
      const result = await resolveScheduleQualifier(versionId, row.id, {
        decision, reason, category: decision === "ACCEPT" ? category : undefined,
      });
      await load();
      setReasons((prev) => ({ ...prev, [row.id]: "" }));
      setNotice(
        (row.marker ? `Footnote ${row.marker}` : "Protocol note")
        + ` recorded. ${result.unresolved_qualifiers} footnote(s) still block approval.`,
      );
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail
        || nextError?.message
        || "That footnote decision could not be saved.",
      );
    } finally { setSaving(null); }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Protocol" title="Schedule Review" />
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
          <Small style={{ marginTop: 10 }}>Loading the schedule version…</Small>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {!!error && (
            <Card style={styles.empty}>
              <AlertTriangle size={28} color={colors.warning} />
              <Small style={styles.message}>{error}</Small>
            </Card>
          )}
          {!!notice && <Card><Small>{notice}</Small></Card>}

          <VersionContextHeader context={versionContext} />
          {blocking !== null && (
            <Card>
              <Small>
                {blocking === 0
                  ? "No issue is blocking approval."
                  : `${blocking} issue${blocking === 1 ? "" : "s"} block approval.`}
              </Small>
            </Card>
          )}

          {/* Doc 4 s7. The protocol did not say, so the system refuses to pick.
              Each option states what it does to real dates, because that is the
              difference the reviewer is actually deciding. */}
          <View>
            <Body weight="700" style={styles.sectionTitle}>Dependency Rules</Body>
            <Small>
              These visits depend on an earlier visit, and the protocol does not say
              whether they count from its planned or its actual date.
            </Small>
          </View>
          {!dependencyRows.length ? (
            <Card style={styles.done}>
              <CheckCircle2 size={22} color={colors.primary} />
              <Small style={{ marginLeft: 8 }}>Every dependency rule is decided.</Small>
            </Card>
          ) : dependencyRows.map((row) => (
            <Card key={row.id}>
              <Body weight="700">{row.visit}</Body>
              <Small>Depends on {row.dependsOn} · {row.current}</Small>
              <View style={styles.choices}>
                {DEPENDENCY_MODE_CHOICES.map((choice) => (
                  <Pressable
                    key={choice.value}
                    disabled={saving === row.id}
                    accessibilityRole="button"
                    accessibilityLabel={`${choice.label} for ${row.visit}`}
                    onPress={() => void chooseMode(row.id, choice.value, choice.label)}
                    style={[styles.choice, saving === row.id && styles.choiceBusy]}>
                    <Body weight="700" style={styles.choiceLabel}>{choice.label}</Body>
                    <Small>{choice.effect}</Small>
                  </Pressable>
                ))}
              </View>
            </Card>
          ))}

          {/* Doc 6. Extraction never decides what a footnote MEANS; it carries
              the marker and its evidence and marks it unresolved, which blocks
              approval. This is where a reviewer states that meaning - the only
              thing that can clear the block. */}
          <View>
            <Body weight="700" style={styles.sectionTitle}>Footnotes and Qualifiers</Body>
            <Small>
              Each table marker must be given a meaning, ruled not applicable, or
              escalated. Nothing here reaches a patient until you decide.
            </Small>
          </View>
          {!unresolvedQualifiers.length ? (
            <Card style={styles.done}>
              <CheckCircle2 size={22} color={colors.primary} />
              <Small style={{ marginLeft: 8 }}>
                {qualifiers.length
                  ? "Every footnote has been decided."
                  : "This schedule carries no footnotes."}
              </Small>
            </Card>
          ) : unresolvedQualifiers.map((row) => (
            <Card key={row.id}>
              <Body weight="700">
                {row.marker ? `Footnote ${row.marker}` : "Protocol note"} · {row.owner_name}
              </Body>
              <Small style={styles.qualifierText}>{row.text}</Small>
              <Small>
                Applies to {row.target_codes.join(", ") || "an unstated target"} ·
                {" "}scope {row.scope.toLowerCase()}
              </Small>
              {row.evidence.map((item) => (
                <Small key={item.id} style={styles.evidence}>
                  {item.page_number ? `Page ${item.page_number}` : "Protocol"}
                  {item.section_title ? ` · ${item.section_title}` : ""}
                  {item.source_text ? ` — ${item.source_text}` : ""}
                </Small>
              ))}

              <Small style={styles.fieldLabel}>What kind of rule is this?</Small>
              <View style={styles.categoryRow}>
                {QUALIFIER_CATEGORIES.map((option) => {
                  const active = (categories[row.id] || row.category) === option.value;
                  return (
                    <Pressable
                      key={option.value}
                      accessibilityRole="button"
                      accessibilityLabel={`${option.label} for ${row.owner_name}`}
                      onPress={() => setCategories(
                        (prev) => ({ ...prev, [row.id]: option.value }))}
                      style={[styles.category, active && styles.categoryActive]}>
                      <Text style={styles.categoryLabel}>{option.label}</Text>
                    </Pressable>
                  );
                })}
              </View>

              <Small style={styles.fieldLabel}>Why (recorded in the audit trail)</Small>
              <TextInput
                value={reasons[row.id] || ""}
                onChangeText={(value) => setReasons(
                  (prev) => ({ ...prev, [row.id]: value }))}
                placeholder="e.g. Confirmed against footnote a on page 42"
                placeholderTextColor={colors.mutedFg}
                multiline
                style={styles.reason}
              />

              <View style={styles.choices}>
                {([
                  ["ACCEPT", "Accept this meaning"],
                  ["NOT_APPLICABLE", "Not applicable to this trial"],
                  ["ESCALATE", "Escalate - leave unresolved"],
                ] as const).map(([decision, label]) => (
                  <Pressable
                    key={decision}
                    disabled={saving === row.id}
                    accessibilityRole="button"
                    accessibilityLabel={`${label} for ${row.owner_name}`}
                    onPress={() => void resolve(row, decision)}
                    style={[styles.choice, saving === row.id && styles.choiceBusy]}>
                    <Body weight="700" style={styles.choiceLabel}>{label}</Body>
                  </Pressable>
                ))}
              </View>
            </Card>
          ))}

          {/* Doc 8 s33. */}
          <View>
            <Body weight="700" style={styles.sectionTitle}>Who Each Visit Applies To</Body>
            <Small>Restrictions by arm, cohort, part or substudy</Small>
          </View>
          <Card>
            {applicabilityRows.map((row) => (
              <View key={row.id} style={styles.applicability}>
                <Body weight="700" style={{ flex: 1 }}>{row.visit}</Body>
                <Small color={row.restricted ? colors.warning : undefined}>
                  {row.appliesTo}
                </Small>
              </View>
            ))}
          </Card>
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 14 },
  empty: { alignItems: "center", paddingVertical: 22 },
  message: { textAlign: "center", marginTop: 6, maxWidth: 420 },
  done: { flexDirection: "row", alignItems: "center" },
  sectionTitle: { fontSize: 19 },
  choices: { gap: 8, marginTop: 10 },
  choice: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10,
  },
  choiceBusy: { opacity: 0.5 },
  qualifierText: { marginTop: 6, marginBottom: 4 },
  evidence: { fontStyle: "italic", marginTop: 2 },
  fieldLabel: { marginTop: 12, marginBottom: 4, fontWeight: "700" },
  categoryRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  category: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm,
    paddingVertical: 5, paddingHorizontal: 9,
  },
  categoryActive: { borderColor: colors.primary, backgroundColor: colors.primary + "14" },
  categoryLabel: { fontSize: 12 },
  reason: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    padding: 10, minHeight: 58, textAlignVertical: "top", color: colors.foreground,
  },
  choiceLabel: { marginBottom: 2 },
  applicability: {
    flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 6,
  },
});
