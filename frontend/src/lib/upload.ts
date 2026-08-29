// Shared file-upload / authenticated-download helpers for the file-storage API
// (POST /api/files, GET /api/files/{id}). The local backend streams file bytes
// WITH the Authorization header, so a raw <Image src=url> won't render — images
// must be fetched through the authed api client and turned into an object URL
// (web) or a base64 data URI (native). Downloads work the same way.
import { Platform, Linking } from "react-native";
import { api } from "@/src/api/client";

export type PickedAsset = { uri: string; name: string; mimeType?: string; file?: any };
export type UploadedFile = { id: string; name: string; size: number; content_type: string; url: string };
export type UploadScope = {
  scopeType?: "user" | "trial" | "ticket" | "conversation";
  scopeId?: string;
  // Explicit bearer token — used when uploading right after account creation,
  // before the session is persisted to the token store (deferred register doc).
  token?: string;
};

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  const len = bytes.length;
  let out = "";
  let i = 0;
  for (; i + 2 < len; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
  }
  const rem = len - i;
  if (rem === 1) {
    const n = bytes[i] << 16;
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + "==";
  } else if (rem === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + "=";
  }
  return out;
}

async function buildForm(asset: PickedAsset): Promise<FormData> {
  const form = new FormData();
  const type = asset.mimeType || "application/octet-stream";
  if (Platform.OS === "web") {
    // Browsers require a real Blob/File for multipart. Prefer the picker's File;
    // otherwise fetch the (blob:/data:) uri into a Blob.
    let file: Blob | undefined = asset.file as File | undefined;
    if (!file) file = await (await fetch(asset.uri)).blob();
    form.append("file", file, asset.name);
  } else {
    form.append("file", { uri: asset.uri, name: asset.name, type } as any);
  }
  return form;
}

export async function uploadFile(asset: PickedAsset, scope: UploadScope = {}): Promise<UploadedFile> {
  const form = await buildForm(asset);
  form.append("scope_type", scope.scopeType || "user");
  if (scope.scopeId) form.append("scope_id", scope.scopeId);
  const headers: Record<string, string> = { "Content-Type": "multipart/form-data" };
  if (scope.token) headers.Authorization = `Bearer ${scope.token}`;
  const r = await api.post("/files", form, { headers, timeout: 60000 });
  return r.data as UploadedFile;
}

// Fetch an uploaded file through the authed api client and return a URI that an
// <Image>/link can render: object URL on web, base64 data URI on native.
export async function fetchFileUri(fileId: string): Promise<string> {
  const path = `/files/${fileId}`;
  if (Platform.OS === "web") {
    const r = await api.get(path, { responseType: "blob" });
    return URL.createObjectURL(r.data as Blob);
  }
  const r = await api.get(path, { responseType: "arraybuffer" });
  const ct = (r.headers as any)?.["content-type"] || "image/jpeg";
  return `data:${ct};base64,${arrayBufferToBase64(r.data as ArrayBuffer)}`;
}

// Download / open an authed file. Web triggers a browser download; native opens
// the bytes via a data URI (best effort — throws if the platform can't open it).
export async function downloadFile(file: { id: string; name: string; content_type?: string }): Promise<void> {
  const path = `/files/${file.id}`;
  if (Platform.OS === "web") {
    const r = await api.get(path, { responseType: "blob" });
    const url = URL.createObjectURL(r.data as Blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    return;
  }
  const r = await api.get(path, { responseType: "arraybuffer" });
  const ct = (r.headers as any)?.["content-type"] || file.content_type || "application/octet-stream";
  const dataUri = `data:${ct};base64,${arrayBufferToBase64(r.data as ArrayBuffer)}`;
  const ok = await Linking.canOpenURL(dataUri).catch(() => false);
  if (!ok) throw new Error("This file can't be opened on your device.");
  await Linking.openURL(dataUri);
}

// ── Deferred registration verification-doc holder ──────────────────────────
// Registration is fully pre-auth (register → verify-phone → [verify-email] →
// security-questions → set-password), but POST /files needs a token. So the
// doc is SELECTED during
// registration and stashed here (module-scope survives the multi-screen flow),
// then uploaded from set-password right after /auth/register/complete returns a
// token. Cleared once consumed.
let pendingVerificationDoc: PickedAsset | null = null;
export function setPendingVerificationDoc(a: PickedAsset | null) { pendingVerificationDoc = a; }
export function peekPendingVerificationDoc(): PickedAsset | null { return pendingVerificationDoc; }
export function takePendingVerificationDoc(): PickedAsset | null {
  const a = pendingVerificationDoc;
  pendingVerificationDoc = null;
  return a;
}
