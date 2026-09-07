import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle, CheckCircle2 } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Card, Small } from "@/src/components/ui";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { getDashboardActions } from "./api";
import type { DashboardAction } from "./api";
import { toActionBoardGroups } from "./presentation";

const first = (value?: string | string[]) => Array.isArray(value) ? value[0] : value;

/**
 * The PI/CRC action board (requirement doc 2 s13, doc 3 s28, doc 5 s25, doc 10 s24).
 *
 * This is deliberately NOT a schedule. It answers "what needs a decision from me
 * today". The engine already bounds it: an open-ended protocol contributes a few
 * near-term visits rather than every future cycle, and a conditional the patient
 * never triggered is absent entirely.
 */
export default function ActionBoardScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ trialId?: string; trial_id?: string }>();
  const [actions, setActions] = useState<DashboardAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const trialId = first(params.trialId) || first(params.trial_id);
      const response = await getDashboardActions(trialId);
      setActions(response.actions || []);
    } catch (nextError: any) {
      setError(
        nextError?.response?.data?.detail
        || nextError?.message
        || "The action board could not be loaded.",
      );
    } finally { setLoading(false); }
  }, [params.trialId, params.trial_id]);

  useEffect(() => { void load(); }, [load]);

  const groups = useMemo(() => toActionBoardGroups(actions), [actions]);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Clinical" title="Needs Your Attention" />
      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
          <Small style={{ marginTop: 10 }}>Loading outstanding actions…</Small>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {error ? (
            <Card style={styles.empty}>
              <AlertTriangle size={30} color={colors.warning} />
              <Body weight="700" style={{ marginTop: 10 }}>Action board unavailable</Body>
              <Small style={styles.message}>{error}</Small>
            </Card>
          ) : !groups.length ? (
            <Card style={styles.empty}>
              <CheckCircle2 size={30} color={colors.primary} />
              <Body weight="700" style={{ marginTop: 10 }}>Nothing needs a decision</Body>
              <Small style={styles.message}>
                No confirmations, overdue visits, or deviations are outstanding.
              </Small>
            </Card>
          ) : (
            groups.map((group) => (
              <View key={group.id} style={styles.group}>
                <Body weight="700" style={styles.groupTitle}>{group.label}</Body>
                {group.items.map((item) => (
                  <Pressable
                    key={item.id}
                    accessibilityRole="button"
                    accessibilityLabel={`${item.title} for ${item.patient}`}
                    onPress={() => router.push({
                      pathname: "/(app)/clinical/patient-schedule",
                      params: { patientId: item.patientId, patientName: item.patient },
                    } as never)}>
                    <Card style={styles.action}>
                      <View style={styles.actionHead}>
                        <Body weight="700">{item.title}</Body>
                        {!!item.due && <Small color={colors.warning}>{item.due}</Small>}
                      </View>
                      <Small>{item.patient}</Small>
                      {!!item.detail && <Small style={styles.detail}>{item.detail}</Small>}
                    </Card>
                  </Pressable>
                ))}
              </View>
            ))
          )}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 18 },
  empty: { alignItems: "center", paddingVertical: 30 },
  message: { textAlign: "center", marginTop: 6, maxWidth: 420 },
  group: { gap: 8 },
  groupTitle: { fontSize: 18, marginBottom: 2 },
  action: { borderRadius: radii.md },
  actionHead: { flexDirection: "row", justifyContent: "space-between", gap: 10 },
  detail: { marginTop: 4 },
});
