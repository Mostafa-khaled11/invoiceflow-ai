import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { API_ORIGIN, INVOICE_ID, REVIEW_CASE_ID, invoice, reviewDetail, reviewPage, resolvedReviewDetail, workflow } from './test/fixtures';
import { render } from './test/helpers/render';
import { server } from './test/server';

function renderAt(path: string): void {
  window.history.pushState({}, '', path);
  render(<App />);
}

describe('InvoiceFlow review and invoice UI', () => {
  afterEach(() => server.resetHandlers());

  it('loads and displays the actionable review queue', async () => {
    renderAt('/reviews');

    expect(await screen.findByRole('heading', { name: 'Review queue' })).toBeVisible();
    expect(await screen.findByText(invoice.filename)).toBeVisible();
    expect(screen.getByText('NEEDS REVIEW')).toBeVisible();
    expect(screen.getByText('REVIEW REQUIRED')).toBeVisible();
  });

  it('shows the empty queue state', async () => {
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases`, () =>
        HttpResponse.json({ ...reviewPage, items: [], total: 0 }),
      ),
    );
    renderAt('/reviews');

    expect(
      await screen.findByText('No invoices currently require review'),
    ).toBeVisible();
  });

  it('shows an API failure and recovers through the retry action', async () => {
    const user = userEvent.setup();
    let failed = true;
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases`, () => {
        if (failed) {
          failed = false;
          return HttpResponse.json(
            { detail: 'Review service unavailable.' },
            { status: 500 },
          );
        }
        return HttpResponse.json(reviewPage);
      }),
    );
    renderAt('/reviews');

    expect(await screen.findByText('Unable to load this view')).toBeVisible();
    expect(screen.getByText('Review service unavailable.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText(invoice.filename)).toBeVisible();
  });

  it('accepts an exception after confirmation and refreshes the case', async () => {
    const user = userEvent.setup();
    let resolved = false;
    let requestBody: unknown;
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases/${REVIEW_CASE_ID}`, () =>
        HttpResponse.json(resolved ? resolvedReviewDetail : reviewDetail),
      ),
      http.post(
        `${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow/review`,
        async ({ request }) => {
          requestBody = await request.json();
          resolved = true;
          return HttpResponse.json(workflow);
        },
      ),
    );
    renderAt(`/reviews/${REVIEW_CASE_ID}`);

    await user.click(await screen.findByRole('button', { name: 'Accept exception' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Accept this exception?');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(await screen.findByText('Exception accepted')).toBeVisible();
    expect(requestBody).toMatchObject({ decision: 'ACCEPT_EXCEPTION' });
  });

  it('rejects an invoice after confirmation', async () => {
    const user = userEvent.setup();
    let resolved = false;
    const rejected = {
      ...resolvedReviewDetail,
      resolution: 'REJECT' as const,
      workflow_state: 'REJECTED' as const,
    };
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases/${REVIEW_CASE_ID}`, () =>
        HttpResponse.json(resolved ? rejected : reviewDetail),
      ),
      http.post(
        `${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow/review`,
        () => {
          resolved = true;
          return HttpResponse.json(workflow);
        },
      ),
    );
    renderAt(`/reviews/${REVIEW_CASE_ID}`);

    await user.click(await screen.findByRole('button', { name: 'Reject invoice' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Reject this invoice?');
    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText('Invoice rejected')).toBeVisible();
  });

  it('renders invoice, validation, and workflow data from their separate APIs', async () => {
    renderAt(`/invoices/${INVOICE_ID}`);

    expect(await screen.findByRole('heading', { name: invoice.filename })).toBeVisible();
    expect(screen.getByText('invoice-validation-v1')).toBeVisible();
    expect(screen.getByText('NEEDS REVIEW')).toBeVisible();
    expect(screen.getByText('The invoice total does not match the line-item total.')).toBeVisible();
  });

  it('shows the invoice error state instead of an endless loading state', async () => {
    server.use(
      http.get(`${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}`, () =>
        HttpResponse.json({ detail: 'Invoice not found.' }, { status: 404 }),
      ),
    );
    renderAt(`/invoices/${INVOICE_ID}`);

    await waitFor(() => expect(screen.getByText('Unable to load this view')).toBeVisible());
    expect(screen.getByText('Invoice not found.')).toBeVisible();
  });
});
