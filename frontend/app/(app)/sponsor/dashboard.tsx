import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  ActivityIndicator,
  Animated,
  Easing,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text as RNText,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Circle, Path } from "react-native-svg";
import {
  AlertTriangle,
  ArrowUpRight,
  Bell,
  Building2,
  ChevronRight,
  FileText,
  FlaskConical,
  Info,
  MapPin,
  RefreshCcw,
  Sun,
  TrendingUp,
  UserCheck,
  Users,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { colors as C, dawnGradient, fonts } from "@/src/theme/tokens";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import type {
  SponsorDashboard as SponsorDashboardData,
  SponsorNotification as DashboardNotification,
  SponsorSite as DashboardSite,
  SponsorTrial as DashboardTrial,
} from "@/src/features/sponsor/types";

const AnimatedCircle = Animated.createAnimatedComponent(Circle);
const DAWN = dawnGradient;
const W = {
  w15: "rgba(255,255,255,0.15)",
  w20: "rgba(255,255,255,0.20)",
  w25: "rgba(255,255,255,0.25)",
  w65: "rgba(255,255,255,0.65)",
  w70: "rgba(255,255,255,0.70)",
  w80: "rgba(255,255,255,0.80)",
};

const EMPTY: SponsorDashboardData = {
  portfolio: {
    healthScore: 0,
    status: "No portfolio data",
    activeTrials: 0,
    alerts: 0,
    enrolled: 0,
    target: 0,
    enrollmentPct: 0,
    compliancePct: 0,
    adherencePct: 0,
    recruitment: {
      screened: 0,
      screen_fail: 0,
      randomized: 0,
      active: 0,
      withdrawn: 0,
      dropout: 0,
      follow_up: 0,
      completed: 0,
    },
  },
  totals: { trials: 0, sites: 0, subjects: 0, pis: 0 },
  trials: [],
  sites: [],
  recentNotifications: [],
  capabilities: { canAddTrial: true, canAddSite: false, canShareSchedule: true },
};

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((value) => mounted && setReduced(value))
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduced,
    );
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);
  return reduced;
}

function healthLabel(score: number, status?: string) {
  if (status) {
    const normalized = status.replace(/_/g, " ").trim();
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }
  if (score >= 80) return "On track";
  if (score >= 60) return "Steady";
  if (score > 0) return "Needs attention";
  return "No portfolio data";
}

