import { useQuery } from "@tanstack/react-query";
import { FolderTree, Plus, RefreshCw, TerminalSquare } from "lucide-react";
import { useEffect, useMemo, useReducer, useState } from "react";
import { setTokenFromInput } from "@/api/auth";
import { api, ApiError } from "@/api/client";
import { windowFocused } from "@/api/refresh";
import { useConfigChanged, useHosts } from "@/api/queries";
import type { Host, ReloadResult, Session } from "@/api/types";
import { ErrorNote } from "@/components/ErrorNote";
import { Button } from "@/components/ui/button";
import { FileExplorer } from "@/features/files/FileExplorer";
import { AddHostDialog } from "@/features/hosts/AddHostDialog";
import { HostStrip } from "@/features/hosts/HostStrip";
import { SignInDialog } from "@/features/hosts/SignInDialog";
import { PreviewPane } from "@/features/preview/PreviewPane";
import { addRecent, loadRecent, removeRecent } from "@/features/preview/recent";
import { NewSessionDialog } from "@/features/sessions/NewSessionDialog";
import { TabBar } from "@/features/tabs/TabBar";
import { TabTerminal } from "@/features/tabs/TabTerminal";
import { loadTabs, type Orientation, type PreviewTarget, saveTabs, tabsReducer } from "@/features/tabs/tabs";
import { Workspace } from "@/features/tabs/Workspace";
import { cn } from "@/lib/utils";

type SidebarMode = "sessions" | "files";

export default function App() {
  const hosts = useHosts();
  if (hosts.error instanceof ApiError && hosts.error.code === "unauthorized") return <AuthScreen />;
  if (hosts.error instanceof ApiError) {
    return (
      <div className="grid h-full place-items-center p-6">
        <ErrorNote error={hosts.error} className="max-w-md" />
      </div>
    );
  }
  if (!hosts.data) return null;
  return <Dashboard hosts={hosts.data} />;
}

