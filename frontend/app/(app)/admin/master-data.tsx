// ADM-05 — Master data: "Others: specify" approvals + global value library.
//
// Live, admin-gated endpoints (backend/admin_routes.py · MASTER DATA):
//   submissions ..... GET  /admin/master-data/submissions?status&fieldType
//   approve ......... POST /admin/master-data/submissions/{id}/approve {value?}
//   reject .......... POST /admin/master-data/submissions/{id}/reject  {reason}
//   values .......... GET  /admin/master-data/values?fieldType
//
// Approving a submission promotes its value into the GLOBAL dropdown library
// (master_data_values); edit-and-approve sends an override value. Rejected
// values stay private to the submitter. Nothing is fabricated — every row is a
// real submission; the status filter runs client-side over the fetched list.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StatusBar, Text as RNText, Modal, Animated,
  RefreshControl, KeyboardAvoidingView, Platform, StyleSheet,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, FileText, Check, Pencil, AlertCircle, Database, ChevronRight,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { Loading, ErrorCard, EmptyCard, Toast, Input, SheetActions, st } from "./users";

type Submission = {
  id: string; fieldType?: string; value?: string; status?: string;
  submittedBy?: string; org?: string; dateSubmitted?: string;
  actionBy?: string; actioned_at?: string; rejectReason?: string;
};
type MasterValue = { id: string; fieldType?: string; value?: string; added_by?: string; added_at?: string };

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const STATUS_FILTERS = [
  { key: "all", label: "All" }, { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" }, { key: "rejected", label: "Rejected" },
];

function statusColors(status?: string): { fg: string; bg: string } {
  switch (status) {
    case "approved": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "rejected": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    default: return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
  }
}
function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}
function cap(s?: string): string { return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—"; }

export default function AdminMasterData() {
  const { open } = useAdminDrawer();
  const [tab, setTab] = useState<"submissions" | "values">("submissions");
  const [subs, setSubs] = useState<Submission[]>([]);
  const [values, setValues] = useState<MasterValue[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("pending");
  const [busy, setBusy] = useState(false);

  const [editItem, setEditItem] = useState<Submission | null>(null);
  const [rejectItem, setRejectItem] = useState<Submission | null>(null);

  // Toast
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
      const [s, v] = await Promise.all([
        api.get("/admin/master-data/submissions"),
        api.get("/admin/master-data/values"),
      ]);
      setSubs(Array.isArray(s.data) ? s.data : []);
      setValues(Array.isArray(v.data) ? v.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load master data. Pull to retry."));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const filtered = useMemo(() => (
    statusFilter === "all" ? subs : subs.filter((s) => (s.status || "pending") === statusFilter)
  ), [subs, statusFilter]);

  const tiles = useMemo(() => [
    { label: "Pending", value: subs.filter((s) => (s.status || "pending") === "pending").length, fg: C.warning },
    { label: "Approved", value: subs.filter((s) => s.status === "approved").length, fg: C.success },
    { label: "Rejected", value: subs.filter((s) => s.status === "rejected").length, fg: C.destructive },
    { label: "Total", value: subs.length, fg: C.primary },
  ], [subs]);

  const valueGroups = useMemo(() => {
    const map = new Map<string, MasterValue[]>();
    for (const v of values) {
      const k = v.fieldType || "other";
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(v);
    }
    return Array.from(map.entries()).map(([key, list]) => ({ key, list }));
  }, [values]);

  const approve = async (item: Submission, value?: string) => {
    setBusy(true);
    try {
      await api.post(`/admin/master-data/submissions/${item.id}/approve`, value ? { value } : {});
      showToast(`"${value || item.value}" approved to the global list`);
      setEditItem(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't approve submission"));
    } finally { setBusy(false); }
  };

  const reject = async (item: Submission, reason: string) => {
    setBusy(true);
    try {
      await api.post(`/admin/master-data/submissions/${item.id}/reject`, { reason });
      showToast(`"${item.value}" rejected`);
      setRejectItem(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't reject submission"));
    } finally { setBusy(false); }
  };

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
          <Loading label="Loading master data…" />
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

            <View style={md.tabs}>
              {(["submissions", "values"] as const).map((t) => (
                <Pressable key={t} onPress={() => setTab(t)} style={[md.tab, tab === t && md.tabActive]}>
                  <RNText style={[md.tabTxt, tab === t && md.tabTxtActive]}>
                    {t === "submissions" ? "Submissions" : "Global values"}
                  </RNText>
                </Pressable>
              ))}
            </View>

            {tab === "submissions" ? (
              <>
                <View style={md.banner}>
                  <AlertCircle size={16} color={C.info} />
                  <RNText style={md.bannerTxt}>
                    Approved values join the global dropdown for all users. Rejected values stay private to the submitter.
                  </RNText>
                </View>

                <FilterChips label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
                <RNText style={st.countLine}>{filtered.length} of {subs.length} submissions</RNText>

                {filtered.length === 0 ? (
                  <EmptyCard message="No submissions match this filter." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {filtered.map((item) => {
                      const sc = statusColors(item.status);
                      const pending = (item.status || "pending") === "pending";
                      return (
                        <View key={item.id} style={st.card}>
                          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                            <View style={md.typeBadge}><RNText style={md.typeBadgeTxt} numberOfLines={1}>{item.fieldType || "—"}</RNText></View>
                            <View style={[st.badge, { backgroundColor: sc.bg }]}>
                              <RNText style={[st.badgeTxt, { color: sc.fg }]}>{cap(item.status || "pending")}</RNText>
                            </View>
                          </View>
                          <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
                            <FileText size={16} color={C.mutedFg} style={{ marginTop: 2 }} />
                            <RNText style={md.value}>{item.value || "—"}</RNText>
                          </View>
                          <RNText style={md.meta}>
                            Submitted by <RNText style={{ color: C.foreground }}>{item.submittedBy || "—"}</RNText>
                            {item.org ? ` · ${item.org}` : ""}
                          </RNText>
                          <RNText style={md.meta}>Date: {fmtDate(item.dateSubmitted)}</RNText>

                          {!pending && (
                            <View style={[md.result, { backgroundColor: item.status === "approved" ? "rgba(92,154,110,0.10)" : "rgba(192,57,43,0.06)" }]}>
                              <RNText style={{ fontFamily: fonts.medium, fontSize: 12, color: sc.fg }}>
                                {cap(item.status)} by {item.actionBy || "Admin"}{item.actioned_at ? ` on ${fmtDate(item.actioned_at)}` : ""}
                              </RNText>
                              {!!item.rejectReason && <RNText style={{ fontFamily: fonts.regular, fontSize: 12, color: C.destructive, marginTop: 2 }}>Reason: {item.rejectReason}</RNText>}
                            </View>
                          )}

                          {pending && (
                            <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                              <Pressable onPress={busy ? undefined : () => approve(item)} style={[md.actBtn, { backgroundColor: C.success, opacity: busy ? 0.5 : 1 }]}>
                                <Check size={14} color={C.white} /><RNText style={md.actBtnSolidTxt}>Approve</RNText>
                              </Pressable>
                              <Pressable onPress={() => setEditItem(item)} style={[md.actBtn, md.actBtnOutline, { borderColor: C.info }]}>
                                <Pencil size={13} color={C.info} /><RNText style={[md.actBtnTxt, { color: C.info }]}>Edit</RNText>
                              </Pressable>
                              <Pressable onPress={() => setRejectItem(item)} style={[md.actBtn, md.actBtnOutline, { borderColor: C.destructive }]}>
                                <X size={14} color={C.destructive} /><RNText style={[md.actBtnTxt, { color: C.destructive }]}>Reject</RNText>
                              </Pressable>
                            </View>
                          )}
                        </View>
                      );
                    })}
                  </View>
                )}
              </>
            ) : (
              <View style={{ marginTop: 14 }}>
                <RNText style={st.countLine}>{values.length} global values across {valueGroups.length} field types</RNText>
                {valueGroups.length === 0 ? (
                  <EmptyCard message="No global values yet. Approve a submission to add one." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {valueGroups.map((g) => (
                      <View key={g.key} style={st.card}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 }}>
                          <Database size={15} color={C.primary} />
                          <RNText style={md.groupName}>{g.key}</RNText>
                          <View style={md.countPill}><RNText style={md.countPillTxt}>{g.list.length}</RNText></View>
                        </View>
                        <View style={{ gap: 6 }}>
                          {g.list.map((v) => (
                            <View key={v.id} style={md.valueRow}>
                              <ChevronRight size={14} color={C.mutedFg} />
                              <RNText style={md.valueRowTxt}>{v.value}</RNText>
                            </View>
                          ))}
                        </View>
                      </View>
                    ))}
                  </View>
                )}
              </View>
            )}
          </View>
        )}
      </ScrollView>

      <EditApproveSheet item={editItem} busy={busy} onClose={() => setEditItem(null)} onConfirm={approve} />
      <RejectSheet item={rejectItem} busy={busy} onClose={() => setRejectItem(null)} onConfirm={reject} />
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
            <RNText style={st.heroTitle} numberOfLines={1}>Master data</RNText>
          </View>
          <Pressable testID="master-data-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Review custom “Others: specify” values submitted across every module and curate the global dropdown library.</RNText>
      </SafeAreaView>
    </LinearGradient>
  );
}

