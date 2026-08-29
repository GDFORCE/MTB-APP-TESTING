// Responsive Platform Admin portal shell.
//
// Wide web/tablet screens use the persistent sidebar and top utility bar from
// the approved UI demo. Phones retain the compact slide-in drawer. Every
// destination remains a real Expo route and global search reads live admin
// endpoints rather than presenting demo-only records.
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import {
  ActivityIndicator, Animated, Modal, Pressable, ScrollView, StyleSheet,
  Text as RNText, TextInput, useWindowDimensions, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Redirect, Slot, usePathname, useRouter, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  BarChart3, Bell, BellRing, Building2, ChevronRight, Database, FileText,
  FlaskConical, Inbox, KeyRound, LayoutDashboard, LogOut, Mail,
  MessageSquare, Search, Share2, ShieldAlert, ScrollText, Settings, UserCog,
  Users, X, type LucideIcon,
} from "lucide-react-native";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { colors as C, fonts } from "@/src/theme/tokens";

const W = {
  w10: "rgba(255,255,255,0.10)",
  w15: "rgba(255,255,255,0.15)",
  w25: "rgba(255,255,255,0.25)",
  w55: "rgba(255,255,255,0.55)",
  w70: "rgba(255,255,255,0.70)",
};
const DESKTOP_BREAKPOINT = 900;
const SIDEBAR_W = 240;

export type AdminNavItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  href: Href;
  group: "Core" | "Operations" | "Governance" | "More";
};

export const ADMIN_NAV: AdminNavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, href: "/(app)/admin" as Href, group: "Core" },
  { id: "users", label: "Users", icon: Users, href: "/(app)/admin/users" as Href, group: "Core" },
  { id: "organizations", label: "Organizations", icon: Building2, href: "/(app)/admin/organizations" as Href, group: "Core" },
  { id: "trials", label: "Trial Management", icon: FlaskConical, href: "/(app)/admin/trials" as Href, group: "Core" },
  { id: "master-data", label: "Master Data", icon: Database, href: "/(app)/admin/master-data" as Href, group: "Operations" },
  { id: "tickets", label: "Support Tickets", icon: Inbox, href: "/(app)/admin/tickets" as Href, group: "Operations" },
  { id: "alerts", label: "System Alerts", icon: ShieldAlert, href: "/(app)/admin/alerts" as Href, group: "Operations" },
  { id: "notification-monitoring", label: "Notifications", icon: BellRing, href: "/(app)/admin/notification-monitoring" as Href, group: "Operations" },
  { id: "audit-logs", label: "Audit Logs", icon: ScrollText, href: "/(app)/admin/audit-logs" as Href, group: "Governance" },
  { id: "terms", label: "Terms & Privacy", icon: FileText, href: "/(app)/admin/terms" as Href, group: "Governance" },
  { id: "delegation", label: "Delegation", icon: Share2, href: "/(app)/admin/delegation" as Href, group: "Governance" },
  { id: "emergency-access", label: "Emergency Access", icon: KeyRound, href: "/(app)/admin/emergency-access" as Href, group: "Governance" },
  { id: "messages", label: "Messages", icon: MessageSquare, href: "/(app)/admin/messages" as Href, group: "More" },
  { id: "invitations", label: "Invitations", icon: Mail, href: "/(app)/admin/invitations" as Href, group: "More" },
  { id: "reports", label: "Reports", icon: BarChart3, href: "/(app)/admin/reports" as Href, group: "More" },
  { id: "profile", label: "My Profile", icon: UserCog, href: "/(app)/admin/profile" as Href, group: "More" },
];

type DrawerCtx = { open: () => void; close: () => void; isOpen: boolean };
const AdminDrawerCtx = createContext<DrawerCtx>({ open: () => {}, close: () => {}, isOpen: false });
export const useAdminDrawer = () => useContext(AdminDrawerCtx);

type SearchResult = {
  id: string;
  title: string;
  subtitle: string;
  category: "Users" | "Trials" | "Organizations" | "Tickets";
  href: Href;
};

const PAGE_TITLES: Record<string, string> = {
  users: "Users", organizations: "Organizations", invitations: "Invitations",
  "master-data": "Master Data", tickets: "Support Tickets", alerts: "System Alerts",
  "notification-monitoring": "Notification Monitoring", "audit-logs": "Audit Logs",
  trials: "Trial Management", terms: "Terms & Privacy", reports: "Reports",
  delegation: "Delegation", "emergency-access": "Emergency Access",
  messages: "Messages", profile: "My Profile",
};

