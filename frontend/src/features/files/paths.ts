export function joinPath(dir: string, name: string): string {
  return dir.endsWith("/") ? `${dir}${name}` : `${dir}/${name}`;
}

export function basename(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) ?? "/";
}

export interface Crumb {
  label: string;
  path: string;
}

/** Breadcrumbs from the containing root down to `path` (never above the root). */
export function breadcrumbs(path: string, root: string): Crumb[] {
  if (path !== root && !path.startsWith(root.endsWith("/") ? root : `${root}/`)) {
    return [{ label: path, path }];
  }
  const crumbs: Crumb[] = [{ label: basename(root) || root, path: root }];
  const rest = path.slice(root.length).split("/").filter(Boolean);
  let current = root;
  for (const part of rest) {
    current = joinPath(current, part);
    crumbs.push({ label: part, path: current });
  }
  return crumbs;
}

export function containingRoot(path: string, roots: string[]): string | null {
  const matches = roots.filter((r) => path === r || path.startsWith(r.endsWith("/") ? r : `${r}/`));
  return matches.sort((a, b) => b.length - a.length)[0] ?? null;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function formatAge(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function suggestSessionName(agent: string, cwd: string): string {
  const base = basename(cwd).replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  const prefix = agent === "shell" ? "sh" : agent;
  return `${prefix}-${base || "home"}`.slice(0, 64);
}
