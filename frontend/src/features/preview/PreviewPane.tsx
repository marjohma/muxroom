import { Columns2, Download, RefreshCw, Rows2, X } from "lucide-react";
import { useState } from "react";
import { api, ApiError, withVersion } from "@/api/client";
import { ErrorNote, type ErrorLike } from "@/components/ErrorNote";
import { Button } from "@/components/ui/button";
import type { Orientation, PreviewTarget } from "@/features/tabs/tabs";
import { PathInput } from "./PathInput";
import { recentForHost, type RecentPreview } from "./recent";
import { useWatchedFile } from "./useWatchedFile";

interface Props {
  target: PreviewTarget | null;
  session: { hostId: string; sessionId: string } | null;
  orientation: Orientation;
  onOpen: (target: PreviewTarget) => void;
  onClose: () => void;
  onToggleOrientation: () => void;
  recent: RecentPreview[];
  onForgetRecent: (recent: RecentPreview) => void;
}

export function PreviewPane({
  target,
  session,
  orientation,
  onOpen,
  onClose,
  onToggleOrientation,
  recent,
  onForgetRecent,
}: Props) {
  const [openError, setOpenError] = useState<ErrorLike | null>(null);
  const hostId = session?.hostId ?? target?.hostId ?? null;

  /** Open a typed or completed path; returns true when a preview was opened. */
  const openPath = async (value: string): Promise<boolean> => {
    setOpenError(null);
    if (!hostId) return false;
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
        return true;
      }
    } catch (err) {
      if (err instanceof ApiError) setOpenError(err);
    }
    return false;
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel">
      <div className="flex items-center gap-1.5 border-b px-2 py-1.5">
        <PathInput
          hostId={hostId}
          sessionId={session?.sessionId ?? null}
          recent={hostId ? recentForHost(recent, hostId) : []}
          onSubmitPath={openPath}
          onOpenRecent={(r) => {
            setOpenError(null);
            onOpen({ hostId: r.hostId, path: r.path, name: r.name, kind: r.kind });
          }}
          onForgetRecent={onForgetRecent}
        />
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
  const [shown, setShown] = useState<{ key: string; src: string } | null>(null);
  const [failedVersion, setFailedVersion] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  // A manual reload must re-render even when the file's version is unchanged.
  const key = `${version}#${reloads}`;
  const pending =
    version && previewUrl && key !== shown?.key
      ? { key, version, src: `${withVersion(previewUrl, version)}&r=${reloads}` }
      : null;
  const failed = failedVersion !== null && failedVersion === version;
  const current =
    version && previewUrl
      ? { key, src: `${withVersion(previewUrl, version)}&r=${reloads}` }
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5 text-[12.5px] text-muted-foreground">
        <span className="truncate text-foreground" title={target.path}>{target.name}</span>
        <span className="flex-1" />
        <Button
          size="icon"
          variant="ghost"
          className="size-7"
          onClick={() => {
            reload();
            setReloads((n) => n + 1);
          }}
          aria-label="Reload preview"
          title="Reload preview"
        >
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
        {target.kind === "pdf" ? (
          // Browsers do not reliably fire `load` for PDFs in iframes, so PDFs
          // switch directly to the newest version instead of double-buffering.
          current && <Layer key={current.key} target={target} src={current.src} visible />
        ) : (
          shown && <Layer target={target} src={shown.src} visible />
        )}
        {target.kind !== "pdf" && pending && (
          <Layer
            key={pending.key}
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
