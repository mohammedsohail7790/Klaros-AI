const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new ApiError(res.status, detail || "Request failed");
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

// --- Operations: workers ---

export interface Worker {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  status: string;
  active: boolean;
}

export function createWorker(token: string, name: string) {
  return request<{ worker: Worker }>("/api/v1/workers", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ name }),
  });
}

export function listWorkers(token: string) {
  return request<{ workers: Worker[] }>("/api/v1/workers", { headers: authHeaders(token) });
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
