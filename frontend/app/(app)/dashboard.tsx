import React, { useCallback, useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable, RefreshControl } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Bell, MessageCircle, Calendar, FlaskConical, Users, LogOut, ChevronRight, Activity, Clock, CheckCircle2, User2 } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, SectionHeader } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";

const ROLE_LABEL: Record<string, string> = {
  patient: "Patient", pi: "Principal Investigator", crc: "Research Coordinator",
  sponsor: "Sponsor", cro: "CRO", smo: "SMO", site: "Site",
};

export default function Dashboard() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<any[]>([]);
  const [visits, setVisits] = useState<any[]>([]);
  const [patients, setPatients] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const reqs: any[] = [api.get("/trials"), api.get("/notifications")];
      if (user?.role === "patient") reqs.push(api.get("/visits/mine"));
      else reqs.push(api.get("/patients").catch(() => ({ data: [] })));
      const [t, n, x] = await Promise.all(reqs);
      setTrials(t.data); setNotifs(n.data);
      if (user?.role === "patient") setVisits(x.data); else setPatients(x.data);
    } catch {
      setLoadError("Couldn't refresh the dashboard. Pull down to try again.");
    }
  }, [user?.role]);
  useEffect(() => { load(); }, [load]);

  if (!user) return null;
  const role = user.role;
  const isPatient = role === "patient";
  const upcoming = visits.filter(v => v.status === "upcoming").sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date))[0];
  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ paddingBottom: 100 }} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} />} showsVerticalScrollIndicator={false}>
        {/* Dawn hero */}
        <LinearGradient colors={[colors.primary, colors.primaryDeep]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={s.heroRow}>
            <View style={{ flex: 1 }}>
              <Eyebrow color={colors.overlay25}>Welcome back</Eyebrow>
              <H1 color={colors.primaryFg}>Hi, {user.full_name.split(" ")[0]}</H1>
              <Small color={colors.overlay25}>{ROLE_LABEL[role]} {user.organization ? `· ${user.organization}` : ""}</Small>
            </View>
            <View style={{ flexDirection: "row", gap: spacing.sm }}>
              <Pressable testID="header-chat" onPress={() => router.push("/(app)/chat")} style={s.iconBtn}><MessageCircle size={20} color={colors.primaryFg} /></Pressable>
              <Pressable testID="header-logout" onPress={async () => { await signOut(); router.replace("/(auth)/welcome"); }} style={s.iconBtn}><LogOut size={20} color={colors.primaryFg} /></Pressable>
            </View>
          </View>

          {/* Progress panel - patient only */}
          {isPatient && (
            <View style={s.progressPanel}>
              <View style={s.ring}>
                <Body weight="700" color={colors.primaryFg} style={{ fontSize: 18 }}>{pct}%</Body>
              </View>
              <View style={{ flex: 1 }}>
                <Eyebrow color={colors.overlay25}>Your progress</Eyebrow>
                <Body weight="700" color={colors.primaryFg} style={{ marginTop: 2 }}>Visit {completed} of {total} completed</Body>
                <View style={{ flexDirection: "row", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  <View style={s.chip}><Activity size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} style={{ fontWeight: "700" as any, fontSize: 11 }}>{pct}% visits complete</Small></View>
                  {upcoming && <View style={s.chip}><Clock size={12} color={colors.primaryFg} /><Small color={colors.primaryFg} style={{ fontWeight: "700" as any, fontSize: 11 }}>Next in {Math.max(0, Math.ceil((new Date(upcoming.scheduled_date).getTime() - Date.now()) / 86400000))}d</Small></View>}
                </View>
              </View>
            </View>
          )}

          {/* Non-patient quick stats */}
          {!isPatient && (
            <View style={{ flexDirection: "row", gap: spacing.sm, marginTop: spacing.md }}>
              <StatCard label="Trials" value={trials.length} />
              <StatCard label={role === "sponsor" || role === "cro" ? "Patients" : "My patients"} value={patients.length} />
              <StatCard label="Updates" value={notifs.filter(n => !n.read).length} />
            </View>
          )}
        </LinearGradient>

        {/* Next visit / Today's actions */}
        <View style={{ paddingHorizontal: spacing.md, marginTop: spacing.lg }}>
          {!!loadError && (
            <Card style={{ marginBottom: spacing.md, borderColor: colors.destructive + "55" }}>
              <Small color={colors.destructive}>{loadError}</Small>
            </Card>
          )}
          {isPatient ? (
            upcoming ? (
              <>
                <SectionHeader index="01" label="Next visit" action={<Pressable testID="open-my-trial" onPress={() => router.push("/(app)/patient/my-trial")}><Small color={colors.accent} style={{ fontWeight: "700" as any }}>My Trial →</Small></Pressable>} />
                <Pressable testID="next-visit-card" onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: upcoming.id } })}>
                  <Card>
                    <View style={{ flexDirection: "row", gap: spacing.md }}>
                      <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.dateBlock}>
                        <Body weight="700" color={colors.primaryFg} style={{ fontSize: 24, lineHeight: 26 }}>{new Date(upcoming.scheduled_date).getDate()}</Body>
                        <Eyebrow color={colors.primaryFg}>{new Date(upcoming.scheduled_date).toLocaleString("en-US", { month: "short" })}</Eyebrow>
                      </LinearGradient>
                      <View style={{ flex: 1 }}>
                        <Eyebrow color={colors.accent}>In {Math.max(0, Math.ceil((new Date(upcoming.scheduled_date).getTime() - Date.now()) / 86400000))} days</Eyebrow>
                        <Body weight="700" style={{ marginTop: 4 }}>Visit {upcoming.visit_number} · {upcoming.name}</Body>
                        {!!(upcoming.protocol_id || upcoming.site) && (
                          <Small style={{ marginTop: 4 }}>
                            {[upcoming.protocol_id, upcoming.site].filter(Boolean).join(" · ")}
                          </Small>
                        )}
                        {!!upcoming.activities?.length && (
                          <Small style={{ marginTop: 4 }}>
                            Activities: {upcoming.activities.slice(0, 2).join(", ")}
                          </Small>
                        )}
                      </View>
                    </View>
                  </Card>
                </Pressable>
              </>
            ) : <Card><Small>No upcoming visits</Small></Card>
          ) : (
            <>
              <SectionHeader index="01" label="Your trials" action={<Pressable testID="open-trials" onPress={() => router.push("/(app)/clinical/my-trials")}><Small color={colors.accent} style={{ fontWeight: "700" as any }}>See all</Small></Pressable>} />
              {trials.length === 0 ? <Card><Small>No trials yet</Small></Card> :
                trials.slice(0, 3).map(t => (
                  <Pressable key={t.id} testID={`dash-trial-${t.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: t.id } })}>
                    <Card style={{ marginBottom: spacing.sm }}>
                      <View style={{ flexDirection: "row", alignItems: "center" }}>
                        <View style={s.trialIcon}><FlaskConical size={20} color={colors.primary} /></View>
                        <View style={{ flex: 1, marginLeft: 12 }}>
                          <Eyebrow color={colors.accent}>{t.protocol_id}</Eyebrow>
                          <Body weight="700" style={{ marginTop: 2 }}>{t.title}</Body>
                          <Small style={{ marginTop: 2 }}>{t.phase} · {t.condition}</Small>
                        </View>
                        <ChevronRight size={18} color={colors.mutedFg} />
                      </View>
                    </Card>
                  </Pressable>
                ))
              }
              {role === "sponsor" && (
                <Pressable testID="open-add-trial" onPress={() => router.push("/(app)/sponsor/add-trial")}>
                  <Card style={{ borderStyle: "dashed", borderColor: colors.primary + "66", backgroundColor: colors.secondary + "44", alignItems: "center" }}><Small weight="700" color={colors.primary}>+ Add new trial</Small></Card>
                </Pressable>
              )}
            </>
          )}
        </View>

        {/* Patients (for clinical roles) */}
        {!isPatient && patients.length > 0 && (
          <View style={{ paddingHorizontal: spacing.md, marginTop: spacing.lg }}>
            <SectionHeader index="02" label="Patients" action={<Pressable testID="open-patients" onPress={() => router.push("/(app)/clinical/patients")}><Small color={colors.accent} style={{ fontWeight: "700" as any }}>See all</Small></Pressable>} />
            {patients.slice(0, 4).map(p => (
              <Pressable key={p.id} testID={`dash-patient-${p.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.id } })}>
                <Card style={{ marginBottom: spacing.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "center" }}>
                    <View style={s.avatar}><Body weight="700" color={colors.primary}>{p.avatar_initials}</Body></View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <Body weight="700">{p.full_name}</Body>
                      <Small style={{ marginTop: 2 }}>{p.email}</Small>
                    </View>
                    <View style={s.statusPill}><Small color={colors.success} style={{ fontWeight: "700" as any }}>Active</Small></View>
                  </View>
                </Card>
              </Pressable>
            ))}
            {(role === "pi" || role === "crc") && (
              <Pressable testID="open-add-patient" onPress={() => router.push("/(app)/clinical/add-patient")}>
                <Card style={{ borderStyle: "dashed", borderColor: colors.primary + "66", backgroundColor: colors.secondary + "44", alignItems: "center" }}><Small weight="700" color={colors.primary}>+ Add patient</Small></Card>
              </Pressable>
            )}
          </View>
        )}

        {/* Visit schedule for patient */}
        {isPatient && visits.length > 0 && (
          <View style={{ paddingHorizontal: spacing.md, marginTop: spacing.lg }}>
            <SectionHeader index="02" label="Visit schedule" />
            {visits.slice(0, 6).map(v => (
              <Card key={v.id} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <View style={[s.visitNum, v.status === "completed" && { backgroundColor: colors.success + "22" }]}>
                    {v.status === "completed" ? <CheckCircle2 size={20} color={colors.success} /> : <Body weight="700" color={colors.primary}>{v.visit_number}</Body>}
                  </View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Body weight="700">{v.name}</Body>
                    <Small style={{ marginTop: 2 }}>{new Date(v.scheduled_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</Small>
                  </View>
                  <View style={[s.statusPill, v.status === "upcoming" && { backgroundColor: colors.warning + "22" }, v.status === "completed" && { backgroundColor: colors.success + "22" }]}>
                    <Small color={v.status === "completed" ? colors.success : v.status === "upcoming" ? colors.warning : colors.mutedFg} style={{ fontWeight: "700" as any }}>{v.status}</Small>
                  </View>
                </View>
              </Card>
            ))}
          </View>
        )}

        {/* Notifications */}
        <View style={{ paddingHorizontal: spacing.md, marginTop: spacing.lg }}>
          <SectionHeader index={isPatient ? "03" : "03"} label="Updates" />
          {notifs.length === 0 ? <Card><Small>No notifications</Small></Card> :
            notifs.slice(0, 4).map(n => (
              <Card key={n.id} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "flex-start", gap: spacing.md }}>
                  <View style={[s.notifIcon, n.kind === "message" ? { backgroundColor: colors.violet + "1A" } : { backgroundColor: colors.accent + "26" }]}>
                    {n.kind === "message" ? <MessageCircle size={18} color={colors.violet} /> : <Bell size={18} color={colors.accent} />}
                  </View>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Body weight="700" style={{ flex: 1 }}>{n.title}</Body>
                      {!n.read && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent }} />}
                    </View>
                    <Small style={{ marginTop: 2 }}>{n.body}</Small>
                  </View>
                </View>
              </Card>
            ))}
        </View>
      </ScrollView>

      {/* Bottom tab */}
      <View style={s.tabBar}>
        <TabBtn icon={<FlaskConical size={20} color={colors.primary} />} label="Home" active />
        {isPatient && <TabBtn icon={<FlaskConical size={20} color={colors.mutedFg} />} label="My Trial" testID="tab-my-trial" onPress={() => router.push("/(app)/patient/my-trial")} />}
        {!isPatient && <TabBtn icon={<FlaskConical size={20} color={colors.mutedFg} />} label="Trials" testID="tab-trials" onPress={() => router.push("/(app)/clinical/my-trials")} />}
        {!isPatient && <TabBtn icon={<Users size={20} color={colors.mutedFg} />} label={role === "sponsor" || role === "cro" ? "Patients" : "People"} testID="tab-people" onPress={() => router.push(role === "crc" ? "/(app)/clinical/schedule-review" : "/(app)/clinical/patients")} />}
        <TabBtn
          icon={<Calendar size={20} color={colors.mutedFg} />}
          label="Calendar"
          testID="tab-calendar"
          onPress={() => isPatient
            ? router.push("/(app)/patient/calendar")
            : router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: role === "crc" ? "crc" : "pi" } })}
        />
        <TabBtn icon={<MessageCircle size={20} color={colors.mutedFg} />} label="Chat" onPress={() => router.push("/(app)/chat")} testID="tab-chat" />
        {!isPatient && <TabBtn icon={<Users size={20} color={colors.mutedFg} />} label="Team" testID="tab-team" onPress={() => router.push("/(app)/clinical/team")} />}
        <TabBtn icon={
          <View>
            <Bell size={20} color={colors.mutedFg} />
            {unread != null && unread > 0 && (
              <View style={s.tabBadge}><Small color={colors.destructiveFg} style={{ fontSize: 9, fontWeight: "700" as any }}>{unread > 9 ? "9+" : unread}</Small></View>
            )}
          </View>
        } label="Alerts" testID="tab-alerts" onPress={() => router.push("/(app)/notifications")} />
        {isPatient && <TabBtn icon={<User2 size={20} color={colors.mutedFg} />} label="Me" testID="tab-me" onPress={() => router.push("/(app)/patient/profile")} />}
      </View>
    </SafeAreaView>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <View style={s.statCard}>
      <Body weight="700" color={colors.primaryFg} style={{ fontSize: 22 }}>{value}</Body>
      <Eyebrow color={colors.overlay25}>{label}</Eyebrow>
    </View>
  );
}
function TabBtn({ icon, label, active, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={s.tabBtn}>
      {icon}
      <Small color={active ? colors.primary : colors.mutedFg} style={{ fontSize: 10, fontWeight: active ? "700" : "500" as any, marginTop: 4 }}>{label}</Small>
    </Pressable>
  );
}

