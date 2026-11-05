import { http, HttpResponse } from 'msw';
import {
  API_ORIGIN,
  INVOICE_ID,
  REVIEW_CASE_ID,
  invoice,
  reviewDetail,
  reviewPage,
  validation,
  workflow,
} from './fixtures';

export const handlers = [
  http.get(`${API_ORIGIN}/api/v1/review-cases`, () => HttpResponse.json(reviewPage)),
  http.get(`${API_ORIGIN}/api/v1/review-cases/${REVIEW_CASE_ID}`, () =>
    HttpResponse.json(reviewDetail),
  ),
  http.get(`${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}`, () =>
    HttpResponse.json(invoice),
  ),
  http.get(`${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/validation`, () =>
    HttpResponse.json(validation),
  ),
  http.get(`${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow`, () =>
    HttpResponse.json(workflow),
  ),
  http.post(`${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow/review`, () =>
    HttpResponse.json(workflow),
  ),
];
