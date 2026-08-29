import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, ScrollView, Pressable, Switch, StyleSheet, StatusBar,
  ActivityIndicator, Animated,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ChevronLeft } from "lucide-react-native";
import { colors, spacing, radii, fonts, shadows } from "@/src/theme/tokens";
import { api } from "@/src/api/client";

// ── Preference shape (server keys) ───────────────────────────────────────────
// These five keys are in server.py's PATCH /preferences allow-list, so they
// persist. Optimistic UI + revert-on-error keeps the UI responsive.
type Prefs = {
  calendar_default_view: "day" | "week" | "month";
  week_start: "sunday" | "monday";
  reminders_visits: boolean;
  reminders_meds: boolean;
  reminder_hours_before: number;
};

const DEFAULTS: Prefs = {
  calendar_default_view: "month",
  week_start: "sunday",
  reminders_visits: true,
  reminders_meds: true,
  reminder_hours_before: 48,
};

const VIEW_OPTS: { value: Prefs["calendar_default_view"]; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
];
const WEEK_OPTS: { value: Prefs["week_start"]; label: string }[] = [
  { value: "sunday", label: "Sunday" },
  { value: "monday", label: "Monday" },
];
const HOURS_OPTS: { value: number; label: string }[] = [
  { value: 24, label: "1 day before" },
  { value: 48, label: "2 days before" },
  { value: 72, label: "3 days before" },
];

// ── Toggle (matches profile.tsx) ─────────────────────────────────────────────
function Toggle({ on, onToggle, testID }: { on: boolean; onToggle: (v: boolean) => void; testID?: string }) {
  return (
    <Switch
      testID={testID}
      value={on}
      onValueChange={onToggle}
      trackColor={{ true: colors.primary, false: colors.border }}
      thumbColor={colors.primaryFg}
    />
  );
}

