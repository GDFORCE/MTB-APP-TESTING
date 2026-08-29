import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable, ActivityIndicator } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { AlertTriangle, Check, Calendar as CalIcon, Building2, Phone, Home, Pencil, Pill, RotateCcw, Sparkles, X } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";
import { formatIsoCalendarDate, formatVisitTiming } from "@/src/lib/visit-timing";

type Slot = { time: string; label?: string };
type Med = { id: string; name: string; dosage: string; route?: string; schedule?: Slot[]; start_date?: string; end_date?: string | null; active?: boolean };
type Dose = { id?: string; medication_id: string; date: string; time: string; status: string; logged_at?: string };
type UiStatus = "taken" | "pending" | "notTaken" | "skipped" | "remindLater";
type DoseStatus = "taken" | "not_taken" | "skipped" | "remind_later";

// Adherence days are UTC-based (product decision) — anchor "today" and dose logs to UTC.
const todayStr = new Date().toISOString().slice(0, 10);

const uiStatus = (s?: string): UiStatus =>
  s === "taken" ? "taken" : s === "skipped" ? "skipped" : s === "not_taken" ? "notTaken" : s === "remind_later" ? "remindLater" : "pending";

function fmtTime(t?: string): string {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  if (isNaN(h)) return t;
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m || 0).padStart(2, "0")} ${ap}`;
}

function fmtLoggedAt(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

function fmtDate(ymd?: string | null): string {
  if (!ymd) return "";
  const d = new Date(ymd + "T00:00:00Z");
  if (isNaN(d.getTime())) return ymd;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

const HIST = {
  taken: { t: "Taken ✓", c: colors.success },
  skipped: { t: "Skipped", c: colors.warning },
  not_taken: { t: "Not taken", c: colors.destructive },
  remind_later: { t: "Remind later", c: colors.info },
} as const;

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <View style={s.legendItem}>
      <View style={[s.legendDot, { backgroundColor: color }]} />
      <Small>{label}</Small>
    </View>
  );
}

export default function MyTrial() {
  const router = useRouter();
  const params = useLocalSearchParams<{ tab?: string; medTab?: string }>();
  const [visits, setVisits] = useState<any[]>([]);
  const [meds, setMeds] = useState<Med[]>([]);
  const [doses, setDoses] = useState<Dose[]>([]);
  const [adherence, setAdherence] = useState<any>(null);
  const [trial, setTrial] = useState<any>(null);
  const [tab, setTab] = useState<"visits" | "medications" | "progress">(params.tab === "medications" || params.tab === "progress" ? params.tab : "visits");
  const [medTab, setMedTab] = useState<"today" | "schedule" | "history">(params.medTab === "schedule" || params.medTab === "history" ? params.medTab : "today");
  const [loading, setLoading] = useState(true);
  const [doseError, setDoseError] = useState<string | null>(null);
  const [doseFeedback, setDoseFeedback] = useState<string | null>(null);
  const [editingDose, setEditingDose] = useState<string | null>(null);
  const [showCompletedDoses, setShowCompletedDoses] = useState(false);
  const [savingDose, setSavingDose] = useState<string | null>(null);
  const [medLoadError, setMedLoadError] = useState<string | null>(null);

  const loadMedicationData = useCallback(async () => {
    const from = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
    setMedLoadError(null);
    try {
      const m = await api.get("/medications").then(r => r.data);
      const medicationRows: Med[] = Array.isArray(m) ? m : [];
      setMeds(medicationRows);
      const doseLists = await Promise.all(medicationRows.map((med: Med) =>
        api.get(`/medications/${med.id}/doses`, { params: { from, to: todayStr } }).then(r => r.data)));
      setDoses(doseLists.flat());
      const a = await api.get("/adherence").then(r => r.data);
      setAdherence(a);
    } catch (error: any) {
      setMedLoadError(error?.response?.data?.detail || "Couldn't load medication history.");
    }
  }, []);

  useEffect(() => { (async () => {
    try {
      const [v, t] = await Promise.all([
        api.get("/visits/mine").then(r => r.data).catch(() => []),
        api.get("/trials").then(r => r.data).catch(() => []),
      ]);
      setVisits(v || []);
      const enrolledId = (v || []).find((row: any) => row.trial_id)?.trial_id;
      setTrial(enrolledId && Array.isArray(t) ? t.find((row: any) => row.id === enrolledId) || null : null);
      await loadMedicationData();
    } finally {
      setLoading(false);
    }
  })(); }, [loadMedicationData]);

  const refreshAdherence = () => api.get("/adherence").then(r => setAdherence(r.data)).catch(() => {});

  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length;
  const next = [...visits].filter(v => v.status === "upcoming" || v.status === "scheduled")
    .sort((a, b) => String(a.scheduled_date || "").localeCompare(String(b.scheduled_date || "")))[0];
  const pct = total ? Math.round((completed / total) * 100) : 0;
  const trialLine = trial ? [trial.protocol_id, trial.condition].filter(Boolean).join(" · ") : "";

  const activeMeds = useMemo(
    () => meds.filter(m => m.active !== false && (m.schedule?.length ?? 0) > 0),
    [meds],
  );

  // Today's expected doses = one row per (med, schedule slot), status from today's logs.
  const todayEntries = useMemo(() => {
    const list = activeMeds.flatMap(m => (m.schedule || []).map(s => {
      const log = doses.find(d => d.medication_id === m.id && d.date === todayStr && d.time === s.time);
      return {
        key: `${m.id}|${s.time}`, medId: m.id, name: m.name, dosage: m.dosage,
        time: s.time, status: uiStatus(log?.status), loggedAt: log?.logged_at,
      };
    }));
    return list.sort((a, b) => a.time.localeCompare(b.time));
  }, [activeMeds, doses]);

  const takenCount = todayEntries.filter(e => e.status === "taken").length;
  const allDone = todayEntries.length > 0 && takenCount === todayEntries.length;

  const medById = useMemo(() => Object.fromEntries(meds.map(m => [m.id, m])), [meds]);
  const history = useMemo(() => {
    const byDate: Record<string, Dose[]> = {};
    doses.forEach(d => { (byDate[d.date] ||= []).push(d); });
    return Object.keys(byDate).sort().reverse().map(date => ({
      date,
      items: byDate[date].slice().sort((a, b) => a.time.localeCompare(b.time)).map(d => ({
        name: `${medById[d.medication_id]?.name ?? "Medication"} ${medById[d.medication_id]?.dosage ?? ""}`.trim(),
        time: fmtTime(d.time),
        status: d.status,
      })),
    }));
  }, [doses, medById]);

  const adherenceDays = useMemo(() => {
    const logBySlot = new Map(
      doses.map(dose => [`${dose.medication_id}|${dose.date}|${dose.time}`, dose]),
    );
    return Array.from({ length: 30 }, (_, index) => {
      const date = new Date(`${todayStr}T00:00:00Z`);
      date.setUTCDate(date.getUTCDate() - (29 - index));
      const ymd = date.toISOString().slice(0, 10);
      const expected = activeMeds.flatMap(med => {
        const activeOnDate = (!med.start_date || med.start_date <= ymd)
          && (!med.end_date || med.end_date >= ymd);
        if (!activeOnDate) return [];
        return (med.schedule || []).map(slot => ({
          log: logBySlot.get(`${med.id}|${ymd}|${slot.time}`),
        }));
      });
      const taken = expected.filter(item => item.log?.status === "taken").length;
      const recordedMissed = expected.filter(item =>
        item.log && item.log.status !== "taken" && item.log.status !== "remind_later"
      ).length;
      const deferred = expected.filter(item => item.log?.status === "remind_later").length;
      const unlogged = expected.filter(item => !item.log).length;
      const missed = recordedMissed + (ymd < todayStr ? unlogged : 0);
      const pending = ymd === todayStr ? unlogged + deferred : deferred;
      const state = expected.length === 0
        ? "none"
        : taken === expected.length ? "taken"
          : missed > 0 ? "missed"
            : taken > 0 ? "partial" : "pending";
      return { date: ymd, expected: expected.length, taken, missed, pending, state };
    });
  }, [activeMeds, doses]);

  // Optimistic dose log with scoped revert on error (touches only this slot).
  const logDose = async (medId: string, time: string, backend: DoseStatus, correcting = false) => {
    const isSlot = (d: Dose) => d.medication_id === medId && d.date === todayStr && d.time === time;
    const prevSlot = doses.find(isSlot); // restore only this slot's prior state on failure
    const nowIso = new Date().toISOString();
    const slotKey = `${medId}|${time}`;
    const isCorrection = correcting || editingDose === slotKey;
    setSavingDose(slotKey);
    setDoseFeedback(null);
    setDoses(cur => [
      ...cur.filter(d => !isSlot(d)),
      { medication_id: medId, date: todayStr, time, status: backend, logged_at: nowIso },
    ]);
    try {
      await api.post(`/medications/${medId}/doses`, { date: todayStr, time, status: backend });
      setDoseError(null); // clear on next successful action
      setDoseFeedback(isCorrection ? "Dose record corrected." : "Dose saved.");
      setEditingDose(null);
      refreshAdherence();
    } catch {
      // Revert ONLY the failed slot so other slots' concurrent optimistic entries survive.
      setDoses(cur => [
        ...cur.filter(d => !isSlot(d)),
        ...(prevSlot ? [prevSlot] : []),
      ]);
      setDoseError("Couldn't save your dose. The previous record was restored.");
    } finally {
      setSavingDose(null);
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={trialLine} title="My Trial" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }} showsVerticalScrollIndicator={false}>
        {/* Journey progress */}
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Eyebrow color={colors.overlay25}>Your journey</Eyebrow>
            <Body weight="700" color={colors.primaryFg}>{pct}%</Body>
          </View>
          <View style={s.barTrack}><View style={[s.barFill, { width: `${pct}%` }]} /></View>
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 8 }}>
            <Small color={colors.overlay25}>{total ? `${completed} of ${total} visits done` : "Schedule pending"}</Small>
            {next && <Small color={colors.overlay25}>Next · {next.name}</Small>}
          </View>
        </LinearGradient>

        {/* Inner tabs */}
        <View style={s.tabs}>
          {(["visits", "medications", "progress"] as const).map(t => (
            <Pressable key={t} testID={`tab-${t}`} onPress={() => setTab(t)} style={[s.tab, tab === t && s.tabActive]}>
              <Small weight="700" color={tab === t ? colors.foreground : colors.mutedFg} style={{ textTransform: "capitalize", fontWeight: "700" as any }}>{t}</Small>
            </Pressable>
          ))}
        </View>

        {/* VISITS */}
        {tab === "visits" && (
          <View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Open information about this study"
              testID="about-trial-cta"
              onPress={() => router.push("/(app)/patient/about-trial")}
              style={s.aboutTrial}
            >
              <View style={s.aboutTrialIcon}><Building2 size={18} color={colors.primary} /></View>
              <View style={{ flex: 1 }}>
                <Body weight="700">About this study</Body>
                <Small style={{ marginTop: 2 }}>View the study overview, care team, sites, and contacts</Small>
              </View>
              <Small weight="700" color={colors.primary}>View</Small>
            </Pressable>
            <Eyebrow style={{ marginTop: spacing.md, marginBottom: spacing.sm }}>The road ahead</Eyebrow>
            {loading && (
              <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
            )}
            {!loading && visits.length === 0 && (
              <Card><Small color={colors.mutedFg}>No visits scheduled yet</Small></Card>
            )}
            {!loading && visits.map((v, i) => {
              const done = v.status === "completed";
              const isNext = v.status === "upcoming";
              const type = String(v.visit_type || v.type || v.location_type || v.name || "").toLowerCase();
              const isPhone = type.includes("tele") || type.includes("phone") || type.includes("remote");
              const isHome = type.includes("home");
              const Icon = isPhone ? Phone : isHome ? Home : Building2;
              const locationLabel = isPhone ? "Telephonic" : isHome ? "Home visit" : (v.site || "Study site");
              return (
                <Pressable key={v.id} testID={`visit-${v.visit_number}`} onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: v.id } })} style={{ flexDirection: "row", gap: 10, marginBottom: 10 }}>
                  <View style={s.spineWrap}>
                    {i < visits.length - 1 && <View style={[s.spine, done && { backgroundColor: colors.accent }]} />}
                    <View style={[s.node, done && { backgroundColor: colors.accent }, isNext && { backgroundColor: colors.warning }]}>
                      {done ? <Check size={14} color={colors.primaryFg} /> : <Small weight="700" color={isNext ? colors.warningFg : colors.mutedFg}>{v.visit_number}</Small>}
                    </View>
                  </View>
                  <Card style={[{ flex: 1, marginBottom: 0 }, isNext && { borderColor: colors.warning + "66", backgroundColor: colors.warning + "0D" }]}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                      <Body weight="700" style={{ flex: 1 }}>Visit {v.visit_number} · {v.name}</Body>
                      <View style={[s.pill, done && { backgroundColor: colors.accent + "22" }, isNext && { backgroundColor: colors.warning + "22" }]}>
                        <Small color={done ? colors.accent : isNext ? colors.warning : colors.info} weight="700">{done ? "Done" : isNext ? "Next →" : "Scheduled"}</Small>
                      </View>
                    </View>
                    <Small color={colors.mutedFg}>{formatVisitTiming(v)}</Small>
                    <View style={{ flexDirection: "row", gap: 12, marginTop: 4 }}>
                      <Small><CalIcon size={11} color={colors.mutedFg} /> {v.scheduled_date ? formatIsoCalendarDate(v.scheduled_date) : "Date pending"}</Small>
                      <Small><Icon size={11} color={colors.mutedFg} /> {locationLabel}</Small>
                    </View>
                  </Card>
                </Pressable>
              );
            })}
          </View>
        )}

        {/* MEDICATIONS */}
        {tab === "medications" && (
          <View>
            <Card style={{ marginTop: spacing.md }}>
              <Eyebrow style={{ marginBottom: 10 }}>{"Today's medications"}</Eyebrow>
              {loading ? (
                <ActivityIndicator color={colors.primary} style={{ alignSelf: "flex-start" }} />
              ) : todayEntries.length === 0 ? (
                <Small color={colors.mutedFg}>No medications prescribed</Small>
              ) : (
                <View style={{ flexDirection: "row", gap: 6 }}>
                  {todayEntries.map(e => <View key={e.key} style={[s.dot, { backgroundColor: e.status === "taken" ? colors.success : e.status === "pending" ? colors.border : e.status === "skipped" ? colors.warning : e.status === "remindLater" ? colors.info : colors.destructive }]} />)}
                  <Small style={{ marginLeft: "auto", fontWeight: "700" as any }}>{allDone ? "All done ✓" : `${takenCount}/${todayEntries.length}`}</Small>
                </View>
              )}
            </Card>
            <View style={s.tabs}>
              {(["today", "schedule", "history"] as const).map(t => (
                <Pressable key={t} onPress={() => setMedTab(t)} style={[s.tab, medTab === t && s.tabActive]}>
                  <Small weight="700" color={medTab === t ? colors.foreground : colors.mutedFg} style={{ textTransform: "capitalize", fontWeight: "700" as any }}>{t}</Small>
                </Pressable>
              ))}
            </View>
            {doseError && (
              <View style={s.errorBanner}>
                <Small weight="700" color={colors.destructive}>{doseError}</Small>
              </View>
            )}
            {doseFeedback && (
              <View accessibilityLiveRegion="polite" style={s.successBanner}>
                <Check size={15} color={colors.success} />
                <Small weight="700" color={colors.success}>{doseFeedback}</Small>
              </View>
            )}
            {medLoadError && (
              <View style={s.loadErrorCard}>
                <AlertTriangle size={18} color={colors.destructive} />
                <View style={{ flex: 1 }}>
                  <Body weight="700">Medication data unavailable</Body>
                  <Small style={{ marginTop: 2 }}>{medLoadError}</Small>
                </View>
                <Pressable testID="retry-medications" onPress={loadMedicationData} style={s.retryButton}>
                  <RotateCcw size={14} color={colors.primaryFg} />
                  <Small weight="700" color={colors.primaryFg}>Retry</Small>
                </Pressable>
              </View>
            )}
            {medTab === "today" && (loading ? (
              <Card style={[s.loadingCard, { marginTop: spacing.md }]}><ActivityIndicator color={colors.primary} /></Card>
            ) : medLoadError ? null : todayEntries.length === 0 ? (
              <Card style={{ marginTop: spacing.md, alignItems: "center", paddingVertical: 24 }}>
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" }}><Pill size={22} color={colors.primary} /></View>
                <Body weight="700" style={{ marginTop: 10 }}>No medications yet</Body>
                <Small>{"Your care team hasn't prescribed any"}</Small>
              </Card>
            ) : allDone && !showCompletedDoses ? (
              <Card style={{ marginTop: spacing.md, alignItems: "center", paddingVertical: 24 }}>
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" }}><Sparkles size={22} color={colors.success} /></View>
                <Body weight="700" style={{ marginTop: 10 }}>All medications done for today!</Body>
                <Small>Great job keeping up 💪</Small>
                <Pressable testID="review-completed-doses" onPress={() => setShowCompletedDoses(true)} style={s.reviewDoses}>
                  <Pencil size={14} color={colors.primary} />
                  <Small weight="700" color={colors.primary}>Review or correct a dose</Small>
                </Pressable>
              </Card>
            ) : todayEntries.map(e => (
              <Card key={e.key} style={{ marginTop: spacing.sm, borderColor: e.status === "taken" ? colors.success + "55" : e.status === "skipped" ? colors.warning + "55" : e.status === "notTaken" ? colors.destructive + "55" : e.status === "remindLater" ? colors.info + "55" : colors.border }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={s.medIcon}><Pill size={20} color={colors.primary} /></View>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                      <Body weight="700">{e.name} {e.dosage}</Body>
                      <View style={[s.pill, { backgroundColor: e.status === "taken" ? colors.success + "22" : e.status === "skipped" ? colors.warning + "22" : e.status === "notTaken" ? colors.destructive + "22" : e.status === "remindLater" ? colors.info + "22" : colors.surface }]}>
                        <Small weight="700" color={e.status === "taken" ? colors.success : e.status === "skipped" ? colors.warning : e.status === "notTaken" ? colors.destructive : e.status === "remindLater" ? colors.info : colors.mutedFg}>{e.status === "taken" ? "Taken ✓" : e.status === "notTaken" ? "Not taken" : e.status === "skipped" ? "Skipped" : e.status === "remindLater" ? "Remind later" : "Pending"}</Small>
                      </View>
                    </View>
                    <Small style={{ marginTop: 2 }}>{fmtTime(e.time)}{e.status === "taken" && e.loggedAt ? ` · logged ${fmtLoggedAt(e.loggedAt)}` : ""}</Small>
                  </View>
                </View>
                {(e.status === "pending" || editingDose === e.key) && (
                  <View style={[s.doseActions, savingDose === e.key && s.disabledActions]}>
                    <Pressable disabled={savingDose === e.key} testID={`med-${e.key}-taken`} onPress={() => logDose(e.medId, e.time, "taken")} style={[s.medBtn, { backgroundColor: colors.success }]}><Small weight="700" color={colors.successFg}>✓ Taken</Small></Pressable>
                    <Pressable disabled={savingDose === e.key} testID={`med-${e.key}-not`} onPress={() => logDose(e.medId, e.time, "not_taken")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.destructive + "66" }]}><Small weight="700" color={colors.destructive}>✗ Not taken</Small></Pressable>
                    <Pressable disabled={savingDose === e.key} testID={`med-${e.key}-skip`} onPress={() => logDose(e.medId, e.time, "skipped")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.warning + "66" }]}><Small weight="700" color={colors.warning}>Skip</Small></Pressable>
                    <Pressable disabled={savingDose === e.key} testID={`med-${e.key}-later`} onPress={() => logDose(e.medId, e.time, "remind_later")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.info + "66" }]}><Small weight="700" color={colors.info}>Later</Small></Pressable>
                  </View>
                )}
                {e.status !== "pending" && editingDose !== e.key && (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Edit ${e.name} ${fmtTime(e.time)} dose record`}
                    testID={`med-${e.key}-edit`}
                    onPress={() => { setEditingDose(e.key); setDoseFeedback(null); }}
                    style={s.editDose}
                  >
                    <Pencil size={14} color={colors.primary} />
                    <Small weight="700" color={colors.primary}>Edit / Correct</Small>
                  </Pressable>
                )}
                {editingDose === e.key && (
                  <Pressable onPress={() => setEditingDose(null)} style={s.cancelEdit}>
                    <X size={13} color={colors.mutedFg} />
                    <Small>Cancel correction</Small>
                  </Pressable>
                )}
              </Card>
            )))}
            {medTab === "schedule" && (
              <View style={{ marginTop: spacing.md }}>
                {loading && (
                  <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
                )}
                {!loading && !medLoadError && activeMeds.length === 0 && (
                  <Card><Small color={colors.mutedFg}>No medications prescribed</Small></Card>
                )}
                {!loading && activeMeds.map(m => (
                  <Card key={m.id} style={{ marginBottom: spacing.sm }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                      <View style={s.medIcon}><Pill size={18} color={colors.primary} /></View>
                      <Body weight="700" style={{ flex: 1 }}>{m.name} {m.dosage}</Body>
                    </View>
                    <View style={{ marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border, gap: 4 }}>
                      <Small>{(m.schedule || []).map(sl => fmtTime(sl.time)).join(" · ") || "No schedule"}</Small>
                      {!!m.route && <Small>Route: {m.route}</Small>}
                      <Small color={colors.mutedFg}>Period: {fmtDate(m.start_date)}{m.end_date ? ` – ${fmtDate(m.end_date)}` : " – ongoing"}</Small>
                    </View>
                  </Card>
                ))}
              </View>
            )}
            {medTab === "history" && (
              <View style={{ marginTop: spacing.md }}>
                {loading && (
                  <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
                )}
                {!loading && !medLoadError && activeMeds.length > 0 && (
                  <Card style={{ marginBottom: spacing.md }}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                      <View>
                        <Body weight="700">30-day adherence</Body>
                        <Small style={{ marginTop: 2 }}>Based on your prescribed schedule and dose records</Small>
                      </View>
                      <CalIcon size={18} color={colors.info} />
                    </View>
                    <View style={s.heatmap} accessibilityRole="summary">
                      {adherenceDays.map(day => {
                        const tone = day.state === "taken"
                          ? colors.success
                          : day.state === "missed" ? colors.destructive
                            : day.state === "partial" ? colors.warning
                              : day.state === "pending" ? colors.info : colors.surface;
                        const label = day.expected === 0
                          ? `${fmtDate(day.date)}: no doses scheduled`
                          : `${fmtDate(day.date)}: ${day.taken} of ${day.expected} taken, ${day.missed} missed, ${day.pending} pending`;
                        return (
                          <View
                            key={day.date}
                            accessible
                            accessibilityLabel={label}
                            style={[s.heatDay, { backgroundColor: tone }]}
                          />
                        );
                      })}
                    </View>
                    <View style={s.legend}>
                      <Legend color={colors.success} label="All taken" />
                      <Legend color={colors.warning} label="Partial" />
                      <Legend color={colors.destructive} label="Missed" />
                      <Legend color={colors.info} label="Pending" />
                      <Legend color={colors.surface} label="No schedule" />
                    </View>
                  </Card>
                )}
                {!loading && !medLoadError && activeMeds.length === 0 && (
                  <Card><Small color={colors.mutedFg}>No medication schedule is available for adherence history.</Small></Card>
                )}
                {!loading && !medLoadError && activeMeds.length > 0 && history.length === 0 && (
                  <Card><Small color={colors.mutedFg}>No dose history yet</Small></Card>
                )}
                {!loading && history.map((d, i) => (
                  <View key={i} style={{ marginBottom: spacing.md }}>
                    <Eyebrow style={{ marginBottom: 8 }}>{fmtDate(d.date)}</Eyebrow>
                    <Card padded={false}>
                      {d.items.map((it, j) => {
                        const meta = HIST[it.status as keyof typeof HIST] ?? { t: it.status, c: colors.mutedFg };
                        return (
                          <View key={j} style={[{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.md }, j > 0 && { borderTopWidth: 1, borderColor: colors.border }]}>
                            <View><Body weight="700">{it.name}</Body><Small>{it.time}</Small></View>
                            <View accessible accessibilityLabel={`Dose status: ${meta.t}`} style={[s.pill, { backgroundColor: meta.c + "22" }]}><Small weight="700" color={meta.c}>{meta.t}</Small></View>
                          </View>
                        );
                      })}
                    </Card>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* PROGRESS */}
        {tab === "progress" && (
          <View style={{ marginTop: spacing.md }}>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: spacing.sm }}>
              {[{v:completed,l:"Completed",c:colors.accent},{v:visits.filter(v=>v.status==="upcoming").length,l:"Upcoming",c:colors.warning},{v:Math.max(0,total-completed-visits.filter(v=>v.status==="upcoming").length),l:"Remaining",c:colors.foreground},{v:adherence?.rate != null ? `${adherence.rate}%` : "—",l:"Med. rate",c:colors.info}].map((s2,i) => (
                <View key={i} style={[s.statBox, { borderColor: s2.c + "33" }]}>
                  <Body weight="700" color={s2.c} style={{ fontSize: 28 }}>{s2.v}</Body>
                  <Small>{s2.l}</Small>
                </View>
              ))}
            </View>
            <Card style={{ marginTop: spacing.md }}>
              <Body weight="700">Visit completion</Body>
              <View style={[s.barTrackLight, { marginTop: 8 }]}><View style={[s.barFillAccent, { width: `${pct}%` }]} /></View>
              <Small style={{ marginTop: 4 }}>{total ? `${completed} of ${total} visits complete (${pct}%)` : "No visit schedule published yet"}</Small>
            </Card>
            <Card style={{ marginTop: spacing.md }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between" }}><Body weight="700">Medication adherence</Body>{adherence?.rate != null && adherence.rate >= 90 && <Small color={colors.success} weight="700">Excellent!</Small>}</View>
              <View style={[s.barTrackLight, { marginTop: 8 }]}><View style={[s.barFillInfo, { width: `${adherence?.rate ?? 0}%` }]} /></View>
              <Small style={{ marginTop: 4 }}>{adherence?.total ? `${adherence.taken} of ${adherence.total} doses (${adherence.rate}%)` : "No doses expected yet"}</Small>
              {!!adherence?.streak_days && <Small color={colors.mutedFg} style={{ marginTop: 2 }}>🔥 {adherence.streak_days}-day streak</Small>}
            </Card>
          </View>
        )}
      </ScrollView>
      <PatientBottomNav active="visits" />
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md, marginBottom: spacing.md },
  aboutTrial: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: spacing.md, padding: spacing.md, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  aboutTrialIcon: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  barTrack: { height: 8, borderRadius: 4, backgroundColor: colors.overlay25, marginTop: 8, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: colors.white, borderRadius: 4 },
  barTrackLight: { height: 10, borderRadius: 5, backgroundColor: colors.surface, overflow: "hidden" },
  barFillAccent: { height: "100%", backgroundColor: colors.accent, borderRadius: 5 },
  barFillInfo: { height: "100%", backgroundColor: colors.info, borderRadius: 5 },
  tabs: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: 999, padding: 4 },
  tab: { flex: 1, paddingVertical: 10, alignItems: "center", borderRadius: 999 },
  tabActive: { backgroundColor: colors.card },
  spineWrap: { width: 28, alignItems: "center", paddingTop: 14 },
  spine: { position: "absolute", top: 32, bottom: -10, width: 2, backgroundColor: colors.border, borderRadius: 1 },
  node: { width: 28, height: 28, borderRadius: 14, backgroundColor: colors.card, borderWidth: 2, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  pill: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: colors.info + "1A" },
  medIcon: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  medBtn: { flex: 1, paddingVertical: 8, borderRadius: 12, alignItems: "center" },
  doseActions: { flexDirection: "row", gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: colors.border },
  disabledActions: { opacity: 0.55 },
  dot: { flex: 1, height: 10, borderRadius: 5 },
  statBox: { flex: 1, minWidth: "47%", padding: 14, borderRadius: radii.lg, backgroundColor: colors.card, borderWidth: 1, alignItems: "center" },
  loadingCard: { alignItems: "center", justifyContent: "center", paddingVertical: 28 },
  errorBanner: { marginTop: spacing.sm, padding: spacing.sm, borderRadius: radii.lg, backgroundColor: colors.destructive + "14", borderWidth: 1, borderColor: colors.destructive + "40" },
  successBanner: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: spacing.sm, padding: spacing.sm, borderRadius: radii.lg, backgroundColor: colors.success + "14", borderWidth: 1, borderColor: colors.success + "40" },
  loadErrorCard: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: spacing.sm, padding: spacing.md, borderRadius: radii.lg, backgroundColor: colors.destructive + "0D", borderWidth: 1, borderColor: colors.destructive + "40" },
  retryButton: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, backgroundColor: colors.primary },
  reviewDoses: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.md, paddingHorizontal: 14, paddingVertical: 9, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "55", backgroundColor: colors.primary + "0D" },
  editDose: { flexDirection: "row", alignSelf: "flex-start", alignItems: "center", gap: 6, marginTop: 12, paddingHorizontal: 10, paddingVertical: 7, borderRadius: 999, backgroundColor: colors.primary + "0D" },
  cancelEdit: { flexDirection: "row", alignSelf: "flex-end", alignItems: "center", gap: 4, marginTop: 8, paddingVertical: 4 },
  heatmap: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: spacing.md },
  heatDay: { width: 18, height: 18, borderRadius: 5, borderWidth: 1, borderColor: colors.border },
  legend: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: spacing.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 9, height: 9, borderRadius: 3, borderWidth: 1, borderColor: colors.border },
});
