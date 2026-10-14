import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Session } from "@/api/types";
import { mockFetch, renderWithClient } from "@/test/render";
import { SessionRow } from "./SessionRow";

afterEach(() => vi.unstubAllGlobals());

const session: Session = {
  id: "$4", name: "0", created: "2026-10-14T10:00:00Z", last_activity: "2026-10-14T10:00:00Z",
  windows: 1, attached_clients: 1, path: "/home/user/projects/shot-analysis",
};

describe("SessionRow rename", () => {
  it("renames via PATCH with the session id", async () => {
    sessionStorage.setItem("muxroom.token", "k".repeat(43));
    const fetchMock = mockFetch((_url, init) =>
      init?.method === "PATCH" ? { body: { ...session, name: "kim-bulk" } } : undefined,
    );
    renderWithClient(<ul><SessionRow hostId="workstation" session={session} isOpen={false} onOpen={() => {}} /></ul>);
    fireEvent.click(screen.getByLabelText("Rename session 0"));
    const input = screen.getByLabelText("New name for session 0");
    fireEvent.change(input, { target: { value: "kim-bulk" } });
    fireEvent.submit(input);
    await waitFor(() => expect(screen.queryByLabelText("New name for session 0")).toBeNull());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/hosts/workstation/sessions/%244");
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ Authorization: `Bearer ${"k".repeat(43)}` });
    expect(JSON.parse(String(init?.body))).toEqual({ name: "kim-bulk" });
  });

  it("validates locally and shows server errors", async () => {
    const fetchMock = mockFetch(() => ({
      status: 409,
      body: { error: { code: "session_exists", message: "Session 'taken' already exists", hint: "Choose a different session name." } },
    }));
    renderWithClient(<ul><SessionRow hostId="h" session={session} isOpen onOpen={() => {}} /></ul>);
    fireEvent.doubleClick(screen.getByText("0"));
    const input = screen.getByLabelText("New name for session 0");
    fireEvent.change(input, { target: { value: "has space" } });
    fireEvent.submit(input);
    expect(screen.getByText(/Use 1–64 letters/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "taken" } });
    fireEvent.submit(input);
    expect(await screen.findByText("Session 'taken' already exists")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("New name for session 0"), { key: "Escape" });
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
