import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Animated,
  Easing,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Circle } from "react-native-svg";
import {
  Activity, AlertTriangle, Bell, Check, ChevronRight, Clock,
  MessageCircle, RefreshCw, WifiOff,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";

const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8",
  accent: "#E69B5C", info: "#7B6BB8", violet: "#8E5BB4", warning: "#D89A3C", success: "#5C9A6E",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;
type SourceKey = "visits" | "notifications" | "adherence" | "trial";
type SourceState = Record<SourceKey, "loading" | "ready" | "error">;
const INITIAL_SOURCES: SourceState = {
  visits: "loading",
  notifications: "loading",
  adherence: "loading",
  trial: "loading",
};

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => { if (alive) setReduced(value); })
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduced,
    );
    return () => {
      alive = false;
      subscription.remove();
    };
  }, []);
  return reduced;
}

function timeAgo(iso?: string): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diff = Math.max(0, Date.now() - then);
  const m = Math.floor(diff / 60000);
  if (m < 1) return "Just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export default function PatientDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [visits, setVisits] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  const [adherence, setAdherence] = useState<any>(null);
  const [trial, setTrial] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [sources, setSources] = useState<SourceState>(INITIAL_SOURCES);
  const reducedMotion = useReducedMotion();

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    const [visitResult, notificationResult, adherenceResult, trialResult] = await Promise.allSettled([
      api.get("/visits/mine"),
      api.get("/notifications"),
      api.get("/adherence"),
      api.get("/trials"),
    ]);
    const nextSources: SourceState = {
      visits: visitResult.status === "fulfilled" ? "ready" : "error",
      notifications: notificationResult.status === "fulfilled" ? "ready" : "error",
      adherence: adherenceResult.status === "fulfilled" ? "ready" : "error",
      trial: trialResult.status === "fulfilled" ? "ready" : "error",
    };
    const visitRows = visitResult.status === "fulfilled" && Array.isArray(visitResult.value.data)
      ? visitResult.value.data : [];
    const trialRows = trialResult.status === "fulfilled" && Array.isArray(trialResult.value.data)
      ? trialResult.value.data : [];

    if (visitResult.status === "fulfilled") setVisits(visitRows);
    if (notificationResult.status === "fulfilled") {
      setNotifs(Array.isArray(notificationResult.value.data) ? notificationResult.value.data : []);
    }
    if (adherenceResult.status === "fulfilled") setAdherence(adherenceResult.value.data);
    if (trialResult.status === "fulfilled") {
      const enrolledId = visitRows.find((row: any) => row.trial_id)?.trial_id;
      const enrolledTrial = enrolledId
        ? trialRows.find((row: any) => row.id === enrolledId)
        : trialRows.length === 1 ? trialRows[0] : null;
      setTrial(enrolledTrial || null);
    }
    setSources(nextSources);
    if (Object.values(nextSources).every((state) => state === "error")) {
      const rejected = [visitResult, notificationResult, adherenceResult, trialResult]
        .find((result) => result.status === "rejected") as PromiseRejectedResult | undefined;
      setError(
        rejected?.reason?.response?.data?.detail
        || "We couldn't connect to your dashboard. Check your connection and retry.",
      );
    }
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length;
  const next = [...visits]
    .filter((visit) => {
      if (visit.status !== "upcoming" && visit.status !== "scheduled") return false;
      const scheduled = visit.scheduled_date ? new Date(visit.scheduled_date) : null;
      return Boolean(scheduled && !Number.isNaN(scheduled.getTime()));
    })
    .sort((a, b) => new Date(a.scheduled_date).getTime() - new Date(b.scheduled_date).getTime())[0];
  const pct = total ? Math.round((completed / total) * 100) : 0;
  const fullName = user?.full_name?.trim() || "";
  const firstName = fullName.split(/\s+/)[0] || "there";
  const initials = user?.avatar_initials
    || fullName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase()
    || "?";
  const adherenceRate: number | null = sources.adherence === "ready"
    ? adherence?.rate ?? null : null;
  const trialLine = sources.trial === "ready" && trial
    ? [trial.protocol_id, trial.condition].filter(Boolean).join(" · ") : "";
  const validNextDate = next?.scheduled_date ? new Date(next.scheduled_date) : null;
  const nextDate = validNextDate && !Number.isNaN(validNextDate.getTime()) ? validNextDate : null;
  const daysToNext = nextDate ? Math.max(0, Math.ceil((nextDate.getTime() - Date.now()) / 86400000)) : null;
  const winStart = next?.window_start ? new Date(next.window_start) : nextDate;
  const winEnd = next?.window_end ? new Date(next.window_end) : nextDate;
  const fmtDay = (d: Date | null) => d && !Number.isNaN(d.getTime())
    ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    : "";
  const trialFooter = sources.trial === "ready" && trial
    ? [trial.protocol_id, trial.phase, trial.condition].filter(Boolean).join(" · ") : "";

  // Calendar mini (current month)
  const calendarAnchor = nextDate || new Date();
  const calendarMonth = new Date(calendarAnchor.getFullYear(), calendarAnchor.getMonth(), 1);
  const monthVisits: Record<number, "completed" | "upcoming" | "scheduled"> = {};
  visits.forEach(v => {
    if (!v.scheduled_date) return;
    const d = new Date(v.scheduled_date);
    if (Number.isNaN(d.getTime())) return;
    if (d.getMonth() === calendarMonth.getMonth() && d.getFullYear() === calendarMonth.getFullYear()) {
      if (v.status === "completed" || v.status === "upcoming" || v.status === "scheduled") {
        monthVisits[d.getDate()] = v.status;
      }
    }
  });
  const today = new Date().getDate();
  const startDay = calendarMonth.getDay();
  const daysInMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 0).getDate();
  const cells: (number | null)[] = Array(startDay).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  const failedSources = useMemo(
    () => Object.entries(sources)
      .filter(([, state]) => state === "error")
      .map(([key]) => key as SourceKey),
    [sources],
  );
  const partial = !loading && !error && failedSources.length > 0;
  const empty = !loading && !error && sources.visits === "ready"
    && sources.trial === "ready" && visits.length === 0 && !trial;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }}
        showsVerticalScrollIndicator={false}
        refreshControl={(
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void load(true)}
            tintColor={C.primary}
            colors={[C.primary]}
          />
        )}
      >
        {/* ── Dawn hero (radial plum → deep plum) ── */}
        <View style={{ backgroundColor: C.primaryDeep, borderBottomLeftRadius: 28, borderBottomRightRadius: 28, overflow: "hidden", paddingHorizontal: 24, paddingTop: 8, paddingBottom: 28 }}>
          {/* Radial-ish plum-to-deep using stacked gradient */}
          <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0.5, y: 0 }} end={{ x: 0.5, y: 1 }} style={StyleSheet.absoluteFill} />
          {/* Corner sun glow top-right */}
          <View pointerEvents="none" style={{ position: "absolute", top: -56, right: -48, width: 180, height: 180, borderRadius: 90, backgroundColor: C.dawnFrom, opacity: 0.30 }} />
          {/* Wide warm dawn rising along bottom */}
          <View pointerEvents="none" style={{ position: "absolute", bottom: -80, left: -50, right: -50, height: 180, borderRadius: 200, backgroundColor: C.dawnMid, opacity: 0.25 }} />
          {/* Drift motes */}
          <View pointerEvents="none" style={{ position: "absolute", top: 40, right: 96, width: 10, height: 10, borderRadius: 5, backgroundColor: "rgba(255,255,255,0.30)" }} />
          <View pointerEvents="none" style={{ position: "absolute", top: 96, left: 48, width: 8, height: 8, borderRadius: 4, backgroundColor: "rgba(255,255,255,0.22)" }} />

          <SafeAreaView edges={["top"]}>
            {/* Greeting + actions */}
            <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: "rgba(251,242,232,0.80)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>WELCOME BACK</Text>
                <Text style={{ color: C.primaryFg, fontSize: 30, fontWeight: "700", lineHeight: 36, letterSpacing: -0.6, marginTop: 4 }}>Hi, {firstName}</Text>
                {!!trialLine && <Text style={{ color: "rgba(251,242,232,0.75)", fontSize: 13, marginTop: 4 }}>{trialLine}</Text>}
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Pressable testID="patient-bell" onPress={() => router.push("/(app)/notifications")} style={pst.iconBtn}>
                  <Bell size={20} color={C.primaryFg} />
                  {unread != null && unread > 0 && (
                    <View style={pst.bellBadge}><Text style={pst.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                  )}
                </Pressable>
                <Pressable testID="patient-avatar" onPress={() => router.push("/(app)/patient/profile")} style={[pst.iconBtn, { backgroundColor: "rgba(255,255,255,0.20)" }]}>
                  <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
                </Pressable>
              </View>
            </View>

            {/* Progress glass panel */}
            <View style={{ flexDirection: "row", alignItems: "center", gap: 16, marginTop: 20, padding: 16, borderRadius: 24, backgroundColor: "rgba(255,255,255,0.10)", borderWidth: 1, borderColor: "rgba(255,255,255,0.15)" }}>
              {loading ? (
                <View style={pst.ringLoading}><ActivityIndicator color={C.primaryFg} /></View>
              ) : (
                <Ring pct={pct} size={72} stroke={7} reducedMotion={reducedMotion} />
              )}
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: "rgba(251,242,232,0.75)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>YOUR PROGRESS</Text>
                <Text style={{ color: C.primaryFg, fontSize: 17, fontWeight: "700", marginTop: 4 }}>
                  {loading
                    ? "Loading your study progress…"
                    : sources.visits === "error"
                      ? "Progress is temporarily unavailable"
                      : total
                        ? `Visit ${completed} of ${total} completed`
                        : "No visit schedule has been published"}
                </Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {adherenceRate != null && (
                    <View style={pst.chip}><Activity size={11} color={C.primaryFg} /><Text style={pst.chipText}>{adherenceRate}% adherence</Text></View>
                  )}
                  {sources.visits === "ready" && next && daysToNext != null && <View style={pst.chip}><Text style={pst.chipText}>Next in {daysToNext} days</Text></View>}
                </View>
              </View>
            </View>
          </SafeAreaView>
        </View>

        {error ? (
          <StatePanel
            icon={WifiOff}
            title="Dashboard unavailable"
            body={error}
            action="Retry"
            onAction={() => void load()}
            destructive
          />
        ) : partial ? (
          <StatePanel
            icon={AlertTriangle}
            title="Some information couldn't load"
            body={`${failedSources.map(sourceLabel).join(", ")} ${failedSources.length === 1 ? "is" : "are"} temporarily unavailable. Available sections remain current.`}
            action="Refresh"
            onAction={() => void load(true)}
          />
        ) : empty ? (
          <StatePanel
            icon={Clock}
            title="Your dashboard is being prepared"
            body="No enrolled study or visit schedule is available yet. Refresh after your study team completes enrollment."
            action="Refresh"
            onAction={() => void load(true)}
          />
        ) : null}

        {/* ── 01 · Next visit ── */}
        <DashboardReveal delay={40} reducedMotion={reducedMotion} style={{ paddingHorizontal: 16, marginTop: 20 }}>
          <SectionHead index="01" label="NEXT VISIT" />
          {loading ? (
            <View style={[pst.card, pst.loadingCard]}><ActivityIndicator color={C.primary} /></View>
          ) : sources.visits === "error" ? (
            <SectionError label="visit schedule" onRetry={() => void load(true)} />
          ) : next && nextDate ? (
          <Pressable testID="next-visit-card" onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: next.id } })}>
            <View style={pst.card}>
              <View style={{ flexDirection: "row", gap: 16 }}>
                <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={pst.dateBlock}>
                  <Text style={{ color: C.primaryFg, fontSize: 26, fontWeight: "700", lineHeight: 28 }}>{nextDate.getDate()}</Text>
                  <Text style={{ color: "rgba(251,242,232,0.85)", fontSize: 11, fontWeight: "700", letterSpacing: 1.4, marginTop: 4 }}>{nextDate.toLocaleString("en-US", { month: "short" }).toUpperCase()}</Text>
                </LinearGradient>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                    <Text style={{ color: C.accent, fontSize: 11, fontWeight: "700", letterSpacing: 1.4 }}>{daysToNext === 0 ? "TODAY" : `IN ${daysToNext} DAYS`}</Text>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(216,154,60,0.15)" }}>
                      <Text style={{ color: C.warning, fontSize: 11, fontWeight: "700" }}>Upcoming</Text>
                    </View>
                  </View>
                  <Text style={{ color: C.fg, fontSize: 17, fontWeight: "700", marginTop: 4 }}>Visit {next.visit_number} · {next.name}</Text>
                  {!!(next.activities?.length) && (
                    <Text style={{ color: C.muted, fontSize: 12, marginTop: 4 }} numberOfLines={1}>{next.activities.join(" · ")}</Text>
                  )}
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6 }}>
                    <Clock size={11} color={C.muted} />
                    <Text style={{ color: C.muted, fontSize: 12 }}>
                      {fmtDay(winStart) && fmtDay(winEnd)
                        ? `Window ${fmtDay(winStart)} – ${fmtDay(winEnd)}`
                        : "Visit window pending"}
                    </Text>
                  </View>
                </View>
              </View>
              {!!trialFooter && (
              <View style={{ marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                <Text style={{ color: C.muted, fontSize: 12 }}>{trialFooter}</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                  <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>View details</Text>
                  <ChevronRight size={16} color={C.accent} />
                </View>
              </View>
              )}
            </View>
          </Pressable>
          ) : (
            <View style={pst.card}>
              <Text style={{ color: C.fg, fontSize: 14, fontWeight: "700" }}>No upcoming visits</Text>
              <Text style={{ color: C.muted, fontSize: 13, marginTop: 4 }}>
                {visits.length
                  ? "Your remaining visits are completed or not yet scheduled."
                  : "Your study team hasn't published a visit schedule yet."}
              </Text>
            </View>
          )}
        </DashboardReveal>

        {/* ── 02 · Calendar mini ── */}
        <DashboardReveal delay={100} reducedMotion={reducedMotion} style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="02" label="CALENDAR" action={
            <Pressable testID="open-calendar" onPress={() => router.push("/(app)/patient/calendar")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>Open calendar </Text>
              <ChevronRight size={16} color={C.accent} />
            </Pressable>
          } />
          {loading ? (
            <View style={[pst.card, pst.loadingCard]}><ActivityIndicator color={C.primary} /></View>
          ) : sources.visits === "error" ? (
            <SectionError label="calendar visits" onRetry={() => void load(true)} />
          ) : <Pressable testID="cal-mini" onPress={() => router.push("/(app)/patient/calendar")}>
            <View style={pst.card}>
              <Text style={{ textAlign: "center", color: C.fg, fontSize: 16, fontWeight: "700", marginBottom: 12 }}>
                {calendarMonth.toLocaleString("en-US", { month: "long", year: "numeric" })}
              </Text>
              <View style={{ flexDirection: "row", marginBottom: 4 }}>
                {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
                  <View key={i} style={{ flex: 1, alignItems: "center", paddingVertical: 4 }}>
                    <Text style={{ fontSize: 11, fontWeight: "700", color: "rgba(123,95,115,0.70)" }}>{d}</Text>
                  </View>
                ))}
              </View>
              {[0, 1, 2, 3, 4, 5].map(row => (
                <View key={row} style={{ flexDirection: "row", marginBottom: 4 }}>
                  {cells.slice(row * 7, row * 7 + 7).map((day, i) => {
                    const status = day ? monthVisits[day] : undefined;
                    const isToday = day === today && calendarMonth.getMonth() === new Date().getMonth() && calendarMonth.getFullYear() === new Date().getFullYear();
                    const bg = status === "completed" ? "rgba(230,155,92,0.12)" : status === "upcoming" ? "rgba(216,154,60,0.15)" : status === "scheduled" ? "rgba(123,107,184,0.10)" : "transparent";
                    const fg = status === "completed" ? C.accent : status === "upcoming" ? C.warning : status === "scheduled" ? C.info : isToday ? C.info : C.fg;
                    const dot = status === "completed" ? C.accent : status === "upcoming" ? C.warning : status === "scheduled" ? C.info : "transparent";
                    return (
                      <View key={i} style={{ flex: 1, aspectRatio: 1, alignItems: "center", justifyContent: "center", marginHorizontal: 2, borderRadius: 12, backgroundColor: bg, borderWidth: !status && isToday ? 1 : 0, borderColor: C.info }}>
                        {day && <Text style={{ fontSize: 12, fontWeight: "600", color: fg }}>{day}</Text>}
                        {status && <View style={{ position: "absolute", bottom: 4, width: 4, height: 4, borderRadius: 2, backgroundColor: dot }} />}
                      </View>
                    );
                  })}
                </View>
              ))}
              <View style={{ flexDirection: "row", justifyContent: "center", gap: 16, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
                <Legend color={C.accent} label="Completed" />
                <Legend color={C.warning} label="Upcoming" />
                <Legend color={C.info} label="Scheduled" />
              </View>
            </View>
          </Pressable>}
        </DashboardReveal>

        {/* ── 03 · Notifications ── */}
        <DashboardReveal delay={160} reducedMotion={reducedMotion} style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="03" label="NOTIFICATIONS" action={
            <Pressable testID="see-all-notifs" onPress={() => router.push("/(app)/notifications")}>
              <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>See all</Text>
            </Pressable>
          } />
          <View style={{ gap: 12 }}>
            {loading && (
              <View style={[pst.card, pst.loadingCard]}><ActivityIndicator color={C.primary} /></View>
            )}
            {!loading && sources.notifications === "error" && (
              <SectionError label="notifications" onRetry={() => void load(true)} />
            )}
            {!loading && sources.notifications === "ready" && notifs.length === 0 && (
              <View style={pst.card}><Text style={{ color: C.muted, fontSize: 13 }}>No notifications yet</Text></View>
            )}
            {!loading && sources.notifications === "ready" && notifs.slice(0, 3).map(n => {
              const Icon = n.kind === "message" ? MessageCircle : Bell;
              const tone = n.kind === "message" ? C.violet : C.accent;
              return (
                <Pressable key={n.id} testID={`notif-${n.id}`} onPress={() => router.push(n.kind === "message" ? "/(app)/patient/messages" : "/(app)/notifications")}>
                  <View style={[pst.card, { flexDirection: "row", alignItems: "flex-start", gap: 12 }]}>
                    <View style={{ width: 44, height: 44, borderRadius: 16, backgroundColor: tone + "26", alignItems: "center", justifyContent: "center" }}>
                      <Icon size={20} color={tone} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                        <Text style={{ color: C.fg, fontSize: 15, fontWeight: "600", flex: 1 }} numberOfLines={1}>{n.title}</Text>
                        {!n.read && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: C.accent, marginLeft: 8 }} />}
                      </View>
                      <Text style={{ color: C.muted, fontSize: 14, marginTop: 2 }} numberOfLines={2}>{n.body}</Text>
                      {!!timeAgo(n.created_at) && <Text style={{ color: "rgba(123,95,115,0.70)", fontSize: 11, marginTop: 4 }}>{timeAgo(n.created_at)}</Text>}
                    </View>
                    <ChevronRight size={16} color="rgba(123,95,115,0.40)" style={{ marginTop: 4 }} />
                  </View>
                </Pressable>
              );
            })}
          </View>
        </DashboardReveal>

        {/* ── 04 · Recent activity ── */}
        <DashboardReveal delay={220} reducedMotion={reducedMotion} style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="04" label="RECENT ACTIVITY" />
          <View style={[pst.card, { padding: 0 }]}>
            {loading && (
              <View style={pst.loadingCard}><ActivityIndicator color={C.primary} /></View>
            )}
            {!loading && sources.visits === "error" && (
              <SectionError label="recent activity" onRetry={() => void load(true)} embedded />
            )}
            {!loading && sources.visits === "ready" && visits.filter(v => v.status === "completed").slice(-2).reverse().map((v, i) => (
              <View key={v.id} style={[{ padding: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }, i > 0 && { borderTopWidth: 1, borderTopColor: C.border }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(92,154,110,0.15)", alignItems: "center", justifyContent: "center" }}>
                    <Check size={16} color={C.success} strokeWidth={2.5} />
                  </View>
                  <View>
                    <Text style={{ color: C.fg, fontSize: 14, fontWeight: "600" }}>Visit {v.visit_number}</Text>
                    <Text style={{ color: C.muted, fontSize: 13 }}>
                      {formatVisitDate(v.scheduled_date) || "Completion date unavailable"}
                    </Text>
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.15)" }}>
                  <Text style={{ color: C.success, fontSize: 12, fontWeight: "600" }}>Done</Text>
                </View>
              </View>
            ))}
            {!loading && sources.visits === "ready" && visits.filter(v => v.status === "completed").length === 0 && (
              <View style={{ padding: 16 }}><Text style={{ color: C.muted, fontSize: 13 }}>No completed visits yet</Text></View>
            )}
          </View>
        </DashboardReveal>
      </ScrollView>

      <PatientBottomNav active="home" />
    </View>
  );
}

