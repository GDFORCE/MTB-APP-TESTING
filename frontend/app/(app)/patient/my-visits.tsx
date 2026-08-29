import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { MapPin, User, Calendar as CalIcon, Clock, Building2, ChevronRight } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";
import { formatIsoCalendarDate, formatVisitTiming } from "@/src/lib/visit-timing";

const fmtTime = (d?: string) =>
  d ? new Date(d).toLocaleTimeString("en-GB", { hour: "numeric", minute: "2-digit", hour12: true }) : "";

export default function MyVisits() {
  const router = useRouter();
  const [trials, setTrials] = useState<any[]>([]);
  const [visits, setVisits] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [trialId, setTrialId] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [t, v] = await Promise.all([api.get("/trials"), api.get("/visits/mine")]);
        const visitRows = Array.isArray(v.data) ? v.data : [];
        const enrolledIds = new Set(visitRows.map((row: any) => row.trial_id).filter(Boolean));
        setTrials((Array.isArray(t.data) ? t.data : []).filter((row: any) => enrolledIds.has(row.id)));
        setVisits(visitRows);
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Couldn't load your visits.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const trial = trials.find(t => t.id === trialId);
  const tv = visits.filter(v => v.trial_id === trialId);
  const completed = tv.filter(v => v.status === "completed").length;
  const pct = tv.length ? Math.round((completed / tv.length) * 100) : 0;
  // site/PI are joined onto each visit instance by the backend (from the
  // patient's assigned PI). Derive per-trial values from that trial's visits.
  const siteFor = (id: string) => visits.find(v => v.trial_id === id)?.site || "";
  const heroSite = tv[0]?.site || "";
  const heroPi = tv[0]?.pi_name || "";

  if (!trialId) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Your trials" title="My Visits" />
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }}>
          {loading ? (
            <View style={{ paddingTop: spacing.xxl, alignItems: "center" }}>
              <ActivityIndicator color={colors.primary} />
            </View>
          ) : error ? (
            <Card style={{ alignItems: "center", paddingVertical: spacing.xl }}>
              <Small color={colors.destructive} weight="700">{error}</Small>
            </Card>
          ) : trials.length === 0 ? (
            <Card style={{ alignItems: "center", paddingVertical: spacing.xl }}>
              <Building2 size={28} color={colors.mutedFg} />
              <Body weight="700" style={{ marginTop: spacing.sm }}>No trials yet</Body>
              <Small style={{ marginTop: 2, textAlign: "center" }}>You are not enrolled in any study yet.</Small>
            </Card>
          ) : (
          <>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Select a trial to view your visits</Eyebrow>
          {trials.map(t => (
            <Pressable key={t.id} testID={`trial-${t.id}`} onPress={() => setTrialId(t.id)}>
              <Card style={{ marginBottom: spacing.md }}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: spacing.sm }}>
                  <View style={st.protoTag}><Small weight="700" color={colors.primary} style={{ fontFamily: "monospace" as any }}>{t.protocol_id}</Small></View>
                  <View style={[st.statusTag, { backgroundColor: colors.success + "22" }]}><Small weight="700" color={colors.success} style={{ textTransform: "capitalize" }}>{t.status}</Small></View>
                </View>
                <Eyebrow color={colors.mutedFg}>Study title</Eyebrow>
                <Body weight="700" style={{ marginTop: 4, marginBottom: spacing.md }}>{t.title}</Body>
                <View style={{ flexDirection: "row", gap: spacing.md, marginBottom: spacing.md }}>
                  <View style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Indication</Eyebrow><Body weight="700" style={{ marginTop: 2, fontSize: 13 }}>{t.condition}</Body></View>
                  <View style={{ flex: 1 }}><Eyebrow color={colors.mutedFg}>Phase</Eyebrow><Body weight="700" style={{ marginTop: 2, fontSize: 13 }}>{t.phase}</Body></View>
                </View>
                <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", paddingTop: spacing.sm, borderTopWidth: 1, borderColor: colors.border }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flex: 1 }}>
                    <MapPin size={14} color={colors.accent} />
                    <Small numberOfLines={1}>{siteFor(t.id) || "Study site"}</Small>
                  </View>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><Small weight="700" color={colors.accent}>View</Small><ChevronRight size={14} color={colors.accent} /></View>
                </View>
              </Card>
            </Pressable>
          ))}
          </>
          )}
        </ScrollView>
        <PatientBottomNav active="visits" />
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={trial?.protocol_id || "Trial"} title="My Visits" onBack={() => setTrialId(null)} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: PATIENT_NAV_CONTENT_BOTTOM }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <View style={st.heroChip}><Small color={colors.primaryFg} weight="700" style={{ fontFamily: "monospace" as any }}>{trial?.protocol_id}</Small></View>
            <View style={st.heroChip}><Small color={colors.primaryFg} weight="700" style={{ textTransform: "capitalize" }}>{trial?.status}</Small></View>
          </View>
          <H1 color={colors.primaryFg} style={{ fontSize: 18, marginTop: spacing.sm }}>{trial?.title}</H1>
          <View style={{ flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm, flexWrap: "wrap" }}>
            {!!heroSite && <View style={st.heroChipSm}><MapPin size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{heroSite}</Small></View>}
            {!!heroPi && <View style={st.heroChipSm}><User size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{heroPi}</Small></View>}
          </View>
          <View style={{ marginTop: spacing.md }}>
            <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
              <Small color={colors.overlay25}>{completed} of {tv.length} visits complete</Small>
              <Small color={colors.primaryFg} weight="700">{pct}%</Small>
            </View>
            <View style={st.bar}><View style={[st.barFill, { width: `${pct}%` }]} /></View>
          </View>
        </LinearGradient>

        <Eyebrow style={{ marginTop: spacing.md, marginBottom: spacing.sm }}>All visits</Eyebrow>
        {tv.length === 0 && <Card><Small color={colors.mutedFg}>No visits have been scheduled for this trial yet.</Small></Card>}
        {tv.map(v => {
          const railColor = v.status === "completed" ? colors.success : v.status === "upcoming" ? colors.warning : v.status === "missed" ? colors.destructive : colors.info;
          return (
            <Pressable key={v.id} testID={`visit-row-${v.visit_number}`} onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: v.id } })}>
              <View style={[st.visitCard, v.status === "upcoming" && { borderColor: colors.warning + "66", backgroundColor: colors.warning + "0D" }]}>
                <View style={[st.rail, { backgroundColor: railColor }]} />
                <View style={[st.visitIcon, { backgroundColor: railColor + "22" }]}><Building2 size={16} color={railColor} /></View>
                <View style={{ flex: 1, paddingLeft: 12 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <Body weight="700">Visit {v.visit_number}</Body>
                    <View style={[st.statusTag, { backgroundColor: railColor + "22" }]}><Small weight="700" color={railColor} style={{ textTransform: "capitalize" }}>{v.status}</Small></View>
                  </View>
                  <Small>{v.name}</Small>
                  <Small color={colors.mutedFg}>{formatVisitTiming(v)}</Small>
                  <View style={{ flexDirection: "row", gap: 12, marginTop: 4 }}>
                    <Small><CalIcon size={11} color={colors.mutedFg} /> {v.scheduled_date ? formatIsoCalendarDate(v.scheduled_date) : "Date pending"}</Small>
                    {!!v.scheduled_date && <Small><Clock size={11} color={colors.mutedFg} /> {fmtTime(v.scheduled_date)}</Small>}
                  </View>
                </View>
                <ChevronRight size={16} color={colors.mutedFg} />
              </View>
            </Pressable>
          );
        })}
      </ScrollView>
      <PatientBottomNav active="visits" />
    </ScreenContainer>
  );
}

const st = StyleSheet.create({
  protoTag: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.secondary },
  statusTag: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  hero: { borderRadius: radii.xl, padding: spacing.md },
  heroChip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  heroChipSm: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  bar: { height: 8, borderRadius: 4, backgroundColor: colors.overlay25, marginTop: 6, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: colors.white, borderRadius: 4 },
  visitCard: { flexDirection: "row", alignItems: "center", padding: 12, paddingLeft: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, marginBottom: spacing.sm, overflow: "hidden" },
  rail: { position: "absolute", left: 0, top: 0, bottom: 0, width: 5 },
  visitIcon: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", marginLeft: 4 },
});
