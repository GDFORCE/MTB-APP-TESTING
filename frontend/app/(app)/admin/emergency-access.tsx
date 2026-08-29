// ADM-13 — Emergency access (Break-The-Glass): justify → senior approval → 2h session.
//
// Live, admin-gated endpoints (backend/admin_routes.py · EMERGENCY ACCESS):
//   request ..... POST /admin/emergency/requests {reason_category, reason_text, trial_id?}
//   poll ........ GET  /admin/emergency/requests/{id}   → {..., session}
//   end ......... POST /admin/emergency/sessions/{id}/end
//   session log . GET  /admin/emergency/sessions/{id}/log
//
// Two-person rule: a SECOND admin must approve — the requester can never approve
// their own request, so this screen has no self-approve control. After
// submitting, it polls the request until a senior admin approves (opening a
// time-boxed 2h session) or denies it. The countdown is derived from the real
// server expiry; every session action is written to the audit trail.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Animated,
  StyleSheet, ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, AlertTriangle, Clock, Shield, FileText, Check, XCircle,
  CheckCircle2,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Toast, Input, st } from "./users";

type Session = { id: string; status?: string; started_at?: string; expires_at?: string; approver_name?: string };
type EmergencyRequest = {
  id: string; status?: string; reason_category?: string; reason_text?: string;
  created_at?: string; requester_name?: string; deny_reason?: string;
  approved_at?: string; session?: Session | null; session_id?: string | null;
  can_action?: boolean; is_own_request?: boolean; trial_id?: string;
};
type LogRow = { id?: string; action?: string; detail?: string; created_at?: string; user_name?: string };

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const REASONS = [
  { key: "patient_safety", label: "Patient safety" },
  { key: "regulatory_audit", label: "Regulatory audit" },
  { key: "data_correction", label: "Data correction" },
  { key: "incident_investigation", label: "Incident investigation" },
  { key: "other", label: "Other" },
];
const reasonLabel = (k?: string) => REASONS.find((r) => r.key === k)?.label || k || "—";

