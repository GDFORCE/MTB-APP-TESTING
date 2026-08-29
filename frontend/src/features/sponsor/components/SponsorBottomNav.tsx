import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Bell, FlaskConical, LayoutGrid, MessageCircle, UserRound } from "lucide-react-native";
import { colors, fonts, shadows } from "@/src/theme/tokens";

// "sites" is kept in the union (not in the tab bar) purely so sites.tsx's
// existing `active="sites"` prop still type-checks — Sites is no longer a
// persistent tab (replaced by Messages) but stays reachable from Dashboard /
// trial detail, so nothing gets highlighted while viewing it.
export type SponsorTab = "dashboard" | "trials" | "chat" | "notifs" | "me" | "sites" | "patients";

const tabs = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid, route: "/(app)/sponsor/dashboard" },
  { key: "trials", label: "Trials", icon: FlaskConical, route: "/(app)/sponsor/trials" },
  { key: "chat", label: "Messages", icon: MessageCircle, route: "/(app)/chat" },
  { key: "notifs", label: "Notifs", icon: Bell, route: "/(app)/sponsor/notifications" },
  { key: "me", label: "Me", icon: UserRound, route: "/(app)/sponsor/profile" },
] as const;

export function SponsorBottomNav({
  active,
  unread = 0,
  unreadMessages = 0,
}: {
  active: SponsorTab;
  unread?: number;
  unreadMessages?: number;
}) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 10);
  return (
    <View style={[styles.shell, { height: 58 + bottomInset, paddingBottom: bottomInset }]}>
      {tabs.map((tab) => {
        const selected = active === tab.key;
        const Icon = tab.icon;
        const badgeCount = tab.key === "notifs" ? unread : tab.key === "chat" ? unreadMessages : 0;
        return (
          <Pressable
            key={tab.key}
            accessibilityRole="button"
            accessibilityState={{ selected }}
            onPress={() => router.replace(tab.route as never)}
            style={({ pressed }) => [styles.item, pressed && { opacity: 0.65 }]}
          >
            <View style={[styles.iconWrap, selected && styles.iconWrapSelected]}>
              <Icon
                size={19}
                strokeWidth={selected ? 2.4 : 1.8}
                color={selected ? colors.primary : colors.mutedFg}
              />
              {badgeCount > 0 ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{badgeCount > 9 ? "9+" : badgeCount}</Text>
                </View>
              ) : null}
            </View>
            <Text style={[styles.label, selected && styles.labelSelected]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  shell: {
    paddingHorizontal: 6,
    paddingTop: 7,
    flexDirection: "row",
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    ...shadows.sm,
  },
  item: { flex: 1, alignItems: "center", justifyContent: "center", gap: 2 },
  iconWrap: {
    width: 34,
    height: 27,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  iconWrapSelected: { backgroundColor: colors.secondary },
  label: { fontFamily: fonts.medium, fontSize: 10, color: colors.mutedFg },
  labelSelected: { color: colors.primary, fontFamily: fonts.semibold },
  badge: {
    position: "absolute",
    right: -3,
    top: -4,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: colors.destructive,
    borderWidth: 2,
    borderColor: colors.card,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: { color: colors.white, fontFamily: fonts.bold, fontSize: 8 },
});
