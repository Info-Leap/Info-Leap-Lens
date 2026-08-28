import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Book, Clock, MoreVertical, Search, BarChart3, TrendingUp, Zap } from "lucide-react";

export function LibraryView() {
  const [data, setData] = useState([0, 0, 0, 0, 0, 0, 0]);

  useEffect(() => {
    // Simulate loading a chart after mount
    setTimeout(() => {
      setData([40, 60, 45, 80, 55, 90, 75]);
    }, 400);
  }, []);

  const manuscripts = [
    { title: "The Aesthetics of Code", date: "Today", excerpt: "A deep dive into how elegant structural patterns..." },
    { title: "Pythonic Parsing Techniques", date: "Yesterday", excerpt: "To parse a literary text with nuance, we must look beyond..." },
    { title: "Notes on Victorian Literature", date: "Oct 12", excerpt: "The primary characteristics of the era's prose can be..." },
    { title: "Initial Inquiry: Machine Learning", date: "Oct 10", excerpt: "Could you articulate the difference between supervised and..." },
  ];

  return (
    <div className="flex-1 overflow-y-auto px-8 md:px-24 py-16 w-full max-w-5xl mx-auto flex flex-col">
      <motion.div 
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-12"
      >
        <h1 className="font-headline text-4xl font-medium text-primary mb-3">Library & Insights</h1>
        <p className="text-secondary">Your archived intellectual pursuits and analytical overview.</p>
      </motion.div>

      {/* Unique Loading Chart Section */}
      <motion.div 
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 0.1 }}
        className="relative mb-12 bg-surface-lowest border border-outline-variant/30 rounded-2xl p-6 shadow-sm overflow-hidden"
      >
        <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl -mr-16 -mt-16 pointer-events-none" />
        
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center text-primary">
              <TrendingUp className="w-5 h-5" />
            </div>
            <div>
              <h2 className="font-medium text-lg">Inquiry Frequency</h2>
              <p className="text-sm text-secondary">Words processed over the last 7 days</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-primary font-medium bg-primary/5 px-3 py-1.5 rounded-lg text-sm">
            <Zap className="w-4 h-4" />
            +12.5% High intellectual activity
          </div>
        </div>

        <div className="flex items-end gap-3 h-32 pl-2">
          {data.map((height, i) => (
            <div key={i} className="flex-1 flex flex-col items-center justify-end gap-2 group relative">
              {/* Tooltip on hover */}
              <div className="absolute -top-8 bg-surface-variant text-on-surface text-xs px-2 py-1 rounded-md opacity-0 group-hover:opacity-100 transition-opacity">
                {height * 123} w
              </div>
              
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: `${height}%` }}
                transition={{ duration: 0.8, delay: i * 0.1, type: "spring", damping: 15 }}
                className={`w-full max-w-[36px] rounded-t-lg mx-auto ${i === 6 ? 'bg-primary' : 'bg-outline-variant group-hover:bg-primary/50'} transition-all`}
              />
            </div>
          ))}
        </div>
        <div className="flex justify-between items-center px-4 mt-3 text-xs text-secondary/70">
          <span>Mon</span>
          <span>Tue</span>
          <span>Wed</span>
          <span>Thu</span>
          <span>Fri</span>
          <span>Sat</span>
          <span className="text-primary font-medium">Sun</span>
        </div>
      </motion.div>

      <motion.div 
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.15 }}
        className="relative mb-8"
      >
        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
          <Search className="text-outline w-5 h-5" />
        </div>
        <input 
          type="text" 
          className="w-full bg-surface-container-low border hover:border-outline-variant border-transparent placeholder:text-outline focus:border-primary/50 focus:ring-2 focus:ring-primary/20 rounded-xl py-3.5 pl-12 pr-4 font-body text-on-surface outline-none transition-all" 
          placeholder="Search manuscripts..."
        />
      </motion.div>

      <div className="flex flex-col gap-4">
        {manuscripts.map((doc, idx) => (
          <motion.div
            key={idx}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 + idx * 0.05 }}
            className="group flex flex-col md:flex-row md:items-center justify-between p-5 rounded-2xl border border-outline-variant/30 hover:border-primary/30 hover:shadow-sm bg-surface-lowest transition-all cursor-pointer relative overflow-hidden"
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-primary/0 group-hover:bg-primary transition-colors" />
            
            <div className="flex items-start gap-4 flex-1 min-w-0 pr-4">
              <div className="p-2.5 bg-surface-variant rounded-xl text-primary shrink-0 mt-0.5">
                <Book className="w-5 h-5" />
              </div>
              <div className="flex pb-2 flex-col gap-1.5 min-w-0 w-full">
                <h3 className="font-medium text-on-surface truncate pr-4 text-base">{doc.title}</h3>
                <p className="text-secondary text-sm truncate">{doc.excerpt}</p>
              </div>
            </div>
            
            <div className="flex items-center gap-6 mt-4 md:mt-0 justify-between md:justify-end shrink-0 pl-16 md:pl-0 border-t md:border-t-0 border-outline-variant/20 md:border-transparent pt-4 md:pt-0">
              <div className="flex items-center gap-1.5 text-xs text-secondary font-medium px-2.5 py-1 bg-surface-container rounded-md">
                <Clock className="w-3.5 h-3.5" />
                {doc.date}
              </div>
              <button className="text-secondary hover:text-primary transition-colors p-1 rounded-md hover:bg-surface-variant">
                <MoreVertical className="w-4 h-4" />
              </button>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
