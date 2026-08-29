// ADM — Reports.
//
// Live, admin-gated endpoints (backend/admin_routes.py · REPORTS):
//   generate ....... POST /admin/reports/generate  {type, from?, to?}
//   recent ......... GET  /admin/reports/recent
//   download ....... GET  /admin/reports/{id}/download  (text/csv)
//
// Five report types are generated server-side to CSV and stored; the recent
// list is real (db.admin_reports). Only login-activity honours the date range.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Animated, Platform, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, AlertTriangle, BarChart3, Users, Building2, ShieldCheck,
  LogIn, FlaskConical, Download, FileText, Clock, FileSpreadsheet,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };
const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

type ReportType = "users" | "org-users" | "user-status" | "login-activity" | "trial-summary";
type ReportFormat = "pdf" | "xlsx";
type RecentReport = {
  id: string; type: string; name?: string; format?: string; size?: number; rows?: number;
  created_at?: string; created_by_name?: string; download_url?: string;
  params?: { from?: string | null; to?: string | null };
};

const REPORT_TYPES: { key: ReportType; label: string; sub: string; icon: any; usesRange: boolean }[] = [
  { key: "users", label: "Users", sub: "Full user directory", icon: Users, usesRange: false },
  { key: "org-users", label: "Org-wise users", sub: "Users grouped by organization", icon: Building2, usesRange: false },
  { key: "user-status", label: "User status", sub: "Counts by account status", icon: ShieldCheck, usesRange: false },
  { key: "login-activity", label: "Login activity", sub: "Login events over a date range", icon: LogIn, usesRange: true },
  { key: "trial-summary", label: "Trial summary", sub: "Enrollment & visit aggregates", icon: FlaskConical, usesRange: false },
];
const TYPE_LABEL: Record<string, string> = Object.fromEntries(REPORT_TYPES.map((t) => [t.key, t.label]));
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}
function fmtSize(bytes?: number): string {
  if (!bytes && bytes !== 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let output = "";
  let index = 0;
  for (; index + 2 < bytes.length; index += 3) {
    const value = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2];
    output += B64[(value >> 18) & 63] + B64[(value >> 12) & 63] + B64[(value >> 6) & 63] + B64[value & 63];
  }
  if (index < bytes.length) {
    const value = (bytes[index] << 16) | (index + 1 < bytes.length ? bytes[index + 1] << 8 : 0);
    output += B64[(value >> 18) & 63] + B64[(value >> 12) & 63]
      + (index + 1 < bytes.length ? B64[(value >> 6) & 63] : "=") + "=";
  }
  return output;
}

