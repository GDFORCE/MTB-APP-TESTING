import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import type { LucideIcon } from "lucide-react-native";
import { RefreshCw } from "lucide-react-native";
import { colors, dawnGradient, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

interface Props {
  icon: LucideIcon;
  eyebrow: string;
  title: string;
  message: string;
  actionLabel: string;
  onAction: () => void;
  testID?: string;
}

/**
 * Dawn Rounds status screen — port of the demo's session-timeout / no-internet
 * layouts: an icon resting in a soft dawn glow over cream paper, eyebrow +
 * display voice, and one springy pill action pinned to the bottom.
 */
export function StatusScreen({ icon: Icon, eyebrow, title, message, actionLabel, onAction, testID }: Props) {
  return (
    <SafeAreaView style={s.root}>
      <View style={s.center}>
        {/* icon resting in a soft dawn glow */}
        <View style={s.glowWrap}>
          <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.glowOuter} />
          <View style={s.glowInner} />
          <View style={s.iconCircle}>
            <Icon size={32} color={colors.primary} />
          </View>
        </View>

        <Text style={s.eyebrow}>{eyebrow.toUpperCase()}</Text>
        <Text style={s.title}>
          {title}
          <Text style={{ color: colors.dawnMid }}>.</Text>
        </Text>
        <Text style={s.message}>{message}</Text>
      </View>

      <View style={s.footer}>
        <Pressable
          testID={testID}
          onPress={onAction}
          style={({ pressed }) => [s.button, { transform: [{ scale: pressed ? 0.97 : 1 }], backgroundColor: pressed ? colors.primaryDeep : colors.primary }]}
        >
          <RefreshCw size={16} color={colors.primaryFg} />
          <Text style={s.buttonText}>{actionLabel}</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background, paddingHorizontal: 28 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  glowWrap: { width: 72, height: 72, marginBottom: spacing.xl, alignItems: "center", justifyContent: "center" },
  glowOuter: { position: "absolute", top: -20, left: -20, right: -20, bottom: -20, borderRadius: radii.pill, opacity: 0.2 },
  glowInner: { position: "absolute", top: -20, left: -20, right: -20, bottom: -20, borderRadius: radii.pill, backgroundColor: colors.accent + "1A" },
  iconCircle: { width: 72, height: 72, borderRadius: 36, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center", ...shadows.md },
  eyebrow: { color: colors.accent, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, marginBottom: spacing.sm, textAlign: "center" },
  title: { color: colors.foreground, fontFamily: fonts.display, fontSize: 32, letterSpacing: -0.6, lineHeight: 38, marginBottom: 12, textAlign: "center" },
  message: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 15, lineHeight: 23, maxWidth: 250, textAlign: "center" },
  footer: { paddingBottom: spacing.xl + 4 },
  button: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 16, borderRadius: radii.pill, ...shadows.md },
  buttonText: { color: colors.primaryFg, fontFamily: fonts.semibold, fontSize: 15, letterSpacing: -0.2 },
});
