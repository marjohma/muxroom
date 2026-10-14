import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { TERMINAL_FONT, XTERM_THEME } from "./theme";

export interface XtermHandle {
  term: Terminal;
  fit: () => void;
  dispose: () => void;
}

/** Create an xterm in `container` that refits on size changes (skipped while hidden). */
export function createXterm(container: HTMLElement): XtermHandle {
  const term = new Terminal({
    fontFamily: TERMINAL_FONT,
    fontSize: 13,
    lineHeight: 1.15,
    cursorBlink: true,
    macOptionIsMeta: true,
    scrollback: 5000,
    theme: XTERM_THEME,
  });
  const fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(new WebLinksAddon());
  term.open(container);
  // Default (DOM) renderer on purpose: the WebGL addon rendered a blank
  // terminal after resizes at devicePixelRatio 2 in our browser tests.

  let frame = 0;
  const fit = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      if (container.clientWidth > 0 && container.clientHeight > 0) {
        try {
          fitAddon.fit();
        } catch {
          // terminal not yet rendered
        }
      }
    });
  };
  const observer = new ResizeObserver(fit);
  observer.observe(container);
  fit();

  return {
    term,
    fit,
    dispose: () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      term.dispose();
    },
  };
}
