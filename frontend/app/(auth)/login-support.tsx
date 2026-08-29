import React, { useEffect, useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ArrowLeft, CheckCircle2, LifeBuoy, RotateCw } from "lucide-react-native";
import { api } from "@/src/api/client";
import { Body, Button, Eyebrow, H1, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";

type Step = "details" | "verify" | "success";
const OTP_SECONDS = 10 * 60;
const MAX_RESENDS = 3;
// Independent of the OTP's own validity — just a cooldown that stops the
// Resend button from being spammed against the API. This flow is email-only.
const RESEND_COOLDOWN_SEC = 120;

function errorMessage(error: any, fallback: string) {
  const detail = error?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function maskEmail(value: string) {
  const [name, domain] = value.split("@");
  if (!domain) return value;
  return `${name.slice(0, 1)}${"•".repeat(Math.max(3, name.length - 1))}@${domain}`;
}

export default function LoginSupport() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("details");
  const [email, setEmail] = useState("");
  const [subject, setSubject] = useState("Unable to log in");
  const [description, setDescription] = useState("");
  const [requestId, setRequestId] = useState("");
  const [otp, setOtp] = useState("");
  const [ticketId, setTicketId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [seconds, setSeconds] = useState(OTP_SECONDS);
  const [resends, setResends] = useState(0);
  const [resendSeconds, setResendSeconds] = useState(RESEND_COOLDOWN_SEC);
  const [showResendCount, setShowResendCount] = useState(false);
  const otpRef = useRef<TextInput>(null);

  useEffect(() => {
    if (step !== "verify" || seconds <= 0) return;
    const timer = setInterval(() => setSeconds((current) => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [step, seconds]);

  useEffect(() => {
    if (step !== "verify" || resendSeconds <= 0) return;
    const timer = setInterval(() => setResendSeconds((current) => Math.max(0, current - 1)), 1000);
    return () => clearInterval(timer);
  }, [step, resendSeconds]);

  // Flash the attempt count for 5s on entry and whenever it changes or the
  // resend cooldown just opened up, instead of leaving it on screen always.
  const resendReady = resendSeconds === 0;
  useEffect(() => {
    if (step !== "verify") return;
    setShowResendCount(true);
    const timer = setTimeout(() => setShowResendCount(false), 5000);
    return () => clearTimeout(timer);
  }, [resends, resendReady, step]);

  const detailsValid = /^\S+@\S+\.\S+$/.test(email.trim())
    && subject.trim().length >= 3
    && description.trim().length >= 10;

  const startRequest = async (isResend = false) => {
    if (!detailsValid || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await api.post("/auth/support/start", {
        email: email.trim().toLowerCase(),
        subject: subject.trim(),
        description: description.trim(),
      });
      setRequestId(response.data.request_id);
      setOtp("");
      setSeconds(Number(response.data.expires_in) || OTP_SECONDS);
      setResendSeconds(RESEND_COOLDOWN_SEC);
      if (isResend) setResends((current) => current + 1);
      setStep("verify");
    } catch (e: any) {
      setError(errorMessage(e, "We couldn't start your support request. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  const verifyAndSubmit = async () => {
    if (otp.length !== 6 || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await api.post("/auth/support/verify", {
        request_id: requestId,
        otp,
      });
      setTicketId(response.data.ticket_id || "");
      setStep("success");
    } catch (e: any) {
      setError(errorMessage(e, "We couldn't verify the code. Please try again."));
    } finally {
      setLoading(false);
    }
  };

  const back = () => {
    setError("");
    if (step === "verify") {
      setStep("details");
      return;
    }
    router.back();
  };

  const resendMm = String(Math.floor(resendSeconds / 60));
  const resendSs = String(resendSeconds % 60).padStart(2, "0");

  return (
    <SafeAreaView style={s.page} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
          {step !== "success" && (
            <Pressable onPress={back} hitSlop={12} style={s.back} accessibilityLabel="Back">
              <ArrowLeft size={22} color={colors.foreground} />
            </Pressable>
          )}

          {step === "details" && (
            <>
              <View style={s.heroIcon}><LifeBuoy size={28} color={colors.primary} /></View>
              <Eyebrow color={colors.accent}>LOGIN SUPPORT</Eyebrow>
              <H1 style={s.title}>How can we help?</H1>
              <Body style={s.subtitle}>Enter your registered email and describe the problem you are having while signing in.</Body>

              <View style={s.form}>
                <Small color={colors.foreground} style={s.label}>Registered email address *</Small>
                <TextInput
                  testID="login-support-email"
                  value={email}
                  onChangeText={setEmail}
                  placeholder="name@example.com"
                  placeholderTextColor={colors.mutedFg}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="email-address"
                  style={s.input}
                />

                <Small color={colors.foreground} style={s.label}>Subject *</Small>
                <TextInput
                  testID="login-support-subject"
                  value={subject}
                  onChangeText={setSubject}
                  maxLength={120}
                  style={s.input}
                />

                <Small color={colors.foreground} style={s.label}>Describe the issue *</Small>
                <TextInput
                  testID="login-support-description"
                  value={description}
                  onChangeText={setDescription}
                  placeholder="Tell us what happens when you try to sign in..."
                  placeholderTextColor={colors.mutedFg}
                  multiline
                  maxLength={2000}
                  textAlignVertical="top"
                  style={[s.input, s.description]}
                />
                <Small style={s.counter}>{description.length}/2000</Small>
                {!!error && <Text style={s.error}>{error}</Text>}
                <Button
                  testID="login-support-send-code"
                  onPress={() => startRequest(false)}
                  disabled={!detailsValid}
                  loading={loading}
                >Continue</Button>
              </View>
            </>
          )}

          {step === "verify" && (
            <>
              <Eyebrow color={colors.accent} style={s.verifyEyebrow}>VERIFY YOUR IDENTITY</Eyebrow>
              <H1>Check your messages</H1>
              <Small style={s.verifyCopy}>
                If this is a registered account, we sent a six-digit code to {maskEmail(email.trim())}.
              </Small>

              <Pressable style={s.otpRow} onPress={() => otpRef.current?.focus()}>
                {Array.from({ length: 6 }, (_, index) => (
                  <View
                    key={index}
                    style={[s.otpCell, otp[index] && s.otpCellFilled, seconds === 0 && s.otpCellExpired]}
                  >
                    <Body weight="700" style={s.otpDigit}>{otp[index] || ""}</Body>
                  </View>
                ))}
                <TextInput
                  ref={otpRef}
                  testID="login-support-otp"
                  value={otp}
                  onChangeText={(value) => {
                    setOtp(value.replace(/\D/g, "").slice(0, 6));
                    setError("");
                  }}
                  keyboardType="number-pad"
                  maxLength={6}
                  style={s.hiddenOtp}
                />
              </Pressable>

              <Pressable
                testID="login-support-resend"
                onPress={() => startRequest(true)}
                disabled={resendSeconds > 0 || resends >= MAX_RESENDS || loading}
                style={[s.resend, (resendSeconds > 0 || resends >= MAX_RESENDS) && { opacity: 0.45 }]}
              >
                <RotateCw size={14} color={colors.primary} />
                <Small color={colors.primary} style={s.linkText}>
                  {resendSeconds > 0 ? `Resend code in ${resendMm}:${resendSs}` : "Resend OTP"}
                </Small>
              </Pressable>
              {showResendCount && (
                <Small style={s.resendCount}>{resends}/{MAX_RESENDS} resend attempts used</Small>
              )}
              {!!error && <Text style={s.error}>{error}</Text>}
              <View style={s.verifyBottom}>
                <Button
                  testID="login-support-submit"
                  onPress={verifyAndSubmit}
                  disabled={otp.length !== 6 || seconds === 0}
                  loading={loading}
                >Submit Ticket</Button>
                <Pressable onPress={() => { setError(""); setStep("details"); }} style={s.secondaryLink}>
                  <Small color={colors.primary} style={s.linkText}>Change support details</Small>
                </Pressable>
              </View>
            </>
          )}

          {step === "success" && (
            <View style={s.success}>
              <CheckCircle2 size={68} color={colors.success} />
              <Eyebrow color={colors.success}>TICKET SUBMITTED</Eyebrow>
              <H1 style={s.successTitle}>We’ll look into it.</H1>
              <Body style={s.successCopy}>Your login support request has been sent to Platform Support.</Body>
              <View style={s.ticketBox}>
                <Small>Ticket reference</Small>
                <Text selectable style={s.ticketId}>{ticketId}</Text>
              </View>
              <View style={s.fullWidth}>
                <Button onPress={() => router.replace("/(auth)/sign-in")}>Back to Sign In</Button>
              </View>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  content: { flexGrow: 1, padding: spacing.lg, paddingBottom: spacing.xxl },
  back: { width: 40, height: 40, alignItems: "center", justifyContent: "center", marginBottom: spacing.lg },
  heroIcon: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.primary + "14", alignItems: "center", justifyContent: "center", marginBottom: spacing.md },
  title: { marginTop: 6 },
  subtitle: { marginTop: 8, lineHeight: 21, maxWidth: 380 },
  form: { marginTop: spacing.xl },
  label: { marginBottom: 7, marginTop: spacing.md, fontFamily: fonts.semibold },
  input: { minHeight: 50, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 14, paddingVertical: 12, color: colors.foreground, fontFamily: fonts.regular, fontSize: 15 },
  description: { minHeight: 125 },
  counter: { textAlign: "right", marginTop: 5 },
  error: { color: "#D00000", fontFamily: fonts.semibold, fontSize: 13, textAlign: "center", marginVertical: spacing.md },
  verifyEyebrow: { marginTop: spacing.md, marginBottom: 6 },
  verifyCopy: { marginTop: 7, lineHeight: 19, maxWidth: 330 },
  otpRow: { position: "relative", flexDirection: "row", justifyContent: "center", gap: 7, marginTop: spacing.xl },
  otpCell: { width: 43, height: 52, borderRadius: 13, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  otpCellFilled: { borderColor: colors.primary, backgroundColor: colors.secondary },
  otpCellExpired: { borderColor: colors.destructive + "55" },
  otpDigit: { fontSize: 20, color: colors.primary },
  hiddenOtp: { position: "absolute", opacity: 0, width: 1, height: 1 },
  resend: { marginTop: spacing.xl, flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 6, paddingVertical: 6 },
  resendCount: { textAlign: "center", fontSize: 10 },
  verifyBottom: { marginTop: "auto", paddingTop: spacing.lg },
  fullWidth: { width: "100%", marginTop: spacing.xl },
  secondaryLink: { paddingVertical: spacing.md, alignItems: "center" },
  linkText: { fontFamily: fonts.semibold },
  success: { flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: spacing.xxl },
  successTitle: { marginTop: spacing.md, textAlign: "center" },
  successCopy: { marginTop: 8, textAlign: "center", lineHeight: 21, maxWidth: 340 },
  ticketBox: { width: "100%", marginTop: spacing.xl, padding: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", gap: 5 },
  ticketId: { color: colors.primary, fontFamily: fonts.bold, fontSize: 18 },
});
