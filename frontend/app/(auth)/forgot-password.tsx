import React, { useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ArrowLeft, CheckCircle2, Eye, EyeOff, HelpCircle, Mail, Phone, RotateCw } from "lucide-react-native";
import { colors, fonts, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Button, Card } from "@/src/components/ui";
import { api } from "@/src/api/client";
import { splitE164 } from "@/src/data/countries";
import { normalizePhone } from "@/src/features/auth/registration-validation";

type Step = "contact" | "otp" | "password" | "success";
const OTP_SECONDS = 120;
const MAX_RESENDS = 3;
// Independent of the OTP's own validity — just a per-channel cooldown that
// stops the Resend button from being spammed against the API.
const RESEND_COOLDOWN_SEC = { phone: 60, email: 120 } as const;

export default function ForgotPassword() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("contact");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [seconds, setSeconds] = useState(OTP_SECONDS);
  const [resends, setResends] = useState(0);
  const [resendSeconds, setResendSeconds] = useState<number>(RESEND_COOLDOWN_SEC.email);
  const [showResendCount, setShowResendCount] = useState(false);
  const [recoveryId, setRecoveryId] = useState("");
  const [recoveryChannel, setRecoveryChannel] = useState<"email" | "phone">("email");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const otpRef = useRef<TextInput>(null);

  useEffect(() => {
    if (step !== "otp" || seconds <= 0) return;
    const timer = setInterval(() => setSeconds(current => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [seconds, step]);

  useEffect(() => {
    if (step !== "otp" || resendSeconds <= 0) return;
    const timer = setInterval(() => setResendSeconds(current => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [resendSeconds, step]);

  // Flash the attempt count for 5s on entry and whenever it changes or the
  // resend cooldown just opened up, instead of leaving it on screen always.
  const resendReady = resendSeconds === 0;
  useEffect(() => {
    if (step !== "otp") return;
    setShowResendCount(true);
    const timer = setTimeout(() => setShowResendCount(false), 5000);
    return () => clearTimeout(timer);
  }, [resends, resendReady, step]);

  const normalizedEmail = email.trim().toLowerCase();
  // Accept any country: a typed "+<code>" picks the country, a bare number is Indian.
  const typedPhone = splitE164(email);
  const normalizedPhone = normalizePhone(typedPhone.national, typedPhone.country.code);
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail);
  const contactValid = email.includes("@") ? emailValid : !!normalizedPhone;
  const otpValid = /^\d{6}$/.test(otp);
  const rules = {
    length: newPw.length >= 8,
    upper: /[A-Z]/.test(newPw),
    lower: /[a-z]/.test(newPw),
    number: /\d/.test(newPw),
    special: /[^A-Za-z0-9]/.test(newPw),
  };
  const strong = Object.values(rules).every(Boolean);
  const passwordsMatch = !!confirmPw && newPw === confirmPw;

  const requestCode = async (isResend = false) => {
    if (!contactValid || loading) return;
    setLoading(true);
    setErr("");
    try {
      const response = await api.post("/auth/forgot", email.includes("@")
        ? { email: normalizedEmail }
        : { phone: normalizedPhone });
      const channel = response.data?.channel === "phone" ? "phone" : "email";
      setRecoveryId(response.data?.recovery_id || "");
      setRecoveryChannel(channel);
      setOtp("");
      setSeconds(response.data?.expires_in || OTP_SECONDS);
      setResendSeconds(RESEND_COOLDOWN_SEC[channel]);
      if (typeof response.data?.resend_count === "number") setResends(response.data.resend_count);
      else if (isResend) setResends(count => count + 1);
      setStep("otp");
      setTimeout(() => otpRef.current?.focus(), 120);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "We couldn't send the code. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const resetPassword = async () => {
    if (!otpValid || !strong || !passwordsMatch || loading) return;
    setLoading(true);
    setErr("");
    try {
      await api.post("/auth/reset", {
        recovery_id: recoveryId,
        otp,
        new_password: newPw,
      });
      setStep("success");
    } catch (e: any) {
      const message = e?.response?.data?.detail || "Password reset failed.";
      setErr(message);
      if (/expired|incorrect attempts|invalid otp/i.test(message)) setStep("otp");
    } finally {
      setLoading(false);
    }
  };

  const verifyOtp = async () => {
    if (!otpValid || seconds === 0 || loading) return;
    setLoading(true);
    setErr("");
    try {
      await api.post("/auth/forgot/verify", {
        recovery_id: recoveryId,
        otp,
      });
      setStep("password");
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      setErr(
        /invalid otp/i.test(detail || "")
          ? "Invalid OTP. Please enter the correct OTP."
          : detail || "We couldn't verify the code. Please try again."
      );
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    setErr("");
    if (step === "password") setStep("otp");
    else if (step === "otp") setStep("contact");
    else if (step === "success") router.replace("/(auth)/sign-in");
    else router.back();
  };

  const resendMm = String(Math.floor(resendSeconds / 60));
  const resendSs = String(resendSeconds % 60).padStart(2, "0");

  return (
    <SafeAreaView style={s.page} edges={["top", "bottom"]}>
      <View style={s.content}>
        <Pressable testID="forgot-back" onPress={goBack} hitSlop={12} style={s.back}>
          <ArrowLeft size={21} color={colors.foreground} />
        </Pressable>

        {step === "contact" && (
          <>
            <Eyebrow color={colors.accent} style={s.eyebrow}>Account recovery</Eyebrow>
            <H1>Forgot your password?</H1>
            <Small style={s.subtitle}>{"Enter the email or phone number registered with your account and we'll send a verification code."}</Small>
            <Card style={s.card}>
              <Eyebrow style={s.label}>Registered contact</Eyebrow>
              <View style={s.inputWrap}>
                {email.includes("@") ? <Mail size={16} color={colors.mutedFg} /> : <Phone size={16} color={colors.mutedFg} />}
                <TextInput
                  testID="forgot-email"
                  value={email}
                  onChangeText={value => { setEmail(value); setErr(""); }}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  placeholder="Email or phone (with country code)"
                  placeholderTextColor={colors.mutedFg}
                  style={s.input}
                />
              </View>
              {!!email && !contactValid && <Small color={colors.destructive} style={s.inlineError}>Enter a valid email or 10-digit Indian phone number.</Small>}
            </Card>
            {!!err && <Small color={colors.destructive} style={s.error}>{err}</Small>}
            <View style={s.bottom}>
              <Button testID="forgot-send-otp" onPress={() => requestCode()} loading={loading} disabled={!contactValid}>Send verification code</Button>
              <SupportLink onPress={() => router.push("/(auth)/help-support")} />
            </View>
          </>
        )}

        {step === "otp" && (
          <>
            <Eyebrow color={colors.accent} style={s.eyebrow}>Verify your identity</Eyebrow>
            <H1>Check your messages</H1>
            <Small style={s.subtitle}>We sent a six-digit code to {recoveryChannel === "phone" ? maskPhone(normalizedPhone) : maskEmail(normalizedEmail)}.</Small>

            <Pressable style={s.otpRow} onPress={() => otpRef.current?.focus()}>
              {Array.from({ length: 6 }, (_, index) => (
                <View key={index} style={[s.otpCell, otp[index] && s.otpCellFilled, seconds === 0 && s.otpCellExpired]}>
                  <Body weight="700" style={s.otpDigit}>{otp[index] || ""}</Body>
                </View>
              ))}
              <TextInput
                ref={otpRef}
                testID="forgot-otp"
                value={otp}
                onChangeText={value => { setOtp(value.replace(/\D/g, "").slice(0, 6)); setErr(""); }}
                keyboardType="number-pad"
                maxLength={6}
                style={s.hiddenOtp}
              />
            </Pressable>

            <Pressable
              testID="forgot-resend"
              onPress={() => requestCode(true)}
              disabled={resendSeconds > 0 || resends >= MAX_RESENDS || loading}
              style={[s.resend, (resendSeconds > 0 || resends >= MAX_RESENDS) && { opacity: 0.45 }]}
            >
              <RotateCw size={14} color={colors.primary} />
              <Small color={colors.primary} weight="700">
                {resendSeconds > 0 ? `Resend code in ${resendMm}:${resendSs}` : "Resend OTP"}
              </Small>
            </Pressable>
            {showResendCount && (
              <Small style={s.resendCount}>{resends}/{MAX_RESENDS} resend attempts used</Small>
            )}
            {!!err && <Text style={s.otpError}>{err}</Text>}
            <View style={s.bottom}>
              <Button
                testID="forgot-verify"
                disabled={!otpValid || seconds === 0}
                loading={loading}
                onPress={verifyOtp}
              >Continue</Button>
              <SupportLink onPress={() => router.push("/(auth)/help-support")} />
            </View>
          </>
        )}

        {step === "password" && (
          <>
            <Eyebrow color={colors.accent} style={s.eyebrow}>Secure your account</Eyebrow>
            <H1>Create a new password</H1>
            <Small style={s.subtitle}>{"Choose a strong password you haven't used for this account before."}</Small>

            <PasswordField testID="forgot-newpw" label="New password" value={newPw} onChange={value => { setNewPw(value); setErr(""); }} visible={showPw} onToggle={() => setShowPw(v => !v)} />
            <PasswordField testID="forgot-confirm" label="Confirm password" value={confirmPw} onChange={value => { setConfirmPw(value); setErr(""); }} visible={showConfirm} onToggle={() => setShowConfirm(v => !v)} />
            {!!confirmPw && !passwordsMatch && <Small color={colors.destructive} style={s.inlineError}>Passwords do not match.</Small>}

            <Card style={s.strengthCard}>
              <View style={s.strengthHead}>
                <Eyebrow>Password strength</Eyebrow>
                <Small color={strong ? colors.success : colors.warning} weight="700">{strong ? "Strong" : "Keep going"}</Small>
              </View>
              <View style={s.ruleGrid}>
                <Rule ok={rules.length} text="8+ characters" />
                <Rule ok={rules.upper} text="Uppercase letter" />
                <Rule ok={rules.lower} text="Lowercase letter" />
                <Rule ok={rules.number} text="Number" />
                <Rule ok={rules.special} text="Special character" />
              </View>
            </Card>
            {!!err && <Small color={colors.destructive} style={s.error}>{err}</Small>}
            <View style={s.bottom}>
              <Button testID="forgot-reset" onPress={resetPassword} loading={loading} disabled={!strong || !passwordsMatch}>Reset password</Button>
            </View>
          </>
        )}

        {step === "success" && (
          <View style={s.success}>
            <View style={s.successIcon}><CheckCircle2 size={42} color={colors.success} /></View>
            <Eyebrow color={colors.success}>Password updated</Eyebrow>
            <H1 style={{ textAlign: "center" }}>{"You're ready to sign in"}</H1>
            <Small style={[s.subtitle, { textAlign: "center" }]}>Your password was changed successfully.</Small>
            <Button testID="forgot-success-signin" style={{ alignSelf: "stretch", marginTop: spacing.lg }} onPress={() => router.replace("/(auth)/sign-in")}>Back to sign in</Button>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

function PasswordField({ testID, label, value, onChange, visible, onToggle }: {
  testID: string; label: string; value: string; onChange: (value: string) => void; visible: boolean; onToggle: () => void;
}) {
  return (
    <View style={{ marginTop: spacing.md }}>
      <Small weight="700" style={{ marginBottom: 6 }}>{label}</Small>
      <View style={s.passwordWrap}>
        <TextInput
          testID={testID}
          value={value}
          onChangeText={onChange}
          secureTextEntry={!visible}
          autoCapitalize="none"
          placeholder="Enter password"
          placeholderTextColor={colors.mutedFg}
          style={s.passwordInput}
        />
        <Pressable onPress={onToggle} hitSlop={10}>{visible ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}</Pressable>
      </View>
    </View>
  );
}

function Rule({ ok, text }: { ok: boolean; text: string }) {
  return (
    <View style={s.rule}>
      <View style={[s.ruleDot, ok && { backgroundColor: colors.success }]} />
      <Small color={ok ? colors.foreground : colors.mutedFg}>{text}</Small>
    </View>
  );
}

function SupportLink({ onPress }: { onPress: () => void }) {
  return (
    <Pressable testID="forgot-contact-support" onPress={onPress} accessibilityRole="link" style={s.supportLink}>
      <HelpCircle size={15} color={colors.mutedFg} />
      <Small>Need help? Contact Support</Small>
    </Pressable>
  );
}

function maskEmail(value: string) {
  const [name, domain] = value.split("@");
  if (!domain) return value;
  return `${name.slice(0, 1)}${"•".repeat(Math.max(3, name.length - 1))}@${domain}`;
}

function maskPhone(value: string) {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 4) return value;
  return `+${digits.slice(0, 2)} ••••••${digits.slice(-4)}`;
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  content: { flex: 1, padding: spacing.lg },
  back: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  eyebrow: { marginTop: spacing.md, marginBottom: 6 },
  subtitle: { marginTop: 7, lineHeight: 19, maxWidth: 330 },
  card: { marginTop: spacing.xl },
  label: { marginBottom: 8 },
  inputWrap: { minHeight: 48, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", gap: 8 },
  input: { flex: 1, color: colors.foreground, fontFamily: fonts.regular, fontSize: 14 },
  inlineError: { marginTop: 7 },
  error: { marginTop: spacing.md, textAlign: "center" },
  otpError: {
    marginTop: spacing.md,
    textAlign: "center",
    color: "#D00000",
    fontFamily: fonts.semibold,
    fontSize: 13,
  },
  bottom: { marginTop: "auto", paddingTop: spacing.lg },
  supportLink: { marginTop: spacing.sm, paddingVertical: spacing.sm, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  otpRow: { position: "relative", flexDirection: "row", justifyContent: "center", gap: 7, marginTop: spacing.xl },
  otpCell: { width: 43, height: 52, borderRadius: 13, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  otpCellFilled: { borderColor: colors.primary, backgroundColor: colors.secondary },
  otpCellExpired: { borderColor: colors.destructive + "55" },
  otpDigit: { fontSize: 20, color: colors.primary },
  hiddenOtp: { position: "absolute", opacity: 0, width: 1, height: 1 },
  resend: { marginTop: spacing.xl, flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 6, paddingVertical: 6 },
  resendCount: { textAlign: "center", fontSize: 10 },
  passwordWrap: { minHeight: 48, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 13, flexDirection: "row", alignItems: "center" },
  passwordInput: { flex: 1, color: colors.foreground, fontFamily: fonts.regular, fontSize: 14 },
  strengthCard: { marginTop: spacing.md },
  strengthHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.sm },
  ruleGrid: { flexDirection: "row", flexWrap: "wrap", gap: 9 },
  rule: { width: "47%", flexDirection: "row", alignItems: "center", gap: 6 },
  ruleDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.border },
  success: { flex: 1, justifyContent: "center", alignItems: "center", gap: spacing.sm },
  successIcon: { width: 82, height: 82, borderRadius: 41, backgroundColor: colors.success + "18", alignItems: "center", justifyContent: "center", marginBottom: spacing.md },
});
