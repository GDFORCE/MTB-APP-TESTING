import React, { useMemo, useState } from "react";
import { View, Text, TextInput, StyleSheet, Modal, Pressable, FlatList, Platform } from "react-native";
import { Check, ChevronDown, Search, X } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Country, DEFAULT_COUNTRY_CODE, flagEmoji, getCountry, searchCountries } from "@/src/data/countries";

// Regional-indicator flags need a colour-emoji font. Windows browsers ship none,
// so there we draw the alpha-2 code in a chip instead of leaking "IN" glyphs.
const SUPPORTS_FLAG_EMOJI = !(
  Platform.OS === "web"
  && typeof navigator !== "undefined"
  && /Windows/i.test(navigator.userAgent || "")
);

export function Flag({ code, size = 20 }: { code: string; size?: number }) {
  if (SUPPORTS_FLAG_EMOJI) {
    return <Text style={{ fontSize: size, lineHeight: size * 1.25 }}>{flagEmoji(code)}</Text>;
  }
  return (
    <View style={[s.flagChip, { height: size, minWidth: size * 1.35 }]}>
      <Text style={[s.flagChipText, { fontSize: size * 0.55 }]}>{code}</Text>
    </View>
  );
}

export function CountryPicker({
  visible,
  selected,
  onSelect,
  onClose,
}: {
  visible: boolean;
  selected: string;
  onSelect: (c: Country) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const results = useMemo(() => searchCountries(query), [query]);

  const close = () => { setQuery(""); onClose(); };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close}>
      <View style={s.overlay}>
        <Pressable style={StyleSheet.absoluteFill} onPress={close} />
        <View style={s.sheet}>
          <View style={s.sheetHead}>
            <View style={{ flex: 1 }}>
              <Text style={s.eyebrow}>Country code</Text>
              <Text style={s.sheetTitle}>Select country</Text>
            </View>
            <Pressable onPress={close} hitSlop={10} style={s.closeBtn}>
              <X size={16} color={colors.mutedFg} />
            </Pressable>
          </View>

          <View style={s.searchWrap}>
            <Search size={16} color={colors.mutedFg} />
            <TextInput
              value={query}
              onChangeText={setQuery}
              autoComplete="off"
              importantForAutofill="no"
              textContentType="none"
              selectionColor={colors.primary}
              cursorColor={colors.primary}
              placeholder="Search country or code"
              placeholderTextColor={colors.mutedFg + "99"}
              autoCorrect={false}
              autoCapitalize="none"
              style={s.searchInput}
            />
          </View>

          <FlatList
            data={results}
            keyExtractor={(c) => c.code}
            style={s.list}
            contentContainerStyle={s.listContent}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
            initialNumToRender={16}
            ListEmptyComponent={<Text style={s.empty}>No country matches “{query.trim()}”.</Text>}
            renderItem={({ item }) => {
              const on = item.code === selected;
              return (
                <Pressable
                  onPress={() => { onSelect(item); close(); }}
                  style={[s.row, on && { backgroundColor: colors.primary }]}
                >
                  <Flag code={item.code} />
                  <Text
                    numberOfLines={1}
                    style={[s.rowName, on && { color: colors.primaryFg, fontFamily: fonts.semibold }]}
                  >
                    {item.name}
                  </Text>
                  <Text style={[s.rowDial, on && { color: colors.primaryFg }]}>+{item.dial}</Text>
                  {on && <Check size={15} color={colors.primaryFg} strokeWidth={3} />}
                </Pressable>
              );
            }}
          />
        </View>
      </View>
    </Modal>
  );
}

/**
 * Country selector + national number in one row. `countryCode` is the ISO-3166
 * alpha-2 of the chosen dialling country; `value` holds only the national digits
 * — the two are combined into E.164 by the validation layer.
 */
