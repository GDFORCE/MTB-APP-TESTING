import React, { useEffect, useRef } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle, Defs, LinearGradient as SvgGrad, Stop, Line } from "react-native-svg";
import { colors, spacing, dawnGradient, fonts } from "@/src/theme/tokens";
import { Eyebrow, Small, Body, Button } from "@/src/components/ui";
import { MtbLogo } from "@/src/components/MtbLogo";

const AnimatedPath = Animated.createAnimatedComponent(Path);
const ARC_LEN = 380; // >= welcome arc path length; sweeps the stroke in on mount

/** Staggered rise-in wrapper (mirrors the demo's `.animate-rise`). Uses RN's
 *  built-in Animated so it needs no Reanimated/babel setup. */
function Rise({ delay = 0, style, children }: { delay?: number; style?: any; children: React.ReactNode }) {
  const t = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(t, { toValue: 1, duration: 520, delay, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  }, [delay, t]);
  return (
    <Animated.View style={[{ opacity: t, transform: [{ translateY: t.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) }] }, style]}>
      {children}
    </Animated.View>
  );
}

export default function Welcome() {
  const router = useRouter();

  // The sun (next visit) rises into place at the arc's crest.
  const sun = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;
  const arc = useRef(new Animated.Value(0)).current;   // journey arc sweeps in
  const line = useRef(new Animated.Value(0)).current;  // "sunrise" underline draws in
  useEffect(() => {
    Animated.timing(arc, { toValue: 1, duration: 1100, delay: 350, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    Animated.timing(line, { toValue: 1, duration: 700, delay: 520, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
    Animated.timing(sun, { toValue: 1, duration: 850, delay: 650, easing: Easing.out(Easing.back(1.4)), useNativeDriver: true }).start();
    Animated.loop(
      Animated.sequence([
        Animated.timing(drift, { toValue: 1, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift, { toValue: 0, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]),
    ).start();
  }, [arc, drift, line, sun]);
  const sunStyle = {
    opacity: sun,
    transform: [
      { scale: sun.interpolate({ inputRange: [0, 1], outputRange: [0.6, 1] }) },
      { translateY: sun.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) },
    ],
  };
  const petal = (o: number) => ({ transform: [{ translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, o] }) }] });
  const dashOffset = arc.interpolate({ inputRange: [0, 1], outputRange: [ARC_LEN, 0] });

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      {/* Ambient dawn wash behind the hero — mirrors the demo's .dawn-ambient glow */}
      <LinearGradient
        colors={[colors.dawnFrom + "2E", "transparent"]}
        start={{ x: 0.12, y: 0 }}
        end={{ x: 0.7, y: 0.5 }}
        style={s.ambient}
        pointerEvents="none"
      />
      <ScrollView contentContainerStyle={s.container} showsVerticalScrollIndicator={false}>
        {/* ── Masthead ── */}
        <Rise delay={60} style={s.masthead}>
          <MtbLogo size={40} />
          <View style={{ flex: 1 }}>
            <Eyebrow>My Trial Board</Eyebrow>
            <Small style={{ marginTop: 1 }}>Patient Visit Schedule</Small>
          </View>
          <View style={s.pill}><Eyebrow color={colors.mutedFg}>Est. 2026</Eyebrow></View>
        </Rise>

        <Rise delay={140}><View style={s.hairline} /></Rise>

        {/* ── Cover statement ── */}
        <View style={s.headlineWrap}>
          <Rise delay={220}>
            <Text style={s.headline}>Your trial,</Text>
            <View style={{ flexDirection: "row", alignItems: "flex-end", flexWrap: "wrap" }}>
              <Text style={s.headline}>one </Text>
              <View>
                <Text style={[s.headline, { color: colors.dawnMid }]}>sunrise</Text>
                <Animated.View style={{ transformOrigin: "left", transform: [{ scaleX: line }] }}>
                  <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={s.underline} />
                </Animated.View>
              </View>
            </View>
            <Text style={s.headline}>at a time<Text style={{ color: colors.dawnMid }}>.</Text></Text>
          </Rise>

          <Rise delay={320}>
            <Body color={colors.mutedFg} style={{ marginTop: spacing.md, maxWidth: 270, lineHeight: 22 }}>
              One warm place for sponsors, sites and patients to follow a clinical trial — visit by visit, morning by morning.
            </Body>
          </Rise>
        </View>

        {/* ── Sunrise motif: the journey drawn as an arc of visits ── */}
        <Rise delay={440} style={{ paddingHorizontal: spacing.lg, marginTop: spacing.xl }}>
          <View style={s.motif}>
            {/* drifting petals */}
            <Animated.View style={[s.petalA, petal(-10)]} />
            <Animated.View style={[s.petalB, petal(-7)]} />
            <Animated.View style={[s.petalC, petal(-5)]} />

            <Svg viewBox="0 0 340 130" width="100%" height={130}>
              <Defs>
                <SvgGrad id="arc" x1="0" y1="120" x2="340" y2="20">
                  <Stop offset="0" stopColor={colors.dawnFrom} />
                  <Stop offset="0.55" stopColor={colors.dawnMid} />
                  <Stop offset="1" stopColor={colors.dawnTo} />
                </SvgGrad>
              </Defs>
              <Line x1="0" y1="112" x2="340" y2="112" stroke={colors.border} strokeWidth="1" />
              <AnimatedPath d="M10 112 C 70 30, 270 30, 330 112" stroke="url(#arc)" strokeWidth="2.5" strokeLinecap="round" fill="none" strokeDasharray={ARC_LEN} strokeDashoffset={dashOffset as any} />
              {/* completed visits */}
              <Circle cx="58" cy="74" r="4.5" fill={colors.dawnFrom} />
              <Circle cx="118" cy="44" r="4.5" fill={colors.dawnMid} />
              {/* future visits */}
              <Circle cx="240" cy="49" r="4" fill="none" stroke={colors.dawnTo} strokeOpacity="0.5" strokeWidth="1.5" />
              <Circle cx="295" cy="84" r="4" fill="none" stroke={colors.dawnTo} strokeOpacity="0.35" strokeWidth="1.5" />
            </Svg>

            {/* the sun — the next visit — rising at the crest, with a soft halo */}
            <Animated.View style={[s.sunWrap, sunStyle]}>
              <View style={s.sunHalo} />
              <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.sun} />
            </Animated.View>
          </View>

          <Eyebrow color={colors.mutedFg} style={{ textAlign: "center", marginTop: spacing.sm, opacity: 0.75 }}>
            Screening · Baseline · Follow-up · Completion
          </Eyebrow>
        </Rise>

        {/* ── Actions: two intents (register org → admin, or join via invite → member) ── */}
        <View style={s.actions}>
          <Rise delay={560}>
            <Button testID="welcome-register-org-button" onPress={() => router.push("/(auth)/entity-type")}>
              Register your organization
            </Button>
          </Rise>
          <Rise delay={620}>
            <Button testID="welcome-join-invite-button" variant="secondary" onPress={() => router.push("/(auth)/join-invite")}>
              Join with an invite
            </Button>
          </Rise>
          <Rise delay={680}>
            <Pressable testID="welcome-signin-button" onPress={() => router.push("/(auth)/sign-in")} style={{ paddingVertical: 6 }}>
              <Small color={colors.mutedFg} style={{ textAlign: "center" }}>
                Already a member? <Small color={colors.primary} style={{ fontWeight: "700" as any, fontFamily: fonts.bold }}>Sign in</Small>
              </Small>
            </Pressable>
          </Rise>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { paddingBottom: spacing.xl, flexGrow: 1 },
  ambient: { position: "absolute", top: 0, left: 0, right: 0, height: 300 },
  masthead: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  pill: { borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4, backgroundColor: colors.card },
  hairline: { height: 1, backgroundColor: colors.border, marginHorizontal: spacing.lg, marginTop: spacing.md },
  headlineWrap: { marginTop: spacing.xl, paddingHorizontal: spacing.lg },
  headline: { fontFamily: fonts.display, fontSize: 40, lineHeight: 44, letterSpacing: -0.8, color: colors.foreground },
  underline: { height: 3, borderRadius: 2, marginTop: 2 },
  motif: { position: "relative", justifyContent: "flex-end" },
  sunWrap: { position: "absolute", top: 4, alignSelf: "center", alignItems: "center", justifyContent: "center" },
  sun: { width: 28, height: 28, borderRadius: 14 },
  sunHalo: { position: "absolute", width: 46, height: 46, borderRadius: 23, backgroundColor: colors.dawnFrom, opacity: 0.25 },
  petalA: { position: "absolute", top: -6, right: 44, width: 12, height: 12, borderRadius: 6, backgroundColor: colors.accent + "4D", zIndex: 2 },
  petalB: { position: "absolute", top: 6, left: 36, width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primary + "33", zIndex: 2 },
  petalC: { position: "absolute", top: 24, right: 96, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.dawnTo + "40", zIndex: 2 },
  actions: { paddingHorizontal: spacing.lg, marginTop: spacing.xl, gap: spacing.sm },
});