function timeLabel(value: unknown) {
  if (typeof value !== "string" || !value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const minutes = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 60000));
  if (minutes < 1) return "Now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)}d`;
  return parsed.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function creatorRoleLabel(value?: string) {
  if (!value?.trim()) return "Role not recorded";
  if (value.trim().toLowerCase() === "cro") return "CRO";
  return value
    .trim()
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function createdAtLabel(value?: string) {
  if (!value) return "Date & time not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function SponsorDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const reducedMotion = useReducedMotion();
  const [dashboard, setDashboard] = useState<SponsorDashboardData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDashboard(await getSponsorDashboard());
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || "We couldn't load your portfolio. Pull down or tap retry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const fullName = user?.full_name || "";
  const firstName = fullName.trim().split(/\s+/)[0] || "";
  const initials = user?.avatar_initials || fullName.split(/\s+/).filter(Boolean).map((word) => word[0]).slice(0, 2).join("").toUpperCase() || "?";
  const roleLabel = user?.role === "cro" ? "CRO" : "Sponsor";
  const organization = user?.organization || "";
  const visibleTrials = useMemo(
    () => dashboard.trials.filter((trial) => trial.status.toLowerCase() === "active"),
    [dashboard.trials],
  );

  const openSites = () => router.push("/(app)/sponsor/sites");
  const openAddSite = () => router.push({
    pathname: "/(app)/sponsor/sites",
    params: { add: "1" },
  });

  return (
    <View style={styles.screen}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={C.primary} />}
      >
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={styles.hero}>
          <LinearGradient colors={[C.primaryDeep, "rgba(107,20,55,0.55)", "rgba(107,20,55,0)"] as any} style={StyleSheet.absoluteFill} />
          <AmbientHeroArt reducedMotion={reducedMotion} />

          <SafeAreaView edges={["top"]}>
            <View style={styles.heroTop}>
              <View style={styles.heroIdentity}>
                <Text style={styles.heroEyebrow} numberOfLines={1}>
                  {roleLabel.toUpperCase()}{organization ? ` · ${organization.toUpperCase()}` : ""}
                </Text>
                <View style={styles.greetingRow}>
                  <Text style={styles.heroTitle}>{firstName ? `Hi, ${firstName}` : "Hello"}</Text>
                  <Sun size={19} color={W.w80} />
                </View>
              </View>
              <Pressable testID="sponsor-bell" onPress={() => router.push("/(app)/sponsor/notifications")} style={styles.iconButton}>
                <Bell size={19} color={C.primaryFg} />
                {(unread ?? dashboard.recentNotifications.filter((item) => item.unread).length) > 0 && (
                  <View style={styles.badge}>
                    <Text style={styles.badgeText}>{Math.min(9, unread ?? dashboard.recentNotifications.filter((item) => item.unread).length)}</Text>
                  </View>
                )}
              </Pressable>
              <Pressable testID="sponsor-avatar" onPress={() => router.push("/(app)/sponsor/profile")} style={styles.iconButton}>
                <Text style={styles.avatarText}>{initials}</Text>
              </Pressable>
            </View>

            <View style={styles.portfolioDeck}>
              <ProgressRing value={dashboard.portfolio.healthScore / 100} reducedMotion={reducedMotion}>
                <Text style={styles.ringValue}>
                  {loading ? "–" : <><CountUp value={dashboard.portfolio.healthScore} reducedMotion={reducedMotion} />%</>}
                </Text>
                <Text style={styles.ringLabel}>HEALTH</Text>
              </ProgressRing>
              <View style={styles.portfolioCopy}>
                <Text style={styles.heroEyebrow}>PORTFOLIO HEALTH</Text>
                <Text style={styles.portfolioTitle}>{loading ? "Loading…" : healthLabel(dashboard.portfolio.healthScore, dashboard.portfolio.status)}</Text>
                <View style={styles.heroChips}>
                  <HeroChip icon={FlaskConical} label={`${loading ? "–" : dashboard.portfolio.activeTrials} active`} />
                  <HeroChip
                    icon={Users}
                    label={loading
                      ? "– enrolled"
                      : dashboard.portfolio.target > 0
                        ? `${dashboard.portfolio.enrolled}/${dashboard.portfolio.target} enrolled`
                        : `${dashboard.portfolio.enrolled} enrolled`}
                  />
                  <HeroChip icon={Bell} label={`${loading ? "–" : dashboard.portfolio.alerts} alerts`} danger={dashboard.portfolio.alerts > 0} />
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        <View style={styles.body}>
          <MotionItem index={0} reducedMotion={reducedMotion}>
            <View style={styles.statRow}>
              <StatTile icon={FlaskConical} color={C.info} tint="rgba(123,107,184,0.12)" value={dashboard.totals.trials} label="Total Trials" loading={loading} reducedMotion={reducedMotion} onPress={() => router.push("/(app)/sponsor/trials")} />
              <StatTile icon={MapPin} color={C.accent} tint="rgba(230,155,92,0.15)" value={dashboard.totals.sites} label="Total Sites" loading={loading} reducedMotion={reducedMotion} onPress={openSites} />
              <StatTile icon={Users} color={C.violet} tint="rgba(142,91,180,0.12)" value={dashboard.totals.subjects} label="Total Patients" loading={loading} reducedMotion={reducedMotion} onPress={() => router.push("/(app)/sponsor/patients")} />
              <StatTile icon={UserCheck} color={C.success} tint="rgba(92,154,110,0.12)" value={dashboard.totals.pis} label="Total PIs" loading={loading} reducedMotion={reducedMotion} onPress={() => router.push("/(app)/sponsor/principal-investigators")} />
            </View>
          </MotionItem>

          {error ? <ErrorCard text={error} onRetry={load} /> : null}
          <MotionItem index={1} reducedMotion={reducedMotion}>
            <SectionLabel label="QUICK ACTIONS" />
            <View style={styles.actionRow}>
              <QuickAction icon={FlaskConical} color={C.info} label="Add Trial" disabled={!dashboard.capabilities.canAddTrial} onPress={() => router.push("/(app)/sponsor/add-trial")} />
              {dashboard.capabilities.canAddSite ? (
                <QuickAction icon={MapPin} gradient label="Add Site" onPress={openAddSite} />
              ) : null}
              <QuickAction icon={FileText} color={C.accent} iconColor={C.accentFg} label="Share Schedule" disabled={!dashboard.capabilities.canShareSchedule} onPress={() => router.push("/(app)/sponsor/share-schedule")} />
            </View>
          </MotionItem>

          <SectionLabel
            label="MY TRIALS"
            actionLabel="See all"
            onAction={() => router.push("/(app)/sponsor/trials")}
          />
          {loading ? (
            <LoadingCard label="Loading trials…" />
          ) : visibleTrials.length === 0 ? (
            <EmptyCard icon={FlaskConical} title="No active trials" description="Your active studies will appear here." />
          ) : (
            <View style={styles.sectionStack}>
              {visibleTrials.map((trial, index) => (
                <MotionItem key={trial.id} index={index + 2} reducedMotion={reducedMotion}>
                  <TrialCard
                    trial={trial}
                    reducedMotion={reducedMotion}
                    onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } })}
                  />
                </MotionItem>
              ))}
            </View>
          )}

          <SectionLabel label="SITE PERFORMANCE" actionLabel="View all" onAction={openSites} />
          {loading ? (
            <LoadingCard label="Loading sites…" />
          ) : dashboard.sites.length === 0 ? (
            <EmptyCard icon={Building2} title="No site data yet" description="Site performance appears after sites are assigned to a trial." />
          ) : (
            <View style={styles.performanceCard}>
              {dashboard.sites.slice(0, 4).map((site, index) => (
                <SitePerformance
                  key={site.id}
                  site={site}
                  last={index === Math.min(4, dashboard.sites.length) - 1}
                  reducedMotion={reducedMotion}
                  onPress={() => router.push({
                    pathname: "/(app)/sponsor/sites",
                    params: { siteId: site.id },
                  })}
                />
              ))}
            </View>
          )}

          <SectionLabel
            label="NOTIFICATIONS"
            actionLabel="See all"
            onAction={() => router.push("/(app)/sponsor/notifications")}
          />
          {loading ? (
            <LoadingCard label="Loading updates…" />
          ) : dashboard.recentNotifications.length === 0 ? (
            <EmptyCard icon={Bell} title="You're all caught up" description="New portfolio updates will appear here." />
          ) : (
            <View style={styles.sectionStack}>
              {dashboard.recentNotifications.slice(0, 2).map((notification) => (
                <NotificationCard key={notification.id} item={notification} onPress={() => router.push("/(app)/sponsor/notifications")} />
              ))}
            </View>
          )}
        </View>
      </ScrollView>

      <SponsorBottomNav active="dashboard" unread={unread ?? 0} />
    </View>
  );
}

