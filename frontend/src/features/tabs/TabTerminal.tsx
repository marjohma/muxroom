import { useSessions } from "@/api/queries";
import { TerminalView } from "@/features/terminal/TerminalView";
import { useEffect } from "react";
import { findSession, type Tab } from "./tabs";

/**
 * Attaches only after the host's session list confirms the tab still points
 * at the same session; tmux reuses ids like $3 after a server restart.
 */
interface Props {
  tab: Tab;
  active: boolean;
  onRenamed: (name: string) => void;
}

export function TabTerminal({ tab, active, onRenamed }: Props) {
  const sessions = useSessions(tab.hostId);
  const list = sessions.data?.sessions;
  const live = list ? findSession(tab, list) : undefined;

  useEffect(() => {
    if (live && live.name !== tab.sessionName) onRenamed(live.name);
  }, [live, tab.sessionName, onRenamed]);

  if (!list) {
    return (
      <Notice>{sessions.error ? "Waiting for the host to become reachable…" : "Looking up session…"}</Notice>
    );
  }
  if (!live) {
    return <Notice>The session “{tab.sessionName}” no longer exists. Close this tab.</Notice>;
  }
  return <TerminalView hostId={tab.hostId} sessionId={tab.sessionId} active={active} />;
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-6 text-center text-[13px] text-muted-foreground">
      <p className="max-w-sm">{children}</p>
    </div>
  );
}
