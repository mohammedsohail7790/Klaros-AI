import { type LucideIcon } from "lucide-react";

export function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
}: {
  title: string;
  description?: string;
  icon?: LucideIcon;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-col gap-4 sm:mb-10 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        {Icon && (
          <span className="mt-1.5 hidden h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface-muted text-muted sm:flex">
            <Icon className="h-[18px] w-[18px]" strokeWidth={1.75} />
          </span>
        )}
        <div className="min-w-0">
          <h1 className="font-display text-[1.75rem] font-semibold leading-tight tracking-[-0.025em] text-foreground sm:text-[2.25rem]">{title}</h1>
          {description && <p className="mt-1.5 max-w-2xl text-[15px] leading-snug text-muted sm:text-[17px]">{description}</p>}
        </div>
      </div>
      {actions && (
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0 sm:justify-end">
          {actions}
        </div>
      )}
    </header>
  );
}
