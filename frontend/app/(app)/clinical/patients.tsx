import React, { useCallback, useEffect, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, StatusBar, Text, ActivityIndicator, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Search, Bell, UserPlus, Menu } from "lucide-react-native";
import { api } from "@/src/api/client";
import { PiBottomNav } from "@/src/features/clinical/components/PiBottomNav";
import { useAuth } from "@/src/auth/AuthContext";
import { useUnreadCount } from "@/src/hooks/use-unread-count";

const C = {
  bg: "#F4E5D3", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FFFFFF", secondary: "#F0D7DC",
  accent: "#E69B5C", info: "#7B6BB8", success: "#5C9A6E", destructive: "#C0392B",
};

// Derived per-patient status — computed by the backend from visit instances.
type DerivedStatus = "active" | "overdue" | "completed" | "no_visits";

type NextVisit = { id: string; name: string; seq: number; scheduled_date: string; status: string };
type PatientRow = {
  id: string;
  full_name: string;
  avatar_initials?: string;
  subject_id?: string;
  age?: number;
  trial?: any;
  status: DerivedStatus;
  next_visit: NextVisit | null;
};

const STATUS_META: Record<DerivedStatus, { label: string; bg: string; fg: string }> = {
  active: { label: "Active", bg: "rgba(230,155,92,0.12)", fg: C.accent },
  overdue: { label: "⚠ Overdue", bg: "rgba(192,57,43,0.12)", fg: C.destructive },
  completed: { label: "Completed", bg: "rgba(92,154,110,0.14)", fg: C.success },
  no_visits: { label: "No visits", bg: "rgba(123,95,115,0.10)", fg: C.muted },
};

const FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "overdue", label: "Overdue" },
  { id: "completed", label: "Completed" },
];

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function nextVisitLabel(nv: NextVisit | null): string {
  if (!nv) return "No upcoming visits";
  const date = fmtDate(nv.scheduled_date);
  return date ? `${nv.name} · ${date}` : nv.name;
}

