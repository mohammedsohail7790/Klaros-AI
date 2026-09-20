const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Retries a request on failures that are plausibly transient — a network/
 * CORS-reported failure (fetch throws something other than our own
 * ApiError for those: a connection reset, a timeout, a proxy hiccup) or a
 * 5xx/429 from the server — with exponential backoff. A real application
 * error (401/403/404/422, "not authenticated", "not found", a validation
 * error) is an ApiError with a 4xx status other than 429, and is rethrown
 * immediately on the first attempt: retrying those would just repeat the
 * same wrong request and delay the real error reaching the user.
 *
 * Built for the dashboard's own fan-out of ~16 independent calls, where
 * intermittent host-level flakiness (observed live: the same call
 * succeeding standalone but occasionally failing under concurrent load,
 * reported by the browser as a misleading "CORS" error) was leaving
 * individual sections perpetually stuck on their loading skeleton.
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options?: { retries?: number; baseDelayMs?: number }
): Promise<T> {
  const retries = options?.retries ?? 2;
  const baseDelayMs = options?.baseDelayMs ?? 500;
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      const retryable = err instanceof ApiError ? err.status >= 500 || err.status === 429 : true;
      if (!retryable || attempt === retries) throw err;
      const delay = baseDelayMs * 2 ** attempt + Math.random() * 200;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw lastError;
}

// Silent access-token refresh (Phase 12): a 401 on an authenticated call no
// longer forces an immediate re-login — it's tried against /auth/refresh
// (which was previously issued and stored but never actually consumed by
// anything) once, transparently, before giving up. Concurrent 401s share
// one in-flight refresh call rather than each firing their own.
let refreshPromise: Promise<string | null> | null = null;

async function trySilentRefresh(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const refreshToken = sessionStorage.getItem("klaros_refresh_token");
  if (!refreshToken) return null;

  if (!refreshPromise) {
    refreshPromise = fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async (res) => {
        if (!res.ok) return null;
        const body = await res.json();
        sessionStorage.setItem("klaros_access_token", body.access_token);
        sessionStorage.setItem("klaros_refresh_token", body.refresh_token);
        return body.access_token as string;
      })
      .catch(() => null)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// FastAPI's own validation errors (422) put `detail` as an array of
// Pydantic error objects ({type, loc, msg, ...}) rather than a string —
// left unhandled, JSON.stringify used to dump that raw structure straight
// into every error banner in the app. Render the human `msg` instead.
function formatErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((e) => (e && typeof e === "object" && typeof (e as { msg?: unknown }).msg === "string" ? (e as { msg: string }).msg : null))
      .filter((m): m is string => m !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit, _isRetry = false): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });

  if (res.status === 401 && !_isRetry && !path.startsWith("/api/v1/auth/")) {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    if (headers.Authorization) {
      const newAccessToken = await trySilentRefresh();
      if (newAccessToken) {
        return request<T>(
          path,
          { ...init, headers: { ...headers, Authorization: `Bearer ${newAccessToken}` } },
          true
        );
      }
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = formatErrorDetail(body.detail ?? body);
    throw new ApiError(res.status, detail || "Request failed");
  }

  // 202 Accepted for an APPROVAL_REQUIRED tool call: FastAPI's HTTPException
  // always wraps its body under "detail", so unwrap that one shape here
  // rather than making every caller know about the wrapper.
  if (res.status === 202) {
    const body = await res.json();
    return (body.detail ?? body) as T;
  }

  return res.json() as Promise<T>;
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string;
  role: string;
}

export function login(organization_slug: string, email: string, password: string) {
  return request<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ organization_slug, email, password }),
  });
}

export function register(organization_name: string, full_name: string, email: string, password: string) {
  return request<{ organization_slug: string; user: UserResponse; tokens: TokenResponse }>(
    "/api/v1/auth/register",
    {
      method: "POST",
      body: JSON.stringify({ organization_name, full_name, email, password }),
    }
  );
}

export function getCurrentUser(accessToken: string) {
  return request<UserResponse>("/api/v1/users/me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

// --- Team management ---

export interface TeamMember {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TeamInvite {
  id: string;
  email: string;
  role: string;
  status: string;
  created_at: string;
  expires_at: string;
}

export function listTeamMembers(token: string) {
  return request<{ members: TeamMember[] }>("/api/v1/users", { headers: authHeaders(token) });
}

export function updateTeamMember(token: string, userId: string, body: { role?: string; is_active?: boolean }) {
  return request<{ member: TeamMember }>(`/api/v1/users/${userId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function createTeamInvite(token: string, email: string, role: string) {
  return request<{ invite: TeamInvite; invite_url_path: string; email_sent: boolean }>("/api/v1/users/invites", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ email, role }),
  });
}

export function listTeamInvites(token: string) {
  return request<{ invites: TeamInvite[] }>("/api/v1/users/invites", { headers: authHeaders(token) });
}

export function revokeTeamInvite(token: string, inviteId: string) {
  return request<{ invite: TeamInvite }>(`/api/v1/users/invites/${inviteId}/revoke`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function getInvitePreview(inviteToken: string) {
  return request<{ organization_name: string; email: string; role: string }>(
    `/api/v1/public/invites/${inviteToken}`
  );
}

export function acceptInvite(inviteToken: string, fullName: string, password: string) {
  return request<{ user: UserResponse; tokens: TokenResponse }>(`/api/v1/public/invites/${inviteToken}/accept`, {
    method: "POST",
    body: JSON.stringify({ full_name: fullName, password }),
  });
}

export function logout(accessToken: string) {
  return request<void>("/api/v1/auth/logout", {
    method: "POST",
    headers: authHeaders(accessToken),
  });
}

// --- CRM: leads ---

export interface Lead {
  id: string;
  customer_id: string | null;
  name: string;
  phone: string | null;
  email: string | null;
  source: string;
  service_requested: string | null;
  location: string | null;
  urgency: string;
  estimated_value: number | null;
  status: string;
  lead_score: number | null;
  qualification_status: string;
  score_reason: string | null;
  assigned_user_id: string | null;
  created_at: string;
}

export interface CreateLeadPayload {
  name: string;
  source: string;
  phone?: string;
  email?: string;
  service_requested?: string;
  description?: string;
  location?: string;
  urgency?: string;
  estimated_value?: number;
  idempotency_key?: string;
}

export function createLead(token: string, payload: CreateLeadPayload) {
  return request<{ lead: Lead; deduplicated: boolean }>("/api/v1/leads", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export interface LeadImportRow {
  name: string;
  phone?: string;
  email?: string;
  service_requested?: string;
  description?: string;
  location?: string;
  estimated_value?: number;
}

export function bulkImportLeads(token: string, rows: LeadImportRow[]) {
  return request<{ created_count: number; matched_existing_customer_count: number; lead_ids: string[] }>(
    "/api/v1/leads/import",
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(rows),
    }
  );
}

export function searchLeads(
  token: string,
  params: { status?: string; source?: string; q?: string; limit?: number; offset?: number } = {}
) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  });
  return request<{ leads: Lead[]; total: number }>(`/api/v1/leads?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function getLead(token: string, leadId: string) {
  return request<{ lead: Lead }>(`/api/v1/leads/${leadId}`, { headers: authHeaders(token) });
}

export function updateLead(
  token: string,
  leadId: string,
  payload: { status?: string; assigned_user_id?: string; description?: string }
) {
  return request<{ lead: Lead }>(`/api/v1/leads/${leadId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export function qualifyLead(token: string, leadId: string) {
  return request<{ lead_id: string; qualification_status: string; score: number; reason: string }>(
    `/api/v1/leads/${leadId}/qualify`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface AIQualifyLeadAdvisory {
  available: boolean;
  qualification_score: number | null;
  intent: string | null;
  urgency: string | null;
  buying_signal: string | null;
  summary: string | null;
  recommended_next_action: string | null;
  unavailable_reason: string | null;
}

// Advisory only — never persists anything to the lead. A human still
// applies a reviewed recommendation via the existing qualifyLead() call
// above, or by editing the lead directly.
export function aiQualifyLeadAdvisory(token: string, leadId: string) {
  return request<AIQualifyLeadAdvisory>(`/api/v1/leads/${leadId}/ai-qualify-advisory`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// section 4: the explicit lead -> customer -> appointment -> job chain, as
// one idempotent action -- matches an existing customer by email/phone or
// creates one, books the appointment, and creates the job together.
export function convertLeadAndBook(
  token: string,
  body: { lead_id: string; title: string; start_time: string; end_time: string; assigned_user_id?: string }
) {
  return request<{
    lead_id: string;
    customer_id: string;
    customer_created: boolean;
    appointment_id: string;
    job: Record<string, unknown>;
    deduplicated: boolean;
  }>("/api/v1/jobs/convert-lead", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

// --- CRM: customers ---

export interface Customer {
  id: string;
  name: string;
  company_name: string | null;
  email: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  status: string;
  created_at: string;
}

export function createCustomer(
  token: string,
  payload: { name: string; email?: string; phone?: string; company_name?: string }
) {
  return request<{ customer: Customer }>("/api/v1/customers", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export interface CustomerImportRow {
  name: string;
  company_name?: string;
  email?: string;
  phone?: string;
  address?: string;
  city?: string;
  state?: string;
  postal_code?: string;
}

export function bulkImportCustomers(token: string, rows: CustomerImportRow[]) {
  return request<{ created_count: number; skipped_duplicate_count: number; customer_ids: string[] }>(
    "/api/v1/customers/import",
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(rows),
    }
  );
}

export function searchCustomers(token: string, params: { q?: string; limit?: number; offset?: number } = {}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  });
  return request<{ customers: Customer[]; total: number }>(`/api/v1/customers?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function getCustomer(token: string, customerId: string) {
  return request<{ customer: Customer }>(`/api/v1/customers/${customerId}`, { headers: authHeaders(token) });
}

export function updateCustomer(
  token: string,
  customerId: string,
  body: { name?: string; email?: string; phone?: string; address?: string; status?: string; notes?: string }
) {
  return request<{ customer: Customer }>(`/api/v1/customers/${customerId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface TimelineEntry {
  type: string;
  timestamp: string;
  summary: string;
}

export function getCustomerTimeline(token: string, customerId: string) {
  return request<{ customer_id: string; entries: TimelineEntry[] }>(
    `/api/v1/customers/${customerId}/timeline`,
    { headers: authHeaders(token) }
  );
}

export function getCustomerSummary(token: string, customerId: string) {
  return request<{ customer_id: string; summary: string }>(`/api/v1/customers/${customerId}/summary`, {
    headers: authHeaders(token),
  });
}

export function createCustomerNote(token: string, customerId: string, body: string) {
  return request<{ note_id: string }>(`/api/v1/customers/${customerId}/notes`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ body }),
  });
}

// --- CRM: appointments / calendar ---

export interface Appointment {
  id: string;
  lead_id: string | null;
  customer_id: string;
  assigned_user_id: string | null;
  title: string;
  service: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  status: string;
  notes: string | null;
  external_provider: string | null;
  external_id: string | null;
}

export interface TimeSlot {
  start_time: string;
  end_time: string;
}

export function listAppointments(token: string, params: { date_from: string; date_to: string }) {
  const qs = new URLSearchParams(params);
  return request<{ appointments: Appointment[] }>(`/api/v1/appointments?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function checkAvailability(
  token: string,
  params: { date_from: string; date_to: string; duration_minutes?: number }
) {
  const qs = new URLSearchParams({
    date_from: params.date_from,
    date_to: params.date_to,
    duration_minutes: String(params.duration_minutes ?? 60),
  });
  return request<{ calendar_provider: string; slots: TimeSlot[] }>(
    `/api/v1/appointments/availability?${qs.toString()}`,
    { headers: authHeaders(token) }
  );
}

export function createAppointment(
  token: string,
  payload: {
    customer_id: string;
    title: string;
    start_time: string;
    end_time: string;
    lead_id?: string;
    service?: string;
    location?: string;
    notes?: string;
  }
) {
  return request<{ appointment: Appointment }>("/api/v1/appointments", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export function cancelAppointment(token: string, appointmentId: string) {
  return request<{ appointment: Appointment }>(`/api/v1/appointments/${appointmentId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export function rescheduleAppointment(token: string, appointmentId: string, start_time: string, end_time: string) {
  return request<{ appointment: Appointment }>(`/api/v1/appointments/${appointmentId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ start_time, end_time }),
  });
}

// --- CRM: cockpit metrics ---

export interface CrmMetrics {
  new_leads_today: number;
  qualified_leads: number;
  appointments_today: number;
  conversion_rate_pct: number;
  uncontacted_leads: number;
  at_risk_leads: number;
}

export function getCrmMetrics(token: string) {
  return request<CrmMetrics>("/api/v1/crm/metrics", { headers: authHeaders(token) });
}

// --- Operations: jobs ---

export interface Job {
  id: string;
  customer_id: string;
  lead_id: string | null;
  appointment_id: string | null;
  job_number: string;
  title: string;
  description: string | null;
  service_type: string | null;
  status: string;
  priority: string;
  location: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  assigned_user_id: string | null;
  actual_start: string | null;
  actual_end: string | null;
  estimated_revenue: number | null;
  estimated_cost: number | null;
  customer_notes: string | null;
  internal_notes: string | null;
  completed_at: string | null;
  created_at: string;
}

export function createJob(
  token: string,
  payload: { title: string; customer_id: string; priority?: string; service_type?: string; description?: string }
) {
  return request<{ job: Job; deduplicated: boolean }>("/api/v1/jobs", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
}

export function searchJobs(
  token: string,
  params: { status?: string; priority?: string; q?: string; limit?: number; offset?: number } = {}
) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  });
  return request<{ jobs: Job[]; total: number }>(`/api/v1/jobs?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function getJob(token: string, jobId: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}`, { headers: authHeaders(token) });
}

export function updateJob(
  token: string,
  jobId: string,
  body: { title?: string; description?: string; priority?: string; customer_notes?: string; internal_notes?: string }
) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function getJobTimeline(token: string, jobId: string) {
  return request<{ job_id: string; entries: TimelineEntry[] }>(`/api/v1/jobs/${jobId}/timeline`, {
    headers: authHeaders(token),
  });
}

export function listJobTasks(token: string, jobId: string) {
  return request<{ tasks: JobTask[] }>(`/api/v1/jobs/${jobId}/tasks`, { headers: authHeaders(token) });
}

export function listJobMaterials(token: string, jobId: string) {
  return request<{ materials: JobMaterial[] }>(`/api/v1/jobs/${jobId}/materials`, { headers: authHeaders(token) });
}

export function listJobAttachments(token: string, jobId: string) {
  return request<{ attachments: JobAttachment[] }>(`/api/v1/jobs/${jobId}/attachments`, {
    headers: authHeaders(token),
  });
}

// Auth is a bearer header, so a plain <a href> can't carry it — fetch the
// real bytes ourselves and hand back a blob: URL the caller can open in a
// new tab or set as an <a download> href.
export async function downloadJobAttachment(token: string, jobId: string, attachmentId: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/v1/jobs/${jobId}/attachments/${attachmentId}/download`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Download failed");
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function getJobSummary(token: string, jobId: string) {
  return request<{ job_id: string; summary: string }>(`/api/v1/jobs/${jobId}/summary`, {
    headers: authHeaders(token),
  });
}

export function scheduleJob(token: string, jobId: string, start_time: string, end_time: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/schedule`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ start_time, end_time }),
  });
}

export function assignJob(token: string, jobId: string, worker_id: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/assign`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ worker_id }),
  });
}

export function transitionJob(token: string, jobId: string, action: string, body: Record<string, unknown> = {}) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/${action}`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface JobTask {
  id: string;
  job_id: string;
  title: string;
  description: string | null;
  status: string;
  required: boolean;
  completed_at: string | null;
}

export function createTask(token: string, jobId: string, title: string, required = true) {
  return request<{ task: JobTask }>(`/api/v1/jobs/${jobId}/tasks`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ title, required }),
  });
}

export function completeTask(token: string, taskId: string, skip = false) {
  return request<{ task: JobTask }>(`/api/v1/jobs/tasks/${taskId}/complete`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ skip }),
  });
}

