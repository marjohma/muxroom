import { useEffect, useState } from "react";
import { api, ApiError } from "@/api/client";
import type { FileInfo } from "@/api/types";
import { initialWatch, observe, POLL_INTERVAL_MS, type WatchState } from "./watcher";

/**
 * Poll a file's version while the page is visible; expose the stable version
 * and the signed preview URL issued for exactly that version (each poll gets a
 * freshly signed URL, so reusing the first one avoids needless reloads).
 */
export function useWatchedFile(hostId: string, path: string) {
  const [watch, setWatch] = useState<WatchState>(initialWatch(null));
  const [latest, setLatest] = useState<FileInfo | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const [urls, setUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (document.hidden) return;
      try {
        const info = await api.stat(hostId, path);
        if (cancelled) return;
        const url = info.preview_url;
        if (url) setUrls((u) => (info.version in u ? u : { ...u, [info.version]: url }));
        setLatest(info);
        setWatch((w) => observe(w, info.version));
        setError(null);
      } catch (err) {
        if (!cancelled && err instanceof ApiError) setError(err);
      }
    };
    void poll();
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hostId, path, tick]);

  const version = watch.shown;
  return {
    version,
    previewUrl: version ? (urls[version] ?? null) : null,
    downloadUrl: latest?.download_url ?? null,
    error,
    reload: () => {
      setUrls({}); // fresh signed URLs (they expire after an hour)
      setTick((t) => t + 1);
    },
  };
}
