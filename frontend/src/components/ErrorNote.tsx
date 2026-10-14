import { cn } from "@/lib/utils";

export interface ErrorLike {
  message: string;
  hint?: string | null;
  code?: string;
}

export function ErrorNote({ error, className }: { error: ErrorLike; className?: string }) {
  return (
    <div role="alert" className={cn("rounded-md border border-fault/40 bg-fault/10 px-3 py-2 text-[13px]", className)}>
      <p className="text-foreground">{error.message}</p>
      {error.hint && <p className="mt-1 text-muted-foreground">{error.hint}</p>}
    </div>
  );
}
