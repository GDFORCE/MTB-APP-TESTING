// Dawn Rounds design tokens — converted from the v0 OKLCH palette to hex/rgba
// so React Native can consume them directly. Tone-mapped for the warm cream-blush
// paper, deep plum ink, raspberry-rose primary and apricot→rose dawn gradient.
export const colors = {
  background: '#FBF2E8',        // cream-blush paper
  surface: '#F4E5D3',           // peach-cream panels
  card: '#FEFAF1',              // warm white card
  foreground: '#2E1B33',        // deep plum ink
  mutedFg: '#7B5F73',           // plum-gray AA on cream
  border: '#E6D6C5',            // warm hairline
  input: '#E6D6C5',

  primary: '#A6213F',           // raspberry-rose
  primaryDeep: '#6B1437',       // deep mulberry
  primaryFg: '#FBF2E8',
  secondary: '#F0D7DC',         // pale rose tint
  secondaryFg: '#7A1834',

  accent: '#E69B5C',            // apricot
  accentFg: '#5A3318',
  info: '#7B6BB8',              // dusty lavender
  infoFg: '#FFFFFF',
  violet: '#8E5BB4',
  warning: '#D89A3C',
  warningFg: '#FFFFFF',
  success: '#5C9A6E',
  successFg: '#FFFFFF',
  destructive: '#C0392B',
  destructiveFg: '#FFFFFF',

  dawnFrom: '#F5C57A',          // apricot
  dawnMid: '#E07A4B',           // sunrise coral
  dawnTo: '#A6213F',            // deep rose
  white: '#FFFFFF',
  black: '#000000',
  overlay10: 'rgba(255,255,255,0.10)',
  overlay20: 'rgba(255,255,255,0.20)',
  overlay25: 'rgba(255,255,255,0.25)',
};

export const dawnGradient = [colors.dawnFrom, colors.dawnMid, colors.dawnTo] as const;
export const heroGradient = [colors.primary, colors.primaryDeep] as const;

export const radii = { sm: 8, md: 12, lg: 16, xl: 24, pill: 999 };
export const spacing = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, xxl: 40 };
export const shadows = {
  sm: { shadowColor: '#2E1B33', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  md: { shadowColor: '#2E1B33', shadowOpacity: 0.12, shadowRadius: 16, shadowOffset: { width: 0, height: 6 }, elevation: 6 },
};

// Dawn Rounds type families — Bricolage Grotesque (display/headings), Figtree
// (UI/body), Spline Sans Mono (OTP + dense numerals). Loaded in app/_layout via
// use-app-fonts. Custom fonts embed their weight, so we swap FAMILY per weight
// (fontWeight is ignored once a custom fontFamily is set).
export const fonts = {
  display: 'BricolageGrotesque-Bold',
  heading: 'BricolageGrotesque-SemiBold',
  regular: 'Figtree-Regular',
  medium: 'Figtree-Medium',
  semibold: 'Figtree-SemiBold',
  bold: 'Figtree-Bold',
  mono: 'SplineSansMono-Medium',
};

export function figtreeFamily(weight?: string | number): string {
  const w = String(weight ?? '400');
  if (w === '700' || w === 'bold') return fonts.bold;
  if (w === '600') return fonts.semibold;
  if (w === '500') return fonts.medium;
  return fonts.regular;
}

export const typography = {
  display: { fontFamily: fonts.display, fontSize: 32, letterSpacing: -0.6 },
  h1: { fontFamily: fonts.display, fontSize: 26, letterSpacing: -0.4 },
  h2: { fontFamily: fonts.heading, fontSize: 20, letterSpacing: -0.2 },
  body: { fontFamily: fonts.regular, fontSize: 15 },
  small: { fontFamily: fonts.regular, fontSize: 13 },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, textTransform: 'uppercase' as const },
};
