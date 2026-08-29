// ADM-06 — Trial monitoring (read-only aggregates; NO subject-level PII).
//
// Live, admin-gated endpoints (backend/admin_routes.py · ADMIN TRIALS):
//   list ...... GET /admin/trials         → metadata + enrollment aggregates only
//   detail .... GET /admin/trials/{id}     → aggregates + subjects (ALWAYS masked
//               SUBJ-xxx + initials unless the caller holds an active break-the-
//               glass session — this screen never requests unmasked data).
//
// Operational oversight surface: counts, recruitment progress and schedule
// status. Every number is a server aggregate — no patient records are fetched.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Modal, Animated,
  RefreshControl, StyleSheet, ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, FlaskConical, Building2, Calendar,
  Activity, CheckCircle2, XCircle, Clock, ChevronRight, ShieldAlert,
} from "lucide-react-native";
import { useLocalSearchParams } from "expo-router";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, SearchBar, st } from "./users";

type Visits = { completed?: number; upcoming?: number; missed?: number };
type Subject = { subject?: string; initials?: string; status?: string; enrolled_date?: string };
type Trial = {
  id: string; title?: string; protocol_id?: string; phase?: string; condition?: string;
  sponsor?: string; status?: string; patients?: number; targetEnrollment?: number | null;
  scheduleVersion?: number; schedule_status?: string; visits?: Visits;
  sponsorOrCroName?: string; ownerType?: "Sponsor" | "CRO"; cro?: string;
  lastModified?: string; modifiedBy?: string; modifiedByRole?: string;
  changeSummary?: string; changedFields?: string[];
  subjects?: Subject[]; unmasked?: boolean;
};

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const STATUS_FILTERS = [
  { key: "all", label: "All" }, { key: "active", label: "Active" },
  { key: "completed", label: "Completed" }, { key: "suspended", label: "Suspended" },
];

