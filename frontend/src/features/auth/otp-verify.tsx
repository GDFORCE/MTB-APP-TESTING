import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Mail, Smartphone, Clock, ShieldOff, Check } from "lucide-react-native";
import { colors, spacing, radii, fonts, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";
import { splitE164 } from "@/src/data/countries";

export const OTP_LEN = 6;
const MAX_RESEND = 3;

// Keep the country code visible, mask the middle: "+91 ••••••3210".
export function maskPhone(full: string): string {
  if (!full.replace(/\D/g, "")) return full;
  const { country, national } = splitE164(full);
  const last4 = national.slice(-4);
  return `+${country.dial} ${"•".repeat(Math.max(0, national.length - 4))}${last4}`;
}
// Keep first + last of the local part, domain visible: "j•••e@example.com".
export function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!domain) return email;
  if (local.length <= 2) return `${local[0]}•@${domain}`;
  return `${local[0]}${"•".repeat(Math.min(local.length - 2, 4))}${local[local.length - 1]}@${domain}`;
}

function OtpCells({ channel, destination, value, onChange }: { channel: "phone" | "email"; destination: string; value: string; onChange: (v: string) => void }) {
  const ref = useRef<TextInput>(null);
  const digits = value.split("");
  const complete = value.length === OTP_LEN;
  const Icon = channel === "phone" ? Smartphone : Mail;
  return (
    <View style={s.block}>
      <View style={s.blockHead}>
        <View style={s.channelChip}>
          <Icon size={15} color={colors.primary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={s.channelText}>{channel === "phone" ? "Phone" : "Email"}</Text>
          <Text style={s.dest} numberOfLines={1}>{destination}</Text>
        </View>
        {complete ? (
          <View style={s.doneChip}>
            <Check size={13} color={colors.success} strokeWidth={3} />
          </View>
        ) : (
          <Text style={s.progressCount}>{value.length}/{OTP_LEN}</Text>
        )}
      </View>
      <Pressable onPress={() => ref.current?.focus()} style={s.cells}>
        {Array.from({ length: OTP_LEN }).map((_, i) => {
          const filled = i < digits.length;
          const isCursor = i === digits.length;
          return (
            <View key={i} style={[s.cell, filled ? s.cellFilled : isCursor ? s.cellCursor : null]}>
              <Text style={s.cellText}>{digits[i] || ""}</Text>
            </View>
          );
        })}
        <TextInput
          ref={ref}
          value={value}
          onChangeText={(t) => onChange(t.replace(/\D/g, "").slice(0, OTP_LEN))}
          keyboardType="number-pad"
          maxLength={OTP_LEN}
          autoComplete="one-time-code"
          textContentType="oneTimeCode"
          style={s.hiddenInput}
        />
      </Pressable>
    </View>
  );
}

/**
 * Sunrise countdown rule — the resend cooldown drains as a dawn-gradient
 * track (mirrors the header's step progress and the password-strength bar),
 * so the whole registration flow shares one motion language.
 */
function CooldownTrack({ timeLeft, total }: { timeLeft: number; total: number }) {
  const fill = useRef(new Animated.Value(timeLeft / total)).current;
  useEffect(() => {
    Animated.timing(fill, {
      toValue: Math.max(0, timeLeft / total),
      duration: 1000,
      easing: Easing.linear,
      useNativeDriver: false,
    }).start();
  }, [fill, timeLeft, total]);
  return (
    <View style={s.track}>
      <Animated.View style={{ height: "100%", width: fill.interpolate({ inputRange: [0, 1], outputRange: ["0%", "100%"] }) }}>
        <LinearGradient
          colors={dawnGradient as any}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={{ flex: 1, borderRadius: 3 }}
        />
      </Animated.View>
    </View>
  );
}

export type OtpVerifyResult = { verified: boolean; [key: string]: any };

export function OtpVerifyScreen({
  channel,
  destination,
  registrationId,
  cooldownSeconds,
  step,
  totalSteps,
  restartRoute,
  onVerified,
}: {
  channel: "phone" | "email";
  destination: string;
  registrationId: string;
  /** Seconds before the "Resend" option appears — 60 for SMS, 120 for email. */
  cooldownSeconds: number;
  step: number;
  totalSteps: number;
  restartRoute: "/(auth)/join-invite" | "/(auth)/entity-type";
  onVerified: (data: OtpVerifyResult) => void;
}) {
  const router = useRouter();
  const invalid = !registrationId;

  const [otp, setOtp] = useState("");
  const [timeLeft, setTimeLeft] = useState(cooldownSeconds);
  const [resendCount, setResendCount] = useState(0);
  const [resending, setResending] = useState(false);
  const [resendResult, setResendResult] = useState<string | undefined>();
  const [serverExpired, setServerExpired] = useState(false);
  const [registrationMissing, setRegistrationMissing] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (timeLeft <= 0) return;
    const t = setTimeout(() => setTimeLeft((x) => x - 1), 1000);
    return () => clearTimeout(t);
  }, [timeLeft]);

  const formatTime = (sec: number) => `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, "0")}`;
  const cooldownDone = timeLeft <= 0;
  const canResend = cooldownDone && resendCount < MAX_RESEND && !resending;
  const complete = otp.length === OTP_LEN;

  const verify = async () => {
    if (!complete || invalid || isLocked || serverExpired || loading) return;
    setLoading(true); setErr("");
    try {
      const body: any = { registration_id: registrationId };
      body[`${channel}_otp`] = otp;
      // The backend either accepts this channel's code and returns 200 (this
      // channel is now verified — `verified` reflects whether EVERY required
      // channel is done, not just this one), or rejects it and throws, caught
      // below. There is no partial-success shape to check here.
      const { data } = await api.post("/auth/register/verify", body);
      onVerified(data);
    } catch (e: any) {
      const detail = String(e?.response?.data?.detail || "");
      const normalized = detail.toLowerCase();
      if (e?.response?.status === 404 || normalized.includes("registration not found")) {
        setRegistrationMissing(true);
        setErr("This verification session is no longer active. Restart registration to get a new code.");
      } else if (normalized.includes("expired") || normalized.includes("restart registration")) {
        setServerExpired(true);
        setErr("This verification code has expired. Restart registration to request a new code.");
      } else if (e?.response?.status === 429 || normalized.includes("too many incorrect")) {
        setIsLocked(true);
        setErr("Too many incorrect attempts. Restart registration to continue.");
      } else {
        setErr(detail || "Could not verify the code. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const resend = async () => {
    if (!canResend) return;
    setErr("");
    setResending(true);
    setResendResult(undefined);
    try {
      const { data } = await api.post("/auth/register/resend", {
        registration_id: registrationId,
        channel,
      });
      const nextCount = Number(data?.resend_count || resendCount + 1);
      setResendCount(nextCount);
      setResendResult(`New ${channel} code sent successfully.`);
      setTimeLeft(cooldownSeconds);
      setOtp("");
    } catch (e: any) {
      const detail = String(e?.response?.data?.detail || "Could not resend the code.");
      const normalized = detail.toLowerCase();
      if (normalized.includes("resend limit") || normalized.includes("restart registration")) {
        setResendCount(MAX_RESEND);
      }
      setResendResult(detail);
    } finally {
      setResending(false);
    }
  };

  const blocked = invalid || isLocked || serverExpired || registrationMissing;
  const blockedTitle = invalid
    ? "Verification unavailable"
    : registrationMissing
      ? "Verification session ended"
      : serverExpired
        ? "Verification expired"
        : "Account temporarily locked";
  const blockedCopy = invalid
    ? "No valid verification session was provided. Restart registration so we can securely verify your contact details."
    : registrationMissing
      ? "This OTP session is no longer active. Restart registration to request a new code."
      : serverExpired
        ? "This code is no longer valid. Restart registration to request a fresh one."
        : "Too many incorrect attempts were made. Restart registration to continue securely.";

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow={`Step ${step} of ${totalSteps}`}
          title={blocked ? blockedTitle : channel === "phone" ? "Verify your phone number" : "Verify your email"}
          subtitle={blocked ? undefined : `Enter the code we sent to ${destination} — this keeps your account secure.`}
          onBack={() => router.back()}
          step={step}
          totalSteps={totalSteps}
        />

        <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {blocked ? (
            <Rise delay={120}>
              <View style={s.lockCard}>
                <View style={s.lockIcon}><ShieldOff size={28} color={colors.destructive} /></View>
                <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.destructive, marginBottom: 6 }}>{blockedTitle}</Text>
                <Small style={{ textAlign: "center", lineHeight: 20 }}>{blockedCopy}</Small>
                <Pressable
                  onPress={() => router.replace(restartRoute)}
                  style={({ pressed }) => [s.restartBtn, pressed && { opacity: 0.8 }]}
                >
                  <Small color={colors.primaryFg} weight="700">Restart registration</Small>
                </Pressable>
              </View>
            </Rise>
          ) : (
            <>
              <Rise delay={200}>
                <View style={s.otpPanel}>
                  <OtpCells channel={channel} destination={destination} value={otp} onChange={setOtp} />
                </View>
              </Rise>

              <Rise delay={400}>
                <View style={s.metaCard}>
                  {cooldownDone ? (
                    <View style={s.resendItem}>
                      <View style={{ flex: 1 }}>
                        <Small weight="700">Didn't get the code?</Small>
                        <Small color={colors.mutedFg}>{resendCount}/{MAX_RESEND} resends used</Small>
                        {!!resendResult && (
                          <Small
                            color={resendResult.toLowerCase().includes("success") ? colors.success : colors.destructive}
                            style={{ marginTop: 3 }}
                          >
                            {resendResult}
                          </Small>
                        )}
                      </View>
                      <Pressable
                        onPress={resend}
                        disabled={!canResend}
                        hitSlop={8}
                        style={[s.resendBtn, canResend && s.resendBtnActive]}
                      >
                        <Small style={{
                          fontFamily: fonts.bold,
                          fontSize: 13,
                          color: canResend ? colors.primary : colors.mutedFg + "80",
                        }}>
                          {resending
                            ? "Sending…"
                            : resendCount >= MAX_RESEND
                              ? "Limit reached"
                              : "Resend"}
                        </Small>
                      </Pressable>
                    </View>
                  ) : (
                    <>
                      <View style={s.metaHead}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                          <Clock size={15} color={colors.mutedFg} />
                          <Eyebrow color={colors.mutedFg}>Resend available in</Eyebrow>
                        </View>
                        <Text style={s.countdown}>{formatTime(timeLeft)}</Text>
                      </View>
                      <CooldownTrack timeLeft={timeLeft} total={cooldownSeconds} />
                    </>
                  )}
                </View>
              </Rise>

              {err ? <Small color={colors.destructive} style={{ marginTop: 14, textAlign: "center" }}>{err}</Small> : null}
            </>
          )}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={verify} disabled={!complete || blocked} style={[s.cta, !complete || blocked ? { backgroundColor: colors.surface } : { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: !complete || blocked ? colors.mutedFg : colors.primaryFg }}>{loading ? "Verifying…" : "Verify OTP"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  content: { flexGrow: 1, paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.lg },
  otpPanel: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md, shadowColor: colors.foreground, shadowOpacity: 0.06, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 2 },
  block: { width: "100%" },
  blockHead: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 12 },
  channelChip: { width: 34, height: 34, borderRadius: 999, backgroundColor: colors.secondary + "88", alignItems: "center", justifyContent: "center" },
  channelText: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.8, textTransform: "uppercase", color: colors.mutedFg },
  dest: { marginTop: 1, fontFamily: fonts.mono, fontSize: 13, color: colors.foreground, fontVariant: ["tabular-nums"] },
  doneChip: { width: 24, height: 24, borderRadius: 999, backgroundColor: colors.success + "1F", alignItems: "center", justifyContent: "center" },
  progressCount: { fontFamily: fonts.mono, fontSize: 12, color: colors.mutedFg + "B0" },
  cells: { flexDirection: "row", gap: 7 },
  cell: { flex: 1, height: 52, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, alignItems: "center", justifyContent: "center" },
  cellFilled: { borderColor: colors.primary, backgroundColor: colors.secondary + "55" },
  cellCursor: { borderColor: colors.accent, borderWidth: 1.5, backgroundColor: colors.card },
  cellText: { fontFamily: fonts.mono, fontSize: 20, color: colors.foreground },
  hiddenInput: { position: "absolute", width: 1, height: 1, opacity: 0 },
  metaCard: { marginTop: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  metaHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  countdown: { fontFamily: fonts.mono, fontSize: 15, color: colors.foreground, fontVariant: ["tabular-nums"] },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.surface, overflow: "hidden" },
  resendItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  resendBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: radii.pill, alignItems: "center" },
  resendBtnActive: { backgroundColor: colors.secondary },
  lockCard: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "0D", padding: spacing.lg, alignItems: "center" },
  lockIcon: { width: 56, height: 56, borderRadius: 999, backgroundColor: colors.destructive + "1A", alignItems: "center", justifyContent: "center", marginBottom: 16 },
  restartBtn: { marginTop: 18, minHeight: 42, paddingHorizontal: 20, borderRadius: radii.pill, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