const asRows = (value: unknown): any[] => Array.isArray(value) ? value : [];
const text = (...values: unknown[]) => values.filter(Boolean).join(" · ");

export default function AdminLayout() {
  const { user, loading, signOut } = useAuth();
  const role = (user?.role as string) || "";

  if (loading) {
    return (
      <View style={st.loading}>
        <ActivityIndicator color={C.primary} />
      </View>
    );
  }
  if (!user) return <Redirect href="/(auth)/welcome" />;
  if (role !== "admin") return <Redirect href="/" />;

  return <AdminShell signOut={signOut} />;
}

function AdminShell({ signOut }: { signOut: () => Promise<void> }) {
  const router = useRouter();
  const pathname = usePathname();
  const { width } = useWindowDimensions();
  const persistent = width >= DESKTOP_BREAKPOINT;
  const drawerWidth = Math.min(300, Math.round(width * 0.82));
  const [isOpen, setOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [alertCount, setAlertCount] = useState(0);
  const tx = useRef(new Animated.Value(-drawerWidth)).current;
  const fade = useRef(new Animated.Value(0)).current;
  const { user } = useAuth();

  const open = useCallback(() => {
    if (!persistent) setOpen(true);
  }, [persistent]);
  const close = useCallback(() => {
    Animated.parallel([
      Animated.timing(tx, { toValue: -drawerWidth, duration: 180, useNativeDriver: true }),
      Animated.timing(fade, { toValue: 0, duration: 180, useNativeDriver: true }),
    ]).start(() => setOpen(false));
  }, [drawerWidth, fade, tx]);

  useEffect(() => {
    if (!persistent && isOpen) {
      tx.setValue(-drawerWidth);
      Animated.parallel([
        Animated.timing(tx, { toValue: 0, duration: 220, useNativeDriver: true }),
        Animated.timing(fade, { toValue: 1, duration: 220, useNativeDriver: true }),
      ]).start();
    }
  }, [drawerWidth, fade, isOpen, persistent, tx]);

  useEffect(() => {
    if (persistent && isOpen) setOpen(false);
  }, [isOpen, persistent]);

  useEffect(() => {
    api.get("/admin/alerts")
      .then((res) => setAlertCount(asRows(res.data).filter((a) => (a?.status || "open") !== "resolved").length))
      .catch(() => setAlertCount(0));
  }, [pathname]);

  useEffect(() => {
    const q = query.trim().toLowerCase();
    if (!searchOpen || q.length < 2) {
      setResults([]);
      setSearching(false);
      return;
    }
    let active = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      const calls = await Promise.allSettled([
        api.get("/admin/users", { params: { search: q, limit: 8 } }),
        api.get("/admin/trials"),
        api.get("/admin/organizations"),
        api.get("/admin/tickets", { params: { search: q } }),
      ]);
      if (!active) return;
      const data = (index: number) =>
        calls[index].status === "fulfilled"
          ? (calls[index] as PromiseFulfilledResult<any>).value.data
          : [];
      const includes = (...values: unknown[]) =>
        values.some((value) => String(value || "").toLowerCase().includes(q));
      const next: SearchResult[] = [
        ...asRows(data(0)).filter((u) => includes(u.full_name, u.email, u.organization, u.role)).slice(0, 5).map((u) => ({
          id: `user-${u.id}`, title: u.full_name || u.email || "User",
          subtitle: text(u.role, u.organization, u.status), category: "Users" as const,
          href: { pathname: "/(app)/admin/users", params: { focus: u.id } } as Href,
        })),
        ...asRows(data(1)).filter((t) => includes(t.title, t.protocol_id, t.sponsor, t.status)).slice(0, 5).map((t) => ({
          id: `trial-${t.id}`, title: t.protocol_id || t.title || "Trial",
          subtitle: text(t.title, t.sponsor, t.status), category: "Trials" as const,
          href: { pathname: "/(app)/admin/trials", params: { focus: t.id } } as Href,
        })),
        ...asRows(data(2)).filter((o) => includes(o.name, o.type, o.address, o.email)).slice(0, 5).map((o) => ({
          id: `org-${o.id}`, title: o.name || "Organization",
          subtitle: text(o.type, o.status, o.address), category: "Organizations" as const,
          href: { pathname: "/(app)/admin/organizations", params: { focus: o.id } } as Href,
        })),
        ...asRows(data(3)).filter((t) => includes(t.id, t.subject, t.user_name, t.organization, t.status)).slice(0, 5).map((t) => ({
          id: `ticket-${t.id}`, title: text(t.id, t.subject) || "Support ticket",
          subtitle: text(t.priority, t.organization, t.status), category: "Tickets" as const,
          href: { pathname: "/(app)/admin/tickets", params: { focus: t.id } } as Href,
        })),
      ];
      setResults(next);
      setSearching(false);
    }, 280);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, searchOpen]);

  const ctx = useMemo<DrawerCtx>(() => ({ open, close, isOpen }), [close, isOpen, open]);
  const isActive = (item: AdminNavItem) =>
    item.id === "dashboard"
      ? pathname === "/(app)/admin" || pathname === "/admin" || pathname.endsWith("/admin")
      : pathname.includes(`/admin/${item.id}`);
  const pageKey = pathname.split("/").filter(Boolean).pop() || "dashboard";
  const pageTitle = PAGE_TITLES[pageKey] || "Dashboard";
  const initials = user?.avatar_initials ||
    (user?.full_name || "Platform Admin").split(" ").map((word) => word[0]).slice(0, 2).join("").toUpperCase();
  const today = new Date().toLocaleDateString("en-GB", {
    weekday: "long", day: "2-digit", month: "short", year: "numeric",
  });

  const go = (item: AdminNavItem) => {
    if (!persistent) close();
    if (item.id === "dashboard") router.replace(item.href);
    else router.push(item.href);
  };
  const doSignOut = async () => {
    if (!persistent) close();
    await signOut();
    router.replace("/(auth)/welcome");
  };
  const openResult = (result: SearchResult) => {
    setSearchOpen(false);
    setQuery("");
    router.push(result.href);
  };

  return (
    <AdminDrawerCtx.Provider value={ctx}>
      <View style={st.shell}>
        {persistent && (
          <Sidebar
            isActive={isActive}
            onNavigate={go}
            onSignOut={doSignOut}
            width={SIDEBAR_W}
          />
        )}

        <View style={st.main}>
          {persistent && (
            <SafeAreaView edges={["top"]} style={st.topBarSafe}>
              <View style={st.topBar}>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <RNText style={st.date}>{today.toUpperCase()}</RNText>
                  <RNText style={st.pageTitle} numberOfLines={1}>{pageTitle}</RNText>
                </View>
                <Pressable
                  testID="admin-global-search"
                  onPress={() => setSearchOpen(true)}
                  style={st.utilityBtn}
                  accessibilityLabel="Global search"
                >
                  <Search size={19} color={C.mutedFg} />
                </Pressable>
                <Pressable
                  testID="admin-alerts-shortcut"
                  onPress={() => router.push("/(app)/admin/alerts")}
                  style={st.utilityBtn}
                  accessibilityLabel="System alerts"
                >
                  <Bell size={19} color={C.mutedFg} />
                  {alertCount > 0 && (
                    <View style={st.alertBadge}>
                      <RNText style={st.alertBadgeText}>{Math.min(alertCount, 99)}</RNText>
                    </View>
                  )}
                </Pressable>
                <Pressable
                  testID="admin-settings-shortcut"
                  onPress={() => router.push("/(app)/admin/profile")}
                  style={st.utilityBtn}
                  accessibilityLabel="Platform settings"
                >
                  <Settings size={19} color={C.mutedFg} />
                </Pressable>
                <Pressable
                  onPress={() => router.push("/(app)/admin/profile")}
                  style={st.identity}
                >
                  <LinearGradient colors={[C.primary, C.primaryDeep] as any} style={st.avatar}>
                    <RNText style={st.avatarText}>{initials}</RNText>
                  </LinearGradient>
                  <View style={{ minWidth: 0 }}>
                    <RNText style={st.identityName} numberOfLines={1}>{user?.full_name || "Platform Admin"}</RNText>
                    <RNText style={st.identityEmail} numberOfLines={1}>{user?.email}</RNText>
                  </View>
                </Pressable>
              </View>
            </SafeAreaView>
          )}
          <View style={st.content}>
            <Slot />
          </View>
        </View>

        <Modal visible={!persistent && isOpen} transparent animationType="none" onRequestClose={close}>
          <View style={st.drawerRow}>
            <Animated.View style={[st.mobilePanel, { width: drawerWidth, transform: [{ translateX: tx }] }]}>
              <Sidebar isActive={isActive} onNavigate={go} onSignOut={doSignOut} width={drawerWidth} onClose={close} />
            </Animated.View>
            <Animated.View style={[st.backdropWrap, { opacity: fade }]}>
              <Pressable testID="admin-drawer-backdrop" onPress={close} style={st.backdrop} />
            </Animated.View>
          </View>
        </Modal>

        <Modal
          visible={searchOpen}
          transparent
          animationType="fade"
          onRequestClose={() => setSearchOpen(false)}
        >
          <Pressable style={st.searchBackdrop} onPress={() => setSearchOpen(false)}>
            <Pressable style={st.searchPanel} onPress={(event) => event.stopPropagation()}>
              <View style={st.searchHeader}>
                <Search size={20} color={C.mutedFg} />
                <TextInput
                  autoFocus
                  value={query}
                  onChangeText={setQuery}
                  placeholder="Search users, trials, organizations, tickets…"
                  placeholderTextColor={C.mutedFg}
                  style={st.searchInput}
                />
                {searching ? (
                  <ActivityIndicator size="small" color={C.primary} />
                ) : (
                  <Pressable onPress={() => setSearchOpen(false)} style={st.searchClose}>
                    <X size={18} color={C.mutedFg} />
                  </Pressable>
                )}
              </View>
              <ScrollView style={st.searchResults} keyboardShouldPersistTaps="handled">
                {query.trim().length < 2 ? (
                  <RNText style={st.searchEmpty}>Enter at least 2 characters to search across the platform.</RNText>
                ) : !searching && results.length === 0 ? (
                  <RNText style={st.searchEmpty}>No matching platform records found.</RNText>
                ) : (
                  results.map((result, index) => {
                    const showCategory = index === 0 || results[index - 1].category !== result.category;
                    return (
                      <View key={result.id}>
                        {showCategory && <RNText style={st.resultCategory}>{result.category}</RNText>}
                        <Pressable onPress={() => openResult(result)} style={st.resultRow}>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <RNText style={st.resultTitle} numberOfLines={1}>{result.title}</RNText>
                            <RNText style={st.resultSub} numberOfLines={1}>{result.subtitle || result.category}</RNText>
                          </View>
                          <ChevronRight size={16} color={C.mutedFg} />
                        </Pressable>
                      </View>
                    );
                  })
                )}
              </ScrollView>
            </Pressable>
          </Pressable>
        </Modal>
      </View>
    </AdminDrawerCtx.Provider>
  );
}