export interface JobMaterial {
  id: string;
  job_id: string;
  name: string;
  quantity: number;
  unit: string | null;
  status: string;
}

export function addMaterial(token: string, jobId: string, name: string, quantity = 1) {
  return request<{ material: JobMaterial }>(`/api/v1/jobs/${jobId}/materials`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, quantity }),
  });
}

export interface JobAttachment {
  id: string;
  job_id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  storage_provider: string;
  transcription_status: string | null;
}

export async function uploadJobFile(
  token: string,
  jobId: string,
  kind: "documents" | "photos" | "voice-notes",
  file: File
) {
  const form = new FormData();
  form.append("file", file);
  return request<{ attachment: JobAttachment }>(`/api/v1/jobs/${jobId}/${kind}`, {
    method: "POST",
    headers: authHeaders(token),
    body: form,
  });
}

export function startQA(token: string, jobId: string) {
  return request<{ qa: Record<string, unknown> }>(`/api/v1/jobs/${jobId}/qa/start`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function completeQA(token: string, jobId: string) {
  return request<{ qa: Record<string, unknown> }>(`/api/v1/jobs/${jobId}/qa/complete`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function failQA(token: string, jobId: string, reason: string) {
  return request<{ qa: Record<string, unknown> }>(`/api/v1/jobs/${jobId}/qa/fail`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ reason }),
  });
}

export function generateCompletionPacket(token: string, jobId: string) {
  return request<{ packet: { id: string; status: string; summary: Record<string, unknown> } }>(
    `/api/v1/jobs/${jobId}/completion-packet`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function closeJob(token: string, jobId: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/close`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function blockJob(token: string, jobId: string, reason: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/block`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ reason }),
  });
}

export function unblockJob(token: string, jobId: string, targetStatus: string) {
  return request<{ job: Job }>(`/api/v1/jobs/${jobId}/unblock`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ target_status: targetStatus }),
  });
}

export interface ScopeChange {
  id: string;
  job_id: string;
  description: string;
  reason: string | null;
  estimated_cost: number | null;
  estimated_revenue: number | null;
  margin_impact: number | null;
  status: string;
}

export function createScopeChange(
  token: string,
  jobId: string,
  body: { description: string; reason?: string; estimated_cost?: number; estimated_revenue?: number }
) {
  return request<{ scope_change: ScopeChange }>(`/api/v1/jobs/${jobId}/scope-changes`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function requestScopeChangeApproval(token: string, scopeChangeId: string, justification: string) {
  return request<{ acknowledged: boolean }>(`/api/v1/jobs/scope-changes/${scopeChangeId}/request-approval`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ justification }),
  });
}

export function recordJobSignoff(token: string, jobId: string, signedBy: string) {
  return request<{
    signoff: { id: string; job_id: string; signed_by: string; signed_at: string; signature_reference: string; provider: string };
  }>(`/api/v1/jobs/${jobId}/signoff`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ signed_by: signedBy }),
  });
}

export function createJobPurchaseOrderDraft(token: string, jobId: string, supplier?: string) {
  return request<{
    purchase_order: { id: string; job_id: string; status: string; supplier: string | null };
    items: { id: string; name: string; quantity: number; unit: string | null; estimated_unit_cost: number | null }[];
  }>(`/api/v1/jobs/${jobId}/purchase-order-draft`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ supplier: supplier || undefined }),
  });
}

// --- Operations: workers ---

export interface Worker {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  role: string | null;
  status: string;
  skills: string[];
  service_types: string[];
  location: string | null;
  active: boolean;
}

export function createWorker(
  token: string,
  body: { name: string; email?: string; phone?: string; role?: string; skills?: string[]; service_types?: string[]; location?: string }
) {
  return request<{ worker: Worker }>("/api/v1/workers", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listWorkers(token: string, activeOnly = true) {
  const qs = new URLSearchParams({ active_only: String(activeOnly) });
  return request<{ workers: Worker[] }>(`/api/v1/workers?${qs.toString()}`, { headers: authHeaders(token) });
}

export function updateWorkerStatus(token: string, workerId: string, status: string) {
  return request<{ worker: Worker }>(`/api/v1/workers/${workerId}/status`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ status }),
  });
}

// --- Vendors / subcontractors ---

export interface Vendor {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  status: string;
}

export interface VendorBill {
  id: string;
  vendor_id: string;
  job_id: string | null;
  amount: string;
  due_date: string;
  status: string;
}

