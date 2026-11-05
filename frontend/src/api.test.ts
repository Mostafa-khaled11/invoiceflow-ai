import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';
import { ApiError, api } from './api';
import { API_ORIGIN, REVIEW_CASE_ID, reviewPage } from './test/fixtures';
import { server } from './test/server';

describe('frontend API client', () => {
  afterEach(() => server.resetHandlers());

  it('builds query parameters and parses a review page', async () => {
    let receivedUrl = '';
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases`, ({ request }) => {
        receivedUrl = request.url;
        return HttpResponse.json(reviewPage);
      }),
    );

    const result = await api.reviews.list(
      new URLSearchParams({ status: 'OPEN', workflow_state: 'NEEDS_REVIEW' }),
    );

    expect(result.items[0]?.review_case_id).toBe(REVIEW_CASE_ID);
    expect(new URL(receivedUrl).searchParams.get('status')).toBe('OPEN');
    expect(new URL(receivedUrl).searchParams.get('workflow_state')).toBe(
      'NEEDS_REVIEW',
    );
  });

  it('normalizes structured HTTP errors', async () => {
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases`, () =>
        HttpResponse.json(
          { detail: { code: 'FILTER_INVALID', message: 'Invalid filter.' } },
          { status: 422 },
        ),
      ),
    );

    const request = api.reviews.list(new URLSearchParams());
    await expect(request).rejects.toMatchObject({
      name: 'Error',
      status: 422,
      code: 'FILTER_INVALID',
      message: 'Invalid filter.',
    } satisfies Partial<ApiError>);
  });

  it('converts network failures into an ApiError with status zero', async () => {
    server.use(
      http.get(`${API_ORIGIN}/api/v1/review-cases`, () => HttpResponse.error()),
    );

    await expect(api.reviews.list(new URLSearchParams())).rejects.toMatchObject({
      status: 0,
      message: 'Unable to reach the InvoiceFlow API.',
    });
  });
});
