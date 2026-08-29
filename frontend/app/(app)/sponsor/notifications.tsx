import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { AlertTriangle, Bell, Building2, CheckCheck, FlaskConical, Target } from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { colors, dawnGradient, fonts, shadows } from "@/src/theme/tokens";

type Notification = {
  id: string;
  title: string;
  body?: string;
  message?: string;
  kind?: string;
  category?: string;
  read?: boolean;
  created_at?: string;
};

const filters = ["All", "Trials", "Sites", "Recruitment", "System"];
const categoryOf = (item: Notification) => {
  const kind = `${item.category || ""} ${item.kind || ""}`.toLowerCase();
  if (kind.includes("trial") || kind.includes("schedule") || kind.includes("protocol")) return "Trials";
  if (kind.includes("site") || kind.includes("pi")) return "Sites";
  if (kind.includes("recruit") || kind.includes("enrol") || kind.includes("visit") || kind.includes("overdue")) return "Recruitment";
  return "System";
};

const iconOf = (category: string) => {
  if (category === "Trials") return { Icon: FlaskConical, fg: colors.info, bg: "rgba(123,107,184,0.12)" };
  if (category === "Sites") return { Icon: Building2, fg: colors.accent, bg: "rgba(230,155,92,0.14)" };
  if (category === "Recruitment") return { Icon: Target, fg: colors.success, bg: "rgba(92,154,110,0.13)" };
  return { Icon: Bell, fg: colors.primary, bg: colors.secondary };
};

