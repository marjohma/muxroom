import { authHeaders } from "./auth";
import type {
  AddHostResult,
  Agent,
  CreateSessionRequest,
  ErrorBody,
  FileInfo,
  FileRoot,
  Host,
  HostDraft,
  HostStatus,
  HostTestResult,
  Listing,
  ReloadResult,
  ResolveResult,
  Session,
  SessionList,
  SshAliases,
} from "./types";

export class ApiError extends Error {
  readonly code: string;
  readonly hint: string | null;
  readonly status: number;

  constructor(status: number, body: ErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.hint = body.hint ?? null;
  }
}


export async function parseError(response: Response): Promise<ApiError> {
  try {
    const data = (await response.json()) as { error?: ErrorBody };
    if (data.error) return new ApiError(response.status, data.error);
  } catch {
    // fall through to the generic error
  }
  if (response.status === 404 || response.status === 405) {
    return new ApiError(response.status, {
      code: "unknown_endpoint",
      message: `The running muxroom backend does not support this action (HTTP ${response.status}).`,
      hint: "The browser UI is probably newer than the backend process. Restart muxroom; your tmux sessions keep running.",
    });
  }
  return new ApiError(response.status, {
    code: response.status === 401 ? "unauthorized" : "http_error",
    message: `Request failed with status ${response.status}`,
  });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "omit",
      ...init,
      headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
    });
  } catch {
    throw new ApiError(0, {
      code: "backend_unreachable",
      message: "muxroom backend is not reachable",
      hint: "Check that `muxroom` is still running.",
    });
  }
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

const q = encodeURIComponent;
export const sessionPath = (sessionId: string) => q(sessionId);

export const api = {
  reloadConfig: () => request<ReloadResult>("/api/config/reload", json("POST", {})),
  sshAliases: () => request<SshAliases>("/api/ssh/aliases"),
  testHost: (draft: HostDraft) => request<HostTestResult>("/api/hosts/test", json("POST", draft)),
  addHost: (draft: HostDraft) => request<AddHostResult>("/api/hosts", json("POST", draft)),
  agents: () => request<Agent[]>("/api/agents"),
  hosts: () => request<Host[]>("/api/hosts"),
  hostStatus: (hostId: string) => request<HostStatus>(`/api/hosts/${q(hostId)}/status`),
  sessions: (hostId: string) => request<SessionList>(`/api/hosts/${q(hostId)}/sessions`),
  renameSession: (hostId: string, sessionId: string, name: string) =>
    request<Session>(`/api/hosts/${q(hostId)}/sessions/${sessionPath(sessionId)}`, json("PATCH", { name })),
  createSession: (hostId: string, body: CreateSessionRequest) =>
    request<Session>(`/api/hosts/${q(hostId)}/sessions`, json("POST", body)),
  roots: (hostId: string) => request<FileRoot[]>(`/api/hosts/${q(hostId)}/files/roots`),
  list: (hostId: string, path: string) =>
    request<Listing>(`/api/hosts/${q(hostId)}/files/list?path=${q(path)}`),
  stat: (hostId: string, path: string) =>
    request<FileInfo>(`/api/hosts/${q(hostId)}/files/stat?path=${q(path)}`),
  resolve: (hostId: string, sessionId: string, path: string) =>
    request<ResolveResult>(
      `/api/hosts/${q(hostId)}/files/resolve`,
      json("POST", { session_id: sessionId, path }),
    ),
  upload: (hostId: string, dir: string, file: File, overwrite: boolean) =>
    request<FileInfo>(
      `/api/hosts/${q(hostId)}/files/content?dir=${q(dir)}&name=${q(file.name)}&overwrite=${overwrite}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      },
    ),
};

/** Add a cache-busting version to a server-issued (signed) content URL. */
export function withVersion(url: string, version: string): string {
  return `${url}&v=${encodeURIComponent(version)}`;
}

export function wsUrl(path: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}`;
}

export function terminalSocketPath(hostId: string, sessionId: string, cols: number, rows: number) {
  return `/api/hosts/${q(hostId)}/sessions/${sessionPath(sessionId)}/terminal?cols=${cols}&rows=${rows}`;
}

export function signInSocketPath(hostId: string, cols: number, rows: number) {
  return `/api/hosts/${q(hostId)}/signin?cols=${cols}&rows=${rows}`;
}
