import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, ActivityIndicator, Modal } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle } from "react-native-svg";
import {
  Bell, Sun, FileText, Building2, Stethoscope, ArrowUpRight,
  FilePlus2, UserPlus, ListTodo, AlertTriangle, ChevronRight,
  Clock, Home, Users, MessageCircle, Calendar as CalIcon, User,
  Check, ClipboardCheck, RefreshCcw, X,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import {
  AnimatedCount,
  ClinicalDashboard,
  ClinicalDashboardTask,
  ClinicalDashboardVisit,
  DashboardReveal,
  useAnimatedProgress,
} from "@/src/features/clinical/dashboard";

// ── Dawn Rounds palette (matches /app/frontend/src/theme/tokens.ts) ──────────
const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33",
  muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8",
  secondary: "#F0D7DC",
  accent: "#E69B5C", accentFg: "#5A3318",
  info: "#7B6BB8", violet: "#8E5BB4",
  warning: "#D89A3C", success: "#5C9A6E", destructive: "#C0392B",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
  w10: "rgba(255,255,255,0.10)", w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w25: "rgba(255,255,255,0.25)", w55: "rgba(255,255,255,0.55)", w65: "rgba(255,255,255,0.65)", w70: "rgba(255,255,255,0.70)", w80: "rgba(255,255,255,0.80)",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;
const VISIT_OUTCOMES = [
  { value: "completed", label: "Completed" },
  { value: "screen_fail", label: "Screen Failure" },
  { value: "dropout", label: "Dropout" },
  { value: "withdrawn", label: "Withdrawn" },
] as const;
type VisitOutcome = typeof VISIT_OUTCOMES[number]["value"];

// GET /api/tasks item — action queue computed server-side for site staff.
type Task = ClinicalDashboardTask;
type TodayVisit = ClinicalDashboardVisit;

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
const daysLate = (iso: string | null) =>
  iso ? Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 86400000)) : 1;

