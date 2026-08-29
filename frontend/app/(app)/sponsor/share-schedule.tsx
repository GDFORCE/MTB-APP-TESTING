import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import * as DocumentPicker from "expo-document-picker";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  FileText,
  MapPin,
  Search,
  Share2,
  ShieldCheck,
  Upload,
  UserRound,
  X,
} from "lucide-react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { api } from "@/src/api/client";
import { getSponsorDashboard, normalizeSite } from "@/src/features/sponsor/api";
import type { SponsorSite, SponsorSitePi, SponsorTrial } from "@/src/features/sponsor/types";
import { uploadFile } from "@/src/lib/upload";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type TrialDocument = {
  id: string;
  name: string;
  version?: string;
  source: "existing" | "schedule";
  uri?: string;
};

type ShareResult = {
  recipients: { siteName: string; piName: string }[];
};

type ShareSite = SponsorSite & {
  directoryOrgId?: string;
  inNetwork?: boolean;
  assignedToTrial?: boolean;
  canReceiveSchedule?: boolean;
};

type SharePI = SponsorSitePi & { id: string };

const sitePIs = (site: SponsorSite): SharePI[] => {
  const rows: SponsorSitePi[] = [
    ...(site.pis || []),
    ...(site.piId ? [{
      id: site.piId,
      name: site.pi || site.piEmail || "Unnamed PI",
      email: site.piEmail,
      phone: site.piPhone,
      department: site.department,
    }] : []),
  ];
  const byId = new Map<string, SharePI>();
  rows.forEach((pi) => {
    const id = String(pi.id || "").trim();
    if (id && !byId.has(id)) byId.set(id, { ...pi, id });
  });
  return Array.from(byId.values());
};

const mergedSitePIs = (...siteRows: (SponsorSite | undefined)[]) => {
  const byId = new Map<string, SharePI>();
  siteRows.forEach((site) => site && sitePIs(site).forEach((pi) => {
    if (!byId.has(pi.id)) byId.set(pi.id, pi);
  }));
  return Array.from(byId.values());
};

const fileName = (value: any) =>
  String(value?.original_name || value?.file_name || value?.filename || value?.name || "Trial document");

