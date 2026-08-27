import { motion } from "motion/react";
import { FolderHeart, MoreVertical } from "lucide-react";

export function CollectionsView() {
  const collections = [
    { title: "Romanticism Authors", count: 12 },
    { title: "Python Engineering", count: 4 },
    { title: "Machine Learning Algorithms", count: 8 },
    { title: "Victorian Era Prose", count: 15 },
    { title: "Modernist Poetry", count: 3 },
  ];

  return (
    <div className="flex-1 overflow-y-auto px-8 md:px-24 py-16 w-full max-w-5xl mx-auto flex flex-col">
      <motion.div 
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-12"
      >
        <h1 className="font-headline text-4xl font-medium text-primary mb-3">Collections</h1>
        <p className="text-secondary">Curated groups of your manuscripts and inquiries.</p>
      </motion.div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {collections.map((col, idx) => (
          <motion.div
            key={idx}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1 + idx * 0.05 }}
            whileHover={{ y: -4 }}
            className="group flex flex-col p-6 rounded-2xl border border-outline-variant/30 hover:border-primary/40 hover:shadow-sm bg-surface-lowest transition-all cursor-pointer relative"
          >
            <div className="flex items-center justify-between mb-8">
              <div className="w-12 h-12 bg-primary/10 text-primary rounded-xl flex items-center justify-center">
                <FolderHeart className="w-6 h-6" />
              </div>
              <button className="text-secondary hover:text-primary transition-colors p-1.5 rounded-md hover:bg-surface-variant opacity-0 group-hover:opacity-100">
                <MoreVertical className="w-5 h-5" />
              </button>
            </div>
            <h3 className="font-medium text-on-surface text-lg mb-1 pr-4">{col.title}</h3>
            <p className="text-secondary text-sm">{col.count} Manuscripts</p>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
