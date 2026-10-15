import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch, renderWithClient } from "@/test/render";
import { PathInput } from "./PathInput";
import { PreviewPane } from "./PreviewPane";
import { addRecent, loadRecent, parentDir, MAX_RECENT_PER_HOST, recentForHost, removeRecent, type RecentPreview } from "./recent";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
});

const target = (path: string, hostId = "ws") => ({
  hostId,
  path,
  name: path.split("/").pop()!,
  kind: (path.endsWith(".pdf") ? "pdf" : "png") as "pdf" | "png",
});

describe("recent previews", () => {
  it("keeps one entry per file, newest first, capped per host", () => {
    addRecent(localStorage, target("/s/a.pdf"), 1);
    addRecent(localStorage, target("/s/b.png"), 2);
    addRecent(localStorage, target("/s/a.pdf"), 3);
    addRecent(localStorage, target("/x/c.pdf", "cluster"), 4);
    const all = loadRecent(localStorage);
    expect(recentForHost(all, "ws").map((r) => r.path)).toEqual(["/s/a.pdf", "/s/b.png"]);
    for (let i = 0; i < 30; i++) addRecent(localStorage, target(`/s/f${i}.png`), 10 + i);
    const capped = loadRecent(localStorage);
    expect(recentForHost(capped, "ws")).toHaveLength(MAX_RECENT_PER_HOST);
    expect(recentForHost(capped, "cluster")).toHaveLength(1);
    expect(recentForHost(removeRecent(localStorage, "cluster", "/x/c.pdf"), "cluster")).toEqual([]);
  });

  it("ignores corrupt storage", () => {
    localStorage.setItem("muxroom.recentPreviews.v1", "{not json");
    expect(loadRecent(localStorage)).toEqual([]);
    localStorage.setItem("muxroom.recentPreviews.v1", JSON.stringify([{ path: 1 }, { hostId: "a" }]));
    expect(loadRecent(localStorage)).toEqual([]);
  });
});

const recents: RecentPreview[] = [{ ...target("/home/u/study/plots/q.pdf"), openedAt: 1 }];

describe("PathInput", () => {
  it("offers recent files when empty and opens one on click", () => {
    const onOpenRecent = vi.fn();
    renderWithClient(
      <PathInput hostId="ws" sessionId="$1" recent={recents} onSubmitPath={async () => true}
        onOpenRecent={onOpenRecent} onForgetRecent={() => {}} />,
    );
    fireEvent.focus(screen.getByLabelText("Open file by path"));
    expect(screen.getByRole("listbox", { name: "Recently previewed files" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: /q\.pdf/ }));
    expect(onOpenRecent).toHaveBeenCalledWith(recents[0]);
  });

  it("forgets a recent file", () => {
    const onForget = vi.fn();
    renderWithClient(
      <PathInput hostId="ws" sessionId={null} recent={recents} onSubmitPath={async () => true}
        onOpenRecent={() => {}} onForgetRecent={onForget} />,
    );
    fireEvent.focus(screen.getByLabelText("Open file by path"));
    fireEvent.click(screen.getByLabelText("Remove q.pdf from recent files"));
    expect(onForget).toHaveBeenCalledWith(recents[0]);
  });

  it("completes folders and files relative to the session", async () => {
    const fetchMock = mockFetch((url) => {
      const path = new URL(url, "http://x").searchParams.get("path");
      if (path === "pl") return { body: { cwd: "/s", directory: "/s", truncated: false, items: [
        { name: "plots", kind: "dir", preview: null, value: "plots/" }] } };
      if (path === "plots/") return { body: { cwd: "/s", directory: "/s/plots", truncated: false, items: [
        { name: "fig.pdf", kind: "file", preview: "pdf", value: "plots/fig.pdf" },
        { name: "fig.png", kind: "file", preview: "png", value: "plots/fig.png" }] } };
    });
    const onSubmitPath = vi.fn(async () => true);
    renderWithClient(
      <PathInput hostId="ws" sessionId="$1" recent={[]} onSubmitPath={onSubmitPath}
        onOpenRecent={() => {}} onForgetRecent={() => {}} />,
    );
    const input = screen.getByLabelText("Open file by path") as HTMLInputElement;
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "pl" } });
    await screen.findByRole("option", { name: "plots/" });
    expect(fetchMock.mock.calls[0][0]).toContain("session_id=%241");
    fireEvent.keyDown(input, { key: "Tab" }); // accept the only suggestion
    expect(input.value).toBe("plots/");
    await screen.findByRole("option", { name: "fig.png" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
    expect(onSubmitPath).toHaveBeenCalledWith("plots/fig.png");
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("submits typed text with Enter when no suggestion is selected", async () => {
    mockFetch(() => ({ body: { cwd: null, directory: "/", truncated: false, items: [] } }));
    const onSubmitPath = vi.fn(async () => false);
    renderWithClient(
      <PathInput hostId="ws" sessionId={null} recent={[]} onSubmitPath={onSubmitPath}
        onOpenRecent={() => {}} onForgetRecent={() => {}} />,
    );
    const input = screen.getByLabelText("Open file by path") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "/data/x.pdf" } });
    await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
    expect(onSubmitPath).toHaveBeenCalledWith("/data/x.pdf");
    expect(input.value).toBe("/data/x.pdf"); // kept when opening failed
  });
});

