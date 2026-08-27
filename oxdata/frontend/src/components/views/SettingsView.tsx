import { motion } from "motion/react";

export function SettingsView() {
  return (
    <div className="flex-1 overflow-y-auto px-8 md:px-24 py-16 w-full max-w-3xl mx-auto flex flex-col">
      <motion.div 
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-12"
      >
        <h1 className="font-headline text-4xl font-medium text-primary mb-3">Settings</h1>
        <p className="text-secondary">Configure your editorial environment.</p>
      </motion.div>

      <div className="space-y-10">
        <SettingSection title="Preferences" delay={0.1}>
          <ToggleRow label="Autosave Manuscripts" description="Automatically save progress to your library." defaultOn={true} />
          <ToggleRow label="Literary Tone" description="Enforce standard academic phrasing in responses." defaultOn={true} />
        </SettingSection>
        
        <SettingSection title="Appearance" delay={0.2}>
          <div className="flex flex-col gap-4">
            <p className="text-sm font-medium text-on-surface">Typeface Preference</p>
            <div className="grid grid-cols-2 gap-4">
              <button className="flex flex-col items-start p-4 hover:border-primary border border-primary bg-primary/5 rounded-xl shadow-sm text-left transition-all relative">
                <div className="absolute top-3 right-3 w-4 h-4 rounded-full bg-primary flex items-center justify-center">
                  <div className="w-1.5 h-1.5 bg-white rounded-full"></div>
                </div>
                <span className="font-headline text-2xl text-primary mb-1">Aa</span>
                <span className="text-sm font-medium text-on-surface">Newsreader</span>
                <span className="text-xs text-secondary mt-1">Editorial & Classic</span>
              </button>
              <button className="flex flex-col items-start p-4 border border-outline-variant hover:border-primary/50 bg-surface-lowest rounded-xl shadow-sm text-left transition-all">
                <span className="font-body text-2xl mb-1">Aa</span>
                <span className="text-sm font-medium text-on-surface">Inter</span>
                <span className="text-xs text-secondary mt-1">Modern & Clean</span>
              </button>
            </div>
          </div>
        </SettingSection>
      </div>
    </div>
  );
}

function SettingSection({ title, children, delay }: { title: string, children: React.ReactNode, delay: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay }}
      className="flex flex-col gap-6 p-6 rounded-2xl bg-surface-lowest border border-outline-variant/30"
    >
      <h3 className="font-semibold text-lg border-b border-outline-variant/30 pb-4">{title}</h3>
      <div className="flex flex-col gap-6">{children}</div>
    </motion.div>
  );
}

function ToggleRow({ label, description, defaultOn }: { label: string, description: string, defaultOn: boolean }) {
  return (
    <div className="flex items-center justify-between gap-8">
      <div className="flex flex-col gap-1">
        <span className="font-medium text-on-surface text-base">{label}</span>
        <span className="text-sm text-secondary leading-relaxed">{description}</span>
      </div>
      <div className={`w-12 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors ${defaultOn ? 'bg-primary' : 'bg-outline-variant'}`}>
        <motion.div 
          layout
          className={`bg-white w-4 h-4 rounded-full shadow-sm ${defaultOn ? 'ml-auto' : ''}`}
        />
      </div>
    </div>
  );
}