function Dashboard({ hosts }: { hosts: Host[] }) {
  const [state, dispatch] = useReducer(tabsReducer, undefined, () => loadTabs(localStorage));
  const [mode, setMode] = useState<SidebarMode>(
    () => (localStorage.getItem("muxroom.sidebar") as SidebarMode) ?? "sessions",
  );
  const [fileHost, setFileHost] = useState(() => localStorage.getItem("muxroom.fileHost") ?? hosts[0].id);
  const [newSessionHost, setNewSessionHost] = useState<Host | null>(null);
  const [signInHost, setSignInHost] = useState<Host | null>(null);
  const [addingHost, setAddingHost] = useState(false);
  const [recent, setRecent] = useState(() => loadRecent(localStorage));
  const [loose, setLoose] = useState<{ open: boolean; target: PreviewTarget | null; orientation: Orientation }>(
    { open: false, target: null, orientation: "horizontal" },
  );

  useEffect(() => saveTabs(localStorage, state), [state]);
  useEffect(() => localStorage.setItem("muxroom.sidebar", mode), [mode]);
  useEffect(() => localStorage.setItem("muxroom.fileHost", fileHost), [fileHost]);

  const active = state.tabs.find((t) => t.id === state.activeId) ?? null;
  const openKeys = useMemo(() => new Set(state.tabs.map((t) => t.id)), [state.tabs]);
  const [previewOpenFor, setPreviewOpenFor] = useState<Set<string>>(new Set());

  const previewOpen = active ? previewOpenFor.has(active.id) || active.preview !== null : loose.open;
  const preview = active ? active.preview : loose.target;
  const orientation = active ? active.orientation : loose.orientation;

  const openSession = (host: Host, session: Session) => dispatch({ type: "open", hostId: host.id, session });
  const showPreview = (target: PreviewTarget | null) => {
    if (target) setRecent(addRecent(localStorage, target));
    if (active) {
      dispatch({ type: "setPreview", id: active.id, preview: target });
      setPreviewOpenFor((s) => new Set(s).add(active.id));
    } else {
      setLoose((l) => ({ ...l, open: true, target }));
    }
  };
  const closePreview = () => {
    if (active) {
      dispatch({ type: "setPreview", id: active.id, preview: null });
      setPreviewOpenFor((s) => {
        const next = new Set(s);
        next.delete(active.id);
        return next;
      });
    } else {
      setLoose((l) => ({ ...l, open: false, target: null }));
    }
  };
  const togglePreview = () => (previewOpen ? closePreview() : active
    ? setPreviewOpenFor((s) => new Set(s).add(active.id))
    : setLoose((l) => ({ ...l, open: true })));
  const toggleOrientation = () => {
    const next: Orientation = orientation === "horizontal" ? "vertical" : "horizontal";
    if (active) dispatch({ type: "setOrientation", id: active.id, orientation: next });
    else setLoose((l) => ({ ...l, orientation: next }));
  };

  const terminals = (
    <div className="relative h-full min-h-0">
      {state.tabs.map((tab) => (
        <div key={tab.id} className={cn("absolute inset-0", tab.id !== state.activeId && "hidden")}>
          <TabTerminal
            tab={tab}
            active={tab.id === state.activeId}
            onRenamed={(name) => dispatch({ type: "setName", id: tab.id, name })}
          />
        </div>
      ))}
      {state.tabs.length === 0 && <EmptyWorkspace />}
    </div>
  );

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-[300px] shrink-0 flex-col border-r bg-panel">
        <div className="flex items-center justify-between px-3 pt-3 pb-2">
          <h1 className="text-[15px] font-semibold tracking-tight">
            mux<span className="text-signal">room</span>
          </h1>
          <div role="tablist" aria-label="Sidebar" className="flex rounded-md border p-0.5">
            <ModeButton active={mode === "sessions"} onClick={() => setMode("sessions")} label="Sessions">
              <TerminalSquare className="size-3.5" />
            </ModeButton>
            <ModeButton active={mode === "files"} onClick={() => setMode("files")} label="Files">
              <FolderTree className="size-3.5" />
            </ModeButton>
          </div>
        </div>
        {mode === "sessions" ? (
          <div className="min-h-0 flex-1 overflow-y-auto border-t">
            {hosts.map((host) => (
              <HostStrip
                key={host.id}
                host={host}
                openSessionKeys={openKeys}
                onOpenSession={openSession}
                onNewSession={setNewSessionHost}
                onSignIn={setSignInHost}
              />
            ))}
            <ConfigActions onAddHost={() => setAddingHost(true)} />
          </div>
        ) : (
          <FileExplorer hosts={hosts} hostId={fileHost} onHostChange={setFileHost} onPreview={showPreview} />
        )}
        <BackendStatus />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <TabBar
          tabs={state.tabs}
          activeId={state.activeId}
          hosts={hosts}
          previewOpen={previewOpen}
          onActivate={(id) => dispatch({ type: "activate", id })}
          onClose={(id) => dispatch({ type: "close", id })}
          onTogglePreview={togglePreview}
        />
        <div className="min-h-0 flex-1">
          <Workspace
            orientation={orientation}
            main={terminals}
            side={
              previewOpen ? (
                <PreviewPane
                  target={preview}
                  session={active ? { hostId: active.hostId, sessionId: active.sessionId } : null}
                  orientation={orientation}
                  onOpen={showPreview}
                  onClose={closePreview}
                  onToggleOrientation={toggleOrientation}
                  recent={recent}
                  onForgetRecent={(r) => setRecent(removeRecent(localStorage, r.hostId, r.path))}
                />
              ) : null
            }
          />
        </div>
      </main>

      {newSessionHost && (
        <NewSessionDialog
          host={newSessionHost}
          open
          onOpenChange={(open) => !open && setNewSessionHost(null)}
          onCreated={(session) => openSession(newSessionHost, session)}
        />
      )}
      {addingHost && (
        <AddHostDialog hosts={hosts} open onOpenChange={setAddingHost} onAdded={() => setMode("sessions")} />
      )}
      {signInHost && (
        <SignInDialog host={signInHost} open onOpenChange={(open) => !open && setSignInHost(null)} />
      )}
    </div>
  );
}

