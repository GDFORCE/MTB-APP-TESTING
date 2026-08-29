// ADM-01 — Platform admin dashboard.
//
// Every number here is derived from LIVE admin endpoints (all under /api/admin,
// all role=admin gated). No aggregate is fabricated: where the backend exposes
// no single "dashboard summary" route, we compose the tiles from the list /
// stats endpoints and count client-side. If a feed is empty we say so; if the
// whole load fails we show a retry.
//
//   users .................. GET /admin/users            (total / active / inactive / locked + role split)
//   trials ................. GET /admin/trials           (count)
//   master-data (pending) .. GET /admin/master-data/submissions?status=pending
//   org name requests ...... GET /admin/organizations/name-requests?status=pending
//   org duplicates ......... GET /admin/organizations/duplicates
//   tickets ................ GET /admin/tickets          (open / in-progress)
//   alerts ................. GET /admin/alerts           (unresolved)
//   notif stats ............ GET /admin/notifications/stats  (failures_24h, total)
//   activity feed .......... GET /admin/audit-logs?limit=8
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, ActivityIndicator, RefreshControl } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, Users, FlaskConical, CheckCircle2, AlertTriangle, BellRing,
  Building2, ScrollText, ShieldAlert, KeyRound, ChevronRight, Clock, type LucideIcon,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { colors as C, dawnGradient, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";

const W = { w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)", w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)" };
type Urgency = "red" | "amber" | "green" | "blue";

const URGENCY: Record<Urgency, { fg: string; bg: string }> = {
  red: { fg: C.destructive, bg: "rgba(192,57,43,0.12)" },
  amber: { fg: C.warning, bg: "rgba(216,154,60,0.15)" },
  green: { fg: C.success, bg: "rgba(92,154,110,0.15)" },
  blue: { fg: C.info, bg: "rgba(123,107,184,0.14)" },
};

const ROLE_LABELS: { key: string; label: string; color: string }[] = [
  { key: "sponsor", label: "Sponsor", color: C.primary },
  { key: "cro", label: "CRO", color: C.info },
  { key: "smo", label: "SMO", color: C.accent },
  { key: "site", label: "Site", color: C.violet },
  { key: "pi", label: "PI", color: C.success },
  { key: "crc", label: "CRC", color: C.warning },
  { key: "patient", label: "Patient", color: C.mutedFg },
];

type AdminUser = { role?: string; status?: string };
type AuditRow = { id?: string; created_at?: string; user_name?: string; action?: string; detail?: string; category?: string; status?: string };

type Metrics = {
  users: AdminUser[];
  trialsCount: number;
  mdPending: number;
  nameReqPending: number;
  duplicates: number;
  openTickets: number;
  openAlerts: number;
  notifFailures24h: number;
  notifTotal: number;
  activity: AuditRow[];
};

const EMPTY: Metrics = {
  users: [], trialsCount: 0, mdPending: 0, nameReqPending: 0, duplicates: 0,
  openTickets: 0, openAlerts: 0, notifFailures24h: 0, notifTotal: 0, activity: [],
};

