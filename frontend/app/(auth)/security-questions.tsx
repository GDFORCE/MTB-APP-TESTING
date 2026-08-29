import React, { useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, Modal, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Eye, EyeOff, Check, ChevronDown } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";

// Every role uses the same ordered catalogue. Each registration chooses one
// question from each group, giving users variety across personal history,
// first products/accounts, and education/language while preventing duplicates.
const QUESTION_POOLS: string[][] = [
  [
    "What is the name of the place you are born?",
    "What is your mother's first name?",
    "What was your childhood nickname?",
  ],
  [
    "What was the brand of your first mobile phone?",
    "What was the brand of your first Laptop?",
    "What was the name of the first bank where you opened an account?",
  ],
  [
    "What was the name of your first school?",
    "What was your favorite subject in school?",
    "What was the first language you learned?",
  ],
];

function QuestionSelect({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} style={[s.input, { flexDirection: "row", alignItems: "center", justifyContent: "space-between" }]}>
        <Text style={{ flex: 1, fontSize: 14, color: value ? colors.foreground : colors.mutedFg + "99", fontFamily: fonts.regular }} numberOfLines={1}>
          {value || "Please select a question"}
        </Text>
        <ChevronDown size={18} color={colors.mutedFg} />
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={s.selectOverlay} onPress={() => setOpen(false)}>
          <View style={s.selectSheet}>
            {options.map((o) => {
              const on = o === value;
              return (
                <Pressable key={o} onPress={() => { onChange(o); setOpen(false); }} style={[s.selectItem, on && { backgroundColor: colors.secondary + "55" }]}>
                  <Text style={{ flex: 1, fontSize: 14, color: on ? colors.primary : colors.foreground, fontFamily: on ? fonts.semibold : fonts.regular }}>{o}</Text>
                  {on && <Check size={16} color={colors.primary} strokeWidth={3} />}
                </Pressable>
              );
            })}
          </View>
        </Pressable>
      </Modal>
    </>
  );
}

export default function SecurityQuestions() {
  const router = useRouter();
  // Phone/email are always verified before this screen (register.tsx starts
  // the registration and sends the OTP; verify-phone.tsx / verify-email.tsx
  // only forward here on success), so registration_id always identifies an
  // already-verified pending registration.
  const { role, registration_id, invited, email, phone, channels } = useLocalSearchParams<{
    role: string;
    registration_id?: string;
    invited?: string;
    email?: string;
    phone?: string;
    channels?: string;
  }>();
  const channelCount = (() => { try { return (JSON.parse(channels || "[]") as string[]).length || 1; } catch { return 1; } })();
  const totalSteps = channelCount + 4;
  const stepNumber = channelCount + 3;

  const [questions, setQuestions] = useState<string[]>(["", "", ""]);
  const [answers, setAnswers] = useState<string[]>(["", "", ""]);
  const [revealed, setRevealed] = useState<boolean[]>([false, false, false]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const startingRef = useRef(false);

  const setQ = (i: number, v: string) => setQuestions((p) => p.map((x, idx) => (idx === i ? v : x)));
  const setA = (i: number, v: string) => setAnswers((p) => p.map((x, idx) => (idx === i ? v : x)));
  const toggle = (i: number) => setRevealed((p) => p.map((x, idx) => (idx === i ? !x : x)));

  const allComplete = questions.every((q) => q) && answers.every((a) => a.trim().length > 0) && !loading;

  const submit = async () => {
    if (!allComplete || startingRef.current) return;
    startingRef.current = true;
    setLoading(true); setErr("");
    try {
      const security_questions = questions.map((q, i) => ({ question: q, answer: answers[i] }));
      await api.post("/auth/register/security-questions", {
        registration_id,
        security_questions,
      });
      router.push({
        pathname: "/(auth)/set-password",
        params: {
          registration_id,
          role: role || "patient",
          invited: invited || "",
          email: email || "",
          phone: phone || "",
          channels: channels || "",
        },
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Could not save your security questions. Please try again.");
    } finally {
      startingRef.current = false;
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader eyebrow={`Step ${stepNumber} of ${totalSteps}`} title="Only you would know" subtitle="Three security questions help us verify it's you and recover your account if it's ever locked." onBack={() => router.back()} step={stepNumber} totalSteps={totalSteps} />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {questions.map((selected, i) => (
            <Rise key={i} delay={200 + i * 90}>
              <View style={s.card}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.accent, fontVariant: ["tabular-nums"] }}>{String(i + 1).padStart(2, "0")}</Text>
                  <Eyebrow color={colors.mutedFg}>Security question</Eyebrow>
                </View>
                <QuestionSelect value={selected} options={QUESTION_POOLS[i]} onChange={(v) => setQ(i, v)} />
                <View style={{ position: "relative", marginTop: 12 }}>
                  <TextInput
                    value={answers[i]}
                    onChangeText={(v) => setA(i, v)}
                    secureTextEntry={!revealed[i]}
                    placeholder="Your answer"
                    placeholderTextColor={colors.mutedFg + "8C"}
                    autoCapitalize="none"
                    style={[s.input, { paddingRight: 44 }]}
                  />
                  <Pressable onPress={() => toggle(i)} hitSlop={8} style={s.eye}>
                    {revealed[i] ? <EyeOff size={18} color={colors.mutedFg} /> : <Eye size={18} color={colors.mutedFg} />}
                  </Pressable>
                </View>
              </View>
            </Rise>
          ))}

          <Small style={{ textAlign: "center", marginTop: spacing.md, opacity: 0.8, lineHeight: 19 }}>
            Choose answers that don’t change over time and that only you would know.
          </Small>
          {err ? <Small color={colors.destructive} style={{ marginTop: 12, textAlign: "center" }}>{err}</Small> : null}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={submit} disabled={!allComplete} style={[s.cta, allComplete ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: allComplete ? colors.primaryFg : colors.mutedFg }}>{loading ? "Saving…" : "Continue"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  card: { borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md, marginBottom: spacing.md },
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 14, color: colors.foreground, fontFamily: fonts.regular },
  eye: { position: "absolute", right: 12, top: 0, bottom: 0, justifyContent: "center" },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  selectOverlay: { flex: 1, backgroundColor: colors.primaryDeep + "55", justifyContent: "center", paddingHorizontal: spacing.lg },
  selectSheet: { backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, overflow: "hidden", paddingVertical: 4 },
  selectItem: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 14 },
});
