// Real-time messaging: conversation list + thread view over the live
// /conversations HTTP contracts and the authenticated WebSocket.
//
// Reliability model (approved Messages states):
//   • initial load has real loading / error+retry / empty states;
//   • the socket reports connecting / online / offline, reconnects with
//     exponential backoff, and resyncs conversations + the open thread after
//     every reconnect (messages missed while offline are recovered);
//   • sends are optimistic — a pending bubble appears immediately, failures
//     stay in the thread as "Not sent" with tap-to-retry / long-press-discard;
//   • send stays disabled without text; HTTP sending works even while the
//     socket is down, so the offline banner never blocks output silently.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, FlatList, KeyboardAvoidingView, Platform, ActivityIndicator, Modal, Alert, StatusBar } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ChevronLeft, Send, WifiOff, RefreshCcw, AlertTriangle, Pin, BellOff, SquarePen, Users, X, Search, Archive, Paperclip, Camera as CameraIcon, Smile, FileText, Image as ImageIcon, Mic, Check, CheckCheck, MoreVertical, ShieldCheck, Play, Pause } from "lucide-react-native";
import * as ImagePicker from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";
import { colors, spacing, radii, shadows, fonts } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api, tokenStore, wsUrl } from "@/src/api/client";
import { animateNextLayout } from "@/src/lib/motion";
import { uploadFile, downloadFile, PickedAsset } from "@/src/lib/upload";
import { useAudioRecorder, useAudioRecorderState, RecordingPresets, AudioModule, useAudioPlayer } from "expo-audio";
import { PatientBottomNav } from "@/src/features/patient/components/PatientBottomNav";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { PiBottomNav } from "@/src/features/clinical/components/PiBottomNav";

const QUICK_EMOJI = ["👍", "❤️", "😂", "🙏", "👏", "✅", "🎉", "😊"];

const AVATAR_THEMES = [
  { bg: colors.primary + "1F", fg: colors.primary, ring: colors.primary + "26" },
  { bg: colors.info + "26", fg: colors.info, ring: colors.info + "26" },
  { bg: colors.violet + "26", fg: colors.violet, ring: colors.violet + "26" },
  { bg: colors.accent + "3D", fg: colors.accent, ring: colors.accent + "40" },
  { bg: colors.success + "26", fg: colors.success, ring: colors.success + "26" },
  { bg: colors.warning + "38", fg: colors.warning, ring: colors.warning + "33" },
];
const SUPPORT_AVATAR_THEME = { bg: colors.destructive + "24", fg: colors.destructive, ring: colors.destructive + "26" };
function avatarTheme(id: string) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return AVATAR_THEMES[hash % AVATAR_THEMES.length];
}

function stripTitle(name: string): string {
  return name.replace(/^(Dr\.|Mr\.|Ms\.)\s/, "");
}

// On-brand sender-name colours for group bubbles (reference senderPalette).
const SENDER_PALETTE = [colors.info, colors.violet, colors.accent, colors.warning, colors.success, colors.destructive];
function senderColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return SENDER_PALETTE[hash % SENDER_PALETTE.length];
}

