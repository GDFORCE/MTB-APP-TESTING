// SMO (Site Management Organization) org-admin console — Task 6.6.
//
// The network deck for an SMO, layered on the research-team dashboard. Its
// signature is the hospital-network rail: a Sites tab that lists affiliated
// hospitals (add / drill-down / remove) and cross-site CRC assignment — staff
// can be moved to any hospital in the network. Trials, Team and Audit reuse the
// shared kit; trials stay access-keyed (full vs schedule-only, subjects masked).
// Everything is wired to the live /api/org endpoints.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, RefreshControl } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  KeyRound, Lock, Plus, UserPlus, ShieldCheck, Landmark, CalendarDays, FileText,
  Users, ArrowUpRight, Building2, MapPin, ChevronRight, Trash2, FlaskConical, Stethoscope,
} from "lucide-react-native";
import { colors as C, fonts } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { formatVisitTiming, formatVisitWindow } from "@/src/lib/visit-timing";
import { sanitizeAddress, sanitizeOrgName } from "@/src/lib/validators";
import {
  useOrgContext, useToast, ConsoleHeader, DeckTabs, AuditTrail, TeamRoster,
  DelegationGate, Loading, ErrorCard, EmptyCard,
  Sheet, Field, KitInput, PrimaryButton, ConfirmDialog,
  TrialAdminActions,
  errMsg, stripTitle, type OrgMember, type AuditEntry, type OrgTrial, type OrgSite, type ConfirmState,
} from "@/src/components/org-admin-kit";

function statusTone(status?: string): { bg: string; fg: string } {
  const s = (status || "active").toLowerCase();
  if (s === "active") return { bg: "rgba(92,154,110,0.15)", fg: C.success };
  if (s === "completed") return { bg: "rgba(123,107,184,0.15)", fg: C.info };
  if (s === "terminated" || s === "closed") return { bg: "rgba(192,57,43,0.12)", fg: C.destructive };
  return { bg: "rgba(123,95,115,0.12)", fg: C.mutedFg };
}

const ADD_PATIENT_ROUTE = "/(app)/clinical/add-patient";