export function PhoneField({
  value,
  onChangeText,
  countryCode = DEFAULT_COUNTRY_CODE,
  onChangeCountry,
  error,
  editable = true,
  placeholder,
}: {
  value: string;
  onChangeText: (v: string) => void;
  countryCode?: string;
  onChangeCountry: (code: string) => void;
  error?: boolean;
  editable?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const country = getCountry(countryCode);

  return (
    <>
      <View style={{ flexDirection: "row", gap: 8 }}>
        <Pressable
          onPress={() => editable && setOpen(true)}
          disabled={!editable}
          style={[s.prefix, error && s.prefixError, !editable && s.disabled]}
        >
          <Flag code={country.code} size={18} />
          <Text style={s.dial}>+{country.dial}</Text>
          <ChevronDown size={15} color={colors.mutedFg} />
        </Pressable>
        <TextInput
          value={value}
          onChangeText={(v) => onChangeText(v.replace(/[^\d\s-]/g, ""))}
          autoComplete="off"
          importantForAutofill="no"
          selectionColor={colors.primary}
          cursorColor={colors.primary}
          editable={editable}
          keyboardType="phone-pad"
          textContentType="none"
          placeholder={placeholder ?? "Mobile number"}
          placeholderTextColor={colors.mutedFg + "99"}
          style={[s.input, error && s.inputError, !editable && s.disabled]}
        />
      </View>
      <CountryPicker
        visible={open}
        selected={country.code}
        onSelect={(c) => onChangeCountry(c.code)}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

const s = StyleSheet.create({
  // ── Field row ─────────────────────────────────────────────────────────────
  prefix: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 12, paddingVertical: 12,
    borderRadius: radii.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  prefixError: { borderColor: colors.destructive },
  dial: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 15 },
  input: {
    flex: 1, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border,
    borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12,
    fontSize: 15, color: colors.foreground, fontFamily: fonts.regular,
  },
  inputError: { borderColor: colors.destructive, backgroundColor: colors.destructive + "08" },
  disabled: { opacity: 0.6 },
  flagChip: {
    alignItems: "center", justifyContent: "center", paddingHorizontal: 4,
    borderRadius: 4, backgroundColor: colors.secondary,
  },
  flagChipText: { fontFamily: fonts.bold, color: colors.secondaryFg, letterSpacing: 0.4 },

  // ── Picker sheet — mirrors the Select modal used elsewhere in the form ─────
  overlay: {
    flex: 1, backgroundColor: colors.primaryDeep + "80",
    alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.lg,
  },
  sheet: {
    width: "100%", maxWidth: 400, maxHeight: "78%",
    backgroundColor: colors.card, borderRadius: radii.lg,
    borderWidth: 1, borderColor: colors.border, overflow: "hidden",
    shadowColor: "#2E1B33", shadowOpacity: 0.2, shadowRadius: 24,
    shadowOffset: { width: 0, height: 12 }, elevation: 12,
  },
  sheetHead: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingHorizontal: spacing.md, paddingTop: spacing.md, paddingBottom: spacing.sm,
  },
  eyebrow: {
    fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4,
    textTransform: "uppercase", color: colors.accent,
  },
  sheetTitle: { fontFamily: fonts.heading, fontSize: 18, color: colors.foreground, marginTop: 2 },
  closeBtn: {
    width: 30, height: 30, borderRadius: 999, alignItems: "center", justifyContent: "center",
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border,
  },
  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    marginHorizontal: spacing.md, marginBottom: spacing.sm,
    paddingHorizontal: 12, paddingVertical: Platform.OS === "web" ? 8 : 6,
    borderRadius: radii.md, borderWidth: 1, borderColor: colors.border,
    backgroundColor: colors.background,
  },
  searchInput: {
    flex: 1, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular,
    paddingVertical: 4,
    ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : null),
  },
  list: { paddingHorizontal: 6, paddingBottom: 6 },
  listContent: { paddingBottom: 6 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingHorizontal: spacing.sm + 2, paddingVertical: 11, borderRadius: radii.md,
  },
  rowName: { flex: 1, fontSize: 15, color: colors.foreground, fontFamily: fonts.regular },
  rowDial: { fontSize: 14, color: colors.mutedFg, fontFamily: fonts.medium, fontVariant: ["tabular-nums"] },
  empty: {
    paddingVertical: spacing.lg, textAlign: "center",
    fontSize: 14, color: colors.mutedFg, fontFamily: fonts.regular,
  },
});
