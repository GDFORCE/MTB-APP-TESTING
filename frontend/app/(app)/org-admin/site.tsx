// Site (hospital) org-admin console — Task 6.6.
//
// The site admin's command deck, layered on the research-team dashboard. Its
// signature is the access key: trials the org created (or was granted) open
// fully — enrolment + masked subjects; every other trial renders restricted —
// visit schedule only, documents locked, "request full access". Team and Audit
// reuse the shared kit. Trial creation sits behind the DelegationGate and hands
// off to the real add-trial flow (the only trial-write endpoint the API has).

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, RefreshControl } from "react-native";
import { usePathname, useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  KeyRound, Lock, Plus, UserPlus, ShieldCheck, Landmark, CalendarDays, FileText,
  Users, ArrowUpRight, Search,
} from "lucide-react-native";
import { colors as C, fonts } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { formatVisitTiming, formatVisitWindow } from "@/src/lib/visit-timing";
import {
  useOrgContext, useToast, ConsoleHeader, DeckTabs, AuditTrail, TeamRoster,
  DelegationGate, Loading, ErrorCard, EmptyCard, KitInput,
  TrialAdminActions,
  errMsg, type OrgMember, type AuditEntry, type OrgTrial,
} from "@/src/components/org-admin-kit";

function statusTone(status?: string): { bg: string; fg: string } {
  const s = (status || "active").toLowerCase();
  if (s === "active") return { bg: "rgba(92,154,110,0.15)", fg: C.success };
  if (s === "completed") return { bg: "rgba(123,107,184,0.15)", fg: C.info };
  if (s === "terminated" || s === "closed") return { bg: "rgba(192,57,43,0.12)", fg: C.destructive };
  return { bg: "rgba(123,95,115,0.12)", fg: C.mutedFg };
}

const ADD_PATIENT_ROUTE = "/(app)/clinical/add-patient";

