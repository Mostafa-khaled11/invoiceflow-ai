export type WorkflowState =
  | 'READY'
  | 'NEEDS_REVIEW'
  | 'BLOCKED'
  | 'EXCEPTION_ACCEPTED'
  | 'REJECTED';
export type ValidationDecision = 'PASS' | 'WARNING' | 'REVIEW_REQUIRED' | 'BLOCKED';
export type ReviewStatus = 'OPEN' | 'RESOLVED';
export type ReviewResolution = 'ACCEPT_EXCEPTION' | 'REJECT';

export interface ApiErrorPayload { detail?: { code?: string; message?: string } | string }
export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) { super(message); }
}

export interface Invoice { invoice_id: string; filename: string; content_type: string; size_bytes: number; status: string; created_at: string; sha256: string }
export interface Workflow { workflow_id: string; validation_id: string; state: WorkflowState; state_version: number; created_at: string; updated_at: string; validation_decision: ValidationDecision; ruleset_version: string; review_case: WorkflowReviewSummary | null }
export interface WorkflowReviewSummary { review_case_id: string; status: ReviewStatus; resolution: ReviewResolution | null; resolved_at?: string | null; resolution_request_id?: string | null }
export interface ValidationFinding { rule_id: string; rule_name: string; status: string; severity: string; message: string; affected_fields: string[]; actual_value?: string | null; expected_value?: string | null; metadata: Record<string, unknown> }
export interface Validation { validation_id: string; invoice_id: string; structured_extraction_id: string; ruleset_version: string; final_decision: ValidationDecision; pass_count: number; fail_count: number; not_evaluable_count: number; created_at: string; findings: ValidationFinding[] }
export interface StructuredData { vendor_name: string | null; invoice_number: string | null; invoice_date: string | null; due_date: string | null; currency: string | null; subtotal: string | null; tax_total: string | null; total_amount: string | null; line_items: Array<{ description: string | null; quantity: string | null; unit_price: string | null; line_total: string | null }> }
export interface StructuredExtraction { data: StructuredData; status: string; prompt_version: string; model: string; provider: string }
export interface ReviewCase { review_case_id: string; invoice_id: string; workflow_id: string; validation_id: string; status: ReviewStatus; resolution: ReviewResolution | null; workflow_state: WorkflowState; state_version: number; validation_decision: ValidationDecision; ruleset_version: string; created_at: string; resolved_at: string | null; reviewer_id: string | null; reviewer_note: string | null; resolution_request_id: string | null; is_current: boolean; is_actionable: boolean; invoice_filename: string; invoice_created_at: string }
export interface ReviewCaseDetail extends ReviewCase { validation_summary: { decision: ValidationDecision; ruleset_version: string; finding_count: number; failed_rule_codes: string[]; failed_severities: string[] } }
export interface ReviewPage { items: ReviewCase[]; limit: number; offset: number; total: number }

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:18000').replace(/\/$/, '');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`${baseUrl}${path}`, { headers: { Accept: 'application/json', ...(init?.headers ?? {}) }, ...init }); }
  catch { throw new ApiError(0, 'Unable to reach the InvoiceFlow API.'); }
  if (!response.ok) {
    let payload: ApiErrorPayload = {};
    try { payload = (await response.json()) as ApiErrorPayload; } catch { /* empty body */ }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail?.message;
    throw new ApiError(response.status, detail ?? `Request failed (${response.status}).`, typeof payload.detail === 'object' ? payload.detail.code : undefined);
  }
  return (await response.json()) as T;
}

export const api = {
  invoices: {
    get: (id: string) => request<Invoice>(`/api/v1/invoices/${id}`),
    validation: (id: string) => request<Validation>(`/api/v1/invoices/${id}/validation`),
    workflow: (id: string) => request<Workflow>(`/api/v1/invoices/${id}/workflow`),
    extraction: (id: string) => request<StructuredExtraction>(`/api/v1/invoices/${id}/extract-structured`),
    route: (id: string) => request<Workflow>(`/api/v1/invoices/${id}/workflow/route`, { method: 'POST' }),
    resolve: (id: string, decision: ReviewResolution, requestId: string, note: string) => request<Workflow>(`/api/v1/invoices/${id}/workflow/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, resolution_request_id: requestId, note: note || undefined }) }),
  },
  reviews: {
    list: (params: URLSearchParams) => request<ReviewPage>(`/api/v1/review-cases?${params.toString()}`),
    get: (id: string) => request<ReviewCaseDetail>(`/api/v1/review-cases/${id}`),
  },
};

export function makeRequestId(): string {
  return crypto.randomUUID();
}
