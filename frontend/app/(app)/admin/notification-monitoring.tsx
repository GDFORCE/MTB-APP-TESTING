// ADM-07 — Notification delivery monitoring.
//
// Live, admin-gated endpoints (backend/admin_routes.py · NOTIFICATION MONITORING):
//   stats ....... GET   /admin/notifications/stats
//   log ......... GET   /admin/notifications/log?status&channel&limit  (recipients masked)
//   retry ....... POST  /admin/notifications/{id}/retry   (Failed only)
//   settings .... GET   /admin/notifications/settings
//   patch ....... PATCH /admin/notifications/settings  {visitReminderHours?, medicationReminderMins?, channels?}
//
// Delivery metadata only — message content and patient identity are protected
// server-side (recipients arrive already masked).
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Animated, RefreshControl,
  ActivityIndicator, Switch, StyleSheet,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, RefreshCw, Smartphone, MessageSquare, Mail, Bell, CheckCircle,
  XCircle, Clock,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, ChipRow, st } from "./users";

type Stats = {
  total?: number;
  by_status?: { delivered?: number; failed?: number; pending?: number };
  by_channel?: { push?: number; sms?: number; email?: number };
  failures_24h?: number;
};
type Delivery = {
  id: string; type?: string; channel?: string; recipient?: string; message?: string;
  status?: string; sentAt?: string; deliveredAt?: string; error?: string;
};
type Settings = {
  visitReminderHours?: number; medicationReminderMins?: number;
  channels?: { push?: boolean; sms?: boolean; email?: boolean };
};

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;
const TABS = ["overview", "log", "settings"] as const;
type Tab = typeof TABS[number];
const STATUS_FILTERS = [
  { key: "all", label: "All" }, { key: "Delivered", label: "Delivered" },
  { key: "Failed", label: "Failed" }, { key: "Pending", label: "Pending" },
];
const CHANNEL_FILTERS = [
  { key: "all", label: "All" }, { key: "Push", label: "Push" },
  { key: "SMS", label: "SMS" }, { key: "Email", label: "Email" },
];
const VISIT_HRS = [12, 24, 48, 72];
const MED_MINS = [15, 30, 60];

function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
function channelIcon(ch?: string, size = 14, color = C.mutedFg) {
  switch (ch) {
    case "Push": return <Smartphone size={size} color={color} />;
    case "SMS": return <MessageSquare size={size} color={color} />;
    case "Email": return <Mail size={size} color={color} />;
    default: return <Bell size={size} color={color} />;
  }
}
function statusIcon(status?: string) {
  switch (status) {
    case "Delivered": return <CheckCircle size={14} color={C.success} />;
    case "Failed": return <XCircle size={14} color={C.destructive} />;
    case "Pending": return <Clock size={14} color={C.warning} />;
    default: return null;
  }
}

