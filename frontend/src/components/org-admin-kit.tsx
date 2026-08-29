// Org-admin console shared kit — Task 6.6.
//
// The three organization-admin consoles (Sponsor/CRO · Site · SMO) all compose
// the same ceremony pieces so the admin experience reads as one family:
//   • ConsoleHeader     — the command header + pulse rail
//   • DeckTabs          — segmented rail
//   • TeamRoster        — invite / delete / make-admin / assign-site
//   • TransferOwnershipSheet — successor → reason → handover → propose (backend
//                         records a PENDING transfer; the successor accepts
//                         out-of-band, so we never fake acceptance here)
//   • AuditTrail        — grouped by trial or shown as a timeline
//   • DelegationGate    — "create trials once delegated"
//
// Everything is wired to the live org endpoints in backend/org_routes.py. All
// patient data arrives already masked (SUBJ-xxx + initials) — the consoles
// never see raw PII. Dawn design tokens only; no new palette.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, Text as RNText, TextInput, Modal,
  ActivityIndicator, Animated, Platform, KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  X, ChevronLeft, ShieldCheck, Clock, ChevronDown, Check, UserPlus, Trash2,
  UserMinus, Send, ArrowRightLeft, FlaskConical, Building2, KeyRound, Landmark,
  GraduationCap, FileSignature, Stamp, RefreshCcw, AlertTriangle, MapPin,
  PenLine, Archive, ArchiveRestore, FolderOpen,
} from "lucide-react-native";
import { useRouter } from "expo-router";
import { colors as C, dawnGradient, fonts } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { sanitizeDesignation, sanitizeName } from "@/src/lib/validators";

const W = { w10: "rgba(255,255,255,0.10)", w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w60: "rgba(255,255,255,0.60)", w70: "rgba(255,255,255,0.70)", w75: "rgba(255,255,255,0.75)" };

export const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

// ── shared helpers ───────────────────────────────────────────────────────────
export const stripTitle = (n: string) => (n || "").replace(/^(Dr\.|Mr\.|Ms\.|Mrs\.)\s/, "");
export const initialsOf = (n: string): string => {
  const p = stripTitle(n).trim().split(/\s+/);
  return ((p[0]?.[0] || "") + (p[1]?.[0] || "")).toUpperCase() || "?";
};
const AVATAR_TONES: { bg: string; fg: string }[] = [
  { bg: "rgba(123,107,184,0.15)", fg: C.info }, { bg: "rgba(230,155,92,0.15)", fg: C.accent },
  { bg: "rgba(142,91,180,0.15)", fg: C.violet }, { bg: "rgba(92,154,110,0.15)", fg: C.success },
  { bg: "rgba(216,154,60,0.20)", fg: C.warning }, { bg: "rgba(166,33,63,0.12)", fg: C.primary },
];
export const avatarTone = (n: string) => {
  let h = 0;
  for (let i = 0; i < (n || "").length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
  return AVATAR_TONES[h % AVATAR_TONES.length];
};

// ── shared types (mirror backend/org_routes.py shapes) ───────────────────────
export type MemberStatus = "active" | "invited" | "deactivated" | "rejected";
export interface OrgMember {
  id: string;
  name: string;
  email?: string;
  designation?: string;
  role: string;
  site?: string;
  department?: string;
  admin: boolean;
  status: MemberStatus;
  you?: boolean;
}

// audit-trail row: backend returns `kind` as the audit category e.g.
// "org.member_invite"; `trial` is a trial id (groups the entry) or null.
export interface AuditEntry {
  id: string;
  at: string;      // iso timestamp
  actor: string;
  action: string;
  detail?: string;
  kind: string;
  trial?: string | null;
  status?: string;
}

export interface OrgSite { id: string; name: string; address?: string }

export interface OrgSubject { subject: string; initials?: string; status?: string; enrolled_date?: string }
export interface OrgVisit {
  visit_number?: number;
  name?: string;
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
}
export interface OrgTrial {
  id: string;
  title?: string;
  protocol_id?: string;
  phase?: string;
  condition?: string;
  status?: string;
  archived?: boolean;
  duration?: string | null;
  drug?: string | null;
  recruitment_status?: string | null;
  accessLevel: "full" | "restricted";
  createdBy?: string;
  enrolled?: number;
  target?: number | null;
  target_enrollment?: number | null;
  sponsor?: string;
  cro?: string;
  pis?: { id: string; name?: string; organization?: string }[];
  crcs?: { id: string; name?: string; organization?: string }[];
  accessStatus?: "full" | "restricted";
  permissions?: {
    canViewDetails?: boolean;
    canEdit?: boolean;
    canArchive?: boolean;
    canManageDocuments?: boolean;
    canRequestAccess?: boolean;
  };
  documentCount?: number;
  updatedAt?: string;
  updatedBy?: { id?: string; name?: string };
  sites?: string[];
  schedule?: OrgVisit[];
  subjects?: OrgSubject[];
}

// ── org-context resolver ─────────────────────────────────────────────────────
// The org endpoints are keyed by orgId, but the signed-in user only carries the
// org *name*. Resolve id + type from the public directory, then route.
export type OrgType = "sponsor" | "cro" | "smo" | "site" | string;

export function consoleRouteForType(type?: OrgType): string {
  switch ((type || "").toLowerCase()) {
    case "smo": return "/(app)/org-admin/smo";
    case "site": return "/(app)/org-admin/site";
    default: return "/(app)/org-admin/sponsor"; // sponsor + cro
  }
}

export function useOrgContext(enabled = true) {
  const { user } = useAuth();
  const [orgId, setOrgId] = useState<string | null>(null);
  const [orgType, setOrgType] = useState<OrgType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const resolve = useCallback(async () => {
    if (!enabled) {
      setOrgId(null);
      setOrgType(null);
      setError(null);
      setLoading(false);
      return;
    }
    const name = (user?.organization || "").trim();
    setLoading(true);
    setOrgId(null);
    setOrgType(null);
    setError(null);
    if (!name) { setLoading(false); setError("Your account is not linked to an organization."); return; }
    try {
      const r = await api.get("/organizations", { params: { search: name } });
      const list: any[] = Array.isArray(r.data) ? r.data : [];
      const match = list.find((o) => (o.name || "").trim() === name) || list[0];
      if (!match) { setError("Organization not found in the directory."); return; }
      setOrgId(match.id);
      setOrgType(match.type);
    } catch (e) {
      setError(errMsg(e, "Couldn't resolve your organization."));
    } finally {
      setLoading(false);
    }
  }, [enabled, user?.organization]);

  useEffect(() => { resolve(); }, [resolve]);
  return { orgId, orgType, orgName: user?.organization || "", loading, error, retry: resolve };
}

// ── Toast ────────────────────────────────────────────────────────────────────
export function useToast() {
  const [toast, setToast] = useState("");
  const anim = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showToast = useCallback((msg: string) => {
    setToast(msg);
    Animated.timing(anim, { toValue: 1, duration: 180, useNativeDriver: true }).start();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      Animated.timing(anim, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setToast(""));
    }, 2600);
  }, [anim]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const ToastView = toast === "" ? null : (
    <Animated.View pointerEvents="none" style={[k.toast, { opacity: anim, transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }] }]}>
      <RNText style={k.toastTxt}>{toast}</RNText>
    </Animated.View>
  );
  return { showToast, ToastView };
}

