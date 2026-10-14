import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { sessionsRefreshInterval, windowFocused } from "./refresh";
import type { CreateSessionRequest } from "./types";

export const keys = {
  agents: ["agents"] as const,
  hosts: ["hosts"] as const,
  status: (hostId: string) => ["status", hostId] as const,
  sessions: (hostId: string) => ["sessions", hostId] as const,
  roots: (hostId: string) => ["roots", hostId] as const,
  listing: (hostId: string, path: string) => ["listing", hostId, path] as const,
};

export const useAgents = () => useQuery({ queryKey: keys.agents, queryFn: api.agents, staleTime: Infinity });
export const useHosts = () => useQuery({ queryKey: keys.hosts, queryFn: api.hosts, staleTime: Infinity });

export const useHostStatus = (hostId: string) =>
  useQuery({
    queryKey: keys.status(hostId),
    queryFn: () => api.hostStatus(hostId),
    refetchInterval: 15_000,
  });

export const useSessions = (hostId: string, enabled = true) =>
  useQuery({
    queryKey: keys.sessions(hostId),
    queryFn: () => api.sessions(hostId),
    refetchInterval: (query) => sessionsRefreshInterval(query.state.data, windowFocused()),
    enabled,
    retry: false,
  });

/** After the host list changes, refetch everything that depends on it. */
export function useConfigChanged() {
  const client = useQueryClient();
  return () => client.invalidateQueries();
}

export function useCreateSession(hostId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) => api.createSession(hostId, body),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.sessions(hostId) }),
  });
}

export function useRenameSession(hostId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, name }: { sessionId: string; name: string }) =>
      api.renameSession(hostId, sessionId, name),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.sessions(hostId) }),
  });
}

export const useRoots = (hostId: string, enabled = true) =>
  useQuery({ queryKey: keys.roots(hostId), queryFn: () => api.roots(hostId), enabled, retry: false });

export const useListing = (hostId: string, path: string | null) =>
  useQuery({
    queryKey: keys.listing(hostId, path ?? ""),
    queryFn: () => api.list(hostId, path as string),
    enabled: path !== null,
    retry: false,
  });