function Sidebar({
  isActive, onNavigate, onSignOut, width, onClose,
}: {
  isActive: (item: AdminNavItem) => boolean;
  onNavigate: (item: AdminNavItem) => void;
  onSignOut: () => void;
  width: number;
  onClose?: () => void;
}) {
  const groups: AdminNavItem["group"][] = ["Core", "Operations", "Governance", "More"];
  return (
    <LinearGradient
      colors={[C.primaryDeep, C.primary] as any}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={[st.sidebar, { width }]}
    >
      <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1 }}>
        <View style={st.brand}>
          <View style={st.logo}><FlaskConical size={20} color={C.primaryFg} /></View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <RNText style={st.brandTitle}>TrialSync</RNText>
            <RNText style={st.brandSub}>ADMIN PORTAL</RNText>
          </View>
          {onClose && (
            <Pressable testID="admin-drawer-close" onPress={onClose} style={st.closeBtn}>
              <X size={18} color={W.w70} />
            </Pressable>
          )}
        </View>
        <ScrollView contentContainerStyle={st.navContent} showsVerticalScrollIndicator={false}>
          {groups.map((group, groupIndex) => (
            <View key={group} style={groupIndex > 0 ? st.navGroup : undefined}>
              {group !== "Core" && <RNText style={st.groupLabel}>{group.toUpperCase()}</RNText>}
              {ADMIN_NAV.filter((item) => item.group === group).map((item) => {
                const active = isActive(item);
                const Icon = item.icon;
                return (
                  <Pressable
                    key={item.id}
                    testID={`admin-nav-${item.id}`}
                    onPress={() => onNavigate(item)}
                    style={[st.navRow, active && st.navRowActive]}
                  >
                    {active && <View style={st.navAccent} />}
                    <Icon size={18} color={active ? C.primaryFg : W.w70} />
                    <RNText style={[st.navLabel, active && st.navLabelActive]} numberOfLines={1}>
                      {item.label}
                    </RNText>
                  </Pressable>
                );
              })}
            </View>
          ))}
        </ScrollView>
        <Pressable testID="admin-signout" onPress={onSignOut} style={st.signOut}>
          <LogOut size={18} color={W.w70} />
          <RNText style={st.navLabel}>Sign out</RNText>
        </Pressable>
      </SafeAreaView>
    </LinearGradient>
  );
}