// ── low-level primitives ─────────────────────────────────────────────────────
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose}>
      <View style={k.sheetOverlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={k.sheet}>
            <View style={k.sheetHeader}>
              <RNText style={k.sheetTitle}>{title}</RNText>
              <Pressable onPress={onClose} hitSlop={10} style={k.sheetClose}><X size={18} color={C.mutedFg} /></Pressable>
            </View>
            <ScrollView showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 28 }}>
              {children}
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

export function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <View style={{ gap: 6 }}>
      <RNText style={k.fieldLabel}>{label}{required && <RNText style={{ color: C.accent }}> *</RNText>}</RNText>
      {children}
    </View>
  );
}

export function KitInput(props: React.ComponentProps<typeof TextInput>) {
  return <TextInput placeholderTextColor="rgba(123,95,115,0.5)" {...props} style={[k.input, props.multiline && { height: 78, textAlignVertical: "top" }, props.style]} />;
}

export function PrimaryButton({ label, onPress, disabled, loading, gradient = true, bg }: { label: string; onPress: () => void; disabled?: boolean; loading?: boolean; gradient?: boolean; bg?: string }) {
  const inner = loading ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={k.primaryBtnTxt}>{label}</RNText>;
  if (gradient && !disabled) {
    return (
      <Pressable onPress={disabled || loading ? undefined : onPress} style={{ borderRadius: 999, overflow: "hidden" }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={k.primaryBtn}>{inner}</LinearGradient>
      </Pressable>
    );
  }
  return (
    <Pressable onPress={disabled || loading ? undefined : onPress} style={[k.primaryBtn, { backgroundColor: disabled ? C.surface : (bg || C.primary) }]}>
      {loading ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RNText style={[k.primaryBtnTxt, disabled && { color: C.mutedFg }]}>{label}</RNText>}
    </Pressable>
  );
}

export function Loading({ label }: { label?: string }) {
  return <View style={{ paddingTop: 48, alignItems: "center" }}><ActivityIndicator color={C.primary} />{!!label && <RNText style={k.loadingTxt}>{label}</RNText>}</View>;
}
export function ErrorCard({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <View style={[k.card, { borderColor: "rgba(192,57,43,0.30)", marginTop: 12 }]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <View style={k.errIcon}><AlertTriangle size={20} color={C.destructive} /></View>
        <RNText style={{ flex: 1, color: C.foreground, fontFamily: fonts.semibold, fontSize: 13 }}>{message}</RNText>
      </View>
      {onRetry && <Pressable onPress={onRetry} style={k.retryBtn}><RefreshCcw size={15} color={C.primary} /><RNText style={{ color: C.primary, fontFamily: fonts.bold, fontSize: 13 }}>Retry</RNText></Pressable>}
    </View>
  );
}
export function EmptyCard({ icon: Icon, title, subtitle }: { icon?: any; title: string; subtitle?: string }) {
  return (
    <View style={[k.card, { alignItems: "center", paddingVertical: 30, borderStyle: "dashed" }]}>
      {Icon && <View style={k.emptyIcon}><Icon size={22} color={C.mutedFg} /></View>}
      <RNText style={k.emptyTitle}>{title}</RNText>
      {!!subtitle && <RNText style={k.emptySub}>{subtitle}</RNText>}
    </View>
  );
}

// ── ConfirmDialog ────────────────────────────────────────────────────────────
export interface ConfirmState { title: string; body: string; confirmLabel: string; onConfirm: () => void }
export function ConfirmDialog({ confirm, onCancel, busy }: { confirm: ConfirmState | null; onCancel: () => void; busy?: boolean }) {
  return (
    <Modal visible={!!confirm} transparent animationType="fade" onRequestClose={onCancel}>
      <View style={k.centerOverlay}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onCancel} />
        {confirm && (
          <View style={k.dialog}>
            <View style={[k.dialogIcon, { backgroundColor: "rgba(192,57,43,0.10)" }]}><Trash2 size={22} color={C.destructive} /></View>
            <RNText style={k.dialogTitle}>{confirm.title}</RNText>
            <RNText style={k.dialogBody}>{confirm.body}</RNText>
            <View style={{ marginTop: 18, gap: 10, width: "100%" }}>
              <Pressable onPress={busy ? undefined : confirm.onConfirm} style={[k.dialogDanger, busy && { opacity: 0.6 }]}>
                {busy ? <ActivityIndicator color={C.destructiveFg} size="small" /> : <RNText style={k.dialogDangerTxt}>{confirm.confirmLabel}</RNText>}
              </Pressable>
              <Pressable onPress={onCancel} style={{ paddingVertical: 10, alignItems: "center" }}><RNText style={k.dialogCancelTxt}>Cancel</RNText></Pressable>
            </View>
          </View>
        )}
      </View>
    </Modal>
  );
}

// ── ConsoleHeader ────────────────────────────────────────────────────────────
export function ConsoleHeader({ eyebrow, org, roleLabel, note, glow, pulse, onBack }: {
  eyebrow: string; org: string; roleLabel: string; note?: string; glow?: string;
  pulse: { value: number | string; label: string; onPress?: () => void }[]; onBack: () => void;
}) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={k.header}>
      <View pointerEvents="none" style={[k.headerGlow, { backgroundColor: glow || "rgba(230,155,92,0.30)" }]} />
      <SafeAreaView edges={["top"]}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Pressable onPress={onBack} hitSlop={8} style={k.iconBtn}><ChevronLeft size={22} color={C.primaryFg} /></Pressable>
          <View style={k.roleBadge}><ShieldCheck size={13} color={C.primaryFg} /><RNText style={k.roleBadgeTxt}>{roleLabel}</RNText></View>
        </View>
        <RNText style={k.headerEyebrow} numberOfLines={1}>{eyebrow}</RNText>
        <RNText style={k.headerTitle} numberOfLines={2}>{org}</RNText>
        {!!note && <RNText style={k.headerNote}>{note}</RNText>}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, marginTop: 14 }}>
          {pulse.map((p) => (
            <Pressable key={p.label} onPress={p.onPress} disabled={!p.onPress} style={k.pulseTile}>
              <RNText style={k.pulseValue}>{p.value}</RNText>
              <RNText style={k.pulseLabel}>{p.label}</RNText>
            </Pressable>
          ))}
        </ScrollView>
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── DeckTabs ─────────────────────────────────────────────────────────────────
export function DeckTabs({ tabs, active, onChange, activeColor = C.primary }: {
  tabs: { key: string; label: string; count?: number }[]; active: string; onChange: (k: string) => void; activeColor?: string;
}) {
  return (
    <View style={k.deck}>
      {tabs.map((t) => {
        const on = active === t.key;
        return (
          <Pressable key={t.key} onPress={() => onChange(t.key)} style={[k.deckTab, on && { backgroundColor: activeColor }]}>
            <RNText style={[k.deckTabTxt, on && { color: C.primaryFg }]}>{t.label}</RNText>
            {typeof t.count === "number" && <RNText style={[k.deckCount, on && { color: W.w75 }]}>{t.count}</RNText>}
          </Pressable>
        );
      })}
    </View>
  );
}

