// ADM-14 — Messages: broadcast compose + sent log + reply threads.
//
// Live, admin-gated endpoints (backend/admin_routes.py · MESSAGES):
//   recipient count . GET  /admin/messages/recipient-count?target=   (preview, no send)
//   send ............ POST /admin/messages {type,subject,body,target,allowReplies,scheduleAt}
//   sent list ....... GET  /admin/messages?box=sent
//   replies ......... GET  /admin/messages/{id}/replies
//   respond ......... POST /admin/messages/replies/{id}/respond {text}
//   resolve ......... POST /admin/messages/replies/{id}/resolve
//
// The recipient preview resolves the audience live (before sending) so the admin
// sees exactly how many users a target expression reaches. Targets are built as
// "all" | "<kind>:<value>" (role/entity/org/site/trial/user) — the same grammar
// the backend resolves.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Modal, Animated,
  RefreshControl, StyleSheet, ActivityIndicator, Switch,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, Send, Users, AlertTriangle, CheckCircle2,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, Input, st } from "./users";

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const MSG_TYPES = [
  { key: "general", label: "General" },
  { key: "compliance", label: "Compliance" },
  { key: "system", label: "System" },
  { key: "targeted", label: "Targeted" },
  { key: "urgent", label: "Urgent" },
];
const TARGET_KINDS = [
  { key: "all", label: "All users", needsValue: false },
  { key: "role", label: "By role", needsValue: true },
  { key: "entity", label: "By entity", needsValue: true },
  { key: "org", label: "By organization", needsValue: true },
  { key: "site", label: "By site", needsValue: true },
  { key: "trial", label: "By trial", needsValue: true },
  { key: "user", label: "Individual", needsValue: true },
];
const ROLE_VALUES = ["sponsor", "cro", "smo", "site", "pi", "crc", "patient", "admin"];
const ENTITY_VALUES = ["sponsor", "cro", "smo", "site"];

type Broadcast = {
  id: string; type?: string; subject?: string; body?: string; target?: string;
  status?: string; sent_at?: string; scheduleAt?: string | null; created_at?: string;
  recipients_count?: number; read_count?: number; replies_count?: number; allowReplies?: boolean;
};
type Reply = {
  id: string; broadcast_id?: string; user_id?: string; user_name?: string; from_name?: string;
  message?: string; text?: string; status?: string; created_at?: string;
  responses?: { by?: string; at?: string; text?: string }[];
  _parentSubject?: string;
};

