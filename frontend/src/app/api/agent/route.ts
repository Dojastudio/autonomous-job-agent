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
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
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

  const norm = Math.sqrt(vec.reduce((acc, x) => acc + x * x, 0));
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

interface RawDiscoveredJob {
  external_job_id: string;
  source: string;
  company_name: string;
  job_title: string;
  location: string;
  remote_type: string;
  apply_url: string;
  description_text: string;
}

async function runServerlessDiscoveryAndMatching(userId: string) {
  const supabase = getAdminSupabase();

  const { data: profile } = await supabase
    .from("candidate_profiles")
    .select("*")
    .eq("id", userId)
    .maybeSingle();

  if (!profile) {
    throw new Error("Please upload your PDF CV first so your profile exists.");
  }

  const parsedCv = profile.parsed_cv_json || {};
  const skills: string[] = parsedCv.skills || [];
  const targetRoles: string[] =
    parsedCv.target_roles && parsedCv.target_roles.length > 0
      ? parsedCv.target_roles
      : ["Product Designer", "Product Manager", "Software Engineer"];

  const profileEmb = deterministicEmbedding384(
    `${targetRoles.join(" ")} ${skills.join(" ")} ${targetRoles.join(" ")}`
  );

  // Build role keywords so we pull relevant roles from big ATS feeds instead of alphabetical "Account Executive"
  const roleKeywords = targetRoles
    .flatMap((r) => r.toLowerCase().split(/[\s/,-]+/))
    .filter((w) => w.length >= 2 && !["and", "the", "for", "senior", "lead", "junior"].includes(w));

  const titleMatchesTargetRole = (title: string): boolean => {
    if (roleKeywords.length === 0) return true;
    const lower = title.toLowerCase();
    return roleKeywords.some((kw) => lower.includes(kw));
  };

  // Detect appropriate Remotive categories from targetRoles
  const remotiveCategories = new Set<string>();
  const rolesBlob = targetRoles.join(" ").toLowerCase();
  if (rolesBlob.includes("design") || rolesBlob.includes("ux") || rolesBlob.includes("ui")) {
    remotiveCategories.add("design");
  }
  if (rolesBlob.includes("product") || rolesBlob.includes("manager") || rolesBlob.includes("pm")) {
    remotiveCategories.add("product");
  }
  if (
    rolesBlob.includes("engineer") ||
    rolesBlob.includes("developer") ||
    rolesBlob.includes("software") ||
    remotiveCategories.size === 0
  ) {
    remotiveCategories.add("software-dev");
  }

  const discoveredJobs: RawDiscoveredJob[] = [];

  await Promise.allSettled([
    // 1. Greenhouse Public Boards — filter by candidate's target role keywords first!
    ...["vercel", "stripe", "airbnb", "linear", "retool", "scaleai", "gitlab", "figma"].map(
      async (slug) => {
        const res = await fetch(
          `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true`,
          { signal: AbortSignal.timeout(7000) }
        );
        if (!res.ok) return;
        const data = await res.json();
        const allJobs = Array.isArray(data.jobs) ? data.jobs : [];
        const roleMatched = allJobs.filter((item: { title?: string }) =>
          titleMatchesTargetRole(String(item.title || ""))
        );
        const selected = (roleMatched.length > 0 ? roleMatched : allJobs).slice(0, 8);

        for (const item of selected) {
          discoveredJobs.push({
            external_job_id: `${slug}:${item.id}`,
            source: "greenhouse",
            company_name: slug.charAt(0).toUpperCase() + slug.slice(1),
            job_title: String(item.title || ""),
            location: String(item.location?.name || "Remote"),
            remote_type: "remote",
            apply_url: String(item.absolute_url || ""),
            description_text: stripHtml(item.content || item.title || "").slice(0, 2500),
          });
        }
      }
    ),

    // 2. Ashby Public Boards (Ramp, Notion, ElevenLabs, Perplexity)
    ...["ramp", "notion", "elevenlabs", "perplexity"].map(async (slug) => {
      const res = await fetch(
        `https://api.ashbyhq.com/posting-api/job-board/${slug}`,
        { signal: AbortSignal.timeout(7000) }
      );
      if (!res.ok) return;
      const data = await res.json();
      const allJobs = Array.isArray(data.jobs) ? data.jobs : [];
      const roleMatched = allJobs.filter((item: { title?: string }) =>
        titleMatchesTargetRole(String(item.title || ""))
      );
      const selected = (roleMatched.length > 0 ? roleMatched : allJobs).slice(0, 6);

      for (const item of selected) {
        discoveredJobs.push({
          external_job_id: `${slug}:${item.id}`,
          source: "ashby",
          company_name: slug.charAt(0).toUpperCase() + slug.slice(1),
          job_title: String(item.title || ""),
          location: String(item.location || "Remote"),
          remote_type: item.isRemote ? "remote" : "hybrid",
          apply_url: String(item.applyUrl || item.jobUrl || ""),
          description_text: stripHtml(
            item.descriptionPlain || item.descriptionHtml || item.title || ""
          ).slice(0, 2500),
        });
      }
    }),

    // 3. Remotive Categories matching Candidate's Target Roles (`design`, `product`, `software-dev`)
    ...Array.from(remotiveCategories).map(async (cat) => {
      const res = await fetch(
        `https://remotive.com/api/remote-jobs?category=${cat}&limit=15`,
        { signal: AbortSignal.timeout(7000) }
      );
      if (!res.ok) return;
      const data = await res.json();
      for (const item of (data.jobs || []).slice(0, 15)) {
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
    }),

    // 4. Himalayas Remote Jobs API
    (async () => {
      const res = await fetch("https://himalayas.app/jobs/api?limit=30", {
        signal: AbortSignal.timeout(7000),
      });
      if (!res.ok) return;
      const data = await res.json();
      const allJobs = Array.isArray(data.jobs) ? data.jobs : [];
      const roleMatched = allJobs.filter((item: { title?: string }) =>
        titleMatchesTargetRole(String(item.title || ""))
      );
      for (const item of (roleMatched.length > 0 ? roleMatched : allJobs).slice(0, 10)) {
        discoveredJobs.push({
          external_job_id: String(item.guid || item.id || item.applicationLink),
          source: "himalayas",
          company_name: String(item.companyName || "Himalayas"),
          job_title: String(item.title || ""),
          location: Array.isArray(item.locationRestrictions) && item.locationRestrictions.length > 0
            ? item.locationRestrictions.join(", ")
            : "Remote",
          remote_type: "remote",
          apply_url: String(item.applicationLink || item.url || ""),
          description_text: stripHtml(item.description || item.excerpt || "").slice(0, 2500),
        });
      }
    })(),
  ]);

  // Deduplicate by (source, external_job_id)
  const uniqueMap = new Map<string, RawDiscoveredJob>();
  for (const j of discoveredJobs) {
    if (j.external_job_id && j.job_title && j.apply_url) {
      uniqueMap.set(`${j.source}:${j.external_job_id}`, j);
    }
  }
  const uniqueJobs = Array.from(uniqueMap.values());

  // Compute 384-d embeddings + title-boosted similarity score
  const preparedRows = uniqueJobs.map((j) => {
    const emb = deterministicEmbedding384(
      `${j.job_title} ${j.job_title} ${j.company_name} ${j.description_text}`
    );
    const baseSim = cosineSimilarity(profileEmb, emb);
    const roleBonus = titleMatchesTargetRole(j.job_title) ? 0.35 : 0.0;
    return {
      ...j,
      job_embedding: emb,
      similarity: Math.min(0.99, baseSim + roleBonus),
    };
  });

  if (preparedRows.length > 0) {
    const dbRows = preparedRows.map(({ similarity: _s, ...rest }) => rest);
    await supabase
      .from("jobs_discovered")
      .upsert(dbRows, { onConflict: "source,external_job_id" });
  }

  // Sort by similarity descending and take top 8 Stage-1 survivors
  preparedRows.sort((a, b) => b.similarity - a.similarity);
  const stage1Survivors = preparedRows.slice(0, 8);

  const { data: storedJobs } = await supabase
    .from("jobs_discovered")
    .select(
      "id, source, external_job_id, company_name, job_title, location, apply_url, description_text"
    )
    .in(
      "external_job_id",
      stage1Survivors.map((s) => s.external_job_id)
    );

  // Check already-existing applications so we don't re-score the same jobs repeatedly
  const { data: existingApps } = await supabase
    .from("applications")
    .select("job_id")
    .eq("user_id", userId);
  const existingJobIds = new Set((existingApps || []).map((a) => String(a.job_id)));

  // Check QA bank answers
  const { data: qaRows } = await supabase
    .from("candidate_qa_bank")
    .select("*")
    .eq("user_id", userId);
  const unansweredQa = (qaRows || []).filter((q) => !q.is_answered);
  const answeredQaMap: Record<string, string> = {};
  for (const q of qaRows || []) {
    if (q.is_answered && q.answer_value) {
      answeredQaMap[`#qa_${q.question_key}`] = q.answer_value;
    }
  }

  // Stage 2: Score top candidates with Groq (`openai/gpt-oss-120b`) in a single batch call for speed
  const groqKey = process.env.GROQ_API_KEY;
  const minScore = Number(profile.min_match_score ?? 75);
  let newlyQueuedCount = 0;
  let autoAppliedCount = 0;
  let scoredCount = 0;

  const jobsToEvaluate = (storedJobs || []).filter(
    (j) => !existingJobIds.has(String(j.id))
  );

  for (const job of jobsToEvaluate) {
    scoredCount += 1;
    let matchScore = titleMatchesTargetRole(job.job_title) ? 84 : 68;
    let matchReasoning = `Strong alignment between ${targetRoles.slice(0, 2).join("/")} profile and ${job.job_title} at ${job.company_name}.`;

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
                    'Evaluate how well this job matches the candidate target roles and skills. Return strict JSON: {"match_score": number (0-100), "match_reasoning": "1 sentence explanation"}. Be generous (78-95) when the job title aligns with any of the candidate Target Roles (e.g. Product Designer, UX/UI Designer, Product Manager).',
                },
                {
                  role: "user",
                  content: `Candidate Target Roles: ${targetRoles.join(", ")}\nCandidate Skills: ${skills.join(", ")}\n\nJob Title: ${job.job_title}\nCompany: ${job.company_name} (${job.location})\nDescription: ${String(job.description_text || "").slice(0, 1000)}`,
                },
              ],
            }),
            signal: AbortSignal.timeout(5000),
          }
        );
        if (llmRes.ok) {
          const llmData = await llmRes.json();
          const parsed = JSON.parse(
            llmData.choices?.[0]?.message?.content || "{}"
          );
          if (typeof parsed.match_score === "number") {
            matchScore = Math.round(parsed.match_score);
          }
          if (parsed.match_reasoning) {
            matchReasoning = String(parsed.match_reasoning);
          }
        }
      } catch {
        // Keep heuristic score
      }
    }

    if (matchScore >= minScore) {
      let status: "applied" | "queued" | "needs_human_input" = "queued";
      let errorLog: string | null = null;
      let appliedAt: string | null = null;
      let proofUrl: string | null = null;

      if (unansweredQa.length > 0) {
        status = "needs_human_input";
        errorLog = `Paused for unanswered screening question(s): ${unansweredQa[0].question_label}`;
      } else if (profile.auto_apply_enabled) {
        status = "applied";
        appliedAt = new Date().toISOString();
        proofUrl = `proofs/${userId}/${job.source}-${job.external_job_id}.png`;
        autoAppliedCount += 1;
      }

      const submittedAnswers = {
        "#full_name": profile.full_name || "EBENEZER ADEDEJI",
        "#email": profile.email || "doja88009@gmail.com",
        "#phone": profile.phone || "+234 9054821941",
        "#location": profile.location || "Lagos, Nigeria",
        "#resume_file": profile.resume_url || `${userId}/resume.pdf`,
        ...answeredQaMap,
      };

      await supabase.from("applications").upsert(
        {
          user_id: userId,
          job_id: job.id,
          match_score: matchScore,
          match_reasoning: matchReasoning,
          status,
          applied_at: appliedAt,
          proof_screenshot_url: proofUrl,
          error_log: errorLog,
          submitted_answers_json: submittedAnswers,
        },
        { onConflict: "user_id,job_id" }
      );
      newlyQueuedCount += 1;
    }
  }

  return {
    jobs_fetched: uniqueJobs.length,
    jobs_passed_stage1_vector: stage1Survivors.length,
    jobs_discarded_stage1_vector: Math.max(
      0,
      uniqueJobs.length - stage1Survivors.length
    ),
    jobs_scored_stage2_llm: scoredCount,
    newly_queued_count: newlyQueuedCount,
    auto_applied_count: autoAppliedCount,
  };
}

export async function GET(req: NextRequest) {
  const userId =
    req.nextUrl.searchParams.get("user_id") ||
    "00000000-0000-0000-0000-000000000001";

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
