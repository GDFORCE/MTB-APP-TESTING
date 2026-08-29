import React from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Camera, Image as ImageIcon, Trash2, X } from "lucide-react-native";
import { colors, fonts, shadows } from "@/src/theme/tokens";

type AvatarPickerSheetProps = {
  visible: boolean;
  onClose: () => void;
  onTakePhoto: () => void;
  onChooseFromGallery: () => void;
  onRemove?: () => void;
  hasPhoto?: boolean;
};

export function AvatarPickerSheet({
  visible,
  onClose,
  onTakePhoto,
  onChooseFromGallery,
  onRemove,
  hasPhoto,
}: AvatarPickerSheetProps) {
  const options = [
    { key: "camera", label: "Take Photo", icon: Camera, onPress: onTakePhoto },
    { key: "gallery", label: "Choose from Gallery", icon: ImageIcon, onPress: onChooseFromGallery },
  ];
  const canRemove = !!(hasPhoto && onRemove);

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={s.overlay}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
        <View style={s.sheet}>
          <View style={s.handle} />
          <View style={s.headerRow}>
            <Pressable accessibilityLabel="Close" onPress={onClose} hitSlop={10} style={s.closeBtn}>
              <X size={17} color={colors.mutedFg} />
            </Pressable>
            <Text style={s.title}>Profile picture</Text>
            <Pressable
              accessibilityLabel="Remove photo"
              accessibilityState={{ disabled: !canRemove }}
              disabled={!canRemove}
              onPress={onRemove}
              hitSlop={10}
              style={[s.closeBtn, !canRemove && s.trashBtnDisabled]}
            >
              <Trash2 size={16} color={canRemove ? colors.destructive : colors.mutedFg} />
            </Pressable>
          </View>
          <View style={s.optionsList}>
            {options.map((opt, index) => {
              const Icon = opt.icon;
              return (
                <Pressable
                  key={opt.key}
                  accessibilityRole="button"
                  onPress={opt.onPress}
                  style={({ pressed }) => [s.optionRow, index > 0 && s.optionBorder, pressed && s.optionPressed]}
                >
                  <View style={s.optionIcon}>
                    <Icon size={17} color={colors.primary} />
                  </View>
                  <Text style={s.optionLabel}>{opt.label}</Text>
                </Pressable>
              );
            })}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const s = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 28,
    ...shadows.md,
  },
  handle: { alignSelf: "center", width: 36, height: 4, borderRadius: 2, backgroundColor: colors.border, marginBottom: 14 },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  title: { flex: 1, textAlign: "center", color: colors.foreground, fontFamily: fonts.bold, fontSize: 15 },
  closeBtn: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  trashBtnDisabled: { opacity: 0.4 },
  optionsList: { borderRadius: 18, borderWidth: 1, borderColor: colors.border, overflow: "hidden", backgroundColor: colors.card },
  optionRow: { minHeight: 54, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", gap: 12 },
  optionBorder: { borderTopWidth: 1, borderTopColor: colors.border },
  optionPressed: { backgroundColor: colors.surface },
  optionIcon: {
    width: 34,
    height: 34,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "rgba(166,33,63,0.10)",
  },
  optionLabel: { flex: 1, color: colors.foreground, fontFamily: fonts.medium, fontSize: 13.5 },
});
