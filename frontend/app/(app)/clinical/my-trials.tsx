import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { ArrowUpRight, Bell, FlaskConical, Plus, Search, SlidersHorizontal } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer } from "@/src/components/ScreenHeader";
import { Rise } from "@/src/components/Rise";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { PiBottomNav } from "@/src/features/clinical/components/PiBottomNav";

type Trial = {
  id: string; protocol_id?: string; title?: string; phase?: string; condition?: string; drug?: string;
  status?: string; recruitment_status?: string; site_names?: string[]; sponsor_name?: string;
  pi_name?: string; department?: string; created_by_name?: string;
};
const ALL = "all";

export default function MyTrials() {
  const router = useRouter();
  const { user } = useAuth();
  const navRole = user?.role === "site" ? "site" : user?.role === "smo" ? "smo" : user?.role === "crc" ? "crc" : "pi";
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [showPhaseFilters, setShowPhaseFilters] = useState(false);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const response = await api.get("/trials"); setTrials(Array.isArray(response.data) ? response.data : []); }
    catch (e: any) { setError(e?.response?.data?.detail || "Couldn't load your trials."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const phases = useMemo(() => [ALL, ...Array.from(new Set(trials.map(t => t.phase).filter(Boolean) as string[]))], [trials]);
  const statuses = useMemo(() => [ALL, ...Array.from(new Set(trials.map(t => t.recruitment_status || t.status).filter(Boolean) as string[]))], [trials]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return trials.filter(t => {
      const text = [t.protocol_id, t.title, t.condition, t.drug, ...(t.site_names || [])].filter(Boolean).join(" ").toLowerCase();
      const state = t.recruitment_status || t.status || "";
      return (!needle || text.includes(needle)) && (phase === ALL || t.phase === phase) && (status === ALL || state === status);
    });
  }, [phase, query, status, trials]);
  const statusOptions = useMemo(() => [
    { value: ALL, label: "All", count: trials.length },
    ...statuses.filter(value => value !== ALL).map(value => ({ value, label: value, count: trials.filter(t => (t.recruitment_status || t.status) === value).length })),
  ], [statuses, trials]);
  const canAdd = user?.role === "sponsor" || user?.role === "cro" || user?.role === "pi";
  return <ScreenContainer>
    <View style={s.piHeader}><View><Text style={s.piEyebrow}>PRINCIPAL INVESTIGATOR</Text><Text style={s.piTitle}>My Trials</Text></View><View style={s.headerActions}><Pressable testID="pi-trials-bell" onPress={() => router.push("/(app)/notifications")} style={s.bell}><Bell size={18} color={colors.primaryFg} />{(unread ?? 0) > 0 && <View style={s.unread}><Text style={s.unreadText}>{Math.min(unread ?? 0, 9)}</Text></View>}</Pressable><View style={s.headerAvatar}><Text style={s.headerAvatarText}>{user?.avatar_initials || (user?.full_name || "PI").split(" ").map(x => x[0]).join("").slice(0, 2)}</Text></View></View></View>
    <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      <Rise><View style={s.controlRow}><View style={s.search}><Search size={15} color={colors.mutedFg} /><TextInput testID="trial-search" value={query} onChangeText={setQuery} placeholder="Search trials..." placeholderTextColor={colors.mutedFg} style={s.searchInput} /></View><Pressable testID="trial-phase-filter" onPress={() => setShowPhaseFilters(open => !open)} style={[s.filterIcon, (showPhaseFilters || phase !== ALL) && s.filterIconActive]}><SlidersHorizontal size={15} color={showPhaseFilters || phase !== ALL ? colors.primaryFg : colors.mutedFg} /></Pressable>{canAdd && <Pressable testID="add-trial" onPress={() => router.push("/(app)/sponsor/add-trial")} style={s.addTrial}><Plus size={15} color={colors.primaryFg} /><Text style={s.addTrialText}>Add Trial</Text></Pressable>}</View></Rise>
      {showPhaseFilters && <Rise delay={40}><View style={s.phasePanel}><View style={s.phasePanelTop}><Eyebrow>FILTER BY PHASE</Eyebrow>{phase !== ALL && <Pressable onPress={() => setPhase(ALL)}><Small color={colors.primary} weight="700">Clear</Small></Pressable>}</View><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 7 }}>{phases.map(value => <Pressable key={value} onPress={() => setPhase(value)} style={[s.filterChip, phase === value && s.filterChipActive]}><Small color={phase === value ? colors.primaryFg : colors.foreground} weight="700">{value === ALL ? "All" : value}</Small></Pressable>)}</ScrollView></View></Rise>}
      <Rise delay={60}><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.statusRow}>{statusOptions.map(option => <Pressable key={option.value} onPress={() => setStatus(option.value)} style={[s.filterChip, status === option.value && s.filterChipActive]}><Small color={status === option.value ? colors.primaryFg : colors.foreground} weight="700">{option.label} {option.count}</Small></Pressable>)}</ScrollView></Rise>
      {loading ? <View style={s.state}><ActivityIndicator color={colors.primary} /><Small>Loading your trials...</Small></View> : error ? <Card style={s.stateCard}><Small color={colors.destructive} weight="700">{error}</Small><Button variant="secondary" style={{ marginTop: spacing.md }} onPress={() => load()}><Small color={colors.primary} weight="700">Retry</Small></Button></Card> : filtered.length === 0 ? <Card style={s.stateCard}><FlaskConical size={28} color={colors.mutedFg + "88"} /><Body weight="700" style={{ marginTop: spacing.sm }}>{trials.length ? "No matching trials" : "No trials assigned"}</Body><Small style={{ marginTop: 4, textAlign: "center" }}>{trials.length ? "Try changing your search or filters." : "Assigned studies will appear here."}</Small></Card> : filtered.map((trial, index) => <Rise key={trial.id} delay={140 + Math.min(index, 8) * 55}><TrialCard trial={trial} onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } })} /></Rise>)}
    </ScrollView>
    <PiBottomNav active="trials" calendarRole={navRole} role={navRole} />
  </ScreenContainer>;
}

