import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { Bell, ChevronRight, Users } from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "@/src/auth/AuthContext";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import type {
  RecruitmentFunnel,
  SponsorDashboard,
  SponsorTrial,
} from "@/src/features/sponsor/types";
import { colors, fonts, shadows } from "@/src/theme/tokens";

const EMPTY_FUNNEL: RecruitmentFunnel = {
  screened: 0,
  screen_fail: 0,
  randomized: 0,
  active: 0,
  withdrawn: 0,
  dropout: 0,
  follow_up: 0,
  completed: 0,
};

const METRICS: { key: keyof RecruitmentFunnel; label: string; tone?: string }[] = [
  { key: "screened", label: "Screened" },
  { key: "screen_fail", label: "Screen Fail", tone: colors.destructive },
  { key: "randomized", label: "Randomized" },
  { key: "withdrawn", label: "Withdrawn", tone: colors.warning },
  { key: "dropout", label: "Dropout", tone: colors.warning },
  { key: "follow_up", label: "Follow-up", tone: colors.info },
  { key: "completed", label: "Completed", tone: colors.success },
];

function statusTone(status: string) {
  const normalized = status.toLowerCase();
  if (normalized.includes("complete")) {
    return { backgroundColor: "rgba(92,154,110,0.14)", color: colors.success };
  }
  if (normalized.includes("terminat")) {
    return { backgroundColor: "rgba(192,57,43,0.12)", color: colors.destructive };
  }
  return { backgroundColor: "rgba(92,154,110,0.14)", color: colors.success };
}

