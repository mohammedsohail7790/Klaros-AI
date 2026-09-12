export default function GradientBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {/* A soft top wash so the very top of the page (behind the sticky
          glass header) always has real color to blur, not just the blobs
          below the fold. */}
      <div
        className="absolute inset-x-0 top-0 h-[26rem]"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--color-accent) / 0.10) 0%, transparent 100%)",
        }}
      />
      <div
        className="absolute -top-32 left-1/4 h-[36rem] w-[36rem] -translate-x-1/2 rounded-full opacity-45 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute top-10 right-[-6rem] h-[32rem] w-[32rem] rounded-full opacity-40 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent-2)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute bottom-[-8rem] left-[-4rem] h-[28rem] w-[28rem] rounded-full opacity-30 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent)) 0%, transparent 70%)" }}
      />
      <div
        className="absolute bottom-0 right-1/4 h-[24rem] w-[24rem] translate-x-1/3 translate-y-1/3 rounded-full opacity-25 blur-3xl"
        style={{ background: "radial-gradient(circle, rgb(var(--color-accent-2)) 0%, transparent 70%)" }}
      />
      {/* Fine dot-grid texture for close-up richness — the detail that
          reads as "designed" rather than a flat color field. */}
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{
          backgroundImage: "radial-gradient(rgb(var(--color-foreground)) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }}
      />
    </div>
  );
}
