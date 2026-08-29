import React from "react";
import Svg, { Defs, LinearGradient, RadialGradient, Stop, Rect, Path, Circle } from "react-native-svg";
import { colors } from "../theme/tokens";

interface MtbLogoProps {
  size?: number;
  /** "tile" — full-color mark on its own rose-gradient tile. "plain" — journey line + dot only (line uses `color`). */
  variant?: "tile" | "plain";
  /** Line color for the "plain" variant. */
  color?: string;
}

/**
 * MTB — My Trial Board logo mark (RN port of components/clinical/mtb-logo).
 * The "M" is one continuous journey line — a patient's path across scheduled
 * visits (reads as a pulse). The apricot dot is the patient at the trial's
 * centre — the next visit milestone.
 */
export function MtbLogo({ size = 40, variant = "tile", color }: MtbLogoProps) {
  const lineColor = variant === "tile" ? colors.primaryFg : color || colors.foreground;
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Defs>
        <LinearGradient id="mtbTile" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
          <Stop offset="0" stopColor="#BE4A63" />
          <Stop offset="0.55" stopColor={colors.primary} />
          <Stop offset="1" stopColor={colors.primaryDeep} />
        </LinearGradient>
        <RadialGradient id="mtbGlow" cx="32" cy="4" r="46" gradientUnits="userSpaceOnUse">
          <Stop offset="0" stopColor={colors.primaryFg} stopOpacity="0.18" />
          <Stop offset="1" stopColor={colors.primaryFg} stopOpacity="0" />
        </RadialGradient>
        <RadialGradient id="mtbDot" cx="0.5" cy="0.4" r="0.6">
          <Stop offset="0" stopColor="#F3C79B" />
          <Stop offset="1" stopColor={colors.accent} />
        </RadialGradient>
      </Defs>

      {variant === "tile" && (
        <>
          <Rect width="64" height="64" rx="14.5" fill="url(#mtbTile)" />
          <Rect width="64" height="64" rx="14.5" fill="url(#mtbGlow)" />
        </>
      )}

      <Path
        d="M15 45.5 L22.5 21.5 L32 39 L41.5 21.5 L49 45.5"
        stroke={lineColor}
        strokeWidth="5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <Circle cx="32" cy="20" r="3.6" fill="url(#mtbDot)" />
    </Svg>
  );
}
