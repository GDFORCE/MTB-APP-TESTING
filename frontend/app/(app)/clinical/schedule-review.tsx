import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Linking,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Clock3,
  Download,
  Eye,
  FileText,
  Minus,
  PenLine,
  Plus,
  RefreshCw,
  X,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api, API_BASE } from "@/src/api/client";
import { Body, Small } from "@/src/components/ui";
import { downloadFile, fetchFileUri } from "@/src/lib/upload";
import { formatVisitTiming, formatVisitWindow } from "@/src/lib/visit-timing";
import { colors, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

type Visit = {
  id: string;
  visit_number: number;
  name: string;
  day_offset: number | null;
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

type ChangedVisit = {
  id: string;
  visit_number: number;
  name: string;
  change_type: "added" | "removed" | "modified";
  changed_fields?: string[];
  before?: Partial<Visit> | null;
  after?: Partial<Visit> | null;
};

type ReviewDocument = {
  id: string;
  name: string;
  content_type?: string;
  size?: number;
  url?: string;
  created_at?: string;
};

type Review = {
  id: string;
  trial_id: string;
  protocol_id: string;
  trial_title: string;
  site_name: string;
  status: "pending" | "approved" | "rejected";
  document_id?: string;
  document_name: string;
  document?: ReviewDocument | null;
  schedule_version?: number;
  version_id?: string;
  version_note?: string;
  message?: string;
  shared_by_name: string;
  shared_by_org: string;
  created_at: string;
  reviewed_at?: string;
  pi_notes?: string;
  rejection_reason?: string;
  share_token?: string;
  visits: Visit[];
  changed_visits?: ChangedVisit[];
};

const dateTime = (value?: string) => {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

const timingLabel = (visit?: Partial<Visit> | null) => formatVisitTiming({
  day_offset: visit?.day_offset,
  day_end: visit?.day_end,
  hour_offset: visit?.hour_offset,
  hour_end: visit?.hour_end,
  hour_offset_basis: visit?.hour_offset_basis,
  relative_to: visit?.relative_to,
  relative_offset_days: visit?.relative_offset_days,
  source_day_label: visit?.source_day_label,
  source_timing_label: visit?.source_timing_label,
});

const timingChangeLabel = (visit?: Partial<Visit> | null) => {
  const display = timingLabel(visit);
  const canonical = formatVisitTiming({
    day_offset: visit?.day_offset,
    day_end: visit?.day_end,
    hour_offset: visit?.hour_offset,
    hour_end: visit?.hour_end,
    hour_offset_basis: visit?.hour_offset_basis,
    relative_to: visit?.relative_to,
    relative_offset_days: visit?.relative_offset_days,
  });
  return canonical === display || canonical === "Timing not specified"
    ? display
    : `${display} (${canonical})`;
};

const TIMING_CHANGE_FIELDS = new Set([
  "day_offset", "day_end", "hour_offset", "hour_end", "hour_offset_basis",
  "source_day_label", "source_timing_label", "anchor_study_day", "includes_day_zero",
  "relative_to", "relative_offset_days", "arm", "arm_label", "period", "visit_type",
]);
const WINDOW_CHANGE_FIELDS = new Set(["window_days", "window_before", "window_after"]);

const fileSize = (value?: number) => {
  if (value === undefined || value === null) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
};

const documentType = (contentType?: string) => {
  if (!contentType) return "";
  const subtype = contentType.split("/").pop() || contentType;
  return subtype.replace("vnd.openxmlformats-officedocument.", "").toUpperCase();
};

export default function ScheduleReview() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string }>();
  const [reviews, setReviews] = useState<Review[]>([]);
  const [selectedId, setSelectedId] = useState(params.id || "");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [documentBusy, setDocumentBusy] = useState<"preview" | "download" | null>(null);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"approve" | "reject" | null>(null);
  const [finalDecision, setFinalDecision] = useState<"approved" | "rejected" | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [toast, setToast] = useState("");
  const toastY = useRef(new Animated.Value(70)).current;

  const selected = useMemo(
    () => selectedId ? reviews.find((review) => review.id === selectedId) : reviews[0],
    [reviews, selectedId],
  );
  const displayedVisits = selected?.visits?.slice(0, showAll ? undefined : 4) || [];

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        setError("");
        const response = await api.get("/schedule-reviews");
        if (!alive) return;
        const rows = (response.data || []) as Review[];
        if (params.id) {
          const exactResponse = await api.get(`/schedule-reviews/${params.id}`);
          if (!alive) return;
          const exact = exactResponse.data as Review;
          setReviews([exact, ...rows.filter((row) => row.id !== exact.id)]);
          setSelectedId(exact.id);
        } else {
          setReviews(rows);
          setSelectedId((current) => current || rows[0]?.id || "");
        }
      } catch (e: any) {
        if (alive) setError(e?.response?.data?.detail || "Couldn't load shared schedules.");
      } finally {
        if (alive) setLoading(false);
      }
    };
    load();
    return () => { alive = false; };
  }, [params.id]);

  useEffect(() => {
    setNotes(selected?.pi_notes || "");
    setRejectReason("");
    setShowAll(false);
    setConfirmAction(null);
  }, [selected?.id, selected?.pi_notes]);

  const showToast = (message: string) => {
    setToast(message);
    toastY.setValue(70);
    Animated.spring(toastY, { toValue: 0, useNativeDriver: true }).start();
    setTimeout(() => {
      Animated.timing(toastY, { toValue: 70, duration: 180, useNativeDriver: true })
        .start(() => setToast(""));
    }, 2200);
  };

  const updateReview = (fresh: Review) => {
    setReviews((current) => current.map((review) => review.id === fresh.id ? fresh : review));
  };

  const approve = async () => {
    if (!selected || selected.status !== "pending" || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.post(`/schedule-reviews/${selected.id}/approve`, {
        notes: notes.trim(),
      });
      updateReview(response.data);
      setConfirmAction(null);
      setFinalDecision("approved");
      showToast("Schedule approved and activated for your site");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't approve this schedule.");
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!selected || !rejectReason.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api.post(`/schedule-reviews/${selected.id}/reject`, {
        reason: rejectReason.trim(),
        notes: notes.trim(),
      });
      updateReview(response.data);
      setConfirmAction(null);
      setFinalDecision("rejected");
      showToast("Rejection and comments sent to the sponsor");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't reject this schedule.");
    } finally {
      setBusy(false);
    }
  };

  const openDocument = async (download = false) => {
    if (!selected) return;
    if (selected.document?.id) {
      setDocumentBusy(download ? "download" : "preview");
      setError("");
      try {
        if (download) {
          await downloadFile(selected.document);
        } else {
          const uri = await fetchFileUri(selected.document.id);
          await Linking.openURL(uri);
        }
      } catch {
        setError(`Couldn't ${download ? "download" : "preview"} the schedule document.`);
      } finally {
        setDocumentBusy(null);
      }
      return;
    }
    const rawSource = selected.document?.url
      || (selected.share_token ? `${API_BASE}/shares/${selected.share_token}/schedule.pdf` : "");
    if (!rawSource) {
      setError("No document is attached to this schedule.");
      return;
    }
    const source = /^https?:\/\//i.test(rawSource)
      ? rawSource
      : `${API_BASE.replace(/\/api\/?$/, "")}${rawSource.startsWith("/") ? "" : "/"}${rawSource}`;
    const url = download && !source.includes("?") ? `${source}?download=1` : source;
    Linking.openURL(url).catch(() => {
      setError(`Couldn't ${download ? "download" : "preview"} the schedule document.`);
    });
  };

  if (loading) {
    return <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View>;
  }

  if (!selected) {
    return (
      <View style={s.page}>
        <SafeAreaView edges={["top"]} style={s.header}>
          <Pressable onPress={() => router.back()} hitSlop={10}><ArrowLeft size={20} color={colors.white} /></Pressable>
          <Body color={colors.white} weight="700">Review Schedule</Body>
          <View style={{ width: 20 }} />
        </SafeAreaView>
        <View style={s.empty}>
          <View style={s.emptyIcon}><FileText size={28} color={colors.primary} /></View>
          <Body weight="700">{error ? "Schedule unavailable" : "No schedules awaiting review"}</Body>
          <Small style={{ marginTop: 5, textAlign: "center" }}>
            {error || "New schedules shared by sponsors will appear here."}
          </Small>
        </View>
      </View>
    );
  }

  const decided = selected.status !== "pending";
  const document = selected.document;
  const versionLabel = selected.schedule_version ? `v${selected.schedule_version}` : "";

  return (
    <View style={s.page}>
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={10}><ArrowLeft size={20} color={colors.white} /></Pressable>
        <Body color={colors.white} weight="700">Review Schedule</Body>
        <Pressable onPress={() => router.replace("/(app)/pi/dashboard" as never)}>
          <Small color="#F5C7D2" weight="700">Dashboard</Small>
        </Pressable>
      </SafeAreaView>

      {reviews.length > 1 && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.queue}>
          {reviews.map((review) => (
            <Pressable
              key={review.id}
              onPress={() => {
                setFinalDecision(null);
                setSelectedId(review.id);
              }}
              style={[s.queueChip, selected.id === review.id && s.queueChipActive]}
            >
              <Small numberOfLines={1} weight="700" color={selected.id === review.id ? colors.white : colors.foreground}>
                {review.protocol_id || review.trial_title}
              </Small>
              <View style={[s.queueDot, {
                backgroundColor: review.status === "pending"
                  ? colors.warning
                  : review.status === "approved" ? colors.success : colors.destructive,
              }]} />
            </Pressable>
          ))}
        </ScrollView>
      )}

      <ScrollView contentContainerStyle={[s.content, { paddingBottom: decided ? 92 : 118 }]} showsVerticalScrollIndicator={false}>
        <View style={s.senderCard}>
          <View style={s.senderRow}>
            <View style={s.senderIcon}><FileText size={21} color="#F5C7D2" /></View>
            <View style={{ flex: 1 }}>
              <Body color={colors.white} weight="700">{document?.name || selected.document_name}</Body>
              <Small color="rgba(255,255,255,0.72)" style={{ marginTop: 3 }}>
                From: {selected.shared_by_org || "Sponsor"} · {selected.shared_by_name}
              </Small>
              <Small color="#F5C7D2" style={{ marginTop: 2 }}>Shared: {dateTime(selected.created_at)}</Small>
            </View>
          </View>
          {!!selected.version_note && (
            <View style={s.senderSection}>
              <Small color="#F5C7D2">Version note {versionLabel ? `· ${versionLabel}` : ""}</Small>
              <Body color={colors.white} weight="700" style={{ marginTop: 2 }}>{selected.version_note}</Body>
            </View>
          )}
          {!!selected.message && (
            <View style={{ marginTop: 10 }}>
              <Small color="#F5C7D2">Message from sponsor</Small>
              <Body color="#FDEEF2" style={{ marginTop: 2, fontStyle: "italic" }}>“{selected.message}”</Body>
            </View>
          )}
        </View>

        <View style={s.card}>
          <View style={s.documentRow}>
            <View style={s.documentIcon}><FileText size={19} color={colors.info} /></View>
            <View style={{ flex: 1 }}>
              <Body weight="700">{document?.name || selected.document_name}</Body>
              <Small style={{ marginTop: 2 }}>{selected.protocol_id} · {selected.site_name}</Small>
              <View style={s.metaWrap}>
                {!!documentType(document?.content_type) && <Small style={s.metaPill}>{documentType(document?.content_type)}</Small>}
                {!!fileSize(document?.size) && <Small style={s.metaPill}>{fileSize(document?.size)}</Small>}
                {!!versionLabel && <Small style={s.metaPill}>{versionLabel}</Small>}
              </View>
              {!!document?.created_at && <Small style={{ marginTop: 5 }}>Uploaded {dateTime(document.created_at)}</Small>}
            </View>
          </View>
          <View style={s.documentActions}>
            <Pressable disabled={!!documentBusy} testID="preview-schedule" onPress={() => openDocument(false)} style={[s.documentButton, s.previewButton]}>
              {documentBusy === "preview"
                ? <ActivityIndicator size="small" color={colors.info} />
                : <><Eye size={15} color={colors.info} /><Small color={colors.info} weight="700">Preview</Small></>}
            </Pressable>
            <Pressable disabled={!!documentBusy} testID="download-schedule" onPress={() => openDocument(true)} style={s.documentButton}>
              {documentBusy === "download"
                ? <ActivityIndicator size="small" color={colors.foreground} />
                : <><Download size={15} color={colors.foreground} /><Small weight="700">Download</Small></>}
            </Pressable>
          </View>
        </View>

        {!!selected.version_note && (
          <View style={s.versionCard}>
            <View style={s.inlineTitle}>
              <PenLine size={15} color={colors.warning} />
              <Body color={colors.warning} weight="700">Version Notes</Body>
            </View>
            <Small color={colors.warning} style={{ marginTop: 5 }}>{selected.version_note}</Small>
            {!!selected.schedule_version && selected.schedule_version > 1 && (
              <Small color={colors.warning} style={{ marginTop: 3 }}>Previous version: v{selected.schedule_version - 1}</Small>
            )}
          </View>
        )}

        {!!selected.changed_visits?.length && (
          <View style={s.changesCard}>
            <View style={s.changesHead}>
              <View style={s.inlineTitle}>
                <RefreshCw size={15} color={colors.primary} />
                <Body weight="700">What changed</Body>
              </View>
              <Small>{selected.changed_visits.length} updates</Small>
            </View>
            {selected.changed_visits.map((change, index) => {
              const visit: Partial<Visit> = change.after || change.before || {
                id: change.id,
                visit_number: change.visit_number,
                name: change.name,
              };
              const tone = change.change_type === "added"
                ? colors.success
                : change.change_type === "removed" ? colors.destructive : colors.warning;
              const ChangeIcon = change.change_type === "added"
                ? Plus
                : change.change_type === "removed" ? Minus : RefreshCw;
              const fields = change.changed_fields || [];
              const timingChanged = fields.some((field) => TIMING_CHANGE_FIELDS.has(field));
              const windowChanged = fields.some((field) => WINDOW_CHANGE_FIELDS.has(field));
              const remainingFields = fields.filter((field) => (
                field !== "name"
                && !TIMING_CHANGE_FIELDS.has(field)
                && !WINDOW_CHANGE_FIELDS.has(field)
              ));
              return (
                <View key={`${change.change_type}-${change.id}-${index}`} style={s.changeRow}>
                  <View style={[s.changeIcon, { backgroundColor: tone + "14" }]}>
                    <ChangeIcon size={14} color={tone} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <View style={s.changeTitle}>
                      <Body weight="700">{visit.name || change.name}</Body>
                      <Small weight="700" color={tone}>{change.change_type.toUpperCase()}</Small>
                    </View>
                    {change.change_type === "modified" ? (
                      <View style={{ marginTop: 4, gap: 2 }}>
                        {fields.includes("name") && (
                          <Small>{change.before?.name || "—"} → {change.after?.name || "—"}</Small>
                        )}
                        {timingChanged && (
                          <Small>Timing: {timingChangeLabel(change.before)} → {timingChangeLabel(change.after)}</Small>
                        )}
                        {windowChanged && (
                          <Small>
                            Window: {formatVisitWindow(change.before || {})} → {formatVisitWindow(change.after || {})}
                          </Small>
                        )}
                        {!!remainingFields.length && (
                          <Small>Updated: {remainingFields.map((field) => field.replace(/_/g, " ")).join(", ")}</Small>
                        )}
                        {!fields.length && <Small>Visit details were updated in this version.</Small>}
                      </View>
                    ) : (
                      <Small style={{ marginTop: 3 }}>
                        Visit {visit.visit_number || change.visit_number} · {timingLabel(visit)} · {formatVisitWindow(visit)}
                      </Small>
                    )}
                  </View>
                </View>
              );
            })}
          </View>
        )}

        <View style={[s.card, { padding: 0, overflow: "hidden" }]}>
          <View style={s.scheduleHead}>
            <Body weight="700">Schedule Summary</Body>
            <Small>{selected.visits.length} visits total</Small>
          </View>
          {displayedVisits.map((visit, index) => (
            <View key={visit.id} style={[s.visitRow, index % 2 === 1 && s.altRow]}>
              <Small style={s.visitNumber}>Visit {visit.visit_number}</Small>
              <Body style={{ flex: 1 }} numberOfLines={1}>{visit.name}</Body>
              <Small style={s.day}>{timingLabel(visit)}</Small>
              <Small style={s.window}>{formatVisitWindow(visit)}</Small>
            </View>
          ))}
          {!showAll && selected.visits.length > 4 && (
            <Pressable testID="view-all-visits" onPress={() => setShowAll(true)} style={s.viewAll}>
              <Small color={colors.info} weight="700">View All {selected.visits.length} Visits ›</Small>
            </Pressable>
          )}
        </View>

        <View>
          <Small weight="700">PI Notes · visible in the audit log</Small>
          <TextInput
            value={notes}
            onChangeText={(value) => setNotes(value.slice(0, 1000))}
            editable={!decided}
            multiline
            textAlignVertical="top"
            placeholder="Add your review notes."
            placeholderTextColor={colors.mutedFg}
            style={[s.notes, decided && { opacity: 0.7 }]}
          />
        </View>

        {decided && (
          <View style={s.auditCard}>
            <View style={s.inlineTitle}>
              <Clock3 size={15} color={colors.mutedFg} />
              <Small weight="700">Decision recorded {dateTime(selected.reviewed_at)}</Small>
            </View>
            {!!selected.pi_notes && <Small style={{ marginTop: 7 }}>PI notes: {selected.pi_notes}</Small>}
            {!!selected.rejection_reason && (
              <Small color={colors.destructive} style={{ marginTop: 5 }}>Rejection reason: {selected.rejection_reason}</Small>
            )}
          </View>
        )}

        {!!error && <Small color={colors.destructive}>{error}</Small>}
      </ScrollView>

      {!decided ? (
        <View style={s.footer}>
          <Pressable testID="reject-schedule" disabled={busy} onPress={() => setRejectOpen(true)} style={[s.footerButton, s.rejectButton]}>
            <Small color={colors.destructive} weight="700">Reject with Comments</Small>
          </Pressable>
          <Pressable testID="approve-schedule" disabled={busy} onPress={() => setConfirmAction("approve")} style={[s.footerButton, s.approveButton, busy && { opacity: 0.65 }]}>
            <Small color={colors.white} weight="700">Approve & Activate →</Small>
          </Pressable>
        </View>
      ) : (
        <View style={[s.decidedBar, selected.status === "approved" ? s.approvedBar : s.rejectedBar]}>
          {selected.status === "approved"
            ? <Check size={19} color={colors.success} />
            : <X size={19} color={colors.destructive} />}
          <Small weight="700" color={selected.status === "approved" ? colors.success : colors.destructive}>
            {selected.status === "approved" ? "Schedule approved and activated" : "Rejection sent to sponsor"}
          </Small>
        </View>
      )}

      <Modal visible={rejectOpen} transparent animationType="slide" onRequestClose={() => setRejectOpen(false)}>
        <View style={s.modalBackdrop}>
          <View style={s.sheet}>
            <View style={s.sheetHandle} />
            <Body weight="700" style={{ fontSize: 19 }}>Reject Schedule</Body>
            <Small style={{ marginTop: 4 }}>The sponsor will receive your reason and review notes.</Small>
            <Small weight="700" style={{ marginTop: 17 }}>Reason for rejection *</Small>
            <TextInput
              autoFocus
              multiline
              textAlignVertical="top"
              value={rejectReason}
              onChangeText={(value) => setRejectReason(value.slice(0, 1000))}
              placeholder="Explain the schedule changes required."
              placeholderTextColor={colors.mutedFg}
              style={s.rejectInput}
            />
            <View style={s.sheetActions}>
              <Pressable disabled={busy} onPress={() => setRejectOpen(false)} style={[s.sheetButton, s.cancelButton]}>
                <Small weight="700">Cancel</Small>
              </Pressable>
              <Pressable
                testID="confirm-reject-schedule"
                disabled={!rejectReason.trim() || busy}
                onPress={() => {
                  setRejectOpen(false);
                  setConfirmAction("reject");
                }}
                style={[s.sheetButton, { backgroundColor: colors.destructive }, (!rejectReason.trim() || busy) && { opacity: 0.45 }]}
              >
                <Small color={colors.white} weight="700">Review Rejection →</Small>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal visible={!!confirmAction} transparent animationType="fade" onRequestClose={() => !busy && setConfirmAction(null)}>
        <View style={s.confirmBackdrop}>
          <View style={s.confirmCard}>
            <View style={[s.confirmIcon, {
              backgroundColor: confirmAction === "approve" ? colors.success + "14" : colors.destructive + "14",
            }]}>
              {confirmAction === "approve"
                ? <Check size={24} color={colors.success} />
                : <AlertTriangle size={24} color={colors.destructive} />}
            </View>
            <Body weight="700" style={s.confirmTitle}>
              {confirmAction === "approve" ? "Approve and activate?" : "Send this rejection?"}
            </Body>
            <Small style={s.confirmCopy}>
              {confirmAction === "approve"
                ? `This activates ${versionLabel || "this schedule"} for ${selected.site_name}.`
                : `The sponsor will receive your reason: “${rejectReason.trim()}”`}
            </Small>
            {!!notes.trim() && (
              <View style={s.confirmNotes}>
                <Small weight="700">PI notes</Small>
                <Small style={{ marginTop: 3 }}>{notes.trim()}</Small>
              </View>
            )}
            <View style={s.sheetActions}>
              <Pressable disabled={busy} onPress={() => setConfirmAction(null)} style={[s.sheetButton, s.cancelButton]}>
                <Small weight="700">Go Back</Small>
              </Pressable>
              <Pressable
                testID={confirmAction === "approve" ? "confirm-approve-schedule" : "send-reject-schedule"}
                disabled={busy}
                onPress={confirmAction === "approve" ? approve : reject}
                style={[s.sheetButton, { backgroundColor: confirmAction === "approve" ? colors.primaryDeep : colors.destructive }]}
              >
                {busy
                  ? <ActivityIndicator color={colors.white} />
                  : <Small color={colors.white} weight="700">
                    {confirmAction === "approve" ? "Yes, Activate" : "Send Rejection"}
                  </Small>}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal visible={!!finalDecision} transparent animationType="fade" onRequestClose={() => setFinalDecision(null)}>
        <View style={s.confirmBackdrop}>
          <View style={s.confirmCard}>
            <View style={[s.confirmIcon, {
              backgroundColor: finalDecision === "approved" ? colors.success + "14" : colors.destructive + "14",
            }]}>
              {finalDecision === "approved"
                ? <Check size={25} color={colors.success} />
                : <X size={25} color={colors.destructive} />}
            </View>
            <Body weight="700" style={s.confirmTitle}>
              {finalDecision === "approved" ? "Schedule activated" : "Rejection sent"}
            </Body>
            <Small style={s.confirmCopy}>
              {finalDecision === "approved"
                ? `The approved schedule is now active for ${selected.site_name}.`
                : `${selected.shared_by_org || "The sponsor"} has been notified with your comments.`}
            </Small>
            {!!selected.reviewed_at && (
              <View style={s.finalTime}>
                <Clock3 size={14} color={colors.mutedFg} />
                <Small>{dateTime(selected.reviewed_at)}</Small>
              </View>
            )}
            <Pressable testID="close-review-confirmation" onPress={() => setFinalDecision(null)} style={s.doneButton}>
              <Small color={colors.white} weight="700">Done</Small>
            </Pressable>
          </View>
        </View>
      </Modal>

      {!!toast && (
        <Animated.View style={[s.toast, { transform: [{ translateY: toastY }] }]}>
          <Check size={16} color={colors.white} /><Small color={colors.white} weight="700">{toast}</Small>
        </Animated.View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  header: { minHeight: 70, paddingTop: 8, paddingBottom: 12, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.primaryDeep },
  queue: { paddingHorizontal: 14, paddingVertical: 9, gap: 8, borderBottomWidth: 1, borderBottomColor: colors.border },
  queueChip: { maxWidth: 150, height: 34, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 7, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  queueChipActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  queueDot: { width: 7, height: 7, borderRadius: 4 },
  content: { padding: 14, gap: 13 },
  senderCard: { padding: 16, borderRadius: 20, backgroundColor: colors.primaryDeep, ...shadows.sm },
  senderRow: { flexDirection: "row", alignItems: "flex-start", gap: 11 },
  senderIcon: { width: 42, height: 42, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.12)" },
  senderSection: { marginTop: 13, paddingTop: 11, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.12)" },
  card: { padding: 14, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  documentRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  documentIcon: { width: 40, height: 40, borderRadius: 13, alignItems: "center", justifyContent: "center", backgroundColor: colors.info + "12" },
  metaWrap: { marginTop: 7, flexDirection: "row", flexWrap: "wrap", gap: 5 },
  metaPill: { paddingHorizontal: 7, paddingVertical: 3, borderRadius: 7, overflow: "hidden", backgroundColor: colors.secondary },
  documentActions: { marginTop: 12, flexDirection: "row", gap: 9 },
  documentButton: { flex: 1, height: 40, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 12, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background },
  previewButton: { borderColor: colors.info + "33", backgroundColor: colors.info + "0D" },
  versionCard: { padding: 14, borderRadius: 17, borderWidth: 1, borderColor: colors.warning + "35", backgroundColor: colors.warning + "10" },
  inlineTitle: { flexDirection: "row", alignItems: "center", gap: 7 },
  changesCard: { borderRadius: 18, overflow: "hidden", borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  changesHead: { padding: 14, flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: colors.border },
  changeRow: { minHeight: 62, paddingHorizontal: 13, paddingVertical: 10, flexDirection: "row", alignItems: "flex-start", gap: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  changeIcon: { width: 30, height: 30, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  changeTitle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  scheduleHead: { padding: 14, flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderBottomWidth: 1, borderBottomColor: colors.border },
  visitRow: { minHeight: 46, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 7, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  altRow: { backgroundColor: colors.background + "88" },
  visitNumber: { width: 48 },
  day: { width: 100, textAlign: "right" },
  window: { width: 90, textAlign: "right" },
  viewAll: { paddingVertical: 12, alignItems: "center" },
  notes: { minHeight: 88, marginTop: 7, padding: 12, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 18, color: colors.foreground, outlineStyle: "none" } as any,
  auditCard: { padding: 13, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.secondary + "66" },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, padding: 14, paddingBottom: 18, flexDirection: "row", gap: 9, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  footerButton: { flex: 1, minHeight: 46, paddingHorizontal: 8, alignItems: "center", justifyContent: "center", borderRadius: 14 },
  rejectButton: { borderWidth: 1.5, borderColor: colors.destructive },
  approveButton: { backgroundColor: colors.primaryDeep },
  decidedBar: { position: "absolute", left: 0, right: 0, bottom: 0, minHeight: 66, paddingBottom: 12, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderTopWidth: 1 },
  approvedBar: { borderTopColor: colors.success + "35", backgroundColor: colors.success + "10" },
  rejectedBar: { borderTopColor: colors.destructive + "35", backgroundColor: colors.destructive + "10" },
  modalBackdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(30,18,22,0.5)" },
  sheet: { padding: 19, paddingBottom: 28, borderTopLeftRadius: 24, borderTopRightRadius: 24, backgroundColor: colors.card },
  sheetHandle: { width: 38, height: 4, marginBottom: 17, alignSelf: "center", borderRadius: 2, backgroundColor: colors.border },
  rejectInput: { minHeight: 105, marginTop: 7, padding: 12, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 18, color: colors.foreground, outlineStyle: "none" } as any,
  sheetActions: { marginTop: 14, flexDirection: "row", gap: 9 },
  sheetButton: { flex: 1, minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: 14 },
  cancelButton: { borderWidth: 1, borderColor: colors.border },
  confirmBackdrop: { flex: 1, padding: 24, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(30,18,22,0.55)" },
  confirmCard: { width: "100%", maxWidth: 380, padding: 20, borderRadius: 22, backgroundColor: colors.card, ...shadows.md },
  confirmIcon: { width: 52, height: 52, marginBottom: 13, alignSelf: "center", alignItems: "center", justifyContent: "center", borderRadius: 18 },
  confirmTitle: { fontSize: 19, textAlign: "center" },
  confirmCopy: { marginTop: 7, textAlign: "center", lineHeight: 18 },
  confirmNotes: { marginTop: 14, padding: 11, borderRadius: 12, backgroundColor: colors.secondary },
  finalTime: { marginTop: 13, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  doneButton: { minHeight: 46, marginTop: 17, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: colors.primaryDeep },
  toast: { position: "absolute", left: spacing.md, right: spacing.md, bottom: 82, minHeight: 48, paddingHorizontal: 15, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderRadius: radii.md, backgroundColor: colors.success, ...shadows.md },
  empty: { flex: 1, padding: 32, alignItems: "center", justifyContent: "center" },
  emptyIcon: { width: 68, height: 68, marginBottom: 14, borderRadius: 34, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
});