export default function ShareSchedule() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id?: string }>();
  const [step, setStep] = useState<1 | 2>(1);
  const [trials, setTrials] = useState<SponsorTrial[]>([]);
  const [sites, setSites] = useState<ShareSite[]>([]);
  const [directorySites, setDirectorySites] = useState<ShareSite[]>([]);
  const [loadingDirectory, setLoadingDirectory] = useState(false);
  const [directoryError, setDirectoryError] = useState("");
  const [documents, setDocuments] = useState<TrialDocument[]>([]);
  const [trialId, setTrialId] = useState(id || "");
  const [selectedDocument, setSelectedDocument] = useState<TrialDocument | null>(null);
  const [selectedSites, setSelectedSites] = useState<Set<string>>(new Set());
  const [selectedReviewerBySite, setSelectedReviewerBySite] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [loadingData, setLoadingData] = useState(true);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState<ShareResult | null>(null);

  useEffect(() => {
    getSponsorDashboard()
      .then((dashboard) => {
        setTrials(dashboard.trials);
        setSites(dashboard.sites.map((site) => ({ ...site, inNetwork: true })));
        if (!id && dashboard.trials[0]) setTrialId(dashboard.trials[0].id);
      })
      .catch((e: any) => setErr(e?.response?.data?.detail || "Couldn't load trials and sites."))
      .finally(() => setLoadingData(false));
  }, [id]);

  const selectedTrial = trials.find((trial) => trial.id === trialId);

  useEffect(() => {
    if (!trialId) {
      setDirectorySites([]);
      return;
    }
    let active = true;
    setLoadingDirectory(true);
    setDirectoryError("");
    api
      .get("/sponsor/share-site-directory", { params: { trial_id: trialId } })
      .then((response) => {
        if (!active) return;
        const rows = Array.isArray(response.data) ? response.data : [];
        setDirectorySites(rows.map((row: any) => ({
          ...normalizeSite(row),
          directoryOrgId: String(row.organization_id || ""),
          inNetwork: Boolean(row.in_network),
          assignedToTrial: Boolean(row.assigned_to_trial),
          canReceiveSchedule: Boolean(row.can_receive_schedule),
        })));
      })
      .catch((error: any) => {
        if (!active) return;
        setDirectorySites([]);
        setDirectoryError(error?.response?.data?.detail || "Couldn't load the application site directory.");
      })
      .finally(() => {
        if (active) setLoadingDirectory(false);
      });
    return () => {
      active = false;
    };
  }, [trialId]);

  useEffect(() => {
    if (!trialId) {
      setDocuments([]);
      setSelectedDocument(null);
      return;
    }
    let active = true;
    setLoadingDocuments(true);
    setSelectedDocument(null);
    api
      .get("/files", { params: { scope_type: "trial", scope_id: trialId } })
      .then((response) => {
        if (!active) return;
        const rows = Array.isArray(response.data) ? response.data : response.data?.files || [];
        const existing = rows.map((row: any) => ({
          id: String(row.id || row._id),
          name: fileName(row),
          version: row.version || row.version_label,
          source: "existing" as const,
        }));
        setDocuments(existing);
      })
      .catch(() => {
        if (active) setDocuments([]);
      })
      .finally(() => {
        if (active) setLoadingDocuments(false);
      });
    return () => {
      active = false;
    };
  }, [trialId]);

  const availableSites = useMemo(() => {
    const byName = new Map<string, ShareSite>();
    sites.forEach((site) => byName.set(site.name.trim().toLocaleLowerCase(), site));
    directorySites.forEach((directorySite) => {
      const key = directorySite.name.trim().toLocaleLowerCase();
      const networkSite = byName.get(key);
      const pis = mergedSitePIs(directorySite, networkSite);
      byName.set(key, networkSite ? {
          ...directorySite,
          ...networkSite,
          pis,
          directoryOrgId: directorySite.directoryOrgId,
          inNetwork: true,
          assignedToTrial: directorySite.assignedToTrial,
          canReceiveSchedule: pis.length > 0,
          pi: networkSite.pi || directorySite.pi,
          piId: networkSite.piId || directorySite.piId,
          piEmail: networkSite.piEmail || directorySite.piEmail,
        } : { ...directorySite, pis });
    });
    return Array.from(byName.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [directorySites, sites]);

  const visibleSites = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return availableSites.filter((site) => {
      const searchable = [
        site.name,
        site.hospital,
        site.city,
        site.state,
        ...sitePIs(site).flatMap((pi) => [pi.name, pi.email, pi.department]),
      ].filter(Boolean).join(" ").toLowerCase();
      return !needle || searchable.includes(needle);
    });
  }, [availableSites, query]);

  const selectedSiteRows = useMemo(
    () => availableSites.filter((site) => selectedSites.has(site.id)),
    [availableSites, selectedSites],
  );
  const pendingSites = useMemo(
    () => selectedSiteRows.filter((site) => !selectedReviewerBySite[site.id]),
    [selectedReviewerBySite, selectedSiteRows],
  );
  const selectableVisibleIds = visibleSites.filter((site) => sitePIs(site).length > 0).map((site) => site.id);
  const allVisibleSelected =
    selectableVisibleIds.length > 0 && selectableVisibleIds.every((siteId) => selectedSites.has(siteId));

  const isDirty =
    selectedSites.size > 0 || !!message.trim() || !!selectedDocument || step === 2;

  const requestClose = () => {
    if (!isDirty) {
      router.back();
      return;
    }
    Alert.alert(
      "Discard this share?",
      "Your selected sites, document and message will be cleared.",
      [
        { text: "Keep editing", style: "cancel" },
        { text: "Discard", style: "destructive", onPress: () => router.back() },
      ],
    );
  };

  const toggleSite = (site: ShareSite) => {
    const wasSelected = selectedSites.has(site.id);
    setSelectedSites((previous) => {
      const next = new Set(previous);
      if (next.has(site.id)) next.delete(site.id);
      else next.add(site.id);
      return next;
    });
    setSelectedReviewerBySite((previous) => {
      const next = { ...previous };
      if (wasSelected) {
        delete next[site.id];
      } else {
        const pis = sitePIs(site);
        if (pis.length === 1) next[site.id] = pis[0].id;
      }
      return next;
    });
  };

  const selectReviewer = (siteId: string, reviewerId: string) =>
    setSelectedReviewerBySite((previous) => ({
      ...previous,
      [siteId]: reviewerId,
    }));

  const toggleAllVisible = () => {
    const selectingAll = !allVisibleSelected;
    setSelectedSites((previous) => {
      const next = new Set(previous);
      if (allVisibleSelected) selectableVisibleIds.forEach((siteId) => next.delete(siteId));
      else selectableVisibleIds.forEach((siteId) => next.add(siteId));
      return next;
    });
    setSelectedReviewerBySite((previous) => {
      const next = { ...previous };
      visibleSites.forEach((site) => {
        if (!selectableVisibleIds.includes(site.id)) return;
        if (!selectingAll) {
          delete next[site.id];
          return;
        }
        const pis = sitePIs(site);
        if (!next[site.id] && pis.length === 1) next[site.id] = pis[0].id;
      });
      return next;
    });
  };

  const selectTrial = (nextTrialId: string) => {
    if (nextTrialId === trialId) return;
    setTrialId(nextTrialId);
    setSelectedSites(new Set());
    setSelectedReviewerBySite({});
    setQuery("");
    setErr("");
  };

  const pickDocument = async () => {
    if (!trialId || uploadingDocument) return;
    setErr("");
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled || !result.assets[0]) return;
      const asset = result.assets[0];
      setUploadingDocument(true);
      const uploaded = await uploadFile(
        {
          uri: asset.uri,
          name: asset.name || "trial-document.pdf",
          mimeType: asset.mimeType,
          file: (asset as any).file,
        },
        { scopeType: "trial", scopeId: trialId },
      );
      const document: TrialDocument = {
        id: uploaded.id,
        name: uploaded.name,
        source: "existing",
        uri: uploaded.url,
      };
      setDocuments((current) => [document, ...current.filter((item) => item.id !== document.id)]);
      setSelectedDocument(document);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Couldn't upload that document. Use a PDF or DOCX up to 10 MB.");
    } finally {
      setUploadingDocument(false);
    }
  };

  const continueToReview = () => {
    if (!trialId) {
      setErr("Select a trial first.");
      return;
    }
    if (!selectedDocument) {
      setErr("Select the visit schedule or a trial document.");
      return;
    }
    if (selectedSites.size === 0) {
      setErr("Select at least one site for review.");
      return;
    }
    if (pendingSites.length > 0) {
      setErr("Choose one PI reviewer for each selected site before continuing.");
      return;
    }
    setErr("");
    setStep(2);
  };

  const share = async () => {
    if (!trialId || !selectedDocument || selectedSites.size === 0 || pendingSites.length > 0) {
      setStep(1);
      setErr("Review the trial, document, sites and selected PI reviewers.");
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const chosenSites = selectedSiteRows.map((site) => ({
        id: site.id,
        name: site.name,
        reviewer_id: selectedReviewerBySite[site.id] || null,
        organization_id: site.directoryOrgId || null,
      }));
      await api.post("/shares", {
        trial_id: trialId,
        via: "in_app",
        recipients: [],
        sites: chosenSites,
        message: message.trim(),
        document_name: selectedDocument.name,
        document_id: selectedDocument.source === "existing" ? selectedDocument.id : null,
        version_note:
          selectedDocument.version ||
          (selectedDocument.source === "schedule" ? "Current approved visit schedule" : "Document shared for PI review"),
      });
      setDone({
        recipients: selectedSiteRows.map((site) => ({
          siteName: site.name,
          piName: sitePIs(site).find(
            (pi) => pi.id === selectedReviewerBySite[site.id],
          )?.name || "Selected PI",
        })),
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Couldn't share this schedule.");
    } finally {
      setLoading(false);
    }
  };

  const shareAnother = () => {
    setDone(null);
    setStep(1);
    setSelectedSites(new Set());
    setSelectedReviewerBySite({});
    setSelectedDocument(null);
    setMessage("");
    setQuery("");
    setErr("");
  };

  if (done) {
    return (
      <View style={s.page}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <SafeAreaView edges={["top"]} style={s.header}>
          <Pressable onPress={() => router.replace("/(app)/sponsor/dashboard" as never)} hitSlop={10}>
            <ArrowLeft size={20} color={colors.white} />
          </Pressable>
          <Text style={s.headerTitle}>Share Schedule</Text>
          <View style={{ width: 20 }} />
        </SafeAreaView>
        <ScrollView contentContainerStyle={s.successPage}>
          <View style={s.successOrb}>
            <CheckCircle2 size={36} color={colors.success} />
          </View>
          <Text style={s.successTitle}>Sent for PI review</Text>
          <Text style={s.successText}>
            {selectedDocument?.name} was sent directly to the assigned PIs in the app.
          </Text>
          <View style={s.successList}>
            <Text style={s.successListTitle}>SHARED WITH</Text>
            {done.recipients.map((recipient) => (
              <View key={`${recipient.siteName}:${recipient.piName}`} style={s.successSite}>
                <View style={s.successCheck}><Check size={12} color={colors.white} /></View>
                <View style={s.flex}>
                  <Text style={s.successSiteName}>{recipient.siteName}</Text>
                  <Text style={s.siteMeta}>PI · {recipient.piName}</Text>
                </View>
                <Text style={s.pendingText}>Pending PI review</Text>
              </View>
            ))}
          </View>
          <Pressable onPress={shareAnother} style={s.primaryButton}>
            <Share2 size={16} color={colors.white} />
            <Text style={s.primaryButtonText}>Share Another Document</Text>
          </Pressable>
          <Pressable onPress={() => router.replace("/(app)/sponsor/dashboard" as never)} style={s.textButton}>
            <Text style={s.textButtonLabel}>Back to Dashboard</Text>
          </Pressable>
        </ScrollView>
      </View>
    );
  }

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <Pressable onPress={step === 2 ? () => setStep(1) : requestClose} hitSlop={10}>
          <ArrowLeft size={20} color={colors.white} />
        </Pressable>
        <Text style={s.headerTitle}>Share Schedule</Text>
        <Pressable onPress={requestClose} hitSlop={10}>
          <X size={19} color={colors.white} />
        </Pressable>
      </SafeAreaView>

      <View style={s.progressWrap}>
        <View style={s.progressTextRow}>
          <Text style={s.progressTitle}>{step === 1 ? "Choose document, sites & PIs" : "Review & share"}</Text>
          <Text style={s.progressCount}>STEP {step} OF 2</Text>
        </View>
        <View style={s.progressTrack}>
          <View style={[s.progressFill, { width: step === 1 ? "50%" : "100%" }]} />
        </View>
      </View>

      {loadingData ? (
        <View style={s.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <>
          <ScrollView contentContainerStyle={s.content} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
            {step === 1 ? (
              <>
                <View>
                  <Text style={s.eyebrow}>TRIAL</Text>
                  <Text style={s.sectionTitle}>Select a trial</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.horizontal}>
                    {trials.map((trial) => {
                      const active = trialId === trial.id;
                      return (
                        <Pressable key={trial.id} onPress={() => selectTrial(trial.id)} style={[s.trialChoice, active && s.trialChoiceActive]}>
                          <Text style={[s.protocol, active && s.lightText]}>{trial.protocolId}</Text>
                          <Text numberOfLines={2} style={[s.trialChoiceTitle, active && s.lightText]}>{trial.title}</Text>
                          <Text style={[s.trialChoiceMeta, active && s.lightMeta]}>{[trial.phase, trial.condition].filter(Boolean).join(" · ")}</Text>
                        </Pressable>
                      );
                    })}
                  </ScrollView>
                </View>

                <View>
                  <Text style={s.eyebrow}>DOCUMENT</Text>
                  <Text style={s.sectionTitle}>Choose what to share</Text>
                  <View style={s.documentList}>
                    <Pressable
                      onPress={() => setSelectedDocument({
                        id: `schedule:${trialId}`,
                        name: `${selectedTrial?.protocolId || "Trial"} Visit Schedule.pdf`,
                        source: "schedule",
                        version: "Current approved visit schedule",
                      })}
                      style={[s.documentRow, selectedDocument?.source === "schedule" && s.documentRowActive]}
                    >
                      <View style={s.documentIcon}><FileText size={18} color={colors.primary} /></View>
                      <View style={s.flex}>
                        <Text style={s.documentName}>Current visit schedule</Text>
                        <Text style={s.documentMeta}>{selectedTrial?.protocolId || "Selected trial"} · Latest saved version</Text>
                      </View>
                      <SelectionMark active={selectedDocument?.source === "schedule"} />
                    </Pressable>
                    {loadingDocuments ? (
                      <ActivityIndicator style={{ marginVertical: 8 }} color={colors.primary} />
                    ) : documents.map((document) => (
                      <Pressable
                        key={document.id}
                        onPress={() => setSelectedDocument(document)}
                        style={[s.documentRow, selectedDocument?.id === document.id && s.documentRowActive]}
                      >
                        <View style={s.documentIcon}><FileText size={18} color={colors.accent} /></View>
                        <View style={s.flex}>
                          <Text numberOfLines={1} style={s.documentName}>{document.name}</Text>
                          <Text style={s.documentMeta}>{document.version || "Existing trial document"}</Text>
                        </View>
                        <SelectionMark active={selectedDocument?.id === document.id} />
                      </Pressable>
                    ))}
                    <Pressable onPress={pickDocument} style={s.uploadButton}>
                      {uploadingDocument
                        ? <ActivityIndicator size="small" color={colors.primary} />
                        : <Upload size={16} color={colors.primary} />}
                      <Text style={s.uploadText}>{uploadingDocument ? "Uploading document..." : "Upload a document from this device"}</Text>
                    </Pressable>
                  </View>
                </View>

                <View>
                  <View style={s.sectionHead}>
                    <View>
                      <Text style={s.eyebrow}>RECIPIENT SITES</Text>
                      <Text style={s.sectionTitle}>Select a site, then choose its PI</Text>
                    </View>
                    <Pressable onPress={toggleAllVisible} disabled={!selectableVisibleIds.length}>
                      <Text style={s.selectAll}>{allVisibleSelected ? "Deselect all" : "Select all"}</Text>
                    </Pressable>
                  </View>
                  <View style={s.searchBox}>
                    <Search size={16} color={colors.mutedFg} />
                    <TextInput value={query} onChangeText={setQuery} placeholder="Search sites..." placeholderTextColor={colors.mutedFg} style={s.searchInput} />
                  </View>
                  <View style={s.siteList}>
                    {loadingDirectory && (
                      <View style={s.directoryState}>
                        <ActivityIndicator size="small" color={colors.primary} />
                        <Text style={s.directoryStateText}>Loading all registered sites…</Text>
                      </View>
                    )}
                    {!!directoryError && (
                      <View style={s.warning}>
                        <AlertTriangle size={17} color={colors.warning} />
                        <Text style={s.warningText}>{directoryError} Existing network sites are still available.</Text>
                      </View>
                    )}
                    {visibleSites.map((site) => {
                      const active = selectedSites.has(site.id);
                      const pis = sitePIs(site);
                      const selectedPI = pis.find((pi) => pi.id === selectedReviewerBySite[site.id]);
                      const unavailable = pis.length === 0;
                      return (
                        <View key={site.id} style={[s.siteGroup, active && s.siteGroupActive]}>
                          <Pressable
                            testID={`select-site-${site.id}`}
                            disabled={unavailable}
                            onPress={() => toggleSite(site)}
                            style={[s.siteRow, active && s.siteRowActive, unavailable && s.siteRowDisabled]}
                          >
                            <View style={[s.checkbox, active && s.checkboxActive, unavailable && s.checkboxDisabled]}>{active && <Check size={13} color={colors.white} />}</View>
                            <View style={s.siteIcon}><MapPin size={16} color={colors.accent} /></View>
                            <View style={s.flex}>
                              <View style={s.siteNameRow}>
                                <Text style={s.siteName}>{site.name}</Text>
                                {site.inNetwork === false && <View style={s.newSiteBadge}><Text style={s.newSiteBadgeText}>NEW SITE</Text></View>}
                              </View>
                              <Text style={s.siteMeta}>
                                {unavailable
                                  ? "No registered PI available"
                                  : selectedPI
                                    ? `Selected PI · ${selectedPI.name}`
                                    : `${pis.length} PI${pis.length === 1 ? "" : "s"} available`}
                              </Text>
                              {site.inNetwork === false && !unavailable && (
                                <Text style={s.networkHint}>Will be added to your network when shared</Text>
                              )}
                            </View>
                          </Pressable>
                          {active && (
                            <View style={s.piPicker}>
                              <Text style={s.piPickerTitle}>CHOOSE PI REVIEWER</Text>
                              {pis.map((pi) => (
                                <Pressable
                                  key={pi.id}
                                  testID={`select-pi-${site.id}-${pi.id}`}
                                  onPress={() => selectReviewer(site.id, pi.id)}
                                  style={[
                                    s.piRow,
                                    selectedReviewerBySite[site.id] === pi.id && s.piRowActive,
                                  ]}
                                >
                                  <View style={s.piIcon}><UserRound size={15} color={colors.primary} /></View>
                                  <View style={s.flex}>
                                    <Text style={s.piName}>{pi.name}</Text>
                                    <Text numberOfLines={1} style={s.piMeta}>
                                      {[pi.department, pi.email].filter(Boolean).join(" · ") || "Principal Investigator"}
                                    </Text>
                                  </View>
                                  <SelectionMark active={selectedReviewerBySite[site.id] === pi.id} />
                                </Pressable>
                              ))}
                            </View>
                          )}
                        </View>
                      );
                    })}
                    {!visibleSites.length && <View style={s.empty}><Text style={s.emptyText}>No available sites match your search.</Text></View>}
                  </View>
                  {selectedSiteRows.length > 0 && (
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}>
                      {selectedSiteRows.map((site) => (
                        <Pressable key={site.id} onPress={() => toggleSite(site)} style={s.chip}>
                          <Text numberOfLines={1} style={s.chipText}>
                            {site.name}{selectedReviewerBySite[site.id] ? " · PI selected" : " · choose PI"}
                          </Text>
                          <X size={12} color={colors.primary} />
                        </Pressable>
                      ))}
                    </ScrollView>
                  )}
                  {pendingSites.length > 0 && (
                    <View style={s.warning}>
                      <AlertTriangle size={17} color={colors.warning} />
                      <Text style={s.warningText}>
                        Choose a PI reviewer for {pendingSites.length} selected site{pendingSites.length === 1 ? "" : "s"} before continuing.
                      </Text>
                    </View>
                  )}
                </View>

                <View>
                  <Text style={s.fieldLabel}>MESSAGE TO SITES · OPTIONAL</Text>
                  <TextInput
                    value={message}
                    onChangeText={(value) => setMessage(value.slice(0, 300))}
                    placeholder="Please review the schedule and add any PI notes."
                    placeholderTextColor={colors.mutedFg}
                    multiline
                    textAlignVertical="top"
                    style={s.messageInput}
                  />
                  <Text style={s.characterCount}>{message.length} / 300</Text>
                </View>
              </>
            ) : (
              <>
                <View style={s.reviewHero}>
                  <View style={s.reviewIcon}><ShieldCheck size={25} color={colors.primary} /></View>
                  <View style={s.flex}>
                    <Text style={s.reviewTitle}>Ready for site review</Text>
                    <Text style={s.reviewCopy}>Each selected PI receives a review task for their site and can approve or reject this version independently.</Text>
                  </View>
                </View>
                <ReviewRow label="Trial" value={`${selectedTrial?.protocolId || "Trial"} · ${selectedTrial?.title || ""}`} />
                <ReviewRow label="Document" value={selectedDocument?.name || "Visit schedule"} />
                <View style={s.reviewCard}>
                  <Text style={s.reviewLabel}>SELECTED SITES · {selectedSiteRows.length}</Text>
                  {selectedSiteRows.map((site) => (
                    <View key={site.id} style={s.reviewSite}>
                      <View style={s.siteIcon}><MapPin size={15} color={colors.accent} /></View>
                      <View style={s.flex}>
                        <Text style={s.siteName}>{site.name}</Text>
                        <Text style={s.siteMeta}>
                          PI · {sitePIs(site).find(
                            (pi) => pi.id === selectedReviewerBySite[site.id],
                          )?.name || "Selection required"}
                        </Text>
                        {site.inNetwork === false && <Text style={s.networkHint}>Will join your trial network</Text>}
                      </View>
                      <Text style={s.pendingText}>Pending</Text>
                    </View>
                  ))}
                </View>
                {!!message.trim() && <ReviewRow label="Message" value={message.trim()} />}
                <View style={s.reviewHero}>
                  <View style={s.reviewIcon}><Share2 size={24} color={colors.primary} /></View>
                  <View style={s.flex}>
                    <Text style={s.reviewTitle}>In-app delivery</Text>
                    <Text style={s.reviewCopy}>The assigned PIs will receive a notification and the schedule will appear in their review inbox.</Text>
                  </View>
                </View>
              </>
            )}
            {!!err && <Text style={s.error}>{err}</Text>}
          </ScrollView>
          <SafeAreaView edges={["bottom"]} style={s.footer}>
            {step === 1 ? (
              <>
                <View style={s.flex}>
                  <Text style={s.footerLabel}>{selectedSites.size} SITE{selectedSites.size === 1 ? "" : "S"} SELECTED</Text>
                  <Text numberOfLines={1} style={s.footerMeta}>{selectedDocument?.name || "Choose a document"}</Text>
                </View>
                <Pressable onPress={continueToReview} style={s.shareButton}>
                  <Text style={s.shareButtonText}>Review</Text>
                  <ChevronRight size={17} color={colors.white} />
                </Pressable>
              </>
            ) : (
              <>
                <Pressable onPress={() => setStep(1)} style={s.footerBack}>
                  <Text style={s.footerBackText}>Back</Text>
                </Pressable>
                <Pressable onPress={share} disabled={loading} style={[s.shareButton, s.flex, loading && s.disabled]}>
                  {loading ? <ActivityIndicator color={colors.white} /> : <><Share2 size={17} color={colors.white} /><Text style={s.shareButtonText}>Send in App</Text></>}
                </Pressable>
              </>
            )}
          </SafeAreaView>
        </>
      )}
    </View>
  );
}

