import { expect, test } from '@playwright/test';
import {
  API_ORIGIN,
  INVOICE_ID,
  REVIEW_CASE_ID,
  invoice,
  reviewDetail,
  reviewPage,
  resolvedReviewDetail,
  workflow,
} from '../src/test/fixtures';

test.beforeEach(async ({ page }) => {
  await page.route(`${API_ORIGIN}/api/v1/review-cases*`, async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith(REVIEW_CASE_ID)) {
      await route.fulfill({ json: reviewDetail });
      return;
    }
    await route.fulfill({ json: reviewPage });
  });
});

test('opens the application and displays the mocked review queue', async ({ page }) => {
  await page.goto('/reviews');

  await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible();
  await expect(page.getByText(invoice.filename)).toBeVisible();
  await expect(page.getByText('NEEDS REVIEW', { exact: true })).toBeVisible();
});

test('completes a mocked Accept Exception journey', async ({ page }) => {
  let resolved = false;
  await page.route(`${API_ORIGIN}/api/v1/review-cases/${REVIEW_CASE_ID}`, async route => {
    await route.fulfill({ json: resolved ? resolvedReviewDetail : reviewDetail });
  });
  await page.route(
    `${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow/review`,
    async route => {
      resolved = true;
      await route.fulfill({ json: workflow });
    },
  );
  await page.goto(`/reviews/${REVIEW_CASE_ID}`);

  await page.getByRole('button', { name: 'Accept exception' }).click();
  await expect(page.getByRole('dialog')).toContainText('Accept this exception?');
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText('Exception accepted', { exact: true })).toBeVisible();
});

test('completes a mocked Reject journey', async ({ page }) => {
  const rejected = {
    ...resolvedReviewDetail,
    resolution: 'REJECT' as const,
    workflow_state: 'REJECTED' as const,
  };
  let resolved = false;
  await page.route(`${API_ORIGIN}/api/v1/review-cases/${REVIEW_CASE_ID}`, async route => {
    await route.fulfill({ json: resolved ? rejected : reviewDetail });
  });
  await page.route(
    `${API_ORIGIN}/api/v1/invoices/${INVOICE_ID}/workflow/review`,
    async route => {
      resolved = true;
      await route.fulfill({ json: workflow });
    },
  );
  await page.goto(`/reviews/${REVIEW_CASE_ID}`);

  await page.getByRole('button', { name: 'Reject invoice' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText('Invoice rejected')).toBeVisible();
});

test('shows a mocked API failure gracefully', async ({ page }) => {
  await page.unroute(`${API_ORIGIN}/api/v1/review-cases*`);
  await page.route(`${API_ORIGIN}/api/v1/review-cases*`, async route => {
    await route.fulfill({ status: 500, json: { detail: 'Review service unavailable.' } });
  });
  await page.goto('/reviews');

  await expect(page.getByText('Unable to load this view')).toBeVisible();
  await expect(page.getByText('Review service unavailable.')).toBeVisible();
});
