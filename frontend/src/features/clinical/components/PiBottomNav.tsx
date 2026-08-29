import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { CalendarDays, FileText, Home, MessageCircle, User, Users } from "lucide-react-native";

type PiTab = "dashboard" | "trials" | "patients" | "messages" | "calendar" | "profile";
export type ClinicalNavRole = "pi" | "crc" | "smo" | "site";

const palette = {
  card: "#FEFAF1",
  border: "#E6D6C5",
  primary: "#A6213F",
  secondary: "#F0D7DC",
  muted: "#7B5F73",
};

export function PiBottomNav({
  active,
  calendarRole,
  role = "pi",
}: {
  active: PiTab;
  calendarRole: ClinicalNavRole;
  role?: ClinicalNavRole;
}) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 10);
  const dashboardRoute = role === "crc"
    ? "/(app)/crc/dashboard"
    : role === "site"
      ? "/(app)/site/dashboard"
      : "/(app)/pi/dashboard";
  const tabs = [
    { key: "dashboard" as const, label: "Dashboard", icon: Home, onPress: () => router.replace(dashboardRoute) },
    role === "crc"
      ? { key: "patients" as const, label: "Patients", icon: Users, onPress: () => router.replace("/(app)/clinical/patients") }
      : { key: "trials" as const, label: "My Trials", icon: FileText, onPress: () => router.replace("/(app)/clinical/my-trials") },
    { key: "messages" as const, label: "Messages", icon: MessageCircle, onPress: () => router.replace("/(app)/chat") },
    { key: "calendar" as const, label: "Calendar", icon: CalendarDays, onPress: () => router.replace({ pathname: "/(app)/clinical/team-calendar", params: { role: calendarRole } } as never) },
    { key: "profile" as const, label: "Me", icon: User, onPress: () => router.replace("/(app)/clinical/profile") },
  ];

  return (
    <View style={[styles.shell, { height: 58 + bottomInset, paddingBottom: bottomInset }]}>
      {tabs.map((tab) => {
        const selected = tab.key === active;
        const Icon = tab.icon;
        return (
          <Pressable
            key={tab.key}
            testID={`clinical-tab-${tab.key}`}
            accessibilityRole="button"
            accessibilityState={{ selected }}
            onPress={tab.onPress}
            style={({ pressed }) => [styles.item, pressed && { opacity: 0.65 }]}
          >
            <View style={[styles.iconWrap, selected && styles.iconWrapSelected]}>
              <Icon size={19} strokeWidth={selected ? 2.4 : 1.8} color={selected ? palette.primary : palette.muted} />
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
    backgroundColor: palette.card,
    borderTopWidth: 1,
    borderTopColor: palette.border,
    elevation: 8,
    shadowColor: "#2E1B33",
    shadowOpacity: 0.1,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: -3 },
  },
  item: { flex: 1, alignItems: "center", justifyContent: "center", gap: 2 },
  iconWrap: { width: 34, height: 27, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  iconWrapSelected: { backgroundColor: palette.secondary },
  label: { fontSize: 10, fontWeight: "500", color: palette.muted },
  labelSelected: { color: palette.primary, fontWeight: "700" },
});
