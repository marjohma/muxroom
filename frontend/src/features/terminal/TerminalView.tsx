import { RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { webSocketProtocols } from "@/api/auth";
import { terminalSocketPath, wsUrl } from "@/api/client";
import { Button } from "@/components/ui/button";
import { EXIT_TEXT, type ExitReason, parseServerMessage, resizeMessage, shouldReconnect } from "./protocol";
import { Reconnector } from "./reconnect";
import { createXterm } from "./useXterm";

const INPUT_CHUNK = 32 * 1024;

type ConnState =
  | { kind: "connecting" }
  | { kind: "live" }
  | { kind: "reconnecting"; delay: number }
  | { kind: "ended"; reason: ExitReason }
  | { kind: "error"; message: string; hint?: string | null };

interface Props {
  hostId: string;
  sessionId: string;
  active: boolean;
}

export function TerminalView({ hostId, sessionId, active }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const fitRef = useRef<() => void>(() => {});
  const focusRef = useRef<() => void>(() => {});
  const restartRef = useRef<() => void>(() => {});
  const [state, setState] = useState<ConnState>({ kind: "connecting" });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const { term, fit, dispose } = createXterm(container);
    fitRef.current = fit;
    focusRef.current = () => term.focus();
    const encoder = new TextEncoder();
    let socket: WebSocket | null = null;
    let exit: ExitReason | null = null;
    let failed = false;
    let disposed = false;
    const reconnector = new Reconnector(() => connect());

    const send = (data: string | Uint8Array<ArrayBuffer>) => {
      if (socket?.readyState !== WebSocket.OPEN) return;
      if (typeof data === "string") return socket.send(data);
      // The server accepts input frames up to 64 KiB; split large pastes.
      for (let offset = 0; offset < data.length; offset += INPUT_CHUNK) {
        socket.send(data.subarray(offset, offset + INPUT_CHUNK));
      }
    };

    function connect() {
      exit = null;
      failed = false;
      setState({ kind: "connecting" });
      const ws = new WebSocket(
        wsUrl(terminalSocketPath(hostId, sessionId, term.cols, term.rows)),
        webSocketProtocols(),
      );
      ws.binaryType = "arraybuffer";
      socket = ws;
      ws.onmessage = (event) => {
        if (typeof event.data !== "string") {
          term.write(new Uint8Array(event.data as ArrayBuffer));
          return;
        }
        const message = parseServerMessage(event.data);
        if (message?.type === "attached") {
          reconnector.succeeded();
          term.reset(); // tmux repaints the full screen on attach
          send(resizeMessage(term.cols, term.rows));
          setState({ kind: "live" });
        } else if (message?.type === "exit") {
          exit = message.reason;
        } else if (message?.type === "error") {
          failed = true;
          setState({ kind: "error", message: message.message, hint: message.hint });
        }
      };
      ws.onclose = (event) => {
        if (disposed || socket !== ws) return;
        socket = null;
        if (exit && !shouldReconnect(event.code, exit)) {
          setState({ kind: "ended", reason: exit });
        } else if (!shouldReconnect(event.code, exit)) {
          if (!failed) setState({ kind: "error", message: event.reason || `Closed (${event.code})` });
        } else {
          setState({ kind: "reconnecting", delay: reconnector.schedule() });
        }
      };
    }

    restartRef.current = () => {
      reconnector.cancel();
      reconnector.succeeded();
      connect();
    };
    const inputs = [
      term.onData((data) => send(encoder.encode(data))),
      term.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0) & 0xff))),
      term.onResize(({ cols, rows }) => send(resizeMessage(cols, rows))),
    ];
    connect();

    return () => {
      disposed = true;
      reconnector.cancel();
      inputs.forEach((d) => d.dispose());
      socket?.close(1000); // detaches only this tmux client
      dispose();
    };
  }, [hostId, sessionId]);

  useEffect(() => {
    if (active) {
      fitRef.current();
      focusRef.current();
    }
  }, [active]);

  return (
    <div className="relative h-full w-full bg-background">
      <div ref={containerRef} className="h-full w-full" data-testid="terminal" />
      {state.kind !== "live" && (
        <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-center p-3">
          <div className="pointer-events-auto flex max-w-xl items-center gap-3 rounded-md border bg-raised/95 px-3 py-2 text-[13px] shadow-lg">
            <span className="text-foreground">{describe(state)}</span>
            {state.kind === "error" && state.hint && <span className="text-muted-foreground">{state.hint}</span>}
            {(state.kind === "ended" || state.kind === "error" || state.kind === "reconnecting") && (
              <Button size="sm" variant="secondary" onClick={() => restartRef.current()}>
                <RotateCw className="size-3.5" /> Reconnect
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function describe(state: Exclude<ConnState, { kind: "live" }>): string {
  switch (state.kind) {
    case "connecting":
      return "Attaching…";
    case "reconnecting":
      return `Connection lost. Retrying in ${Math.round(state.delay / 100) / 10} s`;
    case "ended":
      return EXIT_TEXT[state.reason];
    case "error":
      return state.message;
  }
}