// ── MemberStatusPill ─────────────────────────────────────────────────────────
const STATUS_STYLE: Record<MemberStatus, { bg: string; fg: string; label: string }> = {
  active: { bg: "rgba(92,154,110,0.14)", fg: C.success, label: "Active" },
  invited: { bg: "rgba(216,154,60,0.16)", fg: C.warning, label: "Invited" },
  deactivated: { bg: C.surface, fg: C.mutedFg, label: "Deactivated" },
  rejected: { bg: C.surface, fg: C.mutedFg, label: "Declined" },
};
export function MemberStatusPill({ status }: { status: MemberStatus }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.active;
  return (
    <View style={[k.statusPill, { backgroundColor: s.bg }]}>
      {status === "invited" && <Clock size={10} color={s.fg} />}
      <RNText style={[k.statusPillTxt, { color: s.fg }]}>{s.label}</RNText>
    </View>
  );
}

// ── AuditTrail ───────────────────────────────────────────────────────────────
const ORG_GROUP = "__org__";
function auditVisual(kind: string): { icon: any; bg: string; fg: string } {
  const kd = (kind || "").toLowerCase();
  if (kd.includes("invite")) return { icon: Send, bg: "rgba(123,107,184,0.14)", fg: C.info };
  if (kd.includes("remove") || kd.includes("delete")) return { icon: UserMinus, bg: "rgba(192,57,43,0.10)", fg: C.destructive };
  if (kd.includes("make_admin") || kd.includes("admin")) return { icon: ShieldCheck, bg: "rgba(230,155,92,0.16)", fg: C.accent };
  if (kd.includes("transfer")) return { icon: ArrowRightLeft, bg: "rgba(142,91,180,0.14)", fg: C.violet };
  if (kd.includes("access")) return { icon: KeyRound, bg: "rgba(216,154,60,0.16)", fg: C.warning };
  if (kd.includes("site")) return { icon: Building2, bg: "rgba(166,33,63,0.10)", fg: C.primary };
  if (kd.includes("delegation")) return { icon: Landmark, bg: "rgba(216,154,60,0.16)", fg: C.warning };
  return { icon: FlaskConical, bg: "rgba(92,154,110,0.14)", fg: C.success };
}
function relTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}
function AuditRows({ entries }: { entries: AuditEntry[] }) {
  return (
    <View style={{ gap: 8 }}>
      {entries.map((e) => {
        const v = auditVisual(e.kind);
        return (
          <View key={e.id} style={k.auditRow}>
            <View style={[k.auditDot, { backgroundColor: v.bg }]}><v.icon size={15} color={v.fg} /></View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={k.auditAction}>{e.action || e.kind}</RNText>
              {!!e.detail && <RNText style={k.auditDetail}>{e.detail}</RNText>}
              <RNText style={k.auditMeta}>{e.actor || "System"} · {relTime(e.at)}</RNText>
            </View>
          </View>
        );
      })}
    </View>
  );
}
export function AuditTrail({ entries, loading, error, onRetry }: { entries: AuditEntry[]; loading?: boolean; error?: string | null; onRetry?: () => void }) {
  const [view, setView] = useState<"trial" | "time">("trial");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const map = new Map<string, AuditEntry[]>();
    for (const e of entries) {
      const key = e.trial || ORG_GROUP;
      const list = map.get(key);
      if (list) list.push(e); else map.set(key, [e]);
    }
    return [...map.entries()];
  }, [entries]);

  if (loading) return <Loading label="Loading audit trail…" />;
  if (error) return <ErrorCard message={error} onRetry={onRetry} />;
  if (entries.length === 0) return <EmptyCard icon={Stamp} title="Nothing recorded yet" subtitle="Every admin action — invites, removals, transfers — lands here automatically." />;

  const toggle = (key: string) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <View>
      <View style={{ flexDirection: "row", gap: 8, marginBottom: 12 }}>
        {([{ key: "trial", label: "By trial" }, { key: "time", label: "Timeline" }] as const).map((v) => (
          <Pressable key={v.key} onPress={() => setView(v.key)} style={[k.miniTab, view === v.key && { backgroundColor: C.primary, borderColor: C.primary }]}>
            <RNText style={[k.miniTabTxt, view === v.key && { color: C.primaryFg }]}>{v.label}</RNText>
          </Pressable>
        ))}
      </View>
      {view === "time" ? (
        <AuditRows entries={entries} />
      ) : (
        <View style={{ gap: 10 }}>
          {groups.map(([key, list]) => {
            const isOrg = key === ORG_GROUP;
            const open = !collapsed.has(key);
            return (
              <View key={key} style={k.auditGroup}>
                <Pressable onPress={() => toggle(key)} style={k.auditGroupHead}>
                  <View style={[k.auditGroupIcon, { backgroundColor: isOrg ? "rgba(166,33,63,0.10)" : "rgba(230,155,92,0.14)" }]}>
                    {isOrg ? <Building2 size={17} color={C.primary} /> : <FlaskConical size={17} color={C.accent} />}
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <RNText style={k.auditGroupTitle} numberOfLines={1}>{isOrg ? "Organization & team" : key}</RNText>
                    <RNText style={k.auditGroupSub}>{list.length} change{list.length === 1 ? "" : "s"} recorded</RNText>
                  </View>
                  <View style={k.auditGroupCount}><RNText style={k.auditGroupCountTxt}>{list.length}</RNText></View>
                  <ChevronDown size={16} color={C.mutedFg} style={{ transform: [{ rotate: open ? "180deg" : "0deg" }] }} />
                </Pressable>
                {open && <View style={k.auditGroupBody}><AuditRows entries={list} /></View>}
              </View>
            );
          })}
        </View>
      )}
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 16 }}>
        <ShieldCheck size={12} color={C.mutedFg} />
        <RNText style={k.auditFooter}>Full audit trail is captured and cannot be edited</RNText>
      </View>
    </View>
  );
}

