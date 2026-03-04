import { defineCollection, z } from "astro:content";

const transcriptLine = z.object({
  start: z.number().optional(),
  end: z.number().optional(),
  text: z.string(),
  speaker: z.string().optional(),
  timestamp: z.number().optional(),
});

const segmentSchema = z.object({
  id: z.string(),
  digest: z.string(),
  video_id: z.number(),
  video_url: z.string(),
  start_time: z.number(),
  end_time: z.number(),
  segment_type: z.string(),
  description: z.string(),
  summary: z.string(),
  transcript: z.array(transcriptLine).or(z.array(z.any())),
  comedians: z.array(z.string()),
  updated_at: z.string(),
});

const API_BASE =
  import.meta.env.API_BASE_URL ?? "http://django:8000/api";

const comedians = defineCollection({
  loader: async () => {
    const res = await fetch(`${API_BASE}/comedians`);
    const data: any[] = await res.json();
    return data.map((c) => ({
      id: c.id,
      digest: c.digest,
      name: c.name,
      slug: c.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/(^-|-$)/g, ""),
      segments: c.segments,
      updated_at: c.updated_at,
    }));
  },
  schema: z.object({
    id: z.string(),
    digest: z.string(),
    name: z.string(),
    slug: z.string(),
    segments: z.array(segmentSchema),
    updated_at: z.string(),
  }),
});

export const collections = { comedians };
