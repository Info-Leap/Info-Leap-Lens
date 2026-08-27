import "dotenv/config";
import express from "express";
import { createServer as createViteServer } from "vite";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Python OxData API — runs on port 8000
const OXDATA_API = process.env.OXDATA_API_URL || "http://localhost:8000";

async function startServer() {
  const app = express();
  const PORT = 3000;

  app.use(express.json());

  // Health check — pings both frontend and Python API
  app.get("/api/health", async (_req, res) => {
    try {
      const upstream = await fetch(`${OXDATA_API}/api/health`);
      const data = await upstream.json();
      res.json({ frontend: "ok", python_api: data });
    } catch (e: any) {
      res.status(503).json({ frontend: "ok", python_api: "unreachable", error: e.message });
    }
  });

  // Main chat — proxy straight through to Python OxData API
  app.post("/api/chat", async (req, res) => {
    try {
      const upstream = await fetch(`${OXDATA_API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });

      if (!upstream.ok) {
        const err = await upstream.json().catch(() => ({ error: "Python API error" }));
        return res.status(upstream.status).json(err);
      }

      const data = await upstream.json();
      // Frontend expects { text: "..." } — Python returns { text, skill, sql, row_count, ... }
      res.json(data);
    } catch (e: any) {
      console.error("[proxy] Python API unreachable:", e.message);
      res.status(503).json({
        error: `OxData API unreachable. Start it first:\n\n  python api.py`,
      });
    }
  });

  // Schema info — proxy to Python
  app.get("/api/schema", async (_req, res) => {
    try {
      const upstream = await fetch(`${OXDATA_API}/api/schema`);
      const data = await upstream.json();
      res.json(data);
    } catch (e: any) {
      res.status(503).json({ error: e.message });
    }
  });

  // Query history — proxy to Python
  app.get("/api/history", async (req, res) => {
    try {
      const limit = req.query.limit || "50";
      const upstream = await fetch(`${OXDATA_API}/api/history?limit=${limit}`);
      const data = await upstream.json();
      res.json(data);
    } catch (e: any) {
      res.status(503).json({ rows: [], error: e.message });
    }
  });

  // Vite dev middleware
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*", (_req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`\n🌐  Frontend:   http://localhost:${PORT}`);
    console.log(`🐍  Python API: ${OXDATA_API}\n`);
  });
}

startServer();
