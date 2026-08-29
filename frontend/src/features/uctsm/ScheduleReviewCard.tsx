import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { Evidence, ScheduleEvent, ScheduleProjection, ValidationIssue } from "./types";

type Props = {
  event: ScheduleEvent;
  projection: ScheduleProjection;
  evidence: Evidence[];
  issues: ValidationIssue[];
  onOpenEvidence: (evidence: Evidence) => void;
  onReviewField: (field: "display_name" | "timing") => void;
};

export function ScheduleReviewCard({
  event, projection, evidence, issues, onOpenEvidence, onReviewField,
}: Props) {
  const eventEvidence = evidence.filter((item) => event.evidence_refs.includes(item.id));
  const eventIssues = issues.filter((item) => item.entity_id === event.id && item.status === "OPEN");
  return (
    <View style={styles.card} accessibilityLabel={`Review ${event.display_name}`}>
      <View style={styles.row}>
        <View style={styles.grow}>
          <Text style={styles.title}>{projection.title}</Text>
          <Text style={styles.type}>{projection.event_type_display}</Text>
        </View>
        <Text style={[styles.status, projection.requires_review && styles.warning]}>
          {projection.requires_review ? "Requires review" : projection.status}
        </Text>
      </View>

      <Text style={styles.label}>When</Text>
      <Text style={styles.value}>{projection.timing_display}</Text>
      {projection.window_display ? <Text style={styles.value}>Window {projection.window_display}</Text> : null}
      {projection.condition_display ? <Text style={styles.value}>{projection.condition_display}</Text> : null}

      <Text style={styles.label}>Activities</Text>
      <Text style={styles.value}>{projection.activities_display.join(" · ") || "No activities extracted"}</Text>

      {eventIssues.map((issue) => (
        <View key={issue.id} style={styles.issue}>
          <Text style={styles.issueText}>{issue.blocking ? "Blocking: " : "Review: "}{issue.message}</Text>
        </View>
      ))}

      <Text style={styles.label}>Protocol evidence</Text>
      {eventEvidence.length ? eventEvidence.map((item) => (
        <Pressable key={item.id} onPress={() => onOpenEvidence(item)} style={styles.link}>
          <Text style={styles.linkText}>
            Page {item.page_number ?? "?"}{item.table_title ? ` · ${item.table_title}` : ""}
          </Text>
        </Pressable>
      )) : <Text style={styles.missing}>No linked evidence — approval must remain blocked.</Text>}

      <View style={styles.actions}>
        <Pressable onPress={() => onReviewField("display_name")} style={styles.button}>
          <Text style={styles.buttonText}>Review name</Text>
        </Pressable>
        <Pressable onPress={() => onReviewField("timing")} style={styles.button}>
          <Text style={styles.buttonText}>Review timing</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderColor: "#D5D9E0", borderRadius: 14, padding: 16, gap: 5, backgroundColor: "#FFF" },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  grow: { flex: 1 },
  title: { fontSize: 17, fontWeight: "700", color: "#172033" },
  type: { fontSize: 12, color: "#657087", marginTop: 2 },
  status: { fontSize: 11, color: "#25633D", backgroundColor: "#EAF7EE", padding: 6, borderRadius: 8 },
  warning: { color: "#8A4B00", backgroundColor: "#FFF1D6" },
  label: { fontSize: 12, fontWeight: "700", color: "#657087", marginTop: 8 },
  value: { fontSize: 14, color: "#172033" },
  issue: { backgroundColor: "#FFF0F0", padding: 9, borderRadius: 8, marginTop: 6 },
  issueText: { color: "#8B1E1E", fontSize: 12 },
  link: { paddingVertical: 6 },
  linkText: { color: "#2359A8", fontWeight: "600" },
  missing: { color: "#8B1E1E", fontSize: 12 },
  actions: { flexDirection: "row", gap: 8, marginTop: 10 },
  button: { backgroundColor: "#173B67", paddingHorizontal: 12, paddingVertical: 9, borderRadius: 9 },
  buttonText: { color: "#FFF", fontWeight: "700", fontSize: 12 },
});

