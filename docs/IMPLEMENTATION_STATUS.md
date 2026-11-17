# Implementation status

Implemented locally:

- Authenticated Next.js proxy and FastAPI ownership checks; caller-supplied user IDs cannot override ownership.
- One Python processing pipeline; duplicate serverless submission simulation removed.
- Persistent runs, atomic application claims, conservative daily attempt quotas, heartbeat monitoring, stale recovery, and per-attempt evidence.
- Explicit confirmation required for applied status; negative/pre-existing confirmation and ambiguous outcomes cannot become successes.
- Private screenshot storage with signed access; historical unverified application records flagged for review.
- PDF validation, preserved preferences/answers, versioned embeddings, explicit eligibility evaluation, provider preflight, and visible persistence failures.
- Authentication screen, live run polling, offline worker state, honest application filters, and confirmed settings saves.
- Production frontend build, dependency lockfile, Docker services, one scheduler, and deployment/recovery documentation.

## Validation completed

- 18 offline backend/API tests pass.
- 3 isolated PostgreSQL integration tests pass (claims, quotas, fencing, stale recovery, and owner-scoped read access).
- 1 controlled Chrome submission test passes, including positive confirmation, validation failure, negative confirmation text, and pre-existing confirmation.
- Production Next.js 15.5.29 build and TypeScript validation pass.
- Browser checks pass for login gate, authenticated dashboard requests, failed-save rollback, queued run feedback, and mobile width; fixtures were used, with external requests intercepted.
- API health returns 200; unauthenticated API/proxy requests return 401.
- Configured Gemini and Groq model availability verified through read-only model listings.
- No service-role or provider keys found in generated browser JavaScript.

The isolated SQL fixture validates the new migrations against PostgreSQL, not the original pgvector baseline or the live Supabase Auth/Storage configuration.

## Remaining acceptance

Complete a controlled employer-form submission and two-account isolation check before enabling background scheduling. The worker accepts manual submission runs only when a candidate has enabled Auto-Apply and clicked Search and apply. The six legacy applied records are flagged for review.

## Design report

https://www.lazyweb.com/report/lazyweb/dd7e54b1-1338-4433-9daa-43a3b66964c1/?source=create

## Release follow-up (2026-11-17)

The linked Supabase project is shared with unrelated workloads and has no separate staging branch. A pre-migration dump is saved on the VPS and locally in the private Job Aid backup directory. Migrations `20261117001` and `20261117002` are recorded remotely. The proofs bucket is private; all six historical applied rows require evidence review.

The API and worker run in isolated, resource-limited Docker services on the existing VPS. The worker sends heartbeats; background scheduling is disabled, and employer submissions require the candidate's Auto-Apply setting plus a manual Search and apply request. Vercel production is published at https://autonomous-job-agent-kappa.vercel.app and has a server-only backend URL. Supabase service and model-provider keys were removed from Vercel. Email confirmation redirects allow the production aliases. The API responds at https://resolver.doxad.cloud/job-aid/api/health with a valid HTTPS certificate. The older VPS hostname has an expired certificate and an IPv6 challenge route outside this server; Job Aid no longer relies on that hostname.

## Live release evidence

The production Vercel alias served the login page and returned 401 for an unauthenticated API request. A disposable confirmed Supabase account received a 200 authenticated dashboard response through Vercel and FastAPI; the response contained no profile belonging to another account and reported the worker online. A synthetic PDF upload returned 200, saved a profile and private resume path, and the profile was visible on the next authenticated dashboard read. The disposable account and uploaded object were removed. These checks used synthetic data and did not submit to an employer.

The manual run path was also checked with a separate disposable profile. Vercel returned 202 for a discovery-disabled and submission-disabled run; the persistent worker claimed and completed it. The disposable account was removed afterward. The production action now says Search and apply when Auto-Apply is enabled, and that explicit action can request employer submissions. Background scheduling remains off. A controlled employer confirmation remains unverified.
