import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { AlertTriangle, Bell, Search, UserRoundCheck } from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "@/src/auth/AuthContext";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import type { SponsorSite } from "@/src/features/sponsor/types";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type Investigator = {
  id: string;
  name: string;
  email: string;
  site: string;
  siteId: string;
  department: string;
  status: string;
};

function investigatorsFromSites(sites: SponsorSite[]): Investigator[] {
  const unique = new Map<string, Investigator>();
  for (const site of sites) {
    if (!site.pi && !site.piEmail && !site.piId) continue;
    const key = site.piId || site.piEmail?.toLowerCase() || `${site.pi}-${site.id}`;
    if (unique.has(key)) continue;
    unique.set(key, {
      id: key,
      name: site.pi || "Unnamed investigator",
      email: site.piEmail || "Email not provided",
      site: site.name || site.hospital || "Site not assigned",
      siteId: site.id,
      department: site.department || "Not provided",
      status: site.status || "Active",
    });
  }
  return [...unique.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function statusTone(value: string) {
  const status = value.toLowerCase();
  if (status === "terminated") {
    return { backgroundColor: colors.destructive + "18", borderColor: colors.destructive + "55", color: colors.destructive };
  }
  return { backgroundColor: colors.success + "18", borderColor: colors.success + "45", color: colors.success };
}

export default function PrincipalInvestigatorsScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [investigators, setInvestigators] = useState<Investigator[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const dashboard = await getSponsorDashboard();
      setInvestigators(investigatorsFromSites(dashboard.sites));
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load principal investigators.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return investigators;
    return investigators.filter((investigator) => [
      investigator.name,
      investigator.email,
      investigator.site,
      investigator.department,
      investigator.status,
    ].some((value) => value.toLowerCase().includes(needle)));
  }, [investigators, query]);

  const fullName = user?.full_name || "";
  const initials = user?.avatar_initials
    || fullName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase()
    || "?";

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}>
          <Text numberOfLines={1} style={s.headerEyebrow}>
            {user?.role === "cro" ? "CRO" : "SPONSOR"}{user?.organization ? ` · ${user.organization}` : ""}
          </Text>
          <Text style={s.headerTitle}>Principal Investigators</Text>
        </View>
        <Pressable onPress={() => router.push("/(app)/sponsor/notifications" as never)} style={s.iconButton} accessibilityLabel="Open notifications">
          <Bell size={18} color={colors.white} />
          {!!unread && <View style={s.badge}><Text style={s.badgeText}>{unread > 9 ? "9+" : unread}</Text></View>}
        </Pressable>
        <Pressable onPress={() => router.push("/(app)/sponsor/profile" as never)} style={s.avatar} accessibilityLabel="Open profile">
          <Text style={s.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      <View style={s.searchBox}>
        <Search size={16} color={colors.mutedFg} />
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search PIs, sites or departments..."
          placeholderTextColor={colors.mutedFg}
          style={s.searchInput}
        />
      </View>

      {loading ? (
        <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : error ? (
        <View style={s.center}>
          <AlertTriangle size={28} color={colors.destructive} />
          <Text style={s.errorText}>{error}</Text>
          <Pressable onPress={() => { setLoading(true); load(); }} style={s.retry}><Text style={s.retryText}>Try again</Text></Pressable>
        </View>
      ) : (
        <ScrollView
          style={s.listScroll}
          contentContainerStyle={s.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.primary} />}
        >
          <Text style={s.count}>{filtered.length} principal investigator{filtered.length === 1 ? "" : "s"}</Text>
          {filtered.map((investigator) => {
            const tone = statusTone(investigator.status);
            return (
              <Pressable
                key={investigator.id}
                onPress={() => router.push({ pathname: "/(app)/sponsor/sites", params: { siteId: investigator.siteId } })}
                style={({ pressed }) => [s.card, pressed && s.pressed]}
              >
                <View style={s.cardTop}>
                  <View style={s.nameBlock}>
                    <Text numberOfLines={1} style={s.name}>{investigator.name}</Text>
                    <Text numberOfLines={1} style={s.email}>{investigator.email}</Text>
                  </View>
                  <View style={[s.status, { backgroundColor: tone.backgroundColor, borderColor: tone.borderColor }]}>
                    <View style={[s.statusDot, { backgroundColor: tone.color }]} />
                    <Text style={[s.statusText, { color: tone.color }]}>{investigator.status}</Text>
                  </View>
                </View>
                <View style={s.divider} />
                <View style={s.details}>
                  <View style={s.detailField}>
                    <Text style={s.detailLabel}>SITE</Text>
                    <Text numberOfLines={1} style={s.detailValue}>{investigator.site}</Text>
                  </View>
                  <View style={s.detailField}>
                    <Text style={s.detailLabel}>DEPARTMENT</Text>
                    <Text numberOfLines={1} style={s.detailValue}>{investigator.department}</Text>
                  </View>
                </View>
              </Pressable>
            );
          })}
          {!filtered.length && (
            <View style={s.empty}>
              <View style={s.emptyIcon}><UserRoundCheck size={24} color={colors.primary} /></View>
              <Text style={s.emptyTitle}>No investigators found</Text>
              <Text style={s.emptyCopy}>Try another name, site, or department.</Text>
            </View>
          )}
        </ScrollView>
      )}

      <SponsorBottomNav active="sites" unread={unread ?? 0} />
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  header: { minHeight: 76, paddingHorizontal: 16, paddingTop: 7, paddingBottom: 14, flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.primaryDeep },
  headerIdentity: { flex: 1, minWidth: 0 },
  headerEyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1, color: "rgba(255,255,255,0.58)" },
  headerTitle: { marginTop: 4, fontFamily: fonts.bold, fontSize: 15, color: colors.white },
  iconButton: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 19, backgroundColor: "rgba(255,255,255,0.10)" },
  avatar: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 19, backgroundColor: "rgba(255,255,255,0.20)" },
  avatarText: { fontFamily: fonts.bold, fontSize: 11, color: colors.white },
  badge: { position: "absolute", right: -2, top: -3, minWidth: 16, height: 16, paddingHorizontal: 3, alignItems: "center", justifyContent: "center", borderRadius: 8, borderWidth: 2, borderColor: colors.primaryDeep, backgroundColor: colors.destructive },
  badgeText: { fontFamily: fonts.bold, fontSize: 7.5, color: colors.white },
  searchBox: { height: 42, marginHorizontal: 15, marginTop: 14, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 21, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 11, color: colors.foreground, outlineStyle: "none" } as any,
  listScroll: { flex: 1 },
  list: { paddingHorizontal: 15, paddingTop: 12, paddingBottom: 24, gap: 10 },
  count: { marginBottom: 1, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  card: { padding: 13, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  pressed: { opacity: 0.72, transform: [{ scale: 0.995 }] },
  cardTop: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  nameBlock: { flex: 1, minWidth: 0 },
  name: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  email: { marginTop: 4, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  status: { paddingHorizontal: 7, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 4, borderRadius: 999, borderWidth: 1 },
  statusDot: { width: 4, height: 4, borderRadius: 2 },
  statusText: { fontFamily: fonts.medium, fontSize: 8, textTransform: "capitalize" },
  divider: { height: 1, marginVertical: 11, backgroundColor: colors.border },
  details: { flexDirection: "row", gap: 14 },
  detailField: { flex: 1, minWidth: 0 },
  detailLabel: { fontFamily: fonts.medium, fontSize: 7.5, letterSpacing: 0.45, color: colors.mutedFg },
  detailValue: { marginTop: 4, fontFamily: fonts.medium, fontSize: 9.5, color: colors.foreground },
  center: { flex: 1, padding: 28, alignItems: "center", justifyContent: "center", gap: 12 },
  errorText: { textAlign: "center", fontFamily: fonts.regular, fontSize: 11, color: colors.destructive },
  retry: { minHeight: 40, paddingHorizontal: 18, alignItems: "center", justifyContent: "center", borderRadius: 999, backgroundColor: colors.primary },
  retryText: { fontFamily: fonts.semibold, fontSize: 10, color: colors.white },
  empty: { paddingVertical: 54, alignItems: "center" },
  emptyIcon: { width: 48, height: 48, alignItems: "center", justifyContent: "center", borderRadius: 24, backgroundColor: colors.secondary },
  emptyTitle: { marginTop: 12, fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground },
  emptyCopy: { marginTop: 4, fontFamily: fonts.regular, fontSize: 10, color: colors.mutedFg },
});
