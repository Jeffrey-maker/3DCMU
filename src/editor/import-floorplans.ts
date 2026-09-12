export interface RenderedFloorPlan {
  readonly sourceFileName: string;
  readonly sourceMediaType: string;
  readonly sourcePage?: number;
  readonly suggestedLevel: string;
  readonly imageDataUrl: string;
  readonly width: number;
  readonly height: number;
}

function fileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function loadImageDimensions(src: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error("The selected image could not be decoded."));
    image.src = src;
  });
}

function guessLevel(fileName: string, fallback: number): string {
  const base = fileName.replace(/\.[^.]+$/, "");
  const explicit = base.match(/(?:floor|level|fl|f)[-_ ]?([a-z]?\d+[a-z]?)/i);
  const numericParts = [...base.matchAll(/(?:^|[-_ ])([a-z]?\d+[a-z]?)(?=$|[-_ ])/gi)];
  const match = explicit ?? numericParts.at(-1);
  return match?.[1]?.toUpperCase() ?? String(fallback);
}

async function renderImage(file: File, fallback: number): Promise<RenderedFloorPlan> {
  const imageDataUrl = await fileAsDataUrl(file);
  const dimensions = await loadImageDimensions(imageDataUrl);
  return {
    sourceFileName: file.name,
    sourceMediaType: file.type || "image/unknown",
    suggestedLevel: guessLevel(file.name, fallback),
    imageDataUrl,
    ...dimensions,
  };
}

async function renderPdf(file: File): Promise<RenderedFloorPlan[]> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
  const bytes = new Uint8Array(await file.arrayBuffer());
  const document = await pdfjs.getDocument({ data: bytes }).promise;
  const output: RenderedFloorPlan[] = [];
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1.75 });
    const canvas = window.document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas rendering is unavailable in this browser.");
    await page.render({ canvas, canvasContext: context, viewport }).promise;
    output.push({
      sourceFileName: file.name,
      sourceMediaType: file.type || "application/pdf",
      sourcePage: pageNumber,
      suggestedLevel: document.numPages === 1 ? guessLevel(file.name, pageNumber) : String(pageNumber),
      imageDataUrl: canvas.toDataURL("image/png"),
      width: canvas.width,
      height: canvas.height,
    });
  }
  return output;
}

export async function renderFloorPlanFiles(files: readonly File[]): Promise<RenderedFloorPlan[]> {
  const output: RenderedFloorPlan[] = [];
  for (const [index, file] of files.entries()) {
    if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) output.push(...await renderPdf(file));
    else if (file.type.startsWith("image/")) output.push(await renderImage(file, index + 1));
    else throw new Error(`${file.name} is not a supported image or PDF.`);
  }
  return output;
}