function Text(props: any) {
  return <RNText {...props} style={[{ color: C.foreground, fontFamily: fonts.regular }, props.style]} />;
}

function CountUp({ value, reducedMotion }: { value: number; reducedMotion: boolean }) {
  const animated = useRef(new Animated.Value(reducedMotion ? value : 0)).current;
  const [display, setDisplay] = useState(reducedMotion ? value : 0);

  useEffect(() => {
    const listener = animated.addListener(({ value: next }) => setDisplay(Math.round(next)));
    if (reducedMotion) {
      animated.setValue(value);
      setDisplay(value);
    } else {
      animated.stopAnimation();
      animated.setValue(0);
      Animated.timing(animated, {
        toValue: value,
        duration: 720,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: false,
      }).start();
    }
    return () => animated.removeListener(listener);
  }, [animated, reducedMotion, value]);

  return <RNText>{display}</RNText>;
}

function MotionItem({
  children,
  index,
  reducedMotion,
}: {
  children: React.ReactNode;
  index: number;
  reducedMotion: boolean;
}) {
  const entrance = useRef(new Animated.Value(reducedMotion ? 1 : 0)).current;
  useEffect(() => {
    if (reducedMotion) {
      entrance.setValue(1);
      return;
    }
    entrance.setValue(0);
    Animated.timing(entrance, {
      toValue: 1,
      duration: 430,
      delay: Math.min(index, 10) * 55,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [entrance, index, reducedMotion]);
  return (
    <Animated.View
      style={{
        opacity: entrance,
        transform: [{
          translateY: entrance.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }),
        }],
      }}
    >
      {children}
    </Animated.View>
  );
}

function AmbientHeroArt({ reducedMotion }: { reducedMotion: boolean }) {
  const drift = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (reducedMotion) {
      drift.setValue(0);
      return;
    }
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(drift, {
        toValue: 1,
        duration: 4200,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
      Animated.timing(drift, {
        toValue: 0,
        duration: 4200,
        easing: Easing.inOut(Easing.sin),
        useNativeDriver: true,
      }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [drift, reducedMotion]);
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        styles.heroArt,
        {
          opacity: drift.interpolate({ inputRange: [0, 1], outputRange: [0.72, 0.96] }),
          transform: [
            { translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -8] }) },
            { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, 6] }) },
            { scale: drift.interpolate({ inputRange: [0, 1], outputRange: [1, 1.035] }) },
          ],
        },
      ]}
    >
      <Svg viewBox="0 0 200 200" width={240} height={240}>
        <Path d="M30 110 a70 70 0 0 1 140 0" stroke={W.w25} strokeWidth="1.5" fill="none" />
        <Path d="M52 110 a48 48 0 0 1 96 0" stroke={W.w25} strokeWidth="1" fill="none" />
        <Circle cx="100" cy="110" r="22" stroke={W.w15} strokeWidth="1" fill="none" />
      </Svg>
    </Animated.View>
  );
}