function sourceLabel(source: SourceKey) {
  return {
    visits: "Visit schedule",
    notifications: "Notifications",
    adherence: "Medication adherence",
    trial: "Study details",
  }[source];
}

function formatVisitDate(value?: string) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function StatePanel({
  icon: Icon,
  title,
  body,
  action,
  onAction,
  destructive,
}: {
  icon: React.ComponentType<{ size?: number; color?: string }>;
  title: string;
  body: string;
  action: string;
  onAction: () => void;
  destructive?: boolean;
}) {
  const tone = destructive ? "#C0392B" : C.warning;
  return (
    <View style={[pst.statePanel, { borderColor: `${tone}55` }]}>
      <View style={[pst.stateIcon, { backgroundColor: `${tone}18` }]}>
        <Icon size={19} color={tone} />
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text style={pst.stateTitle}>{title}</Text>
        <Text style={pst.stateBody}>{body}</Text>
      </View>
      <Pressable onPress={onAction} style={pst.stateAction}>
        <RefreshCw size={14} color={C.primary} />
        <Text style={pst.stateActionText}>{action}</Text>
      </Pressable>
    </View>
  );
}

function SectionError({
  label,
  onRetry,
  embedded,
}: {
  label: string;
  onRetry: () => void;
  embedded?: boolean;
}) {
  return (
    <View style={[embedded ? pst.embeddedError : pst.card, pst.sectionError]}>
      <AlertTriangle size={18} color={C.warning} />
      <View style={{ flex: 1 }}>
        <Text style={pst.sectionErrorTitle}>Couldn&apos;t load {label}</Text>
        <Text style={pst.sectionErrorBody}>Other dashboard sections may still be available.</Text>
      </View>
      <Pressable onPress={onRetry} style={pst.retryButton}>
        <Text style={pst.retryText}>Retry</Text>
      </Pressable>
    </View>
  );
}

