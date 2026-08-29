import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, Text, ScrollView, Pressable, StyleSheet, StatusBar, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  ChevronLeft, ChevronRight, RotateCw, Settings, Building2,
  CalendarDays, Clock, Stethoscope, AlertTriangle, Users,
} from "lucide-react-native";
import { colors, spacing, radii, fonts, shadows, dawnGradient } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { PiBottomNav } from "@/src/features/clinical/components/PiBottomNav";

// ── Date helpers (tz-safe, mirrors patient/calendar) ─────────────────────────
// API dates are UTC ISO (midnight-anchored). We compare everything by a
// YYYY-MM-DD string: calendar cells are built from LOCAL Date objects (no tz
// shift on construction) so we read them with local getters, while a visit's
// scheduled_date is read with UTC getters — both land on the intended day.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");
const ymdLocal = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const ymdUTC = (d: Date) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const addMonths = (d: Date, n: number) => { const x = new Date(d); x.setMonth(x.getMonth() + n); return x; };
const firstOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1);
const lastOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth() + 1, 0);
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const startOfWeek = (d: Date) => addDays(startOfDay(d), -d.getDay());
// Some backend paths (legacy fallback) emit tz-NAIVE ISO like "2025-05-19T00:00:00".
// JS parses those as LOCAL time, so UTC getters shift the day back in positive-offset
// zones (IST midnight → previous day). If the string carries no tz designator (no 'Z'
// and no ±HH:MM offset), treat it as UTC by appending 'Z' before parsing.
const parseISO = (iso: string) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);

type Visit = {
  id: string; name?: string; visit_number?: number; seq?: number;
  patient_id?: string; trial_id?: string;
  scheduled_date?: string; status?: string;
  window_start?: string; window_end?: string;
  patient_initials?: string; subject_label?: string;
  protocol_id?: string; condition?: string;
  site?: string; pi_name?: string;
};

type Role = "pi" | "crc" | "smo" | "site";
type ViewMode = "day" | "week" | "month";

// ── Status tone (token colours only) ────────────────────────────────────────
function statusMeta(status?: string): { color: string; label: string } {
  switch (status) {
    case "completed": return { color: colors.success, label: "Completed" };
    case "upcoming": return { color: colors.accent, label: "Upcoming" };
    case "missed": return { color: colors.destructive, label: "Missed" };
    case "overdue": return { color: colors.destructive, label: "Overdue" };
    default: return { color: colors.info, label: "Scheduled" }; // scheduled
  }
}
// Dominant status for a day → drives the date-cell colour.
function dayStatusOf(visits: Visit[]): string | null {
  if (!visits.length) return null;
  if (visits.some(v => v.status === "missed" || v.status === "overdue")) return "missed";
  if (visits.some(v => v.status === "upcoming")) return "upcoming";
  if (visits.some(v => v.status === "scheduled")) return "scheduled";
  return "completed";
}

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