function HeroChip({ icon: Icon, label, danger }: any) {
  return (
    <View style={[styles.heroChip, danger && styles.heroChipDanger]}>
      <Icon size={12} color={C.primaryFg} />
      <Text style={styles.heroChipText}>{label}</Text>
    </View>
  );
}

function ProgressRing({
  value,
  children,
  reducedMotion,
}: {
  value: number;
  children: React.ReactNode;
  reducedMotion: boolean;
}) {
  const size = 76;
  const stroke = 6;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.max(0, Math.min(1, value));
  const draw = useRef(new Animated.Value(reducedMotion ? progress : 0)).current;
  useEffect(() => {
    if (reducedMotion) {
      draw.setValue(progress);
      return;
    }
    draw.setValue(0);
    Animated.timing(draw, {
      toValue: progress,
      duration: 850,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [draw, progress, reducedMotion]);
  const dashOffset = draw.interpolate({
    inputRange: [0, 1],
    outputRange: [circumference, 0],
  });
  return (
    <View style={styles.ring}>
      <Svg width={size} height={size} style={styles.ringSvg}>
        <Circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={W.w20} strokeWidth={stroke} />
        <AnimatedCircle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={C.primaryFg}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={dashOffset as any}
        />
      </Svg>
      <View style={styles.ringContent}>{children}</View>
    </View>
  );
}

function StatTile({ icon: Icon, color, tint, value, label, loading, onPress, disabled, reducedMotion }: any) {
  return (
    <Pressable disabled={disabled || !onPress} onPress={onPress} style={({ pressed }) => [styles.statTile, pressed && styles.pressed]}>
      <View style={[styles.statIcon, { backgroundColor: tint }]}><Icon size={15} color={color} /></View>
      <Text style={styles.statValue}>{loading ? "–" : <CountUp value={value} reducedMotion={reducedMotion} />}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </Pressable>
  );
}

function SectionLabel({ label, actionLabel, onAction, actionDisabled }: any) {
  return (
    <View style={styles.sectionHeader}>
      <View style={styles.sectionTitleRow}>
        <LinearGradient colors={DAWN as any} style={styles.sectionMark} />
        <Text style={styles.sectionTitle}>{label}</Text>
      </View>
      {actionLabel ? (
        <Pressable disabled={actionDisabled} onPress={onAction} style={styles.sectionAction}>
          <Text style={[styles.sectionActionText, actionDisabled && styles.disabledText]}>{actionLabel}</Text>
          <ChevronRight size={15} color={actionDisabled ? C.border : C.info} />
        </Pressable>
      ) : null}
    </View>
  );
}

function QuickAction({ icon: Icon, color, iconColor = C.primaryFg, gradient, label, onPress, disabled }: any) {
  return (
    <Pressable disabled={disabled} onPress={onPress} style={({ pressed }) => [styles.quickAction, disabled && styles.disabled, pressed && styles.pressed]}>
      {gradient ? (
        <LinearGradient colors={DAWN as any} style={styles.actionIcon}><Icon size={21} color={C.primaryFg} /></LinearGradient>
      ) : (
        <View style={[styles.actionIcon, { backgroundColor: color }]}><Icon size={21} color={iconColor} /></View>
      )}
      <Text style={styles.actionLabel}>{label}</Text>
    </Pressable>
  );
}

function TrialCard({
  trial,
  onPress,
  reducedMotion,
}: {
  trial: DashboardTrial;
  onPress: () => void;
  reducedMotion: boolean;
}) {
  const numerator = trial.randomized;
  const percentage = trial.target > 0 ? Math.min(100, Math.round((numerator / trial.target) * 100)) : 0;
  const metadataTags = [
    trial.phase?.trim(),
    trial.condition?.trim(),
    trial.drug?.trim(),
    trial.sites > 0 ? `${trial.sites} ${trial.sites === 1 ? "site" : "sites"}` : "",
  ].filter((tag): tag is string => Boolean(tag));
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.trialCard, pressed && styles.pressed]}>
      <LinearGradient colors={DAWN as any} style={styles.trialStripe} />
      <View style={styles.trialTop}>
        <View style={styles.protocolPill}><Text style={styles.protocolText}>{trial.protocolId}</Text></View>
        <View style={styles.trialStatusRow}>
          <View style={styles.activePill}><View style={styles.activeDot} /><Text style={styles.activeText}>{trial.status}</Text></View>
          <View style={styles.openIcon}><ArrowUpRight size={14} color={C.mutedFg} /></View>
        </View>
      </View>
      <Text style={styles.trialTitle} numberOfLines={2}>{trial.title}</Text>
      {metadataTags.length > 0 ? (
        <View style={styles.tags}>
          {metadataTags.map((tag) => (
            <View key={tag} style={styles.tag}><Text style={styles.tagText}>{tag}</Text></View>
          ))}
        </View>
      ) : null}
      <View style={styles.progressMeta}>
        <Text style={styles.progressLabel}>Randomized</Text>
        <Text style={styles.progressValue}>{trial.target > 0 ? `${numerator}/${trial.target} · ${percentage}%` : `${numerator} randomized`}</Text>
      </View>
      <View style={styles.progressTrack}>
        {trial.target > 0 ? <AnimatedProgress value={percentage} reducedMotion={reducedMotion} style={styles.progressFill} /> : null}
      </View>
      <View style={styles.trialAttribution}>
        <View style={styles.trialAttributionIcon}>
          <UserCheck size={14} color={C.primary} />
        </View>
        <View style={styles.trialCreatorCopy}>
          <Text style={styles.trialAttributionLabel}>CREATED BY</Text>
          <Text style={styles.trialCreatorName} numberOfLines={1}>{trial.createdByName || "Unknown"}</Text>
          <Text style={styles.trialCreatorRole} numberOfLines={1}>{creatorRoleLabel(trial.createdByRole)}</Text>
        </View>
        <View style={styles.trialCreatedAt}>
          <Text style={styles.trialAttributionLabel}>DATE &amp; TIME</Text>
          <Text style={styles.trialCreatedAtText}>{createdAtLabel(trial.createdAt)}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function SitePerformance({
  site,
  last,
  onPress,
  reducedMotion,
}: {
  site: DashboardSite;
  last: boolean;
  onPress: () => void;
  reducedMotion: boolean;
}) {
  const score = site.performanceScore;
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.siteRow, !last && styles.siteDivider, pressed && styles.pressed]}>
      <View style={styles.siteMeta}>
        <View style={styles.siteTitleWrap}>
          <Text style={styles.siteName} numberOfLines={1}>{site.name}</Text>
          <Text style={styles.siteSubscore} numberOfLines={1}>
            Enrollment {site.enrollmentPct}% · Compliance {site.visitCompliance ?? 0}% · Adherence {site.adherencePct ?? 0}%
          </Text>
        </View>
        <View style={styles.siteScoreWrap}>
          <Text style={styles.siteScore}>{score}%</Text>
          <ChevronRight size={14} color={C.mutedFg} />
        </View>
      </View>
      <View style={styles.siteTrack}>
        <AnimatedProgress value={score} reducedMotion={reducedMotion} style={styles.siteFill} />
      </View>
    </Pressable>
  );
}