function SelectionMark({ active }: { active: boolean }) {
  return (
    <View style={[s.radio, active && s.radioActive]}>
      {active && <View style={s.radioDot} />}
    </View>
  );
}

function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.reviewCard}>
      <Text style={s.reviewLabel}>{label.toUpperCase()}</Text>
      <Text style={s.reviewValue}>{value}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  flex: { flex: 1 },
  header: { minHeight: 70, paddingHorizontal: 17, paddingTop: 8, paddingBottom: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between", backgroundColor: colors.primaryDeep },
  headerTitle: { fontFamily: fonts.semibold, fontSize: 15, color: colors.white },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  progressWrap: { paddingHorizontal: 16, paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: colors.border, backgroundColor: colors.card },
  progressTextRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  progressTitle: { fontFamily: fonts.semibold, fontSize: 11, color: colors.foreground },
  progressCount: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.8, color: colors.primary },
  progressTrack: { height: 4, marginTop: 8, overflow: "hidden", borderRadius: 2, backgroundColor: colors.secondary },
  progressFill: { height: 4, borderRadius: 2, backgroundColor: colors.primary },
  content: { padding: 15, paddingBottom: 26, gap: 24 },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 1.1, color: colors.primary },
  sectionTitle: { marginTop: 3, fontFamily: fonts.heading, fontSize: 17, color: colors.foreground },
  horizontal: { paddingTop: 11, paddingRight: 15, gap: 9 },
  trialChoice: { width: 190, minHeight: 112, padding: 13, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, ...shadows.sm },
  trialChoiceActive: { borderColor: colors.primary, backgroundColor: colors.primaryDeep },
  protocol: { fontFamily: fonts.mono, fontSize: 10, color: colors.primary },
  lightText: { color: colors.white },
  lightMeta: { color: "rgba(255,255,255,0.72)" },
  trialChoiceTitle: { marginTop: 9, fontFamily: fonts.semibold, fontSize: 12, lineHeight: 16, color: colors.foreground },
  trialChoiceMeta: { marginTop: "auto", paddingTop: 7, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  documentList: { marginTop: 10, gap: 8 },
  documentRow: { minHeight: 62, padding: 11, flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderColor: colors.border, borderRadius: 17, backgroundColor: colors.card },
  documentRowActive: { borderColor: colors.primary, backgroundColor: "rgba(166,33,63,0.035)" },
  documentIcon: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.secondary },
  documentName: { fontFamily: fonts.semibold, fontSize: 11.5, color: colors.foreground },
  documentMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  uploadButton: { height: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderWidth: 1, borderStyle: "dashed", borderColor: colors.primary, borderRadius: 14, backgroundColor: colors.card },
  uploadText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.primary },
  localFile: { minHeight: 40, paddingHorizontal: 11, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 12, backgroundColor: colors.secondary },
  localFileName: { flex: 1, fontFamily: fonts.regular, fontSize: 10.5, color: colors.foreground },
  sectionHead: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between" },
  selectAll: { paddingVertical: 4, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.primary },
  searchBox: { height: 42, marginTop: 11, paddingHorizontal: 12, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  searchInput: { flex: 1, fontFamily: fonts.regular, fontSize: 12.5, color: colors.foreground, outlineStyle: "none" } as any,
  siteList: { gap: 9, marginTop: 10 },
  siteGroup: { borderRadius: 17 },
  siteGroupActive: { paddingBottom: 8, backgroundColor: "rgba(166,33,63,0.035)" },
  siteRow: { minHeight: 65, padding: 11, flexDirection: "row", alignItems: "center", gap: 9, borderRadius: 17, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  siteRowActive: { borderColor: colors.primary, backgroundColor: "rgba(166,33,63,0.035)" },
  siteRowDisabled: { opacity: 0.58 },
  checkbox: { width: 21, height: 21, borderRadius: 7, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  checkboxActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  checkboxDisabled: { backgroundColor: colors.border, borderColor: colors.mutedFg },
  siteIcon: { width: 35, height: 35, borderRadius: 13, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(230,155,92,0.13)" },
  siteNameRow: { flexDirection: "row", alignItems: "center", gap: 7, flexWrap: "wrap" },
  siteName: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  siteMeta: { marginTop: 3, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  piPicker: { marginHorizontal: 10, paddingTop: 9, gap: 7, borderTopWidth: 1, borderTopColor: colors.border },
  piPickerTitle: { marginBottom: 1, fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.8, color: colors.mutedFg },
  piRow: { minHeight: 50, paddingHorizontal: 10, paddingVertical: 8, flexDirection: "row", alignItems: "center", gap: 9, borderWidth: 1, borderColor: colors.border, borderRadius: 14, backgroundColor: colors.card },
  piRowActive: { borderColor: colors.primary, backgroundColor: "rgba(166,33,63,0.05)" },
  piIcon: { width: 31, height: 31, borderRadius: 11, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  piName: { fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  piMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 8.5, color: colors.mutedFg },
  networkHint: { marginTop: 2, fontFamily: fonts.semibold, fontSize: 8.5, color: colors.info },
  newSiteBadge: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(123,107,184,0.12)" },
  newSiteBadgeText: { fontFamily: fonts.bold, fontSize: 7, letterSpacing: 0.5, color: colors.info },
  directoryState: { minHeight: 46, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderRadius: 14, backgroundColor: colors.card },
  directoryStateText: { fontFamily: fonts.regular, fontSize: 10, color: colors.mutedFg },
  chips: { paddingTop: 10, gap: 7 },
  chip: { maxWidth: 180, height: 29, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 6, borderRadius: 999, backgroundColor: colors.secondary },
  chipText: { maxWidth: 145, fontFamily: fonts.semibold, fontSize: 9.5, color: colors.primary },
  warning: { marginTop: 10, padding: 11, flexDirection: "row", alignItems: "flex-start", gap: 8, borderRadius: 14, borderWidth: 1, borderColor: "rgba(217,142,45,0.25)", backgroundColor: "rgba(217,142,45,0.08)" },
  warningText: { flex: 1, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 14, color: colors.foreground },
  fieldLabel: { marginBottom: 6, fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 0.8, color: colors.mutedFg },
  emailInput: { minHeight: 44, marginBottom: 13, paddingHorizontal: 12, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12.5, color: colors.foreground, outlineStyle: "none" } as any,
  messageInput: { minHeight: 88, paddingHorizontal: 12, paddingVertical: 11, borderRadius: 14, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, fontFamily: fonts.regular, fontSize: 12.5, lineHeight: 17, color: colors.foreground, outlineStyle: "none" } as any,
  characterCount: { marginTop: 4, textAlign: "right", fontFamily: fonts.regular, fontSize: 9, color: colors.mutedFg },
  empty: { padding: 18, alignItems: "center", borderRadius: 16, borderWidth: 1, borderStyle: "dashed", borderColor: colors.border, backgroundColor: colors.card },
  emptyText: { fontFamily: fonts.regular, fontSize: 11, color: colors.mutedFg },
  reviewHero: { padding: 15, flexDirection: "row", alignItems: "flex-start", gap: 11, borderRadius: 20, backgroundColor: colors.secondary },
  reviewIcon: { width: 46, height: 46, alignItems: "center", justifyContent: "center", borderRadius: 16, backgroundColor: colors.card },
  reviewTitle: { fontFamily: fonts.heading, fontSize: 15, color: colors.foreground },
  reviewCopy: { marginTop: 4, fontFamily: fonts.regular, fontSize: 10, lineHeight: 15, color: colors.mutedFg },
  reviewCard: { padding: 14, gap: 7, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  reviewLabel: { fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  reviewValue: { fontFamily: fonts.regular, fontSize: 11.5, lineHeight: 17, color: colors.foreground },
  reviewSite: { minHeight: 50, flexDirection: "row", alignItems: "center", gap: 8, borderTopWidth: 1, borderTopColor: colors.border },
  pendingText: { fontFamily: fonts.semibold, fontSize: 8.5, color: colors.warning },
  deliveryList: { gap: 9, marginTop: 10 },
  delivery: { padding: 12, flexDirection: "row", alignItems: "center", gap: 10, borderRadius: 18, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  deliveryActive: { borderColor: colors.primary },
  deliveryIcon: { width: 39, height: 39, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
  deliveryIconActive: { backgroundColor: colors.primary },
  deliveryTitle: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  deliveryText: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, lineHeight: 13, color: colors.mutedFg },
  radio: { width: 19, height: 19, borderRadius: 10, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  radioActive: { borderColor: colors.primary },
  radioDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.primary },
  error: { fontFamily: fonts.regular, fontSize: 11, color: colors.destructive },
  footer: { padding: 13, paddingHorizontal: 16, flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  footerLabel: { fontFamily: fonts.semibold, fontSize: 8.5, letterSpacing: 0.6, color: colors.primary },
  footerMeta: { marginTop: 2, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  shareButton: { minWidth: 112, height: 44, paddingHorizontal: 17, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  shareButtonText: { fontFamily: fonts.bold, fontSize: 11.5, color: colors.white },
  footerBack: { width: 80, height: 44, alignItems: "center", justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: colors.border },
  footerBackText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.foreground },
  disabled: { opacity: 0.65 },
  successPage: { flexGrow: 1, padding: 24, alignItems: "center", justifyContent: "center" },
  successOrb: { width: 76, height: 76, borderRadius: 38, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(92,154,110,0.12)" },
  successTitle: { marginTop: 16, fontFamily: fonts.heading, fontSize: 21, color: colors.foreground },
  successText: { marginTop: 6, maxWidth: 290, textAlign: "center", fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, color: colors.mutedFg },
  successList: { alignSelf: "stretch", marginTop: 21, padding: 14, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  successListTitle: { marginBottom: 4, fontFamily: fonts.semibold, fontSize: 8, letterSpacing: 0.9, color: colors.mutedFg },
  successSite: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: 8, borderTopWidth: 1, borderTopColor: colors.border },
  successCheck: { width: 20, height: 20, alignItems: "center", justifyContent: "center", borderRadius: 10, backgroundColor: colors.success },
  successSiteName: { flex: 1, fontFamily: fonts.semibold, fontSize: 10.5, color: colors.foreground },
  linkCard: { alignSelf: "stretch", marginTop: 12, padding: 15, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  linkText: { marginTop: 7, fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 15, color: colors.primary },
  copyText: { marginTop: 8, fontFamily: fonts.regular, fontSize: 9.5, color: colors.mutedFg },
  secondaryButton: { alignSelf: "stretch", height: 46, marginTop: 12, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, borderWidth: 1, borderColor: colors.primary, backgroundColor: colors.card },
  secondaryButtonText: { fontFamily: fonts.semibold, fontSize: 12, color: colors.primary },
  primaryButton: { alignSelf: "stretch", height: 47, marginTop: 10, borderRadius: 999, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7, backgroundColor: colors.primary },
  primaryButtonText: { fontFamily: fonts.bold, fontSize: 12, color: colors.white },
  textButton: { marginTop: 7, padding: 8 },
  textButtonLabel: { fontFamily: fonts.semibold, fontSize: 11, color: colors.mutedFg },
});
