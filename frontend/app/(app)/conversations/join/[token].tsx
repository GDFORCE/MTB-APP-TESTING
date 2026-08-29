import React, { useEffect, useState } from "react";
import { View, ActivityIndicator, Pressable, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { AlertTriangle } from "lucide-react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { H1, Body, Small, Card } from "@/src/components/ui";
import { api } from "@/src/api/client";

export default function JoinConversation() {
  const router = useRouter();
  const { token } = useLocalSearchParams<{ token: string }>();
  const [error, setError] = useState("");
  const [joining, setJoining] = useState(true);

  const attempt = async () => {
    setJoining(true); setError("");
    try {
      const r = await api.post(`/conversations/join/${token}`);
      router.replace({ pathname: "/(app)/chat", params: { conversationId: r.data.id } });
    } catch (e: any) {
      setError(e?.response?.data?.detail || "This invite link is invalid or has expired.");
    } finally {
      setJoining(false);
    }
  };

  useEffect(() => { attempt(); }, [token]);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, alignItems: "center", justifyContent: "center", padding: spacing.md }}>
      {joining ? (
        <View style={{ alignItems: "center", gap: 10 }}>
          <ActivityIndicator color={colors.primary} />
          <Small color={colors.mutedFg}>Joining channel…</Small>
        </View>
      ) : error ? (
        <Card style={{ width: "100%", alignItems: "center" }}>
          <AlertTriangle size={22} color={colors.destructive} style={{ marginBottom: 8 }} />
          <H1 style={{ textAlign: "center", fontSize: 20 }}>Couldn&apos;t join channel</H1>
          <Body style={{ textAlign: "center", marginTop: 6 }}>{error}</Body>
          <Pressable testID="join-retry" onPress={attempt} style={s.retryBtn}>
            <Small weight="700" color={colors.primary}>Try again</Small>
          </Pressable>
          <Pressable testID="join-back-to-messages" onPress={() => router.replace("/(app)/chat")} style={{ marginTop: 12 }}>
            <Small color={colors.mutedFg}>Back to Messages</Small>
          </Pressable>
        </Card>
      ) : null}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  retryBtn: { marginTop: 14, paddingVertical: 10, paddingHorizontal: 18, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
});
