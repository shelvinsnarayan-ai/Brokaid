/** R1.8 — claim uploads: images are compressed client-side (which also drops EXIF/GPS) unless the client keeps location. */
const API = import.meta.env.VITE_API_BASE || "";
export const MAX_BYTES = 10 * 1024 * 1024;

async function compress(file: File): Promise<Blob> {
  if (!file.type.startsWith("image/") || file.type === "image/heic" || file.type === "image/heif") return file;
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return new Promise((res) => canvas.toBlob((b) => res(b || file), "image/jpeg", 0.8));
}

export async function uploadClaimFile(base: string, file: File, kind: string, keepLocation: boolean, extra: Record<string, string> = {}) {
  const blob = keepLocation ? file : await compress(file);
  if (blob.size > MAX_BYTES) throw new Error("That file is over 10 MB. Try a smaller photo or PDF.");
  const name = keepLocation || blob === file ? file.name : file.name.replace(/\.\w+$/, "") + ".jpg";
  const fd = new FormData();
  fd.append("file", blob, name);
  fd.append("kind", kind);
  fd.append("keep_location", keepLocation ? "true" : "false");
  Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  const r = await fetch(`${API}${base}/files`, { method: "POST", body: fd, credentials: "include" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : r.status === 403 ? "Your role can't add files to claims." : "Upload failed.");
  return j.file;
}