function typeMeta(t?: string): { fg: string; bg: string } {
  switch (t) {
    case "compliance": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "system":
    case "urgent": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "targeted": return { fg: C.info, bg: "rgba(123,107,184,0.12)" };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function statusMeta(s?: string): { fg: string; bg: string } {
  switch (s) {
    case "scheduled": return { fg: C.info, bg: "rgba(123,107,184,0.12)" };
    case "sent": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export default function AdminMessages() {
  const { open } = useAdminDrawer();
  const [tab, setTab] = useState<"compose" | "sent" | "replies">("compose");

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

  // ── Sent + replies data ──
  const [sent, setSent] = useState<Broadcast[]>([]);
  const [replies, setReplies] = useState<Reply[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSent = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get("/admin/messages", { params: { box: "sent" } });
      const list: Broadcast[] = Array.isArray(res.data) ? res.data : [];
      setSent(list);
      // Aggregate reply threads across every broadcast that has replies.
      const withReplies = list.filter((b) => (b.replies_count || 0) > 0);
      const all: Reply[] = [];
      for (const b of withReplies) {
        try {
          const r = await api.get(`/admin/messages/${b.id}/replies`);
          for (const rep of (Array.isArray(r.data) ? r.data : [])) {
            all.push({ ...rep, _parentSubject: b.subject });
          }
        } catch { /* skip a thread that fails to load */ }
      }
      setReplies(all);
    } catch (e) {
      setError(errMsg(e, "Couldn't load messages. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { loadSent(); }, [loadSent]);
  const onRefresh = async () => { setRefreshing(true); await loadSent(); setRefreshing(false); };

  const openReplies = replies.filter((r) => r.status !== "resolved");

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
          <View style={mg.tabs}>
            {([["compose", "Compose"], ["sent", "Sent"], ["replies", "Replies"]] as const).map(([k, label]) => (
              <Pressable key={k} onPress={() => setTab(k)} style={[mg.tab, tab === k && mg.tabActive]}>
                <RNText style={[mg.tabTxt, tab === k && mg.tabTxtActive]}>{label}</RNText>
                {k === "replies" && openReplies.length > 0 && (
                  <View style={mg.tabBadge}><RNText style={mg.tabBadgeTxt}>{openReplies.length}</RNText></View>
                )}
              </Pressable>
            ))}
          </View>

          {tab === "compose" && <Compose showToast={showToast} onSent={() => { setTab("sent"); loadSent(); }} />}

          {tab === "sent" && (
            loading ? <Loading label="Loading sent messages…" />
              : error ? <ErrorCard message={error} onRetry={loadSent} />
              : sent.length === 0 ? <View style={{ marginTop: 14 }}><EmptyCard message="No broadcasts sent yet." /></View>
              : (
                <View style={{ gap: 10, marginTop: 14 }}>
                  {sent.map((m) => {
                    const tm = typeMeta(m.type); const sm = statusMeta(m.status);
                    const total = m.recipients_count || 0;
                    return (
                      <View key={m.id} style={st.card}>
                        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                          <View style={[mg.pill, { backgroundColor: tm.bg }]}><RNText style={[mg.pillTxt, { color: tm.fg }]}>{m.type || "general"}</RNText></View>
                          <View style={[mg.pill, { backgroundColor: sm.bg }]}><RNText style={[mg.pillTxt, { color: sm.fg }]}>{m.status || "sent"}</RNText></View>
                        </View>
                        <RNText style={mg.sentSubject}>{m.subject}</RNText>
                        <RNText style={mg.sentMeta}>{m.target || "all"} · {total} recipient{total === 1 ? "" : "s"} · {fmtDateTime(m.status === "scheduled" ? m.scheduleAt : m.sent_at)}</RNText>
                        <View style={mg.sentFooter}>
                          <RNText style={mg.sentStat}>Read {m.read_count || 0}/{total}</RNText>
                          {(m.replies_count || 0) > 0 && <RNText style={[mg.sentStat, { color: C.info }]}>{m.replies_count} repl{m.replies_count === 1 ? "y" : "ies"}</RNText>}
                        </View>
                      </View>
                    );
                  })}
                </View>
              )
          )}

          {tab === "replies" && (
            loading ? <Loading label="Loading replies…" />
              : error ? <ErrorCard message={error} onRetry={loadSent} />
              : replies.length === 0 ? <View style={{ marginTop: 14 }}><EmptyCard message="No replies yet." /></View>
              : (
                <View style={{ gap: 10, marginTop: 14 }}>
                  {replies.map((r) => (
                    <ReplyCard key={r.id} reply={r} showToast={showToast} onChanged={loadSent} />
                  ))}
                </View>
              )
          )}
        </View>
      </ScrollView>

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Compose({ showToast, onSent }: { showToast: (m: string) => void; onSent: () => void }) {
  const [type, setType] = useState("general");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [kind, setKind] = useState("all");
  const [value, setValue] = useState("");
  const [allowReplies, setAllowReplies] = useState(true);
  const [scheduled, setScheduled] = useState(false);
  const [scheduleAt, setScheduleAt] = useState("");
  const [count, setCount] = useState<number | null>(null);
  const [counting, setCounting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sending, setSending] = useState(false);

  const kindMeta = TARGET_KINDS.find((k) => k.key === kind)!;
  const targetReady = !kindMeta.needsValue || value.trim().length > 0;
  const targetExpr = kind === "all" ? "all" : `${kind}:${value.trim()}`;
  const canSend = subject.trim().length > 0 && body.trim().length > 0 && targetReady && (!scheduled || scheduleAt.trim().length > 0);

  // Live recipient preview (debounced) — resolves the audience without sending.
  useEffect(() => {
    if (!targetReady) { setCount(null); return; }
    let cancelled = false;
    setCounting(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.get("/admin/messages/recipient-count", { params: { target: targetExpr } });
        if (!cancelled) setCount(res.data?.count ?? null);
      } catch {
        if (!cancelled) setCount(null);
      } finally { if (!cancelled) setCounting(false); }
    }, 400);
    return () => { cancelled = true; clearTimeout(t); };
  }, [targetExpr, targetReady]);

  const send = async () => {
    setSending(true);
    try {
      const payload: any = { type, subject: subject.trim(), body: body.trim(), target: targetExpr, allowReplies };
      if (scheduled && scheduleAt.trim()) payload.scheduleAt = scheduleAt.trim();
      const res = await api.post("/admin/messages", payload);
      setConfirmOpen(false);
      showToast(res.data?.status === "scheduled" ? "Broadcast scheduled" : `Broadcast sent to ${res.data?.recipients_count ?? count ?? 0} users`);
      setSubject(""); setBody(""); setValue(""); setKind("all"); setScheduled(false); setScheduleAt("");
      onSent();
    } catch (e) {
      setConfirmOpen(false);
      showToast(errMsg(e, "Couldn't send broadcast"));
    } finally { setSending(false); }
  };

  return (
    <View style={[st.card, { marginTop: 14, gap: 14 }]}>
      <Field label="Message type">
        <View style={mg.chipWrap}>
          {MSG_TYPES.map((m) => (
            <Pressable key={m.key} onPress={() => setType(m.key)} style={[st.chip, type === m.key && st.chipActive]}>
              <RNText style={[st.chipTxt, type === m.key && st.chipTxtActive]}>{m.label}</RNText>
            </Pressable>
          ))}
        </View>
      </Field>

      <Field label="Subject / title">
        <Input value={subject} onChangeText={(v) => setSubject(v.slice(0, 120))} placeholder="Short headline" />
        <RNText style={mg.counter}>{subject.length}/120</RNText>
      </Field>

      <Field label="Message body">
        <Input value={body} onChangeText={(v) => setBody(v.slice(0, 2000))} placeholder="Full message content…" multiline style={{ height: 120 }} />
        <RNText style={mg.counter}>{body.length}/2000</RNText>
      </Field>

      <Field label="Recipients — target level">
        <View style={mg.chipWrap}>
          {TARGET_KINDS.map((k) => (
            <Pressable key={k.key} onPress={() => { setKind(k.key); setValue(""); }} style={[st.chip, kind === k.key && st.chipActive]}>
              <RNText style={[st.chipTxt, kind === k.key && st.chipTxtActive]}>{k.label}</RNText>
            </Pressable>
          ))}
        </View>
        {kind === "role" && (
          <View style={[mg.chipWrap, { marginTop: 8 }]}>
            {ROLE_VALUES.map((r) => (
              <Pressable key={r} onPress={() => setValue(r)} style={[mg.subChip, value === r && mg.subChipActive]}>
                <RNText style={[mg.subChipTxt, value === r && mg.subChipTxtActive]}>{r}</RNText>
              </Pressable>
            ))}
          </View>
        )}
        {kind === "entity" && (
          <View style={[mg.chipWrap, { marginTop: 8 }]}>
            {ENTITY_VALUES.map((r) => (
              <Pressable key={r} onPress={() => setValue(r)} style={[mg.subChip, value === r && mg.subChipActive]}>
                <RNText style={[mg.subChipTxt, value === r && mg.subChipTxtActive]}>{r}</RNText>
              </Pressable>
            ))}
          </View>
        )}
        {(kind === "org" || kind === "site" || kind === "trial" || kind === "user") && (
          <View style={{ marginTop: 8 }}>
            <Input value={value} onChangeText={setValue} autoCapitalize="none"
              placeholder={kind === "trial" ? "Trial ID" : kind === "user" ? "User ID" : kind === "site" ? "Site name" : "Organization name"} />
          </View>
        )}
        <View style={mg.previewBox}>
          <Users size={14} color={C.info} />
          {counting ? <ActivityIndicator size="small" color={C.info} />
            : <RNText style={mg.previewTxt}>{targetReady ? `This message will reach ${count ?? "—"} user${count === 1 ? "" : "s"}.` : "Choose a target value to preview recipients."}</RNText>}
        </View>
      </Field>

      <Pressable onPress={() => setAllowReplies(!allowReplies)} style={st.switchRow}>
        <RNText style={st.switchLabel}>Allow replies</RNText>
        <Switch value={allowReplies} onValueChange={setAllowReplies} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
      </Pressable>
      <Pressable onPress={() => setScheduled(!scheduled)} style={st.switchRow}>
        <RNText style={st.switchLabel}>Schedule for later</RNText>
        <Switch value={scheduled} onValueChange={setScheduled} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
      </Pressable>
      {scheduled && (
        <View style={{ gap: 6 }}>
          <Input value={scheduleAt} onChangeText={setScheduleAt} autoCapitalize="none" placeholder="2026-07-15T09:00" />
          <RNText style={mg.hint}>ISO 8601 date-time (UTC). Leave the toggle off to send now.</RNText>
        </View>
      )}

      <Pressable onPress={canSend ? () => setConfirmOpen(true) : undefined} style={[mg.sendBtn, { opacity: canSend ? 1 : 0.5 }]}>
        <Send size={16} color={C.primaryFg} /><RNText style={mg.sendBtnTxt}>{scheduled ? "Schedule broadcast" : "Send broadcast"}</RNText>
      </Pressable>

      <Modal visible={confirmOpen} transparent animationType="fade" onRequestClose={() => setConfirmOpen(false)}>
        <View style={mg.confirmOverlay}>
          <View style={mg.confirmCard}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <AlertTriangle size={18} color={C.warning} />
              <RNText style={mg.confirmTitle}>Confirm {scheduled ? "schedule" : "send"}</RNText>
            </View>
            <RNText style={mg.confirmBody}>
              You are about to {scheduled ? "schedule" : "send"} a {MSG_TYPES.find((m) => m.key === type)?.label} message to {count ?? "—"} user{count === 1 ? "" : "s"}. This action cannot be undone.
            </RNText>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <Pressable onPress={() => setConfirmOpen(false)} style={[st.cancelBtn, { flex: 1 }]}><RNText style={st.cancelBtnTxt}>Cancel</RNText></Pressable>
              <Pressable onPress={sending ? undefined : send} style={[st.confirmBtn, { flex: 1, backgroundColor: C.primary, opacity: sending ? 0.6 : 1 }]}>
                {sending ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={st.confirmBtnTxt}>Confirm</RNText>}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function ReplyCard({ reply, showToast, onChanged }: { reply: Reply; showToast: (m: string) => void; onChanged: () => void }) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const resolved = reply.status === "resolved";
  const from = reply.user_name || reply.from_name || "User";
  const message = reply.message || reply.text || "";

  const respond = async () => {
    if (!draft.trim()) return;
    setBusy(true);
    try {
      await api.post(`/admin/messages/replies/${reply.id}/respond`, { text: draft.trim() });
      showToast("Reply sent to user");
      setDraft("");
      onChanged();
    } catch (e) { showToast(errMsg(e, "Couldn't send reply")); } finally { setBusy(false); }
  };
  const resolve = async () => {
    setBusy(true);
    try {
      await api.post(`/admin/messages/replies/${reply.id}/resolve`);
      showToast("Thread marked resolved");
      onChanged();
    } catch (e) { showToast(errMsg(e, "Couldn't resolve thread")); } finally { setBusy(false); }
  };

  return (
    <View style={st.card}>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <RNText style={mg.replyFrom} numberOfLines={1}>{from}</RNText>
        {!resolved && <View style={mg.unreadDot} />}
      </View>
      {!!reply._parentSubject && <RNText style={mg.replyRe}>re: {reply._parentSubject} · {fmtDateTime(reply.created_at)}</RNText>}
      <RNText style={mg.replyMsg}>{message}</RNText>

      {!!(reply.responses && reply.responses.length) && (
        <View style={{ gap: 6, marginTop: 8 }}>
          {reply.responses!.map((rsp, i) => (
            <View key={i} style={mg.responseBubble}>
              <RNText style={mg.responseBy}>{rsp.by || "Admin"} · {fmtDateTime(rsp.at)}</RNText>
              <RNText style={mg.responseTxt}>{rsp.text}</RNText>
            </View>
          ))}
        </View>
      )}

      {resolved ? (
        <View style={[mg.pill, { backgroundColor: "rgba(92,154,110,0.15)", alignSelf: "flex-start", marginTop: 10 }]}>
          <CheckCircle2 size={11} color={C.success} /><RNText style={[mg.pillTxt, { color: C.success }]}>Resolved</RNText>
        </View>
      ) : (
        <View style={{ gap: 8, marginTop: 10 }}>
          <Input value={draft} onChangeText={setDraft} placeholder="Reply to this user…" multiline style={{ height: 64 }} />
          <View style={{ flexDirection: "row", gap: 8 }}>
            <Pressable onPress={busy || !draft.trim() ? undefined : respond} style={[mg.smallBtn, { backgroundColor: C.primary, flex: 1, opacity: busy || !draft.trim() ? 0.5 : 1 }]}>
              <Send size={13} color={C.primaryFg} /><RNText style={mg.smallBtnTxt}>Send reply</RNText>
            </Pressable>
            <Pressable onPress={busy ? undefined : resolve} style={[mg.smallBtnOutline, { flex: 1, opacity: busy ? 0.5 : 1 }]}>
              <RNText style={[mg.smallBtnTxt, { color: C.mutedFg }]}>Mark resolved</RNText>
            </Pressable>
          </View>
        </View>
      )}
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
            <RNText style={st.heroTitle} numberOfLines={1}>Messages</RNText>
          </View>
          <Pressable testID="messages-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Broadcast platform announcements with a live recipient preview and respond to user replies.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <View style={{ gap: 6 }}><RNText style={st.fieldLabel}>{label}</RNText>{children}</View>;
}

const mg = StyleSheet.create({
  tabs: { flexDirection: "row", gap: 8, marginTop: 16, backgroundColor: C.surface, borderRadius: 12, padding: 4 },
  tab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, paddingVertical: 9, borderRadius: 9 },
  tabActive: { backgroundColor: C.card },
  tabTxt: { fontFamily: fonts.medium, fontSize: 13, color: C.mutedFg },
  tabTxtActive: { color: C.primary, fontFamily: fonts.bold },
  tabBadge: { minWidth: 18, height: 18, paddingHorizontal: 5, borderRadius: 9, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center" },
  tabBadgeTxt: { fontFamily: fonts.bold, fontSize: 10, color: C.white },

  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  subChip: { paddingHorizontal: 12, height: 30, borderRadius: 999, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  subChipActive: { backgroundColor: C.secondary, borderColor: C.secondary },
  subChipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  subChipTxtActive: { color: C.secondaryFg },
  counter: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, textAlign: "right" },
  hint: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  previewBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "rgba(123,107,184,0.08)", borderRadius: 10, padding: 10, marginTop: 8 },
  previewTxt: { flex: 1, fontFamily: fonts.medium, fontSize: 12, color: C.info },

  sendBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, height: 50, borderRadius: 14, backgroundColor: C.primary },
  sendBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  pill: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 20, borderRadius: 10, justifyContent: "center" },
  pillTxt: { fontFamily: fonts.bold, fontSize: 10, textTransform: "uppercase" },
  sentSubject: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground, marginTop: 2 },
  sentMeta: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2 },
  sentFooter: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 8 },
  sentStat: { fontFamily: fonts.medium, fontSize: 11, color: C.mutedFg },

  replyFrom: { flex: 1, fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: C.info },
  replyRe: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 1 },
  replyMsg: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, backgroundColor: C.surface, borderRadius: 10, padding: 10, marginTop: 8, lineHeight: 18 },
  responseBubble: { backgroundColor: "rgba(166,33,63,0.06)", borderRadius: 10, padding: 10 },
  responseBy: { fontFamily: fonts.semibold, fontSize: 11, color: C.primary },
  responseTxt: { fontFamily: fonts.regular, fontSize: 12, color: C.foreground, marginTop: 2, lineHeight: 17 },
  smallBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, height: 40, borderRadius: 10 },
  smallBtnOutline: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, height: 40, borderRadius: 10, backgroundColor: C.surface },
  smallBtnTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.primaryFg },

  confirmOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", alignItems: "center", justifyContent: "center", padding: 24 },
  confirmCard: { width: "100%", maxWidth: 400, backgroundColor: C.background, borderRadius: 20, padding: 20, gap: 14 },
  confirmTitle: { fontFamily: fonts.heading, fontSize: 17, color: C.foreground },
  confirmBody: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, lineHeight: 19 },
});
