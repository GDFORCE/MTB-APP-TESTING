import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { useEffect, useState } from "react";
import { LogBox, StatusBar, StyleSheet, View } from "react-native";
import NetInfo from "@react-native-community/netinfo";
import { CloudOff } from "lucide-react-native";
import { useIconFonts } from "@/src/hooks/use-icon-fonts";
import { useAppFonts } from "@/src/hooks/use-app-fonts";
import { AuthProvider, useAuth } from "@/src/auth/AuthContext";
import { StatusScreen } from "@/src/components/StatusScreen";
import { colors } from "@/src/theme/tokens";
import "@/src/i18n";

LogBox.ignoreAllLogs(true);
SplashScreen.preventAutoHideAsync();

// Screens outside (auth) that must stay reachable while signed out.
const PUBLIC_SCREENS = ["session-timeout", "no-internet"];

function RouterGuard() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const segments = useSegments();
  useEffect(() => {
    if (loading) return;
    const first = segments[0] as string | undefined;
    const inAuth = first === "(auth)" || first === undefined || first === "index";
    const isPublic = first !== undefined && PUBLIC_SCREENS.includes(first);
    if (!user && !inAuth && !isPublic) router.replace("/(auth)/welcome");
    else if (user) {
      // route to the role dashboard if currently on auth screens (covers empty segments too)
      if (inAuth) {
        const role = user.role;
        if ((role as string) === "admin") router.replace("/(app)/admin");
        else if (role === "patient") router.replace("/(app)/patient/dashboard");
        else if (role === "site") router.replace("/(app)/site/dashboard");
        else if (role === "smo") router.replace("/(app)/pi/dashboard");
        else if (role === "pi") router.replace("/(app)/pi/dashboard");
        else if (role === "crc") router.replace("/(app)/crc/dashboard");
        else router.replace("/(app)/sponsor/dashboard");
      }
    }
  }, [user, loading, segments, router]);
  return null;
}

/** Full-screen offline takeover while the device reports no connectivity. */
function OfflineOverlay() {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const unsub = NetInfo.addEventListener((state) => {
      setOffline(state.isConnected === false);
    });
    return unsub;
  }, []);
  if (!offline) return null;
  return (
    <View style={[StyleSheet.absoluteFill, { backgroundColor: colors.background, zIndex: 100 }]}>
      <StatusScreen
        icon={CloudOff}
        eyebrow="Connection lost"
        title="You're offline"
        message="We can't reach the network right now. Check your connection and try again."
        actionLabel="Try again"
        onAction={() => { NetInfo.refresh(); }}
        testID="offline-overlay-retry"
      />
    </View>
  );
}

export default function RootLayout() {
  const [loaded, error] = useIconFonts();
  const [fontsLoaded, fontsError] = useAppFonts();
  const ready = (loaded || error) && (fontsLoaded || fontsError);
  useEffect(() => { if (ready) SplashScreen.hideAsync(); }, [ready]);
  if (!ready) return null;
  return (
    <AuthProvider>
      <StatusBar barStyle="dark-content" backgroundColor={colors.background} />
      <RouterGuard />
      <View style={styles.app}>
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.background } }} />
        <OfflineOverlay />
      </View>
    </AuthProvider>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1 },
});
