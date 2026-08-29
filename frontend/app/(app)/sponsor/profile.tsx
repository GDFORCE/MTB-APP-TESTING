import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import {
  BarChart2,
  Bell,
  Building2,
  Camera,
  ChevronRight,
  FileText,
  FlaskConical,
  HelpCircle,
  Lock,
  LogOut,
  MapPin,
  ScrollText,
  ShieldCheck,
  UserPen,
  Users,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { useAvatarUpload } from "@/src/hooks/use-avatar-upload";
import { AvatarPickerSheet } from "@/src/components/AvatarPickerSheet";
import { fetchFileUri } from "@/src/lib/upload";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type Organization = { type?: string; address?: string };
type MenuRow = {
  icon: typeof UserPen;
  label: string;
  route: string;
};

export default function SponsorProfile() {
  const router = useRouter();
  const { user, signOut, refresh } = useAuth();
  const unread = useUnreadCount();
  const [organization, setOrganization] = useState<Organization>({});
  const [loading, setLoading] = useState(true);
  const [avatarUri, setAvatarUri] = useState<string | null>(null);
  const avatar = useAvatarUpload({
    onUploaded: async (uri) => { setAvatarUri(uri); await refresh(); },
    onRemoved: async () => { setAvatarUri(null); await refresh(); },
  });

  useEffect(() => {
    if (!user?.organization) {
      setLoading(false);
      return;
    }
    api.get("/organizations", { params: { search: user.organization } })
      .then((response) => {
        const list = Array.isArray(response.data) ? response.data : [];
        const match = list.find((item: any) => item.name === user.organization) || list[0];
        if (match) setOrganization(match);
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [user?.organization]);

  useEffect(() => {
    if (!user?.avatar_file_id) { setAvatarUri(null); return; }
    let cancelled = false;
    fetchFileUri(user.avatar_file_id).then((uri) => { if (!cancelled) setAvatarUri(uri); }).catch(() => {});
    return () => { cancelled = true; };
  }, [user?.avatar_file_id]);

  if (!user) return null;

  const entityLabel = user.role === "cro" ? "CRO" : "Sponsor";
  const designation = user.org_admin
    ? `${entityLabel} Admin`
    : user.role === "cro"
      ? "Clinical Research Organization"
      : "Sponsor Representative";
  const initials = user.avatar_initials
    || user.full_name.split(/\s+/).filter(Boolean).map((part) => part[0]).slice(0, 2).join("").toUpperCase()
    || "?";
  const roleLabel = user.role === "cro" ? "CRO" : "SPONSOR";

  const sections: { title: string; rows: MenuRow[] }[] = [
    {
      title: "ACCOUNT",
      rows: [
        { icon: UserPen, label: "Edit Profile", route: "/(app)/clinical/profile/edit" },
        { icon: Building2, label: "Entity Change", route: "/(app)/clinical/profile/entity-change" },
        { icon: Lock, label: "Change Password", route: "/(app)/clinical/profile/change-password" },
        { icon: Bell, label: "Notification Preferences", route: "/(app)/clinical/profile/notifications" },
      ],
    },
    {
      title: "TRIAL MANAGEMENT",
      rows: [
        { icon: FlaskConical, label: "My Trials", route: "/(app)/sponsor/trials" },
        { icon: MapPin, label: "My Sites", route: "/(app)/sponsor/sites" },
        { icon: Users, label: "Organization Members", route: "/(app)/clinical/team" },
      ],
    },
    {
      title: "REPORTS",
      rows: [
        { icon: BarChart2, label: "Reports", route: "/(app)/clinical/profile/reports" },
        { icon: ScrollText, label: "Audit Trail", route: "/(app)/audit-trail" },
        { icon: FileText, label: "T&C", route: "/(app)/clinical/profile/tnc" },
        { icon: HelpCircle, label: "Help & Support", route: "/(app)/clinical/profile/help" },
      ],
    },
  ];

  const logout = async () => {
    await signOut();
    router.replace("/(auth)/welcome" as never);
  };

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}>
          <Text numberOfLines={1} style={s.headerEyebrow}>
            {roleLabel}{user.organization ? ` · ${user.organization.toUpperCase()}` : ""}
          </Text>
          <Text style={s.headerTitle}>Profile</Text>
        </View>
        <Pressable
          accessibilityLabel="Open notifications"
          onPress={() => router.push("/(app)/sponsor/notifications")}
          style={({ pressed }) => [s.headerButton, pressed && s.pressed]}
        >
          <Bell size={19} color={colors.white} />
          {!!unread && unread > 0 && (
            <View style={s.badge}><Text style={s.badgeText}>{unread > 9 ? "9+" : unread}</Text></View>
          )}
        </Pressable>
        <View style={s.headerButton}><Text style={s.headerAvatarText}>{initials}</Text></View>
      </SafeAreaView>

      <ScrollView
        style={s.scroll}
        contentContainerStyle={s.content}
        showsVerticalScrollIndicator={false}
      >
        <View style={s.profileHeader}>
          <View style={s.avatarWrap}>
            {avatarUri ? (
              <Image source={{ uri: avatarUri }} style={s.avatar} />
            ) : (
              <View style={s.avatar}><Text style={s.avatarText}>{initials}</Text></View>
            )}
            <Pressable
              accessibilityLabel="Change profile photo"
              onPress={avatar.openSheet}
              disabled={avatar.avatarBusy}
              style={({ pressed }) => [s.cameraBadge, pressed && s.pressed]}
            >
              {avatar.avatarBusy ? (
                <ActivityIndicator size="small" color={colors.primary} />
              ) : (
                <Camera size={13} color={colors.mutedFg} />
              )}
            </Pressable>
          </View>
          <Text style={s.name}>{user.full_name}</Text>
          <View style={s.designation}><Text style={s.designationText}>{designation}</Text></View>
          {avatar.avatarErr ? <Text style={s.avatarErrText}>{avatar.avatarErr}</Text> : null}
        </View>

        <View style={s.details}>
          {loading ? (
            <ActivityIndicator style={s.loader} color={colors.primary} />
          ) : [
            { label: "Phone Number", value: user.phone || "—", verified: true },
            { label: "Email ID", value: user.email, verified: true },
            { label: "Entity Type", value: entityLabel },
            { label: "Org. Name", value: user.organization || "—" },
            { label: "Org. Address", value: organization.address || "—" },
          ].map((row, index) => (
            <View key={row.label} style={[s.detailRow, index > 0 && s.detailBorder]}>
              <View style={s.detailLabelRow}>
                <Text style={s.detailLabel}>{row.label}</Text>
                {row.verified ? <ShieldCheck size={12} color={colors.warning} /> : null}
              </View>
              <Text style={s.detailValue}>{row.value}</Text>
            </View>
          ))}
        </View>

        <View style={s.verificationNote}>
          <ShieldCheck size={17} color={colors.warning} />
          <Text style={s.verificationText}>
            Changing your <Text style={s.verificationStrong}>Phone Number</Text> or <Text style={s.verificationStrong}>Email ID</Text> requires OTP verification. All active trials will be notified of the change.
          </Text>
        </View>

        {sections.map((section) => (
          <View key={section.title} style={s.section}>
            <Text style={s.sectionTitle}>{section.title}</Text>
            <View style={s.menu}>
              {section.rows.map((row, index) => {
                const Icon = row.icon;
                return (
                  <Pressable
                    key={row.label}
                    accessibilityRole="button"
                    onPress={() => router.push(row.route as never)}
                    style={({ pressed }) => [s.menuRow, index > 0 && s.menuBorder, pressed && s.menuPressed]}
                  >
                    <Icon size={17} color={colors.mutedFg} />
                    <Text style={s.menuLabel}>{row.label}</Text>
                    <ChevronRight size={17} color={colors.border} />
                  </Pressable>
                );
              })}
            </View>
          </View>
        ))}

        <View style={s.logoutCard}>
          <Pressable onPress={logout} style={({ pressed }) => [s.logout, pressed && s.menuPressed]}>
            <LogOut size={17} color={colors.destructive} />
            <Text style={s.logoutText}>Sign Out</Text>
          </Pressable>
        </View>
      </ScrollView>

      <SponsorBottomNav active="me" unread={unread ?? 0} />

      <AvatarPickerSheet
        visible={avatar.sheetOpen}
        onClose={avatar.closeSheet}
        onTakePhoto={avatar.pickFromCamera}
        onChooseFromGallery={avatar.pickFromGallery}
        onRemove={avatar.removeAvatar}
        hasPhoto={!!avatarUri}
      />
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  header: {
    minHeight: 84,
    paddingHorizontal: 16,
    paddingBottom: 12,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 9,
    backgroundColor: colors.primaryDeep,
  },
  headerIdentity: { flex: 1, paddingBottom: 2 },
  headerEyebrow: { color: "rgba(255,255,255,0.58)", fontFamily: fonts.bold, fontSize: 8, letterSpacing: 0.8 },
  headerTitle: { marginTop: 2, color: colors.white, fontFamily: fonts.heading, fontSize: 18 },
  headerButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.16)",
    borderWidth: 1,
    borderColor: "rgba(255,255,255,0.16)",
  },
  headerAvatarText: { color: colors.white, fontFamily: fonts.bold, fontSize: 11 },
  badge: {
    position: "absolute", right: -3, top: -4, minWidth: 17, height: 17,
    paddingHorizontal: 3, borderRadius: 9, alignItems: "center", justifyContent: "center",
    backgroundColor: colors.destructive, borderWidth: 2, borderColor: colors.primaryDeep,
  },
  badgeText: { color: colors.white, fontFamily: fonts.bold, fontSize: 8 },
  scroll: { flex: 1 },
  content: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 28 },
  profileHeader: { alignItems: "center", marginBottom: 24 },
  avatarWrap: { width: 76, height: 80, marginBottom: 7 },
  avatar: {
    width: 72,
    height: 72,
    borderRadius: 36,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primaryDeep,
  },
  avatarText: { color: colors.white, fontFamily: fonts.bold, fontSize: 20 },
  cameraBadge: {
    position: "absolute",
    right: 0,
    bottom: 4,
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    ...shadows.sm,
  },
  name: { color: colors.foreground, fontFamily: fonts.bold, fontSize: 18 },
  designation: { marginTop: 5, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(123,107,184,0.10)" },
  designationText: { color: colors.info, fontFamily: fonts.semibold, fontSize: 10 },
  avatarErrText: { marginTop: 8, color: colors.destructive, fontFamily: fonts.regular, fontSize: 10.5, textAlign: "center" },
  details: { paddingHorizontal: 16, paddingVertical: 7, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  loader: { marginVertical: 28 },
  detailRow: { paddingVertical: 9 },
  detailBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  detailLabelRow: { flexDirection: "row", alignItems: "center", gap: 5 },
  detailLabel: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 10.5 },
  detailValue: { marginTop: 2, color: colors.foreground, fontFamily: fonts.medium, fontSize: 12.5 },
  verificationNote: {
    marginTop: 8,
    marginBottom: 16,
    padding: 12,
    borderRadius: 13,
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "rgba(214,150,54,0.10)",
    borderWidth: 1,
    borderColor: "rgba(214,150,54,0.20)",
  },
  verificationText: { flex: 1, color: colors.warning, fontFamily: fonts.regular, fontSize: 10, lineHeight: 15 },
  verificationStrong: { fontFamily: fonts.semibold },
  section: { marginBottom: 16 },
  sectionTitle: { marginBottom: 8, color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1 },
  menu: { overflow: "hidden", borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  menuRow: { minHeight: 50, paddingHorizontal: 15, flexDirection: "row", alignItems: "center", gap: 12 },
  menuBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  menuPressed: { backgroundColor: colors.surface },
  menuLabel: { flex: 1, color: colors.foreground, fontFamily: fonts.regular, fontSize: 12.5 },
  logoutCard: { marginBottom: 8, overflow: "hidden", borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  logout: { minHeight: 50, paddingHorizontal: 15, flexDirection: "row", alignItems: "center", gap: 12 },
  logoutText: { flex: 1, color: colors.destructive, fontFamily: fonts.semibold, fontSize: 12.5 },
  pressed: { opacity: 0.7 },
});
