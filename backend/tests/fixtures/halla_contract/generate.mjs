// Regenerates halla_events.json: Halla-shaped webhook bodies signed with HALLA'S OWN signing function.
//   HALLA_REPO=/path/to/Middle-East node backend/tests/fixtures/halla_contract/generate.mjs
// Bodies follow apps/gateway/src/events/consumers/klaros-webhook.consumer.ts (buildEventData) at Halla main 64c2821 — the key names and
// the omission of undefined values are the real ones; the VALUES are synthetic. The secret below is a throw-away test constant.
import { pathToFileURL } from 'node:url';
import { writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = process.env.HALLA_REPO;
if (!repo) throw new Error('set HALLA_REPO to a checkout of mohammedsohail7790/Middle-East');
const { signWebhookPayload } = await import(pathToFileURL(join(repo, 'apps/gateway/src/security/webhook-signing.ts')).href);

const SECRET = 'whsec-contract-fixture-0123456789abcdef';
const HALLA_TENANT = '00000000-0000-4000-8000-00000000a11a';
const TS = '1791500000'; // fixed Unix seconds: the fixtures are deterministic and verified against this time
const envelope = (id, type, data) => JSON.stringify({ id, type, timestamp: new Date(Number(TS) * 1000).toISOString(), tenant_id: HALLA_TENANT, data });

const events = {
  'lead.created': { id: 'evt-fixture-lead-created', data: { leadId: 'halla-lead-9001', phone: '+15550100901', name: 'Synthetic Patient', callId: 'CA-fixture-1' } },
  'lead.updated': { id: 'evt-fixture-lead-updated', data: { leadId: 'halla-lead-9001', phone: '+15550100901', name: 'Synthetic Patient', callId: 'CA-fixture-1' } },
  'lead.qualified': { id: 'evt-fixture-lead-qualified', data: { leadId: 'halla-lead-9001', callId: 'CA-fixture-1', status: 'qualified', qualification: 'qualified',
      fields: { treatment: 'knee consultation' }, missingFields: ['preferred_dates'], reason: 'Interested in a planned knee consultation; agreed to be contacted by email.', confidence: 0.82 } },
  'lead.escalated': { id: 'evt-fixture-lead-escalated', data: { callId: 'CA-fixture-2', leadId: 'halla-lead-9001', target: 'human-coordinator', reason: 'Caller asked whether the surgery will definitely work.' } },
  'call.completed': { id: 'evt-fixture-call-completed', data: { callId: 'CA-fixture-1', durationMs: 91000, qualificationStatus: 'qualified', escalation: { escalated: false } } },
  'appointment.confirmed': { id: 'evt-fixture-appt-confirmed', data: { appointmentId: 'halla-appt-77', scheduledTime: '2026-11-20T10:00:00.000Z', leadId: 'halla-lead-9001' } },
};
const out = { secret: SECRET, halla_tenant_id: HALLA_TENANT, timestamp: TS, signer: 'apps/gateway/src/security/webhook-signing.ts signWebhookPayload', events: {} };
for (const [type, { id, data }] of Object.entries(events)) {
  const body = envelope(id, type, data);
  out.events[type] = { id, body, signature: 'sha256=' + signWebhookPayload(SECRET, TS, body) };
}
writeFileSync(join(dirname(fileURLToPath(import.meta.url)), 'halla_events.json'), JSON.stringify(out, null, 2) + '\n');
console.log('wrote', Object.keys(out.events).length, 'fixtures');
