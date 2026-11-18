// Protocol extraction gaps — admin view of the PROTOCOL_DEFINED
// extension-handler process (see backend/app/api/uctsm_admin.py).
//
// Live, admin-gated endpoints:
//   ranking ... GET  /uctsm/admin/unsupported-handlers
//   alerts ..... GET  /uctsm/admin/unsupported-handlers/alerts
//   apply ...... POST /uctsm/admin/unsupported-handlers/{handler}/revalidate
//
// This screen only ever shows and triggers -- it never writes clinical
// scheduling logic. "Revalidate" re-checks already-extracted protocols after
// a developer has shipped and registered a real handler; it never re-calls
// the AI and never invents a fix.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Animated, RefreshControl, ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { Menu, RefreshCcw, AlertTriangle, CheckCircle2, Wrench, FileText, ChevronDown, ChevronUp } from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, st } from "./users";

type HandlerGap = {
  handler: string;
  occurrence_count: number;
  distinct_schedule_versions: number;
  sample_message: string;
};
type EvidenceQuote = { source_text: string; page_number: number | null; schedule_version_id: string };
type RevalidateResult = {
  handler: string;
  processed: number;
  now_fully_clear: number;
  results: Array<{ schedule_version_id: string; fully_clear: boolean; remaining_blocking_issue_codes: string[] }>;
};

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function severityColor(occurrenceCount: number): string {
  if (occurrenceCount >= 5) return C.destructive;
  if (occurrenceCount >= 2) return C.warning;
  return C.mutedFg;
}