function DashboardReveal({
  children,
  delay,
  reducedMotion,
  style,
}: {
  children: React.ReactNode;
  delay: number;
  reducedMotion: boolean;
  style?: any;
}) {
  const progress = useRef(new Animated.Value(reducedMotion ? 1 : 0)).current;
  useEffect(() => {
    if (reducedMotion) {
      progress.setValue(1);
      return;
    }
    progress.setValue(0);
    Animated.timing(progress, {
      toValue: 1,
      delay,
      duration: 360,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [delay, progress, reducedMotion]);
  return (
    <Animated.View
      style={[
        style,
        {
          opacity: progress,
          transform: [{
            translateY: progress.interpolate({
              inputRange: [0, 1],
              outputRange: [12, 0],
            }),
          }],
        },
      ]}
    >
      {children}
    </Animated.View>
  );
}

function SectionHead({ index, label, action }: any) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 12 }}>
      <Text style={{ color: C.accent, fontSize: 14, fontWeight: "700", fontVariant: ["tabular-nums"] }}>{index}</Text>
      <Text style={{ color: C.primary, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      <View style={{ flex: 1, height: 1, backgroundColor: C.border }} />
      {action}
    </View>
  );
}

function Ring({ pct, size, stroke, reducedMotion }: {
  pct: number;
  size: number;
  stroke: number;
  reducedMotion: boolean;
}) {
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  const value = useRef(new Animated.Value(reducedMotion ? pct : 0)).current;
  const [display, setDisplay] = useState(reducedMotion ? pct : 0);
  useEffect(() => {
    const listener = value.addListener(({ value: current }) => setDisplay(current));
    return () => value.removeListener(listener);
  }, [value]);
  useEffect(() => {
    if (reducedMotion) {
      value.setValue(pct);
      setDisplay(pct);
      return;
    }
    Animated.timing(value, {
      toValue: pct,
      duration: 620,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [pct, reducedMotion, value]);
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.95)" strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - display / 100)} />
      </Svg>
      <Text style={{ color: C.primaryFg, fontSize: 20, fontWeight: "700", fontVariant: ["tabular-nums"] }}>{Math.round(display)}%</Text>
    </View>
  );
}

function Legend({ color, label }: any) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
      <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: color }} />
      <Text style={{ fontSize: 11, color: C.muted }}>{label}</Text>
    </View>
  );
}

