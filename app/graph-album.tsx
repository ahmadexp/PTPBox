"use client";

/* eslint-disable @next/next/no-img-element -- album sources are runtime host URLs or IndexedDB data URLs */

import { Camera, Download, HardDrive, Image as ImageIcon, Images, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { toPng } from "html-to-image";

export type GraphAlbumItem = {
  id: string;
  title: string;
  section: string;
  graph_id: string;
  captured_at: number;
  created_at: number;
  width: number;
  height: number;
  bytes: number;
  metadata?: Record<string, string | number | boolean | null>;
  image_url?: string;
  data_url?: string;
  storage: "ptpbox-host" | "browser";
};

type CaptureTarget = {
  id: string;
  title: string;
  panel: HTMLElement;
  slot: HTMLElement;
};

const DATABASE_NAME = "ptpbox-observatory";
const DATABASE_VERSION = 1;
const STORE_NAME = "graph-album";
const CAPTURE_SELECTOR = [
  'canvas[role="img"]',
  'svg[role="img"]',
  '[class*="chart"]',
  '[class*="plot"]',
  '[class*="matrix"]',
  '[class*="spectrum"]',
  '[class*="heatmap"]',
  '[class*="return-map"]',
  '[class*="recurrence"]',
  ".histogram",
  ".dyn-mode-bands",
  ".dyn-mode-ribbon",
  ".dyn-clock-bars",
  ".dyn-accumulation",
  ".dyn-eigen-ladder",
  ".dyn-research-grid",
].join(",");

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 96) || "graph";
}

function openAlbumDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) database.createObjectStore(STORE_NAME, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Browser album is unavailable"));
  });
}

async function localAlbumItems() {
  const database = await openAlbumDatabase();
  try {
    return await new Promise<GraphAlbumItem[]>((resolve, reject) => {
      const request = database.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).getAll();
      request.onsuccess = () => resolve((request.result as GraphAlbumItem[]).map((item) => ({ ...item, storage: "browser" })));
      request.onerror = () => reject(request.error ?? new Error("Browser album could not be read"));
    });
  } finally {
    database.close();
  }
}

async function putLocalAlbumItem(item: GraphAlbumItem) {
  const database = await openAlbumDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const request = database.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).put(item);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error ?? new Error("Capture could not be saved locally"));
    });
  } finally {
    database.close();
  }
}

async function deleteLocalAlbumItem(identifier: string) {
  const database = await openAlbumDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const request = database.transaction(STORE_NAME, "readwrite").objectStore(STORE_NAME).delete(identifier);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error ?? new Error("Capture could not be deleted"));
    });
  } finally {
    database.close();
  }
}

function dataUrlBytes(dataUrl: string) {
  const base64 = dataUrl.split(",", 2)[1] ?? "";
  return Math.max(0, Math.floor(base64.length * 0.75) - (base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0));
}

function itemSource(item: GraphAlbumItem, agentBase: string) {
  if (item.data_url) return item.data_url;
  if (!item.image_url) return "";
  return item.image_url.startsWith("http") ? item.image_url : `${agentBase}${item.image_url}`;
}

async function saveCaptureToAlbum(
  agentBase: string,
  payload: Omit<GraphAlbumItem, "id" | "created_at" | "storage" | "data_url" | "image_url"> & { data_url: string },
) {
  try {
    const response = await fetch(`${agentBase}/api/album`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) throw new Error("PTPBox album rejected the capture");
    return { ...(await response.json() as GraphAlbumItem), storage: "ptpbox-host" as const };
  } catch {
    const now = Date.now();
    const local: GraphAlbumItem = {
      ...payload,
      id: `local-shot-${now}-${crypto.randomUUID().slice(0, 8)}`,
      created_at: now / 1000,
      storage: "browser",
    };
    await putLocalAlbumItem(local);
    return local;
  }
}

