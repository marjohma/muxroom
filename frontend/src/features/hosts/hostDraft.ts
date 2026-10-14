import type { HostDraft } from "@/api/types";

/** A config-safe host id derived from an ssh alias. */
export function hostIdFromAlias(alias: string, taken: string[]): string {
  const base =
    alias
      .toLowerCase()
      .replace(/[^a-z0-9-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 28) || "host";
  let id = /^[a-z0-9]/.test(base) ? base : `h-${base}`.slice(0, 28);
  let n = 2;
  while (taken.includes(id)) id = `${base}-${n++}`.slice(0, 32);
  return id;
}

/** Split a textarea / comma list into trimmed, non-empty entries. */
export function parseList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export const HOST_ID_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

export function draftProblems(draft: HostDraft): string[] {
  const problems: string[] = [];
  if (!HOST_ID_RE.test(draft.id)) problems.push("Host id: lowercase letters, digits and “-”, up to 32 characters.");
  if (!draft.ssh_host) problems.push("Choose an SSH alias.");
  if (draft.allowed_roots.length === 0) problems.push("Add at least one folder for sessions.");
  const badRoot = [...draft.allowed_roots, ...draft.upload_roots].find(
    (r) => !(r === "~" || r.startsWith("~/") || r.startsWith("/")),
  );
  if (badRoot) problems.push(`“${badRoot}” must be absolute or start with ~/.`);
  if (!(draft.tmux_path === "tmux" || draft.tmux_path.startsWith("/")))
    problems.push("tmux path must be “tmux” or an absolute path.");
  return problems;
}
