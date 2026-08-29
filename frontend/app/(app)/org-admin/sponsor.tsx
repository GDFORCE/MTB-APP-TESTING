// Sponsor / CRO org-admin console — Task 6.6.
//
// A three-panel command deck layered on the sponsor dashboard: Trials (every
// protocol the org runs, with created-by provenance + a recruitment funnel
// derived from real masked subjects), Team (members and invites)
// and Audit (the permanent record). Everything is wired to the live org
// endpoints; subjects are already masked to SUBJ-xxx + initials server-side.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, RefreshControl } from "react-native";
import { useRouter } from "expo-router";
import {
  ArrowUpRight, ChevronDown, PenLine, Search,
} from "lucide-react-native";
import { colors as C, fonts } from "@/src/theme/tokens";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import {
  useOrgContext, useToast, ConsoleHeader, DeckTabs, AuditTrail, TeamRoster,
  Loading, ErrorCard, EmptyCard, KitInput,
  TrialAdminActions,
  errMsg, type OrgMember, type AuditEntry, type OrgTrial, type OrgSubject,
} from "@/src/components/org-admin-kit";

type Funnel = { screened: number; randomized: number; active: number; completed: number };
function funnelOf(subjects?: OrgSubject[]): Funnel | null {
  if (!subjects || subjects.length === 0) return null;
  let randomized = 0, active = 0, completed = 0;
  for (const s of subjects) {
    const st = (s.status || "").toLowerCase();
    if (st.includes("complet")) { completed++; randomized++; }
    else if (st.includes("screen")) { /* screening only */ }
    else { randomized++; if (st.includes("withdraw") || st.includes("drop")) { /* out */ } else active++; }
  }
  return { screened: subjects.length, randomized, active, completed };
}

function statusTone(status?: string): { bg: string; fg: string } {
  const s = (status || "active").toLowerCase();
  if (s === "active") return { bg: "rgba(92,154,110,0.15)", fg: C.success };
  if (s === "completed") return { bg: "rgba(123,107,184,0.15)", fg: C.info };
  if (s === "terminated" || s === "closed") return { bg: "rgba(192,57,43,0.12)", fg: C.destructive };
  return { bg: "rgba(123,95,115,0.12)", fg: C.mutedFg };
}

