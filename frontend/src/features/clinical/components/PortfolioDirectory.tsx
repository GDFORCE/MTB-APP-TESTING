import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { ArrowLeft, Building2, ChevronRight, MapPin } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors, fonts } from "@/src/theme/tokens";

type Trial = {
  id: string;
  protocol_id?: string;
  title?: string;
  phase?: string;
  condition?: string;
  status?: string;
  recruitment_status?: string;
  site_names?: string[];
  sponsor_name?: string;
};

type DirectoryKind = "sites" | "sponsors";

export function PortfolioDirectory({ kind }: { kind: DirectoryKind }) {
  const router = useRouter();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const response = await api.get("/trials");
      setTrials(Array.isArray(response.data) ? response.data : []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || `Couldn't load ${kind}.`);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [kind]);

  useEffect(() => { load(); }, [load]);

  const groups = useMemo(() => {
    const grouped = new Map<string, Trial[]>();
    trials.forEach((trial) => {
      const names = kind === "sites"
        ? (trial.site_names?.filter(Boolean) || [])
        : [trial.sponsor_name].filter(Boolean) as string[];
      names.forEach((name) => grouped.set(name, [...(grouped.get(name) || []), trial]));
    });
    return Array.from(grouped.entries())
      .map(([name, assigned]) => ({ name, trials: assigned }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [kind, trials]);

  const title = kind === "sites" ? "Sites" : "Sponsors";
  return (
    <View style={s.page}>
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}><ArrowLeft size={21} color={colors.primaryFg} /></Pressable>
        <View style={{ flex: 1 }}><Text style={s.eyebrow}>PORTFOLIO</Text><Text style={s.title}>{title}</Text></View>
      </SafeAreaView>
      <ScrollView
        contentContainerStyle={s.content}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={colors.primary} />}
      >
        {loading ? <View style={s.state}><ActivityIndicator color={colors.primary} /><Text style={s.stateText}>Loading {title.toLowerCase()}…</Text></View> : null}
        {!loading && error ? <View style={s.state}><Text style={[s.stateText, { color: colors.destructive }]}>{error}</Text><Pressable onPress={() => load()} style={s.retry}><Text style={s.retryText}>Retry</Text></Pressable></View> : null}
        {!loading && !error && groups.length === 0 ? <View style={s.state}><Text style={s.stateText}>No {title.toLowerCase()} are assigned to your trials yet.</Text></View> : null}
        {!loading && !error && groups.map((group) => (
          <View key={group.name} style={s.siteCard}>
            <View style={s.siteHeading}>
              <View style={s.siteIcon}>{kind === "sites" ? <MapPin size={18} color={colors.success} /> : <Building2 size={18} color={colors.info} />}</View>
              <View style={{ flex: 1, minWidth: 0 }}><Text style={s.label}>{kind === "sites" ? "SITE NAME" : "SPONSOR"}</Text><Text numberOfLines={1} style={s.name}>{group.name}</Text></View>
              <View style={s.count}><Text style={s.countText}>{group.trials.length} trial{group.trials.length === 1 ? "" : "s"}</Text></View>
            </View>
            <View style={{ gap: 8 }}>
              {group.trials.map((trial) => (
                <Pressable key={trial.id} testID={`${kind}-trial-${trial.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } })} style={({ pressed }) => [s.trialCard, pressed && { opacity: 0.82 }]}>
                  <View style={s.trialTop}><Text style={s.protocol}>{trial.protocol_id || "Protocol"}</Text><ChevronRight size={16} color={colors.mutedFg} /></View>
                  <Text numberOfLines={2} style={s.trialTitle}>{trial.title || "Untitled trial"}</Text>
                  <View style={s.trialGrid}>
                    <Meta label="PHASE" value={trial.phase || "—"} />
                    <Meta label="DISEASE" value={trial.condition || "—"} />
                    <Meta label="STATUS OF TRIAL" value={trial.recruitment_status || trial.status || "Active"} />
                  </View>
                </Pressable>
              ))}
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <View style={{ flex: 1, minWidth: 0 }}><Text style={s.metaLabel}>{label}</Text><Text numberOfLines={1} style={s.metaValue}>{value}</Text></View>;
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  header: { minHeight: 74, flexDirection: "row", alignItems: "center", gap: 14, paddingHorizontal: 16, backgroundColor: colors.primaryDeep },
  eyebrow: { color: colors.primaryFg + "A8", fontFamily: fonts.bold, fontSize: 8, letterSpacing: 1.3 },
  title: { color: colors.primaryFg, fontFamily: fonts.heading, fontSize: 20, marginTop: 2 },
  content: { padding: 14, paddingBottom: 32, gap: 12 },
  state: { minHeight: 180, alignItems: "center", justifyContent: "center", gap: 12, paddingHorizontal: 24 },
  stateText: { color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
  retry: { paddingHorizontal: 18, height: 36, borderRadius: 18, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  retryText: { color: colors.primaryFg, fontFamily: fonts.bold, fontSize: 12 },
  siteCard: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 20, padding: 12, shadowColor: colors.foreground, shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  siteHeading: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 11 },
  siteIcon: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.success + "16" },
  label: { color: colors.mutedFg, fontFamily: fonts.bold, fontSize: 8, letterSpacing: 1 },
  name: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 13, marginTop: 3 },
  count: { paddingHorizontal: 7, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.secondary },
  countText: { color: colors.primary, fontFamily: fonts.bold, fontSize: 8 },
  trialCard: { backgroundColor: colors.surface, borderRadius: 13, borderWidth: 1, borderColor: colors.border, padding: 10 },
  trialTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  protocol: { color: colors.primary, fontFamily: fonts.mono, fontSize: 10, fontWeight: "700" },
  trialTitle: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 12, lineHeight: 16, marginTop: 4 },
  trialGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 9 },
  metaLabel: { color: colors.mutedFg, fontFamily: fonts.bold, fontSize: 7, letterSpacing: 0.8 },
  metaValue: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 9, marginTop: 3, textTransform: "capitalize" },
});
