import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Calendar, Home, MessageCircle, User, FlaskConical, type LucideIcon } from "lucide-react-native";
import { colors, fonts } from "@/src/theme/tokens";
import { useUnreadCount } from "@/src/hooks/use-unread-count";

export type PatientTab = "home" | "visits" | "messages" | "calendar" | "me";

type Item = {
  id: PatientTab;
  label: string;
  icon: LucideIcon;
  href: "/(app)/patient/dashboard" | "/(app)/patient/my-trial" | "/(app)/chat" | "/(app)/patient/calendar" | "/(app)/patient/profile";
  testID: string;
};

const ITEMS: Item[] = [
  { id: "home", label: "Home", icon: Home, href: "/(app)/patient/dashboard", testID: "patient-tab-home" },
  { id: "visits", label: "My Trial", icon: FlaskConical, href: "/(app)/patient/my-trial", testID: "patient-tab-trial" },
  { id: "messages", label: "Messages", icon: MessageCircle, href: "/(app)/chat", testID: "patient-tab-messages" },
  { id: "calendar", label: "Calendar", icon: Calendar, href: "/(app)/patient/calendar", testID: "patient-tab-calendar" },
  { id: "me", label: "Me", icon: User, href: "/(app)/patient/profile", testID: "patient-tab-me" },
];

export const PATIENT_NAV_CONTENT_BOTTOM = 104;

export function PatientBottomNav({ active }: { active: PatientTab }) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const unread = useUnreadCount();

  return (
    <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 10) }]}>
      {ITEMS.map(item => {
        const selected = item.id === active;
        const Icon = item.icon;
        return (
          <Pressable
            key={item.id}
            testID={item.testID}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            onPress={() => {
              if (!selected) router.replace(item.href);
            }}
            style={styles.item}
          >
            {selected && <View style={styles.activeLine} />}
            <View style={styles.iconWrap}>
              <Icon size={20} strokeWidth={selected ? 2.5 : 2} color={selected ? colors.primary : colors.mutedFg} />
              {item.id === "messages" && unread != null && unread > 0 && (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{unread > 9 ? "9+" : unread}</Text>
                </View>
              )}
            </View>
            <Text style={[styles.label, selected && styles.labelActive]}>{item.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    minHeight: 64,
    flexDirection: "row",
    alignItems: "flex-start",
    backgroundColor: "rgba(254,250,241,0.98)",
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 7,
    paddingHorizontal: 4,
    shadowColor: colors.foreground,
    shadowOpacity: 0.08,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: -3 },
    elevation: 10,
  },
  item: {
    minHeight: 52,
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    position: "relative",
  },
  activeLine: {
    position: "absolute",
    top: -7,
    width: 32,
    height: 2,
    borderRadius: 2,
    backgroundColor: colors.primary,
  },
  iconWrap: { position: "relative" },
  label: {
    marginTop: 4,
    color: colors.mutedFg,
    fontFamily: fonts.medium,
    fontSize: 10,
  },
  labelActive: {
    color: colors.primary,
    fontFamily: fonts.semibold,
  },
  badge: {
    position: "absolute",
    top: -6,
    right: -10,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 3,
    borderRadius: 8,
    backgroundColor: colors.destructive,
    borderWidth: 2,
    borderColor: colors.card,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: {
    color: colors.destructiveFg,
    fontFamily: fonts.bold,
    fontSize: 8,
    lineHeight: 10,
  },
});
