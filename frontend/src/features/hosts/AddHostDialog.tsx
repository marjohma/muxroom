import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, ApiError } from "@/api/client";
import { useAgents, useConfigChanged } from "@/api/queries";
import type { Host, HostDraft, HostTestResult } from "@/api/types";
import { STATE_TEXT } from "@/components/hostState";
import { ErrorNote } from "@/components/ErrorNote";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { draftProblems, hostIdFromAlias, parseList } from "./hostDraft";

interface Props {
  hosts: Host[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: (host: Host) => void;
}

const SAVEABLE = new Set(["ok", "auth_failed"]);

export function AddHostDialog({ hosts, open, onOpenChange, onAdded }: Props) {
  const aliases = useQuery({ queryKey: ["ssh-aliases"], queryFn: api.sshAliases, enabled: open });
  const agentsData = useAgents().data;
  const agents = useMemo(() => agentsData ?? [], [agentsData]);
  const configChanged = useConfigChanged();

  const [alias, setAlias] = useState("");
  const [label, setLabel] = useState("");
  const [id, setId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [transport, setTransport] = useState<HostDraft["transport"]>("openssh");
  const [tmuxPath, setTmuxPath] = useState("tmux");
  const [roots, setRoots] = useState("~");
  const [uploads, setUploads] = useState("");
  const [excluded, setExcluded] = useState<string[]>([]);

  const takenIds = hosts.map((h) => h.id);
  const effectiveId = idTouched ? id : alias ? hostIdFromAlias(alias, takenIds) : "";
  const draft: HostDraft = useMemo(
    () => ({
      id: effectiveId,
      ssh_host: alias,
      transport,
      label: label.trim() || null,
      tmux_path: tmuxPath.trim() || "tmux",
      allowed_roots: parseList(roots),
      favorite_dirs: [],
      agents: excluded.length ? agents.map((a) => a.id).filter((a) => !excluded.includes(a)) : null,
      upload_roots: parseList(uploads),
    }),
    [effectiveId, alias, transport, label, tmuxPath, roots, uploads, excluded, agents],
  );
  const draftKey = JSON.stringify(draft);
  const problems = draftProblems(draft);

  const test = useMutation({ mutationFn: (d: HostDraft) => api.testHost(d).then((r) => ({ key: JSON.stringify(d), r })) });
  const add = useMutation({
    mutationFn: api.addHost,
    onSuccess: async (result) => {
      await configChanged();
      onAdded(result.host);
      onOpenChange(false);
    },
  });
  const tested: HostTestResult | null = test.data && test.data.key === draftKey ? test.data.r : null;
  const canSave = !!tested && SAVEABLE.has(tested.status.state) && problems.length === 0;
  const error = (add.error ?? test.error) instanceof ApiError ? ((add.error ?? test.error) as ApiError) : null;

  const allAliases = aliases.data?.aliases ?? [];
  const configured = new Set(aliases.data?.configured ?? []);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Add a host</DialogTitle>
          <DialogDescription>
            Pick a host from your SSH configuration. muxroom tests the connection, then adds it
            to {aliases.data ? <code className="font-mono text-foreground">{aliases.data.config_path}</code> : "your config file"}.
          </DialogDescription>
        </DialogHeader>

        {aliases.data && !aliases.data.editing_allowed ? (
          <ErrorNote error={{ message: "Adding hosts from the UI is turned off.", hint: "Set server.allow_host_editing = true in config.toml, or add the host there." }} />
        ) : aliases.data && allAliases.length === 0 ? (
          <ErrorNote error={{ message: "No Host entries found in ~/.ssh/config.", hint: "Add the host there first (HostName, User, ProxyJump …), then come back." }} />
        ) : (
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label>SSH host</Label>
              <Select value={alias} onValueChange={setAlias}>
                <SelectTrigger aria-label="SSH host"><SelectValue placeholder="Choose an alias from ~/.ssh/config" /></SelectTrigger>
                <SelectContent>
                  {allAliases.map((a) => (
                    <SelectItem key={a} value={a}>
                      {a}{configured.has(a) ? " (already added)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="host-label">Display name</Label>
                <Input id="host-label" value={label} placeholder={alias || "Lab workstation"} onChange={(e) => setLabel(e.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="host-id">Host id</Label>
                <Input id="host-id" value={effectiveId} spellCheck={false}
                  onChange={(e) => { setIdTouched(true); setId(e.target.value); }} />
              </div>
            </div>

            <fieldset className="grid gap-2">
              <legend className="mb-2 text-[13px] text-muted-foreground">Connect with</legend>
              <div className="flex gap-2" role="radiogroup">
                {([
                  ["openssh", "System ssh", "Uses everything in ~/.ssh/config, including shared connections and passwords via Sign in."],
                  ["asyncssh", "Built-in client", "Key or ssh-agent login only."],
                ] as const).map(([value, title, hint]) => (
                  <button key={value} type="button" role="radio" aria-checked={transport === value}
                    onClick={() => setTransport(value)} title={hint}
                    className={cn("flex-1 rounded-md border px-3 py-2 text-left text-[13px]",
                      transport === value ? "border-signal bg-signal/10" : "text-muted-foreground hover:bg-accent")}>
                    <span className="block text-foreground">{title}</span>
                    <span className="block text-[12px] text-muted-foreground">{hint}</span>
                  </button>
                ))}
              </div>
            </fieldset>

            <div className="grid gap-2">
              <Label htmlFor="tmux-path">tmux path</Label>
              <Input id="tmux-path" value={tmuxPath} spellCheck={false} onChange={(e) => setTmuxPath(e.target.value)} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="roots">Session folders</Label>
                <textarea id="roots" rows={2} value={roots} onChange={(e) => setRoots(e.target.value)} spellCheck={false}
                  className="rounded-md border bg-transparent px-3 py-1.5 font-mono text-[12.5px]" />
                <p className="text-[12px] text-muted-foreground">Also browsable in Files. One per line.</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="uploads">Upload folders</Label>
                <textarea id="uploads" rows={2} value={uploads} onChange={(e) => setUploads(e.target.value)} spellCheck={false}
                  placeholder="optional, e.g. ~/proj/incoming"
                  className="rounded-md border bg-transparent px-3 py-1.5 font-mono text-[12.5px]" />
                <p className="text-[12px] text-muted-foreground">Must lie inside a session folder.</p>
              </div>
            </div>

            {agents.length > 0 && (
              <fieldset>
                <legend className="mb-2 text-[13px] text-muted-foreground">Agents allowed on this host</legend>
                <div className="flex flex-wrap gap-3">
                  {agents.map((a) => (
                    <label key={a.id} className="flex items-center gap-2 text-[13px]">
                      <input type="checkbox" className="accent-[var(--signal)]" checked={!excluded.includes(a.id)}
                        onChange={(e) => setExcluded((x) => (e.target.checked ? x.filter((v) => v !== a.id) : [...x, a.id]))} />
                      {a.label}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}

            {problems.length > 0 && alias && (
              <ul className="text-[12.5px] text-fault">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            )}
            {tested && <TestOutcome result={tested} onUseTmux={setTmuxPath} />}
            {error && <ErrorNote error={error} />}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="secondary" disabled={!alias || problems.length > 0 || test.isPending}
            onClick={() => { add.reset(); test.mutate(draft); }}>
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
          <Button disabled={!canSave || add.isPending} onClick={() => add.mutate(draft)}
            title={canSave ? undefined : "Test the connection first"}>
            {add.isPending ? "Adding…" : "Add host"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TestOutcome({ result, onUseTmux }: { result: HostTestResult; onUseTmux: (path: string) => void }) {
  const { status, tmux_suggestion } = result;
  return (
    <div className="rounded-md border px-3 py-2 text-[13px]" role="status">
      <p className="flex items-center gap-2">
        <StatusDot state={status.state} />
        <span className="text-foreground">{STATE_TEXT[status.state]}</span>
        {status.state === "ok" && (
          <span className="text-muted-foreground">{status.tmux_version}, {status.latency_ms} ms</span>
        )}
      </p>
      {status.state === "auth_failed" && (
        <p className="mt-1 text-muted-foreground">
          The host is reachable but needs a password. Add it, then use Sign in (key icon) next to the host.
        </p>
      )}
      {status.error && status.state !== "auth_failed" && (
        <p className="mt-1 text-muted-foreground">{status.error.message} {status.error.hint}</p>
      )}
      {tmux_suggestion && (
        <Button size="sm" variant="secondary" className="mt-2" onClick={() => onUseTmux(tmux_suggestion)}>
          Use {tmux_suggestion}
        </Button>
      )}
    </div>
  );
}
