import { PanelRight, X } from "lucide-react";
import type { Host } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useSessions } from "@/api/queries";
import { AgentIndicator } from "@/components/AgentIndicator";
import { findSession, type Tab } from "./tabs";

function TabAgent({ tab }: { tab: Tab }) {
  const sessions = useSessions(tab.hostId).data?.sessions;
  return <AgentIndicator agent={sessions ? findSession(tab, sessions)?.agent : null} />;
}

interface Props {
  tabs: Tab[];
  activeId: string | null;
  hosts: Host[];
  previewOpen: boolean;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  onTogglePreview: () => void;
}

export function TabBar({ tabs, activeId, hosts, previewOpen, onActivate, onClose, onTogglePreview }: Props) {
  const label = (hostId: string) => hosts.find((h) => h.id === hostId)?.label ?? hostId;
  return (
    <div className="flex h-10 shrink-0 items-stretch border-b bg-panel">
      <div role="tablist" aria-label="Open sessions" className="flex min-w-0 flex-1 overflow-x-auto">
        {tabs.map((tab) => {
          const active = tab.id === activeId;
          return (
            <div
              key={tab.id}
              className={cn(
                "group relative flex min-w-36 max-w-60 items-center border-r",
                active ? "bg-background" : "hover:bg-accent/40",
              )}
            >
              {active && <span className="absolute inset-x-0 top-0 h-0.5 bg-signal" aria-hidden />}
              <button
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onActivate(tab.id)}
                onAuxClick={(e) => e.button === 1 && onClose(tab.id)}
                className="flex min-w-0 flex-1 flex-col items-start px-3 py-1 text-left leading-tight"
              >
                <span className="flex max-w-full items-center gap-1.5">
                  <TabAgent tab={tab} />
                  <span className={cn("truncate text-[13px]", tab.stale && "line-through text-muted-foreground")}>
                    {tab.sessionName}
                  </span>
                </span>
                <span className="max-w-full truncate text-[11.5px] text-muted-foreground">{label(tab.hostId)}</span>
              </button>
              <button
                type="button"
                onClick={() => onClose(tab.id)}
                aria-label={`Close tab ${tab.sessionName} (session keeps running)`}
                title="Close tab. The session keeps running."
                className="mr-1.5 rounded p-0.5 text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-accent hover:text-foreground focus-visible:opacity-100"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
      <Button
        variant="ghost"
        className={cn("h-full rounded-none border-l px-3", previewOpen && "text-signal")}
        onClick={onTogglePreview}
        aria-pressed={previewOpen}
        aria-label="Toggle preview pane"
      >
        <PanelRight className="size-4" />
        <span className="text-[13px]">Preview</span>
      </Button>
    </div>
  );
}
