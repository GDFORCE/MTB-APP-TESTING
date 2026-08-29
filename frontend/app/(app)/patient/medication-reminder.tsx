import React, { useEffect } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { colors } from "@/src/theme/tokens";

// Medication adherence is intentionally kept in one live flow. The previous
// screen managed a disconnected free-text reminder collection, while the
// approved UI and backend use prescribed medications, dose logs and adherence.
export default function MedicationReminder() {
  const router = useRouter();

  useEffect(() => {
    router.replace({
      pathname: "/(app)/patient/my-trial",
      params: { tab: "medications", medTab: "today" },
    });
  }, [router]);

  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background }}>
      <ActivityIndicator color={colors.primary} />
    </View>
  );
}
