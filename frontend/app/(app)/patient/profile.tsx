import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, Text, ScrollView, Pressable, Switch, TextInput, StyleSheet, Modal, KeyboardAvoidingView, Platform, ActivityIndicator, Image, Linking } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import {
  ChevronLeft, ChevronRight, ChevronDown, Camera, User, Lock, Globe, Bell, FileText, Shield, ShieldCheck,
  HelpCircle, LogOut, AlertTriangle, Eye, EyeOff, Check, X, MessageCircle, Mail, Phone, Clock, Ticket, Sparkles,
} from "lucide-react-native";
import { colors, spacing, radii, fonts, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { LinearGradient } from "expo-linear-gradient";
import { fetchFileUri } from "@/src/lib/upload";
import { useAvatarUpload } from "@/src/hooks/use-avatar-upload";
import { AvatarPickerSheet } from "@/src/components/AvatarPickerSheet";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";
import { APP_LOCALES, localeLabel, normalizeLocale, setLanguage, type AppLocale } from "@/src/i18n";
import { sanitizeName } from "@/src/lib/validators";

type Section = "main" | "edit-profile" | "change-password" | "notification-prefs" | "terms" | "privacy" | "help" | "faq" | "contact-support" | "tickets";

const GENDERS = ["Female", "Male", "Other", "Prefer not to say"];

// Independent of the OTP's own validity — just a per-channel cooldown that
// stops the Resend button from being spammed against the API.
const RESEND_COOLDOWN_SEC = { phone: 60, email: 120 } as const;
const MAX_CONTACT_RESENDS = 3;

const passwordRules = [
  { label: "Minimum 8 characters", test: (p: string) => p.length >= 8 },
  { label: "Uppercase letter (A-Z)", test: (p: string) => /[A-Z]/.test(p) },
  { label: "Lowercase letter (a-z)", test: (p: string) => /[a-z]/.test(p) },
  { label: "Numeric character (0-9)", test: (p: string) => /[0-9]/.test(p) },
  { label: "Special character (!@#$%…)", test: (p: string) => /[^A-Za-z0-9]/.test(p) },
];

const FAQS = [
  { q: "How do I view my upcoming visit?", a: "Open My Trial from the dashboard — your next visit is highlighted at the top." },
  { q: "What if I miss a visit?", a: "Contact your research team immediately via the Chat section in the app." },
  { q: "How do I contact my research team?", a: "Use the Chat icon to message your PI or CRC directly." },
  { q: "Can I change my phone number?", a: "Yes — Profile & Settings → Edit Profile. Changing it requires OTP verification." },
  { q: "How are medication reminders set?", a: "Reminders are set by your research team based on your protocol. Manage channels in Notification Preferences." },
];

function formatDob(dob: string) {
  const d = new Date(dob);
  return isNaN(d.getTime()) ? dob : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
function computeAge(dob: string): number | null {
  const d = new Date(dob);
  if (isNaN(d.getTime())) return null;
  const now = new Date();
  let age = now.getFullYear() - d.getFullYear();
  const m = now.getMonth() - d.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < d.getDate())) age--;
  return age;
}