function relTime(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const arr = (v: any): any[] => (Array.isArray(v) ? v : []);

export default function AdminDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const { open } = useAdminDrawer();
  const [m, setM] = useState<Metrics>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const calls = [
      api.get("/admin/users"),
      api.get("/admin/trials"),
      api.get("/admin/master-data/submissions", { params: { status: "pending" } }),
      api.get("/admin/organizations/name-requests", { params: { status: "pending" } }),
      api.get("/admin/organizations/duplicates"),
      api.get("/admin/tickets"),
      api.get("/admin/alerts"),
      api.get("/admin/notifications/stats"),
      api.get("/admin/audit-logs", { params: { limit: 8 } }),
    ];
    const res = await Promise.allSettled(calls);
    // Whole load failed (network / not authorized) → surface a retry.
    if (res.every((r) => r.status === "rejected")) {
      const first = res[0] as PromiseRejectedResult;
      setError(first?.reason?.response?.data?.detail || "Couldn't load the dashboard. Pull to retry.");
      setLoading(false);
      return;
    }
    const data = (i: number): any => (res[i].status === "fulfilled" ? (res[i] as PromiseFulfilledResult<any>).value.data : undefined);

    const users = arr(data(0)) as AdminUser[];
    const tickets = arr(data(5));
    const alerts = arr(data(6));
    const stats = data(7) || {};
    setM({
      users,
      trialsCount: arr(data(1)).length,
      mdPending: arr(data(2)).length,
      nameReqPending: arr(data(3)).length,
      duplicates: arr(data(4)).length,
      openTickets: tickets.filter((t: any) => ["Open", "In Progress"].includes(t?.status)).length,
      openAlerts: alerts.filter((a: any) => (a?.status || "open") !== "resolved").length,
      notifFailures24h: Number(stats?.failures_24h) || 0,
      notifTotal: Number(stats?.total) || 0,
      activity: arr(data(8)) as AuditRow[],
    });
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // ── Derived counts (all from live rows) ──────────────────────────────────
  const totalUsers = m.users.length;
  const activeUsers = useMemo(() => m.users.filter((u) => (u.status || "Active") === "Active").length, [m.users]);
  const lockedUsers = useMemo(() => m.users.filter((u) => u.status === "Locked").length, [m.users]);
  const inactiveUsers = totalUsers - activeUsers - lockedUsers;
  const pendingApprovals = m.mdPending + m.nameReqPending;

  const distribution = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const u of m.users) counts[u.role || "other"] = (counts[u.role || "other"] || 0) + 1;
    return ROLE_LABELS.map((r) => ({ ...r, count: counts[r.key] || 0 })).filter((r) => r.count > 0);
  }, [m.users]);
  const distTotal = distribution.reduce((s, d) => s + d.count, 0);

  const initials = user?.avatar_initials || (user?.full_name || "A").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  const push = (href: string) => router.push(href as Href);

  const tiles: { key: string; label: string; icon: LucideIcon; value: number; sub: string; urgency: Urgency; href: string }[] = [
    { key: "users", label: "Total users", icon: Users, value: totalUsers, sub: `Active ${activeUsers} · Inactive ${inactiveUsers} · Locked ${lockedUsers}`, urgency: "blue", href: "/(app)/admin/users" },
    { key: "trials", label: "Total trials", icon: FlaskConical, value: m.trialsCount, sub: "Monitoring aggregates", urgency: "green", href: "/(app)/admin/trials" },
    { key: "approvals", label: "Pending approvals", icon: CheckCircle2, value: pendingApprovals, sub: `${m.mdPending} values · ${m.nameReqPending} name reqs`, urgency: "amber", href: "/(app)/admin/master-data" },
    { key: "issues", label: "Open issues", icon: AlertTriangle, value: m.openTickets, sub: `${m.openAlerts} system alerts open`, urgency: "red", href: "/(app)/admin/tickets" },
    { key: "notif", label: "Notif failures (24h)", icon: BellRing, value: m.notifFailures24h, sub: `${m.notifTotal} sent total`, urgency: m.notifFailures24h > 0 ? "red" : "green", href: "/(app)/admin/notification-monitoring" },
  ];

  const pending: { label: string; sub: string; count: number; urgency: Urgency; href: string }[] = [
    { label: "Master data approvals", sub: "Custom values awaiting review", count: m.mdPending, urgency: "red", href: "/(app)/admin/master-data" },
    { label: "Org merge reviews", sub: "Possible duplicate organizations", count: m.duplicates, urgency: "amber", href: "/(app)/admin/organizations" },
    { label: "Locked accounts", sub: "Users locked after failed logins", count: lockedUsers, urgency: "red", href: "/(app)/admin/users" },
    { label: "System alerts", sub: "Background process failures", count: m.openAlerts, urgency: "amber", href: "/(app)/admin/alerts" },
    { label: "Open support issues", sub: "User-reported tickets", count: m.openTickets, urgency: "red", href: "/(app)/admin/tickets" },
    { label: "Org name corrections", sub: "Submitted from profiles", count: m.nameReqPending, urgency: "blue", href: "/(app)/admin/organizations" },
  ];

  const quick: { label: string; icon: LucideIcon; href: string }[] = [
    { label: "Organizations", icon: Building2, href: "/(app)/admin/organizations" },
    { label: "Audit Logs", icon: ScrollText, href: "/(app)/admin/audit-logs" },
    { label: "System Alerts", icon: ShieldAlert, href: "/(app)/admin/alerts" },
    { label: "Emergency", icon: KeyRound, href: "/(app)/admin/emergency-access" },
  ];

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        {/* ── Dawn hero ── */}
        <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          <SafeAreaView edges={["top"]}>
            <View style={st.heroTop}>
              <Pressable testID="admin-menu" onPress={open} style={st.iconBtn} hitSlop={8}>
                <Menu size={20} color={C.primaryFg} />
              </Pressable>
              <View style={{ flex: 1, minWidth: 0 }}>
                <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
                <RNText style={st.heroTitle} numberOfLines={1}>Platform overview</RNText>
              </View>
              <Pressable testID="admin-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}>
                <RefreshCcw size={18} color={C.primaryFg} />
              </Pressable>
              <View style={st.avatar}>
                <RNText style={{ color: C.primaryFg, fontFamily: fonts.bold, fontSize: 13 }}>{initials}</RNText>
              </View>
            </View>
            <RNText style={st.heroSub}>Real-time snapshot of users, trials, approvals and system health.</RNText>
          </SafeAreaView>
        </LinearGradient>

        {loading ? (
          <View style={{ paddingTop: 60, alignItems: "center" }}>
            <ActivityIndicator color={C.primary} />
            <RNText style={{ color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 }}>Loading platform metrics…</RNText>
          </View>
        ) : error ? (
          <View style={{ padding: 16, marginTop: 12 }}>
            <View style={[st.card, { borderColor: "rgba(192,57,43,0.30)" }]}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" }}>
                  <AlertTriangle size={20} color={C.destructive} />
                </View>
                <RNText style={{ flex: 1, color: C.foreground, fontFamily: fonts.semibold, fontSize: 13 }}>{error}</RNText>
              </View>
              <Pressable testID="admin-retry" onPress={load} style={st.retryBtn}>
                <RefreshCcw size={15} color={C.primary} />
                <RNText style={{ color: C.primary, fontFamily: fonts.bold, fontSize: 13 }}>Retry</RNText>
              </Pressable>
            </View>
          </View>
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            {/* ── KPI tiles ── */}
            <View style={st.tileGrid}>
              {tiles.map((t) => {
                const Icon = t.icon;
                const u = URGENCY[t.urgency];
                return (
                  <Pressable key={t.key} testID={`kpi-${t.key}`} onPress={() => push(t.href)} style={st.tile}>
                    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                      <View style={[st.tileIcon, { backgroundColor: u.bg }]}>
                        <Icon size={18} color={u.fg} />
                      </View>
                      <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                    </View>
                    <RNText style={st.tileValue}>{t.value}</RNText>
                    <RNText style={st.tileLabel}>{t.label}</RNText>
                    <RNText style={st.tileSub} numberOfLines={2}>{t.sub}</RNText>
                  </Pressable>
                );
              })}
            </View>

            {/* ── Quick access ── */}
            <SectionLabel label="QUICK ACCESS" />
            <View style={{ flexDirection: "row", gap: 10 }}>
              {quick.map((q) => {
                const Icon = q.icon;
                return (
                  <Pressable key={q.label} testID={`quick-${q.label}`} onPress={() => push(q.href)} style={st.quick}>
                    <View style={st.quickIcon}>
                      <Icon size={20} color={C.accent} />
                    </View>
                    <RNText style={st.quickLabel} numberOfLines={1}>{q.label}</RNText>
                  </Pressable>
                );
              })}
            </View>

            {/* ── User distribution ── */}
            <SectionLabel label="USER DISTRIBUTION BY ENTITY" />
            <View style={st.card}>
              {distTotal === 0 ? (
                <RNText style={st.emptyText}>No users yet.</RNText>
              ) : (
                <>
                  <View style={st.distBar}>
                    {distribution.map((d) => (
                      <View key={d.key} style={{ width: `${(d.count / distTotal) * 100}%`, backgroundColor: d.color, height: "100%" }} />
                    ))}
                  </View>
                  <View style={st.distLegend}>
                    {distribution.map((d) => (
                      <View key={d.key} style={{ flexDirection: "row", alignItems: "center", gap: 6, marginRight: 14, marginTop: 8 }}>
                        <View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: d.color }} />
                        <RNText style={{ color: C.mutedFg, fontFamily: fonts.medium, fontSize: 12 }}>{d.label}</RNText>
                        <RNText style={{ color: C.foreground, fontFamily: fonts.bold, fontSize: 12 }}>{d.count}</RNText>
                      </View>
                    ))}
                  </View>
                </>
              )}
            </View>

            {/* ── Pending admin actions ── */}
            <SectionLabel label="PENDING ADMIN ACTIONS" />
            <View style={st.card}>
              {pending.map((p, i) => {
                const u = URGENCY[p.urgency];
                return (
                  <Pressable
                    key={p.label}
                    testID={`pending-${i}`}
                    onPress={() => push(p.href)}
                    style={[st.pendingRow, i < pending.length - 1 && { borderBottomWidth: 1, borderBottomColor: C.border }]}
                  >
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.pendingLabel}>{p.label}</RNText>
                      <RNText style={st.pendingSub} numberOfLines={1}>{p.sub}</RNText>
                    </View>
                    <View style={[st.badge, { backgroundColor: u.bg }]}>
                      <RNText style={{ color: u.fg, fontFamily: fonts.bold, fontSize: 12 }}>{p.count}</RNText>
                    </View>
                    <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                  </Pressable>
                );
              })}
            </View>

            {/* ── Recent activity ── */}
            <SectionLabel label="RECENT PLATFORM ACTIVITY" />
            <View style={st.card}>
              {m.activity.length === 0 ? (
                <RNText style={st.emptyText}>No recent activity in the audit trail.</RNText>
              ) : (
                m.activity.map((ev, i) => (
                  <Pressable
                    key={ev.id || i}
                    testID={`activity-${i}`}
                    onPress={() => push("/(app)/admin/audit-logs")}
                    style={[st.activityRow, i < m.activity.length - 1 && { borderBottomWidth: 1, borderBottomColor: C.border }]}
                  >
                    <View style={[st.dot, { backgroundColor: ev.status === "failure" ? C.destructive : C.info }]} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.activityTitle} numberOfLines={1}>{ev.detail || ev.action || "Activity"}</RNText>
                      <RNText style={st.activitySub} numberOfLines={1}>
                        {[ev.user_name, ev.category].filter(Boolean).join(" · ") || "System"}
                      </RNText>
                    </View>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                      <Clock size={11} color={C.mutedFg} />
                      <RNText style={st.activityTime}>{relTime(ev.created_at)}</RNText>
                    </View>
                  </Pressable>
                ))
              )}
            </View>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