function statusMeta(status?: string): { fg: string; bg: string; Icon: any } {
  switch ((status || "").toLowerCase()) {
    case "active": return { fg: C.success, bg: "rgba(92,154,110,0.15)", Icon: Activity };
    case "completed": return { fg: C.info, bg: "rgba(123,107,184,0.12)", Icon: CheckCircle2 };
    case "suspended": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)", Icon: XCircle };
    default: return { fg: C.mutedFg, bg: C.surface, Icon: Clock };
  }
}
function cap(s?: string): string { return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—"; }
function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

export default function AdminTrials() {
  const { open } = useAdminDrawer();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selected, setSelected] = useState<Trial | null>(null);

  const [toast, setToast] = useState("");
  const toastAnim = useRef(new Animated.Value(0)).current;
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    Animated.timing(toastAnim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => {
      Animated.timing(toastAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2600);
  }, [toastAnim]);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.get("/admin/trials");
      setTrials(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load trials. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // Global-search deep link: /admin/trials?focus=<id> opens that exact record.
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  const consumedFocus = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || typeof focus !== "string" || focus === consumedFocus.current) return;
    const hit = trials.find((t) => t.id === focus);
    if (hit) { consumedFocus.current = focus; setSelected(hit); }
  }, [focus, trials]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return trials.filter((t) => {
      const matchesSearch = !q ||
        (t.title || "").toLowerCase().includes(q) ||
        (t.protocol_id || "").toLowerCase().includes(q) ||
        (t.sponsor || "").toLowerCase().includes(q);
      const matchesStatus = statusFilter === "all" || (t.status || "").toLowerCase() === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [trials, search, statusFilter]);

  const tiles = useMemo(() => [
    { label: "Total trials", value: trials.length, fg: C.primary },
    { label: "Active", value: trials.filter((t) => (t.status || "").toLowerCase() === "active").length, fg: C.success },
    { label: "Completed", value: trials.filter((t) => (t.status || "").toLowerCase() === "completed").length, fg: C.info },
    { label: "Suspended", value: trials.filter((t) => (t.status || "").toLowerCase() === "suspended").length, fg: C.destructive },
    { label: "Enrolled", value: trials.reduce((n, t) => n + (t.patients || 0), 0), fg: C.primary },
    { label: "Missed visits", value: trials.reduce((n, t) => n + (t.visits?.missed || 0), 0), fg: C.warning },
  ], [trials]);

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} />
        {loading ? (
          <Loading label="Loading trials…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              {tiles.map((t) => (
                <View key={t.label} style={st.tile}>
                  <RNText style={[st.tileValue, { color: t.fg }]}>{t.value}</RNText>
                  <RNText style={st.tileLabel}>{t.label}</RNText>
                </View>
              ))}
            </View>

            <View style={tr.banner}>
              <ShieldAlert size={15} color={C.warning} />
              <RNText style={tr.bannerTxt}>Aggregate oversight only. Subject-level data requires a break-the-glass session.</RNText>
            </View>

            <SearchBar value={search} onChange={setSearch} placeholder="Search protocol, title or sponsor…" />
            <FilterChips label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
            <RNText style={st.countLine}>{filtered.length} of {trials.length} trials</RNText>

            {filtered.length === 0 ? (
              <EmptyCard message="No trials match the current filters." />
            ) : (
              <View style={{ gap: 10 }}>
                {filtered.map((t) => {
                  const sm = statusMeta(t.status);
                  const target = t.targetEnrollment || 0;
                  const pct = target > 0 ? Math.min(100, Math.round(((t.patients || 0) / target) * 100)) : null;
                  return (
                    <Pressable key={t.id} onPress={() => setSelected(t)} style={st.card}>
                      <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 10 }}>
                        <View style={tr.iconWrap}><FlaskConical size={18} color={C.primary} /></View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <RNText style={tr.title} numberOfLines={2}>{t.title || "Untitled trial"}</RNText>
                          <RNText style={tr.protocol} numberOfLines={1}>{t.protocol_id || "—"}{t.phase ? ` · ${t.phase}` : ""}</RNText>
                        </View>
                        <View style={[st.badge, { backgroundColor: sm.bg }]}>
                          <sm.Icon size={11} color={sm.fg} />
                          <RNText style={[st.badgeTxt, { color: sm.fg }]}>{cap(t.status)}</RNText>
                        </View>
                      </View>

                      <View style={tr.metaRow}>
                        <Building2 size={13} color={C.mutedFg} />
                        <RNText style={tr.metaTxt} numberOfLines={1}>{t.sponsor || "Sponsor n/a"}</RNText>
                      </View>

                      <View style={{ marginTop: 10 }}>
                        <View style={{ flexDirection: "row", justifyContent: "space-between", marginBottom: 5 }}>
                          <RNText style={tr.enrollLabel}>Enrolled</RNText>
                          <RNText style={tr.enrollVal}>{t.patients || 0}{target > 0 ? ` / ${target}` : ""}</RNText>
                        </View>
                        {pct !== null ? (
                          <View style={tr.progressTrack}><View style={[tr.progressFill, { width: `${pct}%` }]} /></View>
                        ) : (
                          <RNText style={tr.noTarget}>No enrollment target set</RNText>
                        )}
                      </View>

                      <View style={tr.footer}>
                        <View style={tr.visitPill}><CheckCircle2 size={12} color={C.success} /><RNText style={tr.visitTxt}>{t.visits?.completed || 0}</RNText></View>
                        <View style={tr.visitPill}><Clock size={12} color={C.info} /><RNText style={tr.visitTxt}>{t.visits?.upcoming || 0}</RNText></View>
                        <View style={tr.visitPill}><XCircle size={12} color={C.destructive} /><RNText style={tr.visitTxt}>{t.visits?.missed || 0}</RNText></View>
                        <View style={{ flex: 1 }} />
                        <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            )}
          </View>
        )}
      </ScrollView>

      <TrialSheet trial={selected} onClose={() => setSelected(null)} onError={showToast} />
      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Hero({ onMenu, onRefresh }: { onMenu: () => void; onRefresh: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Trial monitoring</RNText>
          </View>
          <Pressable testID="trials-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Metadata and recruitment aggregates across every trial. No subject-level data.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function FilterChips({ label, chips, value, onChange }: { label: string; chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={{ marginTop: 10 }}>
      <RNText style={st.chipRowLabel}>{label}</RNText>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 8 }}>
        {chips.map((c) => (
          <Pressable key={c.key} onPress={() => onChange(c.key)} style={[st.chip, value === c.key && st.chipActive]}>
            <RNText style={[st.chipTxt, value === c.key && st.chipTxtActive]}>{c.label}</RNText>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

function TrialSheet({ trial, onClose, onError }: { trial: Trial | null; onClose: () => void; onError: (m: string) => void }) {
  const [detail, setDetail] = useState<Trial | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!trial) { setDetail(null); return; }
    setDetail(null); setLoading(true);
    (async () => {
      try {
        const res = await api.get(`/admin/trials/${trial.id}`);
        if (!cancelled) setDetail(res.data);
      } catch (e) {
        if (!cancelled) onError(errMsg(e, "Couldn't load trial detail"));
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [trial, onError]);

  const t = detail || trial;
  const sm = statusMeta(t?.status);

  return (
    <Modal visible={!!trial} transparent animationType="slide" onRequestClose={onClose}>
      <View style={st.sheetOverlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={st.sheet}>
          <View style={st.sheetHeader}>
            <RNText style={st.sheetTitle}>Trial detail</RNText>
            <Pressable onPress={onClose} hitSlop={10} style={st.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
          </View>
          {t && (
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 24, gap: 14 }}>
              <View>
                <RNText style={tr.sheetTitle}>{t.title || "Untitled trial"}</RNText>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 }}>
                  <RNText style={tr.protocol}>{t.protocol_id || "—"}</RNText>
                  <View style={[st.badge, { backgroundColor: sm.bg }]}>
                    <sm.Icon size={11} color={sm.fg} /><RNText style={[st.badgeTxt, { color: sm.fg }]}>{cap(t.status)}</RNText>
                  </View>
                </View>
              </View>

              <View style={tr.detailGrid}>
                <Detail label="Phase" value={t.phase || "—"} />
                <Detail label="Condition" value={t.condition || "—"} />
                <Detail label={t.ownerType || "Sponsor / CRO"} value={t.sponsorOrCroName || t.sponsor || "—"} />
                <Detail label="Schedule" value={`v${t.scheduleVersion ?? 1}${t.schedule_status ? ` · ${t.schedule_status}` : ""}`} />
              </View>

              <View style={{ gap: 2 }}>
                <RNText style={tr.section}>ENROLLMENT</RNText>
                <View style={tr.statRow}>
                  <Stat label="Enrolled" value={t.patients || 0} fg={C.primary} />
                  <Stat label="Target" value={t.targetEnrollment ?? "—"} fg={C.foreground} />
                  <Stat label="Completed" value={t.visits?.completed || 0} fg={C.success} />
                  <Stat label="Missed" value={t.visits?.missed || 0} fg={C.destructive} />
                </View>
              </View>

              <View style={{ gap: 6 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <RNText style={tr.section}>SUBJECTS</RNText>
                  <View style={tr.maskPill}><RNText style={tr.maskPillTxt}>MASKED</RNText></View>
                </View>
                {loading ? (
                  <View style={{ paddingVertical: 16, alignItems: "center" }}><ActivityIndicator color={C.primary} /></View>
                ) : (t.subjects && t.subjects.length > 0) ? (
                  <View style={{ gap: 6 }}>
                    {t.subjects.map((s, i) => (
                      <View key={s.subject || i} style={tr.subjectRow}>
                        <View style={tr.subjectAvatar}><RNText style={tr.subjectInitials}>{s.initials || "—"}</RNText></View>
                        <RNText style={tr.subjectId}>{s.subject || "SUBJ-—"}</RNText>
                        <View style={{ flex: 1 }} />
                        {!!s.status && <RNText style={tr.subjectStatus}>{s.status}</RNText>}
                      </View>
                    ))}
                  </View>
                ) : (
                  <RNText style={tr.emptySubjects}>No enrolled subjects.</RNText>
                )}
                <RNText style={tr.maskNote}>Identifiers are pseudonymized. Unmasking requires an approved break-the-glass session.</RNText>
              </View>

              <View style={{ gap: 2 }}>
                <RNText style={tr.section}>PROVENANCE</RNText>
                <View style={tr.provRow}><Calendar size={13} color={C.mutedFg} /><RNText style={tr.provTxt}>Last modified {fmtDate(t.lastModified)}{t.modifiedBy ? ` · ${t.modifiedBy}` : ""}</RNText></View>
                <View style={tr.changeCard}>
                  <RNText style={tr.changeLabel}>CHANGE SUMMARY</RNText>
                  <RNText style={tr.changeText}>{t.changeSummary || "No recorded changes"}</RNText>
                  {!!t.modifiedByRole && <RNText style={tr.changeActor}>Updated by {t.modifiedBy}{` · ${cap(t.modifiedByRole)}`}</RNText>}
                </View>
              </View>
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View style={tr.detailCell}>
      <RNText style={tr.detailLabel}>{label}</RNText>
      <RNText style={tr.detailValue} numberOfLines={2}>{value}</RNText>
    </View>
  );
}
function Stat({ label, value, fg }: { label: string; value: number | string; fg: string }) {
  return (
    <View style={tr.stat}>
      <RNText style={[tr.statValue, { color: fg }]}>{value}</RNText>
      <RNText style={tr.statLabel}>{label}</RNText>
    </View>
  );
}

const tr = StyleSheet.create({
  banner: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "rgba(216,154,60,0.08)", borderRadius: 12, borderWidth: 1, borderColor: "rgba(216,154,60,0.3)", padding: 12, marginTop: 14 },
  bannerTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
  iconWrap: { width: 38, height: 38, borderRadius: 12, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground, lineHeight: 19 },
  protocol: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 2 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8 },
  metaTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg },
  enrollLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  enrollVal: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground, fontVariant: ["tabular-nums"] },
  progressTrack: { height: 8, borderRadius: 4, backgroundColor: C.border, overflow: "hidden" },
  progressFill: { height: 8, borderRadius: 4, backgroundColor: C.info },
  noTarget: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, fontStyle: "italic" },
  footer: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10 },
  visitPill: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: C.surface, borderRadius: 8, paddingHorizontal: 8, height: 24 },
  visitTxt: { fontFamily: fonts.semibold, fontSize: 11, color: C.foreground, fontVariant: ["tabular-nums"] },

  sheetTitle: { fontFamily: fonts.heading, fontSize: 18, color: C.foreground, lineHeight: 23 },
  detailGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  detailCell: { width: "47%", flexGrow: 1, backgroundColor: C.surface, borderRadius: 12, padding: 12 },
  detailLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  detailValue: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground, marginTop: 2 },
  changeCard: { marginTop: 8, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.surface, padding: 12 },
  changeLabel: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 0.8, color: C.mutedFg },
  changeText: { marginTop: 4, fontFamily: fonts.medium, fontSize: 13, lineHeight: 18, color: C.foreground },
  changeActor: { marginTop: 4, fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  section: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1, color: C.mutedFg, marginBottom: 6 },
  statRow: { flexDirection: "row", gap: 8 },
  stat: { flex: 1, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingVertical: 12, alignItems: "center" },
  statValue: { fontFamily: fonts.display, fontSize: 20, fontVariant: ["tabular-nums"] },
  statLabel: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 2 },
  maskPill: { paddingHorizontal: 8, height: 18, borderRadius: 9, backgroundColor: "rgba(216,154,60,0.15)", alignItems: "center", justifyContent: "center", marginBottom: 6 },
  maskPillTxt: { fontFamily: fonts.bold, fontSize: 9, letterSpacing: 0.8, color: C.warning },
  subjectRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.surface, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8 },
  subjectAvatar: { width: 30, height: 30, borderRadius: 10, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  subjectInitials: { fontFamily: fonts.bold, fontSize: 11, color: C.secondaryFg },
  subjectId: { fontFamily: fonts.mono, fontSize: 12, color: C.foreground },
  subjectStatus: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  emptySubjects: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, paddingVertical: 8 },
  maskNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, fontStyle: "italic", marginTop: 4, lineHeight: 15 },
  provRow: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.surface, borderRadius: 10, padding: 10 },
  provTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
});
