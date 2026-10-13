"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Check, ShieldCheck } from "lucide-react";
import AppShell from "@/components/AppShell";
import { LeadConsentPanel } from "@/components/business/LeadConsentPanel";
import { NewReferral, ReferralDetail } from "@/components/mt/ReferralDialogs";
import { useAuth } from "@/lib/useAuth";
import {
  ApiError,
  HandoffTask,
  Lead,
  MedicalTourismProvider,
  PipelineLead,
  acceptHandoffTask,
  completeHandoffTask,
  getLead,
  getMedicalTourismPipeline,
  listHandoffTasks,
  listMedicalTourismProviders,
} from "@/lib/api";
import { ageLabel, contextLine, nextStepFor, patientName } from "@/lib/mtCase";
import { STAGE_LABEL, STAGE_ORDER, TASK_KIND_LABEL, TASK_REASON_LABEL } from "@/lib/mtLabels";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

const FACTS: [keyof PipelineLead["intake"], string][] = [
  ["procedure", "Treatment"],
  ["destination", "Where"],
  ["timeframe", "When"],
  ["language", "Language"],
  ["contact_time", "Best time to call"],
];

function Stepper({ stage }: { stage: PipelineLead["stage"] }) {
  const current = STAGE_ORDER.indexOf(stage);
  return (
    <ol aria-label="Progress" className="flex flex-wrap gap-x-1 gap-y-2">
      {STAGE_ORDER.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={s} aria-current={active ? "step" : undefined} className="flex items-center gap-1.5">
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${
                done ? "bg-success text-white" : active ? "bg-accent text-accent-foreground" : "border border-border-strong text-muted-foreground"
              }`}
            >
              {done ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : i + 1}
            </span>
            <span className={`mr-3 text-xs ${active ? "font-medium text-foreground" : "text-muted"}`}>{STAGE_LABEL[s]}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One patient, everything in one place: where they are, what to do next, what they told us, what they agreed to, which clinics they were referred to, and the
 * tasks a person owes them. The page only shows and records; whether anything may be shared is decided by the backend.
 */
export default function PatientCasePage() {
  const { id } = useParams<{ id: string }>();
  const { token, user, loading: authLoading } = useAuth();
  const [patient, setPatient] = useState<PipelineLead | null>(null);
  const [contact, setContact] = useState<Lead | null>(null);
  const [tasks, setTasks] = useState<HandoffTask[]>([]);
  const [providers, setProviders] = useState<MedicalTourismProvider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [openReferral, setOpenReferral] = useState<string | null>(null);
  const [busyTask, setBusyTask] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !id) return;
    setLoading(true);
    setError(null);
    try {
      const [pipe, t, p] = await Promise.all([getMedicalTourismPipeline(token), listHandoffTasks(token, { lead_id: id }), listMedicalTourismProviders(token, { limit: 100 })]);
      const found = pipe.leads.find((l) => l.lead_id === id) ?? null;
      setPatient(found);
      setNotFound(found === null);
      setTasks(t.tasks.filter((x) => x.status === "OPEN" || x.status === "ACCEPTED"));
      setProviders(p.providers);
      // Contact details only when the patient allows us to keep them (the pipeline row has a name exactly then).
      if (found?.name) {
        try {
          setContact((await getLead(token, id)).lead);
        } catch {
          setContact(null);
        }
      } else {
        setContact(null);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to load this patient.");
    } finally {
      setLoading(false);
    }
  }, [token, id]);

  useEffect(() => {
    load();
  }, [load]);

  const step = useMemo(() => (patient ? nextStepFor(patient) : null), [patient]);
  const providerName = useMemo(() => Object.fromEntries(providers.map((p) => [p.id, p.name])), [providers]);
  const liveReferral = patient?.referrals.find((r) => r.status !== "CANCELLED") ?? null;

  async function task(taskId: string, action: "accept" | "done") {
    if (!token) return;
    setBusyTask(taskId);
    setError(null);
    try {
      await (action === "accept" ? acceptHandoffTask(token, taskId) : completeHandoffTask(token, taskId));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That did not work.");
    } finally {
      setBusyTask(null);
    }
  }

  function primaryAction() {
    if (!patient || !step?.cta) return null;
    const label = step.cta;
    switch (patient.stage) {
      case "PROVIDER_OPTIONS":
        return <Button onClick={() => setChoosing(true)}>{label}</Button>;
      case "PATIENT_APPROVAL":
      case "REFERRED":
      case "CLINIC_RESPONSE":
        return liveReferral ? (
          <Button onClick={() => setOpenReferral(liveReferral.id)}>{label}</Button>
        ) : (
          <Button onClick={() => setChoosing(true)}>{label}</Button>
        );
      case "OUTCOME":
        return (
          <Link href="/medical-tourism/referrals" className="inline-flex items-center rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover">
            {label}
          </Link>
        );
      case "QUALIFICATION":
        return (
          <Link href={`/leads/${patient.lead_id}`} className="inline-flex items-center rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover">
            {label}
          </Link>
        );
      default:
        return (
          <a href="#consent" className="inline-flex items-center rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:bg-accent-hover">
            {label}
          </a>
        );
    }
  }

  return (
    <AppShell user={user}>
      <div className="mx-auto max-w-5xl px-6 py-8 sm:px-8">
        <Link href="/medical-tourism/pipeline" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> All patients
        </Link>

        {authLoading || (loading && !patient && !notFound) ? (
          <div className="mt-6">
            <Skeleton />
          </div>
        ) : error && !patient ? (
          <div className="mt-6">
            <Alert variant="danger" action={<Button size="sm" variant="secondary" onClick={() => load()}>Try again</Button>}>
              {error}
            </Alert>
          </div>
        ) : notFound || !patient ? (
          <div className="mt-6">
            <Alert variant="warning">This patient could not be found. They may have been removed.</Alert>
          </div>
        ) : (
          <>
            <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className={`font-display text-3xl ${patient.name ? "text-foreground" : "italic text-muted"}`}>{patientName(patient)}</h1>
                <p className="mt-1 text-sm text-muted">
                  {contextLine(patient) || "Enquiry details are not on record"}
                  {patient.created_at ? ` · enquired ${ageLabel(patient.created_at)}` : ""}
                </p>
              </div>
              <Badge status={patient.stage}>{STAGE_LABEL[patient.stage]}</Badge>
            </header>

            <div className="mt-5 klaros-card p-4">
              <Stepper stage={patient.stage} />
            </div>

            {error && (
              <div className="mt-4">
                <Alert variant="danger">{error}</Alert>
              </div>
            )}

            <section aria-label="Next step" className="mt-5 klaros-card border-l-4 border-l-accent p-5">
              <h2 className="text-xs font-medium text-muted-foreground">Next step</h2>
              <p className="mt-1.5 text-base text-foreground">{step?.hint}</p>
              {patient.held_for_person && !patient.consent_frozen && <p className="mt-1 text-sm text-warning">This enquiry is waiting for a person to review it.</p>}
              <div className="mt-3">{primaryAction()}</div>
            </section>

            <div className="mt-6 grid gap-6 lg:grid-cols-5">
              <div className="space-y-6 lg:col-span-3">
                <section aria-label="Referrals" className="klaros-card p-5">
                  <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold tracking-tight text-foreground">Clinics</h2>
                    {patient.stage !== "CONSENT" && patient.stage !== "NEW_ENQUIRY" && (
                      <Button size="sm" variant="secondary" onClick={() => setChoosing(true)}>
                        New referral
                      </Button>
                    )}
                  </div>
                  {patient.referrals.length === 0 ? (
                    <p className="mt-2 text-sm text-muted">Not referred to a clinic yet. A clinic receives nothing until the patient has approved sharing with that clinic.</p>
                  ) : (
                    <ul className="mt-3 divide-y divide-border">
                      {patient.referrals.map((r) => (
                        <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                          <div>
                            <span className="font-medium text-foreground">{r.provider_name ?? providerName[r.provider_id] ?? "Clinic"}</span>
                            <span className="ml-2 text-muted-foreground">{r.sent_at ? `sent ${ageLabel(r.sent_at)}` : "not sent"}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge status={r.status}>{r.status.replace("_", " ")}</Badge>
                            <Button size="sm" variant="secondary" onClick={() => setOpenReferral(r.id)}>
                              Open
                            </Button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section aria-label="Tasks" className="klaros-card p-5">
                  <h2 className="text-lg font-semibold tracking-tight text-foreground">Tasks</h2>
                  {tasks.length === 0 ? (
                    <p className="mt-2 text-sm text-muted">Nothing is waiting on a person for this patient.</p>
                  ) : (
                    <ul className="mt-3 divide-y divide-border">
                      {tasks.map((t) => (
                        <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                          <div className="min-w-0">
                            <div className="font-medium text-foreground">{TASK_KIND_LABEL[t.kind] ?? "Task"}</div>
                            {t.reason_code && <div className="text-muted">{TASK_REASON_LABEL[t.reason_code] ?? ""}</div>}
                            <div className="text-xs text-muted-foreground">
                              {t.status === "ACCEPTED" ? "In progress" : "Not picked up yet"}
                              {t.created_at ? ` · opened ${ageLabel(t.created_at)}` : ""}
                            </div>
                          </div>
                          <div className="flex gap-2">
                            {t.status === "OPEN" && (
                              <Button size="sm" disabled={busyTask === t.id} onClick={() => task(t.id, "accept")}>
                                I'll take it
                              </Button>
                            )}
                            <Button size="sm" variant="secondary" disabled={busyTask === t.id} onClick={() => task(t.id, "done")}>
                              Mark done
                            </Button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>

              <div className="space-y-6 lg:col-span-2">
                <section aria-label="About the enquiry" className="klaros-card p-5">
                  <h2 className="text-lg font-semibold tracking-tight text-foreground">About the enquiry</h2>
                  {!patient.name ? (
                    <p className="mt-2 text-sm text-muted">The patient has not agreed to us keeping their details, so none are shown.</p>
                  ) : (
                    <dl className="mt-3 space-y-2 text-sm">
                      {FACTS.map(([k, label]) => (
                        <div key={k} className="flex justify-between gap-4">
                          <dt className="text-muted">{label}</dt>
                          <dd className="text-right text-foreground">{patient.intake?.[k] ?? <span className="text-muted-foreground">Not given</span>}</dd>
                        </div>
                      ))}
                      {contact?.phone && (
                        <div className="flex justify-between gap-4">
                          <dt className="text-muted">Phone</dt>
                          <dd className="text-right text-foreground">{contact.phone}</dd>
                        </div>
                      )}
                      {contact?.email && (
                        <div className="flex justify-between gap-4">
                          <dt className="text-muted">Email</dt>
                          <dd className="break-all text-right text-foreground">{contact.email}</dd>
                        </div>
                      )}
                    </dl>
                  )}
                </section>

                <section id="consent" aria-label="Consent" className="scroll-mt-20">
                  <div className="mb-2 flex items-center gap-2 text-sm text-muted">
                    <ShieldCheck className="h-4 w-4" aria-hidden="true" /> What the patient has agreed to
                  </div>
                  <LeadConsentPanel token={token} leadId={patient.lead_id} onChanged={load} />
                </section>
              </div>
            </div>
          </>
        )}
      </div>

      {choosing && token && patient && (
        <NewReferral
          token={token}
          patients={[patient]}
          providers={providers}
          initialLeadId={patient.lead_id}
          onClose={() => setChoosing(false)}
          onCreated={(rid) => {
            setChoosing(false);
            setOpenReferral(rid);
            load();
          }}
        />
      )}
      {openReferral && token && (
        <ReferralDetail
          token={token}
          id={openReferral}
          providerName={providerName}
          onChanged={load}
          onClose={() => {
            setOpenReferral(null);
            load();
          }}
        />
      )}
    </AppShell>
  );
}
