// Access token for non-loopback use (WP14 finding 6).
//
// `muestra serve --host 0.0.0.0` prints a link like
//     http://192.168.1.20:8765/?token=abc123
// The page picks the token up from the URL on first load, keeps it for the
// session (sessionStorage: survives reloads of the tab), removes it from the
// visible URL (so it is not shared by screenshot / copy-paste of the address
// bar) and the client sends it on every HTTP request and on the websocket.
// With no token in the URL and none stored, nothing changes: the app works
// exactly as before against a loopback server that does not ask for one.

export const TOKEN_PARAM = "token";
const STORAGE_KEY = "muestra.token.v1";

export interface TokenEnv {
  location: Pick<Location, "href" | "search" | "pathname" | "hash">;
  history: Pick<History, "replaceState" | "state">;
  storage: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
}

function browserEnv(): TokenEnv | null {
  try {
    if (typeof window === "undefined") return null;
    let storage: Storage | null = null;
    try {
      storage = window.sessionStorage;
    } catch {
      storage = null; // blocked storage: the token then lives only until reload
    }
    return { location: window.location, history: window.history, storage };
  } catch {
    return null;
  }
}

/** Pick the token up from the URL (and strip it from there), else from
 *  storage. Pure apart from the injected env, so it is unit-tested. */
export function captureToken(env: TokenEnv | null = browserEnv()): string | null {
  if (!env) return null;
  let fromUrl: string | null = null;
  try {
    const params = new URLSearchParams(env.location.search);
    const v = params.get(TOKEN_PARAM);
    if (v && v.trim()) {
      fromUrl = v.trim();
      params.delete(TOKEN_PARAM);
      const qs = params.toString();
      env.history.replaceState(env.history.state, "", env.location.pathname + (qs ? `?${qs}` : "") + env.location.hash);
    }
  } catch {
    /* odd location (tests): ignore */
  }
  try {
    if (fromUrl) {
      env.storage?.setItem(STORAGE_KEY, fromUrl);
      return fromUrl;
    }
    return env.storage?.getItem(STORAGE_KEY) || null;
  } catch {
    return fromUrl;
  }
}

/** Forget a stored token (it was rejected: the server restarted with a new one). */
export function forgetToken(env: TokenEnv | null = browserEnv()): void {
  try {
    env?.storage?.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to forget */
  }
}
