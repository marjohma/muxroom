import { useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Download, File, FileImage, Folder, Link2Off, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/api/client";
import { keys, useListing, useRoots } from "@/api/queries";
import type { FileInfo, Host } from "@/api/types";
import { ErrorNote, type ErrorLike } from "@/components/ErrorNote";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PreviewTarget } from "@/features/tabs/tabs";
import { cn } from "@/lib/utils";
import { breadcrumbs, containingRoot, formatAge, formatSize } from "./paths";

interface Props {
  hosts: Host[];
  hostId: string;
  onHostChange: (hostId: string) => void;
  onPreview: (target: PreviewTarget) => void;
}

export function FileExplorer({ hosts, hostId, onHostChange, onPreview }: Props) {
  const client = useQueryClient();
  const roots = useRoots(hostId);
  const [paths, setPaths] = useState<Record<string, string>>({});
  const path = paths[hostId] ?? roots.data?.[0]?.path ?? null;
  const setPath = (next: string) => setPaths((p) => ({ ...p, [hostId]: next }));
  const listing = useListing(hostId, path);
  const [uploadError, setUploadError] = useState<ErrorLike | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const rootPaths = useMemo(() => (roots.data ?? []).map((r) => r.path), [roots.data]);
  const currentRoot = listing.data ? containingRoot(listing.data.path, rootPaths) : null;
  const writable = !!listing.data && (roots.data ?? []).some(
    (r) => r.writable && containingRoot(listing.data!.path, [r.path]) !== null,
  );
  const error = (listing.error ?? roots.error) instanceof ApiError ? (listing.error ?? roots.error) as ApiError : null;

  const upload = async (files: FileList | File[]) => {
    if (!listing.data || !writable) return;
    setUploadError(null);
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        try {
          await api.upload(hostId, listing.data.path, file, false);
        } catch (err) {
          if (err instanceof ApiError && err.code === "file_exists"
              && window.confirm(`${file.name} already exists. Replace it?`)) {
            await api.upload(hostId, listing.data.path, file, true);
          } else {
            throw err;
          }
        }
      }
    } catch (err) {
      if (err instanceof ApiError) setUploadError(err);
    } finally {
      setUploading(false);
      void client.invalidateQueries({ queryKey: keys.listing(hostId, listing.data.path) });
    }
  };

  const activate = (entry: FileInfo) => {
    if (entry.outside_roots) return;
    if (entry.kind === "dir") setPath(entry.path);
    else if (entry.preview) onPreview({ hostId, path: entry.path, name: entry.name, kind: entry.preview });
    else if (entry.download_url) window.location.assign(entry.download_url);
  };

  return (
    <div
      className={cn("flex min-h-0 flex-1 flex-col", dragging && "outline-2 -outline-offset-2 outline-signal")}
      onDragOver={(e) => {
        if (writable) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
      }}
    >
      <div className="grid gap-2 border-b px-3 py-2.5">
        <div className="flex gap-2">
          <Select value={hostId} onValueChange={onHostChange}>
            <SelectTrigger className="h-8 flex-1" aria-label="Host">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {hosts.map((h) => (
                <SelectItem key={h.id} value={h.id}>{h.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {rootPaths.length > 1 && (
            <Select value={currentRoot ?? undefined} onValueChange={setPath}>
              <SelectTrigger className="h-8 w-28" aria-label="Root folder">
                <SelectValue placeholder="Root" />
              </SelectTrigger>
              <SelectContent>
                {rootPaths.map((r) => (
                  <SelectItem key={r} value={r}>{r}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
        {listing.data && currentRoot && (
          <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-x-1 text-[12.5px]">
            {breadcrumbs(listing.data.path, currentRoot).map((crumb, i, all) => (
              <span key={crumb.path} className="flex items-center gap-1">
                {i > 0 && <span className="text-muted-foreground">/</span>}
                <button
                  type="button"
                  onClick={() => setPath(crumb.path)}
                  className={cn("hover:text-signal", i === all.length - 1 ? "text-foreground" : "text-muted-foreground")}
                  title={crumb.path}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </nav>
        )}
        <div className="flex gap-1.5">
          <Button size="sm" variant="ghost" disabled={!listing.data?.parent}
            onClick={() => listing.data?.parent && setPath(listing.data.parent)}>
            <ArrowUp className="size-3.5" /> Up
          </Button>
          <Button size="sm" variant="ghost" disabled={!writable || uploading}
            title={writable ? undefined : "Uploads are allowed only in upload folders (config.toml)"}
            onClick={() => fileInput.current?.click()}>
            <Upload className="size-3.5" /> {uploading ? "Uploading…" : "Upload"}
          </Button>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => e.target.files && void upload(e.target.files)} />
        </div>
      </div>

      {error && <ErrorNote error={error} className="m-3" />}
      {uploadError && <ErrorNote error={uploadError} className="m-3" />}
      {roots.data && roots.data.length === 0 && (
        <p className="m-3 text-[13px] text-muted-foreground">
          No readable folders are configured for this host. Add read_roots in config.toml.
        </p>
      )}

      <ul className="min-h-0 flex-1 overflow-y-auto py-1" aria-label="Files">
        {listing.data?.entries.map((entry) => (
          <li key={entry.path} className="group flex items-center">
            <button
              type="button"
              disabled={entry.outside_roots}
              onClick={() => activate(entry)}
              title={entry.outside_roots ? "Link points outside the allowed folders" : entry.path}
              className="flex min-w-0 flex-1 items-center gap-2 px-3 py-1 text-left text-[13px] hover:bg-accent/60 disabled:cursor-not-allowed disabled:opacity-45"
            >
              <EntryIcon entry={entry} />
              <span className="truncate">{entry.name}</span>
              <span className="ml-auto shrink-0 tabular-nums text-[12px] text-muted-foreground">
                {entry.kind === "dir" ? "" : formatSize(entry.size)}
              </span>
            </button>
            {entry.download_url && (
              <a
                href={entry.download_url}
                className="mr-2 rounded p-1 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
                aria-label={`Download ${entry.name}`}
                title={`Modified ${formatAge(entry.mtime)}`}
              >
                <Download className="size-3.5" />
              </a>
            )}
          </li>
        ))}
        {listing.data?.truncated && (
          <li className="px-3 py-2 text-[12px] text-muted-foreground">Only the first entries are shown.</li>
        )}
        {listing.data && listing.data.entries.length === 0 && (
          <li className="px-3 py-2 text-[13px] text-muted-foreground">This folder is empty.</li>
        )}
      </ul>
    </div>
  );
}

function EntryIcon({ entry }: { entry: FileInfo }) {
  const cls = "size-4 shrink-0";
  if (entry.outside_roots) return <Link2Off className={cn(cls, "text-muted-foreground")} />;
  if (entry.kind === "dir") return <Folder className={cn(cls, "text-signal/80")} />;
  if (entry.preview) return <FileImage className={cn(cls, "text-ok/80")} />;
  return <File className={cn(cls, "text-muted-foreground")} />;
}
