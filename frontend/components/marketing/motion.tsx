"use client";

import { useRef, type ReactNode } from "react";
import { motion, useReducedMotion, useScroll, useTransform, type MotionValue } from "framer-motion";

/** The easing the whole marketing site shares: a long, soft deceleration. */
export const APPLE_EASE = [0.28, 0.11, 0.32, 1] as const;

/** Fades and lifts its children into place once, when they scroll into view. */
export function Reveal({
  children,
  delay = 0,
  y = 28,
  className,
  as = "div",
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
  as?: "div" | "li" | "section" | "span";
}) {
  const reduce = useReducedMotion();
  const M = motion[as];
  return (
    <M
      className={className}
      initial={reduce ? false : { opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-12% 0px" }}
      transition={{ duration: 0.9, ease: APPLE_EASE, delay }}
    >
      {children}
    </M>
  );
}

/** A grid or list whose direct <Reveal>-free children rise one after another. */
export function Stagger({ children, className, step = 0.08 }: { children: ReactNode; className?: string; step?: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : "hidden"}
      whileInView="show"
      viewport={{ once: true, margin: "-10% 0px" }}
      transition={{ staggerChildren: step }}
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div
      className={className}
      variants={{ hidden: { opacity: 0, y: 32, scale: 0.98 }, show: { opacity: 1, y: 0, scale: 1 } }}
      transition={{ duration: 0.85, ease: APPLE_EASE }}
    >
      {children}
    </motion.div>
  );
}

/**
 * Scales and rounds a large visual up toward full width as it crosses the viewport, and eases the
 * headline above it away: the "product settles into view" scroll the whole page is built around.
 */
export function ScrollStage({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "center center"] });
  const scale = useTransform(scrollYProgress, [0, 1], [0.86, 1]);
  const radius = useTransform(scrollYProgress, [0, 1], [48, 28]);
  const opacity = useTransform(scrollYProgress, [0, 0.35], [0.4, 1]);
  return (
    <div ref={ref} className={className}>
      <motion.div style={reduce ? undefined : { scale, borderRadius: radius, opacity }} className="overflow-hidden">
        {children}
      </motion.div>
    </div>
  );
}

function Word({ word, progress, range }: { word: string; progress: MotionValue<number>; range: [number, number] }) {
  const opacity = useTransform(progress, range, [0.18, 1]);
  return (
    <motion.span style={{ opacity }} className="mr-[0.28em] inline-block">
      {word}
    </motion.span>
  );
}

/** A statement whose words light up one by one as the reader scrolls through it. */
export function WordReveal({ text, className }: { text: string; className?: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.85", "end 0.45"] });
  const words = text.split(" ");
  if (reduce) return <p className={className}>{text}</p>;
  return (
    <p ref={ref} className={className} aria-label={text}>
      {words.map((w, i) => (
        <Word key={`${w}-${i}`} word={w} progress={scrollYProgress} range={[i / words.length, Math.min(1, (i + 1.6) / words.length)]} />
      ))}
    </p>
  );
}

/** Slow vertical drift for a decorative layer, relative to the page scroll. */
export function Parallax({ children, className, distance = 60 }: { children: ReactNode; className?: string; distance?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });
  const y = useTransform(scrollYProgress, [0, 1], [distance, -distance]);
  return (
    <div ref={ref} className={className}>
      <motion.div style={reduce ? undefined : { y }}>{children}</motion.div>
    </div>
  );
}
