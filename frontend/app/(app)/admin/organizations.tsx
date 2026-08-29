// ADM-03 — Organization master list.
//
// Live, admin-gated endpoints (backend/admin_routes.py · ORGANIZATIONS):
//   list ............ GET   /admin/organizations           {…, users, trials, status}
//   create .......... POST  /admin/organizations           {name, type, address, contact, email, website}
//   patch ........... PATCH /admin/organizations/{id}       {name?, type?, address?, …, status?}
//   merge ........... POST  /admin/organizations/{id}/merge {target_org_id, justification(≥10)} — irreversible
//   duplicates ...... GET   /admin/organizations/duplicates → [{key, organizations[]}]
//   name-requests ... GET   /admin/organizations/name-requests
//   approve name .... POST  /admin/organizations/name-requests/{id}/approve {finalName}
//   reject name ..... POST  /admin/organizations/name-requests/{id}/reject  {reason}
//
// Every row is a real record; nothing is fabricated. Search/type filters run
// client-side over the fetched list for snappiness.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Modal, Animated, Platform, KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, Search, Building2, Plus, X, AlertTriangle, MapPin, Phone, Mail,
  Globe, Users, FileText, ChevronRight, Merge, Ban, CheckCircle2, Pencil, ArrowRight,
  Check, Layers,
} from "lucide-react-native";
import { useLocalSearchParams } from "expo-router";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";
import { sanitizeAddress, sanitizeDigits, sanitizeOrgName } from "@/src/lib/validators";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };
const ORG_TYPES = ["sponsor", "cro", "smo", "site"] as const;
type OrgType = (typeof ORG_TYPES)[number];

type Org = {
  id: string; name?: string; type?: OrgType; address?: string; contact?: string;
  email?: string; website?: string; status?: string; users?: number; trials?: number;
  created_at?: string; merged_into?: string;
};
const EMPTY_ORG_FORM = {
  name: "", type: "site" as OrgType, address: "", contact: "", email: "", website: "",
};
type NameRequest = {
  id: string; org_id?: string; current_name?: string; requested_name?: string;
  requested_by?: string; status?: string; created_at?: string;
};
type DuplicateGroup = { key: string; organizations: Org[] };

const TYPE_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All" }, { key: "sponsor", label: "Sponsor" }, { key: "cro", label: "CRO" },
  { key: "smo", label: "SMO" }, { key: "site", label: "Site" },
];
const TABS: { key: "orgs" | "requests" | "duplicates"; label: string }[] = [
  { key: "orgs", label: "Organizations" }, { key: "requests", label: "Name requests" },
  { key: "duplicates", label: "Duplicates" },
];

const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