function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
function fmtClock(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function countdown(sec: number): string {
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export default function AdminEmergencyAccess() {
  const { open } = useAdminDrawer();
  const [mode, setMode] = useState<"request" | "inbox">("request");
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [category, setCategory] = useState("");
  const [reasonText, setReasonText] = useState("");
  const [busy, setBusy] = useState(false);

  const [request, setRequest] = useState<EmergencyRequest | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [log, setLog] = useState<LogRow[]>([]);
  const [inbox, setInbox] = useState<EmergencyRequest[]>([]);
  const [inboxLoading, setInboxLoading] = useState(false);
  const [inboxError, setInboxError] = useState<string | null>(null);
  const [inboxBusy, setInboxBusy] = useState<string | null>(null);

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

  const canSubmit = !!category && reasonText.trim().length >= 10;

  const loadInbox = useCallback(async () => {
    setInboxLoading(true);
    setInboxError(null);
    try {
      const response = await api.get("/admin/emergency/requests", {
        params: { status: "pending" },
      });
      setInbox(Array.isArray(response.data) ? response.data : []);
    } catch (e) {
      setInboxError(errMsg(e, "Couldn't load pending emergency requests."));
    } finally {
      setInboxLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mode === "inbox") void loadInbox();
  }, [loadInbox, mode]);

  const decideInboxRequest = async (
    item: EmergencyRequest,
    decision: "approve" | "deny",
    reason: string,
  ) => {
    setInboxBusy(item.id);
    try {
      await api.post(
        `/admin/emergency/requests/${item.id}/${decision}`,
        decision === "deny" ? { reason } : undefined,
      );
      showToast(decision === "approve" ? "Emergency access approved" : "Emergency access denied");
      await loadInbox();
    } catch (e) {
      showToast(errMsg(e, `Couldn't ${decision} this request`));
    } finally {
      setInboxBusy(null);
    }
  };

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const res = await api.post("/admin/emergency/requests", {
        reason_category: category, reason_text: reasonText.trim(),
      });
      setRequest(res.data);
      setStep(2);
    } catch (e) {
      showToast(errMsg(e, "Couldn't submit request"));
    } finally { setBusy(false); }
  };

  // ── Poll the request while awaiting senior approval ──
  const pollRequest = useCallback(async (silent: boolean) => {
    if (!request) return;
    try {
      const res = await api.get(`/admin/emergency/requests/${request.id}`);
      const data: EmergencyRequest = res.data;
      setRequest((prev) => ({ ...prev, ...data }));
      if (data.status === "approved" && data.session && data.session.status === "active") {
        setSession(data.session);
        setStep(3);
      } else if (data.status === "denied") {
        setStep(2);
      }
    } catch (e) {
      if (!silent) showToast(errMsg(e, "Couldn't refresh request"));
    }
  }, [request, showToast]);

  useEffect(() => {
    if (step !== 2 || !request || request.status === "denied") return;
    const iv = setInterval(() => pollRequest(true), 5000);
    return () => clearInterval(iv);
  }, [step, request, pollRequest]);

  // ── Countdown + session log while access is granted ──
  useEffect(() => {
    if (step !== 3 || !session?.expires_at) return;
    const exp = new Date(session.expires_at).getTime();
    const tick = () => {
      const rem = Math.floor((exp - Date.now()) / 1000);
      setRemaining(rem);
      if (rem <= 0) { showToast("Emergency session expired"); resetFlow(); }
    };
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, session]);

  const loadLog = useCallback(async () => {
    if (!session) return;
    try {
      const res = await api.get(`/admin/emergency/sessions/${session.id}/log`);
      setLog(Array.isArray(res.data) ? res.data : []);
    } catch { /* log is best-effort */ }
  }, [session]);
  useEffect(() => {
    if (step !== 3 || !session) return;
    loadLog();
    const iv = setInterval(loadLog, 15000);
    return () => clearInterval(iv);
  }, [step, session, loadLog]);

  const resetFlow = () => {
    setStep(1); setCategory(""); setReasonText("");
    setRequest(null); setSession(null); setLog([]); setRemaining(0);
  };

  const endSession = async () => {
    if (!session) return;
    setBusy(true);
    try {
      await api.post(`/admin/emergency/sessions/${session.id}/end`);
      showToast("Emergency session ended");
      resetFlow();
    } catch (e) {
      showToast(errMsg(e, "Couldn't end session"));
    } finally { setBusy(false); }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
        <Hero onMenu={open} />

        <View style={{ marginTop: -20, paddingHorizontal: 16, gap: 14 }}>
          <View style={em.warnBanner}>
            <AlertTriangle size={20} color={C.destructive} />
            <View style={{ flex: 1 }}>
              <RNText style={em.warnTitle}>Restricted access area</RNText>
              <RNText style={em.warnBody}>Emergency access is for critical situations only. Every action is permanently logged and audited.</RNText>
            </View>
          </View>

          <View style={em.modeSwitch}>
            <Pressable testID="emergency-mode-request" onPress={() => setMode("request")} style={[em.modeButton, mode === "request" && em.modeButtonActive]}>
              <RNText style={[em.modeText, mode === "request" && em.modeTextActive]}>Request access</RNText>
            </Pressable>
            <Pressable testID="emergency-mode-inbox" onPress={() => setMode("inbox")} style={[em.modeButton, mode === "inbox" && em.modeButtonActive]}>
              <RNText style={[em.modeText, mode === "inbox" && em.modeTextActive]}>Approval inbox</RNText>
              {inbox.length > 0 ? <View style={em.modeCount}><RNText style={em.modeCountText}>{inbox.length}</RNText></View> : null}
            </Pressable>
          </View>

          {mode === "request" ? (
            <>
              <Stepper step={step} />

              {step === 1 && (
            <View style={st.card}>
              <RNText style={em.stepTitle}>Step 1 · Request justification</RNText>
              <View style={{ gap: 6, marginTop: 12 }}>
                <RNText style={st.fieldLabel}>Reason for emergency access</RNText>
                <View style={em.reasonWrap}>
                  {REASONS.map((r) => (
                    <Pressable key={r.key} onPress={() => setCategory(r.key)} style={[em.reasonChip, category === r.key && em.reasonChipActive]}>
                      <RNText style={[em.reasonChipTxt, category === r.key && em.reasonChipTxtActive]}>{r.label}</RNText>
                    </Pressable>
                  ))}
                </View>
              </View>
              <View style={{ gap: 6, marginTop: 14 }}>
                <RNText style={st.fieldLabel}>Detailed justification (min 10 chars)</RNText>
                <Input value={reasonText} onChangeText={setReasonText} placeholder="Describe the critical situation requiring access…" multiline />
                <RNText style={em.counter}>{reasonText.trim().length} characters</RNText>
              </View>
              <Pressable onPress={busy ? undefined : submit} style={[em.dangerBtn, { opacity: canSubmit && !busy ? 1 : 0.5 }]}>
                {busy ? <ActivityIndicator color={C.white} size="small" /> : (<><Shield size={16} color={C.white} /><RNText style={em.dangerBtnTxt}>Request approval</RNText></>)}
              </Pressable>
            </View>
              )}

              {step === 2 && request && (
            <View style={st.card}>
              {request.status === "denied" ? (
                <View style={{ alignItems: "center" }}>
                  <View style={[em.statusIcon, { backgroundColor: "rgba(192,57,43,0.12)" }]}><XCircle size={30} color={C.destructive} /></View>
                  <RNText style={em.stepTitleCenter}>Request denied</RNText>
                  <RNText style={em.awaitingBody}>A senior administrator denied this break-the-glass request.</RNText>
                  {!!request.deny_reason && (
                    <View style={[em.infoBox, { backgroundColor: "rgba(192,57,43,0.06)", borderColor: "rgba(192,57,43,0.2)" }]}>
                      <RNText style={[em.infoBoxTxt, { color: C.destructive }]}>Reason: {request.deny_reason}</RNText>
                    </View>
                  )}
                  <Pressable onPress={resetFlow} style={em.ghostBtn}><RNText style={em.ghostBtnTxt}>Start a new request</RNText></Pressable>
                </View>
              ) : (
                <View style={{ alignItems: "center" }}>
                  <View style={[em.statusIcon, { backgroundColor: "rgba(216,154,60,0.15)" }]}><Clock size={30} color={C.warning} /></View>
                  <RNText style={em.stepTitleCenter}>Awaiting senior approval</RNText>
                  <RNText style={em.awaitingBody}>Submitted and pending a second administrator’s approval (two-person rule — you cannot approve your own request).</RNText>
                  <View style={em.infoBox}>
                    <RNText style={em.infoBoxKey}>Request ID</RNText>
                    <RNText style={em.infoBoxMono}>{request.id}</RNText>
                    <RNText style={[em.infoBoxKey, { marginTop: 8 }]}>Submitted</RNText>
                    <RNText style={em.infoBoxVal}>{fmtDateTime(request.created_at)}</RNText>
                  </View>
                  <View style={em.detailBox}>
                    <RNText style={em.detailKey}>Reason</RNText>
                    <RNText style={em.detailVal}>{reasonLabel(request.reason_category)}</RNText>
                    <RNText style={[em.detailKey, { marginTop: 8 }]}>Justification</RNText>
                    <RNText style={em.detailVal}>{request.reason_text}</RNText>
                  </View>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 14 }}>
                    <ActivityIndicator color={C.warning} size="small" />
                    <RNText style={em.polling}>Checking for approval…</RNText>
                  </View>
                  <View style={{ flexDirection: "row", gap: 10, marginTop: 14, alignSelf: "stretch" }}>
                    <Pressable onPress={() => pollRequest(false)} style={[em.ghostBtn, { flex: 1 }]}>
                      <RefreshCcw size={14} color={C.mutedFg} /><RNText style={em.ghostBtnTxt}>Refresh</RNText>
                    </Pressable>
                    <Pressable onPress={resetFlow} style={[em.ghostBtn, { flex: 1 }]}><RNText style={em.ghostBtnTxt}>Cancel</RNText></Pressable>
                  </View>
                </View>
              )}
            </View>
              )}

              {step === 3 && session && (
            <View style={{ gap: 14 }}>
              <View style={[st.card, { alignItems: "center" }]}>
                <View style={[em.statusIcon, { backgroundColor: "rgba(92,154,110,0.15)" }]}><Shield size={30} color={C.success} /></View>
                <RNText style={em.stepTitleCenter}>Emergency access granted</RNText>
                {!!session.approver_name && <RNText style={em.awaitingBody}>Approved by {session.approver_name}</RNText>}
                <View style={em.countdownBox}>
                  <RNText style={em.countdownLabel}>Access expires in</RNText>
                  <RNText style={em.countdownVal}>{countdown(remaining)}</RNText>
                </View>
                <Pressable onPress={busy ? undefined : endSession} style={[em.dangerBtn, { alignSelf: "stretch", opacity: busy ? 0.5 : 1 }]}>
                  {busy ? <ActivityIndicator color={C.white} size="small" /> : (<><XCircle size={16} color={C.white} /><RNText style={em.dangerBtnTxt}>End emergency session</RNText></>)}
                </Pressable>
              </View>

              <View style={em.reminderBox}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <AlertTriangle size={15} color={C.warning} />
                  <RNText style={em.reminderTitle}>Important reminders</RNText>
                </View>
                {["All actions are fully logged and audited", "Access only what is absolutely necessary", "Document every action taken this session", "Access expires automatically after the countdown"].map((t) => (
                  <View key={t} style={{ flexDirection: "row", gap: 8, marginTop: 3 }}>
                    <RNText style={em.reminderDot}>•</RNText><RNText style={em.reminderItem}>{t}</RNText>
                  </View>
                ))}
              </View>

              <View style={st.card}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <FileText size={15} color={C.mutedFg} />
                    <RNText style={em.logTitle}>Session log</RNText>
                  </View>
                  <Pressable onPress={loadLog} hitSlop={8}><RefreshCcw size={15} color={C.mutedFg} /></Pressable>
                </View>
                <View style={{ gap: 6 }}>
                  <LogLine time={fmtClock(session.started_at)} text="Emergency session initiated" tone="success" />
                  {log.filter((r) => r.action !== "emergency.approve").map((r, i) => (
                    <LogLine key={r.id || i} time={fmtClock(r.created_at)} text={r.detail || r.action || "Audited action"} />
                  ))}
                  {log.length === 0 && <RNText style={em.logEmpty}>No unmasked reads recorded yet.</RNText>}
                </View>
              </View>
            </View>
              )}
            </>
          ) : (
            <ApprovalInbox
              rows={inbox}
              loading={inboxLoading}
              error={inboxError}
              busyId={inboxBusy}
              onRetry={loadInbox}
              onDecision={decideInboxRequest}
            />
          )}

          <RNText style={em.footerNote}>All actions during emergency access are fully logged and subject to audit.</RNText>
        </View>
      </ScrollView>

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function ApprovalInbox({ rows, loading, error, busyId, onRetry, onDecision }: {
  rows: EmergencyRequest[];
  loading: boolean;
  error: string | null;
  busyId: string | null;
  onRetry: () => Promise<void>;
  onDecision: (item: EmergencyRequest, decision: "approve" | "deny", reason: string) => Promise<void>;
}) {
  const [denyFor, setDenyFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  if (loading) {
    return (
      <View testID="emergency-inbox-loading" style={st.card}>
        <ActivityIndicator color={C.primary} />
        <RNText style={em.inboxCenter}>Loading pending requests…</RNText>
      </View>
    );
  }
  if (error) {
    return (
      <View testID="emergency-inbox-error" style={st.card}>
        <AlertTriangle size={24} color={C.destructive} style={{ alignSelf: "center" }} />
        <RNText style={em.inboxTitleCenter}>Approval inbox unavailable</RNText>
        <RNText style={em.inboxCenter}>{error}</RNText>
        <Pressable testID="emergency-inbox-retry" onPress={() => void onRetry()} style={em.ghostBtn}>
          <RefreshCcw size={14} color={C.primary} />
          <RNText style={[em.ghostBtnTxt, { color: C.primary }]}>Retry</RNText>
        </Pressable>
      </View>
    );
  }
  if (rows.length === 0) {
    return (
      <View testID="emergency-inbox-empty" style={st.card}>
        <View style={[em.statusIcon, { alignSelf: "center", backgroundColor: "rgba(92,154,110,0.14)" }]}>
          <CheckCircle2 size={28} color={C.success} />
        </View>
        <RNText style={em.inboxTitleCenter}>No pending approvals</RNText>
        <RNText style={em.inboxCenter}>New break-the-glass requests from other administrators will appear here.</RNText>
      </View>
    );
  }

  return (
    <View style={{ gap: 10 }}>
      <View style={em.inboxIntro}>
        <Shield size={17} color={C.info} />
        <RNText style={em.inboxIntroText}>
          Review the justification before approving. You cannot action your own request.
        </RNText>
      </View>
      {rows.map((item) => {
        const denying = denyFor === item.id;
        const busy = busyId === item.id;
        return (
          <View key={item.id} testID={`emergency-inbox-${item.id}`} style={st.card}>
            <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 11 }}>
              <View style={[em.inboxAvatar, item.is_own_request && { backgroundColor: C.surface }]}>
                <Shield size={18} color={item.is_own_request ? C.mutedFg : C.destructive} />
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <RNText style={em.inboxName} numberOfLines={1}>{item.requester_name || "Platform administrator"}</RNText>
                <RNText style={em.inboxMeta}>{reasonLabel(item.reason_category)} · {fmtDateTime(item.created_at)}</RNText>
              </View>
              {item.is_own_request ? (
                <View style={em.ownBadge}><RNText style={em.ownBadgeText}>Your request</RNText></View>
              ) : null}
            </View>
            <View style={em.detailBox}>
              <RNText style={em.detailKey}>Justification</RNText>
              <RNText style={em.detailVal}>{item.reason_text || "No justification supplied."}</RNText>
              {item.trial_id ? (
                <>
                  <RNText style={[em.detailKey, { marginTop: 8 }]}>Trial</RNText>
                  <RNText style={em.infoBoxMono}>{item.trial_id}</RNText>
                </>
              ) : null}
            </View>

            {item.can_action ? (
              denying ? (
                <View style={{ gap: 9, marginTop: 12 }}>
                  <Input
                    testID={`emergency-deny-reason-${item.id}`}
                    value={reason}
                    onChangeText={setReason}
                    placeholder="Reason for denial"
                    multiline
                  />
                  <View style={em.inboxActions}>
                    <Pressable onPress={() => { setDenyFor(null); setReason(""); }} disabled={busy} style={em.inboxSecondary}>
                      <RNText style={em.inboxSecondaryText}>Cancel</RNText>
                    </Pressable>
                    <Pressable
                      testID={`emergency-confirm-deny-${item.id}`}
                      onPress={() => void onDecision(item, "deny", reason.trim())}
                      disabled={busy || reason.trim().length < 5}
                      style={[em.inboxDeny, (busy || reason.trim().length < 5) && { opacity: 0.45 }]}
                    >
                      {busy ? <ActivityIndicator size="small" color={C.white} /> : <XCircle size={15} color={C.white} />}
                      <RNText style={em.inboxActionText}>Confirm deny</RNText>
                    </Pressable>
                  </View>
                </View>
              ) : (
                <View style={em.inboxActions}>
                  <Pressable
                    testID={`emergency-deny-${item.id}`}
                    onPress={() => { setDenyFor(item.id); setReason(""); }}
                    disabled={busy}
                    style={em.inboxSecondary}
                  >
                    <XCircle size={15} color={C.destructive} />
                    <RNText style={[em.inboxSecondaryText, { color: C.destructive }]}>Deny</RNText>
                  </Pressable>
                  <Pressable
                    testID={`emergency-approve-${item.id}`}
                    onPress={() => void onDecision(item, "approve", "")}
                    disabled={busy}
                    style={[em.inboxApprove, busy && { opacity: 0.5 }]}
                  >
                    {busy ? <ActivityIndicator size="small" color={C.white} /> : <Check size={15} color={C.white} />}
                    <RNText style={em.inboxActionText}>Approve for 2 hours</RNText>
                  </Pressable>
                </View>
              )
            ) : (
              <RNText style={em.ownNote}>A different administrator must action this request.</RNText>
            )}
          </View>
        );
      })}
    </View>
  );
}