export default function AdminNotificationMonitoring() {
  const { open } = useAdminDrawer();
  const [stats, setStats] = useState<Stats | null>(null);
  const [log, setLog] = useState<Delivery[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [statusFilter, setStatusFilter] = useState("all");
  const [channelFilter, setChannelFilter] = useState("all");
  const [savingSettings, setSavingSettings] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

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
      const [s, l, cfg] = await Promise.all([
        api.get("/admin/notifications/stats"),
        api.get("/admin/notifications/log", { params: { limit: 200 } }),
        api.get("/admin/notifications/settings"),
      ]);
      setStats(s.data || {});
      setLog(Array.isArray(l.data) ? l.data : []);
      setSettings(cfg.data || {});
    } catch (e) {
      setError(errMsg(e, "Couldn't load notification data. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const filteredLog = log.filter((d) => {
    const matchesStatus = statusFilter === "all" || d.status === statusFilter;
    const matchesChannel = channelFilter === "all" || d.channel === channelFilter;
    return matchesStatus && matchesChannel;
  });
  const failed = log.filter((d) => d.status === "Failed");

  const total = stats?.total || 0;
  const delivered = stats?.by_status?.delivered || 0;
  const failedCount = stats?.by_status?.failed || 0;
  const pending = stats?.by_status?.pending || 0;
  const deliveryRate = total > 0 ? Math.round((delivered / total) * 100) : 0;

  const retry = async (d: Delivery) => {
    setBusyId(d.id);
    try {
      await api.post(`/admin/notifications/${d.id}/retry`);
      showToast("Retry queued");
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't retry delivery"));
    } finally { setBusyId(null); }
  };

  const saveSettings = async (patch: Settings) => {
    setSavingSettings(true);
    try {
      const res = await api.patch("/admin/notifications/settings", patch);
      setSettings(res.data || settings);
      showToast("Settings saved");
    } catch (e) {
      showToast(errMsg(e, "Couldn't save settings"));
    } finally { setSavingSettings(false); }
  };
  const setChannel = (key: "push" | "sms" | "email", value: boolean) =>
    saveSettings({ channels: { ...(settings?.channels || {}), [key]: value } });

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} />
        {loading ? (
          <Loading label="Loading notifications…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              <StatTile value={delivered} label="Delivered" fg={C.success} />
              <StatTile value={failedCount} label="Failed" fg={C.destructive} />
              <StatTile value={pending} label="Pending" fg={C.warning} />
              <StatTile value={`${deliveryRate}%`} label="Delivery rate" fg={C.primary} />
            </View>

            {/* Tabs */}
            <View style={nm.tabBar}>
              {TABS.map((t) => (
                <Pressable key={t} onPress={() => setTab(t)} style={[nm.tab, tab === t && nm.tabActive]}>
                  <RNText style={[nm.tabTxt, tab === t && nm.tabTxtActive]}>{t === "log" ? "Delivery log" : t[0].toUpperCase() + t.slice(1)}</RNText>
                </Pressable>
              ))}
            </View>

            {tab === "overview" && (
              <View style={{ gap: 12, marginTop: 14 }}>
                <View style={[st.card, { gap: 12 }]}>
                  <RNText style={nm.cardTitle}>Delivery status</RNText>
                  <StatusBar2 label="Delivered" icon={<CheckCircle size={14} color={C.success} />} value={delivered} total={total} color={C.success} />
                  <StatusBar2 label="Pending" icon={<Clock size={14} color={C.warning} />} value={pending} total={total} color={C.warning} />
                  <StatusBar2 label="Failed" icon={<XCircle size={14} color={C.destructive} />} value={failedCount} total={total} color={C.destructive} />
                </View>
                <View style={[st.card, { gap: 12 }]}>
                  <RNText style={nm.cardTitle}>By channel</RNText>
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <ChannelTile icon={<Smartphone size={18} color={C.info} />} value={stats?.by_channel?.push || 0} label="Push" bg="rgba(123,107,184,0.08)" />
                    <ChannelTile icon={<MessageSquare size={18} color={C.success} />} value={stats?.by_channel?.sms || 0} label="SMS" bg="rgba(92,154,110,0.10)" />
                    <ChannelTile icon={<Mail size={18} color={C.violet} />} value={stats?.by_channel?.email || 0} label="Email" bg="rgba(142,91,180,0.08)" />
                  </View>
                </View>
                <View style={[st.card, { gap: 10 }]}>
                  <RNText style={[nm.cardTitle, { color: C.destructive }]}>Failed notifications</RNText>
                  {failed.length === 0 ? (
                    <RNText style={nm.emptyInline}>No failed deliveries.</RNText>
                  ) : failed.slice(0, 20).map((d) => (
                    <View key={d.id} style={nm.failedRow}>
                      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                        <RNText style={nm.failedRecipient} numberOfLines={1}>{d.recipient || "—"}</RNText>
                        <Pressable onPress={busyId === d.id ? undefined : () => retry(d)} style={nm.retryGhost}>
                          {busyId === d.id ? <ActivityIndicator size="small" color={C.primary} /> : <><RefreshCw size={12} color={C.primary} /><RNText style={nm.retryGhostTxt}>Retry</RNText></>}
                        </Pressable>
                      </View>
                      <RNText style={nm.failedMsg} numberOfLines={1}>{d.type || "Notification"} · {d.channel}</RNText>
                      {!!d.error && <RNText style={nm.failedErr}>Error: {d.error}</RNText>}
                    </View>
                  ))}
                </View>
              </View>
            )}

            {tab === "log" && (
              <View style={{ marginTop: 14 }}>
                <ChipRow label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
                <ChipRow label="CHANNEL" chips={CHANNEL_FILTERS} value={channelFilter} onChange={setChannelFilter} />
                <RNText style={st.countLine}>{filteredLog.length} of {log.length} deliveries</RNText>
                {filteredLog.length === 0 ? (
                  <EmptyCard message="No notifications match the current filters." />
                ) : (
                  <View style={{ gap: 8 }}>
                    {filteredLog.map((d) => (
                      <View key={d.id} style={[st.card, { padding: 12 }]}>
                        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <RNText style={nm.logType} numberOfLines={1}>{d.type || "Notification"}</RNText>
                            <RNText style={nm.logRecipient} numberOfLines={1}>{d.recipient || "—"}</RNText>
                          </View>
                          <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                            {channelIcon(d.channel)}
                            <RNText style={nm.logChannel}>{d.channel}</RNText>
                          </View>
                        </View>
                        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 8 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
                            {statusIcon(d.status)}
                            <RNText style={nm.logStatus}>{d.status}</RNText>
                            <RNText style={nm.logTime}> · {fmtDateTime(d.sentAt)}</RNText>
                          </View>
                          {d.status === "Failed" && (
                            <Pressable onPress={busyId === d.id ? undefined : () => retry(d)} style={nm.retryBtn}>
                              {busyId === d.id ? <ActivityIndicator size="small" color={C.primary} /> : <><RefreshCw size={12} color={C.primary} /><RNText style={nm.retryGhostTxt}>Retry</RNText></>}
                            </Pressable>
                          )}
                        </View>
                        {d.status === "Failed" && !!d.error && <RNText style={nm.failedErr}>Error: {d.error}</RNText>}
                      </View>
                    ))}
                  </View>
                )}
              </View>
            )}

            {tab === "settings" && settings && (
              <View style={{ gap: 12, marginTop: 14 }}>
                <View style={[st.card, { gap: 12 }]}>
                  <RNText style={nm.cardTitle}>Reminder timing</RNText>
                  <RNText style={nm.settingLabel}>Visit reminder (before)</RNText>
                  <View style={st.chipWrap}>
                    {VISIT_HRS.map((h) => (
                      <Pressable key={h} disabled={savingSettings} onPress={() => saveSettings({ visitReminderHours: h })}
                        style={[st.chip, settings.visitReminderHours === h && st.chipActive, savingSettings && { opacity: 0.6 }]}>
                        <RNText style={[st.chipTxt, settings.visitReminderHours === h && st.chipTxtActive]}>{h}h</RNText>
                      </Pressable>
                    ))}
                  </View>
                  <RNText style={nm.settingLabel}>Medication reminder</RNText>
                  <View style={st.chipWrap}>
                    {MED_MINS.map((m) => (
                      <Pressable key={m} disabled={savingSettings} onPress={() => saveSettings({ medicationReminderMins: m })}
                        style={[st.chip, settings.medicationReminderMins === m && st.chipActive, savingSettings && { opacity: 0.6 }]}>
                        <RNText style={[st.chipTxt, settings.medicationReminderMins === m && st.chipTxtActive]}>{m}m</RNText>
                      </Pressable>
                    ))}
                  </View>
                </View>
                <View style={[st.card, { gap: 6 }]}>
                  <RNText style={nm.cardTitle}>Channels</RNText>
                  <ChannelSwitch icon={<Smartphone size={16} color={C.mutedFg} />} label="Push notifications" value={!!settings.channels?.push} disabled={savingSettings} onChange={(v) => setChannel("push", v)} />
                  <ChannelSwitch icon={<MessageSquare size={16} color={C.mutedFg} />} label="SMS notifications" value={!!settings.channels?.sms} disabled={savingSettings} onChange={(v) => setChannel("sms", v)} />
                  <ChannelSwitch icon={<Mail size={16} color={C.mutedFg} />} label="Email notifications" value={!!settings.channels?.email} disabled={savingSettings} onChange={(v) => setChannel("email", v)} />
                </View>
              </View>
            )}
          </View>
        )}
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
            <RNText style={st.heroTitle} numberOfLines={1}>Notification delivery</RNText>
          </View>
          <Pressable testID="notif-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Delivery metadata only · message content and patient identity are protected.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function StatTile({ value, label, fg }: { value: number | string; label: string; fg: string }) {
  return (
    <View style={st.tile}>
      <RNText style={[st.tileValue, { color: fg }]}>{value}</RNText>
      <RNText style={st.tileLabel}>{label}</RNText>
    </View>
  );
}
function StatusBar2({ label, icon, value, total, color }: { label: string; icon: React.ReactNode; value: number; total: number; color: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>{icon}<RNText style={nm.rowLabel}>{label}</RNText></View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <RNText style={nm.rowValue}>{value}</RNText>
        <View style={nm.track}><View style={[nm.trackFill, { width: `${pct}%`, backgroundColor: color }]} /></View>
      </View>
    </View>
  );
}
function ChannelTile({ icon, value, label, bg }: { icon: React.ReactNode; value: number; label: string; bg: string }) {
  return (
    <View style={[nm.channelTile, { backgroundColor: bg }]}>
      {icon}
      <RNText style={nm.channelValue}>{value}</RNText>
      <RNText style={nm.channelLabel}>{label}</RNText>
    </View>
  );
}
function ChannelSwitch({ icon, label, value, disabled, onChange }: { icon: React.ReactNode; label: string; value: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <View style={nm.switchRow}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flex: 1 }}>{icon}<RNText style={nm.switchLabel}>{label}</RNText></View>
      <Switch value={value} disabled={disabled} onValueChange={onChange} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
    </View>
  );
}

