/**
 * BrandWordmark — presentational SVG wordmark loader.
 *
 * Renders one of the four v1.0-canonical brokAId wordmark variants
 * from `/brand/wordmark-*.svg`. Kept intentionally minimal: no colour
 * theming, no font swaps, no children — the SVG itself embeds the
 * brand colours per `docs/brand/brokAId_Brand_Guide_v1.0.md`.
 *
 * Variant selection:
 *   ledger-light    → operator, on Bone
 *   ledger-reverse  → operator, on Ink
 *   harbour-bold    → client / landing, on Sand
 *   harbour-reverse → client, on Harbour
 */
import type { ImgHTMLAttributes } from "react";

export type WordmarkVariant =
  | "ledger-light"
  | "ledger-reverse"
  | "harbour-bold"
  | "harbour-reverse";

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt"> & {
  variant: WordmarkVariant;
  height?: number;
  alt?: string;
};

const SRC: Record<WordmarkVariant, string> = {
  "ledger-light": "/brand/wordmark-ledger-light.svg",
  "ledger-reverse": "/brand/wordmark-ledger-reverse.svg",
  "harbour-bold": "/brand/wordmark-harbour-bold.svg",
  "harbour-reverse": "/brand/wordmark-harbour-reverse.svg",
};

export function BrandWordmark({
  variant,
  height = 28,
  alt = "brokAId",
  ...rest
}: Props) {
  return (
    <img
      src={SRC[variant]}
      alt={alt}
      height={height}
      style={{ height, width: "auto", display: "inline-block" }}
      draggable={false}
      {...rest}
    />
  );
}
