// Staff SVG -> PNG in the browser, by drawing each page onto a canvas (spec 11.2 downloads).

const SVG_NS = "http://www.w3.org/2000/svg";
const TARGET_WIDTH = 1600;

/** Give the SVG an explicit pixel size (taken from its viewBox if need be) so it rasterises predictably. */
function sized(svg: string): { markup: string; width: number; height: number } {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  let width = Number.parseFloat(root.getAttribute("width") ?? "");
  let height = Number.parseFloat(root.getAttribute("height") ?? "");
  const box = (root.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  if (!(width > 0 && height > 0) && box.length === 4 && box[2] > 0 && box[3] > 0) {
    width = box[2];
    height = box[3];
  }
  if (!(width > 0 && height > 0)) {
    width = 800;
    height = 300;
  }
  const scale = TARGET_WIDTH / width;
  width = Math.round(width * scale);
  height = Math.round(height * scale);
  root.setAttribute("width", String(width));
  root.setAttribute("height", String(height));
  if (!root.getAttribute("xmlns")) root.setAttribute("xmlns", SVG_NS);
  return { markup: new XMLSerializer().serializeToString(root), width, height };
}

function load(markup: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("The staff could not be drawn as an image in this browser."));
    };
    img.src = url;
  });
}

/** All pages stacked top to bottom on a white background. */
export async function svgPagesToPng(pages: readonly string[]): Promise<Blob> {
  if (pages.length === 0) throw new Error("There is no staff to save.");
  const parts = pages.map(sized);
  const images = await Promise.all(parts.map((p) => load(p.markup)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(...parts.map((p) => p.width));
  canvas.height = parts.reduce((sum, p) => sum + p.height, 0);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw images.");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  let y = 0;
  images.forEach((img, i) => {
    ctx.drawImage(img, 0, y, parts[i].width, parts[i].height);
    y += parts[i].height;
  });
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The image could not be made."))), "image/png");
  });
}