export default function TeamCalendar() {
  const router = useRouter();
  const params = useLocalSearchParams<{ role?: string; initialView?: string }>();
  const role: Role = params.role === "crc"
    ? "crc"
    : params.role === "site"
      ? "site"
      : params.role === "smo"
        ? "smo"
        : "pi";
  const initialView: ViewMode =
    params.initialView === "day" || params.initialView === "week" ? params.initialView : "month";

  const [visits, setVisits] = useState<Visit[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<ViewMode>(initialView);

  const today = useMemo(() => startOfDay(new Date()), []);
  const [selected, setSelected] = useState<Date>(today);
  const [month, setMonth] = useState<Date>(firstOfMonth(today));

  // The site schedule is a ranged endpoint (span capped at ~100 days), so we
  // load a ±1-month window around whichever period is in view and only refetch
  // when the anchor month changes (day/week stepping within a month is free).
  const anchor = view === "month" ? month : selected;
  const anchorMonthStart = firstOfMonth(anchor);
  const anchorKey = anchorMonthStart.getTime();

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    const anchorStart = new Date(anchorKey);
    const from = firstOfMonth(addMonths(anchorStart, -1));
    const to = lastOfMonth(addMonths(anchorStart, 1));
    try {
      const r = await api.get("/calendar/team", {
        params: { from: ymdLocal(from), to: ymdLocal(to) },
      });
      setVisits(Array.isArray(r.data) ? r.data : []);
    } catch {
      setVisits([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [anchorKey]);
  useEffect(() => { load(); }, [load]);

  // Group visits by day-key (UTC).
  const byDay = useMemo(() => {
    const m: Record<string, Visit[]> = {};
    for (const v of visits) {
      if (!v?.scheduled_date) continue;
      const key = ymdUTC(parseISO(v.scheduled_date));
      (m[key] ||= []).push(v);
    }
    return m;
  }, [visits]);
  const visitsOn = (d: Date) => byDay[ymdLocal(d)] || [];

  const todayKey = ymdLocal(today);

  // ── Month grid ──
  const gridStart = firstOfMonth(month).getDay();
  const daysInMonth = lastOfMonth(month).getDate();
  const cells: (number | null)[] = Array(gridStart).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  const weekRows: (number | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weekRows.push(cells.slice(i, i + 7));

  // ── Week strip ──
  const weekStart = startOfWeek(selected);
  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const weekVisitCount = weekDays.reduce((sum, d) => sum + visitsOn(d).length, 0);
  const missedThisWeek = weekDays.reduce(
    (sum, d) => sum + visitsOn(d).filter(v => v.status === "missed" || v.status === "overdue").length, 0);

  const selectedVisits = visitsOn(selected);

  // Visits within the period currently in view (drives the contextual subtitle).
  const periodVisitCount =
    view === "month"
      ? visits.filter(v => v.scheduled_date && ymdUTC(parseISO(v.scheduled_date)).startsWith(
          `${month.getFullYear()}-${pad(month.getMonth() + 1)}`)).length
      : view === "week" ? weekVisitCount
      : selectedVisits.length;

  // ── Period navigation, contextual to the active view ──
  const periodLabel =
    view === "month" ? month.toLocaleString("en-US", { month: "long", year: "numeric" })
      : view === "week" ? `${weekDays[0].getDate()}–${weekDays[6].getDate()} ${weekDays[6].toLocaleString("en-US", { month: "long", year: "numeric" })}`
      : selected.toLocaleString("en-US", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  const goPrev = () => view === "month" ? setMonth(addMonths(month, -1)) : setSelected(addDays(selected, view === "week" ? -7 : -1));
  const goNext = () => view === "month" ? setMonth(addMonths(month, 1)) : setSelected(addDays(selected, view === "week" ? 7 : 1));
  const jumpToday = () => { setSelected(today); setMonth(firstOfMonth(today)); };

  const formatFullDay = (d: Date) => d.toLocaleString("en-US", { weekday: "long", day: "numeric", month: "long" });

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />

      {/* App bar */}
      <View style={s.appbar}>
        <SafeAreaView edges={["top"]}>
          <View style={s.appbarRow}>
            <Pressable testID="team-cal-back" onPress={() => router.back()} hitSlop={10} style={s.appbarBtn}>
              <ChevronLeft size={22} color={colors.primaryFg} />
            </Pressable>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.eyebrowLight}>SITE SCHEDULE</Text>
              <Text style={s.appbarTitle}>Team Calendar</Text>
            </View>
            <Pressable testID="team-cal-refresh" onPress={() => load(true)} disabled={refreshing} hitSlop={10} style={s.appbarBtn}>
              {refreshing ? <ActivityIndicator size="small" color={colors.primaryFg} /> : <RotateCw size={20} color={colors.primaryFg} />}
            </Pressable>
            <Pressable testID="team-cal-settings" onPress={() => router.push("/(app)/clinical/calendar-settings" as any)} hitSlop={10} style={s.appbarBtn}>
              <Settings size={20} color={colors.primaryFg} />
            </Pressable>
          </View>
          <View style={s.roleRow}>
            <Users size={13} color={colors.overlay25} />
            <Text style={s.roleText}>
              {role === "pi" ? "PI" : role === "crc" ? "CRC" : role === "site" ? "SITE" : "SMO"} · All patients
            </Text>
          </View>
        </SafeAreaView>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 40 }} showsVerticalScrollIndicator={false}>
        {/* Period nav + segmented control */}
        <View style={s.controls}>
          <View style={s.periodRow}>
            <Pressable testID="team-cal-prev" onPress={goPrev} style={s.navBtn}><ChevronLeft size={20} color={colors.foreground} /></Pressable>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.periodLabel} numberOfLines={1}>{periodLabel}</Text>
              <Text style={s.periodSub}>{periodVisitCount} visit{periodVisitCount === 1 ? "" : "s"} in view</Text>
            </View>
            <Pressable testID="team-cal-next" onPress={goNext} style={s.navBtn}><ChevronRight size={20} color={colors.foreground} /></Pressable>
          </View>

          <View style={s.segment}>
            {(["day", "week", "month"] as const).map(mode => {
              const active = view === mode;
              const inner = <Text style={[s.segmentText, active && s.segmentTextActive]}>{mode[0].toUpperCase() + mode.slice(1)}</Text>;
              return (
                <Pressable key={mode} testID={`team-cal-view-${mode}`} onPress={() => { setView(mode); if (mode === "day") setSelected(today); }} style={s.segmentBtn}>
                  {active
                    ? <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.segmentActive}>{inner}</LinearGradient>
                    : inner}
                </Pressable>
              );
            })}
          </View>

          <Pressable testID="team-cal-today" onPress={jumpToday} style={s.todayBtn}>
            <CalendarDays size={14} color={colors.primary} />
            <Text style={s.todayText}>Today</Text>
          </Pressable>
        </View>

        {loading ? (
          <View style={s.loadingWrap}><ActivityIndicator color={colors.primary} /></View>
        ) : (
          <>
            {/* MONTH VIEW */}
            {view === "month" && (
              <>
                <View style={s.monthCard}>
                  <View style={s.weekHeaderRow}>
                    {WEEKDAYS.map((d, i) => <Text key={i} style={s.weekHeaderCell}>{d}</Text>)}
                  </View>
                  {weekRows.map((row, wi) => (
                    <View key={wi} style={s.gridRow}>
                      {row.map((day, di) => {
                        if (!day) return <View key={di} style={s.cell} />;
                        const cellDate = new Date(month.getFullYear(), month.getMonth(), day);
                        const v = visitsOn(cellDate);
                        const ds = dayStatusOf(v);
                        const isToday = ymdLocal(cellDate) === todayKey;
                        const isSelected = ymdLocal(cellDate) === ymdLocal(selected);
                        const meta = ds ? statusMeta(ds) : null;
                        return (
                          <Pressable key={di} testID={`team-cal-day-${day}`} onPress={() => setSelected(cellDate)}
                            style={[
                              s.cell,
                              meta && { backgroundColor: meta.color + "26" },
                              isSelected ? s.cellSelected : (!ds && isToday ? s.cellToday : null),
                            ]}>
                            <Text style={[
                              s.cellText,
                              meta ? { color: meta.color, fontFamily: fonts.semibold }
                                : isToday ? { color: colors.info, fontFamily: fonts.bold }
                                : { color: colors.foreground },
                            ]}>{day}</Text>
                            {v.length > 0 && (
                              <View style={s.dotRow}>
                                {v.slice(0, 3).map((vv, vi) => (
                                  <View key={vi} style={[s.cellDot, { backgroundColor: statusMeta(vv.status).color }]} />
                                ))}
                              </View>
                            )}
                          </Pressable>
                        );
                      })}
                    </View>
                  ))}
                  <Legend />
                </View>

                <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} role={role} emptyText="No visits on this day" />
              </>
            )}

            {/* WEEK VIEW */}
            {view === "week" && (
              <>
                <View style={s.weekStripCard}>
                  <View style={s.gridRow}>
                    {weekDays.map((d, i) => {
                      const v = visitsOn(d);
                      const ds = dayStatusOf(v);
                      const isToday = ymdLocal(d) === todayKey;
                      const isSelected = ymdLocal(d) === ymdLocal(selected);
                      const meta = ds ? statusMeta(ds) : null;
                      const chipInner = (
                        <>
                          <Text style={[
                            s.weekChipText,
                            isSelected ? { color: colors.primaryFg } : meta ? { color: meta.color } : { color: colors.foreground },
                          ]}>{d.getDate()}</Text>
                          {!isSelected && v.length > 0 && (
                            <View style={s.weekDotRow}>
                              {v.slice(0, 3).map((vv, vi) => (
                                <View key={vi} style={[s.weekDot, { backgroundColor: statusMeta(vv.status).color }]} />
                              ))}
                            </View>
                          )}
                        </>
                      );
                      return (
                        <Pressable key={i} testID={`team-cal-weekday-${i}`} onPress={() => setSelected(d)} style={s.weekDayBtn}>
                          <Text style={s.weekDayName}>{WEEKDAYS[d.getDay()]}</Text>
                          {isSelected
                            ? <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.weekChip}>{chipInner}</LinearGradient>
                            : <View style={[s.weekChip, meta && { backgroundColor: meta.color + "26" }, !meta && isToday && s.cellToday]}>{chipInner}</View>}
                        </Pressable>
                      );
                    })}
                  </View>
                  <Legend />
                </View>

                <View style={s.weekOverview}>
                  <Text style={s.weekOverviewText}>
                    This week: {weekVisitCount} visit{weekVisitCount !== 1 ? "s" : ""}
                    {missedThisWeek > 0 ? ` · ${missedThisWeek} missed` : ""}
                  </Text>
                </View>

                <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} role={role} emptyText="No visits on this day" />
              </>
            )}

            {/* DAY VIEW */}
            {view === "day" && (
              <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} role={role} emptyText="No visits scheduled this day" />
            )}
          </>
        )}
      </ScrollView>
      <PiBottomNav
        active="calendar"
        calendarRole={role}
        role={role}
      />
    </View>
  );
}

