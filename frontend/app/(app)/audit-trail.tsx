import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, ActivityIndicator, RefreshControl } from "react-native";
import {
  Activity, ShieldCheck, LogIn, User, CalendarCheck, Pill, FlaskConical,
  Mail, Bell, UserCog, Building2, Server, AlertTriangle, EyeOff,
} from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

type AuditRow = {
  id: string;
  action?: string;
  category?: string;
  detail?: string;
  user_name?: string;
  role?: string;
  status?: string;
  created_at?: string;
  subject_label?: string;
  deidentified?: boolean;
};

// Date-range presets → an inclusive `from` bound (YYYY-MM-DD). "All" sends none.
const RANGES: { key: string; label: string; days: number | null }[] = [
  { key: "7", label: "7 days", days: 7 },
  { key: "30", label: "30 days", days: 30 },
  { key: "all", label: "All time", days: null },
];

const ICONS: Record<string, any> = {
  auth: LogIn, login: LogIn, patient: User, visit: CalendarCheck,
  visit_instance: CalendarCheck, schedule: CalendarCheck, medication: Pill,
  dose: Pill, trial: FlaskConical, invitation: Mail, notifications: Bell,
  account: UserCog, contact: UserCog, organization: Building2, system: Server,
};
const TONES: Record<string, string> = {
  auth: colors.info, login: colors.info, patient: colors.primary,
  visit: colors.violet, schedule: colors.violet, medication: colors.success,
  dose: colors.success, trial: colors.accent, invitation: colors.info,
  notifications: colors.warning, account: colors.violet, contact: colors.violet,
  organization: colors.info, system: colors.mutedFg,
};

function fmtDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function fromBound(days: number | null): string | undefined {
  if (days == null) return undefined;
  const d = new Date();
  d.setDate(d.getDate() - (days - 1));
  return d.toISOString().slice(0, 10);
}

