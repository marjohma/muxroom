import { Pencil } from "lucide-react";
import { useState } from "react";
import { ApiError } from "@/api/client";
import { useRenameSession } from "@/api/queries";
import type { Session } from "@/api/types";
import { AgentIndicator } from "@/components/AgentIndicator";
import { ErrorNote } from "@/components/ErrorNote";
import { formatAge } from "@/features/files/paths";
import { cn } from "@/lib/utils";

const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

interface Props {
  hostId: string;
  session: Session;
  isOpen: boolean;
  onOpen: () => void;
}

export function SessionRow({ hostId, session, isOpen, onOpen }: Props) {
  const rename = useRenameSession(hostId);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(session.name);
  const valid = NAME_RE.test(name);
  const error = rename.error instanceof ApiError ? rename.error : null;

  const start = () => {
    rename.reset();
    setName(session.name);
    setEditing(true);
  };
  const cancel = () => {
    rename.reset();
    setEditing(false);
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    if (name === session.name) return cancel();
    rename.mutate({ sessionId: session.id, name }, { onSuccess: () => setEditing(false) });
  };

  const meta = (
    <>
      <span className="truncate text-[12px] text-muted-foreground" title={session.path}>
        {session.path || "—"}
      </span>
      <span className="text-[12px] tabular-nums text-muted-foreground">{formatAge(session.last_activity)}</span>
    </>
  );

  if (editing) {
    return (
      <li className={cn("border-l-2 py-1.5 pr-3 pl-[18px]", isOpen ? "border-signal bg-accent/40" : "border-transparent")}>
        <form onSubmit={submit} className="grid grid-cols-[1fr_auto] gap-x-2">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && cancel()}
            onBlur={() => !rename.isPending && !error && cancel()}
            aria-label={`New name for session ${session.name}`}
            aria-invalid={!valid}
            spellCheck={false}
            disabled={rename.isPending}
            className="min-w-0 rounded border border-signal/70 bg-background px-1.5 py-0.5 text-[13.5px] outline-none"
          />
          <span className="self-center text-[11.5px] text-muted-foreground">Enter to save</span>
          {meta}
        </form>
        {!valid && <p className="mt-1 text-[12px] text-fault">Use 1–64 letters, digits, “-” or “_”.</p>}
        {error && <ErrorNote error={error} className="mt-1.5" />}
      </li>
    );
  }

  return (
    <li className="group relative">
      <button
        type="button"
        onClick={onOpen}
        onDoubleClick={start}
        className={cn(
          "grid w-full grid-cols-[1fr_auto] gap-x-2 border-l-2 py-1.5 pr-3 pl-[18px] text-left hover:bg-accent/60",
          isOpen ? "border-signal bg-accent/40" : "border-transparent",
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5 pr-6">
          <AgentIndicator agent={session.agent} />
          <span className="truncate text-[13.5px]">{session.name}</span>
        </span>
        <span className="flex items-center gap-1" aria-label={`${session.attached_clients} attached clients`}>
          {Array.from({ length: Math.min(session.attached_clients, 4) }, (_, i) => (
            <span key={i} className="size-1.5 rounded-full bg-signal" />
          ))}
        </span>
        {meta}
      </button>
      <button
        type="button"
        onClick={start}
        aria-label={`Rename session ${session.name}`}
        title="Rename session"
        className="absolute top-1.5 right-[4.5rem] rounded p-0.5 text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-accent hover:text-foreground focus-visible:opacity-100"
      >
        <Pencil className="size-3.5" />
      </button>
    </li>
  );
}
