// ADM-02 — User management.
//
// Live, admin-gated endpoints (backend/admin_routes.py · USERS):
//   list ........... GET   /admin/users            (patients pseudonymized server-side)
//   create ......... POST  /admin/users            {email, full_name, role, phone, organization, send_invite}
//   status ......... PATCH /admin/users/{id}/status {status: Active|Suspended, reason?}
//   unlock ......... POST  /admin/users/{id}/unlock {identity_checks[≥2], reason(≥10), force_password_reset}
//   reset pw ....... POST  /admin/users/{id}/reset-password  → emails a single-use
//                    reset link; returns only {reset_sent, email(masked), expires_at}
//   force logout ... POST  /admin/users/{id}/force-logout
//   export ......... GET   /admin/users/export     (text/csv)
//
// Every list row is a real record; nothing is fabricated. The search/entity/
// status filters run client-side over the fetched list for snappiness.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Modal, Animated, Switch, Platform, Share,
  KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, Search, UserPlus, Download, X, AlertTriangle, Mail, Phone,
  Building2, Calendar, Clock, KeyRound, ShieldOff, UserCheck, UserX, Lock,
  ChevronRight,
} from "lucide-react-native";
import { useLocalSearchParams } from "expo-router";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { sanitizeDigits, sanitizeName, sanitizeOrgName } from "@/src/lib/validators";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };

type LockInfo = { locked_at?: string; failed_attempts?: number; last_ip?: string; device?: string; repeated_ip?: boolean };
type AdminUser = {
  id: string; email?: string; full_name?: string; role?: string; phone?: string;
  organization?: string; status?: string; created_at?: string; avatar_initials?: string;
  is_online?: boolean; last_login_at?: string; last_login?: string; pseudonymized?: boolean;
  lock_info?: LockInfo;
};
const EMPTY_USER_FORM = {
  full_name: "", email: "", phone: "", role: "site", organization: "", send_invite: true,
};

