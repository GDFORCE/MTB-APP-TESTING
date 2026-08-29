import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import { AlertTriangle, ArrowUpRight, Bell, FlaskConical, Search, SlidersHorizontal, UserCheck, X } from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "@/src/auth/AuthContext";
import { Rise } from "@/src/components/Rise";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import type { SponsorTrial } from "@/src/features/sponsor/types";
import { colors, dawnGradient, fonts, shadows } from "@/src/theme/tokens";

const phaseFilters = [
  { label: "All", aliases: [] },
  { label: "Phase 1", aliases: ["Phase 1", "Phase I"] },
  {
    label: "Phase 1/Phase 2",
    aliases: ["Phase 1/Phase 2", "Phase I/Phase II", "Phase I/II", "Phase 1/2"],
  },
  { label: "Phase 2", aliases: ["Phase 2", "Phase II"] },
  {
    label: "Phase 2/Phase 3",
    aliases: ["Phase 2/Phase 3", "Phase II/Phase III", "Phase II/III", "Phase 2/3"],
  },
  { label: "Phase 3", aliases: ["Phase 3", "Phase III"] },
  {
    label: "Phase 3/Phase 4",
    aliases: ["Phase 3/Phase 4", "Phase III/Phase IV", "Phase III/IV", "Phase 3/4"],
  },
  { label: "Phase 4", aliases: ["Phase 4", "Phase IV"] },
  {
    label: "Post Marketing Servilliance",
    aliases: ["Post Marketing Surveillance", "Post-Marketing Surveillance", "Post Marketing Servilliance"],
  },
  { label: "BA/BE", aliases: ["BA/BE", "BA BE", "Bioavailability/Bioequivalence"] },
  { label: "Not applicable", aliases: ["Not applicable", "N/A", "NA"] },
] as const;
const statuses = ["All", "Active", "Completed", "Terminated"];

function phaseKey(value?: string) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9/]/g, "");
}

function tone(status: string) {
  const value = status.toLowerCase();
  if (value === "active") return { fg: colors.success, bg: "rgba(92,154,110,0.14)" };
  if (value === "completed") return { fg: colors.info, bg: "rgba(123,107,184,0.14)" };
  if (value === "terminated") return { fg: colors.destructive, bg: "rgba(192,57,43,0.12)" };
  return { fg: colors.mutedFg, bg: colors.surface };
}

