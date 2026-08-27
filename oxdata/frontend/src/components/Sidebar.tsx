import { memo } from "react";
import {
  MessageSquarePlus, Database, History, Settings,
  ChevronLeft, ChevronRight, Activity, BarChart3,
} from "lucide-react";
import { cn } from "../lib/utils";
import { motion, AnimatePresence } from "motion/react";

export type ViewType = "chat" | "schema" | "history" | "settings" | "brandhealth";

interface SidebarProps {
  currentView: ViewType;
  onViewChange: (view: ViewType) => void;
  onNewChat: () => void;
  isCollapsed: boolean;
  setIsCollapsed: (collapsed: boolean) => void;
}

export const Sidebar = memo(function Sidebar({
  currentView, onViewChange, onNewChat, isCollapsed, setIsCollapsed,
}: SidebarProps) {
  const navItems = [
    { id: "chat",        label: "Chat",            icon: MessageSquarePlus, desc: "Ask a question" },
    { id: "brandhealth", label: "Brand Health",    icon: BarChart3,         desc: "Awareness & imagery" },
    { id: "schema",      label: "Schema Explorer", icon: Database,          desc: "Browse tables & views" },
    { id: "history",     label: "Query History",   icon: History,           desc: "Recent queries" },
  ] as const;

  const bottomItems = [
    { id: "settings", label: "Settings", icon: Settings },
  ] as const;

  return (
    <nav className={cn(
      "h-screen flex-shrink-0 flex flex-col py-6 gap-y-4 bg-surface-container transition-[width,padding] duration-300 ease-in-out z-40 border-r border-outline-variant/20 relative",
      isCollapsed ? "w-20 px-3 items-center" : "w-64 px-5",
    )}>
      {/* Collapse toggle */}
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="absolute -right-3 top-10 w-6 h-6 bg-surface-lowest border border-outline-variant/50 rounded-full flex items-center justify-center shadow-sm z-50 text-secondary hover:text-primary transition-all hover:scale-110"
        aria-label="Toggle sidebar"
      >
        {isCollapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
      </button>

      {/* Brand */}
      <motion.div
        layout
        initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.05 }}
        className={cn("flex items-center gap-3 mb-2", isCollapsed && "justify-center")}
      >
        <div className={cn(
          "rounded-xl bg-primary/10 flex items-center justify-center flex-shrink-0 transition-all duration-300",
          isCollapsed ? "w-10 h-10" : "w-10 h-10",
        )}>
          <Activity className="w-5 h-5 text-primary" />
        </div>
        <AnimatePresence mode="wait">
          {!isCollapsed && (
            <motion.div 
              key="brand-text"
              initial={{ opacity: 0, x: -10 }} 
              animate={{ opacity: 1, x: 0 }} 
              exit={{ opacity: 0, x: -10 }}
              className="overflow-hidden whitespace-nowrap"
            >
              <h2 className="font-headline font-semibold text-base leading-tight text-on-surface">OxData</h2>
              <p className="text-secondary text-[11px] leading-tight">OX Wave 1 · 6,631 respondents</p>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* New Query CTA */}
      <motion.button
        layout
        initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}
        onClick={onNewChat}
        className={cn(
          "bg-primary text-on-primary rounded-xl flex items-center justify-center gap-2 font-medium text-sm hover:bg-primary/90 transition-all active:scale-[0.98] shadow-sm",
          isCollapsed ? "w-12 h-12 p-0" : "w-full py-2.5 px-4",
        )}
        title="New Query"
      >
        <MessageSquarePlus className={cn("w-4 h-4 flex-shrink-0", isCollapsed && "w-5 h-5")} />
        <AnimatePresence mode="wait">
          {!isCollapsed && (
            <motion.span
              key="new-query-text"
              initial={{ opacity: 0, width: 0 }}
              animate={{ opacity: 1, width: "auto" }}
              exit={{ opacity: 0, width: 0 }}
              className="whitespace-nowrap overflow-hidden"
            >
              New Query
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>

      {/* Nav items */}
      <div className={cn("flex flex-col gap-1 flex-1 mt-2 w-full", isCollapsed && "items-center")}>
        {navItems.map((item, idx) => (
          <NavItem
            key={item.id}
            active={currentView === item.id}
            onClick={() => onViewChange(item.id as ViewType)}
            icon={item.icon}
            label={item.label}
            delay={0.15 + idx * 0.04}
            isCollapsed={isCollapsed}
            title={isCollapsed ? item.label : undefined}
          />
        ))}
      </div>

      {/* Footer */}
      <div className={cn(
        "flex flex-col gap-1 pt-4 border-t border-outline-variant/25 w-full",
        isCollapsed && "items-center",
      )}>
        {!isCollapsed && (
          <p className="text-[10px] text-secondary/50 px-2 mb-1 uppercase tracking-wider font-medium">System</p>
        )}
        {bottomItems.map((item, idx) => (
          <NavItem
            key={item.id}
            active={currentView === item.id}
            onClick={() => onViewChange(item.id as ViewType)}
            icon={item.icon}
            label={item.label}
            delay={0.35 + idx * 0.04}
            isCollapsed={isCollapsed}
            title={isCollapsed ? item.label : undefined}
          />
        ))}
      </div>
    </nav>
  );
});

function NavItem({ active, onClick, icon: Icon, label, delay, isCollapsed, title }: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  delay: number;
  isCollapsed: boolean;
  title?: string;
}) {
  return (
    <motion.button
      initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay }}
      onClick={onClick}
      title={title}
      className={cn(
        "flex items-center transition-all relative overflow-hidden group",
        isCollapsed
          ? "w-12 h-12 justify-center rounded-xl"
          : "w-full text-left gap-3 py-2.5 px-3 rounded-xl",
        active
          ? "bg-primary/10 text-primary"
          : "text-secondary hover:bg-surface-variant/60 hover:text-on-surface",
      )}
    >
      {active && !isCollapsed && (
        <motion.div
          layoutId="active-nav-indicator"
          className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-5 bg-primary rounded-r-full"
        />
      )}
      <Icon className={cn(
        "w-4 h-4 flex-shrink-0 transition-colors",
        isCollapsed && "w-5 h-5",
        active ? "text-primary" : "text-secondary group-hover:text-on-surface",
      )} />
      {!isCollapsed && (
        <span className="text-sm font-medium whitespace-nowrap">{label}</span>
      )}
    </motion.button>
  );
}
