// Dawn Rounds text fonts — Bricolage Grotesque (display/headings), Figtree
// (UI/body), Spline Sans Mono (OTP + dense numerals). The fontFamily keys here
// are the exact names referenced in theme/tokens.ts (`fonts`). Loaded alongside
// the icon fonts in app/_layout.tsx.
import { useFonts } from "expo-font";

export const useAppFonts = (): readonly [boolean, Error | null] =>
  useFonts({
    "BricolageGrotesque-SemiBold": require("../../assets/fonts/BricolageGrotesque-SemiBold.ttf"),
    "BricolageGrotesque-Bold": require("../../assets/fonts/BricolageGrotesque-Bold.ttf"),
    "Figtree-Regular": require("../../assets/fonts/Figtree-Regular.ttf"),
    "Figtree-Medium": require("../../assets/fonts/Figtree-Medium.ttf"),
    "Figtree-SemiBold": require("../../assets/fonts/Figtree-SemiBold.ttf"),
    "Figtree-Bold": require("../../assets/fonts/Figtree-Bold.ttf"),
    "SplineSansMono-Medium": require("../../assets/fonts/SplineSansMono-Medium.ttf"),
  });
