// ADM — Terms & Privacy version management.
//
// Live, admin-gated endpoints (backend/admin_routes.py · TERMS):
//   versions ....... GET   /admin/terms/versions        (?type=ToS|Privacy)
//   publish ........ POST  /admin/terms/versions        {type, version, content, effectiveDate?, changeSummary?, forceReacceptance}
//   edit ........... PATCH /admin/terms/versions/{id}    {content?, changeSummary?}
//   acceptances .... GET   /admin/terms/acceptances
//
// A new version must be strictly greater than the current one of its type; the
// server supersedes the previous active version and (optionally) clears every
// user's acceptance so the app re-prompts on next login.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Modal, Animated, Switch, Platform, KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, AlertTriangle, FileText, ShieldCheck, Plus, Pencil,
  CheckCircle2, History, Calendar,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };
const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

type TermsVersion = {
  id: string; type: "ToS" | "Privacy"; version: string; status?: string; content?: string;
  effectiveDate?: string; changeSummary?: string; forceReacceptance?: boolean;
  createdAt?: string; activatedAt?: string; acceptedBy?: number;
};
type Acceptance = { user_id: string; name?: string; email?: string; role?: string; accepted_at?: string };
const EMPTY_TERMS_FORM = {
  type: "ToS" as "ToS" | "Privacy", version: "", effectiveDate: "",
  changeSummary: "", content: "", forceReacceptance: false,
};

const TYPE_LABEL: Record<string, string> = { ToS: "Terms of Service", Privacy: "Privacy Policy" };

function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString();
}
function vTuple(v: string): number[] {
  return String(v || "0").trim().split(".").map((x) => parseInt(x, 10) || 0);
}
function vGreater(a: string, b: string): boolean {
  const A = vTuple(a), B = vTuple(b);
  for (let i = 0; i < Math.max(A.length, B.length); i++) {
    const x = A[i] || 0, y = B[i] || 0;
    if (x !== y) return x > y;
  }
  return false;
}

