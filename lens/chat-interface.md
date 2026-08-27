# Google Stitch Prompt — InfoLeap LENS Marketing Website

## Brief

Build a **full marketing website** for **LENS**, a consumer intelligence chat platform made by **InfoLeap**. The design must be a **same-to-same recreation of the Claude (Anthropic) website aesthetic** — warm parchment editorial style, dark/light section alternation, serif headlines, organic feel — but with all content, copy, and branding replaced with InfoLeap and LENS details. **Remove all pricing sections entirely.**

---

## 1. Brand & Company Details

| Field | Value |
|-------|-------|
| Product name | **LENS** |
| Company name | **InfoLeap** |
| Tagline | *Consumer Intelligence, in plain language* |
| What it is | An AI-powered consumer research intelligence platform that answers qualitative, quantitative, and mixed research questions in natural language — with cited, evidence-backed answers |
| Target users | Consumer research teams, brand strategists, insights managers, market researchers |
| Contact email | hello@infoleap.ai |
| Domain (mock) | lens.infoleap.ai |

---

## 2. Visual Theme & Atmosphere

Recreate the **Claude (Anthropic) website visual language** exactly:

- **Warm parchment canvas** (`#f5f4ed`) as the primary light background — not white, not cool gray
- **Near-black with olive warmth** (`#141413`) for all dark sections
- **Terracotta brand accent** (`#c96442`) for CTAs, brand moments, and highlights
- **Exclusively warm-toned neutrals** — every gray must have a yellow-brown undertone (no cool blue-grays anywhere)
- **Serif for headlines, sans for UI** — editorial literary feel
- **Organic, editorial** section pacing — generous whitespace, magazine-like rhythm
- **Dark/light section alternation** — the page alternates between Parchment light and Near Black dark sections, like chapters in a book
- No gradients on backgrounds; depth comes from warm tone layering and section alternation

---

## 3. Color Palette

Use **exactly** these tokens:

### Light surfaces
- Page background: `#f5f4ed` (Parchment)
- Card / elevated surface: `#faf9f5` (Ivory)
- Button surface: `#e8e6dc` (Warm Sand)

### Dark surfaces
- Dark section background: `#141413` (Near Black)
- Dark card / container: `#30302e` (Dark Surface)

### Brand
- Primary CTA: `#c96442` (Terracotta)
- Accent / text links on dark: `#d97757` (Coral)

### Text
- Primary text (light): `#141413`
- Secondary text: `#5e5d59` (Olive Gray)
- Tertiary / meta: `#87867f` (Stone Gray)
- Dark surface body text: `#b0aea5` (Warm Silver)
- Dark surface headlines: `#faf9f5` (Ivory)

### Borders
- Light border: `#f0eee6`
- Prominent light border: `#e8e6dc`
- Dark border: `#30302e`

### No-go colors
- ❌ No cool blue-grays
- ❌ No pure white (`#ffffff`) as a page background
- ❌ No saturated colors beyond Terracotta
- ❌ No traditional drop shadows (use ring shadows: `0px 0px 0px 1px`)

---

## 4. Typography

Use **Google Fonts** to substitute the custom Anthropic typefaces:

| Role | Font | Substitute |
|------|------|------------|
| Headlines / display | Anthropic Serif → | **Playfair Display** (weight 500, italic for accent moments) |
| Body / UI / nav | Anthropic Sans → | **Inter** (weights 300–600) |
| Code / mono | Anthropic Mono → | **JetBrains Mono** |

### Rules
- All headline sizes use **weight 500** only — no bold, no light
- Hero display: `clamp(2.6rem, 5vw, 4rem)`, line-height `1.10`
- Section heading: `clamp(2rem, 3.5vw, 3.25rem)`, line-height `1.20`
- Body text: `line-height: 1.60` — notably generous — for a literary reading rhythm
- Small labels: add `letter-spacing: 0.10–0.18em` for readability

---

## 5. Page Sections (in order)

### Section 1 — Navigation
- Sticky top bar, Parchment background with `backdrop-filter: blur`
- Left: **InfoLeap** wordmark (Playfair Display) + badge `LENS Intelligence` in uppercase sans
- Centre: nav links — *What is LENS, How it works, Capabilities, Engines*
- Right: secondary button (Warm Sand) + primary CTA (Terracotta): **"Request access →"**
- On scroll: add soft shadow, slightly more opaque background

### Section 2 — Hero (light / Parchment)
- Small overline pill with a pulsing terracotta dot: *"Consumer Intelligence · Powered by InfoLeap"*
- **Serif headline** (Playfair Display, weight 500): *"Research questions answered in plain language — with evidence."* — put "in plain language" in italic terracotta
- Subtitle (Inter, 1.2rem, Olive Gray): *"LENS is InfoLeap's AI-powered consumer intelligence platform. Ask qualitative, quantitative, or mixed research questions about your data and get cited answers in seconds."*
- Two CTA buttons: **"Request early access →"** (Terracotta) + **"See how it works"** (Dark Near Black)
- Below CTAs: a **mock browser window** (rounded, dark chrome with macOS dots) showing a LENS chat interface screenshot:
  - Dark chat UI (`#0d0f17`)
  - A user bubble asking: *"What are the top pain points consumers have with mixer grinders?"*
  - A LENS assistant response with inline chips `37 interviews` `NPS data` and a formatted answer about noise, jar quality, motor overheating
  - A second user bubble: *"Compare brand NPS scores across cities."*
  - Bubbles animate in one by one with staggered fade

