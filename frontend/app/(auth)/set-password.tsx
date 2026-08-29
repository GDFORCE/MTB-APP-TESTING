import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Check, X, Eye, EyeOff } from "lucide-react-native";
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
  { label: "Special character", test: (p) => /[!@#$%^&*(),.?":{}|<>]/.test(p) },
];

export default function SetPassword() {
  const router = useRouter();
  const { registration_id, role, email, phone, channels } = useLocalSearchParams<{
    registration_id: string;
    role: string;
    invited?: string;
    email?: string;
    phone?: string;
    channels?: string;
  }>();
  const loginIdentifier = String(email || phone || "");
  const channelCount = (() => { try { return (JSON.parse(channels || "[]") as string[]).length || 1; } catch { return 1; } })();
  const totalSteps = channelCount + 4;
  const stepNumber = channelCount + 4;

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const metRules = RULES.filter((r) => r.test(password)).length;
  const strengthPct = (metRules / RULES.length) * 100;
  const strength = strengthPct >= 80 ? "Strong" : strengthPct >= 60 ? "Medium" : "Weak";
  const strengthColor = strength === "Strong" ? colors.success : strength === "Medium" ? colors.warning : colors.destructive;
  const passwordsMatch = password.length > 0 && password === confirm;
  const showMismatch = confirm.length > 0 && password !== confirm;
  const canContinue = metRules === RULES.length && passwordsMatch && !loading;

  const finishRegistration = (data: any) => {
    router.replace({
      pathname: "/(auth)/register-success",
      params: {
        role: role || "patient",
        session: JSON.stringify({
          access_token: data.access_token,
          refresh_token: data.refresh_token,
          user: data.user,
        }),
      },
    });
  };

  // Strength bar fills itself as rules are met (design's animate-fill-bar).
  const fill = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(fill, { toValue: strengthPct, duration: 350, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
  }, [fill, strengthPct]);

  const createAccount = async () => {
    if (!canContinue) return;
    setLoading(true); setErr("");
    try {
      const data = (await api.post("/auth/register/complete", { registration_id, password })).data;
      finishRegistration(data);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Could not create your account. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow={`Step ${stepNumber} of ${totalSteps}`} title="Set your password" subtitle="This is the last step — your account is created right after." onBack={() => router.back()} step={stepNumber} totalSteps={totalSteps} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {!!loginIdentifier && (
            <Rise delay={150}>
              <Text style={s.label}>{email ? "Email ID" : "Phone Number"}</Text>
              <TextInput
                value={loginIdentifier}
                editable={false}
                autoCapitalize="none"
                keyboardType={email ? "email-address" : "phone-pad"}
                textContentType="username"
                autoComplete={email ? "email" : "username"}
                importantForAutofill="yes"
                style={[s.identityInput, s.identityInputReadOnly]}
              />
            </Rise>
          )}
          <Rise delay={200}>
            <Text style={s.label}>Create Password <Text style={{ color: colors.accent }}>*</Text></Text>
            <View style={s.inputRow}>
              <TextInput
                value={password}
                onChangeText={setPassword}
                secureTextEntry={!showPw}
                placeholder="Enter a strong password"
                placeholderTextColor={colors.mutedFg + "99"}
                autoCapitalize="none"
                textContentType="newPassword"
                autoComplete="new-password"
                importantForAutofill="yes"
                style={s.input}
              />
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
                textContentType="newPassword"
                autoComplete="new-password"
                importantForAutofill="yes"
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

          {/* Strength + rules */}
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

          {err ? <Small color={colors.destructive} style={{ marginTop: 12 }}>{err}</Small> : null}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={createAccount} disabled={!canContinue} style={[s.cta, canContinue ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: canContinue ? colors.primaryFg : colors.mutedFg }}>
              {loading
                ? "Creating account…"
                : "Create Account"}
            </Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  label: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground, marginBottom: 6 },
  identityInput: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular, marginBottom: spacing.md },
  identityInputReadOnly: { backgroundColor: colors.surface, color: colors.mutedFg },
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
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
