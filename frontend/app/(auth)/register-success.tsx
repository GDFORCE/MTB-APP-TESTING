import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Animated, Easing, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle, Defs, LinearGradient as SvgGrad, Stop, Line } from "react-native-svg";
import { Check } from "lucide-react-native";
import { colors, spacing, radii, fonts, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { useAuth } from "@/src/auth/AuthContext";

const AnimatedPath = Animated.createAnimatedComponent(Path);
const ARC_LEN = 300; // >= actual path length; sweeps the stroke in on mount

export default function RegisterSuccess() {
  const router = useRouter();
  const { applySession } = useAuth();
  const { session } = useLocalSearchParams<{ session?: string }>();
  const [opening, setOpening] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const createdSession = useMemo(() => {
    try {
      const parsed = JSON.parse(session || "");
      return parsed?.access_token && parsed?.refresh_token && parsed?.user ? parsed : null;
    } catch {
      return null;
    }
  }, [session]);

  const continueToApp = async () => {
    if (!createdSession) {
      router.replace("/(auth)/sign-in");
      return;
    }
    setOpening(true);
    setSessionError("");
    try {
      await applySession(createdSession);
    } catch {
      setSessionError("Your account was created, but automatic sign-in failed. Please sign in normally.");
      setOpening(false);
    }
  };

  // Sunrise choreography: the arc sweeps in, then the sun (the check) rises at its crest.
  const arc = useRef(new Animated.Value(0)).current;
  const sun = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(arc, { toValue: 1, duration: 1100, delay: 250, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    Animated.timing(sun, { toValue: 1, duration: 800, delay: 700, easing: Easing.out(Easing.back(1.4)), useNativeDriver: true }).start();
    Animated.loop(
      Animated.sequence([
        Animated.timing(drift, { toValue: 1, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift, { toValue: 0, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]),
    ).start();
  }, [arc, drift, sun]);

  const dashOffset = arc.interpolate({ inputRange: [0, 1], outputRange: [ARC_LEN, 0] });
  const sunStyle = {
    opacity: sun,
    transform: [
      { scale: sun.interpolate({ inputRange: [0, 1], outputRange: [0.6, 1] }) },
      { translateY: sun.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) },
    ],
  };
  const petal = (o: number) => ({ transform: [{ translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, o] }) }] });

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.xl }}>
        {/* ── Sunrise milestone ── */}
        <View style={s.motif}>
          <Animated.View style={[s.petalA, petal(-10)]} />
          <Animated.View style={[s.petalB, petal(-7)]} />
          <Animated.View style={[s.petalC, petal(-5)]} />

          <Svg viewBox="0 0 260 96" width="100%" height={110}>
            <Defs>
              <SvgGrad id="success-dawn" x1="0" y1="86" x2="260" y2="20">
                <Stop offset="0" stopColor={colors.dawnFrom} />
                <Stop offset="0.55" stopColor={colors.dawnMid} />
                <Stop offset="1" stopColor={colors.dawnTo} />
              </SvgGrad>
            </Defs>
            <Line x1="0" y1="86" x2="260" y2="86" stroke={colors.border} strokeWidth="1" />
            <AnimatedPath
              d="M14 86 C 60 26, 200 26, 246 86"
              stroke="url(#success-dawn)"
              strokeWidth="2.5"
              strokeLinecap="round"
              fill="none"
              strokeDasharray={ARC_LEN}
              strokeDashoffset={dashOffset as any}
            />
            <Circle cx="46" cy="60" r="4.5" fill={colors.dawnFrom} />
            <Circle cx="96" cy="38" r="4.5" fill={colors.dawnMid} />
            <Circle cx="164" cy="38" r="4.5" fill={colors.dawnMid} />
            <Circle cx="214" cy="60" r="4.5" fill={colors.dawnTo} />
          </Svg>

          {/* the sun — the check — rises into the crest */}
          <Animated.View style={[s.sunWrap, sunStyle]}>
            <View style={s.sunHalo} />
            <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.sun}>
              <Check size={32} color={colors.primaryFg} strokeWidth={2.75} />
            </LinearGradient>
          </Animated.View>
        </View>

        <Rise delay={260}><Eyebrow color={colors.accent} style={{ marginTop: spacing.xl, textAlign: "center" }}>Welcome aboard</Eyebrow></Rise>
        <Rise delay={340}>
          <Text style={s.title}>Registration Successful<Text style={{ color: colors.dawnMid }}>!</Text></Text>
        </Rise>
        <Rise delay={420}>
          <Body color={colors.mutedFg} style={{ marginTop: 12, textAlign: "center", lineHeight: 22, maxWidth: 320 }}>
            Congratulations! Your account has been successfully created. You can now log in to access our features.
          </Body>
        </Rise>
        {!!sessionError && <Small color={colors.destructive} style={{ marginTop: 12, textAlign: "center" }}>{sessionError}</Small>}
      </View>

      <Rise delay={520} style={{ paddingHorizontal: spacing.lg, paddingBottom: spacing.lg }}>
        <Springy onPress={continueToApp} disabled={opening} style={[s.cta, { backgroundColor: colors.primary }]}>
          {opening
            ? <ActivityIndicator color={colors.primaryFg} />
            : <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>
                Continue
              </Text>}
        </Springy>
      </Rise>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  motif: { position: "relative", width: "100%", maxWidth: 280, justifyContent: "center" },
  title: { fontFamily: fonts.display, fontSize: 32, letterSpacing: -0.8, color: colors.foreground, textAlign: "center", marginTop: 8 },
  sunWrap: { position: "absolute", top: 0, alignSelf: "center", alignItems: "center", justifyContent: "center" },
  sun: { width: 68, height: 68, borderRadius: 34, alignItems: "center", justifyContent: "center" },
  sunHalo: { position: "absolute", width: 96, height: 96, borderRadius: 48, backgroundColor: colors.dawnFrom, opacity: 0.25 },
  petalA: { position: "absolute", top: -4, right: 40, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent + "4D", zIndex: 2 },
  petalB: { position: "absolute", top: 40, left: 30, width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primary + "33", zIndex: 2 },
  petalC: { position: "absolute", top: 14, left: 70, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.dawnTo + "40", zIndex: 2 },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