// ── DelegationGate ───────────────────────────────────────────────────────────
export function DelegationGate({ open, delegated, onClose, onRequest, onProceed, busy }: {
  open: boolean; delegated: boolean; onClose: () => void; onRequest: () => void; onProceed: () => void; busy?: boolean;
}) {
  const rows = [
    { ok: delegated, icon: FileSignature, label: "Delegated by your organization", sub: delegated ? "Delegation log signed" : "Awaiting sign-off from the platform admin" },
    { ok: true, icon: GraduationCap, label: "Protocol training completed", sub: "Certificate on file" },
  ];
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
      <View style={k.centerOverlay}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <View style={k.dialog}>
          <View style={[k.dialogIcon, { backgroundColor: delegated ? "rgba(92,154,110,0.14)" : "rgba(216,154,60,0.16)" }]}>
            <Landmark size={22} color={delegated ? C.success : C.warning} />
          </View>
          <RNText style={k.dialogTitle}>{delegated ? "You're cleared to create trials" : "Delegation required"}</RNText>
          <RNText style={k.dialogBody}>
            {delegated ? "Delegation and training are both on file. You can create this trial." : "Creating trials needs delegation and completed training before you can proceed."}
          </RNText>
          <View style={{ marginTop: 14, gap: 8, width: "100%" }}>
            {rows.map((r) => (
              <View key={r.label} style={k.gateRow}>
                <View style={[k.gateRowIcon, { backgroundColor: r.ok ? "rgba(92,154,110,0.14)" : C.surface }]}>
                  <r.icon size={16} color={r.ok ? C.success : C.mutedFg} />
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <RNText style={k.gateRowLabel}>{r.label}</RNText>
                  <RNText style={k.gateRowSub}>{r.sub}</RNText>
                </View>
                <View style={[k.gateCheck, r.ok ? { backgroundColor: C.success } : { borderWidth: 1, borderColor: C.border }]}>
                  {r.ok && <Check size={12} color={C.primaryFg} />}
                </View>
              </View>
            ))}
          </View>
          <View style={{ marginTop: 18, gap: 10, width: "100%" }}>
            <PrimaryButton label={delegated ? "Continue to new trial" : "Request delegation"} loading={busy} onPress={delegated ? onProceed : onRequest} />
            <Pressable onPress={onClose} style={{ paddingVertical: 10, alignItems: "center" }}><RNText style={k.dialogCancelTxt}>Not now</RNText></Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

// ── TransferOwnershipSheet ───────────────────────────────────────────────────
// Backend records a PENDING transfer + notifies the successor, who accepts
// out-of-band. So the ceremony collects successor → reason → handover → review,
// then proposes it. We never fabricate the acceptance.
const TRANSFER_STEPS = ["Successor", "Reason", "Handover", "Propose"];
const REASONS = ["Resignation", "Role change", "Extended leave"];
export function TransferOwnershipSheet({ open, members, fromName, preset, onClose, onSubmit }: {
  open: boolean; members: OrgMember[]; fromName: string; preset?: OrgMember | null;
  onClose: () => void; onSubmit: (successorId: string, reason: string, handover: "deactivate" | "remove") => Promise<void>;
}) {
  const eligible = useMemo(() => members.filter((m) => m.status === "active" && !m.admin && !m.you && !m.id.startsWith("invite:")), [members]);
  const [step, setStep] = useState(0);
  const [pick, setPick] = useState<OrgMember | null>(null);
  const [reason, setReason] = useState(REASONS[0]);
  const [note, setNote] = useState("");
  const [handover, setHandover] = useState<"deactivate" | "remove">("deactivate");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setStep(0); setPick(preset ?? null); setReason(REASONS[0]); setNote(""); setHandover("deactivate"); setErr(null); setBusy(false); }
  }, [open, preset]);

  const reasonLine = `Role transfer due to ${reason.toLowerCase()}${note.trim() ? ` — ${note.trim()}` : ""}`;
  const canNext = step === 0 ? !!pick : true;

  const submit = async () => {
    if (!pick) return;
    setBusy(true); setErr(null);
    try {
      await onSubmit(pick.id, reasonLine, handover);
      onClose();
    } catch (e) {
      setErr(errMsg(e, "Couldn't propose the transfer."));
    } finally { setBusy(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Transfer ownership">
      <View style={{ flexDirection: "row", gap: 4, marginBottom: 16 }}>
        {TRANSFER_STEPS.map((s, i) => (
          <View key={s} style={{ flex: 1, alignItems: "center", gap: 4 }}>
            <View style={[k.stepDot, i < step ? { backgroundColor: C.accent } : i === step ? { backgroundColor: C.primary } : { backgroundColor: C.surface }]}>
              {i < step ? <Check size={12} color={C.primaryFg} /> : <RNText style={[k.stepDotTxt, i === step && { color: C.primaryFg }]}>{i + 1}</RNText>}
            </View>
            <RNText style={[k.stepLabel, i === step && { color: C.accent }]}>{s}</RNText>
          </View>
        ))}
      </View>

      {step === 0 && (
        <View style={{ gap: 8 }}>
          <RNText style={k.helpTxt}>Choose the active member who takes over as organization admin.</RNText>
          {eligible.length === 0 && <RNText style={k.centerMuted}>No eligible active members.</RNText>}
          {eligible.map((m) => {
            const on = pick?.id === m.id; const tone = avatarTone(m.name);
            return (
              <Pressable key={m.id} onPress={() => setPick(m)} style={[k.pickRow, on && { borderColor: C.accent, backgroundColor: "rgba(230,155,92,0.08)" }]}>
                <View style={[k.avatar, { backgroundColor: tone.bg }]}><RNText style={[k.avatarTxt, { color: tone.fg }]}>{initialsOf(m.name)}</RNText></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <RNText style={k.rowName} numberOfLines={1}>{stripTitle(m.name)}</RNText>
                  <RNText style={k.rowSub} numberOfLines={1}>{m.role}{m.site ? ` · ${m.site}` : ""}</RNText>
                </View>
                <View style={[k.radio, on && { borderColor: C.accent, backgroundColor: C.accent }]}>{on && <Check size={12} color={C.primaryFg} />}</View>
              </Pressable>
            );
          })}
        </View>
      )}

      {step === 1 && (
        <View style={{ gap: 12 }}>
          <RNText style={k.helpTxt}>The system records why ownership is moving. This lands in the audit trail.</RNText>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {REASONS.map((r) => (
              <Pressable key={r} onPress={() => setReason(r)} style={[k.chip, reason === r && k.chipActive]}>
                <RNText style={[k.chipTxt, reason === r && k.chipTxtActive]}>{r}</RNText>
              </Pressable>
            ))}
          </View>
          <Field label="Add context (optional)"><KitInput value={note} onChangeText={setNote} multiline placeholder="e.g. Last working day 30 June" /></Field>
          <View style={k.recordCard}>
            <RNText style={k.recordEyebrow}>WILL BE RECORDED AS</RNText>
            <RNText style={k.recordTxt}>“{reasonLine}”</RNText>
          </View>
        </View>
      )}

      {step === 2 && (
        <View style={{ gap: 10 }}>
          <RNText style={k.helpTxt}>What happens to your account, {stripTitle(fromName)}? Your admin role is removed either way.</RNText>
          {([
            { key: "deactivate", title: "Deactivate my account", sub: "Profile kept for records, sign-in disabled" },
            { key: "remove", title: "Remove me from the organization", sub: "Membership ends; history stays in the audit trail" },
          ] as const).map((o) => {
            const on = handover === o.key;
            return (
              <Pressable key={o.key} onPress={() => setHandover(o.key)} style={[k.pickRow, on && { borderColor: C.accent, backgroundColor: "rgba(230,155,92,0.08)" }]}>
                <View style={[k.avatar, { backgroundColor: on ? "rgba(230,155,92,0.16)" : C.surface, borderRadius: 12 }]}><UserMinus size={18} color={on ? C.accent : C.mutedFg} /></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <RNText style={k.rowName}>{o.title}</RNText>
                  <RNText style={k.rowSub}>{o.sub}</RNText>
                </View>
                <View style={[k.radio, on && { borderColor: C.accent, backgroundColor: C.accent }]}>{on && <Check size={12} color={C.primaryFg} />}</View>
              </Pressable>
            );
          })}
        </View>
      )}

      {step === 3 && pick && (
        <View style={{ gap: 12 }}>
          <View style={k.reviewCard}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 12 }}>
              <View style={[k.avatar, { opacity: 0.6, backgroundColor: avatarTone(fromName).bg }]}><RNText style={[k.avatarTxt, { color: avatarTone(fromName).fg }]}>{initialsOf(fromName)}</RNText></View>
              <ArrowRightLeft size={16} color={C.accent} />
              <View style={[k.avatar, { borderWidth: 2, borderColor: C.accent, backgroundColor: avatarTone(pick.name).bg }]}><RNText style={[k.avatarTxt, { color: avatarTone(pick.name).fg }]}>{initialsOf(pick.name)}</RNText></View>
            </View>
            <RNText style={k.reviewNames}>{stripTitle(fromName)} → {stripTitle(pick.name)}</RNText>
            <View style={{ borderTopWidth: 1, borderTopColor: C.border, marginTop: 12, paddingTop: 10, gap: 5 }}>
              <RNText style={k.reviewLine}><RNText style={k.reviewKey}>Reason: </RNText>{reasonLine}</RNText>
              <RNText style={k.reviewLine}><RNText style={k.reviewKey}>Outgoing admin: </RNText>{handover === "deactivate" ? "Account deactivated" : "Removed from organization"}</RNText>
              <RNText style={k.reviewLine}><RNText style={k.reviewKey}>Acceptance: </RNText>Pending — {stripTitle(pick.name)} must accept before it takes effect</RNText>
            </View>
          </View>
          {err && <RNText style={k.errText}>{err}</RNText>}
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 }}>
            <Stamp size={12} color={C.mutedFg} /><RNText style={k.auditFooter}>This transfer is written to the audit trail permanently</RNText>
          </View>
        </View>
      )}

      <View style={{ flexDirection: "row", gap: 10, marginTop: 18 }}>
        {step > 0 && <Pressable onPress={() => setStep(step - 1)} style={k.backBtn}><RNText style={k.backBtnTxt}>Back</RNText></Pressable>}
        <View style={{ flex: 1 }}>
          <PrimaryButton
            label={step === 3 ? "Propose transfer" : "Continue"}
            disabled={!canNext}
            loading={busy}
            onPress={() => { if (step < 3) setStep(step + 1); else submit(); }}
          />
        </View>
      </View>
    </Sheet>
  );
}

