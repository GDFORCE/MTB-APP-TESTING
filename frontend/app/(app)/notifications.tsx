import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { Bell, MessageCircle, FileText, ArrowRightLeft, KeyRound } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { NotificationDetailSheet, AppNotification } from "@/src/components/NotificationDetailSheet";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";

export default function Notifications() {
  const router = useRouter();
  const { user } = useAuth();
  const [items, setItems] = useState<any[]>([]);
  const [selected, setSelected] = useState<AppNotification | null>(null);
  useEffect(() => { (async () => { const r = await api.get("/notifications"); setItems(r.data); })(); }, []);

  const markRead = async (id: string) => {
    await api.post(`/notifications/${id}/read`);
    setItems(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
  };

  // "View Visit Details" routes by notification type (and role for visit screens).
  const viewDetails = (n: AppNotification) => {
    setSelected(null);
    if (n.kind === "trial_access_request") router.push("/(app)/org-admin/trial-access-requests");
    else if (n.kind === "ownership_transfer") router.push("/(app)/ownership-transfer");
    else if (n.kind === "message") router.push("/(app)/chat");
    else if (user?.role === "patient") router.push("/(app)/patient/my-visits");
    else router.push("/(app)/clinical/schedule-review");
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Stay updated" title="Notifications" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        {items.length === 0 ? <Card><Small>No notifications yet</Small></Card> : items.map(n => {
          const Icon = n.kind === "trial_access_request" ? KeyRound : n.kind === "ownership_transfer" ? ArrowRightLeft : n.kind === "message" ? MessageCircle : n.kind === "result" ? FileText : Bell;
          const tone = n.kind === "trial_access_request" ? colors.warning : n.kind === "ownership_transfer" ? colors.primary : n.kind === "message" ? colors.violet : n.kind === "result" ? colors.info : colors.accent;
          return (
            <Pressable key={n.id} testID={`notif-${n.id}`} onPress={() => setSelected(n)}>
              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={[s.icon, { backgroundColor: tone + "1A" }]}><Icon size={18} color={tone} /></View>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Body weight="700" style={{ flex: 1 }}>{n.title}</Body>
                      {!n.read && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent }} />}
                    </View>
                    <Small style={{ marginTop: 2 }}>{n.body}</Small>
                    <Small color={colors.mutedFg} style={{ marginTop: 4 }}>{new Date(n.created_at).toLocaleString()}</Small>
                  </View>
                </View>
              </Card>
            </Pressable>
          );
        })}
      </ScrollView>
      <NotificationDetailSheet
        notification={selected}
        onClose={() => setSelected(null)}
        onMarkRead={(id) => { markRead(id); setSelected(null); }}
        onViewDetails={viewDetails}
      />
    </ScreenContainer>
  );
}
const s = StyleSheet.create({ icon: { width: 40, height: 40, borderRadius: radii.lg, alignItems: "center", justifyContent: "center" } });