export default function SmoConsole() {
  const router = useRouter();
  const { user } = useAuth();
  const { orgId, orgName, loading: orgLoading, error: orgErr, retry } = useOrgContext();
  const { showToast, ToastView } = useToast();

  const [tab, setTab] = useState("trials");
  const [trials, setTrials] = useState<OrgTrial[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [sites, setSites] = useState<OrgSite[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [delegated, setDelegated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditErr, setAuditErr] = useState<string | null>(null);
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [gate, setGate] = useState(false);
  const [gateBusy, setGateBusy] = useState(false);
  const [siteDetail, setSiteDetail] = useState<OrgSite | null>(null);
  const [addSite, setAddSite] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  const loadAll = useCallback(async () => {
    if (!orgId) return;
    setError(null); setAuditErr(null);
    try {
      const [t, m, s, d] = await Promise.all([
        api.get(`/org/${orgId}/trials`),
        api.get(`/org/${orgId}/members`),
        api.get(`/org/${orgId}/sites`),
        api.get(`/org/${orgId}/delegation-status`).catch(() => ({ data: { delegated: false } })),
      ]);
      setTrials(Array.isArray(t.data) ? t.data : []);
      setMembers(Array.isArray(m.data) ? m.data : []);
      setSites(Array.isArray(s.data) ? s.data : []);
      setDelegated(!!d.data?.delegated);
    } catch (e) {
      setError(errMsg(e, "Couldn't load the console. Pull to retry."));
    } finally { setLoading(false); }
    try {
      const a = await api.get(`/org/${orgId}/audit-trail`);
      setAudit(Array.isArray(a.data) ? a.data : []);
    } catch (e) { setAuditErr(errMsg(e, "Couldn't load the audit trail.")); }
  }, [orgId]);

  useEffect(() => { if (orgId) loadAll(); }, [orgId, loadAll]);
  const onRefresh = async () => { setRefreshing(true); await loadAll(); setRefreshing(false); };

  const siteNames = useMemo(() => sites.map((s) => s.name), [sites]);
  const membersAt = useCallback((name: string) => members.filter((m) => m.site === name && m.status !== "rejected"), [members]);
  const trialsAt = useCallback((name: string) => trials.filter((t) => t.sites?.includes(name)), [trials]);

  const pulse = useMemo(() => {
    const patients = trials.reduce((n, t) => n + (t.enrolled || 0), 0);
    const pis = members.filter((m) => (m.role || "").toLowerCase() === "pi" && m.status !== "rejected").length;
    return [
      { value: trials.length, label: "Trials", onPress: () => setTab("trials") },
      { value: sites.length, label: "Sites", onPress: () => setTab("sites") },
      { value: pis, label: "PIs", onPress: () => setTab("team") },
      { value: patients, label: "Patients", onPress: () => setTab("trials") },
    ];
  }, [trials, sites, members]);

  // ── org action handlers (live endpoints) ──
  const inviteMember = async (p: any) => { await api.post(`/org/${orgId}/members/invite`, p); };
  const deleteMember = async (m: OrgMember) => { await api.delete(`/org/${orgId}/members/${m.id}`); };
  const makeAdmin = async (m: OrgMember) => { await api.post(`/org/${orgId}/members/${m.id}/make-admin`); };
  const assignSite = async (m: OrgMember, site: string) => { await api.post(`/org/${orgId}/members/${m.id}/assign-site`, { site }); };

  const requestAccess = async (t: OrgTrial) => {
    setRequested((prev) => new Set(prev).add(t.id));
    try {
      await api.post(`/trials/${t.id}/access-requests`, { org_id: orgId, reason: "SMO admin requesting full access" });
      showToast("Full-access request sent to the trial owner");
      loadAll();
    } catch (e) {
      setRequested((prev) => { const n = new Set(prev); n.delete(t.id); return n; });
      showToast(errMsg(e, "Couldn't send the access request"));
    }
  };

  const addSiteToNetwork = async (name: string, address: string) => {
    await api.post(`/org/${orgId}/sites`, { name, address });
    showToast(`${name} added to your network`);
    setAddSite(false);
    loadAll();
  };
  const removeSite = (s: OrgSite) => {
    const inUse = membersAt(s.name).length;
    setConfirm({
      title: "Remove site?",
      body: inUse
        ? `${s.name} still has ${inUse} member${inUse === 1 ? "" : "s"} assigned. Removing it detaches the hospital from your network.`
        : `${s.name} will be removed from ${orgName}.`,
      confirmLabel: "Remove site",
      onConfirm: async () => {
        setConfirmBusy(true);
        try {
          await api.delete(`/org/${orgId}/sites/${s.id}`);
          showToast(`${s.name} removed`);
          setConfirm(null); setSiteDetail(null); loadAll();
        } catch (e) { showToast(errMsg(e, "Couldn't remove site")); }
        finally { setConfirmBusy(false); }
      },
    });
  };

  const requestDelegation = async () => {
    setGateBusy(true);
    try {
      await api.post(`/org/${orgId}/delegation-requests`, { reason: "Requesting trial-creation delegation for this SMO" });
      showToast("Delegation requested from your organization");
      setGate(false);
    } catch (e) { showToast(errMsg(e, "Couldn't request delegation")); }
    finally { setGateBusy(false); }
  };
  const proceedToCreate = () => { setGate(false); router.push("/(app)/sponsor/add-trial"); };

  if (orgLoading) return <View style={{ flex: 1, backgroundColor: C.background }}><Loading label="Opening console…" /></View>;
  if (orgErr || !orgId) return <View style={{ flex: 1, backgroundColor: C.background, padding: 16, justifyContent: "center" }}><ErrorCard message={orgErr || "Organization unavailable"} onRetry={retry} /></View>;

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        <ConsoleHeader
          eyebrow="SMO · HOSPITAL NETWORK OVERSIGHT"
          org={orgName} roleLabel="SMO Admin"
          note={`${sites.length} affiliated hospital${sites.length === 1 ? "" : "s"} · one research network`}
          glow="rgba(142,91,180,0.34)"
          pulse={pulse} onBack={() => router.back()}
        />

        <View style={{ paddingHorizontal: 16, marginTop: 14 }}>
          <DeckTabs
            activeColor={C.violet}
            tabs={[
              { key: "trials", label: "Trials", count: trials.length },
              { key: "sites", label: "Sites", count: sites.length },
              { key: "team", label: "Organization Members", count: members.filter((m) => m.status !== "rejected").length },
              { key: "audit", label: "Audit", count: audit.length },
            ]}
            active={tab} onChange={setTab}
          />
        </View>

        <View style={{ paddingHorizontal: 16, marginTop: 16 }}>
          {tab === "trials" && (
            loading ? <Loading label="Loading trials…" /> : error ? <ErrorCard message={error} onRetry={loadAll} /> : (
              <>
                <View style={{ flexDirection: "row", gap: 12, marginBottom: 16 }}>
                  <Pressable onPress={() => setGate(true)} style={st.qa}>
                    <View style={[st.qaIcon, { backgroundColor: C.violet }]}><Plus size={20} color={C.white} /></View>
                    <RNText style={st.qaLabel}>New Trial</RNText>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 4, marginTop: 2 }}>
                      {delegated ? <ShieldCheck size={11} color={C.success} /> : <Landmark size={11} color={C.warning} />}
                      <RNText style={st.qaSub}>{delegated ? "Delegation on file" : "Needs delegation"}</RNText>
                    </View>
                  </Pressable>
                  <Pressable onPress={() => router.push(ADD_PATIENT_ROUTE)} style={st.qa}>
                    <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.qaIcon}>
                      <UserPlus size={20} color={C.primaryFg} />
                    </LinearGradient>
                    <RNText style={st.qaLabel}>Add Patient</RNText>
                    <RNText style={st.qaSub}>To trials you can access</RNText>
                  </Pressable>
                </View>

                {trials.length === 0 ? (
                  <EmptyCard icon={FlaskConical} title="No trials yet" subtitle="Trials across your network, or granted to it, appear here." />
                ) : (
                  <View style={{ gap: 12 }}>
                    {trials.map((t) => (
                      <TrialCard
                        key={t.id}
                        t={t}
                        mine={t.createdBy === user?.id}
                        requested={requested.has(t.id)}
                        onOpen={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: t.id } })}
                        onRequest={() => requestAccess(t)}
                        actions={orgId ? <TrialAdminActions trial={t} orgId={orgId} showToast={showToast} onChanged={loadAll} /> : null}
                      />
                    ))}
                  </View>
                )}
              </>
            )
          )}

          {tab === "sites" && (
            loading ? <Loading label="Loading network…" /> : error ? <ErrorCard message={error} onRetry={loadAll} /> : (
              <>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                  <RNText style={st.eyebrow}>AFFILIATED HOSPITALS</RNText>
                  <Pressable onPress={() => setAddSite(true)} style={[st.addPill, { backgroundColor: C.violet }]}>
                    <Plus size={14} color={C.white} /><RNText style={st.addPillTxt}>Add site</RNText>
                  </Pressable>
                </View>
                {sites.length === 0 ? (
                  <EmptyCard icon={Building2} title="No hospitals yet" subtitle="Add the first affiliated hospital to build your network." />
                ) : (
                  <View style={{ gap: 10 }}>
                    {sites.map((s) => {
                      const at = membersAt(s.name);
                      const pis = at.filter((m) => (m.role || "").toLowerCase() === "pi").length;
                      return (
                        <Pressable key={s.id} onPress={() => setSiteDetail(s)} style={st.siteCard}>
                          <View style={st.siteIcon}><Building2 size={20} color={C.violet} /></View>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <RNText style={st.siteName} numberOfLines={1}>{s.name}</RNText>
                            {!!s.address && <RNText style={st.siteAddress} numberOfLines={2}>{s.address}</RNText>}
                            <View style={{ flexDirection: "row", gap: 6, marginTop: 6 }}>
                              <View style={st.siteStat}><Users size={11} color={C.mutedFg} /><RNText style={st.siteStatTxt}>{at.length} member{at.length === 1 ? "" : "s"}</RNText></View>
                              <View style={st.siteStat}><Stethoscope size={11} color={C.mutedFg} /><RNText style={st.siteStatTxt}>{pis} PI{pis === 1 ? "" : "s"}</RNText></View>
                            </View>
                          </View>
                          <ChevronRight size={18} color="rgba(123,95,115,0.4)" />
                        </Pressable>
                      );
                    })}
                  </View>
                )}
              </>
            )
          )}

          {tab === "team" && (
            loading ? <Loading label="Loading organization members…" /> : error ? <ErrorCard message={error} onRetry={loadAll} /> : (
              <>
                <TeamRoster
                  members={members}
                  sites={siteNames}
                  roleFilters={["pi", "crc"]}
                  inviteConfig={{ roles: ["pi", "crc"], sites: siteNames }}
                  accentColor={C.violet}
                  allowAssignSite
                  showToast={showToast}
                  onReload={loadAll}
                  onInvite={inviteMember}
                  onDelete={deleteMember}
                  onMakeAdmin={makeAdmin}
                  onAssignSite={assignSite}
                />
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 12, paddingHorizontal: 2 }}>
                  <Building2 size={13} color={C.mutedFg} />
                  <RNText style={st.footNote}>CRCs and research staff can be assigned to any hospital in your network</RNText>
                </View>
              </>
            )
          )}

          {tab === "audit" && (
            <AuditTrail entries={audit} loading={loading} error={auditErr} onRetry={loadAll} />
          )}
        </View>
      </ScrollView>

      {/* Site drill-down: trials at this hospital, supporting team + remove */}
      <Sheet open={!!siteDetail} onClose={() => setSiteDetail(null)} title={siteDetail?.name || "Site"}>
        {siteDetail && (
          <View style={{ gap: 12 }}>
            {!!siteDetail.address && (
              <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 6 }}>
                <MapPin size={13} color={C.mutedFg} style={{ marginTop: 1 }} />
                <RNText style={st.siteAddress}>{siteDetail.address}</RNText>
              </View>
            )}
            <RNText style={st.eyebrow}>TRIALS AT THIS SITE</RNText>
            {trialsAt(siteDetail.name).length === 0 ? (
              <RNText style={st.emptyNote}>No trials are linked to this hospital yet.</RNText>
            ) : (
              <View style={{ gap: 8 }}>
                {trialsAt(siteDetail.name).map((trial) => (
                  <Pressable
                    key={trial.id}
                    onPress={() => {
                      setSiteDetail(null);
                      router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } });
                    }}
                    style={st.memberRow}
                  >
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.memberName} numberOfLines={1}>{trial.protocol_id || trial.title || "Clinical trial"}</RNText>
                      <RNText style={st.memberSub} numberOfLines={1}>
                        {[trial.phase, trial.condition, `${trial.enrolled || 0} patients`].filter(Boolean).join(" · ")}
                      </RNText>
                    </View>
                    <ChevronRight size={17} color="rgba(123,95,115,0.4)" />
                  </Pressable>
                ))}
              </View>
            )}
            <RNText style={[st.eyebrow, { marginTop: 4 }]}>ASSIGNED TEAM</RNText>
            {membersAt(siteDetail.name).length === 0 ? (
              <RNText style={st.emptyNote}>No members assigned to this hospital yet.</RNText>
            ) : (
              <View style={{ gap: 8 }}>
                {membersAt(siteDetail.name).map((m) => (
                  <View key={m.id} style={st.memberRow}>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <RNText style={st.memberName} numberOfLines={1}>{stripTitle(m.name) || m.email}</RNText>
                      <RNText style={st.memberSub} numberOfLines={1}>{[m.role, m.designation].filter(Boolean).join(" · ")}</RNText>
                    </View>
                  </View>
                ))}
              </View>
            )}
            <Pressable onPress={() => removeSite(siteDetail)} style={st.removeBtn}>
              <Trash2 size={14} color={C.destructive} />
              <RNText style={st.removeTxt}>Remove this site</RNText>
            </Pressable>
          </View>
        )}
      </Sheet>

      {/* Add hospital */}
      <AddSiteSheet open={addSite} onClose={() => setAddSite(false)} onSubmit={addSiteToNetwork} />

      <DelegationGate
        open={gate} delegated={delegated} busy={gateBusy}
        onClose={() => setGate(false)} onRequest={requestDelegation} onProceed={proceedToCreate}
      />
      <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} busy={confirmBusy} />
      {ToastView}
    </View>
  );
}

