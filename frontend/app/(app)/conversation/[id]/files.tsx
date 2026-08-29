import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, ActivityIndicator, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ArrowLeft, FileText, Image as ImageIcon, Mic, AlertTriangle, RefreshCcw } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { api } from "@/src/api/client";
import { downloadFile } from "@/src/lib/upload";

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

export default function ConversationFiles() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openingId, setOpeningId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const r = await api.get(`/conversations/${id}/files`);
      setRows(r.data);
    } catch {
      setError("Couldn't load shared files. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  const open = async (row: any) => {
    if (!row.file_id) return;
    setOpeningId(row.message_id);
    try {
      await downloadFile({ id: row.file_id, name: row.name, content_type: row.content_type });
    } catch {
      setError("Couldn't open this file on your device.");
    } finally {
      setOpeningId(null);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={12}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
        <View style={{ flex: 1, marginLeft: 12 }}>
          <Eyebrow color={colors.accent}>Channel</Eyebrow>
          <H1>Shared files & media</H1>
        </View>
      </View>
      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 10 }}>
          <ActivityIndicator color={colors.primary} />
          <Small color={colors.mutedFg}>Loading shared files…</Small>
        </View>
      ) : error ? (
        <View style={{ padding: spacing.md }}>
          <Card>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <AlertTriangle size={18} color={colors.destructive} />
              <Body weight="600" style={{ flex: 1 }}>{error}</Body>
            </View>
            <Pressable testID="files-retry" onPress={load} style={s.retryBtn}>
              <RefreshCcw size={14} color={colors.primary} />
              <Small weight="700" color={colors.primary}>Retry</Small>
            </Pressable>
          </Card>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
          {rows.length === 0 ? (
            <Card style={{ alignItems: "center", paddingVertical: spacing.lg }}>
              <Body weight="600">No files shared yet</Body>
              <Small style={{ marginTop: 4 }}>Photos, documents, and voice notes sent in this channel appear here.</Small>
            </Card>
          ) : rows.map((row) => {
            const Icon = iconFor(row.type);
            return (
              <Pressable key={row.message_id} testID={`file-${row.message_id}`} onPress={() => open(row)} disabled={!!openingId}>
                <Card style={{ marginBottom: spacing.sm, flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={s.iconTile}><Icon size={18} color={colors.primary} /></View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Body weight="600" numberOfLines={1}>{row.name || "Voice message"}</Body>
                    <Small>{formatSize(row.size)}{row.size ? " · " : ""}{new Date(row.created_at).toLocaleDateString()}</Small>
                  </View>
                  {openingId === row.message_id && <ActivityIndicator size="small" color={colors.primary} />}
                </Card>
              </Pressable>
            );
          })}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  iconTile: { width: 40, height: 40, borderRadius: radii.md, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
});