export default function SiteConsole() {
  const router = useRouter();
  const pathname = usePathname();
  const { user } = useAuth();
  const { orgId, orgName, loading: orgLoading, error: orgErr, retry } = useOrgContext();
  const { showToast, ToastView } = useToast();

  const [tab, setTab] = useState("trials");
  const [trials, setTrials] = useState<OrgTrial[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [delegated, setDelegated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditErr, setAuditErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [requested, setRequested] = useState<Set<string>>(new Set());
  const [gate, setGate] = useState(false);
  const [gateBusy, setGateBusy] = useState(false);

  const loadAll = useCallback(async () => {
    if (!orgId) return;
    setError(null); setAuditErr(null);
    try {
      const [t, m, d] = await Promise.all([
        api.get(`/org/${orgId}/trials`),
        api.get(`/org/${orgId}/members`),
        api.get(`/org/${orgId}/delegation-status`).catch(() => ({ data: { delegated: false } })),
      ]);
      setTrials(Array.isArray(t.data) ? t.data : []);
      setMembers(Array.isArray(m.data) ? m.data : []);
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

  const pulse = useMemo(() => {
    const patients = trials.reduce((n, t) => n + (t.enrolled || 0), 0);
    const pis = members.filter((m) => (m.role || "").toLowerCase() === "pi" && m.status !== "rejected").length;
    return [
      { value: trials.length, label: "Total Trials" },
      { value: pis, label: "Total PIs" },
      { value: patients, label: "Total Patients" },
      { value: members.filter((m) => m.status === "active").length, label: "Organization Members" },
    ];
  }, [trials, members]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return trials;
    return trials.filter((t) => (t.title || "").toLowerCase().includes(q) || (t.protocol_id || "").toLowerCase().includes(q));
  }, [trials, query]);

  // ── org action handlers (live endpoints) ──
  const inviteMember = async (p: any) => {
    const role = p.role === "PI" ? "pi" : p.role === "Research Team" ? "crc" : p.role;
    await api.post(`/org/${orgId}/members/invite`, { ...p, role });
  };
  const deleteMember = async (m: OrgMember) => { await api.delete(`/org/${orgId}/members/${m.id}`); };
  const makeAdmin = async (m: OrgMember) => { await api.post(`/org/${orgId}/members/${m.id}/make-admin`); };
  const assignSite = async (m: OrgMember, site: string) => { await api.post(`/org/${orgId}/members/${m.id}/assign-site`, { site }); };

  const requestAccess = async (t: OrgTrial) => {
    setRequested((prev) => new Set(prev).add(t.id));
    try {
      await api.post(`/trials/${t.id}/access-requests`, { org_id: orgId, reason: "Site admin requesting full access" });
      showToast("Full-access request sent to the trial owner");
      loadAll();
    } catch (e) {
      setRequested((prev) => { const n = new Set(prev); n.delete(t.id); return n; });
      showToast(errMsg(e, "Couldn't send the access request"));
    }
  };

  // Trial creation is gated by delegation; the only write path is the real
  // add-trial flow (POST /trials), so we hand off there once cleared.
  const requestDelegation = async () => {
    setGateBusy(true);
    try {
      await api.post(`/org/${orgId}/delegation-requests`, { reason: "Requesting trial-creation delegation for this site" });
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
          eyebrow="SITE · ORGANIZATION MANAGEMENT"
          org={orgName} roleLabel="Site Admin"
          note="You're also the site administrator — manage trials, access, organization members and audit"
          glow="rgba(123,107,184,0.34)"
          pulse={pulse}
          onBack={() => pathname.includes("/site/dashboard")
            ? router.replace("/(app)/clinical/profile")
            : router.back()}
        />

        <View style={{ paddingHorizontal: 16, marginTop: 14 }}>
          <DeckTabs
            activeColor={C.info}
            tabs={[
              { key: "trials", label: "Trials", count: trials.length },
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
                {/* Quick actions — Site Admin is also the PI */}
                <View style={{ flexDirection: "row", gap: 12, marginBottom: 16 }}>
                  <Pressable onPress={() => setGate(true)} style={st.qa}>
                    <View style={[st.qaIcon, { backgroundColor: C.info }]}><Plus size={20} color={C.infoFg} /></View>
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

                <View style={st.search}>
                  <Search size={16} color={C.mutedFg} />
                  <KitInput value={query} onChangeText={setQuery} placeholder="Search trials" style={st.searchInput} />
                </View>

                {filtered.length === 0 ? (
                  <EmptyCard icon={Search} title="No trials found" subtitle="Trials your site runs or has been granted access to appear here." />
                ) : (
                  <View style={{ gap: 12 }}>
                    {filtered.map((t) => (
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

          {tab === "team" && (
            loading ? <Loading label="Loading organization members…" /> : error ? <ErrorCard message={error} onRetry={loadAll} /> : (
              <>
                <TeamRoster
                  members={members}
                  roleFilters={["pi", "crc"]}
                  inviteConfig={{ roles: ["PI", "Research Team"] }}
                  accentColor={C.info}
                  showToast={showToast}
                  onReload={loadAll}
                  onInvite={inviteMember}
                  onDelete={deleteMember}
                  onMakeAdmin={makeAdmin}
                  onAssignSite={assignSite}
                />
              </>
            )
          )}

          {tab === "audit" && (
            <AuditTrail entries={audit} loading={loading} error={auditErr} onRetry={loadAll} />
          )}
        </View>
      </ScrollView>

      <DelegationGate
        open={gate} delegated={delegated} busy={gateBusy}
        onClose={() => setGate(false)} onRequest={requestDelegation} onProceed={proceedToCreate}
      />
      {ToastView}
    </View>
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
            <FileText size={14} color={C.info} />
            <RNText style={[st.openBtnTxt, { color: C.info }]}>Open trial · {t.documentCount || 0} docs</RNText>
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
  search: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, height: 46, marginBottom: 14 },
  searchInput: { flex: 1, borderWidth: 0, backgroundColor: "transparent", paddingHorizontal: 0, paddingVertical: 0, height: 44 },
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
  requestBtn: { marginTop: 10, paddingVertical: 11, borderRadius: 999, borderWidth: 1, borderColor: "rgba(123,107,184,0.35)", backgroundColor: "rgba(123,107,184,0.08)", alignItems: "center" },
  requestTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.info },
});