// ── TeamRoster ───────────────────────────────────────────────────────────────
/** Ownership action shown in the Organization Members area for the signed-in org admin. */
export function OwnershipTransferCard({ adminLabel = "Org Admin" }: {
  adminLabel?: string;
}) {
  const { user } = useAuth();
  const { orgId, loading: orgLoading, error: orgError } = useOrgContext();
  const { showToast, ToastView } = useToast();
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [membersError, setMembersError] = useState<string | null>(null);

  const loadMembers = useCallback(async () => {
    if (!orgId) return;
    setMembersError(null);
    try {
      const response = await api.get(`/org/${orgId}/members`);
      setMembers(Array.isArray(response.data) ? response.data : []);
    } catch (error) {
      setMembersError(errMsg(error, "Couldn't load eligible organization members."));
    }
  }, [orgId]);

  useEffect(() => {
    if (user?.org_admin && orgId) void loadMembers();
  }, [user?.org_admin, orgId, loadMembers]);

  if (!user?.org_admin) return null;

  const beginTransfer = () => {
    const error = orgError || membersError;
    if (error) { showToast(error); return; }
    if (orgLoading || !orgId) {
      showToast("Your organization is still loading. Please try again.");
      return;
    }
    setOpen(true);
  };

  const submitTransfer = async (
    successorId: string,
    reason: string,
    handover: "deactivate" | "remove",
  ) => {
    if (!orgId) throw new Error("Organization unavailable");
    await api.post(`/org/${orgId}/ownership-transfer`, {
      successor_id: successorId,
      reason,
      handover,
    });
    showToast("Ownership transfer proposed — awaiting acceptance");
    await loadMembers();
  };

  return (
    <>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Transfer organization ownership"
        testID="team-transfer-ownership"
        onPress={beginTransfer}
        style={({ pressed }) => [k.transferCard, pressed && { opacity: 0.82 }]}
      >
        <LinearGradient
          colors={[C.primary, C.primaryDeep] as any}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={StyleSheet.absoluteFill}
        />
        <View style={k.transferCardIcon}>
          <ArrowRightLeft size={20} color={C.primaryFg} />
        </View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <RNText style={k.transferCardTitle}>Transfer ownership</RNText>
          <RNText style={k.transferCardSub}>
            Hand the {adminLabel} role to another active member — reason, handover and audit included.
          </RNText>
        </View>
      </Pressable>
      <TransferOwnershipSheet
        open={open}
        members={members}
        fromName={user.full_name || "You"}
        onClose={() => setOpen(false)}
        onSubmit={submitTransfer}
      />
      {ToastView}
    </>
  );
}

