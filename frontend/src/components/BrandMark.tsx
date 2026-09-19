/**
 * BrandMark — presentational SVG mark loader.
 *
 * Renders one of the brokAId marks (kernel ring for the platform,
 * pulse tile for the client app) from `/brand/mark-*.svg`. See
 * `docs/brand/brokAId_Brand_Guide_v1.0.md` §2 for the audience rules.
 */
import type { ImgHTMLAttributes } from "react";

export type MarkVariant =
  | "kernel-colour"
  | "kernel-reverse"
  | "pulse-colour"
  | "pulse-reverse";

type Props = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt"> & {
  variant: MarkVariant;
  size?: number;
  alt?: string;
};

const SRC: Record<MarkVariant, string> = {
  "kernel-colour": "/brand/mark-kernel-colour.svg",
  "kernel-reverse": "/brand/mark-kernel-reverse.svg",
  "pulse-colour": "/brand/mark-pulse-colour.svg",
  "pulse-reverse": "/brand/mark-pulse-reverse.svg",
};

export function BrandMark({
  variant,
  size = 32,
  alt = "brokAId",
  ...rest
}: Props) {
  return (
    <img
      src={SRC[variant]}
      alt={alt}
      width={size}
      height={size}
      style={{ display: "inline-block" }}
      draggable={false}
      {...rest}
    />
  );
}
