import React, { useState } from "react";
import { View, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Eye, EyeOff, ArrowLeft } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Small, Button } from "@/src/components/ui";
import { MtbLogo } from "@/src/components/MtbLogo";
import { useAuth } from "@/src/auth/AuthContext";

export default function SignIn() {
  const router = useRouter();
  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true); setError("");
    try { await signIn(email.trim(), password); }
    catch (e: any) { setError(e?.response?.data?.detail || "Login failed"); }
    finally { setLoading(false); }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={s.container} keyboardShouldPersistTaps="handled">
          <Pressable onPress={() => router.back()} hitSlop={12} style={s.back}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
          <View style={{ alignItems: "center", marginTop: spacing.md }}>
            <MtbLogo size={64} />
            <Eyebrow color={colors.accent} style={{ marginTop: spacing.md }}>My Trial Board</Eyebrow>
            <H1 style={{ marginTop: 6 }}>Welcome back.</H1>
            <Small style={{ marginTop: 6 }}>Sign in to open your trial board</Small>
          </View>

          <View style={{ marginTop: spacing.xl }}>
            <Small color={colors.foreground} style={{ marginBottom: 6, fontWeight: "600" as any }}>Email or Phone</Small>
            <TextInput
              testID="signin-email"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="default"
              textContentType="username"
              autoComplete="email"
              importantForAutofill="yes"
              placeholder="Email address or phone number"
              style={s.input}
            />

            <Small color={colors.foreground} style={{ marginBottom: 6, marginTop: spacing.md, fontWeight: "600" as any }}>Password</Small>
            <View style={{ position: "relative" }}>
              <TextInput
                testID="signin-password"
                value={password}
                onChangeText={setPassword}
                secureTextEntry={!showPw}
                textContentType="password"
                autoComplete="current-password"
                importantForAutofill="yes"
                style={[s.input, { paddingRight: 48 }]}
              />
              <Pressable testID="toggle-password" onPress={() => setShowPw(!showPw)} style={s.eye}>
                {showPw ? <EyeOff size={20} color={colors.mutedFg} /> : <Eye size={20} color={colors.mutedFg} />}
              </Pressable>
            </View>
            {error ? <Small color={colors.destructive} style={{ marginTop: 8 }}>{error}</Small> : null}

            <View style={s.loginOptions}>
              <Pressable onPress={() => router.push("/(auth)/forgot-password")} hitSlop={8}>
                <Small color={colors.accent} style={{ fontWeight: "700" as any }}>Forgot?</Small>
              </Pressable>
            </View>
          </View>

          <View style={{ marginTop: spacing.xl, gap: spacing.md }}>
            <Button testID="signin-submit-button" onPress={submit} loading={loading}>Sign In</Button>
            <Pressable
              testID="signin-contact-support"
              onPress={() => router.push("/(auth)/login-support")}
              hitSlop={8}
            >
              <Small color={colors.primary} style={{ textAlign: "center", fontWeight: "700" as any }}>
                Having trouble signing in? Contact Support
              </Small>
            </Pressable>
            <Pressable onPress={() => router.push("/(auth)/entity-type")}>
              <Small color={colors.mutedFg} style={{ textAlign: "center" }}>Don’t have an account? <Small color={colors.primary} style={{ fontWeight: "700" as any }}>Sign Up</Small></Small>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { padding: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.xxl, flexGrow: 1 },
  back: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground },
  eye: { position: "absolute", right: 12, top: 12 },
  loginOptions: { marginTop: spacing.md, flexDirection: "row", alignItems: "center", justifyContent: "flex-end" },
});