export default function CrcDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<any[]>([]);
  const [patients, setPatients] = useState<any[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [todayVisits, setTodayVisits] = useState<TodayVisit[]>([]);
  const [completingTask, setCompletingTask] = useState<string | null>(null);
  const [taskTotal, setTaskTotal] = useState(0);
  const [completedTaskCount, setCompletedTaskCount] = useState(0);
  const [taskError, setTaskError] = useState("");
  const [outcomeTask, setOutcomeTask] = useState<Task | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState<VisitOutcome | null>(null);
  const [savingOutcome, setSavingOutcome] = useState(false);
  const [outcomeError, setOutcomeError] = useState("");
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await api.get<ClinicalDashboard>("/crc/dashboard");
      const data = response.data;
      const loadedTasks = Array.isArray(data.tasks) ? data.tasks : [];
      setDashboard(data);
      setTrials(Array.isArray(data.trials) ? data.trials : []);
      setPatients(Array.isArray(data.patients) ? data.patients : []);
      setTasks(loadedTasks);
      setTaskTotal(loadedTasks.length);
      setCompletedTaskCount(0);
      setTodayVisits(Array.isArray(data.today_visits) ? data.today_visits : []);
    } catch (error: any) {
      setLoadError(error?.response?.data?.detail || "Couldn't load your dashboard.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const trialById = useMemo(() => Object.fromEntries(trials.map((t: any) => [t.id, t])), [trials]);
  const patientById = useMemo(() => Object.fromEntries(patients.map((p: any) => [p.id, p])), [patients]);
  const visitTasksToday = useMemo(() => tasks.filter(t => t.type === "visit_today"), [tasks]);
  const overdueVisits = useMemo(() => tasks.filter(t => t.type === "overdue_visit"), [tasks]);
  const rankedTasks = useMemo(() => {
    const rank = { high: 0, medium: 1, low: 2 };
    return [...tasks].sort((a, b) => rank[a.priority] - rank[b.priority]);
  }, [tasks]);
  const sponsorCount = dashboard?.totals.sponsors
    ?? new Set(trials.map((t: any) => t.sponsor_name).filter(Boolean)).size;
  const piCount = dashboard?.totals.pis
    ?? new Set(patients.map((p: any) => p.pi_id).filter(Boolean)).size;

  const fullName = user?.full_name || "";
  const firstName = fullName.split(" ")[0] || "";
  const initials = user?.avatar_initials || fullName.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const todayLabel = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const completedToday = dashboard?.today.completed
    ?? todayVisits.filter(v => v.status === "completed").length;
  const totalToday = dashboard?.today.total ?? (todayVisits.length || visitTasksToday.length);
  const dayProgress = loading || totalToday === 0 ? 0 : completedToday / totalToday;

  const openTask = (task: Task) => {
    if (task.type === "unread_messages") {
      router.push("/(app)/chat");
      return;
    }
    if (task.type === "schedule_review") {
      router.push(task.schedule_review_id
        ? {
            pathname: "/(app)/clinical/schedule-review",
            params: {
              id: task.schedule_review_id,
              trialId: task.trial_id || "",
            },
          }
        : task.trial_id
          ? { pathname: "/(app)/clinical/trial-summary", params: { id: task.trial_id } }
          : "/(app)/clinical/my-trials");
      return;
    }
    router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: task.patient_id || "" } });
  };

  const actOnTask = async (task: Task) => {
    const isVisitAlert = task.type === "visit_today"
      || task.type === "window_closes_today"
      || task.type === "overdue_visit";
    if (isVisitAlert) {
      setOutcomeError("");
      setSelectedOutcome(null);
      setOutcomeTask(task);
      return;
    }
    if (task.type !== "admin_task") {
      openTask(task);
      return;
    }
    const instanceId = task.visit_instance_id;
    const workflowTaskId = task.workflow_task_id;
    if (!instanceId || !workflowTaskId || completingTask) return;
    setTaskError("");
    setCompletingTask(task.id);
    try {
      await api.patch(`/visit-instances/${instanceId}/tasks/${workflowTaskId}`, { completed: true });
      setTasks(current => current.filter(item => item.id !== task.id));
      setCompletedTaskCount(current => current + 1);
    } catch (error: any) {
      setTaskError(error?.response?.data?.detail || "Couldn't complete this task. Please try again.");
    } finally {
      setCompletingTask(null);
    }
  };

  const closeOutcomeSheet = () => {
    if (savingOutcome) return;
    setOutcomeTask(null);
    setSelectedOutcome(null);
    setOutcomeError("");
  };

  const saveVisitOutcome = async () => {
    const instanceId = outcomeTask?.visit_instance_id;
    if (!outcomeTask || !instanceId || !selectedOutcome || savingOutcome) return;
    setSavingOutcome(true);
    setOutcomeError("");
    try {
      await api.patch(`/visit-instances/${instanceId}`, { status: selectedOutcome });
      const resolvedTask = outcomeTask;
      setTasks(current => current.filter(task =>
        task.visit_instance_id !== instanceId
      ));
      setTodayVisits(current => current.filter(visit => visit.id !== instanceId));
      setCompletedTaskCount(current => current + 1);
      setDashboard(current => current ? {
        ...current,
        today: {
          ...current.today,
          total: Math.max(0, current.today.total - 1),
          pending: Math.max(0, current.today.pending - 1),
          completed: current.today.completed + (selectedOutcome === "completed" ? 1 : 0),
          overdue: Math.max(
            0,
            current.today.overdue - (resolvedTask.type === "overdue_visit" ? 1 : 0),
          ),
        },
      } : current);
      setOutcomeTask(null);
      setSelectedOutcome(null);
    } catch (error: any) {
      setOutcomeError(error?.response?.data?.detail || "Couldn't update this visit. Please try again.");
    } finally {
      setSavingOutcome(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 110 }}
        showsVerticalScrollIndicator={false}
        nestedScrollEnabled
      >
        {/* ── Hero with dawn gradient + concentric arcs ── */}
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          {/* Plum overlay top → transparent bottom for legibility */}
          <LinearGradient colors={[C.primaryDeep, "rgba(107,20,55,0.55)", "rgba(107,20,55,0)"] as any} style={StyleSheet.absoluteFill} />
          {/* Concentric sunrise arcs top-right */}
          <View style={{ position: "absolute", right: -48, top: -48, width: 240, height: 240, opacity: 0.85 }} pointerEvents="none">
            <Svg viewBox="0 0 200 200" width={240} height={240}>
              <Path d="M30 110 a70 70 0 0 1 140 0" stroke={C.w25} strokeWidth="1.5" fill="none" />
              <Path d="M52 110 a48 48 0 0 1 96 0" stroke={C.w25} strokeWidth="1" fill="none" />
              <Circle cx="100" cy="110" r="22" stroke={C.w15} strokeWidth="1" fill="none" />
            </Svg>
          </View>
          {/* Motes */}
          <View pointerEvents="none" style={{ position: "absolute", right: 36, top: 96, width: 6, height: 6, borderRadius: 3, backgroundColor: "rgba(255,255,255,0.40)" }} />
          <View pointerEvents="none" style={{ position: "absolute", right: 112, top: 144, width: 5, height: 5, borderRadius: 2.5, backgroundColor: "rgba(255,255,255,0.30)" }} />
          <View pointerEvents="none" style={{ position: "absolute", left: 36, top: 176, width: 4, height: 4, borderRadius: 2, backgroundColor: "rgba(255,255,255,0.30)" }} />

          <SafeAreaView edges={["top"]}>
            {/* Top row */}
            <View style={st.heroTop}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={st.eyebrowLight}>RESEARCH TEAM · CRC</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={st.heroTitle}>{firstName ? `Hi, ${firstName}` : "Hello"}</Text>
                  <Sun size={20} color={C.w80} />
                </View>
              </View>
              <Pressable testID="crc-bell" onPress={() => router.push("/(app)/notifications")} style={st.iconBtn}>
                <Bell size={20} color={C.primaryFg} />
                {unread != null && unread > 0 && (
                  <View style={st.bellBadge}><Text style={st.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                )}
              </Pressable>
              <Pressable testID="crc-avatar" onPress={() => router.push("/(app)/clinical/profile")} style={st.iconBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
              </Pressable>
            </View>

            {/* Day deck */}
            <View style={st.dayDeck}>
              <ProgressRing value={dayProgress} size={84} stroke={7}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 22, lineHeight: 24, fontVariant: ["tabular-nums"] }}>
                  {loading ? "–" : `${completedToday}/${totalToday}`}
                </Text>
                <Text style={{ color: C.w70, fontSize: 8, fontWeight: "700", letterSpacing: 1.4, marginTop: 2 }}>VISITS</Text>
              </ProgressRing>
              <View style={{ flex: 1, minWidth: 0, marginLeft: 16 }}>
                <Text style={st.eyebrowLight}>{todayLabel.toUpperCase()}</Text>
                <Text style={st.heroSubtitle}>Your day at the site</Text>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <View style={st.heroChip}><ListTodo size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : (dashboard?.today.pending ?? todayVisits.length)} visits pending</Text></View>
                  <View style={[st.heroChip, !loading && overdueVisits.length > 0 && { backgroundColor: "rgba(192,57,43,0.30)" }]}>
                    <AlertTriangle size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : (dashboard?.today.overdue ?? overdueVisits.length)} overdue</Text>
                  </View>
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        {/* ── Body floats up into hero ── */}
        <View style={{ marginTop: -40, paddingHorizontal: 16, paddingBottom: 24 }}>
          {!!loadError && <DashboardError message={loadError} onRetry={loadDashboard} />}
          {/* Stat tiles */}
          <DashboardReveal delay={40} style={{ flexDirection: "row", gap: 10 }}>
            <StatTile icon={FileText} iconColor={C.info} iconBg="rgba(123,107,184,0.12)" glow="rgba(123,107,184,0.20)" value={loading ? null : trials.length} label="Total Trials" onPress={() => router.push("/(app)/clinical/my-trials")} />
            <StatTile icon={Building2} iconColor={C.accent} iconBg="rgba(230,155,92,0.15)" glow="rgba(230,155,92,0.20)" value={loading ? null : sponsorCount} label="Sponsors" />
            <StatTile icon={Stethoscope} iconColor={C.violet} iconBg="rgba(142,91,180,0.12)" glow="rgba(142,91,180,0.20)" value={loading ? null : piCount} label="PI's" />
          </DashboardReveal>

          {/* Quick Actions */}
          <SectionLabel label="QUICK ACTIONS" />
          <View style={{ flexDirection: "row", gap: 10 }}>
            <QuickAction icon={FilePlus2} bgGradient={false} bgColor={C.info} iconColor={"#FFFFFF"} label="My Trials" onPress={() => router.push("/(app)/clinical/my-trials")} testID="qa-my-trials" />
            {(loading || dashboard?.capabilities.can_add_patient) && (
              <QuickAction icon={UserPlus} bgGradient bgColor={undefined} iconColor={C.primaryFg} label="Add Patient" onPress={() => router.push("/(app)/clinical/add-patient")} testID="qa-add-patient" />
            )}
          </View>

          {/* My Trials */}
          <SectionLabel label="MY TRIALS" action={
            <Pressable testID="see-all-trials" onPress={() => router.push("/(app)/clinical/my-trials")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.info, fontSize: 14, fontWeight: "700" }}>See all </Text>
              <ChevronRight size={16} color={C.info} />
            </Pressable>
          } />
          <View style={{ gap: 12 }}>
            {loading && <LoadingCard />}
            {!loading && trials.length === 0 && <EmptyCard text="No trials assigned yet" />}
            {!loading && trials.slice(0, 2).map((tr: any) => (
              <TrialPanel
                key={tr.id}
                tr={tr}
                patientCount={patients.filter((p: any) => p.trial_id === tr.id).length}
                onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: tr.id } })}
              />
            ))}
          </View>

          {/* My Tasks Today */}
          <SectionLabel label="MY TASKS TODAY" action={
            <Text style={{ fontSize: 11, fontWeight: "700", color: C.muted, fontVariant: ["tabular-nums"] }}>
              {loading ? "–" : `${rankedTasks.length} LEFT`}
            </Text>
          } />
          {!loading && (
            <View style={st.taskProgressRow}>
              <View style={st.taskProgressTrack}>
                {Array.from({ length: Math.max(1, taskTotal) }, (_, index) => (
                  <View
                    key={index}
                    style={[
                      st.taskProgressSegment,
                      { backgroundColor: taskTotal === 0 || index < completedTaskCount ? C.success : C.surface },
                    ]}
                  />
                ))}
              </View>
              <Text style={st.taskProgressCount}>
                {taskTotal === 0 ? "DONE" : `${completedTaskCount}/${taskTotal}`}
              </Text>
            </View>
          )}
          {loading && <LoadingCard />}
          {!loading && rankedTasks.length === 0 && (
            <View style={st.caughtUpCard}>
              <LinearGradient colors={DAWN as any} style={st.caughtUpIcon}>
                <Check size={24} color={C.primaryFg} strokeWidth={3} />
              </LinearGradient>
              <Text style={st.caughtUpTitle}>All caught up</Text>
              <Text style={st.caughtUpText}>You’ve cleared today’s tasks — nice work.</Text>
            </View>
          )}
          {!loading && rankedTasks.length > 0 && (
            <SectionScroller count={rankedTasks.length} threshold={3} maxHeight={274}>
              {rankedTasks.map((task, index) => (
                <DashboardReveal key={task.id} delay={60 + index * 45}>
                  <TaskCard
                    task={task}
                    busy={completingTask === task.id}
                    onComplete={() => actOnTask(task)}
                    onOpen={() => openTask(task)}
                  />
                </DashboardReveal>
              ))}
            </SectionScroller>
          )}
          {!!taskError && (
            <View style={st.taskError}>
              <AlertTriangle size={15} color={C.destructive} />
              <Text style={st.taskErrorText}>{taskError}</Text>
              <Pressable onPress={() => setTaskError("")} hitSlop={8}>
                <RefreshCcw size={15} color={C.destructive} />
              </Pressable>
            </View>
          )}

          {/* Today's Visits */}
          <SectionLabel label="TODAY'S VISITS" action={!loading ? <Text style={{ fontSize: 11, fontWeight: "700", color: C.muted, fontVariant: ["tabular-nums"] }}>{todayVisits.filter(visit => visit.status !== "completed").length} PENDING</Text> : undefined} />
          {loading && <LoadingCard />}
          {!loading && todayVisits.length === 0 && <EmptyCard text="No visits scheduled for today — you're all clear" />}
          {!loading && todayVisits.length > 0 && (
            <SectionScroller count={todayVisits.length} threshold={3} maxHeight={340}>
              {todayVisits.map((v, i) => {
                const isNext = i === 0;
                const last = i === todayVisits.length - 1;
                const trial = v.trial_id ? trialById[v.trial_id] : null;
                const patient = v.patient_id ? patientById[v.patient_id] : null;
                const assignedPi = patient?.pi_id
                  ? (dashboard as any)?.team?.find((member: any) => member.id === patient.pi_id)?.full_name
                  : "";
                const visitName = v.name || "Visit";
                return (
                  <View key={v.id} style={{ flexDirection: "row", gap: 12 }}>
                    <View style={{ alignItems: "center", paddingTop: 4 }}>
                      <View style={[
                        { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center", borderWidth: 2, zIndex: 1 },
                        { backgroundColor: C.card, borderColor: isNext ? C.info : C.border },
                      ]}>
                        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: isNext ? C.info : "rgba(123,95,115,0.30)" }} />
                      </View>
                      {!last && <View style={{ width: 2, flex: 1, marginVertical: 4, borderRadius: 1, backgroundColor: C.border }} />}
                    </View>
                    <View style={[st.visitCard, isNext && { borderColor: "rgba(123,107,184,0.40)" }]}>
                      <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
                            <Text style={{ fontSize: 14, fontWeight: "700", color: C.fg }}>{v.subject_label || "Subject"}</Text>
                            {patient?.avatar_initials ? <Text style={{ fontSize: 12, color: C.muted }}>· {patient.avatar_initials}</Text> : null}
                            {isNext && (
                              <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(123,107,184,0.10)" }}>
                                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.info }} />
                                <Text style={{ fontSize: 10, fontWeight: "700", color: C.info }}>Up next</Text>
                              </View>
                            )}
                          </View>
                          {trial ? <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }} numberOfLines={1}>{trial.protocol_id} · {trial.title}</Text> : null}
                          {assignedPi ? <Text style={{ fontSize: 11, color: C.info, marginTop: 4, fontWeight: "600" }}>Principal Investigator: {assignedPi}</Text> : null}
                          <View style={{ flexDirection: "row", alignItems: "center", marginTop: 6, gap: 6, flexWrap: "wrap" }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 }}>
                              <Clock size={11} color={C.muted} /><Text style={{ fontSize: 11, fontFamily: "monospace" as any, fontWeight: "600", color: C.fg }}>{fmtTime(v.scheduled_date || null)}</Text>
                            </View>
                            <Text style={{ fontSize: 11, color: C.muted }}>{visitName}</Text>
                          </View>
                        </View>
                        <Pressable testID={`update-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.patient_id || "" } })}>
                          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.updateBtn}>
                            <Text style={{ color: C.primaryFg, fontSize: 12, fontWeight: "700" }}>Update</Text>
                          </LinearGradient>
                        </Pressable>
                      </View>
                    </View>
                  </View>
                );
              })}
            </SectionScroller>
          )}

          {/* Overdue */}
          {!loading && overdueVisits.length > 0 && (
            <>
              <SectionLabel label="OVERDUE" tone={C.destructive} action={
                <View style={{ minWidth: 20, height: 20, borderRadius: 10, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", paddingHorizontal: 6 }}>
                  <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 10 }}>{overdueVisits.length}</Text>
                </View>
              } />
              <SectionScroller count={overdueVisits.length} threshold={2} maxHeight={292}>
                {overdueVisits.map(v => (
                  <View key={v.id} style={st.overdueCard}>
                    <View style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6, backgroundColor: C.destructive }} />
                    <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12, padding: 16, paddingLeft: 20 }}>
                      <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" }}>
                        <AlertTriangle size={20} color={C.destructive} />
                      </View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Text style={{ fontSize: 14, fontWeight: "700", color: C.fg }}>{v.subtitle}</Text>
                        <Text style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{v.title.replace(/^Overdue: /, "")}{v.trial_id && trialById[v.trial_id] ? ` · ${trialById[v.trial_id].protocol_id}` : ""}</Text>
                        <Text style={{ fontSize: 12, color: C.destructive, marginTop: 4, fontWeight: "600" }}>{daysLate(v.due)} {daysLate(v.due) === 1 ? "day" : "days"} overdue · Was due {fmtDate(v.due)}</Text>
                      </View>
                      <Pressable testID={`review-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.patient_id || "" } })} style={{ backgroundColor: C.destructive, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 }}>
                        <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 12 }}>Review</Text>
                      </Pressable>
                    </View>
                  </View>
                ))}
              </SectionScroller>
            </>
          )}
        </View>
      </ScrollView>

      {/* Bottom nav */}
      <View style={st.tabBar}>
        <TabItem icon={Home} label="Dashboard" active />
        <TabItem icon={Users} label="Patients" onPress={() => router.push("/(app)/clinical/patients")} testID="tab-patients" />
        <TabItem icon={MessageCircle} label="Messages" onPress={() => router.push("/(app)/chat")} testID="tab-messages" />
        <TabItem icon={CalIcon} label="Calendar" onPress={() => router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: "crc" } } as any)} testID="tab-calendar" />
        <TabItem icon={User} label="Me" onPress={() => router.push("/(app)/clinical/profile")} testID="tab-me" />
      </View>

      <Modal
        visible={!!outcomeTask}
        transparent
        animationType="slide"
        onRequestClose={closeOutcomeSheet}
      >
        <View style={st.outcomeBackdrop}>
          <Pressable
            accessibilityLabel="Close visit outcome"
            style={StyleSheet.absoluteFill}
            onPress={closeOutcomeSheet}
          />
          <View style={st.outcomeSheet}>
            <View style={st.outcomeHandle} />
            <View style={st.outcomeHeader}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={st.outcomeEyebrow}>UPDATE VISIT</Text>
                <Text style={st.outcomeTitle}>Select an outcome</Text>
                <Text style={st.outcomeSubtitle} numberOfLines={2}>
                  {outcomeTask ? `${outcomeTask.subtitle} · ${outcomeTask.visit_name || "Visit"}` : ""}
                </Text>
              </View>
              <Pressable
                testID="outcome-close"
                accessibilityLabel="Close"
                onPress={closeOutcomeSheet}
                disabled={savingOutcome}
                style={st.outcomeClose}
              >
                <X size={19} color={C.muted} />
              </Pressable>
            </View>

            <View style={st.outcomeGrid}>
              {VISIT_OUTCOMES.map(option => {
                const selected = selectedOutcome === option.value;
                return (
                  <Pressable
                    key={option.value}
                    testID={`outcome-${option.value}`}
                    accessibilityRole="radio"
                    accessibilityState={{ selected }}
                    onPress={() => setSelectedOutcome(option.value)}
                    disabled={savingOutcome}
                    style={[st.outcomeOption, selected && st.outcomeOptionSelected]}
                  >
                    <View style={[st.outcomeRadio, selected && st.outcomeRadioSelected]}>
                      {selected ? <Check size={13} color={C.primaryFg} strokeWidth={3} /> : null}
                    </View>
                    <Text style={[st.outcomeOptionText, selected && st.outcomeOptionTextSelected]}>
                      {option.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            {!!outcomeError && (
              <View style={st.outcomeError}>
                <AlertTriangle size={15} color={C.destructive} />
                <Text style={st.outcomeErrorText}>{outcomeError}</Text>
              </View>
            )}

            <View style={st.outcomeActions}>
              <Pressable
                testID="outcome-cancel"
                onPress={closeOutcomeSheet}
                disabled={savingOutcome}
                style={st.outcomeCancel}
              >
                <Text style={st.outcomeCancelText}>Cancel</Text>
              </Pressable>
              <Pressable
                testID="outcome-save"
                accessibilityState={{ disabled: !selectedOutcome || savingOutcome }}
                onPress={saveVisitOutcome}
                disabled={!selectedOutcome || savingOutcome}
                style={{ flex: 1 }}
              >
                <LinearGradient
                  colors={selectedOutcome ? DAWN as any : [C.border, C.border] as any}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 1 }}
                  style={st.outcomeSave}
                >
                  {savingOutcome
                    ? <ActivityIndicator size="small" color={C.primaryFg} />
                    : <Text style={st.outcomeSaveText}>Save outcome</Text>}
                </LinearGradient>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────
function Text(props: any) {
  return <RNText {...props} style={[{ color: C.fg }, props.style]} />;
}

function SectionScroller({
  children,
  count,
  threshold,
  maxHeight,
}: {
  children: React.ReactNode;
  count: number;
  threshold: number;
  maxHeight: number;
}) {
  if (count <= threshold) {
    return <View style={{ gap: 8 }}>{children}</View>;
  }

  return (
    <View style={[st.sectionScrollFrame, { maxHeight }]}>
      <ScrollView
        nestedScrollEnabled
        showsVerticalScrollIndicator
        persistentScrollbar
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={st.sectionScrollContent}
      >
        {children}
      </ScrollView>
      <LinearGradient
        pointerEvents="none"
        colors={["rgba(251,242,232,0)", C.bg] as any}
        style={st.sectionScrollFade}
      />
    </View>
  );
}

function LoadingCard() {
  return (
    <View style={[st.visitCard, { alignItems: "center", justifyContent: "center", paddingVertical: 28, marginBottom: 0 }]}>
      <ActivityIndicator color={C.primary} />
    </View>
  );
}

function DashboardError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={st.dashboardError}>
      <AlertTriangle size={18} color={C.destructive} />
      <View style={{ flex: 1 }}>
        <Text style={st.dashboardErrorTitle}>Dashboard couldn’t load</Text>
        <Text style={st.dashboardErrorCopy}>{message}</Text>
      </View>
      <Pressable testID="crc-dashboard-retry" onPress={onRetry} style={st.dashboardRetry}>
        <Text style={st.dashboardRetryText}>Retry</Text>
      </Pressable>
    </View>
  );
}

function EmptyCard({ text }: { text: string }) {
  return (
    <View style={[st.visitCard, { marginBottom: 0 }]}>
      <Text style={{ color: C.muted, fontSize: 13 }}>{text}</Text>
    </View>
  );
}

function SectionLabel({ label, action, tone }: { label: string; action?: React.ReactNode; tone?: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 24, marginBottom: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        {tone === C.destructive ? (
          <View style={{ width: 4, height: 14, borderRadius: 2, backgroundColor: C.destructive }} />
        ) : (
          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ width: 4, height: 14, borderRadius: 2 }} />
        )}
        <Text style={{ color: tone || C.muted, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      </View>
      {action}
    </View>
  );
}

function StatTile({ icon: Icon, iconColor, iconBg, glow, value, label, onPress }: any) {
  return (
    <Pressable disabled={!onPress} onPress={onPress} style={st.statTile}>
      <View style={{ position: "absolute", top: -24, right: -24, width: 64, height: 64, borderRadius: 32, backgroundColor: glow }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
        <View style={{ width: 36, height: 36, borderRadius: 12, backgroundColor: iconBg, alignItems: "center", justifyContent: "center" }}>
          <Icon size={18} color={iconColor} />
        </View>
        <ArrowUpRight size={14} color="rgba(123,95,115,0.45)" />
      </View>
      {value == null
        ? <Text style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32 }}>–</Text>
        : <AnimatedCount value={value} style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32, fontVariant: ["tabular-nums"] }} />}
      <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</Text>
    </Pressable>
  );
}

