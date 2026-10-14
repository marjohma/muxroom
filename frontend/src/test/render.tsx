import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

type Route = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined;

export function mockFetch(route: Route) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const hit = route(url, init);
    if (!hit) return new Response(JSON.stringify({ error: { code: "x", message: `unmocked ${url}` } }), { status: 500 });
    return new Response(JSON.stringify(hit.body), { status: hit.status ?? 200 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
