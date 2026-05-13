import { Prisma } from "@prisma/client";

import { db } from "@/lib/db";
import { ok } from "@/lib/api-response";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const q = searchParams.get("q")?.trim();
  const platform = searchParams.get("platform")?.trim();
  const tag = searchParams.get("tag")?.trim();

  const where: Prisma.DetectedJobWhereInput = {};
  if (q) {
    where.OR = [
      { title: { contains: q, mode: "insensitive" } },
      { clientName: { contains: q, mode: "insensitive" } },
      { budget: { contains: q, mode: "insensitive" } },
    ];
  }
  if (platform) {
    where.source = { platform: { equals: platform, mode: "insensitive" } };
  }
  if (tag) {
    where.tags = { has: tag };
  }

  const sort = searchParams.get("sort")?.trim();
  const orderBy: Prisma.DetectedJobOrderByWithRelationInput =
    sort === "score"
      ? { aiScore: "desc" }
      : sort === "posted"
        ? { postedAt: "desc" }
        : { detectedAt: "desc" };

  const rows = await db.detectedJob.findMany({
    where,
    include: {
      source: { select: { platform: true, url: true } },
      discordNotifications: { take: 1, orderBy: { sentAt: "desc" } },
      aiAnalysis: true,
    },
    orderBy,
    take: 200,
  });

  const mapped = rows.map((job) => ({
    ...job,
    platform: job.source.platform,
    sourceUrl: job.source.url,
    aiScoreNormalized: job.aiAnalysis?.relevanceScore ?? job.aiScore,
    notificationStatus:
      job.discordNotifications[0]?.status ??
      (job.notificationSent ? ("SENT" as const) : ("PENDING" as const)),
  }));

  return ok(mapped);
}