const nm = StyleSheet.create({
  tabBar: { flexDirection: "row", gap: 6, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, padding: 4, marginTop: 16 },
  tab: { flex: 1, paddingVertical: 9, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  tabActive: { backgroundColor: C.primary },
  tabTxt: { fontFamily: fonts.semibold, fontSize: 12, color: C.mutedFg },
  tabTxtActive: { color: C.primaryFg },
  cardTitle: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  rowLabel: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground },
  rowValue: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground, fontVariant: ["tabular-nums"], minWidth: 34, textAlign: "right" },
  track: { width: 72, height: 8, borderRadius: 4, backgroundColor: C.border, overflow: "hidden" },
  trackFill: { height: 8, borderRadius: 4 },
  channelTile: { flex: 1, alignItems: "center", borderRadius: 12, paddingVertical: 12, gap: 4 },
  channelValue: { fontFamily: fonts.heading, fontSize: 18, color: C.foreground },
  channelLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  failedRow: { backgroundColor: "rgba(192,57,43,0.06)", borderRadius: 10, padding: 10, gap: 3 },
  failedRecipient: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground, flex: 1 },
  failedMsg: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  failedErr: { fontFamily: fonts.medium, fontSize: 11, color: C.destructive, marginTop: 4 },
  retryGhost: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 26, borderRadius: 8 },
  retryBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, height: 30, borderRadius: 8, borderWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  retryGhostTxt: { fontFamily: fonts.bold, fontSize: 11, color: C.primary },
  emptyInline: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, paddingVertical: 6 },
  logType: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  logRecipient: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 1 },
  logChannel: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  logStatus: { fontFamily: fonts.medium, fontSize: 12, color: C.foreground },
  logTime: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  settingLabel: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, marginTop: 4 },
  switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 8 },
  switchLabel: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
});