const pst = StyleSheet.create({
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(255,255,255,0.15)", alignItems: "center", justifyContent: "center" },
  bellBadge: { position: "absolute", top: -2, right: -2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, backgroundColor: "#C0392B", alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.primary },
  bellBadgeText: { color: C.primaryFg, fontSize: 10, fontWeight: "700" },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.20)" },
  chipText: { color: C.primaryFg, fontSize: 11, fontWeight: "700" },
  card: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  loadingCard: { alignItems: "center", justifyContent: "center", paddingVertical: 28 },
  ringLoading: {
    width: 72,
    height: 72,
    borderRadius: 36,
    borderWidth: 7,
    borderColor: "rgba(255,255,255,0.22)",
    alignItems: "center",
    justifyContent: "center",
  },
  statePanel: {
    marginHorizontal: 16,
    marginTop: 16,
    padding: 14,
    borderRadius: 18,
    borderWidth: 1,
    backgroundColor: C.card,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  stateIcon: {
    width: 38,
    height: 38,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  stateTitle: { color: C.fg, fontSize: 14, fontWeight: "700" },
  stateBody: { color: C.muted, fontSize: 12, lineHeight: 17, marginTop: 2 },
  stateAction: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: `${C.primary}0D`,
  },
  stateActionText: { color: C.primary, fontSize: 12, fontWeight: "700" },
  sectionError: { flexDirection: "row", alignItems: "center", gap: 10 },
  embeddedError: { padding: 16 },
  sectionErrorTitle: { color: C.fg, fontSize: 13, fontWeight: "700" },
  sectionErrorBody: { color: C.muted, fontSize: 11, marginTop: 2 },
  retryButton: {
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: `${C.primary}12`,
  },
  retryText: { color: C.primary, fontSize: 12, fontWeight: "700" },
  dateBlock: { paddingHorizontal: 16, paddingVertical: 12, borderRadius: 16, alignItems: "center", justifyContent: "center" },
});