const ROLE_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" }, { key: "sponsor", label: "Sponsor" }, { key: "cro", label: "CRO" },
  { key: "smo", label: "SMO" }, { key: "site", label: "Site" }, { key: "pi", label: "PI" },
  { key: "crc", label: "CRC" }, { key: "patient", label: "Patient" }, { key: "admin", label: "Admin" },
];
const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" }, { key: "Active", label: "Active" },
  { key: "Pending Verification", label: "Pending" }, { key: "Suspended", label: "Suspended" },
  { key: "Locked", label: "Locked" },
];
const CREATE_ROLES = ["sponsor", "cro", "smo", "site", "pi", "crc", "patient"];

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function statusColors(status?: string): { fg: string; bg: string } {
  switch (status) {
    case "Active": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "Pending Verification": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "Suspended": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "Locked": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function initialsOf(u: AdminUser): string {
  return u.avatar_initials || (u.full_name || u.email || "U").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}
function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

export default function AdminUsers() {
  const { open } = useAdminDrawer();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  const [selected, setSelected] = useState<AdminUser | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [unlockUser, setUnlockUser] = useState<AdminUser | null>(null);
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
      const res = await api.get("/admin/users");
      setUsers(Array.isArray(res.data) ? res.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load users. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // Global-search deep link: /admin/users?focus=<id> opens that exact record.
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  const consumedFocus = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || typeof focus !== "string" || focus === consumedFocus.current) return;
    const hit = users.find((u) => u.id === focus);
    if (hit) { consumedFocus.current = focus; setSelected(hit); }
  }, [focus, users]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users.filter((u) => {
      const matchesSearch = !q ||
        (u.full_name || "").toLowerCase().includes(q) ||
        (u.email || "").toLowerCase().includes(q) ||
        (u.organization || "").toLowerCase().includes(q);
      const matchesRole = roleFilter === "all" || u.role === roleFilter;
      const matchesStatus = statusFilter === "all" || (u.status || "Active") === statusFilter;
      return matchesSearch && matchesRole && matchesStatus;
    });
  }, [users, search, roleFilter, statusFilter]);

  const tiles = useMemo(() => [
    { label: "Total", value: users.length },
    { label: "Active", value: users.filter((u) => (u.status || "Active") === "Active").length },
    { label: "Sponsors", value: users.filter((u) => u.role === "sponsor").length },
    { label: "PIs", value: users.filter((u) => u.role === "pi").length },
    { label: "Patients", value: users.filter((u) => u.role === "patient").length },
    { label: "Locked", value: users.filter((u) => u.status === "Locked").length },
  ], [users]);

  // ── Actions ──
  const patchLocal = (id: string, patch: Partial<AdminUser>) => {
    setUsers((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)));
    setSelected((prev) => (prev && prev.id === id ? { ...prev, ...patch } : prev));
  };

  const setStatus = async (u: AdminUser, status: "Active" | "Suspended") => {
    setBusy(true);
    patchLocal(u.id, { status }); // optimistic
    try {
      await api.patch(`/admin/users/${u.id}/status`, { status });
      showToast(`${u.full_name || "User"} ${status === "Suspended" ? "suspended" : "activated"}`);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't update status"));
      await load();
    } finally { setBusy(false); }
  };

  const resetPassword = async (u: AdminUser) => {
    setBusy(true);
    try {
      const res = await api.post(`/admin/users/${u.id}/reset-password`);
      const masked = res.data?.email;
      showToast(masked ? `Password reset link sent to ${masked}` : "Password reset link sent");
    } catch (e) {
      showToast(errMsg(e, "Couldn't reset password"));
    } finally { setBusy(false); }
  };

  const forceLogout = async (u: AdminUser) => {
    setBusy(true);
    try {
      await api.post(`/admin/users/${u.id}/force-logout`);
      patchLocal(u.id, { is_online: false });
      showToast(`${u.full_name || "User"} signed out of all sessions`);
    } catch (e) {
      showToast(errMsg(e, "Couldn't force logout"));
    } finally { setBusy(false); }
  };

  const exportCsv = async () => {
    try {
      const res = await api.get("/admin/users/export", { responseType: "text" as any });
      const csv: string = typeof res.data === "string" ? res.data : String(res.data ?? "");
      if (Platform.OS === "web") {
        const g: any = globalThis;
        const blob = new g.Blob([csv], { type: "text/csv" });
        const url = g.URL.createObjectURL(blob);
        const a = g.document.createElement("a");
        a.href = url; a.download = "users.csv"; a.click();
        g.URL.revokeObjectURL(url);
      } else {
        await Share.share({ message: csv, title: "users.csv" });
      }
      showToast("User directory exported");
    } catch (e) {
      showToast(errMsg(e, "Export failed"));
    }
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
        <Hero
          onMenu={open} onRefresh={onRefresh}
          onExport={exportCsv} onAdd={() => setAddOpen(true)}
        />

        {loading ? (
          <Loading label="Loading users…" />
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

            <SearchBar value={search} onChange={setSearch} placeholder="Search name, email or organization…" />
            <ChipRow label="ENTITY" chips={ROLE_FILTERS} value={roleFilter} onChange={setRoleFilter} />
            <ChipRow label="STATUS" chips={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />

            <RNText style={st.countLine}>{filtered.length} of {users.length} users</RNText>

            {filtered.length === 0 ? (
              <EmptyCard message="No users match the current filters." />
            ) : (
              <View style={{ gap: 10 }}>
                {filtered.map((u) => {
                  const sc = statusColors(u.status);
                  return (
                    <Pressable key={u.id} testID={`user-${u.id}`} onPress={() => setSelected(u)} style={st.row}>
                      <View style={st.avatar}><RNText style={st.avatarTxt}>{initialsOf(u)}</RNText></View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <RNText style={st.rowName} numberOfLines={1}>{u.full_name || "—"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{u.email || "—"}</RNText>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 }}>
                          <View style={st.rolePill}><RNText style={st.rolePillTxt}>{u.role || "—"}</RNText></View>
                          {!!u.organization && <RNText style={st.rowOrg} numberOfLines={1}>{u.organization}</RNText>}
                        </View>
                      </View>
                      <View style={{ alignItems: "flex-end", gap: 6 }}>
                        <View style={[st.badge, { backgroundColor: sc.bg }]}>
                          {u.status === "Locked" && <Lock size={10} color={sc.fg} />}
                          <RNText style={[st.badgeTxt, { color: sc.fg }]}>{u.status || "Active"}</RNText>
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

      {/* Detail sheet */}
      <UserDetailSheet
        user={selected} busy={busy}
        onClose={() => setSelected(null)}
        onSuspend={(u) => setStatus(u, "Suspended")}
        onActivate={(u) => setStatus(u, "Active")}
        onReset={resetPassword}
        onForceLogout={forceLogout}
        onUnlock={(u) => { setSelected(null); setUnlockUser(u); }}
      />

      {/* Add user */}
      <AddUserSheet
        open={addOpen} onClose={() => setAddOpen(false)}
        onCreated={(msg) => { showToast(msg); load(); }}
      />

      {/* Unlock workflow */}
      <UnlockSheet
        user={unlockUser} onClose={() => setUnlockUser(null)}
        onDone={(msg) => { setUnlockUser(null); showToast(msg); load(); }}
      />

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

// ── Hero header ──────────────────────────────────────────────────────────────
function Hero({ onMenu, onRefresh, onExport, onAdd }: { onMenu: () => void; onRefresh: () => void; onExport: () => void; onAdd: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>User management</RNText>
          </View>
          <Pressable testID="users-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Accounts, roles, status and entity assignments. Patient records are pseudonymized.</RNText>
        <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
          <Pressable testID="users-export" onPress={onExport} style={st.heroBtnGhost}>
            <Download size={15} color={C.primaryFg} /><RNText style={st.heroBtnGhostTxt}>Export CSV</RNText>
          </Pressable>
          <Pressable testID="users-add" onPress={onAdd} style={st.heroBtnSolid}>
            <UserPlus size={15} color={C.primary} /><RNText style={st.heroBtnSolidTxt}>Add user</RNText>
          </Pressable>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Detail sheet ─────────────────────────────────────────────────────────────
function UserDetailSheet({ user, busy, onClose, onSuspend, onActivate, onReset, onForceLogout, onUnlock }: {
  user: AdminUser | null; busy: boolean; onClose: () => void;
  onSuspend: (u: AdminUser) => void; onActivate: (u: AdminUser) => void;
  onReset: (u: AdminUser) => void; onForceLogout: (u: AdminUser) => void; onUnlock: (u: AdminUser) => void;
}) {
  return (
    <Sheet open={!!user} onClose={onClose} title="User details">
      {user && (
        <View style={{ gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
            <View style={[st.avatar, { width: 56, height: 56, borderRadius: 18 }]}>
              <RNText style={[st.avatarTxt, { fontSize: 18 }]}>{initialsOf(user)}</RNText>
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.sheetName} numberOfLines={1}>{user.full_name || "—"}</RNText>
              <RNText style={st.rowSub} numberOfLines={1}>{user.role || "—"}</RNText>
              {(() => { const sc = statusColors(user.status); return (
                <View style={[st.badge, { backgroundColor: sc.bg, alignSelf: "flex-start", marginTop: 6 }]}>
                  <RNText style={[st.badgeTxt, { color: sc.fg }]}>{user.status || "Active"}</RNText>
                </View>
              ); })()}
            </View>
          </View>

          {user.pseudonymized && (
            <View style={st.infoBanner}>
              <AlertTriangle size={15} color={C.info} />
              <RNText style={st.infoBannerTxt}>Patient record — contact details are pseudonymized.</RNText>
            </View>
          )}

          <InfoRow icon={Mail} label="Email" value={user.email || "—"} />
          <InfoRow icon={Phone} label="Phone" value={user.phone || "—"} />
          <InfoRow icon={Building2} label="Organization" value={user.organization || "—"} />
          <InfoRow icon={Calendar} label="Registered" value={fmtDate(user.created_at)} />
          <InfoRow icon={Clock} label="Last login" value={fmtDate(user.last_login_at || user.last_login)} />

          {user.status === "Locked" && user.lock_info && (
            <View style={st.lockCard}>
              <RNText style={st.lockTitle}>Lock details</RNText>
              <RNText style={st.lockLine}>Failed attempts: {user.lock_info.failed_attempts ?? "—"}</RNText>
              {!!user.lock_info.last_ip && <RNText style={st.lockLine}>Last IP: {user.lock_info.last_ip}</RNText>}
              {!!user.lock_info.device && <RNText style={st.lockLine}>Device: {user.lock_info.device}</RNText>}
              {user.lock_info.repeated_ip && <RNText style={[st.lockLine, { color: C.destructive }]}>Same IP caused repeated lockouts in 24h</RNText>}
            </View>
          )}

          <View style={{ gap: 10, marginTop: 4 }}>
            {user.status === "Locked" && (
              <ActionBtn label="Unlock account" icon={KeyRound} tone="warning" onPress={() => onUnlock(user)} disabled={busy} />
            )}
            {user.status === "Active" ? (
              <ActionBtn label="Suspend user" icon={UserX} tone="destructive" onPress={() => onSuspend(user)} disabled={busy} />
            ) : user.status === "Suspended" ? (
              <ActionBtn label="Activate user" icon={UserCheck} tone="success" onPress={() => onActivate(user)} disabled={busy} />
            ) : null}
            <ActionBtn label="Reset password" icon={KeyRound} tone="neutral" onPress={() => onReset(user)} disabled={busy} />
            <ActionBtn label="Force logout" icon={ShieldOff} tone="neutral" onPress={() => onForceLogout(user)} disabled={busy} />
          </View>
        </View>
      )}
    </Sheet>
  );
}

// ── Add user sheet ───────────────────────────────────────────────────────────
function AddUserSheet({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (msg: string) => void }) {
  const [form, setForm] = useState(EMPTY_USER_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setForm(EMPTY_USER_FORM); setErr(null); } }, [open]);

  const valid = form.full_name.trim().length > 0 && /\S+@\S+\.\S+/.test(form.email);
  const submit = async () => {
    if (!valid) { setErr("A valid name and email are required"); return; }
    setSaving(true); setErr(null);
    try {
      const res = await api.post("/admin/users", {
        email: form.email.trim(), full_name: form.full_name.trim(), role: form.role,
        phone: form.phone.trim(), organization: form.organization.trim(), send_invite: form.send_invite,
      });
      const maskedTo = res.data?.password_setup?.email;
      onCreated(
        `${form.full_name.trim()} added · password setup link sent${maskedTo ? ` to ${maskedTo}` : ""}` +
        (form.send_invite ? " · invite sent" : ""),
      );
      onClose();
    } catch (e) {
      setErr(errMsg(e, "Couldn't create user"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Add user">
      <View style={{ gap: 12 }}>
        <FormField label="Full name"><Input value={form.full_name} onChangeText={(v) => setForm({ ...form, full_name: sanitizeName(v) })} placeholder="Jane Doe" /></FormField>
        <FormField label="Email"><Input value={form.email} onChangeText={(v) => setForm({ ...form, email: v })} placeholder="jane@org.com" keyboardType="email-address" autoCapitalize="none" /></FormField>
        <FormField label="Phone"><Input value={form.phone} onChangeText={(v) => setForm({ ...form, phone: sanitizeDigits(v, 10) })} placeholder="+91-XXXXXXXXXX" keyboardType="phone-pad" /></FormField>
        <FormField label="Role">
          <View style={st.chipWrap}>
            {CREATE_ROLES.map((r) => (
              <Pressable key={r} onPress={() => setForm({ ...form, role: r })} style={[st.chip, form.role === r && st.chipActive]}>
                <RNText style={[st.chipTxt, form.role === r && st.chipTxtActive]}>{r}</RNText>
              </Pressable>
            ))}
          </View>
        </FormField>
        <FormField label="Organization"><Input value={form.organization} onChangeText={(v) => setForm({ ...form, organization: sanitizeOrgName(v) })} placeholder="Join existing or create new" /></FormField>
        <Pressable onPress={() => setForm({ ...form, send_invite: !form.send_invite })} style={st.switchRow}>
          <RNText style={st.switchLabel}>Send onboarding invitation</RNText>
          <Switch value={form.send_invite} onValueChange={(v) => setForm({ ...form, send_invite: v })} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
        </Pressable>
        {err && <RNText style={st.errText}>{err}</RNText>}
        <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Save" onConfirm={submit} disabled={!valid} loading={saving} />
      </View>
    </Sheet>
  );
}

// ── Unlock sheet ─────────────────────────────────────────────────────────────
function UnlockSheet({ user, onClose, onDone }: { user: AdminUser | null; onClose: () => void; onDone: (msg: string) => void }) {
  const [checks, setChecks] = useState({ email: false, orgId: false, ticket: false });
  const [reason, setReason] = useState("");
  const [forceReset, setForceReset] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (user) { setChecks({ email: false, orgId: false, ticket: false }); setReason(""); setForceReset(true); setErr(null); } }, [user]);

  const checked = [checks.email, checks.orgId, checks.ticket].filter(Boolean).length;
  const canUnlock = checked >= 2 && reason.trim().length >= 10;

  const submit = async () => {
    if (!user || !canUnlock) return;
    const identity_checks = [
      checks.email && "Verified via registered email",
      checks.orgId && "Verified via organization ID",
      checks.ticket && "Verified via support ticket reference",
    ].filter(Boolean) as string[];
    setSaving(true); setErr(null);
    try {
      await api.post(`/admin/users/${user.id}/unlock`, { identity_checks, reason: reason.trim(), force_password_reset: forceReset });
      onDone(`${user.full_name || "User"} unlocked${forceReset ? " · password reset required" : ""}`);
    } catch (e) {
      setErr(errMsg(e, "Couldn't unlock account"));
    } finally { setSaving(false); }
  };

  const CheckItem = ({ on, onToggle, label }: { on: boolean; onToggle: () => void; label: string }) => (
    <Pressable onPress={onToggle} style={st.checkRow}>
      <View style={[st.checkbox, on && { backgroundColor: C.primary, borderColor: C.primary }]}>{on && <RNText style={st.checkMark}>✓</RNText>}</View>
      <RNText style={st.checkLabel}>{label}</RNText>
    </Pressable>
  );

  return (
    <Sheet open={!!user} onClose={onClose} title="Unlock account">
      {user && (
        <View style={{ gap: 12 }}>
          <View style={st.lockCard}>
            <RNText style={st.lockTitle}>{user.full_name || "User"}</RNText>
            <RNText style={st.lockLine}>Failed attempts: {user.lock_info?.failed_attempts ?? "—"}</RNText>
            {!!user.lock_info?.last_ip && <RNText style={st.lockLine}>Last IP: {user.lock_info.last_ip}</RNText>}
          </View>
          <RNText style={st.fieldLabel}>Identity verification (at least 2 required)</RNText>
          <View style={{ gap: 8 }}>
            <CheckItem on={checks.email} onToggle={() => setChecks({ ...checks, email: !checks.email })} label="Verified via registered email" />
            <CheckItem on={checks.orgId} onToggle={() => setChecks({ ...checks, orgId: !checks.orgId })} label="Verified via organization ID" />
            <CheckItem on={checks.ticket} onToggle={() => setChecks({ ...checks, ticket: !checks.ticket })} label="Verified via support ticket reference" />
          </View>
          <FormField label="Reason for unlock (min 10 chars, permanently logged)">
            <Input value={reason} onChangeText={setReason} placeholder="Describe verification performed…" multiline />
          </FormField>
          <Pressable onPress={() => setForceReset(!forceReset)} style={st.switchRow}>
            <RNText style={st.switchLabel}>Force password reset on next login</RNText>
            <Switch value={forceReset} onValueChange={setForceReset} trackColor={{ true: C.primary, false: C.border }} thumbColor={C.white} />
          </Pressable>
          {err && <RNText style={st.errText}>{err}</RNText>}
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Confirm unlock" onConfirm={submit} disabled={!canUnlock} loading={saving} tone="success" />
        </View>
      )}
    </Sheet>
  );
}

// ── Shared primitives (kept in-file: this screen owns only its own module) ────
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

export function Input(props: React.ComponentProps<typeof TextInput>) {
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
export function SheetActions({ cancelLabel, onCancel, confirmLabel, onConfirm, disabled, loading, tone = "primary" }: { cancelLabel: string; onCancel: () => void; confirmLabel: string; onConfirm: () => void; disabled?: boolean; loading?: boolean; tone?: "primary" | "destructive" | "success" }) {
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
export function SearchBar({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <View style={st.searchBar}>
      <Search size={17} color={C.mutedFg} />
      <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor="rgba(123,95,115,0.5)" style={st.searchInput} />
      {!!value && <Pressable onPress={() => onChange("")} hitSlop={8}><X size={16} color={C.mutedFg} /></Pressable>}
    </View>
  );
}
export function ChipRow({ label, chips, value, onChange }: { label: string; chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
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
export function Loading({ label }: { label: string }) {
  return <View style={{ paddingTop: 60, alignItems: "center" }}><ActivityIndicator color={C.primary} /><RNText style={st.loadingTxt}>{label}</RNText></View>;
}
export function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
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
export function EmptyCard({ message }: { message: string }) {
  return <View style={[st.card, { marginTop: 8 }]}><RNText style={st.emptyText}>{message}</RNText></View>;
}
export function Toast({ text, anim }: { text: string; anim: Animated.Value }) {
  if (text === "") return null;
  return (
    <Animated.View pointerEvents="none" style={[st.toast, { opacity: anim, transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }] }]}>
      <RNText style={st.toastTxt}>{text}</RNText>
    </Animated.View>
  );
}

export const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 40, borderBottomLeftRadius: 24, borderBottomRightRadius: 24 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrow: { color: W.w55, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 24, marginTop: 2 },
  heroSub: { color: W.w70, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  iconBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  heroBtnGhost: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 12, borderRadius: 999, backgroundColor: W.w15, borderWidth: 1, borderColor: W.w20 },
  heroBtnGhostTxt: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 13 },
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
  chipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
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
  badgeTxt: { fontFamily: fonts.bold, fontSize: 11 },

  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingHorizontal: 16, paddingVertical: 16 },
  emptyText: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, paddingVertical: 20, textAlign: "center" },
  loadingTxt: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  errIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },

  // Sheet
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
  lockCard: { backgroundColor: "rgba(216,154,60,0.10)", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: "rgba(216,154,60,0.25)", gap: 3 },
  lockTitle: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground, marginBottom: 2 },
  lockLine: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg },

  actionBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, borderWidth: 1, backgroundColor: C.card },
  actionBtnTxt: { fontFamily: fonts.bold, fontSize: 14 },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: C.surface, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 10 },
  switchLabel: { flex: 1, fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
  checkRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  checkbox: { width: 22, height: 22, borderRadius: 6, borderWidth: 1.5, borderColor: C.border, alignItems: "center", justifyContent: "center", backgroundColor: C.card },
  checkMark: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 13 },
  checkLabel: { flex: 1, fontFamily: fonts.regular, fontSize: 13, color: C.foreground },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  confirmBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
