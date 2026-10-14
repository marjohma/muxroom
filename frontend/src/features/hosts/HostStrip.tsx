import { ChevronDown, KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { useHostStatus, useSessions } from "@/api/queries";
import type { Host, Session } from "@/api/types";
import { ErrorNote } from "@/components/ErrorNote";
import { STATE_TEXT } from "@/components/hostState";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { SessionRow } from "@/features/sessions/SessionRow";
import { cn } from "@/lib/utils";

interface Props {
  host: Host;
  openSessionKeys: Set<string>;
  onOpenSession: (host: Host, session: Session) => void;
  onNewSession: (host: Host) => void;
  onSignIn: (host: Host) => void;
}

const sessionKey = (hostId: string, session: Session) => `${hostId}/${session.id}/${session.created}`;

export function HostStrip({ host, openSessionKeys, onOpenSession, onNewSession, onSignIn }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const status = useHostStatus(host.id);
  const state = status.data?.state ?? "pending";
  const sessions = useSessions(host.id, state === "ok" || state === "pending");
  const list = sessions.data?.sessions ?? [];
  const listError = sessions.error instanceof ApiError ? sessions.error : null;
  const statusError = status.data?.error ?? (status.error instanceof ApiError ? status.error : null);

  return (
    <section aria-label={host.label} className="border-b border-border/70">
      <header className="group flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
        >
          <StatusDot state={state} />
          <span className="truncate font-medium tracking-tight">{host.label}</span>
          <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", collapsed && "-rotate-90")} />
        </button>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-[12px] tabular-nums text-muted-foreground">
              {state === "ok" && status.data?.latency_ms != null ? `${status.data.latency_ms} ms` : STATE_TEXT[state]}
            </span>
          </TooltipTrigger>
          <TooltipContent side="right" className="grid gap-0.5">
            <span>{host.transport === "local" ? "This machine" : `Connected with ${host.transport}`}</span>
            {status.data?.tmux_version && <span>{status.data.tmux_version}</span>}
          </TooltipContent>
        </Tooltip>
        {state === "auth_failed" && host.can_sign_in && (
          <Button size="icon" variant="ghost" className="size-7" aria-label={`Sign in to ${host.label}`} onClick={() => onSignIn(host)}>
            <KeyRound className="size-4 text-attention" />
          </Button>
        )}
        <Button
          size="icon"
          variant="ghost"
          className="size-7"
          aria-label={`New session on ${host.label}`}
          disabled={state !== "ok"}
          onClick={() => onNewSession(host)}
        >
          <Plus className="size-4" />
        </Button>
      </header>

      {!collapsed && (
        <div className="pb-2">
          {statusError && state !== "ok" && <ErrorNote error={statusError} className="mx-3 mb-2" />}
          {listError && <ErrorNote error={listError} className="mx-3 mb-2" />}
          {state === "ok" && !listError && list.length === 0 && !sessions.isLoading && (
            <button
              type="button"
              onClick={() => onNewSession(host)}
              className="mx-3 mb-1 w-[calc(100%-1.5rem)] rounded-md border border-dashed px-3 py-2 text-left text-[13px] text-muted-foreground hover:border-signal/60 hover:text-foreground"
            >
              No tmux sessions yet. Start one.
            </button>
          )}
          <ul>
            {list.map((session) => (
              <SessionRow
                key={session.id}
                hostId={host.id}
                session={session}
                isOpen={openSessionKeys.has(sessionKey(host.id, session))}
                onOpen={() => onOpenSession(host, session)}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
