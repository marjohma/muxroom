import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Host } from "@/api/types";
import { mockFetch, renderWithClient } from "@/test/render";
import { AddHostDialog } from "./AddHostDialog";
import { draftProblems, hostIdFromAlias, parseList } from "./hostDraft";

afterEach(() => vi.unstubAllGlobals());

const local: Host = {
  id: "local", label: "Laptop", transport: "local", agents: [], favorite_dirs: [], allowed_roots: ["~"], can_sign_in: false,
};

describe("host draft helpers", () => {
  it("derives unique ids from aliases", () => {
    expect(hostIdFromAlias("Lab.Mac", [])).toBe("lab-mac");
    expect(hostIdFromAlias("lab", ["lab"])).toBe("lab-2");
    expect(hostIdFromAlias("_x", [])).toBe("x");
  });

  it("parses lists and flags problems", () => {
    expect(parseList("~/a, ~/b\n\n/c")).toEqual(["~/a", "~/b", "/c"]);
    const ok = { id: "lab", ssh_host: "lab", transport: "openssh" as const, tmux_path: "tmux",
      allowed_roots: ["~"], favorite_dirs: [], agents: null, upload_roots: [] };
    expect(draftProblems(ok)).toEqual([]);
    expect(draftProblems({ ...ok, allowed_roots: ["rel"] })).toHaveLength(1);
    expect(draftProblems({ ...ok, tmux_path: "bin/tmux", id: "Bad" })).toHaveLength(2);
  });
});

describe("AddHostDialog", () => {
  it("requires a successful test before adding and offers the tmux suggestion", async () => {
    const calls: string[] = [];
    let tmuxState: "tmux_missing" | "ok" = "tmux_missing";
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/ssh/aliases")
        return { body: { aliases: ["lab"], configured: [], editing_allowed: true, config_path: "/c/config.toml" } };
      if (url === "/api/agents") return { body: [] };
      if (url === "/api/hosts/test") {
        const draft = JSON.parse(String(init?.body));
        if (draft.tmux_path === "/opt/homebrew/bin/tmux") tmuxState = "ok";
        return {
          body: {
            status: { host_id: "lab", state: tmuxState, checked_at: "", tmux_version: "tmux 3.7", latency_ms: 20 },
            tmux_suggestion: tmuxState === "ok" ? null : "/opt/homebrew/bin/tmux",
          },
        };
      }
      if (url === "/api/hosts" && init?.method === "POST")
        return { status: 201, body: { host: { ...local, id: "lab", label: "lab", transport: "openssh" }, reload: { added: ["lab"], removed: [], changed: [], restart_required: [] } } };
    });
    const onAdded = vi.fn();
    renderWithClient(<AddHostDialog hosts={[local]} open onOpenChange={() => {}} onAdded={onAdded} />);

    expect(await screen.findByText("/c/config.toml")).toBeInTheDocument();
    // choose alias via the hidden native select Radix renders for forms
    fireEvent.click(screen.getByRole("combobox", { name: "SSH host" }));
    fireEvent.click(await screen.findByRole("option", { name: "lab" }));
    expect((screen.getByLabelText("Host id") as HTMLInputElement).value).toBe("lab");

    const addButton = screen.getByRole("button", { name: "Add host" });
    expect(addButton).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    fireEvent.click(await screen.findByRole("button", { name: "Use /opt/homebrew/bin/tmux" }));
    expect(addButton).toBeDisabled(); // draft changed: test again
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(addButton).toBeEnabled());
    fireEvent.click(addButton);
    await waitFor(() => expect(onAdded).toHaveBeenCalled());
    const post = calls.find((c) => c === "POST /api/hosts");
    expect(post).toBeDefined();
  });

  it("explains when editing is disabled", async () => {
    mockFetch((url) => {
      if (url === "/api/ssh/aliases")
        return { body: { aliases: ["lab"], configured: [], editing_allowed: false, config_path: "/c" } };
      if (url === "/api/agents") return { body: [] };
    });
    renderWithClient(<AddHostDialog hosts={[local]} open onOpenChange={() => {}} onAdded={() => {}} />);
    expect(await screen.findByText("Adding hosts from the UI is turned off.")).toBeInTheDocument();
  });
});
