import React from "react";
import { View, Text, Pressable, Modal, StyleSheet } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Bell, Calendar, ChevronRight, FileText, MessageCircle, X } from "lucide-react-native";
import type { LucideIcon } from "lucide-react-native";
import { colors, dawnGradient, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

export type AppNotification = {
  id: string;
  title: string;
  body: string;
  kind?: string; // 'reminder' | 'message' | 'result'
  read?: boolean;
  created_at?: string;
};

interface Props {
  notification: AppNotification | null;
  onClose: () => void;
  onMarkRead: (id: string) => void;
  onViewDetails: (n: AppNotification) => void;
}

const KIND_META: Record<string, { label: string; cta: string; icon: LucideIcon; tint: string }> = {
  reminder: { label: "Visit Reminder", cta: "View Visit Details", icon: Bell, tint: colors.accent },
  message: { label: "Message", cta: "Open Chat", icon: MessageCircle, tint: colors.violet },
  result: { label: "Lab Result", cta: "View Visit Details", icon: FileText, tint: colors.info },
};
const DEFAULT_META = { label: "Notification", cta: "View Details", icon: Bell, tint: colors.accent };

/** Dawn Rounds bottom sheet — port of the demo notification-detail-sheet. */
export function NotificationDetailSheet({ notification, onClose, onMarkRead, onViewDetails }: Props) {
  const n = notification;
  const meta = (n?.kind && KIND_META[n.kind]) || DEFAULT_META;
  const KindIcon = meta.icon;
  const received = n?.created_at
    ? new Date(n.created_at).toLocaleString("en-GB", { hour: "numeric", minute: "2-digit", hour12: true, day: "numeric", month: "short", year: "numeric" })
    : "";

  return (
    <Modal visible={!!n} transparent animationType="slide" onRequestClose={onClose}>
      {/* Overlay */}
      <Pressable testID="notif-sheet-overlay" onPress={onClose} style={s.overlay} />

      {/* Bottom sheet */}
      <View style={s.sheet}>
        <View style={s.handleRow}>
          <View style={s.handle} />
        </View>

        <View style={{ paddingHorizontal: spacing.lg, paddingBottom: spacing.xl }}>
          <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", marginBottom: spacing.md }}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={s.eyebrow}>{meta.label.toUpperCase()}</Text>
              <Text style={s.title}>{n?.title}</Text>
              {!!received && <Text style={s.time}>{received}</Text>}
            </View>
            <Pressable testID="notif-sheet-close" onPress={onClose} hitSlop={8} style={({ pressed }) => [s.closeBtn, pressed && { backgroundColor: colors.surface }]}>
              <X size={20} color={colors.mutedFg} />
            </Pressable>
          </View>

          <View style={s.divider} />

          {/* Detail card */}
          <View style={s.detailCard}>
            <View style={s.row}>
              <View style={[s.rowIcon, { backgroundColor: meta.tint + "1A" }]}>
                <KindIcon size={16} color={meta.tint} />
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={s.rowLabel}>Details</Text>
                <Text style={s.rowBody}>{n?.body}</Text>
              </View>
            </View>
            {!!received && (
              <View style={[s.row, { borderTopWidth: 1, borderTopColor: colors.border }]}>
                <View style={[s.rowIcon, { backgroundColor: colors.success + "26" }]}>
                  <Calendar size={16} color={colors.success} />
                </View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={s.rowLabel}>Received</Text>
                  <Text style={s.rowValue} numberOfLines={1}>{received}</Text>
                </View>
              </View>
            )}
          </View>

          {/* Actions */}
          <Pressable testID="notif-sheet-view" onPress={() => n && onViewDetails(n)} style={({ pressed }) => [{ transform: [{ scale: pressed ? 0.97 : 1 }] }]}>
            <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.primaryBtn}>
              <Text style={s.primaryBtnText}>{meta.cta}</Text>
              <ChevronRight size={20} color={colors.primaryFg} />
            </LinearGradient>
          </Pressable>
          <Pressable
            testID="notif-sheet-mark-read"
            onPress={() => n && onMarkRead(n.id)}
            style={({ pressed }) => [s.secondaryBtn, { transform: [{ scale: pressed ? 0.97 : 1 }] }]}
          >
            <Text style={s.secondaryBtnText}>Mark as Read</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  overlay: { ...StyleSheet.absoluteFillObject, backgroundColor: colors.foreground + "66" },
  sheet: { position: "absolute", bottom: 0, left: 0, right: 0, backgroundColor: colors.card, borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, ...shadows.md },
  handleRow: { alignItems: "center", paddingVertical: 12 },
  handle: { width: 40, height: 6, borderRadius: radii.pill, backgroundColor: colors.border },
  eyebrow: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.4, marginBottom: 2 },
  title: { color: colors.foreground, fontFamily: fonts.display, fontSize: 20, letterSpacing: -0.3 },
  time: { color: colors.mutedFg, fontFamily: fonts.mono, fontSize: 13, marginTop: 2 },
  closeBtn: { padding: 6, marginRight: -6, borderRadius: radii.pill },
  divider: { height: 1, backgroundColor: colors.border, marginBottom: spacing.md },
  detailCard: { backgroundColor: colors.surface, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, overflow: "hidden", marginBottom: spacing.lg },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 14, paddingVertical: 10 },
  rowIcon: { width: 32, height: 32, borderRadius: radii.sm, alignItems: "center", justifyContent: "center" },
  rowLabel: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11 },
  rowValue: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 14 },
  rowBody: { color: colors.foreground, fontFamily: fonts.medium, fontSize: 14, lineHeight: 20 },
  primaryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 14, borderRadius: radii.lg, ...shadows.sm },
  primaryBtnText: { color: colors.primaryFg, fontFamily: fonts.semibold, fontSize: 15 },
  secondaryBtn: { alignItems: "center", justifyContent: "center", paddingVertical: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, marginTop: 12 },
  secondaryBtnText: { color: colors.foreground + "CC", fontFamily: fonts.semibold, fontSize: 15 },
});
