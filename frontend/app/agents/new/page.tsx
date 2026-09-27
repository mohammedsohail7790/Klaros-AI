"use client";

/**
 * Phase 15: Agent creation. Submits only the fields
 * `CreateAgentRequest` (backend/app/api/v1/agents.py) actually accepts —
 * name, purpose, autonomy_tier. `acting_role` is never collected here: the
 * backend always derives it from the creator's own Role
 * (`acting_role=current_user.role`), never a client-supplied value, so
 * there is no field for it to spoof.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Bot } from "lucide-react";
import AppShell from "@/components/AppShell";
import { useAuth } from "@/lib/useAuth";
import { Button } from "@/components/ui/Button";
import { Alert } from "@/components/ui/Alert";
import { PageHeader } from "@/components/ui/PageHeader";
import { Field, Input } from "@/components/ui/Input";
import { ApiError, createAgent } from "@/lib/api";

const AUTONOMY_OPTIONS: { value: string; label: string; description: string }[] = [
  {
    value: "OBSERVE",
    label: "Observe only",
    description: "This agent can never call a tool through this system. Useful while you're still configuring it.",
  },
  {
    value: "RECOMMEND",
    label: "Recommend only",
    description: "Same as Observe in this release: tool execution stays disabled for this agent.",
  },
  {
    value: "EXECUTE_WITH_APPROVAL",
    label: "Execute with approval",
    description:
      "Every tool call this agent makes is held for a human to approve before it runs, regardless of that tool's own default policy.",
  },
  {
    value: "EXECUTE_AUTONOMOUS",
    label: "Execute autonomously",
    description:
      "Tool calls run under each tool's own configured policy. Tools that already require approval, or are blocked for this tenant, still require approval or stay blocked — this setting can only narrow what the agent can do, never widen it.",
  },
];

export default function NewAgentPage() {
  const { token, user } = useAuth();
  const router = useRouter();
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [autonomyTier, setAutonomyTier] = useState("OBSERVE");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || submitting) return;
    if (!name.trim()) {
      setError("Give this agent a name.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const agent = await createAgent(token, {
        name: name.trim(),
        purpose: purpose.trim(),
        autonomy_tier: autonomyTier,
      });
      router.push(`/agents/${agent.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create this agent.");
      setSubmitting(false);
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-xl px-6 py-10">
        <PageHeader icon={Bot} title="Create Agent" description="Set up a new governed agent for this business." />

        {error && (
          <div className="mb-4">
            <Alert variant="danger">{error}</Alert>
          </div>
        )}

        <form onSubmit={handleSubmit} className="klaros-card space-y-5 p-5">
          <Field label="Agent name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Invoice Follow-up Agent"
              maxLength={255}
            />
          </Field>

          <Field label="What should this agent do?">
            <textarea
              className="klaros-input min-h-[96px] resize-y"
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              placeholder="Describe the governed business task this agent is responsible for."
            />
          </Field>

          <fieldset>
            <legend className="klaros-label mb-2">Autonomy</legend>
            <div className="space-y-2">
              {AUTONOMY_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors ${
                    autonomyTier === opt.value ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-muted"
                  }`}
                >
                  <input
                    type="radio"
                    name="autonomy_tier"
                    value={opt.value}
                    checked={autonomyTier === opt.value}
                    onChange={() => setAutonomyTier(opt.value)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block font-medium text-foreground">{opt.label}</span>
                    <span className="block text-xs text-muted">{opt.description}</span>
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Every tool call this agent makes, at any tier, still passes through this business&apos;s existing tool
              permissions, approvals, and safety policy. You can change this later while the agent is a draft.
            </p>
          </fieldset>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => router.push("/agents")}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? "Creating..." : "Create Agent"}
            </Button>
          </div>
        </form>
      </div>
    </AppShell>
  );
}
