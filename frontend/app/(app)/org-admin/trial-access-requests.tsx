import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Modal, Pressable, RefreshControl, ScrollView, StatusBar,
  StyleSheet, Text, TextInput, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import {
  AlertTriangle, Building2, CheckCircle2, ChevronLeft, ChevronRight, KeyRound,
  RefreshCw, ShieldCheck, X, XCircle,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { useOrgContext } from "@/src/components/org-admin-kit";
import { colors as C, fonts, shadows } from "@/src/theme/tokens";

type AccessRequest = {
  id: string;
  trial_id: string;
  trial_title?: string;
  protocol_id?: string;
  org_id: string;
  org_name?: string;
  requester_name?: string;
  reason?: string;
  status?: "pending" | "granted" | "rejected";
  created_at?: string;
  granted_by_name?: string;
  rejected_by_name?: string;
  decision_reason?: string;
  granted_at?: string;
  rejected_at?: string;
};

const FILTERS = ["all", "pending", "granted", "rejected"] as const;
const errorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || fallback;
const dateText = (value?: string) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

export default function TrialAccessRequestsScreen() {
  const router = useRouter();
  const { orgId, loading: orgLoading, error: orgError, retry: retryOrg } = useOrgContext();
  const [rows, setRows] = useState<AccessRequest[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("pending");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AccessRequest | null>(null);

  const load = useCallback(async () => {
    if (!orgId) return;
    setError(null);
    try {
      const response = await api.get(`/org/${orgId}/trial-access-requests`);
      setRows(Array.isArray(response.data) ? response.data : []);
    } catch (loadError) {
      setError(errorMessage(
        loadError,
        "We couldn't load trial-access requests. Please try again.",
      ));
    } finally {
      setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    if (orgId) void load();
  }, [load, orgId]);

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const filtered = useMemo(
    () => rows.filter((row) => filter === "all" || (row.status || "pending") === filter),
    [filter, rows],
  );
  const pendingCount = rows.filter((row) => (row.status || "pending") === "pending").length;

  if (orgLoading) {
    return <CenteredState icon="loading" title="Opening access requests…" />;
  }
  if (orgError || !orgId) {
    return (
      <CenteredState
        icon="error"
        title="Organization unavailable"
        message={orgError || "Your organization could not be resolved."}
        onRetry={retryOrg}
      />
    );
  }

  return (
    <View style={s.page}>
      <StatusBar barStyle="dark-content" backgroundColor={C.background} />
      <SafeAreaView edges={["top"]}>
        <View style={s.header}>
          <Pressable testID="trial-access-back" onPress={() => router.back()} style={s.iconButton}>
            <ChevronLeft size={20} color={C.foreground} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={s.eyebrow}>ORGANIZATION OVERSIGHT</Text>
            <Text style={s.title}>Trial access requests</Text>
          </View>
          <Pressable testID="trial-access-refresh" onPress={() => void refresh()} style={s.iconButton}>
            <RefreshCw size={18} color={C.primary} />
          </Pressable>
        </View>
      </SafeAreaView>

      <ScrollView
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={C.primary} />}
        contentContainerStyle={s.content}
      >
        <View style={s.summary}>
          <View style={s.summaryIcon}><KeyRound size={22} color={C.primary} /></View>
          <View style={{ flex: 1 }}>
            <Text style={s.summaryValue}>{pendingCount}</Text>
            <Text style={s.summaryLabel}>pending decision{pendingCount === 1 ? "" : "s"}</Text>
          </View>
          <ShieldCheck size={26} color={C.success} />
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.filters}>
          {FILTERS.map((item) => (
            <Pressable
              key={item}
              testID={`trial-access-filter-${item}`}
              onPress={() => setFilter(item)}
              style={[s.filter, filter === item && s.filterActive]}
            >
              <Text style={[s.filterText, filter === item && s.filterTextActive]}>
                {item[0].toUpperCase() + item.slice(1)}
              </Text>
            </Pressable>
          ))}
        </ScrollView>

        {loading ? (
          <CenteredState icon="loading" title="Loading requests…" compact />
        ) : error ? (
          <CenteredState icon="error" title="Requests unavailable" message={error} onRetry={load} compact />
        ) : filtered.length === 0 ? (
          <CenteredState
            icon="empty"
            title={`No ${filter === "all" ? "" : `${filter} `}requests`}
            message="Trial-access requests will appear here when another organization asks for full access."
            compact
          />
        ) : (
          <View style={{ gap: 10 }}>
            {filtered.map((request) => {
              const tone = request.status === "granted"
                ? C.success
                : request.status === "rejected"
                  ? C.destructive
                  : C.warning;
              return (
                <Pressable
                  key={request.id}
                  testID={`trial-access-request-${request.id}`}
                  onPress={() => setSelected(request)}
                  style={s.card}
                >
                  <View style={s.requestIcon}><Building2 size={18} color={C.primary} /></View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={s.cardTitle} numberOfLines={1}>{request.org_name || "Organization"}</Text>
                    <Text style={s.cardSub} numberOfLines={1}>
                      {request.protocol_id || request.trial_title || request.trial_id}
                    </Text>
                    <Text style={s.cardDate}>{dateText(request.created_at)}</Text>
                  </View>
                  <View style={{ alignItems: "flex-end", gap: 7 }}>
                    <View style={[s.status, { backgroundColor: `${tone}18` }]}>
                      <Text style={[s.statusText, { color: tone }]}>{request.status || "pending"}</Text>
                    </View>
                    <ChevronRight size={16} color={C.border} />
                  </View>
                </Pressable>
              );
            })}
          </View>
        )}
      </ScrollView>

      <DecisionSheet
        request={selected}
        onClose={() => setSelected(null)}
        onChanged={async () => {
          setSelected(null);
          await load();
        }}
      />
    </View>
  );
}

function DecisionSheet({ request, onClose, onChanged }: {
  request: AccessRequest | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"grant" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReason("");
    setError(null);
    setBusy(null);
  }, [request?.id]);

  const decide = async (decision: "grant" | "reject") => {
    if (!request || (decision === "reject" && reason.trim().length < 5)) return;
    setBusy(decision);
    setError(null);
    try {
      await api.post(
        `/trials/${request.trial_id}/access-requests/${request.id}/${decision}`,
        decision === "reject" ? { reason: reason.trim() } : undefined,
      );
      await onChanged();
    } catch (actionError) {
      setError(errorMessage(actionError, `We couldn't ${decision} this request.`));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal visible={!!request} transparent animationType="slide" onRequestClose={onClose}>
      <View style={s.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={s.sheet}>
          <View style={s.sheetHeader}>
            <View style={{ flex: 1 }}>
              <Text style={s.eyebrow}>FULL TRIAL ACCESS</Text>
              <Text style={s.sheetTitle}>{request?.org_name || "Access request"}</Text>
            </View>
            <Pressable onPress={onClose} style={s.iconButton}><X size={18} color={C.mutedFg} /></Pressable>
          </View>
          {request ? (
            <ScrollView contentContainerStyle={{ paddingBottom: 24, gap: 13 }}>
              <Info label="Trial" value={request.protocol_id || request.trial_title || request.trial_id} />
              <Info label="Requested by" value={request.requester_name || "Organization administrator"} />
              <Info label="Requested" value={dateText(request.created_at)} />
              <View style={s.reasonCard}>
                <Text style={s.infoLabel}>BUSINESS JUSTIFICATION</Text>
                <Text style={s.reasonText}>{request.reason || "No reason supplied."}</Text>
              </View>

              {(request.status || "pending") === "pending" ? (
                <>
                  <TextInput
                    testID="trial-access-decision-reason"
                    value={reason}
                    onChangeText={setReason}
                    placeholder="Rejection reason (required only when rejecting)"
                    placeholderTextColor={C.mutedFg}
                    multiline
                    style={s.input}
                  />
                  <View style={s.actionRow}>
                    <Pressable
                      testID="trial-access-reject"
                      onPress={() => void decide("reject")}
                      disabled={!!busy || reason.trim().length < 5}
                      style={[s.rejectButton, (!!busy || reason.trim().length < 5) && s.disabled]}
                    >
                      {busy === "reject" ? <ActivityIndicator size="small" color={C.destructive} /> : <XCircle size={16} color={C.destructive} />}
                      <Text style={s.rejectText}>Reject</Text>
                    </Pressable>
                    <Pressable
                      testID="trial-access-grant"
                      onPress={() => void decide("grant")}
                      disabled={!!busy}
                      style={[s.grantButton, !!busy && s.disabled]}
                    >
                      {busy === "grant" ? <ActivityIndicator size="small" color={C.primaryFg} /> : <CheckCircle2 size={16} color={C.primaryFg} />}
                      <Text style={s.grantText}>Grant full access</Text>
                    </Pressable>
                  </View>
                </>
              ) : (
                <>
                  <Info
                    label="Decision"
                    value={`${request.status === "granted" ? "Granted" : "Rejected"} by ${request.granted_by_name || request.rejected_by_name || "organization administrator"}`}
                  />
                  {request.decision_reason ? <Info label="Decision note" value={request.decision_reason} /> : null}
                </>
              )}
              {error ? (
                <View style={s.errorBanner}>
                  <AlertTriangle size={16} color={C.destructive} />
                  <Text style={s.errorText}>{error}</Text>
                </View>
              ) : null}
            </ScrollView>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.info}>
      <Text style={s.infoLabel}>{label}</Text>
      <Text style={s.infoValue}>{value}</Text>
    </View>
  );
}

function CenteredState({ icon, title, message, onRetry, compact = false }: {
  icon: "loading" | "error" | "empty";
  title: string;
  message?: string;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <View style={[s.center, compact && { minHeight: 190 }]}>
      {icon === "loading"
        ? <ActivityIndicator color={C.primary} />
        : icon === "error"
          ? <AlertTriangle size={26} color={C.destructive} />
          : <ShieldCheck size={28} color={C.success} />}
      <Text style={s.centerTitle}>{title}</Text>
      {message ? <Text style={s.centerText}>{message}</Text> : null}
      {onRetry ? (
        <Pressable onPress={onRetry} style={s.retry}>
          <RefreshCw size={15} color={C.primary} />
          <Text style={s.retryText}>Retry</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: C.background },
  header: { minHeight: 64, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", gap: 10 },
  iconButton: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1, color: C.accent },
  title: { marginTop: 2, fontFamily: fonts.heading, fontSize: 20, color: C.foreground },
  content: { padding: 16, paddingBottom: 40, gap: 13 },
  summary: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 22, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, ...shadows.sm },
  summaryIcon: { width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(166,33,63,0.10)" },
  summaryValue: { fontFamily: fonts.heading, fontSize: 23, color: C.primary },
  summaryLabel: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg },
  filters: { gap: 7, paddingRight: 8 },
  filter: { height: 34, paddingHorizontal: 14, borderRadius: 999, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  filterActive: { borderColor: C.primary, backgroundColor: C.primary },
  filterText: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  filterTextActive: { color: C.primaryFg },
  card: { flexDirection: "row", alignItems: "center", gap: 11, padding: 13, borderRadius: 18, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, ...shadows.sm },
  requestIcon: { width: 42, height: 42, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(166,33,63,0.09)" },
  cardTitle: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  cardSub: { marginTop: 2, fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg },
  cardDate: { marginTop: 3, fontFamily: fonts.regular, fontSize: 10.5, color: C.mutedFg },
  status: { paddingHorizontal: 8, height: 22, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  statusText: { fontFamily: fonts.bold, fontSize: 10.5, textTransform: "capitalize" },
  center: { minHeight: 260, alignItems: "center", justifyContent: "center", gap: 10, padding: 22, borderRadius: 22, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  centerTitle: { fontFamily: fonts.heading, fontSize: 17, color: C.foreground, textAlign: "center" },
  centerText: { fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 18, color: C.mutedFg, textAlign: "center" },
  retry: { minHeight: 38, flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 15, borderRadius: 999, backgroundColor: C.surface },
  retryText: { fontFamily: fonts.bold, fontSize: 12, color: C.primary },
  overlay: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(46,27,51,0.44)" },
  sheet: { maxHeight: "87%", paddingHorizontal: 18, paddingTop: 17, borderTopLeftRadius: 25, borderTopRightRadius: 25, backgroundColor: C.background },
  sheetHeader: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 14 },
  sheetTitle: { marginTop: 2, fontFamily: fonts.heading, fontSize: 19, color: C.foreground },
  info: { gap: 3, padding: 12, borderRadius: 14, backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  infoLabel: { fontFamily: fonts.semibold, fontSize: 9.5, letterSpacing: 0.6, color: C.mutedFg },
  infoValue: { fontFamily: fonts.medium, fontSize: 13, lineHeight: 18, color: C.foreground },
  reasonCard: { gap: 5, padding: 14, borderRadius: 16, backgroundColor: "rgba(123,107,184,0.08)" },
  reasonText: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: C.foreground },
  input: { minHeight: 82, padding: 12, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, color: C.foreground, fontFamily: fonts.regular, fontSize: 13, textAlignVertical: "top" },
  actionRow: { flexDirection: "row", gap: 9 },
  rejectButton: { flex: 1, minHeight: 47, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 999, borderWidth: 1, borderColor: "rgba(192,57,43,0.28)", backgroundColor: C.card },
  rejectText: { fontFamily: fonts.bold, fontSize: 12.5, color: C.destructive },
  grantButton: { flex: 1.35, minHeight: 47, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, borderRadius: 999, backgroundColor: C.success },
  grantText: { fontFamily: fonts.bold, fontSize: 12.5, color: C.primaryFg },
  disabled: { opacity: 0.45 },
  errorBanner: { flexDirection: "row", alignItems: "center", gap: 8, padding: 11, borderRadius: 13, backgroundColor: "rgba(192,57,43,0.07)", borderWidth: 1, borderColor: "rgba(192,57,43,0.22)" },
  errorText: { flex: 1, fontFamily: fonts.medium, fontSize: 11.5, color: C.destructive },
});
