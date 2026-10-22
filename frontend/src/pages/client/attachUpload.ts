/** R3.5 — client-side checks (the server re-checks by file bytes) and one-file upload with progress. */
export interface Limits { max_files: number; photo_bytes: number; video_bytes?: number; pdf_bytes?: number; message_bytes: number; videos_enabled: boolean; documents_enabled?: boolean }
export interface Item {
  key: string; file: File; kind: "photo" | "video" | "document"; url: string; label?: string; status: "uploading" | "done" | "error"; progress: number;
  error?: string; file_id?: string; has_preview?: boolean; tries: number; xhr?: XMLHttpRequest; retryable?: boolean;
}

const PHOTO = /\.(jpe?g|png|webp|heic|heif)$/i;
const VIDEO = /\.(mp4|mov|m4v)$/i;
const MB = 1024 * 1024;

export function kindOf(f: File): "photo" | "video" | "document" | null {
  if (f.type === "application/pdf" || /\.pdf$/i.test(f.name)) return "document";
  if (/^image\/(jpeg|png|webp|heic|heif)$/.test(f.type) || (!f.type && PHOTO.test(f.name)) || PHOTO.test(f.name)) return "photo";
  if (/^video\/(mp4|quicktime)$/.test(f.type) || VIDEO.test(f.name)) return "video";
  return null;
}

export function check(files: File[], items: Item[], lim: Limits): { ok: File[]; errors: string[] } {
  const ok: File[] = []; const errors: string[] = [];
  let count = items.length; let total = items.reduce((s, i) => s + i.file.size, 0);
  for (const f of files) {
    const k = kindOf(f);
    if (!k || (k === "document" && !lim.documents_enabled)) {
      errors.push(`${f.name}: can't be attached. ${lim.documents_enabled ? "Attach a photo (JPEG, PNG, WebP, HEIC) or a PDF." : "Send a photo (JPEG, PNG, WebP, HEIC) or a video (MP4, MOV)."}`); continue;
    }
    if (k === "video" && !lim.videos_enabled) { errors.push(`${f.name}: ${lim.documents_enabled ? "videos can't be attached to broker replies yet." : "video uploads are switched off right now. Send photos instead."}`); continue; }
    const max = (k === "video" ? lim.video_bytes : k === "document" ? lim.pdf_bytes : lim.photo_bytes) || 0;
    if (f.size > max) { errors.push(`${f.name}: too large. ${k === "video" ? "Videos" : k === "document" ? "PDFs" : "Photos"} can be up to ${max / MB} MB.`); continue; }
    if (count >= lim.max_files) { errors.push(`You can attach up to ${lim.max_files} files to one message.`); break; }
    if (total + f.size > lim.message_bytes) { errors.push(`${f.name}: attachments on one message can add up to ${lim.message_bytes / MB} MB.`); continue; }
    ok.push(f); count += 1; total += f.size;
  }
  return { ok, errors };
}

export function upload(url: string, file: File, msgId: string, onProgress: (p: number) => void, label?: string): { xhr: XMLHttpRequest; done: Promise<any> } {
  const xhr = new XMLHttpRequest();
  const done = new Promise<any>((resolve, reject) => {
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => {
      let body: any = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status === 200) return resolve(body.file);
      const d = body?.detail;
      reject({ status: xhr.status, message: d?.detail || (typeof d === "string" ? d : "Upload failed. Please try again.") });
    };
    xhr.onerror = () => reject({ status: 0, message: "Upload failed (connection). Please try again." });
    xhr.onabort = () => reject({ status: -1, message: "Cancelled" });
    const fd = new FormData();
    fd.append("msg_id", msgId);
    if (label) fd.append("label", label);
    fd.append("file", file, file.name);
    xhr.send(fd);
  });
  return { xhr, done };
}
