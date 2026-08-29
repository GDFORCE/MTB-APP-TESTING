import React, { useState } from "react";
import { View, ScrollView, TextInput, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { CheckCircle2, Link2 } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { sanitizeName } from "@/src/lib/validators";

export default function InvitePatient() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [done, setDone] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const send = async () => {
    if (!email && !phone) { setErr("Enter an email or phone number to send the invitation."); return; }
    setErr(null);
    setLoading(true);
    try {
      const r = await api.post("/invitations", {
        email: email || undefined,
        phone: phone || undefined,
        full_name: name || undefined,
        role: "patient",
      });
      setLink(r.data?.invite_link || null);
      setDone(true);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Couldn't send the invitation. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Send invitation" title="Invite Patient" />
      {done ? (
        <ScrollView contentContainerStyle={{ alignItems: "center", padding: spacing.lg, paddingBottom: spacing.xxl }}>
          <View style={s.successBox}><CheckCircle2 size={36} color={colors.success} /></View>
          <Body weight="700" style={{ marginTop: spacing.md, fontSize: 18 }}>Invitation sent!</Body>
          <Small style={{ marginTop: 4, textAlign: "center" }}>They’ll receive a registration link by email/SMS.</Small>
          {link ? (
            <Card style={{ marginTop: spacing.lg, alignSelf: "stretch" }}>
              <Small color={colors.mutedFg}>Registration link</Small>
              <View style={s.linkBox}>
                <Link2 size={16} color={colors.primary} style={{ marginTop: 2 }} />
                <Small testID="invite-link" selectable color={colors.primary} weight="700" style={{ flex: 1 }}>{link}</Small>
              </View>
              <Small color={colors.mutedFg} style={{ marginTop: spacing.sm }}>Long-press the link to copy and share it with the patient.</Small>
            </Card>
          ) : null}
          <Button testID="invite-done" variant="secondary" style={{ marginTop: spacing.lg, alignSelf: "stretch" }} onPress={() => router.back()}>Done</Button>
        </ScrollView>
      ) : (
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
            <Card>
              <Eyebrow style={{ marginBottom: spacing.sm }}>How it works</Eyebrow>
              <Small>The patient receives a secure link to register and download the app. Their record is auto-linked to your trial.</Small>
            </Card>
            <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Patient details</Eyebrow>
            <F label="Full name" value={name} onChange={(v: string) => setName(sanitizeName(v))} testID="invite-name" />
            <F label="Email" value={email} onChange={(v: string) => { setEmail(v); if (err) setErr(null); }} testID="invite-email" keyboardType="email-address" />
            <F label="Phone (optional)" value={phone} onChange={(v: string) => { setPhone(v); if (err) setErr(null); }} testID="invite-phone" keyboardType="phone-pad" />
            {err ? <Small testID="invite-error" color={colors.destructive}>{err}</Small> : null}
          </ScrollView>
          <View style={{ padding: spacing.md }}><Button testID="invite-send" onPress={send} loading={loading}>Send invitation</Button></View>
        </KeyboardAvoidingView>
      )}
    </ScreenContainer>
  );
}

function F({ label, value, onChange, testID, keyboardType }: any) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Small color={colors.foreground} style={{ marginBottom: 6, fontWeight: "600" as any }}>{label}</Small>
      <TextInput testID={testID} value={value} onChangeText={onChange} keyboardType={keyboardType} autoCapitalize={keyboardType === "email-address" ? "none" : "sentences"} style={s.input} />
    </View>
  );
}

const s = StyleSheet.create({
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground },
  successBox: { width: 80, height: 80, borderRadius: 40, backgroundColor: colors.success + "1A", alignItems: "center", justifyContent: "center" },
  linkBox: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: spacing.sm, padding: spacing.sm, backgroundColor: colors.surface, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border },
});
