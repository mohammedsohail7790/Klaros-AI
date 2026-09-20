import { cn } from "@/lib/cn";

/** A single shimmering placeholder bar/block. */
export function SkeletonBlock({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <div className={cn("klaros-skeleton rounded-md", className)} style={style} />;
}

/**
 * Full-section loading placeholder — a page header line, a row of stat
 * tiles, and a few list rows. Used as the drop-in replacement for every
 * bare "Loading..." text node across the dashboard: shape-matched content
 * reads as a product that's actually about to show you something, where
 * plain text reads as a stub.
 */
export function Skeleton({ stats = 3, rows = 4 }: { stats?: number; rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {stats > 0 && (
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: stats }).map((_, i) => (
            <div key={i} className="klaros-card space-y-2.5 p-4">
              <SkeletonBlock className="h-3 w-20" />
              <SkeletonBlock className="h-6 w-14" />
            </div>
          ))}
        </div>
      )}
      {rows > 0 && (
        <div className="klaros-card divide-y divide-border">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3.5">
              <SkeletonBlock className="h-8 w-8 shrink-0 rounded-full" />
              <SkeletonBlock className="h-3.5 flex-1" style={{ maxWidth: `${65 - i * 8}%` }} />
              <SkeletonBlock className="h-3.5 w-16 shrink-0" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
