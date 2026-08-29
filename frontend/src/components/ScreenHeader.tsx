import React from "react";
import { View, Pressable, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ChevronLeft } from "lucide-react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { Eyebrow, H1 } from "./ui";

interface Props { eyebrow: string; title: string; onBack?: () => void; right?: React.ReactNode; }

export function ScreenHeader({ eyebrow, title, onBack, right }: Props) {
  const router = useRouter();
  return (
    <View style={s.wrap}>
      <View style={s.row}>
        <Pressable testID="screen-back" onPress={onBack || (() => router.back())} hitSlop={12} style={s.backBtn}>
          <ChevronLeft size={22} color={colors.primaryFg} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Eyebrow color={colors.overlay25}>{eyebrow}</Eyebrow>
          <H1 color={colors.primaryFg} style={{ fontSize: 20 }}>{title}</H1>
        </View>
        {right}
      </View>
    </View>
  );
}

export function ScreenContainer({ children }: { children: React.ReactNode }) {
  return <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top"]}>{children}</SafeAreaView>;
}

const s = StyleSheet.create({
  wrap: { backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.md, paddingTop: 12, paddingBottom: 16 },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  backBtn: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
});
