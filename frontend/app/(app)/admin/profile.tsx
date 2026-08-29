// ADM-16 — Admin profile: account, contact (OTP), password & security.
//
// Live endpoints (backend/server.py · AUTH):
//   profile ......... GET   /auth/me
//   edit ............ PATCH /auth/me {full_name, phone, ...}
//   password ........ POST  /auth/change-password {current_password, new_password}
//   contact OTP ..... POST  /auth/change-contact/start  {field, value}
//                     POST  /auth/change-contact/verify {code}
//
// Email/phone changes are gated behind an OTP round-trip (start → verify) exactly
// like the rest of the app; there is no server endpoint that lists a user's own
// sessions, so the security panel shows only real /auth/me facts and offers the
// real sign-out — nothing is fabricated.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Modal, Animated,
  RefreshControl, KeyboardAvoidingView, Platform, StyleSheet, ActivityIndicator, TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import {
  Menu, RefreshCcw, X, Pencil, Mail, Phone, KeyRound, ShieldCheck, Eye, EyeOff,
  LogOut, Building2, Calendar, BadgeCheck, Clock,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAuth } from "@/src/auth/AuthContext";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, Toast, Input, SheetActions, st } from "./users";
import { sanitizeName } from "@/src/lib/validators";

type Me = {
  id?: string; full_name?: string; email?: string; phone?: string; role?: string;
  organization?: string; avatar_initials?: string; created_at?: string;
  email_verified?: boolean; phone_verified?: boolean; is_online?: boolean;
  last_login_at?: string; last_login?: string;
};

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;
// Independent of the OTP's own validity — just a per-channel cooldown that
// stops the Resend button from being spammed against the API.
const RESEND_COOLDOWN_SEC = { phone: 60, email: 120 } as const;
const MAX_CONTACT_RESENDS = 3;
function fmtDateTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function AdminProfile() {
  const { open } = useAdminDrawer();
  const { signOut, refresh } = useAuth();
  const router = useRouter();

  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [contactField, setContactField] = useState<"email" | "phone" | null>(null);

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
      const res = await api.get("/auth/me");
      setMe(res.data || {});
    } catch (e) {
      setError(errMsg(e, "Couldn't load your profile. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const afterContactChange = async () => { setContactField(null); await load(); await refresh(); };
  const afterEdit = async () => { setEditOpen(false); await load(); await refresh(); };

  const doSignOut = async () => { await signOut(); router.replace("/(auth)/welcome"); };

  const initials = me?.avatar_initials || (me?.full_name || me?.email || "A").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

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

        {loading ? (
          <Loading label="Loading your profile…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16, gap: 14 }}>
            {/* Identity */}
            <View style={st.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
                <View style={pf.avatar}><RNText style={pf.avatarTxt}>{initials}</RNText></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <RNText style={pf.name} numberOfLines={1}>{me?.full_name || "—"}</RNText>
                  <View style={pf.rolePill}><RNText style={pf.rolePillTxt}>Platform Admin</RNText></View>
                </View>
                <Pressable onPress={() => setEditOpen(true)} style={pf.editBtn}>
                  <Pencil size={13} color={C.primary} /><RNText style={pf.editBtnTxt}>Edit</RNText>
                </Pressable>
              </View>

              <View style={{ marginTop: 14, gap: 8 }}>
                <ContactRow icon={Mail} label="Email (change via OTP)" value={me?.email || "—"} verified={me?.email_verified} onChange={() => setContactField("email")} />
                <ContactRow icon={Phone} label="Phone (change via OTP)" value={me?.phone || "—"} verified={me?.phone_verified} onChange={() => setContactField("phone")} />
                <InfoRow icon={Building2} label="Organization" value={me?.organization || "—"} />
                <InfoRow icon={Calendar} label="Account created" value={fmtDateTime(me?.created_at)} />
              </View>
            </View>

            {/* Password */}
            <PasswordCard showToast={showToast} />

            {/* Security & session */}
            <View style={st.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <ShieldCheck size={16} color={C.info} />
                <RNText style={pf.cardTitle}>Security & session</RNText>
              </View>
              <View style={{ gap: 8 }}>
                <InfoRow icon={BadgeCheck} label="Role" value={me?.role || "admin"} />
                <InfoRow icon={Clock} label="Last sign-in" value={fmtDateTime(me?.last_login_at || me?.last_login)} />
                <InfoRow icon={ShieldCheck} label="Status" value={me?.is_online ? "Online — this device" : "Offline"} />
              </View>
              <RNText style={pf.secNote}>Session policy is enforced server-side; suspending or force-logging-out accounts is done from User management.</RNText>
              <Pressable onPress={doSignOut} style={pf.logoutBtn}>
                <LogOut size={16} color={C.white} /><RNText style={pf.logoutTxt}>Log out</RNText>
              </Pressable>
            </View>
          </View>
        )}
      </ScrollView>

      <EditProfileSheet open={editOpen} me={me} onClose={() => setEditOpen(false)} onSaved={(msg) => { showToast(msg); afterEdit(); }} onError={showToast} />
      <ContactSheet field={contactField} me={me} onClose={() => setContactField(null)} onDone={(msg) => { showToast(msg); afterContactChange(); }} />

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
            <RNText style={st.heroTitle} numberOfLines={1}>My profile</RNText>
          </View>
          <Pressable testID="profile-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Manage your administrator account, contact details, password and security.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function ContactRow({ icon: Icon, label, value, verified, onChange }: { icon: any; label: string; value: string; verified?: boolean; onChange: () => void }) {
  return (
    <View style={pf.infoRow}>
      <Icon size={17} color={C.mutedFg} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <RNText style={pf.infoLabel}>{label}</RNText>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <RNText style={pf.infoValue} numberOfLines={1}>{value}</RNText>
          {verified && <BadgeCheck size={13} color={C.success} />}
        </View>
      </View>
      <Pressable onPress={onChange} hitSlop={6} style={pf.changeBtn}><RNText style={pf.changeBtnTxt}>Change</RNText></Pressable>
    </View>
  );
}
function InfoRow({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <View style={pf.infoRow}>
      <Icon size={17} color={C.mutedFg} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <RNText style={pf.infoLabel}>{label}</RNText>
        <RNText style={pf.infoValue} numberOfLines={1}>{value}</RNText>
      </View>
    </View>
  );
}

function PasswordCard({ showToast }: { showToast: (m: string) => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showC, setShowC] = useState(false);
  const [showN, setShowN] = useState(false);
  const [showCo, setShowCo] = useState(false);
  const [saving, setSaving] = useState(false);

  const strong = next.length >= 8 && /[A-Z]/.test(next) && /[a-z]/.test(next) && /[0-9]/.test(next) && /[^A-Za-z0-9]/.test(next);
  const canUpdate = current.length > 0 && strong && next === confirm;

  const update = async () => {
    if (!canUpdate) return;
    setSaving(true);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      showToast("Password updated");
      setCurrent(""); setNext(""); setConfirm("");
    } catch (e) {
      showToast(errMsg(e, "Couldn't change password"));
    } finally { setSaving(false); }
  };

  return (
    <View style={st.card}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <KeyRound size={16} color={C.info} />
        <RNText style={pf.cardTitle}>Change password</RNText>
      </View>
      <View style={{ gap: 10 }}>
        <PasswordField label="Current password" value={current} onChange={setCurrent} show={showC} toggle={() => setShowC((v) => !v)} />
        <PasswordField label="New password" value={next} onChange={setNext} show={showN} toggle={() => setShowN((v) => !v)} />
        {next.length > 0 && !strong && <RNText style={pf.warnTxt}>Min 8 chars with uppercase, lowercase, number and special character.</RNText>}
        <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} show={showCo} toggle={() => setShowCo((v) => !v)} />
        {confirm.length > 0 && next !== confirm && <RNText style={pf.errTxt}>Passwords do not match.</RNText>}
        <Pressable onPress={canUpdate && !saving ? update : undefined} style={[pf.primaryBtn, { opacity: canUpdate && !saving ? 1 : 0.5 }]}>
          {saving ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={pf.primaryBtnTxt}>Update password</RNText>}
        </Pressable>
      </View>
    </View>
  );
}

function PasswordField({ label, value, onChange, show, toggle }: { label: string; value: string; onChange: (v: string) => void; show: boolean; toggle: () => void }) {
  return (
    <View style={{ gap: 6 }}>
      <RNText style={st.fieldLabel}>{label}</RNText>
      <View style={pf.pwWrap}>
        <TextInput value={value} onChangeText={onChange} secureTextEntry={!show} autoCapitalize="none"
          placeholderTextColor="rgba(123,95,115,0.5)" style={pf.pwInput} />
        <Pressable onPress={toggle} hitSlop={8}>{show ? <EyeOff size={16} color={C.mutedFg} /> : <Eye size={16} color={C.mutedFg} />}</Pressable>
      </View>
    </View>
  );
}

function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <View style={st.sheetOverlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={st.sheet}>
            <View style={st.sheetHeader}>
              <RNText style={st.sheetTitle}>{title}</RNText>
              <Pressable onPress={onClose} hitSlop={10} style={st.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
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

function EditProfileSheet({ open, me, onClose, onSaved, onError }: { open: boolean; me: Me | null; onClose: () => void; onSaved: (m: string) => void; onError: (m: string) => void }) {
  const [fullName, setFullName] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) setFullName(me?.full_name || ""); }, [open, me]);

  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/auth/me", { full_name: fullName.trim() });
      onSaved("Profile updated");
    } catch (e) {
      onError(errMsg(e, "Couldn't update profile"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Edit profile">
      <View style={{ gap: 12 }}>
        <View style={{ gap: 6 }}>
          <RNText style={st.fieldLabel}>Full name</RNText>
          <Input value={fullName} onChangeText={(v: string) => setFullName(sanitizeName(v))} placeholder="Your name" />
        </View>
        <RNText style={pf.sheetHint}>Email and phone are changed via OTP verification from the profile screen.</RNText>
        <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Save" onConfirm={save} disabled={!fullName.trim()} loading={saving} />
      </View>
    </Sheet>
  );
}

function ContactSheet({ field, me, onClose, onDone }: { field: "email" | "phone" | null; me: Me | null; onClose: () => void; onDone: (m: string) => void }) {
  const [stage, setStage] = useState<"enter" | "verify">("enter");
  const [value, setValue] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [resendSeconds, setResendSeconds] = useState(0);
  const [resendCount, setResendCount] = useState(0);
  const [showResendCount, setShowResendCount] = useState(false);

  useEffect(() => { if (field) { setStage("enter"); setValue(""); setCode(""); setErr(null); setResendSeconds(0); setResendCount(0); } }, [field]);

  useEffect(() => {
    if (stage !== "verify" || resendSeconds <= 0) return;
    const timer = setInterval(() => setResendSeconds(current => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [stage, resendSeconds]);

  // Flash the attempt count for 5s on entry and whenever it changes or the
  // resend cooldown just opened up, instead of leaving it on screen always.
  const resendReady = resendSeconds === 0;
  useEffect(() => {
    if (stage !== "verify") return;
    setShowResendCount(true);
    const timer = setTimeout(() => setShowResendCount(false), 5000);
    return () => clearTimeout(timer);
  }, [stage, resendCount, resendReady]);

  const start = async () => {
    if (!field || !value.trim()) return;
    setBusy(true); setErr(null);
    try {
      await api.post("/auth/change-contact/start", { field, value: value.trim() });
      setResendCount(0);
      setResendSeconds(RESEND_COOLDOWN_SEC[field]);
      setStage("verify");
    } catch (e) {
      setErr(errMsg(e, "Couldn't send verification code"));
    } finally { setBusy(false); }
  };
  const resend = async () => {
    if (!field || resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS || busy) return;
    setBusy(true); setErr(null); setCode("");
    try {
      await api.post("/auth/change-contact/start", { field, value: value.trim() });
      setResendCount(c => c + 1);
      setResendSeconds(RESEND_COOLDOWN_SEC[field]);
    } catch (e) {
      setErr(errMsg(e, "Couldn't resend the verification code"));
    } finally { setBusy(false); }
  };
  const verify = async () => {
    if (!code.trim()) return;
    setBusy(true); setErr(null);
    try {
      await api.post("/auth/change-contact/verify", { code: code.trim() });
      onDone(`${field === "email" ? "Email" : "Phone"} updated`);
    } catch (e) {
      setErr(errMsg(e, "Incorrect or expired code"));
    } finally { setBusy(false); }
  };

  return (
    <Sheet open={!!field} onClose={onClose} title={`Change ${field || ""}`}>
      {field && (
        <View style={{ gap: 12 }}>
          <RNText style={pf.sheetHint}>Current: {(field === "email" ? me?.email : me?.phone) || "—"}</RNText>
          {stage === "enter" ? (
            <>
              <View style={{ gap: 6 }}>
                <RNText style={st.fieldLabel}>New {field}</RNText>
                <Input value={value} onChangeText={setValue} autoCapitalize="none"
                  keyboardType={field === "email" ? "email-address" : "phone-pad"}
                  placeholder={field === "email" ? "new@email.com" : "+91-XXXXXXXXXX"} />
              </View>
              {err && <RNText style={pf.errTxt}>{err}</RNText>}
              <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Send code" onConfirm={start} disabled={!value.trim()} loading={busy} />
            </>
          ) : (
            <>
              <RNText style={pf.sheetHint}>A verification code was sent to {value}. Enter it below to confirm.</RNText>
              <View style={{ gap: 6 }}>
                <RNText style={st.fieldLabel}>Verification code</RNText>
                <Input value={code} onChangeText={setCode} keyboardType="number-pad" placeholder="6-digit code" />
              </View>
              {err && <RNText style={pf.errTxt}>{err}</RNText>}
              <Pressable
                onPress={resend}
                disabled={resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS || busy}
                style={[pf.resend, (resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS) && { opacity: 0.45 }]}
              >
                <RNText style={pf.resendTxt}>
                  {resendSeconds > 0
                    ? `Resend code in ${Math.floor(resendSeconds / 60)}:${String(resendSeconds % 60).padStart(2, "0")}`
                    : "Resend code"}
                </RNText>
              </Pressable>
              {showResendCount && (
                <RNText style={pf.resendCount}>{resendCount}/{MAX_CONTACT_RESENDS} resend attempts used</RNText>
              )}
              <SheetActions cancelLabel="Back" onCancel={() => setStage("enter")} confirmLabel="Verify & save" onConfirm={verify} disabled={!code.trim()} loading={busy} tone="success" />
            </>
          )}
        </View>
      )}
    </Sheet>
  );
}

const pf = StyleSheet.create({
  avatar: { width: 60, height: 60, borderRadius: 20, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  avatarTxt: { fontFamily: fonts.bold, fontSize: 20, color: C.secondaryFg },
  name: { fontFamily: fonts.heading, fontSize: 18, color: C.foreground },
  rolePill: { alignSelf: "flex-start", paddingHorizontal: 10, height: 22, borderRadius: 11, backgroundColor: "rgba(123,107,184,0.12)", alignItems: "center", justifyContent: "center", marginTop: 4 },
  rolePillTxt: { fontFamily: fonts.bold, fontSize: 11, color: C.info },
  editBtn: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, height: 34, borderRadius: 999, backgroundColor: C.surface },
  editBtnTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.primary },

  cardTitle: { fontFamily: fonts.semibold, fontSize: 15, color: C.foreground },
  infoRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 12 },
  infoLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  infoValue: { fontFamily: fonts.medium, fontSize: 14, color: C.foreground, marginTop: 1 },
  changeBtn: { paddingHorizontal: 12, height: 32, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  changeBtnTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.primary },

  pwWrap: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, height: 46 },
  pwInput: { flex: 1, fontFamily: fonts.regular, fontSize: 14, color: C.foreground, padding: 0 },
  warnTxt: { fontFamily: fonts.medium, fontSize: 11, color: C.warning },
  errTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },
  primaryBtn: { height: 48, borderRadius: 14, backgroundColor: C.primary, alignItems: "center", justifyContent: "center", marginTop: 2 },
  primaryBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  secNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, lineHeight: 16, marginTop: 12 },
  logoutBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, height: 48, borderRadius: 14, backgroundColor: C.destructive, marginTop: 12 },
  logoutTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.white },

  sheetHint: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, lineHeight: 18 },
  resend: { alignItems: "center", paddingVertical: 4 },
  resendTxt: { fontFamily: fonts.bold, fontSize: 13, color: C.primary },
  resendCount: { textAlign: "center", fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg },
});