function FilterChips({ label, chips, value, onChange }: { label: string; chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={{ marginTop: 14 }}>
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

function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <View style={st.sheetOverlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={st.sheet}>
            <View style={st.sheetHeader}>
              <RNText style={st.sheetTitle}>{title}</RNText>
              <Pressable onPress={onClose} hitSlop={10} style={st.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
            </View>
            <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 24 }}>
              {children}
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

function EditApproveSheet({ item, busy, onClose, onConfirm }: { item: Submission | null; busy: boolean; onClose: () => void; onConfirm: (i: Submission, value: string) => void }) {
  const [value, setValue] = useState("");
  useEffect(() => { if (item) setValue(item.value || ""); }, [item]);
  return (
    <Sheet open={!!item} onClose={onClose} title="Edit & approve">
      {item && (
        <View style={{ gap: 12 }}>
          <RNText style={md.sheetHint}>Adjust the value before it joins the global {item.fieldType} list.</RNText>
          <View style={{ gap: 6 }}>
            <RNText style={st.fieldLabel}>Value</RNText>
            <Input value={value} onChangeText={setValue} placeholder="Corrected value" multiline />
          </View>
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Approve edit" onConfirm={() => onConfirm(item, value.trim())} disabled={!value.trim()} loading={busy} tone="success" />
        </View>
      )}
    </Sheet>
  );
}

function RejectSheet({ item, busy, onClose, onConfirm }: { item: Submission | null; busy: boolean; onClose: () => void; onConfirm: (i: Submission, reason: string) => void }) {
  const [reason, setReason] = useState("");
  useEffect(() => { if (item) setReason(""); }, [item]);
  return (
    <Sheet open={!!item} onClose={onClose} title="Reject submission">
      {item && (
        <View style={{ gap: 12 }}>
          <RNText style={md.sheetHint}>Rejecting “{item.value}”. The submitter keeps it as a private value.</RNText>
          <View style={{ gap: 6 }}>
            <RNText style={st.fieldLabel}>Reason (permanently logged)</RNText>
            <Input value={reason} onChangeText={setReason} placeholder="Why is this value being rejected?" multiline />
          </View>
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Reject" onConfirm={() => onConfirm(item, reason.trim())} disabled={!reason.trim()} loading={busy} tone="destructive" />
        </View>
      )}
    </Sheet>
  );
}

const md = StyleSheet.create({
  tabs: { flexDirection: "row", gap: 8, marginTop: 16, backgroundColor: C.surface, borderRadius: 12, padding: 4 },
  tab: { flex: 1, paddingVertical: 9, borderRadius: 9, alignItems: "center", justifyContent: "center" },
  tabActive: { backgroundColor: C.card },
  tabTxt: { fontFamily: fonts.medium, fontSize: 13, color: C.mutedFg },
  tabTxtActive: { color: C.primary, fontFamily: fonts.bold },
  banner: { flexDirection: "row", alignItems: "flex-start", gap: 10, backgroundColor: "rgba(123,107,184,0.08)", borderRadius: 12, borderWidth: 1, borderColor: "rgba(123,107,184,0.25)", padding: 12, marginTop: 14 },
  bannerTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground, lineHeight: 17 },
  typeBadge: { paddingHorizontal: 10, height: 22, borderRadius: 11, backgroundColor: C.surface, alignItems: "center", justifyContent: "center", maxWidth: 200 },
  typeBadgeTxt: { fontFamily: fonts.medium, fontSize: 11, color: C.mutedFg },
  value: { flex: 1, fontFamily: fonts.semibold, fontSize: 14, color: C.foreground, lineHeight: 19 },
  meta: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 1 },
  result: { borderRadius: 10, padding: 10, marginTop: 10 },
  actBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, height: 38, borderRadius: 10 },
  actBtnOutline: { backgroundColor: C.card, borderWidth: 1 },
  actBtnSolidTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.white },
  actBtnTxt: { fontFamily: fonts.bold, fontSize: 12 },
  groupName: { flex: 1, fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  countPill: { paddingHorizontal: 9, height: 20, borderRadius: 10, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  countPillTxt: { fontFamily: fonts.bold, fontSize: 11, color: C.primary },
  valueRow: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: C.surface, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8 },
  valueRowTxt: { flex: 1, fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
  sheetHint: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, lineHeight: 18 },
});
