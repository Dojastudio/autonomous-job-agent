import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";
import zlib from "zlib";
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

/**
 * Decompresses FlateDecode PDF streams in pure Node.js so compressed PDFs
 * extract clean readable resume text on Vercel Serverless without native C++ bindings.
 */
function extractTextFromPdfBuffer(buffer: Buffer): string {
  const chunks: string[] = [];
  const rawLatin1 = buffer.toString("latin1");

  const streamRegex = /stream[\r\n]+([\s\S]*?)[\r\n]+endstream/g;
  let match: RegExpExecArray | null;

  while ((match = streamRegex.exec(rawLatin1)) !== null) {
    const rawStreamBuf = Buffer.from(match[1], "latin1");
    const candidates: Buffer[] = [rawStreamBuf];
    try {
      candidates.push(zlib.inflateSync(rawStreamBuf));
    } catch {
      try {
        candidates.push(zlib.inflateRawSync(rawStreamBuf));
      } catch {
        // Not a zlib stream
      }
    }

    for (const cand of candidates) {
      const str = cand.toString("latin1");
      // Extract parenthesized PDF text strings (...) inside BT..ET blocks
      const textMatches = str.match(/\(([^()\\]*(?:\\.[^()\\]*)*)\)/g);
      if (textMatches && textMatches.length > 2) {
        const cleaned = textMatches
          .map((m) =>
            m
              .slice(1, -1)
              .replace(/\\n/g, " ")
              .replace(/\\r/g, " ")
              .replace(/\\\(/g, "(")
              .replace(/\\\)/g, ")")
              .replace(/\\\\/g, "\\")
          )
          .join(" ")
          .replace(/[^\x20-\x7E\n]/g, " ")
          .replace(/\s+/g, " ")
          .trim();
        if (cleaned.length > 10) {
          chunks.push(cleaned);
        }
      }
    }
  }

  const combined = chunks.join("\n").trim();
  if (combined.length > 80) {
    return combined;
  }

  return rawLatin1
    .replace(/[^\x20-\x7E\n]/g, " ")
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

const DEFAULT_GAP_QUESTIONS = [
  {
    question_key: "work_authorization",
    question_label:
      "Are you legally authorized to work in your target job country without restrictions?",
  },
  {
    question_key: "visa_sponsorship_required",
    question_label:
      "Will you now or in the future require visa sponsorship for employment?",
  },
  {
    question_key: "salary_expectation_usd",
    question_label:
      "What is your minimum annual base salary expectation (in USD)?",
  },
  {
    question_key: "notice_period_days",
    question_label:
      "What is your current notice period or earliest available start date (in days)?",
  },
  {
    question_key: "willing_to_relocate",
    question_label:
      "Are you willing to relocate for a hybrid/on-site role, or do you require 100% remote?",
  },
];

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();

    // 1. Try local FastAPI backend if running locally
    if (BACKEND_URL && BACKEND_URL.includes("localhost")) {
      try {
        const res = await fetch(`${BACKEND_URL}/api/cv/upload`, {
          method: "POST",
          body: formData,
          signal: AbortSignal.timeout(20000),
        });
        if (res.ok) {
          return NextResponse.json(await res.json());
        }
      } catch {
        // Fall through to Vercel Serverless handler
      }
    }

    const userId =
      (formData.get("user_id") as string) ||
      "00000000-0000-0000-0000-000000000001";
    const file = formData.get("file") as File | null;
    if (!file) {
      return NextResponse.json(
        { error: "No PDF file provided" },
        { status: 400 }
      );
    }

    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);
    const supabase = getAdminSupabase();
    const storagePath = `${userId}/resume.pdf`;

    await supabase.storage.from("resumes").upload(storagePath, buffer, {
      contentType: "application/pdf",
      upsert: true,
    });

    // Extract real PDF text using pdf-parse (or zlib fallback)
    let extractedPdfText = "";
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const pdfParse = require("pdf-parse/lib/pdf-parse.js");
      const parsed = await pdfParse(buffer);
      if (parsed?.text && parsed.text.trim().length > 50) {
        extractedPdfText = parsed.text.trim();
      }
    } catch {
      // Fall back to stream extractor
    }
    if (!extractedPdfText) {
      extractedPdfText = extractTextFromPdfBuffer(buffer);
    }

    const { data: existingProfile } = await supabase
      .from("candidate_profiles")
      .select("resume_raw_text, full_name, email, phone, location")
      .eq("id", userId)
      .maybeSingle();

    const usableResumeText =
      extractedPdfText.length > 100
        ? extractedPdfText
        : existingProfile?.resume_raw_text || extractedPdfText;

    // Redact email & phone before sending to Groq
    const emailMatch = usableResumeText.match(
      /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/
    );
    const phoneMatch = usableResumeText.match(
      /(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}/
    );
    const redactedText = usableResumeText
      .replace(emailMatch?.[0] || "__NO_EMAIL__", "{{EMAIL}}")
      .replace(phoneMatch?.[0] || "__NO_PHONE__", "{{PHONE}}");

    let fullName =
      existingProfile?.full_name ||
      file.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ");
    let location = existingProfile?.location || "Remote";
    let parsedCvJson = {
      skills: [] as string[],
      experience_years: 3,
      work_history: [] as Array<{
        company: string;
        role: string;
        duration: string;
      }>,
      education: [] as Array<{
        institution: string;
        degree: string;
        year: string;
      }>,
      target_roles: [] as string[],
    };
    let generatedGaps = DEFAULT_GAP_QUESTIONS;

    const groqKey = process.env.GROQ_API_KEY;
    if (groqKey) {
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
                content: `You are an accurate CV parser. Extract ONLY the real skills, experience, and roles actually present in the resume text. NEVER invent software engineering skills if the candidate is in Product Management, Design, Operations, or another field.
Return strict JSON with keys:
- "full_name": string (candidate's full name if visible in text, else "")
- "location": string (city, country if visible, else "Remote")
- "skills": string[] (8 to 14 real skills from the resume)
- "experience_years": integer
- "target_roles": string[] (2 to 4 fitting job titles for this candidate)
- "gap_questions": array of 4 to 6 objects with {"question_key": snake_case_str, "question_label": string}`,
              },
              {
                role: "user",
                content: `Resume Filename: ${file.name}\n\nResume Text:\n${redactedText.slice(0, 5000)}`,
              },
            ],
          }),
        }
      );

      if (llmRes.ok) {
        const llmData = await llmRes.json();
        const parsed = JSON.parse(
          llmData.choices?.[0]?.message?.content || "{}"
        );
        if (parsed.full_name && !parsed.full_name.includes("{{")) {
          fullName = String(parsed.full_name).trim();
        }
        if (parsed.location) {
          location = String(parsed.location).trim();
        }
        if (Array.isArray(parsed.skills) && parsed.skills.length > 0) {
          parsedCvJson.skills = parsed.skills.map(String);
        }
        if (typeof parsed.experience_years === "number") {
          parsedCvJson.experience_years = parsed.experience_years;
        }
        if (
          Array.isArray(parsed.target_roles) &&
          parsed.target_roles.length > 0
        ) {
          parsedCvJson.target_roles = parsed.target_roles.map(String);
        }
        if (
          Array.isArray(parsed.gap_questions) &&
          parsed.gap_questions.length >= 3
        ) {
          generatedGaps = parsed.gap_questions;
        }
      }
    }

    const embedding = deterministicEmbedding384(
      `${parsedCvJson.target_roles.join(" ")} ${parsedCvJson.skills.join(" ")} ${usableResumeText.slice(0, 2000)}`
    );

    await supabase.from("candidate_profiles").upsert({
      id: userId,
      full_name: fullName,
      email: emailMatch?.[0] || existingProfile?.email || null,
      phone: phoneMatch?.[0] || existingProfile?.phone || null,
      location,
      resume_url: storagePath,
      resume_raw_text: usableResumeText,
      parsed_cv_json: parsedCvJson,
      profile_embedding: embedding,
    });

    const qaRows = generatedGaps.slice(0, 6).map((q) => ({
      user_id: userId,
      question_key: q.question_key,
      question_label: q.question_label,
      answer_value: "",
      is_answered: false,
    }));

    await supabase.from("candidate_qa_bank").upsert(qaRows, {
      onConflict: "user_id,question_key",
      ignoreDuplicates: true,
    });

    const { data: liveQa } = await supabase
      .from("candidate_qa_bank")
      .select("*")
      .eq("user_id", userId)
      .order("is_answered", { ascending: true });

    return NextResponse.json({
      status: "ok",
      user_id: userId,
      full_name: fullName,
      email: emailMatch?.[0] || existingProfile?.email || null,
      phone: phoneMatch?.[0] || existingProfile?.phone || null,
      location,
      parsed_cv_json: parsedCvJson,
      gap_questions: liveQa || qaRows,
    });
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : "CV upload proxy error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