describe("preview reload button", () => {
  it("re-renders the file even when its version is unchanged", async () => {
    mockFetch((url) =>
      url.includes("/files/stat")
        ? { body: { path: "/p/fig.png", name: "fig.png", kind: "file", size: 1, mtime: "", version: "7-1",
            preview: "png", outside_roots: false, download_url: "/d", preview_url: "/api/hosts/ws/files/content?sig=s" } }
        : undefined,
    );
    const { container } = renderWithClient(
      <PreviewPane target={target("/p/fig.png")} session={null} orientation="horizontal" onOpen={() => {}}
        onClose={() => {}} onToggleOrientation={() => {}} recent={[]} onForgetRecent={() => {}} />,
    );
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    fireEvent.load(container.querySelector("img")!);
    expect(container.querySelector("img")!.getAttribute("src")).toContain("r=0");
    fireEvent.click(screen.getByLabelText("Reload preview"));
    await waitFor(() =>
      expect([...container.querySelectorAll("img")].some((i) => i.getAttribute("src")?.includes("r=1"))).toBe(true),
    );
  });
});

describe("PDF preview", () => {
  it("shows the PDF without waiting for a load event and follows new versions", async () => {
    let version = "1-10";
    mockFetch((url) =>
      url.includes("/files/stat")
        ? { body: { path: "/p/q.pdf", name: "q.pdf", kind: "file", size: 10, mtime: "", version,
            preview: "pdf", outside_roots: false, download_url: "/d", preview_url: "/api/hosts/ws/files/content?sig=s" } }
        : undefined,
    );
    const { container } = renderWithClient(
      <PreviewPane target={target("/p/q.pdf")} session={null} orientation="horizontal" onOpen={() => {}}
        onClose={() => {}} onToggleOrientation={() => {}} recent={[]} onForgetRecent={() => {}} />,
    );
    await waitFor(() => expect(container.querySelector("iframe")).not.toBeNull());
    const frame = () => container.querySelector("iframe")!;
    expect(frame().style.visibility).not.toBe("hidden"); // no load event was fired
    expect(frame().getAttribute("src")).toContain("v=1-10");
    expect(container.querySelectorAll("iframe")).toHaveLength(1);
    fireEvent.click(screen.getByLabelText("Reload preview"));
    await waitFor(() => expect(frame().getAttribute("src")).toContain("r=1"));
    version = "2-20";
    fireEvent.click(screen.getByLabelText("Reload preview"));
    await waitFor(() => expect(frame().getAttribute("src")).toContain("v=2-20"));
  });
});

describe("parentDir", () => {
  it("keeps the meaningful end of long paths", () => {
    expect(parentDir("/home/u/study/plots/q.pdf")).toBe("…/study/plots");
    expect(parentDir("/data/q.pdf")).toBe("/data");
    expect(parentDir("/q.pdf")).toBe("/");
  });
});