function QuickAction({ icon: Icon, bgGradient, bgColor, iconColor, label, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={st.quickAction}>
      {bgGradient ? (
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={iconColor} />
        </LinearGradient>
      ) : (
        <View style={{ width: 48, height: 48, borderRadius: 16, backgroundColor: bgColor, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={iconColor} />
        </View>
      )}
      <Text style={{ fontSize: 12, fontWeight: "500", color: C.fg, marginTop: 8, textAlign: "center" }}>{label}</Text>
    </Pressable>
  );
}

function TaskCard({
  task,
  busy,
  onComplete,
  onOpen,
}: {
  task: Task;
  busy: boolean;
  onComplete: () => void;
  onOpen: () => void;
}) {
  const meta = {
    high: { fg: C.destructive, bg: "rgba(192,57,43,0.12)" },
    medium: { fg: C.warning, bg: "rgba(216,154,60,0.15)" },
    low: { fg: C.info, bg: "rgba(123,107,184,0.12)" },
  }[task.priority];
  const isAdminTask = task.type === "admin_task";
  const isVisitAlert = task.type === "visit_today"
    || task.type === "window_closes_today"
    || task.type === "overdue_visit";
  const Icon = task.type === "unread_messages"
    ? MessageCircle
    : task.type === "schedule_review"
      ? ClipboardCheck
      : Clock;
  const action = task.due_label || (task.type === "unread_messages" ? "Reply" : "Open");

  return (
    <View style={st.taskCard}>
      <View style={[st.taskRail, { backgroundColor: meta.fg }]} />
      <Pressable
        testID={`task-action-${task.id}`}
        onPress={isAdminTask || isVisitAlert ? onComplete : onOpen}
        disabled={busy}
        style={[st.taskOrb, { backgroundColor: meta.bg }]}
      >
        {busy
          ? <ActivityIndicator size="small" color={meta.fg} />
          : isAdminTask || isVisitAlert
            ? <Check size={19} color={meta.fg} strokeWidth={3} />
            : <Icon size={18} color={meta.fg} />}
      </Pressable>
      <Pressable onPress={onOpen} disabled={busy} style={{ flex: 1, minWidth: 0 }}>
        <Text style={st.taskTitle} numberOfLines={2}>{task.title}</Text>
        <Text style={st.taskSubtitle} numberOfLines={1}>{task.subtitle || "Open task details"}</Text>
      </Pressable>
      <View style={{ alignItems: "flex-end", gap: 5 }}>
        <View style={[st.priorityPill, { backgroundColor: meta.bg }]}>
          <Text style={[st.priorityText, { color: meta.fg }]}>{task.priority.toUpperCase()}</Text>
        </View>
        <Text style={[st.taskActionText, { color: meta.fg }]}>{busy ? "Saving…" : action}</Text>
      </View>
    </View>
  );
}

function TrialPanel({ tr, patientCount, onPress }: any) {
  const status = tr.status ? tr.status.charAt(0).toUpperCase() + tr.status.slice(1) : "Active";
  return (
    <Pressable testID={`trial-${tr.id}`} onPress={onPress} style={st.trialPanel}>
      <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6 }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.55)" }}>
          <Text style={{ fontFamily: "monospace" as any, fontSize: 11, fontWeight: "700", color: C.primary }}>{tr.protocol_id}</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.15)" }}>
            <Text style={{ fontSize: 11, fontWeight: "700", color: C.success }}>{status}</Text>
          </View>
          <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" }}>
            <ArrowUpRight size={14} color="rgba(123,95,115,0.7)" />
          </View>
        </View>
      </View>
      <Text style={{ fontSize: 16, fontWeight: "700", color: C.fg, marginBottom: 10 }} numberOfLines={2}>{tr.title}</Text>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {tr.phase ? <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={tr.phase} /> : null}
        {tr.condition ? <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={tr.condition} /> : null}
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
        {[
          { label: "SPONSOR", val: tr.sponsor_name || "—" },
          { label: "MY PATIENTS", val: `${patientCount} enrolled` },
        ].map(f => (
          <View key={f.label} style={{ width: "50%", marginBottom: 8 }}>
            <Text style={{ fontSize: 9, fontWeight: "700", letterSpacing: 1.2, color: "rgba(123,95,115,0.65)" }}>{f.label}</Text>
            <Text style={{ fontSize: 12, fontWeight: "500", color: C.fg, marginTop: 2 }} numberOfLines={1}>{f.val}</Text>
          </View>
        ))}
      </View>
    </Pressable>
  );
}

