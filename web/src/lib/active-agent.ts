import { atom } from "nanostores";

/** Selected /agents profile for shell-level status. Null outside the route. */
export const $activeAgentProfile = atom<string | null>(null);
