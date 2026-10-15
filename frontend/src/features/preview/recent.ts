import type { PreviewTarget } from "@/features/tabs/tabs";

export const RECENT_KEY = "muxroom.recentPreviews.v1";
export const MAX_RECENT_PER_HOST = 15;

export interface RecentPreview extends PreviewTarget {
  openedAt: number;
}

type Store = Pick<Storage, "getItem" | "setItem">;

function isRecent(value: unknown): value is RecentPreview {
  const r = value as RecentPreview;
  return (
    typeof r === "object" && r !== null &&
    typeof r.hostId === "string" && typeof r.path === "string" &&
    typeof r.name === "string" && typeof r.openedAt === "number" &&
    ["pdf", "png", "jpeg", "svg"].includes(r.kind)
  );
}

export function loadRecent(store: Store): RecentPreview[] {
  try {
    const data = JSON.parse(store.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(data) ? data.filter(isRecent) : [];
  } catch {
    return [];
  }
}

/** Most recent first; one entry per (host, path); capped per host. */
export function addRecent(store: Store, target: PreviewTarget, now = Date.now()): RecentPreview[] {
  const others = loadRecent(store).filter((r) => !(r.hostId === target.hostId && r.path === target.path));
  const next = [{ ...target, openedAt: now }, ...others];
  const perHost = new Map<string, number>();
  const capped = next.filter((r) => {
    const count = (perHost.get(r.hostId) ?? 0) + 1;
    perHost.set(r.hostId, count);
    return count <= MAX_RECENT_PER_HOST;
  });
  store.setItem(RECENT_KEY, JSON.stringify(capped));
  return capped;
}

export function removeRecent(store: Store, hostId: string, path: string): RecentPreview[] {
  const next = loadRecent(store).filter((r) => !(r.hostId === hostId && r.path === path));
  store.setItem(RECENT_KEY, JSON.stringify(next));
  return next;
}

export function recentForHost(items: RecentPreview[], hostId: string): RecentPreview[] {
  return items.filter((r) => r.hostId === hostId);
}

/** Parent folder, shortened to its last two segments ("…/study/plots"). */
export function parentDir(path: string): string {
  const parts = path.split("/").filter(Boolean).slice(0, -1);
  if (parts.length === 0) return "/";
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : `/${parts.join("/")}`;
}
