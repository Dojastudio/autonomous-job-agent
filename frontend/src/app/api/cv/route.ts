import { NextRequest } from "next/server";
import { proxy } from "@/lib/backend";
export const maxDuration = 120;
export const POST = (req: NextRequest) => proxy(req, "/api/cv/upload");
