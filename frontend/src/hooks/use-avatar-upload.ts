import { useState } from "react";
import * as ImagePicker from "expo-image-picker";
import { api } from "@/src/api/client";
import { uploadFile, fetchFileUri } from "@/src/lib/upload";

type Options = {
  onUploaded?: (uri: string) => void | Promise<void>;
  onRemoved?: () => void | Promise<void>;
};

// Shared "change profile picture" flow: opens the AvatarPickerSheet, then
// handles camera capture / gallery pick / removal — upload, PATCH /auth/me,
// and refetching a render-ready URI. Screens own the `avatarUri` they render;
// this hook only owns the busy/error/sheet state and the picker actions.
export function useAvatarUpload(options: Options = {}) {
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarErr, setAvatarErr] = useState("");
  const [sheetOpen, setSheetOpen] = useState(false);

  const openSheet = () => { setAvatarErr(""); setSheetOpen(true); };
  const closeSheet = () => setSheetOpen(false);

  const uploadAsset = async (asset: ImagePicker.ImagePickerAsset) => {
    const name = asset.fileName || `avatar.${(asset.uri.split(".").pop() || "jpg").split("?")[0]}`;
    setAvatarBusy(true);
    setAvatarErr("");
    try {
      const uploaded = await uploadFile(
        { uri: asset.uri, name, mimeType: asset.mimeType || "image/jpeg", file: (asset as any).file },
        { scopeType: "user" },
      );
      await api.patch("/auth/me", { avatar_file_id: uploaded.id });
      const uri = await fetchFileUri(uploaded.id);
      await options.onUploaded?.(uri);
    } catch (e: any) {
      setAvatarErr(e?.response?.data?.detail || "Couldn't update your photo. Please try again.");
    } finally {
      setAvatarBusy(false);
    }
  };

  const pickFromCamera = async () => {
    closeSheet();
    setAvatarErr("");
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) { setAvatarErr("Camera access is needed to take a photo."); return; }
    const res = await ImagePicker.launchCameraAsync({ mediaTypes: ["images"], allowsEditing: true, aspect: [1, 1], quality: 0.8 });
    if (res.canceled || !res.assets?.length) return;
    await uploadAsset(res.assets[0]);
  };

  const pickFromGallery = async () => {
    closeSheet();
    setAvatarErr("");
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { setAvatarErr("Photo access is needed to change your picture."); return; }
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], allowsEditing: true, aspect: [1, 1], quality: 0.8 });
    if (res.canceled || !res.assets?.length) return;
    await uploadAsset(res.assets[0]);
  };

  const removeAvatar = async () => {
    closeSheet();
    setAvatarBusy(true);
    setAvatarErr("");
    try {
      await api.patch("/auth/me", { avatar_file_id: "" });
      await options.onRemoved?.();
    } catch (e: any) {
      setAvatarErr(e?.response?.data?.detail || "Couldn't remove your photo. Please try again.");
    } finally {
      setAvatarBusy(false);
    }
  };

  return { avatarBusy, avatarErr, sheetOpen, openSheet, closeSheet, pickFromCamera, pickFromGallery, removeAvatar };
}
