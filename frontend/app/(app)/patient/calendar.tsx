import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, ScrollView, Pressable, StyleSheet, StatusBar, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  ChevronLeft, ChevronRight, RotateCw, Settings, Building2, Check,
  CalendarDays, Clock, Stethoscope, AlertTriangle,
} from "lucide-react-native";
import { colors, spacing, radii, fonts, shadows, dawnGradient } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";

// ── Date helpers ────────────────────────────────────────────────────────────
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
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const startOfWeek = (d: Date) => addDays(startOfDay(d), -d.getDay());
const dateFromKey = (key: string) => { const [y, m, dd] = key.split("-").map(Number); return new Date(y, m - 1, dd); };
// Some backend paths (legacy fallback) emit tz-NAIVE ISO like "2025-05-19T00:00:00".
// JS parses those as LOCAL time, so UTC getters shift the day back in positive-offset
// zones (IST midnight → previous day). If the string carries no tz designator (no 'Z'
// and no ±HH:MM offset), treat it as UTC by appending 'Z' before parsing.
const parseISO = (iso: string) => new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
const fmtUTCShort = (iso?: string) => { if (!iso) return ""; const d = parseISO(iso); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

type Visit = {
  id: string; name?: string; visit_number?: number; seq?: number;
  scheduled_date?: string; status?: string;
  window_start?: string; window_end?: string;
  site?: string; pi_name?: string;
};

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

export default function PatientCalendar() {
  const router = useRouter();
  const [visits, setVisits] = useState<Visit[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [view, setView] = useState<"day" | "week" | "month">("month");

  const today = useMemo(() => startOfDay(new Date()), []);
  const [selected, setSelected] = useState<Date>(today);
  const [month, setMonth] = useState<Date>(firstOfMonth(today));
  const anchored = useRef(false);

  async function load(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    try {
      const r = await api.get("/visits/mine");
      setVisits(Array.isArray(r.data) ? r.data : []);
    } catch {
      setVisits([]);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }
  useEffect(() => { load(); }, []);

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

  // Anchor once to the most relevant visit so the calendar opens on something.
  useEffect(() => {
    if (anchored.current || !visits.length) return;
    anchored.current = true;
    const target = visits.find(v => v.status === "upcoming")
      || visits.find(v => v.status === "scheduled")
      || visits.find(v => v.status === "missed" || v.status === "overdue")
      || visits[visits.length - 1];
    if (target?.scheduled_date) {
      const d = dateFromKey(ymdUTC(parseISO(target.scheduled_date)));
      setSelected(d);
      setMonth(firstOfMonth(d));
    }
  }, [visits]);

  const todayKey = ymdLocal(today);

  // ── Month grid ──
  const gridStart = firstOfMonth(month).getDay();
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells: (number | null)[] = Array(gridStart).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  const weekRows: (number | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weekRows.push(cells.slice(i, i + 7));

  // ── Week strip ──
  const weekStart = startOfWeek(selected);
  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  const completedThisWeek = weekDays.filter(d => visitsOn(d).some(v => v.status === "completed")).length;
  const upcomingThisWeek = weekDays.filter(d => visitsOn(d).some(v => v.status === "upcoming" || v.status === "scheduled")).length;
  const freeDays = 7 - completedThisWeek - upcomingThisWeek;

  const selectedVisits = visitsOn(selected);

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
            <Pressable testID="cal-back" onPress={() => router.back()} hitSlop={10} style={s.appbarBtn}>
              <ChevronLeft size={22} color={colors.primaryFg} />
            </Pressable>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.eyebrowLight}>MY SCHEDULE</Text>
              <Text style={s.appbarTitle}>Calendar</Text>
            </View>
            <Pressable
              testID="cal-refresh"
              accessibilityRole="button"
              accessibilityLabel="Refresh calendar"
              onPress={() => load(true)}
              disabled={refreshing}
              hitSlop={10}
              style={s.appbarBtn}
            >
              {refreshing ? <ActivityIndicator size="small" color={colors.primaryFg} /> : <RotateCw size={20} color={colors.primaryFg} />}
            </Pressable>
            <Pressable testID="cal-settings" onPress={() => router.push("/(app)/clinical/calendar-settings" as any)} hitSlop={10} style={s.appbarBtn}>
              <Settings size={20} color={colors.primaryFg} />
            </Pressable>
          </View>
        </SafeAreaView>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }} showsVerticalScrollIndicator={false}>
        {/* Period nav + segmented control */}
        <View style={s.controls}>
          <View style={s.periodRow}>
            <Pressable testID="cal-prev" onPress={goPrev} style={s.navBtn}><ChevronLeft size={20} color={colors.foreground} /></Pressable>
            <Text style={s.periodLabel} numberOfLines={1}>{periodLabel}</Text>
            <Pressable testID="cal-next" onPress={goNext} style={s.navBtn}><ChevronRight size={20} color={colors.foreground} /></Pressable>
          </View>

          <View style={s.segment}>
            {(["day", "week", "month"] as const).map(mode => {
              const active = view === mode;
              const inner = <Text style={[s.segmentText, active && s.segmentTextActive]}>{mode[0].toUpperCase() + mode.slice(1)}</Text>;
              return (
                <Pressable key={mode} testID={`cal-view-${mode}`} onPress={() => setView(mode)} style={s.segmentBtn}>
                  {active
                    ? <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.segmentActive}>{inner}</LinearGradient>
                    : inner}
                </Pressable>
              );
            })}
          </View>

          <Pressable testID="cal-today" onPress={jumpToday} style={s.todayBtn}>
            <Text style={s.todayText}>Jump to today</Text>
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
                          <Pressable key={di} testID={`cal-day-${day}`} onPress={() => setSelected(cellDate)}
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
                            {meta && <View style={[s.cellDot, { backgroundColor: meta.color }]} />}
                          </Pressable>
                        );
                      })}
                    </View>
                  ))}
                  <Legend />
                </View>

                <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} variant="full" emptyText="No visits on this day" router={router} />
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
                      const chip = (
                        <View style={[
                          s.weekChip,
                          !isSelected && meta && { backgroundColor: meta.color + "26" },
                          !isSelected && !meta && isToday && s.cellToday,
                        ]}>
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
                        </View>
                      );
                      return (
                        <Pressable key={i} testID={`cal-weekday-${i}`} onPress={() => setSelected(d)} style={s.weekDayBtn}>
                          <Text style={s.weekDayName}>{WEEKDAYS[d.getDay()]}</Text>
                          {isSelected
                            ? <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.weekChip}>{chip.props.children}</LinearGradient>
                            : chip}
                        </Pressable>
                      );
                    })}
                  </View>
                  <Legend />
                </View>

                <View style={s.weekOverview}>
                  <Text style={s.weekOverviewText}>
                    This week: {completedThisWeek > 0 ? `${completedThisWeek} completed · ` : ""}
                    {upcomingThisWeek > 0 ? `${upcomingThisWeek} upcoming · ` : ""}
                    {freeDays} free days
                  </Text>
                </View>

                <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} variant="week" emptyText="No visits on this day" router={router} />
              </>
            )}

            {/* DAY VIEW */}
            {view === "day" && (
              <SelectedDaySection title={formatFullDay(selected)} visits={selectedVisits} variant="day" emptyText="No visits scheduled this day" router={router} />
            )}
          </>
        )}
      </ScrollView>
      <PatientBottomNav active="calendar" />
    </View>
  );
}