export default function SponsorTrialsScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const fullName = user?.full_name || "";
  const initials = user?.avatar_initials || fullName.split(/\s+/).filter(Boolean).map((word) => word[0]).slice(0, 2).join("").toUpperCase() || "?";
  const roleLabel = user?.role === "cro" ? "CRO" : "Sponsor";
  const organization = user?.organization || "";
  const [trials, setTrials] = useState<SponsorTrial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("All");
  const [phase, setPhase] = useState("All");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [canAddTrial, setCanAddTrial] = useState(false);

  const load = useCallback(async () => {
    setError("");
    try {
      const dashboard = await getSponsorDashboard();
      setTrials(dashboard.trials);
      setCanAddTrial(Boolean(dashboard.capabilities.canAddTrial));
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load your trials.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return trials.filter((trial) => {
      const matchesStatus = status === "All" || trial.status.toLowerCase() === status.toLowerCase();
      const selectedPhase = phaseFilters.find((filter) => filter.label === phase);
      const matchesPhase = phase === "All" || Boolean(
        selectedPhase?.aliases.some((alias) => phaseKey(alias) === phaseKey(trial.phase)),
      );
      const haystack = [trial.protocolId, trial.title, trial.phase, trial.condition, trial.drug].filter(Boolean).join(" ").toLowerCase();
      return matchesStatus && matchesPhase && (!needle || haystack.includes(needle));
    });
  }, [phase, search, status, trials]);

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}>
          <Text style={s.headerEyebrow} numberOfLines={1}>{roleLabel}{organization ? ` · ${organization}` : ""}</Text>
          <Text style={s.headerTitle}>Trials</Text>
        </View>
        <Pressable onPress={() => router.push("/(app)/sponsor/notifications" as never)} style={s.iconButton}>
          <Bell size={18} color={colors.primaryFg} />
          {!!unread && unread > 0 && (
            <View style={s.notifBadge}><Text style={s.notifBadgeText}>{Math.min(9, unread)}</Text></View>
          )}
        </Pressable>
        <Pressable onPress={() => router.push("/(app)/sponsor/profile" as never)} style={s.iconButton}>
          <Text style={s.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      <View style={s.toolbar}>
        <View style={s.searchBox}>
          <Search size={17} color={colors.mutedFg} />
          <TextInput value={search} onChangeText={setSearch} placeholder="Search trials..." placeholderTextColor={colors.mutedFg} style={s.searchInput} />
          {!!search && <Pressable onPress={() => setSearch("")}><X size={16} color={colors.mutedFg} /></Pressable>}
        </View>
        <Pressable onPress={() => setFiltersOpen((value) => !value)} style={[s.filterToggle, (filtersOpen || phase !== "All") && s.filterToggleActive]}>
          <SlidersHorizontal size={17} color={(filtersOpen || phase !== "All") ? colors.white : colors.mutedFg} />
        </Pressable>
        {canAddTrial && (
          <Pressable onPress={() => router.push("/(app)/sponsor/add-trial" as never)} style={({ pressed }) => [pressed && s.pressed]}>
            <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.addButton}>
              <FlaskConical size={15} color={colors.white} /><Text style={s.addButtonText}>Add Trial</Text>
            </LinearGradient>
          </Pressable>
        )}
      </View>

      {filtersOpen && (
        <View style={s.phasePanel}>
          <View style={s.panelHeader}>
            <Text style={s.panelLabel}>FILTER BY PHASE</Text>
            {phase !== "All" && <Pressable onPress={() => setPhase("All")}><Text style={s.clear}>Clear</Text></Pressable>}
          </View>
          <View style={s.phaseWrap}>
            {phaseFilters.map(({ label }) => (
              <Pressable key={label} onPress={() => setPhase(label)} style={[s.phaseChip, phase === label && s.phaseChipActive]}>
                <Text style={[s.phaseText, phase === label && s.phaseTextActive]}>{label}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.statusScroll} contentContainerStyle={s.statusRow}>
        {statuses.map((value) => {
          const count = value === "All" ? trials.length : trials.filter((trial) => trial.status.toLowerCase() === value.toLowerCase()).length;
          const active = status === value;
          return (
            <Pressable key={value} onPress={() => setStatus(value)} style={[s.statusChip, active && s.statusChipActiveBorder]}>
              {active && <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={StyleSheet.absoluteFill} />}
              <Text style={[s.statusChipText, active && s.statusChipTextActive]}>{value} {count}</Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {loading ? (
        <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : error ? (
        <View style={s.center}>
          <AlertTriangle size={28} color={colors.destructive} />
          <Text style={s.error}>{error}</Text>
          <Pressable onPress={() => { setLoading(true); load(); }} style={s.retry}><Text style={s.retryText}>Try again</Text></Pressable>
        </View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={s.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.primary} />}
        >
          {filtered.length ? filtered.map((trial, index) => {
            const pct = trial.target > 0 ? Math.min(100, Math.round((trial.enrolled / trial.target) * 100)) : 0;
            const badge = tone(trial.status);
            return (
              <Rise key={trial.id} delay={Math.min(index, 8) * 55}>
                <Pressable
                  onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } } as never)}
                  style={({ pressed }) => [s.card, pressed && s.pressed]}
                >
                <LinearGradient colors={dawnGradient as any} style={s.accentRail} />
                <View style={s.cardTop}>
                  <View style={s.protocolPill}><Text style={s.protocol}>{trial.protocolId}</Text></View>
                  <View style={s.cardTopRight}>
                    <View style={[s.badge, { backgroundColor: badge.bg }]}><Text style={[s.badgeText, { color: badge.fg }]}>{trial.status}</Text></View>
                    <View style={s.arrow}><ArrowUpRight size={15} color={colors.primary} /></View>
                  </View>
                </View>
                <Text style={s.trialTitle}>{trial.title}</Text>
                <View style={s.tags}>
                  {[trial.phase, trial.condition, trial.drug, trial.sites ? `${trial.sites} sites` : ""].filter(Boolean).map((value) => (
                    <View key={value} style={s.tag}><Text style={s.tagText}>{value}</Text></View>
                  ))}
                </View>
                <View style={s.progressMeta}>
                  <Text style={s.progressLabel}>Enrolled</Text>
                  <Text style={s.progressValue}>{trial.enrolled}</Text>
                </View>
                <View style={s.track}>
                  <AnimatedProgress percentage={pct} />
                </View>
                <View style={s.attribution}>
                  <UserCheck size={13} color={colors.primary} />
                  <Text style={s.attributionText} numberOfLines={1}>
                    Created by {trial.createdByName || "Unknown"}
                    {trial.createdByRole ? ` · ${trial.createdByRole.toUpperCase()}` : ""}
                  </Text>
                  <Text style={s.attributionDate}>{formatDate(trial.createdAt)}</Text>
                </View>
                </Pressable>
              </Rise>
            );
          }) : (
            <View style={s.empty}>
              <View style={s.emptyIcon}><FlaskConical size={25} color={colors.primary} /></View>
              <Text style={s.emptyTitle}>No trials found</Text>
              <Text style={s.emptyText}>Try a different search or filter.</Text>
            </View>
          )}
        </ScrollView>
      )}
      <SponsorBottomNav active="trials" />
    </View>
  );
}

function formatDate(value?: string) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function AnimatedProgress({ percentage }: { percentage: number }) {
  const value = React.useRef(new Animated.Value(0)).current;
  React.useEffect(() => {
    Animated.timing(value, {
      toValue: Math.max(0, Math.min(100, percentage)),
      duration: 650,
      useNativeDriver: false,
    }).start();
  }, [percentage, value]);
  return (
    <Animated.View style={[s.fill, {
      width: value.interpolate({ inputRange: [0, 100], outputRange: ["0%", "100%"] }),
    }]}>
      <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={StyleSheet.absoluteFill} />
    </Animated.View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  header: { minHeight: 74, paddingHorizontal: 18, paddingTop: 8, paddingBottom: 13, flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.primaryDeep },
  headerIdentity: { flex: 1, minWidth: 0 },
  headerEyebrow: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1, color: "rgba(255,255,255,0.64)", textTransform: "uppercase" },
  headerTitle: { marginTop: 2, fontFamily: fonts.heading, fontSize: 20, color: colors.white },
  iconButton: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.15)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)" },
  avatarText: { fontFamily: fonts.bold, fontSize: 12, color: colors.primaryFg },
  notifBadge: { position: "absolute", top: -2, right: -2, minWidth: 16, height: 16, borderRadius: 8, paddingHorizontal: 3, alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive, borderWidth: 2, borderColor: colors.primaryDeep },
  notifBadgeText: { fontFamily: fonts.bold, fontSize: 8, color: colors.white },
  addButton: { height: 43, paddingHorizontal: 14, borderRadius: 15, flexDirection: "row", alignItems: "center", gap: 5 },
  addButtonText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.white },
  toolbar: { padding: 14, paddingBottom: 10, flexDirection: "row", gap: 8 },
  searchBox: { flex: 1, height: 43, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 13, color: colors.foreground, outlineStyle: "none" } as any,
  filterToggle: { width: 43, height: 43, borderRadius: 15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  filterToggleActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  phasePanel: { marginHorizontal: 14, marginBottom: 10, padding: 12, borderRadius: 17, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  panelHeader: { marginBottom: 9, flexDirection: "row", justifyContent: "space-between" },
  panelLabel: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 0.9, color: colors.mutedFg },
  clear: { fontFamily: fonts.semibold, fontSize: 10, color: colors.info },
  phaseWrap: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  phaseChip: { paddingHorizontal: 11, paddingVertical: 6, borderRadius: 999, backgroundColor: colors.surface },
  phaseChipActive: { backgroundColor: colors.primary },
  phaseText: { fontFamily: fonts.medium, fontSize: 10.5, color: colors.mutedFg },
  phaseTextActive: { color: colors.white },
  statusScroll: { flexGrow: 0, flexShrink: 0 },
  statusRow: { paddingHorizontal: 14, paddingBottom: 12, gap: 8, alignItems: "center" },
  statusChip: { overflow: "hidden", alignSelf: "flex-start", paddingHorizontal: 13, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  statusChipActiveBorder: { borderColor: "transparent" },
  statusChipText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.mutedFg },
  statusChipTextActive: { color: colors.white },
  list: { paddingHorizontal: 14, paddingBottom: 26, gap: 12 },
  card: { position: "relative", overflow: "hidden", padding: 15, paddingLeft: 19, borderRadius: 22, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  accentRail: { position: "absolute", left: 0, top: 0, bottom: 0, width: 5 },
  cardTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardTopRight: { flexDirection: "row", alignItems: "center", gap: 6 },
  protocolPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: 999, backgroundColor: colors.secondary },
  protocol: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.primary },
  badge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999 },
  badgeText: { fontFamily: fonts.semibold, fontSize: 9.5, textTransform: "capitalize" },
  arrow: { width: 27, height: 27, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  trialTitle: { marginTop: 11, fontFamily: fonts.heading, fontSize: 15, lineHeight: 20, color: colors.foreground },
  tags: { marginTop: 9, flexDirection: "row", flexWrap: "wrap", gap: 6 },
  tag: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.surface },
  tagText: { fontFamily: fonts.medium, fontSize: 9.5, color: colors.mutedFg },
  attribution: { marginTop: 12, paddingTop: 9, flexDirection: "row", alignItems: "center", gap: 6, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: "rgba(107,20,55,0.1)" },
  attributionText: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  attributionDate: { fontFamily: fonts.mono, fontSize: 10, color: colors.mutedFg },
  progressMeta: { marginTop: 10, marginBottom: 6, flexDirection: "row", justifyContent: "space-between" },
  progressLabel: { fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  progressValue: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.foreground },
  track: { height: 7, borderRadius: 999, overflow: "hidden", backgroundColor: colors.surface },
  fill: { height: "100%", borderRadius: 999 },
  pressed: { opacity: 0.88, transform: [{ scale: 0.99 }] },
  center: { flex: 1, padding: 30, alignItems: "center", justifyContent: "center", gap: 12 },
  error: { textAlign: "center", fontFamily: fonts.regular, fontSize: 13, color: colors.destructive },
  retry: { paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.primary },
  retryText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.white },
  empty: { alignItems: "center", paddingVertical: 54 },
  emptyIcon: { width: 52, height: 52, marginBottom: 10, borderRadius: 26, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  emptyTitle: { fontFamily: fonts.heading, fontSize: 16, color: colors.foreground },
  emptyText: { marginTop: 3, fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
});
