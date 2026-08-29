import React from "react";
import { Pressable, PressableProps, StyleProp, ViewStyle } from "react-native";

/**
 * Springy press — the RN counterpart of the design's `.springy` + `active:scale-[0.97]`.
 * A Pressable that scales down slightly while pressed. Accepts the same style array
 * as a normal Pressable so existing CTAs can swap `Pressable` → `Springy` in place.
 */
export function Springy({ style, disabled, children, ...rest }: PressableProps & { style?: StyleProp<ViewStyle> }) {
  return (
    <Pressable
      disabled={disabled}
      style={({ pressed }) => [
        style as ViewStyle,
        pressed && !disabled ? { transform: [{ scale: 0.97 }] } : null,
      ]}
      {...rest}
    >
      {children as any}
    </Pressable>
  );
}