function AnimatedProgress({
  value,
  reducedMotion,
  style,
}: {
  value: number;
  reducedMotion: boolean;
  style: any;
}) {
  const progress = Math.max(0, Math.min(100, value));
  const fill = useRef(new Animated.Value(reducedMotion ? progress : 0)).current;
  useEffect(() => {
    if (reducedMotion) {
      fill.setValue(progress);
      return;
    }
    fill.setValue(0);
    Animated.timing(fill, {
      toValue: progress,
      duration: 760,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [fill, progress, reducedMotion]);
  return (
    <Animated.View
      style={[
        style,
        {
          width: fill.interpolate({
            inputRange: [0, 100],
            outputRange: ["0%", "100%"],
          }),
        },
      ]}
    >
      <LinearGradient
        colors={DAWN as any}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 0 }}
        style={StyleSheet.absoluteFill}
      />
    </Animated.View>
  );
}

function notificationTone(type: string) {
  const normalized = type.toLowerCase();
  if (normalized.includes("milestone") || normalized.includes("recruit")) return { Icon: TrendingUp, bg: "rgba(92,154,110,0.14)", color: C.success };
  if (normalized.includes("overdue") || normalized.includes("alert")) return { Icon: AlertTriangle, bg: "rgba(192,57,43,0.12)", color: C.destructive };
  if (normalized.includes("site")) return { Icon: MapPin, bg: "rgba(230,155,92,0.14)", color: C.accent };
  if (normalized.includes("trial")) return { Icon: FlaskConical, bg: "rgba(123,107,184,0.12)", color: C.info };
  return { Icon: Info, bg: C.surface, color: C.mutedFg };
}

function NotificationCard({ item, onPress }: { item: DashboardNotification; onPress: () => void }) {
  const tone = notificationTone(item.type || "system");
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.notificationCard, pressed && styles.pressed]}>
      <View style={[styles.notificationIcon, { backgroundColor: tone.bg }]}><tone.Icon size={17} color={tone.color} /></View>
      <View style={styles.notificationCopy}>
        <Text style={styles.notificationTitle} numberOfLines={1}>{item.title}</Text>
        {item.message ? <Text style={styles.notificationMessage} numberOfLines={1}>{item.message}</Text> : null}
      </View>
      {item.time ? <Text style={styles.notificationTime}>{timeLabel(item.time)}</Text> : null}
      {item.unread ? <View style={styles.unreadDot} /> : null}
    </Pressable>
  );
}

