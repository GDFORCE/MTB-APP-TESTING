import React, { useEffect, useRef } from "react";
import { View, Pressable, StyleSheet, Animated, Easing } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { ChevronLeft } from "lucide-react-native";
import { colors, spacing, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Small } from "@/src/components/ui";
import { Rise } from "@/src/components/Rise";

/**
 * Sunrise progress rule — completed steps carry the dawn gradient, the active step
 * is wider and glows like the cresting sun, upcoming steps are hairline bars.
 * The active step animates its width in on mount (mirrors the design's transition).
 */
function StepProgress({ step, total = 5 }: { step: number; total?: number }) {
  const grow = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(grow, { toValue: 1, duration: 500, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
  }, [grow, step]);
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }} accessibilityLabel={`Step ${step} of ${total}`}>
      {Array.from({ length: total }).map((_, i) => {
        const done = i < step - 1;
        const active = i === step - 1;
        if (active) {
          return (
            <Animated.View key={i} style={{ width: grow.interpolate({ inputRange: [0, 1], outputRange: [16, 32] }) }}>
              <LinearGradient
                colors={dawnGradient as any}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={[s.seg, s.segGlow]}
              />
            </Animated.View>
          );
        }
        if (done) {
          return (
            <LinearGradient key={i} colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={[s.seg, { width: 16, opacity: 0.7 }]} />
          );
        }
        return <View key={i} style={[s.seg, { width: 16, backgroundColor: colors.border }]} />;
      })}
    </View>
  );
}

export function AuthHeader({
  eyebrow,
  title,
  subtitle,
  onBack,
  step,
  totalSteps = 5,
}: {
  eyebrow?: string;
  title?: string;
  subtitle?: string;
  onBack?: () => void;
  /** 1-based current step for the sunrise progress rule; omit to hide it. */
  step?: number;
  totalSteps?: number;
}) {
  return (
    <View style={s.wrap}>
      {/* Ambient morning-light wash behind the header block */}
      <LinearGradient
        colors={[colors.dawnFrom + "22", "transparent"]}
        start={{ x: 0.1, y: 0 }}
        end={{ x: 0.7, y: 1 }}
        style={StyleSheet.absoluteFill}
        pointerEvents="none"
      />
      <View style={s.topRow}>
        {onBack ? (
          <Pressable onPress={onBack} hitSlop={10} style={s.backBtn} accessibilityLabel="Back">
            <ChevronLeft size={20} color={colors.foreground} />
          </Pressable>
        ) : (
          <View style={{ width: 40, height: 40 }} />
        )}
        {step !== undefined && <StepProgress step={step} total={totalSteps} />}
      </View>

      {eyebrow ? (
        <Rise delay={40}>
          <Eyebrow color={colors.accent}>{eyebrow}</Eyebrow>
        </Rise>
      ) : null}
      {title ? (
        <Rise delay={100}>
          <H1 style={{ marginTop: 6 }}>{title}</H1>
        </Rise>
      ) : null}
      {subtitle ? (
        <Rise delay={160}>
          <Small style={{ marginTop: 6, lineHeight: 20 }}>{subtitle}</Small>
        </Rise>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  wrap: { paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.sm, position: "relative" },
  topRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.lg },
  backBtn: { width: 40, height: 40, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  seg: { height: 6, borderRadius: 3 },
  segGlow: { width: "100%", shadowColor: colors.dawnMid, shadowOpacity: 0.7, shadowRadius: 6, shadowOffset: { width: 0, height: 0 }, elevation: 3 },
});