function typeColors(t?: string): { fg: string; bg: string } {
  switch (t) {
    case "sponsor": return { fg: C.violet, bg: "rgba(142,91,180,0.14)" };
    case "cro": return { fg: C.info, bg: "rgba(123,107,184,0.14)" };
    case "smo": return { fg: C.accentFg, bg: "rgba(230,155,92,0.18)" };
    case "site": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function statusColors(status?: string): { fg: string; bg: string } {
  switch (status) {
    case "active": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "suspended": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "merged": return { fg: C.mutedFg, bg: C.surface };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}

export default function AdminOrganizations() {
  const { open } = useAdminDrawer();
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [requests, setRequests] = useState<NameRequest[]>([]);
  const [duplicates, setDuplicates] = useState<DuplicateGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [tab, setTab] = useState<"orgs" | "requests" | "duplicates">("orgs");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");

  const [selected, setSelected] = useState<Org | null>(null);
  const [editOrg, setEditOrg] = useState<Org | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [mergeSource, setMergeSource] = useState<Org | null>(null);
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
      const [o, r, d] = await Promise.all([
        api.get("/admin/organizations"),
        api.get("/admin/organizations/name-requests"),
        api.get("/admin/organizations/duplicates"),
      ]);
      setOrgs(Array.isArray(o.data) ? o.data : []);
      setRequests(Array.isArray(r.data) ? r.data : []);
      setDuplicates(Array.isArray(d.data) ? d.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load organizations. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // Global-search deep link: /admin/organizations?focus=<id> opens that record.
  const { focus } = useLocalSearchParams<{ focus?: string }>();
  const consumedFocus = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || typeof focus !== "string" || focus === consumedFocus.current) return;
    const hit = orgs.find((o) => o.id === focus);
    if (hit) { consumedFocus.current = focus; setTab("orgs"); setSelected(hit); }
  }, [focus, orgs]);

  const pendingRequests = useMemo(() => requests.filter((r) => (r.status || "pending") === "pending"), [requests]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return orgs.filter((o) => {
      const matchesSearch = !q ||
        (o.name || "").toLowerCase().includes(q) ||
        (o.address || "").toLowerCase().includes(q) ||
        (o.email || "").toLowerCase().includes(q);
      const matchesType = typeFilter === "all" || o.type === typeFilter;
      return matchesSearch && matchesType;
    });
  }, [orgs, search, typeFilter]);

  const tiles = useMemo(() => [
    { label: "Total", value: orgs.length },
    { label: "Sponsor", value: orgs.filter((o) => o.type === "sponsor").length },
    { label: "CRO", value: orgs.filter((o) => o.type === "cro").length },
    { label: "SMO", value: orgs.filter((o) => o.type === "smo").length },
    { label: "Sites", value: orgs.filter((o) => o.type === "site").length },
    { label: "Dup. groups", value: duplicates.length },
    { label: "Name reqs", value: pendingRequests.length },
    { label: "Linked users", value: orgs.reduce((n, o) => n + (o.users || 0), 0) },
  ], [orgs, duplicates, pendingRequests]);

  // ── Actions ──
  const setStatus = async (o: Org, status: "active" | "suspended") => {
    setBusy(true);
    try {
      await api.patch(`/admin/organizations/${o.id}`, { status });
      showToast(`${o.name || "Organization"} ${status === "suspended" ? "suspended" : "activated"}`);
      setSelected(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't update status"));
    } finally { setBusy(false); }
  };

  const approveName = async (r: NameRequest, finalName: string) => {
    if (!finalName.trim()) { showToast("A final name is required"); return; }
    setBusy(true);
    try {
      await api.post(`/admin/organizations/name-requests/${r.id}/approve`, { finalName: finalName.trim() });
      showToast(`Name correction applied: ${finalName.trim()}`);
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't approve request"));
    } finally { setBusy(false); }
  };
  const rejectName = async (r: NameRequest, reason: string) => {
    setBusy(true);
    try {
      await api.post(`/admin/organizations/name-requests/${r.id}/reject`, { reason: reason.trim() || "Rejected by admin" });
      showToast("Request rejected");
      await load();
    } catch (e) {
      showToast(errMsg(e, "Couldn't reject request"));
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
          <Loading label="Loading organizations…" />
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

            <View style={st.tabRow}>
              {TABS.map((t) => (
                <Pressable key={t.key} onPress={() => setTab(t.key)} style={[st.tab, tab === t.key && st.tabActive]}>
                  <RNText style={[st.tabTxt, tab === t.key && st.tabTxtActive]}>{t.label}</RNText>
                  {t.key === "requests" && pendingRequests.length > 0 && (
                    <View style={st.tabDot}><RNText style={st.tabDotTxt}>{pendingRequests.length}</RNText></View>
                  )}
                </Pressable>
              ))}
            </View>

            {tab === "orgs" && (
              <>
                <SearchBar value={search} onChange={setSearch} placeholder="Search name, address or email…" />
                <ChipRow label="ENTITY TYPE" chips={TYPE_FILTERS} value={typeFilter} onChange={setTypeFilter} />
                <RNText style={st.countLine}>{filtered.length} of {orgs.length} organizations</RNText>
                {filtered.length === 0 ? (
                  <EmptyCard message="No organizations match the current filters." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {filtered.map((o) => {
                      const tc = typeColors(o.type); const sc = statusColors(o.status);
                      return (
                        <Pressable key={o.id} testID={`org-${o.id}`} onPress={() => setSelected(o)} style={st.row}>
                          <View style={st.orgIcon}><Building2 size={20} color={C.primary} /></View>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <RNText style={st.rowName} numberOfLines={1}>{o.name || "—"}</RNText>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 4 }}>
                              <View style={[st.typePill, { backgroundColor: tc.bg }]}>
                                <RNText style={[st.typePillTxt, { color: tc.fg }]}>{(o.type || "—").toUpperCase()}</RNText>
                              </View>
                              <RNText style={st.rowSub} numberOfLines={1}>{o.users || 0} users · {o.trials || 0} trials</RNText>
                            </View>
                          </View>
                          <View style={{ alignItems: "flex-end", gap: 6 }}>
                            <View style={[st.badge, { backgroundColor: sc.bg }]}>
                              <RNText style={[st.badgeTxt, { color: sc.fg }]}>{o.status || "active"}</RNText>
                            </View>
                            <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                          </View>
                        </Pressable>
                      );
                    })}
                  </View>
                )}
              </>
            )}

            {tab === "requests" && (
              <View style={{ marginTop: 6 }}>
                <View style={st.infoBanner}>
                  <AlertTriangle size={15} color={C.info} />
                  <RNText style={st.infoBannerTxt}>Approving a correction renames the organization and re-points every linked member.</RNText>
                </View>
                {pendingRequests.length === 0 ? (
                  <EmptyCard message="No pending name-correction requests." />
                ) : (
                  <View style={{ gap: 10, marginTop: 10 }}>
                    {pendingRequests.map((r) => (
                      <NameRequestCard key={r.id} req={r} busy={busy}
                        onApprove={(final) => approveName(r, final)} onReject={(reason) => rejectName(r, reason)} />
                    ))}
                  </View>
                )}
              </View>
            )}

            {tab === "duplicates" && (
              <View style={{ marginTop: 6 }}>
                <View style={st.infoBanner}>
                  <Layers size={15} color={C.info} />
                  <RNText style={st.infoBannerTxt}>Groups whose normalized names collide are likely duplicates. Merge to consolidate members and trials.</RNText>
                </View>
                {duplicates.length === 0 ? (
                  <EmptyCard message="No duplicate organizations detected." />
                ) : (
                  <View style={{ gap: 12, marginTop: 10 }}>
                    {duplicates.map((g) => (
                      <View key={g.key} style={st.dupCard}>
                        <RNText style={st.dupTitle}>{g.organizations.length} possible duplicates</RNText>
                        <View style={{ gap: 8, marginTop: 8 }}>
                          {g.organizations.map((o) => {
                            const tc = typeColors(o.type);
                            return (
                              <Pressable key={o.id} onPress={() => setSelected(o)} style={st.dupRow}>
                                <View style={{ flex: 1, minWidth: 0 }}>
                                  <RNText style={st.dupName} numberOfLines={1}>{o.name}</RNText>
                                  <RNText style={st.rowSub}>{o.users || 0} users · {o.trials || 0} trials</RNText>
                                </View>
                                <View style={[st.typePill, { backgroundColor: tc.bg }]}>
                                  <RNText style={[st.typePillTxt, { color: tc.fg }]}>{(o.type || "—").toUpperCase()}</RNText>
                                </View>
                              </Pressable>
                            );
                          })}
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

      <OrgDetailSheet
        org={selected} busy={busy}
        onClose={() => setSelected(null)}
        onEdit={(o) => { setSelected(null); setEditOrg(o); }}
        onMerge={(o) => { setSelected(null); setMergeSource(o); }}
        onSuspend={(o) => setStatus(o, "suspended")}
        onActivate={(o) => setStatus(o, "active")}
      />
      <OrgFormSheet
        open={addOpen || !!editOrg} org={editOrg}
        onClose={() => { setAddOpen(false); setEditOrg(null); }}
        onSaved={(msg) => { setAddOpen(false); setEditOrg(null); showToast(msg); load(); }}
      />
      <MergeSheet
        source={mergeSource} orgs={orgs}
        onClose={() => setMergeSource(null)}
        onDone={(msg) => { setMergeSource(null); showToast(msg); load(); }}
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
            <RNText style={st.heroTitle} numberOfLines={1}>Organizations</RNText>
          </View>
          <Pressable testID="orgs-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>The organization master list — duplicates, merges and name-correction requests.</RNText>
        <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
          <Pressable testID="orgs-add" onPress={onAdd} style={st.heroBtnSolid}>
            <Plus size={15} color={C.primary} /><RNText style={st.heroBtnSolidTxt}>Add organization</RNText>
          </Pressable>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

// ── Name-request card ────────────────────────────────────────────────────────
function NameRequestCard({ req, busy, onApprove, onReject }: {
  req: NameRequest; busy: boolean; onApprove: (final: string) => void; onReject: (reason: string) => void;
}) {
  const [final, setFinal] = useState(req.requested_name || "");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  return (
    <View style={st.reqCard}>
      <RNText style={st.reqBy}>{req.requested_by || "Unknown"} · {fmtDate(req.created_at)}</RNText>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <RNText style={st.reqOld} numberOfLines={1}>{req.current_name || "—"}</RNText>
        <ArrowRight size={13} color={C.mutedFg} />
        <RNText style={st.reqNew} numberOfLines={1}>{req.requested_name || "—"}</RNText>
      </View>
      <RNText style={st.fieldLabel}>Final name</RNText>
      <Input value={final} onChangeText={(v: string) => setFinal(sanitizeOrgName(v))} placeholder="Approved organization name" />
      {rejecting && (
        <>
          <RNText style={st.fieldLabel}>Rejection reason</RNText>
          <Input value={reason} onChangeText={setReason} placeholder="Why is this correction rejected?" multiline />
        </>
      )}
      <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
        {rejecting ? (
          <>
            <Pressable onPress={() => setRejecting(false)} style={[st.smallBtn, { backgroundColor: C.surface, flex: 1 }]}>
              <RNText style={[st.smallBtnTxt, { color: C.mutedFg }]}>Back</RNText>
            </Pressable>
            <Pressable onPress={busy ? undefined : () => onReject(reason)} style={[st.smallBtn, { backgroundColor: C.destructive, flex: 1, opacity: busy ? 0.5 : 1 }]}>
              <X size={14} color={C.destructiveFg} /><RNText style={[st.smallBtnTxt, { color: C.destructiveFg }]}>Confirm reject</RNText>
            </Pressable>
          </>
        ) : (
          <>
            <Pressable onPress={busy ? undefined : () => onApprove(final)} style={[st.smallBtn, { backgroundColor: C.success, flex: 1, opacity: busy ? 0.5 : 1 }]}>
              <Check size={14} color={C.successFg} /><RNText style={[st.smallBtnTxt, { color: C.successFg }]}>Approve</RNText>
            </Pressable>
            <Pressable onPress={() => setRejecting(true)} style={[st.smallBtn, { backgroundColor: C.card, borderWidth: 1, borderColor: "rgba(192,57,43,0.30)", flex: 1 }]}>
              <RNText style={[st.smallBtnTxt, { color: C.destructive }]}>Reject</RNText>
            </Pressable>
          </>
        )}
      </View>
    </View>
  );
}

// ── Detail sheet ─────────────────────────────────────────────────────────────
function OrgDetailSheet({ org, busy, onClose, onEdit, onMerge, onSuspend, onActivate }: {
  org: Org | null; busy: boolean; onClose: () => void; onEdit: (o: Org) => void;
  onMerge: (o: Org) => void; onSuspend: (o: Org) => void; onActivate: (o: Org) => void;
}) {
  return (
    <Sheet open={!!org} onClose={onClose} title="Organization details">
      {org && (
        <View style={{ gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
            <View style={[st.orgIcon, { width: 56, height: 56, borderRadius: 18 }]}><Building2 size={26} color={C.primary} /></View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.sheetName} numberOfLines={2}>{org.name || "—"}</RNText>
              {(() => { const tc = typeColors(org.type); return (
                <View style={[st.typePill, { backgroundColor: tc.bg, alignSelf: "flex-start", marginTop: 6 }]}>
                  <RNText style={[st.typePillTxt, { color: tc.fg }]}>{(org.type || "—").toUpperCase()}</RNText>
                </View>
              ); })()}
            </View>
          </View>

          {org.status === "merged" && (
            <View style={st.infoBanner}>
              <AlertTriangle size={15} color={C.mutedFg} />
              <RNText style={st.infoBannerTxt}>This organization has been merged and is archived.</RNText>
            </View>
          )}

          <View style={{ flexDirection: "row", gap: 10 }}>
            <View style={st.statBox}>
              <Users size={18} color={C.info} /><RNText style={st.statValue}>{org.users || 0}</RNText>
              <RNText style={st.statLabel}>Users</RNText>
            </View>
            <View style={st.statBox}>
              <FileText size={18} color={C.accent} /><RNText style={st.statValue}>{org.trials || 0}</RNText>
              <RNText style={st.statLabel}>Trials</RNText>
            </View>
          </View>

          <InfoRow icon={MapPin} label="Address" value={org.address || "—"} />
          <InfoRow icon={Phone} label="Contact" value={org.contact || "—"} />
          <InfoRow icon={Mail} label="Email" value={org.email || "—"} />
          <InfoRow icon={Globe} label="Website" value={org.website || "—"} />

          {org.status !== "merged" && (
            <View style={{ gap: 10, marginTop: 4 }}>
              <ActionBtn label="Edit info" icon={Pencil} tone="neutral" onPress={() => onEdit(org)} disabled={busy} />
              <ActionBtn label="Merge into another organization" icon={Merge} tone="warning" onPress={() => onMerge(org)} disabled={busy} />
              {org.status === "suspended" ? (
                <ActionBtn label="Activate" icon={CheckCircle2} tone="success" onPress={() => onActivate(org)} disabled={busy} />
              ) : (
                <ActionBtn label="Suspend" icon={Ban} tone="destructive" onPress={() => onSuspend(org)} disabled={busy} />
              )}
            </View>
          )}
        </View>
      )}
    </Sheet>
  );
}

// ── Create / edit sheet ──────────────────────────────────────────────────────
function OrgFormSheet({ open, org, onClose, onSaved }: {
  open: boolean; org: Org | null; onClose: () => void; onSaved: (msg: string) => void;
}) {
  const [form, setForm] = useState(EMPTY_ORG_FORM);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setForm(org ? {
        name: org.name || "", type: (org.type as OrgType) || "site", address: org.address || "",
        contact: org.contact || "", email: org.email || "", website: org.website || "",
      } : EMPTY_ORG_FORM);
      setErr(null);
    }
  }, [open, org]);

  const valid = form.name.trim().length > 0;
  const submit = async () => {
    if (!valid) { setErr("An organization name is required"); return; }
    setSaving(true); setErr(null);
    const payload = {
      name: form.name.trim(), type: form.type, address: form.address.trim(),
      contact: form.contact.trim(), email: form.email.trim(), website: form.website.trim(),
    };
    try {
      if (org) {
        await api.patch(`/admin/organizations/${org.id}`, payload);
        onSaved(`${payload.name} updated`);
      } else {
        await api.post("/admin/organizations", payload);
        onSaved(`${payload.name} created`);
      }
    } catch (e) {
      setErr(errMsg(e, "Couldn't save organization"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title={org ? "Edit organization" : "Add organization"}>
      <View style={{ gap: 12 }}>
        <FormField label="Name"><Input value={form.name} onChangeText={(v) => setForm({ ...form, name: sanitizeOrgName(v) })} placeholder="Organization name" /></FormField>
        <FormField label="Type">
          <View style={st.chipWrap}>
            {ORG_TYPES.map((t) => (
              <Pressable key={t} onPress={() => setForm({ ...form, type: t })} style={[st.chip, form.type === t && st.chipActive]}>
                <RNText style={[st.chipTxt, form.type === t && st.chipTxtActive]}>{t}</RNText>
              </Pressable>
            ))}
          </View>
        </FormField>
        <FormField label="Address"><Input value={form.address} onChangeText={(v) => setForm({ ...form, address: sanitizeAddress(v) })} placeholder="Street, city, region" /></FormField>
        <FormField label="Contact"><Input value={form.contact} onChangeText={(v) => setForm({ ...form, contact: sanitizeDigits(v, 10) })} placeholder="+91-XXXXXXXXXX" keyboardType="phone-pad" /></FormField>
        <FormField label="Email"><Input value={form.email} onChangeText={(v) => setForm({ ...form, email: v })} placeholder="contact@org.com" keyboardType="email-address" autoCapitalize="none" /></FormField>
        <FormField label="Website"><Input value={form.website} onChangeText={(v) => setForm({ ...form, website: v })} placeholder="www.org.com" autoCapitalize="none" /></FormField>
        {err && <RNText style={st.errText}>{err}</RNText>}
        <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Save" onConfirm={submit} disabled={!valid} loading={saving} />
      </View>
    </Sheet>
  );
}

// ── Merge sheet ──────────────────────────────────────────────────────────────
function MergeSheet({ source, orgs, onClose, onDone }: {
  source: Org | null; orgs: Org[]; onClose: () => void; onDone: (msg: string) => void;
}) {
  const [targetId, setTargetId] = useState<string | null>(null);
  const [justification, setJustification] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (source) { setTargetId(null); setJustification(""); setErr(null); } }, [source]);

  const candidates = useMemo(
    () => orgs.filter((o) => source && o.id !== source.id && o.status !== "merged"),
    [orgs, source],
  );
  const target = candidates.find((o) => o.id === targetId) || null;
  const canMerge = !!targetId && justification.trim().length >= 10;

  const submit = async () => {
    if (!source || !canMerge) return;
    setSaving(true); setErr(null);
    try {
      const res = await api.post(`/admin/organizations/${source.id}/merge`, {
        target_org_id: targetId, justification: justification.trim(),
      });
      const mu = res.data?.moved_users ?? 0; const mt = res.data?.moved_trials ?? 0;
      onDone(`Merged into ${target?.name || "target"} · ${mu} users, ${mt} trials moved`);
    } catch (e) {
      setErr(errMsg(e, "Couldn't merge organizations"));
    } finally { setSaving(false); }
  };

  return (
    <Sheet open={!!source} onClose={onClose} title="Merge organization">
      {source && (
        <View style={{ gap: 12 }}>
          <View style={st.dangerCard}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <AlertTriangle size={16} color={C.destructive} />
              <RNText style={st.dangerTitle}>Irreversible action</RNText>
            </View>
            <RNText style={st.dangerLine}>
              {source.users || 0} users and {source.trials || 0} trials will be re-linked to the target.
              “{source.name}” will be archived as merged. This cannot be undone.
            </RNText>
          </View>

          <RNText style={st.fieldLabel}>Merge “{source.name}” into</RNText>
          {candidates.length === 0 ? (
            <RNText style={st.emptyText}>No other organizations available to merge into.</RNText>
          ) : (
            <View style={{ gap: 8 }}>
              {candidates.map((o) => {
                const active = o.id === targetId;
                return (
                  <Pressable key={o.id} onPress={() => setTargetId(o.id)} style={[st.pickRow, active && st.pickRowActive]}>
                    <View style={[st.radio, active && { borderColor: C.primary }]}>{active && <View style={st.radioDot} />}</View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.pickName} numberOfLines={1}>{o.name}</RNText>
                      <RNText style={st.rowSub}>{(o.type || "—").toUpperCase()} · {o.users || 0} users</RNText>
                    </View>
                  </Pressable>
                );
              })}
            </View>
          )}

          <FormField label="Admin justification (required, min 10 chars, logged)">
            <Input value={justification} onChangeText={setJustification} placeholder="Reason for merging these records…" multiline />
          </FormField>
          {err && <RNText style={st.errText}>{err}</RNText>}
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Confirm merge" onConfirm={submit} disabled={!canMerge} loading={saving} tone="destructive" />
        </View>
      )}
    </Sheet>
  );
}

// ── Shared primitives (in-file: this screen owns only its own module) ─────────
function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
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
  tile: { width: "23%", flexGrow: 1, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingVertical: 12, paddingHorizontal: 10 },
  tileValue: { fontFamily: fonts.display, fontSize: 20, color: C.primary, fontVariant: ["tabular-nums"] },
  tileLabel: { fontFamily: fonts.regular, fontSize: 10.5, color: C.mutedFg, marginTop: 2 },

  tabRow: { flexDirection: "row", gap: 8, marginTop: 16, backgroundColor: C.surface, borderRadius: 12, padding: 4 },
  tab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 9, borderRadius: 9 },
  tabActive: { backgroundColor: C.card },
  tabTxt: { fontFamily: fonts.semibold, fontSize: 12, color: C.mutedFg },
  tabTxtActive: { color: C.primary },
  tabDot: { minWidth: 18, height: 18, borderRadius: 9, backgroundColor: C.warning, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
  tabDotTxt: { fontFamily: fonts.bold, fontSize: 10, color: C.warningFg },

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
  orgIcon: { width: 42, height: 42, borderRadius: 14, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, flexShrink: 1 },
  typePill: { paddingHorizontal: 8, height: 20, borderRadius: 6, alignItems: "center", justifyContent: "center" },
  typePillTxt: { fontFamily: fonts.bold, fontSize: 10 },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 11, justifyContent: "center" },
  badgeTxt: { fontFamily: fonts.bold, fontSize: 11, textTransform: "capitalize" },

  reqCard: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: "rgba(216,154,60,0.30)", padding: 14, gap: 6 },
  reqBy: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  reqOld: { fontFamily: fonts.medium, fontSize: 13, color: C.destructive, textDecorationLine: "line-through" },
  reqNew: { fontFamily: fonts.semibold, fontSize: 13, color: C.success },

  dupCard: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 14 },
  dupTitle: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  dupRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.surface, borderRadius: 12, padding: 10 },
  dupName: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },

  statBox: { flex: 1, alignItems: "center", gap: 4, backgroundColor: C.surface, borderRadius: 12, paddingVertical: 14 },
  statValue: { fontFamily: fonts.display, fontSize: 20, color: C.foreground },
  statLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },

  smallBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, height: 40, borderRadius: 10 },
  smallBtnTxt: { fontFamily: fonts.bold, fontSize: 13 },

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

  dangerCard: { backgroundColor: "rgba(192,57,43,0.08)", borderRadius: 12, padding: 12, borderWidth: 1, borderColor: "rgba(192,57,43,0.25)", gap: 6 },
  dangerTitle: { fontFamily: fonts.bold, fontSize: 13, color: C.destructive },
  dangerLine: { fontFamily: fonts.regular, fontSize: 12, color: C.foreground, lineHeight: 18 },

  pickRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, padding: 12 },
  pickRowActive: { borderColor: C.primary },
  pickName: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  radio: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  radioDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: C.primary },

  actionBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, borderWidth: 1, backgroundColor: C.card },
  actionBtnTxt: { fontFamily: fonts.bold, fontSize: 14 },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground, marginTop: 2 },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  confirmBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