function AddSiteSheet({ open, onClose, onSubmit }: { open: boolean; onClose: () => void; onSubmit: (name: string, address: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setName(""); setAddress(""); setErr(null); setBusy(false); } }, [open]);
  const submit = async () => {
    if (!name.trim()) { setErr("A hospital name is required"); return; }
    setBusy(true); setErr(null);
    try { await onSubmit(name.trim(), address.trim()); }
    catch (e) { setErr(errMsg(e, "Couldn't add the site")); }
    finally { setBusy(false); }
  };
  return (
    <Sheet open={open} onClose={onClose} title="Add affiliated hospital">
      <View style={{ gap: 12 }}>
        <Field label="Hospital name" required><KitInput value={name} onChangeText={(v: string) => setName(sanitizeOrgName(v))} placeholder="e.g. Apollo Mumbai" /></Field>
        <Field label="Hospital address"><KitInput value={address} onChangeText={(v: string) => setAddress(sanitizeAddress(v))} multiline placeholder="Street, city, PIN" /></Field>
        {err && <RNText style={st.errTxt}>{err}</RNText>}
        <PrimaryButton label="Add hospital" disabled={!name.trim()} loading={busy} onPress={submit} bg={C.violet} gradient={false} />
      </View>
    </Sheet>
  );
}