// ── Selected-day header + visit list ────────────────────────────────────────
function SelectedDaySection({ title, visits, role, emptyText }: {
  title: string; visits: Visit[]; role: Role; emptyText: string;
}) {
  return (
    <View style={{ paddingHorizontal: spacing.md, paddingVertical: spacing.md }}>
      <View style={s.sectionHead}>
        <View style={{ minWidth: 0, flex: 1 }}>
          <Text style={s.sectionEyebrow}>SELECTED DAY</Text>
          <Text style={s.sectionTitle}>{title}</Text>
        </View>
        {visits.length > 0 && (
          <View style={s.countPill}>
            <Text style={s.countPillText}>{visits.length} visit{visits.length > 1 ? "s" : ""}</Text>
          </View>
        )}
      </View>
      {visits.length === 0 ? (
        <View style={s.emptyDay}>
          <CalendarDays size={32} color={colors.mutedFg + "66"} />
          <Text style={s.emptyText}>{emptyText}</Text>
        </View>
      ) : (
        <View style={{ gap: spacing.sm }}>
          {visits.map((v, i) => <VisitCard key={v.id || i} v={v} role={role} />)}
        </View>
      )}
    </View>
  );
}

// ── Visit card — site-wide, privacy-safe (initials + subject label only) ─────
function VisitCard({ v, role }: { v: Visit; role: Role }) {
  const meta = statusMeta(v.status);
  const upcoming = v.status === "upcoming";
  const d = v.scheduled_date ? parseISO(v.scheduled_date) : null;
  const initials = v.patient_initials || "P";
  const subject = v.subject_label || "SUBJ-—";

  return (
    <View style={s.visitCard}>
      <View style={{ flexDirection: "row", gap: spacing.md }}>
        {/* date tear-block */}
        {upcoming ? (
          <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.dateBlock}>
            <Text style={s.dateBlockDay}>{d ? d.getUTCDate() : "–"}</Text>
            <Text style={s.dateBlockMon}>{d ? MONTHS[d.getUTCMonth()].toUpperCase() : ""}</Text>
          </LinearGradient>
        ) : (
          <View style={[s.dateBlock, { backgroundColor: meta.color }]}>
            <Text style={s.dateBlockDay}>{d ? d.getUTCDate() : "–"}</Text>
            <Text style={s.dateBlockMon}>{d ? MONTHS[d.getUTCMonth()].toUpperCase() : ""}</Text>
          </View>
        )}

        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 }}>
              <Clock size={13} color={colors.mutedFg} />
              <Text style={s.subjText} numberOfLines={1}>{initials} · {subject}</Text>
            </View>
            <View style={[s.statusChip, { backgroundColor: meta.color + "26" }]}>
              <Text style={[s.statusChipText, { color: meta.color }]}>{meta.label}</Text>
            </View>
          </View>

          {(!!v.protocol_id || !!v.condition) && (
            <Text style={s.protocolText} numberOfLines={1}>
              {[v.protocol_id, v.condition].filter(Boolean).join(" · ")}
            </Text>
          )}
          <Text style={s.visitName} numberOfLines={1}>
            {v.name || "Visit"}{v.visit_number != null ? ` · Visit ${v.visit_number}` : ""}
          </Text>

          {!!v.site && (
            <View style={s.metaRow}><Building2 size={13} color={colors.mutedFg} /><Text style={s.metaText} numberOfLines={1}>{v.site}</Text></View>
          )}
          {/* On the CRC view, surface which PI is attending the patient for this visit. */}
          {role === "crc" && !!v.pi_name && (
            <View style={s.metaRow}><Stethoscope size={13} color={colors.mutedFg} /><Text style={s.metaText} numberOfLines={1}>{v.pi_name}</Text></View>
          )}
        </View>
      </View>

      {(v.status === "missed" || v.status === "overdue") && (
        <View style={s.cardFooter}>
          <AlertTriangle size={13} color={colors.destructive} />
          <Text style={[s.footerText, { color: colors.destructive }]}>Overdue · Follow up with the patient</Text>
        </View>
      )}
    </View>
  );
}