const s = StyleSheet.create({
  hero: { paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.xl, borderBottomLeftRadius: 28, borderBottomRightRadius: 28 },
  heroRow: { flexDirection: "row", alignItems: "center" },
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" },
  progressPanel: { flexDirection: "row", alignItems: "center", gap: spacing.md, marginTop: spacing.md, padding: spacing.md, borderRadius: radii.xl, backgroundColor: colors.overlay10, borderWidth: 1, borderColor: colors.overlay20 },
  ring: { width: 64, height: 64, borderRadius: 32, borderWidth: 4, borderColor: colors.overlay25, alignItems: "center", justifyContent: "center" },
  chip: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  statCard: { flex: 1, padding: 12, borderRadius: radii.lg, backgroundColor: colors.overlay10, borderWidth: 1, borderColor: colors.overlay20 },
  dateBlock: { paddingHorizontal: 14, paddingVertical: 10, borderRadius: radii.lg, alignItems: "center", justifyContent: "center" },
  trialIcon: { width: 44, height: 44, borderRadius: 14, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.success + "22" },
  visitNum: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  notifIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center" },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: colors.card, borderTopWidth: 1, borderColor: colors.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
  tabBtn: { flex: 1, alignItems: "center", justifyContent: "center" },
  tabBadge: { position: "absolute", top: -6, right: -10, minWidth: 16, height: 16, paddingHorizontal: 3, borderRadius: 8, backgroundColor: colors.destructive, alignItems: "center", justifyContent: "center" },
});
