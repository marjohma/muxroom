export type TransportKind = "local" | "openssh" | "asyncssh";

export interface Agent {
  id: string;
  label: string;
}

export interface Host {
  id: string;
  label: string;
  transport: TransportKind;
  agents: string[];
  favorite_dirs: string[];
  allowed_roots: string[];
  can_sign_in: boolean;
}

export interface ErrorBody {
  code: string;
  message: string;
  hint?: string | null;
  host_id?: string | null;
}

export type HostState = "ok" | "unreachable" | "auth_failed" | "host_key" | "tmux_missing" | "error";

export interface HostStatus {
  host_id: string;
  state: HostState;
  checked_at: string;
  tmux_version?: string | null;
  home?: string | null;
  latency_ms?: number | null;
  error?: ErrorBody | null;
}

export interface AgentActivity {
  id: string;
  label: string;
  state: "working" | "waiting";
}

export interface Session {
  id: string;
  name: string;
  created: string;
  last_activity: string;
  windows: number;
  attached_clients: number;
  path: string;
  agent?: AgentActivity | null;
}

export interface SessionList {
  sessions: Session[];
  fetched_at: string;
}

export interface CreateSessionRequest {
  name: string;
  cwd: string;
  agent: string;
}

export type PreviewKind = "pdf" | "png" | "jpeg" | "svg";

export interface FileInfo {
  path: string;
  name: string;
  kind: "file" | "dir" | "symlink" | "other";
  size: number;
  mtime: string;
  version: string;
  preview: PreviewKind | null;
  outside_roots: boolean;
  /** Signed, short-lived URLs (usable by <img>, <iframe> and downloads). */
  download_url: string | null;
  preview_url: string | null;
}

export interface FileRoot {
  path: string;
  writable: boolean;
}

export interface Listing {
  path: string;
  parent: string | null;
  entries: FileInfo[];
  truncated: boolean;
}

export interface CompletionItem {
  name: string;
  kind: FileInfo["kind"];
  preview: PreviewKind | null;
  /** Full text to put into the path field. */
  value: string;
}

export interface Completion {
  cwd: string | null;
  directory: string;
  items: CompletionItem[];
  truncated: boolean;
}

export interface ResolveResult {
  cwd: string;
  file: FileInfo;
}

export interface ReloadResult {
  added: string[];
  removed: string[];
  changed: string[];
  restart_required: string[];
}

export interface SshAliases {
  aliases: string[];
  configured: string[];
  editing_allowed: boolean;
  config_path: string;
}

export interface HostDraft {
  id: string;
  ssh_host: string;
  transport: "openssh" | "asyncssh";
  label?: string | null;
  tmux_path: string;
  allowed_roots: string[];
  favorite_dirs: string[];
  agents: string[] | null;
  upload_roots: string[];
}

export interface HostTestResult {
  status: HostStatus;
  tmux_suggestion: string | null;
}

export interface AddHostResult {
  host: Host;
  reload: ReloadResult;
}
