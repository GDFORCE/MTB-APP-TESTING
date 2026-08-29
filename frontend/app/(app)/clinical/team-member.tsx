import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  Building2,
  Mail,
  Pencil,
  Phone,
  ShieldCheck,
  Trash2,
  UserRound,
  X,
} from "lucide-react-native";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { Body, Button, Card, Eyebrow, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { sanitizeDesignation, sanitizeDigits, sanitizeName } from "@/src/lib/validators";

type Member = {
  id: string;
  full_name?: string;
  email?: string;
  phone?: string;
  role?: string;
  designation?: string;
  profile?: { designation?: string };
  organization?: string;
  avatar_initials?: string;
  is_online?: boolean;
  status?: string;
  capabilities?: {
    can_edit?: boolean;
    can_remove?: boolean;
  };
};

type TeamPayload = Member[] | {
  members?: Member[];
  capabilities?: {
    can_edit_members?: boolean;
    can_remove_members?: boolean;
  };
};

function getInitials(member: Member) {
  return member.avatar_initials || (member.full_name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

export default function TeamMemberDetail() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string | string[] }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;
  const [member, setMember] = useState<Member | null>(null);
  const [teamCaps, setTeamCaps] = useState<{
    can_edit_members?: boolean;
    can_remove_members?: boolean;
  }>({});
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState({
    full_name: "",
    designation: "",
    phone: "",
    role: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const response = await api.get<TeamPayload>("/team");
        const payload = response.data;
        const rows = Array.isArray(payload) ? payload : payload.members || [];
        const raw = rows.find((row) => row.id === id) || null;
        const selected = raw ? {
          ...raw,
          designation: raw.designation || raw.profile?.designation,
        } : null;
        if (!alive) return;
        setMember(selected);
        setTeamCaps(Array.isArray(payload) ? {} : payload.capabilities || {});
        if (selected) {
          setForm({
            full_name: selected.full_name || "",
            designation: selected.designation || "",
            phone: selected.phone || "",
            role: selected.role || "",
          });
        }
      } catch {
        if (alive) setError("Couldn't load this organization member.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [id]);

  const canEdit = Boolean(
    member?.capabilities?.can_edit ?? teamCaps.can_edit_members,
  );
  const canRemove = Boolean(
    member?.capabilities?.can_remove ?? teamCaps.can_remove_members,
  );

  const save = async () => {
    if (!member || !canEdit) return;
    setSaving(true);
    setError("");
    try {
      const response = await api.patch(`/team/${member.id}`, {
        full_name: form.full_name.trim(),
        designation: form.designation.trim(),
        phone: form.phone.trim(),
        role: form.role,
      });
      setMember({ ...member, ...response.data });
      setMode("view");
    } catch (err: any) {
      setError(
        err?.response?.data?.detail
        || "Couldn't save this member. Please try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  const remove = () => {
    if (!member || !canRemove) return;
    Alert.alert(
      "Remove organization member?",
      `${member.full_name || member.email || "This member"} will lose access to the organization and its trials.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: async () => {
            try {
              await api.delete(`/team/${member.id}`);
              router.back();
            } catch (err: any) {
              setError(
                err?.response?.data?.detail
                || "Couldn't remove this member.",
              );
            }
          },
        },
      ],
    );
  };

  const headerAction = useMemo(() => {
    if (!member || !canEdit) return undefined;
    if (mode === "edit") {
      return (
        <Pressable
          testID="member-cancel-edit"
          onPress={() => setMode("view")}
          style={s.headerButton}
        >
          <X size={18} color={colors.primaryFg} />
        </Pressable>
      );
    }
    return (
      <Pressable
        testID="member-edit"
        onPress={() => setMode("edit")}
        style={s.headerButton}
      >
        <Pencil size={17} color={colors.primaryFg} />
      </Pressable>
    );
  }, [canEdit, member, mode]);

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow={mode === "edit" ? "Editing access profile" : "Organization member"}
        title={mode === "edit" ? "Edit Member" : "Member Details"}
        right={headerAction}
      />
      {loading ? (
        <View style={s.loading}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : !member ? (
        <View style={s.loading}>
          <Body weight="700">Member not found</Body>
          <Small style={s.notFoundText}>
            This person is no longer in your organization member scope.
          </Small>
        </View>
      ) : (
        <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
          <View style={s.identity}>
            <View style={s.avatar}>
              <Body weight="700" color={colors.primary} style={s.avatarText}>
                {getInitials(member)}
              </Body>
            </View>
            <Body weight="700" style={s.name}>
              {member.full_name || member.email || "Unnamed member"}
            </Body>
            <View style={s.rolePill}>
              <ShieldCheck size={12} color={colors.primary} />
              <Small color={colors.primary} style={s.rolePillText}>
                {(member.role || "team").toUpperCase()}
              </Small>
            </View>
            <Small style={s.status}>
              {member.is_online ? "Online now" : member.status || "Organization member"}
            </Small>
          </View>

          {error ? (
            <Card style={s.errorCard}>
              <Small color={colors.destructive}>{error}</Small>
            </Card>
          ) : null}

          <Eyebrow style={s.sectionLabel}>Profile details</Eyebrow>
          <Card style={s.detailsCard}>
            {mode === "edit" ? (
              <>
                <EditableRow
                  icon={UserRound}
                  label="Full name"
                  value={form.full_name}
                  onChangeText={(value) => setForm((current) => ({ ...current, full_name: sanitizeName(value) }))}
                />
                <EditableRow
                  icon={ShieldCheck}
                  label="Designation"
                  value={form.designation}
                  onChangeText={(value) => setForm((current) => ({ ...current, designation: sanitizeDesignation(value) }))}
                />
                <EditableRow
                  icon={Phone}
                  label="Phone"
                  value={form.phone}
                  keyboardType="phone-pad"
                  onChangeText={(value) => setForm((current) => ({ ...current, phone: sanitizeDigits(value, 10) }))}
                />
                <DetailRow icon={Mail} label="Email (account identifier)" value={member.email} />
                <DetailRow icon={Building2} label="Organization" value={member.organization} last />
              </>
            ) : (
              <>
                <DetailRow icon={ShieldCheck} label="Designation" value={member.designation || member.role} />
                <DetailRow icon={Mail} label="Email" value={member.email} />
                <DetailRow icon={Phone} label="Phone" value={member.phone} />
                <DetailRow icon={Building2} label="Organization" value={member.organization} last />
              </>
            )}
          </Card>

          {mode === "view" ? (
            <View style={s.actions}>
              <Button
                testID="member-message"
                onPress={() => router.push({
                  pathname: "/(app)/chat",
                  params: { participantId: member.id },
                })}
              >
                Message organization member
              </Button>
              {member.phone ? (
                <Button
                  testID="member-call"
                  variant="secondary"
                  onPress={() => void Linking.openURL(`tel:${member.phone}`)}
                >
                  Call
                </Button>
              ) : null}
            </View>
          ) : (
            <Button
              testID="member-save"
              onPress={() => void save()}
              loading={saving}
              style={s.saveButton}
            >
              Save member details
            </Button>
          )}

          {canRemove && mode === "view" ? (
            <Pressable testID="member-remove" onPress={remove} style={s.remove}>
              <Trash2 size={16} color={colors.destructive} />
              <Small color={colors.destructive}>Remove from organization</Small>
            </Pressable>
          ) : null}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

function DetailRow({ icon: Icon, label, value, last }: {
  icon: React.ComponentType<{ size?: number; color?: string }>;
  label: string;
  value?: string;
  last?: boolean;
}) {
  return (
    <View style={[s.detailRow, last && s.lastRow]}>
      <View style={s.detailIcon}><Icon size={16} color={colors.primary} /></View>
      <View style={s.detailBody}>
        <Small>{label}</Small>
        <Body style={s.detailValue}>{value || "Not provided"}</Body>
      </View>
    </View>
  );
}

function EditableRow({ icon: Icon, label, ...props }: {
  icon: React.ComponentType<{ size?: number; color?: string }>;
  label: string;
} & React.ComponentProps<typeof TextInput>) {
  return (
    <View style={s.editRow}>
      <View style={s.detailIcon}><Icon size={16} color={colors.primary} /></View>
      <View style={s.detailBody}>
        <Small>{label}</Small>
        <TextInput
          {...props}
          placeholderTextColor={colors.mutedFg}
          style={s.input}
        />
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  notFoundText: { textAlign: "center", marginTop: 5 },
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  headerButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: colors.overlay20,
    alignItems: "center",
    justifyContent: "center",
  },
  identity: { alignItems: "center", paddingVertical: spacing.md },
  avatar: {
    width: 78,
    height: 78,
    borderRadius: 26,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.secondary,
  },
  avatarText: { fontSize: 23 },
  name: { marginTop: 12, fontSize: 19, textAlign: "center" },
  rolePill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    marginTop: 7,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: radii.pill,
    backgroundColor: `${colors.primary}12`,
  },
  rolePillText: { fontSize: 10, fontFamily: fonts.semibold },
  status: { marginTop: 6 },
  errorCard: { marginBottom: spacing.md },
  sectionLabel: { marginTop: 4, marginBottom: spacing.sm },
  detailsCard: { paddingVertical: 4 },
  detailRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 11,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderColor: colors.border,
  },
  lastRow: { borderBottomWidth: 0 },
  detailIcon: {
    width: 34,
    height: 34,
    borderRadius: 12,
    backgroundColor: `${colors.primary}10`,
    alignItems: "center",
    justifyContent: "center",
  },
  detailBody: { flex: 1, minWidth: 0 },
  detailValue: { marginTop: 2 },
  editRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 11,
    paddingVertical: 8,
  },
  input: {
    marginTop: 4,
    minHeight: 42,
    paddingHorizontal: 11,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.background,
    color: colors.foreground,
    fontFamily: fonts.regular,
    fontSize: 14,
  },
  actions: { gap: 9, marginTop: spacing.md },
  saveButton: { marginTop: spacing.md },
  remove: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    marginTop: spacing.lg,
    padding: 12,
  },
});
