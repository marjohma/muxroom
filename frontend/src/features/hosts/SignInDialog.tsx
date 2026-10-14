import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { webSocketProtocols } from "@/api/auth";
import { signInSocketPath, wsUrl } from "@/api/client";
import { keys } from "@/api/queries";
import type { Host } from "@/api/types";
import { ErrorNote, type ErrorLike } from "@/components/ErrorNote";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { parseServerMessage } from "@/features/terminal/protocol";
import { createXterm } from "@/features/terminal/useXterm";

interface Props {
  host: Host;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Runs `ssh -f -N <host>` interactively; the password goes straight to ssh. */
export function SignInDialog({ host, open, onOpenChange }: Props) {
  const client = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const [result, setResult] = useState<"pending" | "signed_in" | "failed">("pending");
  const [error, setError] = useState<ErrorLike | null>(null);

  useEffect(() => {
    if (!open) return;
    let cleanup = () => {};
    const frame = requestAnimationFrame(() => {
      const container = containerRef.current;
      if (!container) return;
      const { term, dispose } = createXterm(container);
      const ws = new WebSocket(
        wsUrl(signInSocketPath(host.id, term.cols || 80, term.rows || 12)),
        webSocketProtocols(),
      );
      ws.binaryType = "arraybuffer";
      const encoder = new TextEncoder();
      ws.onmessage = (event) => {
        if (typeof event.data !== "string") {
          term.write(new Uint8Array(event.data as ArrayBuffer));
          return;
        }
        const message = parseServerMessage(event.data);
        if (message?.type === "exit") {
          const ok = message.reason === "signed_in";
          setResult(ok ? "signed_in" : "failed");
          if (ok) {
            void client.invalidateQueries({ queryKey: keys.status(host.id) });
            void client.invalidateQueries({ queryKey: keys.sessions(host.id) });
          }
        } else if (message?.type === "error") {
          setError(message);
        }
      };
      const input = term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
      });
      term.focus();
      cleanup = () => {
        input.dispose();
        ws.close(1000);
        dispose();
      };
    });
    return () => {
      cancelAnimationFrame(frame);
      cleanup();
    };
  }, [open, host.id, client]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Sign in to {host.label}</DialogTitle>
          <DialogDescription>
            Type your password or one-time code below. It goes straight to ssh; muxroom never
            sees or stores it. ssh then keeps a shared connection open for muxroom to reuse.
          </DialogDescription>
        </DialogHeader>
        <div className="h-56 overflow-hidden rounded-md border bg-background" ref={containerRef} />
        {error && <ErrorNote error={error} />}
        {result === "signed_in" && <p className="text-[13px] text-ok">Signed in. You can close this dialog.</p>}
        {result === "failed" && (
          <p className="text-[13px] text-fault">ssh exited with an error. Check the output above.</p>
        )}
        <div className="flex justify-end">
          <Button variant={result === "signed_in" ? "default" : "ghost"} onClick={() => onOpenChange(false)}>
            {result === "signed_in" ? "Done" : "Close"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
