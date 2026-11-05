import type {
  Invoice,
  ReviewCase,
  ReviewCaseDetail,
  ReviewPage,
  Validation,
  Workflow,
} from '../api';

export const API_ORIGIN = 'http://localhost:18000';
export const REVIEW_CASE_ID = '11111111-1111-4111-8111-111111111111';
export const INVOICE_ID = '22222222-2222-4222-8222-222222222222';
export const WORKFLOW_ID = '33333333-3333-4333-8333-333333333333';
const CREATED_AT = '2026-11-05T08:00:00Z';

export const invoice: Invoice = {
  invoice_id: INVOICE_ID,
  filename: 'acme-invoice-001.pdf',
  content_type: 'application/pdf',
  size_bytes: 12_345,
  status: 'TEXT_EXTRACTED',
  created_at: CREATED_AT,
  sha256: 'a'.repeat(64),
};

export const reviewCase: ReviewCase = {
  review_case_id: REVIEW_CASE_ID,
  invoice_id: INVOICE_ID,
  workflow_id: WORKFLOW_ID,
  validation_id: '44444444-4444-4444-8444-444444444444',
  status: 'OPEN',
  resolution: null,
  workflow_state: 'NEEDS_REVIEW',
  state_version: 1,
  validation_decision: 'REVIEW_REQUIRED',
  ruleset_version: 'invoice-validation-v1',
  created_at: CREATED_AT,
  resolved_at: null,
  reviewer_id: null,
  reviewer_note: null,
  resolution_request_id: null,
  is_current: true,
  is_actionable: true,
  invoice_filename: invoice.filename,
  invoice_created_at: CREATED_AT,
};

export const reviewDetail: ReviewCaseDetail = {
  ...reviewCase,
  validation_summary: {
    decision: 'REVIEW_REQUIRED',
    ruleset_version: 'invoice-validation-v1',
    finding_count: 1,
    failed_rule_codes: ['invoice_total_matches_lines'],
    failed_severities: ['ERROR'],
  },
};

export const resolvedReviewDetail: ReviewCaseDetail = {
  ...reviewDetail,
  status: 'RESOLVED',
  resolution: 'ACCEPT_EXCEPTION',
  workflow_state: 'EXCEPTION_ACCEPTED',
  state_version: 2,
  resolved_at: '2026-11-05T08:05:00Z',
  is_actionable: false,
  resolution_request_id: '55555555-5555-4555-8555-555555555555',
};

export const reviewPage: ReviewPage = {
  items: [reviewCase],
  limit: 20,
  offset: 0,
  total: 1,
};

export const workflow: Workflow = {
  workflow_id: WORKFLOW_ID,
  validation_id: reviewCase.validation_id,
  state: 'NEEDS_REVIEW',
  state_version: 1,
  created_at: CREATED_AT,
  updated_at: CREATED_AT,
  validation_decision: 'REVIEW_REQUIRED',
  ruleset_version: 'invoice-validation-v1',
  review_case: {
    review_case_id: REVIEW_CASE_ID,
    status: 'OPEN',
    resolution: null,
  },
};

export const validation: Validation = {
  validation_id: reviewCase.validation_id,
  invoice_id: INVOICE_ID,
  structured_extraction_id: '66666666-6666-4666-8666-666666666666',
  ruleset_version: 'invoice-validation-v1',
  final_decision: 'REVIEW_REQUIRED',
  pass_count: 4,
  fail_count: 1,
  not_evaluable_count: 0,
  created_at: CREATED_AT,
  findings: [
    {
      rule_id: 'invoice_total_matches_lines',
      rule_name: 'Invoice total matches line items',
      status: 'FAIL',
      severity: 'ERROR',
      message: 'The invoice total does not match the line-item total.',
      affected_fields: ['total_amount', 'line_items'],
      metadata: {},
    },
  ],
};