function TrialCard({ t, mine, requested, onOpen, onRequest, actions }: {
  t: OrgTrial; mine: boolean; requested: boolean; onOpen: () => void; onRequest: () => void;
  actions?: React.ReactNode;
}) {
  const full = t.accessLevel === "full";
  const tone = statusTone(t.status);
  const subjects = t.subjects || [];
  return (
    <View style={[st.trialCard, !full && st.trialCardRestricted]}>
      <Pressable onPress={full ? onOpen : undefined} disabled={!full}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <View style={st.protocolPill}><RNText style={st.protocolTxt}>{t.protocol_id || t.id.slice(0, 8)}</RNText></View>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <View style={[st.accessPill, { backgroundColor: full ? "rgba(92,154,110,0.14)" : "rgba(216,154,60,0.16)" }]}>
              {full ? <KeyRound size={11} color={C.success} /> : <Lock size={11} color={C.warning} />}
              <RNText style={[st.accessTxt, { color: full ? C.success : C.warning }]}>
                {mine ? "Full · created by you" : full ? "Full access granted" : "Restricted"}
              </RNText>
            </View>
            {full && <View style={st.arrowBtn}><ArrowUpRight size={14} color="rgba(123,95,115,0.7)" /></View>}
          </View>
        </View>

        <RNText style={st.trialTitle} numberOfLines={2}>{t.title || "Untitled trial"}</RNText>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {!!t.phase && <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={t.phase} />}
          {!!t.condition && <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={t.condition} />}
          <View style={[st.statusBadge, { backgroundColor: tone.bg }]}><RNText style={[st.statusTxt, { color: tone.fg }]}>{t.status || "active"}</RNText></View>
        </View>
      </Pressable>

      {full ? (
        <>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 12 }}>
            <RNText style={st.metaLabel}>ENROLLED</RNText>
            <RNText style={st.metaVal}>
              {t.enrolled || 0}{typeof t.target === "number" ? ` / ${t.target}` : ""} subject{(t.enrolled || 0) === 1 ? "" : "s"}
            </RNText>
          </View>
          {subjects.length > 0 ? (
            <View style={st.subjectBox}>
              {subjects.slice(0, 4).map((s) => (
                <View key={s.subject} style={st.subjectRow}>
                  <RNText style={st.subjectId}>{s.subject}{s.initials ? ` · ${s.initials}` : ""}</RNText>
                  {!!s.status && <RNText style={st.subjectStatus}>{s.status}</RNText>}
                </View>
              ))}
              {subjects.length > 4 && <RNText style={st.subjectMore}>+{subjects.length - 4} more · masked to Subject ID & initials</RNText>}
            </View>
          ) : (
            <RNText style={st.emptyNote}>No subjects enrolled yet.</RNText>
          )}
          <Pressable onPress={onOpen} style={st.openBtn}>
            <FileText size={14} color={C.violet} />
            <RNText style={[st.openBtnTxt, { color: C.violet }]}>Open trial · {t.documentCount || 0} docs</RNText>
          </Pressable>
          {actions}
        </>
      ) : (
        <>
          <View style={st.scheduleBox}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 8 }}>
              <CalendarDays size={12} color={C.mutedFg} />
              <RNText style={st.scheduleEyebrow}>VISIT SCHEDULE</RNText>
            </View>
            {(t.schedule || []).length > 0 ? (
              <View style={{ gap: 6 }}>
                {(t.schedule || []).slice(0, 4).map((v, i) => (
                  <View key={i} style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                    <RNText style={st.visitName} numberOfLines={1}>{v.name || `Visit ${v.visit_number ?? i + 1}`}</RNText>
                    <RNText style={st.visitDay}>
                      {formatVisitTiming(v)} · {formatVisitWindow(v, true)}
                    </RNText>
                  </View>
                ))}
                {(t.schedule || []).length > 4 && <RNText style={st.subjectMore}>+{(t.schedule || []).length - 4} more visits</RNText>}
              </View>
            ) : (
              <RNText style={st.emptyNote}>Schedule not published for this trial.</RNText>
            )}
          </View>
          <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
            {[{ icon: FileText, label: "Documents" }, { icon: Users, label: "Patient details" }].map((l) => (
              <View key={l.label} style={st.lockedTile}>
                <l.icon size={14} color={C.mutedFg} />
                <RNText style={st.lockedTxt}>{l.label}</RNText>
                <Lock size={12} color="rgba(123,95,115,0.5)" />
              </View>
            ))}
          </View>
          <Pressable onPress={requested ? undefined : onRequest} disabled={requested} style={[st.requestBtn, requested && { backgroundColor: C.surface, borderColor: C.border }]}>
            <RNText style={[st.requestTxt, requested && { color: C.mutedFg }]}>{requested ? "Request sent" : "Request full access"}</RNText>
          </Pressable>
        </>
      )}
    </View>
  );
}