export default function PatientList() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string>("all");
  const [rows, setRows] = useState<PatientRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [quickMenuOpen, setQuickMenuOpen] = useState(false);
  const quickMenuProgress = React.useRef(new Animated.Value(0)).current;

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [r, trialsResponse] = await Promise.all([api.get("/patients"), api.get("/trials")]);
      const trialById = Object.fromEntries((trialsResponse.data || []).map((trial: any) => [trial.id, trial]));
      const mapped: PatientRow[] = (r.data || []).map((p: any) => ({
        id: p.id,
        full_name: p.full_name || "Unknown",
        avatar_initials: p.avatar_initials,
        subject_id: p.subject_id,
        age: p.age,
        trial: trialById[p.trial_id],
        status: (p.status as DerivedStatus) || "no_visits",
        next_visit: p.next_visit || null,
      }));
      setRows(mapped);
    } catch {
      setError("Couldn't load patients. Tap Retry to try again.");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const counts = {
    all: rows.length,
    active: rows.filter(r => r.status === "active").length,
    overdue: rows.filter(r => r.status === "overdue").length,
    completed: rows.filter(r => r.status === "completed").length,
  };

  const filtered = rows
    .filter(r => active === "all" || r.status === active)
    .filter(r => q === "" || r.full_name.toLowerCase().includes(q.toLowerCase()));
  const isCrc = user?.role === "crc";
  const navRole = isCrc ? "crc" : user?.role === "site" ? "site" : user?.role === "smo" ? "smo" : "pi";
  const initials = user?.avatar_initials
    || (user?.full_name || (isCrc ? "CRC" : "PI"))
      .split(/\s+/)
      .filter(Boolean)
      .map(part => part[0])
      .slice(0, 2)
      .join("")
      .toUpperCase();
  const toggleQuickMenu = () => {
    const nextOpen = !quickMenuOpen;
    setQuickMenuOpen(nextOpen);
    Animated.timing(quickMenuProgress, {
      toValue: nextOpen ? 1 : 0,
      duration: 220,
      easing: nextOpen ? Easing.out(Easing.back(1.15)) : Easing.in(Easing.cubic),
      useNativeDriver: true,
    }).start();
  };
  const openQuickAction = (path: "/(app)/clinical/add-patient") => {
    setQuickMenuOpen(false);
    quickMenuProgress.setValue(0);
    router.push(path);
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.surface }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.headerSafeArea}>
        <View style={s.header}>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={s.headerEyebrow}>{isCrc ? "RESEARCH TEAM · CRC" : "PRINCIPAL INVESTIGATOR"}</Text>
            <Text style={s.headerTitle}>Patients</Text>
          </View>
          <Pressable
            testID="patients-bell"
            accessibilityLabel="Notifications"
            onPress={() => router.push("/(app)/notifications")}
            style={s.headerIcon}
          >
            <Bell size={18} color={C.primaryFg} />
            {(unread ?? 0) > 0 && (
              <View style={s.unreadBadge}>
                <Text style={s.unreadText}>{Math.min(unread ?? 0, 9)}</Text>
              </View>
            )}
          </Pressable>
          <Pressable
            testID="patients-avatar"
            accessibilityLabel="Profile"
            onPress={() => router.push("/(app)/clinical/profile")}
            style={s.headerAvatar}
          >
            <Text style={s.headerAvatarText}>{initials || "?"}</Text>
          </Pressable>
        </View>
      </SafeAreaView>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 110 }} showsVerticalScrollIndicator={false}>
        {/* Search */}
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          <View style={s.search}>
            <Search size={20} color={C.muted} />
            <TextInput
              testID="patient-search"
              value={q}
              onChangeText={setQ}
              placeholder="Search by name..."
              placeholderTextColor={C.muted}
              style={{ flex: 1, color: C.fg, fontSize: 15 }}
            />
          </View>
        </View>

        {/* Filter chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, gap: 8 }}>
          {FILTERS.map(f => {
            const on = active === f.id;
            const count = (counts as any)[f.id];
            return (
              <Pressable key={f.id} testID={`filter-${f.id}`} onPress={() => setActive(f.id)} style={[s.chip, on ? s.chipActive : s.chipIdle, { flexShrink: 0 }]}>
                <Text style={[s.chipText, { color: on ? C.primaryFg : C.muted }]}>{f.label} {count}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        {/* Patient list */}
        <View style={{ paddingHorizontal: 16 }}>
          {loading ? (
            <View style={{ padding: 40, alignItems: "center" }}>
              <ActivityIndicator color={C.primary} />
            </View>
          ) : error ? (
            <View style={{ padding: 24, alignItems: "center", gap: 12 }}>
              <Text style={{ color: C.destructive, textAlign: "center" }}>{error}</Text>
              <Pressable testID="patients-retry" onPress={load} style={s.retryBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 14 }}>Retry</Text>
              </Pressable>
            </View>
          ) : (
            <View style={s.listCard}>
              {filtered.map((p, i) => {
                const meta = STATUS_META[p.status];
                return (
                  <Pressable
                    key={p.id}
                    testID={`patient-${p.id}`}
                    onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.id } })}
                    style={s.subjectCard}
                  >
                    <View style={s.subjectTop}>
                      <View style={s.avatar}><Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{p.avatar_initials || p.full_name.slice(0, 2).toUpperCase()}</Text></View>
                      <View style={{ flex: 1, minWidth: 0 }}><Text style={{ color: C.fg, fontSize: 15, fontWeight: "700" }} numberOfLines={1}>{p.subject_id || `SUBJ-${String(p.id).slice(-3)}`}</Text><Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }} numberOfLines={1}>{p.avatar_initials || p.full_name.slice(0, 2).toUpperCase()}{p.age ? ` · Age ${p.age}` : ""}</Text></View>
                      <View style={[s.statusPill, { backgroundColor: meta.bg }]}><Text style={{ color: meta.fg, fontSize: 11, fontWeight: "600" }}>{meta.label}</Text></View>
                    </View>
                    <View style={s.trialMeta}>{[{ label: "PROTOCOL ID", value: p.trial?.protocol_id || "—" }, { label: "PHASE", value: p.trial?.phase || "—" }, { label: "INDICATION", value: p.trial?.condition || "—" }].map(field => <View key={field.label} style={{ flex: 1, minWidth: 0 }}><Text style={s.metaLabel}>{field.label}</Text><Text style={s.metaValue} numberOfLines={1}>{field.value}</Text></View>)}</View>
                    <View style={s.nextVisit}><Text style={s.metaLabel}>NEXT VISIT</Text><View style={s.nextGrid}>{[{ label: "VISIT NO.", value: p.next_visit ? `Visit ${p.next_visit.seq}` : "—" }, { label: "VISIT NAME", value: p.next_visit?.name || "Not scheduled" }, { label: "STATUS", value: p.next_visit?.status || "—" }, { label: "VISIT DATE", value: fmtDate(p.next_visit?.scheduled_date) || "—" }].map(field => <View key={field.label} style={{ width: "50%" }}><Text style={s.metaLabel}>{field.label}</Text><Text style={s.nextValue} numberOfLines={1}>{field.value}</Text></View>)}</View></View>
                    <View style={s.subjectFooter}><Text style={s.metaLabel}>VISIT STATUS</Text><Text style={{ color: C.primary, fontSize: 12, fontWeight: "700" }}>{nextVisitLabel(p.next_visit)}</Text></View>
                  </Pressable>
                );
              })}
              {filtered.length === 0 && (
                <View style={{ padding: 24, alignItems: "center" }}>
                  <Text style={{ color: C.muted }}>
                    {rows.length === 0 ? "No patients enrolled yet" : "No patients match your filters"}
                  </Text>
                </View>
              )}
            </View>
          )}
        </View>
      </ScrollView>

      <PiBottomNav
        active={isCrc ? "patients" : "dashboard"}
        calendarRole={navRole}
        role={navRole}
      />

      <View pointerEvents="box-none" style={s.quickMenuRoot}>
        <Animated.View
          pointerEvents={quickMenuOpen ? "auto" : "none"}
          style={[
            s.quickActionStack,
            {
              opacity: quickMenuProgress,
              transform: [{
                translateY: quickMenuProgress.interpolate({
                  inputRange: [0, 1],
                  outputRange: [24, 0],
                }),
              }, {
                scale: quickMenuProgress.interpolate({
                  inputRange: [0, 1],
                  outputRange: [0.92, 1],
                }),
              }],
            },
          ]}
        >
          <Pressable
            testID="patients-add"
            accessibilityRole="button"
            onPress={() => openQuickAction("/(app)/clinical/add-patient")}
            style={({ pressed }) => [s.quickAction, pressed && s.quickActionPressed]}
          >
            <View style={s.quickActionIcon}><UserPlus size={17} color={C.primary} /></View>
            <Text style={s.quickActionText}>Add Patient</Text>
          </Pressable>
        </Animated.View>

        <Pressable
          testID="patients-quick-menu"
          accessibilityRole="button"
          accessibilityLabel={quickMenuOpen ? "Close patient actions" : "Open patient actions"}
          accessibilityState={{ expanded: quickMenuOpen }}
          onPress={toggleQuickMenu}
          style={({ pressed }) => [s.quickMenuButton, pressed && { transform: [{ scale: 0.94 }] }]}
        >
          <Animated.View style={{
            transform: [{
              rotate: quickMenuProgress.interpolate({
                inputRange: [0, 1],
                outputRange: ["0deg", "90deg"],
              }),
            }],
          }}>
            <Menu size={25} color={C.primaryFg} strokeWidth={2.4} />
          </Animated.View>
        </Pressable>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  headerSafeArea: { backgroundColor: C.primaryDeep },
  header: { minHeight: 64, flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingVertical: 10, backgroundColor: C.primaryDeep },
  headerEyebrow: { color: "rgba(255,255,255,0.58)", fontSize: 9, fontWeight: "800", letterSpacing: 1.1 },
  headerTitle: { color: C.primaryFg, fontSize: 19, lineHeight: 24, fontWeight: "700", marginTop: 2 },
  headerIcon: { position: "relative", width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.12)" },
  headerAvatar: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.18)" },
  headerAvatarText: { color: C.primaryFg, fontSize: 12, fontWeight: "800" },
  unreadBadge: { position: "absolute", right: -1, top: -3, minWidth: 16, height: 16, borderRadius: 8, paddingHorizontal: 4, alignItems: "center", justifyContent: "center", backgroundColor: C.destructive, borderWidth: 1.5, borderColor: C.primaryDeep },
  unreadText: { color: C.primaryFg, fontSize: 9, fontWeight: "800" },
  search: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 999, backgroundColor: C.border },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  chipIdle: { backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  chipActive: { backgroundColor: C.primary },
  chipText: { fontSize: 13, fontWeight: "600" },
  listCard: { gap: 12 },
  row: { padding: 14, flexDirection: "row", alignItems: "center", gap: 12 },
  subjectCard: { padding: 14, borderWidth: 1, borderColor: C.border, borderRadius: 20, backgroundColor: C.card, gap: 12, shadowColor: "#3f1d2e", shadowOpacity: 0.07, shadowRadius: 8, elevation: 2 },
  subjectTop: { flexDirection: "row", alignItems: "center", gap: 10 },
  trialMeta: { flexDirection: "row", gap: 8, paddingTop: 11, borderTopWidth: 1, borderTopColor: C.border },
  metaLabel: { color: C.muted, opacity: 0.7, fontSize: 9, fontWeight: "700", letterSpacing: 0.7 },
  metaValue: { color: C.fg, fontSize: 11, fontWeight: "700", marginTop: 3 },
  nextVisit: { padding: 11, borderRadius: 14, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border },
  nextGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: 10, marginTop: 8 },
  nextValue: { color: C.fg, fontSize: 12, fontWeight: "700", marginTop: 3 },
  subjectFooter: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999 },
  retryBtn: { paddingHorizontal: 20, paddingVertical: 10, borderRadius: 24, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
  quickMenuRoot: { position: "absolute", right: 16, bottom: 86, alignItems: "flex-end" },
  quickActionStack: { alignItems: "flex-end", gap: 9, marginBottom: 10 },
  quickAction: { minWidth: 158, height: 46, borderRadius: 16, flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 10, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.16, shadowRadius: 10, shadowOffset: { width: 0, height: 4 }, elevation: 7 },
  quickActionPressed: { opacity: 0.78, transform: [{ scale: 0.98 }] },
  quickActionIcon: { width: 30, height: 30, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: C.secondary },
  quickActionText: { color: C.fg, fontSize: 13, fontWeight: "700" },
  quickMenuButton: { width: 54, height: 54, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: C.primary, shadowColor: "#2E1B33", shadowOpacity: 0.26, shadowRadius: 12, shadowOffset: { width: 0, height: 5 }, elevation: 10 },
});
