// ADM-04 — User invitations.
//
// Live, admin-gated endpoints (backend/admin_routes.py · INVITATIONS):
//   list ...... GET  /admin/invitations
//   create .... POST /admin/invitations {email?, phone?, full_name?, designation?, role, entityType?, organization?, site?}
//   resend .... POST /admin/invitations/{id}/resend  → {invite_link, expires_at}
//   cancel .... POST /admin/invitations/{id}/cancel
//
// Effective status is computed server-side (a pending invite past expiry reads
// as "expired"). Every row is a real record. Search/status/entity filters run
// client-side over the fetched list.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Modal, Animated, Platform, KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, Search, UserPlus, X, AlertTriangle, Mail, Phone, Building2, User,
  Clock, CheckCircle2, XCircle, RefreshCw, Calendar, ChevronRight, Link2,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { sanitizeDesignation, sanitizeDigits, sanitizeName, sanitizeOrgName } from "@/src/lib/validators";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };

type Invitation = {
  id: string; token?: string; email?: string; phone?: string; full_name?: string;
  designation?: string; role?: string; entityType?: string; org?: string; site?: string;
  status?: string; created_at?: string; expires_at?: string; accepted_at?: string;
  last_sent_at?: string; cancelled_at?: string; resend_count?: number; trial_id?: string | null;
};
const EMPTY_INVITE_FORM = {
  full_name: "", email: "", phone: "", designation: "", role: "site",
  entityType: "site", organization: "", site: "",
};

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" }, { key: "pending", label: "Pending" },
  { key: "accepted", label: "Accepted" }, { key: "expired", label: "Expired" },
  { key: "cancelled", label: "Cancelled" },
];
const CREATE_ROLES = ["sponsor", "cro", "smo", "site", "pi", "crc", "patient"];
const ENTITY_TYPES = ["sponsor", "cro", "smo", "site"];

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function statusMeta(status?: string): { fg: string; bg: string; icon: any } {
  switch (status) {
    case "accepted": return { fg: C.success, bg: "rgba(92,154,110,0.15)", icon: CheckCircle2 };
    case "pending": return { fg: C.info, bg: "rgba(123,107,184,0.14)", icon: Clock };
    case "expired": return { fg: C.warning, bg: "rgba(216,154,60,0.15)", icon: Clock };
    case "cancelled": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)", icon: XCircle };
    default: return { fg: C.mutedFg, bg: C.surface, icon: Clock };
  }
}
function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}
function initialsOf(inv: Invitation): string {
  const base = inv.full_name || inv.email || inv.phone || "?";
  return base.split(/[\s@]/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";
}

export default function AdminInvitations() {
  const { open } = useAdminDrawer();
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [selected, setSelected] = useState<Invitation | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // ── Toast ──
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
      const res = await api.get("/admin/invitations");
      setInvites(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load invitations. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return invites.filter((i) => {
      const matchesSearch = !q ||
        (i.full_name || "").toLowerCase().includes(q) ||
        (i.email || "").toLowerCase().includes(q) ||
        (i.phone || "").toLowerCase().includes(q) ||
        (i.org || "").toLowerCase().includes(q);
      const matchesStatus = statusFilter === "all" || (i.status || "pending") === statusFilter;
      return matchesSearch && matchesStatus;
    });
  }, [invites, search, statusFilter]);

  const tiles = useMemo(() => [
    { label: "Total", value: invites.length },
    { label: "Pending", value: invites.filter((i) => i.status === "pending").length },
    { label: "Accepted", value: invites.filter((i) => i.status === "accepted").length },
    { label: "Expired", value: invites.filter((i) => i.status === "expired").length },
    { label: "Cancelled", value: invites.filter((i) => i.status === "cancelled").length },
  ], [invites]);

  // ── Actions ──
  const resend = async (inv: Invitation) => {
    setBusy(true);
    try {
      await api.post(`/admin/invitations/${inv.id}/resend`);
      showToast(`Invitation resent to ${inv.email || inv.phone || "invitee"}`);
      setSelected(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't resend invitation"));
    } finally { setBusy(false); }
  };
  const cancel = async (inv: Invitation) => {
    setBusy(true);
    try {
      await api.post(`/admin/invitations/${inv.id}/cancel`);
      showToast(`Invitation to ${inv.email || inv.phone || "invitee"} cancelled`);
      setSelected(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't cancel invitation"));
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
        <Hero onMenu={open} onRefresh={onRefresh} onAdd={() => setAddOpen(true)} />

        {loading ? (
          <Loading label="Loading invitations…" />
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

            <SearchBar value={search} onChange={setSearch} placeholder="Search name, email, phone or organization…" />
            <ChipRow label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />

            <RNText style={st.countLine}>{filtered.length} of {invites.length} invitations</RNText>

            {filtered.length === 0 ? (
              <EmptyCard message="No invitations match the current filters." />
            ) : (
              <View style={{ gap: 10 }}>
                {filtered.map((inv) => {
                  const sm = statusMeta(inv.status); const Icon = sm.icon;
                  return (
                    <Pressable key={inv.id} testID={`invite-${inv.id}`} onPress={() => setSelected(inv)} style={st.row}>
                      <View style={st.avatar}><RNText style={st.avatarTxt}>{initialsOf(inv)}</RNText></View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <RNText style={st.rowName} numberOfLines={1}>{inv.full_name || inv.email || inv.phone || "—"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{inv.email || inv.phone || "—"}</RNText>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 }}>
                          <View style={st.rolePill}><RNText style={st.rolePillTxt}>{inv.role || "—"}</RNText></View>
                          {!!inv.org && <RNText style={st.rowOrg} numberOfLines={1}>{inv.org}</RNText>}
                        </View>
                      </View>
                      <View style={{ alignItems: "flex-end", gap: 6 }}>
                        <View style={[st.badge, { backgroundColor: sm.bg }]}>
                          <Icon size={10} color={sm.fg} />
                          <RNText style={[st.badgeTxt, { color: sm.fg }]}>{inv.status || "pending"}</RNText>
                        </View>
                        <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            )}
          </View>
        )}
      </ScrollView>

      <InviteDetailSheet
        invite={selected} busy={busy}
        onClose={() => setSelected(null)}
        onResend={resend} onCancel={cancel}
      />
      <AddInviteSheet
        open={addOpen} onClose={() => setAddOpen(false)}
        onCreated={(msg) => { setAddOpen(false); showToast(msg); load(); }}
      />
      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

// ── Hero ─────────────────────────────────────────────────────────────────────
function Hero({ onMenu, onRefresh, onAdd }: { onMenu: () => void; onRefresh: () => void; onAdd: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Invitations</RNText>
          </View>
          <Pressable testID="invites-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Track sent, accepted, expired and cancelled invites across every organization.</RNText>
        <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
          <Pressable testID="invites-add" onPress={onAdd} style={st.heroBtnSolid}>
            <UserPlus size={15} color={C.primary} /><RNText style={st.heroBtnSolidTxt}>New invitation</RNText>
          </Pressable>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Detail sheet ─────────────────────────────────────────────────────────────
function InviteDetailSheet({ invite, busy, onClose, onResend, onCancel }: {
  invite: Invitation | null; busy: boolean; onClose: () => void;
  onResend: (i: Invitation) => void; onCancel: (i: Invitation) => void;
}) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  useEffect(() => { setConfirmCancel(false); }, [invite]);
  const canResend = invite && (invite.status === "pending" || invite.status === "expired");
  const canCancel = invite && invite.status !== "accepted" && invite.status !== "cancelled";
  return (
    <Sheet open={!!invite} onClose={onClose} title="Invitation details">
      {invite && (() => {
        const sm = statusMeta(invite.status);
        return (
          <View style={{ gap: 14 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
              <View style={[st.avatar, { width: 56, height: 56, borderRadius: 18 }]}>
                <RNText style={[st.avatarTxt, { fontSize: 18 }]}>{initialsOf(invite)}</RNText>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <RNText style={st.sheetName} numberOfLines={1}>{invite.full_name || invite.email || invite.phone || "—"}</RNText>
                {!!invite.designation && <RNText style={st.rowSub} numberOfLines={1}>{invite.designation}</RNText>}
                <View style={[st.badge, { backgroundColor: sm.bg, alignSelf: "flex-start", marginTop: 6 }]}>
                  <RNText style={[st.badgeTxt, { color: sm.fg }]}>{invite.status || "pending"}</RNText>
                </View>
              </View>
            </View>

            <InfoRow icon={Mail} label="Email" value={invite.email || "—"} />
            <InfoRow icon={Phone} label="Phone" value={invite.phone || "—"} />
            <InfoRow icon={User} label="Role" value={invite.role || "—"} />
            <InfoRow icon={Building2} label="Organization" value={invite.org || "—"} />
            {!!invite.site && <InfoRow icon={Building2} label="Site" value={invite.site} />}
            <InfoRow icon={Calendar} label="Invited" value={fmtDate(invite.created_at)} />
            {invite.status === "accepted" ? (
              <InfoRow icon={CheckCircle2} label="Accepted" value={fmtDate(invite.accepted_at)} />
            ) : (
              <InfoRow icon={Clock} label="Expires" value={fmtDate(invite.expires_at)} />
            )}
            {(invite.resend_count || 0) > 0 && (
              <InfoRow icon={RefreshCw} label="Resends" value={String(invite.resend_count)} />
            )}

            <View style={{ gap: 10, marginTop: 4 }}>
              {canResend && (
                <ActionBtn label="Resend invitation" icon={RefreshCw} tone="primary" onPress={() => onResend(invite)} disabled={busy} />
              )}
              {canCancel && (
                confirmCancel ? (
                  <View style={{ gap: 8 }}>
                    <View style={st.infoBanner}>
                      <AlertTriangle size={15} color={C.destructive} />
                      <RNText style={st.infoBannerTxt}>Cancel this invitation? The link will stop working.</RNText>
                    </View>
                    <View style={{ flexDirection: "row", gap: 10 }}>
                      <Pressable onPress={() => setConfirmCancel(false)} style={[st.cancelBtn, { flex: 1 }]}><RNText style={st.cancelBtnTxt}>Keep</RNText></Pressable>
                      <Pressable onPress={busy ? undefined : () => onCancel(invite)} style={[st.confirmBtn, { flex: 1, backgroundColor: C.destructive, opacity: busy ? 0.5 : 1 }]}>
                        {busy ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={st.confirmBtnTxt}>Cancel invite</RNText>}
                      </Pressable>
                    </View>
                  </View>
                ) : (
                  <ActionBtn label="Cancel invitation" icon={XCircle} tone="destructive" onPress={() => setConfirmCancel(true)} disabled={busy} />
                )
              )}
              {!canResend && !canCancel && (
                <RNText style={st.emptyText}>No actions available for {invite.status || "this"} invitations.</RNText>
              )}
            </View>
          </View>
        );
      })()}
    </Sheet>
  );
}

// ── Create sheet ─────────────────────────────────────────────────────────────
function AddInviteSheet({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (msg: string) => void }) {
  const [form, setForm] = useState(EMPTY_INVITE_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  useEffect(() => { if (open) { setForm(EMPTY_INVITE_FORM); setErr(null); setInviteLink(null); } }, [open]);

  const emailOk = !form.email || /\S+@\S+\.\S+/.test(form.email);
  const valid = (form.email.trim().length > 0 || form.phone.trim().length > 0) && emailOk;

  const submit = async () => {
    if (!valid) { setErr("A valid email or phone is required"); return; }
    setSaving(true); setErr(null);
    try {
      const res = await api.post("/admin/invitations", {
        email: form.email.trim() || undefined, phone: form.phone.trim() || undefined,
        full_name: form.full_name.trim(), designation: form.designation.trim(),
        role: form.role, entityType: form.entityType,
        organization: form.organization.trim(), site: form.site.trim(),
      });
      setInviteLink(res.data?.invite_link || null);
      onCreated(`Invitation created for ${form.email.trim() || form.phone.trim()}`);
    } catch (e) {
      setErr(errMsg(e, "Couldn't create invitation"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="New invitation">
      {inviteLink ? (
        <View style={{ gap: 12 }}>
          <View style={st.infoBanner}>
            <CheckCircle2 size={15} color={C.success} />
            <RNText style={st.infoBannerTxt}>Invitation created. Share the link below with the invitee.</RNText>
          </View>
          <View style={st.linkBox}>
            <Link2 size={16} color={C.mutedFg} />
            <RNText style={st.linkTxt} numberOfLines={2}>{inviteLink}</RNText>
          </View>
          <Pressable onPress={onClose} style={[st.confirmBtn, { backgroundColor: C.primary }]}><RNText style={st.confirmBtnTxt}>Done</RNText></Pressable>
        </View>
      ) : (
        <View style={{ gap: 12 }}>
          <FormField label="Full name"><Input value={form.full_name} onChangeText={(v) => setForm({ ...form, full_name: sanitizeName(v) })} placeholder="Dr. Jane Doe" /></FormField>
          <FormField label="Email"><Input value={form.email} onChangeText={(v) => setForm({ ...form, email: v })} placeholder="jane@org.com" keyboardType="email-address" autoCapitalize="none" /></FormField>
          <FormField label="Phone"><Input value={form.phone} onChangeText={(v) => setForm({ ...form, phone: sanitizeDigits(v, 10) })} placeholder="+91-XXXXXXXXXX" keyboardType="phone-pad" /></FormField>
          <FormField label="Designation"><Input value={form.designation} onChangeText={(v) => setForm({ ...form, designation: sanitizeDesignation(v) })} placeholder="Principal Investigator" /></FormField>
          <FormField label="Role">
            <View style={st.chipWrap}>
              {CREATE_ROLES.map((r) => (
                <Pressable key={r} onPress={() => setForm({ ...form, role: r })} style={[st.chip, form.role === r && st.chipActive]}>
                  <RNText style={[st.chipTxt, form.role === r && st.chipTxtActive]}>{r}</RNText>
                </Pressable>
              ))}
            </View>
          </FormField>
          <FormField label="Entity type">
            <View style={st.chipWrap}>
              {ENTITY_TYPES.map((t) => (
                <Pressable key={t} onPress={() => setForm({ ...form, entityType: t })} style={[st.chip, form.entityType === t && st.chipActive]}>
                  <RNText style={[st.chipTxt, form.entityType === t && st.chipTxtActive]}>{t}</RNText>
                </Pressable>
              ))}
            </View>
          </FormField>
          <FormField label="Organization"><Input value={form.organization} onChangeText={(v) => setForm({ ...form, organization: sanitizeOrgName(v) })} placeholder="Organization name" /></FormField>
          <FormField label="Site"><Input value={form.site} onChangeText={(v) => setForm({ ...form, site: sanitizeOrgName(v) })} placeholder="Site name (optional)" /></FormField>
          {err && <RNText style={st.errText}>{err}</RNText>}
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Send invitation" onConfirm={submit} disabled={!valid} loading={saving} />
        </View>
      )}
    </Sheet>
  );
}

// ── Shared primitives (in-file: this screen owns only its own module) ─────────
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
function ActionBtn({ label, icon: Icon, tone, onPress, disabled }: { label: string; icon: any; tone: "primary" | "destructive" | "success" | "warning" | "neutral"; onPress: () => void; disabled?: boolean }) {
  const map: Record<string, string> = { primary: C.primary, destructive: C.destructive, success: C.success, warning: C.warning, neutral: C.mutedFg };
  const fg = map[tone];
  return (
    <Pressable onPress={disabled ? undefined : onPress} style={[st.actionBtn, { borderColor: fg + "44" }, disabled && { opacity: 0.5 }]}>
      <Icon size={16} color={fg} /><RNText style={[st.actionBtnTxt, { color: fg }]}>{label}</RNText>
    </Pressable>
  );
}
function SheetActions({ cancelLabel, onCancel, confirmLabel, onConfirm, disabled, loading, tone = "primary" }: { cancelLabel: string; onCancel: () => void; confirmLabel: string; onConfirm: () => void; disabled?: boolean; loading?: boolean; tone?: "primary" | "destructive" | "success" }) {
  const bg = tone === "destructive" ? C.destructive : tone === "success" ? C.success : C.primary;
  return (
    <View style={{ flexDirection: "row", gap: 10, marginTop: 6 }}>
      <Pressable onPress={onCancel} style={[st.cancelBtn, { flex: 1 }]}><RNText style={st.cancelBtnTxt}>{cancelLabel}</RNText></Pressable>
      <Pressable onPress={disabled || loading ? undefined : onConfirm} style={[st.confirmBtn, { flex: 1, backgroundColor: bg, opacity: disabled || loading ? 0.5 : 1 }]}>
        {loading ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={st.confirmBtnTxt}>{confirmLabel}</RNText>}
      </Pressable>
    </View>
  );
}
function SearchBar({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <View style={st.searchBar}>
      <Search size={17} color={C.mutedFg} />
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor="rgba(123,95,115,0.5)" style={st.searchInput} />
      {!!value && <Pressable onPress={() => onChange("")} hitSlop={8}><X size={16} color={C.mutedFg} /></Pressable>}
    </View>
  );
}
function ChipRow({ label, chips, value, onChange }: { label: string; chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <View style={{ marginTop: 10 }}>
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
  tile: { width: "31.5%", flexGrow: 1, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingVertical: 12, paddingHorizontal: 12 },
  tileValue: { fontFamily: fonts.display, fontSize: 22, color: C.primary, fontVariant: ["tabular-nums"] },
  tileLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2 },

  searchBar: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, height: 46, marginTop: 16 },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 14, color: C.foreground, padding: 0 },
  chipRowLabel: { color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1.2, marginBottom: 8 },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingHorizontal: 14, height: 34, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  chipActive: { backgroundColor: C.primary, borderColor: C.primary },
  chipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg, textTransform: "capitalize" },
  chipTxtActive: { color: C.primaryFg },

  countLine: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 14, marginBottom: 10 },

  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  avatar: { width: 42, height: 42, borderRadius: 14, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  avatarTxt: { color: C.secondaryFg, fontFamily: fonts.bold, fontSize: 14 },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 1 },
  rowOrg: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, flexShrink: 1 },
  rolePill: { paddingHorizontal: 8, height: 20, borderRadius: 6, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  rolePillTxt: { fontFamily: fonts.medium, fontSize: 10, color: C.mutedFg, textTransform: "uppercase" },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 11, justifyContent: "center" },
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
  infoBanner: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "rgba(123,107,184,0.10)", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: "rgba(123,107,184,0.25)" },
  infoBannerTxt: { flex: 1, fontFamily: fonts.regular, fontSize: 12, color: C.foreground },
  linkBox: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.surface, borderRadius: 12, padding: 12 },
  linkTxt: { flex: 1, fontFamily: fonts.mono, fontSize: 12, color: C.foreground },

  actionBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, borderWidth: 1, backgroundColor: C.card },
  actionBtnTxt: { fontFamily: fonts.bold, fontSize: 14 },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  confirmBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