function Tag({ bg, fg, label }: { bg: string; fg: string; label: string }) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><RNText style={{ fontSize: 11, fontFamily: fonts.semibold, color: fg }}>{label}</RNText></View>;
}

const st = StyleSheet.create({
  qa: { flex: 1, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 14 },
  qaIcon: { width: 40, height: 40, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  qaLabel: { fontFamily: fonts.semibold, fontSize: 14, color: C.foreground, marginTop: 10 },
  qaSub: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.8, color: C.mutedFg, textTransform: "uppercase" },
  addPill: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, height: 34, borderRadius: 999 },
  addPillTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.white },
  siteCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14 },
  siteIcon: { width: 44, height: 44, borderRadius: 16, backgroundColor: "rgba(142,91,180,0.12)", alignItems: "center", justifyContent: "center" },
  siteName: { fontFamily: fonts.heading, fontSize: 15, color: C.foreground },
  siteAddress: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 2, lineHeight: 15, flex: 1 },
  siteStat: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 999, backgroundColor: C.surface },
  siteStatTxt: { fontFamily: fonts.semibold, fontSize: 10, color: C.mutedFg },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, padding: 12 },
  memberName: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
  memberSub: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 1 },
  removeBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 4, paddingVertical: 12, borderRadius: 999, borderWidth: 1, borderColor: "rgba(192,57,43,0.25)", backgroundColor: "rgba(192,57,43,0.05)" },
  removeTxt: { fontFamily: fonts.bold, fontSize: 13, color: C.destructive },
  footNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, flex: 1, lineHeight: 15 },
  errTxt: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive },

  trialCard: { backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 16 },
  trialCardRestricted: { borderStyle: "dashed" },
  protocolPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.55)" },
  protocolTxt: { fontFamily: fonts.mono, fontSize: 11, color: C.primary },
  accessPill: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, height: 22, borderRadius: 11, justifyContent: "center" },
  accessTxt: { fontFamily: fonts.bold, fontSize: 10 },
  arrowBtn: { width: 26, height: 26, borderRadius: 13, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  trialTitle: { fontFamily: fonts.heading, fontSize: 15, color: C.foreground, lineHeight: 20 },
  statusBadge: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999 },
  statusTxt: { fontFamily: fonts.semibold, fontSize: 11, textTransform: "capitalize" },
  metaLabel: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1, color: "rgba(123,95,115,0.75)" },
  metaVal: { fontFamily: fonts.bold, fontSize: 12, color: C.foreground },
  subjectBox: { marginTop: 8, backgroundColor: C.surface, borderRadius: 12, padding: 10, gap: 6 },
  subjectRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  subjectId: { fontFamily: fonts.mono, fontSize: 11, color: C.foreground },
  subjectStatus: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, textTransform: "capitalize" },
  subjectMore: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg },
  emptyNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, marginTop: 8 },
  openBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface },
  openBtnTxt: { fontFamily: fonts.bold, fontSize: 12 },
  scheduleBox: { marginTop: 12, borderRadius: 12, borderWidth: 1, borderColor: C.border, backgroundColor: C.surface, padding: 10 },
  scheduleEyebrow: { fontFamily: fonts.semibold, fontSize: 10, letterSpacing: 1, color: "rgba(123,95,115,0.7)" },
  visitName: { fontFamily: fonts.medium, fontSize: 11, color: C.foreground, flex: 1, minWidth: 0 },
  visitDay: { fontFamily: fonts.mono, fontSize: 10, color: C.mutedFg },
  lockedTile: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6, borderRadius: 12, borderWidth: 1, borderStyle: "dashed", borderColor: C.border, backgroundColor: "rgba(123,95,115,0.05)", paddingHorizontal: 10, paddingVertical: 10 },
  lockedTxt: { flex: 1, minWidth: 0, fontFamily: fonts.semibold, fontSize: 10, color: C.mutedFg },
  requestBtn: { marginTop: 10, paddingVertical: 11, borderRadius: 999, borderWidth: 1, borderColor: "rgba(142,91,180,0.35)", backgroundColor: "rgba(142,91,180,0.08)", alignItems: "center" },
  requestTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.violet },
});