function LoadingCard({ label }: { label: string }) {
  return (
    <View style={styles.loadingCard}>
      <ActivityIndicator color={C.primary} />
      <Text style={styles.loadingText}>{label}</Text>
    </View>
  );
}

function EmptyCard({ icon: Icon, title, description }: any) {
  return (
    <View style={styles.emptyCard}>
      <View style={styles.emptyIcon}><Icon size={20} color={C.primary} /></View>
      <View style={styles.emptyCopy}>
        <Text style={styles.emptyTitle}>{title}</Text>
        <Text style={styles.emptyDescription}>{description}</Text>
      </View>
    </View>
  );
}

function ErrorCard({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <View style={styles.errorCard}>
      <AlertTriangle size={19} color={C.destructive} />
      <Text style={styles.errorText}>{text}</Text>
      <Pressable onPress={onRetry} style={styles.retryButton}>
        <RefreshCcw size={14} color={C.primary} />
        <Text style={styles.retryText}>Retry</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: C.background },
  scroll: { flex: 1 },
  scrollContent: { paddingBottom: 112 },
  hero: { paddingHorizontal: 16, paddingTop: 4, paddingBottom: 58, overflow: "hidden" },
  heroArt: { position: "absolute", right: -50, top: -54, width: 240, height: 240, opacity: 0.88 },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 9, marginTop: 6 },
  heroIdentity: { flex: 1, minWidth: 0 },
  heroEyebrow: { color: W.w65, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.2 },
  greetingRow: { flexDirection: "row", alignItems: "center", gap: 7, marginTop: 3 },
  heroTitle: { color: C.primaryFg, fontFamily: fonts.display, fontSize: 23, letterSpacing: -0.5 },
  iconButton: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: W.w15, borderWidth: 1, borderColor: W.w20 },
  badge: { position: "absolute", top: -2, right: -2, minWidth: 17, height: 17, borderRadius: 9, paddingHorizontal: 3, alignItems: "center", justifyContent: "center", backgroundColor: C.destructive, borderWidth: 2, borderColor: C.primaryDeep },
  badgeText: { color: C.destructiveFg, fontFamily: fonts.bold, fontSize: 8 },
  avatarText: { color: C.primaryFg, fontFamily: fonts.bold, fontSize: 12 },
  portfolioDeck: { flexDirection: "row", alignItems: "center", marginTop: 18 },
  portfolioCopy: { flex: 1, minWidth: 0, marginLeft: 14 },
  portfolioTitle: { color: C.primaryFg, fontFamily: fonts.heading, fontSize: 20, marginTop: 2 },
  heroChips: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  heroChip: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 9, height: 24, borderRadius: 999, backgroundColor: W.w15, borderWidth: 1, borderColor: W.w15 },
  heroChipDanger: { backgroundColor: "rgba(192,57,43,0.28)" },
  heroChipText: { color: C.primaryFg, fontFamily: fonts.semibold, fontSize: 9 },
  ring: { width: 76, height: 76, alignItems: "center", justifyContent: "center" },
  ringSvg: { position: "absolute", transform: [{ rotate: "-90deg" }] },
  ringContent: { alignItems: "center" },
  ringValue: { color: C.primaryFg, fontFamily: fonts.heading, fontSize: 17, lineHeight: 19 },
  ringLabel: { color: W.w70, fontFamily: fonts.bold, fontSize: 7, letterSpacing: 1.1, marginTop: 2 },
  body: { marginTop: -39, paddingHorizontal: 14, paddingBottom: 20 },
  statRow: { flexDirection: "row", gap: 7 },
  statTile: { flex: 1, minHeight: 82, alignItems: "center", backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, paddingHorizontal: 5, paddingVertical: 9, shadowColor: C.foreground, shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 },
  statIcon: { width: 29, height: 29, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  statValue: { fontFamily: fonts.heading, fontSize: 20, lineHeight: 22, marginTop: 5 },
  statLabel: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 8, textAlign: "center", lineHeight: 10, marginTop: 1 },
  sectionHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 21, marginBottom: 10 },
  sectionTitleRow: { flexDirection: "row", alignItems: "center", gap: 7 },
  sectionMark: { width: 3, height: 13, borderRadius: 2 },
  sectionTitle: { color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.25 },
  sectionAction: { flexDirection: "row", alignItems: "center" },
  sectionActionText: { color: C.info, fontFamily: fonts.semibold, fontSize: 11 },
  actionRow: { flexDirection: "row", gap: 9 },
  quickAction: { flex: 1, minHeight: 91, alignItems: "center", justifyContent: "center", backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 10, shadowColor: C.foreground, shadowOpacity: 0.04, shadowRadius: 5, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  actionIcon: { width: 45, height: 45, borderRadius: 15, alignItems: "center", justifyContent: "center" },
  actionLabel: { fontFamily: fonts.medium, fontSize: 10, textAlign: "center", marginTop: 7 },
  sectionStack: { gap: 10 },
  trialCard: { position: "relative", overflow: "hidden", backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, padding: 14, paddingLeft: 17, shadowColor: C.foreground, shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  trialStripe: { position: "absolute", left: 0, top: 0, bottom: 0, width: 5 },
  trialTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  protocolPill: { maxWidth: "56%", paddingHorizontal: 9, height: 24, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.58)", justifyContent: "center" },
  protocolText: { color: C.primary, fontFamily: fonts.mono, fontSize: 9 },
  trialStatusRow: { flexDirection: "row", alignItems: "center", gap: 5 },
  activePill: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 7, height: 22, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.14)" },
  activeDot: { width: 5, height: 5, borderRadius: 3, backgroundColor: C.success },
  activeText: { color: C.success, fontFamily: fonts.semibold, fontSize: 9, textTransform: "capitalize" },
  openIcon: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: C.surface },
  trialTitle: { fontFamily: fonts.heading, fontSize: 14, lineHeight: 18, marginTop: 9 },
  tags: { flexDirection: "row", flexWrap: "wrap", gap: 5, marginTop: 8 },
  tag: { paddingHorizontal: 8, height: 20, borderRadius: 999, justifyContent: "center", backgroundColor: C.surface },
  tagText: { color: C.mutedFg, fontFamily: fonts.medium, fontSize: 8 },
  progressMeta: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 11, marginBottom: 5 },
  progressLabel: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 9 },
  progressValue: { fontFamily: fonts.mono, fontSize: 9 },
  progressTrack: { height: 6, borderRadius: 999, backgroundColor: C.surface, overflow: "hidden" },
  progressFill: { height: "100%", borderRadius: 999 },
  trialAttribution: { flexDirection: "row", alignItems: "center", gap: 9, marginTop: 12, paddingTop: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.border },
  trialAttributionIcon: { width: 30, height: 30, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: C.secondary },
  trialCreatorCopy: { flex: 1, minWidth: 0 },
  trialAttributionLabel: { color: C.mutedFg, fontFamily: fonts.semibold, fontSize: 7, letterSpacing: 0.7 },
  trialCreatorName: { fontFamily: fonts.medium, fontSize: 10.5, lineHeight: 14, marginTop: 1 },
  trialCreatorRole: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 8.5, lineHeight: 11 },
  trialCreatedAt: { maxWidth: "42%", alignItems: "flex-end" },
  trialCreatedAtText: { color: C.mutedFg, fontFamily: fonts.mono, fontSize: 8.5, lineHeight: 12, marginTop: 2, textAlign: "right" },
  performanceCard: { backgroundColor: C.card, borderRadius: 20, borderWidth: 1, borderColor: C.border, paddingHorizontal: 14, shadowColor: C.foreground, shadowOpacity: 0.04, shadowRadius: 5, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  siteRow: { paddingVertical: 12 },
  siteDivider: { borderBottomWidth: 1, borderBottomColor: C.border },
  siteMeta: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 7 },
  siteTitleWrap: { flex: 1, minWidth: 0 },
  siteName: { fontFamily: fonts.medium, fontSize: 11 },
  siteSubscore: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 7.5, lineHeight: 11, marginTop: 2 },
  siteScoreWrap: { flexDirection: "row", alignItems: "center", gap: 2 },
  siteScore: { fontFamily: fonts.mono, fontSize: 10 },
  siteTrack: { height: 7, borderRadius: 999, backgroundColor: C.surface, overflow: "hidden" },
  siteFill: { height: "100%", borderRadius: 999 },
  notificationCard: { flexDirection: "row", alignItems: "flex-start", gap: 9, minHeight: 58, backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.border, padding: 10 },
  notificationIcon: { width: 34, height: 34, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  notificationCopy: { flex: 1, minWidth: 0, paddingTop: 1 },
  notificationTitle: { fontFamily: fonts.medium, fontSize: 11, lineHeight: 14 },
  notificationMessage: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 9, lineHeight: 12, marginTop: 2 },
  notificationTime: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 8, marginTop: 2 },
  unreadDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: C.destructive, marginTop: 4 },
  loadingCard: { minHeight: 82, alignItems: "center", justifyContent: "center", backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border },
  loadingText: { color: C.mutedFg, fontSize: 10, marginTop: 7 },
  emptyCard: { flexDirection: "row", alignItems: "center", gap: 11, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 13 },
  emptyIcon: { width: 40, height: 40, borderRadius: 13, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" },
  emptyCopy: { flex: 1 },
  emptyTitle: { fontFamily: fonts.semibold, fontSize: 12 },
  emptyDescription: { color: C.mutedFg, fontFamily: fonts.regular, fontSize: 9, lineHeight: 13, marginTop: 2 },
  errorCard: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 14, padding: 11, borderRadius: 15, borderWidth: 1, borderColor: "rgba(192,57,43,0.30)", backgroundColor: "rgba(192,57,43,0.07)" },
  errorText: { flex: 1, fontFamily: fonts.medium, fontSize: 10, lineHeight: 14 },
  retryButton: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 9, height: 30, borderRadius: 999, backgroundColor: C.card },
  retryText: { color: C.primary, fontFamily: fonts.semibold, fontSize: 9 },
  pressed: { opacity: 0.8, transform: [{ scale: 0.985 }] },
  disabled: { opacity: 0.45 },
  disabledText: { color: C.border },
});
