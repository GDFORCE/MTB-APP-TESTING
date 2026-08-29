import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { AxiosError } from "axios";
import { Ticket, ArrowRight, AlertCircle, Clock, XCircle, CheckCircle2 } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";

// Resolved from GET /api/invitations/{token}: the admin who sent the invite already
// chose the org/site and the role — the invitee only accepts, never edits them.
interface ResolvedInvite {
  org: string;
  site: string;
  role: string;
  inviter: string;
  admin_name?: string;
  org_name?: string;
  full_name?: string;
  designation?: string;
  phone?: string;
  dob?: string;
  gender?: string;
  language?: string;
  email: string;
  status: string; // pending | expired | cancelled | accepted
  expires_at: string;
}

const ROLE_LABELS: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO Manager", site: "Site / Hospital",
  pi: "Principal Investigator", crc: "Research Coordinator (CRC)", patient: "Patient",
};
const roleLabel = (r?: string) => (r ? ROLE_LABELS[r] || r : "Organization member");
const normalizeInviteCode = (value: string) => {
  let raw = value.trim();
  if (raw.includes("/")) raw = raw.replace(/\/+$/, "").split("/").pop()?.split("?")[0] || raw;
  const compact = raw.replace(/[^a-z0-9]/gi, "");
  if (compact.slice(0, 3).toUpperCase() === "MTB") {
    const suffix = compact.slice(3, 11).toUpperCase();
    const groups = suffix.match(/.{1,4}/g) || [];
    return ["MTB", ...groups].join("-");
  }
  return /^[a-f0-9]{32}$/i.test(compact) ? compact.toLowerCase() : raw;
};

// UI copy for a resolved-but-not-acceptable invite, keyed by effective status.
const STATE_INFO: Record<string, { icon: React.ReactNode; title: string; note: string; tone: string }> = {
  expired: { icon: <Clock size={18} color={colors.accent} />, title: "This invitation has expired", note: "Ask your admin to resend the invitation, then enter the new code here.", tone: colors.accent },
  cancelled: { icon: <XCircle size={18} color={colors.destructive} />, title: "This invitation was cancelled", note: "This invite is no longer valid. Please contact your admin for a new one.", tone: colors.destructive },
  accepted: { icon: <CheckCircle2 size={18} color={colors.success} />, title: "Already accepted", note: "This invitation has already been accepted. You can sign in to your account.", tone: colors.success },
};

