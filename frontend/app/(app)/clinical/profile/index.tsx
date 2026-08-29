import React, { useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, ScrollView, StatusBar, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import {
  BarChart2, Bell, Building2, Camera, ChevronRight, FileText, FlaskConical,
  HelpCircle, KeyRound, Lock, LogOut, MapPin, ScrollText, ShieldCheck,
  UserPen, Users,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { useAvatarUpload } from "@/src/hooks/use-avatar-upload";
import { AvatarPickerSheet } from "@/src/components/AvatarPickerSheet";
import { fetchFileUri } from "@/src/lib/upload";
import { PiBottomNav } from "@/src/features/clinical/components/PiBottomNav";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type MenuItem = { icon: typeof UserPen; label: string; route: string };
type Organization = { address?: string; type?: string };

const TYPE_LABEL: Record<string, string> = {
  sponsor: "Sponsor", cro: "CRO", smo: "SMO", site: "Site / Hospital",
};

export default function ClinicalProfile() {
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
    if (!user?.organization) { setLoading(false); return; }
    api.get("/organizations", { params: { search: user.organization } })
      .then(response => {
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
  const isPi = user.role === "pi";
  const isCrc = user.role === "crc";
  const isSmo = user.role === "smo";
  const isSite = user.role === "site";
  const navRole = isCrc ? "crc" : isSite ? "site" : isSmo ? "smo" : "pi";
  const designation = isPi ? "Principal Investigator" : isSmo ? "SMO Operations" : isSite ? "Site Administrator" : "Clinical Research Coordinator";
  const roleLabel = isPi ? "PRINCIPAL INVESTIGATOR" : isSmo ? "SMO" : isSite ? "SITE" : "RESEARCH TEAM";
  const entity = TYPE_LABEL[user.role] || (isPi ? "Site / Hospital" : "Clinical Site");
  const initials = user.avatar_initials || user.full_name.split(/\s+/).filter(Boolean).map(part => part[0]).slice(0, 2).join("").toUpperCase() || "?";

  const sections: { title: string; rows: MenuItem[] }[] = [
    { title: "ACCOUNT", rows: [
      { icon: UserPen, label: "Edit Profile", route: "/(app)/clinical/profile/edit" },
      { icon: Building2, label: "Entity Change", route: "/(app)/clinical/profile/entity-change" },
      { icon: Lock, label: "Change Password", route: "/(app)/clinical/profile/change-password" },
      { icon: Bell, label: "Notification Preferences", route: "/(app)/clinical/profile/notifications" },
    ] },
    { title: "TRIAL MANAGEMENT", rows: [
      { icon: FlaskConical, label: "My Trials", route: "/(app)/clinical/my-trials" },
      { icon: Users, label: "Organization Members", route: "/(app)/clinical/team" },
      ...(isSmo && user.org_admin ? [{ icon: MapPin, label: "Managed Hospitals", route: "/(app)/org-admin/smo" }] : []),
      ...(user.org_admin ? [{ icon: KeyRound, label: "Trial Access Requests", route: "/(app)/org-admin/trial-access-requests" }] : []),
    ] },
    { title: "REPORTS & SUPPORT", rows: [
      { icon: BarChart2, label: "Reports", route: "/(app)/clinical/profile/reports" },
      { icon: ScrollText, label: "Audit Trail", route: "/(app)/audit-trail" },
      { icon: FileText, label: "T&C", route: "/(app)/clinical/profile/tnc" },
      { icon: HelpCircle, label: "Help & Support", route: "/(app)/clinical/profile/help" },
    ] },
  ];

  const logout = async () => { await signOut(); router.replace("/(auth)/welcome" as never); };
  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}><Text numberOfLines={1} style={s.headerEyebrow}>{roleLabel}{user.organization ? ` · ${user.organization.toUpperCase()}` : ""}</Text><Text style={s.headerTitle}>Profile</Text></View>
        <Pressable onPress={() => router.push("/(app)/notifications")} style={({ pressed }) => [s.headerButton, pressed && s.pressed]}><Bell size={19} color={colors.white} />{!!unread && <View style={s.badge}><Text style={s.badgeText}>{unread > 9 ? "9+" : unread}</Text></View>}</Pressable>
        <View style={s.headerButton}><Text style={s.headerAvatarText}>{initials}</Text></View>
      </SafeAreaView>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={s.content} showsVerticalScrollIndicator={false}>
        <View style={s.profileHeader}>
          <View style={s.avatarWrap}>
            {avatarUri ? <Image source={{ uri: avatarUri }} style={s.avatar} /> : <View style={s.avatar}><Text style={s.avatarText}>{initials}</Text></View>}
            <Pressable accessibilityLabel="Change profile photo" onPress={avatar.openSheet} disabled={avatar.avatarBusy} style={s.cameraBadge}>
              {avatar.avatarBusy ? <ActivityIndicator size="small" color={colors.primary} /> : <Camera size={13} color={colors.mutedFg} />}
            </Pressable>
          </View>
          <Text style={s.name}>{user.full_name}</Text><View style={s.designation}><Text style={s.designationText}>{designation}</Text></View>
          {avatar.avatarErr ? <Text style={s.avatarErrText}>{avatar.avatarErr}</Text> : null}
        </View>

        <View style={s.details}>
          {loading ? <ActivityIndicator style={{ marginVertical: 28 }} color={colors.primary} /> : [
            { label: "Phone Number", value: user.phone || "—", verified: true }, { label: "Email ID", value: user.email, verified: true },
            { label: "Entity Type", value: entity }, { label: "Org. Name", value: user.organization || "—" },
            { label: "Org. Address", value: organization.address || "—" }, { label: "Role", value: isPi ? "PI" : designation },
          ].map((row, index) => <View key={row.label} style={[s.detailRow, index > 0 && s.detailBorder]}><View style={s.detailLabelRow}><Text style={s.detailLabel}>{row.label}</Text>{row.verified && <ShieldCheck size={12} color={colors.warning} />}</View><Text style={s.detailValue}>{row.value}</Text></View>)}
        </View>

        <View style={s.verification}><ShieldCheck size={17} color={colors.warning} /><Text style={s.verificationText}>Phone and email changes require OTP verification.</Text></View>

        {sections.map(section => <View key={section.title} style={s.section}><Text style={s.sectionTitle}>{section.title}</Text><View style={s.menu}>{section.rows.map((row, index) => { const Icon = row.icon; return <Pressable key={row.label} onPress={() => router.push(row.route as never)} style={({ pressed }) => [s.menuRow, index > 0 && s.menuBorder, pressed && s.menuPressed]}><View style={s.menuIcon}><Icon size={17} color={colors.mutedFg} /></View><Text style={s.menuLabel}>{row.label}</Text><ChevronRight size={17} color={colors.border} /></Pressable>; })}</View></View>)}

        <Pressable onPress={logout} style={({ pressed }) => [s.logout, pressed && s.menuPressed]}><LogOut size={17} color={colors.destructive} /><Text style={s.logoutText}>Sign Out</Text></Pressable>
      </ScrollView>
      {(isPi || isCrc || isSmo || isSite) && (
        <PiBottomNav
          active="profile"
          calendarRole={navRole}
          role={navRole}
        />
      )}

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
  header: { minHeight: 84, paddingHorizontal: 16, paddingBottom: 12, flexDirection: "row", alignItems: "flex-end", gap: 9, backgroundColor: colors.primaryDeep },
  headerIdentity: { flex: 1, paddingBottom: 2 }, headerEyebrow: { color: "rgba(255,255,255,0.58)", fontFamily: fonts.bold, fontSize: 8, letterSpacing: 0.8 }, headerTitle: { marginTop: 2, color: colors.white, fontFamily: fonts.heading, fontSize: 18 },
  headerButton: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.16)", borderWidth: 1, borderColor: "rgba(255,255,255,0.16)" }, headerAvatarText: { color: colors.white, fontFamily: fonts.bold, fontSize: 11 },
  badge: { position: "absolute", right: -3, top: -4, minWidth: 17, height: 17, paddingHorizontal: 3, borderRadius: 9, alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive, borderWidth: 2, borderColor: colors.primaryDeep }, badgeText: { color: colors.white, fontFamily: fonts.bold, fontSize: 8 },
  content: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 30 }, profileHeader: { alignItems: "center", marginBottom: 20 }, avatarWrap: { width: 76, height: 80, marginBottom: 7 },
  avatar: { width: 72, height: 72, borderRadius: 36, alignItems: "center", justifyContent: "center", backgroundColor: colors.primaryDeep }, avatarText: { color: colors.white, fontFamily: fonts.bold, fontSize: 20 },
  cameraBadge: { position: "absolute", right: 0, bottom: 4, width: 26, height: 26, borderRadius: 13, alignItems: "center", justifyContent: "center", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, ...shadows.sm },
  name: { color: colors.foreground, fontFamily: fonts.bold, fontSize: 18 }, designation: { marginTop: 5, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: colors.info + "18" }, designationText: { color: colors.info, fontFamily: fonts.semibold, fontSize: 10 }, avatarErrText: { marginTop: 8, color: colors.destructive, fontFamily: fonts.regular, fontSize: 10.5, textAlign: "center" },
  details: { paddingHorizontal: 16, paddingVertical: 7, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm }, detailRow: { paddingVertical: 9 }, detailBorder: { borderTopWidth: 1, borderTopColor: colors.border + "99" }, detailLabelRow: { flexDirection: "row", alignItems: "center", gap: 4 }, detailLabel: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11 }, detailValue: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 13, marginTop: 2 },
  verification: { marginTop: 12, padding: 12, borderRadius: 14, flexDirection: "row", alignItems: "center", gap: 9, borderWidth: 1, borderColor: colors.warning + "4D", backgroundColor: colors.warning + "12" }, verificationText: { flex: 1, color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11 },
  section: { marginTop: 18 }, sectionTitle: { color: colors.mutedFg, fontFamily: fonts.bold, fontSize: 9, letterSpacing: 1.2, marginBottom: 7, marginLeft: 4 }, menu: { overflow: "hidden", borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm }, menuRow: { minHeight: 51, paddingHorizontal: 13, flexDirection: "row", alignItems: "center", gap: 10 }, menuBorder: { borderTopWidth: 1, borderTopColor: colors.border }, menuPressed: { opacity: 0.65 }, menuIcon: { width: 30, height: 30, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface }, menuLabel: { flex: 1, color: colors.foreground, fontFamily: fonts.medium, fontSize: 13 },
  logout: { marginTop: 20, minHeight: 50, borderRadius: 18, borderWidth: 1, borderColor: colors.destructive + "45", backgroundColor: colors.card, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 }, logoutText: { color: colors.destructive, fontFamily: fonts.bold, fontSize: 13 }, pressed: { opacity: 0.7 },
});