const st = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: C.background },
  shell: { flex: 1, flexDirection: "row", backgroundColor: C.background },
  main: { flex: 1, minWidth: 0, backgroundColor: C.background },
  content: { flex: 1, minHeight: 0 },
  sidebar: { height: "100%", overflow: "hidden" },
  brand: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 17, borderBottomWidth: 1, borderBottomColor: W.w15 },
  logo: { width: 40, height: 40, borderRadius: 13, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w10 },
  brandTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 17 },
  brandSub: { color: W.w55, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.6, marginTop: 1 },
  closeBtn: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  navContent: { paddingHorizontal: 8, paddingVertical: 10 },
  navGroup: { borderTopWidth: 1, borderTopColor: W.w10, paddingTop: 9, marginTop: 7 },
  groupLabel: { color: W.w55, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.5, paddingHorizontal: 12, marginBottom: 5 },
  navRow: { flexDirection: "row", alignItems: "center", gap: 12, minHeight: 42, paddingHorizontal: 14, marginVertical: 1, borderRadius: 11, position: "relative" },
  navRowActive: { backgroundColor: W.w15 },
  navAccent: { position: "absolute", left: 4, top: 10, bottom: 10, width: 3, borderRadius: 2, backgroundColor: C.accent },
  navLabel: { color: W.w70, fontFamily: fonts.medium, fontSize: 13, flex: 1 },
  navLabelActive: { color: C.primaryFg, fontFamily: fonts.semibold },
  signOut: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 15, paddingHorizontal: 20, borderTopWidth: 1, borderTopColor: W.w15 },
  topBarSafe: { backgroundColor: C.card, borderBottomWidth: 1, borderBottomColor: C.border },
  topBar: { minHeight: 64, flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 22, paddingVertical: 8 },
  date: { color: C.accent, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1 },
  pageTitle: { color: C.foreground, fontFamily: fonts.display, fontSize: 18, marginTop: 1 },
  utilityBtn: { width: 40, height: 40, borderRadius: 12, alignItems: "center", justifyContent: "center", position: "relative" },
  alertBadge: { position: "absolute", top: 3, right: 2, minWidth: 16, height: 16, paddingHorizontal: 3, borderRadius: 8, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.card },
  alertBadgeText: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 8 },
  identity: { maxWidth: 230, flexDirection: "row", alignItems: "center", gap: 9, paddingLeft: 10, marginLeft: 4, borderLeftWidth: 1, borderLeftColor: C.border },
  avatar: { width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center" },
  avatarText: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 11 },
  identityName: { color: C.foreground, fontFamily: fonts.semibold, fontSize: 12 },
  identityEmail: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 10, marginTop: 1, maxWidth: 160 },
  drawerRow: { flex: 1, flexDirection: "row" },
  mobilePanel: { height: "100%", overflow: "hidden" },
  backdropWrap: { flex: 1 },
  backdrop: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)" },
  searchBackdrop: { flex: 1, backgroundColor: "rgba(46,27,51,0.48)", alignItems: "center", paddingHorizontal: 20, paddingTop: 90 },
  searchPanel: { width: "100%", maxWidth: 680, maxHeight: "72%", backgroundColor: C.card, borderRadius: 20, overflow: "hidden", borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.18, shadowRadius: 24, shadowOffset: { width: 0, height: 10 }, elevation: 12 },
  searchHeader: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 58, paddingHorizontal: 16, borderBottomWidth: 1, borderBottomColor: C.border },
  searchInput: { flex: 1, color: C.foreground, fontFamily: fonts.regular, fontSize: 14, paddingVertical: 12, outlineStyle: "none" } as any,
  searchClose: { width: 32, height: 32, alignItems: "center", justifyContent: "center" },
  searchResults: { maxHeight: 480, paddingHorizontal: 12 },
  searchEmpty: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 13, textAlign: "center", paddingVertical: 32 },
  resultCategory: { color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.4, paddingHorizontal: 5, paddingTop: 14, paddingBottom: 5 },
  resultRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 12, paddingVertical: 11, borderRadius: 12, backgroundColor: C.surface, marginBottom: 5 },
  resultTitle: { color: C.foreground, fontFamily: fonts.semibold, fontSize: 13 },
  resultSub: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 11, marginTop: 2 },
});
