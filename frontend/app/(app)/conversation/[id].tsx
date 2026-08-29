import React, { useEffect, useState } from "react";
import { View, Text, ScrollView, Pressable, TextInput, Alert, Share, ActivityIndicator, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  X, Pencil, Check, UserPlus, Link2, Search, Users, Bell, Timer, Lock,
  ShieldCheck, Copy, Trash2, LogOut, ThumbsDown, ChevronRight, AlertTriangle, RefreshCcw,
  FileText, Image as ImageIcon, Mic,
} from "lucide-react-native";
import { colors, spacing, radii, fonts, shadows, heroGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { downloadFile } from "@/src/lib/upload";
import { AddMemberSheet } from "@/src/components/AddMemberSheet";

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function iconFor(type?: string) {
  if (type === "image") return ImageIcon;
  if (type === "voice") return Mic;
  return FileText;
}

function tintFor(type?: string) {
  if (type === "image") return { bg: colors.info + "1F", fg: colors.info };
  if (type === "voice") return { bg: colors.violet + "1F", fg: colors.violet };
  return { bg: colors.destructive + "1A", fg: colors.destructive };
}

function roleTone(role: string) {
  const r = role.toLowerCase();
  if (r.includes("investigator") || /\bpi\b/.test(r)) return { bg: colors.accent + "1F", fg: colors.accent, ring: colors.accent + "4D", dot: colors.accent };
  if (r.includes("coordinator") || r.includes("crc") || r.includes("research")) return { bg: colors.info + "1F", fg: colors.info, ring: colors.info + "4D", dot: colors.info };
  if (r.includes("sponsor") || r.includes("cro")) return { bg: colors.violet + "1F", fg: colors.violet, ring: colors.violet + "4D", dot: colors.violet };
  if (r.includes("nurse") || r.includes("pharmac")) return { bg: colors.success + "1F", fg: colors.success, ring: colors.success + "4D", dot: colors.success };
  if (r.includes("patient")) return { bg: colors.primary + "1A", fg: colors.primary, ring: colors.primary + "40", dot: colors.primary };
  return { bg: colors.mutedFg + "1A", fg: colors.mutedFg, ring: colors.border, dot: colors.mutedFg + "80" };
}

const AUTO_DELETE_OPTIONS: { label: string; days: number | null }[] = [
  { label: "Off", days: null },
  { label: "1 day", days: 1 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];

export default function ConversationInfo() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { user } = useAuth();
  const [conv, setConv] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [showMemberSearch, setShowMemberSearch] = useState(false);
  const [memberQuery, setMemberQuery] = useState("");
  const [autoDeleteOpen, setAutoDeleteOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [previewFiles, setPreviewFiles] = useState<any[]>([]);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const r = await api.get(`/conversations/${id}`);
      setConv(r.data);
    } catch {
      setError("Couldn't load this channel. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  useEffect(() => {
    let cancelled = false;
    api.get(`/conversations/${id}/files`)
      .then((r) => { if (!cancelled) setPreviewFiles((r.data || []).slice(0, 3)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [id]);

  const startEdit = () => {
    setEditTitle(conv.title || "");
    setEditDescription(conv.description || "");
    setEditing(true);
  };

  const saveEdit = async () => {
    setSavingEdit(true);
    try {
      await api.patch(`/conversations/${id}/settings`, { title: editTitle.trim(), description: editDescription.trim() });
      setConv((prev: any) => ({ ...prev, title: editTitle.trim(), description: editDescription.trim() }));
      setEditing(false);
    } catch {
      Alert.alert("Couldn't save", "Try again in a moment.");
    } finally {
      setSavingEdit(false);
    }
  };

  const toggleNotifications = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/conversations/${id}/flags`, { muted: !conv.muted });
      setConv((prev: any) => ({ ...prev, muted: r.data.muted }));
    } catch {
      Alert.alert("Couldn't update notifications", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const setAutoDelete = async (days: number | null) => {
    setAutoDeleteOpen(false);
    setBusy(true);
    try {
      await api.patch(`/conversations/${id}/settings`, { auto_delete_days: days });
      setConv((prev: any) => ({ ...prev, auto_delete_days: days }));
    } catch {
      Alert.alert("Couldn't update auto-delete", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const shareInviteLink = async () => {
    setBusy(true);
    try {
      const r = await api.get(`/conversations/${id}/invite-link`);
      await Share.share({ message: `Join "${conv.title}" on My Trial Board: mytrialboard://conversations/join/${r.data.token}` });
    } catch {
      Alert.alert("Couldn't create invite link", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const setupSimilarChannel = async () => {
    setBusy(true);
    try {
      const memberIds = (conv.participants || []).map((p: any) => p.id).filter((pid: string) => pid !== user?.id);
      const r = await api.post("/conversations", {
        participant_ids: memberIds, is_group: true,
        title: `${conv.title} (copy)`, description: conv.description, trial_id: conv.trial_id,
      });
      router.replace({ pathname: "/(app)/conversation/[id]", params: { id: r.data.id } });
    } catch {
      Alert.alert("Couldn't set up a similar channel", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const clearMessages = () => {
    Alert.alert("Clear messages", "This clears your view of this channel's history. Other members keep theirs.", [
      { text: "Cancel", style: "cancel" },
      { text: "Clear", style: "destructive", onPress: async () => {
        try { await api.post(`/conversations/${id}/clear`); Alert.alert("Cleared", "Your message history for this channel has been cleared."); }
        catch { Alert.alert("Couldn't clear messages", "Try again in a moment."); }
      } },
    ]);
  };

  const leaveGroup = () => {
    Alert.alert("Leave group", "You'll stop receiving messages from this channel.", [
      { text: "Cancel", style: "cancel" },
      { text: "Leave", style: "destructive", onPress: async () => {
        try { await api.delete(`/conversations/${id}/members/${user?.id}`); router.replace("/(app)/chat"); }
        catch { Alert.alert("Couldn't leave", "Try again in a moment."); }
      } },
    ]);
  };

  const submitReport = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/conversations/${id}/report`, { reason: reportReason.trim() || undefined });
      setReportOpen(false); setReportReason("");
      Alert.alert("Reported", `Support ticket ${r.data.ticket_id} was opened for review.`);
    } catch {
      Alert.alert("Couldn't report this channel", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const removeMember = (memberId: string, memberName: string) => {
    Alert.alert(`Remove ${memberName}?`, "They will lose access to this channel.", [
      { text: "Cancel", style: "cancel" },
      { text: "Remove", style: "destructive", onPress: async () => {
        try {
          await api.delete(`/conversations/${id}/members/${memberId}`);
          setConv((prev: any) => ({ ...prev, participants: (prev.participants || []).filter((p: any) => p.id !== memberId) }));
        } catch { Alert.alert("Couldn't remove member", "Try again in a moment."); }
      } },
    ]);
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.primary} />
      </SafeAreaView>
    );
  }
  if (error || !conv) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, padding: spacing.md }}>
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <AlertTriangle size={18} color={colors.destructive} />
            <Body weight="600" style={{ flex: 1 }}>{error || "Channel not found."}</Body>
          </View>
          <Pressable testID="conv-info-retry" onPress={load} style={s.retryBtn}>
            <RefreshCcw size={14} color={colors.primary} />
            <Small weight="700" color={colors.primary}>Retry</Small>
          </Pressable>
        </Card>
      </SafeAreaView>
    );
  }

  const participants: any[] = conv.participants || [];
  const onlineCount = participants.filter((p) => p.is_online).length;
  const memberQ = memberQuery.trim().toLowerCase();
  const roster = memberQ
    ? participants.filter((p) => `${p.full_name || ""} ${p.role || ""}`.toLowerCase().includes(memberQ))
    : participants;
  const autoDeleteLabel = AUTO_DELETE_OPTIONS.find((o) => o.days === (conv.auto_delete_days ?? null))?.label || "Off";
  const complianceStandard = !!conv.trial_id;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <LinearGradient colors={heroGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ borderBottomLeftRadius: 32, borderBottomRightRadius: 32, overflow: "hidden" }}>
        <View style={s.header}>
          <Pressable testID="conv-info-close" onPress={() => router.back()} hitSlop={12}><X size={22} color={colors.primaryFg} /></Pressable>
          <Eyebrow style={{ flex: 1, textAlign: "center" }} color={colors.primaryFg + "CC"}>Team channel</Eyebrow>
          {conv.is_group && conv.is_admin ? (
            editing ? (
              <Pressable testID="conv-info-save" onPress={saveEdit} disabled={savingEdit} hitSlop={12}>
                {savingEdit ? <ActivityIndicator size="small" color={colors.primaryFg} /> : <Check size={20} color={colors.primaryFg} />}
              </Pressable>
            ) : (
              <Pressable testID="conv-info-edit" onPress={startEdit} hitSlop={12}><Pencil size={19} color={colors.primaryFg} /></Pressable>
            )
          ) : <View style={{ width: 22 }} />}
        </View>

        <View style={{ alignItems: "center", paddingTop: spacing.md, paddingBottom: 56, paddingHorizontal: spacing.lg }}>
          <View style={s.bigAvatar}><Users size={30} color={colors.primaryFg} /></View>
          {editing ? (
            <>
              <TextInput testID="conv-info-title-input" value={editTitle} onChangeText={setEditTitle} style={s.titleInput} placeholder="Channel name" placeholderTextColor={colors.primaryFg + "99"} />
              <TextInput testID="conv-info-desc-input" value={editDescription} onChangeText={setEditDescription} style={s.descInput} placeholder="Description" placeholderTextColor={colors.primaryFg + "99"} multiline />
            </>
          ) : (
            <>
              <H1 style={{ marginTop: 10, textAlign: "center" }} color={colors.primaryFg}>{conv.title || "Conversation"}</H1>
              {conv.protocol_id ? (
                <View style={s.protocolTag}>
                  <FileText size={12} color={colors.primaryFg} />
                  <Small color={colors.primaryFg} style={{ fontFamily: fonts.mono, fontSize: 11 }}>{conv.protocol_id} · {conv.trial_title || "Site coordination"}</Small>
                </View>
              ) : null}
              {conv.description ? <Small style={{ marginTop: 8, textAlign: "center", maxWidth: 280 }} color={colors.primaryFg + "CC"}>{conv.description}</Small> : null}
            </>
          )}
          {participants.length > 0 && (
            <View style={{ flexDirection: "row", alignItems: "center", marginTop: 14, gap: 10 }}>
              <View style={{ flexDirection: "row" }}>
                {participants.slice(0, 4).map((p, i) => (
                  <View key={p.id} style={[s.stackAvatar, i > 0 && { marginLeft: -10 }]}>
                    <Small color={colors.primaryFg} style={{ fontSize: 10, fontFamily: fonts.semibold }}>{p.avatar_initials || "?"}</Small>
                  </View>
                ))}
                {participants.length > 4 && (
                  <View style={[s.stackAvatar, { marginLeft: -10, backgroundColor: colors.overlay10 }]}>
                    <Small color={colors.primaryFg} style={{ fontSize: 9, fontFamily: fonts.semibold }}>+{participants.length - 4}</Small>
                  </View>
                )}
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Small color={colors.primaryFg + "CC"} style={{ fontSize: 12 }}>{participants.length} members</Small>
                {onlineCount > 0 && (
                  <>
                    <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: colors.success }} />
                    <Small color={colors.primaryFg + "CC"} style={{ fontSize: 12 }}>{onlineCount} online</Small>
                  </>
                )}
              </View>
            </View>
          )}
        </View>
      </LinearGradient>

      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xxl, gap: spacing.sm }}>
        {conv.is_group && (
          <View style={s.actionsCard}>
            <Pressable testID="conv-info-add-member" style={s.heroAction} onPress={() => setAddMemberOpen(true)}>
              <View style={[s.actionIcon, { backgroundColor: colors.primary + "1A" }]}><UserPlus size={18} color={colors.primary} /></View>
              <Small color={colors.foreground} style={{ fontSize: 11, fontFamily: fonts.semibold }}>Add member</Small>
            </Pressable>
            <Pressable testID="conv-info-invite-link" style={s.heroAction} onPress={shareInviteLink} disabled={busy}>
              <View style={[s.actionIcon, { backgroundColor: colors.violet + "1F" }]}><Link2 size={18} color={colors.violet} /></View>
              <Small color={colors.foreground} style={{ fontSize: 11, fontFamily: fonts.semibold }}>Invite link</Small>
            </Pressable>
            <Pressable testID="conv-info-find-member" style={s.heroAction} onPress={() => { setShowMemberSearch(v => !v); if (showMemberSearch) setMemberQuery(""); }}>
              <View style={[s.actionIcon, { backgroundColor: colors.info + "1F" }]}><Search size={18} color={colors.info} /></View>
              <Small color={colors.foreground} style={{ fontSize: 11, fontFamily: fonts.semibold }}>Find member</Small>
            </Pressable>
          </View>
        )}

        <View style={{ flexDirection: "row", alignItems: "center", marginTop: spacing.md }}>
          <Eyebrow style={{ flex: 1 }}>Who&apos;s in this channel</Eyebrow>
          <Small>{roster.length} of {participants.length}</Small>
        </View>
        {showMemberSearch && (
          <View style={s.memberSearchBox}>
            <Search size={16} color={colors.mutedFg + "99"} />
            <TextInput
              testID="conv-info-member-search"
              autoFocus
              value={memberQuery}
              onChangeText={setMemberQuery}
              placeholder="Find a member or role"
              placeholderTextColor={colors.mutedFg + "99"}
              style={{ flex: 1, paddingVertical: 10, color: colors.foreground, fontSize: 14 }}
            />
            {memberQuery ? (
              <Pressable testID="conv-info-member-search-clear" onPress={() => setMemberQuery("")} hitSlop={8}>
                <X size={14} color={colors.mutedFg} />
              </Pressable>
            ) : null}
          </View>
        )}
        <Card padded={false}>
          {roster.length === 0 && (
            <Small style={{ padding: spacing.lg, textAlign: "center", fontSize: 12 }}>
              No member matches &ldquo;{memberQuery.trim()}&rdquo;. Clear the search to see the full roster.
            </Small>
          )}
          {roster.map((p, i) => {
            const tone = roleTone(String(p.role || ""));
            return (
              <View key={p.id}>
                {i > 0 && <View style={s.memberDivider} />}
                <Pressable
                  testID={`conv-info-member-${p.id}`}
                  onLongPress={() => (conv.is_admin && p.id !== user?.id ? removeMember(p.id, p.full_name) : undefined)}
                  style={s.memberRow}
                >
                  <View style={[s.avatar, { backgroundColor: tone.bg, borderWidth: 2, borderColor: tone.ring }]}>
                    <Small color={tone.fg} style={{ fontFamily: fonts.bold, fontSize: 12 }}>{p.avatar_initials || "?"}</Small>
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Body weight="600" numberOfLines={1} style={{ flexShrink: 1 }}>{p.full_name}{p.id === user?.id ? " (you)" : ""}</Body>
                      {p.admin ? (
                        <View style={s.adminBadge}>
                          <ShieldCheck size={10} color={colors.accent} />
                          <Small color={colors.accent} style={{ fontSize: 9, fontFamily: fonts.semibold }}>Admin</Small>
                        </View>
                      ) : null}
                    </View>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 2 }}>
                      <View style={[s.roleDot, { backgroundColor: tone.dot }]} />
                      <Small numberOfLines={1} style={{ flexShrink: 1 }}>{String(p.role || "").toUpperCase()}{p.organization ? ` · ${p.organization}` : ""}</Small>
                    </View>
                  </View>
                  {p.is_online && <View style={s.onlineDot} />}
                  <ChevronRight size={16} color={colors.mutedFg + "4D"} />
                </Pressable>
              </View>
            );
          })}
        </Card>

        <View style={{ flexDirection: "row", alignItems: "center", marginTop: spacing.md }}>
          <Eyebrow style={{ flex: 1 }}>Shared files & media</Eyebrow>
          <Pressable testID="conv-info-all-files" onPress={() => router.push({ pathname: "/(app)/conversation/[id]/files", params: { id: String(id) } })} hitSlop={8} style={{ flexDirection: "row", alignItems: "center", gap: 2 }}>
            <Small color={colors.accent} style={{ fontSize: 11, fontFamily: fonts.semibold }}>All {conv.media_count || 0}</Small>
            <ChevronRight size={14} color={colors.accent} />
          </Pressable>
        </View>
        {previewFiles.length > 0 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
            {previewFiles.map((row) => {
              const Icon = iconFor(row.type);
              const tint = tintFor(row.type);
              return (
                <Pressable
                  key={row.message_id}
                  testID={`conv-info-file-${row.message_id}`}
                  disabled={!row.file_id}
                  onPress={() => downloadFile({ id: row.file_id, name: row.name, content_type: row.content_type }).catch(() => Alert.alert("Couldn't open file", "Try again in a moment."))}
                  style={s.fileTile}
                >
                  <View style={[s.fileIcon, { backgroundColor: tint.bg }]}><Icon size={17} color={tint.fg} /></View>
                  <Small numberOfLines={1} color={colors.foreground} style={{ fontSize: 11, fontFamily: fonts.semibold, marginTop: 8, maxWidth: "100%" }}>{row.name || "Voice message"}</Small>
                  {row.size ? <Small style={{ fontSize: 9, marginTop: 1 }}>{formatSize(row.size)}</Small> : null}
                </Pressable>
              );
            })}
          </ScrollView>
        )}

        <Eyebrow style={{ marginTop: spacing.md }}>Channel controls</Eyebrow>
        <Card padded={false} style={{ borderRadius: radii.lg }}>
          <Pressable testID="conv-info-notifications" onPress={toggleNotifications} disabled={busy} style={[s.controlRow]}>
            <View style={[s.controlIcon, { backgroundColor: colors.warning + "26" }]}><Bell size={17} color={colors.warning} /></View>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Body weight="600">Notifications</Body>
              <Small style={{ fontSize: 11 }}>{conv.muted ? "Muted — mentions only" : "Every message"}</Small>
            </View>
            <View style={[s.toggleTrack, !conv.muted && s.toggleTrackOn]}>
              <View style={[s.toggleThumb, !conv.muted && s.toggleThumbOn]} />
            </View>
          </Pressable>
          <View style={s.controlDivider} />
          <Pressable testID="conv-info-auto-delete" onPress={() => setAutoDeleteOpen(true)} style={s.controlRow}>
            <View style={[s.controlIcon, { backgroundColor: colors.info + "1F" }]}><Timer size={17} color={colors.info} /></View>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Body weight="600">Auto-delete timer</Body>
              <Small style={{ fontSize: 11 }}>Messages stay until deleted</Small>
            </View>
            <View style={s.pill}><Small color={colors.mutedFg} style={{ fontSize: 11, fontFamily: fonts.semibold }}>{autoDeleteLabel}</Small></View>
          </Pressable>
          <View style={s.controlDivider} />
          <View style={s.controlRow}>
            <View style={[s.controlIcon, { backgroundColor: colors.violet + "1F" }]}><ShieldCheck size={17} color={colors.violet} /></View>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Body weight="600">Compliance & data controls</Body>
              <Small style={{ fontSize: 11 }}>Retention and audit follow trial policy</Small>
            </View>
            <View style={s.pill}><Small color={colors.mutedFg} style={{ fontSize: 11, fontFamily: fonts.semibold }}>{complianceStandard ? "Standard" : "General"}</Small></View>
          </View>
        </Card>

        {autoDeleteOpen && (
          <Card>
            {AUTO_DELETE_OPTIONS.map((opt) => (
              <Pressable key={opt.label} testID={`auto-delete-${opt.days ?? "off"}`} onPress={() => setAutoDelete(opt.days)} style={{ paddingVertical: 10, flexDirection: "row", alignItems: "center" }}>
                <Body weight={opt.days === (conv.auto_delete_days ?? null) ? "700" : "400"} style={{ flex: 1 }}>{opt.label}</Body>
                {opt.days === (conv.auto_delete_days ?? null) && <Check size={16} color={colors.primary} />}
              </Pressable>
            ))}
          </Card>
        )}

        <View style={s.encryptionCard}>
          <View style={s.encryptionIcon}><Lock size={15} color={colors.success} /></View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Body weight="600" style={{ fontSize: 13 }}>Encrypted in transit and at rest</Body>
            <Small style={{ fontSize: 11, marginTop: 2, lineHeight: 16 }}>
              Every message here is covered by MTB&apos;s data-protection policy.{" "}
              <Text testID="conv-info-view-policy" onPress={() => router.push("/(app)/data-policy")} style={{ fontFamily: fonts.semibold, fontSize: 11, color: colors.success }}>View policy</Text>
            </Small>
          </View>
        </View>

        {conv.is_group && (
          <Pressable testID="conv-info-duplicate" onPress={setupSimilarChannel} disabled={busy}>
            <Card style={{ flexDirection: "row", alignItems: "center", gap: 12, borderRadius: radii.lg, borderStyle: "dashed", borderColor: colors.primary + "4D", backgroundColor: colors.card + "99" }}>
              <View style={[s.controlIcon, { backgroundColor: colors.primary + "1A" }]}><Copy size={17} color={colors.primary} /></View>
              <View style={{ flex: 1 }}>
                <Body weight="600">Set up a similar channel</Body>
                <Small style={{ fontSize: 11 }}>Start a new channel with these {participants.length} members, ready to adjust</Small>
              </View>
            </Card>
          </Pressable>
        )}

        {conv.is_group && (
          <>
            <Card padded={false} style={{ marginTop: spacing.sm, borderRadius: radii.lg }}>
              <Pressable testID="conv-info-clear" onPress={clearMessages} style={s.dangerRow}>
                <View style={s.dangerIcon}><Trash2 size={17} color={colors.destructive} /></View>
                <Body weight="600" color={colors.destructive} style={{ fontSize: 14 }}>Clear messages</Body>
              </Pressable>
              <View style={s.controlDivider} />
              <Pressable testID="conv-info-leave" onPress={leaveGroup} style={s.dangerRow}>
                <View style={s.dangerIcon}><LogOut size={17} color={colors.destructive} /></View>
                <Body weight="600" color={colors.destructive} style={{ fontSize: 14 }}>Leave group</Body>
              </Pressable>
              <View style={s.controlDivider} />
              <Pressable testID="conv-info-report" onPress={() => setReportOpen(true)} style={s.dangerRow}>
                <View style={s.dangerIcon}><ThumbsDown size={17} color={colors.destructive} /></View>
                <Body weight="600" color={colors.destructive} style={{ fontSize: 14 }}>Report group</Body>
              </Pressable>
            </Card>
            <Small style={{ fontSize: 10, textAlign: "center", marginTop: 4 }} color={colors.mutedFg + "99"}>
              Channel created for {conv.protocol_id || "this trial"} · actions here are recorded in the audit trail
            </Small>
          </>
        )}
      </ScrollView>

      <AddMemberSheet
        visible={addMemberOpen}
        onClose={() => setAddMemberOpen(false)}
        conversationId={String(id)}
        existingIds={participants.map((p) => p.id)}
        onMemberAdded={(updated) => setConv((prev: any) => ({ ...prev, participant_ids: updated.participant_ids }))}
      />

      {reportOpen && (
        <View style={s.reportOverlay}>
          <Card style={{ width: "100%" }}>
            <Body weight="700" style={{ marginBottom: 8 }}>Report this channel</Body>
            <TextInput
              testID="conv-info-report-reason"
              value={reportReason}
              onChangeText={setReportReason}
              placeholder="What's wrong? (optional)"
              placeholderTextColor={colors.mutedFg}
              style={s.reportInput}
              multiline
            />
            <View style={{ flexDirection: "row", gap: 10, marginTop: 12 }}>
              <Pressable testID="conv-info-report-cancel" onPress={() => setReportOpen(false)} style={[s.actionBtn, { flex: 1 }]}>
                <Small weight="700" color={colors.foreground}>Cancel</Small>
              </Pressable>
              <Pressable testID="conv-info-report-submit" onPress={submitReport} disabled={busy} style={[s.actionBtn, { flex: 1, backgroundColor: colors.destructive, borderColor: colors.destructive }]}>
                {busy ? <ActivityIndicator size="small" color={colors.destructiveFg} /> : <Small weight="700" color={colors.destructiveFg}>Submit</Small>}
              </Pressable>
            </View>
          </Card>
        </View>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 12 },
  bigAvatar: { width: 84, height: 84, borderRadius: radii.xl, backgroundColor: colors.overlay20, borderWidth: 1, borderColor: colors.overlay25, alignItems: "center", justifyContent: "center" },
  protocolTag: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 8, paddingHorizontal: 12, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20, borderWidth: 1, borderColor: colors.overlay25 },
  titleInput: { marginTop: 10, width: "100%", textAlign: "center", fontFamily: "BricolageGrotesque-Bold", fontSize: 22, color: colors.primaryFg, borderBottomWidth: 1, borderColor: colors.overlay25, paddingVertical: 4 },
  descInput: { marginTop: 8, width: "100%", textAlign: "center", fontSize: 14, color: colors.primaryFg, paddingHorizontal: spacing.lg, minHeight: 40 },
  actionBtn: { flex: 1, alignItems: "center", gap: 6, paddingVertical: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  actionsCard: { flexDirection: "row", marginTop: -36, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: 6, gap: 4, ...shadows.md },
  heroAction: { flex: 1, alignItems: "center", gap: 6, paddingVertical: 12, borderRadius: radii.lg },
  actionIcon: { width: 40, height: 40, borderRadius: radii.lg, alignItems: "center", justifyContent: "center" },
  avatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10, paddingHorizontal: spacing.md },
  memberDivider: { marginLeft: 70, borderTopWidth: 1, borderTopColor: colors.border + "99" },
  memberSearchBox: { flexDirection: "row", alignItems: "center", gap: 10, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: 14 },
  adminBadge: { flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 6, paddingVertical: 2, borderRadius: radii.pill, backgroundColor: colors.accent + "26" },
  roleDot: { width: 6, height: 6, borderRadius: 3 },
  onlineDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success },
  controlRow: { flexDirection: "row", alignItems: "center", paddingVertical: 12, paddingHorizontal: spacing.md },
  controlIcon: { width: 36, height: 36, borderRadius: radii.md, alignItems: "center", justifyContent: "center" },
  controlDivider: { marginLeft: 60, borderTopWidth: 1, borderTopColor: colors.border + "99" },
  toggleTrack: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.border, padding: 2, justifyContent: "center" },
  toggleTrackOn: { backgroundColor: colors.success },
  toggleThumb: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.card },
  toggleThumbOn: { alignSelf: "flex-end" },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.mutedFg + "1F" },
  stackAvatar: { width: 30, height: 30, borderRadius: 15, backgroundColor: colors.overlay20, borderWidth: 2, borderColor: colors.primaryDeep, alignItems: "center", justifyContent: "center" },
  fileTile: { width: 104, borderRadius: radii.lg, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, padding: 10, ...shadows.sm },
  fileIcon: { width: 36, height: 36, borderRadius: radii.md, alignItems: "center", justifyContent: "center" },
  encryptionCard: { flexDirection: "row", alignItems: "flex-start", gap: 10, marginTop: 2, borderRadius: radii.lg, backgroundColor: colors.success + "14", borderWidth: 1, borderColor: colors.success + "33", padding: 14 },
  encryptionIcon: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" },
  dangerRow: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14 },
  dangerIcon: { width: 36, height: 36, borderRadius: radii.md, backgroundColor: colors.destructive + "1A", alignItems: "center", justifyContent: "center" },
  reportOverlay: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, backgroundColor: "rgba(46,27,51,0.45)", alignItems: "center", justifyContent: "center", padding: spacing.md },
  reportInput: { minHeight: 80, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground, textAlignVertical: "top" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
});
