import React from 'react';
import { View, Text, Pressable, StyleSheet, StyleProp, ViewStyle, ActivityIndicator } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { colors, radii, spacing, shadows, typography, dawnGradient, fonts, figtreeFamily } from '../theme/tokens';

export const Eyebrow = ({ children, style, color }: any) => (
  <Text style={[{ ...typography.eyebrow, color: color || colors.primary }, style]}>{children}</Text>
);

export const Display = ({ children, style, color }: any) => (
  <Text style={[{ ...typography.display, color: color || colors.foreground }, style]}>{children}</Text>
);

export const H1 = ({ children, style, color }: any) => (
  <Text style={[{ ...typography.h1, color: color || colors.foreground }, style]}>{children}</Text>
);

export const Body = ({ children, style, color, weight }: any) => (
  <Text style={[{ ...typography.body, color: color || colors.foreground, fontFamily: figtreeFamily(weight) }, style]}>{children}</Text>
);

export const Small = ({ children, style, color }: any) => (
  <Text style={[{ ...typography.small, color: color || colors.mutedFg }, style]}>{children}</Text>
);

export function Card({ children, style, padded = true }: { children: React.ReactNode; style?: StyleProp<ViewStyle>; padded?: boolean }) {
  return <View style={[{ backgroundColor: colors.card, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, padding: padded ? spacing.md : 0, ...shadows.sm }, style]}>{children}</View>;
}

interface BtnProps { children: React.ReactNode; onPress?: () => void; variant?: 'primary' | 'secondary' | 'ghost' | 'dawn'; disabled?: boolean; loading?: boolean; testID?: string; style?: ViewStyle; }
export function Button({ children, onPress, variant = 'primary', disabled, loading, testID, style }: BtnProps) {
  const body = loading
    ? <ActivityIndicator color={variant === 'primary' || variant === 'dawn' ? colors.primaryFg : colors.primary} />
    : <Text style={{ color: variant === 'primary' || variant === 'dawn' ? colors.primaryFg : colors.primary, fontFamily: fonts.bold, fontSize: 15 }}>{children}</Text>;

  if (variant === 'dawn') {
    return (
      <Pressable testID={testID} onPress={disabled || loading ? undefined : onPress} style={[{ opacity: disabled ? 0.5 : 1 }, style]}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={[btnStyles.base, { backgroundColor: 'transparent' }]}>{body}</LinearGradient>
      </Pressable>
    );
  }
  const bg = variant === 'primary' ? colors.primary : variant === 'secondary' ? colors.card : 'transparent';
  return (
    <Pressable testID={testID} onPress={disabled || loading ? undefined : onPress}
      style={({ pressed }) => [btnStyles.base, { backgroundColor: bg, borderWidth: variant === 'secondary' ? 1 : 0, borderColor: colors.primary + '55', opacity: disabled ? 0.4 : pressed ? 0.85 : 1, transform: [{ scale: pressed ? 0.97 : 1 }] }, style]}>
      {body}
    </Pressable>
  );
}

const btnStyles = StyleSheet.create({
  base: { borderRadius: radii.pill, paddingVertical: 14, alignItems: 'center', justifyContent: 'center', ...shadows.sm },
});

export function SectionHeader({ index, label, action }: { index: string; label: string; action?: React.ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 12 }}>
      <Text style={{ color: colors.accent, fontFamily: fonts.bold, fontSize: 14, fontVariant: ['tabular-nums'] }}>{index}</Text>
      <Eyebrow>{label}</Eyebrow>
      <View style={{ flex: 1, height: 1, backgroundColor: colors.border }} />
      {action}
    </View>
  );
}

export const PaperBg = ({ children, style }: any) => (
  <View style={[{ flex: 1, backgroundColor: colors.background }, style]}>{children}</View>
);