export interface InviteConfig { roles: string[]; sites?: string[] }
export function TeamRoster({ members, sites, roleFilters, inviteConfig, accentColor = C.primary, allowAssignSite, showToast, onReload, onInvite, onDelete, onMakeAdmin, onAssignSite }: {
  members: OrgMember[];
  sites?: string[];
  roleFilters?: string[];
  inviteConfig: InviteConfig;
  accentColor?: string;
  allowAssignSite?: boolean;
  showToast: (m: string) => void;
  onReload: () => void;
  onInvite: (payload: { email: string; full_name: string; designation: string; role: string; site?: string }) => Promise<void>;
  onDelete: (m: OrgMember) => Promise<void>;
  onMakeAdmin: (m: OrgMember) => Promise<void>;
  onAssignSite: (m: OrgMember, site: string) => Promise<void>;
}) {
  const [siteFilter, setSiteFilter] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState<string | null>(null);
  const [invite, setInvite] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [assignFor, setAssignFor] = useState<OrgMember | null>(null);
  const [busy, setBusy] = useState(false);

  const visible = members.filter((m) => {
    if (m.status === "rejected") return false;
    const bySite = !siteFilter || m.site === siteFilter;
    const byRole = !roleFilter || m.role === roleFilter;
    return bySite && byRole;
  });

  const doDelete = (m: OrgMember) => setConfirm({
    title: "Deactivate organization member?",
    body: `${stripTitle(m.name)} will lose access to the organization and its trials. The record is retained.`,
    confirmLabel: "Deactivate organization member",
    onConfirm: async () => {
      setBusy(true);
      try { await onDelete(m); showToast(`${stripTitle(m.name)} deactivated`); setConfirm(null); onReload(); }
      catch (e) { showToast(errMsg(e, "Couldn't deactivate organization member")); }
      finally { setBusy(false); }
    },
  });

  const doMakeAdmin = (m: OrgMember) => setConfirm({
    title: "Grant org-admin?",
    body: `${stripTitle(m.name)} will be able to administer the whole organization.`,
    confirmLabel: "Grant admin",
    onConfirm: async () => {
      setBusy(true);
      try { await onMakeAdmin(m); showToast(`${stripTitle(m.name)} is now an org admin`); setConfirm(null); onReload(); }
      catch (e) { showToast(errMsg(e, "Couldn't grant admin")); }
      finally { setBusy(false); }
    },
  });

  return (
    <View>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <RNText style={k.rosterCount}>{visible.length} organization member{visible.length === 1 ? "" : "s"}</RNText>
        <Pressable onPress={() => setInvite(true)} style={[k.invitePill, { backgroundColor: accentColor }]}>
          <UserPlus size={14} color={C.primaryFg} /><RNText style={k.invitePillTxt}>Invite organization member</RNText>
        </Pressable>
      </View>

      {(sites || roleFilters) && (
        <View style={{ gap: 8, marginBottom: 12 }}>
          {sites && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              <FilterChip label="All sites" active={!siteFilter} onPress={() => setSiteFilter(null)} />
              {sites.map((s) => <FilterChip key={s} label={s} active={siteFilter === s} onPress={() => setSiteFilter(siteFilter === s ? null : s)} />)}
            </ScrollView>
          )}
          {roleFilters && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              <FilterChip label="All roles" active={!roleFilter} onPress={() => setRoleFilter(null)} />
              {roleFilters.map((r) => <FilterChip key={r} label={r} active={roleFilter === r} onPress={() => setRoleFilter(roleFilter === r ? null : r)} />)}
            </ScrollView>
          )}
        </View>
      )}

      <View style={{ gap: 8 }}>
        {visible.map((m) => {
          const tone = avatarTone(m.name);
          const actionable = !m.you && m.status === "active" && !m.admin && !m.id.startsWith("invite:");
          return (
            <View key={m.id} style={[k.memberRow, m.status === "deactivated" && { opacity: 0.55 }]}>
              <View style={[k.avatar, { backgroundColor: tone.bg }]}><RNText style={[k.avatarTxt, { color: tone.fg }]}>{initialsOf(m.name)}</RNText></View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                  <RNText style={k.rowName} numberOfLines={1}>{stripTitle(m.name) || m.email}</RNText>
                  {m.you && <View style={k.youPill}><RNText style={k.youPillTxt}>You</RNText></View>}
                  {m.admin && <View style={k.adminPill}><ShieldCheck size={10} color={C.accent} /><RNText style={k.adminPillTxt}>Admin</RNText></View>}
                </View>
                <RNText style={k.rowSub} numberOfLines={1}>{[m.role, m.department, m.site].filter(Boolean).join(" · ") || m.email}</RNText>
              </View>
              <MemberStatusPill status={m.status} />
              {actionable && (
                <View style={{ flexDirection: "row", gap: 2 }}>
                  {allowAssignSite && <Pressable onPress={() => setAssignFor(m)} hitSlop={6} style={k.rowIconBtn}><MapPin size={16} color={C.violet} /></Pressable>}
                  <Pressable onPress={() => doMakeAdmin(m)} hitSlop={6} style={k.rowIconBtn}><ShieldCheck size={16} color={C.accent} /></Pressable>
                  <Pressable onPress={() => doDelete(m)} hitSlop={6} style={k.rowIconBtn}><Trash2 size={16} color={C.destructive} /></Pressable>
                </View>
              )}
            </View>
          );
        })}
        {visible.length === 0 && <EmptyCard icon={UserPlus} title="No organization members yet" subtitle="Invite your first organization member to get started." />}
      </View>

      {invite && (
        <InviteMemberSheet
          config={inviteConfig}
          onClose={() => setInvite(false)}
          onSend={async (payload) => { await onInvite(payload); showToast(`Invite sent to ${payload.email}`); setInvite(false); onReload(); }}
        />
      )}
      {assignFor && sites && (
        <AssignSiteSheet
          member={assignFor} sites={sites}
          onClose={() => setAssignFor(null)}
          onAssign={async (site) => { await onAssignSite(assignFor, site); showToast(`${stripTitle(assignFor.name)} assigned to ${site}`); setAssignFor(null); onReload(); }}
        />
      )}
      <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} busy={busy} />
    </View>
  );
}

function FilterChip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={[k.filterChip, active && { backgroundColor: C.primary, borderColor: C.primary }]}>
      <RNText style={[k.filterChipTxt, active && { color: C.primaryFg }]}>{label}</RNText>
    </Pressable>
  );
}

