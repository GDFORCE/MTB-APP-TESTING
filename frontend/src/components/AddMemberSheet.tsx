import React, { useEffect, useState } from "react";
import { View, Modal, Pressable, TextInput, ScrollView, ActivityIndicator, StyleSheet } from "react-native";
import { X, Search, Check } from "lucide-react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { Body, Small } from "@/src/components/ui";
import { api } from "@/src/api/client";

type AddMemberSheetProps = {
  visible: boolean;
  onClose: () => void;
  conversationId: string;
  existingIds: string[];
  autoFocusSearch?: boolean;
  onMemberAdded: (updatedConversation: any) => void;
};

export function AddMemberSheet({ visible, onClose, conversationId, existingIds, autoFocusSearch, onMemberAdded }: AddMemberSheetProps) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [addingId, setAddingId] = useState<string | null>(null);
  const [addedIds, setAddedIds] = useState<string[]>([]);

  useEffect(() => {
    if (!visible) return;
    setQuery(""); setAddedIds([]); setLoadError("");
    setLoading(true);
    api.get("/messaging/recipients")
      .then((r) => setCandidates(r.data))
      .catch(() => setLoadError("Couldn't load your contacts. Check your connection and retry."))
      .finally(() => setLoading(false));
  }, [visible]);

  const addMember = async (userId: string) => {
    setAddingId(userId);
    try {
      const r = await api.post(`/conversations/${conversationId}/members`, { user_ids: [userId] });
      setAddedIds((prev) => [...prev, userId]);
      onMemberAdded(r.data);
    } catch {
      setLoadError("Couldn't add that person. Try again.");
    } finally {
      setAddingId(null);
    }
  };

  const q = query.trim().toLowerCase();
  const visible_candidates = candidates
    .filter((u) => !existingIds.includes(u.id) && !addedIds.includes(u.id))
    .filter((u) => !q || [u.full_name, u.role, u.organization, u.email].some((v) => String(v || "").toLowerCase().includes(q)));

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={s.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={s.sheet}>
          <View style={{ flexDirection: "row", alignItems: "center", marginBottom: spacing.sm }}>
            <Body weight="700" style={{ flex: 1 }}>Add member</Body>
            <Pressable testID="add-member-close" onPress={onClose} hitSlop={10}><X size={18} color={colors.mutedFg} /></Pressable>
          </View>
          <View style={s.searchBox}>
            <Search size={15} color={colors.mutedFg} />
            <TextInput
              testID="add-member-search"
              autoFocus={!!autoFocusSearch}
              value={query}
              onChangeText={setQuery}
              placeholder="Search your care team & contacts"
              placeholderTextColor={colors.mutedFg + "99"}
              style={{ flex: 1, paddingVertical: 8, color: colors.foreground, fontSize: 14 }}
            />
          </View>
          {loadError ? <Small color={colors.destructive} style={{ marginTop: 8 }}>{loadError}</Small> : null}
          <ScrollView style={{ maxHeight: 340, marginTop: spacing.sm }} keyboardShouldPersistTaps="handled">
            {loading ? (
              <View style={{ paddingVertical: spacing.lg, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
            ) : visible_candidates.length === 0 ? (
              <Small color={colors.mutedFg} style={{ paddingVertical: spacing.md, textAlign: "center" }}>
                No more contacts to add.
              </Small>
            ) : visible_candidates.map((u) => (
              <Pressable
                key={u.id}
                testID={`add-member-${u.id}`}
                disabled={!!addingId}
                onPress={() => addMember(u.id)}
                style={{ flexDirection: "row", alignItems: "center", paddingVertical: 10, gap: 10, opacity: addingId && addingId !== u.id ? 0.6 : 1 }}
              >
                <View style={s.avatar}><Small weight="700" color={colors.primary}>{u.avatar_initials}</Small></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Body weight="600" numberOfLines={1}>{u.full_name}</Body>
                  <Small numberOfLines={1}>{String(u.role || "").toUpperCase()} · {u.organization || u.email}</Small>
                </View>
                {addingId === u.id ? <ActivityIndicator size="small" color={colors.primary} /> : <Check size={18} color={colors.mutedFg} style={{ opacity: 0.35 }} />}
              </Pressable>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: { backgroundColor: colors.card, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: spacing.md, paddingBottom: spacing.xl },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 14, paddingHorizontal: 12 },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
});