export default function JoinInvite() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [codeFocused, setCodeFocused] = useState(false);
  const [phase, setPhase] = useState<"idle" | "loading" | "resolved" | "error">("idle");
  const [invite, setInvite] = useState<ResolvedInvite | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const canVerify = code.trim().length >= 4;
  const isAccepted = phase === "resolved" && invite?.status === "accepted";

  const onCodeChange = (t: string) => {
    setCode(normalizeInviteCode(t));
    if (phase !== "idle") { setPhase("idle"); setInvite(null); setErrorMsg(""); }
  };

  const verify = async () => {
    const token = normalizeInviteCode(code);
    if (token.length < 4) return;
    setPhase("loading");
    setInvite(null);
    setErrorMsg("");
    try {
      const res = await api.get<ResolvedInvite>(`/invitations/${encodeURIComponent(token)}`);
      if (res.data.status === "pending") {
        setPhase("idle");
        router.push({
          pathname: "/(auth)/register",
          params: {
            inviteToken: token,
            role: res.data.role || "patient",
            org: res.data.org || "",
            email: res.data.email || "",
            fullName: res.data.full_name || "",
            designation: res.data.role.trim().toLowerCase() === "patient" ? "" : res.data.designation || "",
            phone: res.data.phone || "",
            dob: res.data.dob || "",
            gender: res.data.gender || "",
            language: res.data.language || "",
          },
        });
        return;
      }
      setInvite(res.data);
      setPhase("resolved");
    } catch (e) {
      const status = (e as AxiosError)?.response?.status;
      setErrorMsg(
        status === 404
          ? "We couldn't find an invitation for that code. Double-check it with the person who invited you."
          : "Something went wrong while checking this code. Please try again in a moment."
      );
      setPhase("error");
    }
  };

  const stateInfo = phase === "resolved" && invite && invite.status !== "pending" ? STATE_INFO[invite.status] : null;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Join your organization"
          title="Enter your invite"
          subtitle="Paste the code from the invitation your site admin sent you. No new site is created — you join theirs."
          onBack={() => router.back()}
        />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {/* Code entry */}
          <Rise delay={160}>
            <View style={s.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <View style={s.iconBadge}><Ticket size={14} color={colors.primary} /></View>
                <Eyebrow color={colors.mutedFg}>Invitation code</Eyebrow>
              </View>
              <TextInput
                value={code}
                onChangeText={onCodeChange}
                onFocus={() => setCodeFocused(true)}
                onBlur={() => setCodeFocused(false)}
                autoCapitalize="characters"
                autoCorrect={false}
                // Android puts the caret after the placeholder text, so a centered field
                // shows it far right. Dropping the placeholder on focus re-centers the caret.
                placeholder={codeFocused ? "" : "Enter invitation code"}
                accessibilityLabel="Invitation code"
                placeholderTextColor={colors.mutedFg + "66"}
                style={s.codeInput}
              />
              {phase !== "resolved" && (
                <Pressable onPress={verify} disabled={!canVerify || phase === "loading"} style={[s.next, canVerify ? { backgroundColor: colors.secondary } : { backgroundColor: colors.surface }]}>
                  {phase === "loading" ? (
                    <ActivityIndicator size="small" color={colors.primary} />
                  ) : (
                    <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: canVerify ? colors.primary : colors.mutedFg }}>Check invite</Text>
                  )}
                </Pressable>
              )}
            </View>
          </Rise>

          {/* Error state (not found / network / accept failure) */}
          {phase === "error" && (
            <Rise delay={40}>
              <View style={s.errorCard}>
                <AlertCircle size={18} color={colors.destructive} />
                <Small color={colors.destructive} style={{ flex: 1, lineHeight: 19 }}>{errorMsg}</Small>
              </View>
            </Rise>
          )}

          {/* Resolved but not acceptable (expired / cancelled / accepted) */}
          {stateInfo && (
            <Rise delay={40}>
              <View style={[s.stateCard, { borderColor: stateInfo.tone + "40", backgroundColor: stateInfo.tone + "12" }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  {stateInfo.icon}
                  <Body weight="700" style={{ fontSize: 15, color: stateInfo.tone }}>{stateInfo.title}</Body>
                </View>
                <Small style={{ lineHeight: 19 }}>{stateInfo.note}</Small>
                {invite && (invite.org || invite.role) ? (
                  <Small style={{ marginTop: 8 }}>Invite for {roleLabel(invite.role)}{invite.org ? ` · ${invite.org}` : ""}</Small>
                ) : null}
              </View>
            </Rise>
          )}

        </ScrollView>

        {isAccepted && (
          <View style={s.footer}>
            <Springy onPress={() => router.push("/(auth)/sign-in")} style={[s.cta, { backgroundColor: colors.primary }]}>
              <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>Go to sign in</Text>
              <ArrowRight size={16} color={colors.primaryFg} />
            </Springy>
          </View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  card: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md + 4 },
  iconBadge: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  codeInput: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, paddingHorizontal: 16, paddingVertical: 14, textAlign: "center", fontFamily: fonts.mono, fontSize: 16, letterSpacing: 2, color: colors.foreground },
  next: { marginTop: 12, paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  errorCard: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginTop: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "12", padding: spacing.md },
  stateCard: { marginTop: spacing.lg, borderRadius: radii.xl, borderWidth: 1, padding: spacing.md + 4 },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { flexDirection: "row", gap: 8, paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
