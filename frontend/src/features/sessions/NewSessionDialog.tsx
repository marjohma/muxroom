import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/api/client";
import { useAgents, useCreateSession } from "@/api/queries";
import type { Host, Session } from "@/api/types";
import { ErrorNote } from "@/components/ErrorNote";
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
import { suggestSessionName } from "@/features/files/paths";
import { cn } from "@/lib/utils";

const RECENT_KEY = (hostId: string) => `muxroom.recentDirs.${hostId}`;
const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

function recentDirs(hostId: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_KEY(hostId)) ?? "[]");
    return Array.isArray(value) ? value.filter((v) => typeof v === "string").slice(0, 8) : [];
  } catch {
    return [];
  }
}

function rememberDir(hostId: string, dir: string) {
  const next = [dir, ...recentDirs(hostId).filter((d) => d !== dir)].slice(0, 8);
  localStorage.setItem(RECENT_KEY(hostId), JSON.stringify(next));
}

interface Props {
  host: Host;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (session: Session) => void;
}

export function NewSessionDialog({ host, open, onOpenChange, onCreated }: Props) {
  const agents = useAgents().data;
  const create = useCreateSession(host.id);
  const choices = useMemo(
    () => [{ id: "shell", label: "Shell" }, ...(agents ?? []).filter((a) => host.agents.includes(a.id))],
    [agents, host.agents],
  );
  const dirs = useMemo(
    () => Array.from(new Set([...host.favorite_dirs, ...recentDirs(host.id), ...host.allowed_roots])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [host, open],
  );
  const [agentChoice, setAgentChoice] = useState<string | null>(null);
  const agent = agentChoice ?? choices[1]?.id ?? "shell";
  const setAgent = setAgentChoice;
  const [cwd, setCwd] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setAgentChoice(null);
      setCwd(dirs[0] ?? "~");
      setNameTouched(false);
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!nameTouched) setName(suggestSessionName(agent, cwd));
  }, [agent, cwd, nameTouched]);

  const nameValid = NAME_RE.test(name);
  const error = create.error instanceof ApiError ? create.error : null;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!nameValid || !cwd) return;
    create.mutate(
      { name, cwd, agent },
      {
        onSuccess: (session) => {
          rememberDir(host.id, cwd);
          onCreated(session);
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>New session on {host.label}</DialogTitle>
            <DialogDescription>
              Starts a detached tmux session. It keeps running when you close muxroom.
            </DialogDescription>
          </DialogHeader>

          <fieldset className="grid gap-2">
            <legend className="mb-2 text-[13px] text-muted-foreground">Run</legend>
            <div className="flex flex-wrap gap-2" role="radiogroup">
              {choices.map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  role="radio"
                  aria-checked={agent === choice.id}
                  onClick={() => setAgent(choice.id)}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-[13px] transition-colors",
                    agent === choice.id
                      ? "border-signal bg-signal/15 text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  {choice.label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="grid gap-2">
            <Label htmlFor="cwd">Working directory</Label>
            <Input id="cwd" value={cwd} onChange={(e) => setCwd(e.target.value)} list={`dirs-${host.id}`} spellCheck={false} />
            <datalist id={`dirs-${host.id}`}>
              {dirs.map((d) => (
                <option key={d} value={d} />
              ))}
            </datalist>
            <p className="text-[12px] text-muted-foreground">
              Allowed: {host.allowed_roots.join(", ")}
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="session-name">Session name</Label>
            <Input
              id="session-name"
              value={name}
              aria-invalid={!nameValid}
              spellCheck={false}
              onChange={(e) => {
                setNameTouched(true);
                setName(e.target.value);
              }}
            />
            {!nameValid && (
              <p className="text-[12px] text-fault">Use 1–64 letters, digits, “-” or “_”.</p>
            )}
          </div>

          {error && <ErrorNote error={error} />}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!nameValid || !cwd || create.isPending}>
              {create.isPending ? "Starting…" : "Start session"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
