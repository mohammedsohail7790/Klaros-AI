const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
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
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
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

export function triggerInvoiceFromJob(token: string, jobId: string) {
  const qs = new URLSearchParams({ job_id: jobId });
  return request<{ invoice: Invoice; deduplicated: boolean }>(`/api/v1/invoices/trigger-from-job?${qs.toString()}`, {
    method: "POST",
    headers: authHeaders(token),
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

export function attributeLead(
  token: string,
  body: { lead_id: string; campaign_id?: string; source?: string; medium?: string; attribution_model?: string }
) {
  return request<{ attribution: Record<string, unknown> }>("/api/v1/marketing/attribution/leads", {
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

export function listRetentionOpportunities(token: string, status?: string) {
  const qs = status ? `?${new URLSearchParams({ status }).toString()}` : "";
  return request<{ opportunities: RetentionOpportunityRow[] }>(`/api/v1/retention/opportunities${qs}`, { headers: authHeaders(token) });
}

export function updateOpportunityStatus(token: string, opportunityId: string, status: string) {
  const qs = new URLSearchParams({ status });
  return request<{ opportunity_id: string; status: string }>(`/api/v1/retention/opportunities/${opportunityId}/status?${qs.toString()}`, {
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

export function listServiceReminders(token: string) {
  return request<{ reminders: ServiceReminderRow[] }>("/api/v1/retention/reminders", { headers: authHeaders(token) });
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
}

export function listReviewRequests(token: string) {
  return request<{ review_requests: ReviewRequestRow[] }>("/api/v1/retention/reviews/requests", { headers: authHeaders(token) });
}

export function sendReviewRequest(token: string, reviewRequestId: string) {
  return request<{ review_request_id: string; status: string; channel: string } | { status: string; approval_request_id: string }>(
    `/api/v1/retention/reviews/requests/${reviewRequestId}/send`,
    { method: "POST", headers: authHeaders(token) }
  );
}

export function listFeedback(token: string, sentiment?: string) {
  const qs = sentiment ? `?${new URLSearchParams({ sentiment }).toString()}` : "";
  return request<{ feedback: FeedbackRow[] }>(`/api/v1/retention/reviews/feedback${qs}`, { headers: authHeaders(token) });
}

export function recordFeedback(token: string, body: { customer_id: string; job_id?: string; rating?: number; comment?: string }) {
  return request<{ feedback_id: string; sentiment: string | null }>("/api/v1/retention/reviews/feedback", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
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