function InviteMemberSheet({ config, onClose, onSend }: { config: InviteConfig; onClose: () => void; onSend: (p: { email: string; full_name: string; designation: string; role: string; site?: string }) => Promise<void> }) {
  const [name, setName] = useState("");
  const [designation, setDesignation] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState(config.roles[0]);
  const [site, setSite] = useState(config.sites?.[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const valid = /\S+@\S+\.\S+/.test(email);

  const submit = async () => {
    if (!valid) { setErr("A valid email is required"); return; }
    setBusy(true); setErr(null);
    try { await onSend({ email: email.trim().toLowerCase(), full_name: name.trim(), designation: designation.trim(), role, site: config.sites ? site : undefined }); }
    catch (e) { setErr(errMsg(e, "Couldn't send the invite")); }
    finally { setBusy(false); }
  };

  return (
    <Sheet open onClose={onClose} title="Invite organization member">
      <View style={{ gap: 12 }}>
        <Field label="Full name"><KitInput value={name} onChangeText={(v: string) => setName(sanitizeName(v))} placeholder="Enter member's name" /></Field>
        <Field label="Designation"><KitInput value={designation} onChangeText={(v: string) => setDesignation(sanitizeDesignation(v))} placeholder="e.g. Research Associate" /></Field>
        <Field label="Email ID" required><KitInput value={email} onChangeText={setEmail} placeholder="member@example.com" keyboardType="email-address" autoCapitalize="none" /></Field>
        <Field label="Role">
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {config.roles.map((r) => (
              <Pressable key={r} onPress={() => setRole(r)} style={[k.chip, role === r && k.chipActive]}>
                <RNText style={[k.chipTxt, role === r && k.chipTxtActive]}>{r}</RNText>
              </Pressable>
            ))}
          </View>
        </Field>
        {config.sites && (
          <Field label="Site" required>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {config.sites.map((s) => (
                <Pressable key={s} onPress={() => setSite(s)} style={[k.chip, site === s && k.chipActive]}>
                  <RNText style={[k.chipTxt, site === s && k.chipTxtActive]}>{s}</RNText>
                </Pressable>
              ))}
            </View>
          </Field>
        )}
        {err && <RNText style={k.errText}>{err}</RNText>}
        <PrimaryButton label="Send invite" disabled={!valid} loading={busy} onPress={submit} />
      </View>
    </Sheet>
  );
}

function AssignSiteSheet({ member, sites, onClose, onAssign }: { member: OrgMember; sites: string[]; onClose: () => void; onAssign: (site: string) => Promise<void> }) {
  const [site, setSite] = useState(member.site || sites[0] || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    if (!site) return;
    setBusy(true); setErr(null);
    try { await onAssign(site); } catch (e) { setErr(errMsg(e, "Couldn't assign site")); } finally { setBusy(false); }
  };
  return (
    <Sheet open onClose={onClose} title={`Assign ${stripTitle(member.name)}`}>
      <View style={{ gap: 12 }}>
        <RNText style={k.helpTxt}>Move this member to any hospital in your network.</RNText>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          {sites.map((s) => (
            <Pressable key={s} onPress={() => setSite(s)} style={[k.chip, site === s && k.chipActive]}>
              <RNText style={[k.chipTxt, site === s && k.chipTxtActive]}>{s}</RNText>
            </Pressable>
          ))}
        </View>
        {err && <RNText style={k.errText}>{err}</RNText>}
        <PrimaryButton label="Assign site" disabled={!site} loading={busy} onPress={submit} bg={C.violet} gradient={false} />
      </View>
    </Sheet>
  );
}

// ── styles ───────────────────────────────────────────────────────────────────
// ── TrialAdminActions ────────────────────────────────────────────────────────
// Permission-gated owner actions on an org-console trial card: Edit (PATCH
// /org/{orgId}/trials/{id}), Archive/Unarchive (POST …/archive) and protocol
// Documents & version history (deep-links into the trial-summary record).
// Renders nothing when the backend grants none of the three permissions.
const TRIAL_STATUSES = ["active", "completed", "terminated"] as const;

export function TrialAdminActions({ trial, orgId, showToast, onChanged }: {
  trial: OrgTrial; orgId: string; showToast: (msg: string) => void; onChanged: () => void;
}) {
  const router = useRouter();
  const perms = trial.permissions || {};
  const [editOpen, setEditOpen] = useState(false);
  const [form, setForm] = useState({ title: "", duration: "", target: "", recruitment: "", status: "active" });
  const [saving, setSaving] = useState(false);
  const [editErr, setEditErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [busy, setBusy] = useState(false);

  if (!perms.canEdit && !perms.canArchive && !perms.canManageDocuments) return null;

  const openEdit = () => {
    setForm({
      title: trial.title || "",
      duration: trial.duration || "",
      target: typeof (trial.target ?? trial.target_enrollment) === "number" ? String(trial.target ?? trial.target_enrollment) : "",
      recruitment: trial.recruitment_status || "",
      status: trial.status || "active",
    });
    setEditErr(null);
    setEditOpen(true);
  };

  const saveEdit = async () => {
    const target = form.target.trim();
    if (target && (!/^\d+$/.test(target))) { setEditErr("Target enrollment must be a whole number"); return; }
    if (!form.title.trim()) { setEditErr("Title is required"); return; }
    setSaving(true); setEditErr(null);
    try {
      const body: Record<string, unknown> = { title: form.title.trim(), status: form.status };
      if (form.duration.trim()) body.duration = form.duration.trim();
      if (form.recruitment.trim()) body.recruitment_status = form.recruitment.trim();
      if (target) body.target_enrollment = Number(target);
      await api.patch(`/org/${orgId}/trials/${trial.id}`, body);
      setEditOpen(false);
      showToast("Trial updated");
      onChanged();
    } catch (e) {
      setEditErr(errMsg(e, "Couldn't save the trial changes"));
    } finally { setSaving(false); }
  };

  const toggleArchive = () => {
    const archiving = !trial.archived;
    setConfirm({
      title: archiving ? "Archive this trial?" : "Restore this trial?",
      body: archiving
        ? `${trial.protocol_id || trial.title || "This trial"} stays readable but is locked against edits until it is restored. The action is audited.`
        : `${trial.protocol_id || trial.title || "This trial"} becomes editable again. The action is audited.`,
      confirmLabel: archiving ? "Archive trial" : "Restore trial",
      onConfirm: async () => {
        setBusy(true);
        try {
          await api.post(`/org/${orgId}/trials/${trial.id}/archive`, { archived: archiving });
          setConfirm(null);
          showToast(archiving ? "Trial archived" : "Trial restored");
          onChanged();
        } catch (e) {
          setConfirm(null);
          showToast(errMsg(e, "Couldn't change the archive state"));
        } finally { setBusy(false); }
      },
    });
  };

  const actionPill = (label: string, Icon: any, onPress: () => void, tone = C.primary, disabled = false) => (
    <Pressable
      key={label}
      testID={`trial-action-${label.toLowerCase().replace(/\s/g, "-")}-${trial.id}`}
      onPress={disabled ? undefined : onPress}
      style={{
        flexDirection: "row", alignItems: "center", gap: 5,
        paddingHorizontal: 10, height: 30, borderRadius: 999,
        borderWidth: 1, borderColor: disabled ? C.border : tone + "55",
        backgroundColor: disabled ? C.surface : tone + "10",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <Icon size={13} color={disabled ? C.mutedFg : tone} />
      <RNText style={{ fontFamily: fonts.bold, fontSize: 11, color: disabled ? C.mutedFg : tone }}>{label}</RNText>
    </Pressable>
  );

  return (
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
      {perms.canEdit && actionPill("Edit", PenLine, openEdit, C.primary, !!trial.archived)}
      {perms.canArchive && (trial.archived
        ? actionPill("Unarchive", ArchiveRestore, toggleArchive, C.success)
        : actionPill("Archive", Archive, toggleArchive, C.warning))}
      {perms.canManageDocuments && actionPill(
        "Documents & versions", FolderOpen,
        () => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } }),
        C.info,
      )}

      <Sheet open={editOpen} onClose={() => setEditOpen(false)} title={`Edit ${trial.protocol_id || "trial"}`}>
        <View style={{ gap: 14 }}>
          <Field label="Title" required>
            <KitInput value={form.title} onChangeText={(v) => setForm({ ...form, title: v })} placeholder="Trial title" />
          </Field>
          <Field label="Duration">
            <KitInput value={form.duration} onChangeText={(v) => setForm({ ...form, duration: v })} placeholder="e.g. 24 months" />
          </Field>
          <Field label="Target enrollment">
            <KitInput value={form.target} onChangeText={(v) => setForm({ ...form, target: v })} placeholder="e.g. 120" keyboardType="number-pad" />
          </Field>
          <Field label="Recruitment status">
            <KitInput value={form.recruitment} onChangeText={(v) => setForm({ ...form, recruitment: v })} placeholder="e.g. recruiting" />
          </Field>
          <Field label="Trial status">
            <View style={{ flexDirection: "row", gap: 8 }}>
              {TRIAL_STATUSES.map((s) => {
                const on = form.status === s;
                return (
                  <Pressable key={s} onPress={() => setForm({ ...form, status: s })}
                    style={{ paddingHorizontal: 12, height: 32, borderRadius: 999, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: on ? C.primary : C.border, backgroundColor: on ? "rgba(166,33,63,0.08)" : C.card }}>
                    <RNText style={{ fontFamily: fonts.semibold, fontSize: 12, color: on ? C.primary : C.mutedFg, textTransform: "capitalize" }}>{s}</RNText>
                  </Pressable>
                );
              })}
            </View>
          </Field>
          {editErr && <RNText style={{ color: C.destructive, fontFamily: fonts.semibold, fontSize: 12 }}>{editErr}</RNText>}
          <PrimaryButton label="Save changes" loading={saving} onPress={saveEdit} />
        </View>
      </Sheet>

      <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} busy={busy} />
    </View>
  );
}

export const k = StyleSheet.create({
  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },

  sheetOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.background, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 18, paddingTop: 16, maxHeight: "88%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 14 },
  sheetTitle: { fontFamily: fonts.display, fontSize: 20, color: C.foreground },
  sheetClose: { width: 34, height: 34, borderRadius: 17, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  helpTxt: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, lineHeight: 17 },
  centerMuted: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, textAlign: "center", paddingVertical: 24 },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  primaryBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  primaryBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },
  backBtn: { paddingHorizontal: 22, paddingVertical: 14, borderRadius: 999, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  backBtnTxt: { fontFamily: fonts.bold, fontSize: 14, color: C.mutedFg },

  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, paddingHorizontal: 16, paddingVertical: 16 },
  loadingTxt: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, marginTop: 12 },
  errIcon: { width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },
  emptyIcon: { width: 48, height: 48, borderRadius: 24, backgroundColor: C.surface, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  emptyTitle: { fontFamily: fonts.heading, fontSize: 15, color: C.foreground },
  emptySub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 4, textAlign: "center", paddingHorizontal: 16, lineHeight: 17 },

  transferCard: { flexDirection: "row", alignItems: "center", gap: 14, borderRadius: 22, padding: 16, overflow: "hidden", marginTop: 16, marginBottom: 2 },
  transferCardIcon: { width: 44, height: 44, borderRadius: 16, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  transferCardTitle: { fontFamily: fonts.heading, fontSize: 15, color: C.primaryFg },
  transferCardSub: { fontFamily: fonts.regular, fontSize: 11, color: W.w70, marginTop: 2, lineHeight: 15 },

  centerOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.50)", alignItems: "center", justifyContent: "center", paddingHorizontal: 28 },
  dialog: { width: "100%", maxWidth: 340, backgroundColor: C.background, borderRadius: 24, padding: 20, alignItems: "center" },
  dialogIcon: { width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  dialogTitle: { fontFamily: fonts.heading, fontSize: 18, color: C.foreground, textAlign: "center" },
  dialogBody: { fontFamily: fonts.regular, fontSize: 13, color: C.mutedFg, textAlign: "center", marginTop: 6, lineHeight: 19 },
  dialogDanger: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center" },
  dialogDangerTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.destructiveFg },
  dialogCancelTxt: { fontFamily: fonts.semibold, fontSize: 14, color: C.mutedFg },

  header: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 20, borderBottomLeftRadius: 28, borderBottomRightRadius: 28, overflow: "hidden" },
  headerGlow: { position: "absolute", right: -40, top: -40, width: 150, height: 150, borderRadius: 75, opacity: 0.6 },
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  roleBadge: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, height: 28, borderRadius: 999, backgroundColor: W.w15, borderWidth: 1, borderColor: W.w20 },
  roleBadgeTxt: { color: C.primaryFg, fontFamily: fonts.semibold, fontSize: 11 },
  headerEyebrow: { color: W.w60, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, marginTop: 12 },
  headerTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 23, marginTop: 2 },
  headerNote: { color: W.w75, fontFamily: fonts.regular, fontSize: 12, marginTop: 6 },
  pulseTile: { minWidth: 82, backgroundColor: W.w10, borderRadius: 16, borderWidth: 1, borderColor: W.w15, paddingVertical: 10, paddingHorizontal: 12, alignItems: "center" },
  pulseValue: { color: C.primaryFg, fontFamily: fonts.heading, fontSize: 18, fontVariant: ["tabular-nums"] },
  pulseLabel: { color: W.w60, fontFamily: fonts.regular, fontSize: 9, marginTop: 3, textAlign: "center" },

  deck: { flexDirection: "row", gap: 6, backgroundColor: C.card, borderRadius: 999, borderWidth: 1, borderColor: C.border, padding: 4 },
  deckTab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, paddingVertical: 9, borderRadius: 999 },
  deckTabTxt: { fontFamily: fonts.semibold, fontSize: 12, color: C.mutedFg },
  deckCount: { fontFamily: fonts.medium, fontSize: 10, color: C.mutedFg, fontVariant: ["tabular-nums"] },

  statusPill: { flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 8, height: 20, borderRadius: 10, justifyContent: "center" },
  statusPillTxt: { fontFamily: fonts.bold, fontSize: 9 },

  miniTab: { paddingHorizontal: 14, height: 30, borderRadius: 999, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: "center", justifyContent: "center" },
  miniTabTxt: { fontFamily: fonts.semibold, fontSize: 11, color: C.mutedFg },

  auditRow: { flexDirection: "row", gap: 12, alignItems: "flex-start", backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  auditDot: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center" },
  auditAction: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  auditDetail: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2, lineHeight: 16 },
  auditMeta: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 4, opacity: 0.8 },
  auditGroup: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, overflow: "hidden" },
  auditGroupHead: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14 },
  auditGroupIcon: { width: 36, height: 36, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  auditGroupTitle: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  auditGroupSub: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 1 },
  auditGroupCount: { minWidth: 24, height: 24, borderRadius: 12, backgroundColor: C.surface, alignItems: "center", justifyContent: "center", paddingHorizontal: 6 },
  auditGroupCountTxt: { fontFamily: fonts.bold, fontSize: 11, color: C.mutedFg, fontVariant: ["tabular-nums"] },
  auditGroupBody: { borderTopWidth: 1, borderTopColor: C.border, backgroundColor: C.surface, padding: 12 },
  auditFooter: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg },

  gateRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 10 },
  gateRowIcon: { width: 32, height: 32, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  gateRowLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  gateRowSub: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, marginTop: 1 },
  gateCheck: { width: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center" },

  stepDot: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  stepDotTxt: { fontFamily: fonts.bold, fontSize: 11, color: C.mutedFg },
  stepLabel: { fontFamily: fonts.semibold, fontSize: 8, color: C.mutedFg, letterSpacing: 0.4, textTransform: "uppercase" },

  chip: { paddingHorizontal: 14, height: 34, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  chipActive: { backgroundColor: C.primary, borderColor: C.primary },
  chipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  chipTxtActive: { color: C.primaryFg },

  recordCard: { borderRadius: 12, borderWidth: 1, borderColor: "rgba(230,155,92,0.40)", borderStyle: "dashed", backgroundColor: "rgba(230,155,92,0.06)", padding: 12 },
  recordEyebrow: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1.2, color: C.accent },
  recordTxt: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, fontStyle: "italic", marginTop: 4, lineHeight: 18 },

  reviewCard: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 16 },
  reviewNames: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground, textAlign: "center", marginTop: 12 },
  reviewLine: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, lineHeight: 16 },
  reviewKey: { fontFamily: fonts.semibold, color: C.foreground },

  pickRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  avatar: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  avatarTxt: { fontFamily: fonts.bold, fontSize: 13 },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 1 },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },

  rosterCount: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.8, color: C.mutedFg, textTransform: "uppercase" },
  invitePill: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, height: 34, borderRadius: 999 },
  invitePillTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.primaryFg },
  filterChip: { paddingHorizontal: 12, height: 30, borderRadius: 999, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, alignItems: "center", justifyContent: "center" },
  filterChipTxt: { fontFamily: fonts.semibold, fontSize: 11, color: C.mutedFg },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  youPill: { paddingHorizontal: 7, height: 18, borderRadius: 9, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  youPillTxt: { fontFamily: fonts.semibold, fontSize: 9, color: C.mutedFg },
  adminPill: { flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 7, height: 18, borderRadius: 9, backgroundColor: "rgba(230,155,92,0.16)", justifyContent: "center" },
  adminPillTxt: { fontFamily: fonts.semibold, fontSize: 9, color: C.accent },
  rowIconBtn: { width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center" },
});