export default function SponsorConsole() {
  const router = useRouter();
  const { user } = useAuth();
  const { orgId, orgName, loading: orgLoading, error: orgErr, retry } = useOrgContext();
  const { showToast, ToastView } = useToast();

  const [tab, setTab] = useState("trials");
  const [trials, setTrials] = useState<OrgTrial[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditErr, setAuditErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const loadAll = useCallback(async () => {
    if (!orgId) return;
    setError(null); setAuditErr(null);
    try {
      const [t, m] = await Promise.all([
        api.get(`/org/${orgId}/trials`),
        api.get(`/org/${orgId}/members`),
      ]);
      setTrials(Array.isArray(t.data) ? t.data : []);
      setMembers(Array.isArray(m.data) ? m.data : []);
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

  const memberName = useCallback((id?: string) => members.find((m) => m.id === id)?.name || null, [members]);

  const pulse = useMemo(() => {
    const enrolled = trials.reduce((n, t) => n + (t.enrolled || 0), 0);
    const active = trials.filter((t) => (t.status || "active").toLowerCase() === "active").length;
    return [
      { value: trials.length, label: "Total Trials" },
      { value: active, label: "Active" },
      { value: enrolled, label: "Patients" },
      { value: members.filter((m) => m.status === "active").length, label: "Members" },
    ];
  }, [trials, members]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return trials;
    return trials.filter((t) => (t.title || "").toLowerCase().includes(q) || (t.protocol_id || "").toLowerCase().includes(q));
  }, [trials, query]);

  const toggle = (id: string) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  // ── org action handlers (wired to live endpoints) ──
  const inviteMember = async (p: any) => { await api.post(`/org/${orgId}/members/invite`, p); };
  const deleteMember = async (m: OrgMember) => { await api.delete(`/org/${orgId}/members/${m.id}`); };
  const makeAdmin = async (m: OrgMember) => { await api.post(`/org/${orgId}/members/${m.id}/make-admin`); };
  const assignSite = async (m: OrgMember, site: string) => { await api.post(`/org/${orgId}/members/${m.id}/assign-site`, { site }); };

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
          eyebrow="SPONSOR / CRO · ORGANIZATION OVERSIGHT"
          org={orgName} roleLabel="Org Admin"
          glow="rgba(230,155,92,0.30)"
          pulse={pulse} onBack={() => router.back()}
        />

        <View style={{ paddingHorizontal: 16, marginTop: 14 }}>
          <DeckTabs
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
                <View style={st.search}>
                  <Search size={16} color={C.mutedFg} />
                  <KitInput value={query} onChangeText={setQuery} placeholder="Search trials" style={st.searchInput} />
                </View>
                {filtered.length === 0 ? (
                  <EmptyCard icon={Search} title="No trials found" subtitle="Trials your organization runs or has been granted access to appear here." />
                ) : (
                  <View style={{ gap: 12 }}>
                    {filtered.map((t) => {
                      const tone = statusTone(t.status);
                      const funnel = funnelOf(t.subjects);
                      const open = expanded.has(t.id);
                      const creator = memberName(t.createdBy);
                      const mine = t.createdBy === user?.id;
                      return (
                        <View key={t.id} style={st.trialCard}>
                          <Pressable onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: t.id } })}>
                            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                              <View style={st.protocolPill}><RNText style={st.protocolTxt}>{t.protocol_id || t.id.slice(0, 8)}</RNText></View>
                              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                                <View style={[st.badge, { backgroundColor: tone.bg }]}><RNText style={[st.badgeTxt, { color: tone.fg }]}>{(t.status || "active")}</RNText></View>
                                <View style={st.arrowBtn}><ArrowUpRight size={14} color="rgba(123,95,115,0.7)" /></View>
                              </View>
                            </View>
                            <RNText style={st.trialTitle} numberOfLines={2}>{t.title || "Untitled trial"}</RNText>
                            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                              {!!t.phase && <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={t.phase} />}
                              {!!t.condition && <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={t.condition} />}
                              <Tag bg={mine ? "rgba(230,155,92,0.15)" : C.surface} fg={mine ? C.accent : C.mutedFg} label={mine ? "Managed by you" : "Oversight"} />
                            </View>
                            {/* created-by provenance */}
                            <View style={st.provenance}>
                              <PenLine size={12} color={C.mutedFg} />
                              <RNText style={st.provTxt} numberOfLines={1}>
                                Created by <RNText style={{ fontFamily: fonts.semibold, color: C.foreground }}>{creator || "another member"}</RNText>
                              </RNText>
                            </View>
                            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 10 }}>
                              <RNText style={st.enrolled}>
                                {t.enrolled || 0}{typeof t.target === "number" ? ` / ${t.target}` : ""} enrolled
                                {t.accessLevel === "restricted" ? " · schedule-only" : ""}
                              </RNText>
                              <RNText style={st.lastAct}>{t.documentCount || 0} document{(t.documentCount || 0) === 1 ? "" : "s"}</RNText>
                            </View>
                          </Pressable>
                          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: C.border }}>
                            <RNText style={st.lastAct} numberOfLines={1}>{t.schedule?.length || 0} visit template{(t.schedule?.length || 0) === 1 ? "" : "s"}</RNText>
                            <Pressable onPress={() => toggle(t.id)} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                              <RNText style={st.viewDetail}>{open ? "Hide funnel" : "View funnel"}</RNText>
                              <ChevronDown size={15} color={C.accent} style={{ transform: [{ rotate: open ? "180deg" : "0deg" }] }} />
                            </Pressable>
                          </View>
                          {open && (
                            <View style={st.funnelBox}>
                              {funnel ? (
                                <View style={{ flexDirection: "row", gap: 8 }}>
                                  {[
                                    { label: "Screened", val: funnel.screened, fg: C.foreground },
                                    { label: "Randomized", val: funnel.randomized, fg: C.foreground },
                                    { label: "Active", val: funnel.active, fg: C.accent },
                                    { label: "Completed", val: funnel.completed, fg: C.success },
                                  ].map((f) => (
                                    <View key={f.label} style={st.funnelCell}>
                                      <RNText style={[st.funnelVal, { color: f.fg }]}>{f.val}</RNText>
                                      <RNText style={st.funnelLabel}>{f.label}</RNText>
                                    </View>
                                  ))}
                                </View>
                              ) : (
                                <RNText style={st.funnelNote}>Recruitment funnel needs full access — subjects are masked or unavailable for this trial.</RNText>
                              )}
                            </View>
                          )}
                          {orgId && (
                            <TrialAdminActions trial={t} orgId={orgId} showToast={showToast} onChanged={loadAll} />
                          )}
                        </View>
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
                  roleFilters={["sponsor", "cro", "pi", "crc"]}
                  inviteConfig={{ roles: ["sponsor", "cro", "pi", "crc"] }}
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

      {ToastView}
    </View>
  );
}

function Tag({ bg, fg, label }: { bg: string; fg: string; label: string }) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><RNText style={{ fontSize: 11, fontFamily: fonts.semibold, color: fg }}>{label}</RNText></View>;
}

const st = StyleSheet.create({
  search: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.card, borderRadius: 14, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, height: 46, marginBottom: 14 },
  searchInput: { flex: 1, borderWidth: 0, backgroundColor: "transparent", paddingHorizontal: 0, paddingVertical: 0, height: 44 },
  trialCard: { backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 16 },
  protocolPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.55)" },
  protocolTxt: { fontFamily: fonts.mono, fontSize: 11, color: C.primary },
  badge: { paddingHorizontal: 8, height: 22, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  badgeTxt: { fontFamily: fonts.bold, fontSize: 11, textTransform: "capitalize" },
  arrowBtn: { width: 26, height: 26, borderRadius: 13, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" },
  trialTitle: { fontFamily: fonts.heading, fontSize: 15, color: C.foreground, lineHeight: 20 },
  provenance: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10, alignSelf: "flex-start", paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderStyle: "dashed", borderColor: C.border, backgroundColor: C.surface },
  provTxt: { fontFamily: fonts.regular, fontSize: 10, color: C.mutedFg, flexShrink: 1 },
  enrolled: { fontFamily: fonts.semibold, fontSize: 12, color: C.foreground },
  lastAct: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, flex: 1 },
  viewDetail: { fontFamily: fonts.bold, fontSize: 12, color: C.accent },
  funnelBox: { marginTop: 12, backgroundColor: C.surface, borderRadius: 14, padding: 12 },
  funnelCell: { flex: 1, backgroundColor: C.card, borderRadius: 10, borderWidth: 1, borderColor: C.border, paddingVertical: 8, alignItems: "center" },
  funnelVal: { fontFamily: fonts.bold, fontSize: 16, fontVariant: ["tabular-nums"] },
  funnelLabel: { fontFamily: fonts.regular, fontSize: 9, color: C.mutedFg, marginTop: 2 },
  funnelNote: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg, lineHeight: 16 },
});
