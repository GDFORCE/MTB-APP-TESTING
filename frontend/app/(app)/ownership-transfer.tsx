import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Pressable, ScrollView, StatusBar, StyleSheet, Text,
  TextInput, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import {
  AlertTriangle, ArrowRight, ArrowRightLeft, Building2, CheckCircle2,
  ChevronLeft, RefreshCw, ShieldCheck, XCircle,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { consoleRouteForType } from "@/src/components/org-admin-kit";
import { colors as C, fonts, shadows } from "@/src/theme/tokens";

type Transfer = {
  id: string;
  org_id: string;
  org_name?: string;
  from_name?: string;
  to_name?: string;
  reason?: string;
  handover?: "deactivate" | "remove";
  created_at?: string;
};

const messageOf = (error: any, fallback: string) =>
  error?.response?.data?.detail || fallback;

export default function OwnershipTransferScreen() {
  const router = useRouter();
  const { user, refresh } = useAuth();
  const [orgId, setOrgId] = useState<string | null>(null);
  const [orgType, setOrgType] = useState<string | undefined>();
  const [transfer, setTransfer] = useState<Transfer | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"accept" | "decline" | null>(null);
  const [declineMode, setDeclineMode] = useState(false);
  const [declineReason, setDeclineReason] = useState("");
  const [done, setDone] = useState<"accepted" | "declined" | null>(null);

  const load = useCallback(async () => {
    const organizationName = (user?.organization || "").trim();
    if (!organizationName) {
      setError("Your account is not linked to an organization.");
      setLoading(false);
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const directory = await api.get("/organizations", {
        params: { search: organizationName },
      });
      const organizations = Array.isArray(directory.data) ? directory.data : [];
      const organization = organizations.find(
        (item: any) => (item.name || "").trim() === organizationName,
      ) || organizations[0];
      if (!organization?.id) {
        throw new Error("Organization not found");
      }
      setOrgId(organization.id);
      setOrgType(organization.type);
      const pending = await api.get(
        `/org/${organization.id}/ownership-transfer/pending`,
      );
      setTransfer(pending.data || null);
    } catch (loadError) {
      setError(messageOf(
        loadError,
        "We couldn't load your pending ownership transfer. Please try again.",
      ));
    } finally {
      setLoading(false);
    }
  }, [user?.organization]);

  useEffect(() => {
    void load();
  }, [load]);

  const accept = async () => {
    if (!orgId || !transfer) return;
    setBusy("accept");
    setError(null);
    try {
      await api.post(
        `/org/${orgId}/ownership-transfer/${transfer.id}/accept`,
      );
      await refresh();
      setTransfer(null);
      setDone("accepted");
    } catch (actionError) {
      setError(messageOf(actionError, "We couldn't accept the transfer."));
    } finally {
      setBusy(null);
    }
  };

  const decline = async () => {
    if (!orgId || !transfer || declineReason.trim().length < 5) return;
    setBusy("decline");
    setError(null);
    try {
      await api.post(
        `/org/${orgId}/ownership-transfer/${transfer.id}/decline`,
        { reason: declineReason.trim() },
      );
      setTransfer(null);
      setDone("declined");
    } catch (actionError) {
      setError(messageOf(actionError, "We couldn't decline the transfer."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <View style={s.page}>
      <StatusBar barStyle="dark-content" backgroundColor={C.background} />
      <SafeAreaView edges={["top"]}>
        <View style={s.header}>
          <Pressable testID="ownership-transfer-back" onPress={() => router.back()} style={s.back}>
            <ChevronLeft size={21} color={C.foreground} />
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={s.eyebrow}>ORGANIZATION GOVERNANCE</Text>
            <Text style={s.title}>Ownership transfer</Text>
          </View>
          <View style={{ width: 40 }} />
        </View>
      </SafeAreaView>

      <ScrollView contentContainerStyle={s.content}>
        {loading ? (
          <View testID="ownership-transfer-loading" style={s.centerCard}>
            <ActivityIndicator color={C.primary} />
            <Text style={s.muted}>Checking for a pending transfer…</Text>
          </View>
        ) : error && !transfer ? (
          <View testID="ownership-transfer-error" style={s.centerCard}>
            <AlertTriangle size={25} color={C.destructive} />
            <Text style={s.centerTitle}>Transfer unavailable</Text>
            <Text style={s.muted}>{error}</Text>
            <Pressable testID="ownership-transfer-retry" onPress={() => void load()} style={s.secondaryButton}>
              <RefreshCw size={15} color={C.primary} />
              <Text style={s.secondaryButtonText}>Retry</Text>
            </Pressable>
          </View>
        ) : done ? (
          <View testID={`ownership-transfer-${done}`} style={s.centerCard}>
            {done === "accepted"
              ? <CheckCircle2 size={34} color={C.success} />
              : <XCircle size={34} color={C.warning} />}
            <Text style={s.centerTitle}>
              {done === "accepted" ? "Ownership accepted" : "Transfer declined"}
            </Text>
            <Text style={s.muted}>
              {done === "accepted"
                ? "You are now the organization administrator. Your permissions have been refreshed."
                : "The current administrator has been notified. No permissions were changed."}
            </Text>
            <Pressable
              testID="ownership-transfer-done"
              onPress={() => done === "accepted"
                ? router.replace(consoleRouteForType(orgType) as any)
                : router.back()}
              style={s.primaryButton}
            >
              <Text style={s.primaryButtonText}>
                {done === "accepted" ? "Open organization oversight" : "Done"}
              </Text>
              <ArrowRight size={16} color={C.primaryFg} />
            </Pressable>
          </View>
        ) : !transfer ? (
          <View testID="ownership-transfer-empty" style={s.centerCard}>
            <ShieldCheck size={31} color={C.success} />
            <Text style={s.centerTitle}>No pending transfer</Text>
            <Text style={s.muted}>
              There is no organization ownership request waiting for your decision.
            </Text>
          </View>
        ) : (
          <>
            <View style={s.heroCard}>
              <View style={s.orgIcon}><Building2 size={24} color={C.primary} /></View>
              <Text style={s.cardEyebrow}>YOU HAVE BEEN NOMINATED</Text>
              <Text style={s.orgName}>{transfer.org_name || user?.organization}</Text>
              <View style={s.transferLine}>
                <View style={s.personPill}><Text style={s.personText}>{transfer.from_name || "Current admin"}</Text></View>
                <ArrowRightLeft size={17} color={C.accent} />
                <View style={[s.personPill, { borderColor: C.primary }]}>
                  <Text style={[s.personText, { color: C.primary }]}>{transfer.to_name || user?.full_name}</Text>
                </View>
              </View>
            </View>

            <View style={s.detailCard}>
              <Text style={s.detailLabel}>Reason recorded by the current administrator</Text>
              <Text style={s.detailValue}>{transfer.reason || "No reason supplied."}</Text>
              <View style={s.divider} />
              <Text style={s.detailLabel}>Outgoing administrator account</Text>
              <Text style={s.detailValue}>
                {transfer.handover === "remove"
                  ? "Removed from the organization after acceptance"
                  : "Deactivated after acceptance"}
              </Text>
              <View style={s.notice}>
                <ShieldCheck size={16} color={C.info} />
                <Text style={s.noticeText}>
                  Nothing changes until you accept. Both decisions are permanently audited.
                </Text>
              </View>
            </View>

            {declineMode ? (
              <View style={s.detailCard}>
                <Text style={s.detailLabel}>Why are you declining?</Text>
                <TextInput
                  testID="ownership-transfer-decline-reason"
                  value={declineReason}
                  onChangeText={setDeclineReason}
                  placeholder="Add a short reason for the current administrator"
                  placeholderTextColor={C.mutedFg}
                  multiline
                  style={s.input}
                />
                <View style={s.actionRow}>
                  <Pressable onPress={() => setDeclineMode(false)} disabled={!!busy} style={s.secondaryButton}>
                    <Text style={s.secondaryButtonText}>Cancel</Text>
                  </Pressable>
                  <Pressable
                    testID="ownership-transfer-confirm-decline"
                    onPress={() => void decline()}
                    disabled={!!busy || declineReason.trim().length < 5}
                    style={[s.dangerButton, (!!busy || declineReason.trim().length < 5) && s.disabled]}
                  >
                    {busy === "decline" ? <ActivityIndicator size="small" color={C.primaryFg} /> : <XCircle size={15} color={C.primaryFg} />}
                    <Text style={s.primaryButtonText}>Confirm decline</Text>
                  </Pressable>
                </View>
              </View>
            ) : (
              <View style={s.actionRow}>
                <Pressable
                  testID="ownership-transfer-decline"
                  onPress={() => setDeclineMode(true)}
                  disabled={!!busy}
                  style={s.secondaryButton}
                >
                  <XCircle size={15} color={C.destructive} />
                  <Text style={[s.secondaryButtonText, { color: C.destructive }]}>Decline</Text>
                </Pressable>
                <Pressable
                  testID="ownership-transfer-accept"
                  onPress={() => void accept()}
                  disabled={!!busy}
                  style={[s.primaryButton, !!busy && s.disabled]}
                >
                  {busy === "accept" ? <ActivityIndicator size="small" color={C.primaryFg} /> : <CheckCircle2 size={16} color={C.primaryFg} />}
                  <Text style={s.primaryButtonText}>Accept ownership</Text>
                </Pressable>
              </View>
            )}

            {error ? (
              <View style={s.errorBanner}>
                <AlertTriangle size={16} color={C.destructive} />
                <Text style={s.errorText}>{error}</Text>
              </View>
            ) : null}
          </>
        )}
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: C.background },
  header: { minHeight: 64, flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 14 },
  back: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.2, color: C.accent },
  title: { marginTop: 2, fontFamily: fonts.heading, fontSize: 20, color: C.foreground },
  content: { padding: 16, paddingBottom: 38, gap: 14 },
  centerCard: { minHeight: 230, alignItems: "center", justifyContent: "center", gap: 12, padding: 24, borderRadius: 24, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, ...shadows.sm },
  centerTitle: { fontFamily: fonts.heading, fontSize: 19, color: C.foreground, textAlign: "center" },
  muted: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: C.mutedFg, textAlign: "center" },
  heroCard: { alignItems: "center", padding: 20, borderRadius: 25, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, ...shadows.sm },
  orgIcon: { width: 52, height: 52, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(166,33,63,0.10)" },
  cardEyebrow: { marginTop: 12, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.2, color: C.accent },
  orgName: { marginTop: 5, fontFamily: fonts.heading, fontSize: 20, color: C.foreground, textAlign: "center" },
  transferLine: { marginTop: 18, flexDirection: "row", alignItems: "center", gap: 8 },
  personPill: { maxWidth: "42%", paddingHorizontal: 11, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: C.border, backgroundColor: C.background },
  personText: { fontFamily: fonts.semibold, fontSize: 11, color: C.mutedFg },
  detailCard: { gap: 8, padding: 17, borderRadius: 21, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, ...shadows.sm },
  detailLabel: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 0.4, textTransform: "uppercase", color: C.mutedFg },
  detailValue: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: C.foreground },
  divider: { height: 1, marginVertical: 4, backgroundColor: C.border },
  notice: { marginTop: 5, flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 11, borderRadius: 14, backgroundColor: "rgba(123,107,184,0.08)" },
  noticeText: { flex: 1, fontFamily: fonts.regular, fontSize: 11.5, lineHeight: 17, color: C.info },
  input: { minHeight: 92, padding: 13, borderRadius: 14, borderWidth: 1, borderColor: C.border, backgroundColor: C.background, color: C.foreground, fontFamily: fonts.regular, fontSize: 13, textAlignVertical: "top" },
  actionRow: { flexDirection: "row", gap: 10 },
  primaryButton: { flex: 1, minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, paddingHorizontal: 15, borderRadius: 999, backgroundColor: C.primary },
  primaryButtonText: { fontFamily: fonts.bold, fontSize: 13, color: C.primaryFg },
  secondaryButton: { flex: 1, minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, paddingHorizontal: 15, borderRadius: 999, borderWidth: 1, borderColor: C.border, backgroundColor: C.card },
  secondaryButtonText: { fontFamily: fonts.bold, fontSize: 13, color: C.primary },
  dangerButton: { flex: 1.4, minHeight: 48, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, paddingHorizontal: 15, borderRadius: 999, backgroundColor: C.destructive },
  disabled: { opacity: 0.45 },
  errorBanner: { flexDirection: "row", alignItems: "center", gap: 8, padding: 12, borderRadius: 14, borderWidth: 1, borderColor: "rgba(192,57,43,0.24)", backgroundColor: "rgba(192,57,43,0.06)" },
  errorText: { flex: 1, fontFamily: fonts.medium, fontSize: 12, color: C.destructive },
});
