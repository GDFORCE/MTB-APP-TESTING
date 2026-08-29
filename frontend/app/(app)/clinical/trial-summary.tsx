import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Animated,
  Easing,
  KeyboardAvoidingView,
  Linking,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  ArrowLeft,
  CalendarDays,
  Check,
  CheckCircle2,
  Download,
  FileClock,
  FileText,
  Mail,
  MapPin,
  MoreVertical,
  Pencil,
  Phone,
  RefreshCw,
  Share2,
  Target,
  Upload,
  UserPlus,
  UserRoundCheck,
  Users,
  X,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import type {
  RecruitmentFunnel,
  SponsorTrialSubject,
  SponsorTrialTeamMember,
} from "@/src/features/sponsor/types";
import { downloadFile, uploadFile, type UploadedFile } from "@/src/lib/upload";
import {
  formatIsoCalendarDate,
  formatVisitTiming,
  formatVisitWindow,
} from "@/src/lib/visit-timing";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type Trial = {
  id: string;
  protocol_id: string;
  title: string;
  phase?: string;
  condition?: string;
  drug?: string;
  status?: string;
  description?: string;
  sponsor_name?: string;
  duration?: string;
  target_enrollment?: number;
  recruitment_status?: string;
  ctri_number?: string;
  created_by?: string;
  created_by_name?: string;
  created_by_role?: string;
  created_at?: string;
  updated_at?: string;
  updated_by_name?: string;
  visits?: VisitTemplate[];
};

type VisitTemplate = {
  id: string;
  visit_number?: number;
  name: string;
  day_offset?: number | null;
  day_end?: number | null;
  hour_offset?: number | null;
  hour_end?: number | null;
  hour_offset_basis?: "absolute" | "within_day" | null;
  relative_to?: string | null;
  relative_offset_days?: number | null;
  source_day_label?: string | null;
  source_timing_label?: string | null;
  anchor_study_day?: 0 | 1 | null;
  includes_day_zero?: boolean | null;
  window_days?: number;
  window_before?: number | null;
  window_after?: number | null;
};

type SubjectVisit = {
  id: string;
  visit_number?: number;
  name: string;
  status: string;
  scheduled_date?: string;
  completed_at?: string;
};

type RecruitmentPayload = {
  recruitment: RecruitmentFunnel;
  sites: {
    id: string;
    name: string;
    address?: string;
    city?: string;
    state?: string;
    target_enrollment?: number;
    enrolled: number;
    enrollment_pct: number;
    department?: string;
    pi_name?: string;
    pi_email?: string;
    pi_phone?: string;
    crc_name?: string;
    recruitment?: RecruitmentFunnel;
  }[];
};

type Version = {
  id: string;
  version?: number;
  version_note?: string;
  created_at?: string;
  created_by_name?: string;
  document_name?: string;
};

type Feedback = { tone: "success" | "error"; message: string } | null;

const EMPTY_FUNNEL: RecruitmentFunnel = {
  screened: 0,
  screen_fail: 0,
  randomized: 0,
  active: 0,
  withdrawn: 0,
  dropout: 0,
  follow_up: 0,
  completed: 0,
};

const FUNNEL: { key: keyof RecruitmentFunnel; label: string }[] = [
  { key: "screened", label: "Screened" },
  { key: "screen_fail", label: "Screen Fail" },
  { key: "randomized", label: "Randomized" },
  { key: "withdrawn", label: "Withdrawn" },
  { key: "dropout", label: "Dropout" },
  { key: "follow_up", label: "Follow-up" },
  { key: "completed", label: "Completed" },
];

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => mounted && setReduced(value))
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduced);
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);
  return reduced;
}

function dateLabel(value?: string) {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

const scheduledDateLabel = (value?: string) => formatIsoCalendarDate(value, value || "Not recorded");

function initials(value?: string) {
  return (value || "").split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0]?.toUpperCase()).join("") || "—";
}