// ── Selected-day header + visit list ────────────────────────────────────────
function SelectedDaySection({ title, visits, variant, emptyText, router }: {
  title: string; visits: Visit[]; variant: "full" | "week" | "day"; emptyText: string; router: ReturnType<typeof useRouter>;
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
          {visits.map((v, i) => <VisitCard key={v.id || i} v={v} variant={variant} router={router} />)}
        </View>
      )}
    </View>
  );
}

// ── Visit card — date tear-block + details (mirrors dashboard Next Visit) ─────
function VisitCard({ v, variant, router }: { v: Visit; variant: "full" | "week" | "day"; router: ReturnType<typeof useRouter> }) {
  const meta = statusMeta(v.status);
  const upcoming = v.status === "upcoming";
  const missed = v.status === "missed" || v.status === "overdue";
  const d = v.scheduled_date ? parseISO(v.scheduled_date) : null;
  const window = v.window_start && v.window_end ? `${fmtUTCShort(v.window_start)} – ${fmtUTCShort(v.window_end)}` : null;

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
            {window ? (
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 }}>
                <Clock size={13} color={colors.mutedFg} />
                <Text style={s.windowText} numberOfLines={1}>Window {window}</Text>
              </View>
            ) : <View style={{ flex: 1 }} />}
            <View style={[s.statusChip, { backgroundColor: meta.color + "26" }]}>
              <Text style={[s.statusChipText, { color: meta.color }]}>{meta.label}</Text>
            </View>
          </View>

          <Text style={s.visitName}>{v.name || "Visit"}</Text>
          {v.visit_number != null && <Text style={s.visitSub}>Visit {v.visit_number}</Text>}

          {!!v.site && (
            <View style={s.metaRow}><Building2 size={13} color={colors.mutedFg} /><Text style={s.metaText} numberOfLines={1}>{v.site}</Text></View>
          )}
          {variant === "full" && !!v.pi_name && (
            <View style={s.metaRow}><Stethoscope size={13} color={colors.mutedFg} /><Text style={s.metaText} numberOfLines={1}>{v.pi_name}</Text></View>
          )}
        </View>
      </View>

      {v.status === "completed" && variant !== "day" && d && (
        <View style={s.cardFooter}>
          <Check size={13} color={colors.success} />
          <Text style={[s.footerText, { color: colors.success }]}>
            Completed on {d.getUTCDate()} {MONTHS[d.getUTCMonth()]} {d.getUTCFullYear()}
          </Text>
        </View>
      )}
      {variant === "full" && missed && (
        <View style={s.cardFooter}>
          <AlertTriangle size={13} color={colors.destructive} />
          <Text style={[s.footerText, { color: colors.destructive }]}>Was due · Contact your care team</Text>
        </View>
      )}
      {variant === "full" && (upcoming || v.status === "scheduled") && (
        <Pressable
          testID={`visit-details-${v.id}`}
          onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: v.id } })}
          style={s.cardFooter}
        >
          <Text style={[s.footerText, { color: colors.info }]}>View details →</Text>
        </Pressable>
      )}
    </View>
  );
}

