// ADM-11 — System alerts.
//
// Live, admin-gated endpoints (backend/admin_routes.py · SYSTEM ALERTS):
//   list ........ GET  /admin/alerts?status&severity
//   retry ....... POST /admin/alerts/{id}/retry
//   notify ...... POST /admin/alerts/{id}/notify-user
//   escalate .... POST /admin/alerts/{id}/escalate
//   resolve ..... POST /admin/alerts/{id}/resolve   {note?}
//
// Auto-generated when a background process fails; these do NOT auto-resolve.
// Active alerts and resolved history come from the same list, split by status.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, TextInput, Modal,
  Animated, Platform, KeyboardAvoidingView, RefreshControl, ActivityIndicator, StyleSheet,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, AlertTriangle, RefreshCw, Bell, ArrowUpRight, CheckCircle2,
  ChevronDown, ChevronUp,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, st } from "./users";

type Alert = {
  id: string; type?: string; description?: string; affected?: string;
  severity?: string; status?: string; timestamp?: string; retries?: number;
  escalated?: boolean; resolved_at?: string; resolved_by?: string; resolution_note?: string;
};

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function sevColors(sev?: string): { fg: string; bg: string } {
  switch ((sev || "").toLowerCase()) {
    case "critical":
    case "high": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "medium": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    default: return { fg: C.info, bg: "rgba(123,107,184,0.12)" };
  }
}
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
function retryLabel(type?: string): string {
  switch (type) {
    case "AI extraction": return "Re-trigger extraction";
    case "OTP failure": return "Resend OTP";
    case "Invite failure": return "Resend invite";
    default: return "Retry";
  }
}

export default function AdminAlerts() {
  const { open } = useAdminDrawer();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [resolving, setResolving] = useState<Alert | null>(null);

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

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get("/admin/alerts");
      setAlerts(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load alerts. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const active = alerts.filter((a) => a.status !== "resolved");
  const resolved = alerts.filter((a) => a.status === "resolved");
  const hasHigh = active.some((a) => ["high", "critical"].includes((a.severity || "").toLowerCase()));
  const tiles = [
    { label: "Active", value: active.length, fg: C.primary },
    { label: "Critical / high", value: active.filter((a) => ["high", "critical"].includes((a.severity || "").toLowerCase())).length, fg: C.destructive },
    { label: "Medium / low", value: active.filter((a) => !["high", "critical"].includes((a.severity || "").toLowerCase())).length, fg: C.warning },
    { label: "Resolved", value: resolved.length, fg: C.success },
  ];

  const act = async (a: Alert, verb: "retry" | "notify-user" | "escalate", okMsg: string) => {
    setBusyId(a.id);
    try {
      await api.post(`/admin/alerts/${a.id}/${verb}`);
      showToast(okMsg);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Action failed"));
    } finally { setBusyId(null); }
  };

  const resolve = async (a: Alert, note: string) => {
    setBusyId(a.id);
    try {
      await api.post(`/admin/alerts/${a.id}/resolve`, { note });
      setResolving(null);
      showToast(`${a.type || "Alert"} resolved`);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't resolve alert"));
    } finally { setBusyId(null); }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} count={active.length} />
        {loading ? (
          <Loading label="Loading alerts…" />
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

            {hasHigh && (
              <View style={al.warnBanner}>
                <AlertTriangle size={15} color={C.primaryFg} />
                <RNText style={al.warnTxt}>Active system alerts require admin attention. These do not auto-resolve.</RNText>
              </View>
            )}

            <RNText style={al.sectionTitle}>Active alerts</RNText>
            {active.length === 0 ? (
              <View style={al.allClear}>
                <CheckCircle2 size={18} color={C.success} />
                <RNText style={al.allClearTxt}>All clear — no active alerts</RNText>
              </View>
            ) : (
              <View style={{ gap: 10 }}>
                {active.map((a) => {
                  const sc = sevColors(a.severity);
                  const busy = busyId === a.id;
                  return (
                    <View key={a.id} testID={`alert-${a.id}`} style={[st.card, { padding: 14 }]}>
                      <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 8 }}>
                        <View style={[al.dot, { backgroundColor: sc.fg }]} />
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                            <RNText style={al.alertId}>{a.id.slice(0, 8)}</RNText>
                            <View style={[st.badge, { backgroundColor: sc.bg }]}>
                              <RNText style={[st.badgeTxt, { color: sc.fg, textTransform: "capitalize" }]}>{a.severity || "low"}</RNText>
                            </View>
                          </View>
                          {!!a.type && <View style={[st.rolePill, { alignSelf: "flex-start", marginTop: 4 }]}><RNText style={st.rolePillTxt}>{a.type}</RNText></View>}
                          <RNText style={al.desc}>{a.description}</RNText>
                          <RNText style={al.meta}>{a.affected || "—"} · {fmtDateTime(a.timestamp)}{a.escalated ? " · escalated" : ""}</RNText>
                        </View>
                      </View>
                      <View style={al.actionGrid}>
                        <ActBtn label={retryLabel(a.type)} icon={RefreshCw} solid disabled={busy} onPress={() => act(a, "retry", `${retryLabel(a.type)} queued`)} />
                        <ActBtn label="Notify user" icon={Bell} disabled={busy} onPress={() => act(a, "notify-user", "User notified")} />
                        <ActBtn label="Escalate" icon={ArrowUpRight} tone={C.warning} disabled={busy} onPress={() => act(a, "escalate", "Alert escalated")} />
                        <ActBtn label="Resolve" icon={CheckCircle2} tone={C.success} solid disabled={busy} onPress={() => setResolving(a)} />
                      </View>
                    </View>
                  );
                })}
              </View>
            )}

            {/* Alert history */}
            <Pressable onPress={() => setHistoryOpen((v) => !v)} style={al.historyHeader}>
              <RNText style={al.sectionTitleInline}>Alert history ({resolved.length})</RNText>
              {historyOpen ? <ChevronUp size={18} color={C.mutedFg} /> : <ChevronDown size={18} color={C.mutedFg} />}
            </Pressable>
            {historyOpen && (
              resolved.length === 0 ? (
                <EmptyCard message="No resolved alerts yet." />
              ) : (
                <View style={{ gap: 8 }}>
                  {resolved.map((h) => (
                    <View key={h.id} style={[st.card, { padding: 12 }]}>
                      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                        <RNText style={al.alertId}>{h.type || h.id.slice(0, 8)}</RNText>
                        <View style={[st.badge, { backgroundColor: "rgba(92,154,110,0.15)" }]}>
                          <RNText style={[st.badgeTxt, { color: C.success }]}>Resolved</RNText>
                        </View>
                      </View>
                      <RNText style={al.meta}>{fmtDateTime(h.resolved_at)}{h.resolved_by ? ` · ${h.resolved_by}` : ""}</RNText>
                      {!!h.resolution_note && <RNText style={al.histNote}>{h.resolution_note}</RNText>}
                    </View>
                  ))}
                </View>
              )
            )}
          </View>
        )}
      </ScrollView>

      <ResolveSheet alert={resolving} busy={busyId === resolving?.id} onClose={() => setResolving(null)} onConfirm={resolve} />
      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Hero({ onMenu, onRefresh, count }: { onMenu: () => void; onRefresh: () => void; count: number }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>System alerts</RNText>
          </View>
          <Pressable testID="alerts-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Auto-generated when background processes fail · {count} active.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function ActBtn({ label, icon: Icon, tone, solid, disabled, onPress }: { label: string; icon: any; tone?: string; solid?: boolean; disabled?: boolean; onPress: () => void }) {
  const color = tone || C.primary;
  return (
    <Pressable onPress={disabled ? undefined : onPress}
      style={[al.actBtn, solid ? { backgroundColor: color } : { borderWidth: 1, borderColor: color + "44", backgroundColor: C.card }, disabled && { opacity: 0.5 }]}>
      <Icon size={13} color={solid ? C.primaryFg : color} />
      <RNText style={[al.actTxt, { color: solid ? C.primaryFg : color }]} numberOfLines={1}>{label}</RNText>
    </Pressable>
  );
}