### Section 3 — Trust / Category Bar (Ivory, light)
- Thin band: label *"Built for consumer research teams"* + 5 category tags:
  - Brand Intelligence · Qualitative Analysis · NPS & Satisfaction · Market Comparison · Voice of Consumer

### Section 4 — Features (dark / Near Black)
- Overline: *"What is LENS"*
- Serif heading: *"Research intelligence that speaks your language."*
- Body: *"LENS combines deep qualitative insight with hard quantitative evidence to answer the research questions that matter — no SQL, no pivot tables, no waiting for the analyst."*
- **3-column card grid** (6 cards, 2 rows), each with:
  - Icon in a small warm container
  - Serif card title
  - Body in Warm Silver
  - Hover: soft terracotta border glow
  
  Cards:
  1. 🔍 **Natural Language Queries** — Ask research questions the way you think them. LENS routes, selects tools, and synthesises answers automatically.
  2. 📊 **Quantitative Analytics** — NPS scores, satisfaction ratings, brand comparisons, and segmentation metrics pulled live from your structured data.
  3. 💬 **Qualitative Depth** — Searches hundreds of consumer interview transcripts for verbatim evidence, themes, and sentiment in seconds.
  4. 🔗 **Mixed Evidence Synthesis** — The only platform that unifies quant scores with qual passages into a single, coherent, cited answer.
  5. 🧭 **Intelligent Routing** — An agentic router classifies intent, chooses tools, and decides whether qual retrieval is needed — all automatically.
  6. 📌 **Citations & Traceability** — Every answer is backed by traceable sources — interview IDs, survey variables, and statistic values.

### Section 5 — How It Works (light / Parchment)
- Overline: *"How it works"*
- Serif heading: *"From question to insight in four steps."*
- **4-step horizontal stepper** with numbered circles (terracotta number, Ivory fill), connected by a thin horizontal line:
  1. **Ask in plain English** — Type your research question just as you would ask a senior analyst.
  2. **Intelligent routing** — LENS classifies your query and selects the right analytics tools and retrieval paths.
  3. **Evidence gathering** — Quant data is pulled from your database; qual evidence is retrieved from interview archives with semantic search.
  4. **Synthesised answer** — A language model weaves both streams into a clear, cited, actionable answer with suggested follow-up questions.
- Step circles animate on hover: fill with Terracotta, number turns Ivory

### Section 6 — Capabilities (dark / Near Black)
- **Two-column layout**: text left, visual panel right
- Left:
  - Overline: *"Capabilities"*
  - Serif heading: *"A research analyst that never sleeps."*
  - Body: *"LENS handles the full research workflow — from raw question to boardroom-ready insight — across qualitative depth and quantitative breadth."*
  - **4 capability rows** (icon + bold title + description):
    - 🏷️ **Brand & NPS Analysis** — Net Promoter Scores, satisfaction rankings, and brand reputation compared across competitors, cities, and segments.
    - 🗣️ **Voice-of-Consumer Retrieval** — Semantic search across hundreds of interview transcripts to surface verbatim quotes and recurring themes.
    - 📍 **Geo & Segment Breakdown** — Slice any metric by city, zone, age group, or purchase occasion in natural language.
    - 🔄 **Trend & Comparison Queries** — Compare brands, product categories, or segments side-by-side with evidence from both quant and qual.
- Right: **Dark analytics panel** (monospaced terminal-style, rounded, dark charcoal) showing:
  - Header: pulsing terracotta dot + *"Live analytics — Mixer Grinder Study"*
  - Stats list: Brands tracked · Consumer interviews (37) · Survey respondents (1,200+) · Cities covered · Response latency (< 12s avg)
  - Three progress bars (terracotta fill, animate on scroll):
    - Answer accuracy: 88%
    - Qualitative relevance: 92%
    - Citation traceability: 100%

### Section 7 — Testimonials (light / Parchment)
- Overline: *"From the field"*
- Serif heading: *"What researchers say."*
- **3-column card grid**, each card: Ivory background, border, soft shadow, hover lifts
  - Card 1: *"LENS cut our insight turnaround from two weeks to two hours."* — Ananya R., Senior Research Manager
  - Card 2: *"Finally a platform that doesn't ask me to clean data before I can ask a question."* — Sanjay M., Consumer Insights Lead
  - Card 3: *"The citation trail is what sets it apart. I can take any LENS answer into a client meeting."* — Priya V., Brand Strategy Director

### Section 8 — Intelligence Engines (dark / Near Black)
> **Note: This replaces the Pricing section. Do NOT include any pricing tiers, costs, or subscription plans.**

