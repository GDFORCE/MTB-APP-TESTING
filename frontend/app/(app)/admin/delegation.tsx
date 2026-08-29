// ADM — Admin-staff delegation.
//
// Live, admin-gated endpoints (backend/admin_routes.py · DELEGATIONS):
//   list ........... GET    /admin/delegations           (?status=)
//   create ......... POST   /admin/delegations           {user_id, tasks[], reason(>=20)}
//   edit ........... PATCH  /admin/delegations/{id}       {tasks?, reason?}
//   suspend ........ POST   /admin/delegations/{id}/suspend
//   revoke ......... DELETE /admin/delegations/{id}
//   user lookup .... GET    /admin/users?search=
//
// One active/suspended delegation per user (backend enforces); a patient can
// never be delegated admin tasks. Revocation is a soft tombstone (audit trail).
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, TextInput,
  ActivityIndicator, RefreshControl, Modal, Animated, Platform, KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import {
  Menu, RefreshCcw, X, AlertTriangle, Plus, Search, Pencil, PauseCircle,
  Ban, ChevronRight, Building2, CheckCircle2, XCircle,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { colors as C, fonts } from "@/src/theme/tokens";
import { useAdminDrawer } from "./_layout";

const W = { w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w55: "rgba(255,255,255,0.55)", w70: "rgba(255,255,255,0.70)" };
const errMsg = (e: any, fb: string): string => e?.response?.data?.detail || fb;

const TASKS: { key: string; label: string }[] = [
  { key: "user_management", label: "User management" },
  { key: "support_tickets", label: "Support tickets" },
  { key: "invitations", label: "Invitations" },
  { key: "master_data", label: "Master data" },
  { key: "notifications", label: "Notifications" },
  { key: "reports", label: "Reports" },
  { key: "audit_review", label: "Audit review" },
];
const TASK_LABEL: Record<string, string> = Object.fromEntries(TASKS.map((t) => [t.key, t.label]));

type Delegation = {
  id: string; user_id: string; name?: string; email?: string; tasks?: string[];
  status?: string; reason?: string; delegatedDate?: string; lastActive?: string | null;
};
type LookupUser = { id: string; full_name?: string; email?: string; role?: string; organization?: string };
type OrgDelegationRequest = {
  id: string; org_id: string; org_name?: string; requester_name?: string;
  reason?: string; status?: "pending" | "approved" | "rejected";
  created_at?: string; decided_at?: string; decider_name?: string;
  decision_reason?: string;
};

const STATUS_FILTERS = [{ key: "all", label: "All" }, { key: "active", label: "Active" }, { key: "suspended", label: "Suspended" }, { key: "revoked", label: "Revoked" }];
const REQUEST_STATUS_FILTERS = [{ key: "all", label: "All" }, { key: "pending", label: "Pending" }, { key: "approved", label: "Approved" }, { key: "rejected", label: "Rejected" }];

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString();
}
function statusColors(s?: string): { fg: string; bg: string } {
  switch (s) {
    case "active": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "approved": return { fg: C.success, bg: "rgba(92,154,110,0.15)" };
    case "suspended": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "pending": return { fg: C.warning, bg: "rgba(216,154,60,0.15)" };
    case "revoked": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    case "rejected": return { fg: C.destructive, bg: "rgba(192,57,43,0.12)" };
    default: return { fg: C.mutedFg, bg: C.surface };
  }
}
function initialsOf(name?: string, email?: string): string {
  return (name || email || "U").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
}

