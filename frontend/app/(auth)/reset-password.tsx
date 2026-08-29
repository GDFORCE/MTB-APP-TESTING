// Branded single-use password setup/reset screen.
//
// Opened from the emailed deep link `mytrialboard://reset-password?token=...`
// (admin-issued setup or reset). The raw token is submitted once to
// POST /auth/password-reset-link together with the new password; the backend
// consumes it atomically, so replayed/expired/revoked links fail with a clear
// message and a route back to recovery options.
import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, X, Eye, EyeOff, ShieldCheck, Link2 } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";

const RULES: { label: string; test: (p: string) => boolean }[] = [
  { label: "8+ characters", test: (p) => p.length >= 8 },
  { label: "Uppercase letter", test: (p) => /[A-Z]/.test(p) },
  { label: "Lowercase letter", test: (p) => /[a-z]/.test(p) },
  { label: "Number", test: (p) => /[0-9]/.test(p) },
  { label: "Special character", test: (p) => /[^A-Za-z0-9]/.test(p) },
];

export default function ResetPasswordLink() {
  const router = useRouter();
  const { token } = useLocalSearchParams<{ token?: string }>();
  const rawToken = typeof token === "string" ? token.trim() : "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const metRules = RULES.filter((r) => r.test(password)).length;
  const strengthPct = (metRules / RULES.length) * 100;
  const strength = strengthPct >= 80 ? "Strong" : strengthPct >= 60 ? "Medium" : "Weak";
  const strengthColor = strength === "Strong" ? colors.success : strength === "Medium" ? colors.warning : colors.destructive;
  const passwordsMatch = password.length > 0 && password === confirm;
  const showMismatch = confirm.length > 0 && password !== confirm;
  const canContinue = !!rawToken && metRules === RULES.length && passwordsMatch && !loading;

  const fill = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(fill, { toValue: strengthPct, duration: 350, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
  }, [fill, strengthPct]);

  const submit = async () => {
    if (!canContinue) return;
    setLoading(true); setErr("");
    try {
      await api.post("/auth/password-reset-link", { token: rawToken, new_password: password });
      setDone(true);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "This password link could not be used. Please request a new one.");
    } finally {
      setLoading(false);
    }
  };

  // Missing/malformed token: honest dead-link state instead of a form that can't succeed.
  if (!rawToken) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
        <AuthHeader eyebrow="Account access" title="Link not valid" subtitle="This password link is missing its security token." onBack={() => router.replace("/(auth)/sign-in")} />
        <View style={s.statusWrap}>
          <View style={[s.statusBadge, { backgroundColor: colors.destructive + "14" }]}>
            <Link2 size={28} color={colors.destructive} />
          </View>
          <Text style={s.statusTitle}>This link can&apos;t be opened</Text>
          <Small color={colors.mutedFg} style={{ textAlign: "center" }}>
            Open the most recent password email on this device, or ask your administrator to send a new link.
          </Small>
          <Springy onPress={() => router.replace("/(auth)/sign-in")} style={[s.cta, { backgroundColor: colors.primary, alignSelf: "stretch", marginTop: spacing.lg }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>Back to Sign In</Text>
          </Springy>
        </View>
      </SafeAreaView>
    );
  }

  if (done) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
        <AuthHeader eyebrow="Account access" title="Password updated" subtitle="Your new password is active." />
        <View style={s.statusWrap}>
          <Rise delay={80}>
            <View style={[s.statusBadge, { backgroundColor: colors.success + "1A" }]}>
              <ShieldCheck size={30} color={colors.success} />
            </View>
          </Rise>
          <Rise delay={160}>
            <Text style={s.statusTitle}>You&apos;re all set</Text>
          </Rise>
          <Rise delay={220}>
            <Small color={colors.mutedFg} style={{ textAlign: "center" }}>
              For security, every other session was signed out. Sign in with your new password to continue.
            </Small>
          </Rise>
          <Rise delay={280} style={{ alignSelf: "stretch" }}>
            <Springy onPress={() => router.replace("/(auth)/sign-in")} style={[s.cta, { backgroundColor: colors.primary, marginTop: spacing.lg }]}>
              <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>Go to Sign In</Text>
            </Springy>
          </Rise>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Account access"
          title="Set your new password"
          subtitle="This secure link works once and expires shortly after it was sent."
          onBack={() => router.replace("/(auth)/sign-in")}
        />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <Rise delay={200}>
            <Text style={s.label}>New Password <Text style={{ color: colors.accent }}>*</Text></Text>
            <View style={s.inputRow}>
              <TextInput value={password} onChangeText={setPassword} secureTextEntry={!showPw} placeholder="Enter a strong password" placeholderTextColor={colors.mutedFg + "99"} autoCapitalize="none" style={s.input} />
              <Pressable onPress={() => setShowPw((v) => !v)} hitSlop={8} style={s.eye}>{showPw ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}</Pressable>
            </View>
          </Rise>

          <Rise delay={260}>
            <Text style={[s.label, { marginTop: spacing.md }]}>Confirm Password <Text style={{ color: colors.accent }}>*</Text></Text>
            <View style={[s.inputRow, showMismatch && s.inputRowError]}>
              <TextInput
                value={confirm}
                onChangeText={setConfirm}
                secureTextEntry={!showConfirm}
                placeholder="Re-enter your password"
                placeholderTextColor={colors.mutedFg + "99"}
                autoCapitalize="none"
                style={s.input}
                accessibilityHint={showMismatch ? "Passwords do not match" : undefined}
              />
              <Pressable onPress={() => setShowConfirm((v) => !v)} hitSlop={8} style={s.eye}>{showConfirm ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}</Pressable>
            </View>
            {showMismatch ? (
              <View style={s.validationRow} accessibilityLiveRegion="polite">
                <X size={16} color={colors.destructive} strokeWidth={2.5} />
                <Small color={colors.destructive}>Passwords do not match</Small>
              </View>
            ) : passwordsMatch ? (
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 }}>
                <Check size={16} color={colors.success} strokeWidth={3} />
                <Small color={colors.success}>Passwords match</Small>
              </View>
            ) : null}
          </Rise>

          <Rise delay={320}>
            <View style={s.strengthCard}>
              <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <Eyebrow color={colors.mutedFg}>Password strength</Eyebrow>
                <Small style={{ fontFamily: fonts.bold, color: strengthColor }}>{strength}</Small>
              </View>
              <View style={s.strengthTrack}>
                <Animated.View style={{ height: "100%", borderRadius: 3, width: fill.interpolate({ inputRange: [0, 100], outputRange: ["0%", "100%"] }), backgroundColor: strengthColor }} />
              </View>
              <View style={s.rulesGrid}>
                {RULES.map((rule) => {
                  const ok = rule.test(password);
                  return (
                    <View key={rule.label} style={s.ruleItem}>
                      <View style={[s.ruleDot, ok ? { backgroundColor: colors.success + "26" } : { backgroundColor: colors.surface }]}>
                        {ok ? <Check size={11} color={colors.success} strokeWidth={3} /> : <X size={11} color={colors.mutedFg + "80"} />}
                      </View>
                      <Small style={{ color: ok ? colors.foreground : colors.mutedFg, fontSize: 13 }}>{rule.label}</Small>
                    </View>
                  );
                })}
              </View>
            </View>
          </Rise>

          {err ? (
            <Rise delay={0}>
              <View style={s.errorCard} accessibilityLiveRegion="polite">
                <Small color={colors.destructive}>{err}</Small>
                <Small color={colors.mutedFg} style={{ marginTop: 4 }}>
                  Links can only be used once. If this one expired or was replaced, ask your administrator to send a new one, or use Forgot Password.
                </Small>
                <Pressable onPress={() => router.replace("/(auth)/forgot-password")} hitSlop={6} style={{ marginTop: 8 }}>
                  <Small style={{ fontFamily: fonts.bold, color: colors.primary }}>Recover with Forgot Password</Small>
                </Pressable>
              </View>
            </Rise>
          ) : null}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={submit} disabled={!canContinue} style={[s.cta, canContinue ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: canContinue ? colors.primaryFg : colors.mutedFg }}>
              {loading ? "Saving password…" : "Save New Password"}
            </Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  inputRow: { flexDirection: "row", alignItems: "center", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14 },
  inputRowError: { borderColor: colors.destructive, backgroundColor: colors.destructive + "08" },
  input: { flex: 1, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  eye: { paddingLeft: 8, paddingVertical: 8 },
  validationRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  strengthCard: { marginTop: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  strengthTrack: { height: 6, borderRadius: 3, backgroundColor: colors.surface, overflow: "hidden", marginBottom: 16 },
  rulesGrid: { flexDirection: "row", flexWrap: "wrap" },
  ruleItem: { width: "50%", flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 },
  ruleDot: { width: 18, height: 18, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  errorCard: { marginTop: 12, borderRadius: radii.md, borderWidth: 1, borderColor: colors.destructive + "33", backgroundColor: colors.destructive + "0A", padding: spacing.md },
  statusWrap: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.lg, gap: 10 },
  statusBadge: { width: 64, height: 64, borderRadius: 999, alignItems: "center", justifyContent: "center", marginBottom: 4 },
  statusTitle: { fontFamily: fonts.bold, fontSize: 20, color: colors.foreground },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
