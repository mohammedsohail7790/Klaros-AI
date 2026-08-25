import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <h1 className="text-4xl font-semibold tracking-tight">Klaros AI</h1>
      <p className="max-w-md text-sm text-neutral-400">
        The AI operating system for the one-person company.
      </p>
      <div className="flex gap-3">
        <Link
          href="/login"
          className="rounded-md border border-neutral-700 px-4 py-2 text-sm hover:bg-neutral-900"
        >
          Sign in
        </Link>
        <Link
          href="/register"
          className="rounded-md bg-white px-4 py-2 text-sm font-medium text-black hover:bg-neutral-200"
        >
          Create your company
        </Link>
      </div>
    </main>
  );
}