export default function AdminDelegation() {
  const { open } = useAdminDrawer();
  const [view, setView] = useState<"staff" | "requests">("staff");
  const [rows, setRows] = useState<Delegation[]>([]);
  const [requests, setRequests] = useState<OrgDelegationRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("all");

  const [selected, setSelected] = useState<Delegation | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<OrgDelegationRequest | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<Delegation | null>(null);
  const [busy, setBusy] = useState(false);

  const { toast, toastAnim, showToast } = useToast();

  const load = useCallback(async () => {
    setError(null);
    try {
      const [delegationsRes, requestsRes] = await Promise.all([
        api.get("/admin/delegations"),
        api.get("/admin/org-delegation-requests"),
      ]);
      setRows(Array.isArray(delegationsRes.data) ? delegationsRes.data : []);
      setRequests(Array.isArray(requestsRes.data) ? requestsRes.data : []);
    } catch (e) {
      setError(errMsg(e, "Couldn't load delegations. Pull to retry."));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  const filtered = useMemo(
    () => rows.filter((d) => statusFilter === "all" || (d.status || "active") === statusFilter),
    [rows, statusFilter],
  );
  const filteredRequests = useMemo(
    () => requests.filter((request) => statusFilter === "all" || (request.status || "pending") === statusFilter),
    [requests, statusFilter],
  );
  const tiles = useMemo(() => view === "staff" ? [
    { label: "Total", value: rows.length },
    { label: "Active", value: rows.filter((d) => d.status === "active").length },
    { label: "Suspended", value: rows.filter((d) => d.status === "suspended").length },
  ] : [
    { label: "Total", value: requests.length },
    { label: "Pending", value: requests.filter((request) => (request.status || "pending") === "pending").length },
    { label: "Approved", value: requests.filter((request) => request.status === "approved").length },
  ], [requests, rows, view]);

  const suspend = async (d: Delegation) => {
    setBusy(true);
    try {
      await api.post(`/admin/delegations/${d.id}/suspend`);
      showToast(`Suspended delegation for ${d.name || d.email}`);
      setSelected(null); await load();
    } catch (e) { showToast(errMsg(e, "Couldn't suspend")); } finally { setBusy(false); }
  };
  const revoke = async (d: Delegation) => {
    setBusy(true);
    try {
      await api.delete(`/admin/delegations/${d.id}`);
      showToast(`Revoked delegation for ${d.name || d.email}`);
      setSelected(null); await load();
    } catch (e) { showToast(errMsg(e, "Couldn't revoke")); } finally { setBusy(false); }
  };
  const decideRequest = async (request: OrgDelegationRequest, decision: "approve" | "reject", reason: string) => {
    setBusy(true);
    try {
      await api.post(`/admin/org-delegation-requests/${request.id}/${decision}`, { reason });
      showToast(`${decision === "approve" ? "Approved" : "Rejected"} trial-creation delegation for ${request.org_name || "organization"}`);
      setSelectedRequest(null);
      await load();
    } catch (e) {
      showToast(errMsg(e, `Couldn't ${decision} the request`));
    } finally {
      setBusy(false);
    }
  };
  const changeView = (next: "staff" | "requests") => {
    setView(next);
    setStatusFilter("all");
    setSelected(null);
    setSelectedRequest(null);
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
        <Hero onMenu={open} onRefresh={onRefresh} onAdd={view === "staff" ? () => setAddOpen(true) : undefined} />

        {loading ? (
          <Loading label="Loading delegations…" />
        ) : error ? (
          <ErrorCard message={error} onRetry={load} />
        ) : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.viewSwitch}>
              <Pressable testID="delegation-view-staff" onPress={() => changeView("staff")} style={[st.viewSwitchBtn, view === "staff" && st.viewSwitchBtnActive]}>
                <RNText style={[st.viewSwitchTxt, view === "staff" && st.viewSwitchTxtActive]}>Admin tasks</RNText>
              </Pressable>
              <Pressable testID="delegation-view-requests" onPress={() => changeView("requests")} style={[st.viewSwitchBtn, view === "requests" && st.viewSwitchBtnActive]}>
                <RNText style={[st.viewSwitchTxt, view === "requests" && st.viewSwitchTxtActive]}>Trial creation requests</RNText>
              </Pressable>
            </View>
            <View style={st.tileGrid}>
              {tiles.map((t) => (
                <View key={t.label} style={st.tile}>
                  <RNText style={st.tileValue}>{t.value}</RNText>
                  <RNText style={st.tileLabel}>{t.label}</RNText>
                </View>
              ))}
            </View>

            <ChipRow chips={view === "staff" ? STATUS_FILTERS : REQUEST_STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
            <RNText style={st.countLine}>
              {view === "staff"
                ? `${filtered.length} of ${rows.length} delegations`
                : `${filteredRequests.length} of ${requests.length} requests`}
            </RNText>

            {view === "staff" ? (filtered.length === 0 ? (
              <EmptyCard message="No admin-task delegations match the current filter." />
            ) : (
              <View style={{ gap: 10 }}>
                {filtered.map((d) => {
                  const sc = statusColors(d.status);
                  return (
                    <Pressable key={d.id} testID={`delegation-${d.id}`} onPress={() => setSelected(d)} style={st.row}>
                      <View style={st.avatar}><RNText style={st.avatarTxt}>{initialsOf(d.name, d.email)}</RNText></View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <RNText style={st.rowName} numberOfLines={1}>{d.name || "—"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{d.email || "—"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{(d.tasks || []).length} task{(d.tasks || []).length === 1 ? "" : "s"} delegated</RNText>
                      </View>
                      <View style={{ alignItems: "flex-end", gap: 6 }}>
                        <View style={[st.badge, { backgroundColor: sc.bg }]}>
                          <RNText style={[st.badgeTxt, { color: sc.fg }]}>{d.status || "active"}</RNText>
                        </View>
                        <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            )) : (filteredRequests.length === 0 ? (
              <EmptyCard message="No trial-creation delegation requests match the current filter." />
            ) : (
              <View style={{ gap: 10 }}>
                {filteredRequests.map((request) => {
                  const sc = statusColors(request.status);
                  return (
                    <Pressable key={request.id} testID={`org-delegation-request-${request.id}`} onPress={() => setSelectedRequest(request)} style={st.row}>
                      <View style={[st.avatar, { backgroundColor: "rgba(166,33,63,0.10)" }]}>
                        <Building2 size={19} color={C.primary} />
                      </View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <RNText style={st.rowName} numberOfLines={1}>{request.org_name || "Organization"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>Requested by {request.requester_name || "organization administrator"}</RNText>
                        <RNText style={st.rowSub} numberOfLines={1}>{fmtDate(request.created_at)}</RNText>
                      </View>
                      <View style={{ alignItems: "flex-end", gap: 6 }}>
                        <View style={[st.badge, { backgroundColor: sc.bg }]}>
                          <RNText style={[st.badgeTxt, { color: sc.fg }]}>{request.status || "pending"}</RNText>
                        </View>
                        <ChevronRight size={16} color="rgba(123,95,115,0.4)" />
                      </View>
                    </Pressable>
                  );
                })}
              </View>
            ))}
          </View>
        )}
      </ScrollView>

      <DetailSheet
        delegation={selected} busy={busy}
        onClose={() => setSelected(null)}
        onEdit={(d) => { setSelected(null); setEditing(d); }}
        onSuspend={suspend} onRevoke={revoke}
      />
      <AddSheet open={addOpen} onClose={() => setAddOpen(false)} onDone={(msg) => { setAddOpen(false); showToast(msg); load(); }} />
      <EditSheet delegation={editing} onClose={() => setEditing(null)} onDone={(msg) => { setEditing(null); showToast(msg); load(); }} />
      <OrgRequestSheet
        request={selectedRequest}
        busy={busy}
        onClose={() => setSelectedRequest(null)}
        onDecision={decideRequest}
      />

      <Toast text={toast} anim={toastAnim} />
    </View>
  );
}

function Hero({ onMenu, onRefresh, onAdd }: { onMenu: () => void; onRefresh: () => void; onAdd?: () => void }) {
  return (
    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
      <SafeAreaView edges={["top"]}>
        <View style={st.heroTop}>
          <Pressable testID="admin-menu" onPress={onMenu} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
            <RNText style={st.heroTitle} numberOfLines={1}>Delegation</RNText>
          </View>
          <Pressable testID="delegation-refresh" onPress={onRefresh} style={st.iconBtn} hitSlop={8}><RefreshCcw size={18} color={C.primaryFg} /></Pressable>
        </View>
        <RNText style={st.heroSub}>Grant scoped admin tasks to trusted staff — every change is audited.</RNText>
        {onAdd ? (
          <View style={{ flexDirection: "row", gap: 10, marginTop: 14 }}>
            <Pressable testID="delegation-add" onPress={onAdd} style={st.heroBtnSolid}>
              <Plus size={15} color={C.primary} /><RNText style={st.heroBtnSolidTxt}>Add delegation</RNText>
            </Pressable>
          </View>
        ) : <View style={{ height: 14 }} />}
      </SafeAreaView>
    </LinearGradient>
  );
}

function TaskChips({ tasks }: { tasks?: string[] }) {
  if (!tasks || tasks.length === 0) return <RNText style={st.rowSub}>No tasks</RNText>;
  return (
    <View style={st.taskWrap}>
      {tasks.map((t) => (
        <View key={t} style={st.taskPill}><RNText style={st.taskPillTxt}>{TASK_LABEL[t] || t}</RNText></View>
      ))}
    </View>
  );
}

function DetailSheet({ delegation, busy, onClose, onEdit, onSuspend, onRevoke }: {
  delegation: Delegation | null; busy: boolean; onClose: () => void;
  onEdit: (d: Delegation) => void; onSuspend: (d: Delegation) => void; onRevoke: (d: Delegation) => void;
}) {
  return (
    <Sheet open={!!delegation} onClose={onClose} title="Delegation details">
      {delegation && (
        <View style={{ gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 14 }}>
            <View style={[st.avatar, { width: 56, height: 56, borderRadius: 18 }]}>
              <RNText style={[st.avatarTxt, { fontSize: 18 }]}>{initialsOf(delegation.name, delegation.email)}</RNText>
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.sheetName} numberOfLines={1}>{delegation.name || "—"}</RNText>
              <RNText style={st.rowSub} numberOfLines={1}>{delegation.email || "—"}</RNText>
              {(() => { const sc = statusColors(delegation.status); return (
                <View style={[st.badge, { backgroundColor: sc.bg, alignSelf: "flex-start", marginTop: 6 }]}>
                  <RNText style={[st.badgeTxt, { color: sc.fg }]}>{delegation.status || "active"}</RNText>
                </View>
              ); })()}
            </View>
          </View>

          <View style={{ gap: 6 }}>
            <RNText style={st.fieldLabel}>Delegated tasks</RNText>
            <TaskChips tasks={delegation.tasks} />
          </View>
          {!!delegation.reason && (
            <View style={st.block}>
              <RNText style={st.blockLabel}>Reason</RNText>
              <RNText style={st.blockBody}>{delegation.reason}</RNText>
            </View>
          )}
          <InfoRow label="Delegated" value={fmtDate(delegation.delegatedDate)} />
          <InfoRow label="Last active" value={fmtDate(delegation.lastActive)} />

          {delegation.status !== "revoked" && (
            <View style={{ gap: 10, marginTop: 4 }}>
              <ActionBtn label="Edit tasks & reason" icon={Pencil} tone="primary" onPress={() => onEdit(delegation)} disabled={busy} />
              {delegation.status === "active" && (
                <ActionBtn label="Suspend delegation" icon={PauseCircle} tone="warning" onPress={() => onSuspend(delegation)} disabled={busy} />
              )}
              <ActionBtn label="Revoke delegation" icon={Ban} tone="destructive" onPress={() => onRevoke(delegation)} disabled={busy} />
            </View>
          )}
        </View>
      )}
    </Sheet>
  );
}

function TaskChecklist({ value, onToggle }: { value: string[]; onToggle: (k: string) => void }) {
  return (
    <View style={{ gap: 8 }}>
      {TASKS.map((t) => {
        const on = value.includes(t.key);
        return (
          <Pressable key={t.key} onPress={() => onToggle(t.key)} style={st.checkRow}>
            <View style={[st.checkbox, on && { backgroundColor: C.primary, borderColor: C.primary }]}>{on && <RNText style={st.checkMark}>✓</RNText>}</View>
            <RNText style={st.checkLabel}>{t.label}</RNText>
          </Pressable>
        );
      })}
    </View>
  );
}

function AddSheet({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (msg: string) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LookupUser[]>([]);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<LookupUser | null>(null);
  const [tasks, setTasks] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setQuery(""); setResults([]); setPicked(null); setTasks([]); setReason(""); setErr(null); }
  }, [open]);

  useEffect(() => {
    if (picked || query.trim().length < 2) { setResults([]); return; }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.get("/admin/users", { params: { search: query.trim(), limit: 20 } });
        if (!cancelled) setResults((Array.isArray(res.data) ? res.data : []).filter((u: LookupUser) => u.role !== "patient"));
      } catch { if (!cancelled) setResults([]); }
      finally { if (!cancelled) setSearching(false); }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [query, picked]);

  const toggle = (k: string) => setTasks((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);
  const valid = !!picked && tasks.length > 0 && reason.trim().length >= 20;

  const submit = async () => {
    if (!picked) { setErr("Select a staff member first"); return; }
    if (tasks.length === 0) { setErr("Select at least one task"); return; }
    if (reason.trim().length < 20) { setErr("Reason must be at least 20 characters"); return; }
    setSaving(true); setErr(null);
    try {
      await api.post("/admin/delegations", { user_id: picked.id, tasks, reason: reason.trim() });
      onDone(`Delegated ${tasks.length} task${tasks.length === 1 ? "" : "s"} to ${picked.full_name || picked.email}`);
    } catch (e) { setErr(errMsg(e, "Couldn't create delegation")); } finally { setSaving(false); }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Add delegation">
      <View style={{ gap: 12 }}>
        {!picked ? (
          <FormField label="Find staff member">
            <View style={st.searchBar}>
              <Search size={17} color={C.mutedFg} />
              <TextInput value={query} onChangeText={setQuery} placeholder="Search name, email or org…" placeholderTextColor="rgba(123,95,115,0.5)" autoCapitalize="none" style={st.searchInput} />
              {searching && <ActivityIndicator size="small" color={C.primary} />}
            </View>
            {query.trim().length >= 2 && (
              <View style={{ gap: 8, marginTop: 4 }}>
                {results.length === 0 && !searching ? (
                  <RNText style={st.rowSub}>No non-patient users match.</RNText>
                ) : results.map((u) => (
                  <Pressable key={u.id} onPress={() => setPicked(u)} style={st.lookupRow}>
                    <View style={st.avatar}><RNText style={st.avatarTxt}>{initialsOf(u.full_name, u.email)}</RNText></View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.rowName} numberOfLines={1}>{u.full_name || "—"}</RNText>
                      <RNText style={st.rowSub} numberOfLines={1}>{u.email} · {u.role}</RNText>
                    </View>
                  </Pressable>
                ))}
              </View>
            )}
          </FormField>
        ) : (
          <View style={st.pickedRow}>
            <View style={st.avatar}><RNText style={st.avatarTxt}>{initialsOf(picked.full_name, picked.email)}</RNText></View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.rowName} numberOfLines={1}>{picked.full_name || "—"}</RNText>
              <RNText style={st.rowSub} numberOfLines={1}>{picked.email} · {picked.role}</RNText>
            </View>
            <Pressable onPress={() => setPicked(null)} hitSlop={8}><X size={18} color={C.mutedFg} /></Pressable>
          </View>
        )}

        {picked && (
          <>
            <FormField label={`Tasks to delegate (${tasks.length} selected)`}>
              <TaskChecklist value={tasks} onToggle={toggle} />
            </FormField>
            <FormField label="Reason (min 20 chars, permanently logged)">
              <Input value={reason} onChangeText={setReason} placeholder="Why is this delegation being granted…" multiline />
            </FormField>
          </>
        )}
        {err && <RNText style={st.errText}>{err}</RNText>}
        <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Delegate" onConfirm={submit} disabled={!valid} loading={saving} />
      </View>
    </Sheet>
  );
}

function EditSheet({ delegation, onClose, onDone }: { delegation: Delegation | null; onClose: () => void; onDone: (msg: string) => void }) {
  const [tasks, setTasks] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (delegation) { setTasks(delegation.tasks || []); setReason(delegation.reason || ""); setErr(null); } }, [delegation]);

  const toggle = (k: string) => setTasks((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);
  const valid = tasks.length > 0 && reason.trim().length >= 20;

  const submit = async () => {
    if (!delegation) return;
    if (tasks.length === 0) { setErr("Select at least one task"); return; }
    if (reason.trim().length < 20) { setErr("Reason must be at least 20 characters"); return; }
    setSaving(true); setErr(null);
    try {
      await api.patch(`/admin/delegations/${delegation.id}`, { tasks, reason: reason.trim() });
      onDone(`Updated delegation for ${delegation.name || delegation.email}`);
    } catch (e) { setErr(errMsg(e, "Couldn't update delegation")); } finally { setSaving(false); }
  };

  return (
    <Sheet open={!!delegation} onClose={onClose} title="Edit delegation">
      {delegation && (
        <View style={{ gap: 12 }}>
          <RNText style={st.rowSub}>{delegation.name || delegation.email}</RNText>
          <FormField label={`Tasks (${tasks.length} selected)`}>
            <TaskChecklist value={tasks} onToggle={toggle} />
          </FormField>
          <FormField label="Reason (min 20 chars)">
            <Input value={reason} onChangeText={setReason} placeholder="Updated justification…" multiline />
          </FormField>
          {err && <RNText style={st.errText}>{err}</RNText>}
          <SheetActions cancelLabel="Cancel" onCancel={onClose} confirmLabel="Save" onConfirm={submit} disabled={!valid} loading={saving} />
        </View>
      )}
    </Sheet>
  );
}

function OrgRequestSheet({ request, busy, onClose, onDecision }: {
  request: OrgDelegationRequest | null;
  busy: boolean;
  onClose: () => void;
  onDecision: (request: OrgDelegationRequest, decision: "approve" | "reject", reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    setReason("");
  }, [request?.id]);

  return (
    <Sheet open={!!request} onClose={onClose} title="Trial creation request">
      {request ? (
        <View style={{ gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={[st.avatar, { width: 52, height: 52, borderRadius: 17, backgroundColor: "rgba(166,33,63,0.10)" }]}>
              <Building2 size={22} color={C.primary} />
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <RNText style={st.sheetName} numberOfLines={2}>{request.org_name || "Organization"}</RNText>
              <RNText style={st.rowSub}>Requested {fmtDate(request.created_at)}</RNText>
            </View>
            {(() => {
              const sc = statusColors(request.status);
              return (
                <View style={[st.badge, { backgroundColor: sc.bg }]}>
                  <RNText style={[st.badgeTxt, { color: sc.fg }]}>{request.status || "pending"}</RNText>
                </View>
              );
            })()}
          </View>

          <InfoRow label="Requested by" value={request.requester_name || "Organization administrator"} />
          <View style={st.block}>
            <RNText style={st.blockLabel}>Business justification</RNText>
            <RNText style={st.blockBody}>{request.reason || "No justification supplied."}</RNText>
          </View>

          {request.status === "pending" ? (
            <>
              <FormField label="Decision note (required when rejecting)">
                <Input
                  testID="org-delegation-decision-reason"
                  value={reason}
                  onChangeText={setReason}
                  placeholder="Record the approval conditions or rejection reason…"
                  multiline
                />
              </FormField>
              <View style={{ flexDirection: "row", gap: 10 }}>
                <Pressable
                  testID="org-delegation-reject"
                  disabled={busy || reason.trim().length < 5}
                  onPress={() => onDecision(request, "reject", reason.trim())}
                  style={[st.decisionBtn, { borderColor: "rgba(192,57,43,0.35)" }, (busy || reason.trim().length < 5) && st.disabled]}
                >
                  <XCircle size={16} color={C.destructive} />
                  <RNText style={[st.decisionBtnTxt, { color: C.destructive }]}>Reject</RNText>
                </Pressable>
                <Pressable
                  testID="org-delegation-approve"
                  disabled={busy}
                  onPress={() => onDecision(request, "approve", reason.trim())}
                  style={[st.decisionBtn, { backgroundColor: C.success, borderColor: C.success }, busy && st.disabled]}
                >
                  {busy ? <ActivityIndicator color={C.primaryFg} size="small" /> : <CheckCircle2 size={16} color={C.primaryFg} />}
                  <RNText style={[st.decisionBtnTxt, { color: C.primaryFg }]}>Approve</RNText>
                </Pressable>
              </View>
            </>
          ) : (
            <>
              <InfoRow label="Decided by" value={request.decider_name || "Platform administrator"} />
              <InfoRow label="Decision date" value={fmtDate(request.decided_at)} />
              {request.decision_reason ? (
                <View style={st.block}>
                  <RNText style={st.blockLabel}>Decision note</RNText>
                  <RNText style={st.blockBody}>{request.decision_reason}</RNText>
                </View>
              ) : null}
            </>
          )}
        </View>
      ) : null}
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
  return <TextInput placeholderTextColor="rgba(123,95,115,0.5)" {...props} style={[st.input, props.multiline && { height: 90, textAlignVertical: "top" }, props.style]} />;
}
function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return <View style={{ gap: 6 }}><RNText style={st.fieldLabel}>{label}</RNText>{children}</View>;
}
function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={st.infoRow}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <RNText style={st.infoLabel}>{label}</RNText>
        <RNText style={st.infoValue}>{value}</RNText>
      </View>
    </View>
  );
}
function ActionBtn({ label, icon: Icon, tone, onPress, disabled }: { label: string; icon: any; tone: "primary" | "destructive" | "warning"; onPress: () => void; disabled?: boolean }) {
  const map: Record<string, string> = { primary: C.primary, destructive: C.destructive, warning: C.warning };
  const fg = map[tone];
  return (
    <Pressable onPress={disabled ? undefined : onPress} style={[st.actionBtn, { borderColor: fg + "44" }, disabled && { opacity: 0.5 }]}>
      <Icon size={16} color={fg} /><RNText style={[st.actionBtnTxt, { color: fg }]}>{label}</RNText>
    </Pressable>
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
function ChipRow({ chips, value, onChange }: { chips: { key: string; label: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 8 }} style={{ marginTop: 14 }}>
      {chips.map((c) => (
        <Pressable key={c.key} onPress={() => onChange(c.key)} style={[st.chip, value === c.key && st.chipActive]}>
          <RNText style={[st.chipTxt, value === c.key && st.chipTxtActive]}>{c.label}</RNText>
        </Pressable>
      ))}
    </ScrollView>
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
  tile: { width: "31%", flexGrow: 1, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingVertical: 12, paddingHorizontal: 12 },
  tileValue: { fontFamily: fonts.display, fontSize: 22, color: C.primary, fontVariant: ["tabular-nums"] },
  tileLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2 },
  viewSwitch: { flexDirection: "row", gap: 4, padding: 4, marginBottom: 12, borderRadius: 14, backgroundColor: C.surface, borderWidth: 1, borderColor: C.border },
  viewSwitchBtn: { flex: 1, minHeight: 38, borderRadius: 11, alignItems: "center", justifyContent: "center", paddingHorizontal: 8 },
  viewSwitchBtnActive: { backgroundColor: C.card },
  viewSwitchTxt: { color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 12, textAlign: "center" },
  viewSwitchTxtActive: { color: C.primary },

  countLine: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 14, marginBottom: 10 },

  row: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 12 },
  avatar: { width: 42, height: 42, borderRadius: 14, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  avatarTxt: { color: C.secondaryFg, fontFamily: fonts.bold, fontSize: 14 },
  rowName: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground },
  rowSub: { fontFamily: fonts.regular, fontSize: 12, color: C.mutedFg, marginTop: 1 },
  badge: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 11, justifyContent: "center" },
  badgeTxt: { fontFamily: fonts.bold, fontSize: 11, textTransform: "capitalize" },

  taskWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  taskPill: { paddingHorizontal: 10, height: 24, borderRadius: 8, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  taskPillTxt: { fontFamily: fonts.medium, fontSize: 11, color: C.foreground },

  lookupRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 10 },
  pickedRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 12 },

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

  searchBar: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, height: 46 },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 14, color: C.foreground, padding: 0 },

  infoRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.surface, borderRadius: 12, padding: 12 },
  infoLabel: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  infoValue: { fontFamily: fonts.medium, fontSize: 14, color: C.foreground, marginTop: 1 },
  block: { backgroundColor: C.surface, borderRadius: 12, padding: 12, gap: 4 },
  blockLabel: { fontFamily: fonts.semibold, fontSize: 11, color: C.mutedFg, textTransform: "uppercase", letterSpacing: 0.6 },
  blockBody: { fontFamily: fonts.regular, fontSize: 13, color: C.foreground, lineHeight: 19 },

  fieldLabel: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  input: { backgroundColor: C.card, borderRadius: 12, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, paddingVertical: 12, fontFamily: fonts.regular, fontSize: 14, color: C.foreground },
  errText: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  checkRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  checkbox: { width: 22, height: 22, borderRadius: 6, borderWidth: 1.5, borderColor: C.border, alignItems: "center", justifyContent: "center", backgroundColor: C.card },
  checkMark: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 13 },
  checkLabel: { flex: 1, fontFamily: fonts.regular, fontSize: 13, color: C.foreground },

  chip: { paddingHorizontal: 14, height: 34, borderRadius: 999, backgroundColor: C.card, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  chipActive: { backgroundColor: C.primary, borderColor: C.primary },
  chipTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.mutedFg },
  chipTxtActive: { color: C.primaryFg },

  actionBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 13, borderRadius: 12, borderWidth: 1, backgroundColor: C.card },
  actionBtnTxt: { fontFamily: fonts.bold, fontSize: 14 },
  decisionBtn: { flex: 1, minHeight: 46, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderRadius: 999, borderWidth: 1, backgroundColor: C.card },
  decisionBtnTxt: { fontFamily: fonts.bold, fontSize: 13 },
  disabled: { opacity: 0.45 },

  cancelBtn: { paddingVertical: 14, borderRadius: 999, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  cancelBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.mutedFg },
  confirmBtn: { paddingVertical: 14, borderRadius: 999, alignItems: "center", justifyContent: "center" },
  confirmBtnTxt: { fontFamily: fonts.bold, fontSize: 15, color: C.primaryFg },

  toast: { position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.foreground, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 16 },
  toastTxt: { color: C.primaryFg, fontFamily: fonts.medium, fontSize: 13, textAlign: "center" },
});
