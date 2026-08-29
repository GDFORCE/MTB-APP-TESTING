import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from "react-native";
import {
  AlertCircle,
  Building2,
  CalendarDays,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Clock3,
  Mail,
  MapPin,
  Phone,
  Pill,
  RefreshCw,
  ShieldAlert,
  UserRound,
} from "lucide-react-native";
import { LinearGradient } from "expo-linear-gradient";
import { api } from "@/src/api/client";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Card, Eyebrow, Small } from "@/src/components/ui";
import { formatIsoCalendarDate, formatVisitTiming } from "@/src/lib/visit-timing";
import { colors, dawnGradient, radii, shadows, spacing } from "@/src/theme/tokens";

type Section = "overview" | "schedule" | "medication" | "risks" | "contacts" | "faq";
type Contact = {
  id?: string;
  full_name?: string;
  designation?: string;
  role?: string;
  phone?: string;
  email?: string;
  organization?: string;
};
type Medication = {
  id: string;
  name?: string;
  dosage?: string;
  route?: string;
  schedule?: { time?: string; label?: string }[];
  start_date?: string;
  end_date?: string | null;
  active?: boolean;
};
type Faq = { q: string; a: string };

const SECTION_META: { id: Section; label: string; icon: typeof CalendarDays }[] = [
  { id: "overview", label: "Overview", icon: Building2 },
  { id: "schedule", label: "Visit Schedule", icon: CalendarDays },
  { id: "medication", label: "Medication", icon: Pill },
  { id: "risks", label: "Risks & Side Effects", icon: AlertCircle },
  { id: "contacts", label: "Contacts", icon: Phone },
  { id: "faq", label: "FAQ", icon: CircleHelp },
];

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => typeof item === "string"
      ? item.trim()
      : text(
        (item as any)?.risk
        || (item as any)?.effect
        || (item as any)?.description
        || (item as any)?.text
        || (item as any)?.name
        || (item as any)?.label
      ))
    .filter(Boolean);
}

function formatDate(value?: string): string {
  if (!value) return "Date pending";
  return formatIsoCalendarDate(value, value);
}

