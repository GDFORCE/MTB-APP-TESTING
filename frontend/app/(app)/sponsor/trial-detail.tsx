import React, { useEffect } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { colors } from "@/src/theme/tokens";

/**
 * Compatibility route for old notifications, bookmarks, and shared links.
 * Every role now renders the production-backed shared Trial Summary screen.
 */
export default function SponsorTrialDetailRedirect() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id?: string }>();

  useEffect(() => {
    router.replace({
      pathname: "/(app)/clinical/trial-summary",
      params: id ? { id } : {},
    });
  }, [id, router]);

  return (
    <View style={styles.page}>
      <ActivityIndicator color={colors.primary} />
    </View>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
  },
});
