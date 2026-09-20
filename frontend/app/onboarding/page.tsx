"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2 } from "lucide-react";
import GradientBackdrop from "@/components/GradientBackdrop";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  createBillingCheckout,
  indexKnowledgeFile,
  setAutomationTimezone,
  setKnowledgeFile,
} from "@/lib/api";

const COMMON_TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Phoenix",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Australia/Sydney",
  "UTC",
];

const STEPS = ["Choose your plan", "Business hours", "Teach Klaros your business", "Done"] as const;

export default function OnboardingPage() {
  const router = useRouter();
  const { token, user } = useAuth();
  const [step, setStep] = useState(0);

  const [subscribingPlan, setSubscribingPlan] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  );
  const [savingTimezone, setSavingTimezone] = useState(false);
  const [timezoneError, setTimezoneError] = useState<string | null>(null);

  const [services, setServices] = useState("");
  const [pricing, setPricing] = useState("");
  const [voice, setVoice] = useState("");
  const [savingKnowledge, setSavingKnowledge] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null);

  function goToDashboard() {
    router.push("/dashboard");
  }

  async function handleSubscribe(plan: "solo" | "growth") {
    if (!token) return;
    setSubscribingPlan(plan);
    setPlanError(null);
    try {
      const origin = window.location.origin;
      const result = await createBillingCheckout(token, plan, `${origin}/onboarding`, `${origin}/onboarding`);
      window.location.href = result.checkout_url;
    } catch (err) {
      setPlanError(err instanceof ApiError ? err.message : "Unable to start checkout.");
      setSubscribingPlan(null);
    }
  }

  async function handleSaveTimezone() {
    if (!token) return;
    setSavingTimezone(true);
    setTimezoneError(null);
    try {
      await setAutomationTimezone(token, timezone);
      setStep(2);
    } catch (err) {
      setTimezoneError(err instanceof ApiError ? err.message : "Unable to save your timezone.");
    } finally {
      setSavingTimezone(false);
    }
  }

  async function handleSaveKnowledge() {
    if (!token) return;
    const entries: { path: string; content: string }[] = [];
    // Writes into the same canonical paths the app already pre-seeds with
    // generic placeholder content for every new tenant (see
    // KnowledgeService.DEFAULT_FILES) — this replaces those placeholders
    // with the tenant's real answers instead of creating parallel files,
    // so Settings → Knowledge never shows two competing "services" files.
    if (services.trim()) entries.push({ path: "office/service-catalog.md", content: services.trim() });
    if (pricing.trim()) entries.push({ path: "office/pricing-rules.md", content: pricing.trim() });
    if (voice.trim()) entries.push({ path: "brand/voice-guide.md", content: voice.trim() });

    if (entries.length === 0) {
      setStep(3);
      return;
    }

    setSavingKnowledge(true);
    setKnowledgeError(null);
    try {
      for (const entry of entries) {
        await setKnowledgeFile(token, entry.path, entry.content);
        try {
          await indexKnowledgeFile(token, entry.path);
        } catch {
          // Best-effort — a save is never blocked by an indexing hiccup
          // (e.g. no AI provider configured yet), same as the Knowledge
          // Layer settings page.
        }
      }
      setStep(3);
    } catch (err) {
      setKnowledgeError(err instanceof ApiError ? err.message : "Unable to save your answers.");
    } finally {
      setSavingKnowledge(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-6 py-12">
      <GradientBackdrop />
      <div className="w-full max-w-xl">
        <Link href="/" className="font-display mb-8 block text-center text-xl italic text-foreground">
          Klaros AI
        </Link>

        <div className="klaros-glass rounded-2xl p-8">
          <div className="mb-6 flex items-center gap-2">
            {STEPS.map((label, i) => (
              <div key={label} className="flex flex-1 items-center gap-2">
                <div
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${
                    i < step
                      ? "bg-success text-white"
                      : i === step
                      ? "border border-foreground text-foreground"
                      : "border border-border-strong text-muted"
                  }`}
                >
                  {i < step ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
                </div>
                {i < STEPS.length - 1 && <div className="h-px flex-1 bg-border" />}
              </div>
            ))}
          </div>

          {step === 0 && (
            <>
              <h1 className="font-display text-2xl text-foreground">
                Welcome{user ? `, ${user.full_name.split(" ")[0]}` : ""}
              </h1>
              <p className="mt-1 text-sm text-muted">
                You&apos;re already on a real 14-day free trial with full access — no card needed. Subscribe
                now if you'd rather start paid right away, or just continue on the trial.
              </p>

              <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-border bg-surface p-4">
                  <div className="flex items-baseline justify-between">
                    <h3 className="font-medium">Solo</h3>
                    <span className="text-sm text-muted">$49/mo</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">Up to 50 AI recommendations/mo, 1 user.</p>
                  <button
                    onClick={() => handleSubscribe("solo")}
                    disabled={subscribingPlan !== null}
                    className="mt-3 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    {subscribingPlan === "solo" ? "Redirecting..." : "Subscribe to Solo"}
                  </button>
                </div>
                <div className="rounded-lg border border-border bg-surface p-4">
                  <div className="flex items-baseline justify-between">
                    <h3 className="font-medium">Growth</h3>
                    <span className="text-sm text-muted">$129/mo</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">Unlimited AI recommendations, full automation engine.</p>
                  <button
                    onClick={() => handleSubscribe("growth")}
                    disabled={subscribingPlan !== null}
                    className="mt-3 w-full rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-muted disabled:opacity-50"
                  >
                    {subscribingPlan === "growth" ? "Redirecting..." : "Subscribe to Growth"}
                  </button>
                </div>
              </div>

              {planError && <p className="mt-3 text-sm text-danger">{planError}</p>}

              <div className="mt-6 flex justify-end">
                <button
                  onClick={() => setStep(1)}
                  disabled={subscribingPlan !== null}
                  className="klaros-btn-primary disabled:opacity-50"
                >
                  Start my 14-day free trial
                </button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h1 className="font-display text-2xl text-foreground">Set your business hours</h1>
              <p className="mt-1 text-sm text-muted">
                So Klaros runs on your business's own schedule, not generic defaults. Can be changed
                later in Settings.
              </p>

              <div className="mt-6">
                <label className="mb-1 block text-xs text-muted">
                  What timezone does your business operate in?
                </label>
                <p className="mb-2 text-xs text-muted">
                  Used to schedule automations and reports at the right local time — e.g. a
                  &quot;send reminder at 9am&quot; automation fires at 9am here, not on server time.
                </p>
                <input
                  list="onboarding-timezones"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="America/New_York"
                  className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                />
                <datalist id="onboarding-timezones">
                  {COMMON_TIMEZONES.map((tz) => (
                    <option key={tz} value={tz} />
                  ))}
                </datalist>
              </div>

              {timezoneError && <p className="mt-3 text-sm text-danger">{timezoneError}</p>}

              <div className="mt-6 flex items-center justify-between">
                <button onClick={goToDashboard} className="text-sm text-muted hover:underline">
                  Skip setup for now
                </button>
                <button
                  onClick={handleSaveTimezone}
                  disabled={savingTimezone || !timezone.trim()}
                  className="klaros-btn-primary disabled:opacity-50"
                >
                  {savingTimezone ? "Saving..." : "Continue"}
                </button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <h1 className="font-display text-2xl text-foreground">Teach Klaros your business</h1>
              <p className="mt-1 text-sm text-muted">
                This writes real, editable files into your Knowledge Layer — the same source Klaros
                reads from for Ask AI and AI-drafted content. Answer as much or as little as you
                like; you can expand it anytime in Settings → Knowledge.
              </p>

              <div className="mt-6 space-y-4">
                <div>
                  <label className="mb-1 block text-xs text-muted">
                    What services or products do you offer?
                  </label>
                  <textarea
                    value={services}
                    onChange={(e) => setServices(e.target.value)}
                    rows={3}
                    placeholder="e.g. Residential HVAC repair and installation, emergency after-hours service, annual maintenance plans..."
                    className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">
                    How do you price your work?
                  </label>
                  <textarea
                    value={pricing}
                    onChange={(e) => setPricing(e.target.value)}
                    rows={3}
                    placeholder="e.g. $125/hr labor, free estimates on jobs over $500, 50% deposit required on installs..."
                    className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs text-muted">
                    How should Klaros sound when writing to your customers?
                  </label>
                  <textarea
                    value={voice}
                    onChange={(e) => setVoice(e.target.value)}
                    rows={3}
                    placeholder="e.g. Friendly and direct, no jargon, always mention our 24-month workmanship warranty..."
                    className="w-full rounded-md border border-border-strong bg-surface-muted px-3 py-2 text-sm"
                  />
                </div>
              </div>

              {knowledgeError && <p className="mt-3 text-sm text-danger">{knowledgeError}</p>}

              <div className="mt-6 flex items-center justify-between">
                <button onClick={() => setStep(3)} className="text-sm text-muted hover:underline">
                  Skip this step
                </button>
                <button
                  onClick={handleSaveKnowledge}
                  disabled={savingKnowledge}
                  className="klaros-btn-primary disabled:opacity-50"
                >
                  {savingKnowledge ? "Saving..." : "Continue"}
                </button>
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <h1 className="font-display text-2xl text-foreground">You&apos;re set up</h1>
              <p className="mt-1 text-sm text-muted">
                Klaros now knows your timezone and what you told it about your business. Next, connect
                the tools you actually use — Stripe, QuickBooks, Google Calendar — from the checklist on
                your dashboard, and Klaros will verify each connection for real before marking it
                connected.
              </p>
              <div className="mt-6 flex justify-end">
                <button onClick={goToDashboard} className="klaros-btn-primary">
                  Go to dashboard
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