export function createVendor(token: string, body: { name: string; email?: string; phone?: string }) {
  return request<{ vendor: Vendor }>("/api/v1/vendors", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listVendors(token: string) {
  return request<{ vendors: Vendor[] }>("/api/v1/vendors", { headers: authHeaders(token) });
}

export function recordVendorBill(
  token: string,
  body: { vendor_id: string; job_id?: string; amount: string; due_date: string }
) {
  return request<{ vendor_bill: VendorBill }>("/api/v1/vendors/bills", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listVendorBills(token: string, vendorId?: string) {
  const qs = vendorId ? `?${new URLSearchParams({ vendor_id: vendorId }).toString()}` : "";
  return request<{ vendor_bills: VendorBill[] }>(`/api/v1/vendors/bills${qs}`, { headers: authHeaders(token) });
}

export function recordVendorPayout(token: string, billId: string, vendorId: string) {
  return request<{ status: string; approval_request_id?: string }>(`/api/v1/vendors/bills/${billId}/payout`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ vendor_id: vendorId, bill_id: billId }),
  });
}

// --- Compliance: licenses, insurance, bonds, certifications ---

export interface License {
  id: string;
  type: string;
  name: string;
  license_number: string | null;
  issuing_authority: string | null;
  holder_name: string | null;
  holder_user_id: string | null;
  issue_date: string | null;
  expiry_date: string;
  status: string;
  document_url: string | null;
  notes: string | null;
}

export function createLicense(
  token: string,
  body: {
    type: string;
    name: string;
    license_number?: string;
    issuing_authority?: string;
    holder_name?: string;
    issue_date?: string;
    expiry_date: string;
    document_url?: string;
    notes?: string;
  }
) {
  return request<{ license: License }>(`/api/v1/compliance/licenses`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listLicenses(token: string, filters?: { status?: string; type?: string }) {
  const qs = new URLSearchParams();
  if (filters?.status) qs.set("status", filters.status);
  if (filters?.type) qs.set("type", filters.type);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<{ licenses: License[] }>(`/api/v1/compliance/licenses${suffix}`, { headers: authHeaders(token) });
}

export function renewLicense(
  token: string,
  licenseId: string,
  body: { issue_date?: string; expiry_date: string; document_url?: string }
) {
  return request<{ license: License }>(`/api/v1/compliance/licenses/${licenseId}/renew`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function detectExpiringLicenses(token: string) {
  return request<{ newly_expiring_soon: string[]; newly_expired: string[] }>(
    `/api/v1/compliance/detect-expiring`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface Warranty {
  id: string;
  customer_id: string;
  job_id: string | null;
  item_description: string;
  start_date: string;
  expiry_date: string;
  status: string;
  last_checked_in_at: string | null;
  notes: string | null;
}

export function createWarranty(
  token: string,
  body: { customer_id: string; job_id?: string; item_description: string; start_date: string; expiry_date: string; notes?: string }
) {
  return request<{ warranty: Warranty }>(`/api/v1/warranties`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listWarranties(token: string, filters?: { customer_id?: string; status?: string }) {
  const qs = new URLSearchParams();
  if (filters?.customer_id) qs.set("customer_id", filters.customer_id);
  if (filters?.status) qs.set("status", filters.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<{ warranties: Warranty[] }>(`/api/v1/warranties${suffix}`, { headers: authHeaders(token) });
}

export function checkInWarranty(token: string, warrantyId: string, notes?: string) {
  return request<{ warranty: Warranty }>(`/api/v1/warranties/${warrantyId}/check-in`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ notes }),
  });
}

export function detectExpiringWarranties(token: string) {
  return request<{ newly_expiring_soon: string[]; newly_expired: string[] }>(`/api/v1/warranties/detect-expiring`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Operations: exceptions ---

export interface OpsException {
  id: string;
  type: string;
  severity: string;
  entity_type: string;
  entity_id: string;
  description: string;
  recommended_action: string | null;
  status: string;
  created_at: string;
}

export function listExceptions(token: string, status = "OPEN") {
  const qs = new URLSearchParams({ status });
  return request<{ exceptions: OpsException[] }>(`/api/v1/exceptions?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function resolveException(token: string, exceptionId: string) {
  return request<{ exception: OpsException }>(`/api/v1/exceptions/${exceptionId}/resolve`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function detectDelays(token: string) {
  return request<Record<string, number>>("/api/v1/exceptions/detect", {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Operations: dashboard ---

export interface OperationsDashboard {
  jobs_today: number;
  unassigned_jobs: number;
  at_risk_jobs: number;
  blocked_jobs: number;
  in_progress_jobs: number;
  qa_pending_jobs: number;
  completed_today: number;
  open_exceptions: number;
}

export function getOperationsDashboard(token: string) {
  return request<OperationsDashboard>("/api/v1/operations/dashboard", { headers: authHeaders(token) });
}

// --- Finance ---

export interface Invoice {
  id: string;
  invoice_number: string;
  customer_id: string;
  job_id: string | null;
  status: string;
  issue_date: string;
  due_date: string;
  currency: string;
  subtotal: string;
  tax: string;
  discount: string;
  total: string;
  amount_paid: string;
  amount_due: string;
  notes: string | null;
  sent_at: string | null;
  paid_at: string | null;
  voided_at: string | null;
}

export interface InvoiceLineItem {
  id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount: string;
  tax_rate: string;
  line_total: string;
}

export function listInvoices(token: string, params: { status?: string; customer_id?: string; job_id?: string } = {}) {
  const qs = new URLSearchParams(params as Record<string, string>);
  return request<{ invoices: Invoice[] }>(`/api/v1/invoices?${qs.toString()}`, { headers: authHeaders(token) });
}

export function getInvoice(token: string, invoiceId: string) {
  return request<Invoice & { line_items: InvoiceLineItem[] }>(`/api/v1/invoices/${invoiceId}`, {
    headers: authHeaders(token),
  });
}

export function requestInvoiceApproval(token: string, invoiceId: string) {
  return request<{ invoice: Invoice }>(`/api/v1/invoices/${invoiceId}/request-approval`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function approveInvoice(token: string, invoiceId: string) {
  return request<{ invoice: Invoice }>(`/api/v1/invoices/${invoiceId}/approve`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function rejectInvoice(token: string, invoiceId: string) {
  return request<{ invoice: Invoice }>(`/api/v1/invoices/${invoiceId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function sendInvoice(token: string, invoiceId: string) {
  return request<{ invoice: Invoice } | { status: string; approval_request_id: string }>(
    `/api/v1/invoices/${invoiceId}/send`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function voidInvoice(token: string, invoiceId: string, reason: string) {
  const qs = new URLSearchParams({ reason });
  return request<{ invoice: Invoice }>(`/api/v1/invoices/${invoiceId}/void?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function createInvoiceCheckout(
  token: string,
  invoiceId: string,
  body: { success_url: string; cancel_url: string; customer_email?: string }
) {
  return request<{ checkout_url: string; checkout_session_id: string }>(
    `/api/v1/invoices/${invoiceId}/checkout`,
    { method: "POST", headers: authHeaders(token), body: JSON.stringify(body) }
  );
}

export function syncInvoiceToQuickBooks(token: string, invoiceId: string) {
  return request<{ quickbooks_invoice_id: string; quickbooks_customer_id: string; already_synced: boolean }>(
    `/api/v1/invoices/${invoiceId}/sync-to-quickbooks`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface Refund {
  id: string;
  payment_id: string;
  invoice_id: string | null;
  amount: string;
  reason: string;
  status: string;
}

// Refunds/write-offs/credit-notes always land as a pending request with a
// real ApprovalRequest attached (see backend/app/services/adjustments_service.py,
// payment_service.py) -- approve/reject happens generically via the
// existing /approvals page (approveApproval/rejectApproval below), not a
// dedicated endpoint here.
export function createRefundRequest(
  token: string,
  body: { payment_id: string; invoice_id?: string; amount: string; reason: string }
) {
  return request<{ refund: Refund }>("/api/v1/refunds", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface WriteOff {
  id: string;
  invoice_id: string;
  amount: string;
  reason: string;
  status: string;
}

export function createWriteOffRequest(token: string, body: { invoice_id: string; amount: string; reason: string }) {
  return request<{ writeoff: WriteOff }>("/api/v1/writeoffs", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface CreditNote {
  id: string;
  invoice_id: string;
  credit_note_number: string | null;
  reason: string;
  total: string;
  status: string;
}

export function createCreditNoteRequest(
  token: string,
  body: { invoice_id: string; reason: string; line_items: { description: string; amount: string }[] }
) {
  return request<{ credit_note: CreditNote }>("/api/v1/credit-notes", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface InvoiceLineItemDraft {
  description: string;
  quantity: string;
  unit_price: string;
  discount?: string;
  tax_rate?: string;
}

export function createInvoiceDraft(
  token: string,
  body: { customer_id: string; job_id?: string; line_items: InvoiceLineItemDraft[]; due_date?: string }
) {
  return request<{ invoice: Invoice }>(`/api/v1/invoices`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function updateInvoiceDraft(token: string, invoiceId: string, lineItems: InvoiceLineItemDraft[]) {
  return request<{ invoice: Invoice }>(`/api/v1/invoices/${invoiceId}`, {
    method: "PATCH",
    headers: authHeaders(token),
    body: JSON.stringify({ line_items: lineItems }),
  });
}

export function triggerInvoiceFromJob(token: string, jobId: string) {
  const qs = new URLSearchParams({ job_id: jobId });
  return request<{ invoice: Invoice; deduplicated: boolean }>(`/api/v1/invoices/trigger-from-job?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface InvoiceImportRow {
  customer_name: string;
  customer_email?: string;
  customer_phone?: string;
  invoice_number?: string;
  issue_date: string;
  due_date: string;
  amount: string;
  amount_paid?: string;
  description?: string;
}

export interface InvoiceImportResult {
  created_count: number;
  skipped_count: number;
  customers_created_count: number;
  results: { customer_name: string; status: string; invoice_id: string | null; reason: string | null }[];
}

export function bulkImportInvoices(token: string, rows: InvoiceImportRow[]) {
  return request<InvoiceImportResult>("/api/v1/invoices/import", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(rows),
  });
}

// --- Phase 14: Quotes/Estimates. Internal (authenticated, staff-facing)
// CRUD/send below; the public customer view/accept/decline functions are
// separate and deliberately never send an Authorization header — the
// signed `token` query param IS the auth for those (see
// app/api/v1/public_quotes.py). ---

export interface Quote {
  id: string;
  quote_number: string;
  customer_id: string;
  lead_id: string | null;
  job_id: string | null;
  status: string;
  currency: string;
  subtotal: string;
  tax: string;
  discount: string;
  total: string;
  notes: string | null;
  terms: string | null;
  valid_until: string | null;
  sent_at: string | null;
  viewed_at: string | null;
  decided_at: string | null;
  decline_reason: string | null;
  deposit_type: string | null;
  deposit_value: string | null;
  deposit_amount: string | null;
}

export interface QuoteLineItem {
  id: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount: string;
  tax_rate: string;
  line_total: string;
}

export interface QuoteLineItemInput {
  description: string;
  quantity: string;
  unit_price: string;
  discount?: string;
  tax_rate?: string;
}

export function listQuotes(token: string, params: { status_filter?: string; customer_id?: string } = {}) {
  const qs = new URLSearchParams(params as Record<string, string>);
  return request<{ quotes: Quote[] }>(`/api/v1/quotes?${qs.toString()}`, { headers: authHeaders(token) });
}

export function getQuote(token: string, quoteId: string) {
  return request<Quote & { line_items: QuoteLineItem[] }>(`/api/v1/quotes/${quoteId}`, {
    headers: authHeaders(token),
  });
}

export function createQuoteDraft(
  token: string,
  body: {
    customer_id: string;
    lead_id?: string;
    line_items: QuoteLineItemInput[];
    notes?: string;
    terms?: string;
    deposit_type?: "FIXED" | "PERCENTAGE";
    deposit_value?: string;
  }
) {
  return request<{ quote: Quote; deduplicated: boolean }>("/api/v1/quotes", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function updateQuoteDraft(
  token: string,
  quoteId: string,
  body: {
    line_items: QuoteLineItemInput[];
    notes?: string;
    terms?: string;
    deposit_type?: "FIXED" | "PERCENTAGE";
    deposit_value?: string;
    clear_deposit?: boolean;
  }
) {
  return request<{ quote: Quote }>(`/api/v1/quotes/${quoteId}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function sendQuote(token: string, quoteId: string) {
  return request<{ quote: Quote; view_url_path: string }>(`/api/v1/quotes/${quoteId}/send`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function detectExpiredQuotes(token: string) {
  return request<{ expired_quote_ids: string[] }>("/api/v1/quotes/detect-expired", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function createStaffQuoteDepositCheckout(
  token: string,
  quoteId: string,
  body: { success_url: string; cancel_url: string }
) {
  return request<{ checkout_url: string; checkout_session_id: string }>(
    `/api/v1/quotes/${quoteId}/deposit/checkout`,
    {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    }
  );
}

// --- Public (unauthenticated) quote view — Klaros' first customer-facing
// surface with no login. Never pass authHeaders here. ---

export interface PublicQuote {
  id: string;
  quote_number: string;
  status: string;
  currency: string;
  subtotal: string;
  tax: string;
  discount: string;
  total: string;
  notes: string | null;
  terms: string | null;
  valid_until: string | null;
  decided_at: string | null;
  // Phase 15/16: whether a deposit is required, and its frozen amount once
  // known (set once the customer accepts — null before that). Never
  // includes deposit_type/deposit_value (internal configuration) or any
  // Stripe/payment identifier — this is the honest, redacted shape the
  // backend's public endpoint actually returns (app/api/v1/public_quotes.py).
  deposit_required: boolean;
  deposit_amount: string | null;
  line_items: QuoteLineItem[];
}

export function getPublicQuote(quoteId: string, token: string) {
  const qs = new URLSearchParams({ token });
  return request<PublicQuote>(`/api/v1/public/quotes/${quoteId}?${qs.toString()}`);
}

export function acceptPublicQuote(quoteId: string, token: string) {
  const qs = new URLSearchParams({ token });
  return request<{ quote: PublicQuote; job_created: boolean }>(
    `/api/v1/public/quotes/${quoteId}/accept?${qs.toString()}`,
    { method: "POST" }
  );
}

export function declinePublicQuote(quoteId: string, token: string, reason?: string) {
  const qs = new URLSearchParams({ token });
  return request<{ quote: PublicQuote }>(`/api/v1/public/quotes/${quoteId}/decline?${qs.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

// Phase 16: starts the customer's deposit payment via a real, hosted
// Stripe Checkout Session. The backend computes the amount from the
// quote's own frozen deposit_amount and builds success/cancel redirect
// URLs itself (never accepts them from this client) — this function sends
// nothing but the same quote_view token already used to view/accept the
// quote. The browser is expected to navigate to the returned checkout_url
// itself (e.g. `window.location.href = checkout_url`), not treat this
// call as payment confirmation.
export function createPublicQuoteDepositCheckout(quoteId: string, token: string) {
  const qs = new URLSearchParams({ token });
  return request<{ checkout_url: string }>(
    `/api/v1/public/quotes/${quoteId}/deposit/checkout?${qs.toString()}`,
    { method: "POST" }
  );
}

// --- Commercial pipeline (Quote -> Contract -> Deposit -> Job), surfaced
// on /dashboard. Consumes the existing backend aggregation endpoint —
// this file never computes these numbers itself. ---

export interface CommercialPipeline {
  quotes_awaiting_response: number;
  quotes_accepted: number;
  quotes_accepted_value: string;
  contracts_awaiting_signature: number;
  contracts_signed: number;
  deposits_awaiting_payment: number;
  deposits_awaiting_value: string;
  deposits_collected: string;
  jobs_from_quotes: number;
  needs_attention: boolean;
}

export function getCommercialPipeline(token: string) {
  return request<CommercialPipeline>("/api/v1/finance/commercial-pipeline", { headers: authHeaders(token) });
}

// --- Contracts (Phase 30/32): the sales agreement between quote
// acceptance and payment. Internal attestation only — no third-party
// e-signature provider is integrated; never represented as one. Staff
// (authenticated) list/get/send below; the public customer view/sign/
// decline functions mirror the public quote pattern exactly — never send
// authHeaders there, the signed `token` query param IS the auth. ---

export interface Contract {
  id: string;
  contract_number: string;
  quote_id: string;
  customer_id: string;
  status: string;
  content: string;
  sent_at: string | null;
  viewed_at: string | null;
  decided_at: string | null;
  signer_name: string | null;
  signer_email: string | null;
  decline_reason: string | null;
  created_at: string;
}

export function listContracts(
  token: string,
  params: { status_filter?: string; quote_id?: string; customer_id?: string } = {}
) {
  const qs = new URLSearchParams(params as Record<string, string>);
  return request<{ contracts: Contract[] }>(`/api/v1/contracts?${qs.toString()}`, { headers: authHeaders(token) });
}

export function getContract(token: string, contractId: string) {
  return request<Contract>(`/api/v1/contracts/${contractId}`, { headers: authHeaders(token) });
}

export function sendContract(token: string, contractId: string) {
  return request<{ contract: Record<string, unknown>; view_url_path: string }>(
    `/api/v1/contracts/${contractId}/send`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function detectPendingContracts(token: string) {
  return request<{ expired_contract_ids: string[] }>(`/api/v1/contracts/detect-pending`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Public (unauthenticated) contract view/sign/decline. ---

export interface PublicContract {
  id: string;
  contract_number: string;
  status: string;
  content: string;
  sent_at: string | null;
  viewed_at: string | null;
  decided_at: string | null;
  signer_name: string | null;
}

export function getPublicContract(contractId: string, token: string) {
  const qs = new URLSearchParams({ token });
  return request<PublicContract>(`/api/v1/public/contracts/${contractId}?${qs.toString()}`);
}

export function signPublicContract(contractId: string, token: string, signerName: string, signerEmail?: string) {
  const qs = new URLSearchParams({ token });
  return request<PublicContract>(`/api/v1/public/contracts/${contractId}/sign?${qs.toString()}`, {
    method: "POST",
    body: JSON.stringify({ signer_name: signerName, signer_email: signerEmail ?? null }),
  });
}

export function declinePublicContract(contractId: string, token: string, reason?: string) {
  const qs = new URLSearchParams({ token });
  return request<PublicContract>(`/api/v1/public/contracts/${contractId}/decline?${qs.toString()}`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export interface Payment {
  id: string;
  customer_id: string;
  amount: string;
  status: string;
  payment_method: string | null;
  provider: string;
  external_id: string | null;
  received_at: string;
}

export function listPayments(token: string, customerId?: string) {
  const qs = customerId ? `?${new URLSearchParams({ customer_id: customerId }).toString()}` : "";
  return request<{ payments: Payment[] }>(`/api/v1/payments${qs}`, { headers: authHeaders(token) });
}

export function recordTestPayment(
  token: string,
  body: { customer_id: string; amount: string; allocations: { invoice_id: string; amount: string }[] }
) {
  return request<{ payment: Payment }>("/api/v1/payments/test-payment", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface AgingSummary {
  current: string;
  days_1_30: string;
  days_31_60: string;
  days_61_90: string;
  days_90_plus: string;
  total: string;
}

export function getARAging(token: string) {
  return request<AgingSummary>("/api/v1/ar/aging", { headers: authHeaders(token) });
}

export interface CollectionActionRow {
  id: string;
  invoice_id: string;
  invoice_number: string;
  action_type: string;
  scheduled_for: string;
  status: string;
  attempt: number;
}

export function listCollectionActions(token: string) {
  return request<{ collection_actions: CollectionActionRow[] }>("/api/v1/ar/collections", {
    headers: authHeaders(token),
  });
}

export function detectOverdueInvoices(token: string) {
  return request<{ newly_overdue_invoice_ids: string[] }>("/api/v1/ar/detect-overdue", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function executeDueCollections(token: string) {
  return request<{ executed_action_ids: string[] }>("/api/v1/ar/collections/execute-due", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface JobProfitability {
  job_id: string;
  job_number: string;
  title: string;
  estimated_revenue: string | null;
  estimated_cost: string | null;
  estimated_margin_pct: number | null;
  actual_revenue: string | null;
  actual_cost: string | null;
  actual_margin_pct: number | null;
  cost_breakdown: Record<string, string>;
}

export function listProfitability(token: string) {
  return request<{ jobs: JobProfitability[] }>("/api/v1/profitability", { headers: authHeaders(token) });
}

export function getJobProfitability(token: string, jobId: string) {
  return request<JobProfitability>(`/api/v1/profitability/jobs/${jobId}`, { headers: authHeaders(token) });
}

export interface JobCost {
  id: string;
  job_id: string;
  category: string;
  description: string | null;
  quantity: string;
  unit_cost: string;
  total_cost: string;
  source: string;
}

export function recordJobCost(
  token: string,
  body: { job_id: string; category: string; description?: string; quantity?: string; unit_cost: string }
) {
  return request<{ job_cost: Record<string, unknown> }>("/api/v1/job-costs", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listJobCosts(token: string, jobId: string) {
  const qs = new URLSearchParams({ job_id: jobId });
  return request<{ job_costs: JobCost[] }>(`/api/v1/job-costs?${qs.toString()}`, { headers: authHeaders(token) });
}

export function syncMaterialCosts(token: string, jobId: string) {
  const qs = new URLSearchParams({ job_id: jobId });
  return request<{ created: number }>(`/api/v1/job-costs/sync-materials?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface CashForecastWeek {
  week_start: string;
  inflow: string;
  outflow: string;
  net: string;
  projected_balance: string;
  items: { type: string; source: string; amount: string; confidence: string }[];
}

export interface CashForecastResult {
  forecast_id: string;
  generated_at: string;
  starting_cash: string | null;
  starting_cash_source: string;
  weeks: CashForecastWeek[];
}

export function generateCashForecast(token: string) {
  return request<CashForecastResult>("/api/v1/cash/forecast/generate", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface FinanceSummary {
  total_ar: string;
  overdue_invoice_count: number;
  pending_approval_invoice_count: number;
  draft_invoice_count: number;
  total_paid: string;
  open_finance_exception_count: number;
  needs_attention: boolean;
}

export function getFinanceSummary(token: string) {
  return request<FinanceSummary>("/api/v1/finance/summary", { headers: authHeaders(token) });
}

// --- Marketing ---

export interface MarketingSummary {
  campaign_count: number;
  marketing_spend: string;
  leads: number;
  qualified_leads: number;
  appointments_booked: number;
  jobs_won: number;
  revenue: string;
  collected_revenue: string;
  cac: string | null;
  cac_note: string | null;
  roas: string | null;
  roas_note: string | null;
  conversion_rate_pct: number | null;
  open_marketing_exception_count: number;
  needs_attention: boolean;
}

export function getMarketingSummary(token: string) {
  return request<MarketingSummary>("/api/v1/marketing/summary", { headers: authHeaders(token) });
}

export interface AdsProviderStatus {
  provider: string;
  status: string;
  detail: string;
}

export function getAdsProviderStatus(token: string) {
  return request<{ providers: AdsProviderStatus[] }>("/api/v1/marketing/ads-provider-status", {
    headers: authHeaders(token),
  });
}

export interface Campaign {
  id: string;
  name: string;
  channel: string;
  objective: string;
  status: string;
  monthly_budget: string | null;
  total_budget: string | null;
  start_date: string | null;
  end_date: string | null;
  external_provider: string | null;
}

export interface CampaignPerformance {
  spend: string;
  leads: number;
  qualified_leads: number;
  booked: number;
  jobs_created: number;
  jobs_closed: number;
  invoiced_count: number;
  revenue: string;
  collected_revenue: string;
  cac: string | null;
  cac_note: string | null;
  revenue_per_lead: string | null;
  roas: string | null;
  roas_note: string | null;
}

export function listCampaigns(token: string) {
  return request<{ campaigns: Campaign[] }>("/api/v1/marketing/campaigns", { headers: authHeaders(token) });
}

export function getCampaign(token: string, campaignId: string) {
  return request<Campaign & { performance: CampaignPerformance }>(`/api/v1/marketing/campaigns/${campaignId}`, {
    headers: authHeaders(token),
  });
}

export interface CampaignBudgetStatus {
  budget: string | null;
  spend_to_date: string;
  remaining: string | null;
  utilization_pct: number | null;
  alert: string | null;
}

export function getCampaignBudgetStatus(token: string, campaignId: string) {
  return request<CampaignBudgetStatus>(`/api/v1/marketing/campaigns/${campaignId}/budget`, {
    headers: authHeaders(token),
  });
}

export function createCampaign(
  token: string,
  body: { name: string; channel: string; objective?: string; total_budget?: string; monthly_budget?: string }
) {
  return request<{ campaign: Campaign }>("/api/v1/marketing/campaigns", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function setCampaignStatus(token: string, campaignId: string, status: string) {
  const qs = new URLSearchParams({ status });
  return request<{ campaign: Campaign }>(`/api/v1/marketing/campaigns/${campaignId}/status?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function recordCampaignSpend(
  token: string,
  campaignId: string,
  body: { channel: string; amount: string; spend_date: string; source?: string }
) {
  return request<{ spend_id: string; amount: string }>(`/api/v1/marketing/campaigns/${campaignId}/spend`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function detectMarketingExceptions(token: string) {
  return request<{ flagged_campaign_ids: string[] }>("/api/v1/marketing/campaigns/detect-exceptions", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface LeadAttribution {
  id: string;
  lead_id: string;
  campaign_id: string | null;
  source: string | null;
  medium: string | null;
  attribution_model: string;
}

export function attributeLead(
  token: string,
  body: {
    lead_id: string;
    campaign_id?: string;
    source?: string;
    medium?: string;
    landing_page?: string;
    referral_source?: string;
    utm_source?: string;
    utm_medium?: string;
    utm_campaign?: string;
    utm_term?: string;
    utm_content?: string;
    click_id?: string;
    attribution_model?: string;
  }
) {
  return request<{ attribution: LeadAttribution }>("/api/v1/marketing/attribution/leads", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface MarketingContentItem {
  id: string;
  source_job_id: string | null;
  title: string;
  summary: string | null;
  status: string;
  ai_generated: boolean;
}

export function listContent(token: string, status?: string) {
  const qs = status ? `?${new URLSearchParams({ status }).toString()}` : "";
  return request<{ content: MarketingContentItem[] }>(`/api/v1/marketing/content${qs}`, { headers: authHeaders(token) });
}

export function getContent(token: string, contentId: string) {
  return request<MarketingContentItem & { variants: { id: string; channel: string; body_text: string | null; status: string }[] }>(
    `/api/v1/marketing/content/${contentId}`,
    { headers: authHeaders(token) }
  );
}

export function createContentIdea(token: string, title: string, summary?: string) {
  return request<{ content: MarketingContentItem }>("/api/v1/marketing/content/ideas", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ title, summary }),
  });
}

export function generateContentFromJob(token: string, jobId: string) {
  const qs = new URLSearchParams({ job_id: jobId });
  return request<{ content: MarketingContentItem }>(`/api/v1/marketing/content/generate-from-job?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function addContentVariant(token: string, contentId: string, channel: string, body_text?: string) {
  return request<{ variant: Record<string, unknown> }>(`/api/v1/marketing/content/${contentId}/variants`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ channel, body_text }),
  });
}

export function requestContentApproval(token: string, contentId: string) {
  return request<{ content: MarketingContentItem }>(`/api/v1/marketing/content/${contentId}/request-approval`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function approveContent(token: string, contentId: string) {
  return request<{ content: MarketingContentItem }>(`/api/v1/marketing/content/${contentId}/approve`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function rejectContent(token: string, contentId: string) {
  return request<{ content: MarketingContentItem }>(`/api/v1/marketing/content/${contentId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function publishContentVariant(token: string, contentVariantId: string) {
  return request<{ publication_id: string; status: string; provider: string } | { status: string; approval_request_id: string }>(
    `/api/v1/marketing/content/variants/${contentVariantId}/publish`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface SEOPageSummary {
  id: string;
  service: string;
  location: string;
  status: string;
  title: string | null;
  url_slug: string | null;
  ai_generated: boolean;
}

export function listSEOPages(token: string) {
  return request<{ pages: SEOPageSummary[] }>("/api/v1/marketing/seo/pages", { headers: authHeaders(token) });
}

export function generateSEOPage(token: string, service: string, location: string) {
  return request<{ page: SEOPageSummary }>("/api/v1/marketing/seo/pages/generate", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ service, location }),
  });
}

export function publishSEOPage(token: string, pageId: string) {
  return request<{ page: SEOPageSummary } | { status: string; approval_request_id: string }>(
    `/api/v1/marketing/seo/pages/${pageId}/publish`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface SEOKeywordRow {
  id: string;
  keyword: string;
  target_location: string | null;
  search_volume: number | null;
  current_ranking: number | null;
  page_id: string | null;
}

export function listSEOKeywords(token: string) {
  return request<{ keywords: SEOKeywordRow[] }>("/api/v1/marketing/seo/keywords", { headers: authHeaders(token) });
}

export function recordSEOKeyword(
  token: string,
  body: { keyword: string; target_location?: string; page_id?: string; search_volume?: number; current_ranking?: number }
) {
  return request<{ keyword_id: string; keyword: string }>("/api/v1/marketing/seo/keywords", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function createSEOOpportunity(
  token: string,
  body: { service: string; location: string; rationale?: string; priority?: string }
) {
  return request<{ opportunity_id: string }>("/api/v1/marketing/seo/opportunities", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface LocalListingRow {
  id: string;
  business_name: string;
  city: string | null;
  state: string | null;
  provider: string | null;
}

export function listLocalListings(token: string) {
  return request<{ listings: LocalListingRow[] }>("/api/v1/marketing/seo/local/listings", {
    headers: authHeaders(token),
  });
}

export function createLocalListing(
  token: string,
  body: { business_name: string; address?: string; city?: string; state?: string }
) {
  return request<{ listing_id: string }>("/api/v1/marketing/seo/local/listings", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface LocalReviewRow {
  id: string;
  listing_id: string;
  rating: number;
  author: string | null;
  body: string | null;
  responded: boolean;
}

export function listLocalReviews(token: string, listingId?: string) {
  const qs = listingId ? `?${new URLSearchParams({ listing_id: listingId }).toString()}` : "";
  return request<{ reviews: LocalReviewRow[] }>(`/api/v1/marketing/seo/local/reviews${qs}`, {
    headers: authHeaders(token),
  });
}

export function recordLocalReview(
  token: string,
  body: { listing_id: string; rating: number; author?: string; body?: string; source?: string }
) {
  return request<{ review_id: string }>("/api/v1/marketing/seo/local/reviews", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function respondToLocalReview(token: string, reviewId: string, responseText: string) {
  const qs = new URLSearchParams({ response_text: responseText });
  return request<{ review_id: string } | { status: string; approval_request_id: string }>(
    `/api/v1/marketing/seo/local/reviews/${reviewId}/respond?${qs.toString()}`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface OutboundListRow {
  id: string;
  name: string;
  description: string | null;
}

export interface OutboundContactRow {
  id: string;
  list_id: string;
  company: string | null;
  contact_name: string | null;
  email: string | null;
  phone: string | null;
  source: string;
  enrichment_status: string;
  qualification_status: string;
}

export function listOutboundLists(token: string) {
  return request<{ lists: OutboundListRow[] }>("/api/v1/marketing/outbound/lists", { headers: authHeaders(token) });
}

export function createOutboundList(token: string, name: string, description?: string) {
  return request<{ list_id: string; name: string }>("/api/v1/marketing/outbound/lists", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, description }),
  });
}

export function listOutboundContacts(token: string, listId?: string) {
  const qs = listId ? `?${new URLSearchParams({ list_id: listId }).toString()}` : "";
  return request<{ contacts: OutboundContactRow[] }>(`/api/v1/marketing/outbound/contacts${qs}`, { headers: authHeaders(token) });
}

export function addOutboundContact(
  token: string,
  body: { list_id: string; company?: string; contact_name?: string; email?: string; phone?: string }
) {
  return request<{ contact_id: string }>("/api/v1/marketing/outbound/contacts", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface OutboundSequenceRow {
  id: string;
  name: string;
  status: string;
}

export function listOutboundSequences(token: string) {
  return request<{ sequences: OutboundSequenceRow[] }>("/api/v1/marketing/outbound/sequences", {
    headers: authHeaders(token),
  });
}

export function createOutboundSequence(token: string, name: string, description?: string) {
  return request<{ sequence_id: string }>("/api/v1/marketing/outbound/sequences", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, description }),
  });
}

export function addOutboundStep(
  token: string,
  sequenceId: string,
  body: { day_offset: number; channel?: string; subject?: string; body?: string; sort_order?: number }
) {
  return request<{ step_id: string }>(`/api/v1/marketing/outbound/sequences/${sequenceId}/steps`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function enrollOutboundContact(token: string, sequenceId: string, contactId: string) {
  return request<{ enrollment_id: string; status: string }>("/api/v1/marketing/outbound/enrollments", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ sequence_id: sequenceId, contact_id: contactId }),
  });
}

export function executeDueOutboundActivities(token: string) {
  return request<{ executed_activity_ids: string[] }>("/api/v1/marketing/outbound/activities/execute-due", {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Marketing nurture sequences (stale leads / unbooked qualified leads) ---

export interface NurtureSequenceRow {
  id: string;
  name: string;
  trigger_type: string;
  status: string;
}

export function listNurtureSequences(token: string) {
  return request<{ sequences: NurtureSequenceRow[] }>("/api/v1/marketing/nurture/sequences", {
    headers: authHeaders(token),
  });
}

export function createNurtureSequence(token: string, name: string, triggerType?: string) {
  return request<{ sequence_id: string }>("/api/v1/marketing/nurture/sequences", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, trigger_type: triggerType }),
  });
}

export function findStaleLeadCandidates(token: string) {
  return request<{ lead_ids: string[] }>("/api/v1/marketing/nurture/candidates/stale-leads", {
    headers: authHeaders(token),
  });
}

export function enrollLeadInNurture(token: string, sequenceId: string, leadId: string) {
  return request<{ enrollment_id: string; status: string }>("/api/v1/marketing/nurture/enrollments", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ sequence_id: sequenceId, lead_id: leadId }),
  });
}

export interface NurtureEnrollmentRow {
  id: string;
  sequence_id: string;
  lead_id: string;
  status: string;
}

export function listNurtureEnrollments(token: string) {
  return request<{ enrollments: NurtureEnrollmentRow[] }>("/api/v1/marketing/nurture/enrollments", {
    headers: authHeaders(token),
  });
}

export function executeDueNurtureActivities(token: string) {
  return request<{ executed_activity_ids: string[] }>("/api/v1/marketing/nurture/activities/execute-due", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface ReactivationCampaignRow {
  id: string;
  name: string;
  status: string;
}

export interface ReactivationCandidateRow {
  id: string;
  campaign_id: string;
  customer_id: string | null;
  lead_id: string | null;
  reason: string;
  score: number;
  status: string;
}

export function listReactivationCampaigns(token: string) {
  return request<{ campaigns: ReactivationCampaignRow[] }>("/api/v1/marketing/reactivation/campaigns", { headers: authHeaders(token) });
}

export function createReactivationCampaign(token: string, name: string, target_criteria?: string) {
  return request<{ campaign_id: string }>("/api/v1/marketing/reactivation/campaigns", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, target_criteria }),
  });
}

export function listReactivationCandidates(token: string, campaignId?: string) {
  const qs = campaignId ? `?${new URLSearchParams({ campaign_id: campaignId }).toString()}` : "";
  return request<{ candidates: ReactivationCandidateRow[] }>(`/api/v1/marketing/reactivation/candidates${qs}`, {
    headers: authHeaders(token),
  });
}

export function identifyInactiveCustomers(token: string, campaignId: string) {
  return request<{ candidate_ids: string[] }>(`/api/v1/marketing/reactivation/campaigns/${campaignId}/identify-inactive-customers`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function identifyUnbookedQualifiedLeads(token: string, campaignId: string) {
  return request<{ candidate_ids: string[] }>(
    `/api/v1/marketing/reactivation/campaigns/${campaignId}/identify-unbooked-qualified-leads`,
    { method: "POST", headers: authHeaders(token) }
  );
}

// --- Retention & Referral ---

export interface RetentionSummary {
  active_customers: number;
  repeat_customers: number;
  at_risk_customers: number;
  inactive_customers: number;
  retention_opportunities_open: number;
  upcoming_service_reminders: number;
  review_requests_sent: number;
  positive_feedback_count: number;
  negative_feedback_count: number;
  referral_leads: number;
  referral_conversions: number;
  referral_revenue: string;
  repeat_customer_revenue: string;
  open_retention_exception_count: number;
  needs_attention: boolean;
}

export function getRetentionSummary(token: string) {
  return request<RetentionSummary>("/api/v1/retention/summary", { headers: authHeaders(token) });
}

export interface RetentionAnalytics {
  retention_rate: number | null;
  retention_rate_note: string;
  repeat_customer_rate: number | null;
  repeat_customer_rate_note: string;
  customer_reactivation_rate: number | null;
  customer_reactivation_rate_note: string;
  average_customer_value: string | null;
  average_customer_value_note: string;
  referral_conversion_rate: number | null;
  referral_conversion_rate_note: string;
  referral_revenue: string;
  revenue_from_repeat_customers: string;
  revenue_from_referrals: string;
}

export function getRetentionAnalytics(token: string) {
  return request<RetentionAnalytics>("/api/v1/retention/analytics", { headers: authHeaders(token) });
}

export interface CustomerHealth {
  lifecycle_state: string;
  total_jobs: number;
  completed_jobs: number;
  cancelled_jobs: number;
  total_invoiced: string;
  total_collected: string;
  open_balance: string;
  last_completed_job_at: string | null;
  last_service_type: string | null;
  average_days_between_jobs: number | null;
  last_review_request_at: string | null;
  last_referral_at: string | null;
}

export function getCustomerHealth(token: string, customerId: string) {
  return request<CustomerHealth>(`/api/v1/retention/customers/${customerId}/health`, { headers: authHeaders(token) });
}

export interface RetentionOpportunityRow {
  id: string;
  customer_id: string;
  type: string;
  reason: string;
  detected_at: string;
  priority: string;
  status: string;
  source_event: string | null;
  recommended_action: string | null;
}

export function listRetentionOpportunities(token: string, status?: string, customerId?: string) {
  const params: Record<string, string> = {};
  if (status) params.status = status;
  if (customerId) params.customer_id = customerId;
  const qs = Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : "";
  return request<{ opportunities: RetentionOpportunityRow[] }>(`/api/v1/retention/opportunities${qs}`, { headers: authHeaders(token) });
}

export function updateOpportunityStatus(token: string, opportunityId: string, status: string) {
  const qs = new URLSearchParams({ status });
  return request<{ opportunity_id: string; status: string }>(`/api/v1/retention/opportunities/${opportunityId}/status?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface RiskSignalRow {
  id: string;
  customer_id: string;
  signal_type: string;
  severity: string;
  description: string;
  detected_at: string;
  resolved: boolean;
}

export interface AdvocateCandidateRow {
  id: string;
  customer_id: string;
  reason: string;
  signals: string[];
  priority: string;
  status: string;
  identified_at: string;
}

export function listRiskSignals(token: string, resolved: boolean = false) {
  const qs = new URLSearchParams({ resolved: String(resolved) });
  return request<{ risk_signals: RiskSignalRow[] }>(`/api/v1/retention/risk-signals?${qs.toString()}`, {
    headers: authHeaders(token),
  });
}

export function listAdvocateCandidates(token: string, status?: string) {
  const qs = status ? `?${new URLSearchParams({ status }).toString()}` : "";
  return request<{ advocate_candidates: AdvocateCandidateRow[] }>(`/api/v1/retention/advocate-candidates${qs}`, {
    headers: authHeaders(token),
  });
}

export function detectAtRisk(token: string) {
  return request<{ changed_customer_ids: string[] }>(`/api/v1/retention/detect-at-risk`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function detectPaymentRisk(token: string) {
  return request<{ flagged_customer_ids: string[] }>(`/api/v1/retention/detect-payment-risk`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function detectAdvocates(token: string) {
  return request<{ candidate_customer_ids: string[] }>(`/api/v1/retention/detect-advocates`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface ServiceReminderRow {
  id: string;
  customer_id: string;
  source_job_id: string | null;
  service_type: string | null;
  reminder_date: string;
  reason: string | null;
  status: string;
}

export function listServiceReminders(token: string, customerId?: string) {
  const qs = customerId ? `?${new URLSearchParams({ customer_id: customerId }).toString()}` : "";
  return request<{ reminders: ServiceReminderRow[] }>(`/api/v1/retention/reminders${qs}`, { headers: authHeaders(token) });
}

export function markDueReminders(token: string) {
  return request<{ due_reminder_ids: string[] }>("/api/v1/retention/reminders/mark-due", { method: "POST", headers: authHeaders(token) });
}

export function updateReminderStatus(token: string, reminderId: string, status: string) {
  const qs = new URLSearchParams({ status });
  return request<{ reminder_id: string; status: string }>(`/api/v1/retention/reminders/${reminderId}/status?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface ReviewRequestRow {
  id: string;
  customer_id: string;
  job_id: string | null;
  channel: string;
  status: string;
  requested_at: string | null;
}

export interface FeedbackRow {
  id: string;
  customer_id: string;
  job_id: string | null;
  rating: number | null;
  sentiment: string | null;
  comment: string | null;
  received_at: string;
  // null = never asked, false = declined, true = explicitly granted —
  // never inferred; see retention.record_review_consent.
  consent_to_use_publicly: boolean | null;
}

export function listReviewRequests(token: string, customerId?: string) {
  const qs = customerId ? `?${new URLSearchParams({ customer_id: customerId }).toString()}` : "";
  return request<{ review_requests: ReviewRequestRow[] }>(`/api/v1/retention/reviews/requests${qs}`, { headers: authHeaders(token) });
}

export function sendReviewRequest(token: string, reviewRequestId: string) {
  return request<{ review_request_id: string; status: string; channel: string } | { status: string; approval_request_id: string }>(
    `/api/v1/retention/reviews/requests/${reviewRequestId}/send`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function listFeedback(token: string, sentiment?: string, customerId?: string) {
  const params: Record<string, string> = {};
  if (sentiment) params.sentiment = sentiment;
  if (customerId) params.customer_id = customerId;
  const qs = Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : "";
  return request<{ feedback: FeedbackRow[] }>(`/api/v1/retention/reviews/feedback${qs}`, { headers: authHeaders(token) });
}

export function recordFeedback(token: string, body: { customer_id: string; job_id?: string; rating?: number; comment?: string }) {
  return request<{ feedback_id: string; sentiment: string | null }>("/api/v1/retention/reviews/feedback", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

// A human recording a fact confirmed with the customer directly (never
// inferred, never AI-settable — see retention.record_review_consent).
export function recordReviewConsent(token: string, feedbackId: string, consent: boolean) {
  return request<{ feedback_id: string; consent_to_use_publicly: boolean | null }>(
    `/api/v1/retention/reviews/feedback/${feedbackId}/consent`,
    { method: "POST", headers: authHeaders(token), body: JSON.stringify({ consent }) }
  );
}

// Only succeeds server-side when the review is both eligible (real rating
// bar) and consented (real, human-recorded consent) — APPROVAL_REQUIRED
// by policy, so a real ApprovalRequest is created, not immediate content.
export function createContentFromReview(token: string, feedbackId: string) {
  return request<{ content: Record<string, unknown> } | { status: string; approval_request_id: string }>(
    `/api/v1/retention/reviews/feedback/${feedbackId}/create-content`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface ReferralProgramRow {
  id: string;
  name: string;
  campaign_id: string;
  reward_type: string;
  reward_amount: string | null;
  status: string;
}

export interface ReferralRow {
  id: string;
  program_id: string;
  referrer_customer_id: string;
  lead_id: string | null;
  referred_customer_id: string | null;
  status: string;
  revenue_amount: string | null;
  collected_amount: string | null;
}

export interface ReferralRewardRow {
  id: string;
  referral_id: string;
  customer_id: string;
  amount: string;
  status: string;
}

export function listReferralPrograms(token: string) {
  return request<{ programs: ReferralProgramRow[] }>("/api/v1/retention/referrals/programs", { headers: authHeaders(token) });
}

export function createReferralProgram(token: string, body: { name: string; reward_type?: string; reward_amount?: string }) {
  return request<{ program_id: string; campaign_id: string }>("/api/v1/retention/referrals/programs", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function getOrCreateReferralCode(token: string, programId: string, customerId: string) {
  return request<{ code_id: string; code: string }>("/api/v1/retention/referrals/codes", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ program_id: programId, customer_id: customerId }),
  });
}

export function listReferrals(token: string) {
  return request<{ referrals: ReferralRow[] }>("/api/v1/retention/referrals", { headers: authHeaders(token) });
}

export function createReferral(token: string, referralCodeId: string) {
  return request<{ referral_id: string; status: string }>("/api/v1/retention/referrals", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ referral_code_id: referralCodeId }),
  });
}

export function convertReferralToLead(token: string, referralId: string, body: { name: string; phone?: string; email?: string; service_requested?: string }) {
  return request<{ referral_id: string; status: string }>(`/api/v1/retention/referrals/${referralId}/convert-to-lead`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function listReferralRewards(token: string) {
  return request<{ rewards: ReferralRewardRow[] }>("/api/v1/retention/referrals/rewards", { headers: authHeaders(token) });
}

export function approveReferralReward(token: string, rewardId: string) {
  return request<{ reward_id: string; status: string }>(`/api/v1/retention/referrals/rewards/${rewardId}/approve`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function rejectReferralReward(token: string, rewardId: string) {
  return request<{ reward_id: string; status: string }>(`/api/v1/retention/referrals/rewards/${rewardId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function issueReferralReward(token: string, rewardId: string) {
  return request<{ reward_id: string; status: string }>(`/api/v1/retention/referrals/rewards/${rewardId}/issue`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Retention campaigns (win-back / post-job follow-up / VIP / etc.) ---

export interface RetentionCampaignRow {
  id: string;
  name: string;
  type: string;
  status: string;
}

export function listRetentionCampaigns(token: string) {
  return request<{ campaigns: RetentionCampaignRow[] }>("/api/v1/retention/campaigns", {
    headers: authHeaders(token),
  });
}

export function createRetentionCampaign(token: string, name: string, type: string) {
  return request<{ campaign_id: string; status: string }>("/api/v1/retention/campaigns", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name, type }),
  });
}

export function setRetentionCampaignStatus(token: string, campaignId: string, status: string) {
  const qs = new URLSearchParams({ status });
  return request<{ campaign_id: string; status: string }>(
    `/api/v1/retention/campaigns/${campaignId}/status?${qs.toString()}`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function enrollCustomerInRetentionCampaign(token: string, campaignId: string, customerId: string) {
  const qs = new URLSearchParams({ customer_id: customerId });
  return request<{ enrollment_id: string; status: string }>(
    `/api/v1/retention/campaigns/${campaignId}/enroll?${qs.toString()}`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function executeDueRetentionActivities(token: string) {
  return request<{ executed_activity_ids: string[] }>("/api/v1/retention/campaigns/activities/execute-due", {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Phase 8: Event Worker admin ---

export interface EventRow {
  event_id: string;
  event_type: string;
  status: string;
  retry_count: number;
  entity_type: string | null;
  entity_id: string | null;
  created_at: string;
}

export interface ProcessingAttemptRow {
  handler_name: string;
  status: string;
  attempts: number;
  last_error: string | null;
  last_attempt_at: string | null;
  processed_at: string | null;
}

export interface EventDetail {
  event_id: string;
  event_type: string;
  status: string;
  retry_count: number;
  payload: Record<string, unknown>;
  created_at: string;
  attempts: ProcessingAttemptRow[];
}

export interface DeadLetterRow {
  dead_letter_id: string;
  event_id: string;
  event_type: string;
  handler_name: string;
  reason: string;
  replayed: boolean;
  replayed_at: string | null;
  created_at: string;
}

export interface EventWorkerMetrics {
  events_processed: number;
  events_failed: number;
  events_retried: number;
  events_dead_lettered: number;
  events_deduplicated: number;
  ticks: number;
  started_at: string | null;
  last_tick_at: string | null;
  last_tick_duration_ms: number | null;
  per_event_type: Record<string, Record<string, number>>;
}

export function listEvents(token: string, statusFilter?: string) {
  const qs = statusFilter ? `?${new URLSearchParams({ status_filter: statusFilter }).toString()}` : "";
  return request<{ events: EventRow[] }>(`/api/v1/events${qs}`, { headers: authHeaders(token) });
}

export function getEventDetail(token: string, eventId: string) {
  return request<EventDetail>(`/api/v1/events/${eventId}/detail`, { headers: authHeaders(token) });
}

export function listDeadLetters(token: string, includeReplayed = false) {
  const qs = `?${new URLSearchParams({ include_replayed: String(includeReplayed) }).toString()}`;
  return request<{ dead_letters: DeadLetterRow[] }>(`/api/v1/events/dead-letters${qs}`, { headers: authHeaders(token) });
}

export function replayDeadLetter(token: string, deadLetterId: string) {
  return request<{ dead_letter_id: string; event_id: string; handler_name: string; result: string }>(
    `/api/v1/events/dead-letters/${deadLetterId}/replay`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function getEventWorkerMetrics(token: string) {
  return request<EventWorkerMetrics>("/api/v1/events/metrics", { headers: authHeaders(token) });
}

// --- Phase 8: Morning Brief ---

export interface MorningBriefInsightRow {
  insight_id: string;
  category: string;
  priority: string;
  summary: string;
  related_entity_type: string | null;
  related_entity_id: string | null;
}

export interface MorningBriefRecommendationRow {
  recommendation_id: string;
  what: string;
  why: string;
  related_entity_type: string | null;
  related_entity_id: string | null;
  next_action: string;
  executable: boolean;
  status: string;
  approval_request_id: string | null;
}

export interface MorningBriefData {
  brief_id: string | null;
  brief_date: string | null;
  generated_at: string | null;
  mode: string | null;
  generated_by: string | null;
  headline: string | null;
  ai_provider: string | null;
  ai_model: string | null;
  insights: MorningBriefInsightRow[];
  recommendations: MorningBriefRecommendationRow[];
}

export interface MorningBriefSettings {
  enabled: boolean;
  local_time: string;
  timezone: string;
}

export function getLatestMorningBrief(token: string) {
  return request<MorningBriefData>("/api/v1/morning-brief/latest", { headers: authHeaders(token) });
}

export function generateMorningBrief(token: string) {
  return request<{ brief_id: string; headline: string; mode: string }>("/api/v1/morning-brief/generate", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function executeRecommendation(token: string, recommendationId: string) {
  return request<{
    recommendation_id: string;
    status: string;
    tool_result: Record<string, unknown> | null;
    approval_request_id: string | null;
  }>(`/api/v1/morning-brief/recommendations/${recommendationId}/execute`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function dismissRecommendation(token: string, recommendationId: string) {
  return request<{ recommendation_id: string; status: string }>(
    `/api/v1/morning-brief/recommendations/${recommendationId}/dismiss`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function getMorningBriefSettings(token: string) {
  return request<MorningBriefSettings>("/api/v1/morning-brief/settings", { headers: authHeaders(token) });
}

export function updateMorningBriefSettings(token: string, settings: MorningBriefSettings) {
  return request<MorningBriefSettings>("/api/v1/morning-brief/settings", {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(settings),
  });
}

// --- Phase 9: Approval orchestration ---

export interface ApprovalRow {
  id: string;
  tool_name: string;
  action_type: string;
  reason: string;
  status: string;
  execution_status: string;
  requested_by_type: string;
  created_at: string;
  decided_at: string | null;
  executed_at: string | null;
}

export interface ApprovalDetail {
  approval_request_id: string;
  tenant_id: string;
  tool_name: string;
  action_type: string;
  reason: string;
  tool_input: Record<string, unknown>;
  status: string;
  requested_by_type: string;
  requested_by_id: string | null;
  decided_by: string | null;
  decision_note: string | null;
  execution_status: string;
  execution_result: Record<string, unknown> | null;
  execution_error: string | null;
  execution_attempts: number;
  created_at: string;
  decided_at: string | null;
  executed_at: string | null;
}

export function listApprovals(token: string, status?: string) {
  const qs = status ? `?${new URLSearchParams({ status_filter: status }).toString()}` : "";
  return request<{ approvals: ApprovalRow[] }>(`/api/v1/approvals${qs}`, { headers: authHeaders(token) });
}

export function getApprovalDetail(token: string, approvalId: string) {
  return request<ApprovalDetail>(`/api/v1/approvals/${approvalId}`, { headers: authHeaders(token) });
}

export function approveApproval(token: string, approvalId: string, decisionNote?: string) {
  return request<ApprovalDetail>(`/api/v1/approvals/${approvalId}/approve`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ decision_note: decisionNote ?? null }),
  });
}

export function rejectApproval(token: string, approvalId: string, decisionNote?: string) {
  return request<ApprovalDetail>(`/api/v1/approvals/${approvalId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ decision_note: decisionNote ?? null }),
  });
}

export function retryApprovalExecution(token: string, approvalId: string) {
  return request<ApprovalDetail>(`/api/v1/approvals/${approvalId}/retry`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Phase 9: AI activity (reuses AuditLog, no second audit store) ---

export interface AIActivityRow {
  id: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  tool: string | null;
  entity_type: string | null;
  entity_id: string | null;
  result: string;
  approval_id: string | null;
  created_at: string;
}

export function listAIActivity(token: string, limit = 100) {
  const qs = `?${new URLSearchParams({ limit: String(limit) }).toString()}`;
  return request<{ rows: AIActivityRow[] }>(`/api/v1/ai-activity${qs}`, { headers: authHeaders(token) });
}

// --- Phase 10A: per-tenant automation policy ---

export interface PolicyRow {
  tool_name: string;
  default_policy: string;
  current_policy: string;
  has_override: boolean;
  system_blocked: boolean;
  configured_by: string | null;
  updated_at: string | null;
  version: number;
}

export function listAutomationPolicies(token: string) {
  return request<{ policies: PolicyRow[] }>("/api/v1/automation/policies", { headers: authHeaders(token) });
}

export function setAutomationPolicy(token: string, toolName: string, policy: string) {
  return request<PolicyRow>(`/api/v1/automation/policies/${encodeURIComponent(toolName)}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ policy }),
  });
}

export function resetAutomationPolicy(token: string, toolName: string) {
  return request<PolicyRow>("/api/v1/automation/policies/reset", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ tool_name: toolName }),
  });
}

// --- Phase 10B: notifications ---

export interface NotificationRow {
  id: string;
  type: string;
  priority: string;
  title: string;
  body: string;
  entity_type: string | null;
  entity_id: string | null;
  status: string;
  read_at: string | null;
  created_at: string;
}

export function listNotifications(token: string, unreadOnly = false, limit = 50) {
  const qs = `?${new URLSearchParams({ unread_only: String(unreadOnly), limit: String(limit) }).toString()}`;
  return request<{ notifications: NotificationRow[] }>(`/api/v1/notifications${qs}`, {
    headers: authHeaders(token),
  });
}

export function getUnreadNotificationCount(token: string) {
  return request<{ unread_count: number }>("/api/v1/notifications/unread-count", { headers: authHeaders(token) });
}

export function markNotificationRead(token: string, id: string) {
  return request<NotificationRow>(`/api/v1/notifications/${id}/read`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function markAllNotificationsRead(token: string) {
  return request<{ marked_count: number }>("/api/v1/notifications/read-all", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function dismissNotification(token: string, id: string) {
  return request<NotificationRow>(`/api/v1/notifications/${id}/dismiss`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface NotificationPreferenceRow {
  type: string;
  channel: string;
  enabled: boolean;
}

export function getNotificationPreferences(token: string) {
  return request<{ preferences: NotificationPreferenceRow[] }>("/api/v1/notifications/preferences", {
    headers: authHeaders(token),
  });
}

export function setNotificationPreference(token: string, type: string, channel: string, enabled: boolean) {
  return request<NotificationPreferenceRow>("/api/v1/notifications/preferences", {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ type, channel, enabled }),
  });
}

// --- Integration connection status (used by the Automation Settings page
// to show Email/SMS as honestly NOT_CONNECTED when no real credentials
// exist — never fabricated) ---

export interface IntegrationStatusRow {
  provider: string;
  status: string;
  detail: string;
}

export function listIntegrationStatus(token: string) {
  return request<IntegrationStatusRow[]>("/api/v1/integrations", { headers: authHeaders(token) });
}

// --- Phase 12D: tenant-scoped integration connections (for providers
// where each tenant has their OWN external account — QuickBooks, Google
// Calendar, etc. — distinct from the platform-level providers above) ---

export interface IntegrationConnectionRow {
  provider: string;
  status: string;
  external_account_id: string | null;
  scopes: string | null;
  last_verified_at: string | null;
  last_error: string | null;
}

export function listIntegrationConnections(token: string) {
  return request<IntegrationConnectionRow[]>("/api/v1/integrations/connections", {
    headers: authHeaders(token),
  });
}

export function connectIntegration(
  token: string,
  provider: string,
  credential: Record<string, string>
) {
  return request<IntegrationConnectionRow>(`/api/v1/integrations/connections/${provider}/connect`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ credential }),
  });
}

export function verifyIntegrationConnection(token: string, provider: string) {
  return request<IntegrationConnectionRow>(`/api/v1/integrations/connections/${provider}/verify`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function disconnectIntegration(token: string, provider: string) {
  return request<IntegrationConnectionRow>(`/api/v1/integrations/connections/${provider}/disconnect`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export interface QuickBooksImportResult {
  customers_created: number;
  customers_matched: number;
  invoices_created: number;
  invoices_skipped: number;
  invoice_results: { quickbooks_invoice_id: string; status: string; invoice_id: string | null; reason: string | null }[];
}

export function importFromQuickBooks(token: string, maxRecords = 300) {
  return request<QuickBooksImportResult>("/api/v1/integrations/quickbooks/import", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ max_records: maxRecords }),
  });
}

// --- Phase 13: QuickBooks Online OAuth2 connect flow. Distinct from the
// generic connect() above (that's for a credential the caller already
// has in hand, like Stripe's secret key) — QuickBooks requires a real
// browser redirect through Intuit's own consent page, so the frontend
// only ever asks the backend for that real URL and sends the browser
// there; the backend's own callback (never called from the frontend
// directly) does the actual token exchange. ---

export function getQuickBooksAuthorizeUrl(token: string) {
  return request<{ authorization_url: string }>("/api/v1/integrations/quickbooks/authorize", {
    headers: authHeaders(token),
  });
}

// --- Phase 14: Google Calendar OAuth2 connect flow + appointment sync.
// Same shape as the QuickBooks flow above (real browser redirect through
// Google's own consent page; the backend's callback does the token
// exchange server-side, the frontend never sees a Google credential). ---

export function getGoogleCalendarAuthorizeUrl(token: string) {
  return request<{ authorization_url: string }>("/api/v1/integrations/google-calendar/authorize", {
    headers: authHeaders(token),
  });
}

export interface GoogleCalendarEntry {
  id: string;
  summary: string | null;
  primary: boolean;
  time_zone: string | null;
}

export function listGoogleCalendars(token: string) {
  return request<{ calendars: GoogleCalendarEntry[] }>("/api/v1/calendar/google/calendars", {
    headers: authHeaders(token),
  });
}

export function syncAppointmentToGoogle(token: string, appointmentId: string, calendarId = "primary") {
  const qs = new URLSearchParams({ calendar_id: calendarId });
  return request<{ action: string; google_event_id: string | null }>(
    `/api/v1/calendar/google/appointments/${appointmentId}/sync?${qs.toString()}`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface GoogleCalendarImportResult {
  appointments_created: number;
  appointments_skipped: number;
  results: { google_event_id: string; status: string; appointment_id: string | null; reason: string | null }[];
}

export function importFromGoogleCalendar(
  token: string,
  params: { calendar_id?: string; time_min: string; time_max: string; max_records?: number }
) {
  return request<GoogleCalendarImportResult>("/api/v1/calendar/google/import", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      calendar_id: params.calendar_id ?? "primary",
      time_min: params.time_min,
      time_max: params.time_max,
      max_records: params.max_records ?? 300,
    }),
  });
}

export interface AutonomyStats {
  date: string;
  automatic: number;
  approval_required: number;
  blocked: number;
  failed: number;
  total: number;
}

export function getAutonomyStats(token: string) {
  return request<AutonomyStats>("/api/v1/automation/autonomy-stats", { headers: authHeaders(token) });
}

// --- Phase 12: the Company-OS Knowledge Layer ---

export interface KnowledgeFileRow {
  path: string;
  content: string;
  updated_by: string | null;
  updated_at: string;
}

export function listKnowledgeFiles(token: string, prefix?: string) {
  const qs = prefix ? `?${new URLSearchParams({ prefix }).toString()}` : "";
  return request<{ files: KnowledgeFileRow[] }>(`/api/v1/knowledge/files${qs}`, { headers: authHeaders(token) });
}

export function getKnowledgeFile(token: string, path: string) {
  return request<KnowledgeFileRow>(`/api/v1/knowledge/files/${path}`, { headers: authHeaders(token) });
}

export function setKnowledgeFile(token: string, path: string, content: string) {
  return request<KnowledgeFileRow>(`/api/v1/knowledge/files/${path}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ content }),
  });
}

export function deleteKnowledgeFile(token: string, path: string) {
  return request<{ deleted: boolean }>(`/api/v1/knowledge/files/${path}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export function indexKnowledgeFile(token: string, path: string) {
  return request<{ file_path: string; status: string; chunk_count: number }>(
    `/api/v1/knowledge/files/${path}/index`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export interface KnowledgeSearchResultRow {
  file_path: string;
  chunk_index: number;
  content: string;
  score: number;
}

export interface KnowledgeSearchResponse {
  results: KnowledgeSearchResultRow[];
  available: boolean;
  error_detail: string | null;
}

export function searchKnowledge(token: string, query: string) {
  return request<KnowledgeSearchResponse>(`/api/v1/knowledge/search`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ query }),
  });
}

export interface KnowledgeAskCitation {
  file_path: string;
  chunk_index: number;
  score: number;
}

export interface KnowledgeAskResponse {
  available: boolean;
  answer: string | null;
  answered_from_excerpts: boolean | null;
  sources: string[] | null;
  citations: KnowledgeAskCitation[] | null;
  error_detail: string | null;
}

export function askKnowledge(token: string, question: string) {
  return request<KnowledgeAskResponse>(`/api/v1/knowledge/ask`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ question }),
  });
}

export interface VoiceSettings {
  enabled: boolean;
  greeting: string;
  business_hours_note: string | null;
  voice_name: string | null;
  updated_at: string;
  stt_provider: string;
  tts_provider: string;
}

export function getVoiceSettings(token: string) {
  return request<VoiceSettings>(`/api/v1/voice/settings`, { headers: authHeaders(token) });
}

export function updateVoiceSettings(
  token: string,
  body: Partial<Pick<VoiceSettings, "enabled" | "greeting" | "business_hours_note" | "voice_name">>
) {
  return request<VoiceSettings>(`/api/v1/voice/settings`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export interface VoiceCallRow {
  id: string;
  provider: string;
  external_call_id: string;
  direction: string;
  caller_number: string | null;
  status: string;
  outcome: string | null;
  started_at: string;
  ended_at: string | null;
  customer_id: string | null;
  lead_id: string | null;
  appointment_id: string | null;
  handoff_requested: boolean;
  handoff_reason: string | null;
  failure_reason: string | null;
  transcript: { role: string; text: string }[];
  booking: {
    state: string | null;
    service_type: string | null;
    service_summary: string | null;
    selected_slot: { start_time: string; end_time: string; label: string } | null;
  } | null;
  latency_ms: { conversation_ms: number | null; tts_ms: number | null; total_ms: number | null }[];
}

export function listVoiceCalls(token: string) {
  return request<{ calls: VoiceCallRow[] }>(`/api/v1/voice/calls`, { headers: authHeaders(token) });
}

export function getVoiceCall(token: string, callId: string) {
  return request<VoiceCallRow>(`/api/v1/voice/calls/${callId}`, { headers: authHeaders(token) });
}

// --- Automation Engine ---

export interface AutomationStep {
  action: string;
  params: Record<string, unknown>;
}

export interface ConditionNode {
  field?: string;
  op?: string;
  value?: unknown;
  and?: ConditionNode[];
  or?: ConditionNode[];
  not?: ConditionNode;
}

export interface AutomationRow {
  id: string;
  name: string;
  description: string | null;
  status: string;
  published_version_id: string | null;
  created_at: string;
  next_scheduled_run?: string | null;
}

export interface AutomationVersionRow {
  id: string;
  automation_id: string;
  version_number: number;
  trigger_type: string;
  trigger_config: Record<string, unknown>;
  condition: ConditionNode | null;
  steps: AutomationStep[];
  created_at: string;
}

export interface AutomationExecutionStepRow {
  id: string;
  step_index: number;
  action: string;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface AutomationExecutionRow {
  id: string;
  automation_id: string;
  automation_version_id: string;
  trigger_type: string;
  status: string;
  current_step_index: number;
  context: Record<string, unknown>;
  error: string | null;
  retry_count: number;
  temporal_workflow_id: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface AutomationExecutionDetail extends AutomationExecutionRow {
  steps: AutomationExecutionStepRow[];
}

export interface AutomationSummary {
  automations_total: number;
  automations_enabled: number;
  automations_scheduled: number;
  executions_running: number;
  executions_failed: number;
  executions_completed_today: number;
  pending_approvals: number;
}

export function getAutomationSummary(token: string) {
  return request<AutomationSummary>(`/api/v1/automations/summary`, { headers: authHeaders(token) });
}

// Phase 26: the Owner Attention Queue — one deterministic, prioritized
// list of everything across the business that deserves the owner's
// attention right now, built entirely from existing domain tables.
export interface AttentionItem {
  category: string;
  priority: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  score: number;
  title: string;
  reason: string;
  entity_type: string;
  entity_id: string;
  link: string;
  age_days: number | null;
  monetary_value: string | null;
}

export interface AttentionQueue {
  items: AttentionItem[];
  critical_count: number;
  high_count: number;
}

export function getAttentionQueue(token: string) {
  return request<AttentionQueue>(`/api/v1/dashboard/attention`, { headers: authHeaders(token) });
}

export interface AiHealth {
  provider_configured: boolean;
  provider_name: string;
  invocations_24h: number;
  invocations_24h_succeeded: number;
  invocations_24h_failed: number;
}

export function getAiHealth(token: string) {
  return request<AiHealth>(`/api/v1/dashboard/ai-health`, { headers: authHeaders(token) });
}

// --- AI kill switch — an org-wide emergency control (OWNER only to set,
// enforced for real in ToolRegistry.execute() for every non-human actor). ---

export interface KillSwitchStatus {
  ai_paused: boolean;
  ai_paused_at: string | null;
  ai_paused_by: string | null;
}

export function getKillSwitchStatus(token: string) {
  return request<KillSwitchStatus>("/api/v1/organization/kill-switch", { headers: authHeaders(token) });
}

export function setKillSwitch(token: string, active: boolean) {
  return request<KillSwitchStatus>("/api/v1/organization/kill-switch", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ active }),
  });
}

// Phase 27: the Owner Activity Feed — read-only, human-readable business
// history aggregated from existing persisted sources.
export interface ActivityItem {
  id: string;
  timestamp: string;
  activity_type: string;
  category: string;
  title: string;
  description: string;
  severity: "INFO" | "WARNING" | "ERROR";
  actor_type: string;
  actor_name: string | null;
  entity_type: string | null;
  entity_id: string | null;
  entity_label: string | null;
  link: string | null;
  status: string | null;
  metadata: Record<string, unknown> | null;
}

export interface ActivityFeed {
  items: ActivityItem[];
  total: number;
  page: number;
  page_size: number;
}

export function getActivityFeed(token: string, opts?: { page?: number; pageSize?: number; category?: string }) {
  const params = new URLSearchParams();
  if (opts?.page) params.set("page", String(opts.page));
  if (opts?.pageSize) params.set("page_size", String(opts.pageSize));
  if (opts?.category) params.set("category", opts.category);
  const qs = params.toString();
  return request<ActivityFeed>(`/api/v1/dashboard/activity${qs ? `?${qs}` : ""}`, { headers: authHeaders(token) });
}

export function listAutomations(token: string) {
  return request<{ automations: AutomationRow[] }>(`/api/v1/automations`, { headers: authHeaders(token) });
}

export function getAutomation(token: string, automationId: string) {
  return request<AutomationRow>(`/api/v1/automations/${automationId}`, { headers: authHeaders(token) });
}

export function listAutomationVersions(token: string, automationId: string) {
  return request<{ versions: AutomationVersionRow[] }>(`/api/v1/automations/${automationId}/versions`, {
    headers: authHeaders(token),
  });
}

export function createAutomation(
  token: string,
  body: {
    name: string;
    description?: string | null;
    trigger_type: string;
    trigger_config: Record<string, unknown>;
    condition: ConditionNode | null;
    steps: AutomationStep[];
  }
) {
  return request<AutomationRow>(`/api/v1/automations`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function updateAutomation(
  token: string,
  automationId: string,
  body: {
    trigger_type: string;
    trigger_config: Record<string, unknown>;
    condition: ConditionNode | null;
    steps: AutomationStep[];
  }
) {
  return request<AutomationVersionRow>(`/api/v1/automations/${automationId}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function publishAutomation(token: string, automationId: string) {
  return request<AutomationRow>(`/api/v1/automations/${automationId}/publish`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function setAutomationEnabled(token: string, automationId: string, enabled: boolean) {
  return request<AutomationRow>(`/api/v1/automations/${automationId}/enabled`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ enabled }),
  });
}

export function triggerAutomation(token: string, automationId: string, context: Record<string, unknown> = {}) {
  return request<AutomationExecutionRow | { deduplicated: true }>(`/api/v1/automations/${automationId}/trigger`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ context }),
  });
}

export function listAutomationExecutions(token: string, automationId: string) {
  return request<{ executions: AutomationExecutionRow[] }>(`/api/v1/automations/${automationId}/executions`, {
    headers: authHeaders(token),
  });
}

export function getAutomationExecution(token: string, executionId: string) {
  return request<AutomationExecutionDetail>(`/api/v1/automations/executions/${executionId}`, {
    headers: authHeaders(token),
  });
}

export function getAutomationTimezone(token: string) {
  return request<{ timezone: string }>(`/api/v1/automations/timezone`, { headers: authHeaders(token) });
}

export function setAutomationTimezone(token: string, tz: string) {
  return request<{ timezone: string }>(`/api/v1/automations/timezone`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ timezone: tz }),
  });
}

export function dispatchScheduledTick(token: string) {
  return request<{ dispatched_execution_ids: string[] }>(`/api/v1/automations/scheduled/dispatch-tick`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

// --- Company Memory ---

export interface CompanyMemoryRow {
  id: string;
  memory_type: string;
  key: string;
  value: string;
  description: string | null;
  source: string;
  source_entity_type: string | null;
  source_entity_id: string | null;
  created_by: string | null;
  status: string;
  confidence: number | null;
  effective_from: string | null;
  effective_until: string | null;
  supersedes_id: string | null;
  reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface CompanyMemoryContextEntry {
  memory_type: string;
  key: string;
  value: string;
  source: string;
}

export function listCompanyMemories(
  token: string,
  filters?: { memory_type?: string; status_filter?: string; key?: string; source?: string }
) {
  const qs = filters
    ? "?" + new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]).toString()
    : "";
  return request<{ memories: CompanyMemoryRow[] }>(`/api/v1/memory${qs}`, { headers: authHeaders(token) });
}

export function getCompanyMemoryContext(token: string) {
  return request<{ context: CompanyMemoryContextEntry[] }>(`/api/v1/memory/context`, { headers: authHeaders(token) });
}

export function getCompanyMemoryHistory(token: string, key: string) {
  return request<{ history: CompanyMemoryRow[] }>(`/api/v1/memory/history/${encodeURIComponent(key)}`, {
    headers: authHeaders(token),
  });
}

export function createCompanyMemory(
  token: string,
  body: {
    memory_type: string;
    key: string;
    value: string;
    description?: string | null;
    source?: string;
    effective_from?: string | null;
    effective_until?: string | null;
    reason?: string | null;
  }
) {
  return request<CompanyMemoryRow>(`/api/v1/memory`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
}

export function updatePendingMemory(token: string, memoryId: string, value: string, description?: string | null) {
  return request<CompanyMemoryRow>(`/api/v1/memory/${memoryId}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify({ value, description }),
  });
}

export function confirmMemory(token: string, memoryId: string) {
  return request<CompanyMemoryRow>(`/api/v1/memory/${memoryId}/confirm`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function rejectMemory(token: string, memoryId: string, reason?: string) {
  return request<CompanyMemoryRow>(`/api/v1/memory/${memoryId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export function revokeMemory(token: string, memoryId: string, reason?: string) {
  return request<CompanyMemoryRow>(`/api/v1/memory/${memoryId}/revoke`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

// --- Klaros's own SaaS subscription billing (real Stripe Checkout in
// subscription mode + Billing Portal) — a separate integration from the
// tenant's own Stripe key in Settings -> Integrations, which collects
// THAT tenant's own customer payments. ---

export interface BillingStatus {
  plan: string;
  billing_status: string;
  trial_ends_at: string | null;
  current_period_end: string | null;
  ai_usage_this_month: number;
  ai_usage_limit: number | null;
}

export function getBillingStatus(token: string) {
  return request<BillingStatus>("/api/v1/billing/status", { headers: authHeaders(token) });
}

export function createBillingCheckout(
  token: string,
  plan: "solo" | "growth",
  successUrl: string,
  cancelUrl: string
) {
  return request<{ checkout_url: string }>("/api/v1/billing/checkout", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ plan, success_url: successUrl, cancel_url: cancelUrl }),
  });
}

export function createBillingPortalSession(token: string, returnUrl: string) {
  return request<{ portal_url: string }>("/api/v1/billing/portal", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ return_url: returnUrl }),
  });
}
