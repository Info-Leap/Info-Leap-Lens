/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Sidebar, type ViewType } from "./components/Sidebar";
import { ChatView } from "./components/views/ChatView";
import { SchemaView } from "./components/views/SchemaView";
import { QueryHistoryView } from "./components/views/QueryHistoryView";
import { SettingsView } from "./components/views/SettingsView";
import { BrandHealthView } from "./components/views/BrandHealthView";
import type { Message } from "./lib/gemini";

export default function App() {
  const [currentView, setCurrentView] = useState<ViewType>("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  const renderView = () => {
    switch (currentView) {
      case "chat":
        return <ChatView messages={messages} setMessages={setMessages} isInitializing={false} />;
      case "brandhealth":
        return <BrandHealthView />;
      case "schema":
        return <SchemaView />;
      case "history":
        return <QueryHistoryView />;
      case "settings":
        return <SettingsView />;
      default:
        return <ChatView messages={messages} setMessages={setMessages} isInitializing={false} />;
    }
  };

  return (
    <div className="flex bg-surface font-body text-on-surface antialiased h-screen w-full overflow-hidden">
      <Sidebar 
        currentView={currentView} 
        onViewChange={(v) => setCurrentView(v)} 
        onNewChat={() => {
          setMessages([]);
          setCurrentView("chat");
        }}
        isCollapsed={isSidebarCollapsed}
        setIsCollapsed={setIsSidebarCollapsed}
      />
      <main className="flex-1 flex flex-col h-screen relative bg-surface-container-low overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentView}
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
            className="w-full h-full absolute inset-0 flex"
          >
            {renderView()}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
