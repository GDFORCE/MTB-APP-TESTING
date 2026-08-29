import React, { useEffect, useRef } from "react";
import { Animated, Easing, ViewStyle } from "react-native";
import { useReducedMotionPref } from "@/src/lib/motion";

/**
 * Staggered rise-in wrapper — the RN counterpart of the design's `.animate-rise`
 * ("Every screen gets ONE orchestrated load reveal"). Fades up 16px on mount.
 * Uses RN's built-in Animated so it needs no Reanimated/babel setup.
 * When the OS requests reduced motion the content appears immediately.
 */
export function Rise({
  delay = 0,
  distance = 16,
  duration = 520,
  style,
  children,
}: {
  delay?: number;
  distance?: number;
  duration?: number;
  style?: ViewStyle | ViewStyle[];
  children: React.ReactNode;
}) {
  const t = useRef(new Animated.Value(0)).current;
  const reduced = useReducedMotionPref();
  useEffect(() => {
    if (reduced) { t.setValue(1); return; }
    Animated.timing(t, {
      toValue: 1,
      duration,
      delay,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [delay, duration, reduced, t]);
  return (
    <Animated.View
      style={[
        {
          opacity: t,
          transform: [{ translateY: t.interpolate({ inputRange: [0, 1], outputRange: [distance, 0] }) }],
        },
        style as any,
      ]}
    >
      {children}
    </Animated.View>
  );
}
