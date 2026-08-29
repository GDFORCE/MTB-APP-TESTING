import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Linking,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { router, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  Check,
  ChevronRight,
  FileCheck2,
  FileText,
  Mail,
  MapPin,
  Phone,
  Plus,
  Search,
  ShieldCheck,
  UploadCloud,
  UserRoundCheck,
  X,
} from "lucide-react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/auth/AuthContext";
import { useOrgContext } from "@/src/components/org-admin-kit";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { getSponsorDashboard } from "@/src/features/sponsor/api";
import type {
  RecruitmentFunnel,
  SponsorSite,
  SponsorTrial,
} from "@/src/features/sponsor/types";
import type { PickedAsset } from "@/src/lib/upload";
import { sanitizeAddress, sanitizeDesignation, sanitizeDigits, sanitizeName, sanitizeOrgName } from "@/src/lib/validators";
import { colors, dawnGradient, fonts, shadows } from "@/src/theme/tokens";

type SiteAccess = "full" | "restricted" | "view_only";
type SiteImportResult = {
  row: number;
  status: "imported" | "error";
  site_id?: string;
  invitation_id?: string;
  error?: string;
};
type SiteImportSummary = {
  total: number;
  imported: number;
  failed: number;
  results: SiteImportResult[];
};
type PiLookupStatus = "idle" | "checking" | "found" | "not_found" | "error";

const accessOptions: { value: SiteAccess; label: string; copy: string }[] = [
  {
    value: "full",
    label: "Full trial access",
    copy: "PI can manage participants, schedules and trial documents.",
  },
  {
    value: "restricted",
    label: "Restricted access",
    copy: "PI can manage assigned site activity without sponsor controls.",
  },
  {
    value: "view_only",
    label: "View only",
    copy: "PI can review shared trial information without making changes.",
  },
];

const statusTone = (value: string) => {
  const status = value.toLowerCase();
  if (status === "active") return { bg: "rgba(92,154,110,0.14)", fg: colors.success };
  if (status === "completed") return { bg: "rgba(123,107,184,0.14)", fg: colors.info };
  if (status === "terminated") return { bg: "rgba(192,57,43,0.12)", fg: colors.destructive };
  return { bg: colors.surface, fg: colors.mutedFg };
};

function SiteDetail({ site, onBack }: { site: SponsorSite; onBack: () => void }) {
  const location = [site.hospital, site.address, site.city, site.state].filter(Boolean).join(", ");
  const principalInvestigators = site.pis?.length
    ? site.pis
    : [{ name: site.pi || "Not assigned", phone: site.piPhone, email: site.piEmail }];
  const access = accessOptions.find((option) => option.value === site.accessType)
    ?? accessOptions[0];
  return (
    <View style={styles.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={styles.compactHeader}>
        <Pressable onPress={onBack} hitSlop={10}><ArrowLeft size={20} color={colors.white} /></Pressable>
        <Text numberOfLines={1} style={styles.headerTitle}>{site.name}</Text>
      </SafeAreaView>
      <ScrollView contentContainerStyle={styles.detailContent} showsVerticalScrollIndicator={false}>
        <LinearGradient colors={dawnGradient as any} style={styles.siteHero}>
          <View style={styles.between}>
            <View style={{ flex: 1 }}>
              <Text style={styles.siteHeroTitle}>{site.name}</Text>
              {!!location && <Text style={styles.siteHeroMeta}>⌖ {location}</Text>}
            </View>
            <StatusBadge value={site.status} onDark />
          </View>
        </LinearGradient>

        <View style={styles.contactGrid}>
          {principalInvestigators.map((pi, index) => (
            <ContactCard
              key={pi.id || pi.email || `${pi.name}-${index}`}
              title={principalInvestigators.length > 1 ? `Principal Investigator ${index + 1}` : "Principal Investigator"}
              name={pi.name}
              phone={pi.phone}
              email={pi.email}
            />
          ))}
          <ContactCard title="Coordinator" name={site.crc || "Not assigned"} />
        </View>

        <View style={styles.card}>
          <View style={styles.between}>
            <Text style={styles.cardTitle}>Patient Enrollment</Text>
            <Text style={styles.metricStrong}>{site.enrolled}/{site.target || "—"}</Text>
          </View>
          <Progress value={site.enrollmentPct} />
          <View style={styles.privacyNote}>
            <UserRoundCheck size={16} color={colors.mutedFg} />
            <Text style={styles.privacyText}>Patient-level data remains managed by the site investigator.</Text>
          </View>
        </View>

        <View style={styles.card}>
          <View style={styles.between}>
            <View>
              <Text style={styles.cardTitle}>Recruitment Funnel</Text>
              <Text style={styles.cardSubtitle}>Site-level participant progress</Text>
            </View>
            <View style={styles.targetBadge}>
              <Text style={styles.targetBadgeLabel}>TARGET</Text>
              <Text style={styles.targetBadgeValue}>{site.target || "—"}</Text>
            </View>
          </View>
          {site.recruitment ? (
            <RecruitmentGrid data={site.recruitment} />
          ) : (
            <View style={styles.unavailablePanel}>
              <Text style={styles.unavailableTitle}>Recruitment detail unavailable</Text>
              <Text style={styles.unavailableCopy}>
                Enrollment totals are shown above. Funnel stages will appear when the site reports participant statuses.
              </Text>
            </View>
          )}
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Performance Metrics</Text>
          <View style={styles.metricGrid}>
            <Metric value={`${site.enrollmentPct}%`} label="Enrollment Rate" />
            <Metric
              value={site.visitCompliance !== undefined ? `${site.visitCompliance}%` : "—"}
              label="Visit Compliance"
            />
            <Metric value={String(site.trials.length)} label="Assigned Trials" />
          </View>
          {!!site.overdueVisits && (
            <View style={styles.warningRow}>
              <AlertTriangle size={15} color={colors.destructive} />
              <Text style={styles.warningText}>{site.overdueVisits} overdue visits need attention</Text>
            </View>
          )}
        </View>

        <View style={styles.accessSummary}>
          <ShieldCheck size={17} color={colors.info} />
          <View style={{ flex: 1 }}>
            <Text style={styles.accessSummaryLabel}>SITE ACCESS</Text>
            <Text style={styles.accessSummaryTitle}>{access.label}</Text>
            <Text style={styles.accessSummaryCopy}>{access.copy}</Text>
          </View>
        </View>

        <View>
          <Text style={styles.sectionTitle}>ASSIGNED TRIALS</Text>
          {site.trials.length ? site.trials.map((trial) => (
            <Pressable
              key={trial.id}
              onPress={() => router.push({
                pathname: "/(app)/clinical/trial-summary",
                params: { id: trial.id },
              })}
              style={({ pressed }) => [styles.trialPanel, pressed && styles.pressed]}
            >
              <View style={styles.between}>
                <Text style={styles.protocol}>{trial.protocolId}</Text>
                <StatusBadge value={trial.status || "Active"} />
              </View>
              <Text style={styles.trialTitle}>{trial.title}</Text>
              <Text style={styles.trialMeta}>
                {[trial.phase, trial.condition, trial.drug].filter(Boolean).join(" · ")}
              </Text>
            </Pressable>
          )) : (
            <View style={styles.emptyCompact}><Text style={styles.muted}>No trials assigned yet.</Text></View>
          )}
        </View>
      </ScrollView>
    </View>
  );
}

function RecruitmentGrid({ data }: { data: RecruitmentFunnel }) {
  const stages: [string, number][] = [
    ["Screened", data.screened],
    ["Screen fails", data.screen_fail],
    ["Randomized", data.randomized],
    ["Active", data.active],
    ["Follow-up", data.follow_up],
    ["Completed", data.completed],
    ["Withdrawn", data.withdrawn],
    ["Dropout", data.dropout],
  ];
  return (
    <View style={styles.funnelGrid}>
      {stages.map(([label, value]) => (
        <View key={label} style={styles.funnelMetric}>
          <Text style={styles.funnelValue}>{value}</Text>
          <Text style={styles.funnelLabel}>{label}</Text>
        </View>
      ))}
    </View>
  );
}

function ContactCard({
  title,
  name,
  phone,
  email,
}: {
  title: string;
  name: string;
  phone?: string;
  email?: string;
}) {
  return (
    <View style={styles.contactCard}>
      <Text style={styles.contactLabel}>{title}</Text>
      <Text numberOfLines={2} style={styles.contactName}>{name}</Text>
      <View style={styles.contactActions}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Call ${name}`}
          disabled={!phone}
          onPress={() => phone && Linking.openURL(`tel:${phone}`)}
          style={({ pressed }) => [
            styles.contactIcon,
            !phone && styles.contactIconDisabled,
            pressed && styles.pressed,
          ]}
        >
          <Phone size={14} color={phone ? colors.accent : colors.mutedFg} />
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Email ${name}`}
          disabled={!email}
          onPress={() => email && Linking.openURL(`mailto:${email}`)}
          style={({ pressed }) => [
            styles.contactIcon,
            styles.emailIcon,
            !email && styles.contactIconDisabled,
            pressed && styles.pressed,
          ]}
        >
          <Mail size={14} color={email ? colors.info : colors.mutedFg} />
        </Pressable>
      </View>
    </View>
  );
}