export default function TrialSummary() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id?: string }>();
  const { user } = useAuth();
  const reducedMotion = useReducedMotion();
  const [trial, setTrial] = useState<Trial | null>(null);
  const [recruitment, setRecruitment] = useState<RecruitmentPayload>({
    recruitment: EMPTY_FUNNEL,
    sites: [],
  });
  const [subjects, setSubjects] = useState<SponsorTrialSubject[]>([]);
  const [team, setTeam] = useState<SponsorTrialTeamMember[]>([]);
  const [documents, setDocuments] = useState<UploadedFile[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [subjectVisits, setSubjectVisits] = useState<Record<string, SubjectVisit[]>>({});
  const [expandedSubject, setExpandedSubject] = useState<string | null>(null);
  const [loadingSubject, setLoadingSubject] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [showActions, setShowActions] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editForm, setEditForm] = useState({
    title: "",
    phase: "",
    condition: "",
    drug: "",
    duration: "",
    target_enrollment: "",
    ctri_number: "",
    recruitment_status: "",
    description: "",
  });
  const feedbackAnim = useRef(new Animated.Value(0)).current;

  const showFeedback = useCallback((next: Exclude<Feedback, null>) => {
    setFeedback(next);
    feedbackAnim.setValue(reducedMotion ? 1 : 0);
    if (!reducedMotion) {
      Animated.spring(feedbackAnim, {
        toValue: 1,
        damping: 16,
        stiffness: 170,
        mass: 0.8,
        useNativeDriver: true,
      }).start();
    }
  }, [feedbackAnim, reducedMotion]);

  const load = useCallback(async () => {
    if (!id) {
      setError("This trial link is missing its record ID.");
      setLoading(false);
      setRefreshing(false);
      return;
    }
    setError("");
    try {
      const [trialRes, recruitmentRes, subjectsRes, teamRes, docsRes, versionsRes] =
        await Promise.all([
          api.get(`/trials/${id}`),
          api.get(`/trials/${id}/recruitment`),
          api.get(`/trials/${id}/subjects`),
          api.get(`/trials/${id}/team`),
          api.get(`/trials/${id}/documents`),
          api.get(`/trials/${id}/versions`),
        ]);
      setTrial(trialRes.data);
      setRecruitment(recruitmentRes.data);
      setSubjects(Array.isArray(subjectsRes.data) ? subjectsRes.data : []);
      setTeam(Array.isArray(teamRes.data) ? teamRes.data : []);
      setDocuments(Array.isArray(docsRes.data) ? docsRes.data : []);
      setVersions(Array.isArray(versionsRes.data) ? versionsRes.data : []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load this trial summary.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const target = Number(trial?.target_enrollment || 0);
  const protocolDocument = useMemo(
    () => documents.find((document) => /protocol/i.test(document.name)) || documents[0],
    [documents],
  );
  const sponsorLike = user?.role === "sponsor" || user?.role === "cro";
  const canAddPatient = user?.role === "pi" || user?.role === "crc";
  const canEdit = sponsorLike || (user?.role === "pi" && trial?.created_by === user.id);
  const canShare = sponsorLike || user?.role === "pi";
  const canUpload = sponsorLike || user?.role === "pi" || user?.role === "crc";

  const openSubject = async (subject: SponsorTrialSubject) => {
    if (expandedSubject === subject.id) {
      setExpandedSubject(null);
      return;
    }
    setExpandedSubject(subject.id);
    if (subjectVisits[subject.id] || !trial) return;
    setLoadingSubject(subject.id);
    try {
      const response = await api.get(
        `/trials/${trial.id}/subjects/${encodeURIComponent(subject.id)}/visits`,
      );
      setSubjectVisits((current) => ({
        ...current,
        [subject.id]: Array.isArray(response.data) ? response.data : [],
      }));
    } catch (e: any) {
      showFeedback({
        tone: "error",
        message: e?.response?.data?.detail || "Couldn't load this subject's visits.",
      });
      setExpandedSubject(null);
    } finally {
      setLoadingSubject(null);
    }
  };

  const uploadDocument = async () => {
    if (!trial || uploading || !canUpload) return;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [
          "application/pdf",
          "image/png",
          "image/jpeg",
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets[0]) return;
      setUploading(true);
      const asset = result.assets[0];
      await uploadFile(
        {
          uri: asset.uri,
          name: asset.name || "Trial document",
          mimeType: asset.mimeType || undefined,
          file: asset.file,
        },
        { scopeType: "trial", scopeId: trial.id },
      );
      await load();
      showFeedback({ tone: "success", message: "Document uploaded to this trial." });
    } catch (e: any) {
      showFeedback({
        tone: "error",
        message: e?.response?.data?.detail || "Document upload failed.",
      });
    } finally {
      setUploading(false);
    }
  };

  const download = async (document = protocolDocument) => {
    if (!document) {
      showFeedback({ tone: "error", message: "No protocol document is available yet." });
      return;
    }
    setDownloading(document.id);
    try {
      await downloadFile(document);
      showFeedback({ tone: "success", message: `${document.name} is ready.` });
    } catch (e: any) {
      showFeedback({ tone: "error", message: e?.message || "Couldn't open this document." });
    } finally {
      setDownloading(null);
    }
  };

  const openEdit = () => {
    if (!trial || !canEdit) return;
    setEditForm({
      title: trial.title || "",
      phase: trial.phase || "",
      condition: trial.condition || "",
      drug: trial.drug || "",
      duration: trial.duration || "",
      target_enrollment: trial.target_enrollment ? String(trial.target_enrollment) : "",
      ctri_number: trial.ctri_number || "",
      recruitment_status: trial.recruitment_status || trial.status || "",
      description: trial.description || "",
    });
    setShowEdit(true);
  };

  const saveEdit = async () => {
    if (!trial || savingEdit || !canEdit) return;
    if (!editForm.title.trim() || !editForm.phase.trim() || !editForm.condition.trim()) {
      showFeedback({ tone: "error", message: "Title, phase, and condition are required." });
      return;
    }
    const targetEnrollment = editForm.target_enrollment.trim()
      ? Number(editForm.target_enrollment)
      : undefined;
    if (targetEnrollment !== undefined && (!Number.isInteger(targetEnrollment) || targetEnrollment < 0)) {
      showFeedback({ tone: "error", message: "Target enrollment must be a whole number." });
      return;
    }
    setSavingEdit(true);
    try {
      await api.patch(`/trials/${trial.id}`, {
        title: editForm.title.trim(),
        phase: editForm.phase.trim(),
        condition: editForm.condition.trim(),
        drug: editForm.drug.trim(),
        duration: editForm.duration.trim(),
        target_enrollment: targetEnrollment,
        ctri_number: editForm.ctri_number.trim(),
        recruitment_status: editForm.recruitment_status.trim(),
        description: editForm.description.trim(),
      });
      setShowEdit(false);
      await load();
      showFeedback({ tone: "success", message: "Trial details updated." });
    } catch (e: any) {
      showFeedback({
        tone: "error",
        message: e?.response?.data?.detail || "Couldn't update this trial.",
      });
    } finally {
      setSavingEdit(false);
    }
  };

  const openContact = async (url: string) => {
    try {
      if (!await Linking.canOpenURL(url)) throw new Error();
      await Linking.openURL(url);
    } catch {
      showFeedback({ tone: "error", message: "This contact action isn't available." });
    }
  };

  if (loading) {
    return (
      <View style={s.center}>
        <ActivityIndicator size="large" color={colors.primary} />
        <Text style={s.muted}>Loading trial summary…</Text>
      </View>
    );
  }

  if (!trial || error) {
    return (
      <View style={s.center}>
        <AlertTriangle size={30} color={colors.destructive} />
        <Text style={s.error}>{error || "Trial not found."}</Text>
        <Pressable
          onPress={() => { setLoading(true); load(); }}
          style={s.primaryButton}
        >
          <RefreshCw size={16} color={colors.white} />
          <Text style={s.primaryButtonText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={s.page}>
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}>
          <ArrowLeft size={20} color={colors.white} />
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text numberOfLines={1} style={s.headerTitle}>{trial.protocol_id}</Text>
        </View>
        <Pressable
          testID="trial-actions-menu"
          accessibilityLabel="Trial actions"
          accessibilityState={{ expanded: showActions }}
          onPress={() => setShowActions(true)}
          hitSlop={10}
          style={s.headerAction}
        >
          <MoreVertical size={20} color={colors.white} />
        </Pressable>
      </SafeAreaView>

      <ScrollView
        contentContainerStyle={s.content}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={colors.primary}
          />
        }
      >
        {!!feedback && (
          <Animated.View
            accessibilityRole="alert"
            style={[
              s.feedback,
              feedback.tone === "error" ? s.feedbackError : s.feedbackSuccess,
              {
                opacity: feedbackAnim,
                transform: [{
                  translateY: feedbackAnim.interpolate({
                    inputRange: [0, 1],
                    outputRange: reducedMotion ? [0, 0] : [-10, 0],
                  }),
                }],
              },
            ]}
          >
            {feedback.tone === "success"
              ? <CheckCircle2 size={17} color={colors.success} />
              : <AlertTriangle size={17} color={colors.destructive} />}
            <Text style={[
              s.feedbackText,
              { color: feedback.tone === "success" ? colors.success : colors.destructive },
            ]}>
              {feedback.message}
            </Text>
            <Pressable onPress={() => setFeedback(null)} hitSlop={8}>
              <X size={15} color={feedback.tone === "success" ? colors.success : colors.destructive} />
            </Pressable>
          </Animated.View>
        )}

        <Entrance index={0} reduced={reducedMotion}>
          <View style={s.hero}>
            <View style={s.between}>
              <View style={s.protocolPill}><Text style={s.protocol}>{trial.protocol_id}</Text></View>
              <View style={s.statusPill}>
                <View style={s.statusDot} />
                <Text style={s.statusText}>{trial.recruitment_status || trial.status || "Active"}</Text>
              </View>
            </View>
            <Text style={s.heroTitle}>{trial.title}</Text>
            <View style={s.detailGrid}>
              {!!trial.ctri_number && <Detail label="CTRI number" value={trial.ctri_number} />}
              {!!trial.phase && <Detail label="Phase" value={trial.phase} />}
              {!!trial.condition && <Detail label="Disease" value={trial.condition} />}
              {!!trial.drug && <Detail label="Drug" value={trial.drug} />}
              {!!trial.duration && <Detail label="Duration" value={trial.duration} />}
              {!!trial.visits?.length && <Detail label="Total visits" value={String(trial.visits.length)} />}
            </View>
            <View style={s.heroFooter}>
              <UserRoundCheck size={14} color="rgba(255,255,255,0.8)" />
              <Text style={s.heroFooterText}>Created by {trial.created_by_name || trial.sponsor_name || "Trial administrator"}{trial.created_by_role ? ` · ${trial.created_by_role}` : ""}</Text>
              <Text style={s.heroFooterDate}>{dateLabel(trial.created_at)}</Text>
            </View>
          </View>
        </Entrance>

        <Entrance index={1} reduced={reducedMotion}>
          <Section title="Recruitment · Across All Sites" icon={Target}>
            <View style={s.metrics}>
              <Metric label="Total Sites" value={String(recruitment.sites.length)} />
              <Metric label="Sample Size" value={target ? String(target) : "—"} />
            </View>
            <Funnel data={recruitment.recruitment} />
          </Section>
        </Entrance>

        <Entrance index={2} reduced={reducedMotion}>
          <Section
            title="Sites · Recruitment Status"
            icon={MapPin}
            action={sponsorLike ? (
              <Pressable
                onPress={() => router.push({ pathname: "/(app)/sponsor/sites", params: { trialId: trial.id } })}
                style={s.inlineAction}
              >
                <Text style={s.inlineActionText}>Add Site</Text>
              </Pressable>
            ) : undefined}
          >
            {recruitment.sites.length ? recruitment.sites.map((site) => (
              <View key={site.id} style={s.siteCard}>
                <View style={s.between}>
                  <View style={{ flex: 1 }}>
                    <Text style={s.siteName}>{site.name}</Text>
                    {!![site.address, site.city, site.state].filter(Boolean).length && (
                      <View style={s.siteLocationRow}>
                        <MapPin size={11} color={colors.mutedFg} />
                        <Text style={s.siteMeta}>{[site.address, site.city, site.state].filter(Boolean).join(", ")}</Text>
                      </View>
                    )}
                    <Text style={s.siteMeta}>PI: {site.pi_name || "Not assigned"}{site.department ? `        Dept: ${site.department}` : ""}</Text>
                    {!!site.pi_email && <Text style={s.siteMeta}>{site.pi_email}</Text>}
                  </View>
                  {!![site.pi_name, site.crc_name].filter(Boolean).length && (
                    <View style={s.sitePeoplePill}><Users size={11} color={colors.primaryDeep} /><Text style={s.sitePeopleText}>{[site.pi_name, site.crc_name].filter(Boolean).length}</Text></View>
                  )}
                </View>
                <Funnel data={site.recruitment || EMPTY_FUNNEL} compact />
              </View>
            )) : <Empty text="No trial sites are assigned yet." />}
          </Section>
        </Entrance>

        <Entrance index={3} reduced={reducedMotion}>
          <Section title="Trial team" icon={Users}>
            {team.length ? team.map((member) => (
              <View key={member.id} style={s.teamCard}>
                <View style={s.avatar}><Text style={s.avatarText}>{initials(member.name)}</Text></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <View style={s.teamTop}>
                    <Text numberOfLines={1} style={s.memberName}>{member.name || "Unnamed member"}</Text>
                    <View style={s.teamRolePill}>
                      <Text style={s.teamRoleText}>
                        {member.role === "pi" ? "PI" : member.role === "crc" ? "CRC" : "Sponsor"}
                      </Text>
                    </View>
                  </View>
                  <Text numberOfLines={1} style={s.memberMeta}>
                    {[member.designation, member.organization].filter(Boolean).join(" · ") || "Trial team"}
                  </Text>
                  <View style={s.contactRow}>
                    {!!member.email && (
                      <Pressable onPress={() => openContact(`mailto:${member.email}`)} style={s.teamContactRow}>
                        <Mail size={12} color={colors.mutedFg} />
                        <Text numberOfLines={1} style={s.teamContactText}>{member.email}</Text>
                      </Pressable>
                    )}
                    {!!member.phone && (
                      <Pressable onPress={() => openContact(`tel:${member.phone}`)} style={s.teamContactRow}>
                        <Phone size={12} color={colors.mutedFg} />
                        <Text style={s.teamContactText}>{member.phone}</Text>
                      </Pressable>
                    )}
                  </View>
                </View>
              </View>
            )) : <Empty text="Assigned trial team members will appear here." />}
          </Section>
        </Entrance>

        <Entrance index={4} reduced={reducedMotion}>
          <Section
            title="Patients"
            icon={UserRoundCheck}
            action={canAddPatient ? (
              <Pressable
                testID="add-patient"
                onPress={() => router.push({
                  pathname: "/(app)/clinical/add-patient",
                  params: { trialId: trial.id },
                })}
                style={s.inlineAction}
              >
                <UserPlus size={13} color={colors.info} />
                <Text style={s.inlineActionText}>Add Patient</Text>
              </Pressable>
            ) : undefined}
          >
            {subjects.length ? subjects.map((subject) => {
              const expanded = expandedSubject === subject.id;
              const visits = subjectVisits[subject.id] || [];
              const subjectStatus = subject.current_visit?.status === "overdue"
                ? "overdue"
                : subject.status;
              return (
                <Pressable
                  key={subject.id}
                  testID={`subject-${subject.id}`}
                  onPress={() => openSubject(subject)}
                  style={s.subjectCard}
                >
                  <View style={s.between}>
                    <View style={s.subjectIdentity}>
                      <View style={s.subjectAvatar}>
                        <Text style={s.subjectAvatarText}>{subject.initials || "—"}</Text>
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={s.subjectId}>{subject.subject_id}</Text>
                        <Text style={s.subjectMeta}>{subject.site} · {subject.visits_completed} visits completed</Text>
                      </View>
                    </View>
                    <View style={[
                      s.subjectStatusPill,
                      {
                        backgroundColor: subjectStatus === "overdue"
                          ? colors.destructive + "16"
                          : subjectStatus === "completed"
                            ? colors.info + "16"
                            : subjectStatus === "withdrawn" || subjectStatus === "dropout"
                              ? colors.mutedFg + "22"
                              : colors.success + "18",
                      },
                    ]}>
                      <Text style={[
                        s.subjectStatus,
                        {
                          color: subjectStatus === "overdue"
                            ? colors.destructive
                            : subjectStatus === "completed"
                              ? colors.info
                              : subjectStatus === "withdrawn" || subjectStatus === "dropout"
                                ? colors.mutedFg
                                : colors.success,
                        },
                      ]}>{subjectStatus}</Text>
                    </View>
                  </View>
                  <View style={s.patientVisitGrid}>
                    <PatientField label="VISIT NO." value={subject.current_visit?.visit_number ? `Visit ${subject.current_visit.visit_number}` : "—"} />
                    <PatientField label="VISIT NAME" value={subject.current_visit?.name || "Not scheduled"} />
                    <PatientField label="VISIT TYPE" value={subject.current_visit?.visit_type || "—"} />
                    <PatientField label="VISIT DATE" value={scheduledDateLabel(subject.current_visit?.scheduled_date)} />
                  </View>
                  {expanded && (
                    <View style={s.subjectVisits}>
                      {loadingSubject === subject.id ? (
                        <ActivityIndicator color={colors.primary} />
                      ) : visits.length ? visits.map((visit) => (
                        <View key={visit.id} style={s.subjectVisitRow}>
                          <View style={s.visitNumber}>
                            <Text style={s.visitNumberText}>{visit.visit_number || "—"}</Text>
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={s.visitName}>{visit.name}</Text>
                            <Text style={s.visitMeta}>
                              {visit.completed_at
                                ? dateLabel(visit.completed_at)
                                : scheduledDateLabel(visit.scheduled_date)}
                            </Text>
                          </View>
                          <Text style={[
                            s.visitStatus,
                            visit.status === "completed" && { color: colors.success },
                          ]}>
                            {visit.status}
                          </Text>
                        </View>
                      )) : <Empty text="No visit instances are available for this subject." />}
                    </View>
                  )}
                </Pressable>
              );
            }) : <Empty text="No patients are enrolled in this trial." />}
            {!!subjects.length && (
              <Text style={s.privacy}>
                This summary uses study IDs and initials only. Direct patient identifiers remain within the clinical site.
              </Text>
            )}
          </Section>
        </Entrance>

        <Entrance index={5} reduced={reducedMotion}>
          <Section
            title="Visit schedule"
            icon={CalendarDays}
            action={sponsorLike ? (
              <Pressable
                onPress={() => router.push({ pathname: "/(app)/sponsor/visit-schedule", params: { id: trial.id } })}
                style={s.inlineAction}
              >
                <Text style={s.inlineActionText}>Edit Schedule</Text>
              </Pressable>
            ) : undefined}
          >
            {(trial.visits || []).length ? (trial.visits || []).map((visit, index) => (
              <View key={visit.id || `${visit.name}-${index}`} style={s.scheduleRow}>
                <View style={s.visitNumber}>
                  <Text style={s.visitNumberText}>{visit.visit_number || index + 1}</Text>
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={s.visitName}>{visit.name}</Text>
                  <Text style={s.visitMeta}>
                    {formatVisitTiming(visit)} · {formatVisitWindow(visit)}
                  </Text>
                </View>
              </View>
            )) : <Empty text="No visit schedule has been created." />}
          </Section>
        </Entrance>

        <Entrance index={6} reduced={reducedMotion}>
          <Section
            title="Documents"
            icon={FileClock}
          >
            {documents.length ? documents.map((document) => (
              <Pressable
                key={document.id}
                testID={`download-document-${document.id}`}
                onPress={() => download(document)}
                disabled={downloading === document.id}
                style={s.documentRow}
              >
                <View style={s.documentIcon}><FileText size={17} color={colors.info} /></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text numberOfLines={1} style={s.documentName}>{document.name}</Text>
                  <Text style={s.documentMeta}>
                    {dateLabel((document as any).created_at)}
                    {document.size ? ` · ${Math.max(1, Math.round(document.size / 1024))} KB` : ""}
                  </Text>
                </View>
                {downloading === document.id
                  ? <ActivityIndicator size="small" color={colors.info} />
                  : <Download size={16} color={colors.info} />}
              </Pressable>
            )) : <Empty text="No persistent trial documents have been uploaded." />}

            <View style={s.versionDivider} />
            <Text style={s.smallCaps}>SCHEDULE VERSION HISTORY</Text>
            {versions.length ? versions.map((version) => (
              <View key={version.id} style={s.versionRow}>
                <View style={s.versionBadge}><Text style={s.versionBadgeText}>v{version.version || "—"}</Text></View>
                <View style={{ flex: 1 }}>
                  <Text style={s.versionTitle}>{version.version_note || version.document_name || "Schedule version"}</Text>
                  <Text style={s.versionMeta}>
                    {dateLabel(version.created_at)}
                    {version.created_by_name ? ` · ${version.created_by_name}` : ""}
                  </Text>
                </View>
              </View>
            )) : <Empty text="No schedule versions have been shared yet." />}
            {canUpload && (
              <Pressable
                testID="upload-document"
                onPress={uploadDocument}
                disabled={uploading}
                style={[s.uploadButton, uploading && s.disabled]}
              >
                {uploading
                  ? <ActivityIndicator size="small" color={colors.info} />
                  : <Upload size={14} color={colors.info} />}
                <Text style={s.uploadButtonText}>{uploading ? "Uploading…" : "+ Upload Document"}</Text>
              </Pressable>
            )}
          </Section>
        </Entrance>

        <Entrance index={7} reduced={reducedMotion}>
          <View style={s.actions}>
            {canEdit && (
              <Pressable testID="edit-trial" onPress={openEdit} style={s.secondaryButton}>
                <Pencil size={16} color={colors.primary} />
                <Text style={s.secondaryButtonText}>Edit Trial</Text>
              </Pressable>
            )}
            <Pressable
              testID="download-protocol"
              onPress={() => download()}
              disabled={!!downloading}
              style={[s.secondaryButton, !!downloading && s.disabled]}
            >
              <Download size={16} color={colors.primary} />
              <Text style={s.secondaryButtonText}>Download Protocol</Text>
            </Pressable>
          </View>
          {canShare && (
            <Pressable
              testID="share-schedule"
              onPress={() => router.push({
                pathname: "/(app)/sponsor/share-schedule",
                params: { id: trial.id },
              })}
              style={s.primaryWide}
            >
              <Share2 size={17} color={colors.white} />
              <Text style={s.primaryButtonText}>Share Schedule</Text>
            </Pressable>
          )}
        </Entrance>
      </ScrollView>

      <Modal
        visible={showActions}
        transparent
        statusBarTranslucent
        animationType={reducedMotion ? "none" : "fade"}
        onRequestClose={() => setShowActions(false)}
      >
        <View style={s.actionMenuRoot}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setShowActions(false)} />
          <View accessibilityRole="menu" style={s.actionMenu}>
            {canEdit && (
              <Pressable
                accessibilityRole="menuitem"
                onPress={() => { setShowActions(false); openEdit(); }}
                style={s.actionMenuItem}
              >
                <Pencil size={16} color={colors.mutedFg} />
                <Text style={s.actionMenuText}>Edit</Text>
              </Pressable>
            )}
            <Pressable
              accessibilityRole="menuitem"
              onPress={() => { setShowActions(false); void download(); }}
              style={s.actionMenuItem}
            >
              <Download size={16} color={colors.mutedFg} />
              <Text style={s.actionMenuText}>Download</Text>
            </Pressable>
            {canShare && (
              <Pressable
                accessibilityRole="menuitem"
                onPress={() => {
                  setShowActions(false);
                  router.push({ pathname: "/(app)/sponsor/share-schedule", params: { id: trial.id } });
                }}
                style={s.actionMenuItem}
              >
                <Share2 size={16} color={colors.mutedFg} />
                <Text style={s.actionMenuText}>Share</Text>
              </Pressable>
            )}
          </View>
        </View>
      </Modal>

      <Modal
        visible={showEdit}
        transparent
        animationType={reducedMotion ? "none" : "slide"}
        onRequestClose={() => !savingEdit && setShowEdit(false)}
      >
        <KeyboardAvoidingView
          style={s.modalRoot}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <Pressable style={s.backdrop} onPress={() => !savingEdit && setShowEdit(false)} />
          <View style={s.editSheet}>
            <View style={s.sheetHandle} />
            <View style={s.sheetHeader}>
              <View style={{ flex: 1 }}>
                <Text style={s.sheetEyebrow}>TRIAL MANAGEMENT</Text>
                <Text style={s.sheetTitle}>Edit trial details</Text>
              </View>
              <Pressable onPress={() => setShowEdit(false)} disabled={savingEdit}>
                <X size={20} color={colors.foreground} />
              </Pressable>
            </View>
            <ScrollView
              contentContainerStyle={s.form}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <EditField label="Title *" value={editForm.title} onChangeText={(value) => setEditForm((current) => ({ ...current, title: value }))} />
              <View style={s.formRow}>
                <View style={{ flex: 1 }}><EditField label="Phase *" value={editForm.phase} onChangeText={(value) => setEditForm((current) => ({ ...current, phase: value }))} /></View>
                <View style={{ flex: 1 }}><EditField label="Condition *" value={editForm.condition} onChangeText={(value) => setEditForm((current) => ({ ...current, condition: value }))} /></View>
              </View>
              <View style={s.formRow}>
                <View style={{ flex: 1 }}><EditField label="Drug" value={editForm.drug} onChangeText={(value) => setEditForm((current) => ({ ...current, drug: value }))} /></View>
                <View style={{ flex: 1 }}><EditField label="Duration" value={editForm.duration} onChangeText={(value) => setEditForm((current) => ({ ...current, duration: value }))} /></View>
              </View>
              <EditField
                label="Target enrollment"
                value={editForm.target_enrollment}
                keyboardType="number-pad"
                onChangeText={(value) => setEditForm((current) => ({ ...current, target_enrollment: value.replace(/\D/g, "") }))}
              />
              <EditField label="CTRI number" value={editForm.ctri_number} onChangeText={(value) => setEditForm((current) => ({ ...current, ctri_number: value }))} />
              <EditField label="Recruitment status" value={editForm.recruitment_status} onChangeText={(value) => setEditForm((current) => ({ ...current, recruitment_status: value }))} />
              <EditField label="Description" value={editForm.description} onChangeText={(value) => setEditForm((current) => ({ ...current, description: value }))} multiline />
            </ScrollView>
            <View style={s.sheetActions}>
              <Pressable onPress={() => setShowEdit(false)} disabled={savingEdit} style={s.cancelButton}>
                <Text style={s.cancelText}>Cancel</Text>
              </Pressable>
              <Pressable onPress={saveEdit} disabled={savingEdit} style={[s.saveButton, savingEdit && s.disabled]}>
                {savingEdit ? <ActivityIndicator color={colors.white} /> : <Check size={17} color={colors.white} />}
                <Text style={s.saveText}>{savingEdit ? "Saving…" : "Save changes"}</Text>
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

function Entrance({
  index,
  reduced,
  children,
}: {
  index: number;
  reduced: boolean;
  children: React.ReactNode;
}) {
  const value = useRef(new Animated.Value(reduced ? 1 : 0)).current;
  useEffect(() => {
    if (reduced) {
      value.setValue(1);
      return;
    }
    Animated.timing(value, {
      toValue: 1,
      delay: index * 65,
      duration: 430,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [index, reduced, value]);
  return (
    <Animated.View
      style={{
        opacity: value,
        transform: [{
          translateY: value.interpolate({
            inputRange: [0, 1],
            outputRange: reduced ? [0, 0] : [14, 0],
          }),
        }],
      }}
    >
      {children}
    </Animated.View>
  );
}

function Section({
  title,
  icon: Icon,
  action,
  children,
}: {
  title: string;
  icon: any;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <View style={s.card}>
      <View style={s.sectionHead}>
        <View style={s.sectionIdentity}>
          <Icon size={16} color={colors.primary} />
          <Text style={s.sectionTitle}>{title}</Text>
        </View>
        {action}
      </View>
      {children}
    </View>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.detail}>
      <Text style={s.detailLabel}>{label}</Text>
      <Text numberOfLines={2} style={s.detailValue}>{value}</Text>
    </View>
  );
}

function PatientField({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.patientField}>
      <Text style={s.patientFieldLabel}>{label}</Text>
      <Text numberOfLines={2} style={s.patientFieldValue}>{value}</Text>
    </View>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.metric}>
      <Text style={s.metricValue}>{value}</Text>
      <Text style={s.metricLabel}>{label}</Text>
    </View>
  );
}

function Funnel({ data, compact = false }: { data: RecruitmentFunnel; compact?: boolean }) {
  const tones: Record<string, { backgroundColor: string; color: string }> = {
    screened: { backgroundColor: colors.info + "1A", color: colors.info },
    screen_fail: { backgroundColor: colors.warning + "26", color: colors.warning },
    randomized: { backgroundColor: colors.violet + "1A", color: colors.violet },
    follow_up: { backgroundColor: colors.accent + "1F", color: colors.accent },
    completed: { backgroundColor: colors.success + "26", color: colors.success },
    withdrawn: { backgroundColor: colors.surface, color: colors.mutedFg },
    dropout: { backgroundColor: colors.destructive + "1A", color: colors.destructive },
  };
  return (
    <View style={s.funnel}>
      {FUNNEL.map((field) => {
        const tone = tones[field.key] || tones.screened;
        return (
        <View key={field.key} style={[s.funnelCell, compact && s.funnelCellCompact, { backgroundColor: tone.backgroundColor }]}>
          <Text style={[s.funnelValue, { color: tone.color }]}>
            {Number(data?.[field.key] || 0)}
          </Text>
          <Text style={s.funnelLabel}>{field.label}</Text>
        </View>
      )})}
    </View>
  );
}

function EditField({
  label,
  multiline,
  ...props
}: React.ComponentProps<typeof TextInput> & { label: string; multiline?: boolean }) {
  return (
    <View>
      <Text style={s.fieldLabel}>{label}</Text>
      <TextInput
        {...props}
        multiline={multiline}
        placeholderTextColor={colors.mutedFg}
        style={[s.input, multiline && s.textarea]}
      />
    </View>
  );
}

function Empty({ text }: { text: string }) {
  return <View style={s.empty}><Text style={s.emptyText}>{text}</Text></View>;
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#FFF8F0" },
  center: { flex: 1, padding: 28, alignItems: "center", justifyContent: "center", gap: 13, backgroundColor: colors.background },
  muted: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
  error: { textAlign: "center", fontFamily: fonts.regular, fontSize: 13, color: colors.destructive },
  header: { minHeight: 66, paddingHorizontal: 18, paddingTop: 7, paddingBottom: 11, flexDirection: "row", alignItems: "center", gap: 14, backgroundColor: "#741847" },
  headerEyebrow: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 1.05, color: "rgba(255,255,255,0.62)" },
  headerTitle: { fontFamily: fonts.bold, fontSize: 14, color: colors.white },
  headerAction: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 17 },
  content: { padding: 14, paddingBottom: 42, gap: 14 },
  feedback: { minHeight: 44, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 15, borderWidth: 1 },
  feedbackSuccess: { borderColor: colors.success + "45", backgroundColor: colors.success + "12" },
  feedbackError: { borderColor: colors.destructive + "45", backgroundColor: colors.destructive + "0D" },
  feedbackText: { flex: 1, fontFamily: fonts.semibold, fontSize: 10.5 },
  hero: { padding: 18, borderRadius: 16, backgroundColor: "#791B4E", ...shadows.sm },
  between: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  protocolPill: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.13)" },
  protocol: { fontFamily: fonts.medium, fontSize: 9.5, color: colors.white },
  statusPill: { paddingHorizontal: 9, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 4, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.22)" },
  statusDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: "#79C58D" },
  statusText: { fontFamily: fonts.semibold, fontSize: 8.5, color: "#8FDA9F", textTransform: "capitalize" },
  heroTitle: { marginTop: 15, fontFamily: fonts.heading, fontSize: 17, lineHeight: 24, color: colors.white },
  heroTags: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 12 },
  heroTag: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.16)" },
  heroTagText: { color: colors.white, fontFamily: fonts.semibold, fontSize: 8.5 },
  heroSponsor: { marginTop: 14, flexDirection: "row", alignItems: "center", gap: 6 },
  heroSponsorText: { flex: 1, fontFamily: fonts.medium, fontSize: 9.5, color: "rgba(255,255,255,0.88)" },
  detailGrid: { marginTop: 17, flexDirection: "row", flexWrap: "wrap", rowGap: 12 },
  detail: { width: "50%", paddingRight: 9 },
  detailLabel: { fontFamily: fonts.semibold, fontSize: 7.5, letterSpacing: 0.4, textTransform: "uppercase", color: "rgba(255,255,255,0.70)" },
  detailValue: { marginTop: 2, fontFamily: fonts.semibold, fontSize: 10.5, lineHeight: 14, color: colors.white },
  heroFooter: { marginTop: 15, paddingTop: 12, flexDirection: "row", alignItems: "center", gap: 6, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.18)" },
  heroFooterText: { flex: 1, fontFamily: fonts.regular, fontSize: 8.5, color: "rgba(255,255,255,0.84)" },
  heroFooterDate: { width: 58, textAlign: "right", fontFamily: fonts.regular, fontSize: 8.5, color: "rgba(255,255,255,0.84)" },
  heroAudit: { display: "none", marginTop: 6, fontFamily: fonts.regular, fontSize: 8.5, lineHeight: 12, color: "rgba(255,255,255,0.62)" },
  card: { padding: 14, gap: 12, borderRadius: 15, borderWidth: 1, borderColor: "#E5D5C8", backgroundColor: "#FFFCF7", ...shadows.sm },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  sectionIdentity: { flex: 1, flexDirection: "row", alignItems: "center", gap: 7 },
  sectionTitle: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  inlineAction: { flexDirection: "row", alignItems: "center", gap: 4 },
  inlineActionText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.info },
  metrics: { flexDirection: "row", gap: 8 },
  metric: { flex: 1, padding: 11, alignItems: "center", borderRadius: 12, backgroundColor: "#F8E9DC" },
  metricValue: { fontFamily: fonts.heading, fontSize: 17, color: "#7B1D4E" },
  metricLabel: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  funnel: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  funnelCell: { width: "23%", minHeight: 51, padding: 5, alignItems: "center", justifyContent: "center", borderRadius: 11, borderWidth: 1, borderColor: "#E9D9CD" },
  funnelCellCompact: { width: "23%", minHeight: 48 },
  funnelValue: { fontFamily: fonts.heading, fontSize: 14, color: colors.foreground },
  funnelLabel: { marginTop: 2, textAlign: "center", fontFamily: fonts.regular, fontSize: 7.5, color: colors.mutedFg },
  smallCaps: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  siteCard: { padding: 12, gap: 10, borderRadius: 16, borderWidth: 1, borderColor: "#E5D2C4", backgroundColor: "#FAEDE2" },
  siteName: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  siteMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, lineHeight: 12, color: colors.mutedFg },
  siteLocationRow: { marginTop: 2, flexDirection: "row", alignItems: "center", gap: 3 },
  sitePeoplePill: { paddingHorizontal: 7, paddingVertical: 4, flexDirection: "row", alignItems: "center", gap: 3, borderRadius: 999, borderWidth: 1, borderColor: "#DDBFC7", backgroundColor: "#FFF8F2" },
  sitePeopleText: { fontFamily: fonts.mono, fontSize: 8, color: colors.primaryDeep },
  percent: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.primary },
  teamCard: { padding: 12, flexDirection: "row", alignItems: "flex-start", gap: 9, borderRadius: 16, borderWidth: 1, borderColor: "#E5D2C4", backgroundColor: "#FAEDE2" },
  avatar: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 17, backgroundColor: "#962552" },
  avatarText: { fontFamily: fonts.bold, fontSize: 10.5, color: colors.white },
  teamTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  memberName: { flex: 1, fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  teamRolePill: { paddingHorizontal: 7, paddingVertical: 3, borderRadius: 999, backgroundColor: colors.secondary },
  teamRoleText: { fontFamily: fonts.semibold, fontSize: 7.5, color: colors.primary },
  memberMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9, color: colors.mutedFg },
  contactRow: { marginTop: 8, paddingTop: 8, gap: 5, borderTopWidth: 1, borderTopColor: colors.border },
  teamContactRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  teamContactText: { flex: 1, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  subjectCard: { padding: 11, gap: 10, borderRadius: 16, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  subjectIdentity: { flex: 1, flexDirection: "row", alignItems: "center", gap: 9 },
  subjectAvatar: { width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.primaryDeep },
  subjectAvatarText: { fontFamily: fonts.bold, fontSize: 10, color: colors.white },
  subjectId: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  subjectMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  subjectStatusPill: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999 },
  subjectStatus: { fontFamily: fonts.semibold, fontSize: 8, color: colors.success, textTransform: "capitalize" },
  patientVisitGrid: { padding: 10, flexDirection: "row", flexWrap: "wrap", rowGap: 9, borderRadius: 13, backgroundColor: colors.card },
  patientField: { width: "50%", paddingRight: 8 },
  patientFieldLabel: { fontFamily: fonts.semibold, fontSize: 7.5, letterSpacing: 0.65, color: colors.mutedFg },
  patientFieldValue: { marginTop: 2, fontFamily: fonts.medium, fontSize: 9.5, lineHeight: 12, color: colors.foreground },
  subjectVisits: { paddingTop: 9, gap: 8, borderTopWidth: 1, borderTopColor: colors.border },
  subjectVisitRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  scheduleRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  visitNumber: { width: 30, height: 30, alignItems: "center", justifyContent: "center", borderRadius: 11, backgroundColor: colors.secondary },
  visitNumberText: { fontFamily: fonts.mono, fontSize: 10, color: colors.primary },
  visitName: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  visitMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  visitStatus: { fontFamily: fonts.semibold, fontSize: 8, color: colors.mutedFg, textTransform: "capitalize" },
  privacy: { padding: 10, borderRadius: 12, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.mutedFg, backgroundColor: colors.surface },
  documentRow: { paddingVertical: 9, flexDirection: "row", alignItems: "center", gap: 9, borderBottomWidth: 1, borderBottomColor: colors.border },
  documentIcon: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: colors.card },
  documentName: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  documentMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  versionDivider: { height: 1, backgroundColor: colors.border },
  versionRow: { padding: 10, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  versionBadge: { width: 34, height: 34, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: colors.secondary },
  versionBadgeText: { fontFamily: fonts.mono, fontSize: 9.5, color: colors.primary },
  versionTitle: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  versionMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  uploadButton: { minHeight: 38, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, borderRadius: 999, borderWidth: 1, borderColor: colors.info, backgroundColor: colors.card },
  uploadButtonText: { fontFamily: fonts.semibold, fontSize: 9.5, color: colors.info },
  empty: { paddingVertical: 14, alignItems: "center" },
  emptyText: { textAlign: "center", fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  actions: { flexDirection: "row", gap: 9 },
  secondaryButton: { flex: 1, minHeight: 46, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 999, borderWidth: 1, borderColor: colors.primary, backgroundColor: colors.card },
  secondaryButtonText: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  primaryButton: { minHeight: 46, paddingHorizontal: 17, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 999, backgroundColor: colors.primary },
  primaryWide: { minHeight: 47, marginTop: 9, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 999, backgroundColor: colors.primary },
  primaryButtonText: { fontFamily: fonts.bold, fontSize: 11, color: colors.white },
  disabled: { opacity: 0.45 },
  actionMenuRoot: { flex: 1, alignItems: "flex-end", paddingTop: Platform.OS === "ios" ? 86 : 64, paddingRight: 14, backgroundColor: "rgba(46,27,51,0.18)" },
  actionMenu: { width: 142, paddingVertical: 6, borderRadius: 15, backgroundColor: colors.card, ...shadows.md },
  actionMenuItem: { minHeight: 42, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  actionMenuText: { fontFamily: fonts.medium, fontSize: 12, color: colors.foreground },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(46,27,51,0.48)" },
  editSheet: { maxHeight: "92%", paddingTop: 10, borderTopLeftRadius: 28, borderTopRightRadius: 28, backgroundColor: colors.background, ...shadows.md },
  sheetHandle: { alignSelf: "center", width: 42, height: 4, marginBottom: 13, borderRadius: 2, backgroundColor: colors.border },
  sheetHeader: { paddingHorizontal: 18, paddingBottom: 14, flexDirection: "row", alignItems: "flex-start", gap: 12, borderBottomWidth: 1, borderBottomColor: colors.border },
  sheetEyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.05, color: colors.primary },
  sheetTitle: { marginTop: 3, fontFamily: fonts.heading, fontSize: 20, color: colors.foreground },
  form: { padding: 18, gap: 13 },
  formRow: { flexDirection: "row", gap: 10 },
  fieldLabel: { marginBottom: 6, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 0.4, color: colors.mutedFg, textTransform: "uppercase" },
  input: { minHeight: 44, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12, color: colors.foreground, outlineStyle: "none" } as any,
  textarea: { minHeight: 88, textAlignVertical: "top" },
  sheetActions: { paddingHorizontal: 18, paddingTop: 12, paddingBottom: Platform.OS === "ios" ? 28 : 18, flexDirection: "row", gap: 10, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  cancelButton: { flex: 1, minHeight: 47, alignItems: "center", justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background },
  cancelText: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  saveButton: { flex: 1.5, minHeight: 47, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 999, backgroundColor: colors.primary },
  saveText: { fontFamily: fonts.bold, fontSize: 11.5, color: colors.white },
});