// ── Shared bits ───────────────────────────────────────────────────────────────
function Header({ title, eyebrow = "Profile & settings", onBack, rightLabel, onRight }: { title: string; eyebrow?: string; onBack: () => void; rightLabel?: string; onRight?: () => void }) {
  return (
    <View style={h.wrap}>
      <Springy onPress={onBack} style={h.back}><ChevronLeft size={22} color={colors.primaryFg} /></Springy>
      <View style={{ flex: 1 }}>
        <Eyebrow color={colors.overlay25}>{eyebrow}</Eyebrow>
        <Text style={h.title} numberOfLines={1}>{title}</Text>
      </View>
      {rightLabel ? <Springy onPress={onRight} style={h.rightBtn}><Small weight="700" color={colors.primaryFg}>{rightLabel}</Small></Springy> : null}
    </View>
  );
}
function Toggle({ on, onToggle, testID, disabled = false }: { on: boolean; onToggle: () => void; testID?: string; disabled?: boolean }) {
  return (
    <Switch testID={testID} value={on} onValueChange={onToggle} disabled={disabled} trackColor={{ true: colors.primary, false: colors.border }} thumbColor={colors.primaryFg} />
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Text style={p.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

export default function Profile() {
  const router = useRouter();
  const { user, signOut, refresh } = useAuth();
  const [section, setSection] = useState<Section>("main");
  const [showLogout, setShowLogout] = useState(false);
  const [showLang, setShowLang] = useState(false);
  const [genderOpen, setGenderOpen] = useState(false);

  // Avatar (uploaded profile photo). `avatarUri` is a render-ready object URL /
  // data URI fetched through the authed api client; null → fall back to initials.
  const [avatarUri, setAvatarUri] = useState<string | null>(null);
  const avatar = useAvatarUpload({
    onUploaded: async (uri) => { setAvatarUri(uri); await refresh(); },
    onRemoved: async () => { setAvatarUri(null); await refresh(); },
  });

  // Profile fields (loaded from /auth/me — includes the profile sub-document)
  const [prof, setProf] = useState({ fullName: "", dob: "", gender: "", phone: "", email: "", language: "en" as AppLocale });
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileLoadError, setProfileLoadError] = useState("");
  const [profileFeedback, setProfileFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Password change
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [showPw, setShowPw] = useState({ current: false, next: false, confirm: false });
  const [pwErr, setPwErr] = useState(""); const [pwSaving, setPwSaving] = useState(false);

  // Notification prefs
  const [prefs, setPrefs] = useState<any>({ visit_push: true, visit_sms: true, visit_email: false, visit_remind_days: 2, med_push: true, med_sms: true, trial_updates: true, pi_messages: true, system_notifs: false });
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [prefsError, setPrefsError] = useState("");
  const [prefSaving, setPrefSaving] = useState<string | null>(null);
  const [prefsFeedback, setPrefsFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Help / tickets
  const [faqOpen, setFaqOpen] = useState<number | null>(null);
  const [contact, setContact] = useState({ category: "Login Issue", subject: "", description: "" });
  const [ticketSubmitted, setTicketSubmitted] = useState(false);
  const [lastTicketId, setLastTicketId] = useState("");
  const [tickets, setTickets] = useState<any[]>([]);
  const [ticketsLoading, setTicketsLoading] = useState(true);
  const [ticketsError, setTicketsError] = useState("");
  const [ticketSubmitting, setTicketSubmitting] = useState(false);
  const [ticketError, setTicketError] = useState("");
  const [supportContact, setSupportContact] = useState<{ name?: string; email?: string; phone?: string; hours?: string } | null>(null);
  const [supportContactError, setSupportContactError] = useState("");
  const [termsAcceptedAt, setTermsAcceptedAt] = useState("");

  // Legal (T&C / Privacy) + FAQ fetched from the API
  type LegalDoc = { version: string; effective_date: string; blocks: { heading: string; body: string }[] };
  const [legal, setLegal] = useState<Record<string, LegalDoc>>({});
  const [legalLoading, setLegalLoading] = useState(false);
  const [legalError, setLegalError] = useState(false);
  const [faqs, setFaqs] = useState(FAQS);
  const [faqLoading, setFaqLoading] = useState(true);
  const [faqError, setFaqError] = useState("");

  // Contact-change OTP flow (email / phone edits require verification)
  const [loaded, setLoaded] = useState({ phone: "", email: "" });
  type OtpItem = { field: "email" | "phone"; value: string };
  const [otp, setOtp] = useState<{ open: boolean; field: "email" | "phone"; value: string; code: string; step: "sending" | "code"; error: string; busy: boolean }>(
    { open: false, field: "email", value: "", code: "", step: "sending", error: "", busy: false });
  const [otpQueue, setOtpQueue] = useState<OtpItem[]>([]);
  const [resendSeconds, setResendSeconds] = useState(0);
  const [resendCount, setResendCount] = useState(0);
  const [showResendCount, setShowResendCount] = useState(false);
  const [pendingLanguage, setPendingLanguage] = useState<AppLocale>("en");
  const [languageBusy, setLanguageBusy] = useState(false);
  const [languageError, setLanguageError] = useState("");

  const loadProfile = useCallback(async () => {
    setProfileLoading(true);
    setProfileLoadError("");
    try {
      const me = (await api.get("/auth/me")).data;
      const pf = me.profile || {};
      const language = normalizeLocale(pf.language);
      setProf({
        fullName: me.full_name || "",
        dob: pf.dob || "",
        gender: pf.gender || "",
        phone: (me.phone || "").replace(/^\+91\s?/, ""),
        email: me.email || "",
        language,
      });
      setPendingLanguage(language);
      await setLanguage(language);
      setLoaded({ phone: me.phone || "", email: me.email || "" });
      setTermsAcceptedAt(me.terms_accepted_at || "");
      if (me.avatar_file_id) {
        try { setAvatarUri(await fetchFileUri(me.avatar_file_id)); } catch {}
      }
    } catch {
      setProfileLoadError("Couldn't load your profile. Check your connection and retry.");
    } finally {
      setProfileLoading(false);
    }
  }, []);

  const loadPreferences = useCallback(async () => {
    setPrefsLoading(true);
    setPrefsError("");
    try {
      const pr = (await api.get("/preferences")).data || {};
      setPrefs((current: any) => ({ ...current, ...pr }));
    } catch {
      setPrefsError("Couldn't load your saved notification preferences.");
    } finally {
      setPrefsLoading(false);
    }
  }, []);

  const loadTickets = useCallback(async () => {
    setTicketsLoading(true);
    setTicketsError("");
    try {
      setTickets((await api.get("/support/tickets")).data || []);
    } catch {
      setTicketsError("Couldn't load your support tickets.");
    } finally {
      setTicketsLoading(false);
    }
  }, []);

  const loadFaq = useCallback(async () => {
    setFaqLoading(true);
    setFaqError("");
    try {
      const fq = (await api.get("/faq")).data;
      if (Array.isArray(fq) && fq.length) setFaqs(fq);
      else setFaqs([]);
    } catch {
      setFaqError("Couldn't load the latest frequently asked questions.");
    } finally {
      setFaqLoading(false);
    }
  }, []);

  const loadSupportContact = useCallback(async () => {
    setSupportContactError("");
    try {
      setSupportContact((await api.get("/support/contact")).data || null);
    } catch {
      setSupportContact(null);
      setSupportContactError("Support contact details are unavailable.");
    }
  }, []);

  useEffect(() => {
    loadProfile();
    loadPreferences();
    loadTickets();
    loadFaq();
    loadSupportContact();
  }, [loadFaq, loadPreferences, loadProfile, loadSupportContact, loadTickets]);

  // Fetch legal copy the first time each doc's section is opened.
  useEffect(() => {
    if (section !== "terms" && section !== "privacy") return;
    if (legal[section]) return;
    let cancelled = false;
    (async () => {
      setLegalLoading(true); setLegalError(false);
      try {
        const data = (await api.get(`/legal/${section}`)).data as LegalDoc;
        if (!cancelled) setLegal(l => ({ ...l, [section]: data }));
      } catch { if (!cancelled) setLegalError(true); }
      finally { if (!cancelled) setLegalLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [section, legal]);

  const initials = useMemo(() => (prof.fullName || user?.full_name || "P").trim().split(/\s+/).slice(0, 2).map(w => w[0]?.toUpperCase() || "").join("") || "P", [prof.fullName, user]);
  const acceptedTermsLabel = termsAcceptedAt
    ? new Date(termsAcceptedAt).toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";
  const openSupportLink = (url: string) => {
    setSupportContactError("");
    Linking.openURL(url).catch(() => {
      setSupportContactError("No compatible email or phone app is available.");
    });
  };

  const validateProfile = (): string => {
    const name = prof.fullName.trim();
    if (name.length < 2) return "Enter your full name.";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(prof.dob)) return "Enter date of birth as YYYY-MM-DD.";
    const dob = new Date(`${prof.dob}T00:00:00Z`);
    if (Number.isNaN(dob.getTime()) || dob.toISOString().slice(0, 10) !== prof.dob) return "Enter a valid date of birth.";
    const age = computeAge(prof.dob);
    if (dob > new Date()) return "Date of birth cannot be in the future.";
    if (age == null || age > 120) return "Enter a valid date of birth.";
    if (!GENDERS.includes(prof.gender)) return "Select your gender.";
    const phoneDigits = prof.phone.replace(/\D/g, "");
    if (phoneDigits && phoneDigits.length !== 10) return "Enter a valid 10-digit phone number.";
    const email = prof.email.trim().toLowerCase();
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return "Enter a valid email address.";
    return "";
  };

  const saveProfile = async () => {
    if (savingProfile) return;
    const validation = validateProfile();
    if (validation) {
      setProfileFeedback({ type: "error", message: validation });
      return;
    }
    setProfileFeedback(null);
    setSavingProfile(true);
    try {
      const newEmail = prof.email.trim().toLowerCase();
      const newPhone = prof.phone ? "+91" + prof.phone.replace(/\D/g, "") : "";
      // Non-contact fields save immediately; email/phone changes require OTP.
      await api.patch("/auth/me", {
        full_name: prof.fullName.trim(), dob: prof.dob, gender: prof.gender, language: normalizeLocale(prof.language),
      });
      await refresh();
      const queue: OtpItem[] = [];
      if (newEmail && newEmail !== loaded.email) queue.push({ field: "email", value: newEmail });
      if (newPhone && newPhone !== loaded.phone) queue.push({ field: "phone", value: newPhone });
      if (queue.length) {
        setProfileFeedback({ type: "success", message: "Profile details saved. Verify your changed contact details to finish." });
        beginOtpQueue(queue);
      } else {
        setProfileFeedback({ type: "success", message: "Profile changes saved." });
      }
    } catch (error: any) {
      setProfileFeedback({
        type: "error",
        message: error?.response?.data?.detail || "Couldn't save your profile. Please try again.",
      });
    } finally { setSavingProfile(false); }
  };

  // ── Contact-change OTP flow ──────────────────────────────────────────────
  useEffect(() => {
    if (!otp.open || resendSeconds <= 0) return;
    const timer = setInterval(() => setResendSeconds(current => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [otp.open, resendSeconds]);

  // Flash the attempt count for 5s on entry and whenever it changes or the
  // resend cooldown just opened up, instead of leaving it on screen always.
  const resendReady = resendSeconds === 0;
  useEffect(() => {
    if (!otp.open) return;
    setShowResendCount(true);
    const timer = setTimeout(() => setShowResendCount(false), 5000);
    return () => clearTimeout(timer);
  }, [otp.open, resendCount, resendReady]);

  const beginOtpQueue = (queue: OtpItem[]) => {
    if (!queue.length) { setSection("main"); return; }
    setOtpQueue(queue);
    startContact(queue[0]);
  };
  const startContact = async (item: OtpItem) => {
    setOtp({ open: true, field: item.field, value: item.value, code: "", step: "sending", error: "", busy: true });
    setResendCount(0);
    setResendSeconds(RESEND_COOLDOWN_SEC[item.field]);
    try {
      await api.post("/auth/change-contact/start", { field: item.field, value: item.value });
      setOtp(o => ({ ...o, step: "code", busy: false }));
    } catch (e: any) {
      setOtp(o => ({ ...o, step: "code", busy: false, error: e?.response?.data?.detail || "Could not send the verification code." }));
    }
  };
  const resendContact = async () => {
    if (resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS || otp.busy) return;
    setOtp(o => ({ ...o, code: "", error: "", busy: true }));
    try {
      await api.post("/auth/change-contact/start", { field: otp.field, value: otp.value });
      setResendCount(c => c + 1);
      setResendSeconds(RESEND_COOLDOWN_SEC[otp.field]);
      setOtp(o => ({ ...o, busy: false }));
    } catch (e: any) {
      setOtp(o => ({ ...o, busy: false, error: e?.response?.data?.detail || "Could not resend the verification code." }));
    }
  };
  const verifyContact = async () => {
    setOtp(o => ({ ...o, busy: true, error: "" }));
    try {
      await api.post("/auth/change-contact/verify", { code: otp.code });
      const { field, value } = otp;
      setLoaded(l => ({ ...l, [field]: value }));
      if (field === "email") setProf(p => ({ ...p, email: value }));
      else setProf(p => ({ ...p, phone: value.replace(/^\+91\s?/, "") }));
      await refresh();
      const rest = otpQueue.slice(1);
      setOtpQueue(rest);
      if (rest.length) startContact(rest[0]);
      else {
        setOtp(o => ({ ...o, open: false, busy: false }));
        setProfileFeedback({ type: "success", message: "Profile and verified contact details saved." });
      }
    } catch (e: any) {
      setOtp(o => ({ ...o, busy: false, error: e?.response?.data?.detail || "Incorrect code. Please try again." }));
    }
  };
  const cancelOtp = () => { setOtp(o => ({ ...o, open: false })); setOtpQueue([]); setResendSeconds(0); setResendCount(0); };

  const passStrength = passwordRules.filter(r => r.test(pw.next)).length;
  const passLabel = passStrength <= 2 ? "Weak" : passStrength <= 3 ? "Medium" : "Strong";
  const passColor = passStrength <= 2 ? colors.destructive : passStrength <= 3 ? colors.warning : colors.success;
  const passMatch = pw.confirm.length > 0 && pw.next === pw.confirm;
  const canUpdatePass = pw.current.length > 0 && passStrength === 5 && passMatch && !pwSaving;

  const changePassword = async () => {
    if (!canUpdatePass) return;
    setPwSaving(true); setPwErr("");
    try {
      await api.post("/auth/change-password", { current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", confirm: "" });
      setSection("main");
    } catch (e: any) { setPwErr(e?.response?.data?.detail || "Could not change password."); } finally { setPwSaving(false); }
  };

  const togglePref = async (k: string) => {
    if (prefSaving) return;
    const previous = prefs[k];
    const value = !previous;
    setPrefSaving(k);
    setPrefsFeedback(null);
    setPrefs((current: any) => ({ ...current, [k]: value }));
    try {
      await api.patch("/preferences", { [k]: value });
      setPrefsFeedback({ type: "success", message: "Preference saved." });
    } catch (error: any) {
      setPrefs((current: any) => ({ ...current, [k]: previous }));
      setPrefsFeedback({
        type: "error",
        message: error?.response?.data?.detail || "Couldn't save that preference. Your previous setting was restored.",
      });
    } finally {
      setPrefSaving(null);
    }
  };
  const setRemindDays = async (d: number) => {
    if (prefSaving) return;
    const previous = prefs.visit_remind_days;
    setPrefSaving("visit_remind_days");
    setPrefsFeedback(null);
    setPrefs((current: any) => ({ ...current, visit_remind_days: d }));
    try {
      await api.patch("/preferences", { visit_remind_days: d });
      setPrefsFeedback({ type: "success", message: "Reminder timing saved." });
    } catch (error: any) {
      setPrefs((current: any) => ({ ...current, visit_remind_days: previous }));
      setPrefsFeedback({
        type: "error",
        message: error?.response?.data?.detail || "Couldn't save reminder timing. Your previous setting was restored.",
      });
    } finally {
      setPrefSaving(null);
    }
  };

  const submitTicket = async () => {
    if (ticketSubmitting) return;
    const subject = contact.subject.trim();
    const description = contact.description.trim();
    if (subject.length < 3) {
      setTicketError("Enter a subject of at least 3 characters.");
      return;
    }
    if (description.length < 10) {
      setTicketError("Describe the issue in at least 10 characters.");
      return;
    }
    setTicketSubmitting(true);
    setTicketError("");
    try {
      const r = await api.post("/support/tickets", { ...contact, subject, description });
      setLastTicketId(r.data.ticket_id || r.data.id);
      setTicketSubmitted(true);
      setContact({ category: "Login Issue", subject: "", description: "" });
      await loadTickets();
    } catch (error: any) {
      setTicketError(error?.response?.data?.detail || "Couldn't submit your ticket. Please try again.");
    } finally {
      setTicketSubmitting(false);
    }
  };

  const applyLanguage = async () => {
    if (languageBusy) return;
    const previous = prof.language;
    setLanguageBusy(true);
    setLanguageError("");
    try {
      await api.patch("/auth/me", { language: pendingLanguage });
      await api.patch("/preferences", { language: pendingLanguage });
      await setLanguage(pendingLanguage);
      setProf(current => ({ ...current, language: pendingLanguage }));
      await refresh();
      setShowLang(false);
    } catch (error: any) {
      // A profile write may have succeeded before the preference write failed.
      // Best-effort compensation keeps both server records on the prior locale.
      await Promise.allSettled([
        api.patch("/auth/me", { language: previous }),
        api.patch("/preferences", { language: previous }),
      ]);
      setPendingLanguage(previous);
      await setLanguage(previous);
      setLanguageError(error?.response?.data?.detail || "Couldn't save your language. Your previous language remains active.");
    } finally {
      setLanguageBusy(false);
    }
  };

  // ══════════════════════ SUB-SCREENS ══════════════════════
  if (section === "edit-profile") {
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="Edit Profile" onBack={() => setSection("main")} rightLabel={savingProfile ? "Saving…" : "Save"} onRight={saveProfile} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
            {profileLoadError ? (
              <View style={[p.feedbackBanner, p.feedbackError]}>
                <AlertTriangle size={16} color={colors.destructive} />
                <Small color={colors.destructive} style={{ flex: 1 }}>{profileLoadError}</Small>
                <Pressable onPress={loadProfile}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
              </View>
            ) : null}
            {profileFeedback ? (
              <View style={[p.feedbackBanner, profileFeedback.type === "success" ? p.feedbackSuccess : p.feedbackError]}>
                {profileFeedback.type === "success"
                  ? <Check size={16} color={colors.success} />
                  : <AlertTriangle size={16} color={colors.destructive} />}
                <Small color={profileFeedback.type === "success" ? colors.success : colors.destructive} style={{ flex: 1 }}>
                  {profileFeedback.message}
                </Small>
              </View>
            ) : null}
            <Rise delay={40} style={{ alignItems: "center", marginBottom: spacing.sm }}>
              <View>
                {avatarUri ? (
                  <Image source={{ uri: avatarUri }} style={p.avatarLg} />
                ) : (
                  <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={p.avatarLg}>
                    <Text style={{ color: colors.primaryFg, fontFamily: fonts.display, fontSize: 26 }}>{initials}</Text>
                  </LinearGradient>
                )}
                <Pressable onPress={avatar.openSheet} disabled={avatar.avatarBusy} style={p.camBtn}>
                  {avatar.avatarBusy ? <ActivityIndicator size="small" color={colors.primary} /> : <Camera size={16} color={colors.primary} />}
                </Pressable>
              </View>
              {avatar.avatarErr ? <Small color={colors.destructive} style={{ marginTop: 8, textAlign: "center" }}>{avatar.avatarErr}</Small> : null}
            </Rise>
            <AvatarPickerSheet
              visible={avatar.sheetOpen}
              onClose={avatar.closeSheet}
              onTakePhoto={avatar.pickFromCamera}
              onChooseFromGallery={avatar.pickFromGallery}
              onRemove={avatar.removeAvatar}
              hasPhoto={!!avatarUri}
            />
            <Rise delay={110}>
              <View style={p.card}>
                <Field label="Full Name *"><TextInput value={prof.fullName} onChangeText={v => { setProf({ ...prof, fullName: sanitizeName(v) }); setProfileFeedback(null); }} style={p.input} /></Field>
                <Field label="Date of Birth *"><TextInput value={prof.dob} onChangeText={v => { setProf({ ...prof, dob: v }); setProfileFeedback(null); }} placeholder="YYYY-MM-DD" placeholderTextColor={colors.mutedFg + "99"} style={p.input} /></Field>
                <Field label="Gender *">
                  <Pressable onPress={() => setGenderOpen(true)} style={[p.input, { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
                    <Text style={{ color: prof.gender ? colors.foreground : colors.mutedFg + "99", fontFamily: fonts.regular, fontSize: 15 }}>{prof.gender || "Select gender"}</Text>
                    <ChevronDown size={18} color={colors.mutedFg} />
                  </Pressable>
                </Field>
              </View>
            </Rise>
            <Rise delay={200}>
              <View style={[p.card, { marginTop: spacing.md }]}>
                <Eyebrow color={colors.mutedFg} style={{ marginBottom: spacing.sm }}>Contact — verified channels</Eyebrow>
                <Field label="Phone Number">
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <View style={p.prefix}><Text style={{ color: colors.mutedFg, fontFamily: fonts.semibold }}>+91</Text></View>
                    <TextInput value={prof.phone} onChangeText={v => { setProf({ ...prof, phone: v }); setProfileFeedback(null); }} keyboardType="phone-pad" style={[p.input, { flex: 1 }]} />
                  </View>
                  <View style={p.warn}><AlertTriangle size={14} color={colors.warning} /><Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Changing this notifies your research team and requires OTP verification.</Small></View>
                </Field>
                <Field label="Email ID">
                  <TextInput value={prof.email} onChangeText={v => { setProf({ ...prof, email: v }); setProfileFeedback(null); }} keyboardType="email-address" autoCapitalize="none" style={p.input} />
                  <View style={p.warn}><AlertTriangle size={14} color={colors.warning} /><Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>Changing this notifies your research team and requires OTP verification.</Small></View>
                </Field>
              </View>
            </Rise>
          </ScrollView>
          <View style={p.footer}>
            <Springy onPress={saveProfile} disabled={savingProfile} style={[p.cta, { backgroundColor: colors.primaryDeep }]}><Text style={p.ctaText}>{savingProfile ? "Saving…" : "Save Changes"}</Text></Springy>
          </View>
        </KeyboardAvoidingView>

        <Modal visible={genderOpen} transparent animationType="fade" onRequestClose={() => setGenderOpen(false)}>
          <Pressable style={p.sheetOverlay} onPress={() => setGenderOpen(false)}>
            <View style={p.sheetCenter}>
              {GENDERS.map(g => (
                <Pressable key={g} onPress={() => { setProf({ ...prof, gender: g }); setProfileFeedback(null); setGenderOpen(false); }} style={[p.sheetItem, prof.gender === g && { backgroundColor: colors.secondary + "55" }]}>
                  <Text style={{ color: prof.gender === g ? colors.primary : colors.foreground, fontFamily: prof.gender === g ? fonts.semibold : fonts.regular, fontSize: 15 }}>{g}</Text>
                  {prof.gender === g && <Check size={16} color={colors.primary} strokeWidth={3} />}
                </Pressable>
              ))}
            </View>
          </Pressable>
        </Modal>

        {/* Contact-change OTP verification */}
        <Modal visible={otp.open} transparent animationType="fade" onRequestClose={cancelOtp}>
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
            <View style={p.dialogOverlay}>
              <Pressable style={StyleSheet.absoluteFill} onPress={otp.busy ? undefined : cancelOtp} />
              <View style={p.dialog}>
                <View style={[p.iconCircle, { backgroundColor: colors.primary + "1A", marginBottom: 12 }]}>
                  {otp.field === "email" ? <Mail size={22} color={colors.primary} /> : <Phone size={22} color={colors.primary} />}
                </View>
                <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginBottom: 6 }}>Verify your {otp.field === "email" ? "email" : "phone"}</Text>
                <Small style={{ marginBottom: 16, lineHeight: 20 }}>
                  {otp.step === "sending" ? "Sending a verification code…" : `Enter the 6-digit code sent to ${otp.value}.`}
                </Small>
                <TextInput
                  value={otp.code}
                  onChangeText={v => setOtp(o => ({ ...o, code: v.replace(/\D/g, "").slice(0, 6), error: "" }))}
                  keyboardType="number-pad" placeholder="000000" placeholderTextColor={colors.mutedFg + "99"}
                  editable={otp.step === "code" && !otp.busy}
                  style={[p.input, { textAlign: "center", letterSpacing: 8, fontSize: 20, marginBottom: 10 }]}
                />
                {otp.error ? <Small color={colors.destructive} style={{ marginBottom: 10 }}>{otp.error}</Small> : null}
                {otp.step === "code" && (
                  <>
                    <Pressable
                      onPress={resendContact}
                      disabled={resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS || otp.busy}
                      style={[p.resend, (resendSeconds > 0 || resendCount >= MAX_CONTACT_RESENDS) && { opacity: 0.45 }]}
                    >
                      <Small color={colors.primary} weight="700">
                        {resendSeconds > 0
                          ? `Resend code in ${Math.floor(resendSeconds / 60)}:${String(resendSeconds % 60).padStart(2, "0")}`
                          : "Resend code"}
                      </Small>
                    </Pressable>
                    {showResendCount && (
                      <Small style={p.resendCount}>{resendCount}/{MAX_CONTACT_RESENDS} resend attempts used</Small>
                    )}
                  </>
                )}
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <Springy onPress={cancelOtp} style={[p.dialogBtn, { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={colors.foreground}>Cancel</Small></Springy>
                  <Springy onPress={verifyContact} disabled={otp.busy || otp.code.length < 6} style={[p.dialogBtn, { backgroundColor: (otp.busy || otp.code.length < 6) ? colors.surface : colors.primaryDeep }]}>
                    <Small weight="700" color={(otp.busy || otp.code.length < 6) ? colors.mutedFg : colors.primaryFg}>{otp.busy ? "Verifying…" : "Verify"}</Small>
                  </Springy>
                </View>
              </View>
            </View>
          </KeyboardAvoidingView>
        </Modal>
      </SafeAreaView>
    );
  }

  if (section === "change-password") {
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="Change Password" onBack={() => setSection("main")} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
            {([["current", "Current Password *"], ["next", "New Password *"], ["confirm", "Confirm New Password *"]] as const).map(([key, label], i) => (
              <Rise key={key} delay={40 + i * 70}>
                <Field label={label}>
                  <View style={{ position: "relative" }}>
                    <TextInput
                      value={(pw as any)[key]} onChangeText={v => setPw({ ...pw, [key]: v })}
                      secureTextEntry={!(showPw as any)[key]} autoCapitalize="none"
                      placeholder={label.replace(" *", "")} placeholderTextColor={colors.mutedFg + "99"}
                      style={[p.input, { paddingRight: 44 }]}
                    />
                    <Pressable onPress={() => setShowPw({ ...showPw, [key]: !(showPw as any)[key] })} hitSlop={8} style={p.eye}>
                      {(showPw as any)[key] ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}
                    </Pressable>
                  </View>
                </Field>
                {key === "next" && pw.next.length > 0 && (
                  <View style={{ marginBottom: spacing.md }}>
                    <View style={{ flexDirection: "row", gap: 6, marginBottom: 6 }}>
                      {[1, 2, 3, 4, 5].map(n => <View key={n} style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: passStrength >= n ? passColor : colors.border }} />)}
                    </View>
                    <Small>Password strength: <Small color={colors.foreground} weight="700">{passLabel}</Small></Small>
                  </View>
                )}
                {key === "next" && (
                  <View style={[p.card, { marginBottom: spacing.md }]}>
                    <Eyebrow color={colors.mutedFg} style={{ marginBottom: 8 }}>Requirements</Eyebrow>
                    {passwordRules.map(r => {
                      const met = r.test(pw.next);
                      return (
                        <View key={r.label} style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 8 }}>
                          <View style={[p.ruleDot, met ? { backgroundColor: colors.success + "26" } : { backgroundColor: colors.surface }]}>{met ? <Check size={11} color={colors.success} strokeWidth={3} /> : <X size={11} color={colors.mutedFg + "80"} />}</View>
                          <Small color={met ? colors.foreground : colors.mutedFg}>{r.label}</Small>
                        </View>
                      );
                    })}
                  </View>
                )}
                {key === "confirm" && pw.confirm.length > 0 && (
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: spacing.sm }}>
                    {passMatch ? <Check size={14} color={colors.success} /> : <X size={14} color={colors.destructive} />}
                    <Small color={passMatch ? colors.success : colors.destructive}>{passMatch ? "Passwords match" : "Passwords do not match"}</Small>
                  </View>
                )}
              </Rise>
            ))}
            {pwErr ? <Small color={colors.destructive}>{pwErr}</Small> : null}
          </ScrollView>
          <View style={p.footer}>
            <Springy onPress={changePassword} disabled={!canUpdatePass} style={[p.cta, { backgroundColor: canUpdatePass ? colors.primaryDeep : colors.surface }]}>
              <Text style={[p.ctaText, { color: canUpdatePass ? colors.primaryFg : colors.mutedFg }]}>{pwSaving ? "Updating…" : "Update Password"}</Text>
            </Springy>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  if (section === "notification-prefs") {
    const groups: { title: string; items: { label: string; key: string }[]; remind?: boolean }[] = [
      { title: "Visit Reminders", items: [{ label: "Push Notifications", key: "visit_push" }, { label: "SMS Reminders", key: "visit_sms" }, { label: "Email Reminders", key: "visit_email" }], remind: true },
      { title: "Medication Reminders", items: [{ label: "Push Notifications", key: "med_push" }, { label: "SMS Reminders", key: "med_sms" }] },
      { title: "General Notifications", items: [{ label: "Trial Updates", key: "trial_updates" }, { label: "Messages from PI", key: "pi_messages" }, { label: "System Notifications", key: "system_notifs" }] },
    ];
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="Notifications" onBack={() => setSection("main")} />
        <ScrollView contentContainerStyle={p.body}>
          {prefsLoading ? (
            <View style={p.sectionState}>
              <ActivityIndicator color={colors.primary} />
              <Small>Loading your saved preferences…</Small>
            </View>
          ) : null}
          {prefsError ? (
            <View style={[p.feedbackBanner, p.feedbackError]}>
              <AlertTriangle size={16} color={colors.destructive} />
              <Small color={colors.destructive} style={{ flex: 1 }}>{prefsError}</Small>
              <Pressable onPress={loadPreferences}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
            </View>
          ) : null}
          {prefsFeedback ? (
            <View style={[p.feedbackBanner, prefsFeedback.type === "success" ? p.feedbackSuccess : p.feedbackError]}>
              {prefsFeedback.type === "success"
                ? <Check size={16} color={colors.success} />
                : <AlertTriangle size={16} color={colors.destructive} />}
              <Small color={prefsFeedback.type === "success" ? colors.success : colors.destructive} style={{ flex: 1 }}>
                {prefsFeedback.message}
              </Small>
            </View>
          ) : null}
          {groups.map((g, gi) => (
            <Rise key={g.title} delay={40 + gi * 80}>
              <View style={[p.card, { marginBottom: spacing.md }]}>
                <Eyebrow color={colors.mutedFg} style={{ borderBottomWidth: 1, borderBottomColor: colors.border, paddingBottom: 10, marginBottom: 4 }}>{g.title}</Eyebrow>
                {g.items.map((it, i) => (
                  <View key={it.key} style={[{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 12 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border + "99" }]}>
                    <Body>{it.label}</Body>
                    <Toggle testID={`pref-${it.key}`} on={!!prefs[it.key]} onToggle={() => togglePref(it.key)} disabled={prefsLoading || !!prefSaving} />
                  </View>
                ))}
                {g.remind && (
                  <View style={{ paddingTop: 12 }}>
                    <Body style={{ marginBottom: 8 }}>Remind me before visit</Body>
                    <View style={{ flexDirection: "row", gap: 8 }}>
                      {[1, 2, 3].map(d => {
                        const active = prefs.visit_remind_days === d;
                        return (
                          <Springy key={d} disabled={prefsLoading || !!prefSaving} onPress={() => setRemindDays(d)} style={[p.dayBtn, active ? { borderColor: colors.primary, backgroundColor: colors.primary + "14" } : { borderColor: colors.border }, (prefsLoading || !!prefSaving) && { opacity: 0.6 }]}>
                            <Small weight="700" color={active ? colors.primary : colors.mutedFg}>{d} day{d > 1 ? "s" : ""}</Small>
                          </Springy>
                        );
                      })}
                    </View>
                  </View>
                )}
              </View>
            </Rise>
          ))}
          <Springy onPress={() => setSection("main")} disabled={!!prefSaving} style={[p.cta, { backgroundColor: prefSaving ? colors.surface : colors.primaryDeep }]}>
            <Text style={[p.ctaText, prefSaving && { color: colors.mutedFg }]}>{prefSaving ? "Saving…" : "Done"}</Text>
          </Springy>
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (section === "terms" || section === "privacy") {
    const isTerms = section === "terms";
    const fallback: [string, string][] = isTerms ? [
      ["1. Use of Application", "This app helps patients manage clinical-trial visit schedules, medication reminders, and communication with research teams."],
      ["2. Privacy", "Your personal health information is protected in accordance with applicable privacy laws including HIPAA and GDPR."],
      ["3. Data Security", "We use industry-standard security. All communications are encrypted using TLS 1.3."],
      ["4. Medical Disclaimer", "This app is informational only and does not replace professional medical advice. Always consult your healthcare provider."],
      ["5. User Responsibilities", "You are responsible for keeping your login credentials confidential and for all activity under your account."],
    ] : [
      ["Information We Collect", "We collect information you provide including contact details, trial-relevant health information, and usage data."],
      ["How We Use Information", "To manage your trial participation, send reminders, and facilitate communication with your research team."],
      ["Data Sharing", "Shared only with your designated research team and the trial sponsor as required by your protocol."],
      ["Your Rights", "You may access, correct, or request deletion of your personal data at any time via your research team."],
    ];
    const doc = legal[section];
    const version = doc?.version || "2.1";
    const effective = doc?.effective_date || "01 Jan 2025";
    const blocks: [string, string][] = doc ? doc.blocks.map(b => [b.heading, b.body]) : fallback;
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title={isTerms ? "Terms & Conditions" : "Privacy Policy"} eyebrow="Legal" onBack={() => setSection("main")} />
        <ScrollView contentContainerStyle={p.body}>
          {legalLoading && !doc ? (
            <View style={{ alignItems: "center", paddingVertical: 24, gap: 10 }}>
              <ActivityIndicator color={colors.primary} />
              <Small color={colors.mutedFg}>Loading latest document…</Small>
            </View>
          ) : null}
          {legalError && !doc ? (
            <View style={[p.warn, { marginBottom: spacing.md }]}>
              <AlertTriangle size={14} color={colors.warning} />
              <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>{"Couldn't load the latest version. Showing a saved copy."}</Small>
            </View>
          ) : null}
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.md }}>
            <Eyebrow color={colors.mutedFg}>Version {version}</Eyebrow>
            <Small style={{ fontFamily: fonts.mono }}>Effective {effective}</Small>
          </View>
          {blocks.map(([hd, bd], i) => (
            <Rise key={hd} delay={40 + i * 60}>
              <View style={[p.card, { marginBottom: spacing.sm }]}>
                <Text style={{ fontFamily: fonts.heading, fontSize: 15, color: colors.foreground, marginBottom: 4 }}>{hd}</Text>
                <Small style={{ lineHeight: 20 }}>{bd}</Small>
              </View>
            </Rise>
          ))}
          {!!acceptedTermsLabel && (
            <View style={[p.doneBanner, { marginTop: spacing.sm }]}>
              <Check size={18} color={colors.success} />
              <Small color={colors.success} weight="700">Accepted on {acceptedTermsLabel}</Small>
            </View>
          )}
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (section === "faq") {
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="FAQ" eyebrow="Help & support" onBack={() => setSection("help")} />
        <ScrollView contentContainerStyle={p.body}>
          {faqLoading ? (
            <View style={p.sectionState}><ActivityIndicator color={colors.primary} /><Small>Loading frequently asked questions…</Small></View>
          ) : null}
          {faqError ? (
            <View style={[p.feedbackBanner, p.feedbackError]}>
              <AlertTriangle size={16} color={colors.destructive} />
              <Small color={colors.destructive} style={{ flex: 1 }}>{faqError} Showing the saved copy.</Small>
              <Pressable onPress={loadFaq}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
            </View>
          ) : null}
          {faqs.map((f, i) => {
            const open = faqOpen === i;
            return (
              <Rise key={i} delay={40 + i * 60}>
                <View style={[p.card, { marginBottom: spacing.sm, borderColor: open ? colors.primary + "66" : colors.border }]}>
                  <Pressable onPress={() => setFaqOpen(open ? null : i)} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                    <Body weight="600" color={open ? colors.primary : colors.foreground} style={{ flex: 1 }}>{f.q}</Body>
                    <ChevronDown size={18} color={open ? colors.primary : colors.mutedFg} style={{ transform: [{ rotate: open ? "180deg" : "0deg" }] }} />
                  </Pressable>
                  {open && <Small style={{ marginTop: 10, lineHeight: 20 }}>{f.a}</Small>}
                </View>
              </Rise>
            );
          })}
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (section === "contact-support") {
    if (ticketSubmitted) {
      return (
        <SafeAreaView style={p.container} edges={["top"]}>
          <Header title="Contact Support" eyebrow="Help & support" onBack={() => { setTicketSubmitted(false); setSection("help"); }} />
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md }}>
            <View style={p.successCircle}><Check size={32} color={colors.success} /></View>
            <Text style={{ fontFamily: fonts.heading, fontSize: 20, color: colors.foreground }}>Ticket Submitted!</Text>
            <Small style={{ textAlign: "center" }}>{"We'll respond within 24 hours."}</Small>
            <View style={p.ticketIdBox}><Eyebrow color={colors.mutedFg}>Ticket ID</Eyebrow><Text style={{ fontFamily: fonts.mono, color: colors.primaryDeep, marginTop: 2 }}>{lastTicketId}</Text></View>
            <Pressable onPress={() => setSection("tickets")}><Small color={colors.info} weight="700">View my tickets →</Small></Pressable>
          </View>
        </SafeAreaView>
      );
    }
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="Contact Support" eyebrow="Help & support" onBack={() => setSection("help")} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={p.body} keyboardShouldPersistTaps="handled">
            <View style={p.card}>
              <Field label="Issue Category">
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                  {["Login Issue", "Notification Problem", "App Bug", "Visit Query", "Other"].map(c => {
                    const on = contact.category === c;
                    return <Springy key={c} onPress={() => setContact({ ...contact, category: c })} style={[p.chip, on ? { backgroundColor: colors.primary } : { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{c}</Small></Springy>;
                  })}
                </View>
              </Field>
              <Field label="Subject"><TextInput value={contact.subject} onChangeText={v => { setContact({ ...contact, subject: v }); setTicketError(""); }} placeholder="Brief subject" placeholderTextColor={colors.mutedFg + "99"} style={p.input} maxLength={120} /></Field>
              <Field label="Description"><TextInput value={contact.description} onChangeText={v => { setContact({ ...contact, description: v }); setTicketError(""); }} placeholder="Describe your issue…" placeholderTextColor={colors.mutedFg + "99"} multiline style={[p.input, { height: 110, textAlignVertical: "top" }]} maxLength={2000} /></Field>
              {ticketError ? (
                <View style={[p.feedbackBanner, p.feedbackError]}>
                  <AlertTriangle size={16} color={colors.destructive} />
                  <Small color={colors.destructive} style={{ flex: 1 }}>{ticketError}</Small>
                </View>
              ) : null}
            </View>
          </ScrollView>
          <View style={p.footer}>
            <Springy onPress={submitTicket} disabled={ticketSubmitting} style={[p.cta, { backgroundColor: ticketSubmitting ? colors.surface : colors.primaryDeep }]}>
              <Text style={[p.ctaText, ticketSubmitting && { color: colors.mutedFg }]}>{ticketSubmitting ? "Submitting…" : "Submit Ticket"}</Text>
            </Springy>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  }

  if (section === "tickets") {
    const statusTone = (s: string) => s === "Resolved" ? colors.success : s === "In Progress" ? colors.warning : colors.info;
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="My Tickets" eyebrow="Help & support" onBack={() => setSection("help")} />
        <ScrollView contentContainerStyle={p.body}>
          {ticketsLoading ? (
            <View style={p.sectionState}><ActivityIndicator color={colors.primary} /><Small>Loading your tickets…</Small></View>
          ) : ticketsError ? (
            <View style={[p.feedbackBanner, p.feedbackError]}>
              <AlertTriangle size={16} color={colors.destructive} />
              <Small color={colors.destructive} style={{ flex: 1 }}>{ticketsError}</Small>
              <Pressable onPress={loadTickets}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
            </View>
          ) : tickets.length === 0 ? (
            <View style={[p.card, { alignItems: "center", paddingVertical: 32, gap: 8 }]}><Ticket size={32} color={colors.mutedFg + "66"} /><Small>{"You haven't raised any tickets yet."}</Small></View>
          ) : tickets.map((t, i) => (
            <Rise key={t.id} delay={40 + i * 60}>
              <View style={[p.card, { marginBottom: spacing.sm }]}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <Text style={{ fontFamily: fonts.mono, fontSize: 12, color: colors.primaryDeep, fontWeight: "700" }}>{t.ticket_id || t.id}</Text>
                  <View style={[p.statusTag, { backgroundColor: statusTone(t.status) + "22" }]}><Small weight="700" color={statusTone(t.status)} style={{ fontSize: 10 }}>{t.status}</Small></View>
                </View>
                <Body weight="600">{t.subject}</Body>
                <Small style={{ marginTop: 2 }}>{t.category}{t.created_at ? ` · ${new Date(t.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}` : ""}</Small>
              </View>
            </Rise>
          ))}
        </ScrollView>
        <View style={p.footer}>
          <Springy onPress={() => { setTicketSubmitted(false); setSection("contact-support"); }} style={[p.cta, { backgroundColor: colors.primaryDeep, flexDirection: "row", gap: 8 }]}><MessageCircle size={16} color={colors.primaryFg} /><Text style={p.ctaText}>Raise New Ticket</Text></Springy>
        </View>
      </SafeAreaView>
    );
  }

  if (section === "help") {
    const items = [
      { icon: HelpCircle, tint: colors.info, label: "Frequently Asked Questions", sub: "Browse common questions", go: () => setSection("faq") },
      { icon: MessageCircle, tint: colors.success, label: "Contact Support", sub: "Get help from our team", go: () => setSection("contact-support") },
      { icon: Ticket, tint: colors.violet, label: "My Tickets", sub: "Track your raised tickets", go: () => setSection("tickets") },
    ];
    return (
      <SafeAreaView style={p.container} edges={["top"]}>
        <Header title="Help & Support" onBack={() => setSection("main")} />
        <ScrollView contentContainerStyle={p.body}>
          {items.map((it, i) => (
            <Rise key={it.label} delay={40 + i * 60}>
              <Springy onPress={it.go} style={[p.card, { marginBottom: spacing.sm, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={[p.iconCircle, { backgroundColor: it.tint + "1A" }]}><it.icon size={20} color={it.tint} /></View>
                  <View><Body weight="600">{it.label}</Body><Small>{it.sub}</Small></View>
                </View>
                <ChevronRight size={18} color={colors.mutedFg} />
              </Springy>
            </Rise>
          ))}
          <View style={[p.card, { marginTop: spacing.sm }]}>
            <Eyebrow color={colors.primary} style={{ marginBottom: 12 }}>Contact Us</Eyebrow>
            <View style={{ gap: 10 }}>
              {!!supportContact?.email && (
                <Pressable
                  onPress={() => openSupportLink(`mailto:${supportContact.email}`)}
                  style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
                >
                  <View style={[p.iconSm, { backgroundColor: colors.info + "1A" }]}><Mail size={15} color={colors.info} /></View>
                  <Small>{supportContact.email}</Small>
                </Pressable>
              )}
              {!!supportContact?.phone && (
                <Pressable
                  onPress={() => openSupportLink(`tel:${supportContact.phone?.replace(/[^\d+]/g, "")}`)}
                  style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
                >
                  <View style={[p.iconSm, { backgroundColor: colors.success + "1A" }]}><Phone size={15} color={colors.success} /></View>
                  <Small>{supportContact.phone}</Small>
                </Pressable>
              )}
              {!!supportContact?.hours && (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                  <View style={[p.iconSm, { backgroundColor: colors.warning + "1A" }]}><Clock size={15} color={colors.warning} /></View>
                  <Small>{supportContact.hours}</Small>
                </View>
              )}
              {!!supportContactError && (
                <View style={{ gap: 6 }}>
                  <Small color={colors.destructive}>{supportContactError}</Small>
                  <Pressable onPress={loadSupportContact}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
                </View>
              )}
              {!supportContact && !supportContactError && <Small>Loading support contact…</Small>}
            </View>
          </View>
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ══════════════════════ MAIN ══════════════════════
  const infoRows: { label: string; val: string; verify?: boolean }[] = [
    { label: "Date of birth", val: prof.dob ? (computeAge(prof.dob) != null ? `${formatDob(prof.dob)} · ${computeAge(prof.dob)} yrs` : formatDob(prof.dob)) : "—" },
    { label: "Gender", val: prof.gender || "—" },
    { label: "Phone number", val: prof.phone ? `+91 ${prof.phone}` : "—", verify: true },
    { label: "Email ID", val: prof.email || "—", verify: true },
    { label: "Preferred language", val: localeLabel(prof.language) },
  ];
  const menuGroups: { eyebrow: string; items: { icon: any; label: string; meta?: string; go: () => void }[] }[] = [
    { eyebrow: "Account", items: [{ icon: User, label: "Edit Profile", go: () => setSection("edit-profile") }, { icon: Lock, label: "Change Password", go: () => setSection("change-password") }] },
    { eyebrow: "Preferences", items: [{ icon: Globe, label: "Preferred Language", meta: localeLabel(prof.language).split(" —")[0], go: () => { setPendingLanguage(prof.language); setLanguageError(""); setShowLang(true); } }, { icon: Bell, label: "Notification Preferences", go: () => setSection("notification-prefs") }] },
    { eyebrow: "Legal & support", items: [{ icon: FileText, label: "Terms & Conditions", go: () => setSection("terms") }, { icon: Shield, label: "Privacy Policy", go: () => setSection("privacy") }, { icon: HelpCircle, label: "Help & Support", go: () => setSection("help") }] },
  ];

  return (
    <SafeAreaView style={p.container} edges={["top"]}>
      <View style={h.wrap}>
        <Springy onPress={() => router.back()} style={h.back}><ChevronLeft size={22} color={colors.primaryFg} /></Springy>
        <View style={{ flex: 1 }}><Eyebrow color={colors.overlay25}>Account</Eyebrow><Text style={h.title}>Profile & Settings</Text></View>
      </View>
      <ScrollView contentContainerStyle={[p.body, { paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }]}>
        {profileLoading ? (
          <View style={p.sectionState}><ActivityIndicator color={colors.primary} /><Small>Loading your profile…</Small></View>
        ) : null}
        {profileLoadError ? (
          <View style={[p.feedbackBanner, p.feedbackError]}>
            <AlertTriangle size={16} color={colors.destructive} />
            <Small color={colors.destructive} style={{ flex: 1 }}>{profileLoadError}</Small>
            <Pressable onPress={loadProfile}><Small color={colors.primary} weight="700">Retry</Small></Pressable>
          </View>
        ) : null}
        {/* Identity hero */}
        <Rise delay={40}>
          <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={p.hero}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 16 }}>
              {avatarUri
                ? <Image source={{ uri: avatarUri }} style={p.heroAvatar} />
                : <View style={p.heroAvatar}><Text style={{ fontFamily: fonts.display, fontSize: 24, color: colors.primaryFg }}>{initials}</Text></View>}
              <View style={{ flex: 1 }}>
                <Text style={{ fontFamily: fonts.heading, fontSize: 20, color: colors.primaryFg }} numberOfLines={1}>{prof.fullName || user?.full_name}</Text>
                <View style={p.roleBadge}><Sparkles size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700" style={{ textTransform: "capitalize" }}>{user?.role || "Patient"}</Small></View>
              </View>
            </View>
          </LinearGradient>
        </Rise>

        {/* Personal details */}
        <Rise delay={110}>
          <View style={[p.card, { marginTop: spacing.md }]}>
            <Eyebrow color={colors.mutedFg} style={{ marginBottom: 4 }}>Personal details</Eyebrow>
            {infoRows.map((r, i) => (
              <View key={r.label} style={[{ paddingVertical: 10 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border + "99" }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><Small>{r.label}</Small>{r.verify && <ShieldCheck size={12} color={colors.success} />}</View>
                <Body weight="600" style={{ marginTop: 2 }}>{r.val}</Body>
              </View>
            ))}
          </View>
        </Rise>

        {/* Menu groups */}
        {menuGroups.map((group, gi) => (
          <Rise key={group.eyebrow} delay={200 + gi * 80}>
            <Eyebrow color={colors.mutedFg} style={{ marginTop: spacing.md, marginBottom: spacing.sm, marginLeft: 4 }}>{group.eyebrow}</Eyebrow>
            <View style={[p.card, { padding: 0 }]}>
              {group.items.map((it, i) => (
                <Springy key={it.label} onPress={it.go} testID={`profile-${it.label.toLowerCase().replace(/[^a-z]+/g, "-")}`} style={[{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 14 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={p.iconCircle}><it.icon size={18} color={colors.primary} /></View>
                    <Body>{it.label}</Body>
                  </View>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>{it.meta ? <Small>{it.meta}</Small> : null}<ChevronRight size={16} color={colors.mutedFg} /></View>
                </Springy>
              ))}
            </View>
          </Rise>
        ))}

        {/* Logout */}
        <Rise delay={460}>
          <Springy onPress={() => setShowLogout(true)} testID="profile-logout" style={[p.card, { marginTop: spacing.md, borderColor: colors.destructive + "33", flexDirection: "row", alignItems: "center", gap: 12 }]}>
            <View style={[p.iconCircle, { backgroundColor: colors.destructive + "1A" }]}><LogOut size={18} color={colors.destructive} /></View>
            <Body weight="700" color={colors.destructive}>Log out</Body>
          </Springy>
        </Rise>

        <View style={{ alignItems: "center", paddingTop: spacing.md }}>
          <Small color={colors.mutedFg}>My Trial Board · v2.1.0</Small>
          <Small color={colors.mutedFg}>© 2026 MTB Health Technologies</Small>
        </View>
      </ScrollView>
      <PatientBottomNav active="me" />

      {/* Logout dialog */}
      <Modal visible={showLogout} transparent animationType="fade" onRequestClose={() => setShowLogout(false)}>
        <View style={p.dialogOverlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowLogout(false)} />
          <View style={p.dialog}>
            <View style={[p.iconCircle, { backgroundColor: colors.destructive + "1A", marginBottom: 12 }]}><LogOut size={22} color={colors.destructive} /></View>
            <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginBottom: 6 }}>Log Out?</Text>
            <Small style={{ marginBottom: 20, lineHeight: 20 }}>Are you sure you want to log out of My Trial Board?</Small>
            <View style={{ flexDirection: "row", gap: 12 }}>
              <Springy onPress={() => setShowLogout(false)} style={[p.dialogBtn, { borderWidth: 1, borderColor: colors.border }]}><Small weight="700" color={colors.foreground}>Cancel</Small></Springy>
              <Springy onPress={async () => { setShowLogout(false); await signOut(); router.replace("/(auth)/welcome"); }} style={[p.dialogBtn, { backgroundColor: colors.destructive }]}><Small weight="700" color={colors.destructiveFg}>Log Out</Small></Springy>
            </View>
          </View>
        </View>
      </Modal>

      {/* Language sheet */}
      <Modal visible={showLang} transparent animationType="slide" onRequestClose={() => setShowLang(false)}>
        <View style={p.sheetBottomOverlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowLang(false)} />
          <View style={p.bottomSheet}>
            <View style={p.grabber} />
            <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.primaryDeep, marginBottom: spacing.md }}>Preferred Language</Text>
            {APP_LOCALES.map(locale => {
              const active = pendingLanguage === locale.code;
              return (
                <Pressable key={locale.code} disabled={languageBusy} onPress={() => { setPendingLanguage(locale.code); setLanguageError(""); }} style={[p.langRow, active && { backgroundColor: colors.primary + "14" }]}>
                  <View style={[p.radio, { borderColor: active ? colors.primary : colors.border }]}>{active && <View style={p.radioDot} />}</View>
                  <Body weight={active ? "600" : "400"}>{locale.label}</Body>
                </Pressable>
              );
            })}
            {languageError ? (
              <View style={[p.feedbackBanner, p.feedbackError, { marginTop: spacing.sm }]}>
                <AlertTriangle size={16} color={colors.destructive} />
                <Small color={colors.destructive} style={{ flex: 1 }}>{languageError}</Small>
              </View>
            ) : null}
            <Springy onPress={applyLanguage} disabled={languageBusy || pendingLanguage === prof.language} style={[p.cta, { backgroundColor: languageBusy || pendingLanguage === prof.language ? colors.surface : colors.primaryDeep, marginTop: spacing.md }]}>
              <Text style={[p.ctaText, (languageBusy || pendingLanguage === prof.language) && { color: colors.mutedFg }]}>
                {languageBusy ? "Applying…" : pendingLanguage === prof.language ? "Selected" : "Apply"}
              </Text>
            </Springy>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const h = StyleSheet.create({
  wrap: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.md, paddingTop: 12, paddingBottom: 16 },
  back: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.display, fontSize: 20, color: colors.primaryFg },
  rightBtn: { backgroundColor: colors.overlay20, borderRadius: 999, paddingHorizontal: 14, paddingVertical: 7 },
});

const p = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  body: { padding: spacing.md, paddingBottom: spacing.xxl },
  card: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.md },
  hero: { borderRadius: radii.xl, padding: spacing.md + 4 },
  heroAvatar: { width: 60, height: 60, borderRadius: 30, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: colors.overlay25 },
  roleBadge: { flexDirection: "row", alignItems: "center", gap: 4, alignSelf: "flex-start", marginTop: 6, backgroundColor: colors.overlay20, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3 },
  iconCircle: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  iconSm: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center" },
  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: colors.mutedFg, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.6 },
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  prefix: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  warn: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 8, backgroundColor: colors.warning + "14", borderWidth: 1, borderColor: colors.warning + "33", borderRadius: radii.md, padding: 10 },
  sectionState: { alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: spacing.lg },
  feedbackBanner: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1, borderRadius: radii.md, padding: 10, marginBottom: spacing.md },
  feedbackSuccess: { backgroundColor: colors.success + "14", borderColor: colors.success + "40" },
  feedbackError: { backgroundColor: colors.destructive + "12", borderColor: colors.destructive + "40" },
  avatarLg: { width: 80, height: 80, borderRadius: 40, alignItems: "center", justifyContent: "center" },
  camBtn: { position: "absolute", bottom: -2, right: -2, width: 28, height: 28, borderRadius: 14, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  eye: { position: "absolute", right: 12, top: 0, bottom: 0, justifyContent: "center" },
  ruleDot: { width: 18, height: 18, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  dayBtn: { flex: 1, borderWidth: 1, borderRadius: radii.md, paddingVertical: 10, alignItems: "center" },
  chip: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999 },
  doneBanner: { flexDirection: "row", alignItems: "center", gap: 8, padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.success + "40", backgroundColor: colors.success + "14" },
  statusTag: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  successCircle: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" },
  ticketIdBox: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 20, paddingVertical: 12, alignItems: "center" },
  footer: { paddingHorizontal: spacing.md, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  ctaText: { fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg },
  dialogOverlay: { flex: 1, backgroundColor: colors.black + "80", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  dialog: { backgroundColor: colors.card, borderRadius: 28, padding: spacing.lg, width: "100%", maxWidth: 320 },
  dialogBtn: { flex: 1, paddingVertical: 12, borderRadius: radii.md, alignItems: "center" },
  resend: { alignItems: "center", paddingVertical: 6, marginBottom: 4 },
  resendCount: { textAlign: "center", fontSize: 10, marginBottom: 10 },
  sheetBottomOverlay: { flex: 1, backgroundColor: colors.black + "80", justifyContent: "flex-end" },
  bottomSheet: { backgroundColor: colors.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, padding: spacing.lg },
  grabber: { alignSelf: "center", width: 40, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: spacing.md },
  langRow: { flexDirection: "row", alignItems: "center", gap: 12, borderRadius: radii.md, paddingHorizontal: 12, paddingVertical: 12 },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, alignItems: "center", justifyContent: "center" },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.primary },
  sheetOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  sheetCenter: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, width: "100%", overflow: "hidden", paddingVertical: 4 },
  sheetItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 14 },
});