export default function AdminTerms() {
  const { open } = useAdminDrawer();
  const [tab, setTab] = useState<"versions" | "acceptances">("versions");
  const [typeFilter, setTypeFilter] = useState<"all" | "ToS" | "Privacy">("all");
  const [versions, setVersions] = useState<TermsVersion[]>([]);
  const [acceptances, setAcceptances] = useState<Acceptance[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<TermsVersion | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [editing, setEditing] = useState<TermsVersion | null>(null);

  const { toast, toastAnim, showToast } = useToast();

  const load = useCallback(async () => {
    setError(null);
    try {
      const [v, a] = await Promise.all([
        api.get("/admin/terms/versions"),
        api.get("/admin/terms/acceptances"),
      ]);
      setVersions(Array.isArray(v.data) ? v.data : []);
      setAcceptances(Array.isArray(a.data) ? a.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load terms. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const activeByType = useMemo(() => {
    const m: Record<string, TermsVersion | undefined> = {};
    for (const v of versions) if (v.status === "active") m[v.type] = v;
    return m;
  }, [versions]);

  const filtered = useMemo(
    () => versions.filter((v) => typeFilter === "all" || v.type === typeFilter),
    [versions, typeFilter],
  );

  const tiles = useMemo(() => [
    { label: "Versions", value: versions.length },
    { label: "Active ToS", value: activeByType.ToS?.version || "—" },
    { label: "Active Privacy", value: activeByType.Privacy?.version || "—" },
    { label: "Acceptances", value: acceptances.length },
  ], [versions, activeByType, acceptances]);

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <Hero onMenu={open} onRefresh={onRefresh} onPublish={() => setPublishOpen(true)} />

        {loading ? (
          <Loading label="Loading terms…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              {tiles.map((t) => (
                <View key={t.label} style={st.tile}>
                  <RNText style={st.tileValue}>{t.value}</RNText>
                  <RNText style={st.tileLabel}>{t.label}</RNText>
                </View>
              ))}
            </View>

            <View style={st.segment}>
              {(["versions", "acceptances"] as const).map((k) => (
                <Pressable key={k} onPress={() => setTab(k)} style={[st.segmentBtn, tab === k && st.segmentBtnActive]}>
                  <RNText style={[st.segmentTxt, tab === k && st.segmentTxtActive]}>
                    {k === "versions" ? "Versions" : "Acceptance history"}
                  </RNText>
                </Pressable>
              ))}
            </View>

            {tab === "versions" ? (
              <>
                <ChipRow
                  chips={[{ key: "all", label: "All" }, { key: "ToS", label: "Terms" }, { key: "Privacy", label: "Privacy" }]}
                  value={typeFilter} onChange={(v) => setTypeFilter(v as any)}
                />
                <RNText style={st.countLine}>{filtered.length} version{filtered.length === 1 ? "" : "s"}</RNText>
                {filtered.length === 0 ? (
                  <EmptyCard message="No terms versions yet. Publish the first one." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {filtered.map((v) => (
                      <Pressable key={v.id} testID={`terms-${v.id}`} onPress={() => setSelected(v)} style={st.row}>
                        <View style={st.rowIcon}><FileText size={18} color={C.primary} /></View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                            <RNText style={st.rowName}>{TYPE_LABEL[v.type] || v.type} v{v.version}</RNText>
                          </View>
                          <RNText style={st.rowSub} numberOfLines={1}>
                            Effective {fmtDate(v.effectiveDate)} · {v.acceptedBy ?? 0} accepted
                          </RNText>
                          {!!v.changeSummary && <RNText style={st.rowSub} numberOfLines={1}>{v.changeSummary}</RNText>}
                        </View>
                        <StatusBadge status={v.status} />
                      </Pressable>
                    ))}
                  </View>
                )}
              </>
            ) : (
              <>
                <RNText style={st.countLine}>{acceptances.length} acceptance{acceptances.length === 1 ? "" : "s"}</RNText>
                {acceptances.length === 0 ? (
                  <EmptyCard message="No acceptances recorded yet." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {acceptances.map((a) => (
                      <View key={a.user_id} style={st.row}>
                        <View style={st.rowIcon}><ShieldCheck size={18} color={C.success} /></View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <RNText style={st.rowName} numberOfLines={1}>{a.name || "—"}</RNText>
                          <RNText style={st.rowSub} numberOfLines={1}>{a.email || "—"} · {a.role || "—"}</RNText>
                        </View>
                        <RNText style={st.rowMeta}>{fmtDate(a.accepted_at)}</RNText>
                      </View>
                    ))}
                  </View>
                )}
              </>
            )}
          </View>
        )}
      </ScrollView>

      <VersionSheet
        version={selected}
        onClose={() => setSelected(null)}
        onEdit={(v) => { setSelected(null); setEditing(v); }}
      />

      <PublishSheet
        open={publishOpen}
        activeByType={activeByType}
        onClose={() => setPublishOpen(false)}
        onDone={(msg) => { setPublishOpen(false); showToast(msg); load(); }}
      />

      <EditSheet
        version={editing}
        onClose={() => setEditing(null)}
        onDone={(msg) => { setEditing(null); showToast(msg); load(); }}
      />

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function StatusBadge({ status }: { status?: string }) {
  const active = status === "active";
  const fg = active ? C.success : C.mutedFg;
  const bg = active ? "rgba(92,154,110,0.15)" : C.surface;
  return (
    <View style={[st.badge, { backgroundColor: bg }]}>
      {active && <CheckCircle2 size={11} color={fg} />}
      <RNText style={[st.badgeTxt, { color: fg }]}>{active ? "Active" : (status || "—")}</RNText>
    </View>
  );
}

function Hero({ onMenu, onRefresh, onPublish }: { onMenu: () => void; onRefresh: () => void; onPublish: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Terms & Privacy</RNText>
          </View>
          <Pressable testID="terms-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Publish and track the legal documents users must accept.</RNText>
        <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
          <Pressable testID="terms-publish" onPress={onPublish} style={st.heroBtnSolid}>
            <Plus size={15} color={C.primary} /><RNText style={st.heroBtnSolidTxt}>Publish new version</RNText>
          </Pressable>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

function VersionSheet({ version, onClose, onEdit }: { version: TermsVersion | null; onClose: () => void; onEdit: (v: TermsVersion) => void }) {
  return (
    <Sheet open={!!version} onClose={onClose} title="Version details">
      {version && (
        <View style={{ gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={[st.rowIcon, { width: 48, height: 48, borderRadius: 16 }]}><FileText size={22} color={C.primary} /></View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.sheetName}>{TYPE_LABEL[version.type] || version.type} v{version.version}</RNText>
              <StatusBadge status={version.status} />
            </View>
          </View>
          <InfoRow icon={Calendar} label="Effective date" value={fmtDate(version.effectiveDate)} />
          <InfoRow icon={History} label="Created" value={fmtDate(version.createdAt)} />
          <InfoRow icon={ShieldCheck} label="Accepted by" value={`${version.acceptedBy ?? 0} users`} />
          {version.forceReacceptance && (
            <View style={st.warnBanner}>
              <AlertTriangle size={15} color={C.warning} />
              <RNText style={st.warnBannerTxt}>Re-acceptance was forced for this version.</RNText>
            </View>
          )}
          {!!version.changeSummary && (
            <View style={st.block}>
              <RNText style={st.blockLabel}>Change summary</RNText>
              <RNText style={st.blockBody}>{version.changeSummary}</RNText>
            </View>
          )}
          <View style={st.block}>
            <RNText style={st.blockLabel}>Content</RNText>
            <RNText style={st.blockBody}>{version.content || "—"}</RNText>
          </View>
          <Pressable onPress={() => onEdit(version)} style={[st.actionBtn, { borderColor: C.primary + "44" }]}>
            <Pencil size={16} color={C.primary} /><RNText style={[st.actionBtnTxt, { color: C.primary }]}>Edit content</RNText>
          </Pressable>
        </View>
      )}
    </Sheet>
  );
}

function PublishSheet({ open, activeByType, onClose, onDone }: {
  open: boolean; activeByType: Record<string, TermsVersion | undefined>;
  onClose: () => void; onDone: (msg: string) => void;
}) {
  const [form, setForm] = useState(EMPTY_TERMS_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setForm(EMPTY_TERMS_FORM); setErr(null); } }, [open]);

  const current = activeByType[form.type]?.version;
  const versionOk = /^\d+(\.\d+)*$/.test(form.version.trim()) && (!current || vGreater(form.version.trim(), current));
  const valid = versionOk && form.content.trim().length > 0;

  const submit = async () => {
    if (!valid) { setErr(current && !versionOk ? `Version must be greater than the current ${current}` : "A numeric version and content are required"); return; }
    setSaving(true); setErr(null);
    try {
      const res = await api.post("/admin/terms/versions", {
        type: form.type, version: form.version.trim(), content: form.content,
        effectiveDate: form.effectiveDate.trim() || undefined,
        changeSummary: form.changeSummary.trim(), forceReacceptance: form.forceReacceptance,
      });
      const n = res.data?.reacceptance_required ?? 0;
      onDone(`Published ${form.type} v${form.version.trim()}${form.forceReacceptance ? ` · ${n} must re-accept` : ""}`);
    } catch (e) {
      setErr(errMsg(e, "Couldn't publish version"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Publish new version">
      <View style={{ gap: 12 }}>
        <FormField label="Document type">
          <View style={st.chipWrap}>
            {(["ToS", "Privacy"] as const).map((t) => (
              <Pressable key={t} onPress={() => setForm({ ...form, type: t })} style={[st.chip, form.type === t && st.chipActive]}>
                <RNText style={[st.chipTxt, form.type === t && st.chipTxtActive]}>{TYPE_LABEL[t]}</RNText>
              </Pressable>
            ))}
          </View>
        </FormField>
        <FormField label={`Version${current ? ` (current ${current})` : ""}`}>
          <Input value={form.version} onChangeText={(v) => setForm({ ...form, version: v })} placeholder="e.g. 2.1" keyboardType="decimal-pad" />
        </FormField>
        <FormField label="Effective date (optional)">
          <Input value={form.effectiveDate} onChangeText={(v) => setForm({ ...form, effectiveDate: v })} placeholder="YYYY-MM-DD" autoCapitalize="none" />
        </FormField>
        <FormField label="Change summary">
          <Input value={form.changeSummary} onChangeText={(v) => setForm({ ...form, changeSummary: v })} placeholder="What changed in this version" multiline />
        </FormField>
        <FormField label="Content">
          <Input value={form.content} onChangeText={(v) => setForm({ ...form, content: v })} placeholder="Full document text…" multiline style={{ height: 140 }} />
        </FormField>
        <Pressable onPress={() => setForm({ ...form, forceReacceptance: !form.forceReacceptance })} style={st.switchRow}>
          <RNText style={st.switchLabel}>Force re-acceptance for all users</RNText>
          <Switch value={form.forceReacceptance} onValueChange={(v) => setForm({ ...form, forceReacceptance: v })} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
        </Pressable>
        {err && <RNText style={st.errText}>{err}</RNText>}
        <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Publish" onConfirm={submit} disabled={!valid} loading={saving} />
      </View>
    </Sheet>
  );
}

function EditSheet({ version, onClose, onDone }: { version: TermsVersion | null; onClose: () => void; onDone: (msg: string) => void }) {
  const [content, setContent] = useState("");
  const [summary, setSummary] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (version) { setContent(version.content || ""); setSummary(version.changeSummary || ""); setErr(null); } }, [version]);

  const submit = async () => {
    if (!version) return;
    if (content.trim().length === 0) { setErr("Content cannot be empty"); return; }
    setSaving(true); setErr(null);
    try {
      await api.patch(`/admin/terms/versions/${version.id}`, { content, changeSummary: summary });
      onDone(`Updated ${version.type} v${version.version}`);
    } catch (e) {
      setErr(errMsg(e, "Couldn't update version"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={!!version} onClose={onClose} title="Edit version">
      {version && (
        <View style={{ gap: 12 }}>
          <RNText style={st.rowSub}>{TYPE_LABEL[version.type] || version.type} v{version.version}</RNText>
          <FormField label="Change summary">
            <Input value={summary} onChangeText={setSummary} placeholder="What changed" multiline />
          </FormField>
          <FormField label="Content">
            <Input value={content} onChangeText={setContent} placeholder="Full document text…" multiline style={{ height: 160 }} />
          </FormField>
          {err && <RNText style={st.errText}>{err}</RNText>}
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Save" onConfirm={submit} disabled={content.trim().length === 0} loading={saving} />
        </View>
      )}
    </Sheet>
  );
}

// ── Shared primitives (kept in-file: this screen owns only its own module) ────
function useToast() {
  const [toast, setToast] = useState("");
  const toastAnim = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    Animated.timing(toastAnim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      Animated.timing(toastAnim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2600);
  }, [toastAnim]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { toast, toastAnim, showToast };
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
function Input(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput placeholderTextColor="rgba(123,95,115,0.5)" {...props} style={[st.input, props.multiline && { height: 80, textAlignVertical: "top" }, props.style]} />;
}
function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return <View style={{ gap: 6 }}><RNText style={st.fieldLabel}>{label}</RNText>{children}</View>;
}
function InfoRow({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <View style={st.infoRow}>
      <Icon size={18} color={C.mutedFg} />
      <View style={{ flex: 1, minWidth: 0 }}>
        <RNText style={st.infoLabel}>{label}</RNText>
        <RNText style={st.infoValue}>{value}</RNText>
      </View>
    </View>
  );
}
function ChipRow({ chips, value, onChange }: { chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 8 }} style={{ marginTop: 4 }}>
      {chips.map((c) => (
        <Pressable key={c.key} onPress={() => onChange(c.key)} style={[st.chip, value === c.key && st.chipActive]}>
          <RNText style={[st.chipTxt, value === c.key && st.chipTxtActive]}>{c.label}</RNText>
        </Pressable>
      ))}
    </ScrollView>
  );
}
function SheetActions({ cancelLabel, onCancel, confirmLabel, onConfirm, disabled, loading }: { cancelLabel: string; onCancel: () => void; confirmLabel: string; onConfirm: () => void; disabled?: boolean; loading?: boolean }) {
  return (
    <View style={{ flexDirection: "row", gap: 10, marginTop: 6 }}>
      <Pressable onPress={onCancel} style={[st.cancelBtn, { flex: 1 }]}><RNText style={st.cancelBtnTxt}>{cancelLabel}</RNText></Pressable>
      <Pressable onPress={disabled || loading ? undefined : onConfirm} style={[st.confirmBtn, { flex: 1, backgroundColor: C.primary, opacity: disabled || loading ? 0.5 : 1 }]}>
        {loading ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={st.confirmBtnTxt}>{confirmLabel}</RNText>}
      </Pressable>
    </View>
  );
}
function Loading({ label }: { label: string }) {
  return <View style={{ paddingTop: 60, alignItems: "center" }}><ActivityIndicator color={C.primary} /><RNText style={st.loadingTxt}>{label}</RNText></View>;
}
function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={{ padding: 16, marginTop: 12 }}>
      <View style={[st.card, { borderColor: "rgba(192,57,43,0.30)" }]}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View style={st.errIcon}><AlertTriangle size={20} color={C.destructive} /></View>
          <RNText style={{ flex: 1, color: C.foreground, fontFamily: fonts.semibold, fontSize: 13 }}>{message}</RNText>
        </View>
        <Pressable onPress={onRetry} style={st.retryBtn}><RefreshCcw size={15} color={C.primary} /><RNText style={{ color: C.primary, fontFamily: fonts.bold, fontSize: 13 }}>Retry</RNText></Pressable>
      </View>
    </View>
  );
}
function EmptyCard({ message }: { message: string }) {
  return <View style={[st.card, { marginTop: 8 }]}><RNText style={st.emptyText}>{message}</RNText></View>;
}
function Toast({ text, anim }: { text: string; anim: Animated.Value }) {
  if (text === "") return null;
  return (
    <Animated.View pointerEvents="none" style={[st.toast, { opacity: anim, transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }] }]}>
      <RNText style={st.toastTxt}>{text}</RNText>
    </Animated.View>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 40, borderBottomLeftRadius: 24, borderBottomRightRadius: 24 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrow: { color: W.w55, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 24, marginTop: 2 },
  heroSub: { color: W.w70, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  heroBtnSolid: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 12, borderRadius: 999, backgroundColor: C.card },
  heroBtnSolidTxt: { color: C.primary, fontFamily: fonts.bold, fontSize: 13 },

  tileGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tile: { width: "47%", flexGrow: 1, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingVertical: 12, paddingHorizontal: 12 },
  tileValue: { fontFamily: fonts.display, fontSize: 22, color: C.primary, fontVariant: ["tabular-nums"] },
  tileLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2 },

  segment: { flexDirection: "row", backgroundColor: C.surface, borderRadius: 12, padding: 4, marginTop: 16, gap: 4 },
  segmentBtn: { flex: 1, paddingVertical: 9, borderRadius: 9, alignItems: "center" },
  segmentBtnActive: { backgroundColor: C.card },
  segmentTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  segmentTxtActive: { color: C.primary, fontFamily: fonts.bold },

  countLine: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 14, marginBottom: 10 },

  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  rowIcon: { width: 42, height: 42, borderRadius: 14, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 1 },
  rowMeta: { fontFamily: fonts.medium, fontSize: 11, color: C.mutedFg },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 11, justifyContent: "center", alignSelf: "flex-start", marginTop: 4 },
  badgeTxt: { fontFamily: fonts.bold, fontSize: 11, textTransform: "capitalize" },

  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingHorizontal: 16, paddingVertical: 16 },
  emptyText: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, paddingVertical: 20, textAlign: "center" },
  loadingTxt: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  errIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },

  sheetOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 18, paddingTop: 16, maxHeight: "88%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 14 },
  sheetTitle: { fontFamily: fonts.display, fontSize: 20, color: C.foreground },
  sheetClose: { width: 34, height: 34, borderRadius: 17, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  sheetName: { fontFamily: fonts.heading, fontSize: 17, color: C.foreground },

  infoRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 12 },
  infoLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  infoValue: { fontFamily: fonts.medium, fontSize: 14, color: C.foreground, marginTop: 1 },
  block: { backgroundColor: C.surface, borderRadius: 12, padding: 12, gap: 4 },
  blockLabel: { fontFamily: fonts.semibold, fontSize: 11, color: C.mutedFg, textTransform: "uppercase", letterSpacing: 0.6 },
  blockBody: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, lineHeight: 19 },
  warnBanner: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "rgba(216,154,60,0.10)", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: "rgba(216,154,60,0.25)" },
  warnBannerTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: C.surface, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10 },
  switchLabel: { flex: 1, fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingHorizontal: 14, height: 34, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  chipActive: { backgroundColor: C.primary, borderColor: C.primary },
  chipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  chipTxtActive: { color: C.primaryFg },

  actionBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, borderWidth: 1, backgroundColor: C.card },
  actionBtnTxt: { fontFamily: fonts.bold, fontSize: 14 },

  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  confirmBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