function FormField({
  label,
  ...inputProps
}: {
  label: string;
} & React.ComponentProps<typeof TextInput>) {
  return (
    <View>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.mutedFg}
        style={styles.input}
        {...inputProps}
      />
    </View>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  return (
    <View style={styles.metricBox}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function StatusBadge({ value, onDark = false }: { value: string; onDark?: boolean }) {
  const tone = statusTone(value);
  return (
    <View style={[styles.status, onDark ? styles.statusOnDark : { backgroundColor: tone.bg }]}>
      <Text style={[styles.statusText, { color: onDark ? colors.white : tone.fg }]}>{value}</Text>
    </View>
  );
}

function Progress({ value }: { value: number }) {
  return (
    <View style={styles.track}>
      <LinearGradient
        colors={dawnGradient as any}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 0 }}
        style={[styles.fill, { width: `${Math.max(2, Math.min(100, value))}%` }]}
      />
    </View>
  );
}

export default function SponsorSitesScreen() {
  const insets = useSafeAreaInsets();
  const { siteId, add } = useLocalSearchParams<{ siteId?: string; add?: string }>();
  const { user } = useAuth();
  const { orgId } = useOrgContext();
  const unread = useUnreadCount();
  const [sites, setSites] = useState<SponsorSite[]>([]);
  const [trials, setTrials] = useState<SponsorTrial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [selected, setSelected] = useState<SponsorSite | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [entryMode, setEntryMode] = useState<"single" | "upload">("single");
  const [selectedTrialId, setSelectedTrialId] = useState("");
  const [showTrials, setShowTrials] = useState(false);
  const [siteName, setSiteName] = useState("");
  const [address, setAddress] = useState("");
  const [city, setCity] = useState("");
  const [stateName, setStateName] = useState("");
  const [hospitalType, setHospitalType] = useState<"Private" | "Government">("Private");
  const [piName, setPiName] = useState("");
  const [department, setDepartment] = useState("");
  const [piEmail, setPiEmail] = useState("");
  const [piPhone, setPiPhone] = useState("");
  const [piLookupStatus, setPiLookupStatus] = useState<PiLookupStatus>("idle");
  const [matchedPiEmail, setMatchedPiEmail] = useState("");
  const [targetEnrollment, setTargetEnrollment] = useState("");
  const [accessType, setAccessType] = useState<SiteAccess>("full");
  const [uploadAsset, setUploadAsset] = useState<PickedAsset | null>(null);
  const [uploadComplete, setUploadComplete] = useState(false);
  const [importSummary, setImportSummary] = useState<SiteImportSummary | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const canAddSite = Boolean(user?.org_admin && orgId);
  const roleLabel = user?.role === "cro" ? "CRO" : "Sponsor";
  const organization = user?.organization || "";
  const fullName = user?.full_name || "";
  const initials = user?.avatar_initials || fullName.split(/\s+/).filter(Boolean).map((word) => word[0]).slice(0, 2).join("").toUpperCase() || "?";

  const load = useCallback(async () => {
    setError("");
    try {
      const dashboard = await getSponsorDashboard();
      setSites(dashboard.sites);
      setTrials(dashboard.trials);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load your sites.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!siteId || !sites.length) return;
    const requested = sites.find((site) => site.id === siteId);
    if (requested) {
      setSelected(requested);
      router.setParams({ siteId: undefined });
    }
  }, [siteId, sites]);

  useEffect(() => {
    if (add !== "1" || !canAddSite) return;
    setShowAdd(true);
    router.setParams({ add: undefined });
  }, [add, canAddSite]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sites.filter((site) => {
      const matchesStatus = filter === "All" || site.status.toLowerCase() === filter.toLowerCase();
      const haystack = [site.name, site.hospital, site.address, site.city, site.state, site.pi]
        .filter(Boolean).join(" ").toLowerCase();
      return matchesStatus && (!needle || haystack.includes(needle));
    });
  }, [filter, query, sites]);

  const resetForm = () => {
    setEntryMode("single");
    setSelectedTrialId("");
    setShowTrials(false);
    setSiteName("");
    setAddress("");
    setCity("");
    setStateName("");
    setHospitalType("Private");
    setPiName("");
    setDepartment("");
    setPiEmail("");
    setPiPhone("");
    setPiLookupStatus("idle");
    setMatchedPiEmail("");
    setTargetEnrollment("");
    setAccessType("full");
    setUploadAsset(null);
    setUploadComplete(false);
    setImportSummary(null);
    setFormError("");
  };

  const closeAdd = () => {
    if (saving) return;
    setShowAdd(false);
    resetForm();
  };

  const pickSiteDocument = async () => {
    setFormError("");
    const result = await DocumentPicker.getDocumentAsync({
      type: [
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
      ],
      copyToCacheDirectory: true,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      setUploadAsset({
        uri: asset.uri,
        name: asset.name || "site-roster",
        mimeType: asset.mimeType || undefined,
        file: asset.file,
      });
      setUploadComplete(false);
      setImportSummary(null);
    }
  };

  const uploadSiteDocument = async () => {
    if (!selectedTrialId) {
      setFormError("Select the trial that this site roster belongs to.");
      return;
    }
    if (!uploadAsset) {
      setFormError("Choose a CSV site roster to import.");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      const form = new FormData();
      if (Platform.OS === "web") {
        let file: Blob | undefined = uploadAsset.file as File | undefined;
        if (!file) file = await (await fetch(uploadAsset.uri)).blob();
        form.append("file", file, uploadAsset.name);
      } else {
        form.append("file", {
          uri: uploadAsset.uri,
          name: uploadAsset.name,
          type: uploadAsset.mimeType || "text/csv",
        } as any);
      }
      const response = await api.post(
        `/sponsor/trials/${selectedTrialId}/sites/import`,
        form,
        {
          headers: { "Content-Type": "multipart/form-data" },
          timeout: 60000,
        },
      );
      setImportSummary(response.data as SiteImportSummary);
      setUploadComplete(true);
      await load();
    } catch (e: any) {
      setFormError(
        e?.response?.data?.detail
        || "Couldn't import this roster. Check the CSV format and try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  const addSite = async () => {
    if (!selectedTrialId) { setFormError("Select the trial this site will run."); return; }
    if (!siteName.trim()) { setFormError("Enter the site or hospital name."); return; }
    if (!piName.trim()) { setFormError("Enter the principal investigator's name."); return; }
    if (!piEmail.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(piEmail.trim())) {
      setFormError("Enter a valid PI email address.");
      return;
    }
    const target = targetEnrollment.trim() ? Number(targetEnrollment) : undefined;
    if (target !== undefined && (!Number.isInteger(target) || target < 0)) {
      setFormError("Target allocation must be a whole number.");
      return;
    }
    setSaving(true); setFormError("");
    try {
      await api.post(`/sponsor/trials/${selectedTrialId}/sites`, {
        name: siteName.trim(),
        address: address.trim(),
        city: city.trim(),
        state: stateName.trim(),
        hospital_type: hospitalType,
        department: department.trim(),
        pi_name: piName.trim(),
        pi_email: piEmail.trim().toLowerCase(),
        pi_phone: piPhone.trim(),
        target_enrollment: target,
        access_type: accessType,
      });
      setShowAdd(false);
      resetForm();
      setLoading(true);
      await load();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || "Couldn't add this site.");
    } finally { setSaving(false); }
  };

  const changePiEmail = (value: string) => {
    const normalizedEmail = value.trim().toLowerCase();
    if (matchedPiEmail && normalizedEmail !== matchedPiEmail) {
      setPiName("");
      setDepartment("");
      setPiPhone("");
      setMatchedPiEmail("");
    }
    setPiEmail(value);
    setPiLookupStatus("idle");
  };

  const lookupPi = async () => {
    const email = piEmail.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;
    setPiLookupStatus("checking");
    try {
      const response = await api.get("/sponsor/pi-lookup", { params: { email } });
      if (!response.data?.found) {
        setMatchedPiEmail("");
        setPiLookupStatus("not_found");
        return;
      }
      const pi = response.data.pi || {};
      setPiName(pi.full_name || "");
      setDepartment(pi.department || "");
      setPiPhone(pi.phone || "");
      setSiteName((current) => current.trim() ? current : (pi.organization || ""));
      setMatchedPiEmail(email);
      setPiLookupStatus("found");
    } catch {
      setPiLookupStatus("error");
    }
  };

  const selectedTrial = trials.find((trial) => trial.id === selectedTrialId);
  const canSaveSite = Boolean(
    selectedTrialId
    && siteName.trim()
    && piName.trim()
    && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(piEmail.trim()),
  );

  if (selected) return <SiteDetail site={selected} onBack={() => setSelected(null)} />;

  return (
    <View style={styles.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={styles.compactHeader}>
        <View style={styles.headerIdentity}>
          <Text style={styles.headerEyebrow} numberOfLines={1}>{roleLabel}{organization ? ` · ${organization}` : ""}</Text>
          <Text style={styles.headerTitle}>Sites</Text>
        </View>
        <Pressable onPress={() => router.push("/(app)/sponsor/notifications" as never)} style={styles.iconButton} accessibilityLabel="Open notifications">
          <Bell size={18} color={colors.primaryFg} />
          {!!unread && unread > 0 && <View style={styles.notifBadge}><Text style={styles.notifBadgeText}>{Math.min(9, unread)}</Text></View>}
        </Pressable>
        <Pressable onPress={() => router.push("/(app)/sponsor/profile" as never)} style={styles.iconButton} accessibilityLabel="Open profile">
          <Text style={styles.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      <View style={styles.searchRow}>
        <View style={styles.searchBox}>
          <Search size={17} color={colors.mutedFg} />
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search sites..."
            placeholderTextColor={colors.mutedFg}
            style={styles.searchInput}
          />
          {!!query && <Pressable onPress={() => setQuery("")}><X size={16} color={colors.mutedFg} /></Pressable>}
        </View>
        {canAddSite && (
          <Pressable testID="sites-add" onPress={() => setShowAdd(true)} style={styles.addSiteButton}>
            <Plus size={14} color={colors.white} /><Text style={styles.addSiteButtonText}>Add Site</Text>
          </Pressable>
        )}
      </View>

      <View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
          {["All", "Active", "Completed", "Terminated"].map((value) => {
            const count = value === "All" ? sites.length : sites.filter((site) => site.status.toLowerCase() === value.toLowerCase()).length;
            const active = filter === value;
            return (
              <Pressable key={value} onPress={() => setFilter(value)} style={[styles.filter, active && styles.filterActive]}>
                <Text style={[styles.filterText, active && styles.filterTextActive]}>{value} ({count})</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : error ? (
        <View style={styles.center}>
          <AlertTriangle size={28} color={colors.destructive} />
          <Text style={styles.errorText}>{error}</Text>
          <Pressable onPress={() => { setLoading(true); load(); }} style={styles.retry}><Text style={styles.retryText}>Try again</Text></Pressable>
        </View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={colors.primary} />}
        >
          {filtered.length ? filtered.map((site) => (
            <Pressable key={site.id} onPress={() => setSelected(site)} style={({ pressed }) => [styles.siteCard, pressed && styles.pressed]}>
              <View style={styles.between}>
                <Text numberOfLines={1} style={styles.siteName}>{site.name}</Text>
                <StatusBadge value={site.status} />
              </View>
              <View style={styles.locationRow}>
                <MapPin size={11} color={colors.mutedFg} />
                <Text numberOfLines={1} style={styles.siteAddress}>
                  {[site.hospital, site.address, site.city, site.state].filter(Boolean).join(", ") || "Address not provided"}
                </Text>
              </View>
              {!!site.hospitalType && (
                <View style={styles.hospitalTypeBadge}>
                  <Text style={styles.hospitalTypeText}>{site.hospitalType}</Text>
                </View>
              )}
              <Text style={styles.trialDetailsLabel}>TRIAL DETAILS</Text>
              {site.trials.length ? site.trials.map((trial) => (
                <View key={trial.id} style={styles.siteTrialPanel}>
                  <View style={styles.trialPanelTop}>
                    <Text numberOfLines={1} style={styles.siteProtocol}>{trial.protocolId}</Text>
                    <View style={styles.trialTopRight}>
                      <StatusBadge value={trial.status || "active"} />
                      <ChevronRight size={13} color={colors.mutedFg} />
                    </View>
                  </View>
                  <View style={styles.trialGrid}>
                    <View style={styles.trialField}><Text style={styles.trialFieldLabel}>PHASE</Text><Text numberOfLines={1} style={styles.trialFieldValue}>{trial.phase || "—"}</Text></View>
                    <View style={styles.trialField}><Text style={styles.trialFieldLabel}>DISEASE</Text><Text numberOfLines={1} style={styles.trialFieldValue}>{trial.condition || "—"}</Text></View>
                    <View style={styles.trialField}><Text style={styles.trialFieldLabel}>DRUG</Text><Text numberOfLines={1} style={styles.trialFieldValue}>{trial.drug || "—"}</Text></View>
                    <View style={styles.trialField}><Text style={styles.trialFieldLabel}>PI NAMES</Text><Text numberOfLines={2} style={styles.trialFieldValue}>{site.pis?.map((pi) => pi.name).filter(Boolean).join(", ") || trial.piName || site.pi || "Not assigned"}</Text></View>
                    <View style={[styles.trialField, styles.departmentField]}>
                      <Text style={[styles.trialFieldLabel, styles.departmentText]}>DEPARTMENT</Text>
                      <Text numberOfLines={1} style={[styles.trialFieldValue, styles.departmentText]}>{trial.department || site.department || "Not provided"}</Text>
                    </View>
                  </View>
                </View>
              )) : <Text style={styles.noTrials}>No trials assigned to this site.</Text>}
            </Pressable>
          )) : (
            <View style={styles.empty}>
              <View style={styles.emptyIcon}><MapPin size={25} color={colors.primary} /></View>
              <Text style={styles.emptyTitle}>No sites found</Text>
              <Text style={styles.muted}>Try another search or filter.</Text>
            </View>
          )}
        </ScrollView>
      )}

      <SponsorBottomNav active="sites" />

      {showAdd && (
        <View style={styles.overlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={closeAdd} />
          <View style={[styles.sheet, { paddingBottom: 20 + Math.max(insets.bottom, 16) }]}>
            <View style={styles.sheetHandle} />
            <View style={styles.between}>
              <View>
                <Text style={styles.sheetEyebrow}>ORGANIZATION NETWORK</Text>
                <Text style={styles.sheetTitle}>Add a new site</Text>
              </View>
              <Pressable onPress={closeAdd} style={styles.close}><X size={18} color={colors.foreground} /></Pressable>
            </View>

            <View style={styles.segmented}>
              {([
                ["single", "Single Entry"],
                ["upload", "Upload File"],
              ] as const).map(([value, label]) => (
                <Pressable
                  key={value}
                  onPress={() => { setEntryMode(value); setFormError(""); }}
                  style={[styles.segment, entryMode === value && styles.segmentActive]}
                >
                  <Text style={[styles.segmentText, entryMode === value && styles.segmentTextActive]}>{label}</Text>
                </Pressable>
              ))}
            </View>

            {entryMode === "upload" ? (
              <>
                <ScrollView
                  style={styles.formScroll}
                  contentContainerStyle={styles.uploadContent}
                  keyboardShouldPersistTaps="handled"
                  showsVerticalScrollIndicator={false}
                >
                <Text style={styles.panelLabel}>TRIAL DETAILS</Text>
                <Text style={styles.fieldLabel}>PROTOCOL / TRIAL *</Text>
                <Pressable
                  onPress={() => setShowTrials((value) => !value)}
                  style={[styles.input, styles.selectInput]}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={selectedTrial ? styles.selectValue : styles.selectPlaceholder}>
                      {selectedTrial ? selectedTrial.protocolId : "Select a trial"}
                    </Text>
                    {!!selectedTrial && (
                      <Text numberOfLines={1} style={styles.selectMeta}>{selectedTrial.title}</Text>
                    )}
                  </View>
                  <Text style={styles.selectChevron}>{showTrials ? "▲" : "▼"}</Text>
                </Pressable>
                {showTrials && (
                  <View style={styles.trialOptions}>
                    {trials.length ? trials.map((trial) => (
                      <Pressable
                        key={trial.id}
                        onPress={() => {
                          setSelectedTrialId(trial.id);
                          setShowTrials(false);
                          setUploadComplete(false);
                          setImportSummary(null);
                        }}
                        style={[
                          styles.trialOption,
                          selectedTrialId === trial.id && styles.trialOptionSelected,
                        ]}
                      >
                        <Text style={styles.trialOptionProtocol}>{trial.protocolId}</Text>
                        <Text numberOfLines={1} style={styles.trialOptionTitle}>{trial.title}</Text>
                      </Pressable>
                    )) : (
                      <Text style={styles.uploadNote}>No trials are available for roster upload.</Text>
                    )}
                  </View>
                )}

                <Pressable
                  onPress={pickSiteDocument}
                  disabled={saving}
                  style={({ pressed }) => [
                    styles.uploadBox,
                    uploadAsset && styles.uploadBoxSelected,
                    pressed && styles.pressed,
                  ]}
                >
                  {uploadAsset
                    ? <FileText size={25} color={colors.success} />
                    : <UploadCloud size={27} color={colors.primary} />}
                  <Text style={styles.uploadTitle}>
                    {uploadAsset?.name || "Choose a site roster"}
                  </Text>
                  <Text style={styles.uploadHint}>
                    {uploadAsset ? "Tap to replace this file" : "CSV file · one site per row"}
                  </Text>
                </Pressable>
                <Text style={styles.uploadNote}>
                  Headers: name, address, city, state, hospital_type, department, pi_name, pi_email, pi_phone, target_enrollment, access_type.
                </Text>
                {uploadComplete && importSummary && (
                  <View style={styles.uploadSuccess}>
                    <FileCheck2 size={18} color={colors.success} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.uploadSuccessTitle}>
                        {importSummary.imported} of {importSummary.total} sites imported
                      </Text>
                      <Text style={styles.uploadSuccessCopy}>
                        {importSummary.failed
                          ? `${importSummary.failed} row${importSummary.failed === 1 ? "" : "s"} need correction before retrying.`
                          : `Every PI invitation for ${selectedTrial?.protocolId} was created successfully.`}
                      </Text>
                    </View>
                  </View>
                )}
                {!!importSummary?.results.length && (
                  <ScrollView style={styles.importResults} nestedScrollEnabled>
                    {importSummary.results.map((result) => (
                      <View
                        key={`${result.row}-${result.status}`}
                        style={styles.importResultRow}
                      >
                        <View style={[
                          styles.resultMark,
                          result.status === "error" && styles.resultMarkError,
                        ]}>
                          {result.status === "imported"
                            ? <Check size={11} color={colors.success} />
                            : <X size={11} color={colors.destructive} />}
                        </View>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.importResultTitle}>
                            Row {result.row} · {result.status === "imported" ? "Imported" : "Needs correction"}
                          </Text>
                          {!!result.error && (
                            <Text style={styles.importResultError}>{result.error}</Text>
                          )}
                        </View>
                      </View>
                    ))}
                  </ScrollView>
                )}
                {!!formError && <Text style={styles.formError}>{formError}</Text>}
                </ScrollView>
                <Pressable
                  onPress={uploadSiteDocument}
                  disabled={saving || uploadComplete || !uploadAsset || !selectedTrialId}
                  style={[
                    styles.primaryButton,
                    (saving || uploadComplete || !uploadAsset || !selectedTrialId)
                      && styles.primaryButtonDisabled,
                  ]}
                >
                  {saving ? (
                    <ActivityIndicator color={colors.white} />
                  ) : uploadComplete ? (
                    <>
                      <Check size={18} color={colors.white} />
                      <Text style={styles.primaryButtonText}>Uploaded</Text>
                    </>
                  ) : (
                    <>
                      <UploadCloud size={18} color={colors.white} />
                      <Text style={styles.primaryButtonText}>Import sites & invite PIs</Text>
                    </>
                  )}
                </Pressable>
              </>
            ) : (
              <>
                <ScrollView
                  style={styles.formScroll}
                  contentContainerStyle={styles.formContent}
                  keyboardShouldPersistTaps="handled"
                  showsVerticalScrollIndicator={false}
                >
                  <Text style={styles.panelLabel}>TRIAL DETAILS</Text>
                  <Text style={styles.fieldLabel}>PROTOCOL / TRIAL *</Text>
                  <Pressable
                    onPress={() => setShowTrials((value) => !value)}
                    style={[styles.input, styles.selectInput]}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={selectedTrial ? styles.selectValue : styles.selectPlaceholder}>
                        {selectedTrial ? selectedTrial.protocolId : "Select a trial"}
                      </Text>
                      {!!selectedTrial && <Text numberOfLines={1} style={styles.selectMeta}>{selectedTrial.title}</Text>}
                    </View>
                    <Text style={styles.selectChevron}>{showTrials ? "▲" : "▼"}</Text>
                  </Pressable>
                  {showTrials && (
                    <View style={styles.trialOptions}>
                      {trials.length ? trials.map((trial) => (
                        <Pressable
                          key={trial.id}
                          onPress={() => {
                            setSelectedTrialId(trial.id);
                            setShowTrials(false);
                          }}
                          style={[styles.trialOption, selectedTrialId === trial.id && styles.trialOptionSelected]}
                        >
                          <Text style={styles.trialOptionProtocol}>{trial.protocolId}</Text>
                          <Text numberOfLines={1} style={styles.trialOptionTitle}>{trial.title}</Text>
                        </Pressable>
                      )) : <Text style={styles.uploadNote}>No trials are available for site assignment.</Text>}
                    </View>
                  )}

                  <Text style={styles.panelLabel}>SITE DETAILS</Text>
                  <FormField label="SITE / HOSPITAL NAME *" value={siteName} onChangeText={(v: string) => setSiteName(sanitizeOrgName(v))} placeholder="Apollo Site 04" />
                  <FormField label="ADDRESS" value={address} onChangeText={(v: string) => setAddress(sanitizeAddress(v))} placeholder="Building and street" />
                  <View style={styles.fieldRow}>
                    <View style={styles.fieldHalf}>
                      <FormField label="CITY" value={city} onChangeText={(v: string) => setCity(sanitizeAddress(v))} placeholder="Mumbai" />
                    </View>
                    <View style={styles.fieldHalf}>
                      <FormField label="STATE" value={stateName} onChangeText={(v: string) => setStateName(sanitizeName(v))} placeholder="Maharashtra" />
                    </View>
                  </View>

                  <Text style={styles.fieldLabel}>HOSPITAL TYPE</Text>
                  <View style={styles.choiceRow}>
                    {(["Private", "Government"] as const).map((value) => (
                      <Pressable
                        key={value}
                        onPress={() => setHospitalType(value)}
                        style={[styles.choice, hospitalType === value && styles.choiceActive]}
                      >
                        <Text style={[styles.choiceText, hospitalType === value && styles.choiceTextActive]}>{value}</Text>
                      </Pressable>
                    ))}
                  </View>

                  <Text style={styles.panelLabel}>PRINCIPAL INVESTIGATOR</Text>
                  <FormField
                    label="PI EMAIL *"
                    value={piEmail}
                    onChangeText={changePiEmail}
                    onBlur={lookupPi}
                    placeholder="pi@hospital.com"
                    keyboardType="email-address"
                    autoCapitalize="none"
                  />
                  {piLookupStatus !== "idle" && (
                    <View style={[
                      styles.piLookupMessage,
                      piLookupStatus === "found" && styles.piLookupFound,
                      piLookupStatus === "not_found" && styles.piLookupNotFound,
                    ]}>
                      {piLookupStatus === "checking" && <ActivityIndicator size="small" color={colors.info} />}
                      <Text style={styles.piLookupText}>
                        {piLookupStatus === "checking"
                          ? "Checking PI registration…"
                          : piLookupStatus === "found"
                            ? "Registered PI found. Available profile details were filled automatically."
                            : piLookupStatus === "not_found"
                              ? "This PI is not registered. Fill the details manually; an invitation will be sent."
                              : "PI lookup is temporarily unavailable. You can continue by entering the details manually."}
                      </Text>
                    </View>
                  )}
                  <FormField label="PI NAME *" value={piName} onChangeText={(v: string) => setPiName(sanitizeName(v))} placeholder="Dr. First Last" />
                  <FormField label="DEPARTMENT" value={department} onChangeText={(v: string) => setDepartment(sanitizeDesignation(v))} placeholder="Endocrinology" />
                  <FormField label="PI PHONE" value={piPhone} onChangeText={(v: string) => setPiPhone(sanitizeDigits(v, 10))} placeholder="+91 98765 43210" keyboardType="phone-pad" />
                  <FormField
                    label="TARGET ALLOCATION"
                    value={targetEnrollment}
                    onChangeText={(v: string) => setTargetEnrollment(sanitizeDigits(v))}
                    placeholder="e.g. 40"
                    keyboardType="number-pad"
                  />

                  <Text style={styles.fieldLabel}>ACCESS TYPE</Text>
                  <View style={styles.accessOptions}>
                    {accessOptions.map((option) => {
                      const active = accessType === option.value;
                      return (
                        <Pressable
                          key={option.value}
                          accessibilityRole="radio"
                          accessibilityState={{ checked: active }}
                          onPress={() => setAccessType(option.value)}
                          style={[styles.accessOption, active && styles.accessOptionActive]}
                        >
                          <View style={[styles.radio, active && styles.radioActive]}>
                            {active && <View style={styles.radioDot} />}
                          </View>
                          <View style={{ flex: 1 }}>
                            <Text style={[styles.accessOptionTitle, active && styles.accessOptionTitleActive]}>
                              {option.label}
                            </Text>
                            <Text style={styles.accessOptionCopy}>{option.copy}</Text>
                          </View>
                        </Pressable>
                      );
                    })}
                  </View>
                  <Text style={styles.accessHelp}>
                    Access applies only to the selected trial. The PI receives a secure invitation after saving.
                  </Text>

                  {!!formError && <Text style={styles.formError}>{formError}</Text>}
                </ScrollView>
                <Pressable
                  onPress={addSite}
                  disabled={saving || !canSaveSite}
                  style={[
                    styles.primaryButton,
                    (saving || !canSaveSite) && styles.primaryButtonDisabled,
                  ]}
                >
                  {saving ? <ActivityIndicator color={colors.white} /> : <><Check size={18} color={colors.white} /><Text style={styles.primaryButtonText}>Save & Share with PI</Text></>}
                </Pressable>
              </>
            )}
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  compactHeader: {
    minHeight: 74, paddingHorizontal: 18, paddingBottom: 13, paddingTop: 8,
    backgroundColor: colors.primaryDeep, flexDirection: "row", alignItems: "center",
    gap: 10,
  },
  headerIdentity: { flex: 1, minWidth: 0 },
  headerEyebrow: { fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1, color: "rgba(255,255,255,0.65)", textTransform: "uppercase" },
  headerTitle: { flex: 1, fontFamily: fonts.heading, fontSize: 19, color: colors.white },
  iconButton: { width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.15)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)" },
  avatarText: { fontFamily: fonts.bold, fontSize: 12, color: colors.primaryFg },
  notifBadge: { position: "absolute", top: -2, right: -2, minWidth: 16, height: 16, borderRadius: 8, paddingHorizontal: 3, alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive, borderWidth: 2, borderColor: colors.primaryDeep },
  notifBadgeText: { fontFamily: fonts.bold, fontSize: 8, color: colors.white },
  headerAction: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 12, height: 36, borderRadius: 14, backgroundColor: "rgba(255,255,255,0.14)" },
  headerActionText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.white },
  searchRow: { paddingHorizontal: 16, paddingTop: 14, flexDirection: "row", alignItems: "center", gap: 8 },
  searchBox: { flex: 1, height: 42, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: 15 },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 14, color: colors.foreground, outlineStyle: "none" } as any,
  addSiteButton: { height: 42, paddingHorizontal: 12, borderRadius: 14, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4, backgroundColor: colors.primary, ...shadows.sm },
  addSiteButtonText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.white },
  filters: { paddingHorizontal: 16, paddingVertical: 12, gap: 8 },
  filter: { paddingHorizontal: 13, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  filterActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  filterText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg },
  filterTextActive: { color: colors.white },
  list: { padding: 16, paddingTop: 2, paddingBottom: 28, gap: 10 },
  siteCard: { padding: 13, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  between: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  siteIdentity: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 10 },
  siteIcon: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 14, backgroundColor: "rgba(230,155,92,0.13)" },
  siteName: { flex: 1, fontFamily: fonts.semibold, fontSize: 13.5, color: colors.foreground },
  locationRow: { marginTop: 5, flexDirection: "row", alignItems: "center", gap: 4 },
  siteAddress: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  hospitalTypeBadge: { alignSelf: "flex-start", marginTop: 7, paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.secondary },
  hospitalTypeText: { fontFamily: fonts.semibold, fontSize: 8.5, color: colors.secondaryFg },
  trialDetailsLabel: { marginTop: 11, marginBottom: 7, fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.8, color: colors.mutedFg },
  siteTrialPanel: { marginBottom: 8, padding: 11, borderRadius: 16, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  trialPanelTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  siteProtocol: { flex: 1, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  trialTopRight: { flexDirection: "row", alignItems: "center", gap: 5 },
  trialGrid: { marginTop: 9, flexDirection: "row", flexWrap: "wrap", rowGap: 8 },
  trialField: { width: "50%", paddingRight: 8 },
  departmentField: { width: "100%", paddingRight: 0, alignItems: "center", transform: [{ translateX: -42 }] },
  departmentText: { textAlign: "center" },
  trialFieldLabel: { fontFamily: fonts.semibold, fontSize: 7.5, letterSpacing: 0.35, color: colors.mutedFg },
  trialFieldValue: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.foreground },
  noTrials: { paddingVertical: 10, fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  status: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999 },
  statusOnDark: { backgroundColor: "rgba(255,255,255,0.16)", borderWidth: 1, borderColor: "rgba(255,255,255,0.22)" },
  statusText: { fontFamily: fonts.semibold, fontSize: 9.5, textTransform: "capitalize" },
  performanceHeader: { marginTop: 15, marginBottom: 6, flexDirection: "row", justifyContent: "space-between" },
  performanceLabel: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 0.8, color: colors.mutedFg },
  performanceValue: { fontFamily: fonts.mono, fontSize: 11, color: colors.foreground },
  track: { height: 7, borderRadius: 999, overflow: "hidden", backgroundColor: colors.surface },
  fill: { height: "100%", borderRadius: 999 },
  siteFooter: { marginTop: 13, paddingTop: 11, borderTopWidth: 1, borderTopColor: colors.border, flexDirection: "row", alignItems: "center", gap: 8 },
  piRow: { flex: 1, flexDirection: "row", alignItems: "center", gap: 5 },
  piText: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  trialCount: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  center: { flex: 1, padding: 30, alignItems: "center", justifyContent: "center", gap: 12 },
  errorText: { textAlign: "center", fontFamily: fonts.regular, fontSize: 13, color: colors.destructive },
  retry: { paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.primary },
  retryText: { color: colors.white, fontFamily: fonts.semibold, fontSize: 12 },
  empty: { alignItems: "center", paddingVertical: 56 },
  emptyCompact: { alignItems: "center", padding: 20, backgroundColor: colors.card, borderRadius: 18, borderWidth: 1, borderColor: colors.border },
  emptyIcon: { width: 52, height: 52, borderRadius: 26, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center", marginBottom: 10 },
  emptyTitle: { fontFamily: fonts.heading, fontSize: 16, color: colors.foreground, marginBottom: 3 },
  muted: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
  detailContent: { padding: 16, gap: 14, paddingBottom: 36 },
  siteHero: { padding: 18, borderRadius: 24, ...shadows.md },
  siteHeroTitle: { fontFamily: fonts.heading, fontSize: 19, color: colors.white },
  siteHeroMeta: { marginTop: 6, maxWidth: 230, fontFamily: fonts.regular, fontSize: 11, color: "rgba(255,255,255,0.82)" },
  contactGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  contactCard: { flexGrow: 1, width: "47%", padding: 13, minHeight: 118, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  contactLabel: { fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  contactName: { marginTop: 5, fontFamily: fonts.semibold, fontSize: 12.5, color: colors.foreground },
  contactActions: { flexDirection: "row", gap: 7, marginTop: "auto" },
  contactIcon: { width: 28, height: 28, borderRadius: 9, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(230,155,92,0.12)" },
  contactIconDisabled: { opacity: 0.38 },
  emailIcon: { backgroundColor: "rgba(123,107,184,0.10)" },
  pressed: { opacity: 0.75, transform: [{ scale: 0.985 }] },
  card: { padding: 15, borderRadius: 20, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, gap: 11, ...shadows.sm },
  cardTitle: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground },
  cardSubtitle: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  metricStrong: { fontFamily: fonts.mono, fontSize: 12, color: colors.primary },
  privacyNote: { flexDirection: "row", alignItems: "flex-start", gap: 8, padding: 10, borderRadius: 13, backgroundColor: colors.surface },
  privacyText: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, lineHeight: 15, color: colors.mutedFg },
  metricGrid: { flexDirection: "row", gap: 7 },
  metricBox: { flex: 1, minHeight: 64, borderRadius: 13, padding: 8, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  metricValue: { fontFamily: fonts.heading, fontSize: 17, color: colors.primaryDeep },
  metricLabel: { marginTop: 2, textAlign: "center", fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  targetBadge: { minWidth: 52, paddingHorizontal: 9, paddingVertical: 7, alignItems: "center", borderRadius: 12, backgroundColor: colors.secondary },
  targetBadgeLabel: { fontFamily: fonts.bold, fontSize: 7, letterSpacing: 0.8, color: colors.primary },
  targetBadgeValue: { marginTop: 1, fontFamily: fonts.heading, fontSize: 15, color: colors.primaryDeep },
  funnelGrid: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  funnelMetric: { width: "23%", minHeight: 54, paddingHorizontal: 5, paddingVertical: 8, alignItems: "center", justifyContent: "center", borderRadius: 12, backgroundColor: colors.surface },
  funnelValue: { fontFamily: fonts.heading, fontSize: 15, color: colors.primaryDeep },
  funnelLabel: { marginTop: 2, textAlign: "center", fontFamily: fonts.regular, fontSize: 7.5, color: colors.mutedFg },
  unavailablePanel: { padding: 12, borderRadius: 14, backgroundColor: colors.surface },
  unavailableTitle: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  unavailableCopy: { marginTop: 3, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.mutedFg },
  warningRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  warningText: { fontFamily: fonts.medium, fontSize: 10.5, color: colors.destructive },
  sectionTitle: { marginBottom: 8, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1, color: colors.mutedFg },
  trialPanel: { marginBottom: 9, padding: 13, borderRadius: 17, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  protocol: { fontFamily: fonts.mono, fontSize: 10.5, color: colors.info },
  trialTitle: { marginTop: 8, fontFamily: fonts.semibold, fontSize: 12.5, color: colors.foreground },
  trialMeta: { marginTop: 4, fontFamily: fonts.regular, fontSize: 10.5, color: colors.mutedFg },
  accessSummary: { flexDirection: "row", alignItems: "flex-start", gap: 10, padding: 13, borderRadius: 17, borderWidth: 1, borderColor: "rgba(123,107,184,0.22)", backgroundColor: "rgba(123,107,184,0.08)" },
  accessSummaryLabel: { fontFamily: fonts.bold, fontSize: 7.5, letterSpacing: 0.9, color: colors.info },
  accessSummaryTitle: { marginTop: 2, fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  accessSummaryCopy: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9, lineHeight: 13, color: colors.mutedFg },
  overlay: { ...StyleSheet.absoluteFillObject, zIndex: 20, justifyContent: "flex-end", backgroundColor: "rgba(46,27,51,0.38)" },
  sheet: { maxHeight: "92%", padding: 20, paddingBottom: 22, borderTopLeftRadius: 28, borderTopRightRadius: 28, backgroundColor: colors.background, ...shadows.md },
  sheetHandle: { alignSelf: "center", width: 42, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: 18 },
  sheetEyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.primary },
  sheetTitle: { marginTop: 3, fontFamily: fonts.heading, fontSize: 20, color: colors.foreground },
  close: { width: 35, height: 35, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  segmented: { marginTop: 17, padding: 3, borderRadius: 15, flexDirection: "row", backgroundColor: colors.surface },
  segment: { flex: 1, minHeight: 38, borderRadius: 12, alignItems: "center", justifyContent: "center" },
  segmentActive: { backgroundColor: colors.primary },
  segmentText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg },
  segmentTextActive: { color: colors.white },
  formScroll: { marginTop: 5 },
  formContent: { paddingBottom: 10 },
  panelLabel: { marginTop: 18, marginBottom: 2, fontFamily: fonts.bold, fontSize: 9, letterSpacing: 1.1, color: colors.primary },
  fieldLabel: { marginTop: 12, marginBottom: 6, fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 0.8, color: colors.mutedFg },
  input: { paddingHorizontal: 13, paddingVertical: 11, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, color: colors.foreground, fontFamily: fonts.regular, fontSize: 13, textAlignVertical: "top", outlineStyle: "none" } as any,
  selectInput: { minHeight: 50, flexDirection: "row", alignItems: "center", gap: 8 },
  selectValue: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  selectPlaceholder: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
  selectMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  selectChevron: { fontFamily: fonts.semibold, fontSize: 9, color: colors.primary },
  trialOptions: { marginTop: 6, padding: 6, gap: 3, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  trialOption: { paddingHorizontal: 10, paddingVertical: 9, borderRadius: 11 },
  trialOptionSelected: { backgroundColor: colors.secondary },
  trialOptionProtocol: { fontFamily: fonts.mono, fontSize: 10, color: colors.primary },
  trialOptionTitle: { marginTop: 2, fontFamily: fonts.regular, fontSize: 10.5, color: colors.foreground },
  fieldRow: { flexDirection: "row", gap: 9 },
  piLookupMessage: { marginTop: -3, marginBottom: 2, padding: 10, flexDirection: "row", alignItems: "center", gap: 7, borderRadius: 12, backgroundColor: colors.info + "12" },
  piLookupFound: { backgroundColor: colors.success + "16" },
  piLookupNotFound: { backgroundColor: colors.warning + "18" },
  piLookupText: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 13, color: colors.mutedFg },
  fieldHalf: { flex: 1 },
  choiceRow: { flexDirection: "row", gap: 8 },
  choice: { flex: 1, minHeight: 40, borderRadius: 13, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  choiceActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  choiceText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg },
  choiceTextActive: { color: colors.white },
  accessOptions: { gap: 7 },
  accessOption: { minHeight: 61, paddingHorizontal: 12, paddingVertical: 10, flexDirection: "row", alignItems: "flex-start", gap: 10, borderRadius: 15, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  accessOptionActive: { borderColor: colors.primary, backgroundColor: colors.secondary },
  radio: { width: 17, height: 17, marginTop: 1, borderRadius: 9, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  radioActive: { borderColor: colors.primary },
  radioDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primary },
  accessOptionTitle: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  accessOptionTitleActive: { color: colors.primaryDeep },
  accessOptionCopy: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, lineHeight: 12, color: colors.mutedFg },
  accessHelp: { marginTop: 6, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.mutedFg },
  uploadContent: { paddingVertical: 4 },
  uploadBox: { minHeight: 150, padding: 20, borderRadius: 19, borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.border, alignItems: "center", justifyContent: "center", backgroundColor: colors.card },
  uploadBoxSelected: { borderColor: colors.success, backgroundColor: "rgba(92,154,110,0.07)" },
  uploadTitle: { marginTop: 10, textAlign: "center", fontFamily: fonts.semibold, fontSize: 12.5, color: colors.foreground },
  uploadHint: { marginTop: 4, fontFamily: fonts.regular, fontSize: 10, color: colors.mutedFg },
  uploadNote: { marginTop: 10, fontFamily: fonts.regular, fontSize: 10.5, lineHeight: 15, color: colors.mutedFg },
  uploadSuccess: { marginTop: 12, padding: 12, flexDirection: "row", alignItems: "flex-start", gap: 9, borderRadius: 15, borderWidth: 1, borderColor: "rgba(92,154,110,0.25)", backgroundColor: "rgba(92,154,110,0.08)" },
  uploadSuccessTitle: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.success },
  uploadSuccessCopy: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9, lineHeight: 13, color: colors.mutedFg },
  importResults: { maxHeight: 126, marginTop: 9, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  importResultRow: { minHeight: 43, paddingHorizontal: 10, paddingVertical: 8, flexDirection: "row", alignItems: "flex-start", gap: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  resultMark: { width: 19, height: 19, borderRadius: 7, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(92,154,110,0.12)" },
  resultMarkError: { backgroundColor: "rgba(192,57,43,0.10)" },
  importResultTitle: { fontFamily: fonts.semibold, fontSize: 9.5, color: colors.foreground },
  importResultError: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, lineHeight: 12, color: colors.destructive },
  formError: { marginTop: 10, fontFamily: fonts.regular, fontSize: 11, color: colors.destructive },
  primaryButton: { marginTop: 12, height: 48, borderRadius: 999, flexDirection: "row", gap: 7, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary },
  primaryButtonDisabled: { opacity: 0.48 },
  primaryButtonText: { fontFamily: fonts.bold, fontSize: 13, color: colors.white },
});
