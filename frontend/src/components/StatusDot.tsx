import type { HostState } from "@/api/types";
import { cn } from "@/lib/utils";
import { STATE_TEXT } from "./hostState";

const COLORS: Record<HostState | "pending", string> = {
  ok: "bg-ok shadow-[0_0_6px_var(--ok)]",
  unreachable: "bg-fault",
  auth_failed: "bg-attention shadow-[0_0_6px_var(--attention)]",
  host_key: "bg-fault",
  tmux_missing: "bg-signal",
  error: "bg-fault",
  pending: "bg-idle animate-pulse",
};


export function StatusDot({ state, className }: { state: HostState | "pending"; className?: string }) {
  return (
    <span
      role="img"
      aria-label={STATE_TEXT[state]}
      className={cn("inline-block size-2 shrink-0 rounded-full", COLORS[state], className)}
    />
  );
}