function SectionLabel({ label }: { label: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 22, marginBottom: 12 }}>
      <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ width: 4, height: 14, borderRadius: 2 }} />
      <RNText style={{ color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.5 }}>{label}</RNText>
    </View>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 40, borderBottomLeftRadius: 24, borderBottomRightRadius: 24 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrow: { color: W.w55, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 24, marginTop: 2 },
  heroSub: { color: W.w70, fontFamily: fonts.regular, fontSize: 13, marginTop: 12, marginBottom: 4 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: W.w20, alignItems: "center", justifyContent: "center" },
  tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  tile: { width: "47.8%", flexGrow: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  tileIcon: { width: 34, height: 34, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  tileValue: { fontFamily: fonts.display, fontSize: 26, color: C.foreground, marginTop: 10, fontVariant: ["tabular-nums"] },
  tileLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground, marginTop: 2 },
  tileSub: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 3, lineHeight: 15 },
  quick: { flex: 1, alignItems: "center", paddingVertical: 14, paddingHorizontal: 6, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border },
  quickIcon: { width: 36, height: 36, borderRadius: 12, backgroundColor: "rgba(230,155,92,0.14)", alignItems: "center", justifyContent: "center" },
  quickLabel: { fontFamily: fonts.medium, fontSize: 11, color: C.foreground, marginTop: 8 },
  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingHorizontal: 16, paddingVertical: 4, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  distBar: { flexDirection: "row", height: 12, borderRadius: 999, overflow: "hidden", backgroundColor: C.surface, marginTop: 14 },
  distLegend: { flexDirection: "row", flexWrap: "wrap", paddingBottom: 14 },
  pendingRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 13 },
  pendingLabel: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  pendingSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 2 },
  badge: { minWidth: 28, paddingHorizontal: 8, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  activityRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  activityTitle: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
  activitySub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 2 },
  activityTime: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  emptyText: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, paddingVertical: 20, textAlign: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },
});
