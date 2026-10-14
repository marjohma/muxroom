import type { PreviewKind, Session } from "@/api/types";

export type Orientation = "horizontal" | "vertical";

export interface PreviewTarget {
  hostId: string;
  path: string;
  name: string;
  kind: PreviewKind;
}

export interface Tab {
  id: string;
  hostId: string;
  sessionId: string;
  sessionName: string;
  createdAt: string;
  stale?: boolean;
  preview: PreviewTarget | null;
  orientation: Orientation;
}

export interface TabsState {
  tabs: Tab[];
  activeId: string | null;
}

export type TabsAction =
  | { type: "open"; hostId: string; session: Pick<Session, "id" | "name" | "created"> }
  | { type: "close"; id: string }
  | { type: "activate"; id: string }
  | { type: "markStale"; id: string; stale: boolean }
  | { type: "setName"; id: string; name: string }
  | { type: "setPreview"; id: string; preview: PreviewTarget | null }
  | { type: "setOrientation"; id: string; orientation: Orientation };

export const EMPTY: TabsState = { tabs: [], activeId: null };
export const STORAGE_KEY = "muxroom.tabs.v1";

export const tabId = (hostId: string, sessionId: string, createdAt: string) =>
  `${hostId}/${sessionId}/${createdAt}`;

function update(state: TabsState, id: string, patch: Partial<Tab>): TabsState {
  return { ...state, tabs: state.tabs.map((t) => (t.id === id ? { ...t, ...patch } : t)) };
}

export function tabsReducer(state: TabsState, action: TabsAction): TabsState {
  switch (action.type) {
    case "open": {
      const id = tabId(action.hostId, action.session.id, action.session.created);
      if (state.tabs.some((t) => t.id === id)) return { ...state, activeId: id };
      const tab: Tab = {
        id,
        hostId: action.hostId,
        sessionId: action.session.id,
        sessionName: action.session.name,
        createdAt: action.session.created,
        preview: null,
        orientation: "horizontal",
      };
      return { tabs: [...state.tabs, tab], activeId: id };
    }
    case "close": {
      const index = state.tabs.findIndex((t) => t.id === action.id);
      if (index < 0) return state;
      const tabs = state.tabs.filter((t) => t.id !== action.id);
      let activeId = state.activeId;
      if (activeId === action.id) activeId = (tabs[index] ?? tabs[index - 1])?.id ?? null;
      return { tabs, activeId };
    }
    case "activate":
      return state.tabs.some((t) => t.id === action.id) ? { ...state, activeId: action.id } : state;
    case "markStale":
      return update(state, action.id, { stale: action.stale });
    case "setName": {
      const tab = state.tabs.find((t) => t.id === action.id);
      return tab && tab.sessionName !== action.name ? update(state, action.id, { sessionName: action.name }) : state;
    }
    case "setPreview":
      return update(state, action.id, { preview: action.preview });
    case "setOrientation":
      return update(state, action.id, { orientation: action.orientation });
  }
}

/**
 * The live session a tab refers to. Matching uses id plus creation time:
 * tmux reuses ids like $3 after a server restart, while names can change
 * through renames.
 */
export function findSession(tab: Tab, sessions: Session[]): Session | undefined {
  return sessions.find(
    (s) => s.id === tab.sessionId && new Date(s.created).getTime() === new Date(tab.createdAt).getTime(),
  );
}

export function matchesSession(tab: Tab, sessions: Session[]): boolean {
  return findSession(tab, sessions) !== undefined;
}

function isTab(value: unknown): value is Tab {
  const t = value as Tab;
  return (
    typeof t === "object" &&
    t !== null &&
    typeof t.id === "string" &&
    typeof t.hostId === "string" &&
    typeof t.sessionId === "string" &&
    /^\$\d+$/.test(t.sessionId) &&
    typeof t.sessionName === "string" &&
    typeof t.createdAt === "string"
  );
}

export function loadTabs(storage: Pick<Storage, "getItem">): TabsState {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY;
    const data = JSON.parse(raw) as { tabs?: unknown[]; activeId?: unknown };
    const tabs = (data.tabs ?? []).filter(isTab).map((t) => ({
      ...t,
      stale: false,
      preview: t.preview ?? null,
      orientation: t.orientation === "vertical" ? "vertical" : "horizontal",
    })) as Tab[];
    const activeId =
      typeof data.activeId === "string" && tabs.some((t) => t.id === data.activeId)
        ? data.activeId
        : (tabs[0]?.id ?? null);
    return { tabs, activeId };
  } catch {
    return EMPTY;
  }
}

export function saveTabs(storage: Pick<Storage, "setItem">, state: TabsState): void {
  const tabs = state.tabs.map(({ stale: _stale, ...rest }) => rest);
  storage.setItem(STORAGE_KEY, JSON.stringify({ tabs, activeId: state.activeId }));
}