function Tag({ bg, fg, label }: any) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><Text style={{ fontSize: 11, fontWeight: "700", color: fg }}>{label}</Text></View>;
}

function ProgressRing({ value, size, stroke, children }: any) {
  const animatedValue = useAnimatedProgress(value);
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.w20} strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.primaryFg} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - animatedValue)} />
      </Svg>
      <View style={{ alignItems: "center" }}>{children}</View>
    </View>
  );
}

function TabItem({ icon: Icon, label, active, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 8 }}>
      <Icon size={22} color={active ? C.primary : C.muted} />
      <Text style={{ fontSize: 10, fontWeight: active ? "700" : "500", color: active ? C.primary : C.muted, marginTop: 4 }}>{label}</Text>
      {active && <View style={{ position: "absolute", top: 0, height: 3, width: 32, backgroundColor: C.primary, borderRadius: 2 }} />}
    </Pressable>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 56, overflow: "hidden" },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrowLight: { color: C.w65, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontSize: 28, fontWeight: "700", letterSpacing: -0.4 },
  heroSubtitle: { color: C.primaryFg, fontSize: 22, fontWeight: "700", letterSpacing: -0.2, marginTop: 2 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: C.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.w20 },
  bellBadge: { position: "absolute", top: -2, right: -2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.primaryDeep },
  bellBadgeText: { color: C.primaryFg, fontSize: 10, fontWeight: "700" },
  dayDeck: { flexDirection: "row", alignItems: "center", marginTop: 20 },
  heroChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: C.w15, borderWidth: 1, borderColor: C.w15 },
  heroChipText: { color: C.primaryFg, fontSize: 12, fontWeight: "700" },
  statTile: { flex: 1, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 14, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 },
  quickAction: { flex: 1, alignItems: "center", paddingVertical: 14, paddingHorizontal: 8, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  trialPanel: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, paddingLeft: 18, overflow: "hidden", position: "relative", shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  taskProgressRow: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 10 },
  taskProgressTrack: { flex: 1, flexDirection: "row", gap: 4 },
  taskProgressSegment: { height: 6, flex: 1, borderRadius: 999 },
  taskProgressCount: { color: C.muted, fontSize: 11, fontWeight: "700", fontVariant: ["tabular-nums"] },
  taskCard: { position: "relative", overflow: "hidden", flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingVertical: 12, paddingHorizontal: 14, paddingLeft: 17, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  taskRail: { position: "absolute", left: 0, top: 8, bottom: 8, width: 4, borderRadius: 2 },
  taskOrb: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  taskTitle: { color: C.fg, fontSize: 14, fontWeight: "600", lineHeight: 18 },
  taskSubtitle: { color: C.muted, fontSize: 12, marginTop: 2 },
  priorityPill: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3 },
  priorityText: { fontSize: 9, fontWeight: "700", letterSpacing: 0.7 },
  taskActionText: { fontSize: 10, fontWeight: "700" },
  sectionScrollFrame: { position: "relative", overflow: "hidden", borderRadius: 18 },
  sectionScrollContent: { gap: 8, paddingRight: 5, paddingBottom: 18 },
  sectionScrollFade: { position: "absolute", left: 0, right: 5, bottom: 0, height: 24 },
  caughtUpCard: { alignItems: "center", backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 22 },
  caughtUpIcon: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  caughtUpTitle: { color: C.fg, fontSize: 16, fontWeight: "700", marginTop: 10 },
  caughtUpText: { color: C.muted, fontSize: 12, marginTop: 3, textAlign: "center" },
  taskError: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10, padding: 11, borderRadius: 14, backgroundColor: "rgba(192,57,43,0.08)", borderWidth: 1, borderColor: "rgba(192,57,43,0.22)" },
  taskErrorText: { flex: 1, color: C.destructive, fontSize: 12, lineHeight: 16 },
  dashboardError: { marginBottom: 12, padding: 13, flexDirection: "row", alignItems: "center", gap: 10, borderRadius: 16, borderWidth: 1, borderColor: "rgba(192,57,43,0.28)", backgroundColor: "rgba(192,57,43,0.08)" },
  dashboardErrorTitle: { color: C.destructive, fontSize: 13, fontWeight: "700" },
  dashboardErrorCopy: { marginTop: 2, color: C.muted, fontSize: 11 },
  dashboardRetry: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, backgroundColor: C.destructive },
  dashboardRetryText: { color: C.primaryFg, fontSize: 11, fontWeight: "700" },
  visitCard: { flex: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 12, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  updateBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 999 },
  overdueCard: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: "rgba(192,57,43,0.30)", overflow: "hidden", position: "relative", marginBottom: 12 },
  outcomeBackdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(46,27,51,0.42)" },
  outcomeSheet: { backgroundColor: C.card, borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingHorizontal: 20, paddingTop: 10, paddingBottom: 30, borderWidth: 1, borderBottomWidth: 0, borderColor: C.border },
  outcomeHandle: { width: 42, height: 4, borderRadius: 2, backgroundColor: C.border, alignSelf: "center", marginBottom: 16 },
  outcomeHeader: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  outcomeEyebrow: { fontSize: 9, fontWeight: "800", letterSpacing: 1.4, color: C.primary },
  outcomeTitle: { fontSize: 20, lineHeight: 26, fontWeight: "700", color: C.fg, marginTop: 3 },
  outcomeSubtitle: { fontSize: 12, color: C.muted, marginTop: 3 },
  outcomeClose: { width: 36, height: 36, borderRadius: 18, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  outcomeGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 20 },
  outcomeOption: { width: "48%", minHeight: 52, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 16, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, paddingHorizontal: 12 },
  outcomeOptionSelected: { borderColor: C.primary, backgroundColor: C.secondary },
  outcomeRadio: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  outcomeRadioSelected: { borderColor: C.primary, backgroundColor: C.primary },
  outcomeOptionText: { flex: 1, fontSize: 12, fontWeight: "700", color: C.muted },
  outcomeOptionTextSelected: { color: C.primaryDeep },
  outcomeError: { flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 14, borderWidth: 1, borderColor: "rgba(192,57,43,0.30)", backgroundColor: "rgba(192,57,43,0.08)", padding: 11, marginTop: 14 },
  outcomeErrorText: { flex: 1, fontSize: 12, fontWeight: "600", color: C.destructive },
  outcomeActions: { flexDirection: "row", gap: 10, marginTop: 20 },
  outcomeCancel: { flex: 1, height: 50, borderRadius: 16, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  outcomeCancelText: { fontSize: 13, fontWeight: "700", color: C.muted },
  outcomeSave: { height: 50, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  outcomeSaveText: { fontSize: 13, fontWeight: "700", color: C.primaryFg },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