function TrialCard({ trial, onPress }: { trial: Trial; onPress: () => void }) {
  const state = trial.recruitment_status || trial.status || "Active";
  const statusColor = state.toLowerCase().includes("complete") ? colors.success : state.toLowerCase().includes("terminat") || state.toLowerCase().includes("withdraw") ? colors.destructive : colors.accent;
  const details = [{ label: "SPONSOR", value: trial.sponsor_name || trial.created_by_name || "Not assigned" }, { label: "PI", value: trial.pi_name || trial.created_by_name || "Study team" }, { label: "SITE", value: trial.site_names?.[0] || "Not assigned" }, { label: "DEPARTMENT", value: trial.department || "Not assigned" }];
  const tags = [trial.phase, trial.condition, trial.drug].filter(Boolean) as string[];
  return <Pressable testID={`trial-card-${trial.id}`} onPress={onPress} style={({ pressed }) => [s.trialCard, pressed && s.trialCardPressed]}><View style={s.trialStripe} /><View style={s.trialContent}><View style={s.trialTop}><Small color={colors.primary} weight="700" style={s.protocol}>{trial.protocol_id || "NO PROTOCOL"}</Small><View style={s.stateAction}><View style={[s.statusPill, { backgroundColor: statusColor + "20" }]}><Small color={statusColor} weight="700">{state}</Small></View><View style={s.arrow}><ArrowUpRight size={15} color={colors.mutedFg} /></View></View></View><Body weight="700" numberOfLines={2} style={s.trialTitle}>{trial.title || "Untitled trial"}</Body>{tags.length > 0 && <View style={s.tags}>{tags.map(tag => <View key={tag} style={s.tag}><Small numberOfLines={1}>{tag}</Small></View>)}</View>}<View style={s.detailGrid}>{details.map(detail => <View key={detail.label} style={s.detailItem}><Eyebrow style={s.detailLabel}>{detail.label}</Eyebrow><Small numberOfLines={1} weight="700" style={s.detailValue}>{detail.value}</Small></View>)}</View></View></Pressable>;
}