export default function SponsorNotifications() {
  const router = useRouter();
  const { user } = useAuth();
  const [items, setItems] = useState<Notification[]>([]);
  const [filter, setFilter] = useState("All");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const response = await api.get("/notifications");
      setItems(Array.isArray(response.data) ? response.data : []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load notifications.");
    } finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  const unread = items.filter((item) => !item.read).length;
  const roleLabel = user?.role === "cro" ? "CRO" : "Sponsor";
  const organization = user?.organization || "";
  const fullName = user?.full_name || "";
  const initials = user?.avatar_initials || fullName.split(/\s+/).filter(Boolean).map((word) => word[0]).slice(0, 2).join("").toUpperCase() || "?";
  const visible = useMemo(() => filter === "All" ? items : items.filter((item) => categoryOf(item) === filter), [filter, items]);

  const markRead = async (id: string) => {
    setItems((previous) => previous.map((item) => item.id === id ? { ...item, read: true } : item));
    try { await api.post(`/notifications/${id}/read`); } catch { load(); }
  };
  const markAll = async () => {
    setItems((previous) => previous.map((item) => ({ ...item, read: true })));
    try { await api.post("/notifications/read-all"); } catch { load(); }
  };

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}>
          <Text style={s.headerEyebrow} numberOfLines={1}>{roleLabel}{organization ? ` · ${organization}` : ""}</Text>
          <Text style={s.headerTitle}>Notifications</Text>
        </View>
        <View style={s.iconButton} accessibilityLabel={`${unread} unread notifications`}>
          <Bell size={18} color={colors.primaryFg} />
          {unread > 0 && <View style={s.notifBadge}><Text style={s.notifBadgeText}>{Math.min(9, unread)}</Text></View>}
        </View>
        <Pressable onPress={() => router.push("/(app)/sponsor/profile" as never)} style={s.iconButton} accessibilityLabel="Open profile">
          <Text style={s.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      <View style={s.summary}>
        <LinearGradient colors={dawnGradient as any} style={s.summaryRail} />
        <View style={s.summaryTop}>
          <Text style={s.summaryLabel}>{unread ? `${unread} UNREAD` : "ALL CAUGHT UP"}</Text>
          {unread > 0 && <Pressable testID="notifications-mark-all" onPress={markAll} style={s.markAll}><CheckCheck size={13} color={colors.info} /><Text style={s.markAllText}>Mark all read</Text></Pressable>}
        </View>
        {!unread && <Text style={s.summaryText}>You have reviewed every portfolio update.</Text>}
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={s.filtersScroll} contentContainerStyle={s.filters}>
        {filters.map((value) => {
          const count = value === "All" ? items.length : items.filter((item) => categoryOf(item) === value).length;
          const active = filter === value;
          return (
            <Pressable key={value} onPress={() => setFilter(value)} style={s.filterPressable}>
              {active ? (
                <LinearGradient colors={dawnGradient as any} style={s.filterActive}>
                  <Text style={s.filterTextActive}>{value}</Text>
                  <View style={s.filterCountActive}><Text style={s.filterCountTextActive}>{count}</Text></View>
                </LinearGradient>
              ) : (
                <View style={s.filter}>
                  <Text style={s.filterText}>{value}</Text>
                  <View style={s.filterCount}><Text style={s.filterCountText}>{count}</Text></View>
                </View>
              )}
            </Pressable>
          );
        })}
      </ScrollView>

      {loading ? <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View> : error ? (
        <View style={s.center}><AlertTriangle size={28} color={colors.destructive} /><Text style={s.error}>{error}</Text><Pressable onPress={() => { setLoading(true); load(); }} style={s.retry}><Text style={s.retryText}>Try again</Text></Pressable></View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={s.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.primary} />}
          showsVerticalScrollIndicator={false}
        >
          {visible.length ? visible.map((item) => {
            const category = categoryOf(item);
            const { Icon, fg, bg } = iconOf(category);
            return (
              <Pressable key={item.id} onPress={() => !item.read && markRead(item.id)} style={[s.card, !item.read && s.cardUnread]}>
                {!item.read && <LinearGradient colors={dawnGradient as any} style={s.unreadRail} />}
                <View style={[s.icon, { backgroundColor: bg }]}><Icon size={18} color={fg} /></View>
                <View style={{ flex: 1 }}>
                  <View style={s.cardTop}>
                    <Text style={[s.title, !item.read && s.titleUnread]} numberOfLines={1}>{item.title}</Text>
                    <View style={s.timeWrap}>
                      {!item.read && <View style={s.unreadDot} />}
                      <Text style={[s.time, !item.read && s.timeUnread]}>{item.created_at ? new Date(item.created_at).toLocaleString() : ""}</Text>
                    </View>
                  </View>
                  <Text style={s.body} numberOfLines={2}>{item.body || item.message || "Open this update for more information."}</Text>
                  <View style={s.metaRow}>
                    <Text style={s.category}>{category}</Text>
                  </View>
                </View>
              </Pressable>
            );
          }) : (
            <View style={s.empty}>
              <View style={s.emptyIcon}><Bell size={25} color={colors.primary} /></View>
              <Text style={s.emptyTitle}>Nothing here</Text>
              <Text style={s.emptyText}>No notifications in this category.</Text>
            </View>
          )}
        </ScrollView>
      )}
      <SponsorBottomNav active="notifs" unread={unread} />
    </View>
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
  summary: { marginHorizontal: 14, marginTop: 11, marginBottom: 1, paddingVertical: 8, paddingLeft: 12, paddingRight: 2, overflow: "hidden" },
  summaryRail: { position: "absolute", left: 0, top: 7, bottom: 7, width: 3, borderRadius: 2 },
  summaryTop: { minHeight: 22, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  summaryLabel: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1, color: colors.primary },
  summaryText: { marginTop: 2, fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  markAll: { minHeight: 26, paddingHorizontal: 8, borderRadius: 10, flexDirection: "row", alignItems: "center", gap: 4 },
  markAllText: { fontFamily: fonts.semibold, fontSize: 10, color: colors.info },
  filtersScroll: { flexGrow: 0, flexShrink: 0 },
  filters: { paddingHorizontal: 14, paddingVertical: 11, gap: 8, alignItems: "flex-start" },
  filterPressable: { alignSelf: "flex-start" },
  filter: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 13, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  filterActive: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 13, paddingVertical: 7, borderRadius: 999, ...shadows.sm },
  filterText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.mutedFg },
  filterTextActive: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.white },
  filterCount: { paddingHorizontal: 5, paddingVertical: 1, borderRadius: 999, backgroundColor: colors.secondary },
  filterCountText: { fontFamily: fonts.mono, fontSize: 9, color: colors.mutedFg },
  filterCountActive: { paddingHorizontal: 5, paddingVertical: 1, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.25)" },
  filterCountTextActive: { fontFamily: fonts.mono, fontSize: 9, color: colors.white },
  center: { flex: 1, padding: 30, alignItems: "center", justifyContent: "center", gap: 12 },
  error: { textAlign: "center", fontFamily: fonts.regular, fontSize: 13, color: colors.destructive },
  retry: { paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.primary },
  retryText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.white },
  list: { padding: 14, paddingTop: 2, paddingBottom: 26, gap: 9 },
  card: { position: "relative", overflow: "hidden", padding: 12, paddingLeft: 15, flexDirection: "row", gap: 10, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  cardUnread: { backgroundColor: "rgba(166,33,63,0.045)", borderColor: "rgba(224,122,75,0.34)" },
  unreadRail: { position: "absolute", left: 0, top: 0, bottom: 0, width: 4 },
  icon: { width: 38, height: 38, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  cardTop: { flexDirection: "row", alignItems: "flex-start", gap: 7 },
  title: { flex: 1, fontFamily: fonts.medium, fontSize: 12.5, color: colors.foreground },
  titleUnread: { fontFamily: fonts.bold },
  body: { marginTop: 3, fontFamily: fonts.regular, fontSize: 10.5, lineHeight: 14, color: colors.mutedFg },
  metaRow: { marginTop: 7, flexDirection: "row", justifyContent: "space-between" },
  category: { fontFamily: fonts.semibold, fontSize: 9, color: colors.primary },
  timeWrap: { paddingTop: 1, flexShrink: 0, flexDirection: "row", alignItems: "center", gap: 4 },
  unreadDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.destructive },
  time: { fontFamily: fonts.mono, fontSize: 8.5, color: colors.mutedFg },
  timeUnread: { color: colors.mutedFg },
  empty: { paddingVertical: 55, alignItems: "center" },
  emptyIcon: { width: 52, height: 52, marginBottom: 10, borderRadius: 26, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  emptyTitle: { fontFamily: fonts.heading, fontSize: 16, color: colors.foreground },
  emptyText: { marginTop: 3, fontFamily: fonts.regular, fontSize: 11.5, color: colors.mutedFg },
});