function ConfigActions({ onAddHost }: { onAddHost: () => void }) {
  const configChanged = useConfigChanged();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ result?: ReloadResult; error?: ApiError } | null>(null);

  const reload = async () => {
    setBusy(true);
    try {
      const result = await api.reloadConfig();
      setOutcome({ result });
      await configChanged();
    } catch (err) {
      if (err instanceof ApiError) setOutcome({ error: err });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-2 px-3 py-3">
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={onAddHost}>
          <Plus className="size-3.5" /> Add host
        </Button>
        <Button size="sm" variant="ghost" onClick={reload} disabled={busy}
          title="Re-read config.toml after editing it">
          <RefreshCw className={cn("size-3.5", busy && "animate-spin")} /> Reload config
        </Button>
      </div>
      {outcome?.error && <ErrorNote error={outcome.error} />}
      {outcome?.result && <p className="text-[12px] text-muted-foreground">{describeReload(outcome.result)}</p>}
    </div>
  );
}

function describeReload(r: ReloadResult): string {
  const parts = [
    r.added.length && `added ${r.added.join(", ")}`,
    r.changed.length && `updated ${r.changed.join(", ")}`,
    r.removed.length && `removed ${r.removed.join(", ")}`,
  ].filter(Boolean);
  const summary = parts.length ? `Configuration reloaded: ${parts.join("; ")}.` : "Configuration reloaded. No host changes.";
  return r.restart_required.length
    ? `${summary} Restart muxroom to apply ${r.restart_required.join(", ")}.`
    : summary;
}

function ModeButton(props: { active: boolean; onClick: () => void; label: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={props.active}
      onClick={props.onClick}
      className={cn(
        "flex items-center gap-1.5 rounded px-2 py-1 text-[12.5px]",
        props.active ? "bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {props.children}
      {props.label}
    </button>
  );
}

function BackendStatus() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await fetch("/api/health")).ok,
    refetchInterval: () => (windowFocused() ? 5_000 : 15_000),
    retry: false,
  });
  const ok = health.data === true;
  return (
    <div className="flex items-center gap-2 border-t px-3 py-2 text-[12px] text-muted-foreground">
      <span className={cn("size-1.5 rounded-full", ok ? "bg-ok" : "bg-fault")} />
      {ok ? "Backend running" : "Backend not reachable. Sessions keep running; tabs reconnect automatically."}
    </div>
  );
}

function EmptyWorkspace() {
  return (
    <div className="grid h-full place-items-center p-8">
      <div className="max-w-md text-[13.5px] text-muted-foreground">
        <p className="text-[15px] text-foreground">Pick a session on the left to attach.</p>
        <p className="mt-2">
          Sessions live in tmux on each host. Closing a tab or this window only detaches, and
          your agents keep running. Use + next to a host to start a new one.
        </p>
      </div>
    </div>
  );
}

function AuthScreen() {
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (setTokenFromInput(value)) window.location.reload();
    else setInvalid(true);
  };
  return (
    <div className="grid h-full place-items-center p-8">
      <div className="max-w-md">
        <h1 className="text-[18px] font-semibold">
          mux<span className="text-signal">room</span> needs its access link
        </h1>
        <p className="mt-3 text-[13.5px] text-muted-foreground">
          Open the link that <code className="font-mono text-foreground">muxroom</code> printed when it
          started (or paste it below). The token stays valid across restarts; each browser tab needs it once. Run{" "}
          <code className="font-mono text-foreground">muxroom --rotate-token</code> to issue a new one.
        </p>
        <form onSubmit={submit} className="mt-5 flex gap-2">
          <input
            value={value}
            onChange={(e) => { setValue(e.target.value); setInvalid(false); }}
            placeholder="Paste the access link"
            aria-label="Access link or token"
            aria-invalid={invalid}
            spellCheck={false}
            className="min-w-0 flex-1 rounded-md border bg-transparent px-3 py-1.5 font-mono text-[12.5px]"
          />
          <Button type="submit">Open</Button>
        </form>
        {invalid && <p className="mt-2 text-[12.5px] text-fault">That is not a muxroom access link or token.</p>}
      </div>
    </div>
  );
}
