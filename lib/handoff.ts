// Cross-part handoffs (spec 3.5). They are plain URLs, so they survive a reload and can
// be opened in a new tab.
//   Chat      -> Workspace : "Open in Workspace" on a generated track
//   Chat      -> Notation  : "Transcribe this" on a generated track
//   Notation  -> Workspace : the notation becomes a piano-roll clip (microtones kept as detune)
//   Workspace -> Notation  : bounce a selection, upload it, then transcribe
//   Notation  -> Chat      : "Ask Orin about this loss report"

export const handoff = {
  workspaceWithAsset: (assetId: string) => `/workspace?import_asset=${encodeURIComponent(assetId)}`,
  workspaceWithConversion: (conversionId: string) =>
    `/workspace?import_conversion=${encodeURIComponent(conversionId)}`,
  studioWithAsset: (assetId: string) => `/studio?asset=${encodeURIComponent(assetId)}`,
  studioWithConversion: (conversionId: string) => `/studio?conversion=${encodeURIComponent(conversionId)}`,
  chatAboutLoss: (conversionId: string, itemIndexes: number[] = []) =>
    `/chat?conversion=${encodeURIComponent(conversionId)}` + (itemIndexes.length ? `&items=${itemIndexes.join(",")}` : ""),
  chatWithAsset: (assetId: string) => `/chat?asset=${encodeURIComponent(assetId)}`,
};

export type Handoff = {
  importAsset?: string;
  importConversion?: string;
  asset?: string;
  conversion?: string;
  items: number[];
};

type Params = { get(name: string): string | null };

export function readHandoff(params: Params): Handoff {
  const items = (params.get("items") ?? "")
    .split(",")
    .map((s) => Number.parseInt(s, 10))
    .filter((n) => Number.isInteger(n) && n >= 0);
  return {
    importAsset: params.get("import_asset") ?? undefined,
    importConversion: params.get("import_conversion") ?? undefined,
    asset: params.get("asset") ?? undefined,
    conversion: params.get("conversion") ?? undefined,
    items,
  };
}