// ── Legend ──
function Legend() {
  const items = [
    { color: colors.success, label: "Completed" },
    { color: colors.accent, label: "Upcoming" },
    { color: colors.info, label: "Scheduled" },
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
  appbar: { backgroundColor: colors.primaryDeep, paddingHorizontal: spacing.md, paddingBottom: 14 },
  appbarRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 6 },
  appbarBtn: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center" },
  eyebrowLight: { color: colors.overlay25, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4 },
  appbarTitle: { color: colors.primaryFg, fontFamily: fonts.display, fontSize: 20, letterSpacing: -0.4 },

  controls: { backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border, paddingHorizontal: spacing.md, paddingTop: spacing.md, paddingBottom: 14, gap: 12 },
  periodRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  navBtn: { width: 40, height: 40, borderRadius: 20, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  periodLabel: { flex: 1, textAlign: "center", color: colors.foreground, fontFamily: fonts.heading, fontSize: 18, letterSpacing: -0.2 },

  segment: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: radii.pill, padding: 4 },
  segmentBtn: { flex: 1, borderRadius: radii.pill, overflow: "hidden" },
  segmentActive: { paddingVertical: 9, alignItems: "center", borderRadius: radii.pill, ...shadows.sm },
  segmentText: { textAlign: "center", paddingVertical: 9, fontFamily: fonts.semibold, fontSize: 14, color: colors.mutedFg },
  segmentTextActive: { color: colors.primaryFg, paddingVertical: 0 },

  todayBtn: { alignSelf: "center", paddingHorizontal: 14, paddingVertical: 4 },
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
  cellDot: { position: "absolute", bottom: 5, width: 4, height: 4, borderRadius: 2 },

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
  windowText: { color: colors.mutedFg, fontFamily: fonts.medium, fontSize: 12 },
  statusChip: { borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 3 },
  statusChipText: { fontFamily: fonts.semibold, fontSize: 11 },
  visitName: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 17, lineHeight: 21, marginTop: 4 },
  visitSub: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, marginTop: 1 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 5 },
  metaText: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, flexShrink: 1 },
  cardFooter: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border },
  footerText: { fontFamily: fonts.semibold, fontSize: 12 },

  legend: { flexDirection: "row", justifyContent: "center", gap: 16, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border },
  legendText: { fontSize: 10, color: colors.mutedFg, fontFamily: fonts.regular },
});
