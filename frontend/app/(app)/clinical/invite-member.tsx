import React, { useMemo, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { CheckCircle2, Link2, LockKeyhole } from "lucide-react-native";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { Body, Button, Card, Eyebrow, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { useOrgContext } from "@/src/components/org-admin-kit";
import { sanitizeDesignation, sanitizeName } from "@/src/lib/validators";

type InviteRole = "pi" | "crc" | "sponsor" | "cro";

const roleNames: Record<InviteRole, string> = {
  pi: "Principal Investigator",
  crc: "Research Coordinator",
  sponsor: "Sponsor",
  cro: "CRO",
};

export default function InviteMember() {
  const { user } = useAuth();
  const isOrgAdmin = Boolean(user?.org_admin);
  const {
    orgId,
    orgType,
    loading: orgLoading,
    error: orgError,
  } = useOrgContext(isOrgAdmin);
  const roles = useMemo<InviteRole[]>(
    () => orgType === "sponsor" || orgType === "cro"
      ? ["pi", "crc", "sponsor", "cro"]
      : ["pi", "crc"],
    [orgType],
  );
  const [role, setRole] = useState<InviteRole>(
    user?.role === "crc" ? "pi" : "crc",
  );
  const [name, setName] = useState("");
  const [designation, setDesignation] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState<{
    email?: string;
    phone?: string;
    invite_link?: string;
    expires_at?: string;
  } | null>(null);

  const send = async () => {
    const normalizedEmail = email.trim().toLocaleLowerCase();
    const normalizedPhone = phone.trim();
    if (!/\S+@\S+\.\S+/.test(normalizedEmail)) {
      setError("Enter a valid email address for the organization member.");
      return;
    }
    if (!isOrgAdmin) {
      setError("Organization Admin access is required to invite members.");
      return;
    }
    if (!orgId) {
      setError(orgError || "Your organization is still loading. Please try again.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = {
        full_name: name.trim() || undefined,
        designation: designation.trim() || undefined,
        email: normalizedEmail || undefined,
        phone: normalizedPhone || undefined,
        role,
      };
      const response = await api.post(`/org/${orgId}/members/invite`, payload);
      setInvite(response.data);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(
        (typeof detail === "string" ? detail : undefined)
        || "Couldn't send the invitation. Please try again.",
      );
    } finally {
      setLoading(false);
    }
  };

  if (!isOrgAdmin) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Organization access" title="Invite unavailable" />
        <View style={s.deniedContent}>
          <LockKeyhole size={38} color={colors.mutedFg} />
          <Body weight="700">Organization Admin access required</Body>
          <Small style={s.deniedText}>Only an Organization Admin can invite organization members.</Small>
        </View>
      </ScreenContainer>
    );
  }

  if (invite) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Organization access" title="Invitation Sent" />
        <ScrollView contentContainerStyle={s.successContent}>
          <View style={s.successIcon}>
            <CheckCircle2 size={36} color={colors.success} />
          </View>
          <Body weight="700" style={s.successTitle}>Your invitation is ready</Body>
          <Small style={s.successText}>
            {name.trim() || email.trim() || phone.trim()} can use the secure link
            to join {user?.organization || "your organization"} as {roleNames[role]}.
          </Small>
          {invite.invite_link ? (
            <Card style={s.linkCard}>
              <View style={s.linkTitle}>
                <Link2 size={16} color={colors.primary} />
                <Eyebrow>Secure invite link</Eyebrow>
              </View>
              <Small
                testID="member-invite-link"
                selectable
                color={colors.primary}
                style={s.linkText}
              >
                {invite.invite_link}
              </Small>
              <Small style={s.linkHelp}>
                Long-press the link to copy it. The link expires after three days.
              </Small>
            </Card>
          ) : null}
          <Button
            testID="invite-another-member"
            variant="secondary"
            style={s.fullButton}
            onPress={() => {
              setInvite(null);
              setName("");
              setDesignation("");
              setEmail("");
              setPhone("");
            }}
          >
            Invite another member
          </Button>
        </ScrollView>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Organization access" title="Add Organization Member" />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={s.flex}
      >
        <ScrollView
          contentContainerStyle={s.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <Card style={s.scopeCard}>
            <View style={s.scopeIcon}>
              <LockKeyhole size={18} color={colors.success} />
            </View>
            <View style={s.scopeText}>
              <Body weight="700">Organization-scoped access</Body>
              <Small style={s.scopeSub}>
                Please send the invitation only to members working in your organization. Don&apos;t send to external members as they may join into your organization and get access to your organizational information.
              </Small>
            </View>
          </Card>

          <Eyebrow style={s.label}>Member role</Eyebrow>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={s.roles}
          >
            {roles.map((item) => (
              <Button
                key={item}
                testID={`invite-role-${item}`}
                variant={role === item ? "primary" : "secondary"}
                style={s.roleButton}
                onPress={() => setRole(item)}
              >
                {roleNames[item]}
              </Button>
            ))}
          </ScrollView>

          <Field
            label="Full name"
            value={name}
            onChangeText={(v) => setName(sanitizeName(v))}
            placeholder="e.g. Dr. Aisha Rao"
            testID="member-invite-name"
          />
          <Field
            label="Designation"
            value={designation}
            onChangeText={(v) => setDesignation(sanitizeDesignation(v))}
            placeholder="e.g. Senior Research Coordinator"
            testID="member-invite-designation"
          />
          <Field
            label="Email address"
            value={email}
            onChangeText={(value) => {
              setEmail(value);
              if (error) setError("");
            }}
            placeholder="name@organization.com"
            keyboardType="email-address"
            autoCapitalize="none"
            testID="member-invite-email"
          />
          <Field
            label="Phone number (optional)"
            value={phone}
            onChangeText={(value) => {
              setPhone(value);
              if (error) setError("");
            }}
            placeholder="+91 98765 43210"
            keyboardType="phone-pad"
            testID="member-invite-phone"
          />
          {isOrgAdmin && orgError ? (
            <Small color={colors.destructive} style={s.error}>
              {orgError}
            </Small>
          ) : null}
          {error ? (
            <Small testID="member-invite-error" color={colors.destructive} style={s.error}>
              {error}
            </Small>
          ) : null}
        </ScrollView>
        <View style={s.footer}>
          <Button
            testID="member-invite-send"
            onPress={() => void send()}
            loading={loading || (isOrgAdmin && orgLoading)}
            disabled={isOrgAdmin && !orgLoading && !orgId}
          >
            Send secure invitation
          </Button>
        </View>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

function Field({ label, ...props }: React.ComponentProps<typeof TextInput> & {
  label: string;
}) {
  return (
    <View style={s.field}>
      <Small color={colors.foreground} style={s.fieldLabel}>{label}</Small>
      <TextInput
        {...props}
        placeholderTextColor={colors.mutedFg}
        style={s.input}
      />
    </View>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  deniedContent: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  deniedText: { textAlign: "center", maxWidth: 320 },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  scopeCard: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    borderRadius: radii.lg,
  },
  scopeIcon: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: `${colors.success}18`,
    alignItems: "center",
    justifyContent: "center",
  },
  scopeText: { flex: 1 },
  scopeSub: { marginTop: 3, lineHeight: 18 },
  label: { marginTop: spacing.lg, marginBottom: spacing.sm },
  roles: { gap: 8, paddingBottom: 4 },
  roleButton: { paddingHorizontal: 15, paddingVertical: 10 },
  field: { marginTop: spacing.md },
  fieldLabel: { marginBottom: 6, fontFamily: fonts.semibold },
  input: {
    minHeight: 48,
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radii.md,
    backgroundColor: colors.card,
    color: colors.foreground,
    fontFamily: fonts.regular,
    fontSize: 14,
  },
  error: { marginTop: 10 },
  footer: {
    paddingHorizontal: spacing.md,
    paddingVertical: 12,
    borderTopWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.background,
  },
  successContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
    alignItems: "center",
  },
  successIcon: {
    width: 78,
    height: 78,
    borderRadius: 39,
    backgroundColor: `${colors.success}18`,
    alignItems: "center",
    justifyContent: "center",
  },
  successTitle: { marginTop: spacing.md, fontSize: 19 },
  successText: {
    marginTop: 6,
    maxWidth: 330,
    textAlign: "center",
    lineHeight: 19,
  },
  linkCard: { alignSelf: "stretch", marginTop: spacing.lg },
  linkTitle: { flexDirection: "row", alignItems: "center", gap: 8 },
  linkText: { marginTop: 10, lineHeight: 19 },
  linkHelp: { marginTop: 9 },
  fullButton: { alignSelf: "stretch", marginTop: spacing.lg },
});
