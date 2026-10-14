import { afterEach, describe, expect, it } from "vitest";
import { authHeaders, captureTokenFromUrl, getToken, setTokenFromInput, webSocketProtocols } from "./auth";

const TOKEN = "Abc_def-123".padEnd(43, "x");

afterEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("access token", () => {
  it("is taken from the URL fragment and removed from the address bar", () => {
    window.history.replaceState(null, "", `/?x=1#token=${TOKEN}`);
    captureTokenFromUrl();
    expect(getToken()).toBe(TOKEN);
    expect(window.location.hash).toBe("");
    expect(window.location.search).toBe("?x=1");
    expect(authHeaders()).toEqual({ Authorization: `Bearer ${TOKEN}` });
    expect(webSocketProtocols()).toEqual(["muxroom", `muxroom.token.${TOKEN}`]);
  });

  it("accepts a pasted link or bare token and rejects junk", () => {
    expect(setTokenFromInput(`http://127.0.0.1:8765/#token=${TOKEN}`)).toBe(true);
    expect(getToken()).toBe(TOKEN);
    sessionStorage.clear();
    expect(setTokenFromInput(`  ${TOKEN}  `)).toBe(true);
    expect(setTokenFromInput("short")).toBe(false);
    expect(setTokenFromInput("<script>alert(1)</script>".padEnd(50, "x"))).toBe(false);
  });

  it("sends no credentials without a token", () => {
    expect(authHeaders()).toEqual({});
    expect(webSocketProtocols()).toEqual(["muxroom"]);
  });
});
