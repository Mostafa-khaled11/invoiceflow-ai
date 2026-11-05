# Milestone 8A - Frontend Automated Testing

## Objectives

Milestone 8A adds deterministic frontend regression protection around the
existing InvoiceFlow AI React application. It covers the actual review queue,
review resolution, invoice detail, API client, loading, empty, and failure
behaviors without calling the backend, Groq, PostgreSQL, n8n, or external
services.

## Testing Architecture

- Vitest 4.1.12 runs TypeScript unit and component tests in `jsdom`.
- React Testing Library 16.3.3 and `user-event` 14.6.1 exercise visible UI
  behavior and real user interactions.
- MSW 2.11.5 intercepts `fetch` calls through a Node request server. Unhandled
  requests fail tests, and handlers reset after every test.
- `@testing-library/jest-dom` provides semantic DOM assertions.
- `@vitest/coverage-v8` 4.1.12 reports statements, branches, functions, and
  lines.
- Playwright Test 1.64.1 runs four Chromium browser scenarios. Its web server
  uses a separate localhost port, `4173`, so it does not depend on the running
  Docker frontend on `5173`.
- Browser tests intercept every application API request with Playwright route
  handlers before navigation. They do not mutate live records.

## Test Inventory

| Feature | Source | Test layer | Scenarios | Result |
|---|---|---|---|---|
| Review queue | `src/App.tsx` | Vitest + RTL/MSW | Success, empty, HTTP 500, retry recovery | PASS |
| Review detail | `src/App.tsx` | Vitest + RTL/MSW | Detail loading and displayed workflow/validation context | PASS |
| Accept Exception | `src/App.tsx` | Vitest + Playwright | Confirmation, POST payload, resolved state | PASS |
| Reject | `src/App.tsx` | Vitest + Playwright | Confirmation and resolved state | PASS |
| Invoice detail | `src/App.tsx` | Vitest + RTL/MSW | Three endpoint responses and invoice error state | PASS |
| API client | `src/api.ts` | Vitest + MSW | Query construction, structured HTTP error, network error | PASS |
| Browser startup | Existing app shell | Playwright | Navigation and mocked review data | PASS |
| Browser failure handling | Existing app shell | Playwright | Mocked review API failure | PASS |

Invoice upload is not tested because the current frontend has no upload UI.
No upload feature or test-only production branch was introduced.

## Test Structure

```text
frontend/
  src/
    App.test.tsx
    api.test.ts
    test/
      fixtures.ts
      handlers.ts
      server.ts
      setup.ts
      helpers/render.tsx
  e2e/
    review-workflow.spec.ts
  playwright.config.ts
  vitest.config.ts
```

Fixtures contain synthetic identifiers and invoice data only. They mirror the
existing TypeScript API contracts and backend workflow terminology.

## Coverage Results

The measured selected-source coverage is:

| Metric | Result |
|---|---:|
| Statements | 69.23% (108/156) |
| Branches | 69.23% (99/143) |
| Functions | 60.34% (35/58) |
| Lines | 88.00% (44/50) |

Coverage is reported for `src/App.tsx` and `src/api.ts`. Entry-point, test,
setup, and environment declaration files are excluded as non-target code. No
arbitrary global threshold was added; the measured baseline should guide later
expansion.

## Bug Found and Fixed

The review queue rendered its loading state whenever `page` was null before it
checked `error`. A failed initial API request therefore left the user on an
indefinite loading screen and hid the retry action.

`src/App.tsx` now prioritizes the error state and ensures a failed request
provides an empty page value so the error branch is reachable. The regression
test `shows an API failure and recovers through the retry action` verifies both
the visible error and successful recovery.

## PowerShell Commands

Run from `D:\InvoiceFlow AI\frontend`:

```powershell
npm ci
npm test
npm run test:watch
npm run test:coverage
npx playwright install chromium
npm run test:e2e
npm run lint
npm run build
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
```

The Playwright browser install is required once per machine or CI worker.
Normal Vitest tests, production builds, and linting do not require the browser
binary.

## Verification Results

- Vitest: 10 passed, repeated twice after final test changes.
- Coverage: generated successfully with all four requested metrics.
- Playwright Chromium: 4 passed.
- ESLint: PASS.
- TypeScript and Vite production build: PASS.
- Full npm audit: 0 vulnerabilities.
- Production-only npm audit: 0 vulnerabilities.
- Backend Pytest: 260 passed, 14 skipped, 6 warnings.
- Ruff check and format: PASS.
- Backend and root Pyright: 0 errors.

Testing dependencies were checked after installation. Vitest was selected at
4.1.12 because earlier 3.x versions introduced critical audit findings in
Vitest/tinypool. The final resolved tree is clean. The existing targeted
minimatch override from Milestone 7C remains in place and is also clean.

## Limitations and Uncovered Scenarios

- There is no frontend component-test framework beyond the new coverage, and
  no visual-regression testing.
- Invoice lookup validation and pagination/filter interactions have limited
  dedicated coverage beyond the exercised queue path.
- Browser coverage currently targets Chromium only.
- The tests do not verify backend/database contract drift against a live API;
  the fixtures are contract-shaped and should be reviewed when API schemas
  change.

## Future CI Integration

Milestone 8B can run `npm ci`, `npm test`, `npm run test:coverage`, and
`npm run test:e2e` in separate CI steps. The browser job should install
Chromium with `npx playwright install --with-deps chromium` on a compatible
runner, publish Playwright reports on failure, and retain npm audit output.
Backend gates should remain a separate job so frontend tests stay independent
of Docker and external services.