// ── Legend ──
function Legend() {
  const items = [
    { color: colors.success, label: "Completed" },
    { color: colors.accent, label: "Upcoming" },
    { color: colors.destructive, label: "Missed" },
  ];
  return (
    <View style={s.legend}>
      {items.map(l => (
        <View key={l.label} style={{ flexDirection: "row", alignItems: "center", gap: 5 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: l.color }} />
          <Text style={s.legendText}>{l.label}</Text>
        </View>
      ))}
    </View>
  );
}

const s = StyleSheet.create({
  appbar: { backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.md, paddingBottom: 12 },
  appbarRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 6 },
  appbarBtn: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center" },
  eyebrowLight: { color: colors.overlay25, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4 },
  appbarTitle: { color: colors.primaryFg, fontFamily: fonts.display, fontSize: 20, letterSpacing: -0.4 },
  roleRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6, paddingLeft: 2 },
  roleText: { color: colors.overlay25, fontFamily: fonts.medium, fontSize: 11 },

  controls: { backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border, paddingHorizontal: spacing.md, paddingTop: spacing.md, paddingBottom: 14, gap: 12 },
  periodRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  navBtn: { width: 40, height: 40, borderRadius: 20, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  periodLabel: { textAlign: "center", color: colors.foreground, fontFamily: fonts.heading, fontSize: 18, letterSpacing: -0.2 },
  periodSub: { textAlign: "center", color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11, marginTop: 2 },

  segment: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: radii.pill, padding: 4 },
  segmentBtn: { flex: 1, borderRadius: radii.pill, overflow: "hidden" },
  segmentActive: { paddingVertical: 9, alignItems: "center", borderRadius: radii.pill, ...shadows.sm },
  segmentText: { textAlign: "center", paddingVertical: 9, fontFamily: fonts.semibold, fontSize: 14, color: colors.mutedFg },
  segmentTextActive: { color: colors.primaryFg, paddingVertical: 0 },

  todayBtn: { alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, paddingVertical: 4 },
  todayText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 13 },

  loadingWrap: { paddingVertical: 60, alignItems: "center", justifyContent: "center" },

  monthCard: { backgroundColor: colors.card, paddingHorizontal: spacing.md, paddingVertical: spacing.md },
  weekHeaderRow: { flexDirection: "row", marginBottom: 6 },
  weekHeaderCell: { flex: 1, textAlign: "center", fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg + "B3", paddingVertical: 2 },
  gridRow: { flexDirection: "row" },
  cell: { flex: 1, aspectRatio: 1, margin: 2, borderRadius: radii.lg, alignItems: "center", justifyContent: "center" },
  cellSelected: { borderWidth: 2, borderColor: colors.primary },
  cellToday: { borderWidth: 1, borderColor: colors.info },
  cellText: { fontSize: 14, fontFamily: fonts.medium, lineHeight: 16 },
  dotRow: { position: "absolute", bottom: 5, flexDirection: "row", gap: 2 },
  cellDot: { width: 4, height: 4, borderRadius: 2 },

  weekStripCard: { backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border, paddingHorizontal: spacing.md, paddingVertical: spacing.md },
  weekDayBtn: { flex: 1, alignItems: "center", gap: 6 },
  weekDayName: { fontSize: 11, color: colors.mutedFg + "B3", fontFamily: fonts.medium },
  weekChip: { width: 38, height: 38, borderRadius: radii.lg, alignItems: "center", justifyContent: "center" },
  weekChipText: { fontSize: 14, fontFamily: fonts.semibold },
  weekDotRow: { position: "absolute", bottom: 4, flexDirection: "row", gap: 2 },
  weekDot: { width: 4, height: 4, borderRadius: 2 },

  weekOverview: { backgroundColor: colors.surface, paddingHorizontal: spacing.md, paddingVertical: 10 },
  weekOverviewText: { textAlign: "center", color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12 },

  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm, marginBottom: 12 },
  sectionEyebrow: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4 },
  sectionTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 18, letterSpacing: -0.2, marginTop: 2 },
  countPill: { backgroundColor: colors.primary + "14", borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 4 },
  countPillText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },

  emptyDay: { alignItems: "center", gap: 8, paddingVertical: 40 },
  emptyText: { color: colors.mutedFg + "99", fontFamily: fonts.regular, fontSize: 14 },

  visitCard: { backgroundColor: colors.card, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, padding: spacing.md, ...shadows.sm },
  dateBlock: { paddingHorizontal: 14, paddingVertical: 12, borderRadius: radii.lg, alignItems: "center", justifyContent: "center", ...shadows.sm },
  dateBlockDay: { color: colors.primaryFg, fontFamily: fonts.display, fontSize: 24, lineHeight: 26 },
  dateBlockMon: { color: colors.primaryFg, opacity: 0.85, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, marginTop: 3 },
  subjText: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 13, flexShrink: 1 },
  statusChip: { borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 3 },
  statusChipText: { fontFamily: fonts.semibold, fontSize: 11 },
  protocolText: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, marginTop: 4 },
  visitName: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 16, lineHeight: 20, marginTop: 2 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 5 },
  metaText: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, flexShrink: 1 },
  cardFooter: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border },
  footerText: { fontFamily: fonts.semibold, fontSize: 12 },

  legend: { flexDirection: "row", justifyContent: "center", gap: 16, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border },
  legendText: { fontSize: 10, color: colors.mutedFg, fontFamily: fonts.regular },
});
