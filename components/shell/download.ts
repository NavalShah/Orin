// Saving files from the browser. Asset URLs live on the API origin, where the `download`
// attribute is ignored, so assets are fetched and saved as Blobs.
import { api, type Asset } from "@/lib/api";

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const saveText = (text: string, filename: string, type = "text/plain"): void =>
  saveBlob(new Blob([text], { type: `${type};charset=utf-8` }), filename);

/** Fetch a signed asset URL and save it under the given name. Throws ApiError on failure. */
export async function saveUrl(url: string, filename: string, type?: string): Promise<void> {
  const data = await api.assetBytes({ url });
  saveBlob(new Blob([data], type ? { type } : undefined), filename);
}

export const saveAsset = (asset: Pick<Asset, "url" | "filename" | "media_type">): Promise<void> =>
  saveUrl(asset.url, asset.filename, asset.media_type);