export default function AdminReports() {
  const { open } = useAdminDrawer();
  const [selected, setSelected] = useState<ReportType>("users");
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genErr, setGenErr] = useState<string | null>(null);

  const [recent, setRecent] = useState<RecentReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  const { toast, toastAnim, showToast } = useToast();

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get("/admin/reports/recent");
      setRecent(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load reports. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const usesRange = REPORT_TYPES.find((t) => t.key === selected)?.usesRange;
  const rangeValid = (!from || DATE_RE.test(from)) && (!to || DATE_RE.test(to));

  const generate = async () => {
    if (!rangeValid) { setGenErr("Dates must be in YYYY-MM-DD format"); return; }
    setGenerating(true); setGenErr(null);
    try {
      const body: Record<string, string> = { type: selected, format };
      if (usesRange && from) body.from = from;
      if (usesRange && to) body.to = to;
      const res = await api.post("/admin/reports/generate", body);
      showToast(`Generated ${TYPE_LABEL[selected]} report · ${res.data?.rows ?? 0} rows`);
      await load();
    } catch (e) {
      setGenErr(errMsg(e, "Couldn't generate report"));
    } finally { setGenerating(false); }
  };

  const download = async (rep: RecentReport) => {
    setDownloading(rep.id);
    try {
      const filename = rep.name || `report.${rep.format || "pdf"}`;
      const mime = rep.format === "xlsx"
        ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        : "application/pdf";
      if (Platform.OS === "web") {
        const res = await api.get(`/admin/reports/${rep.id}/download`, { responseType: "blob" });
        const g: any = globalThis;
        const blob = res.data instanceof g.Blob ? res.data : new g.Blob([res.data], { type: mime });
        const url = g.URL.createObjectURL(blob);
        const a = g.document.createElement("a");
        a.href = url; a.download = filename; a.click();
        g.URL.revokeObjectURL(url);
      } else {
        const res = await api.get(`/admin/reports/${rep.id}/download`, { responseType: "arraybuffer" });
        const uri = `data:${mime};base64,${arrayBufferToBase64(res.data as ArrayBuffer)}`;
        if (!await Linking.canOpenURL(uri)) throw new Error("No app can open this report format.");
        await Linking.openURL(uri);
      }
      showToast(`Downloaded ${filename}`);
    } catch (e) {
      showToast(errMsg(e, "Download failed"));
    } finally { setDownloading(null); }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} />

        <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
          <View style={st.card}>
            <RNText style={st.sectionTitle}>Generate a report</RNText>
            <View style={{ gap: 10, marginTop: 12 }}>
              {REPORT_TYPES.map((t) => {
                const active = selected === t.key;
                const Icon = t.icon;
                return (
                  <Pressable key={t.key} testID={`report-type-${t.key}`} onPress={() => setSelected(t.key)} style={[st.typeRow, active && st.typeRowActive]}>
                    <View style={[st.rowIcon, active && { backgroundColor: C.primary }]}>
                      <Icon size={18} color={active ? C.primaryFg : C.primary} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.rowName}>{t.label}</RNText>
                      <RNText style={st.rowSub} numberOfLines={1}>{t.sub}</RNText>
                    </View>
                    <View style={[st.radio, active && { borderColor: C.primary }]}>{active && <View style={st.radioDot} />}</View>
                  </Pressable>
                );
              })}
            </View>

            <RNText style={[st.fieldLabel, { marginTop: 14 }]}>Format</RNText>
            <View style={st.formatRow}>
              {([
                { key: "pdf", label: "PDF", icon: FileText },
                { key: "xlsx", label: "Excel", icon: FileSpreadsheet },
              ] as const).map((option) => {
                const active = format === option.key;
                const Icon = option.icon;
                return (
                  <Pressable
                    key={option.key}
                    testID={`report-format-${option.key}`}
                    onPress={() => setFormat(option.key)}
                    style={[st.formatButton, active && st.formatButtonActive]}
                  >
                    <Icon size={17} color={active ? C.primaryFg : C.primary} />
                    <RNText style={[st.formatText, active && st.formatTextActive]}>{option.label}</RNText>
                  </Pressable>
                );
              })}
            </View>

            <View style={[st.rangeWrap, { opacity: usesRange ? 1 : 0.45 }]}>
              <View style={{ flex: 1, gap: 6 }}>
                <RNText style={st.fieldLabel}>From</RNText>
                <TextInput value={from} onChangeText={setFrom} editable={usesRange} placeholder="YYYY-MM-DD" placeholderTextColor="rgba(123,95,115,0.5)" autoCapitalize="none" style={st.input} />
              </View>
              <View style={{ flex: 1, gap: 6 }}>
                <RNText style={st.fieldLabel}>To</RNText>
                <TextInput value={to} onChangeText={setTo} editable={usesRange} placeholder="YYYY-MM-DD" placeholderTextColor="rgba(123,95,115,0.5)" autoCapitalize="none" style={st.input} />
              </View>
            </View>
            {!usesRange && <RNText style={st.hint}>Date range applies to the Login activity report only.</RNText>}
            {genErr && <RNText style={[st.errText, { marginTop: 10 }]}>{genErr}</RNText>}

            <Pressable testID="report-generate" onPress={generating ? undefined : generate} style={[st.generateBtn, generating && { opacity: 0.6 }]}>
              {generating ? <ActivityIndicator color={C.primaryFg} size="small" /> : (
                <><BarChart3 size={16} color={C.primaryFg} /><RNText style={st.generateTxt}>Generate {TYPE_LABEL[selected]} · {format === "pdf" ? "PDF" : "Excel"}</RNText></>
              )}
            </Pressable>
          </View>

          <RNText style={st.recentHeader}>Recent reports</RNText>
          {loading ? (
            <Loading label="Loading reports…" />
          ) : error ? (
            <ErrorCard message={error} onRetry={load} />
          ) : recent.length === 0 ? (
            <EmptyCard message="No reports generated yet." />
          ) : (
            <View style={{ gap: 10 }}>
              {recent.map((r) => (
                <View key={r.id} style={st.row}>
                  <View style={st.rowIcon}><FileText size={18} color={C.primary} /></View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <RNText style={st.rowName} numberOfLines={1}>{r.name || TYPE_LABEL[r.type] || r.type}</RNText>
                    <RNText style={st.rowSub} numberOfLines={1}>
                      {TYPE_LABEL[r.type] || r.type} · {r.rows ?? 0} rows · {fmtSize(r.size)}
                    </RNText>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 5, marginTop: 3 }}>
                      <Clock size={11} color={C.mutedFg} />
                      <RNText style={st.rowMeta} numberOfLines={1}>{fmtDateTime(r.created_at)}{r.created_by_name ? ` · ${r.created_by_name}` : ""}</RNText>
                    </View>
                  </View>
                  <Pressable testID={`report-download-${r.id}`} onPress={downloading ? undefined : () => download(r)} style={st.downloadBtn} hitSlop={6}>
                    {downloading === r.id ? <ActivityIndicator color={C.primary} size="small" /> : <Download size={18} color={C.primary} />}
                  </Pressable>
                </View>
              ))}
            </View>
          )}
        </View>
      </ScrollView>

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Hero({ onMenu, onRefresh }: { onMenu: () => void; onRefresh: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Reports</RNText>
          </View>
          <Pressable testID="reports-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Generate approved PDF or Excel reports. Patient data is pseudonymized.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Shared primitives (kept in-file: this screen owns only its own module) ────
function useToast() {
  const [toast, setToast] = useState("");
  const toastAnim = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    Animated.timing(toastAnim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      Animated.timing(toastAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2600);
  }, [toastAnim]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { toast, toastAnim, showToast };
}
function Loading({ label }: { label: string }) {
  return <View style={{ paddingTop: 40, alignItems: "center" }}><ActivityIndicator color={C.primary} /><RNText style={st.loadingTxt}>{label}</RNText></View>;
}
function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={{ marginTop: 12 }}>
      <View style={[st.card, { borderColor: "rgba(192,57,43,0.30)" }]}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View style={st.errIcon}><AlertTriangle size={20} color={C.destructive} /></View>
          <RNText style={{ flex: 1, color: C.foreground, fontFamily: fonts.semibold, fontSize: 13 }}>{message}</RNText>
        </View>
        <Pressable onPress={onRetry} style={st.retryBtn}><RefreshCcw size={15} color={C.primary} /><RNText style={{ color: C.primary, fontFamily: fonts.bold, fontSize: 13 }}>Retry</RNText></Pressable>
      </View>
    </View>
  );
}
function EmptyCard({ message }: { message: string }) {
  return <View style={[st.card, { marginTop: 4 }]}><RNText style={st.emptyText}>{message}</RNText></View>;
}
function Toast({ text, anim }: { text: string; anim: Animated.Value }) {
  if (text === "") return null;
  return (
    <Animated.View pointerEvents="none" style={[st.toast, { opacity: anim, transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }] }]}>
      <RNText style={st.toastTxt}>{text}</RNText>
    </Animated.View>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 40, borderBottomLeftRadius: 24, borderBottomRightRadius: 24 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrow: { color: W.w55, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 24, marginTop: 2 },
  heroSub: { color: W.w70, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },

  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingHorizontal: 16, paddingVertical: 16 },
  sectionTitle: { fontFamily: fonts.heading, fontSize: 16, color: C.foreground },

  typeRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 14, borderWidth: 1, borderColor: C.border, padding: 12 },
  typeRowActive: { borderColor: C.primary, backgroundColor: "rgba(166,33,63,0.06)" },
  rowIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 1 },
  rowMeta: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, flexShrink: 1 },
  radio: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  radioDot: { width: 11, height: 11, borderRadius: 6, backgroundColor: C.primary },
  formatRow: { flexDirection: "row", gap: 9, marginTop: 7 },
  formatButton: { flex: 1, minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 13, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  formatButtonActive: { borderColor: C.primary, backgroundColor: C.primary },
  formatText: { fontFamily: fonts.bold, fontSize: 12.5, color: C.primary },
  formatTextActive: { color: C.primaryFg },

  rangeWrap: { flexDirection: "row", gap: 10, marginTop: 14 },
  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.background, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  hint: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 8 },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  generateBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: 999, backgroundColor: C.primary, marginTop: 16 },
  generateTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  recentHeader: { fontFamily: fonts.heading, fontSize: 16, color: C.foreground, marginTop: 22, marginBottom: 12 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  downloadBtn: { width: 40, height: 40, borderRadius: 12, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },

  emptyText: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, paddingVertical: 20, textAlign: "center" },
  loadingTxt: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  errIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
