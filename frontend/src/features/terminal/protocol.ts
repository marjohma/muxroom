export type ExitReason = "session_ended" | "detached" | "transport_lost" | "signed_in" | "sign_in_failed";

export type ServerMessage =
  | { type: "attached"; session_id?: string }
  | { type: "exit"; reason: ExitReason }
  | { type: "error"; code: string; message: string; hint?: string | null };

export function parseServerMessage(text: string): ServerMessage | null {
  try {
    const data = JSON.parse(text) as { type?: unknown };
    if (data && (data.type === "attached" || data.type === "exit" || data.type === "error")) {
      return data as ServerMessage;
    }
  } catch {
    // ignore malformed control frames
  }
  return null;
}

export function resizeMessage(cols: number, rows: number): string {
  return JSON.stringify({ type: "resize", cols, rows });
}

/** Close codes after which reconnecting cannot help. */
const FINAL_CLOSE_CODES = new Set([4401, 4403, 4404, 4422]);

export function shouldReconnect(closeCode: number, exit: ExitReason | null): boolean {
  if (exit === "session_ended" || exit === "detached") return false;
  return !FINAL_CLOSE_CODES.has(closeCode);
}

export const EXIT_TEXT: Record<ExitReason, string> = {
  session_ended: "The tmux session has ended.",
  detached: "This client was detached from tmux.",
  transport_lost: "Connection to the host was lost.",
  signed_in: "Signed in.",
  sign_in_failed: "Sign-in failed.",
};
