import React, { useState } from "react";
import { View, StyleSheet, ScrollView, Pressable, Text } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Check } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Body } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";

// Entry is by action, not self-declared role: you register an organization as one of
// these four entities. (Patients self-register elsewhere; PI/CRC come from the Site form.)
const entities = [
  { id: "sponsor", label: "Sponsor" },
  { id: "cro", label: "CRO" },
  { id: "smo", label: "SMO" },
  { id: "site", label: "Site / Hospital" },
];

export default function EntityType() {
  const router = useRouter();
  const [selected, setSelected] = useState<string | null>(null);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <AuthHeader
        eyebrow="Step 1 of 5"
        title="I am joining as…"
        onBack={() => router.back()}
        step={1}
      />

      <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm }} showsVerticalScrollIndicator={false}>
        <View style={s.list}>
          {entities.map((e, i) => {
            const on = selected === e.id;
            return (
              <Rise key={e.id} delay={180 + i * 70}>
                <Pressable
                  testID={`entity-${e.id}`}
                  onPress={() => setSelected(e.id)}
                  style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }, on && { backgroundColor: colors.secondary + "55" }]}
                >
                  {on && <View style={s.spine} />}
                  <Text style={[s.index, { color: on ? colors.accent : colors.mutedFg + "80" }]}>{String(i + 1).padStart(2, "0")}</Text>
                  <Body weight="600" color={on ? colors.primary : colors.foreground} style={{ flex: 1, fontSize: 17 }}>
                    {e.label}
                  </Body>
                  <View style={[s.check, on ? { backgroundColor: colors.primary, borderColor: colors.primary } : { borderColor: colors.border, backgroundColor: colors.card }]}>
                    {on && <Check size={14} color={colors.primaryFg} strokeWidth={3} />}
                  </View>
                </Pressable>
              </Rise>
            );
          })}
        </View>

        {selected && (
          <View style={s.selectionNote}>
            <Text style={s.selectionNoteText}>
              You’ll join as an administrator for{" "}
              <Text style={s.selectionNoteEntity}>{entities.find((entity) => entity.id === selected)?.label}</Text>.
            </Text>
          </View>
        )}
      </ScrollView>

      <View style={{ padding: spacing.lg }}>
        <Springy
          testID="entity-continue-button"
          disabled={!selected}
          onPress={() => router.push({ pathname: "/(auth)/register", params: { role: selected || "" } })}
          style={[s.cta, selected ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}
        >
          <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: selected ? colors.primaryFg : colors.mutedFg }}>Continue</Text>
        </Springy>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  list: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: "hidden" },
  row: { flexDirection: "row", alignItems: "center", gap: 16, paddingHorizontal: spacing.md + 4, paddingVertical: 26, position: "relative" },
  spine: { position: "absolute", left: 0, top: 0, bottom: 0, width: 3, backgroundColor: colors.accent },
  index: { width: 28, fontFamily: fonts.heading, fontSize: 18, fontVariant: ["tabular-nums"] },
  check: { width: 24, height: 24, borderRadius: 999, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  selectionNote: { minHeight: 40, marginTop: spacing.lg, paddingHorizontal: spacing.md, justifyContent: "center" },
  selectionNoteText: { textAlign: "center", fontFamily: fonts.regular, fontSize: 14, lineHeight: 20, color: colors.mutedFg },
  selectionNoteEntity: { fontFamily: fonts.bold, color: colors.foreground },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
