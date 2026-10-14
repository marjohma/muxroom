import { Keyboard, LoaderCircle } from "lucide-react";
import type { AgentActivity } from "@/api/types";
import { cn } from "@/lib/utils";

/** Spinner while an agent works; keyboard icon when it waits for input. */
export function AgentIndicator({ agent, className }: { agent?: AgentActivity | null; className?: string }) {
  if (!agent) return null;
  const working = agent.state === "working";
  const text = working ? `${agent.label} is working` : `${agent.label} is waiting for your input`;
  const Icon = working ? LoaderCircle : Keyboard;
  return (
    <span role="img" aria-label={text} title={text} className={cn("inline-flex shrink-0", className)}>
      <Icon className={cn("size-3.5", working ? "animate-spin text-signal" : "text-attention")} />
    </span>
  );
}
