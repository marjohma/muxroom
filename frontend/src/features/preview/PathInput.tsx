import { FileImage, FileText, Folder, History, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { api } from "@/api/client";
import type { CompletionItem } from "@/api/types";
import { Input } from "@/components/ui/input";
import type { PreviewTarget } from "@/features/tabs/tabs";
import { cn } from "@/lib/utils";
import { parentDir, type RecentPreview } from "./recent";

type Option =
  | { type: "recent"; recent: RecentPreview }
  | { type: "completion"; item: CompletionItem };

interface Props {
  hostId: string | null;
  sessionId: string | null;
  recent: RecentPreview[];
  onSubmitPath: (path: string) => Promise<boolean>;
  onOpenRecent: (target: PreviewTarget) => void;
  onForgetRecent: (recent: RecentPreview) => void;
}

const DEBOUNCE_MS = 150;

/** Path field with recent files (when empty) and folder/file completion (when typing). */
export function PathInput({ hostId, sessionId, recent, onSubmitPath, onOpenRecent, onForgetRecent }: Props) {
  const listId = useId();
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [completions, setCompletions] = useState<{ query: string; items: CompletionItem[] }>({
    query: "",
    items: [],
  });
  const [active, setActive] = useState(-1);

  const query = value.trim();
  useEffect(() => {
    if (!hostId || !focused || !query) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      api.complete(hostId, query, sessionId).then(
        (result) => !cancelled && setCompletions({ query, items: result.items }),
        () => !cancelled && setCompletions({ query, items: [] }),
      );
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [hostId, sessionId, query, focused]);

  const options: Option[] = query
    ? completions.query === query
      ? completions.items.map((item) => ({ type: "completion", item }))
      : []
    : recent.map((r) => ({ type: "recent", recent: r }));
  const open = focused && !dismissed && options.length > 0;

  const update = (next: string) => {
    setValue(next);
    setActive(-1);
    setDismissed(false);
  };

  const choose = async (option: Option) => {
    if (option.type === "recent") {
      onOpenRecent(option.recent);
      update("");
      return;
    }
    if (option.item.kind === "dir") {
      update(option.item.value); // descend; completions for the folder follow
      return;
    }
    if (await onSubmitPath(option.item.value)) update("");
  };

  const onKeyDown = async (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      setDismissed(false);
      setActive((i) => (i + 1) % options.length);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      setActive((i) => (i <= 0 ? options.length - 1 : i - 1));
    } else if (event.key === "Tab" && open) {
      const option = options[Math.max(active, 0)];
      if (option?.type === "completion") {
        event.preventDefault();
        update(option.item.value);
      }
    } else if (event.key === "Escape") {
      setDismissed(true);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (open && active >= 0) await choose(options[active]);
      else if (query && (await onSubmitPath(query))) update("");
    }
  };

  return (
    <div className="relative flex-1">
      <Input
        value={value}
        onChange={(e) => update(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 120)}
        onKeyDown={onKeyDown}
        placeholder={sessionId ? "Open file, e.g. plots/fig3.pdf" : "Open file by absolute path"}
        aria-label="Open file by path"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        className="h-8"
        spellCheck={false}
        autoComplete="off"
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={query ? "Matching files and folders" : "Recently previewed files"}
          className="absolute top-full right-0 left-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-md border bg-popover py-1 shadow-lg"
        >
          {!query && (
            <li role="presentation" className="flex items-center gap-1.5 px-3 pt-1 pb-1.5 text-[11.5px] text-muted-foreground">
              <History className="size-3" /> Recently previewed
            </li>
          )}
          {options.map((option, index) => (
            <li
              key={option.type === "recent" ? `r:${option.recent.path}` : `c:${option.item.value}`}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void choose(option)}
              onMouseEnter={() => setActive(index)}
              className={cn(
                "group flex cursor-pointer items-center gap-2 px-3 py-1 text-[13px]",
                index === active && "bg-accent",
              )}
            >
              <OptionIcon option={option} />
              {option.type === "recent" ? (
                <>
                  <span className="truncate">{option.recent.name}</span>
                  <span className="min-w-0 flex-1 truncate text-right text-[11.5px] text-muted-foreground" title={option.recent.path}>
                    {parentDir(option.recent.path)}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${option.recent.name} from recent files`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onForgetRecent(option.recent);
                    }}
                    className="rounded p-0.5 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                </>
              ) : (
                <span className="truncate">
                  {option.item.name}
                  {option.item.kind === "dir" ? "/" : ""}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function OptionIcon({ option }: { option: Option }) {
  const cls = "size-3.5 shrink-0";
  const kind = option.type === "recent" ? option.recent.kind : option.item.preview;
  if (option.type === "completion" && option.item.kind === "dir") return <Folder className={cn(cls, "text-signal/80")} />;
  if (kind === "pdf") return <FileText className={cn(cls, "text-attention")} />;
  return <FileImage className={cn(cls, "text-ok/80")} />;
}
