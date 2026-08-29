import React from "react";
import { useRouter } from "expo-router";
import { Lock } from "lucide-react-native";
import { StatusScreen } from "@/src/components/StatusScreen";

/** Session expired — a gentle dusk moment; one clear way back in. */
export default function SessionTimeout() {
  const router = useRouter();
  return (
    <StatusScreen
      icon={Lock}
      eyebrow="Signed out"
      title="Session expired"
      message="You were signed out after a period of inactivity. Sign in again to pick up where you left off."
      actionLabel="Sign in again"
      onAction={() => router.replace("/(auth)/sign-in")}
      testID="session-timeout-signin"
    />
  );
}