export default function AuditTrail() {
  const [items, setItems] = useState<AuditRow[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [category, setCategory] = useState<string | null>(null);
  const [range, setRange] = useState<string>("30");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(false);
    try {
      const params: Record<string, string> = {};
      if (category) params.category = category;
      const days = RANGES.find(r => r.key === range)?.days ?? null;
      const fb = fromBound(days);
      if (fb) params.from = fb;
      const r = await api.get("/audit-logs", { params });
      const rows: AuditRow[] = Array.isArray(r.data) ? r.data : [];
      setItems(rows);
      // Seed the category chip set once from the broadest result we've seen so
      // chips don't vanish when a narrow filter is applied.
      setCats(prev => {
        const merged = new Set(prev);
        rows.forEach(x => x.category && merged.add(x.category));
        return Array.from(merged).sort();
      });
    } catch {
      setError(true);
    } finally {
      if (isRefresh) setRefreshing(false);
      else setLoading(false);
    }
  }, [category, range]);

  useEffect(() => { load(); }, [load]);

  const chips = useMemo(() => ["all", ...cats], [cats]);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Compliance" title="Audit Trail" />

      {/* Category filter chips */}
      <View style={{ paddingTop: spacing.sm }}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: spacing.md, gap: 8 }}>
          {chips.map(c => {
            const active = c === "all" ? category === null : category === c;
            return (
              <Pressable key={c} testID={`cat-${c}`}
                onPress={() => setCategory(c === "all" ? null : c)}
                style={[s.chip, active && s.chipActive]}>
                <Body weight="600" style={{ fontSize: 13 }}
                  color={active ? colors.primaryFg : colors.mutedFg}>
                  {c === "all" ? "All" : c}
                </Body>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {/* Date range segmented control */}
      <View style={s.rangeRow}>
        {RANGES.map(r => {
          const active = range === r.key;
          return (
            <Pressable key={r.key} testID={`range-${r.key}`} onPress={() => setRange(r.key)}
              style={[s.seg, active && s.segActive]}>
              <Small color={active ? colors.foreground : colors.mutedFg}
                style={{ fontFamily: active ? fonts.semibold : fonts.regular }}>
                {r.label}
              </Small>
            </Pressable>
          );
        })}
      </View>

      <ScrollView
        contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor={colors.primary} />}>

        {loading ? (
          <View style={s.center}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg} style={{ marginTop: 10 }}>Loading activity…</Small>
          </View>
        ) : error ? (
          <Card>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <AlertTriangle size={18} color={colors.warning} />
              <View style={{ flex: 1 }}>
                <Body weight="600">Couldn’t load the audit trail</Body>
                <Small style={{ marginTop: 2 }}>Check your connection and try again.</Small>
              </View>
              <Pressable testID="audit-retry" onPress={() => load()} style={s.retry}>
                <Small color={colors.primary} style={{ fontFamily: fonts.semibold }}>Retry</Small>
              </Pressable>
            </View>
          </Card>
        ) : items.length === 0 ? (
          <Card>
            <View style={{ alignItems: "center", paddingVertical: 20, gap: 8 }}>
              <Activity size={28} color={colors.mutedFg} />
              <Body weight="600">No activity to show</Body>
              <Small style={{ textAlign: "center" }}>
                Nothing matches this filter yet. Adjust the category or date range.
              </Small>
            </View>
          </Card>
        ) : (
          items.map(row => {
            const cat = row.category || "system";
            const Icon = ICONS[cat] || Activity;
            const tone = TONES[cat] || colors.mutedFg;
            const failed = row.status === "failure";
            const actor = row.user_name?.trim() || (row.role ? row.role.toUpperCase() : "System");
            return (
              <View key={row.id} testID={`audit-${row.id}`} style={{ marginBottom: spacing.sm }}>
              <Card>
                <View style={{ flexDirection: "row", gap: 12 }}>
                  <View style={[s.icon, { backgroundColor: tone + "1A" }]}>
                    <Icon size={18} color={tone} />
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                      <Body weight="700" style={{ flex: 1 }}>{row.action || cat}</Body>
                      <View style={[s.badge, { backgroundColor: (failed ? colors.destructive : colors.success) + "1A" }]}>
                        <Small color={failed ? colors.destructive : colors.success}
                          style={{ fontSize: 11, fontFamily: fonts.semibold }}>
                          {failed ? "Failed" : "OK"}
                        </Small>
                      </View>
                    </View>
                    {row.detail ? <Small style={{ marginTop: 2 }}>{row.detail}</Small> : null}
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                      <Small color={colors.mutedFg}>{actor}</Small>
                      <Small color={colors.mutedFg}>· {fmtDate(row.created_at)}</Small>
                      {row.subject_label ? (
                        <View style={s.subj}><Small color={colors.info} style={{ fontSize: 11 }}>{row.subject_label}</Small></View>
                      ) : null}
                      {row.deidentified ? (
                        <View style={s.deid}>
                          <EyeOff size={11} color={colors.mutedFg} />
                          <Small color={colors.mutedFg} style={{ fontSize: 11 }}>De-identified</Small>
                        </View>
                      ) : null}
                    </View>
                  </View>
                </View>
              </Card>
              </View>
            );
          })
        )}

        {!loading && !error && items.length > 0 ? (
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: spacing.md }}>
            <ShieldCheck size={13} color={colors.mutedFg} />
            <Small color={colors.mutedFg} style={{ fontSize: 11 }}>Showing only records you’re authorized to view</Small>
          </View>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  chip: { paddingHorizontal: 14, paddingVertical: 7, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  rangeRow: { flexDirection: "row", gap: 6, paddingHorizontal: spacing.md, paddingTop: spacing.sm, paddingBottom: 4 },
  seg: { flex: 1, alignItems: "center", paddingVertical: 8, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  segActive: { backgroundColor: colors.secondary, borderColor: colors.primary + "55" },
  center: { alignItems: "center", paddingVertical: 48 },
  icon: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  badge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: radii.pill },
  retry: { paddingHorizontal: 10, paddingVertical: 6 },
  subj: { backgroundColor: colors.info + "1A", paddingHorizontal: 8, paddingVertical: 2, borderRadius: radii.pill },
  deid: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.surface, paddingHorizontal: 8, paddingVertical: 2, borderRadius: radii.pill },
});