const s = StyleSheet.create({
  piHeader: { backgroundColor: colors.primaryDeep, minHeight: 68, paddingHorizontal: spacing.md, paddingVertical: 11, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }, piEyebrow: { color: colors.overlay25, fontSize: 9, fontWeight: "700", letterSpacing: 1 }, piTitle: { color: colors.primaryFg, fontSize: 17, fontWeight: "700", marginTop: 2 }, headerActions: { flexDirection: "row", alignItems: "center", gap: 10 }, bell: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" }, unread: { position: "absolute", top: -2, right: -2, minWidth: 15, height: 15, borderRadius: 8, backgroundColor: colors.destructive, alignItems: "center", justifyContent: "center" }, unreadText: { color: colors.white, fontSize: 9, fontWeight: "700" }, headerAvatar: { width: 34, height: 34, borderRadius: 17, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" }, headerAvatarText: { color: colors.primaryFg, fontSize: 11, fontWeight: "700" },
  content: { padding: spacing.md, paddingBottom: 104 }, controlRow: { flexDirection: "row", alignItems: "center", gap: 7, marginBottom: 10 }, search: { flex: 1, height: 40, paddingHorizontal: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, flexDirection: "row", alignItems: "center", gap: 8 }, searchInput: { flex: 1, color: colors.foreground, paddingVertical: 0, fontSize: 12 }, filterIcon: { height: 40, width: 40, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" }, filterIconActive: { backgroundColor: colors.primary, borderColor: colors.primary }, addTrial: { height: 40, borderRadius: radii.lg, paddingHorizontal: 11, flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.primary }, addTrialText: { color: colors.primaryFg, fontSize: 12, fontWeight: "700" }, phasePanel: { marginBottom: 10, padding: 10, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card }, phasePanelTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 7 }, statusRow: { gap: 7, paddingBottom: 12 }, filterChip: { paddingHorizontal: 11, paddingVertical: 6, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card }, filterChipActive: { backgroundColor: colors.primary, borderColor: colors.primary }, buttonInner: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 }, state: { minHeight: 180, alignItems: "center", justifyContent: "center", gap: spacing.sm }, stateCard: { minHeight: 150, alignItems: "center", justifyContent: "center", marginTop: spacing.md },
  trialCard: { flexDirection: "row", overflow: "hidden", marginBottom: spacing.md, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, shadowColor: "#3f1d2e", shadowOpacity: 0.08, shadowRadius: 8, elevation: 2 }, trialCardPressed: { opacity: 0.86, transform: [{ scale: 0.99 }] }, trialStripe: { width: 5, backgroundColor: colors.accent }, trialContent: { flex: 1, padding: spacing.md }, trialTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: spacing.sm }, protocol: { fontFamily: "monospace" as any, backgroundColor: colors.secondary, overflow: "hidden", paddingHorizontal: 9, paddingVertical: 4, borderRadius: radii.pill }, stateAction: { flexDirection: "row", alignItems: "center", gap: 6 }, statusPill: { maxWidth: 92, paddingHorizontal: 8, paddingVertical: 4, borderRadius: radii.pill }, arrow: { width: 28, height: 28, borderRadius: 14, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" }, trialTitle: { fontSize: 16, lineHeight: 21, marginTop: 10 }, tags: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10 }, tag: { maxWidth: "100%", paddingHorizontal: 9, paddingVertical: 4, borderRadius: radii.pill, backgroundColor: colors.surface }, detailGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: 11, paddingTop: 11, marginTop: 12, borderTopWidth: 1, borderColor: colors.border }, detailItem: { width: "50%", paddingRight: 8 }, detailLabel: { color: colors.mutedFg, opacity: 0.72, fontSize: 9 }, detailValue: { fontSize: 11, marginTop: 3 },
});
