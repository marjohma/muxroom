import type { HostState } from "@/api/types";

export const STATE_TEXT: Record<HostState | "pending", string> = {
  ok: "Connected",
  unreachable: "Unreachable",
  auth_failed: "Needs sign-in",
  host_key: "Host key problem",
  tmux_missing: "tmux not found",
  error: "Error",
  pending: "Checking…",
};