export default function AdminProtocolExtractionGaps() {
  const { open } = useAdminDrawer();
  const [gaps, setGaps] = useState<HandlerGap[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyHandler, setBusyHandler] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<Record<string, RevalidateResult>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<Record<string, EvidenceQuote[]>>({});
  const [evidenceLoading, setEvidenceLoading] = useState<string | null>(null);

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
      const r = await api.get("/uctsm/admin/unsupported-handlers");
      setGaps(Array.isArray(r.data?.handlers) ? r.data.handlers : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load protocol extraction gaps. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const revalidate = async (handler: string) => {
    setBusyHandler(handler);
    try {
      const r = await api.post(`/uctsm/admin/unsupported-handlers/${encodeURIComponent(handler)}/revalidate`);
      const result: RevalidateResult = r.data;
      setLastResult((prev) => ({ ...prev, [handler]: result }));
      showToast(
        result.processed === 0
          ? `No protocols currently blocked by ${handler}.`
          : `${result.now_fully_clear} of ${result.processed} protocol(s) cleared.`,
      );
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't revalidate affected protocols"));
    } finally { setBusyHandler(null); }
  };

  const toggleEvidence = async (handler: string) => {
    if (expanded === handler) { setExpanded(null); return; }
    setExpanded(handler);
    if (evidence[handler]) return; // already fetched
    setEvidenceLoading(handler);
    try {
      const r = await api.get(`/uctsm/admin/unsupported-handlers/${encodeURIComponent(handler)}/evidence`);
      setEvidence((prev) => ({ ...prev, [handler]: r.data?.evidence || [] }));
    } catch (e) {
      showToast(errMsg(e, "Couldn't load the real protocol text for this pattern"));
      setExpanded(null);
    } finally { setEvidenceLoading(null); }
  };

  const totalGaps = gaps?.length || 0;
  const totalProtocolsBlocked = (gaps || []).reduce((sum, g) => sum + g.distinct_schedule_versions, 0);

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          <SafeAreaView edges={["top"]}>
            <View style={st.heroTop}>
              <Pressable testID="admin-menu" onPress={open} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
              <View style={{ flex: 1, minWidth: 0 }}>
                <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
                <RNText style={st.heroTitle} numberOfLines={1}>Protocol extraction gaps</RNText>
              </View>
              <Pressable testID="gaps-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
            </View>
            <RNText style={st.heroSub}>Patterns no deterministic handler exists for yet, ranked by real impact.</RNText>
          </SafeAreaView>
        </LinearGradient>

        {loading ? (
          <Loading label="Loading protocol extraction gaps…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16, gap: 12 }}>
            <View style={st.tileGrid}>
              <View style={st.tile}>
                <RNText style={[st.tileValue, { color: totalGaps > 0 ? C.warning : C.success }]}>{totalGaps}</RNText>
                <RNText style={st.tileLabel}>Unbuilt patterns</RNText>
              </View>
              <View style={st.tile}>
                <RNText style={[st.tileValue, { color: C.mutedFg }]}>{totalProtocolsBlocked}</RNText>
                <RNText style={st.tileLabel}>Protocols blocked</RNText>
              </View>
            </View>

            {totalGaps === 0 ? (
              <EmptyCard message="No unsupported protocol patterns right now — every extracted schedule is either fully resolved or waiting on normal clinical review." />
            ) : (
              <View style={{ gap: 10 }}>
                {gaps!.map((gap) => {
                  const result = lastResult[gap.handler];
                  const color = severityColor(gap.occurrence_count);
                  return (
                    <View key={gap.handler} style={[st.card, { padding: 14, gap: 8 }]}>
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <AlertTriangle size={16} color={color} />
                        <RNText style={{ fontWeight: "700", color: C.foreground, flex: 1 }} numberOfLines={1}>
                          {gap.handler}
                        </RNText>
                      </View>
                      <RNText style={{ color: C.mutedFg, fontSize: 13 }}>
                        Blocking {gap.occurrence_count} issue(s) across {gap.distinct_schedule_versions} protocol schedule(s)
                      </RNText>
                      <RNText style={{ color: C.mutedFg, fontSize: 12 }} numberOfLines={3}>
                        {gap.sample_message}
                      </RNText>
                      {result && (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                          <CheckCircle2 size={14} color={result.now_fully_clear === result.processed ? C.success : C.warning} />
                          <RNText style={{ color: C.mutedFg, fontSize: 12 }}>
                            Last run: {result.now_fully_clear} of {result.processed} cleared
                          </RNText>
                        </View>
                      )}
                      <Pressable
                        onPress={() => toggleEvidence(gap.handler)}
                        style={{ flexDirection: "row", alignItems: "center", gap: 6 }}
                      >
                        <FileText size={14} color={C.mutedFg} />
                        <RNText style={{ color: C.mutedFg, fontSize: 12, fontWeight: "600" }}>
                          {expanded === gap.handler ? "Hide" : "View"} real protocol text
                        </RNText>
                        {expanded === gap.handler ? <ChevronUp size={14} color={C.mutedFg} /> : <ChevronDown size={14} color={C.mutedFg} />}
                      </Pressable>
                      {expanded === gap.handler && (
                        <View style={{ gap: 6, paddingLeft: 4, borderLeftWidth: 2, borderLeftColor: C.primary + "33" }}>
                          {evidenceLoading === gap.handler ? (
                            <ActivityIndicator size="small" color={C.primary} />
                          ) : (evidence[gap.handler] || []).length === 0 ? (
                            <RNText style={{ color: C.mutedFg, fontSize: 12, fontStyle: "italic" }}>
                              No cited protocol text found for this pattern yet.
                            </RNText>
                          ) : (
                            (evidence[gap.handler] || []).map((quote, index) => (
                              <View key={index} style={{ paddingLeft: 8 }}>
                                <RNText style={{ color: C.foreground, fontSize: 13, fontStyle: "italic" }}>
                                  "{quote.source_text}"
                                </RNText>
                                <RNText style={{ color: C.mutedFg, fontSize: 11 }}>
                                  {quote.page_number != null ? `Page ${quote.page_number}` : "Page unknown"}
                                </RNText>
                              </View>
                            ))
                          )}
                        </View>
                      )}
                      <Pressable
                        onPress={busyHandler === gap.handler ? undefined : () => revalidate(gap.handler)}
                        style={{
                          flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
                          paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: C.primary, marginTop: 4,
                        }}
                      >
                        {busyHandler === gap.handler
                          ? <ActivityIndicator size="small" color={C.primary} />
                          : <><Wrench size={14} color={C.primary} /><RNText style={{ color: C.primary, fontWeight: "600" }}>Revalidate affected protocols</RNText></>}
                      </Pressable>
                    </View>
                  );
                })}
              </View>
            )}
          </View>
        )}
      </ScrollView>
      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}
