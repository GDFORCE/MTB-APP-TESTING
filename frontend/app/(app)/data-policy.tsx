import React from "react";
import { View, ScrollView, Pressable, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { X, ShieldCheck } from "lucide-react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";

const SECTIONS = [
  {
    title: "Encryption",
    body: "Every message, file, and voice note sent in My Trial Board is encrypted in transit (TLS) and at rest. Only members of a conversation can decrypt and read its contents.",
  },
  {
    title: "Retention",
    body: "Messages are retained per your organization's trial policy. Channel admins can set a shorter auto-delete timer for a specific channel from that channel's controls; deleted messages are not recoverable.",
  },
  {
    title: "Audit trail",
    body: "Adding or removing members, renaming a channel, and reporting a conversation are recorded in the platform audit log for compliance review.",
  },
  {
    title: "Reporting a conversation",
    body: "Reporting a channel files a support ticket that platform administrators triage — the conversation stays active while it's reviewed.",
  },
];

export default function DataPolicy() {
  const router = useRouter();
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={s.header}>
        <View style={{ flex: 1 }}>
          <Eyebrow color={colors.accent}>Compliance</Eyebrow>
          <H1>Data protection policy</H1>
        </View>
        <Pressable testID="data-policy-close" onPress={() => router.back()} hitSlop={12}>
          <X size={22} color={colors.foreground} />
        </Pressable>
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.sm }}>
        <Card style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <ShieldCheck size={20} color={colors.success} />
          <Small style={{ flex: 1 }}>Every channel on My Trial Board is covered by this policy automatically — there is nothing to configure.</Small>
        </Card>
        {SECTIONS.map((section) => (
          <Card key={section.title} style={{ marginTop: spacing.sm }}>
            <Body weight="700" style={{ marginBottom: 6 }}>{section.title}</Body>
            <Small>{section.body}</Small>
          </Card>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
});