function fmtDuration(secs: number): string {
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

// Inbox preview text for a message — mirrors the backend's stored preview
// so optimistic/WS updates don't blank attachment previews (content is "").
function previewFor(msg: any): string {
  if (msg?.content) return msg.content;
  if (msg?.type === "image") return "📷 Photo";
  if (msg?.type === "document") return `📄 ${msg?.attachment?.name || "Document"}`;
  if (msg?.type === "voice") return "🎤 Voice message";
  return msg?.content || "";
}

function formatRowTimestamp(iso?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDiff = Math.round((startOfToday.getTime() - startOfDate.getTime()) / 86400000);
  if (dayDiff <= 0) return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (dayDiff === 1) return "Yesterday";
  if (dayDiff < 7) return `${dayDiff}d ago`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

function formatSize(bytes?: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type Connection = "connecting" | "online" | "offline";

function VoiceBubble({ fileId, duration, mine }: { fileId: string; duration?: number; mine: boolean }) {
  const [uri, setUri] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const player = useAudioPlayer(uri || undefined);
  useEffect(() => { if (uri) { player.play(); setPlaying(true); } }, [uri]);
  const toggle = async () => {
    if (!uri) {
      const { fetchFileUri } = await import("@/src/lib/upload");
      setUri(await fetchFileUri(fileId));
      return;
    }
    if (playing) { player.pause(); setPlaying(false); }
    else { player.seekTo(0); player.play(); setPlaying(true); }
  };
  const fg = mine ? colors.primaryFg : colors.primary;
  return (
    <Pressable testID={`voice-${fileId}`} onPress={toggle} style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 2 }}>
      {playing ? <Pause size={24} color={fg} /> : <Play size={24} color={fg} />}
      <View style={{ height: 4, width: 112, borderRadius: 2, backgroundColor: mine ? colors.overlay25 : colors.border }}>
        <View style={{ height: 4, width: "33%", borderRadius: 2, backgroundColor: mine ? colors.card : colors.primary }} />
      </View>
      <Small color={mine ? colors.primaryFg + "B3" : colors.mutedFg} style={{ fontSize: 11, fontFamily: fonts.mono }}>{fmtDuration(duration ?? 0)}</Small>
    </Pressable>
  );
}

export default function Chat() {
  const router = useRouter();
  const params = useLocalSearchParams<{ conversationId?: string; participantId?: string }>();
  const { user } = useAuth();
  const [convs, setConvs] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [active, setActive] = useState<any | null>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState("");
  const [starting, setStarting] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [directoryLoaded, setDirectoryLoaded] = useState(false);
  const [filter, setFilter] = useState<"all" | "unread" | "groups" | "archived">("all");
  const [details, setDetails] = useState<any | null>(null);
  const [flagBusy, setFlagBusy] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const [composeQuery, setComposeQuery] = useState("");
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [inboxMenuOpen, setInboxMenuOpen] = useState(false);
  const [threadMenuOpen, setThreadMenuOpen] = useState(false);
  const [emojiRowOpen, setEmojiRowOpen] = useState(false);
  const [groupMode, setGroupMode] = useState(false);
  const [groupSelectedIds, setGroupSelectedIds] = useState<string[]>([]);
  const [groupTitle, setGroupTitle] = useState("");
  const [creatingGroup, setCreatingGroup] = useState(false);
  const audioRecorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(audioRecorder);
  const [recording, setRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const recordStartRef = useRef<number>(0);
  const recordTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);
  const mountedRef = useRef(true);
  const autoOpenedRef = useRef(false);
  const activeIdRef = useRef<string | undefined>(undefined);
  const listRef = useRef<FlatList>(null);
  const userId = user?.id;

  useEffect(() => {
    activeIdRef.current = active?.id;
  }, [active?.id]);

  const loadDirectory = useCallback(async (silent = false) => {
    if (!silent) { setLoading(true); setLoadError(""); }
    try {
      const [c, u] = await Promise.all([api.get("/conversations"), api.get("/messaging/recipients")]);
      setConvs(c.data); setUsers(u.data);
      setLoadError("");
    } catch {
      if (!silent) setLoadError("Couldn't load your messages. Check your connection and retry.");
    } finally {
      setLoading(false);
      setDirectoryLoaded(true);
    }
  }, []);

  // Re-sync the open thread (used after a reconnect so nothing is missed).
  const resyncActive = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    try {
      const r = await api.get(`/conversations/${id}/messages`);
      setMessages(prev => {
        const pendingLocal = prev.filter(m => m.pending || m.failed);
        return [...r.data, ...pendingLocal];
      });
    } catch { /* thread keeps its current contents; user can pull the thread again */ }
  }, []);

  // ── WebSocket with exponential-backoff reconnect ──
  useEffect(() => {
    if (!userId) return;
    mountedRef.current = true;
    loadDirectory();

    const connect = async () => {
      const t = await tokenStore.get("access_token");
      if (!t || !mountedRef.current) return;
      setConnection(prev => (prev === "online" ? prev : "connecting"));
      const ws = new WebSocket(wsUrl(t));
      wsRef.current = ws;
      ws.onopen = () => {
        if (!mountedRef.current) return;
        const wasRetry = reconnectAttempts.current > 0;
        reconnectAttempts.current = 0;
        setConnection("online");
        if (wasRetry) { loadDirectory(true); resyncActive(); }
      };
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "message") {
            if (data.conversation_id === activeIdRef.current) {
              setMessages(prev => prev.some(m => m.id === data.id) ? prev : [...prev, data]);
              ws.send(JSON.stringify({ type: "read", conversation_id: data.conversation_id }));
            }
            setConvs(prev => prev.map(c => c.id === data.conversation_id
              ? { ...c, last_message: previewFor(data), last_sender_id: data.sender_id, last_read: false, unread_count: data.conversation_id === activeIdRef.current ? 0 : (c.unread_count || 0) + (data.sender_id === userId ? 0 : 1) }
              : c));
          } else if (data.type === "typing" && data.conversation_id === activeIdRef.current) {
            setTyping(true); setTimeout(() => setTyping(false), 2500);
          } else if (data.type === "read") {
            // live read receipt: flip the inbox ✓✓ and, when the thread is
            // open, mark my messages as read by that member
            setConvs(prev => prev.map(c => c.id === data.conversation_id && c.last_sender_id === userId
              ? { ...c, last_read: true }
              : c));
            if (data.conversation_id === activeIdRef.current) {
              setMessages(prev => prev.map(m => m.sender_id === userId
                ? { ...m, read_by: { ...(m.read_by || {}), [data.user_id]: data.read_at } }
                : m));
            }
          }
        } catch {}
      };
      const scheduleReconnect = () => {
        if (!mountedRef.current) return;
        setConnection("offline");
        const delay = Math.min(30000, 2000 * 2 ** reconnectAttempts.current);
        reconnectAttempts.current += 1;
        if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
        reconnectTimer.current = setTimeout(connect, delay);
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
      ws.onclose = scheduleReconnect;
    };
    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      if (ws) { ws.onclose = null; ws.close(); }
    };
  }, [userId, loadDirectory, resyncActive]);

  const openConv = useCallback(async (c: any) => {
    setError(""); setThreadError("");
    setActive(c);
    setThreadLoading(true);
    setConvs(prev => prev.map(x => x.id === c.id ? { ...x, unread_count: 0 } : x));
    try {
      const r = await api.get(`/conversations/${c.id}/messages`);
      setMessages(r.data);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: false }), 100);
    } catch {
      setMessages([]);
      setThreadError("Couldn't load this conversation.");
    } finally {
      setThreadLoading(false);
    }
  }, []);

  const startWith = useCallback(async (otherId: string) => {
    setStarting(otherId);
    try {
      const r = await api.post("/conversations", { participant_ids: [otherId] });
      const c = r.data;
      const refresh = await api.get("/conversations");
      setConvs(refresh.data);
      const enriched = refresh.data.find((x: any) => x.id === c.id) || c;
      await openConv(enriched);
    } finally {
      setStarting(null);
    }
  }, [openConv]);

  useEffect(() => {
    if (!directoryLoaded || autoOpenedRef.current) return;
    const requestedConversation = params.conversationId
      ? convs.find((conversation) => conversation.id === params.conversationId)
      : null;
    const requestedParticipant = String(params.participantId || "").trim();
    const existingDirect = requestedParticipant
      ? convs.find((conversation) => (
          conversation.other_participant?.id === requestedParticipant
          || conversation.participant_ids?.includes(requestedParticipant)
        ))
      : null;
    if (!requestedConversation && !requestedParticipant) return;
    autoOpenedRef.current = true;
    if (requestedConversation || existingDirect) {
      openConv(requestedConversation || existingDirect);
      return;
    }
    startWith(requestedParticipant).catch((e: any) => {
      setError(e?.response?.data?.detail || "Could not open the requested conversation.");
    });
  }, [convs, directoryLoaded, openConv, params.conversationId, params.participantId, startWith]);

  // ── Optimistic send with persistent failed-message retry ──
  const sendContent = useCallback(async (content: string, reuseLocalId?: string) => {
    if (!active) return;
    const localId = reuseLocalId || `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const optimistic = {
      id: localId, content, sender_id: userId,
      created_at: new Date().toISOString(), pending: true, failed: false,
    };
    setMessages(prev => {
      const without = prev.filter(m => m.id !== localId);
      return [...without, optimistic];
    });
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    setSending(true);
    try {
      const response = await api.post(`/conversations/${active.id}/messages`, { content });
      setMessages(prev => {
        const replaced = prev.map(m => (m.id === localId ? response.data : m));
        return replaced.filter((m, i, arr) => arr.findIndex(x => x.id === m.id) === i);
      });
      setConvs(prev => prev.map(c => c.id === active.id ? { ...c, last_message: previewFor(response.data), last_sender_id: userId, last_read: false } : c));
    } catch {
      setMessages(prev => prev.map(m => m.id === localId ? { ...m, pending: false, failed: true } : m));
    } finally {
      setSending(false);
    }
  }, [active, userId]);

  const sendAttachment = useCallback(async (asset: PickedAsset, msgType: "image" | "document") => {
    if (!active) return;
    setSending(true);
    try {
      const uploaded = await uploadFile(asset, { scopeType: "conversation", scopeId: active.id });
      const response = await api.post(`/conversations/${active.id}/messages`, {
        content: "", type: msgType,
        attachment: { file_id: uploaded.id, name: uploaded.name, size: uploaded.size, content_type: uploaded.content_type },
      });
      setMessages(prev => [...prev, response.data]);
      setConvs(prev => prev.map(c => c.id === active.id ? { ...c, last_message: previewFor(response.data), last_sender_id: userId, last_read: false } : c));
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    } catch {
      Alert.alert("Couldn't send attachment", "Try again in a moment.");
    } finally {
      setSending(false);
    }
  }, [active]);

  const pickAndSendImage = async (fromCamera: boolean) => {
    const perm = fromCamera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { Alert.alert("Permission needed", fromCamera ? "Camera access is required to take a photo." : "Photo library access is required."); return; }
    const result = fromCamera
      ? await ImagePicker.launchCameraAsync({ quality: 0.7 })
      : await ImagePicker.launchImageLibraryAsync({ quality: 0.7, mediaTypes: ["images"] });
    if (result.canceled || !result.assets?.length) return;
    const a = result.assets[0];
    await sendAttachment({ uri: a.uri, name: a.fileName || `photo-${Date.now()}.jpg`, mimeType: a.mimeType || "image/jpeg" }, "image");
  };

  const pickAndSendDocument = async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
      copyToCacheDirectory: true,
    });
    if (result.canceled || !result.assets?.length) return;
    const a = result.assets[0];
    await sendAttachment({ uri: a.uri, name: a.name || "document", mimeType: a.mimeType, file: (a as any).file }, "document");
  };

  const startRecording = async () => {
    const status = await AudioModule.requestRecordingPermissionsAsync();
    if (!status.granted) { Alert.alert("Permission needed", "Microphone access is required to record a voice message."); return; }
    setAttachMenuOpen(false); setEmojiRowOpen(false);
    recordStartRef.current = Date.now();
    setRecordingTime(0);
    if (recordTimerRef.current) clearInterval(recordTimerRef.current);
    recordTimerRef.current = setInterval(() => setRecordingTime(t => t + 1), 1000);
    await audioRecorder.prepareToRecordAsync();
    audioRecorder.record();
    setRecording(true);
  };

  const stopAndSendRecording = async () => {
    setRecording(false);
    if (recordTimerRef.current) { clearInterval(recordTimerRef.current); recordTimerRef.current = null; }
    setRecordingTime(0);
    await audioRecorder.stop();
    const uri = audioRecorder.uri;
    if (!uri || !active) return;
    const durationSec = Math.round((Date.now() - recordStartRef.current) / 1000);
    if (durationSec < 1) return;
    setSending(true);
    try {
      const uploaded = await uploadFile({ uri, name: `voice-${Date.now()}.m4a`, mimeType: "audio/m4a" }, { scopeType: "conversation", scopeId: active.id });
      const response = await api.post(`/conversations/${active.id}/messages`, {
        content: "", type: "voice",
        attachment: { file_id: uploaded.id, name: uploaded.name, size: uploaded.size, content_type: uploaded.content_type, duration: durationSec },
      });
      setMessages(prev => [...prev, response.data]);
      setConvs(prev => prev.map(c => c.id === active.id ? { ...c, last_message: previewFor(response.data), last_sender_id: userId, last_read: false } : c));
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    } catch {
      Alert.alert("Couldn't send voice message", "Try again in a moment.");
    } finally {
      setSending(false);
    }
  };

  const cancelRecording = async () => {
    setRecording(false);
    if (recordTimerRef.current) { clearInterval(recordTimerRef.current); recordTimerRef.current = null; }
    setRecordingTime(0);
    await audioRecorder.stop().catch(() => {});
  };

  const send = async () => {
    const content = text.trim();
    if (!content || !active || sending) return;
    setError("");
    setText("");
    await sendContent(content);
  };

  const onType = (v: string) => {
    setText(v);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && active) {
      ws.send(JSON.stringify({ type: "typing", conversation_id: active.id }));
    }
  };

  // Per-user pin/mute via POST /conversations/{id}/flags (member-gated).
  const setFlags = async (c: any, flags: { pinned?: boolean; muted?: boolean; archived?: boolean }) => {
    setFlagBusy(true);
    try {
      const r = await api.post(`/conversations/${c.id}/flags`, flags);
      animateNextLayout();
      setConvs(prev => prev.map(x => x.id === c.id ? { ...x, pinned: r.data.pinned, muted: r.data.muted, archived: r.data.archived } : x));
      setDetails((prev: any) => prev && prev.id === c.id ? { ...prev, pinned: r.data.pinned, muted: r.data.muted, archived: r.data.archived } : prev);
    } catch {
      setError("Couldn't update this conversation. Try again.");
    } finally { setFlagBusy(false); }
  };

  const markAllRead = async () => {
    const unread = convs.filter(c => (c.unread_count || 0) > 0);
    setInboxMenuOpen(false);
    if (!unread.length) return;
    setConvs(prev => prev.map(c => ({ ...c, unread_count: 0 })));
    try {
      await Promise.all(unread.map(c => api.post(`/conversations/${c.id}/read`)));
    } catch {
      setError("Couldn't mark every conversation as read. Try again.");
      loadDirectory(true);
    }
  };

  const isPatient = user?.role === "patient";
  const isSponsorLike = user?.role === "sponsor" || user?.role === "cro";
  const isClinical = user?.role === "pi"
    || user?.role === "crc"
    || user?.role === "smo"
    || user?.role === "site";
  const clinicalNavRole = user?.role === "crc"
    ? "crc"
    : user?.role === "site"
      ? "site"
      : user?.role === "smo"
        ? "smo"
        : "pi";
  const inboxRole = user?.role === "cro" ? "CRO" : (user?.role || "Secure inbox").toUpperCase();
  const inboxEyebrow = user?.organization
    ? `${inboxRole} · ${user.organization.toUpperCase()}`
    : inboxRole;

  const unarchivedConvs = convs.filter(c => !c.archived);
  const archivedConvs = convs.filter(c => c.archived);
  const unreadTotal = unarchivedConvs.reduce((sum, c) => sum + (c.unread_count || 0), 0);
  const visibleConvs = (filter === "archived" ? archivedConvs : unarchivedConvs)
    .filter(c => filter === "all" || filter === "archived" ? true : filter === "unread" ? (c.unread_count || 0) > 0 : !!c.is_group)
    .slice()
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned));

  const connectionBanner = connection === "offline" ? (
    <View testID="chat-connection-banner" style={s.offlineBanner} accessibilityLiveRegion="polite">
      <WifiOff size={13} color={colors.warning} />
      <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>
        You&apos;re offline — reconnecting… New messages will sync automatically.
      </Small>
    </View>
  ) : null;

  // ── Conversation list view ──
  if (!active) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top"]}>
        <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
        <View style={s.header}>
          <View style={{ flex: 1 }}>
            <Eyebrow color={colors.primaryFg + "A3"} numberOfLines={1}>{inboxEyebrow}</Eyebrow>
            <H1 color={colors.primaryFg} style={{ fontSize: 20 }}>Messages</H1>
          </View>
          <Pressable testID="chat-compose" onPress={() => { setComposeQuery(""); setComposeOpen(true); }} hitSlop={10} style={s.headerIconBtn} accessibilityLabel="New message">
            <SquarePen size={20} color={colors.primaryFg} />
          </Pressable>
          <Pressable testID="chat-menu" onPress={() => setInboxMenuOpen(true)} hitSlop={10} style={s.headerIconBtn} accessibilityLabel="More options">
            <MoreVertical size={20} color={colors.primaryFg} />
          </Pressable>
        </View>
        {connectionBanner}
        {!loading && !loadError && (
          <View style={s.filterRow}>
            {([
              { key: "all", label: "All", count: 0 },
              { key: "unread", label: "Unread", count: unreadTotal },
              { key: "groups", label: "Groups", count: 0 },
            ] as { key: "all" | "unread" | "groups"; label: string; count: number }[]).map(f => {
              const on = filter === f.key;
              return (
                <Pressable key={f.key} testID={`chat-filter-${f.key}`} onPress={() => { animateNextLayout(); setFilter(f.key); }} style={[s.filterChip, on && s.filterChipOn]}>
                  <Small color={on ? colors.primaryFg : colors.mutedFg} style={on ? { fontFamily: fonts.medium } : undefined}>
                    {f.label}{f.count ? ` ${f.count}` : ""}
                  </Small>
                </Pressable>
              );
            })}
          </View>
        )}
        {!loading && !loadError && (
          <Pressable testID="chat-filter-archived" onPress={() => { animateNextLayout(); setFilter(filter === "archived" ? "all" : "archived"); }} style={s.archivedRow}>
            <Archive size={17} color={colors.primary} />
            <Body weight={filter === "archived" ? "700" : "400"} style={{ flex: 1, marginLeft: 12, fontSize: 12.5 }} color={filter === "archived" ? colors.primary : colors.foreground}>Archived</Body>
            <Small color={colors.mutedFg} style={{ fontSize: 10.5 }}>{archivedConvs.length}</Small>
          </Pressable>
        )}
        {loading ? (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading your messages…</Small>
          </View>
        ) : loadError ? (
          <View style={{ padding: spacing.md }}>
            <Card>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <AlertTriangle size={18} color={colors.destructive} />
                <Body weight="600" style={{ flex: 1 }}>{loadError}</Body>
              </View>
              <Pressable testID="chat-retry" onPress={() => loadDirectory()} style={s.retryBtn}>
                <RefreshCcw size={14} color={colors.primary} />
                <Small weight="700" color={colors.primary}>Retry</Small>
              </Pressable>
            </Card>
          </View>
        ) : (
        <ScrollView contentContainerStyle={{ paddingBottom: isPatient || isSponsorLike || isClinical ? 120 : spacing.xxl }}>
          {error ? <Small color={colors.destructive} style={{ marginHorizontal: spacing.md, marginVertical: spacing.sm }}>{error}</Small> : null}
          {visibleConvs.map(c => {
            const other = c.other_participant;
            const name = c.title || other?.full_name || "Conversation";
            const isSupport = !c.is_group && other?.role === "admin";
            const lastFromSelf = !!c.last_sender_id && c.last_sender_id === userId;
            const lastSenderName = !lastFromSelf && c.is_group && c.last_sender_id
              ? stripTitle(((c.participants || []).find((p: any) => p.id === c.last_sender_id)?.full_name) || "")
              : "";
            const prefix = lastFromSelf ? "You: " : lastSenderName ? `${lastSenderName}: ` : "";
            const isUnread = (c.unread_count || 0) > 0;
            const timestamp = formatRowTimestamp(c.updated_at);
            const theme = isSupport ? SUPPORT_AVATAR_THEME : avatarTheme(c.id);
            return (
              <Pressable key={c.id} testID={`conv-${c.id}`} onPress={() => openConv(c)} onLongPress={() => setDetails(c)} style={[s.convRow, isUnread && { backgroundColor: colors.primary + "09" }]}>
                <View style={[s.listAvatar, { backgroundColor: theme.bg, borderColor: theme.ring }]}>
                  {c.is_group
                    ? <Users size={22} color={theme.fg} />
                    : isSupport
                      ? <ShieldCheck size={22} color={theme.fg} />
                      : <Body weight="700" color={theme.fg}>{other?.avatar_initials || "?"}</Body>}
                </View>
                <View style={s.convRowContent}>
                  <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
                    <Body weight={isUnread ? "700" : "600"} style={{ flex: 1, fontSize: 13.5 }} numberOfLines={1}>{name}</Body>
                    {timestamp ? <Small color={isUnread ? colors.primary : colors.mutedFg} style={{ fontSize: 9.5, fontFamily: isUnread ? fonts.bold : fonts.regular }}>{timestamp}</Small> : null}
                  </View>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 4, flex: 1, minWidth: 0 }}>
                      {lastFromSelf && !!c.last_message && <CheckCheck size={12} color={c.last_read ? colors.accent : colors.mutedFg + "99"} />}
                      <Small numberOfLines={1} color={isUnread ? colors.foreground + "BF" : colors.mutedFg} style={{ flexShrink: 1, fontSize: 10.5, fontFamily: isUnread ? fonts.medium : fonts.regular }}>
                        {prefix ? <Small color={colors.foreground + "A6"} style={{ fontFamily: fonts.medium }}>{prefix}</Small> : null}
                        {c.last_message || "Start chatting"}
                      </Small>
                    </View>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      {c.pinned ? <Pin size={11} color={colors.mutedFg + "99"} style={{ transform: [{ rotate: "-45deg" }] }} /> : null}
                      {c.muted ? <BellOff size={11} color={colors.mutedFg} /> : null}
                      {c.unread_count > 0 && (
                        <View style={[s.badge, c.muted && { backgroundColor: colors.mutedFg }]}>
                          <Small color={colors.primaryFg} style={{ fontSize: 8.5, fontFamily: fonts.bold }}>{c.unread_count}</Small>
                        </View>
                      )}
                    </View>
                  </View>
                </View>
              </Pressable>
            );
          })}
          {visibleConvs.length === 0 && (
            <View style={{ alignItems: "center", justifyContent: "center", paddingVertical: 80, gap: 12, paddingHorizontal: spacing.lg }}>
              <Search size={40} color={colors.mutedFg + "66"} />
              <Small color={colors.mutedFg}>No chats found</Small>
            </View>
          )}
        </ScrollView>
        )}

        {/* Inbox overflow menu (kebab in the header) */}
        <Modal visible={inboxMenuOpen} transparent animationType="fade" onRequestClose={() => setInboxMenuOpen(false)}>
          <Pressable style={s.menuOverlay} onPress={() => setInboxMenuOpen(false)}>
            <View style={s.menuCard}>
              <Pressable
                testID="chat-menu-mark-read"
                onPress={markAllRead}
                style={s.menuRow}
              >
                <Body weight="600">Mark all as read</Body>
              </Pressable>
              <Pressable
                testID="chat-menu-notifications"
                onPress={() => { setInboxMenuOpen(false); Alert.alert("Coming soon", "Notification settings will be available in a future update."); }}
                style={[s.menuRow, { borderTopWidth: 1, borderTopColor: colors.border }]}
              >
                <Body weight="600">Notification settings</Body>
              </Pressable>
              <Pressable
                testID="chat-menu-archived"
                onPress={() => { setInboxMenuOpen(false); animateNextLayout(); setFilter(filter === "archived" ? "all" : "archived"); }}
                style={[s.menuRow, { borderTopWidth: 1, borderTopColor: colors.border }]}
              >
                <Body weight="600">Archived chats</Body>
              </Pressable>
            </View>
          </Pressable>
        </Modal>

        {/* Conversation / group details + pin/mute (long-press a conversation) */}
        <Modal visible={!!details} transparent animationType="slide" onRequestClose={() => setDetails(null)}>
          <View style={s.modalOverlay}>
            <Pressable style={{ flex: 1 }} onPress={() => setDetails(null)} />
            {details && (
              <View style={s.modalSheet}>
                <View style={{ flexDirection: "row", alignItems: "center", marginBottom: spacing.sm }}>
                  <Body weight="700" style={{ flex: 1 }}>{details.title || details.other_participant?.full_name || "Conversation"}</Body>
                  <Pressable onPress={() => setDetails(null)} hitSlop={10}><X size={18} color={colors.mutedFg} /></Pressable>
                </View>
                {details.is_group ? (
                  <>
                    <Eyebrow style={{ marginBottom: 6 }}>{(details.participants || []).length} members</Eyebrow>
                    <ScrollView style={{ maxHeight: 260 }}>
                      {(details.participants || []).map((p: any) => (
                        <View key={p.id} style={{ flexDirection: "row", alignItems: "center", paddingVertical: 8, gap: 10 }}>
                          <View style={[s.avatar, { width: 34, height: 34, borderRadius: 17 }]}><Small weight="700" color={colors.primary}>{p.avatar_initials || "?"}</Small></View>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <Body weight="600" numberOfLines={1}>{p.full_name}{p.id === userId ? " (you)" : ""}</Body>
                            <Small numberOfLines={1}>{(p.role || "").toUpperCase()}{p.organization ? ` · ${p.organization}` : ""}</Small>
                          </View>
                          {p.is_online && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success }} />}
                        </View>
                      ))}
                    </ScrollView>
                  </>
                ) : details.other_participant ? (
                  <Small style={{ marginBottom: 4 }}>
                    {(details.other_participant.role || "").toUpperCase()}
                    {details.other_participant.organization ? ` · ${details.other_participant.organization}` : ""}
                  </Small>
                ) : null}
                <View style={{ flexDirection: "row", gap: 10, marginTop: spacing.md }}>
                  <Pressable
                    testID="conv-pin-toggle"
                    disabled={flagBusy}
                    onPress={() => setFlags(details, { pinned: !details.pinned })}
                    style={[s.flagBtn, details.pinned && s.flagBtnOn]}
                  >
                    <Pin size={14} color={details.pinned ? colors.primaryFg : colors.primary} />
                    <Small weight="700" color={details.pinned ? colors.primaryFg : colors.primary}>{details.pinned ? "Unpin" : "Pin"}</Small>
                  </Pressable>
                  <Pressable
                    testID="conv-mute-toggle"
                    disabled={flagBusy}
                    onPress={() => setFlags(details, { muted: !details.muted })}
                    style={[s.flagBtn, details.muted && s.flagBtnOn]}
                  >
                    <BellOff size={14} color={details.muted ? colors.primaryFg : colors.primary} />
                    <Small weight="700" color={details.muted ? colors.primaryFg : colors.primary}>{details.muted ? "Unmute" : "Mute"}</Small>
                  </Pressable>
                </View>
                <Pressable
                  testID="conv-archive-toggle"
                  disabled={flagBusy}
                  onPress={() => setFlags(details, { archived: !details.archived })}
                  style={[s.flagBtn, { marginTop: 10 }, details.archived && s.flagBtnOn]}
                >
                  <Archive size={14} color={details.archived ? colors.primaryFg : colors.primary} />
                  <Small weight="700" color={details.archived ? colors.primaryFg : colors.primary}>{details.archived ? "Unarchive" : "Archive"}</Small>
                </Pressable>
                <Pressable onPress={() => { const c = details; setDetails(null); openConv(c); }} style={[s.flagBtn, { marginTop: 10, borderColor: colors.border }]}>
                  <Small weight="700" color={colors.foreground}>Open conversation</Small>
                </Pressable>
              </View>
            )}
          </View>
        </Modal>

        {/* Compose: searchable authorized-recipient picker, with a group-creation mode */}
        <Modal visible={composeOpen} animationType="slide" onRequestClose={() => setComposeOpen(false)}>
          <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
            <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
            <View style={s.header}>
              <Pressable testID="compose-close" onPress={() => setComposeOpen(false)} hitSlop={12}><ChevronLeft size={24} color={colors.primaryFg} /></Pressable>
              <View style={{ flex: 1, marginLeft: 16 }}>
                <Body weight="600" color={colors.primaryFg}>{groupMode ? "New group" : "New chat"}</Body>
                <Small color={colors.primaryFg + "B3"} style={{ fontSize: 12 }}>
                  {groupMode ? `${groupSelectedIds.length} selected` : `${users.length} available`}
                </Small>
              </View>
              <Pressable
                testID="compose-group-toggle"
                onPress={() => { setGroupMode(v => !v); setGroupSelectedIds([]); setGroupTitle(""); }}
                style={[s.groupToggle, groupMode && s.groupToggleOn]}
              >
                <Users size={14} color={groupMode ? colors.primary : colors.primaryFg} />
                <Small color={groupMode ? colors.primary : colors.primaryFg} style={{ fontFamily: fonts.bold }}>Group</Small>
              </Pressable>
            </View>
            <View style={{ paddingHorizontal: spacing.md, paddingTop: spacing.sm }}>
              <View style={s.composeSearch}>
                <Search size={15} color={colors.mutedFg} />
                <TextInput
                  testID="compose-search"
                  value={composeQuery}
                  onChangeText={setComposeQuery}
                  placeholder="Search your care team & contacts"
                  placeholderTextColor={colors.mutedFg + "99"}
                  style={{ flex: 1, paddingVertical: 8, color: colors.foreground, fontSize: 14 }}
                />
              </View>
            </View>
            <ScrollView style={{ flex: 1, marginTop: spacing.sm }} contentContainerStyle={{ paddingHorizontal: spacing.md, paddingBottom: spacing.md }} keyboardShouldPersistTaps="handled">
                {users
                  .filter(u => {
                    const q = composeQuery.trim().toLowerCase();
                    if (!q) return true;
                    return [u.full_name, u.role, u.organization, u.email].some(v => String(v || "").toLowerCase().includes(q));
                  })
                  .map(u => {
                    const checked = groupSelectedIds.includes(u.id);
                    return (
                      <Pressable
                        key={u.id}
                        testID={`compose-user-${u.id}`}
                        disabled={!!starting}
                        onPress={() => {
                          if (groupMode) {
                            setGroupSelectedIds(prev => checked ? prev.filter(id => id !== u.id) : [...prev, u.id]);
                            return;
                          }
                          setComposeOpen(false);
                          startWith(u.id).catch((e: any) => setError(e?.response?.data?.detail || "Couldn't start this conversation."));
                        }}
                        style={{ flexDirection: "row", alignItems: "center", paddingVertical: 10, gap: 10 }}
                      >
                        <View style={[s.avatar, { width: 36, height: 36, borderRadius: 18 }]}><Small weight="700" color={colors.primary}>{u.avatar_initials}</Small></View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <Body weight="600" numberOfLines={1}>{u.full_name}</Body>
                          <Small numberOfLines={1}>{u.role.toUpperCase()} · {u.organization || u.email}</Small>
                        </View>
                        {groupMode
                          ? <View style={[s.checkbox, checked && s.checkboxOn]}>{checked && <Check size={13} color={colors.primaryFg} />}</View>
                          : (u.is_online && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success }} />)}
                      </Pressable>
                    );
                  })}
                {users.length === 0 && (
                  <Small color={colors.mutedFg} style={{ paddingVertical: spacing.md, textAlign: "center" }}>
                    No authorized contacts are available for your account.
                  </Small>
                )}
            </ScrollView>
            {groupMode && groupSelectedIds.length > 0 && (
              <View style={{ padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card }}>
                  <TextInput
                    testID="compose-group-title"
                    value={groupTitle}
                    onChangeText={setGroupTitle}
                    placeholder="Channel name (e.g. Apollo Mumbai — Site Team)"
                    placeholderTextColor={colors.mutedFg + "99"}
                    style={s.composeSearch}
                  />
                  <Pressable
                    testID="compose-create-group"
                    disabled={creatingGroup || !groupTitle.trim()}
                    onPress={async () => {
                      setCreatingGroup(true);
                      try {
                        const r = await api.post("/conversations", { participant_ids: groupSelectedIds, is_group: true, title: groupTitle.trim() });
                        setComposeOpen(false);
                        setGroupMode(false); setGroupSelectedIds([]); setGroupTitle("");
                        const refresh = await api.get("/conversations");
                        setConvs(refresh.data);
                        const enriched = refresh.data.find((x: any) => x.id === r.data.id) || r.data;
                        await openConv(enriched);
                      } catch (e: any) {
                        setError(e?.response?.data?.detail || "Couldn't create this group.");
                      } finally {
                        setCreatingGroup(false);
                      }
                    }}
                    style={[s.flagBtn, { marginTop: 10 }, (creatingGroup || !groupTitle.trim()) && { opacity: 0.5 }]}
                  >
                    {creatingGroup ? <ActivityIndicator size="small" color={colors.primary} /> : <Small weight="700" color={colors.primary}>Create group ({groupSelectedIds.length} member{groupSelectedIds.length === 1 ? "" : "s"})</Small>}
                  </Pressable>
              </View>
            )}
          </SafeAreaView>
        </Modal>

        {/* Role tab bar — inbox only; threads hide it, matching the reference. */}
        {isPatient && <PatientBottomNav active="messages" />}
        {isSponsorLike && <SponsorBottomNav active="chat" unreadMessages={unreadTotal} />}
        {isClinical && (
          <PiBottomNav
            active="messages"
            calendarRole={clinicalNavRole}
            role={clinicalNavRole}
          />
        )}
      </SafeAreaView>
    );
  }

  // ── Active conversation view ──
  const other = active.other_participant;
  const memberCount = (active.participants || []).length;
  const onlineCount = (active.participants || []).filter((p: any) => p.is_online).length;
  const isThreadSupport = !active.is_group && other?.role === "admin";
  const joinedNames = active.is_group
    ? (active.participants || []).map((p: any) => (p.id === userId ? "You" : stripTitle(p.full_name || ""))).join(", ")
    : "";
  const groupSubline = joinedNames && joinedNames.length <= 60 ? joinedNames : `${memberCount} members`;
  const subline = connection === "offline"
    ? "reconnecting…"
    : typing
      ? "typing…"
      : active.is_group
        ? `${groupSubline}${onlineCount ? ` · ${onlineCount} online` : ""}`
        : other?.is_online ? "online" : "offline";
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <View style={s.header}>
          <Pressable onPress={() => setActive(null)} hitSlop={12}><ChevronLeft size={24} color={colors.primaryFg} /></Pressable>
          <Pressable testID="chat-header-info" onPress={() => router.push({ pathname: "/(app)/conversation/[id]", params: { id: active.id } })} style={{ flexDirection: "row", alignItems: "center", flex: 1 }}>
            <View style={s.threadAvatar}>
              {active.is_group
                ? <Users size={20} color={colors.primaryFg} />
                : isThreadSupport
                  ? <ShieldCheck size={20} color={colors.primaryFg} />
                  : <Body weight="700" color={colors.primaryFg}>{other?.avatar_initials || "?"}</Body>}
            </View>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Body weight="600" color={colors.primaryFg} numberOfLines={1}>{active.title || other?.full_name || "Conversation"}</Body>
              <Small color={colors.primaryFg + "B3"} numberOfLines={1} style={{ fontSize: 12 }}>{subline}</Small>
            </View>
          </Pressable>
          <Pressable testID="chat-thread-menu" onPress={() => setThreadMenuOpen(true)} hitSlop={10} style={{ marginLeft: 8 }}>
            <MoreVertical size={20} color={colors.primaryFg} />
          </Pressable>
        </View>
        {connectionBanner}

        {threadLoading ? (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading conversation…</Small>
          </View>
        ) : threadError ? (
          <View style={{ flex: 1, padding: spacing.md }}>
            <Card>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <AlertTriangle size={18} color={colors.destructive} />
                <Body weight="600" style={{ flex: 1 }}>{threadError}</Body>
              </View>
              <Pressable testID="thread-retry" onPress={() => openConv(active)} style={s.retryBtn}>
                <RefreshCcw size={14} color={colors.primary} />
                <Small weight="700" color={colors.primary}>Retry</Small>
              </Pressable>
            </Card>
          </View>
        ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m) => m.id}
          contentContainerStyle={{ padding: spacing.md, gap: 8 }}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          ListHeaderComponent={active.is_group ? (
            <View style={{ alignItems: "center", paddingVertical: 8 }}>
              <View style={s.encryptedPill}>
                <Small color={colors.mutedFg} style={{ fontSize: 11, textAlign: "center" }}>
                  Encrypted in transit and at rest · shared with all {memberCount} organization members.
                </Small>
              </View>
            </View>
          ) : null}
          ListEmptyComponent={
            <View style={{ alignItems: "center", paddingTop: spacing.xl }}>
              <Small color={colors.mutedFg}>No messages yet — say hello.</Small>
            </View>
          }
          renderItem={({ item }) => {
            const mine = item.sender_id === user?.id;
            const senderName = active.is_group && !mine
              ? (active.participants || []).find((p: any) => p.id === item.sender_id)?.full_name
              : null;
            const isRead = mine && item.read_by && Object.keys(item.read_by).some((id: string) => id !== userId);
            const bubble = (
              <View style={[s.bubble, mine ? s.bubbleMine : s.bubbleOther, item.failed && s.bubbleFailed]}>
                {senderName ? <Small style={{ marginBottom: 2, fontFamily: fonts.bold, fontSize: 12 }} color={senderColor(senderName)}>{senderName}</Small> : null}
                {item.attachment ? (
                  item.type === "voice" ? (
                    <VoiceBubble fileId={item.attachment.file_id} duration={item.attachment.duration} mine={mine} />
                  ) : item.type === "image" ? (
                    <Pressable
                      testID={`attachment-${item.id}`}
                      onPress={() => downloadFile({ id: item.attachment.file_id, name: item.attachment.name, content_type: item.attachment.content_type }).catch(() => Alert.alert("Couldn't open file", "Try again in a moment."))}
                      style={[s.imageBox, { backgroundColor: mine ? colors.overlay10 : colors.surface }]}
                    >
                      <CameraIcon size={32} color={mine ? colors.primaryFg + "99" : colors.mutedFg + "B3"} />
                      <Small color={mine ? colors.primaryFg + "B3" : colors.mutedFg} style={{ fontSize: 12 }} numberOfLines={1}>{item.attachment.name}</Small>
                    </Pressable>
                  ) : (
                    <Pressable
                      testID={`attachment-${item.id}`}
                      onPress={() => downloadFile({ id: item.attachment.file_id, name: item.attachment.name, content_type: item.attachment.content_type }).catch(() => Alert.alert("Couldn't open file", "Try again in a moment."))}
                      style={[s.docBox, { backgroundColor: mine ? colors.overlay20 : colors.surface }]}
                    >
                      <FileText size={28} color={mine ? colors.primaryFg + "CC" : colors.destructive} />
                      <View style={{ minWidth: 0, flexShrink: 1 }}>
                        <Small numberOfLines={1} color={mine ? colors.primaryFg : colors.foreground} style={{ fontSize: 12, fontFamily: fonts.semibold }}>{item.attachment.name}</Small>
                        {item.attachment.size ? (
                          <Small color={mine ? colors.primaryFg + "99" : colors.mutedFg} style={{ fontSize: 10 }}>{formatSize(item.attachment.size)} · Tap to open</Small>
                        ) : null}
                      </View>
                    </Pressable>
                  )
                ) : (
                  <Body color={mine ? colors.primaryFg : colors.foreground}>{item.content}</Body>
                )}
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 4, marginTop: 3 }}>
                  <Small color={mine ? colors.primaryFg + "B3" : colors.mutedFg + "B3"} style={{ fontSize: 10 }}>
                    {item.pending
                      ? "Sending…"
                      : item.failed
                        ? "Not sent"
                        : new Date(item.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                  </Small>
                  {mine && !item.pending && !item.failed && (
                    isRead
                      ? <CheckCheck size={13} color={colors.accent} />
                      : <Check size={13} color={colors.primaryFg + "B3"} />
                  )}
                </View>
              </View>
            );
            if (!item.failed) return bubble;
            return (
              <Pressable
                testID={`retry-msg-${item.id}`}
                onPress={() => sendContent(item.content, item.id)}
                onLongPress={() => setMessages(prev => prev.filter(m => m.id !== item.id))}
              >
                {bubble}
                <Small color={colors.destructive} style={{ alignSelf: "flex-end", marginTop: 2, fontSize: 10 }}>
                  Tap to retry · long-press to discard
                </Small>
              </Pressable>
            );
          }}
        />
        )}

        {emojiRowOpen && (
          <View style={s.emojiRow}>
            {QUICK_EMOJI.map((e) => (
              <Pressable key={e} testID={`emoji-${e}`} onPress={() => setText((t) => t + e)} style={{ padding: 6 }}>
                <Body style={{ fontSize: 22 }}>{e}</Body>
              </Pressable>
            ))}
          </View>
        )}
        {recording && (
          <View style={s.recordingBar}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <View style={{ width: 12, height: 12, borderRadius: 6, backgroundColor: colors.destructive }} />
              <Small color={colors.destructive} style={{ fontFamily: fonts.mono, fontSize: 13 }}>Recording… {fmtDuration(recordingTime)}</Small>
            </View>
            <Pressable testID="chat-recording-stop" onPress={stopAndSendRecording} hitSlop={10}>
              <Small color={colors.destructive} style={{ fontFamily: fonts.bold, fontSize: 13 }}>Stop</Small>
            </Pressable>
          </View>
        )}
        {attachMenuOpen && !recording && (
          <View style={s.attachRow}>
            <Pressable testID="attach-document" onPress={() => { setAttachMenuOpen(false); pickAndSendDocument(); }} style={s.attachOption}>
              <View style={[s.attachCircle, { backgroundColor: colors.destructive + "1A" }]}><FileText size={20} color={colors.destructive} /></View>
              <Small color={colors.mutedFg} style={{ fontSize: 11 }}>Document</Small>
            </Pressable>
            <Pressable testID="attach-photo" onPress={() => { setAttachMenuOpen(false); pickAndSendImage(false); }} style={s.attachOption}>
              <View style={[s.attachCircle, { backgroundColor: colors.violet + "1F" }]}><ImageIcon size={20} color={colors.violet} /></View>
              <Small color={colors.mutedFg} style={{ fontSize: 11 }}>Gallery</Small>
            </Pressable>
            <Pressable testID="attach-camera" onPress={() => { setAttachMenuOpen(false); pickAndSendImage(true); }} style={s.attachOption}>
              <View style={[s.attachCircle, { backgroundColor: colors.info + "1F" }]}><CameraIcon size={20} color={colors.info} /></View>
              <Small color={colors.mutedFg} style={{ fontSize: 11 }}>Camera</Small>
            </Pressable>
          </View>
        )}
        <Modal visible={threadMenuOpen} transparent animationType="fade" onRequestClose={() => setThreadMenuOpen(false)}>
          <Pressable style={s.menuOverlay} onPress={() => setThreadMenuOpen(false)}>
            <View style={s.menuCard}>
              <Pressable
                testID="chat-thread-menu-info"
                onPress={() => { setThreadMenuOpen(false); router.push({ pathname: "/(app)/conversation/[id]", params: { id: active.id } }); }}
                style={s.menuRow}
              >
                <Body weight="600">View channel info</Body>
              </Pressable>
              <Pressable
                testID="chat-thread-menu-search"
                onPress={() => { setThreadMenuOpen(false); Alert.alert("Coming soon", "Searching within a conversation will be available in a future update."); }}
                style={[s.menuRow, { borderTopWidth: 1, borderTopColor: colors.border }]}
              >
                <Body weight="600">Search in conversation</Body>
              </Pressable>
            </View>
          </Pressable>
        </Modal>

        <View style={s.inputBar}>
          {error ? <Small color={colors.destructive} style={s.inputError}>{error}</Small> : null}
          <View style={s.inputPill}>
            <Pressable testID="chat-emoji-toggle" onPress={() => { setAttachMenuOpen(false); setEmojiRowOpen((v) => !v); }} hitSlop={8}>
              <Smile size={20} color={colors.mutedFg} />
            </Pressable>
            <TextInput testID="chat-input" placeholder="Message" placeholderTextColor={colors.mutedFg + "99"} value={text} onChangeText={onType} style={s.textInput} multiline />
            <Pressable testID="chat-attach-toggle" onPress={() => { setEmojiRowOpen(false); setAttachMenuOpen((v) => !v); }} hitSlop={8}>
              <Paperclip size={20} color={colors.mutedFg} />
            </Pressable>
            {!text.trim() && (
              <Pressable testID="chat-camera" onPress={() => pickAndSendImage(true)} hitSlop={8}>
                <CameraIcon size={20} color={colors.mutedFg} />
              </Pressable>
            )}
          </View>
          {text.trim() ? (
            <Pressable testID="chat-send" onPress={send} disabled={sending} style={[s.sendBtn, sending && { opacity: 0.5 }]}>
              {sending ? <ActivityIndicator size="small" color={colors.primaryFg} /> : <Send size={20} color={colors.primaryFg} />}
            </Pressable>
          ) : (
            <Pressable
              testID="chat-mic"
              onPressIn={startRecording}
              onPressOut={stopAndSendRecording}
              onLongPress={cancelRecording}
              disabled={sending}
              style={[s.sendBtn, recording && { backgroundColor: colors.destructive }, sending && { opacity: 0.5 }]}
            >
              <Mic size={20} color={colors.primaryFg} />
            </Pressable>
          )}
        </View>
      </KeyboardAvoidingView>
      {isClinical && (
        <PiBottomNav
          active="messages"
          calendarRole={clinicalNavRole}
          role={clinicalNavRole}
        />
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { minHeight: 74, flexDirection: "row", alignItems: "center", paddingHorizontal: 14, paddingVertical: 9, backgroundColor: colors.primaryDeep },
  headerIconBtn: { width: 32, height: 32, alignItems: "center", justifyContent: "center", marginLeft: 2 },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  listAvatar: { width: 46, height: 46, borderRadius: 23, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  threadAvatar: { width: 40, height: 40, borderRadius: 20, marginLeft: 12, backgroundColor: colors.overlay20, borderWidth: 1, borderColor: colors.overlay25, alignItems: "center", justifyContent: "center" },
  badge: { minWidth: 18, height: 18, paddingHorizontal: 5, borderRadius: 9, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center", ...shadows.sm },
  bubble: { maxWidth: "78%", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 16 },
  bubbleMine: { alignSelf: "flex-end", backgroundColor: colors.primary, borderTopRightRadius: 4 },
  bubbleOther: { alignSelf: "flex-start", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderTopLeftRadius: 4 },
  bubbleFailed: { opacity: 0.85, borderWidth: 1, borderColor: colors.destructive },
  offlineBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 8, backgroundColor: "rgba(216,154,60,0.12)", borderBottomWidth: 1, borderBottomColor: "rgba(216,154,60,0.25)" },
  filterRow: { flexDirection: "row", alignItems: "center", gap: 9, paddingHorizontal: 14, paddingVertical: 9, backgroundColor: colors.card, borderBottomWidth: 1, borderBottomColor: colors.border },
  filterChip: { paddingHorizontal: 14, paddingVertical: 5, borderRadius: radii.pill, backgroundColor: colors.surface },
  filterChipOn: { backgroundColor: colors.primary },
  archivedRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: 16, paddingVertical: 10, backgroundColor: colors.background, borderBottomWidth: 1, borderBottomColor: colors.border },
  convRow: { flexDirection: "row", alignItems: "center", gap: 11, paddingLeft: 13, paddingRight: 11 },
  convRowContent: { flex: 1, minWidth: 0, borderBottomWidth: 1, borderBottomColor: colors.border + "B3", paddingVertical: 10 },
  encryptedPill: { maxWidth: "82%", backgroundColor: colors.card + "E6", borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, paddingHorizontal: 12, paddingVertical: 6, ...shadows.sm },
  modalOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.card, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: spacing.md, paddingBottom: spacing.xl },
  flagBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 11, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  flagBtnOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  composeSearch: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 14, paddingHorizontal: 12 },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  inputBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 8, paddingHorizontal: 10, paddingVertical: 8, backgroundColor: colors.surface },
  inputError: { width: "100%" },
  inputPill: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8, borderRadius: radii.pill, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12, minHeight: 44 },
  textInput: { flex: 1, maxHeight: 100, paddingVertical: 10, color: colors.foreground, fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  imageBox: { width: 208, height: 160, borderRadius: radii.sm, margin: 2, alignItems: "center", justifyContent: "center", gap: 4, overflow: "hidden" },
  docBox: { flexDirection: "row", alignItems: "center", gap: 12, margin: 2, borderRadius: radii.sm, paddingHorizontal: 10, paddingVertical: 8, minWidth: 160 },
  recordingBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.md, paddingVertical: 12, backgroundColor: colors.card, borderTopWidth: 1, borderTopColor: colors.border },
  emojiRow: { flexDirection: "row", flexWrap: "wrap", gap: 4, paddingHorizontal: spacing.md, paddingVertical: 8, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  attachRow: { flexDirection: "row", gap: 24, paddingHorizontal: 20, paddingVertical: 14, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  attachOption: { alignItems: "center", gap: 6 },
  attachCircle: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  groupToggle: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, height: 28, borderRadius: 999, borderWidth: 1, borderColor: colors.overlay25, backgroundColor: colors.overlay10 },
  groupToggleOn: { backgroundColor: colors.primaryFg, borderColor: colors.primaryFg },
  checkbox: { width: 22, height: 22, borderRadius: 6, borderWidth: 1.5, borderColor: colors.primary + "66", alignItems: "center", justifyContent: "center" },
  checkboxOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  menuOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.25)" },
  menuCard: { position: "absolute", top: 68, right: spacing.md, minWidth: 210, backgroundColor: colors.card, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, overflow: "hidden", ...shadows.md },
  menuRow: { paddingHorizontal: spacing.md, paddingVertical: 13 },
});
