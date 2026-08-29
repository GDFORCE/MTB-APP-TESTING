// ADM-08 — Platform activity (audit) log.
//
// Live, admin-gated endpoints (backend/admin_routes.py · AUDIT LOG):
//   list ............ GET /admin/audit-logs?category&from&to&limit
//   summary ......... GET /admin/audit-logs/summary
//   security alerts . GET /admin/audit-logs/security-alerts
//   export .......... GET /admin/audit-logs/export?category&from&to  (text/csv)
//
// Immutable record of who did what, to which record, and when. Rows arrive flat;
// grouping (user / org / category / date) and search run client-side. The date
// filter is translated to from/to YYYY-MM-DD query params for the server.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Modal, Animated,
  RefreshControl, Platform, Share, StyleSheet,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import * as Clipboard from "expo-clipboard";
import {
  Menu, RefreshCcw, X, Download, AlertTriangle, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, Server, Copy,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, SearchBar, st } from "./users";

type AuditRow = {
  id: string; user_id?: string; user_name?: string; role?: string; org?: string;
  action?: string; category?: string; detail?: string; ip?: string; device?: string;
  status?: string; created_at?: string; target_id?: string;
};
type Summary = { total?: number; last_24h?: number; failures_24h?: number; by_category?: Record<string, number> };
type SecurityAlert = { user_id?: string; user_name?: string; ip?: string; count?: number; last_at?: string; pattern?: string };

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const DATE_FILTERS = [
  { key: "today", label: "Today" }, { key: "yesterday", label: "Yesterday" },
  { key: "week", label: "Last 7 days" }, { key: "month", label: "Last 30 days" },
];
const GROUP_FILTERS = [
  { key: "user", label: "By user" }, { key: "org", label: "By org" },
  { key: "category", label: "By category" }, { key: "date", label: "By date" },
];

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function dateRange(key: string): { from: string; to: string } {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const minus = (n: number) => { const d = new Date(today); d.setDate(d.getDate() - n); return d; };
  switch (key) {
    case "yesterday": { const y = minus(1); return { from: ymd(y), to: ymd(y) }; }
    case "week": return { from: ymd(minus(6)), to: ymd(today) };
    case "month": return { from: ymd(minus(29)), to: ymd(today) };
    default: return { from: ymd(today), to: ymd(today) };
  }
}
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
function catColors(cat?: string): { fg: string; bg: string } {
  switch ((cat || "").toLowerCase()) {
    case "login": return { fg: C.info, bg: "rgba(123,107,184,0.12)" };
    case "emergency": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "account":
    case "admin": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "system": return { fg: C.mutedFg, bg: C.surface };
    default: return { fg: C.info, bg: "rgba(123,107,184,0.10)" };
  }
}