export function GraphCaptureControls({
  section,
  dataMode,
  agentBase,
  onCaptured,
  onError,
}: {
  section: string;
  dataMode: string;
  agentBase: string;
  onCaptured: (item: GraphAlbumItem) => void;
  onError: (message: string) => void;
}) {
  const [targets, setTargets] = useState<CaptureTarget[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const panels = Array.from(document.querySelectorAll<HTMLElement>("main .instrument-panel"));
    const created: CaptureTarget[] = [];
    panels.forEach((panel, index) => {
      const heading = panel.querySelector<HTMLElement>(":scope > .panel-heading");
      if (!heading || !panel.querySelector(CAPTURE_SELECTOR)) return;
      const title = heading.querySelector("h2")?.textContent?.trim() || panel.getAttribute("aria-label") || `${section} graph`;
      const slot = document.createElement("span");
      slot.className = "graph-capture-slot";
      slot.dataset.captureControl = "true";
      heading.appendChild(slot);
      created.push({ id: `${slug(section)}-${slug(title)}-${index}`, title, panel, slot });
    });
    const frame = window.requestAnimationFrame(() => setTargets(created));
    return () => {
      window.cancelAnimationFrame(frame);
      created.forEach((target) => target.slot.remove());
    };
  }, [section]);

  const capture = useCallback(async (target: CaptureTarget) => {
    if (busy) return;
    setBusy(target.id);
    const capturedAt = Date.now() / 1000;
    const stamp = document.createElement("div");
    stamp.className = "graph-capture-stamp";
    stamp.textContent = `PTPBOX PRECISION OBSERVATORY · ${section.toUpperCase()} · ${dataMode} · ${new Date(capturedAt * 1000).toLocaleString()}`;
    target.panel.appendChild(stamp);
    target.panel.classList.add("graph-capture-rendering");
    try {
      const bounds = target.panel.getBoundingClientRect();
      const pixelRatio = Math.min(2.25, Math.max(1.5, window.devicePixelRatio || 1));
      const dataUrl = await toPng(target.panel, {
        backgroundColor: "#0a1116",
        cacheBust: true,
        pixelRatio,
        filter: (node) => !(node instanceof HTMLElement && node.dataset.captureControl === "true"),
      });
      const saved = await saveCaptureToAlbum(agentBase, {
        title: target.title,
        section,
        graph_id: target.id,
        captured_at: capturedAt,
        width: Math.round(bounds.width * pixelRatio),
        height: Math.round(bounds.height * pixelRatio),
        bytes: dataUrlBytes(dataUrl),
        metadata: { data_mode: dataMode, source: "graph-panel", pixel_ratio: pixelRatio },
        data_url: dataUrl,
      });
      onCaptured(saved);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Graph capture failed");
    } finally {
      stamp.remove();
      target.panel.classList.remove("graph-capture-rendering");
      setBusy(null);
    }
  }, [agentBase, busy, dataMode, onCaptured, onError, section]);

  return (
    <>
      {targets.map((target) => createPortal(
        <button
          className="quiet-button graph-capture-button"
          type="button"
          data-capture-control="true"
          disabled={busy !== null}
          aria-label={`Save ${target.title} to album`}
          onClick={() => void capture(target)}
        >
          <Camera size={12} /> {busy === target.id ? "Saving…" : "Capture"}
        </button>,
        target.slot,
        target.id,
      ))}
    </>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function GraphAlbumView({ agentBase, revision }: { agentBase: string; revision: number }) {
  const [items, setItems] = useState<GraphAlbumItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<GraphAlbumItem | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    const [remote, local] = await Promise.all([
      fetch(`${agentBase}/api/album`, { signal: AbortSignal.timeout(2_500) })
        .then(async (response) => response.ok ? (await response.json() as { items?: GraphAlbumItem[] }).items ?? [] : [])
        .catch(() => [] as GraphAlbumItem[]),
      localAlbumItems().catch(() => []),
    ]);
    const combined = [
      ...remote.map((item) => ({ ...item, storage: "ptpbox-host" as const })),
      ...local,
    ].sort((left, right) => right.captured_at - left.captured_at);
    setItems(combined);
    setLoading(false);
  }, [agentBase]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh, revision]);

  useEffect(() => {
    if (!selected) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [selected]);

  const storageBytes = useMemo(() => items.reduce((total, item) => total + item.bytes, 0), [items]);

  const remove = async (item: GraphAlbumItem) => {
    setError("");
    try {
      if (item.storage === "ptpbox-host") {
        const response = await fetch(`${agentBase}/api/album/${item.id}`, { method: "DELETE", signal: AbortSignal.timeout(5_000) });
        if (!response.ok) throw new Error("The PTPBox host could not delete this capture");
      } else {
        await deleteLocalAlbumItem(item.id);
      }
      if (selected?.id === item.id) setSelected(null);
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Capture could not be deleted");
    }
  };

  const download = async (item: GraphAlbumItem) => {
    setError("");
    try {
      const source = itemSource(item, agentBase);
      if (!source) throw new Error("This capture has no image source");
      const anchor = document.createElement("a");
      anchor.download = `${slug(item.title)}-${new Date(item.captured_at * 1000).toISOString().replace(/[:.]/g, "-")}.png`;
      if (item.data_url) {
        anchor.href = source;
        anchor.click();
        return;
      }
      const response = await fetch(source, { signal: AbortSignal.timeout(8_000) });
      if (!response.ok) throw new Error("The PTPBox host could not provide this capture");
      const objectUrl = URL.createObjectURL(await response.blob());
      anchor.href = objectUrl;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Capture could not be downloaded");
    }
  };

  return (
    <div className="album-layout">
      <section className="instrument-panel album-hero">
        <div>
          <span className="section-kicker">GRAPH EVIDENCE ARCHIVE</span>
          <h2>Captured Observatory frames</h2>
          <p>Every image preserves the graph title, current evidence labels, data mode, and capture time.</p>
        </div>
        <div className="album-summary">
          <div><Images size={17} /><span><strong>{items.length}</strong><small>captures</small></span></div>
          <div><HardDrive size={17} /><span><strong>{formatBytes(storageBytes)}</strong><small>stored</small></span></div>
        </div>
      </section>

      {error && <div className="album-error" role="alert">{error}</div>}

      {loading ? (
        <div className="album-empty"><ImageIcon size={26} /><strong>Opening the graph album…</strong></div>
      ) : items.length ? (
        <div className="album-grid">
          {items.map((item) => (
            <article className="album-card" key={item.id}>
              <button className="album-image-button" type="button" onClick={() => setSelected(item)} aria-label={`Open ${item.title}`}>
                <img src={itemSource(item, agentBase)} alt={`${item.title}, captured ${new Date(item.captured_at * 1000).toLocaleString()}`} />
              </button>
              <div className="album-card-body">
                <span>{item.section}</span>
                <strong>{item.title}</strong>
                <small>{new Date(item.captured_at * 1000).toLocaleString()} · {item.width}×{item.height}</small>
                <div>
                  <em className={item.storage === "ptpbox-host" ? "host" : "browser"}>{item.storage === "ptpbox-host" ? "PTPBOX HOST" : "THIS BROWSER"}</em>
                  <button type="button" onClick={() => void download(item)} aria-label={`Download ${item.title}`}><Download size={13} /></button>
                  <button type="button" onClick={() => void remove(item)} aria-label={`Delete ${item.title}`}><Trash2 size={13} /></button>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="album-empty">
          <Camera size={27} />
          <strong>No graph captures yet</strong>
          <span>Use the Capture button on any Observatory graph to preserve it here.</span>
        </div>
      )}

      {selected && (
        <div className="album-lightbox" role="dialog" aria-modal="true" aria-labelledby="album-preview-title">
          <button className="album-lightbox-backdrop" type="button" onClick={() => setSelected(null)} aria-label="Close image preview" />
          <section>
            <header>
              <div><span>{selected.section}</span><strong id="album-preview-title">{selected.title}</strong><small>{new Date(selected.captured_at * 1000).toLocaleString()} · {selected.width}×{selected.height} · {formatBytes(selected.bytes)}</small></div>
              <button type="button" onClick={() => setSelected(null)} aria-label="Close image preview"><X size={17} /></button>
            </header>
            <img src={itemSource(selected, agentBase)} alt={`${selected.title} full-size capture`} />
            <footer>
              <span>{selected.storage === "ptpbox-host" ? "Stored on the PTPBox host and available to connected operators." : "Stored in this browser because the PTPBox album was unavailable."}</span>
              <button className="secondary-button" type="button" onClick={() => void download(selected)}><Download size={14} /> Download PNG</button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
