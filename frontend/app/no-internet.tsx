import React from "react";
import { useRouter } from "expo-router";
import NetInfo from "@react-native-community/netinfo";
import { CloudOff } from "lucide-react-native";
import { StatusScreen } from "@/src/components/StatusScreen";

/** Offline — the morning light dims for a moment. Retry re-checks the network. */
export default function NoInternet() {
  const router = useRouter();
  const retry = async () => {
    const state = await NetInfo.fetch();
    if (state.isConnected !== false && router.canGoBack()) router.back();
  };
  return (
    <StatusScreen
      icon={CloudOff}
      eyebrow="Connection lost"
      title="You're offline"
      message="We can't reach the network right now. Check your connection and try again."
      actionLabel="Try again"
      onAction={retry}
      testID="no-internet-retry"
    />
  );
}
