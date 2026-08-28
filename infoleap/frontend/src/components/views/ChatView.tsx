import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowUp, Copy, Check, ChevronDown, ChevronUp,
  Database, Brain, AlertTriangle, BarChart2, Clock,
  ThumbsUp, ThumbsDown, RotateCcw
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../../lib/utils";
import { 
  type Message, sendMessageToBackend, sendFeedback, 
  SKILL_META, getCurrentSessionId, newSession 
} from "../../lib/gemini";
import { ChartRenderer, ChartFromSpec } from "../ChartRenderer";
import { Typewriter } from "../ui/typewriter-text";
import { PromptInputBox } from "../ui/ai-prompt-box";

interface ChatViewProps {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  isInitializing: boolean;
}

export function ChatView({ messages, setMessages, isInitializing }: ChatViewProps) {
  const [isTyping, setIsTyping] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const handleFeedback = async (index: number, rating: number) => {
    try {
      await sendFeedback(index, rating);
      // Update local state to show feedback recorded
      const newMessages = [...messages];
      if (newMessages[index]) {
        newMessages[index] = { ...newMessages[index], rating };
        setMessages(newMessages);
      }
    } catch (e) {
      console.error("Feedback failed", e);
    }
  };

  const handleRegenerate = async (index: number) => {
    const userPrompt = messages[index - 1]?.content;
    if (!userPrompt || isTyping) return;

    const truncatedHistory = messages.slice(0, index);
    setMessages(truncatedHistory);
    setIsTyping(true);

    try {
      const res = await sendMessageToBackend(truncatedHistory);
      const modelMsg: Message = {
        role: "model",
        content: res.text,
        skill: res.skill,
        sql: res.sql,
        reasoning: res.reasoning,
        synthesis: res.synthesis,
        row_count: res.row_count,
        complexity: res.complexity,
        plan_reasoning: res.plan_reasoning,
        plan_steps: res.plan_steps,
        validation_ok: res.validation_ok,
        validation_warnings: res.validation_warnings,
        chart: res.chart,
        error: res.error,
        latency_ms: res.latency_ms,
      };
      setMessages([...truncatedHistory, modelMsg]);
    } catch (error: any) {
      setMessages([...truncatedHistory, {
        role: "model",
        content: `**Error:** ${error.message || "Backend unreachable."}`,
        error: error.message,
      }]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleSend = async (content: string, files?: File[]) => {
    if (!content.trim() || isTyping) return;

    const userMessage = content.trim();
    const newMessages: Message[] = [...messages, { role: "user", content: userMessage }];
    setMessages(newMessages);
    setIsTyping(true);

    try {
      const res = await sendMessageToBackend(newMessages);
      const modelMsg: Message = {
        role: "model",
        content: res.text,
        skill: res.skill,
        sql: res.sql,
        reasoning: res.reasoning,
        synthesis: res.synthesis,
        row_count: res.row_count,
        complexity: res.complexity,
        plan_reasoning: res.plan_reasoning,
        plan_steps: res.plan_steps,
        validation_ok: res.validation_ok,
        validation_warnings: res.validation_warnings,
        chart: res.chart,
        error: res.error,
        latency_ms: res.latency_ms,
      };
      setMessages([...newMessages, modelMsg]);
    } catch (error: any) {
      setMessages([...newMessages, {
        role: "model",
        content: `**Error:** ${error.message || "Backend unreachable."}`,
        error: error.message,
      }]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full relative w-full items-center">
      <header className="h-16 flex-shrink-0 w-full" />

      <div className="flex-1 overflow-y-auto px-4 md:px-16 pb-48 w-full max-w-5xl flex flex-col gap-8">
        {messages.length === 0 ? (
          <EmptyState isInitializing={isInitializing} onPrompt={p => handleSend(p)} />
        ) : (
          <div className="flex flex-col gap-6 w-full">
            <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }}
              className="text-center pt-4 pb-6">
              <h1 className="font-headline text-2xl font-medium text-primary mb-1">
                <Typewriter text="OxData Research Intelligence" speed={70} />
              </h1>
              <p className="text-xs text-secondary/60">OX Wave 1 · 6,631 respondents · 18 cities · 4 zones</p>
              <div className="flex items-center justify-center gap-4 mt-2">
                <span className="text-xs px-2 py-0.5 bg-surface-variant rounded-full text-secondary">
                  Session: <span className="font-mono">{getCurrentSessionId()}</span>
                </span>
                <button type="button" onClick={() => { newSession(); setMessages([]); }}
                  className="text-xs text-secondary hover:text-primary underline">
                  New Session
                </button>
              </div>
              <div className="w-8 h-0.5 bg-outline-variant mx-auto mt-2 rounded-full" />
            </motion.div>

            <AnimatePresence initial={false}>
              {messages.map((msg, i) => (
                <ChatMessage 
                  key={`msg-${i}`} 
                  message={msg} 
                  index={i} 
                  onFeedback={(r) => handleFeedback(i, r)}
                  onRegenerate={() => handleRegenerate(i)}
                />
              ))}
              {isTyping && <ThinkingIndicator key="thinking" />}
            </AnimatePresence>
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="absolute bottom-0 left-0 w-full px-4 md:px-16 pb-8 pt-6 bg-gradient-to-t from-surface-container-low via-surface-container-low to-transparent">
        <div className="max-w-4xl mx-auto w-full">
          <PromptInputBox 
            onSend={handleSend} 
            isLoading={isTyping} 
            placeholder="Ask about brands, NPS, awareness, appliances…"
          />
          <div className="text-center mt-3">
            <p className="font-body text-[11px] text-secondary/60">
              OxData · OX Wave 1 · 6,631 respondents · 18 cities
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Message renderer ──────────────────────────────────────────────────────────

function ChatMessage({ message, index, onFeedback, onRegenerate }: { 
  message: Message; 
  index: number;
  onFeedback: (rating: number) => void;
  onRegenerate: () => void;
}) {
  const isAi = message.role === "model";
  const [revealStage, setRevealStage] = useState(isAi ? 0 : 5); 
  // Stages: 0:Planning, 1:SQL, 2:Results, 3:Synthesis, 4:Answer, 5:Complete

  // Automatically advance stages if content is missing
  useEffect(() => {
    if (!isAi) return;
    if (revealStage === 0 && !message.reasoning) setRevealStage(1);
    if (revealStage === 1 && !message.sql) setRevealStage(2);
    if (revealStage === 2 && !message.chart && !message.row_count) setRevealStage(3);
    if (revealStage === 3 && !message.synthesis) setRevealStage(4);
  }, [revealStage, message, isAi]);

  if (!isAi) {
    return (
      <motion.div 
        initial={{ opacity: 0, y: 20, scale: 0.95 }} 
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        layout
        className="flex w-full justify-end"
      >
        <div className="max-w-[80%] bg-surface-container rounded-2xl rounded-tr-sm p-4 shadow-sm border border-outline-variant/10">
          <p className="font-body text-on-surface leading-relaxed whitespace-pre-wrap">{message.content}</p>
        </div>
      </motion.div>
    );
  }

  const skillMeta = message.skill ? SKILL_META[message.skill.split(",")[0].trim()] : null;

  const STAGE_LABELS: Record<number, string> = {
    0: "Formulating Analysis Plan",
    1: "Generating Verified SQL",
    2: "Executing & Fetching Data",
    3: "Cross-checking Results",
    4: "Finalizing Insights",
    5: "Complete"
  };

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20, scale: 0.95 }} 
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      layout
      className="flex w-full justify-start"
    >
      <div className="flex flex-col gap-3 w-full max-w-[95%]">

        {/* Action Badge */}
        <div className="flex flex-wrap items-center gap-3">
          <AnimatePresence mode="wait">
            {revealStage < 5 && (
              <motion.div 
                key="status-badge"
                initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.8 }}
                className="inline-flex items-center gap-2 px-2 py-0.5 rounded-full bg-primary/5 border border-primary/10"
              >
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                <span className="text-[10px] font-bold text-primary/60 uppercase tracking-widest">{STAGE_LABELS[revealStage]}</span>
              </motion.div>
            )}
          </AnimatePresence>

          {skillMeta && (
            <span className={cn("inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full", skillMeta.color)}>
              {skillMeta.icon} {skillMeta.label}
            </span>
          )}
          {message.latency_ms !== undefined && message.latency_ms > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-secondary/60">
              <Clock className="w-3 h-3" /> {(message.latency_ms / 1000).toFixed(1)}s
            </span>
          )}
        </div>

        {/* 1. Planning Thought */}
        {message.reasoning && (
          <motion.div 
            initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
            className="mb-2 bg-surface-variant/5 rounded-2xl p-4 border border-outline-variant/10 relative overflow-hidden group"
          >
            <div className="flex items-center gap-2 mb-3">
              <Brain className="w-4 h-4 text-primary/40" />
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-primary/40">Thinking Process</span>
            </div>
            <div className="text-[13px] leading-relaxed text-secondary/70 italic font-body whitespace-pre-wrap">
               <Typewriter 
                 text={message.reasoning} 
                 speed={2} 
                 cursor="" 
                 onComplete={() => setRevealStage(1)} 
               />
            </div>
            {revealStage === 0 && (
              <button onClick={() => setRevealStage(1)} className="absolute bottom-2 right-4 text-[10px] text-primary/40 hover:text-primary transition-colors font-bold uppercase tracking-tighter">
                Skip →
              </button>
            )}
          </motion.div>
        )}

        {/* 2. SQL Trace */}
        {message.sql && revealStage >= 1 && (
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} onAnimationComplete={() => { if(revealStage === 1) setTimeout(() => setRevealStage(2), 800) }}>
            <Collapsible 
              icon={<Database className="w-3.5 h-3.5 text-primary/60" />} 
              label="SQL Query Trace"
            >
              <div className="flex flex-col gap-2">
                <CodeBlock language="sql" value={message.sql} />
              </div>
            </Collapsible>
          </motion.div>
        )}

        {/* 3. Results / Data */}
        {revealStage >= 2 && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} onAnimationComplete={() => { if(revealStage === 2) setTimeout(() => setRevealStage(3), 800) }}>
            {message.chart && <ChartFromSpec spec={message.chart} />}
            {message.row_count !== undefined && message.row_count > 0 && (
              <p className="text-[10px] text-secondary/40 font-mono mt-1 px-1">Successfully fetched {message.row_count} rows from DB.</p>
            )}
          </motion.div>
        )}

        {/* 4. Synthesis (Cross-check) */}
        {message.synthesis && revealStage >= 3 && (
          <motion.div 
            initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
            className="mb-4 bg-primary/[0.02] rounded-2xl p-4 border border-primary/10 relative"
          >
            <div className="flex items-center gap-2 mb-3">
              <Check className="w-4 h-4 text-green-500/50" />
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-secondary/40">Data Validation & Cross-check</span>
            </div>
            <div className="text-[13px] leading-relaxed text-secondary/80 font-body">
               <Typewriter 
                 text={message.synthesis} 
                 speed={2} 
                 cursor="" 
                 onComplete={() => setRevealStage(4)} 
               />
            </div>
          </motion.div>
        )}

        {/* 5. Final Answer */}
        {revealStage >= 4 && (
          <motion.div 
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
            className="prose prose-sm max-w-none prose-stone prose-p:leading-relaxed prose-table:text-xs"
            onAnimationComplete={() => setRevealStage(5)}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code({ node, inline, className, children, ...props }: any) {
                  const match = /language-(\w+)/.exec(className || '');
                  if (!inline && match && match[1] === 'chart') {
                    return <ChartRenderer content={String(children)} />;
                  }
                  if (!inline && match) {
                    return <CodeBlock language={match[1]} value={String(children).replace(/\n$/, '')} />;
                  }
                  return (
                    <code className="bg-surface-variant text-primary font-mono text-[0.85em] px-1.5 py-0.5 rounded-md" {...props}>
                      {children}
                    </code>
                  );
                },
                table({ children }) {
                  return (
                    <div className="overflow-x-auto my-3 rounded-xl border border-outline-variant/30 shadow-sm">
                      <table className="w-full text-xs border-collapse">{children}</table>
                    </div>
                  );
                },
                thead({ children }) {
                  return <thead className="bg-surface-variant/50 text-secondary font-semibold">{children}</thead>;
                },
                th({ children }) {
                  return <th className="px-3 py-2 text-left font-semibold border-b border-outline-variant/20 whitespace-nowrap">{children}</th>;
                },
                td({ children }) {
                  return <td className="px-3 py-2 border-b border-outline-variant/10 text-on-surface/80">{children}</td>;
                },
                tr({ children }) {
                  return <tr className="hover:bg-surface-variant/20 transition-colors">{children}</tr>;
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
          </motion.div>
        )}

        {/* Error */}
        {message.error && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-xl px-3 py-2">
            ⚠️ {message.error}
          </div>
        )}

        {/* Action Bar (Agentic Feedback) */}
        {isAi && revealStage >= 4 && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex items-center gap-4 mt-2 px-1">
            <div className="flex items-center gap-1 border-r border-outline-variant/20 pr-4">
              <button 
                onClick={() => onFeedback(1)}
                className={cn(
                  "p-1.5 rounded-lg hover:bg-surface-variant transition-colors",
                  message.rating === 1 ? "text-green-600 bg-green-50 shadow-sm" : "text-secondary/40 hover:text-green-600"
                )}
                title="Helpful"
              >
                <ThumbsUp className="w-3.5 h-3.5" />
              </button>
              <button 
                onClick={() => onFeedback(-1)}
                className={cn(
                  "p-1.5 rounded-lg hover:bg-surface-variant transition-colors",
                  message.rating === -1 ? "text-red-600 bg-red-50 shadow-sm" : "text-secondary/40 hover:text-red-600"
                )}
                title="Not Helpful"
              >
                <ThumbsDown className="w-3.5 h-3.5" />
              </button>
            </div>
            <button 
              onClick={onRegenerate}
              className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg hover:bg-surface-variant text-secondary/60 hover:text-primary transition-colors text-[11px] font-medium"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Regenerate
            </button>
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

// ── Collapsible ───────────────────────────────────────────────────────────────

function Collapsible({ label, icon, children, variant = "default" }: {
  label: string; icon: React.ReactNode; children: React.ReactNode; variant?: "default" | "thought";
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cn(
      "border rounded-xl overflow-hidden text-xs transition-all",
      variant === "thought" ? "border-primary/20 bg-surface-lowest shadow-sm" : "border-outline-variant/30"
    )}>
      <button onClick={() => setOpen(o => !o)}
        className={cn(
          "w-full flex items-center justify-between px-3 py-2 transition-colors font-medium",
          variant === "thought" ? "bg-primary/5 hover:bg-primary/10 text-primary" : "bg-surface-variant/40 hover:bg-surface-variant/70 text-secondary"
        )}>
        <span className="flex items-center gap-1.5">{icon}{label}</span>
        {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }}
            className="overflow-hidden">
            <div className={cn("p-3", variant === "thought" ? "bg-white/50" : "bg-inverse-surface/5")}>
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Code block ────────────────────────────────────────────────────────────────

function CodeBlock({ language, value }: { language: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="rounded-xl overflow-hidden border border-outline-variant/20 bg-inverse-surface">
      <div className="bg-white/5 px-3 py-1.5 flex justify-between items-center border-b border-white/10">
        <span className="font-mono text-xs text-inverse-on-surface/60 lowercase">{language}</span>
        <button onClick={handleCopy}
          className="text-inverse-on-surface/60 hover:text-inverse-on-surface text-xs flex items-center gap-1 bg-white/5 hover:bg-white/10 px-2 py-0.5 rounded-md transition-colors">
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="p-3 overflow-x-auto max-h-64">
        <pre className="!bg-transparent !p-0 !m-0">
          <code className="font-mono text-[12px] leading-relaxed text-inverse-on-surface/85 whitespace-pre">
            {value}
          </code>
        </pre>
      </div>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ isInitializing, onPrompt }: { isInitializing: boolean; onPrompt: (p: string) => void }) {
  const promptCategories = [
    {
      category: "Brand Awareness",
      icon: "📢",
      prompts: [
        "Which brand has highest TOM awareness?",
        "Show spontaneous brand awareness by brand",
        "Compare TOM vs aided awareness by brand",
      ],
    },
    {
      category: "NPS & Ratings",
      icon: "⭐",
      prompts: [
        "Show NPS score for all brands",
        "Which brand has highest promoters?",
        "Show NPS breakdown by zone",
      ],
    },
    {
      category: "Ownership",
      icon: "🏠",
      prompts: [
        "Which kitchen appliance has highest ownership?",
        "Mixer grinder penetration by zone",
        "Show kitchen appliance ownership by city",
      ],
    },
    {
      category: "Room Appliances",
      icon: "💡",
      prompts: [
        "Ceiling fan ownership by zone",
        "LED bulb penetration by city",
        "AC ownership by gender",
      ],
    },
    {
      category: "Demographics",
      icon: "👥",
      prompts: [
        "Gender split in respondents",
        "Respondents count by zone",
        "Age distribution by city",
      ],
    },
    {
      category: "Purchases",
      icon: "🛒",
      prompts: [
        "Recent mixer grinder purchases by city",
        "Which appliance was purchased most recently?",
      ],
    },
    {
      category: "Brand Comparisons",
      icon: "⚖️",
      prompts: [
        "Compare Crompton vs Bajaj awareness",
        "Crompton vs Havells vs Orient NPS",
        "Philips vs Syska vs Wipro comparison",
      ],
    },
  ];
  return (
    <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}
      className="text-center my-auto flex flex-col items-center justify-center h-full gap-6 pb-16">
      <div>
        <h1 className="font-headline text-4xl md:text-5xl font-medium text-primary mb-3">OxData</h1>
        <p className="font-body text-secondary text-base max-w-md mx-auto">
          Ask anything about brands, NPS, awareness, appliances and demographics — OX Wave 1 survey data.
        </p>
      </div>
      <div className="w-full max-w-4xl px-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {promptCategories.map((cat, ci) => (
            <div key={ci} className="bg-surface-variant/20 rounded-2xl p-4 border border-outline-variant/20">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-lg">{cat.icon}</span>
                <span className="font-medium text-sm text-primary">{cat.category}</span>
              </div>
              <div className="flex flex-col gap-1.5">
                {cat.prompts.map((p, pi) => (
                  <button key={pi}
                    className="text-left text-xs px-2 py-1.5 rounded-lg bg-surface-low/50 hover:bg-surface-low text-secondary hover:text-on-surface transition-all"
                    onClick={() => onPrompt(p)}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
      {isInitializing && (
        <p className="text-xs text-secondary/50 animate-pulse">Connecting to OxData engine…</p>
      )}
      <div className="w-12 h-0.5 bg-outline-variant rounded-full" />
    </motion.div>
  );
}

// ── Thinking indicator ────────────────────────────────────────────────────────

function ThinkingIndicator() {
  const [step, setStep] = useState(0);
  const steps = [
    "Identifying semantic intent...",
    "Pruning database schema...",
    "Generating optimized SQL query...",
    "Verifying columns and joins...",
    "Executing query on OxData DB...",
    "Synthesizing executive insights..."
  ];

  useEffect(() => {
    const interval = setInterval(() => {
      setStep(s => (s < steps.length - 1 ? s + 1 : s));
    }, 2500);
    return () => clearInterval(interval);
  }, []);

  return (
    <motion.div 
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className="flex flex-col gap-3 w-full max-w-[95%] py-4 pl-1"
    >
      <div className="flex items-center gap-3">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <motion.div
              key={i}
              className="w-1.5 h-1.5 bg-primary rounded-full"
              animate={{ opacity: [0.3, 1, 0.3], scale: [1, 1.2, 1] }}
              transition={{ duration: 1, repeat: Infinity, delay: i * 0.2 }}
            />
          ))}
        </div>
        <AnimatePresence mode="wait">
          <motion.span 
            key={step}
            initial={{ opacity: 0, x: 5 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -5 }}
            className="text-sm font-medium text-secondary italic"
          >
            {steps[step]}
          </motion.span>
        </AnimatePresence>
      </div>
      
      {/* Skeleton Thinking Block */}
      <div className="bg-surface-variant/5 rounded-2xl p-4 border border-outline-variant/10 w-full animate-pulse mt-1">
        <div className="flex items-center gap-2 mb-3">
          <Brain className="w-4 h-4 text-primary/20" />
          <div className="h-2 w-24 bg-primary/10 rounded" />
        </div>
        <div className="space-y-2">
          <div className="h-2 w-full bg-secondary/5 rounded" />
          <div className="h-2 w-3/4 bg-secondary/5 rounded" />
        </div>
      </div>
    </motion.div>
  );
}