function formatTime(value?: string): string {
  if (!value) return "";
  const [hour, minute] = value.split(":").map(Number);
  if (Number.isNaN(hour)) return value;
  const date = new Date();
  date.setHours(hour, minute || 0, 0, 0);
  return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function scheduleDuration(visits: any[]): string {
  const dates = visits
    .map((visit) => new Date(visit?.scheduled_date))
    .filter((date) => !Number.isNaN(date.getTime()))
    .sort((a, b) => a.getTime() - b.getTime());
  if (dates.length < 2) return "";
  const days = Math.max(1, Math.ceil(
    (dates[dates.length - 1].getTime() - dates[0].getTime()) / 86_400_000,
  ));
  if (days >= 60) {
    const months = Math.max(1, Math.round(days / 30.44));
    return `${months} month${months === 1 ? "" : "s"}`;
  }
  const weeks = Math.max(1, Math.ceil(days / 7));
  return `${weeks} week${weeks === 1 ? "" : "s"}`;
}

function roleLabel(contact: Contact): string {
  if (text(contact.designation)) return text(contact.designation);
  const role = text(contact.role).toLowerCase();
  if (role === "pi") return "Principal Investigator";
  if (role === "crc") return "Clinical Research Coordinator";
  return text(contact.role) || "Study team";
}

export default function AboutTrial() {
  const [section, setSection] = useState<Section>("overview");
  const [trial, setTrial] = useState<any | null>(null);
  const [visits, setVisits] = useState<any[]>([]);
  const [medications, setMedications] = useState<Medication[]>([]);
  const [recipients, setRecipients] = useState<Contact[]>([]);
  const [faq, setFaq] = useState<Faq[]>([]);
  const [care, setCare] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [partialErrors, setPartialErrors] = useState<string[]>([]);
  const [expandedFaq, setExpandedFaq] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setPartialErrors([]);

    const results = await Promise.allSettled([
      api.get("/visits/mine"),
      api.get("/trials"),
      api.get("/medications"),
      api.get("/messaging/recipients"),
      api.get("/faq"),
    ]);
    const [visitResult, trialResult, medicationResult, recipientResult, faqResult] = results;

    if (visitResult.status === "rejected" || trialResult.status === "rejected") {
      setVisits([]);
      setTrial(null);
      setError("We couldn't load your enrolled study. Check your connection and try again.");
      setLoading(false);
      return;
    }

    const allVisits: any[] = Array.isArray(visitResult.value.data) ? visitResult.value.data : [];
    const allTrials: any[] = Array.isArray(trialResult.value.data) ? trialResult.value.data : [];
    const enrolledId = allVisits.find((visit) => visit?.trial_id)?.trial_id;
    // /trials is relationship-scoped for patients. Prefer the visit-linked ID;
    // when no schedule exists yet, the sole scoped trial is still the patient's
    // enrollment rather than a global-list fallback.
    const enrolledTrial = enrolledId
      ? allTrials.find((candidate) => candidate?.id === enrolledId) || null
      : allTrials.length === 1 ? allTrials[0] : null;
    const currentTrialId = enrolledId || enrolledTrial?.id;
    const enrolledVisits = currentTrialId
      ? allVisits.filter((visit) => visit?.trial_id === currentTrialId)
      : [];

    setTrial(enrolledTrial);
    setVisits(enrolledVisits);
    setCare(enrolledVisits.find((visit) =>
      visit?.site || visit?.pi_name || visit?.pi_phone || visit?.pi_email
    ) || {});

    const missing: string[] = [];
    if (medicationResult.status === "fulfilled") {
      setMedications(Array.isArray(medicationResult.value.data) ? medicationResult.value.data : []);
    } else {
      setMedications([]);
      missing.push("Medication information is temporarily unavailable.");
    }
    if (recipientResult.status === "fulfilled") {
      setRecipients(Array.isArray(recipientResult.value.data) ? recipientResult.value.data : []);
    } else {
      setRecipients([]);
      missing.push("Care-team contacts are temporarily unavailable.");
    }
    if (faqResult.status === "fulfilled") {
      setFaq(Array.isArray(faqResult.value.data) ? faqResult.value.data : []);
    } else {
      setFaq([]);
      missing.push("Frequently asked questions are temporarily unavailable.");
    }
    setPartialErrors(missing);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openLink = async (url: string, label: string) => {
    try {
      await Linking.openURL(url);
    } catch {
      Alert.alert(`Couldn't open ${label}`, `No compatible ${label} app is available on this device.`);
    }
  };

  const sortedVisits = useMemo(
    () => [...visits].sort((a, b) =>
      Number(a.visit_number ?? a.seq ?? 0) - Number(b.visit_number ?? b.seq ?? 0)
    ),
    [visits],
  );
  const activeMedications = useMemo(
    () => medications.filter((medication) => medication.active !== false),
    [medications],
  );
  const piContact = useMemo<Contact | null>(() => {
    if (text(care.pi_name) || text(care.pi_phone) || text(care.pi_email)) {
      return {
        id: text(care.pi_id),
        full_name: text(care.pi_name),
        phone: text(care.pi_phone),
        email: text(care.pi_email),
        organization: text(care.site),
        role: "pi",
      };
    }
    return recipients.find((contact) => text(contact.role).toLowerCase() === "pi") || null;
  }, [care, recipients]);
  const coordinator = useMemo(
    () => recipients.find((contact) => text(contact.role).toLowerCase() === "crc") || null,
    [recipients],
  );
  const risks = stringList(trial?.risks);
  const sideEffects = stringList(trial?.side_effects);
  const emergencyPhone = text(trial?.emergency_phone || trial?.emergency_contact?.phone);
  const emergencyLabel = text(trial?.emergency_contact?.name || trial?.emergency_contact_name);
  const emergencyInstructions = text(trial?.emergency_contact?.instructions);
  const protocol = text(trial?.protocol_id) || "Your study";
  const duration = text(trial?.duration) || scheduleDuration(sortedVisits);
  const medicationSummary = text(trial?.drug) || Array.from(new Set(
    activeMedications.map((medication) => text(medication.name)).filter(Boolean),
  )).join(", ");
  const recruitment = text(trial?.recruitment_status) || text(trial?.status);

  const renderOverview = () => (
    <View style={s.sectionStack}>
      <LinearGradient colors={dawnGradient as any} style={s.hero}>
        <View style={s.heroTop}>
          <Small color={colors.primaryFg} style={s.protocol}>{protocol}</Small>
          {!!text(trial?.status) && <Small color={colors.primaryFg} weight="700">{text(trial.status)}</Small>}
        </View>
        <Body color={colors.primaryFg} weight="700" style={s.heroTitle}>
          {text(trial?.title) || "Study title has not been provided"}
        </Body>
        {!!text(trial?.sponsor_name) && (
          <View style={s.inline}>
            <Building2 size={14} color={colors.primaryFg} />
            <Small color={colors.primaryFg}>{text(trial.sponsor_name)}</Small>
          </View>
        )}
      </LinearGradient>

      <View style={s.factRow}>
        <Card style={s.factCard}>
          <Clock3 size={18} color={colors.accent} />
          <Body weight="700" style={s.factValue}>{duration || "Not provided"}</Body>
          <Small>Duration</Small>
        </Card>
        <Card style={s.factCard}>
          <CalendarDays size={18} color={colors.accent} />
          <Body weight="700" style={s.factValue}>{visits.length || "—"}</Body>
          <Small>Scheduled visits</Small>
        </Card>
      </View>

      <Card>
        <Eyebrow style={s.cardHeading}>Study details</Eyebrow>
        {[
          ["Phase", text(trial?.phase)],
          ["Condition", text(trial?.condition)],
          ["Study medication", medicationSummary],
          ["Recruitment", recruitment],
          ["Site", text(care.site)],
        ].map(([label, value], index) => (
          <View key={label} style={[s.detailRow, index > 0 && s.divider]}>
            <Small>{label}</Small>
            <Small weight="700" color={value ? colors.foreground : colors.mutedFg}>
              {value || "Not provided"}
            </Small>
          </View>
        ))}
      </Card>

      <Card>
        <Eyebrow style={s.cardHeading}>Overview</Eyebrow>
        <Body color={text(trial?.description) ? colors.foreground : colors.mutedFg}>
          {text(trial?.description) || "The study team has not published a patient overview yet."}
        </Body>
      </Card>
    </View>
  );

  const renderSchedule = () => (
    <View style={s.sectionStack}>
      <View>
        <Eyebrow style={s.sectionHeading}>Your visit schedule</Eyebrow>
        <Small>Dates, visit types and locations below come from your enrolled schedule.</Small>
      </View>
      {sortedVisits.length === 0 ? (
        <Card style={s.emptyCard}>
          <CalendarDays size={24} color={colors.mutedFg} />
          <Body weight="700">No visits published</Body>
          <Small style={s.center}>Your study team has not published a visit schedule yet.</Small>
        </Card>
      ) : sortedVisits.map((visit, index) => {
        const rawType = text(visit.visit_type || visit.type || visit.location_type);
        const typeLower = rawType.toLowerCase();
        const remote = typeLower.includes("tele") || typeLower.includes("phone") || typeLower.includes("remote");
        const home = typeLower.includes("home");
        const location = remote
          ? "Remote visit"
          : home
            ? "Home visit"
            : text(visit.location) || text(visit.site) || "Location pending";
        return (
          <View key={visit.id || `${visit.visit_number}-${index}`} style={s.timelineRow}>
            <View style={s.timelineRail}>
              {index < sortedVisits.length - 1 && <View style={s.timelineLine} />}
              <View style={[s.timelineDot, visit.status === "completed" && s.timelineDone]}>
                <Small weight="700" color={visit.status === "completed" ? colors.successFg : colors.primary}>
                  {visit.visit_number ?? index + 1}
                </Small>
              </View>
            </View>
            <Card style={s.visitCard}>
              <View style={s.between}>
                <Body weight="700" style={s.flex}>{text(visit.name) || `Visit ${index + 1}`}</Body>
                <Small weight="700" color={visit.status === "completed" ? colors.success : colors.primary}>
                  {text(visit.status) || "Scheduled"}
                </Small>
              </View>
              <Small style={s.rowGap}>{formatDate(visit.scheduled_date)}</Small>
              <Small style={s.rowGap} color={colors.mutedFg}>{formatVisitTiming(visit)}</Small>
              <View style={[s.inline, s.rowGap]}>
                {remote ? <Phone size={13} color={colors.mutedFg} /> : <MapPin size={13} color={colors.mutedFg} />}
                <Small>{rawType ? `${rawType} · ${location}` : location}</Small>
              </View>
            </Card>
          </View>
        );
      })}
    </View>
  );

  const renderMedication = () => (
    <View style={s.sectionStack}>
      <View>
        <Eyebrow style={s.sectionHeading}>Current study medication</Eyebrow>
        <Small>Only prescriptions assigned to your patient record are shown.</Small>
      </View>
      {activeMedications.length === 0 ? (
        <Card style={s.emptyCard}>
          <Pill size={24} color={colors.mutedFg} />
          <Body weight="700">No medication assigned</Body>
          <Small style={s.center}>Your care team has not assigned an active medication schedule.</Small>
        </Card>
      ) : activeMedications.map((medication) => (
        <Card key={medication.id}>
          <View style={s.contactTitle}>
            <View style={s.iconCircle}><Pill size={19} color={colors.primary} /></View>
            <View style={s.flex}>
              <Body weight="700">{text(medication.name) || "Medication"}</Body>
              <Small>{text(medication.dosage) || "Dose not provided"}</Small>
            </View>
          </View>
          <View style={[s.medDetails, s.divider]}>
            <Small>Route: {text(medication.route) || "Not provided"}</Small>
            <Small>
              Schedule: {(medication.schedule || []).map((slot) =>
                [text(slot.label), formatTime(slot.time)].filter(Boolean).join(" ")
              ).filter(Boolean).join(" · ") || "Not provided"}
            </Small>
            <Small>
              Period: {medication.start_date ? formatDate(medication.start_date) : "Start date pending"}
              {medication.end_date ? ` – ${formatDate(medication.end_date)}` : " – ongoing"}
            </Small>
          </View>
        </Card>
      ))}
    </View>
  );

  const renderRisks = () => (
    <View style={s.sectionStack}>
      <View style={s.warningCard}>
        <ShieldAlert size={20} color={colors.warning} />
        <Small style={s.flex}>
          Report any symptoms or health changes to your assigned study team. For urgent medical danger, use your local emergency service.
        </Small>
      </View>
      <Card>
        <Eyebrow style={s.cardHeading}>Risks</Eyebrow>
        {risks.length ? risks.map((risk, index) => (
          <View key={`${risk}-${index}`} style={s.bulletRow}>
            <View style={s.bullet} />
            <Body style={s.flex}>{risk}</Body>
          </View>
        )) : (
          <Small color={colors.mutedFg}>Structured risk information has not been published by the study team.</Small>
        )}
      </Card>
      <Card>
        <Eyebrow style={s.cardHeading}>Possible side effects</Eyebrow>
        {sideEffects.length ? sideEffects.map((effect, index) => (
          <View key={`${effect}-${index}`} style={s.bulletRow}>
            <View style={[s.bullet, { backgroundColor: colors.destructive }]} />
            <Body style={s.flex}>{effect}</Body>
          </View>
        )) : (
          <Small color={colors.mutedFg}>Structured side-effect information has not been published by the study team.</Small>
        )}
      </Card>
    </View>
  );

  const ContactCard = ({ contact, fallback, site }: { contact: Contact | null; fallback: string; site?: string }) => (
    <Card>
      <View style={s.contactTitle}>
        <View style={s.iconCircle}><UserRound size={19} color={colors.primary} /></View>
        <View style={s.flex}>
          <Body weight="700">{text(contact?.full_name) || fallback}</Body>
          <Small>{contact ? roleLabel(contact) : "Not assigned"}</Small>
          {!!(text(contact?.organization) || site) && <Small>{text(contact?.organization) || site}</Small>}
        </View>
      </View>
      {!!contact && (text(contact.phone) || text(contact.email)) && (
        <View style={s.contactActions}>
          {!!text(contact.phone) && (
            <Pressable onPress={() => openLink(`tel:${text(contact.phone).replace(/[^\d+]/g, "")}`, "phone")} style={s.contactButton}>
              <Phone size={15} color={colors.primary} /><Small color={colors.primary} weight="700">Call</Small>
            </Pressable>
          )}
          {!!text(contact.email) && (
            <Pressable onPress={() => openLink(`mailto:${text(contact.email)}`, "email")} style={s.contactButton}>
              <Mail size={15} color={colors.info} /><Small color={colors.info} weight="700">Email</Small>
            </Pressable>
          )}
        </View>
      )}
    </Card>
  );

  const renderContacts = () => (
    <View style={s.sectionStack}>
      <ContactCard contact={piContact} fallback="Principal Investigator not assigned" site={text(care.site)} />
      <ContactCard contact={coordinator} fallback="Coordinator not assigned" site={text(care.site)} />
      <Card>
        <View style={s.contactTitle}>
          <View style={s.iconCircle}><MapPin size={19} color={colors.accent} /></View>
          <View style={s.flex}>
            <Body weight="700">{text(care.site) || "Study site not provided"}</Body>
            <Small>Enrolled site</Small>
          </View>
        </View>
      </Card>
      <Card style={emergencyPhone ? s.emergencyCard : undefined}>
        <View style={s.contactTitle}>
          <View style={[s.iconCircle, { backgroundColor: colors.destructive + "14" }]}>
            <Phone size={19} color={colors.destructive} />
          </View>
          <View style={s.flex}>
            <Body weight="700">{emergencyLabel || "Study emergency contact"}</Body>
            <Small color={emergencyPhone ? colors.foreground : colors.mutedFg}>
              {emergencyPhone || "No study emergency number has been configured."}
            </Small>
            {!!emergencyInstructions && <Small color={colors.mutedFg}>{emergencyInstructions}</Small>}
          </View>
        </View>
        {!!emergencyPhone && (
          <Pressable onPress={() => openLink(`tel:${emergencyPhone.replace(/[^\d+]/g, "")}`, "phone")} style={s.emergencyButton}>
            <Phone size={15} color={colors.destructiveFg} />
            <Small color={colors.destructiveFg} weight="700">Call emergency contact</Small>
          </Pressable>
        )}
      </Card>
    </View>
  );

  const renderFaq = () => (
    <View style={s.sectionStack}>
      {faq.length === 0 ? (
        <Card style={s.emptyCard}>
          <CircleHelp size={24} color={colors.mutedFg} />
          <Body weight="700">FAQ unavailable</Body>
          <Small style={s.center}>No frequently asked questions are available right now.</Small>
        </Card>
      ) : faq.map((item, index) => {
        const expanded = expandedFaq === index;
        return (
          <Pressable key={`${item.q}-${index}`} onPress={() => setExpandedFaq(expanded ? null : index)} style={s.faqCard}>
            <View style={s.between}>
              <Body weight="700" style={s.flex}>{item.q}</Body>
              {expanded ? <ChevronUp size={18} color={colors.primary} /> : <ChevronDown size={18} color={colors.mutedFg} />}
            </View>
            {expanded && <Body color={colors.mutedFg} style={s.faqAnswer}>{item.a}</Body>}
          </Pressable>
        );
      })}
    </View>
  );

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="About this study" title={protocol} />
      {loading ? (
        <View style={s.centerState}>
          <ActivityIndicator color={colors.primary} />
          <Small>Loading your study information…</Small>
        </View>
      ) : error ? (
        <View style={s.centerState}>
          <AlertCircle size={30} color={colors.destructive} />
          <Body weight="700">Unable to load your study</Body>
          <Small color={colors.destructive} style={s.center}>{error}</Small>
          <Pressable onPress={load} style={s.retryButton}>
            <RefreshCw size={15} color={colors.primaryFg} />
            <Small color={colors.primaryFg} weight="700">Try again</Small>
          </Pressable>
        </View>
      ) : !trial ? (
        <View style={s.centerState}>
          <Building2 size={30} color={colors.mutedFg} />
          <Body weight="700">No enrolled study found</Body>
          <Small style={s.center}>Your account is not linked to a published trial yet.</Small>
          <Pressable onPress={load} style={s.retryButton}>
            <RefreshCw size={15} color={colors.primaryFg} />
            <Small color={colors.primaryFg} weight="700">Refresh</Small>
          </Pressable>
        </View>
      ) : (
        <>
          <ScrollView
            horizontal
            style={s.sectionTabsScroller}
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={s.sectionTabs}
          >
            {SECTION_META.map((item) => {
              const Icon = item.icon;
              const active = section === item.id;
              return (
                <Pressable key={item.id} onPress={() => setSection(item.id)} style={[s.sectionTab, active && s.sectionTabActive]}>
                  <Icon size={15} color={active ? colors.primaryFg : colors.mutedFg} />
                  <Small color={active ? colors.primaryFg : colors.mutedFg} weight="700">{item.label}</Small>
                </Pressable>
              );
            })}
          </ScrollView>
          <ScrollView style={s.bodyScroller} contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
            {partialErrors.length > 0 && (
              <View style={s.partialBanner}>
                <AlertCircle size={17} color={colors.warning} />
                <View style={s.flex}>
                  {partialErrors.map((message) => <Small key={message}>{message}</Small>)}
                </View>
                <Pressable onPress={load} hitSlop={8}><RefreshCw size={17} color={colors.primary} /></Pressable>
              </View>
            )}
            {section === "overview" && renderOverview()}
            {section === "schedule" && renderSchedule()}
            {section === "medication" && renderMedication()}
            {section === "risks" && renderRisks()}
            {section === "contacts" && renderContacts()}
            {section === "faq" && renderFaq()}
          </ScrollView>
        </>
      )}
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  center: { textAlign: "center" },
  centerState: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    padding: spacing.xl,
  },
  retryButton: {
    marginTop: spacing.sm,
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  sectionTabs: {
    alignItems: "center",
    paddingHorizontal: spacing.md,
    paddingVertical: 12,
    gap: 8,
    backgroundColor: colors.surface,
  },
  sectionTabsScroller: {
    flexGrow: 0,
    flexShrink: 0,
    height: 62,
    backgroundColor: colors.surface,
  },
  bodyScroller: { flex: 1 },
  sectionTab: {
    height: 38,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 9,
    borderRadius: radii.pill,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
  },
  sectionTabActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  sectionStack: { gap: spacing.md },
  sectionHeading: { marginBottom: 4 },
  partialBanner: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    padding: 12,
    marginBottom: spacing.md,
    borderWidth: 1,
    borderColor: colors.warning + "55",
    backgroundColor: colors.warning + "12",
    borderRadius: radii.lg,
  },
  hero: { borderRadius: radii.xl, padding: spacing.lg, ...shadows.md },
  heroTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  protocol: { backgroundColor: colors.overlay20, paddingHorizontal: 9, paddingVertical: 4, borderRadius: radii.pill },
  heroTitle: { fontSize: 20, marginTop: 14, marginBottom: 10 },
  inline: { flexDirection: "row", alignItems: "center", gap: 6 },
  between: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  factRow: { flexDirection: "row", gap: spacing.sm },
  factCard: { flex: 1 },
  factValue: { marginTop: 10 },
  cardHeading: { marginBottom: 10 },
  detailRow: { flexDirection: "row", justifyContent: "space-between", gap: 12, paddingVertical: 9 },
  divider: { borderTopWidth: 1, borderTopColor: colors.border },
  emptyCard: { alignItems: "center", gap: 7, paddingVertical: spacing.xl },
  timelineRow: { flexDirection: "row", gap: 10 },
  timelineRail: { width: 30, alignItems: "center" },
  timelineLine: { position: "absolute", top: 30, bottom: -16, width: 2, backgroundColor: colors.border },
  timelineDot: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.secondary,
    borderWidth: 2,
    borderColor: colors.card,
  },
  timelineDone: { backgroundColor: colors.success },
  visitCard: { flex: 1, marginBottom: 0 },
  rowGap: { marginTop: 6 },
  contactTitle: { flexDirection: "row", alignItems: "center", gap: 12 },
  iconCircle: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.secondary,
  },
  medDetails: { marginTop: 12, paddingTop: 12, gap: 5 },
  warningCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    padding: spacing.md,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.warning + "55",
    backgroundColor: colors.warning + "12",
  },
  bulletRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginBottom: 9 },
  bullet: { width: 7, height: 7, borderRadius: 4, marginTop: 7, backgroundColor: colors.warning },
  contactActions: { flexDirection: "row", gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: colors.border },
  contactButton: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 9,
    borderRadius: radii.md,
    backgroundColor: colors.surface,
  },
  emergencyCard: { borderColor: colors.destructive + "55" },
  emergencyButton: {
    marginTop: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    paddingVertical: 10,
    borderRadius: radii.md,
    backgroundColor: colors.destructive,
  },
  faqCard: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.lg,
    padding: spacing.md,
    ...shadows.sm,
  },
  faqAnswer: { marginTop: 10, lineHeight: 21 },
});
