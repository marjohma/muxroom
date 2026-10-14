import type { ReactNode } from "react";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import type { Orientation } from "./tabs";

interface Props {
  main: ReactNode;
  side: ReactNode | null;
  orientation: Orientation;
}

/**
 * Terminal pane plus an optional, resizable preview pane. The main panel is
 * always the first child so toggling the preview never remounts terminals.
 */
export function Workspace({ main, side, orientation }: Props) {
  return (
    <ResizablePanelGroup orientation={orientation} className="h-full min-h-0">
      <ResizablePanel id="main" defaultSize={side ? "58%" : "100%"} minSize="20%">
        {main}
      </ResizablePanel>
      {side && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel id="side" defaultSize="42%" minSize="15%">
            {side}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
