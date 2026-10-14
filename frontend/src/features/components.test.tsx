import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Host, HostStatus } from "@/api/types";
import { HostStrip } from "@/features/hosts/HostStrip";
import { PreviewPane } from "@/features/preview/PreviewPane";
import { NewSessionDialog } from "@/features/sessions/NewSessionDialog";
import { TabBar } from "@/features/tabs/TabBar";
import { mockFetch, renderWithClient } from "@/test/render";

beforeAll(() => {
  class RO {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

afterEach(() => {
  vi.unstubAllGlobals();
  class RO {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
  localStorage.clear();
});

const host: Host = {
  id: "work",
  label: "Workstation",
  transport: "openssh",
  agents: ["claude"],
  favorite_dirs: ["~/proj"],
  allowed_roots: ["~"],
  can_sign_in: true,
};

const status = (state: HostStatus["state"], extra: Partial<HostStatus> = {}): HostStatus => ({
  host_id: "work",
  state,
  checked_at: new Date().toISOString(),
  ...extra,
});

const noop = () => {};

describe("HostStrip", () => {
  it("lists sessions with attached-client pips", async () => {
    mockFetch((url) => {
      if (url.endsWith("/status")) return { body: status("ok", { latency_ms: 42, tmux_version: "tmux 3.5a" }) };
      if (url.endsWith("/sessions"))
        return {
          body: {
            fetched_at: "",
            sessions: [
              { id: "$1", name: "claude-proj", created: "2026-10-14T10:00:00Z", last_activity: "2026-10-14T10:00:00Z", windows: 1, attached_clients: 2, path: "/home/u/proj" },
            ],
          },
        };
    });
    const onOpen = vi.fn();
    renderWithClient(<HostStrip host={host} openSessionKeys={new Set()} onOpenSession={onOpen} onNewSession={noop} onSignIn={noop} />);
    expect(await screen.findByText("claude-proj")).toBeInTheDocument();
    expect(screen.getByLabelText("2 attached clients")).toBeInTheDocument();
    expect(await screen.findByText("42 ms")).toBeInTheDocument();
    fireEvent.click(screen.getByText("claude-proj"));
    expect(onOpen).toHaveBeenCalledWith(host, expect.objectContaining({ id: "$1" }));
  });

  it("offers sign-in and shows the hint when authentication fails", async () => {
    mockFetch((url) =>
      url.endsWith("/status")
        ? { body: status("auth_failed", { error: { code: "ssh_auth_failed", message: "Permission denied", hint: "Use Sign in" } }) }
        : undefined,
    );
    const onSignIn = vi.fn();
    renderWithClient(<HostStrip host={host} openSessionKeys={new Set()} onOpenSession={noop} onNewSession={noop} onSignIn={onSignIn} />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.getByText("Use Sign in")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Sign in to Workstation"));
    expect(onSignIn).toHaveBeenCalled();
    expect(screen.getByLabelText("New session on Workstation")).toBeDisabled();
  });

  it("invites creating a session when the host has none", async () => {
    mockFetch((url) => {
      if (url.endsWith("/status")) return { body: status("ok") };
      if (url.endsWith("/sessions")) return { body: { fetched_at: "", sessions: [] } };
    });
    renderWithClient(<HostStrip host={host} openSessionKeys={new Set()} onOpenSession={noop} onNewSession={noop} onSignIn={noop} />);
    expect(await screen.findByText("No tmux sessions yet. Start one.")).toBeInTheDocument();
  });
});

describe("NewSessionDialog", () => {
  it("suggests a name, validates it and shows server errors", async () => {
    sessionStorage.setItem("muxroom.token", "k".repeat(43));
    const fetchMock = mockFetch((url, init) => {
      if (url === "/api/agents") return { body: [{ id: "claude", label: "Claude Code" }] };
      if (init?.method === "POST")
        return { status: 409, body: { error: { code: "session_exists", message: "Session exists", hint: "Choose a different session name." } } };
    });
    renderWithClient(<NewSessionDialog host={host} open onOpenChange={noop} onCreated={noop} />);
    const name = (await screen.findByLabelText("Session name")) as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe("claude-proj"));
    fireEvent.change(name, { target: { value: "bad name" } });
    expect(screen.getByText(/Use 1–64 letters/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start session" })).toBeDisabled();
    fireEvent.change(name, { target: { value: "ok-name" } });
    fireEvent.click(screen.getByRole("button", { name: "Start session" }));
    expect(await screen.findByText("Session exists")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[1]?.headers).toMatchObject({ Authorization: `Bearer ${"k".repeat(43)}` });
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: "ok-name", cwd: "~/proj", agent: "claude" });
  });
});

describe("PreviewPane", () => {
  it("renders SVG only through <img>, never as a document", async () => {
    mockFetch((url) =>
      url.includes("/files/stat") ? { body: { path: "/p/fig.svg", name: "fig.svg", kind: "file", size: 10, mtime: "", version: "1-10", preview: "svg", outside_roots: false, download_url: "/api/hosts/work/files/content?path=%2Fp%2Ffig.svg&disposition=attachment&exp=1&sig=a", preview_url: "/api/hosts/work/files/content?path=%2Fp%2Ffig.svg&disposition=inline&exp=1&sig=b" } } : undefined,
    );
    const { container } = renderWithClient(
      <PreviewPane target={{ hostId: "work", path: "/p/fig.svg", name: "fig.svg", kind: "svg" }} session={null}
        orientation="horizontal" onOpen={noop} onClose={noop} onToggleOrientation={noop} />,
    );
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    expect(container.querySelector("iframe, object, embed")).toBeNull();
    const src = container.querySelector("img")?.getAttribute("src") ?? "";
    expect(src).toContain("disposition=inline");
    expect(src).toContain("sig=b");
    expect(src).toContain("v=1-10");
  });

  it("rejects non-previewable files opened by path", async () => {
    mockFetch((url) =>
      url.includes("/files/resolve")
        ? { body: { cwd: "/p", file: { path: "/p/a.txt", name: "a.txt", kind: "file", size: 1, mtime: "", version: "1-1", preview: null, outside_roots: false } } }
        : undefined,
    );
    const onOpen = vi.fn();
    renderWithClient(
      <PreviewPane target={null} session={{ hostId: "work", sessionId: "$1" }} orientation="horizontal"
        onOpen={onOpen} onClose={noop} onToggleOrientation={noop} />,
    );
    const input = screen.getByLabelText("Open file by path");
    fireEvent.change(input, { target: { value: "a.txt" } });
    fireEvent.submit(input);
    expect(await screen.findByText("a.txt can't be previewed.")).toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe("TabBar", () => {
  it("explains that closing a tab keeps the session running", () => {
    const onClose = vi.fn();
    renderWithClient(
      <TabBar
        tabs={[{ id: "t1", hostId: "work", sessionId: "$1", sessionName: "agent", createdAt: "", preview: null, orientation: "horizontal" }]}
        activeId="t1" hosts={[host]} previewOpen={false} onActivate={noop} onClose={onClose} onTogglePreview={noop}
      />,
    );
    fireEvent.click(screen.getByLabelText("Close tab agent (session keeps running)"));
    expect(onClose).toHaveBeenCalledWith("t1");
    expect(screen.getByText("Workstation")).toBeInTheDocument();
  });
});

describe("AgentIndicator", () => {
  it("shows a spinner while working and a keyboard while waiting", async () => {
    const { AgentIndicator } = await import("@/components/AgentIndicator");
    const { rerender } = renderWithClient(
      <AgentIndicator agent={{ id: "claude", label: "Claude Code", state: "working" }} />,
    );
    const working = screen.getByRole("img", { name: "Claude Code is working" });
    expect(working.querySelector("svg")?.getAttribute("class")).toContain("animate-spin");
    rerender(<AgentIndicator agent={{ id: "codex", label: "Codex CLI", state: "waiting" }} />);
    const waiting = screen.getByRole("img", { name: "Codex CLI is waiting for your input" });
    expect(waiting.querySelector("svg")?.getAttribute("class")).not.toContain("animate-spin");
    rerender(<AgentIndicator agent={null} />);
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("Tab agent state", () => {
  it("shows the live agent state of the tab's session", async () => {
    mockFetch((url) =>
      url.endsWith("/sessions")
        ? { body: { fetched_at: "", sessions: [{ id: "$1", name: "agent", created: "2026-10-14T10:00:00Z", last_activity: "", windows: 1, attached_clients: 1, path: "/p", agent: { id: "opencode", label: "OpenCode", state: "waiting" } }] } }
        : undefined,
    );
    renderWithClient(
      <TabBar
        tabs={[{ id: "t1", hostId: "work", sessionId: "$1", sessionName: "agent", createdAt: "2026-10-14T10:00:00Z", preview: null, orientation: "horizontal" }]}
        activeId="t1" hosts={[host]} previewOpen={false} onActivate={noop} onClose={noop} onTogglePreview={noop}
      />,
    );
    expect(await screen.findByRole("img", { name: "OpenCode is waiting for your input" })).toBeInTheDocument();
  });
});
