import React, { useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, ActivityIndicator } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Calendar as CalIcon, Clock, Building2, Stethoscope, MessageCircle, CheckCircle, Info, FileText, MapPin, UserRound } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { PatientBottomNav, PATIENT_NAV_CONTENT_BOTTOM } from "@/src/features/patient/components/PatientBottomNav";
import { formatIsoCalendarDate, formatVisitTiming } from "@/src/lib/visit-timing";

const fmtScheduleDate = (d?: string) => formatIsoCalendarDate(d, "");
const fmtEventDate = (d?: string) => {
  if (!d) return "";
  const parsed = new Date(d);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
};
const fmtTime = (d?: string) => {
  if (!d) return "";
  const clock = d.match(/[T\s](\d{2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?/);
  if (!clock) return "";
  const rawSeconds = Number(clock[3] || 0);
  if (Number(clock[1]) === 0 && Number(clock[2]) === 0 && rawSeconds === 0) return "";
  const parsed = new Date(d);
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleTimeString("en-GB", { hour: "numeric", minute: "2-digit", hour12: true });
};

export default function VisitDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [visit, setVisit] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        if (!id) {
          setVisit(null);
          return;
        }
        const response = await api.get(`/visits/mine/${id}`);
        setVisit(response.data || null);
      } catch (requestError: any) {
        setVisit(null);
        const status = requestError?.response?.status;
        setError(
          status === 403
            ? "You don't have access to this visit."
            : status === 404
              ? "This visit is no longer available."
              : "Couldn't load this visit. Check your connection and try again.",
        );
      } finally {
        setLoading(false);
      }
    })();
  }, [id, refreshKey]);

  if (loading) return (
    <ScreenContainer>
      <ScreenHeader eyebrow="My Trial" title="Visit Details" />
      <View style={{ paddingTop: spacing.xxl, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
      <PatientBottomNav active="visits" />
    </ScreenContainer>
  );
  if (!visit) return (
    <ScreenContainer>
      <ScreenHeader eyebrow="My Trial" title="Visit Details" />
      <View style={{ padding: spacing.md }}>
        <Card style={{ alignItems: "center", paddingVertical: spacing.xl }}>
          <CalIcon size={28} color={colors.mutedFg} />
          <Body weight="700" style={{ marginTop: spacing.sm }}>{error ? "Unable to load visit" : "Visit not found"}</Body>
          <Small style={{ marginTop: 2, textAlign: "center" }}>{error || "This visit is no longer available or you don't have access to it."}</Small>
          {!!error && <Button variant="secondary" style={{ marginTop: spacing.md }} onPress={() => setRefreshKey((value) => value + 1)}><Small color={colors.primary} weight="700">Retry</Small></Button>}
        </Card>
      </View>
      <PatientBottomNav active="visits" />
    </ScreenContainer>
  );
  const done = visit.status === "completed";
  const protocol = visit.protocol_id || "";
  const eyebrow = protocol ? `My Trial · ${protocol}` : "My Trial";
  const site = visit.location || visit.site || "";
  const pi = visit.pi_name || "";
  const siteLine = [site, pi].filter(Boolean).join(" · ");
  const checklist: string[] = Array.isArray(visit.preparation)
    ? visit.preparation
    : Array.isArray(visit.checklist) ? visit.checklist : [];
  const procedures: { id?: string; label: string; description?: string }[] =
    Array.isArray(visit.procedures) ? visit.procedures : [];
  const contactId = visit.assigned_contact_id || visit.pi_id || "";
  const contactRole = visit.assigned_contact_role === "crc" ? "Coordinator" : "PI";
  const completionDate = visit.completion_timestamp || visit.completed_at;
  const scheduledTime = fmtTime(visit.scheduled_date);
  const windowLabel = visit.window_start && visit.window_end
    ? `${fmtScheduleDate(visit.window_start)} – ${fmtScheduleDate(visit.window_end)}`
    : visit.window_days
      ? `±${visit.window_days} days`
      : "Not published";

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={eyebrow} title={`Visit ${visit.visit_number} Details`} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: PATIENT_NAV_CONTENT_BOTTOM, gap: spacing.md }}>
        {/* Hero */}
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Eyebrow color={colors.overlay25}>{protocol || "Trial"}</Eyebrow>
            <View style={s.chip}><Small weight="700" color={colors.primaryFg} style={{ textTransform: "capitalize" }}>{visit.status === "upcoming" ? "Upcoming" : done ? "Completed ✓" : "Scheduled"}</Small></View>
          </View>
          <H1 color={colors.primaryFg} style={{ marginTop: spacing.sm, fontSize: 20 }}>Visit {visit.visit_number} · {visit.name}</H1>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: spacing.sm }}>
            <View style={s.chipSm}><CalIcon size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{fmtScheduleDate(visit.scheduled_date)}</Small></View>
            {!!scheduledTime && <View style={s.chipSm}><Clock size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{scheduledTime}</Small></View>}
            <View style={s.chipSm}><Small color={colors.primaryFg} weight="700">{formatVisitTiming(visit)}</Small></View>
            {!!site && <View style={s.chipSm}><Building2 size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">{site}</Small></View>}
          </View>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.sm }}>
            <Info size={14} color={colors.primaryFg} />
            <Small color={colors.primaryFg}>Visit window: {windowLabel}</Small>
          </View>
          {!!siteLine && (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: spacing.sm }}>
              <Stethoscope size={14} color={colors.primaryFg} /><Small color={colors.primaryFg}>{siteLine}</Small>
            </View>
          )}
        </LinearGradient>

        <Card>
          <View style={s.sectionTitle}><FileText size={16} color={colors.primary} /><Eyebrow>Study details</Eyebrow></View>
          <View style={s.detailGrid}>
            <View style={s.detailCell}><Small color={colors.mutedFg}>Protocol ID</Small><Body weight="700">{protocol || "Not available"}</Body></View>
            <View style={s.detailCell}><Small color={colors.mutedFg}>Phase</Small><Body weight="700">{visit.phase || "Not available"}</Body></View>
            <View style={s.detailCell}><Small color={colors.mutedFg}>Indication</Small><Body weight="700">{visit.indication || "Not available"}</Body></View>
            <View style={s.detailCell}><Small color={colors.mutedFg}>Visit type</Small><Body weight="700">{visit.visit_type || "Not published"}</Body></View>
          </View>
          {!!site && <View style={s.contactRow}><MapPin size={16} color={colors.primary} /><View style={{ flex: 1 }}><Body weight="700">{site}</Body><Small color={colors.mutedFg}>Study site</Small></View></View>}
          {!!pi && <View style={s.contactRow}><UserRound size={16} color={colors.primary} /><View style={{ flex: 1 }}><Body weight="700">{pi}</Body><Small color={colors.mutedFg}>Principal Investigator</Small></View></View>}
          {!!visit.assigned_contact_name && visit.assigned_contact_id !== visit.pi_id && (
            <View style={s.contactRow}><UserRound size={16} color={colors.primary} /><View style={{ flex: 1 }}><Body weight="700">{visit.assigned_contact_name}</Body><Small color={colors.mutedFg}>Study coordinator</Small></View></View>
          )}
        </Card>

        {/* Before you come in */}
        {checklist.length > 0 && (
          <Card>
            <Eyebrow style={{ marginBottom: spacing.sm }}>Before you come in</Eyebrow>
            {checklist.map((it, i) => (
              <View key={i} style={{ flexDirection: "row", gap: 10, marginBottom: 8 }}>
                <View style={s.numCircle}><Small weight="700" color={colors.accent}>{i + 1}</Small></View>
                <Body style={{ flex: 1 }}>{it}</Body>
              </View>
            ))}
          </Card>
        )}

        {/* Patient-safe procedures */}
        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Activities</Eyebrow>
          {procedures.length > 0 ? procedures.map((procedure, i) => (
              <View key={procedure.id || `${procedure.label}-${i}`} style={{ flexDirection: "row", alignItems: "flex-start", gap: 10, marginBottom: 10 }}>
                <View style={s.numCircle}><Small weight="700" color={colors.accent}>{i + 1}</Small></View>
                <View style={{ flex: 1 }}>
                  <Body weight="700">{procedure.label}</Body>
                  {!!procedure.description && <Small color={colors.mutedFg}>{procedure.description}</Small>}
                </View>
              </View>
            )) : <Small color={colors.mutedFg}>No clinical tasks have been published for this visit.</Small>}
          <Small color={colors.mutedFg} style={{ marginTop: 4 }}>Tasks are managed by the research team</Small>
        </Card>

        {done && (
          <View style={s.doneBanner}>
            <CheckCircle size={16} color={colors.success} />
            <Small color={colors.success} style={{ flex: 1 }}>Completed on {completionDate ? fmtEventDate(completionDate) : "date unavailable"}{visit.clinician_name ? ` · Confirmed by ${visit.clinician_name}${visit.clinician_role ? `, ${visit.clinician_role.toUpperCase()}` : ""}` : ""}</Small>
          </View>
        )}

        <Button
          testID="contact-pi-button"
          disabled={!contactId}
          onPress={() => router.push({
            pathname: "/(app)/patient/messages",
            params: { participantId: contactId },
          })}
        >
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><MessageCircle size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Contact {contactRole}</Small></View>
        </Button>
      </ScrollView>
      <PatientBottomNav active="visits" />
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  chipSm: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  numCircle: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.accent + "22", alignItems: "center", justifyContent: "center" },
  doneBanner: { flexDirection: "row", alignItems: "center", gap: 8, padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.success + "40", backgroundColor: colors.success + "14" },
  sectionTitle: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: spacing.md },
  detailGrid: { flexDirection: "row", flexWrap: "wrap", rowGap: spacing.md, marginBottom: spacing.md },
  detailCell: { width: "50%", paddingRight: spacing.sm },
  contactRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, paddingTop: spacing.sm, marginTop: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
});
