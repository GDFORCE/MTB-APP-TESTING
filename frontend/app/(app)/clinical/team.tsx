import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import {
  ChevronRight,
  ChevronDown,
  ChevronUp,
  FlaskConical,
  Mail,
  MessageCircle,
  Phone,
  Search,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react-native";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { Body, Button, Card, Eyebrow, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { OwnershipTransferCard } from "@/src/components/org-admin-kit";

type TeamRole = "pi" | "crc" | "sponsor" | "cro" | "smo" | "site";

type TeamMember = {
  id: string;
  full_name?: string;
  email?: string;
  phone?: string;
  role: TeamRole;
  organization?: string;
  designation?: string;
  profile?: { designation?: string };
  avatar_initials?: string;
  is_online?: boolean;
  status?: string;
  trials?: { id: string; label: string }[];
  capabilities?: {
    can_edit?: boolean;
    can_remove?: boolean;
  };
};

type TeamResponse = TeamMember[] | {
  members?: TeamMember[];
  capabilities?: {
    can_invite?: boolean;
    can_edit_members?: boolean;
    can_remove_members?: boolean;
  };
};

const roleOrder: TeamRole[] = ["pi", "crc", "sponsor", "cro", "smo", "site"];
const roleLabel: Record<TeamRole, string> = {
  pi: "Principal Investigators",
  crc: "Coordinators",
  sponsor: "Sponsors",
  cro: "CRO",
  smo: "SMO",
  site: "Site team",
};

function initials(member: Pick<TeamMember, "full_name" | "avatar_initials">) {
  if (member.avatar_initials) return member.avatar_initials;
  return (member.full_name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

export default function Team() {
  const router = useRouter();
  const { user } = useAuth();
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<TeamRole | "all">("all");
  const [expandedMember, setExpandedMember] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get<TeamResponse>("/team");
      const payload = response.data;
      const rows = Array.isArray(payload) ? payload : payload.members || [];
      setMembers(rows.map((member) => ({
        ...member,
        designation: member.designation || member.profile?.designation,
      })));
    } catch {
      setError("Couldn't load organization members. Please try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canInvite = Boolean(user?.org_admin);

  const allMembers = useMemo<TeamMember[]>(() => {
    if (!user) return members;
    return [{
      id: user.id,
      full_name: user.full_name,
      email: user.email,
      phone: user.phone,
      role: user.role as TeamRole,
      organization: user.organization,
      avatar_initials: user.avatar_initials,
      status: "Active",
    }, ...members.filter((member) => member.id !== user.id)];
  }, [members, user]);

  const roles = useMemo(
    () => roleOrder.filter((item) => allMembers.some((member) => member.role === item)),
    [allMembers],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return allMembers.filter((member) => {
      if (role !== "all" && member.role !== role) return false;
      if (!needle) return true;
      return [
        member.full_name,
        member.email,
        member.phone,
        member.organization,
        member.designation,
        member.role,
      ].some((value) => value?.toLocaleLowerCase().includes(needle));
    });
  }, [allMembers, query, role]);

  const openMember = (member: TeamMember) => {
    if (member.id === user?.id) return;
    router.push({
      pathname: "/(app)/clinical/team-member",
      params: { id: member.id },
    });
  };

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow={`${allMembers.length} member${allMembers.length === 1 ? "" : "s"}`}
        title="Organization Members"
        right={canInvite ? (
          <Pressable
            accessibilityLabel="Invite organization member"
            testID="invite-fab"
            onPress={() => router.push("/(app)/clinical/invite-member")}
            style={s.fab}
          >
            <UserPlus size={18} color={colors.primaryFg} />
          </Pressable>
        ) : undefined}
      />

      <ScrollView
        contentContainerStyle={s.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={s.summaryRow}>
          <Summary
            icon={Users}
            value={allMembers.length}
            label="Total"
            color={colors.primary}
          />
          <Summary
            icon={ShieldCheck}
            value={allMembers.filter((member) => member.role === "pi").length}
            label="PIs"
            color={colors.info}
          />
          <Summary
            icon={MessageCircle}
            value={allMembers.filter((member) => member.role === "crc").length}
            label="CRCs"
            color={colors.success}
          />
        </View>

        <OwnershipTransferCard
          adminLabel={user?.role === "smo" ? "SMO Admin" : user?.role === "site" ? "Site Admin" : "Org Admin"}
        />

        <View style={s.search}>
          <Search size={17} color={colors.mutedFg} />
          <TextInput
            testID="team-search"
            value={query}
            onChangeText={setQuery}
            placeholder="Search name, role or organization"
            placeholderTextColor={colors.mutedFg}
            autoCapitalize="none"
            style={s.searchInput}
          />
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={s.filters}
        >
          <FilterChip label="All roles" active={role === "all"} onPress={() => setRole("all")} />
          {roles.map((item) => (
            <FilterChip
              key={item}
              label={roleLabel[item]}
              active={role === item}
              onPress={() => setRole(item)}
            />
          ))}
        </ScrollView>

        <View style={s.sectionTitle}>
          <Eyebrow>Organization members</Eyebrow>
          {!loading && !error ? (
            <Small>{visible.length} shown</Small>
          ) : null}
        </View>

        {loading ? (
          <View style={s.loading}>
            <ActivityIndicator color={colors.primary} />
            <Small>Loading organization members…</Small>
          </View>
        ) : error ? (
          <Card style={s.stateCard}>
            <Small color={colors.destructive}>{error}</Small>
            <Button variant="ghost" style={s.retry} onPress={() => void load()}>
              Try again
            </Button>
          </Card>
        ) : visible.length === 0 ? (
          <Card style={s.stateCard}>
            <Body weight="700">No matching members</Body>
            <Small style={s.stateText}>Try another search or role filter.</Small>
          </Card>
        ) : (
          visible.map((member) => {
            const isYou = member.id === user?.id;
            const trials = member.trials || [];
            const isExpanded = expandedMember === member.id;
            return (
              <Card key={member.id} style={[s.memberCard, isYou && s.youCard]}>
                <Pressable
                  testID={`team-${member.id}`}
                  disabled={isYou}
                  onPress={() => openMember(member)}
                  style={({ pressed }) => [s.memberOverview, { opacity: pressed ? 0.82 : 1 }]}
                >
                  <View style={s.avatarWrap}>
                    <View style={s.avatar}>
                      <Body weight="700" color={colors.primary}>{initials(member)}</Body>
                    </View>
                    {member.is_online ? <View style={s.online} /> : null}
                  </View>
                  <View style={s.memberBody}>
                    <View style={s.nameRow}>
                      <Body weight="700" numberOfLines={1} style={s.memberName}>
                        {member.full_name || member.email || "Unnamed member"}
                      </Body>
                      {isYou ? (
                        <View style={s.youPill}><Small color={colors.accentFg}>You</Small></View>
                      ) : null}
                    </View>
                    <Small color={colors.primary} style={s.roleText}>
                      {roleLabel[member.role] || member.role.toUpperCase()}
                    </Small>
                    <Small numberOfLines={1} style={s.meta}>
                      {member.designation || member.organization || member.email || "Organization member"}
                    </Small>
                  </View>
                  {!isYou ? (
                    <View style={s.memberActions}>
                      <Pressable
                        testID={`team-message-${member.id}`}
                        accessibilityLabel={`Message ${member.full_name || "organization member"}`}
                        hitSlop={6}
                        onPress={(event) => {
                          event.stopPropagation();
                          router.push({
                            pathname: "/(app)/chat",
                            params: { participantId: member.id },
                          });
                        }}
                        style={s.messageButton}
                      >
                        <MessageCircle size={17} color={colors.primary} />
                      </Pressable>
                      <ChevronRight size={18} color={colors.mutedFg} />
                    </View>
                  ) : null}
                </Pressable>

                <View style={s.memberDetails}>
                  {member.phone ? (
                    <View style={s.contactRow}>
                      <Phone size={13} color={colors.mutedFg} />
                      <Small numberOfLines={1}>{member.phone}</Small>
                    </View>
                  ) : null}
                  {member.email ? (
                    <View style={s.contactRow}>
                      <Mail size={13} color={colors.mutedFg} />
                      <Small numberOfLines={1}>{member.email}</Small>
                    </View>
                  ) : null}
                </View>

                <Pressable
                  testID={`team-trials-${member.id}`}
                  accessibilityRole="button"
                  accessibilityState={{ expanded: isExpanded }}
                  accessibilityLabel={`${isExpanded ? "Hide" : "Show"} trials for ${member.full_name || "organization member"}`}
                  onPress={() => setExpandedMember((current) => current === member.id ? null : member.id)}
                  style={s.trialsToggle}
                >
                  <View style={s.trialsLabel}>
                    <FlaskConical size={13} color={colors.primary} />
                    <Small color={colors.primary}>
                      {trials.length} trial{trials.length === 1 ? "" : "s"} involved
                    </Small>
                  </View>
                  {isExpanded ? <ChevronUp size={16} color={colors.primary} /> : <ChevronDown size={16} color={colors.primary} />}
                </Pressable>

                {isExpanded ? (
                  <View style={s.trialList}>
                    {trials.length ? trials.map((trial) => (
                      <View key={trial.id} style={s.trialRow}>
                        <View style={s.trialBullet} />
                        <Small style={s.trialName}>{trial.label}</Small>
                      </View>
                    )) : <Small style={s.noTrials}>No trials currently assigned.</Small>}
                  </View>
                ) : null}
              </Card>
            );
          })
        )}

        {canInvite ? (
          <Button
            testID="invite-member"
            variant="secondary"
            style={s.inviteButton}
            onPress={() => router.push("/(app)/clinical/invite-member")}
          >
            Invite organization member
          </Button>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

function Summary({ icon: Icon, value, label, color }: {
  icon: React.ComponentType<{ size?: number; color?: string }>;
  value: number;
  label: string;
  color: string;
}) {
  return (
    <Card style={s.summary}>
      <View style={[s.summaryIcon, { backgroundColor: `${color}18` }]}>
        <Icon size={17} color={color} />
      </View>
      <Body weight="700" style={s.summaryValue}>{value}</Body>
      <Small>{label}</Small>
    </Card>
  );
}

function FilterChip({ label, active, onPress }: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[s.filter, active && s.filterActive]}
    >
      <Small color={active ? colors.primaryFg : colors.mutedFg}>{label}</Small>
    </Pressable>
  );
}

const s = StyleSheet.create({
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  fab: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: colors.overlay20,
    alignItems: "center",
    justifyContent: "center",
  },
  summaryRow: { flexDirection: "row", gap: 8 },
  summary: {
    flex: 1,
    minWidth: 0,
    padding: 12,
    alignItems: "center",
    borderRadius: radii.lg,
  },
  summaryIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 6,
  },
  summaryValue: { fontSize: 19 },
  search: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: spacing.md,
    paddingHorizontal: 13,
    minHeight: 46,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.lg,
    backgroundColor: colors.card,
  },
  searchInput: {
    flex: 1,
    color: colors.foreground,
    fontFamily: fonts.regular,
    fontSize: 14,
    paddingVertical: 10,
  },
  filters: { gap: 8, paddingVertical: 12 },
  filter: {
    paddingHorizontal: 13,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.pill,
    backgroundColor: colors.card,
  },
  filterActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  sectionTitle: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
    marginBottom: spacing.sm,
  },
  loading: { alignItems: "center", gap: 8, paddingVertical: spacing.xl },
  stateCard: { alignItems: "center", marginTop: 4 },
  stateText: { marginTop: 4, textAlign: "center" },
  retry: { marginTop: 8, alignSelf: "stretch" },
  memberCard: {
    marginBottom: 9,
    borderRadius: radii.lg,
    padding: 12,
  },
  memberOverview: { flexDirection: "row", alignItems: "center", gap: 12 },
  youCard: { borderColor: `${colors.accent}66` },
  avatarWrap: { position: "relative" },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 16,
    backgroundColor: colors.secondary,
    alignItems: "center",
    justifyContent: "center",
  },
  online: {
    position: "absolute",
    right: -1,
    bottom: -1,
    width: 12,
    height: 12,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: colors.card,
    backgroundColor: colors.success,
  },
  memberBody: { flex: 1, minWidth: 0 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  memberName: { flexShrink: 1 },
  youPill: {
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: radii.pill,
    backgroundColor: `${colors.accent}20`,
  },
  roleText: { marginTop: 2, fontSize: 11, textTransform: "uppercase" },
  meta: { marginTop: 2 },
  memberActions: { flexDirection: "row", alignItems: "center", gap: 4 },
  messageButton: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: `${colors.primary}10`,
  },
  memberDetails: {
    marginTop: 10,
    paddingTop: 9,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    gap: 5,
  },
  contactRow: { flexDirection: "row", alignItems: "center", gap: 7, minWidth: 0 },
  trialsToggle: {
    marginTop: 10,
    minHeight: 34,
    paddingHorizontal: 10,
    borderRadius: radii.pill,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: `${colors.primary}0D`,
  },
  trialsLabel: { flexDirection: "row", alignItems: "center", gap: 6 },
  trialList: { paddingHorizontal: 8, paddingTop: 9, gap: 7 },
  trialRow: { flexDirection: "row", alignItems: "flex-start", gap: 7 },
  trialBullet: { width: 5, height: 5, borderRadius: 3, marginTop: 5, backgroundColor: colors.primary },
  trialName: { flex: 1, lineHeight: 17 },
  noTrials: { paddingBottom: 2, color: colors.mutedFg },
  inviteButton: { marginTop: spacing.md },
});
