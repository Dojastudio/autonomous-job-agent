import { NextRequest } from "next/server";
import { proxy } from "@/lib/backend";
export const GET = (req: NextRequest) => proxy(req, "/api/dashboard");
export const POST = (req: NextRequest) => proxy(req, "/api/agent/run");
export const PATCH = (req: NextRequest) => proxy(req, "/api/profile/settings");