function Hero({ onMenu }: { onMenu: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Emergency access</RNText>
          </View>
        </View>
        <RNText style={st.heroSub}>Break-the-glass: temporary, time-limited access to protected PHI. Every access is permanently logged.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function Stepper({ step }: { step: 1 | 2 | 3 }) {
  const labels = ["Justify", "Approval", "Session"];
  return (
    <View style={em.stepper}>
      {labels.map((l, i) => {
        const n = (i + 1) as 1 | 2 | 3;
        const done = step > n;
        const active = step === n;
        return (
          <React.Fragment key={l}>
            <View style={{ alignItems: "center", gap: 4 }}>
              <View style={[em.stepDot, active && em.stepDotActive, done && em.stepDotDone]}>
                {done ? <Check size={13} color={C.white} /> : <RNText style={[em.stepNum, (active || done) && { color: C.white }]}>{n}</RNText>}
              </View>
              <RNText style={[em.stepLabel, active && { color: C.primary, fontFamily: fonts.semibold }]}>{l}</RNText>
            </View>
            {i < labels.length - 1 && <View style={[em.stepBar, step > n && { backgroundColor: C.success }]} />}
          </React.Fragment>
        );
      })}
    </View>
  );
}

function LogLine({ time, text, tone }: { time: string; text: string; tone?: "success" }) {
  return (
    <View style={em.logRow}>
      <RNText style={em.logTime}>{time}</RNText>
      <RNText style={[em.logText, tone === "success" && { color: C.success }]} numberOfLines={2}>{text}</RNText>
    </View>
  );
}

const em = StyleSheet.create({
  warnBanner: { flexDirection: "row", alignItems: "flex-start", gap: 12, backgroundColor: "rgba(192,57,43,0.06)", borderRadius: 14, borderWidth: 1, borderColor: "rgba(192,57,43,0.22)", padding: 14 },
  warnTitle: { fontFamily: fonts.bold, fontSize: 14, color: C.destructive },
  warnBody: { fontFamily: fonts.regular, fontSize: 12, color: C.destructive, marginTop: 2, lineHeight: 17 },
  modeSwitch: { flexDirection: "row", gap: 4, padding: 4, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.surface },
  modeButton: { flex: 1, minHeight: 39, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 11 },
  modeButtonActive: { backgroundColor: C.card },
  modeText: { fontFamily: fonts.semibold, fontSize: 12, color: C.mutedFg },
  modeTextActive: { color: C.primary },
  modeCount: { minWidth: 20, height: 20, paddingHorizontal: 5, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: C.destructive },
  modeCountText: { fontFamily: fonts.bold, fontSize: 10, color: C.white },

  stepper: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 4 },
  stepDot: { width: 30, height: 30, borderRadius: 15, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  stepDotActive: { backgroundColor: C.primary, borderColor: C.primary },
  stepDotDone: { backgroundColor: C.success, borderColor: C.success },
  stepNum: { fontFamily: fonts.bold, fontSize: 13, color: C.mutedFg },
  stepLabel: { fontFamily: fonts.medium, fontSize: 11, color: C.mutedFg },
  stepBar: { flex: 1, maxWidth: 48, height: 2, borderRadius: 1, backgroundColor: C.border, marginHorizontal: 2, marginBottom: 16 },

  stepTitle: { fontFamily: fonts.heading, fontSize: 16, color: C.foreground },
  stepTitleCenter: { fontFamily: fonts.heading, fontSize: 17, color: C.foreground, marginTop: 12, textAlign: "center" },
  statusIcon: { width: 60, height: 60, borderRadius: 30, alignItems: "center", justifyContent: "center" },
  awaitingBody: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, textAlign: "center", marginTop: 6, lineHeight: 18, paddingHorizontal: 8 },

  reasonWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  reasonChip: { paddingHorizontal: 14, height: 36, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  reasonChipActive: { backgroundColor: C.primary, borderColor: C.primary },
  reasonChipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  reasonChipTxtActive: { color: C.primaryFg },
  counter: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, textAlign: "right" },

  dangerBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, height: 50, borderRadius: 14, backgroundColor: C.destructive, marginTop: 16 },
  dangerBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.white },
  ghostBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, height: 44, borderRadius: 12, backgroundColor: C.surface, marginTop: 14, paddingHorizontal: 16 },
  ghostBtnTxt: { fontFamily: fonts.bold, fontSize: 13, color: C.mutedFg },

  infoBox: { alignSelf: "stretch", backgroundColor: "rgba(216,154,60,0.08)", borderRadius: 12, borderWidth: 1, borderColor: "rgba(216,154,60,0.22)", padding: 12, marginTop: 14 },
  infoBoxKey: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  infoBoxVal: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground, marginTop: 1 },
  infoBoxMono: { fontFamily: fonts.mono, fontSize: 12, color: C.foreground, marginTop: 1 },
  infoBoxTxt: { fontFamily: fonts.medium, fontSize: 12 },
  detailBox: { alignSelf: "stretch", backgroundColor: C.surface, borderRadius: 12, padding: 12, marginTop: 10 },
  detailKey: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  detailVal: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground, marginTop: 1, lineHeight: 18 },
  polling: { fontFamily: fonts.medium, fontSize: 12, color: C.warning },
  inboxCenter: { fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 18, color: C.mutedFg, textAlign: "center", marginTop: 8 },
  inboxTitleCenter: { fontFamily: fonts.heading, fontSize: 17, color: C.foreground, textAlign: "center", marginTop: 10 },
  inboxIntro: { flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 12, borderRadius: 14, backgroundColor: "rgba(123,107,184,0.08)" },
  inboxIntroText: { flex: 1, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, color: C.info },
  inboxAvatar: { width: 42, height: 42, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(192,57,43,0.10)" },
  inboxName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  inboxMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  ownBadge: { paddingHorizontal: 8, height: 22, borderRadius: 999, alignItems: "center", justifyContent: "center", backgroundColor: C.surface },
  ownBadgeText: { fontFamily: fonts.bold, fontSize: 9.5, color: C.mutedFg },
  inboxActions: { flexDirection: "row", gap: 9, marginTop: 12 },
  inboxSecondary: { flex: 1, minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  inboxSecondaryText: { fontFamily: fonts.bold, fontSize: 12, color: C.mutedFg },
  inboxApprove: { flex: 1.4, minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 12, backgroundColor: C.success },
  inboxDeny: { flex: 1.4, minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 12, backgroundColor: C.destructive },
  inboxActionText: { fontFamily: fonts.bold, fontSize: 12, color: C.white },
  ownNote: { marginTop: 12, padding: 10, borderRadius: 12, backgroundColor: C.surface, fontFamily: fonts.medium, fontSize: 11.5, color: C.mutedFg, textAlign: "center" },

  countdownBox: { alignSelf: "stretch", backgroundColor: "rgba(192,57,43,0.06)", borderRadius: 14, borderWidth: 1, borderColor: "rgba(192,57,43,0.22)", padding: 16, marginTop: 14, alignItems: "center" },
  countdownLabel: { fontFamily: fonts.regular, fontSize: 12, color: C.destructive },
  countdownVal: { fontFamily: fonts.mono, fontSize: 34, color: C.destructive, marginTop: 4, fontVariant: ["tabular-nums"] },

  reminderBox: { backgroundColor: "rgba(216,154,60,0.08)", borderRadius: 14, borderWidth: 1, borderColor: "rgba(216,154,60,0.22)", padding: 14 },
  reminderTitle: { fontFamily: fonts.bold, fontSize: 13, color: C.warning },
  reminderDot: { fontFamily: fonts.bold, fontSize: 12, color: C.warning },
  reminderItem: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground, lineHeight: 17 },

  logTitle: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  logRow: { flexDirection: "row", gap: 10, backgroundColor: C.surface, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8 },
  logTime: { fontFamily: fonts.mono, fontSize: 11, color: C.mutedFg, width: 62 },
  logText: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
  logEmpty: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, fontStyle: "italic", paddingVertical: 4 },

  footerNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, textAlign: "center", marginTop: 4, paddingHorizontal: 16, lineHeight: 16 },
});