function ResolveSheet({ alert, busy, onClose, onConfirm }: { alert: Alert | null; busy: boolean; onClose: () => void; onConfirm: (a: Alert, note: string) => void }) {
  const [note, setNote] = useState("");
  useEffect(() => { setNote(""); }, [alert?.id]);
  return (
    <Modal visible={!!alert} transparent animationType="slide" onRequestClose={onClose}>
      <View style={al.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={al.sheet}>
            <View style={al.sheetHeader}>
              <RNText style={al.sheetTitle}>Resolve alert</RNText>
              <Pressable onPress={onClose} hitSlop={10} style={al.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
            </View>
            {alert && (
              <View style={{ gap: 12 }}>
                <RNText style={al.desc}>{alert.description}</RNText>
                <RNText style={al.fieldLabel}>Resolution note (optional, logged)</RNText>
                <TextInput value={note} onChangeText={setNote} placeholder="What resolved this?" placeholderTextColor="rgba(123,95,115,0.5)"
                  multiline style={[st.card, al.input]} />
                <View style={{ flexDirection: "row", gap: 10 }}>
                  <Pressable onPress={onClose} style={[al.cancelBtn, { flex: 1 }]}><RNText style={al.cancelTxt}>Cancel</RNText></Pressable>
                  <Pressable onPress={busy ? undefined : () => onConfirm(alert, note.trim())} style={[al.confirmBtn, { flex: 1, opacity: busy ? 0.5 : 1 }]}>
                    {busy ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={al.confirmTxt}>Confirm resolve</RNText>}
                  </Pressable>
                </View>
              </View>
            )}
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

const al = StyleSheet.create({
  warnBanner: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.destructive, borderRadius: 12, padding: 12, marginTop: 16 },
  warnTxt: { flex: 1, fontFamily: fonts.medium, fontSize: 12, color: C.primaryFg },
  sectionTitle: { fontFamily: fonts.heading, fontSize: 15, color: C.primary, marginTop: 18, marginBottom: 10 },
  sectionTitleInline: { fontFamily: fonts.heading, fontSize: 15, color: C.primary },
  allClear: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "rgba(92,154,110,0.10)", borderRadius: 14, borderWidth: 1, borderColor: "rgba(92,154,110,0.25)", padding: 14 },
  allClearTxt: { fontFamily: fonts.semibold, fontSize: 13, color: C.success },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 5 },
  alertId: { fontFamily: fonts.bold, fontSize: 12, color: C.foreground },
  desc: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, marginTop: 6, lineHeight: 19 },
  meta: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 6 },
  histNote: { fontFamily: fonts.regular, fontSize: 12, color: C.foreground, marginTop: 4 },
  actionGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  actBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, height: 34, borderRadius: 999, paddingHorizontal: 12, minWidth: "47%", flexGrow: 1 },
  actTxt: { fontFamily: fonts.bold, fontSize: 12 },
  historyHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, padding: 14, marginTop: 18, marginBottom: 10 },
  overlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 18, paddingTop: 16, paddingBottom: 28 },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 14 },
  sheetTitle: { fontFamily: fonts.display, fontSize: 20, color: C.foreground },
  sheetClose: { width: 34, height: 34, borderRadius: 17, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { height: 84, textAlignVertical: "top", paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.success, alignItems: "center", justifyContent: "center" },
  confirmTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },
});
