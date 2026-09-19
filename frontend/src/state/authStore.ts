/*! brokaid:dev-only */
import { create } from "zustand";

export type Role = "guest" | "client" | "broker" | "admin";

interface AuthState {
  role: Role;
  setRole: (r: Role) => void;
  clear: () => void;
}

/**
 * Phase 0 auth store — only a role slot, no real credentials.
 * Real auth (server session + JWT) lands in phase 4.
 */
export const useAuthStore = create<AuthState>((set) => ({
  role: "guest",
  setRole: (role) => set({ role }),
  clear: () => set({ role: "guest" }),
}));
