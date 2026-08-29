// Shared motion utilities — the app-wide counterparts of the design's
// orchestrated reveal + task/list transitions, with OS reduced-motion respect.
import { useEffect, useState } from "react";
import { AccessibilityInfo, LayoutAnimation, Platform, UIManager } from "react-native";

// Module-level mirror of the OS reduce-motion preference so non-hook call
// sites (LayoutAnimation triggers) can consult it synchronously.
let reduceMotionPref = false;
try {
  AccessibilityInfo.isReduceMotionEnabled()
    .then((value) => { reduceMotionPref = !!value; })
    .catch(() => {});
  AccessibilityInfo.addEventListener?.("reduceMotionChanged", (value) => {
    reduceMotionPref = !!value;
  });
} catch { /* accessibility API unavailable (e.g. some web targets) */ }

export function prefersReducedMotion(): boolean {
  return reduceMotionPref;
}

/** Hook variant for components that render differently under reduced motion. */
export function useReducedMotionPref(): boolean {
  const [reduced, setReduced] = useState(reduceMotionPref);
  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => { if (mounted) setReduced(!!value); })
      .catch(() => {});
    const sub = AccessibilityInfo.addEventListener?.("reduceMotionChanged", (value) => {
      setReduced(!!value);
    });
    return () => { mounted = false; (sub as any)?.remove?.(); };
  }, []);
  return reduced;
}

let androidLayoutAnimationEnabled = false;

/**
 * Animate the NEXT layout change (task completion, list insertion/removal,
 * filter switches). Call immediately before the state update. No-ops when the
 * OS asks for reduced motion.
 */
export function animateNextLayout(duration = 220) {
  if (reduceMotionPref || Platform.OS === "web") return;
  if (Platform.OS === "android" && !androidLayoutAnimationEnabled) {
    UIManager.setLayoutAnimationEnabledExperimental?.(true);
    androidLayoutAnimationEnabled = true;
  }
  LayoutAnimation.configureNext(
    LayoutAnimation.create(duration, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.opacity),
  );
}