function TrialCard({ trial, onPress }: { trial: SponsorTrial; onPress: () => void }) {
  const funnel = trial.recruitment || {
    ...EMPTY_FUNNEL,
    screened: trial.enrolled,
    randomized: trial.randomized,
  };
  const tone = statusTone(trial.status);

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Open ${trial.protocolId}`}
      onPress={onPress}
      style={({ pressed }) => [styles.trialCard, pressed && styles.pressed]}
    >
      <View style={styles.cardTop}>
        <View style={styles.protocolPill}>
          <Text numberOfLines={1} style={styles.protocolText}>{trial.protocolId}</Text>
        </View>
        <View style={[styles.statusPill, { backgroundColor: tone.backgroundColor }]}>
          <View style={[styles.statusDot, { backgroundColor: tone.color }]} />
          <Text style={[styles.statusText, { color: tone.color }]}>{trial.status || "Active"}</Text>
        </View>
        <ChevronRight size={16} color={colors.mutedFg} />
      </View>

      <View style={styles.infoGrid}>
        <InfoField label="PHASE" value={trial.phase || "—"} />
        <InfoField label="DISEASE" value={trial.condition || "—"} />
        <InfoField label="DRUG" value={trial.drug || "—"} />
      </View>

      <Text style={styles.recruitmentLabel}>RECRUITMENT STATUS</Text>
      <View style={styles.metricGrid}>
        {METRICS.map((metric) => (
          <View key={metric.key} style={styles.metric}>
            <Text style={[styles.metricValue, metric.tone ? { color: metric.tone } : null]}>
              {funnel[metric.key]}
            </Text>
            <Text numberOfLines={1} style={styles.metricLabel}>{metric.label}</Text>
          </View>
        ))}
      </View>
    </Pressable>
  );
}

function InfoField({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoField}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text numberOfLines={1} style={styles.infoValue}>{value}</Text>
    </View>
  );
}

export default function SponsorPatients() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [dashboard, setDashboard] = useState<SponsorDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setDashboard(await getSponsorDashboard());
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || "Couldn't load patient recruitment status.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const fullName = user?.full_name || "";
  const initials = user?.avatar_initials
    || fullName.split(/\s+/).filter(Boolean).map((part) => part[0]).slice(0, 2).join("").toUpperCase()
    || "?";
  const role = user?.role === "cro" ? "CRO" : "SPONSOR";
  const organization = user?.organization || "";
  const trials = dashboard?.trials || [];

  return (
    <View style={styles.screen}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={styles.header}>
        <View style={styles.headerIdentity}>
          <Text numberOfLines={1} style={styles.eyebrow}>
            {role}{organization ? ` · ${organization.toUpperCase()}` : ""}
          </Text>
          <Text style={styles.headerTitle}>Patient Stats</Text>
        </View>
        <Pressable
          accessibilityLabel="Open notifications"
          onPress={() => router.push("/(app)/sponsor/notifications")}
          style={styles.iconButton}
        >
          <Bell size={19} color={colors.white} />
          {!!unread && unread > 0 && (
            <View style={styles.badge}><Text style={styles.badgeText}>{Math.min(9, unread)}</Text></View>
          )}
        </Pressable>
        <Pressable
          accessibilityLabel="Open profile"
          onPress={() => router.push("/(app)/sponsor/profile")}
          style={styles.iconButton}
        >
          <Text style={styles.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.muted}>Loading patient status…</Text>
        </View>
      ) : error ? (
        <View style={styles.center}>
          <Users size={30} color={colors.primary} />
          <Text style={styles.errorText}>{error}</Text>
          <Pressable onPress={() => { setLoading(true); load(); }} style={styles.retryButton}>
            <Text style={styles.retryText}>Try again</Text>
          </Pressable>
        </View>
      ) : (
        <ScrollView
          style={styles.scroll}
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          refreshControl={(
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => { setRefreshing(true); load(); }}
              tintColor={colors.primary}
            />
          )}
        >
          <Text style={styles.pageTitle}>Patient Recruitment Status</Text>
          <Text style={styles.pageSubtitle}>
            {dashboard?.totals.subjects || 0} patients enrolled across {trials.length} trials
          </Text>

          {trials.length ? trials.map((trial) => (
            <TrialCard
              key={trial.id}
              trial={trial}
              onPress={() => router.push({
                pathname: "/(app)/clinical/trial-summary",
                params: { id: trial.id },
              })}
            />
          )) : (
            <View style={styles.emptyCard}>
              <Users size={25} color={colors.primary} />
              <Text style={styles.emptyTitle}>No trials yet</Text>
              <Text style={styles.muted}>Patient recruitment appears after a trial is created.</Text>
            </View>
          )}
        </ScrollView>
      )}

      <SponsorBottomNav active="patients" unread={unread ?? 0} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  header: {
    minHeight: 84,
    paddingHorizontal: 18,
    paddingBottom: 12,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 9,
    backgroundColor: colors.primaryDeep,
  },
  headerIdentity: { flex: 1, paddingBottom: 2 },
  eyebrow: { color: "rgba(255,255,255,0.62)", fontFamily: fonts.bold, fontSize: 9, letterSpacing: 0.8 },
  headerTitle: { color: colors.white, fontFamily: fonts.heading, fontSize: 18, marginTop: 2 },
  iconButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.14)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.16)",
  },
  avatarText: { color: colors.white, fontFamily: fonts.bold, fontSize: 11 },
  badge: {
    position: "absolute", right: -2, top: -3, minWidth: 16, height: 16, borderRadius: 8,
    alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive,
    borderWidth: 2, borderColor: colors.primaryDeep,
  },
  badgeText: { color: colors.white, fontFamily: fonts.bold, fontSize: 8 },
  scroll: { flex: 1 },
  content: { padding: 18, paddingBottom: 30, gap: 12 },
  pageTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 18 },
  pageSubtitle: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11, marginTop: -8, marginBottom: 2 },
  trialCard: {
    padding: 14,
    borderRadius: 17,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    ...shadows.sm,
  },
  pressed: { opacity: 0.72, transform: [{ scale: 0.995 }] },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  protocolPill: { maxWidth: "52%", paddingHorizontal: 9, paddingVertical: 5, borderRadius: 999, backgroundColor: colors.secondary },
  protocolText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 10 },
  statusPill: { marginLeft: "auto", paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, flexDirection: "row", alignItems: "center", gap: 4 },
  statusDot: { width: 5, height: 5, borderRadius: 3 },
  statusText: { fontFamily: fonts.semibold, fontSize: 9, textTransform: "capitalize" },
  infoGrid: { flexDirection: "row", marginTop: 13, gap: 8 },
  infoField: { flex: 1, minWidth: 0 },
  infoLabel: { color: colors.mutedFg, fontFamily: fonts.bold, fontSize: 7, letterSpacing: 0.4 },
  infoValue: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 9, marginTop: 2 },
  recruitmentLabel: { color: colors.mutedFg, fontFamily: fonts.bold, fontSize: 7, letterSpacing: 0.45, marginTop: 13, marginBottom: 7 },
  metricGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  metric: {
    width: "22.8%",
    minHeight: 48,
    paddingHorizontal: 3,
    borderRadius: 11,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
  },
  metricValue: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 13 },
  metricLabel: { color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 7, marginTop: 2 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 30, gap: 10 },
  muted: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11, textAlign: "center" },
  errorText: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 12, textAlign: "center" },
  retryButton: { marginTop: 4, paddingHorizontal: 18, paddingVertical: 9, borderRadius: 999, backgroundColor: colors.primary },
  retryText: { color: colors.white, fontFamily: fonts.semibold, fontSize: 11 },
  emptyCard: { minHeight: 160, alignItems: "center", justifyContent: "center", gap: 8, borderRadius: 17, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  emptyTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 15 },
});