- Overline: *"Intelligence Engines"*
- Serif heading: *"Powered by the best models, routed intelligently."*
- Body: *"LENS selects the right model for each task — speed for routing, power for synthesis — so you always get the best answer without waiting."*
- **3-column model cards**:
  - **Groq Llama 3.1 8B Instant** — Routing & Classification. Lightning-fast intent classification. 131k context. Sub-second decisions.
  - **Gemini 2.5 Flash** *(Recommended badge)* — Qual Analysis & Synthesis. Best output quality. Handles large evidence blocks. Multimodal ready.
  - **Llama 3.3 70B Versatile** — Deep Synthesis. Higher reasoning quality. Complex multi-hop queries. Groq-accelerated.

### Section 9 — CTA Banner (light / Parchment)
- Serif heading: *"Start asking questions that matter."*
- Body: *"LENS is built for consumer research teams who need answers, not dashboards. Request early access or reach out to the InfoLeap team."*
- Two buttons: **"Request early access →"** (Terracotta) + **"Talk to the team"** (Dark Near Black)

### Section 10 — Footer (dark / Near Black)
- 4-column grid:
  - **Col 1** (wider): InfoLeap · LENS wordmark + description: *"Consumer intelligence powered by AI. We help research teams turn raw qualitative and quantitative data into actionable insight — at the speed of a conversation."*
  - **Col 2**: Platform links — What is LENS, How it works, Capabilities, Engines
  - **Col 3**: Company links — About InfoLeap, Research, Careers, Contact
  - **Col 4**: Legal — Privacy Policy, Terms of Service, Data Security
- Bottom bar: *"© 2026 InfoLeap. All rights reserved."* + Privacy · Terms · hello@infoleap.ai

---

## 6. Component & Animation Rules

### Buttons
- **Terracotta (primary CTA)**: `#c96442` bg, Ivory text, `border-radius: 10px`, ring shadow
- **Warm Sand (secondary)**: `#e8e6dc` bg, `#4d4c48` text, `border-radius: 8px`, ring shadow
- **Dark (tertiary)**: `#141413` bg, `#b0aea5` text, `1px solid #30302e` border, `border-radius: 12px`
- All buttons: hover lifts `translateY(-1px)`, smooth `0.2s` transitions

### Cards
- Light theme: Ivory bg, `1px solid #f0eee6` border, `border-radius: 16px`, whisper shadow `rgba(0,0,0,0.05) 0px 4px 24px`
- Dark theme: `rgba(255,255,255,0.025)` bg, `1px solid #30302e` border; hover: terracotta tint `rgba(201,100,66,0.04)` + terracotta border glow

### Animations
- **Page load**: hero elements fade up sequentially (0.1s → 0.7s stagger), opacity 0 → 1 + translateY(20px → 0)
- **Scroll reveal**: all sections use `IntersectionObserver`; elements `opacity: 0 + translateY(28px)` → visible on enter (threshold 0.12)
- **Stagger delays**: grid items delay `0.1s` per column
- **Chat bubbles**: stagger in with `fadeLeft` / `fadeRight` animations after hero loads
- **Progress bars**: animate width on scroll entry (CSS transition 1.5s ease)
- **Pulse dot**: `opacity 1 → 0.4` loop on the live indicator
- **Nav scroll**: adds `backdrop-filter`, shadow, and more opaque bg after 20px scroll

### Shadows (ring-based, not drop)
- Interactive elements: `0px 0px 0px 1px #d1cfc5`
- Cards hover: `rgba(0,0,0,0.08) 0px 8px 32px`
- No traditional heavy drop shadows

---

## 7. Layout & Spacing

- Max container: `1200px`, centered
- Section padding: `120px 0` (desktop), `80px 0` (tablet), `60px 0` (mobile)
- Grid: 3-column → 2-column (tablet 1024px) → 1-column (mobile 768px)
- Base spacing unit: `8px`

### Responsive breakpoints
| Name | Width | Key change |
|------|-------|------------|
| Desktop | 992px+ | Full layout |
| Tablet | 768–991px | 2-col grids, condensed nav |
| Mobile | < 768px | Stacked, hamburger nav, 36px headings |

---

## 8. What to Exclude (vs. Claude.ai)

- ❌ **No pricing section** — remove entirely
- ❌ No Anthropic / Claude branding, logos, or copy
- ❌ No "Constitutional AI" or Anthropic safety copy
- ❌ No cool blue-grays anywhere in the UI
- ❌ No generic tech/startup illustrations — no icons that feel like SaaS clip art

---

## 9. What to Keep Identical to Claude.ai

- ✅ The overall warm editorial visual atmosphere (parchment, serif, terracotta)
- ✅ Dark/light section alternation rhythm
- ✅ Ring shadow system (no heavy drop shadows)
- ✅ Nav structure and sticky behaviour
- ✅ Hero with mock product screenshot / UI preview
- ✅ Organic, generous whitespace and section pacing
- ✅ Staggered scroll-reveal animations
- ✅ Footer 4-column grid structure
- ✅ All interaction micro-animations (hover lifts, border glows, pulse)
