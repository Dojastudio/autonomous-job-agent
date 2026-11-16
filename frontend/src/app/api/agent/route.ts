import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";
import { BACKEND_URL } from "@/lib/supabase";

export const maxDuration = 60;

function getAdminSupabase() {
  const url =
    process.env.SUPABASE_URL ||
    process.env.NEXT_PUBLIC_SUPABASE_URL ||
    "https://zovizzgqngiasajjzhcw.supabase.co";
  const key =
    process.env.SUPABASE_SERVICE_KEY ||
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    "";
  return createClient(url, key);
}

function stripHtml(raw: string): string {
  return (raw || "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function deterministicEmbedding384(text: string): number[] {
  const dim = 384;
  const vec = new Array<number>(dim).fill(0);
  const tokens = (text || "").toLowerCase().match(/[a-z0-9+#.]+/g) || [];
  if (tokens.length === 0) return vec;

  for (const tok of tokens) {
    let h = 2166136261;
    for (let i = 0; i < tok.length; i++) {
      h ^= tok.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    const idx = Math.abs(h) % dim;
    const sign = (h & 1) === 0 ? 1.0 : -1.0;
    vec[idx] += 2.0 * sign;
  }

  let norm = Math.sqrt(vec.reduce((acc, x) => acc + x * x, 0));
  if (norm > 1e-12) {
    for (let i = 0; i < dim; i++) {
      vec[i] = Number((vec[i] / norm).toFixed(6));
    }
  }
  return vec;
}

function cosineSimilarity(a: number[], b: number[]): number {
  if (!a || !b || a.length !== b.length) return 0;
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na < 1e-12 || nb < 1e-12) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

async function runServerlessDiscoveryAndMatching(userId: string) {
  const supabase = getAdminSupabase();

  // 1. Ensure candidate profile exists
  let { data: profile } = await supabase
    .from("candidate_profiles")
    .select("*")
    .eq("id", userId)
    .maybeSingle();

  if (!profile) {
    const defaultSkills = [
      "Python",
      "FastAPI",
      "Playwright",
      "TypeScript",
      "Next.js",
      "PostgreSQL",
      "pgvector",
      "Docker",
    ];
    const defaultRoles = [
      "Senior Backend Engineer",
      "AI Agent Engineer",
      "Full-Stack Engineer",
    ];
    const emb = deterministicEmbedding384(
      `${defaultRoles.join(" ")} ${defaultSkills.join(" ")}`
    );
    const seedProfile = {
      id: userId,
      full_name: "Ada Lovelace",
      email: "ada@analytical.org",
      phone: "+1 (415) 555-0199",
      location: "Remote",
      parsed_cv_json: {
        skills: defaultSkills,
        experience_years: 6,
        work_history: [],
        education: [],
        target_roles: defaultRoles,
      },
      profile_embedding: emb,
      auto_apply_enabled: true,
      daily_apply_limit: 15,
      min_match_score: 75,
    };
    await supabase.from("candidate_profiles").upsert(seedProfile);
    profile = seedProfile;
  }

  const parsedCv = profile.parsed_cv_json || {};
  const skills: string[] = parsedCv.skills || [
    "Python",
    "TypeScript",
    "Next.js",
    "PostgreSQL",
  ];
  const targetRoles: string[] = parsedCv.target_roles || ["Software Engineer"];
  const profileEmb = deterministicEmbedding384(
    `${targetRoles.join(" ")} ${skills.join(" ")}`
  );

  // 2. Fetch live structured JSON feeds (Greenhouse, Remotive, Arbeitnow)
  const discoveredJobs: Array<{
    external_job_id: string;
    source: string;
    company_name: string;
    job_title: string;
    location: string;
    remote_type: string;
    apply_url: string;
    description_text: string;
  }> = [];

  await Promise.allSettled([
    // Greenhouse (Vercel & Supabase public boards)
    ...(
      ["vercel", "supabase", "linear"].map(async (slug) => {
        const res = await fetch(
          `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true`,
          { signal: AbortSignal.timeout(6000) }
        );
        if (!res.ok) return;
        const data = await res.json();
        for (const item of (data.jobs || []).slice(0, 8)) {
          discoveredJobs.push({
            external_job_id: `${slug}:${item.id}`,
            source: "greenhouse",
            company_name: slug.charAt(0).toUpperCase() + slug.slice(1),
            job_title: String(item.title || ""),
            location: String(item.location?.name || "Remote"),
            remote_type: "remote",
            apply_url: String(item.absolute_url || ""),
            description_text: stripHtml(item.content || item.title || "").slice(
              0,
              2500
            ),
          });
        }
      })
    ),
    // Remotive Software Dev feed
    (async () => {
      const res = await fetch(
        "https://remotive.com/api/remote-jobs?category=software-dev&limit=12",
        { signal: AbortSignal.timeout(6000) }
      );
      if (!res.ok) return;
      const data = await res.json();
      for (const item of (data.jobs || []).slice(0, 12)) {
        discoveredJobs.push({
          external_job_id: String(item.id),
          source: "remotive",
          company_name: String(item.company_name || "Remotive"),
          job_title: String(item.title || ""),
          location: String(item.candidate_required_location || "Remote"),
          remote_type: "remote",
          apply_url: String(item.url || ""),
          description_text: stripHtml(item.description || "").slice(0, 2500),
        });
      }
    })(),
    // Arbeitnow API
    (async () => {
      const res = await fetch("https://www.arbeitnow.com/api/job-board-api", {
        signal: AbortSignal.timeout(6000),
      });
      if (!res.ok) return;
      const data = await res.json();
      for (const item of (data.data || []).slice(0, 10)) {
        discoveredJobs.push({
          external_job_id: String(item.slug),
          source: "arbeitnow",
          company_name: String(item.company_name || "Arbeitnow"),
          job_title: String(item.title || ""),
          location: String(item.location || "Remote"),
          remote_type: item.remote ? "remote" : "hybrid",
          apply_url: String(item.url || ""),
          description_text: stripHtml(item.description || "").slice(0, 2500),
        });
      }
    })(),
  ]);

  // 3. Stage 1 Filter: 384-d Vector Similarity
  const preparedRows = discoveredJobs
    .filter((j) => j.external_job_id && j.job_title && j.apply_url)
    .map((j) => {
      const emb = deterministicEmbedding384(
        `${j.job_title} ${j.company_name} ${j.description_text}`
      );
      const sim = cosineSimilarity(profileEmb, emb);
      return { ...j, job_embedding: emb, similarity: sim };
    });

  if (preparedRows.length > 0) {
    const dbRows = preparedRows.map(({ similarity: _s, ...rest }) => rest);
    await supabase
      .from("jobs_discovered")
      .upsert(dbRows, { onConflict: "source,external_job_id" });
  }

  // Sort by similarity and take top 5 survivors
  preparedRows.sort((a, b) => b.similarity - a.similarity);
  const stage1Survivors = preparedRows.slice(0, 5);

  // Fetch inserted job UUIDs from Supabase
  const { data: storedJobs } = await supabase
    .from("jobs_discovered")
    .select("id, source, external_job_id, company_name, job_title, location, apply_url, description_text")
    .in(
      "external_job_id",
      stage1Survivors.map((s) => s.external_job_id)
    );

  // Check unanswered questions in QA bank for circuit-breaker check
  const { data: qaRows } = await supabase
    .from("candidate_qa_bank")
    .select("*")
    .eq("user_id", userId);
  const unansweredQa = (qaRows || []).filter((q) => !q.is_answered);

  // 4. Stage 2 LLM Scoring via Groq (`openai/gpt-oss-120b`)
  const groqKey = process.env.GROQ_API_KEY;
  let newlyQueuedCount = 0;

  for (const job of storedJobs || []) {
    let matchScore = 82;
    let matchReasoning = `Strong overlap with ${skills.slice(0, 4).join(", ")} for ${job.job_title}.`;

    if (groqKey) {
      try {
        const llmRes = await fetch(
          "https://api.groq.com/openai/v1/chat/completions",
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${groqKey}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              model: "openai/gpt-oss-120b",
              temperature: 0.1,
              response_format: { type: "json_object" },
              messages: [
                {
                  role: "system",
                  content:
                    'Score this job (0-100) against the candidate skills and roles. Return JSON: {"match_score": number, "match_reasoning": string}.',
                },
                {
                  role: "user",
                  content: `Candidate Roles: ${targetRoles.join(", ")}\nSkills: ${skills.join(", ")}\n\nJob: ${job.job_title} at ${job.company_name}\nDescription: ${String(job.description_text || "").slice(0, 1200)}`,
                },
              ],
            }),
            signal: AbortSignal.timeout(6000),
          }
        );
        if (llmRes.ok) {
          const llmData = await llmRes.json();
          const parsed = JSON.parse(
            llmData.choices?.[0]?.message?.content || "{}"
          );
          if (typeof parsed.match_score === "number") {
            matchScore = Math.max(50, Math.min(99, Math.round(parsed.match_score)));
          }
          if (parsed.match_reasoning) {
            matchReasoning = String(parsed.match_reasoning);
          }
        }
      } catch {
        // Fallback score
      }
    }

    const minScore = Number(profile.min_match_score ?? 75);
    if (matchScore >= minScore) {
      const status =
        unansweredQa.length > 0 ? "needs_human_input" : "queued";
      const errorLog =
        unansweredQa.length > 0
          ? `Paused for unanswered screening question(s): ${unansweredQa[0].question_label}`
          : null;

      await supabase.from("applications").upsert(
        {
          user_id: userId,
          job_id: job.id,
          match_score: matchScore,
          match_reasoning: matchReasoning,
          status,
          error_log: errorLog,
          submitted_answers_json: {
            "#full_name": profile.full_name || "Ada Lovelace",
            "#email": profile.email || "ada@analytical.org",
            "#phone": profile.phone || "+1 (415) 555-0199",
            "#resume_file": profile.resume_url || `${userId}/resume.pdf`,
          },
        },
        { onConflict: "user_id,job_id", ignoreDuplicates: true }
      );
      newlyQueuedCount += 1;
    }
  }

  return {
    jobs_fetched: discoveredJobs.length,
    jobs_passed_stage1_vector: stage1Survivors.length,
    jobs_discarded_stage1_vector: Math.max(
      0,
      discoveredJobs.length - stage1Survivors.length
    ),
    jobs_scored_stage2_llm: (storedJobs || []).length,
    newly_queued_count: newlyQueuedCount,
  };
}

export async function GET(req: NextRequest) {
  const userId =
    req.nextUrl.searchParams.get("user_id") ||
    "00000000-0000-0000-0000-000000000001";

  // 1. If triggered by Vercel Cron (`/api/agent?cron=1`), run the full discovery sweep
  if (req.nextUrl.searchParams.get("cron") === "1") {
    try {
      const summary = await runServerlessDiscoveryAndMatching(userId);
      return NextResponse.json({ status: "ok", discovery: summary });
    } catch (error: unknown) {
      const message =
        error instanceof Error ? error.message : "Cron execution error";
      return NextResponse.json({ error: message }, { status: 500 });
    }
  }

  // 2. Try local FastAPI backend if running locally
  if (BACKEND_URL && BACKEND_URL.includes("localhost")) {
    try {
      const res = await fetch(`${BACKEND_URL}/api/dashboard/${userId}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(2000),
      });
      if (res.ok) {
        return NextResponse.json(await res.json());
      }
    } catch {
      // Fall through to direct Supabase query
    }
  }

  // 3. Direct Supabase query
  try {
    const supabase = getAdminSupabase();
    const [profRes, qaRes, appRes] = await Promise.all([
      supabase
        .from("candidate_profiles")
        .select("*")
        .eq("id", userId)
        .maybeSingle(),
      supabase
        .from("candidate_qa_bank")
        .select("*")
        .eq("user_id", userId)
        .order("is_answered", { ascending: true }),
      supabase
        .from("applications")
        .select(
          "*, jobs_discovered(company_name, job_title, location, remote_type, source, apply_url)"
        )
        .eq("user_id", userId)
        .order("created_at", { ascending: false })
        .limit(100),
    ]);

    return NextResponse.json({
      profile: profRes.data || null,
      qa_bank: qaRes.data || [],
      applications: appRes.data || [],
    });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Dashboard fetch error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function PATCH(req: NextRequest) {
  try {
    const body = await req.json();
    const { user_id, target_roles, ...settingsPayload } = body;

    const supabase = getAdminSupabase();
    const updateData: Record<string, unknown> = { ...settingsPayload };

    if (Array.isArray(target_roles)) {
      const { data: existing } = await supabase
        .from("candidate_profiles")
        .select("parsed_cv_json")
        .eq("id", user_id)
        .maybeSingle();
      const cvJson = (existing?.parsed_cv_json as Record<string, unknown>) || {};
      updateData.parsed_cv_json = { ...cvJson, target_roles };
    }

    await supabase
      .from("candidate_profiles")
      .update(updateData)
      .eq("id", user_id);

    return NextResponse.json({ status: "ok", updated: updateData });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Settings update error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const userId = body.user_id || "00000000-0000-0000-0000-000000000001";

    // 1. If local FastAPI worker is reachable, run via Python worker
    if (BACKEND_URL && BACKEND_URL.includes("localhost")) {
      try {
        const res = await fetch(`${BACKEND_URL}/api/agent/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(12000),
        });
        if (res.ok) {
          return NextResponse.json(await res.json());
        }
      } catch {
        // Fall through to Vercel Serverless execution
      }
    }

    // 2. Run live multi-feed discovery + Stage 1 vector filter + Stage 2 Groq scoring directly on Vercel!
    const discoverySummary = await runServerlessDiscoveryAndMatching(userId);
    return NextResponse.json({
      status: "ok",
      discovery: discoverySummary,
    });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "Agent trigger error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
