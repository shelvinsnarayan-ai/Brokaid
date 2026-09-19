/*! brokaid:dev-only */
/**
 * Client-side jurisdiction preview store — dev-only.
 * Toggling flips currency + rail-label + disclosure text on client screens.
 * Real production jurisdiction is server-authoritative (from the client
 * entity); this store only drives the preview banner.
 */
import { create } from "zustand";

export type JurisdictionCode = "AU" | "SG";

interface JurisdictionState {
  preview: JurisdictionCode | null;
  setPreview: (c: JurisdictionCode | null) => void;
}

export const useJurisdictionStore = create<JurisdictionState>((set) => ({
  preview: null,
  setPreview: (preview) => set({ preview }),
}));