// ── Radio group ──────────────────────────────────────────────────────────────
function RadioGroup<T extends string | number>({
  options, value, onChange, testIDPrefix,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  testIDPrefix: string;
}) {
  return (
    <View style={{ gap: 10, marginTop: 10 }}>
      {options.map((opt) => {
        const active = value === opt.value;
        return (
          <Pressable
            key={String(opt.value)}
            testID={`${testIDPrefix}-${opt.value}`}
            onPress={() => onChange(opt.value)}
            style={s.radioRow}
            hitSlop={6}
          >
            <View style={[s.radioOuter, active && s.radioOuterActive]}>
              {active && <View style={s.radioInner} />}
            </View>
            <Text style={s.radioLabel}>{opt.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={s.card}>
      <View style={s.cardHead}>
        <Text style={s.cardHeadText}>{title}</Text>
      </View>
      <View style={s.cardBody}>{children}</View>
    </View>
  );
}

export default function CalendarSettings() {
  const router = useRouter();
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  const [loading, setLoading] = useState(true);

  // ── Toast ──
  const [toast, setToast] = useState<string>("");
  const toastAnim = useRef(new Animated.Value(0)).current;
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    Animated.timing(toastAnim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    toastTimer.current = setTimeout(() => {
      Animated.timing(toastAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2200);
  }, [toastAnim]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  // ── Load on mount ──
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = (await api.get("/preferences")).data || {};
        if (cancelled) return;
        setPrefs((p) => ({
          calendar_default_view: data.calendar_default_view ?? p.calendar_default_view,
          week_start: data.week_start ?? p.week_start,
          reminders_visits: typeof data.reminders_visits === "boolean" ? data.reminders_visits : p.reminders_visits,
          reminders_meds: typeof data.reminders_meds === "boolean" ? data.reminders_meds : p.reminders_meds,
          reminder_hours_before: typeof data.reminder_hours_before === "number" ? data.reminder_hours_before : p.reminder_hours_before,
        }));
      } catch {
        // keep defaults on failure
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Optimistic PATCH with revert on error ──
  const savePref = useCallback(async <K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    const prev = prefs[key];
    if (prev === value) return;
    setPrefs((p) => ({ ...p, [key]: value }));
    try {
      await api.patch("/preferences", { [key]: value });
    } catch {
      // Only revert if no newer write superseded this one: the key's current
      // value must still equal what THIS request optimistically set.
      setPrefs((p) => (p[key] === value ? { ...p, [key]: prev } : p));
      showToast("Couldn't save. Please try again.");
    }
  }, [prefs, showToast]);

  const remindersOn = prefs.reminders_visits || prefs.reminders_meds;

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />

      {/* App bar */}
      <View style={s.appbar}>
        <SafeAreaView edges={["top"]}>
          <View style={s.appbarRow}>
            <Pressable testID="cs-back" onPress={() => router.back()} hitSlop={10} style={s.appbarBtn}>
              <ChevronLeft size={22} color={colors.primaryFg} />
            </Pressable>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.eyebrowLight}>CALENDAR</Text>
              <Text style={s.appbarTitle}>Settings</Text>
            </View>
          </View>
        </SafeAreaView>
      </View>

      {loading ? (
        <View style={s.loadingWrap}><ActivityIndicator color={colors.primary} /></View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ padding: spacing.md, gap: spacing.md, paddingBottom: 40 }}
          showsVerticalScrollIndicator={false}
        >
          {/* DISPLAY */}
          <SectionCard title="DISPLAY">
            <View>
              <Text style={s.rowLabel}>Default view</Text>
              <RadioGroup
                testIDPrefix="cs-view"
                options={VIEW_OPTS}
                value={prefs.calendar_default_view}
                onChange={(v) => savePref("calendar_default_view", v)}
              />
            </View>
            <View style={s.divider} />
            <View>
              <Text style={s.rowLabel}>Start week on</Text>
              <RadioGroup
                testIDPrefix="cs-week"
                options={WEEK_OPTS}
                value={prefs.week_start}
                onChange={(v) => savePref("week_start", v)}
              />
            </View>
          </SectionCard>

          {/* REMINDERS */}
          <SectionCard title="REMINDERS">
            <View style={s.toggleRow}>
              <Text style={s.rowLabel}>Visit reminders</Text>
              <Toggle testID="cs-reminders-visits" on={prefs.reminders_visits} onToggle={(v) => savePref("reminders_visits", v)} />
            </View>
            <View style={s.toggleRow}>
              <Text style={s.rowLabel}>Medication reminders</Text>
              <Toggle testID="cs-reminders-meds" on={prefs.reminders_meds} onToggle={(v) => savePref("reminders_meds", v)} />
            </View>
            {remindersOn && (
              <>
                <View style={s.divider} />
                <View>
                  <Text style={s.rowLabel}>Remind me</Text>
                  <RadioGroup
                    testIDPrefix="cs-hours"
                    options={HOURS_OPTS}
                    value={prefs.reminder_hours_before}
                    onChange={(v) => savePref("reminder_hours_before", v)}
                  />
                </View>
              </>
            )}
          </SectionCard>

        </ScrollView>
      )}

      {/* Toast */}
      {toast !== "" && (
        <Animated.View
          pointerEvents="none"
          style={[
            s.toast,
            { opacity: toastAnim, transform: [{ translateY: toastAnim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }] },
          ]}
        >
          <Text style={s.toastText}>{toast}</Text>
        </Animated.View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  appbar: { backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.md, paddingBottom: 14 },
  appbarRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 6 },
  appbarBtn: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center" },
  eyebrowLight: { color: colors.overlay25, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4 },
  appbarTitle: { color: colors.primaryFg, fontFamily: fonts.display, fontSize: 20, letterSpacing: -0.4 },

  loadingWrap: { flex: 1, alignItems: "center", justifyContent: "center" },

  card: { backgroundColor: colors.card, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, overflow: "hidden", ...shadows.sm },
  cardHead: { paddingHorizontal: spacing.md, paddingVertical: 10, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  cardHeadText: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, color: colors.mutedFg },
  cardBody: { paddingHorizontal: spacing.md, paddingVertical: spacing.md, gap: 14 },

  rowLabel: { fontFamily: fonts.medium, fontSize: 15, color: colors.foreground },
  toggleRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },

  divider: { height: 1, backgroundColor: colors.border },

  radioRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  radioOuter: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: colors.border, alignItems: "center", justifyContent: "center", backgroundColor: colors.card },
  radioOuterActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  radioInner: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primaryFg },
  radioLabel: { fontFamily: fonts.regular, fontSize: 15, color: colors.foreground },

  toast: { position: "absolute", left: spacing.md, right: spacing.md, bottom: 32, backgroundColor: colors.foreground, borderRadius: radii.md, paddingHorizontal: spacing.md, paddingVertical: 12, ...shadows.md },
  toastText: { color: colors.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
