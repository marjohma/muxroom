import { Columns2, Download, RefreshCw, Rows2, X } from "lucide-react";
import { useState } from "react";
import { api, ApiError, withVersion } from "@/api/client";
import { ErrorNote, type ErrorLike } from "@/components/ErrorNote";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Orientation, PreviewTarget } from "@/features/tabs/tabs";
import { useWatchedFile } from "./useWatchedFile";

interface Props {
  target: PreviewTarget | null;
  session: { hostId: string; sessionId: string } | null;
  orientation: Orientation;
  onOpen: (target: PreviewTarget) => void;
  onClose: () => void;
  onToggleOrientation: () => void;
}

export function PreviewPane({ target, session, orientation, onOpen, onClose, onToggleOrientation }: Props) {
  const [path, setPath] = useState("");
  const [openError, setOpenError] = useState<ErrorLike | null>(null);

  const open = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = path.trim();
    if (!value) return;
    setOpenError(null);
    const hostId = session?.hostId ?? target?.hostId;
    if (!hostId) return;
    try {
      const file = value.startsWith("/") || value.startsWith("~")
        ? await api.stat(hostId, value)
        : session
          ? (await api.resolve(hostId, session.sessionId, value)).file
          : null;
      if (!file) {
        setOpenError({ message: "Relative paths need an open session tab." });
      } else if (!file.preview) {
        setOpenError({ message: `${file.name} can't be previewed.`, hint: "Preview supports PDF, PNG, JPEG and SVG." });
      } else {
        onOpen({ hostId, path: file.path, name: file.name, kind: file.preview });
        setPath("");
      }
    } catch (err) {
      if (err instanceof ApiError) setOpenError(err);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel">
      <div className="flex items-center gap-1.5 border-b px-2 py-1.5">
        <form onSubmit={open} className="flex-1">
          <Input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder={session ? "Open file, e.g. plots/fig3.pdf" : "Open file by absolute path"}
            aria-label="Open file by path"
            className="h-8"
            spellCheck={false}
          />
        </form>
        <Button size="icon" variant="ghost" className="size-8" onClick={onToggleOrientation}
          aria-label={orientation === "horizontal" ? "Stack preview below" : "Show preview beside"}>
          {orientation === "horizontal" ? <Rows2 className="size-4" /> : <Columns2 className="size-4" />}
        </Button>
        <Button size="icon" variant="ghost" className="size-8" onClick={onClose} aria-label="Close preview">
          <X className="size-4" />
        </Button>
      </div>
      {openError && <ErrorNote error={openError} className="m-2" />}
      {target ? <PreviewBody key={`${target.hostId}:${target.path}`} target={target} /> : <EmptyPreview />}
    </div>
  );
}

function EmptyPreview() {
  return (
    <div className="grid flex-1 place-items-center p-6 text-center text-[13px] text-muted-foreground">
      <p className="max-w-xs">
        Open a plot from the file browser, or type a path relative to the session's working
        directory. The preview refreshes whenever the file changes.
      </p>
    </div>
  );
}

function PreviewBody({ target }: { target: PreviewTarget }) {
  const { version, previewUrl, downloadUrl, error, reload } = useWatchedFile(
    target.hostId,
    target.path,
  );
  const [shown, setShown] = useState<{ version: string; src: string } | null>(null);
  const [failedVersion, setFailedVersion] = useState<string | null>(null);
  const pending =
    version && previewUrl && version !== shown?.version
      ? { version, src: withVersion(previewUrl, version) }
      : null;
  const failed = failedVersion !== null && failedVersion === version;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
        <span className="truncate text-foreground" title={target.path}>{target.name}</span>
        <span className="flex-1" />
        <Button size="icon" variant="ghost" className="size-7" onClick={reload} aria-label="Reload preview">
          <RefreshCw className="size-3.5" />
        </Button>
        <Button size="icon" variant="ghost" className="size-7" asChild>
          <a href={downloadUrl ?? undefined} aria-label={`Download ${target.name}`}>
            <Download className="size-3.5" />
          </a>
        </Button>
      </div>
      {error && <ErrorNote error={error} className="mx-2 mb-2" />}
      {failed && <ErrorNote error={{ message: "The browser could not render this file." }} className="mx-2 mb-2" />}
      <div className="relative min-h-0 flex-1 overflow-hidden bg-[#0c1118]">
        {shown && <Layer target={target} src={shown.src} visible />}
        {pending && (
          <Layer
            key={pending.version}
            target={target}
            src={pending.src}
            visible={false}
            onLoad={() => setShown(pending)}
            onError={() => setFailedVersion(pending.version)}
          />
        )}
      </div>
    </div>
  );
}

interface LayerProps {
  target: PreviewTarget;
  src: string;
  visible: boolean;
  onLoad?: () => void;
  onError?: () => void;
}

/** New versions load in a hidden layer and replace the old one only once rendered. */
function Layer({ target, src, visible, onLoad, onError }: LayerProps) {
  const style = visible ? undefined : { visibility: "hidden" as const };
  if (target.kind === "pdf") {
    return (
      <iframe
        title={target.name}
        src={src}
        style={style}
        onLoad={onLoad}
        className="absolute inset-0 h-full w-full border-0"
      />
    );
  }
  // SVGs are only ever rendered through <img>: no scripts, no external loads.
  return (
    <img
      src={src}
      alt={target.name}
      style={style}
      onLoad={onLoad}
      onError={onError}
      className="absolute inset-0 h-full w-full object-contain p-3"
    />
  );
}
