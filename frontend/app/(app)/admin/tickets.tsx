// ADM-10 — Support-ticket triage.
//
// Live, admin-gated endpoints (backend/admin_routes.py · TICKETS):
//   list ....... GET   /admin/tickets?status&category&search
//   detail ..... GET   /admin/tickets/{id}
//   add note ... POST  /admin/tickets/{id}/notes   {text}
//   patch ...... PATCH /admin/tickets/{id}          {status?, priority?}
//
// Every row is a real ticket; the ticket owner is notified server-side when a
// note is added or the status changes. Patient reporters are pseudonymized
// upstream. Filters run client-side over the fetched list for snappiness.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, TextInput, Modal,
  Animated, Platform, KeyboardAvoidingView, StyleSheet, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, User, MessageSquare, ChevronRight, Send, Clock,
} from "lucide-react-native";
import { useLocalSearchParams } from "expo-router";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, SearchBar, ChipRow, st } from "./users";

type Note = { by?: string; at?: string; text?: string };
type Ticket = {
  id: string; ticket_id?: string; subject?: string; description?: string;
  category?: string; status?: string; priority?: string; created_at?: string;
  updated_at?: string; notes?: Note[]; userType?: string;
  user?: { id?: string; name?: string; email?: string; role?: string };
};

const STATUS_FILTERS = [
  { key: "all", label: "All" }, { key: "Open", label: "Open" },
  { key: "In Progress", label: "In progress" }, { key: "Resolved", label: "Resolved" },
  { key: "Closed", label: "Closed" },
];
const STATUS_OPTIONS = ["Open", "In Progress", "Resolved", "Closed"] as const;

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function statusColors(status?: string): { fg: string; bg: string } {
  switch (status) {
    case "Open": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "In Progress": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "Resolved": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "Closed": return { fg: C.mutedFg, bg: C.surface };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function AdminTickets() {
  const { open } = useAdminDrawer();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [busy, setBusy] = useState(false);

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
      const res = await api.get("/admin/tickets");
      setTickets(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load tickets. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // Global-search deep link: /admin/tickets?focus=<id> opens that exact record.
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  const consumedFocus = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || typeof focus !== "string" || focus === consumedFocus.current) return;
    const hit = tickets.find((t) => t.id === focus);
    if (hit) { consumedFocus.current = focus; setSelected(hit); }
  }, [focus, tickets]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return tickets.filter((t) => {
      const matchesSearch = !q ||
        (t.subject || "").toLowerCase().includes(q) ||
        (t.ticket_id || "").toLowerCase().includes(q) ||
        (t.user?.name || "").toLowerCase().includes(q);
      const matchesStatus = statusFilter === "all" || (t.status || "Open") === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [tickets, search, statusFilter]);

  const tiles = useMemo(() => [
    { label: "Open", value: tickets.filter((t) => t.status === "Open").length },
    { label: "In progress", value: tickets.filter((t) => t.status === "In Progress").length },
    { label: "Resolved", value: tickets.filter((t) => t.status === "Resolved").length },
    { label: "Total", value: tickets.length },
  ], [tickets]);

  const changeStatus = async (t: Ticket, status: string) => {
    setBusy(true);
    try {
      const res = await api.patch(`/admin/tickets/${t.id}`, { status });
      const fresh: Ticket = res.data || { ...t, status };
      setTickets((prev) => prev.map((x) => (x.id === t.id ? fresh : x)));
      setSelected((prev) => (prev && prev.id === t.id ? fresh : prev));
      showToast(`Ticket set to ${status}`);
    } catch (e) {
      showToast(errMsg(e, "Couldn't update status"));
    } finally { setBusy(false); }
  };

  const addNote = async (t: Ticket, text: string) => {
    setBusy(true);
    try {
      await api.post(`/admin/tickets/${t.id}/notes`, { text });
      const res = await api.get(`/admin/tickets/${t.id}`);
      const fresh: Ticket = res.data;
      setTickets((prev) => prev.map((x) => (x.id === t.id ? fresh : x)));
      setSelected((prev) => (prev && prev.id === t.id ? fresh : prev));
      showToast("Response sent");
    } catch (e) {
      showToast(errMsg(e, "Couldn't send response"));
    } finally { setBusy(false); }
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
        <Hero onMenu={open} onRefresh={onRefresh} count={tickets.length} />
        {loading ? (
          <Loading label="Loading tickets…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              {tiles.map((t) => (
                <View key={t.label} style={st.tile}>
                  <RNText style={st.tileValue}>{t.value}</RNText>
                  <RNText style={st.tileLabel}>{t.label}</RNText>
                </View>
              ))}
            </View>
            <SearchBar value={search} onChange={setSearch} placeholder="Search ticket ID, subject or user…" />
            <ChipRow label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
            <RNText style={st.countLine}>{filtered.length} of {tickets.length} tickets</RNText>
            {filtered.length === 0 ? (
              <EmptyCard message="No tickets match the current filters." />
            ) : (
              <View style={{ gap: 10 }}>
                {filtered.map((t) => {
                  const sc = statusColors(t.status);
                  return (
                    <Pressable key={t.id} testID={`ticket-${t.id}`} onPress={() => setSelected(t)} style={st.row}>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <RNText style={st.rowName} numberOfLines={1}>{t.subject || "—"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{t.ticket_id || t.id} · {t.category || "General"}</RNText>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 }}>
                          <User size={12} color={C.mutedFg} />
                          <RNText style={st.rowOrg} numberOfLines={1}>{t.user?.name || "—"}</RNText>
                          {!!t.user?.role && <View style={st.rolePill}><RNText style={st.rolePillTxt}>{t.user.role}</RNText></View>}
                        </View>
                      </View>
                      <View style={{ alignItems: "flex-end", gap: 6 }}>
                        <View style={[st.badge, { backgroundColor: sc.bg }]}>
                          <RNText style={[st.badgeTxt, { color: sc.fg }]}>{t.status || "Open"}</RNText>
                        </View>
                        <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            )}
          </View>
        )}
      </ScrollView>

      <TicketSheet
        ticket={selected} busy={busy}
        onClose={() => setSelected(null)}
        onChangeStatus={changeStatus}
        onAddNote={addNote}
      />
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
            <RNText style={st.heroTitle} numberOfLines={1}>Issue tracker</RNText>
          </View>
          <Pressable testID="tickets-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Receive, track, action and close all user-reported issues. {count} total.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function TicketSheet({ ticket, busy, onClose, onChangeStatus, onAddNote }: {
  ticket: Ticket | null; busy: boolean; onClose: () => void;
  onChangeStatus: (t: Ticket, status: string) => void;
  onAddNote: (t: Ticket, text: string) => void;
}) {
  const [reply, setReply] = useState("");
  useEffect(() => { setReply(""); }, [ticket?.id]);
  const sc = statusColors(ticket?.status);
  return (
    <Sheet open={!!ticket} onClose={onClose} title="Ticket details">
      {ticket && (
        <View style={{ gap: 14 }}>
          <View style={ts.infoCard}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <RNText style={st.rowSub}>{ticket.ticket_id || ticket.id}</RNText>
              <View style={[st.badge, { backgroundColor: sc.bg }]}>
                <RNText style={[st.badgeTxt, { color: sc.fg }]}>{ticket.status || "Open"}</RNText>
              </View>
            </View>
            <RNText style={ts.subject}>{ticket.subject || "—"}</RNText>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <User size={14} color={C.mutedFg} />
              <RNText style={st.rowSub}>{ticket.user?.name || "—"}</RNText>
              {!!ticket.user?.role && <View style={st.rolePill}><RNText style={st.rolePillTxt}>{ticket.user.role}</RNText></View>}
            </View>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <MessageSquare size={14} color={C.mutedFg} />
              <RNText style={st.rowSub}>{ticket.category || "General"}</RNText>
            </View>
          </View>

          <View>
            <RNText style={ts.sectionLabel}>Description</RNText>
            <View style={ts.descBox}><RNText style={ts.descTxt}>{ticket.description || "—"}</RNText></View>
          </View>

          <View>
            <RNText style={ts.sectionLabel}>Activity</RNText>
            <View style={{ gap: 8 }}>
              <View style={ts.activityRow}>
                <Clock size={13} color={C.mutedFg} />
                <RNText style={ts.activityTxt}><RNText style={{ fontFamily: fonts.semibold }}>{ticket.user?.name || "User"}</RNText> created ticket</RNText>
                <RNText style={ts.activityTime}>{fmtDateTime(ticket.created_at)}</RNText>
              </View>
              {(ticket.notes || []).map((n, i) => (
                <View key={i} style={[ts.activityRow, { backgroundColor: "rgba(123,107,184,0.08)" }]}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <RNText style={ts.activityTxt}><RNText style={{ fontFamily: fonts.semibold, color: C.primary }}>{n.by || "Admin"}</RNText>: {n.text}</RNText>
                    <RNText style={ts.activityTime}>{fmtDateTime(n.at)}</RNText>
                  </View>
                </View>
              ))}
            </View>
          </View>

          <View>
            <RNText style={ts.sectionLabel}>Change status</RNText>
            <View style={st.chipWrap}>
              {STATUS_OPTIONS.map((s) => (
                <Pressable key={s} disabled={busy} onPress={() => onChangeStatus(ticket, s)}
                  style={[st.chip, ticket.status === s && st.chipActive, busy && { opacity: 0.5 }]}>
                  <RNText style={[st.chipTxt, ticket.status === s && st.chipTxtActive]}>{s}</RNText>
                </Pressable>
              ))}
            </View>
          </View>

          {ticket.status !== "Closed" && (
            <View style={{ gap: 10 }}>
              <RNText style={ts.sectionLabel}>Add a response or note</RNText>
              <TextInput
                value={reply} onChangeText={setReply}
                placeholder="Reply to the reporter…" placeholderTextColor="rgba(123,95,115,0.5)"
                multiline style={[st.input, { height: 84, textAlignVertical: "top" }]}
              />
              <Pressable
                onPress={busy || !reply.trim() ? undefined : () => { onAddNote(ticket, reply.trim()); setReply(""); }}
                style={[ts.sendBtn, { opacity: busy || !reply.trim() ? 0.5 : 1 }]}>
                <Send size={15} color={C.primaryFg} />
                <RNText style={ts.sendTxt}>Send response</RNText>
              </Pressable>
            </View>
          )}
        </View>
      )}
    </Sheet>
  );
}

function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <View style={st2.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={st2.sheet}>
            <View style={st2.sheetHeader}>
              <RNText style={st2.sheetTitle}>{title}</RNText>
              <Pressable onPress={onClose} hitSlop={10} style={st2.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
            </View>
            <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 24 }}>
              {children}
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

const st2 = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 18, paddingTop: 16, maxHeight: "88%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 14 },
  sheetTitle: { fontFamily: fonts.display, fontSize: 20, color: C.foreground },
  sheetClose: { width: 34, height: 34, borderRadius: 17, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
});

const ts = StyleSheet.create({
  infoCard: { backgroundColor: C.surface, borderRadius: 14, padding: 14, gap: 8 },
  subject: { fontFamily: fonts.heading, fontSize: 16, color: C.foreground },
  sectionLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground, marginBottom: 8 },
  descBox: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, padding: 12 },
  descTxt: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, lineHeight: 19 },
  activityRow: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.surface, borderRadius: 10, padding: 10 },
  activityTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
  activityTime: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 2 },
  sendBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 999, backgroundColor: C.primary },
  sendTxt: { fontFamily: fonts.bold, fontSize: 14, color: C.primaryFg },
});