export default function AdminAuditLogs() {
  const { open } = useAdminDrawer();
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [secAlerts, setSecAlerts] = useState<SecurityAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [dateFilter, setDateFilter] = useState("week");
  const [category, setCategory] = useState("all");
  const [groupBy, setGroupBy] = useState("user");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [alertsDismissed, setAlertsDismissed] = useState(false);
  const [selected, setSelected] = useState<AuditRow | null>(null);

  const [toast, setToast] = useState("");
  const toastAnim = useRef(new Animated.Value(0)).current;
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    Animated.timing(toastAnim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => {
      Animated.timing(toastAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2600);
  }, [toastAnim]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const queryParams = useCallback(() => {
    const { from, to } = dateRange(dateFilter);
    const params: Record<string, string | number> = { from, to, limit: 500 };
    if (category !== "all") params.category = category;
    return params;
  }, [dateFilter, category]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [l, s, a] = await Promise.all([
        api.get("/admin/audit-logs", { params: queryParams() }),
        api.get("/admin/audit-logs/summary"),
        api.get("/admin/audit-logs/security-alerts"),
      ]);
      setRows(Array.isArray(l.data) ? l.data : []);
      setSummary(s.data || {});
      setSecAlerts(Array.isArray(a.data) ? a.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load audit logs. Pull to retry."));
    } finally { setLoading(false); }
  }, [queryParams]);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const categoryChips = useMemo(() => {
    const cats = Object.keys(summary?.by_category || {}).sort();
    return [{ key: "all", label: "All" }, ...cats.map((c) => ({ key: c, label: c }))];
  }, [summary]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      (r.detail || "").toLowerCase().includes(q) ||
      (r.user_name || "").toLowerCase().includes(q) ||
      (r.action || "").toLowerCase().includes(q) ||
      (r.org || "").toLowerCase().includes(q));
  }, [rows, search]);

  const groups = useMemo(() => {
    const keyOf = (r: AuditRow): string => {
      switch (groupBy) {
        case "org": return r.org || "Platform";
        case "category": return r.category || "other";
        case "date": return r.created_at ? new Date(r.created_at).toLocaleDateString() : "—";
        default: return r.user_name || "System";
      }
    };
    const map = new Map<string, AuditRow[]>();
    for (const r of filtered) {
      const k = keyOf(r);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(r);
    }
    return Array.from(map.entries()).map(([key, list]) => ({
      key, list,
      failCount: list.filter((r) => r.status === "failure").length,
    }));
  }, [filtered, groupBy]);

  const toggle = (k: string) => setExpanded((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);

  const exportCsv = async () => {
    try {
      const res = await api.get("/admin/audit-logs/export", { params: queryParams(), responseType: "text" as any });
      const csv: string = typeof res.data === "string" ? res.data : String(res.data ?? "");
      if (Platform.OS === "web") {
        const g: any = globalThis;
        const blob = new g.Blob([csv], { type: "text/csv" });
        const url = g.URL.createObjectURL(blob);
        const a = g.document.createElement("a");
        a.href = url; a.download = "audit-logs.csv"; a.click();
        g.URL.revokeObjectURL(url);
      } else {
        await Share.share({ message: csv, title: "audit-logs.csv" });
      }
      showToast("Audit log exported");
    } catch (e) {
      showToast(errMsg(e, "Export failed"));
    }
  };

  const copyAuditId = async (id: string) => {
    try {
      await Clipboard.setStringAsync(id);
      showToast("Audit ID copied");
    } catch {
      showToast("Couldn't copy the audit ID");
    }
  };

  const tiles = [
    { label: "Total actions", value: summary?.total ?? 0, fg: C.foreground },
    { label: "Last 24h", value: summary?.last_24h ?? 0, fg: C.foreground },
    { label: "Failures 24h", value: summary?.failures_24h ?? 0, fg: C.destructive },
    { label: "Loaded", value: filtered.length, fg: C.primary },
  ];

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} onExport={exportCsv} />
        {loading ? (
          <Loading label="Loading audit log…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              {tiles.map((t) => (
                <View key={t.label} style={st.tile}>
                  <RNText style={[st.tileValue, { color: t.fg }]}>{t.value}</RNText>
                  <RNText style={st.tileLabel}>{t.label}</RNText>
                </View>
              ))}
            </View>

            <SearchBar value={search} onChange={setSearch} placeholder="Search user, action, detail or org…" />
            <FilterChips label="DATE" chips={DATE_FILTERS} value={dateFilter} onChange={setDateFilter} />
            <FilterChips label="CATEGORY" chips={categoryChips} value={category} onChange={setCategory} />
            <FilterChips label="GROUP BY" chips={GROUP_FILTERS} value={groupBy} onChange={setGroupBy} />

            {/* Security alerts */}
            {!alertsDismissed && secAlerts.length > 0 && (
              <View style={au.secCard}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flex: 1 }}>
                    <AlertTriangle size={15} color={C.warning} />
                    <RNText style={au.secTitle}>Security alerts — {secAlerts.length} today</RNText>
                  </View>
                  <Pressable onPress={() => setAlertsDismissed(true)} hitSlop={8}><X size={16} color={C.mutedFg} /></Pressable>
                </View>
                <View style={{ gap: 4, marginTop: 6 }}>
                  {secAlerts.map((a, i) => (
                    <RNText key={i} style={au.secLine}>
                      {a.pattern === "repeated_failed_login" ? "Repeated failed login" : (a.pattern || "Anomaly")} · {a.user_name || a.user_id || "unknown"} · {a.ip || "—"} · {a.count} attempts · {fmtDateTime(a.last_at)}
                    </RNText>
                  ))}
                </View>
              </View>
            )}

            <RNText style={st.countLine}>{groups.length} groups · {filtered.length} entries</RNText>

            {groups.length === 0 ? (
              <EmptyCard message="No audit entries match the current filters." />
            ) : (
              <View style={{ gap: 10 }}>
                {groups.map((g) => {
                  const isOpen = expanded.includes(g.key);
                  return (
                    <View key={g.key} style={[st.card, { padding: 0, overflow: "hidden", borderLeftWidth: 4, borderLeftColor: g.failCount > 0 ? C.destructive : C.success }]}>
                      <Pressable onPress={() => toggle(g.key)} style={au.groupHeader}>
                        {groupBy === "user" && g.key === "System" ? <Server size={18} color={C.mutedFg} /> : null}
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <RNText style={au.groupName} numberOfLines={1}>{g.key}</RNText>
                          <RNText style={au.groupSub}>{g.list.length} actions{g.failCount > 0 ? ` · ${g.failCount} failed` : ""}</RNText>
                        </View>
                        {g.failCount > 0 && <View style={au.failDot} />}
                        {isOpen ? <ChevronUp size={18} color={C.mutedFg} /> : <ChevronDown size={18} color={C.mutedFg} />}
                      </Pressable>
                      {isOpen && (
                        <View style={{ borderTopWidth: 1, borderTopColor: C.border }}>
                          {g.list.map((r) => {
                            const cc = catColors(r.category);
                            const failed = r.status === "failure";
                            return (
                              <Pressable key={r.id} onPress={() => setSelected(r)} style={[au.entry, failed && { backgroundColor: "rgba(192,57,43,0.05)" }]}>
                                <View style={[au.catBadge, { backgroundColor: cc.bg }]}>
                                  <RNText style={[au.catBadgeTxt, { color: cc.fg }]} numberOfLines={1}>{r.category || "other"}</RNText>
                                </View>
                                <RNText style={au.entryDetail} numberOfLines={1}>{r.detail || r.action || "—"}</RNText>
                                <RNText style={au.entryTime}>{r.created_at ? new Date(r.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}</RNText>
                                {failed ? <XCircle size={15} color={C.destructive} /> : <CheckCircle2 size={15} color={C.success} />}
                              </Pressable>
                            );
                          })}
                        </View>
                      )}
                    </View>
                  );
                })}
              </View>
            )}
          </View>
        )}
      </ScrollView>

      <EntrySheet row={selected} onClose={() => setSelected(null)} onCopy={copyAuditId} />
      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Hero({ onMenu, onRefresh, onExport }: { onMenu: () => void; onRefresh: () => void; onExport: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Activity log</RNText>
          </View>
          <Pressable testID="audit-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Immutable record of who did what, to which record, and when.</RNText>
        <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
          <Pressable testID="audit-export" onPress={onExport} style={st.heroBtnGhost}>
            <Download size={15} color={C.primaryFg} /><RNText style={st.heroBtnGhostTxt}>Export CSV</RNText>
          </Pressable>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

function FilterChips({ label, chips, value, onChange }: { label: string; chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={{ marginTop: 10 }}>
      <RNText style={st.chipRowLabel}>{label}</RNText>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 8 }}>
        {chips.map((c) => (
          <Pressable key={c.key} onPress={() => onChange(c.key)} style={[st.chip, value === c.key && st.chipActive]}>
            <RNText style={[st.chipTxt, value === c.key && st.chipTxtActive]} numberOfLines={1}>{c.label}</RNText>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

function EntrySheet({ row, onClose, onCopy }: { row: AuditRow | null; onClose: () => void; onCopy: (id: string) => Promise<void> }) {
  const cc = catColors(row?.category);
  const failed = row?.status === "failure";
  return (
    <Modal visible={!!row} transparent animationType="slide" onRequestClose={onClose}>
      <View style={au.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={au.sheet}>
          <View style={au.sheetHeader}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <View style={[au.catBadge, { backgroundColor: cc.bg }]}><RNText style={[au.catBadgeTxt, { color: cc.fg }]}>{row?.category || "other"}</RNText></View>
              {failed ? <XCircle size={16} color={C.destructive} /> : <CheckCircle2 size={16} color={C.success} />}
            </View>
            <Pressable onPress={onClose} hitSlop={10} style={au.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
          </View>
          {row && (
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 24, gap: 14 }}>
              <RNText style={au.sheetDetail}>{row.detail || row.action || "—"}</RNText>
              <View style={{ gap: 2 }}>
                <RNText style={au.sectionLabel}>DETAILS</RNText>
                <KV k="Audit ID" v={row.id} />
                <KV k="Timestamp" v={fmtDateTime(row.created_at)} />
                <KV k="User" v={row.user_name || "System"} />
                <KV k="Role" v={row.role || "—"} />
                <KV k="Organization" v={row.org || "—"} />
                <KV k="Action" v={row.action || "—"} />
                <KV k="Status" v={row.status || "success"} />
              </View>
              {(row.ip || row.device || row.target_id) && (
                <View style={{ gap: 2 }}>
                  <RNText style={au.sectionLabel}>CONTEXT</RNText>
                  {!!row.ip && <KV k="IP address" v={row.ip} mono />}
                  {!!row.device && <KV k="Device" v={row.device} />}
                  {!!row.target_id && <KV k="Target" v={row.target_id} mono />}
                </View>
              )}
              <Pressable testID="audit-copy-id" onPress={() => void onCopy(row.id)} style={au.copyBtn}>
                <Copy size={15} color={C.primary} /><RNText style={au.copyTxt}>Copy audit ID</RNText>
              </Pressable>
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}
function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <View style={au.kv}>
      <RNText style={au.kvKey}>{k}</RNText>
      <RNText style={[au.kvVal, mono && { fontFamily: fonts.mono }]} numberOfLines={2}>{v}</RNText>
    </View>
  );
}

const au = StyleSheet.create({
  secCard: { backgroundColor: "rgba(216,154,60,0.08)", borderRadius: 14, borderWidth: 1, borderColor: "rgba(216,154,60,0.3)", padding: 12, marginTop: 14 },
  secTitle: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground, flex: 1 },
  secLine: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, lineHeight: 16 },
  groupHeader: { flexDirection: "row", alignItems: "center", gap: 10, padding: 14 },
  groupName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  groupSub: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 1 },
  failDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: C.destructive },
  entry: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 14, paddingVertical: 10, borderTopWidth: 1, borderTopColor: C.border },
  catBadge: { paddingHorizontal: 8, height: 20, borderRadius: 6, alignItems: "center", justifyContent: "center", maxWidth: 90 },
  catBadgeTxt: { fontFamily: fonts.medium, fontSize: 10, textTransform: "uppercase" },
  entryDetail: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
  entryTime: { fontFamily: fonts.mono, fontSize: 11, color: C.mutedFg },
  overlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 18, paddingTop: 16, maxHeight: "85%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  sheetClose: { width: 34, height: 34, borderRadius: 17, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  sheetDetail: { fontFamily: fonts.heading, fontSize: 16, color: C.foreground },
  sectionLabel: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1, color: C.mutedFg, marginBottom: 6 },
  kv: { flexDirection: "row", justifyContent: "space-between", gap: 12, paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: C.border },
  kvKey: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg },
  kvVal: { flex: 1, fontFamily: fonts.medium, fontSize: 12, color: C.foreground, textAlign: "right" },
  copyBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 999, backgroundColor: C.surface, marginTop: 4 },
  copyTxt: { fontFamily: fonts.bold, fontSize: 14, color: C.primary },
});
