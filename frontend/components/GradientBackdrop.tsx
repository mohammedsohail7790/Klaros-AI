"use client";

import { useEffect, useRef } from "react";

/**
 * The shared background for every marketing/auth surface (homepage,
 * pricing, login, register, onboarding, accept-invite): a soft gradient
 * wash plus a quiet particle network — small nodes drifting slowly,
 * connecting with a thin line when two happen to be close, in the
 * brand's own gold/navy tones at very low opacity. This replaced a
 * static blurred-blob gradient that read as flat/generated; the
 * particle layer is the same "designed system, not a color fill"
 * language the Halla AI family's own marketing site uses for its hero
 * backgrounds, at a fraction of the complexity (plain canvas 2D, no
 * WebGL/three.js) since this only ever needs to read as ambient
 * texture behind foreground content, never as the main visual.
 *
 * Respects prefers-reduced-motion (renders one static frame, no
 * animation loop) and pauses entirely off-screen/tab-hidden to avoid
 * burning CPU on a page the visitor isn't looking at.
 */
export default function GradientBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const styles = getComputedStyle(document.documentElement);
    const accent = styles.getPropertyValue("--color-accent").trim() || "199 162 90";
    const accent2 = styles.getPropertyValue("--color-accent-2").trim() || "43 58 99";

    let width = 0;
    let height = 0;
    let particles: { x: number; y: number; vx: number; vy: number; r: number; c: string }[] = [];
    let raf = 0;
    let visible = true;

    function resize() {
      const canvasEl = canvasRef.current;
      if (!canvasEl) return;
      width = canvasEl.clientWidth;
      height = canvasEl.clientHeight;
      canvasEl.width = width * dpr;
      canvasEl.height = height * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Density scales with area but stays capped — this is ambient
      // texture, not a visualization, so it should never get busy.
      const count = Math.min(70, Math.round((width * height) / 22000));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.15,
        vy: (Math.random() - 0.5) * 0.15,
        r: Math.random() * 1.4 + 0.6,
        c: Math.random() > 0.35 ? accent : accent2,
      }));
    }

    function draw() {
      ctx!.clearRect(0, 0, width, height);
      const linkDist = 130;

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        if (!prefersReducedMotion) {
          p.x += p.vx;
          p.y += p.vy;
          if (p.x < 0 || p.x > width) p.vx *= -1;
          if (p.y < 0 || p.y > height) p.vy *= -1;
        }
        for (let j = i + 1; j < particles.length; j++) {
          const q = particles[j];
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < linkDist) {
            ctx!.strokeStyle = `rgb(${p.c} / ${0.1 * (1 - dist / linkDist)})`;
            ctx!.lineWidth = 1;
            ctx!.beginPath();
            ctx!.moveTo(p.x, p.y);
            ctx!.lineTo(q.x, q.y);
            ctx!.stroke();
          }
        }
      }
      for (const p of particles) {
        ctx!.fillStyle = `rgb(${p.c} / 0.35)`;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx!.fill();
      }
    }

    function loop() {
      if (!visible) return;
      draw();
      if (!prefersReducedMotion) raf = requestAnimationFrame(loop);
    }

    function handleVisibility() {
      visible = document.visibilityState === "visible";
      if (visible && !prefersReducedMotion) loop();
      else cancelAnimationFrame(raf);
    }

    resize();
    loop();
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {/* A soft top wash so the very top of the page (behind the sticky
          glass header) always has real color to blur, not just the
          particle field below. */}
      <div
        className="absolute inset-x-0 top-0 h-[26rem]"
        style={{
          background: "linear-gradient(180deg, rgb(var(--color-accent) / 0.10) 0%, transparent 100%)",
        }}
      />
      <div
        className="absolute -top-32 left-1/4 h-[36rem] w-[36rem] -translate-x-1/2 rounded-full opacity-40 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute top-10 right-[-6rem] h-[32rem] w-[32rem] rounded-full opacity-35 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent-2)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute bottom-[-8rem] left-[-4rem] h-[28rem] w-[28rem] rounded-full opacity-25 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {/* Fine dot-grid texture for close-up richness — the detail that
          reads as "designed" rather than a flat color field. */}
      <div
        className="absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage: "radial-gradient(rgb(var(--color-foreground)) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />
    </div>
  );
}
