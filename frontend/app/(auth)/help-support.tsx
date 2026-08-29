import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import {
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Mail,
  MessageCircle,
  Phone,
  RefreshCw,
  Ticket,
} from "lucide-react-native";
import { Body, Eyebrow, Small } from "@/src/components/ui";
import { colors, fonts, radii, shadows, spacing } from "@/src/theme/tokens";
import { api } from "@/src/api/client";

type SupportContact = {
  name?: string;
  email?: string;
  phone?: string;
  hours?: string;
};

export default function HelpSupport() {
  const router = useRouter();
  const [contact, setContact] = useState<SupportContact | null>(null);
  const [contactLoading, setContactLoading] = useState(true);
  const [contactError, setContactError] = useState("");

  const loadContact = () => {
    setContactLoading(true);
    setContactError("");
    api.get("/support/contact")
      .then((response) => setContact(response.data || null))
      .catch(() => {
        setContact(null);
        setContactError("Platform Support details are unavailable. Check your connection and try again.");
      })
      .finally(() => setContactLoading(false));
  };

  useEffect(loadContact, []);

  const openContactLink = async (url: string, label: string) => {
    try {
      await Linking.openURL(url);
    } catch {
      Alert.alert(`Couldn't open ${label}`, `No compatible ${label} app is available on this device.`);
    }
  };

  const items = [
    {
      icon: CircleHelp,
      tint: colors.info,
      label: "Frequently Asked Questions",
      sub: "Browse common questions",
      onPress: () => Alert.alert(
        "Frequently Asked Questions",
        "For registration or password recovery help, contact Platform Support below. After signing in, the full FAQ is available under Profile & Settings.",
      ),
    },
    {
      icon: MessageCircle,
      tint: colors.success,
      label: "Contact Support",
      sub: "Get help from our team",
      onPress: () => contact?.email
        ? openContactLink(`mailto:${contact.email}`, "email")
        : Alert.alert("Contact unavailable", "Platform Support details have not loaded yet."),
    },
    {
      icon: Ticket,
      tint: colors.violet,
      label: "My Tickets",
      sub: "Track your raised tickets",
      onPress: () => Alert.alert(
        "Sign in required",
        "Sign in to view or raise support tickets from Profile & Settings.",
      ),
    },
  ];

  return (
    <SafeAreaView style={s.container} edges={["top", "bottom"]}>
      <View style={s.header}>
        <Pressable
          onPress={() => router.back()}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="Back to forgot password"
          style={s.back}
        >
          <ChevronLeft size={22} color={colors.primaryFg} />
        </Pressable>
        <View>
          <Eyebrow color={colors.overlay25}>Profile & settings</Eyebrow>
          <Text style={s.title}>Help & Support</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={s.body}>
        {items.map((item) => (
          <Pressable
            key={item.label}
            onPress={item.onPress}
            style={({ pressed }) => [s.card, pressed && s.pressed]}
          >
            <View style={s.row}>
              <View style={[s.icon, { backgroundColor: item.tint + "1A" }]}>
                <item.icon size={20} color={item.tint} />
              </View>
              <View>
                <Body weight="600">{item.label}</Body>
                <Small>{item.sub}</Small>
              </View>
            </View>
            <ChevronRight size={18} color={colors.mutedFg} />
          </Pressable>
        ))}

        <View style={[s.card, s.contactCard]}>
          <Eyebrow color={colors.primary}>Contact us</Eyebrow>
          {contactLoading ? (
            <View style={s.stateRow}>
              <ActivityIndicator color={colors.primary} />
              <Small>Loading Platform Support details…</Small>
            </View>
          ) : contactError ? (
            <View style={{ gap: 10 }}>
              <Small color={colors.destructive}>{contactError}</Small>
              <Pressable
                onPress={loadContact}
                style={({ pressed }) => [s.retry, pressed && s.pressed]}
              >
                <RefreshCw size={14} color={colors.primary} />
                <Small color={colors.primary} weight="700">Retry</Small>
              </Pressable>
            </View>
          ) : contact ? (
            <>
              <Body weight="600">{contact.name || "Platform Support"}</Body>
              {!!contact.email && (
                <Pressable
                  onPress={() => openContactLink(`mailto:${contact.email}`, "email")}
                  style={s.contactRow}
                >
                  <View style={[s.smallIcon, { backgroundColor: colors.info + "1A" }]}>
                    <Mail size={15} color={colors.info} />
                  </View>
                  <Small>{contact.email}</Small>
                </Pressable>
              )}
              {!!contact.phone && (
                <Pressable
                  onPress={() => openContactLink(
                    `tel:${contact.phone?.replace(/[^\d+]/g, "")}`,
                    "phone",
                  )}
                  style={s.contactRow}
                >
                  <View style={[s.smallIcon, { backgroundColor: colors.success + "1A" }]}>
                    <Phone size={15} color={colors.success} />
                  </View>
                  <Small>{contact.phone}</Small>
                </Pressable>
              )}
              {!!contact.hours && (
                <View style={s.contactRow}>
                  <View style={[s.smallIcon, { backgroundColor: colors.warning + "1A" }]}>
                    <Clock3 size={15} color={colors.warning} />
                  </View>
                  <Small>{contact.hours}</Small>
                </View>
              )}
            </>
          ) : (
            <Small>No Platform Support contact is configured.</Small>
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  header: {
    minHeight: 76,
    paddingHorizontal: spacing.md,
    paddingVertical: 14,
    backgroundColor: colors.primaryDeep,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  back: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: radii.pill,
  },
  title: {
    color: colors.primaryFg,
    fontFamily: fonts.heading,
    fontSize: 18,
    marginTop: 1,
  },
  body: { padding: spacing.md, gap: 10 },
  card: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.lg,
    padding: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    ...shadows.sm,
  },
  pressed: { opacity: 0.78, transform: [{ scale: 0.99 }] },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  icon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
  },
  contactCard: {
    marginTop: 2,
    flexDirection: "column",
    alignItems: "stretch",
    gap: 12,
  },
  contactRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  stateRow: { minHeight: 44, flexDirection: "row", alignItems: "center", gap: 10 },
  retry: {
    alignSelf: "flex-start",
    minHeight: 36,
    paddingHorizontal: 12,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.primary + "45",
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
  },
  smallIcon: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: "center",
    justifyContent: "center",
  },
});
